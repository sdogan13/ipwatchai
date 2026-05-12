"""Design watchlist service — CRUD over ``design_watchlist_mt``.

Sister to ``services/watchlist_service.py`` (Marka). Pure psycopg2 + raw SQL
keeps the surface light; there is no DesignWatchlistCRUD layer in
``database/crud.py`` (yet). The trademark watchlist service uses a CRUD
helper, but the design schema is small enough that inlining keeps the diff
reviewable.

Quota model (per the locked plan): design watchlist rows count against the
combined ``max_watchlist_items`` budget alongside ``watchlist_mt`` rows.
A free-plan org with 5 trademark watchlist entries cannot add a 6th item of
either type.

Image attachment is two-step: create row first (text + Locarno only), then
upload an image via the route layer to populate the embedding columns.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from fastapi import HTTPException, status

from database.crud import Database


logger = logging.getLogger("turkpatent.design_watchlist")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def to_halfvec_literal(values: Optional[Sequence[float]]) -> Optional[str]:
    """``[v1,v2,...]`` literal for casting to halfvec(N) in SQL."""
    if values is None:
        return None
    materialized = list(values)
    if not materialized:
        return None
    return "[" + ",".join(f"{float(v):.6f}" for v in materialized) + "]"


def _normalize_locarno(values: Optional[Sequence[str]]) -> List[str]:
    """Accept `['06-01', '6.2', '06']` -> `['06-01', '06-02', '06']`."""
    if not values:
        return []
    out: List[str] = []
    for raw in values:
        if raw is None:
            continue
        s = str(raw).strip().replace(".", "-")
        if not s:
            continue
        parts = s.split("-")
        norm_parts = []
        for part in parts:
            digits = "".join(ch for ch in part if ch.isdigit())
            if not digits:
                continue
            norm_parts.append(digits.zfill(2))
        if norm_parts:
            out.append("-".join(norm_parts))
    # de-dupe preserving order
    seen = set()
    deduped = []
    for v in out:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def _row_to_dict(row) -> Dict[str, Any]:
    """psycopg2 RealDictRow -> plain dict (avoids leaking the cursor proxy)."""
    if row is None:
        return {}
    return dict(row)


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------

def _combined_watchlist_count(cur, organization_id: UUID) -> int:
    """Active watchlist item count across trademarks + designs + patents.

    Delegates to ``services.patent_watchlist_service.combined_watchlist_count``
    so all three registry watchlists share one canonical count function and
    the ``max_watchlist_items`` plan bucket stays consistent regardless of
    which surface created the row.
    """
    from services.patent_watchlist_service import combined_watchlist_count
    return combined_watchlist_count(cur, organization_id)


def _check_watchlist_quota(db, current_user) -> None:
    """Raise 403 if combined trademark+design watchlist count would exceed plan."""
    from utils.subscription import get_plan_limit, get_user_plan

    plan_info = get_user_plan(db, str(current_user.id))
    plan_name = plan_info.get("plan_name", "free")
    max_items = get_plan_limit(plan_name, "max_watchlist_items")
    cur = db.cursor()
    current_count = _combined_watchlist_count(cur, current_user.organization_id)
    if current_count >= max_items:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "limit_exceeded",
                "message": (
                    f"Izleme listesi limitinize ulastiniz ({max_items}). "
                    "Daha fazla eklemek icin planinizi yukseltin."
                ),
                "current_count": current_count,
                "max_items": max_items,
                "plan_name": plan_name,
            },
        )


# ---------------------------------------------------------------------------
# Service methods
# ---------------------------------------------------------------------------

def create_design_watchlist_item(
    *,
    data: Dict[str, Any],
    current_user,
    db_factory=Database,
) -> Dict[str, Any]:
    """Create a watchlist item. Optionally clones embeddings from a referenced
    design row. Returns the inserted row as a dict.
    """
    product_name = (data.get("product_name") or "").strip()
    if not product_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="product_name is required",
        )

    locarno = _normalize_locarno(data.get("locarno_classes"))
    reference_design_id = data.get("reference_design_id")
    customer_app_no = (data.get("customer_application_no") or "").strip() or None

    with db_factory() as db:
        _check_watchlist_quota(db, current_user)
        cur = db.cursor()

        # Dedupe: same org + same customer_application_no + active = conflict
        if customer_app_no:
            cur.execute(
                """
                SELECT 1 FROM design_watchlist_mt
                WHERE organization_id = %s
                  AND customer_application_no = %s
                  AND is_active = TRUE
                LIMIT 1
                """,
                (str(current_user.organization_id), customer_app_no),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Bu basvuru no ile bir takip kaydi zaten var",
                )

        # Optional: clone embeddings from an existing design row
        cloned: Dict[str, Optional[str]] = {
            "image_path": None,
            "dinov2_embedding": None,
            "clip_embedding": None,
            "color_histogram": None,
        }
        if reference_design_id:
            cur.execute(
                """
                SELECT d.dinov2_vitl14_mean::text  AS dinov2,
                       d.clip_vitb32_mean::text    AS clip,
                       (
                           SELECT image_path FROM design_views
                           WHERE design_id = d.id ORDER BY view_index ASC LIMIT 1
                       ) AS image_path,
                       (
                           SELECT color_hsv::text FROM design_views
                           WHERE design_id = d.id ORDER BY view_index ASC LIMIT 1
                       ) AS color
                FROM designs d WHERE d.id = %s
                """,
                (str(reference_design_id),),
            )
            ref = cur.fetchone()
            if ref:
                cloned["image_path"] = ref.get("image_path")
                cloned["dinov2_embedding"] = ref.get("dinov2")
                cloned["clip_embedding"] = ref.get("clip")
                cloned["color_histogram"] = ref.get("color")

        params = {
            "organization_id": str(current_user.organization_id),
            "user_id": str(current_user.id),
            "product_name": product_name[:500],
            "locarno_classes": locarno,
            "description": data.get("description"),
            "customer_application_no": customer_app_no,
            "customer_registration_no": (data.get("customer_registration_no") or "").strip() or None,
            "reference_design_id": str(reference_design_id) if reference_design_id else None,
            "image_path": cloned["image_path"],
            "dinov2_embedding": cloned["dinov2_embedding"],
            "clip_embedding": cloned["clip_embedding"],
            "color_histogram": cloned["color_histogram"],
            "similarity_threshold": float(data.get("similarity_threshold") or 0.50),
            "monitor_text": bool(data.get("monitor_text", True)),
            "monitor_visual": bool(data.get("monitor_visual", True)),
            "alert_email": bool(data.get("alert_email", True)),
            "alert_webhook": bool(data.get("alert_webhook", False)),
            "webhook_url": data.get("webhook_url"),
            "alert_frequency": data.get("alert_frequency") or "daily",
            "tags": list(data.get("tags") or []),
            "priority": data.get("priority") or "medium",
        }

        cur.execute(
            """
            INSERT INTO design_watchlist_mt
                (organization_id, user_id, product_name, locarno_classes, description,
                 customer_application_no, customer_registration_no, reference_design_id,
                 image_path, dinov2_embedding, clip_embedding, color_histogram,
                 similarity_threshold, monitor_text, monitor_visual,
                 alert_email, alert_webhook, webhook_url, alert_frequency,
                 tags, priority)
            VALUES
                (%(organization_id)s, %(user_id)s, %(product_name)s, %(locarno_classes)s, %(description)s,
                 %(customer_application_no)s, %(customer_registration_no)s, %(reference_design_id)s,
                 %(image_path)s, %(dinov2_embedding)s::halfvec, %(clip_embedding)s::halfvec, %(color_histogram)s::halfvec,
                 %(similarity_threshold)s, %(monitor_text)s, %(monitor_visual)s,
                 %(alert_email)s, %(alert_webhook)s, %(webhook_url)s, %(alert_frequency)s,
                 %(tags)s, %(priority)s)
            RETURNING *
            """,
            params,
        )
        row = cur.fetchone()
        db.commit()
        return _row_to_dict(row)


def get_design_watchlist_item(*, item_id: UUID, current_user, db_factory=Database) -> Dict[str, Any]:
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT * FROM design_watchlist_mt
            WHERE id = %s AND organization_id = %s AND is_active = TRUE
            """,
            (str(item_id), str(current_user.organization_id)),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Design watchlist item not found",
        )
    return _row_to_dict(row)


def list_design_watchlist_items(
    *,
    current_user,
    page: int = 1,
    page_size: int = 20,
    is_active: Optional[bool] = True,
    db_factory=Database,
) -> Dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    offset = (page - 1) * page_size

    where = ["organization_id = %s"]
    params: List[Any] = [str(current_user.organization_id)]
    if is_active is not None:
        where.append("is_active = %s")
        params.append(bool(is_active))
    where_sql = " AND ".join(where)

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(f"SELECT COUNT(*) AS c FROM design_watchlist_mt WHERE {where_sql}", params)
        total_row = cur.fetchone()
        total = int(total_row.get("c") if isinstance(total_row, dict) else total_row[0])

        cur.execute(
            f"""
            SELECT w.*,
                   (SELECT COUNT(*) FROM design_alerts_mt
                    WHERE watchlist_item_id = w.id AND status = 'new') AS new_alerts_count,
                   (SELECT COUNT(*) FROM design_alerts_mt
                    WHERE watchlist_item_id = w.id) AS total_alerts_count
            FROM design_watchlist_mt w
            WHERE {where_sql}
            ORDER BY w.created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]

    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }


def update_design_watchlist_item(
    *,
    item_id: UUID,
    data: Dict[str, Any],
    current_user,
    db_factory=Database,
) -> Dict[str, Any]:
    """Update mutable fields. Returns the refreshed row."""
    fields = []
    params: Dict[str, Any] = {"id": str(item_id), "org": str(current_user.organization_id)}

    string_fields = ("product_name", "description", "webhook_url", "alert_frequency", "priority",
                     "customer_application_no", "customer_registration_no")
    bool_fields = ("monitor_text", "monitor_visual", "alert_email", "alert_webhook", "is_active")
    real_fields = ("similarity_threshold",)

    for f in string_fields:
        if f in data and data[f] is not None:
            fields.append(f"{f} = %({f})s")
            params[f] = str(data[f])
    for f in bool_fields:
        if f in data and data[f] is not None:
            fields.append(f"{f} = %({f})s")
            params[f] = bool(data[f])
    for f in real_fields:
        if f in data and data[f] is not None:
            fields.append(f"{f} = %({f})s")
            params[f] = float(data[f])
    if "locarno_classes" in data and data["locarno_classes"] is not None:
        fields.append("locarno_classes = %(locarno_classes)s")
        params["locarno_classes"] = _normalize_locarno(data["locarno_classes"])
    if "tags" in data and data["tags"] is not None:
        fields.append("tags = %(tags)s")
        params["tags"] = list(data["tags"])

    if not fields:
        return get_design_watchlist_item(item_id=item_id, current_user=current_user, db_factory=db_factory)

    fields.append("updated_at = NOW()")
    set_sql = ", ".join(fields)

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            UPDATE design_watchlist_mt SET {set_sql}
            WHERE id = %(id)s AND organization_id = %(org)s
            RETURNING *
            """,
            params,
        )
        row = cur.fetchone()
        db.commit()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Design watchlist item not found",
        )
    return _row_to_dict(row)


def delete_design_watchlist_item(
    *,
    item_id: UUID,
    current_user,
    db_factory=Database,
) -> Dict[str, Any]:
    """Hard-delete the watchlist item; cascade removes its alerts."""
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM design_alerts_mt WHERE watchlist_item_id = %s",
            (str(item_id),),
        )
        deleted_alerts_row = cur.fetchone()
        deleted_alerts = int(
            deleted_alerts_row.get("c") if isinstance(deleted_alerts_row, dict) else deleted_alerts_row[0]
        )
        cur.execute(
            """
            DELETE FROM design_watchlist_mt
            WHERE id = %s AND organization_id = %s
            RETURNING id
            """,
            (str(item_id), str(current_user.organization_id)),
        )
        deleted = cur.fetchone()
        db.commit()
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Design watchlist item not found",
        )
    return {
        "success": True,
        "id": str(item_id),
        "removed_alerts": deleted_alerts,
        "message": f"Tasarim takibi ve {deleted_alerts} uyari silindi",
    }


def attach_design_watchlist_image(
    *,
    item_id: UUID,
    image_path: str,
    embeddings: Dict[str, Sequence[float]],
    current_user,
    db_factory=Database,
) -> Dict[str, Any]:
    """Set image_path and per-signal embeddings on a watchlist row.

    ``embeddings`` keys: ``dinov2_vitl14`` (1024-d), ``clip_vitb32`` (512-d),
    ``color_hsv`` (512-d). Missing keys leave the corresponding column NULL.
    """
    params = {
        "id": str(item_id),
        "org": str(current_user.organization_id),
        "image_path": image_path,
        "dinov2": to_halfvec_literal(embeddings.get("dinov2_vitl14")),
        "clip": to_halfvec_literal(embeddings.get("clip_vitb32")),
        "color": to_halfvec_literal(embeddings.get("color_hsv")),
    }
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE design_watchlist_mt
            SET image_path        = %(image_path)s,
                dinov2_embedding  = %(dinov2)s::halfvec,
                clip_embedding    = %(clip)s::halfvec,
                color_histogram   = %(color)s::halfvec,
                updated_at        = NOW()
            WHERE id = %(id)s AND organization_id = %(org)s
            RETURNING *
            """,
            params,
        )
        row = cur.fetchone()
        db.commit()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Design watchlist item not found",
        )
    return _row_to_dict(row)


def get_active_design_watchlist_items(*, db) -> List[Dict[str, Any]]:
    """Return every active design watchlist row across all orgs.

    Used by the scanner; routes/services should not call this directly.
    """
    cur = db.cursor()
    cur.execute(
        "SELECT * FROM design_watchlist_mt WHERE is_active = TRUE ORDER BY created_at ASC"
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


def update_last_scan_at(*, item_id: UUID, db) -> None:
    cur = db.cursor()
    cur.execute(
        "UPDATE design_watchlist_mt SET last_scan_at = NOW() WHERE id = %s",
        (str(item_id),),
    )


# ---------------------------------------------------------------------------
# Phase 2 — bulk operations + stats
# ---------------------------------------------------------------------------

def get_design_watchlist_stats(*, current_user, db_factory=Database) -> Dict[str, Any]:
    """Return totals for the Watchlist tab's Tasarım stats cards.

    Shape: ``{total, threatened, critical, new_alerts}``.
        * total       — active watchlist items for the org
        * threatened  — items with ≥1 active (status='new') alert
        * critical    — alerts with severity='critical' AND status='new'
        * new_alerts  — alerts with status='new' (any severity)
    """
    org_id = str(current_user.organization_id)
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM design_watchlist_mt
               WHERE organization_id = %(org)s AND is_active = TRUE)
                AS total,
              (SELECT COUNT(DISTINCT a.watchlist_item_id)
               FROM design_alerts_mt a
               JOIN design_watchlist_mt w ON w.id = a.watchlist_item_id
               WHERE w.organization_id = %(org)s
                 AND w.is_active = TRUE
                 AND a.status = 'new')
                AS threatened,
              (SELECT COUNT(*) FROM design_alerts_mt
               WHERE organization_id = %(org)s
                 AND status = 'new' AND severity = 'critical')
                AS critical,
              (SELECT COUNT(*) FROM design_alerts_mt
               WHERE organization_id = %(org)s AND status = 'new')
                AS new_alerts
            """,
            {"org": org_id},
        )
        row = cur.fetchone() or {}
    if isinstance(row, dict):
        return {
            "total": int(row.get("total", 0) or 0),
            "threatened": int(row.get("threatened", 0) or 0),
            "critical": int(row.get("critical", 0) or 0),
            "new_alerts": int(row.get("new_alerts", 0) or 0),
        }
    # Fallback for tuple-style rows
    return {
        "total": int(row[0] or 0),
        "threatened": int(row[1] or 0),
        "critical": int(row[2] or 0),
        "new_alerts": int(row[3] or 0),
    }


def list_active_item_ids_for_org(*, current_user, db_factory=Database) -> List[str]:
    """IDs of every active design-watchlist item for the org. Used by
    scan-all (routes layer queues a background task per id)."""
    org_id = str(current_user.organization_id)
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id FROM design_watchlist_mt
            WHERE organization_id = %s AND is_active = TRUE
            ORDER BY created_at ASC
            """,
            (org_id,),
        )
        rows = cur.fetchall() or []
    out: List[str] = []
    for r in rows:
        v = r.get("id") if isinstance(r, dict) else r[0]
        if v is not None:
            out.append(str(v))
    return out


def delete_all_design_watchlist_items(*, current_user, db_factory=Database) -> Dict[str, Any]:
    """Delete every design-watchlist item for the org. The FK on
    design_alerts_mt cascades, so all related alerts disappear too.
    Returns ``{deleted: int}``."""
    org_id = str(current_user.organization_id)
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            DELETE FROM design_watchlist_mt
            WHERE organization_id = %s
            RETURNING id
            """,
            (org_id,),
        )
        deleted = len(cur.fetchall() or [])
        db.commit()
    return {"success": True, "deleted": deleted}


def update_all_design_watchlist_thresholds(
    *,
    current_user,
    threshold: float,
    db_factory=Database,
) -> Dict[str, Any]:
    """Apply a single similarity_threshold to every active item for the org.
    Bound-checked 0.0..1.0 here (the route also validates via Pydantic)."""
    if threshold < 0.0 or threshold > 1.0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="threshold must be between 0.0 and 1.0",
        )
    org_id = str(current_user.organization_id)
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE design_watchlist_mt
            SET similarity_threshold = %s, updated_at = NOW()
            WHERE organization_id = %s AND is_active = TRUE
            RETURNING id
            """,
            (float(threshold), org_id),
        )
        updated = len(cur.fetchall() or [])
        db.commit()
    return {"success": True, "updated": updated, "threshold": float(threshold)}


# ---------------------------------------------------------------------------
# Phase 3 — CSV bulk upload (Excel deferred; Phase-1 said CSV-only)
# ---------------------------------------------------------------------------

# Column-name aliases the auto-detector tries when matching user CSV headers
# to the canonical design-watchlist field set.
_DWL_FIELD_ALIASES: Dict[str, List[str]] = {
    "product_name": [
        "product_name", "product name", "ürün adı", "urun adi",
        "tasarım adı", "tasarim adi", "name",
    ],
    "locarno_classes": [
        "locarno_classes", "locarno classes", "locarno",
        "locarno sınıfları", "locarno siniflari", "classes", "sınıflar",
    ],
    "description": ["description", "açıklama", "aciklama", "notes", "notlar"],
    "customer_application_no": [
        "customer_application_no", "application_no", "app no", "başvuru no",
        "basvuru no", "kendi başvuru no", "kendi basvuru no",
    ],
    "customer_registration_no": [
        "customer_registration_no", "registration_no", "tescil no", "kendi tescil no",
    ],
    "similarity_threshold": [
        "similarity_threshold", "threshold", "eşik", "esik",
    ],
    "priority": ["priority", "öncelik", "oncelik"],
    "tags": ["tags", "etiketler"],
    "alert_email": [
        "alert_email", "email_alert", "e-posta uyarı", "email", "mail",
    ],
    "alert_frequency": [
        "alert_frequency", "frequency", "bildirim sıklığı", "bildirim sikligi",
    ],
}


_DWL_TEMPLATE_HEADERS: List[str] = [
    "product_name",
    "locarno_classes",
    "description",
    "customer_application_no",
    "customer_registration_no",
    "similarity_threshold",
    "priority",
    "tags",
    "alert_email",
    "alert_frequency",
]


def build_design_csv_template() -> bytes:
    """Return a UTF-8 BOM CSV (Excel-friendly) containing only the header row
    plus a single example row. Used by GET /upload/template."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_DWL_TEMPLATE_HEADERS)
    writer.writerow([
        "Lamba",
        "26-05;26-04",
        "Modern masa lambası",
        "2024/00123",
        "",
        "0.7",
        "medium",
        "iç-mekan;aydınlatma",
        "true",
        "daily",
    ])
    # UTF-8 BOM so Excel opens Turkish chars correctly.
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _normalize_header(s: str) -> str:
    return (s or "").strip().lower().replace(" ", " ")


def _suggest_mapping(headers: List[str]) -> Dict[str, Optional[str]]:
    """For each canonical field, pick the first matching CSV header (or None)."""
    norm_headers = {_normalize_header(h): h for h in headers if h}
    out: Dict[str, Optional[str]] = {}
    for field, aliases in _DWL_FIELD_ALIASES.items():
        match: Optional[str] = None
        for alias in aliases:
            n = _normalize_header(alias)
            if n in norm_headers:
                match = norm_headers[n]
                break
        out[field] = match
    return out


def _decode_csv_bytes(content: bytes) -> str:
    """Decode an uploaded CSV file as UTF-8, stripping BOM if present.
    Falls back to latin-1 only if UTF-8 decoding fails."""
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1", errors="replace")


def detect_design_csv_columns(content: bytes) -> Dict[str, Any]:
    """Read uploaded CSV bytes and return columns + sample rows + a suggested
    field-to-header mapping. Used by POST /upload/detect-columns."""
    text = _decode_csv_bytes(content)
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if r]
    if not rows:
        return {"columns": [], "sample_rows": [], "total_rows": 0, "suggested_mapping": {}}
    headers = [c.strip() for c in rows[0]]
    sample_rows: List[Dict[str, str]] = []
    for r in rows[1:4]:  # up to 3 sample rows
        d: Dict[str, str] = {}
        for i, h in enumerate(headers):
            d[h] = r[i] if i < len(r) else ""
        sample_rows.append(d)
    return {
        "columns": headers,
        "sample_rows": sample_rows,
        "total_rows": max(0, len(rows) - 1),
        "suggested_mapping": _suggest_mapping(headers),
    }


def _coerce_bool(v) -> Optional[bool]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "evet", "y", "on"):
        return True
    if s in ("false", "0", "no", "hayır", "hayir", "n", "off"):
        return False
    return None


def _split_list(v) -> List[str]:
    """Split a free-text cell on `,` or `;` into a stripped non-empty list."""
    if not v:
        return []
    s = str(v)
    parts: List[str] = []
    for chunk in s.replace(";", ",").split(","):
        t = chunk.strip()
        if t:
            parts.append(t)
    return parts


def _coerce_threshold(v) -> Optional[float]:
    if v is None or str(v).strip() == "":
        return None
    try:
        f = float(str(v).strip().replace(",", "."))
    except ValueError:
        return None
    if 0.0 <= f <= 1.0:
        return f
    # accept percent-style values (50, 70) for usability
    if 0.0 <= f <= 100.0:
        return f / 100.0
    return None


def _row_to_create_payload(row: Dict[str, str], mapping: Dict[str, str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Project a CSV row through the mapping to a DesignWatchlistCreate-shape
    dict. Returns (payload, error). On error payload is None."""
    def _val(field: str) -> Optional[str]:
        col = mapping.get(field)
        if not col:
            return None
        v = row.get(col)
        return v.strip() if isinstance(v, str) else v

    name = (_val("product_name") or "").strip()
    if not name:
        return None, "product_name is required"
    payload: Dict[str, Any] = {"product_name": name}
    locarno = _split_list(_val("locarno_classes"))
    if locarno:
        payload["locarno_classes"] = locarno
    desc = _val("description")
    if desc:
        payload["description"] = desc
    app_no = _val("customer_application_no")
    if app_no:
        payload["customer_application_no"] = app_no
    reg_no = _val("customer_registration_no")
    if reg_no:
        payload["customer_registration_no"] = reg_no
    threshold = _coerce_threshold(_val("similarity_threshold"))
    if threshold is not None:
        payload["similarity_threshold"] = threshold
    priority = (_val("priority") or "").strip().lower()
    if priority in ("low", "medium", "high"):
        payload["priority"] = priority
    tags = _split_list(_val("tags"))
    if tags:
        payload["tags"] = tags
    alert_email = _coerce_bool(_val("alert_email"))
    if alert_email is not None:
        payload["alert_email"] = alert_email
    freq = (_val("alert_frequency") or "").strip().lower()
    if freq in ("immediate", "daily", "weekly"):
        payload["alert_frequency"] = freq
    return payload, None


def _existing_app_nos_for_org(cur, organization_id: str) -> set:
    cur.execute(
        """
        SELECT customer_application_no FROM design_watchlist_mt
        WHERE organization_id = %s AND customer_application_no IS NOT NULL
        """,
        (organization_id,),
    )
    out: set = set()
    for r in cur.fetchall() or []:
        v = r.get("customer_application_no") if isinstance(r, dict) else r[0]
        if v:
            out.add(str(v).strip())
    return out


def import_design_csv_with_mapping(
    *,
    content: bytes,
    column_mapping,
    current_user,
    db_factory=Database,
) -> Dict[str, Any]:
    """Apply ``column_mapping`` to the rows of an uploaded CSV and create
    design watchlist items. Mirrors trademark side semantics:
        * dedup by customer_application_no within the org
        * combined plan limit (trademark + design) via _check_watchlist_quota
        * returns {added, skipped, errors, total, limit_reached, scan_item_ids}
    """
    if isinstance(column_mapping, str):
        try:
            mapping = json.loads(column_mapping)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="column_mapping must be valid JSON",
            )
    else:
        mapping = dict(column_mapping or {})

    if not isinstance(mapping, dict) or not mapping:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="column_mapping must be a non-empty object",
        )

    text = _decode_csv_bytes(content)
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {"added": 0, "skipped": 0, "errors": 0, "total": 0,
                "limit_reached": False, "errors_detail": [], "scan_item_ids": []}

    org_id = str(current_user.organization_id)
    plan_limit: Optional[int] = None
    current_count = 0
    with db_factory() as db:
        # Determine plan capacity using the same combined trademark+design
        # quota that single-add already enforces.
        from utils.subscription import get_plan_limit, get_user_plan
        plan_info = get_user_plan(db, str(current_user.id))
        plan_name = plan_info.get("plan_name", "free")
        plan_limit = int(get_plan_limit(plan_name, "max_watchlist_items") or 0)
        cur = db.cursor()
        current_count = _combined_watchlist_count(cur, current_user.organization_id)
        existing_app_nos = _existing_app_nos_for_org(cur, org_id)

        added = 0
        skipped = 0
        errors_detail: List[Dict[str, Any]] = []
        limit_reached = False
        scan_item_ids: List[str] = []
        # Use a single connection: insert items in one transaction.
        for idx, row in enumerate(rows, start=2):  # row 1 is the header
            payload, err = _row_to_create_payload(row, mapping)
            if err:
                errors_detail.append({"row": idx, "error": err})
                continue
            app_no = (payload.get("customer_application_no") or "").strip()
            if app_no and app_no in existing_app_nos:
                skipped += 1
                continue
            if plan_limit and current_count >= plan_limit:
                limit_reached = True
                break

            cur.execute(
                """
                INSERT INTO design_watchlist_mt (
                    organization_id, user_id, product_name, locarno_classes,
                    description, customer_application_no, customer_registration_no,
                    similarity_threshold, priority, tags, alert_email,
                    alert_frequency
                ) VALUES (
                    %(org)s, %(uid)s, %(product_name)s, %(locarno)s,
                    %(description)s, %(app_no)s, %(reg_no)s,
                    %(threshold)s, %(priority)s, %(tags)s, %(alert_email)s,
                    %(alert_frequency)s
                )
                RETURNING id
                """,
                {
                    "org": org_id,
                    "uid": str(current_user.id),
                    "product_name": payload["product_name"],
                    "locarno": payload.get("locarno_classes") or [],
                    "description": payload.get("description"),
                    "app_no": payload.get("customer_application_no"),
                    "reg_no": payload.get("customer_registration_no"),
                    "threshold": payload.get("similarity_threshold", 0.5),
                    "priority": payload.get("priority", "medium"),
                    "tags": payload.get("tags") or [],
                    "alert_email": payload.get("alert_email", True),
                    "alert_frequency": payload.get("alert_frequency", "daily"),
                },
            )
            new_row = cur.fetchone()
            if new_row:
                new_id = new_row.get("id") if isinstance(new_row, dict) else new_row[0]
                if new_id:
                    scan_item_ids.append(str(new_id))
                    if app_no:
                        existing_app_nos.add(app_no)
                    added += 1
                    current_count += 1
        db.commit()

    return {
        "added": added,
        "skipped": skipped,
        "errors": len(errors_detail),
        "total": len(rows),
        "limit_reached": limit_reached,
        "errors_detail": errors_detail[:25],
        "scan_item_ids": scan_item_ids,
        "plan_limit": plan_limit,
        "current_count": current_count,
    }


# ---------------------------------------------------------------------------
# Bulk import from a holder's design portfolio. Mirrors the trademark
# watchlist /bulk-from-portfolio endpoint: pulls the holder's full
# design list, calls create_design_watchlist_item per row (which
# handles dedupe, plan quota, and reference-design embedding clone),
# and returns the {added/skipped/errors/scan_item_ids} shape.
# ---------------------------------------------------------------------------

async def import_design_watchlist_from_portfolio(
    *,
    holder_id: str,
    current_user,
    db_factory=Database,
) -> Dict[str, Any]:
    if not holder_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="holder_id is required",
        )

    # PRO gate (mirrors trademark portfolio access policy).
    from utils.subscription import get_plan_limit, get_user_plan

    with db_factory() as db:
        plan = get_user_plan(db, str(current_user.id))
        if not get_plan_limit(plan["plan_name"], "can_view_holder_portfolio"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "pro_feature",
                    "message": "Sahip portföyü PRO özelliğidir",
                    "upgrade_url": "/pricing",
                },
            )

        cur = db.cursor()
        cur.execute(
            "SELECT id, name FROM holders WHERE tpe_client_id = %s LIMIT 1",
            (str(holder_id).strip(),),
        )
        h = cur.fetchone()
        if not h:
            raise HTTPException(status_code=404, detail="Holder not found")

        cur.execute(
            """
            SELECT d.id::text AS design_id,
                   d.application_no,
                   d.product_name_tr, d.product_name_en,
                   d.locarno_classes
            FROM designs d
            WHERE d.holder_id = %s
            ORDER BY d.application_date DESC NULLS LAST, d.application_no DESC
            """,
            (h["id"],),
        )
        rows = cur.fetchall()

    if not rows:
        return {
            "created": 0, "skipped": 0, "errors": 0, "total": 0,
            "limit_reached": False, "errors_detail": [], "scan_item_ids": [],
        }

    created = 0
    skipped = 0
    errors_detail: List[Dict[str, Any]] = []
    limit_reached = False
    scan_item_ids: List[str] = []

    for d in rows:
        product_name = (
            d.get("product_name_tr") or d.get("product_name_en")
            or d.get("application_no") or ""
        )
        if not product_name:
            errors_detail.append({"application_no": d.get("application_no"), "reason": "missing_product_name"})
            continue

        payload = {
            "product_name": product_name,
            "customer_application_no": d.get("application_no"),
            "locarno_classes": list(d.get("locarno_classes") or []),
            "reference_design_id": d["design_id"],
        }
        try:
            item = create_design_watchlist_item(
                data=payload, current_user=current_user, db_factory=db_factory,
            )
            new_id = item.get("id") if isinstance(item, dict) else None
            if new_id:
                scan_item_ids.append(str(new_id))
                created += 1
        except HTTPException as exc:
            # 409 = duplicate (already watching); 402/403 = plan/quota
            # limit reached → stop trying further inserts so the caller
            # can surface "limit reached" to the user.
            if exc.status_code == status.HTTP_409_CONFLICT:
                skipped += 1
            elif exc.status_code in (402, 403):
                limit_reached = True
                errors_detail.append({
                    "application_no": d.get("application_no"),
                    "reason": "plan_limit_reached",
                })
                break
            else:
                errors_detail.append({
                    "application_no": d.get("application_no"),
                    "reason": str(exc.detail)[:200],
                })

    return {
        # Field name mirrors the trademark watchlist bulk service so
        # the shared bulk-confirm modal in _modals.html can read
        # data.created without branching on registry.
        "created": created,
        "skipped": skipped,
        "errors": len(errors_detail),
        "total": len(rows),
        "limit_reached": limit_reached,
        "errors_detail": errors_detail[:25],
        "scan_item_ids": scan_item_ids,
    }

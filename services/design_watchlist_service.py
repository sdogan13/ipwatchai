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

import json
import logging
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

import psycopg2
import psycopg2.extras
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
    cur.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM watchlist_mt
             WHERE organization_id = %(org)s AND is_active = TRUE)
          + (SELECT COUNT(*) FROM design_watchlist_mt
             WHERE organization_id = %(org)s AND is_active = TRUE)
            AS total
        """,
        {"org": str(organization_id)},
    )
    row = cur.fetchone()
    if row is None:
        return 0
    return int(row.get("total") if isinstance(row, dict) else row[0])


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

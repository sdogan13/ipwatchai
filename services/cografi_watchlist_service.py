"""Coğrafi İşaret watchlist service.

Sister to ``services/patent_watchlist_service.py`` and
``services/design_watchlist_service.py``. CRUD over
``cografi_watchlist_mt`` with four watch types:

  * ``holder``    — alert on every new GI by a watched applicant
                    (matched by holder_id or denormalised holder_name
                    + tpe_client_id).
  * ``reference`` — semantic similarity vs a reference cografi record
                    or free-text query (reference_embedding cloned
                    from cografi_records.text_embedding at
                    watchlist-create time when reference_record_id
                    is given).
  * ``region``    — NEW for cografi; trigram + ANY-match against
                    cografi_records.geographical_boundary.
  * ``lifecycle`` — NEW; tracks an existing registration_no across
                    art42 change requests / art42 finalized changes /
                    corrections.

Quota: counts cografi alongside trademarks + designs + patents in a
single shared bucket, so the user's plan limit covers all four
registries.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from fastapi import HTTPException, status

from database.crud import Database


logger = logging.getLogger("turkpatent.cografi_watchlist")

WATCH_TYPES = ("holder", "reference", "region", "lifecycle")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def to_halfvec_literal(values: Optional[Sequence[float]]) -> Optional[str]:
    if values is None:
        return None
    materialized = list(values)
    if not materialized:
        return None
    return "[" + ",".join(f"{float(v):.6f}" for v in materialized) + "]"


def _normalize_str_list(
    values: Optional[Sequence[str]], *, upper: bool = False, lower: bool = False,
) -> List[str]:
    if not values:
        return []
    out: List[str] = []
    for v in values:
        if not v:
            continue
        s = v.strip()
        if upper:
            s = s.upper()
        elif lower:
            s = s.lower()
        if s and s not in out:
            out.append(s)
    return out


def _row_to_dict(row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# Quota — shared bucket across all four registries
# ---------------------------------------------------------------------------

def combined_watchlist_count(cur, organization_id: UUID | str) -> int:
    """Active watchlist count across trademarks + designs + patents + cografi."""
    cur.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM watchlist_mt
             WHERE organization_id = %(org)s AND is_active = TRUE)
          + (SELECT COUNT(*) FROM design_watchlist_mt
             WHERE organization_id = %(org)s AND is_active = TRUE)
          + (SELECT COUNT(*) FROM patent_watchlist_mt
             WHERE organization_id = %(org)s AND is_active = TRUE)
          + (SELECT COUNT(*) FROM cografi_watchlist_mt
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
    """Raise 403 if combined watchlist count would exceed plan limit."""
    from utils.subscription import get_plan_limit, get_user_plan

    plan_info = get_user_plan(db, str(current_user.id))
    plan_name = plan_info.get("plan_name", "free")
    max_items = get_plan_limit(plan_name, "max_watchlist_items")
    cur = db.cursor()
    current_count = combined_watchlist_count(cur, current_user.organization_id)
    if current_count >= max_items:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "limit_exceeded",
                "message": (
                    f"İzleme listesi limitinize ulaştınız ({max_items}). "
                    "Daha fazla eklemek için planınızı yükseltin."
                ),
                "current_count": current_count,
                "max_items": max_items,
                "plan_name": plan_name,
            },
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_holder_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    holder_name = (data.get("holder_name") or "").strip() or None
    holder_id = data.get("holder_id")
    holder_tpe = (data.get("holder_tpe_client_id") or "").strip() or None
    if not (holder_name or holder_id or holder_tpe):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Holder watch requires holder_name, holder_id, or holder_tpe_client_id",
        )
    return {
        "holder_name": holder_name,
        "holder_id": str(holder_id) if holder_id else None,
        "holder_tpe_client_id": holder_tpe,
    }


def _validate_reference_payload(data: Dict[str, Any], *, db) -> Dict[str, Any]:
    """Resolve reference_* fields. If reference_record_id is set, clone the
    record's text_embedding into reference_embedding so scan-time
    retrieval is a pure cosine query against cografi_records.
    Otherwise reference_query (free-text) is required and the embedding
    stays NULL until the route layer computes it (the route module owns
    the e5 model loader)."""
    ref_record_id = data.get("reference_record_id")
    ref_query = (data.get("reference_query") or "").strip() or None
    ref_embedding = data.get("reference_embedding")
    if not (ref_record_id or ref_query):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reference watch requires reference_record_id or reference_query",
        )

    cloned_embedding_literal: Optional[str] = None
    if ref_record_id:
        cur = db.cursor()
        cur.execute(
            """
            SELECT text_embedding::text AS emb,
                   COALESCE(NULLIF(name, ''), application_no, registration_no::text) AS title_or_id
            FROM cografi_records
            WHERE id = %s
            """,
            (str(ref_record_id),),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="reference_record_id not found",
            )
        cloned_embedding_literal = (row.get("emb") if isinstance(row, dict) else row[0])
    elif ref_embedding:
        cloned_embedding_literal = to_halfvec_literal(ref_embedding)

    return {
        "reference_record_id": str(ref_record_id) if ref_record_id else None,
        "reference_query": ref_query,
        "reference_embedding": cloned_embedding_literal,
    }


def _validate_region_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    region_query = (data.get("region_query") or "").strip() or None
    region_terms = _normalize_str_list(data.get("region_terms"))
    if not (region_query or region_terms):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Region watch requires region_query or non-empty region_terms",
        )
    return {"region_query": region_query, "region_terms": region_terms}


def _validate_lifecycle_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    raw = data.get("lifecycle_registration_no")
    try:
        reg_no = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="lifecycle_registration_no must be an integer",
        )
    if not reg_no or reg_no < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lifecycle watch requires a positive lifecycle_registration_no",
        )
    return {"lifecycle_registration_no": reg_no}


# ---------------------------------------------------------------------------
# Service methods
# ---------------------------------------------------------------------------

# Halfvec column needs explicit ::halfvec cast on INSERT/UPDATE.
_HALFVEC_COLS = frozenset({"reference_embedding"})

INSERT_COLS = (
    "organization_id", "user_id", "watch_type", "label", "description",
    "holder_name", "holder_id", "holder_tpe_client_id",
    "reference_record_id", "reference_query", "reference_embedding",
    "region_query", "region_terms",
    "lifecycle_registration_no",
    "section_keys", "record_types", "gi_type",
    "customer_application_no", "customer_registration_no",
    "similarity_threshold", "alert_email", "alert_webhook", "webhook_url", "alert_frequency",
    "tags", "priority",
)


def _build_insert_sql() -> str:
    placeholders = []
    for c in INSERT_COLS:
        if c in _HALFVEC_COLS:
            placeholders.append("%s::halfvec")
        else:
            placeholders.append("%s")
    return (
        f"INSERT INTO cografi_watchlist_mt (\n        " + ",\n        ".join(INSERT_COLS) + ")\n"
        f"VALUES (" + ", ".join(placeholders) + ")\n"
        f"RETURNING id"
    )


_INSERT_SQL = _build_insert_sql()


def create_cografi_watchlist_item(
    *,
    data: Dict[str, Any],
    current_user,
    db_factory=Database,
) -> Dict[str, Any]:
    """Create a cografi watchlist item. Routes the payload through the
    appropriate validator based on ``watch_type``. Returns the inserted row."""
    watch_type = (data.get("watch_type") or "").strip().lower()
    if watch_type not in WATCH_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"watch_type must be one of {WATCH_TYPES}",
        )
    label = (data.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="label is required")

    section_keys = _normalize_str_list(data.get("section_keys"), lower=True)
    record_types = _normalize_str_list(data.get("record_types"), upper=True)
    gi_type = (data.get("gi_type") or "").strip() or None
    customer_app_no = (data.get("customer_application_no") or "").strip() or None
    customer_reg_no = data.get("customer_registration_no")
    if customer_reg_no is not None:
        try:
            customer_reg_no = int(customer_reg_no)
        except (TypeError, ValueError):
            customer_reg_no = None

    with db_factory() as db:
        _check_watchlist_quota(db, current_user)

        # Per-watch_type field projections.
        type_fields: Dict[str, Any] = {
            "holder_name": None, "holder_id": None, "holder_tpe_client_id": None,
            "reference_record_id": None, "reference_query": None, "reference_embedding": None,
            "region_query": None, "region_terms": [],
            "lifecycle_registration_no": None,
        }
        if watch_type == "holder":
            type_fields.update(_validate_holder_payload(data))
        elif watch_type == "reference":
            type_fields.update(_validate_reference_payload(data, db=db))
        elif watch_type == "region":
            type_fields.update(_validate_region_payload(data))
        elif watch_type == "lifecycle":
            type_fields.update(_validate_lifecycle_payload(data))

        alert_webhook = bool(data.get("alert_webhook"))
        webhook_url = (data.get("webhook_url") or "").strip() or None
        if alert_webhook and not webhook_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="webhook_url is required when alert_webhook=true",
            )

        sim_threshold = float(data.get("similarity_threshold") or 0.5)
        if not 0.0 <= sim_threshold <= 1.0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="similarity_threshold must be between 0 and 1",
            )

        alert_frequency = (data.get("alert_frequency") or "daily").strip().lower()
        if alert_frequency not in ("immediate", "daily", "weekly"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="alert_frequency must be immediate|daily|weekly",
            )

        priority = (data.get("priority") or "medium").strip().lower()
        if priority not in ("low", "medium", "high"):
            priority = "medium"

        values = (
            str(current_user.organization_id),
            str(current_user.id),
            watch_type,
            label,
            (data.get("description") or "").strip() or None,
            type_fields["holder_name"],
            type_fields["holder_id"],
            type_fields["holder_tpe_client_id"],
            type_fields["reference_record_id"],
            type_fields["reference_query"],
            type_fields["reference_embedding"],
            type_fields["region_query"],
            type_fields["region_terms"],
            type_fields["lifecycle_registration_no"],
            section_keys,
            record_types,
            gi_type,
            customer_app_no,
            customer_reg_no,
            sim_threshold,
            bool(data.get("alert_email", True)),
            alert_webhook,
            webhook_url,
            alert_frequency,
            _normalize_str_list(data.get("tags")),
            priority,
        )
        cur = db.cursor()
        cur.execute(_INSERT_SQL, values)
        new_id = cur.fetchone()
        new_id = new_id["id"] if isinstance(new_id, dict) else new_id[0]
        db.commit()

    return get_cografi_watchlist_item(item_id=new_id, current_user=current_user, db_factory=db_factory)


def get_cografi_watchlist_item(*, item_id: UUID | str, current_user, db_factory=Database) -> Dict[str, Any]:
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, organization_id, user_id, watch_type, label, description,
                   holder_name, holder_id, holder_tpe_client_id,
                   reference_record_id, reference_query,
                   region_query, region_terms,
                   lifecycle_registration_no,
                   section_keys, record_types, gi_type,
                   customer_application_no, customer_registration_no,
                   similarity_threshold, alert_email, alert_webhook, webhook_url, alert_frequency,
                   tags, priority, is_active, last_scan_at, created_at, updated_at
            FROM cografi_watchlist_mt
            WHERE id = %s AND organization_id = %s
            """,
            (str(item_id), str(current_user.organization_id)),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    return _row_to_dict(row)


def list_cografi_watchlist_items(
    *,
    current_user,
    is_active: Optional[bool] = None,
    watch_type: Optional[str] = None,
    db_factory=Database,
) -> List[Dict[str, Any]]:
    parts = ["organization_id = %s"]
    params: List[Any] = [str(current_user.organization_id)]
    if is_active is not None:
        parts.append("is_active = %s")
        params.append(bool(is_active))
    if watch_type:
        parts.append("watch_type = %s")
        params.append(watch_type.strip().lower())
    where = " AND ".join(parts)
    sql = f"""
        SELECT id, watch_type, label, description,
               holder_name, region_query, region_terms,
               lifecycle_registration_no,
               section_keys, record_types, gi_type,
               similarity_threshold, alert_email, alert_webhook, alert_frequency,
               tags, priority, is_active, last_scan_at, created_at, updated_at
        FROM cografi_watchlist_mt
        WHERE {where}
        ORDER BY created_at DESC
    """
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(sql, params)
        return [_row_to_dict(r) for r in cur.fetchall()]


_MUTABLE_COLS = (
    "label", "description", "section_keys", "record_types", "gi_type",
    "customer_application_no", "customer_registration_no",
    "similarity_threshold", "alert_email", "alert_webhook", "webhook_url",
    "alert_frequency", "tags", "priority", "is_active",
)


def update_cografi_watchlist_item(
    *,
    item_id: UUID | str,
    data: Dict[str, Any],
    current_user,
    db_factory=Database,
) -> Dict[str, Any]:
    """Update mutable fields. Watch-type-discriminating columns
    (holder_*, reference_*, region_*, lifecycle_*) are intentionally NOT
    mutable here — change-of-target requires deleting + recreating to
    avoid lifecycle-state confusion."""
    sets = []
    values: List[Any] = []
    for col in _MUTABLE_COLS:
        if col in data:
            sets.append(f"{col} = %s")
            values.append(data[col])
    if not sets:
        return get_cografi_watchlist_item(item_id=item_id, current_user=current_user, db_factory=db_factory)
    sets.append("updated_at = NOW()")
    values.append(str(item_id))
    values.append(str(current_user.organization_id))
    sql = f"""
        UPDATE cografi_watchlist_mt
        SET {', '.join(sets)}
        WHERE id = %s AND organization_id = %s
        RETURNING id
    """
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(sql, values)
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Watchlist item not found")
        db.commit()
    return get_cografi_watchlist_item(item_id=item_id, current_user=current_user, db_factory=db_factory)


def delete_cografi_watchlist_item(
    *, item_id: UUID | str, current_user, db_factory=Database,
) -> Dict[str, Any]:
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            "DELETE FROM cografi_watchlist_mt WHERE id = %s AND organization_id = %s RETURNING id",
            (str(item_id), str(current_user.organization_id)),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Watchlist item not found")
        db.commit()
    return {"deleted": True, "id": str(item_id)}


# ---------------------------------------------------------------------------
# Scanner-side helpers
# ---------------------------------------------------------------------------

def get_active_cografi_watchlist_items(*, db) -> List[Dict[str, Any]]:
    """Return all active items with their reference_embedding rendered as
    a list[float] so the scanner can ship it back to a halfvec query
    without going through the full halfvec round-trip."""
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, organization_id, user_id, watch_type, label,
               holder_name, holder_id, holder_tpe_client_id,
               reference_record_id, reference_query,
               reference_embedding::text AS reference_embedding_text,
               region_query, region_terms,
               lifecycle_registration_no,
               section_keys, record_types, gi_type,
               customer_application_no, customer_registration_no,
               similarity_threshold,
               alert_email, alert_webhook, webhook_url
        FROM cografi_watchlist_mt
        WHERE is_active = TRUE
        """,
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


def update_last_scan_at(*, item_id: UUID | str, db) -> None:
    cur = db.cursor()
    cur.execute(
        "UPDATE cografi_watchlist_mt SET last_scan_at = NOW() WHERE id = %s",
        (str(item_id),),
    )

"""Patent / Faydalı Model watchlist service.

Sister to ``services/design_watchlist_service.py``. CRUD over
``patent_watchlist_mt`` with two watch types:

  * ``holder``    — competitor tracking. The user identifies a holder
                    (preferably by ``holder_id`` or ``tpe_client_id``;
                    free-text ``holder_name`` is the fallback). Scanner
                    alerts on every new patent matching the holder.
  * ``reference`` — prior-art / similarity monitoring. The user pastes
                    a reference (an existing ``patents.id``, a free-text
                    query, or both). Scanner alerts on text+embedding
                    similarity.

Quota: shares ``subscription_plans.max_watchlist_items`` with the
trademark and design watchlists — a single bucket covers all three
registries.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from fastapi import HTTPException, status

from database.crud import Database


logger = logging.getLogger("turkpatent.patent_watchlist")

WATCH_TYPES = ("holder", "reference")


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


def _normalize_str_list(values: Optional[Sequence[str]], *, upper: bool = False) -> List[str]:
    if not values:
        return []
    out: List[str] = []
    for v in values:
        if not v:
            continue
        s = v.strip()
        if upper:
            s = s.upper()
        if s and s not in out:
            out.append(s)
    return out


def _row_to_dict(row) -> Dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    return dict(row)  # psycopg2 RealDictRow already mapping-like


# ---------------------------------------------------------------------------
# Quota — shared trademark + design + patent bucket
# ---------------------------------------------------------------------------

def combined_watchlist_count(cur, organization_id: UUID | str) -> int:
    """Return active watchlist item count across trademarks + designs + patents.

    Single source of truth for the cross-registry quota. The trademark and
    design services delegate to this helper so the shared
    ``max_watchlist_items`` bucket is consistent regardless of which
    surface created the row.
    """
    cur.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM watchlist_mt
             WHERE organization_id = %(org)s AND is_active = TRUE)
          + (SELECT COUNT(*) FROM design_watchlist_mt
             WHERE organization_id = %(org)s AND is_active = TRUE)
          + (SELECT COUNT(*) FROM patent_watchlist_mt
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
                    f"Izleme listesi limitinize ulastiniz ({max_items}). "
                    "Daha fazla eklemek icin planinizi yukseltin."
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
    """Resolve reference fields. If reference_patent_id is set, clone the
    patent's title_abstract_embedding into reference_embedding. Otherwise
    reference_query (free-text) is required and the embedding stays NULL
    until the route layer computes it (the patent_search_routes module
    owns the e5 model loader)."""
    ref_patent_id = data.get("reference_patent_id")
    ref_query = (data.get("reference_query") or "").strip() or None
    ref_embedding = data.get("reference_embedding")
    if not (ref_patent_id or ref_query):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reference watch requires reference_patent_id or reference_query",
        )

    cloned_embedding_literal: Optional[str] = None
    if ref_patent_id:
        cur = db.cursor()
        cur.execute(
            """
            SELECT title_abstract_embedding::text AS emb,
                   COALESCE(NULLIF(title, ''), publication_no) AS title_or_pub
            FROM patents
            WHERE id = %s
            """,
            (str(ref_patent_id),),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="reference_patent_id not found",
            )
        cloned_embedding_literal = (row.get("emb") if isinstance(row, dict) else row[0])
    elif ref_embedding:
        cloned_embedding_literal = to_halfvec_literal(ref_embedding)

    return {
        "reference_patent_id": str(ref_patent_id) if ref_patent_id else None,
        "reference_query": ref_query,
        "reference_embedding": cloned_embedding_literal,
    }


# ---------------------------------------------------------------------------
# Service methods
# ---------------------------------------------------------------------------

INSERT_COLS = (
    "organization_id, user_id, watch_type, label, description, "
    "holder_name, holder_id, holder_tpe_client_id, "
    "reference_patent_id, reference_query, reference_embedding, "
    "ipc_classes, kind_codes, customer_application_no, "
    "similarity_threshold, alert_email, alert_webhook, webhook_url, alert_frequency, "
    "tags, priority"
)


def create_patent_watchlist_item(
    *,
    data: Dict[str, Any],
    current_user,
    db_factory=Database,
) -> Dict[str, Any]:
    """Create a patent watchlist item. Routes the payload through the
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

    ipc = _normalize_str_list(data.get("ipc_classes"), upper=True)
    kinds = _normalize_str_list(data.get("kind_codes"), upper=True)
    customer_app_no = (data.get("customer_application_no") or "").strip() or None

    with db_factory() as db:
        _check_watchlist_quota(db, current_user)

        # Type-specific validation + (for reference) clone embedding from patent row
        type_fields: Dict[str, Any] = {
            "holder_name": None, "holder_id": None, "holder_tpe_client_id": None,
            "reference_patent_id": None, "reference_query": None, "reference_embedding": None,
        }
        if watch_type == "holder":
            type_fields.update(_validate_holder_payload(data))
        else:
            type_fields.update(_validate_reference_payload(data, db=db))

        cur = db.cursor()

        # Dedupe: same org + same identity + active = conflict
        if watch_type == "holder" and (type_fields["holder_id"] or type_fields["holder_tpe_client_id"]):
            cur.execute(
                """
                SELECT 1 FROM patent_watchlist_mt
                WHERE organization_id = %s AND watch_type = 'holder' AND is_active = TRUE
                  AND (
                    (holder_id IS NOT NULL AND holder_id = %s)
                    OR (holder_tpe_client_id IS NOT NULL AND holder_tpe_client_id = %s)
                  )
                LIMIT 1
                """,
                (
                    str(current_user.organization_id),
                    type_fields["holder_id"],
                    type_fields["holder_tpe_client_id"],
                ),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Bu hak sahibi zaten takipte",
                )
        elif watch_type == "reference" and type_fields["reference_patent_id"]:
            cur.execute(
                """
                SELECT 1 FROM patent_watchlist_mt
                WHERE organization_id = %s AND watch_type = 'reference' AND is_active = TRUE
                  AND reference_patent_id = %s
                LIMIT 1
                """,
                (str(current_user.organization_id), type_fields["reference_patent_id"]),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Bu referans patent zaten takipte",
                )

        params = {
            "organization_id": str(current_user.organization_id),
            "user_id": str(current_user.id),
            "watch_type": watch_type,
            "label": label[:500],
            "description": data.get("description"),
            "holder_name": type_fields["holder_name"],
            "holder_id": type_fields["holder_id"],
            "holder_tpe_client_id": type_fields["holder_tpe_client_id"],
            "reference_patent_id": type_fields["reference_patent_id"],
            "reference_query": type_fields["reference_query"],
            "reference_embedding": type_fields["reference_embedding"],
            "ipc_classes": ipc,
            "kind_codes": kinds,
            "customer_application_no": customer_app_no,
            "similarity_threshold": float(data.get("similarity_threshold") or 0.50),
            "alert_email": bool(data.get("alert_email", True)),
            "alert_webhook": bool(data.get("alert_webhook", False)),
            "webhook_url": data.get("webhook_url"),
            "alert_frequency": data.get("alert_frequency") or "daily",
            "tags": list(data.get("tags") or []),
            "priority": data.get("priority") or "medium",
        }

        cur.execute(
            f"""
            INSERT INTO patent_watchlist_mt ({INSERT_COLS})
            VALUES (
                %(organization_id)s, %(user_id)s, %(watch_type)s, %(label)s, %(description)s,
                %(holder_name)s, %(holder_id)s, %(holder_tpe_client_id)s,
                %(reference_patent_id)s, %(reference_query)s,
                CASE WHEN %(reference_embedding)s IS NULL THEN NULL
                     ELSE %(reference_embedding)s::halfvec END,
                %(ipc_classes)s, %(kind_codes)s, %(customer_application_no)s,
                %(similarity_threshold)s, %(alert_email)s, %(alert_webhook)s, %(webhook_url)s, %(alert_frequency)s,
                %(tags)s, %(priority)s
            )
            RETURNING *
            """,
            params,
        )
        row = cur.fetchone()
        db.commit()
        return _row_to_dict(row)


def get_patent_watchlist_item(*, item_id: UUID, current_user, db_factory=Database) -> Dict[str, Any]:
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT * FROM patent_watchlist_mt
            WHERE id = %s AND organization_id = %s
            """,
            (str(item_id), str(current_user.organization_id)),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Watchlist item not found")
        return _row_to_dict(row)


def list_patent_watchlist_items(
    *,
    current_user,
    watch_type: Optional[str] = None,
    is_active: Optional[bool] = True,
    limit: int = 50,
    offset: int = 0,
    db_factory=Database,
) -> Dict[str, Any]:
    where = ["organization_id = %(org)s"]
    params: Dict[str, Any] = {"org": str(current_user.organization_id)}
    if watch_type:
        if watch_type not in WATCH_TYPES:
            raise HTTPException(status_code=400, detail="invalid watch_type filter")
        where.append("watch_type = %(wt)s")
        params["wt"] = watch_type
    if is_active is not None:
        where.append("is_active = %(active)s")
        params["active"] = bool(is_active)
    params["limit"] = max(1, min(int(limit or 50), 200))
    params["offset"] = max(0, int(offset or 0))

    where_sql = " AND ".join(where)
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT id, watch_type, label, description,
                   holder_name, holder_id, holder_tpe_client_id,
                   reference_patent_id, reference_query,
                   ipc_classes, kind_codes, customer_application_no,
                   similarity_threshold, alert_email, alert_frequency,
                   tags, priority, is_active, last_scan_at, created_at, updated_at
            FROM patent_watchlist_mt
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        rows = cur.fetchall()
        cur.execute(
            f"SELECT COUNT(*) AS total FROM patent_watchlist_mt WHERE {where_sql}",
            params,
        )
        total_row = cur.fetchone()
    total = int(total_row.get("total") if isinstance(total_row, dict) else total_row[0])
    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": total,
        "limit": params["limit"],
        "offset": params["offset"],
    }


# Allowed UPDATE columns. Watch-type fields aren't updatable post-create; the
# user should delete + recreate to switch a holder watch into a reference watch.
UPDATABLE_FIELDS = (
    "label", "description",
    "ipc_classes", "kind_codes", "customer_application_no",
    "similarity_threshold", "alert_email", "alert_webhook", "webhook_url",
    "alert_frequency", "tags", "priority", "is_active",
)


def update_patent_watchlist_item(
    *,
    item_id: UUID,
    data: Dict[str, Any],
    current_user,
    db_factory=Database,
) -> Dict[str, Any]:
    sets: List[str] = []
    params: Dict[str, Any] = {
        "id": str(item_id),
        "org": str(current_user.organization_id),
    }
    for field in UPDATABLE_FIELDS:
        if field in data:
            sets.append(f"{field} = %({field})s")
            params[field] = data[field]
    if not sets:
        raise HTTPException(status_code=400, detail="No updatable fields provided")
    sets.append("updated_at = NOW()")
    sql = f"""
        UPDATE patent_watchlist_mt
        SET {", ".join(sets)}
        WHERE id = %(id)s AND organization_id = %(org)s
        RETURNING *
    """
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Watchlist item not found")
        db.commit()
        return _row_to_dict(row)


def delete_patent_watchlist_item(*, item_id: UUID, current_user, db_factory=Database) -> Dict[str, Any]:
    """Hard delete. Cascades to patent_alerts_mt via FK ON DELETE CASCADE."""
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            DELETE FROM patent_watchlist_mt
            WHERE id = %s AND organization_id = %s
            RETURNING id
            """,
            (str(item_id), str(current_user.organization_id)),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Watchlist item not found")
        db.commit()
        return {"id": str(item_id), "deleted": True}


# ---------------------------------------------------------------------------
# Scanner-side helpers (used by patent_scanner_service.py — next commit)
# ---------------------------------------------------------------------------

def get_active_patent_watchlist_items(*, db) -> List[Dict[str, Any]]:
    """Return ALL active watchlist items across all organizations.

    Used by the post-ingest scanner hook. Includes the reference_embedding
    cast to text for downstream cosine queries.
    """
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, organization_id, user_id, watch_type, label,
               holder_name, holder_id, holder_tpe_client_id,
               reference_patent_id, reference_query,
               reference_embedding::text AS reference_embedding,
               ipc_classes, kind_codes, customer_application_no,
               similarity_threshold
        FROM patent_watchlist_mt
        WHERE is_active = TRUE
        """
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


def update_last_scan_at(*, item_id: UUID | str, db) -> None:
    cur = db.cursor()
    cur.execute(
        "UPDATE patent_watchlist_mt SET last_scan_at = NOW() WHERE id = %s",
        (str(item_id),),
    )
    db.commit()

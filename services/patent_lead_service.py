"""Patent / Faydalı Model lead service.

Sister to ``services/lead_service.py`` (trademarks). Patent leads are
DERIVED from ``patent_events`` rows on-the-fly — there's no separate
``patent_leads_mt`` table. Each lead category maps to one or more
event_type values:

  * ``lapse``      — GRANT_FEE_LAPSE + APPLICATION_FEE_LAPSE
                     (patent abandoned / annuity not paid; acquisition
                     opportunity)
  * ``transfer``   — ASSIGNMENT_RECORDED
                     (ownership transfer; signals IP firm change)
  * ``license``    — LICENSE_OFFER
                     (owner is publicly seeking licensees)
  * ``rejected``   — APPLICATION_LAPSED_OR_REJECTED + APPLICATION_REJECTED
                     (application failed; tech may be free to use)

All queries scope by organization_id where applicable for billing /
quota; the underlying patent_events corpus is shared across tenants
(same data the search/watchlist surfaces use).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from fastapi import HTTPException, status

from database.crud import Database


logger = logging.getLogger("turkpatent.patent_leads")


# ---------------------------------------------------------------------------
# Category → event_type mapping
# ---------------------------------------------------------------------------

LEAD_CATEGORIES: Dict[str, Sequence[str]] = {
    "lapse":    ("GRANT_FEE_LAPSE", "APPLICATION_FEE_LAPSE"),
    "transfer": ("ASSIGNMENT_RECORDED",),
    "license":  ("LICENSE_OFFER",),
    "rejected": ("APPLICATION_LAPSED_OR_REJECTED", "APPLICATION_REJECTED"),
}


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

HYDRATE_COLS = (
    "e.id::text AS event_id, "
    "e.event_type, e.event_date, e.bulletin_no, e.bulletin_date, "
    "e.application_no, e.publication_no, e.free_text AS event_text, "
    "p.id::text AS patent_id, p.title, p.abstract, p.kind_code, "
    "p.record_type, p.ipc_classes, "
    "p.application_date, p.publication_date, p.grant_date, "
    "(SELECT ph.name FROM patent_holders ph "
    " WHERE ph.patent_id = p.id ORDER BY ph.seq ASC LIMIT 1) AS holder_name, "
    "(SELECT ph.country FROM patent_holders ph "
    " WHERE ph.patent_id = p.id ORDER BY ph.seq ASC LIMIT 1) AS holder_country, "
    "(SELECT h.tpe_client_id FROM patent_holders ph "
    " LEFT JOIN holders h ON h.id = ph.holder_id "
    " WHERE ph.patent_id = p.id ORDER BY ph.seq ASC LIMIT 1) AS holder_tpe_client_id"
)


def _serialize_lead(row: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize one DB row into the response shape. Idempotent — safe
    to call on already-dict rows from psycopg2 RealDictCursor."""
    record_type = row.get("record_type")
    if hasattr(record_type, "value"):
        record_type = record_type.value
    holder = None
    if row.get("holder_name"):
        holder = {
            "name": row.get("holder_name"),
            "country": row.get("holder_country"),
            "tpe_client_id": row.get("holder_tpe_client_id"),
        }

    def _iso(d):
        return d.isoformat() if d else None

    return {
        "event_id": row.get("event_id"),
        "event_type": row.get("event_type"),
        "event_date": _iso(row.get("event_date")),
        "bulletin_no": row.get("bulletin_no"),
        "bulletin_date": _iso(row.get("bulletin_date")),
        "event_text": row.get("event_text"),
        "patent_id": row.get("patent_id"),
        "application_no": row.get("application_no"),
        "publication_no": row.get("publication_no"),
        "kind_code": row.get("kind_code"),
        "record_type": record_type,
        "title": row.get("title"),
        "abstract": row.get("abstract"),
        "ipc_classes": list(row.get("ipc_classes") or []),
        "application_date": _iso(row.get("application_date")),
        "publication_date": _iso(row.get("publication_date")),
        "grant_date": _iso(row.get("grant_date")),
        "holder": holder,
    }


# ---------------------------------------------------------------------------
# List leads
# ---------------------------------------------------------------------------

def list_patent_leads(
    *,
    current_user,
    category: str,
    page: int = 1,
    page_size: int = 20,
    holder: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    watchlist_scoped: bool = False,
    db_factory=Database,
) -> Dict[str, Any]:
    """Paginated patent leads for a category.

    Plan-gated: shares the same ``daily_lead_views`` quota with trademark
    leads. Free-plan users hit 403 'upgrade_required'; Pro/Enterprise
    pass through. The list itself doesn't burn the daily counter — that
    counter increments on individual lead-detail views (which patent
    leads don't expose yet).

    ``watchlist_scoped=True`` restricts results to events on patents whose
    holder matches one of the user's active 'holder' patent watchlist
    rows. Useful for "leads on competitors I'm tracking".
    """
    if category not in LEAD_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown lead category. Valid: {sorted(LEAD_CATEGORIES.keys())}",
        )
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    offset = (page - 1) * page_size

    event_types = list(LEAD_CATEGORIES[category])
    where = ["e.event_type = ANY(%(types)s::text[])"]
    params: Dict[str, Any] = {"types": event_types}

    if date_from:
        where.append("e.bulletin_date >= %(date_from)s")
        params["date_from"] = date_from
    if date_to:
        where.append("e.bulletin_date <= %(date_to)s")
        params["date_to"] = date_to
    if holder and len(holder.strip()) >= 2:
        where.append(
            "EXISTS (SELECT 1 FROM patent_holders ph "
            " WHERE ph.patent_id = p.id "
            " AND LOWER(ph.name) LIKE LOWER(%(holder_like)s))"
        )
        params["holder_like"] = f"%{holder.strip()}%"

    if watchlist_scoped:
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        where.append(
            "EXISTS ("
            "  SELECT 1 FROM patent_watchlist_mt w"
            "  JOIN patent_holders ph ON ph.patent_id = p.id"
            "  WHERE w.organization_id = %(org)s"
            "    AND w.is_active = TRUE"
            "    AND w.watch_type = 'holder'"
            "    AND ("
            "      (w.holder_id IS NOT NULL AND w.holder_id = ph.holder_id)"
            "      OR (w.holder_tpe_client_id IS NOT NULL"
            "          AND EXISTS (SELECT 1 FROM holders h"
            "                      WHERE h.id = ph.holder_id"
            "                        AND h.tpe_client_id = w.holder_tpe_client_id))"
            "      OR (w.holder_name IS NOT NULL"
            "          AND LOWER(ph.name) = LOWER(w.holder_name))"
            "    )"
            ")"
        )
        params["org"] = str(current_user.organization_id)

    where_sql = " AND ".join(where)
    params["limit"] = page_size
    params["offset"] = offset

    with db_factory() as db:
        # Plan gate: shares daily_lead_views with trademark leads. Raises
        # 403 (upgrade_required) on free plan, 429 (daily_limit_exceeded)
        # if the user has burned their daily counter via trademark-lead
        # detail views.
        from services.lead_service import _require_lead_access
        _require_lead_access(db, str(current_user.id))

        cur = db.cursor()
        cur.execute(
            f"""
            SELECT {HYDRATE_COLS}
            FROM patent_events e
            LEFT JOIN patents p ON p.id = e.patent_id
            WHERE {where_sql}
              AND p.id IS NOT NULL
            ORDER BY e.bulletin_date DESC NULLS LAST, e.id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        rows = cur.fetchall()
        cur.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM patent_events e
            LEFT JOIN patents p ON p.id = e.patent_id
            WHERE {where_sql} AND p.id IS NOT NULL
            """,
            params,
        )
        total_row = cur.fetchone()

    total = int(total_row.get("total") if isinstance(total_row, dict) else total_row[0])
    return {
        "category": category,
        "items": [_serialize_lead(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "watchlist_scoped": watchlist_scoped,
    }


# ---------------------------------------------------------------------------
# Summary (counts per category, for dashboard badges)
# ---------------------------------------------------------------------------

def get_patent_lead_summary(
    *,
    current_user,
    watchlist_scoped: bool = False,
    db_factory=Database,
) -> Dict[str, Any]:
    """Returns counts per category, optionally scoped to the user's
    active holder watchlist for "leads I care about" metrics.

    Plan-gated identically to ``list_patent_leads`` — free-plan users
    hit 403 so dashboard badges don't leak counts.
    """
    out: Dict[str, int] = {cat: 0 for cat in LEAD_CATEGORIES}
    out["total"] = 0

    if watchlist_scoped and current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    with db_factory() as db:
        from services.lead_service import _require_lead_access
        _require_lead_access(db, str(current_user.id))

        cur = db.cursor()
        for cat, types in LEAD_CATEGORIES.items():
            params: Dict[str, Any] = {"types": list(types)}
            extra = ""
            if watchlist_scoped:
                extra = (
                    " AND EXISTS ("
                    "   SELECT 1 FROM patent_watchlist_mt w"
                    "   JOIN patent_holders ph ON ph.patent_id = p.id"
                    "   WHERE w.organization_id = %(org)s AND w.is_active = TRUE"
                    "     AND w.watch_type = 'holder'"
                    "     AND ("
                    "       (w.holder_id IS NOT NULL AND w.holder_id = ph.holder_id)"
                    "       OR (w.holder_tpe_client_id IS NOT NULL"
                    "           AND EXISTS (SELECT 1 FROM holders h"
                    "                       WHERE h.id = ph.holder_id"
                    "                         AND h.tpe_client_id = w.holder_tpe_client_id))"
                    "       OR (w.holder_name IS NOT NULL"
                    "           AND LOWER(ph.name) = LOWER(w.holder_name))"
                    "     )"
                    " )"
                )
                params["org"] = str(current_user.organization_id)
            cur.execute(
                f"""
                SELECT COUNT(*) AS n
                FROM patent_events e
                LEFT JOIN patents p ON p.id = e.patent_id
                WHERE e.event_type = ANY(%(types)s::text[]) AND p.id IS NOT NULL
                  {extra}
                """,
                params,
            )
            row = cur.fetchone()
            n = int(row.get("n") if isinstance(row, dict) else row[0])
            out[cat] = n
            out["total"] += n

    return {"by_category": {k: v for k, v in out.items() if k != "total"},
            "total": out["total"], "watchlist_scoped": watchlist_scoped}

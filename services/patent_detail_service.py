"""Patent detail service — full hydrated record for the detail modal.

Sister to ``services/patent_search_service.py`` (which returns a
truncated card-shape) and ``services/patent_lead_service.py`` (which
hydrates per event). The detail service hydrates EVERYTHING the user
might want to see: all holders, all inventors, all attorneys, all
priority claims, figures, and a recent slice of events.

Used by ``GET /api/v1/patents/{id}`` (and the planned
``/api/v1/patents/by-application/{app_no}``).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException

from database.crud import Database


logger = logging.getLogger("turkpatent.patent_detail")

RECENT_EVENTS_LIMIT = 25


def _isofmt(d: Any) -> Optional[str]:
    return d.isoformat() if d else None


def _row_to_dict(row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# Sub-queries
# ---------------------------------------------------------------------------

def _fetch_patent(cur, patent_id: str) -> Optional[Dict[str, Any]]:
    cur.execute(
        """
        SELECT id::text, registry_type, application_no, publication_no, kind_code,
               record_type, application_date, publication_date, grant_date,
               bulletin_no, bulletin_date, title, abstract, ipc_classes, patent_type,
               source_format, source_archive, source_pdf, bulletin_folder,
               page_range_start, page_range_end, created_at, updated_at
        FROM patents
        WHERE id = %s
        """,
        (patent_id,),
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def _fetch_holders(cur, patent_id: str) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT ph.seq, ph.name, ph.address, ph.city, ph.state, ph.postal_code, ph.country,
               ph.holder_id::text AS holder_id,
               h.tpe_client_id, h.name AS canonical_name, h.country AS canonical_country
        FROM patent_holders ph
        LEFT JOIN holders h ON h.id = ph.holder_id
        WHERE ph.patent_id = %s
        ORDER BY ph.seq ASC
        """,
        (patent_id,),
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


def _fetch_inventors(cur, patent_id: str) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT seq, name, address, city, state, postal_code, country
        FROM patent_inventors
        WHERE patent_id = %s
        ORDER BY seq ASC
        """,
        (patent_id,),
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


def _fetch_attorneys(cur, patent_id: str) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT seq, agent_no, name, firm, address
        FROM patent_attorneys
        WHERE patent_id = %s
        ORDER BY seq ASC
        """,
        (patent_id,),
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


def _fetch_priorities(cur, patent_id: str) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT seq, priority_no, priority_date, country
        FROM patent_priorities
        WHERE patent_id = %s
        ORDER BY seq ASC
        """,
        (patent_id,),
    )
    out = []
    for r in cur.fetchall():
        d = _row_to_dict(r)
        d["priority_date"] = _isofmt(d.get("priority_date"))
        out.append(d)
    return out


def _fetch_figures(cur, patent_id: str) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT seq, source, image_path, page, image_xref
        FROM patent_figures
        WHERE patent_id = %s
        ORDER BY seq ASC
        LIMIT 20
        """,
        (patent_id,),
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


def _fetch_recent_events(cur, patent_id: str, app_no: Optional[str]) -> List[Dict[str, Any]]:
    """Recent events. Joins patent_events on patent_id when set; falls back
    to application_no for events that landed before this patent row had an
    id (events from earlier bulletins reference app_no, not patent_id)."""
    cur.execute(
        """
        SELECT id::text, event_type, event_date, bulletin_no, bulletin_date,
               application_no, publication_no, free_text
        FROM patent_events
        WHERE patent_id = %s
           OR (application_no IS NOT NULL AND application_no = %s)
        ORDER BY bulletin_date DESC NULLS LAST, id DESC
        LIMIT %s
        """,
        (patent_id, app_no or "", RECENT_EVENTS_LIMIT),
    )
    out = []
    for r in cur.fetchall():
        d = _row_to_dict(r)
        d["event_date"] = _isofmt(d.get("event_date"))
        d["bulletin_date"] = _isofmt(d.get("bulletin_date"))
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def get_patent_detail(*, patent_id: UUID | str, db_factory=Database) -> Dict[str, Any]:
    """Return the full hydrated detail record for a patent.

    Public-facing data only — nothing tenant-scoped. The patent corpus
    is shared across all users (same as patent search).
    """
    pid = str(patent_id)
    with db_factory() as db:
        cur = db.cursor()
        row = _fetch_patent(cur, pid)
        if not row:
            raise HTTPException(status_code=404, detail="Patent not found")

        record_type = row.get("record_type")
        if hasattr(record_type, "value"):
            record_type = record_type.value

        # Normalize date fields
        for k in ("application_date", "publication_date", "grant_date",
                  "bulletin_date", "created_at", "updated_at"):
            if k in row:
                row[k] = _isofmt(row[k])

        app_no = row.get("application_no")

        return {
            "patent": {**row, "record_type": record_type,
                       "ipc_classes": list(row.get("ipc_classes") or [])},
            "holders": _fetch_holders(cur, pid),
            "inventors": _fetch_inventors(cur, pid),
            "attorneys": _fetch_attorneys(cur, pid),
            "priorities": _fetch_priorities(cur, pid),
            "figures": _fetch_figures(cur, pid),
            "recent_events": _fetch_recent_events(cur, pid, app_no),
        }


def get_patent_detail_by_application_no(
    *, application_no: str, db_factory=Database,
) -> Dict[str, Any]:
    """Convenience: look up a patent by application_no.

    Returns the latest publication (highest publication_date) when the
    same application has multiple publications (e.g. A2 publication +
    later B grant). The detail modal can render tabs across all of
    them later, but for v1 we surface the most recent.
    """
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id::text FROM patents
            WHERE application_no = %s
            ORDER BY publication_date DESC NULLS LAST
            LIMIT 1
            """,
            (application_no,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Patent not found for application_no")
        pid = row["id"] if isinstance(row, dict) else row[0]
    return get_patent_detail(patent_id=pid)

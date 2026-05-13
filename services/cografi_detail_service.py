"""Coğrafi İşaret detail service — full hydrated record for the detail modal.

Sister to ``services/cografi_search_service.py`` (returns truncated cards).
Hydrates EVERYTHING the user might want to see: full record header,
body_sections (the four free-text subsections), all holders
(applicant + agent), all change_requests (only relevant for art42
records), all figures, plus a recency-ordered slice of related
records (other rows for the same application_no).

Used by ``GET /api/v1/cografi/{id}``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException

from database.crud import Database


logger = logging.getLogger("turkpatent.cografi_detail")

RELATED_LIMIT = 25


def _isofmt(d: Any) -> Optional[str]:
    return d.isoformat() if d else None


def _row_to_dict(row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# Sub-queries
# ---------------------------------------------------------------------------

def _fetch_record(cur, record_id: str) -> Optional[Dict[str, Any]]:
    cur.execute(
        """
        SELECT id::text, registry_type, section_key::text, record_type::text,
               application_no, registration_no, existing_registration_no,
               name, application_date, registration_date,
               bulletin_no, bulletin_date, bulletin_folder,
               product_group, gi_type, geographical_boundary,
               usage_description, agent,
               body_sections,
               raw_text,
               correction_referenced_bulletin_no,
               correction_referenced_bulletin_date,
               correction_referenced_record_id,
               correction_old_text, correction_new_text,
               start_page, extractor_version,
               extracted_at, embeddings_at, created_at, updated_at
        FROM cografi_records
        WHERE id = %s
        """,
        (record_id,),
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def _fetch_holders(cur, record_id: str) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT ch.seq, ch.role::text, ch.name, ch.address,
               ch.holder_id::text AS holder_id,
               h.tpe_client_id, h.name AS canonical_name
        FROM cografi_holders ch
        LEFT JOIN holders h ON h.id = ch.holder_id
        WHERE ch.record_id = %s
        ORDER BY ch.role, ch.seq ASC
        """,
        (record_id,),
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


def _fetch_change_requests(cur, record_id: str) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT seq, field, old_text, new_text
        FROM cografi_change_requests
        WHERE record_id = %s
        ORDER BY seq ASC
        """,
        (record_id,),
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


def _fetch_figures(cur, record_id: str, bulletin_folder: Optional[str]) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT seq, image_path, page, bbox, width, height
        FROM cografi_figures
        WHERE record_id = %s
        ORDER BY seq ASC
        LIMIT 50
        """,
        (record_id,),
    )
    out: List[Dict[str, Any]] = []
    for r in cur.fetchall():
        d = _row_to_dict(r)
        path = d.get("image_path")
        d["image_url"] = (
            f"/api/v1/cografi-image/{bulletin_folder}/{path.lstrip('/')}"
            if path and bulletin_folder else None
        )
        # bbox is a NUMERIC[] which psycopg2 returns as Python list of
        # decimal.Decimal; coerce to float for JSON-friendliness.
        bbox = d.get("bbox")
        if isinstance(bbox, list):
            d["bbox"] = [float(x) for x in bbox]
        out.append(d)
    return out


def _fetch_related(cur, application_no: Optional[str], record_id: str) -> List[Dict[str, Any]]:
    """Other records for the same application_no (lifecycle siblings).

    For an examined record, these would be the subsequent registered /
    art40 / etc. publications referencing the same application. Returns
    empty list when application_no is missing (e.g. art42 / corrections).
    """
    if not application_no:
        return []
    cur.execute(
        """
        SELECT id::text, section_key::text, name, bulletin_no, bulletin_date
        FROM cografi_records
        WHERE application_no = %s AND id <> %s
        ORDER BY bulletin_date DESC NULLS LAST
        LIMIT %s
        """,
        (application_no, record_id, RELATED_LIMIT),
    )
    out = []
    for r in cur.fetchall():
        d = _row_to_dict(r)
        d["bulletin_date"] = _isofmt(d.get("bulletin_date"))
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def get_cografi_detail(*, record_id: UUID | str, db_factory=Database) -> Dict[str, Any]:
    """Return the full hydrated detail record for a cografi record.

    Public-facing data only — nothing tenant-scoped. The cografi corpus
    is shared across all users (same as patent / design / marka search).
    """
    rid = str(record_id)
    with db_factory() as db:
        cur = db.cursor()
        row = _fetch_record(cur, rid)
        if not row:
            raise HTTPException(status_code=404, detail="Cografi record not found")

        for k in (
            "application_date", "registration_date",
            "bulletin_date", "correction_referenced_bulletin_date",
            "extracted_at", "embeddings_at", "created_at", "updated_at",
        ):
            if k in row:
                row[k] = _isofmt(row[k])

        app_no = row.get("application_no")
        bulletin_folder = row.get("bulletin_folder")

        return {
            "record": row,
            "holders": _fetch_holders(cur, rid),
            "change_requests": _fetch_change_requests(cur, rid),
            "figures": _fetch_figures(cur, rid, bulletin_folder),
            "related": _fetch_related(cur, app_no, rid),
        }


def get_cografi_detail_by_application_no(
    *, application_no: str, db_factory=Database,
) -> Dict[str, Any]:
    """Convenience: look up the most recent record for an application_no."""
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id::text FROM cografi_records
            WHERE application_no = %s
            ORDER BY bulletin_date DESC NULLS LAST
            LIMIT 1
            """,
            (application_no,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No cografi record for application_no")
        rid = row["id"] if isinstance(row, dict) else row[0]
    return get_cografi_detail(record_id=rid)

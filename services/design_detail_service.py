"""Design detail service — full hydrated record for the detail modal.

Mirrors `services/patent_detail_service.py`. Returns:

  * The design row (status, dates, product name, locarno classes,
    deferred flag, source provenance).
  * Holder block (joined to canonical `holders` for the TPE id and
    canonical name).
  * Designers (denormalized array on the row).
  * Attorney (single (name, firm) pair per design).
  * Views (figures — limited to 20 most recent).
  * Recent events (full timeline — cancellations, transfers,
    renewals, seizures, injunctions).

Used by GET /api/v1/designs/{id}.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException

from database.crud import Database


logger = logging.getLogger("turkpatent.design_detail")

RECENT_EVENTS_LIMIT = 25


def _isofmt(d: Any) -> Optional[str]:
    return d.isoformat() if d else None


def _row_to_dict(row) -> Dict[str, Any]:
    return dict(row)


def _fetch_design(cur, design_id: str) -> Optional[Dict[str, Any]]:
    cur.execute(
        """
        SELECT id::text, registry_type, application_no, registration_no,
               design_index, section,
               current_status::text AS current_status,
               last_event_type, last_event_date,
               product_name_tr, product_name_en, locarno_classes, designers,
               application_date, registration_date,
               bulletin_no, bulletin_date, source_issue_folder,
               holder_id::text AS holder_id,
               attorney_name, attorney_firm,
               created_at, updated_at
        FROM designs
        WHERE id = %s
        """,
        (design_id,),
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def _fetch_holder(cur, holder_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Join the canonical holders row when the design has one.
    Returns None for designs without a holder_id."""
    if not holder_id:
        return None
    cur.execute(
        """
        SELECT id::text, tpe_client_id, name, country
        FROM holders
        WHERE id = %s
        """,
        (holder_id,),
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def _fetch_views(cur, design_id: str) -> List[Dict[str, Any]]:
    """Design figures (views). Returns up to 20 ordered by view_index."""
    cur.execute(
        """
        SELECT view_index, image_path
        FROM design_views
        WHERE design_id = %s AND image_path IS NOT NULL
        ORDER BY view_index ASC
        LIMIT 20
        """,
        (design_id,),
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


def _fetch_recent_events(
    cur, design_id: str, app_no: Optional[str], reg_no: Optional[str],
) -> List[Dict[str, Any]]:
    """Recent events for this design. Joins on design_id when set;
    falls back to application_no / registration_no for events that
    landed before this design row existed (or for events whose FK
    was never resolved — the application_no fallback still finds
    them).

    Each row includes details->'design_indices' so the UI can show
    "this affected indices 1 and 3" when partial."""
    cur.execute(
        """
        SELECT id::text, event_type, event_date, bulletin_no, bulletin_date,
               application_no, registration_no, free_text,
               details->'design_indices' AS design_indices
        FROM design_events
        WHERE design_id = %s
           OR (application_no IS NOT NULL AND application_no = %s)
           OR (registration_no IS NOT NULL AND registration_no = %s)
        ORDER BY bulletin_date DESC NULLS LAST, id DESC
        LIMIT %s
        """,
        (design_id, app_no or "", reg_no or "", RECENT_EVENTS_LIMIT),
    )
    out = []
    for r in cur.fetchall():
        d = _row_to_dict(r)
        d["event_date"] = _isofmt(d.get("event_date"))
        d["bulletin_date"] = _isofmt(d.get("bulletin_date"))
        out.append(d)
    return out


def _synthetic_events(design_row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Synthesize timeline events from the design row's own dates.
    The `design_events` table only contains *secondary* lifecycle
    events (renewals, transfers, cancellations, ...). The first-class
    milestones — when the design was filed and published — live on
    the design row itself. Surface them so the detail-modal timeline
    is complete in chronological order.

    For TR designs filing date == registration date (registration
    happens automatically at publication), so we emit one `application_filed`
    event for the filing, and one `published` (or `publication_postponed` /
    `publication_resumed` depending on section) event for the bulletin
    appearance.
    """
    out: List[Dict[str, Any]] = []
    app_date = design_row.get("application_date")
    bull_date = design_row.get("bulletin_date")
    section = design_row.get("section") or ""

    if app_date:
        out.append({
            "id": "synthetic:application_filed",
            "event_type": "application_filed",
            "event_date": _isofmt(app_date),
            "bulletin_date": _isofmt(app_date),
            "synthetic": True,
        })

    if bull_date:
        if section == "deferred":
            et = "publication_postponed"
        elif section == "deferred_lifted":
            et = "publication_resumed"
        else:
            # tr_native / hague / republished / unknown — all are
            # "design appeared in the bulletin" events.
            et = "published"
        out.append({
            "id": "synthetic:" + et,
            "event_type": et,
            "event_date": _isofmt(bull_date),
            "bulletin_date": _isofmt(bull_date),
            "synthetic": True,
        })

    return out


def _merge_events_chronologically(
    real_events: List[Dict[str, Any]],
    synthetic: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Combine real + synthetic events, newest first. Real events
    come back from SQL pre-sorted; synthetic events get folded in
    based on their date. Stable secondary sort on event_type keeps
    the order deterministic when two events share a date (e.g. the
    rare case where a renewal lands on the same date as the original
    filing)."""
    combined = list(real_events) + list(synthetic)
    combined.sort(
        key=lambda e: (
            e.get("bulletin_date") or e.get("event_date") or "",
            e.get("event_type") or "",
        ),
        reverse=True,
    )
    return combined


def get_design_detail(*, design_id: UUID | str, db_factory=Database) -> Dict[str, Any]:
    """Return the full hydrated detail record for a design."""
    did = str(design_id)
    with db_factory() as db:
        cur = db.cursor()
        row = _fetch_design(cur, did)
        if not row:
            raise HTTPException(status_code=404, detail="Design not found")

        # Capture raw dates BEFORE the iso-format conversion so the
        # synthetic-event builder can use them as date objects (its
        # _isofmt helper expects raw date / datetime).
        raw_for_synthetic = {
            "application_date": row.get("application_date"),
            "bulletin_date": row.get("bulletin_date"),
            "section": row.get("section"),
        }

        for k in ("application_date", "registration_date",
                  "bulletin_date", "last_event_date",
                  "created_at", "updated_at"):
            if k in row:
                row[k] = _isofmt(row[k])

        app_no = row.get("application_no")
        reg_no = row.get("registration_no")
        holder_id = row.get("holder_id")

        # Merge real design_events with synthetic milestone events
        # (filing + publication) so the timeline is complete.
        real_events = _fetch_recent_events(cur, did, app_no, reg_no)
        synthetic = _synthetic_events(raw_for_synthetic)
        all_events = _merge_events_chronologically(real_events, synthetic)

        return {
            "design": {
                **row,
                "locarno_classes": list(row.get("locarno_classes") or []),
                "designers": list(row.get("designers") or []),
            },
            "holder": _fetch_holder(cur, holder_id),
            "views": _fetch_views(cur, did),
            "recent_events": all_events,
        }

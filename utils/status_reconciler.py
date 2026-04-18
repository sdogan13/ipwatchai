"""
Shared utility for computing final_status from current_status + effective_status.

Used by both ingest.py (after batch upserts) and ingest_events.py (after materialization).
The reconciliation logic uses the most recent date as tiebreaker:
  - ingest_date: BLT→bulletin_date, GZ→gazette_date, APP→updated_at
  - event_date:  last_event_date
"""
import logging
from datetime import date, datetime
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

# SQL CASE expression reused in update_final_status_batch() and migration
_INGEST_DATE_EXPR = """COALESCE(
    CASE status_source
        WHEN 'BLT' THEN bulletin_date
        WHEN 'GZ'  THEN gazette_date
        ELSE updated_at::date
    END,
    updated_at::date
)"""

_FINAL_STATUS_SQL = f"""
    final_status = CASE
        WHEN effective_status IS NULL THEN current_status
        WHEN current_status IS NULL THEN effective_status
        WHEN last_event_date >= {_INGEST_DATE_EXPR} THEN effective_status
        WHEN last_event_date < {_INGEST_DATE_EXPR} THEN current_status
        ELSE COALESCE(effective_status, current_status)
    END,
    final_status_source = CASE
        WHEN effective_status IS NULL THEN 'ingest'
        WHEN current_status IS NULL THEN 'event'
        WHEN last_event_date >= {_INGEST_DATE_EXPR} THEN 'event'
        WHEN last_event_date < {_INGEST_DATE_EXPR} THEN 'ingest'
        ELSE 'event'
    END,
    final_status_at = GREATEST(last_event_date, {_INGEST_DATE_EXPR})
"""


def reconcile_status(
    current_status: Optional[str],
    effective_status: Optional[str],
    ingest_date: Optional[date],
    event_date: Optional[date],
) -> Tuple[Optional[str], str, Optional[date]]:
    """
    Pure function: reconcile two status sources into a single final status.

    Returns (final_status, final_status_source, final_status_at).
    """
    if not effective_status:
        return current_status, 'ingest', ingest_date or event_date
    if not current_status:
        return effective_status, 'event', event_date or ingest_date

    # Both populated — compare dates
    if event_date and ingest_date:
        if event_date >= ingest_date:
            return effective_status, 'event', event_date
        else:
            return current_status, 'ingest', ingest_date

    # Missing date(s) — prefer effective_status (more granular than heuristic)
    return effective_status, 'event', event_date or ingest_date


def compute_ingest_status_date(
    status_source: Optional[str],
    bulletin_date: Optional[date],
    gazette_date: Optional[date],
    updated_at=None,
) -> Optional[date]:
    """Derive the effective date for the ingest-sourced current_status."""
    if status_source == 'BLT' and bulletin_date:
        return bulletin_date
    if status_source == 'GZ' and gazette_date:
        return gazette_date
    if updated_at:
        return updated_at.date() if isinstance(updated_at, datetime) else updated_at
    return None


def update_final_status_batch(conn, app_nos: Optional[List[str]] = None) -> int:
    """
    Recompute final_status for a set of trademarks (or all if app_nos is None).

    Executes a single UPDATE statement doing all computation in SQL for performance.
    Returns the number of rows updated.
    """
    cur = conn.cursor()

    where_clause = ""
    params: list = []
    if app_nos:
        where_clause = "WHERE application_no = ANY(%s)"
        params = [app_nos]

    sql = f"UPDATE trademarks SET {_FINAL_STATUS_SQL} {where_clause}"
    cur.execute(sql, params)
    count = cur.rowcount
    conn.commit()
    cur.close()
    logger.info(f"final_status recomputed for {count} trademarks")
    return count

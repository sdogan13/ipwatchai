"""Scan job repository operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from database.crud import Database


class ScanLogCRUD:
    """Repository for scan_jobs lifecycle records."""

    _JOB_TYPE_MAP = {
        "bulletin": "bulletin_scan",
        "gazette": "gazette_scan",
        "application": "application_scan",
    }

    @staticmethod
    def create(db: Database, source_type: str, source_reference: str) -> UUID:
        """Create a scan job entry."""
        cur = db.cursor()
        scan_id = uuid4()
        job_type = ScanLogCRUD._JOB_TYPE_MAP.get(source_type, source_type)

        cur.execute(
            """
            INSERT INTO scan_jobs (id, job_type, source_folder, status, started_at, created_at)
            VALUES (%s, %s, %s, 'running', NOW(), NOW())
            RETURNING id
        """,
            (str(scan_id), job_type, source_reference),
        )

        db.commit()
        return scan_id

    @staticmethod
    def complete(
        db: Database,
        scan_id: UUID,
        trademarks_scanned: int,
        watchlist_checked: int,
        alerts_generated: int,
    ):
        """Mark a scan as complete."""
        cur = db.cursor()
        cur.execute(
            """
            UPDATE scan_jobs SET
                status = 'completed',
                completed_at = NOW(),
                total_trademarks_scanned = %s,
                total_watchlist_items_checked = %s,
                total_alerts_generated = %s
            WHERE id = %s
        """,
            (trademarks_scanned, watchlist_checked, alerts_generated, str(scan_id)),
        )
        db.commit()

    @staticmethod
    def fail(db: Database, scan_id: UUID, error_message: str):
        """Mark a scan as failed."""
        cur = db.cursor()
        cur.execute(
            """
            UPDATE scan_jobs SET
                status = 'failed',
                completed_at = NOW(),
                error_message = %s
            WHERE id = %s
        """,
            (error_message, str(scan_id)),
        )
        db.commit()

    @staticmethod
    def get_last_scan(db: Database, source_type: str) -> Optional[Dict]:
        """Get the last successful scan for a source type."""
        cur = db.cursor()
        cur.execute(
            """
            SELECT * FROM scan_jobs
            WHERE job_type = %s AND status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
        """,
            (source_type,),
        )
        row = cur.fetchone()
        return dict(row) if row else None

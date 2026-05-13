"""Backfill patents.current_status from the patent_events timeline.

Two passes:

  1. Link orphan events. About 58% of patent_events have
     patent_id = NULL (carrying application_no only). We resolve
     them to the *canonical* patents row per application_no:

         canonical = row with highest record_type rank
                     (GRANTED_PATENT > GRANTED_UM > PUBLISHED_APP
                      > PUBLISHED_UM_APP > UNKNOWN > LEGACY),
                     tiebreak by latest bulletin_date.

     Future ingest should use the same resolver so events stay
     linked from the moment they land.

  2. Derive current_status per application_no. For every application,
     fetch all its events oldest-first, run the state machine in
     pipeline.patent_status_derivation, then UPDATE every patents row
     sharing that application_no with the resulting status,
     last_event_type, last_event_date, and status_computed_at.

Idempotent. Re-running on the same input produces the same writes.
Run anytime — the migration that introduces current_status already
shipped.

Usage:
  python scripts/backfill_patent_current_status.py --dry-run --limit 1000
  python scripts/backfill_patent_current_status.py
  python scripts/backfill_patent_current_status.py --skip-link  # status only
  python scripts/backfill_patent_current_status.py --skip-status  # link only
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor, execute_batch
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

try:
    from config.settings import settings  # type: ignore
except Exception:
    settings = None

from pipeline.patent_status_derivation import Event, derive_patent_status


logger = logging.getLogger("backfill.patent_status")


# ---- DB connection (same pattern as scripts/regenerate_name_tr.py) ----


def get_db_connection():
    kwargs = {
        "host": os.environ.get("DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("DB_PORT", "5433")),
        "dbname": os.environ.get("DB_NAME", "trademark_db"),
        "user": os.environ.get("DB_USER", "turk_patent"),
        "password": os.environ.get("DB_PASSWORD", ""),
        "connect_timeout": 30,
    }
    if settings is not None:
        kwargs.update({
            "host": settings.database.host,
            "port": settings.database.port,
            "dbname": settings.database.name,
            "user": settings.database.user,
            "password": settings.database.password,
        })
    return psycopg2.connect(**kwargs)


# ---- Pass 1: link orphan events ---------------------------------


# The canonical-row resolver. record_type rank decides who "owns" the
# events for a given application_no when there are multiple patents
# rows (different bulletins). GRANTED_PATENT wins over GRANTED_UM
# wins over the application-stage rows wins over UNKNOWN/LEGACY.
_CANONICAL_SQL = """
    WITH ranked AS (
        SELECT
            id,
            application_no,
            record_type,
            bulletin_date,
            ROW_NUMBER() OVER (
                PARTITION BY application_no
                ORDER BY
                    CASE record_type
                        WHEN 'GRANTED_PATENT'    THEN 1
                        WHEN 'GRANTED_UM'        THEN 2
                        WHEN 'PUBLISHED_APP'    THEN 3
                        WHEN 'PUBLISHED_UM_APP' THEN 4
                        WHEN 'UNKNOWN'           THEN 5
                        WHEN 'LEGACY'            THEN 6
                        ELSE 7
                    END,
                    bulletin_date DESC NULLS LAST,
                    id
            ) AS rk
        FROM patents
        WHERE application_no IS NOT NULL
    )
    SELECT application_no, id::text AS canonical_patent_id
    FROM ranked
    WHERE rk = 1
"""


def link_orphan_events(conn, *, dry_run: bool, batch_size: int = 5000) -> Dict[str, int]:
    """Resolve patent_events.patent_id for the rows where it's NULL.

    Single SQL UPDATE...FROM against the canonical-row CTE — no
    Python streaming. Fast (~3s on 527k rows) and keeps the whole
    operation in one transaction. Unresolved events (where no patents
    row exists for the application_no) stay with patent_id = NULL
    and continue to be queried via the application_no fallback.

    Returns counters {'linked': N, 'unresolved': N}.
    """
    stats = {"linked": 0, "unresolved": 0}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM patent_events WHERE patent_id IS NULL"
        )
        orphan_total = cur.fetchone()[0]
        logger.info("Found %d orphan events (patent_id IS NULL)", orphan_total)

        if orphan_total == 0:
            return stats

        # The CTE in _CANONICAL_SQL ranks patents rows per
        # application_no. We pick rk=1 (the canonical row) and join
        # back to the orphan events to fill patent_id.
        update_sql = f"""
            WITH canonical AS (
                {_CANONICAL_SQL}
            )
            UPDATE patent_events pe
            SET patent_id = c.canonical_patent_id::uuid
            FROM canonical c
            WHERE pe.patent_id IS NULL
              AND pe.application_no = c.application_no
        """

        if dry_run:
            # Use EXPLAIN-style estimate via a COUNT query — no writes.
            cur.execute(f"""
                SELECT COUNT(*)
                FROM patent_events pe
                JOIN ({_CANONICAL_SQL}) c
                  ON pe.application_no = c.application_no
                WHERE pe.patent_id IS NULL
            """)
            stats["linked"] = cur.fetchone()[0]
            stats["unresolved"] = orphan_total - stats["linked"]
            logger.info(
                "Link pass dry-run: would link %d, leave %d unresolved (no writes)",
                stats["linked"], stats["unresolved"],
            )
        else:
            cur.execute(update_sql)
            stats["linked"] = cur.rowcount
            stats["unresolved"] = orphan_total - stats["linked"]
            conn.commit()
            logger.info(
                "Link pass done: linked=%d unresolved=%d total=%d",
                stats["linked"], stats["unresolved"], orphan_total,
            )
    return stats


# ---- Pass 2: compute current_status per application_no ----------


def _fetch_events_grouped(cur, app_nos: List[str]) -> Dict[str, List[Event]]:
    """Pull all events for a batch of applications. Returns
    {application_no: [Event, ...]} unsorted (derive_patent_status
    sorts internally)."""
    cur.execute(
        """
        SELECT application_no, event_type, event_date, bulletin_date
        FROM patent_events
        WHERE application_no = ANY(%s)
        """,
        (app_nos,),
    )
    grouped: Dict[str, List[Event]] = defaultdict(list)
    for row in cur.fetchall():
        grouped[row[0]].append(Event(
            event_type=row[1], event_date=row[2], bulletin_date=row[3],
        ))
    return grouped


def _fetch_canonical_record_types(cur, app_nos: List[str]) -> Dict[str, str]:
    """Pull the canonical record_type per application. Same rule as
    the FK resolver in pipeline/ingest_patents.py."""
    cur.execute(
        """
        SELECT DISTINCT ON (application_no) application_no, record_type::text
        FROM patents
        WHERE application_no = ANY(%s)
        ORDER BY application_no,
            CASE record_type
                WHEN 'GRANTED_PATENT'    THEN 1
                WHEN 'GRANTED_UM'        THEN 2
                WHEN 'PUBLISHED_APP'    THEN 3
                WHEN 'PUBLISHED_UM_APP' THEN 4
                WHEN 'UNKNOWN'           THEN 5
                WHEN 'LEGACY'            THEN 6
                ELSE 7
            END,
            bulletin_date DESC NULLS LAST,
            id
        """,
        (app_nos,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def compute_current_status(
    conn,
    *,
    dry_run: bool,
    limit: Optional[int] = None,
    batch_size: int = 1000,
) -> Dict[str, int]:
    """Walk every application_no with events OR a record_type seed,
    derive status, UPDATE all patents rows sharing that
    application_no. Returns counters.

    Apps without events but with a PUBLISHED_*/GRANTED_* record_type
    still get a status — the bulletin appearance itself seeds the
    state machine (see pipeline.patent_status_derivation).
    """
    stats = {"applications": 0, "rows_updated": 0, "by_status": defaultdict(int)}

    with conn.cursor() as cur:
        # Union: apps that have events + apps with a seedable
        # record_type. Either is enough to compute a status.
        cur.execute("""
            SELECT application_no FROM patent_events WHERE application_no IS NOT NULL
            UNION
            SELECT application_no FROM patents
            WHERE application_no IS NOT NULL
              AND record_type IN ('PUBLISHED_APP', 'PUBLISHED_UM_APP',
                                  'GRANTED_PATENT', 'GRANTED_UM')
            ORDER BY 1
        """)
        all_app_nos = [r[0] for r in cur.fetchall()]
        if limit is not None:
            all_app_nos = all_app_nos[:limit]
        logger.info("Computing status for %d application_no(s)", len(all_app_nos))

        for i in range(0, len(all_app_nos), batch_size):
            chunk = all_app_nos[i:i + batch_size]
            grouped = _fetch_events_grouped(cur, chunk)
            canonical_rt = _fetch_canonical_record_types(cur, chunk)

            update_rows: List[Tuple[str, Optional[str], Optional[date], str]] = []
            for app_no in chunk:
                events = grouped.get(app_no, [])
                rt = canonical_rt.get(app_no)
                # Skip if nothing to compute (no events, no seedable
                # record_type).
                if not events and rt not in (
                    "PUBLISHED_APP", "PUBLISHED_UM_APP", "GRANTED_PATENT", "GRANTED_UM",
                ):
                    continue
                res = derive_patent_status(events, record_type=rt)
                update_rows.append((
                    res.status.value,
                    res.last_event_type,
                    res.last_event_date,
                    app_no,
                ))
                stats["applications"] += 1
                stats["by_status"][res.status.value] += 1

            if update_rows and not dry_run:
                with conn.cursor() as upd:
                    execute_batch(
                        upd,
                        """
                        UPDATE patents
                        SET current_status = %s::patent_lifecycle_status,
                            last_event_type = %s,
                            last_event_date = %s,
                            status_computed_at = NOW()
                        WHERE application_no = %s
                        """,
                        update_rows,
                        page_size=batch_size,
                    )
                    # Capture rows actually updated for this batch.
                    upd.execute(
                        "SELECT COUNT(*) FROM patents WHERE application_no = ANY(%s)",
                        ([r[3] for r in update_rows],),
                    )
                    stats["rows_updated"] += upd.fetchone()[0]
                    conn.commit()
            logger.info(
                "  status: processed %d / %d apps",
                min(i + batch_size, len(all_app_nos)), len(all_app_nos),
            )

    logger.info(
        "Status pass done%s: applications=%d patents_rows~=%d distribution=%s",
        " (dry-run, no writes)" if dry_run else "",
        stats["applications"], stats["rows_updated"], dict(stats["by_status"]),
    )
    return stats


# ---- Entry point ------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute everything but skip the UPDATEs.")
    parser.add_argument("--skip-link", action="store_true",
                        help="Skip the patent_events.patent_id link pass.")
    parser.add_argument("--skip-status", action="store_true",
                        help="Skip the patents.current_status compute pass.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N application_no's (status pass).")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    start = time.time()
    conn = get_db_connection()
    try:
        if not args.skip_link:
            link_orphan_events(conn, dry_run=args.dry_run, batch_size=args.batch_size)
        if not args.skip_status:
            compute_current_status(
                conn, dry_run=args.dry_run,
                limit=args.limit, batch_size=args.batch_size,
            )
    finally:
        conn.close()
    elapsed = time.time() - start
    logger.info("Done in %.1fs", elapsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

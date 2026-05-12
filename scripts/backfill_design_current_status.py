"""Backfill designs.current_status from the design_events timeline.

Two passes (mirrors scripts/backfill_patent_current_status.py):

  1. Link orphan events. About 80% of design_events have
     design_id = NULL — most events landed before the design row
     was ingested. Resolve to the canonical design row:

         canonical = lowest design_index for the application_no,
                     fallback by registration_no.

  2. Per design_id, walk events that target it (full-scope events
     OR partial events whose design_indices includes this row's
     design_index), seed status from section, run state machine,
     UPDATE the row. Apply the 25-year term-cap override last.

Idempotent. Re-runs produce the same writes.

Usage:
  python scripts/backfill_design_current_status.py --dry-run --limit 1000
  python scripts/backfill_design_current_status.py
  python scripts/backfill_design_current_status.py --skip-link
  python scripts/backfill_design_current_status.py --skip-status
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
from typing import Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

try:
    from config.settings import settings  # type: ignore
except Exception:
    settings = None

from pipeline.design_status_derivation import (
    Event,
    derive_design_status,
    _parse_design_indices,
)


logger = logging.getLogger("backfill.design_status")


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


# Canonical design row per application_no. Lowest design_index wins
# (events typically reference the application as a whole, with
# `details.design_indices` carrying the targeting set when partial).
_CANONICAL_BY_APP_SQL = """
    SELECT DISTINCT ON (application_no) application_no, id::text AS canonical_design_id
    FROM designs
    WHERE application_no IS NOT NULL
    ORDER BY application_no, design_index ASC NULLS LAST, id
"""

_CANONICAL_BY_REG_SQL = """
    SELECT DISTINCT ON (registration_no) registration_no::text, id::text AS canonical_design_id
    FROM designs
    WHERE registration_no IS NOT NULL
    ORDER BY registration_no, design_index ASC NULLS LAST, id
"""


def link_orphan_events(conn, *, dry_run: bool) -> Dict[str, int]:
    """Resolve design_events.design_id where NULL. Two-pass: first by
    application_no, then by registration_no for events that didn't
    match the first pass."""
    stats = {"linked_by_app": 0, "linked_by_reg": 0, "unresolved": 0}
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM design_events WHERE design_id IS NULL")
        orphan_total = cur.fetchone()[0]
        logger.info("Found %d orphan events (design_id IS NULL)", orphan_total)
        if orphan_total == 0:
            return stats

        # Pass 1a: link by application_no
        update_sql_app = f"""
            WITH canonical AS ({_CANONICAL_BY_APP_SQL})
            UPDATE design_events de
            SET design_id = c.canonical_design_id::uuid
            FROM canonical c
            WHERE de.design_id IS NULL
              AND de.application_no = c.application_no
        """
        if dry_run:
            cur.execute(f"""
                SELECT COUNT(*) FROM design_events de
                JOIN ({_CANONICAL_BY_APP_SQL}) c
                  ON de.application_no = c.application_no
                WHERE de.design_id IS NULL
            """)
            stats["linked_by_app"] = cur.fetchone()[0]
        else:
            cur.execute(update_sql_app)
            stats["linked_by_app"] = cur.rowcount
            conn.commit()
            logger.info("  linked %d via application_no", stats["linked_by_app"])

        # Pass 1b: registration_no fallback for whatever is left.
        update_sql_reg = f"""
            WITH canonical AS ({_CANONICAL_BY_REG_SQL})
            UPDATE design_events de
            SET design_id = c.canonical_design_id::uuid
            FROM canonical c
            WHERE de.design_id IS NULL
              AND de.registration_no::text = c.registration_no
        """
        if dry_run:
            cur.execute(f"""
                SELECT COUNT(*) FROM design_events de
                JOIN ({_CANONICAL_BY_REG_SQL}) c
                  ON de.registration_no::text = c.registration_no
                WHERE de.design_id IS NULL
            """)
            stats["linked_by_reg"] = cur.fetchone()[0]
        else:
            cur.execute(update_sql_reg)
            stats["linked_by_reg"] = cur.rowcount
            conn.commit()
            logger.info("  linked %d via registration_no", stats["linked_by_reg"])

        linked = stats["linked_by_app"] + stats["linked_by_reg"]
        stats["unresolved"] = orphan_total - linked
        logger.info(
            "Link pass done%s: by_app=%d by_reg=%d unresolved=%d total=%d",
            " (dry-run)" if dry_run else "",
            stats["linked_by_app"], stats["linked_by_reg"],
            stats["unresolved"], orphan_total,
        )
    return stats


# ---- Pass 2: compute current_status per design_id ---------------


def compute_current_status(
    conn,
    *,
    dry_run: bool,
    limit: Optional[int] = None,
    batch_size: int = 2000,
) -> Dict[str, int]:
    """Walk every design's events + section seed + 25-year term cap,
    UPDATE designs.current_status. Returns counters."""
    stats = {"designs": 0, "rows_updated": 0, "by_status": defaultdict(int)}
    today = date.today()

    with conn.cursor() as cur:
        # All designs need a pass — even ones without events get the
        # term-cap check + section seed (the section seed already
        # matches the current ingest value, but recomputing it
        # idempotently is safe and lets us add new sections later).
        cur.execute("""
            SELECT id::text, application_no, registration_no, section,
                   design_index, application_date
            FROM designs
            ORDER BY id
        """)
        designs_meta = cur.fetchall()
        if limit is not None:
            designs_meta = designs_meta[:limit]
        total = len(designs_meta)
        logger.info("Computing status for %d designs", total)

        # Group designs by (app_no, reg_no) so we can pre-fetch all
        # the relevant events per batch.
        for i in range(0, total, batch_size):
            chunk = designs_meta[i:i + batch_size]
            app_nos = list({r[1] for r in chunk if r[1]})
            reg_nos = list({r[2] for r in chunk if r[2]})

            events_by_app: dict = defaultdict(list)
            events_by_reg: dict = defaultdict(list)
            if app_nos or reg_nos:
                cur.execute(
                    """
                    SELECT application_no, registration_no, event_type,
                           event_date, bulletin_date,
                           details->'design_indices' AS design_indices
                    FROM design_events
                    WHERE (application_no = ANY(%s) AND application_no IS NOT NULL)
                       OR (registration_no = ANY(%s) AND registration_no IS NOT NULL)
                    """,
                    (app_nos, reg_nos),
                )
                for app, reg, et, ed, bd, di in cur.fetchall():
                    ev = Event(
                        event_type=et, event_date=ed, bulletin_date=bd,
                        design_indices=_parse_design_indices(di),
                    )
                    if app:
                        events_by_app[app].append(ev)
                    if reg:
                        events_by_reg[reg].append(ev)

            update_rows: List[Tuple[str, Optional[str], Optional[date], str]] = []
            for did, app_no, reg_no, section, design_index, app_date in chunk:
                events: List[Event] = []
                if app_no and app_no in events_by_app:
                    events.extend(events_by_app[app_no])
                if reg_no and reg_no in events_by_reg:
                    events.extend(
                        ev for ev in events_by_reg[reg_no]
                        if ev not in events
                    )
                res = derive_design_status(
                    events,
                    section=section,
                    design_index=design_index,
                    application_date=app_date,
                    today=today,
                )
                update_rows.append((
                    res.status.value,
                    res.last_event_type,
                    res.last_event_date,
                    did,
                ))
                stats["designs"] += 1
                stats["by_status"][res.status.value] += 1

            if update_rows and not dry_run:
                with conn.cursor() as upd:
                    execute_batch(
                        upd,
                        """
                        UPDATE designs
                        SET current_status = %s::design_status,
                            last_event_type = %s,
                            last_event_date = %s,
                            status_computed_at = NOW()
                        WHERE id = %s::uuid
                        """,
                        update_rows,
                        page_size=batch_size,
                    )
                    stats["rows_updated"] += len(update_rows)
                conn.commit()

            logger.info("  status: processed %d / %d designs", min(i + batch_size, total), total)

    logger.info(
        "Status pass done%s: designs=%d rows_updated=%d distribution=%s",
        " (dry-run)" if dry_run else "",
        stats["designs"], stats["rows_updated"], dict(stats["by_status"]),
    )
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-link", action="store_true")
    parser.add_argument("--skip-status", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
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
            link_orphan_events(conn, dry_run=args.dry_run)
        if not args.skip_status:
            compute_current_status(
                conn, dry_run=args.dry_run,
                limit=args.limit, batch_size=args.batch_size,
            )
    finally:
        conn.close()
    logger.info("Done in %.1fs", time.time() - start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Re-apply patent_events for every bulletin from on-disk events.json.

Use case: after a fix to `pdf_extract_patent_events.py`, run the
extractor with `--all --force` to regenerate the events.json files,
then run THIS script to push the new events into the DB. Avoids
re-upserting all patent metadata (which is what `ingest_bulletin`
would do).

Each call to `replace_events` does a DELETE+INSERT scoped to the
bulletin_no — old (wrongly-classified) rows are removed before the
new ones land, so this is idempotent.

After the events are reapplied, call
`scripts/backfill_patent_current_status.py --skip-link` to refresh
`patents.current_status` from the corrected timeline.

Usage:
  python scripts/reingest_patent_events.py
  python scripts/reingest_patent_events.py --bulletins-root bulletins/Patent__Faydali_Model
  python scripts/reingest_patent_events.py --only PT_2025_3_2025-03-21
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

try:
    from config.settings import settings  # type: ignore
except Exception:
    settings = None

from pipeline.ingest_patents import replace_events
from pipeline.patent_status_derivation import recompute_current_status


logger = logging.getLogger("reingest.patent_events")


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


def process_one(conn, events_path: Path, *, recompute_status: bool = False) -> int:
    """Replace all events for one bulletin. Returns inserted count.

    Status recompute is OFF by default because the recompute is the
    slow step and the result is the same whether we do it per-bulletin
    or once at the end. Run scripts/backfill_patent_current_status.py
    --skip-link after the bulk reingest to refresh status in one pass.
    """
    doc = json.loads(events_path.read_text(encoding="utf-8"))
    with conn.cursor() as cur:
        inserted = replace_events(cur, doc)
        if recompute_status:
            app_nos = list({
                ev.get("application_no")
                for ev in doc.get("events", [])
                if ev.get("application_no")
            })
            if app_nos:
                recompute_current_status(cur, app_nos)
        conn.commit()
    return inserted


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bulletins-root", type=Path,
        default=PROJECT_ROOT / "bulletins" / "Patent__Faydali_Model",
    )
    parser.add_argument("--only", help="Process only this bulletin folder name")
    parser.add_argument(
        "--recompute-status", action="store_true",
        help="Recompute patents.current_status per-bulletin (slow). "
             "Default: off. Run scripts/backfill_patent_current_status.py "
             "after the bulk reingest instead.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    folders = sorted(args.bulletins_root.iterdir()) if args.bulletins_root.is_dir() else []
    if args.only:
        folders = [f for f in folders if f.name == args.only]
    if not folders:
        logger.error("No bulletin folders found in %s", args.bulletins_root)
        return 1

    started = time.time()
    conn = get_db_connection()
    total_inserted = 0
    total_folders = 0
    skipped = 0
    failed = 0
    try:
        for folder in folders:
            events_path = folder / "events.json"
            if not events_path.is_file():
                skipped += 1
                continue
            try:
                n = process_one(conn, events_path, recompute_status=args.recompute_status)
            except Exception as exc:  # noqa: BLE001
                logger.error("[!] %s: %r", folder.name, exc)
                failed += 1
                continue
            total_inserted += n
            total_folders += 1
            logger.info("[+] %s: %d events", folder.name, n)
    finally:
        conn.close()
    logger.info(
        "Done in %.1fs: %d bulletins processed, %d events upserted, %d skipped (no events.json), %d failed",
        time.time() - started, total_folders, total_inserted, skipped, failed,
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

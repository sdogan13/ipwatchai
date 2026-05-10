"""Seed ``ipc_classes_lookup`` from ``data/ipc_classes_seed.json``.

Idempotent: upserts each section + subclass row. Run repeatedly to
extend the lookup table; existing rows are updated to match the seed
file (descriptions can be improved over time without a migration).

CLI::

    python scripts/seed_ipc_classes.py
    python scripts/seed_ipc_classes.py --dry-run     # just print plan
    python scripts/seed_ipc_classes.py --truncate    # wipe table first

The schema (migrations/patents.sql Stage 0) is:
    code            VARCHAR(20) PRIMARY KEY
    section         CHAR(1)
    class_code      VARCHAR(3)
    subclass        VARCHAR(5)
    description_tr  TEXT
    description_en  TEXT
    updated_at      TIMESTAMP DEFAULT NOW()
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import psycopg2


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("seed_ipc")

ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = ROOT / "data" / "ipc_classes_seed.json"


def _db_conn():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5433")),
        dbname=os.environ.get("DB_NAME", "trademark_db"),
        user=os.environ.get("DB_USER", "turk_patent"),
        password=os.environ.get("DB_PASSWORD", "Dogan.1996"),
    )


UPSERT_SQL = """
INSERT INTO ipc_classes_lookup
    (code, section, class_code, subclass, description_en, description_tr, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, NOW())
ON CONFLICT (code) DO UPDATE SET
    section        = EXCLUDED.section,
    class_code     = EXCLUDED.class_code,
    subclass       = EXCLUDED.subclass,
    description_en = EXCLUDED.description_en,
    description_tr = EXCLUDED.description_tr,
    updated_at     = NOW()
"""


def _row_for_section(s):
    return (
        s["code"], s["section"], None, None,
        s["description_en"], s["description_tr"],
    )


def _row_for_subclass(s):
    return (
        s["code"], s["section"], s["class_code"], s["subclass"],
        s["description_en"], s["description_tr"],
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="seed_ipc_classes")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the plan without modifying the DB.")
    p.add_argument("--truncate", action="store_true",
                   help="DELETE FROM ipc_classes_lookup before seeding.")
    args = p.parse_args(argv)

    if not SEED_PATH.is_file():
        logger.error("seed file not found: %s", SEED_PATH)
        return 1
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    sections = data.get("sections", [])
    subclasses = data.get("subclasses", [])
    logger.info("seed: %d sections + %d subclasses", len(sections), len(subclasses))

    if args.dry_run:
        for s in sections:
            print(f"  [SECTION] {s['code']}  {s['description_en'][:60]}")
        for s in subclasses:
            print(f"  [SUBCLAS] {s['code']}  {s['description_en'][:60]}")
        return 0

    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            if args.truncate:
                logger.info("truncating ipc_classes_lookup")
                cur.execute("DELETE FROM ipc_classes_lookup")

            inserted = 0
            for s in sections:
                cur.execute(UPSERT_SQL, _row_for_section(s))
                inserted += 1
            for s in subclasses:
                cur.execute(UPSERT_SQL, _row_for_subclass(s))
                inserted += 1
            conn.commit()
            logger.info("upserted %d rows", inserted)

            cur.execute(
                "SELECT COUNT(*) AS total, "
                "COUNT(*) FILTER (WHERE description_tr IS NOT NULL) AS with_tr "
                "FROM ipc_classes_lookup"
            )
            row = cur.fetchone()
            total = row[0] if not isinstance(row, dict) else row.get("total")
            with_tr = row[1] if not isinstance(row, dict) else row.get("with_tr")
            logger.info("ipc_classes_lookup now has %d rows (%d with TR descriptions)",
                        total, with_tr)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Run the Tasarım (industrial design) schema migration.

Creates the design_status enum, locarno_classes_lookup, designs,
design_views, and design_events tables. Idempotent — safe to re-run.

Usage:
    python migrations/run_designs_migration.py            # apply
    python migrations/run_designs_migration.py --down     # drop the new objects (rollback)
"""
import argparse
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv()


def _db_config():
    try:
        from config.settings import settings
        return {
            "host": settings.database.host,
            "port": settings.database.port,
            "database": settings.database.name,
            "user": settings.database.user,
            "password": settings.database.password,
            "connect_timeout": 30,
        }
    except Exception:
        return {
            "host": os.getenv("DB_HOST", "127.0.0.1"),
            "port": int(os.getenv("DB_PORT", 5432)),
            "database": os.getenv("DB_NAME", "trademark_db"),
            "user": os.getenv("DB_USER", "turk_patent"),
            "password": os.getenv("DB_PASSWORD", ""),
            "connect_timeout": 30,
        }


def _connect():
    return psycopg2.connect(**_db_config())


def _table_exists(cur, name: str) -> bool:
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
        (name,),
    )
    return bool(cur.fetchone()[0])


def _enum_exists(cur, name: str) -> bool:
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = %s)",
        (name,),
    )
    return bool(cur.fetchone()[0])


def _holders_table_exists(cur) -> bool:
    return _table_exists(cur, "holders")


SQL_PATH = PROJECT_ROOT / "migrations" / "designs.sql"
DESCRIPTIONS_SQL_PATH = PROJECT_ROOT / "migrations" / "locarno_descriptions.sql"

DOWN_SQL = """
DROP TABLE IF EXISTS design_events CASCADE;
DROP TABLE IF EXISTS design_views CASCADE;
DROP TABLE IF EXISTS designs CASCADE;
DROP TABLE IF EXISTS locarno_classes_lookup CASCADE;
DROP TYPE IF EXISTS design_status;
"""


def apply_up(verbose: bool = True) -> None:
    sql = SQL_PATH.read_text(encoding="utf-8")
    with _connect() as conn:
        with conn.cursor() as cur:
            if not _holders_table_exists(cur):
                raise RuntimeError(
                    "designs migration requires the existing 'holders' table; run the trademark "
                    "schema bootstrap (deploy/schema.sql) first"
                )

            already_up = (
                _enum_exists(cur, "design_status")
                and _table_exists(cur, "designs")
                and _table_exists(cur, "design_views")
                and _table_exists(cur, "design_events")
                and _table_exists(cur, "locarno_classes_lookup")
            )
            if verbose:
                print(f"designs migration starting (already up = {already_up})")
            cur.execute(sql)
            if DESCRIPTIONS_SQL_PATH.exists():
                cur.execute(DESCRIPTIONS_SQL_PATH.read_text(encoding="utf-8"))
        conn.commit()
    if verbose:
        print("designs migration applied")


def apply_down(verbose: bool = True) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DOWN_SQL)
        conn.commit()
    if verbose:
        print("designs migration rolled back (tables + enum dropped)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run_designs_migration")
    parser.add_argument("--down", action="store_true",
                        help="drop the design tables + enum (destructive)")
    args = parser.parse_args(argv)
    if args.down:
        apply_down()
    else:
        apply_up()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

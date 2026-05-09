"""Run the Patent / Faydalı Model schema migration.

Creates the patent_record_type enum, ipc_classes_lookup, patents,
patent_holders, patent_inventors, patent_attorneys, patent_priorities,
patent_figures, and patent_events tables. Idempotent — safe to re-run.

Mirrors run_designs_migration.py one-for-one with patent-specific
table names.

Usage:
    python migrations/run_patents_migration.py            # apply
    python migrations/run_patents_migration.py --down     # drop the new objects (rollback)
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


SQL_PATH = PROJECT_ROOT / "migrations" / "patents.sql"

# Drop in reverse FK dependency order so CASCADE doesn't matter for
# correctness, but the explicit ordering documents the dependency
# graph for anyone reading.
DOWN_SQL = """
DROP TABLE IF EXISTS patent_events CASCADE;
DROP TABLE IF EXISTS patent_figures CASCADE;
DROP TABLE IF EXISTS patent_priorities CASCADE;
DROP TABLE IF EXISTS patent_attorneys CASCADE;
DROP TABLE IF EXISTS patent_inventors CASCADE;
DROP TABLE IF EXISTS patent_holders CASCADE;
DROP TABLE IF EXISTS patents CASCADE;
DROP TABLE IF EXISTS ipc_classes_lookup CASCADE;
DROP TYPE IF EXISTS patent_record_type;
"""


def apply_up(verbose: bool = True) -> None:
    sql = SQL_PATH.read_text(encoding="utf-8")
    with _connect() as conn:
        with conn.cursor() as cur:
            if not _holders_table_exists(cur):
                raise RuntimeError(
                    "patents migration requires the existing 'holders' table; run the "
                    "trademark schema bootstrap (deploy/schema.sql) first"
                )

            already_up = (
                _enum_exists(cur, "patent_record_type")
                and _table_exists(cur, "patents")
                and _table_exists(cur, "patent_holders")
                and _table_exists(cur, "patent_inventors")
                and _table_exists(cur, "patent_attorneys")
                and _table_exists(cur, "patent_priorities")
                and _table_exists(cur, "patent_figures")
                and _table_exists(cur, "patent_events")
                and _table_exists(cur, "ipc_classes_lookup")
            )
            if verbose:
                print(f"patents migration starting (already up = {already_up})")
            cur.execute(sql)
        conn.commit()
    if verbose:
        print("patents migration applied")


def apply_down(verbose: bool = True) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DOWN_SQL)
        conn.commit()
    if verbose:
        print("patents migration rolled back (tables + enum dropped)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run_patents_migration")
    parser.add_argument("--down", action="store_true",
                        help="drop the patent tables + enum (destructive)")
    args = parser.parse_args(argv)
    if args.down:
        apply_down()
    else:
        apply_up()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

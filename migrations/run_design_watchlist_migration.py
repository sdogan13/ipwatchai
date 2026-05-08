"""Run the design watchlist + alerts schema migration.

Creates the ``design_watchlist_mt`` and ``design_alerts_mt`` tables. Idempotent.

Usage:
    python migrations/run_design_watchlist_migration.py            # apply
    python migrations/run_design_watchlist_migration.py --down     # drop both tables (destructive)
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


SQL_PATH = PROJECT_ROOT / "migrations" / "design_watchlist.sql"

DOWN_SQL = """
DROP TABLE IF EXISTS design_alerts_mt CASCADE;
DROP TABLE IF EXISTS design_watchlist_mt CASCADE;
"""


def apply_up(verbose: bool = True) -> None:
    sql = SQL_PATH.read_text(encoding="utf-8")
    with _connect() as conn:
        with conn.cursor() as cur:
            for required in ("organizations", "users", "designs"):
                if not _table_exists(cur, required):
                    raise RuntimeError(
                        f"design watchlist migration requires the '{required}' table; run the "
                        "core schema + designs bootstrap first"
                    )
            already_up = (
                _table_exists(cur, "design_watchlist_mt")
                and _table_exists(cur, "design_alerts_mt")
            )
            if verbose:
                print(f"design_watchlist migration starting (already up = {already_up})")
            cur.execute(sql)
        conn.commit()
    if verbose:
        print("design_watchlist migration applied")


def apply_down(verbose: bool = True) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DOWN_SQL)
        conn.commit()
    if verbose:
        print("design_watchlist migration rolled back (both tables dropped)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run_design_watchlist_migration")
    parser.add_argument("--down", action="store_true",
                        help="drop design_watchlist_mt + design_alerts_mt (destructive)")
    args = parser.parse_args(argv)
    if args.down:
        apply_down()
    else:
        apply_up()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

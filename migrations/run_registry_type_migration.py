"""Apply the registry_type discriminator to the trademarks table.

Companion to designs.sql / run_designs_migration.py — gives both registries
a stable internal label so queries can join across them.

Usage:
    python migrations/run_registry_type_migration.py            # apply
    python migrations/run_registry_type_migration.py --down     # drop the column + constraint + index
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


SQL_PATH = PROJECT_ROOT / "migrations" / "registry_type.sql"

DOWN_SQL = """
ALTER TABLE trademarks DROP CONSTRAINT IF EXISTS trademarks_registry_type_check;
DROP INDEX IF EXISTS idx_tm_registry_type;
ALTER TABLE trademarks DROP COLUMN IF EXISTS registry_type;
"""


def apply_up(verbose: bool = True) -> None:
    sql = SQL_PATH.read_text(encoding="utf-8")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "SELECT registry_type, COUNT(*) FROM trademarks GROUP BY 1 ORDER BY 1"
            )
            rows = cur.fetchall()
        conn.commit()
    if verbose:
        print("registry_type migration applied to trademarks")
        for v, n in rows:
            print(f"  {v:10s} {n:,}")


def apply_down(verbose: bool = True) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DOWN_SQL)
        conn.commit()
    if verbose:
        print("registry_type column dropped from trademarks")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run_registry_type_migration")
    p.add_argument("--down", action="store_true",
                   help="drop the registry_type column from trademarks (destructive)")
    args = p.parse_args(argv)
    if args.down:
        apply_down()
    else:
        apply_up()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the explicit ingest runtime setup migration."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()

try:
    from config.settings import settings
except Exception:
    settings = None

from pipeline.ingest_bootstrap import apply_ingest_runtime_setup, default_ingest_root


def _connection_kwargs():
    kwargs = {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", 5432)),
        "database": os.getenv("DB_NAME", "trademark_db"),
        "user": os.getenv("DB_USER", "turk_patent"),
        "password": os.getenv("DB_PASSWORD", ""),
        "connect_timeout": 30,
    }
    if settings is not None:
        kwargs.update(
            {
                "host": settings.database.host,
                "port": settings.database.port,
                "database": settings.database.name,
                "user": settings.database.user,
                "password": settings.database.password,
            }
        )
    return kwargs


def run_migration() -> bool:
    conn = psycopg2.connect(**_connection_kwargs())
    try:
        apply_ingest_runtime_setup(conn, root_dir=default_ingest_root())
        print("Migration complete: ingest runtime setup applied")
        return True
    except Exception as exc:
        conn.rollback()
        print(f"Migration failed: {exc}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)

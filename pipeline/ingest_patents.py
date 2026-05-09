"""Patent / Faydalı Model DB ingest.

Reads each ``bulletins/Patent__Faydali_Model/PT_*/metadata.json`` and
upserts to the patent tables created by ``migrations/patents.sql``.
Idempotent — re-running is a no-op (records match on natural key
``publication_no``; child tables match on ``(patent_id, seq)``).

Reuses the existing ``holders`` table for applicants (TPECLIENT IDs
are shared across the trademark + design + patent registries — locked
decision in patent_processing_decisions memory).

Typical workflow:
  1. ``python -m pipeline.reconcile_patent --all``     # produces metadata.json
  2. ``python embeddings_patent.py --all``             # adds embeddings (optional;
                                                         records ingest with NULL
                                                         vectors otherwise)
  3. ``python -m pipeline.ingest_patents --all``       # JSON → DB rows

CLI::

    python -m pipeline.ingest_patents                          # all PT_*/metadata.json
    python -m pipeline.ingest_patents --bulletin PT_2025_8_2025-08-21
    python -m pipeline.ingest_patents --bulletins-root ...
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv()

_LOCAL_DEFAULT_BULLETINS_DIR = PROJECT_ROOT / "bulletins" / "Patent__Faydali_Model"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [PATENT-INGEST] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.patent_ingest")


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------


def _db_config() -> Dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def to_halfvec_literal(values: Optional[Iterable[float]]) -> Optional[str]:
    """``List[float]`` → ``'[v1,v2,...]'`` literal for casting to halfvec(N)
    in SQL. Returns ``None`` for empty/None input so the caller can pass
    ``None`` straight into a nullable halfvec column."""
    if values is None:
        return None
    materialized = list(values)
    if not materialized:
        return None
    return "[" + ",".join(f"{float(v):.6f}" for v in materialized) + "]"


def parse_date_safe(value: Optional[str]) -> Optional[date]:
    """Parse an ISO date string; return ``None`` on missing/malformed."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def figure_source(image_path: Optional[str]) -> str:
    """Decide ``patent_figures.source`` ('CD' or 'PDF') from the image_path.

    CD TIFFs land at ``figures/{year}_{appno}.tif``; PDF PNGs at
    ``figures/{year}_{appno}_p{page}_{idx}.png``. Extension is the
    cleanest discriminator (TIFF iff CD post the unified-folder
    refactor). Defaults to 'PDF' when image_path is None or unknown
    extension — CASCADE prevents orphan rows so a wrong-source label
    is recoverable on re-ingest.
    """
    if isinstance(image_path, str) and image_path.lower().endswith(".tif"):
        return "CD"
    return "PDF"


# ---------------------------------------------------------------------------
# Holder resolution (mirrors ingest_designs.resolve_holder_id but
# patent applicants have no TPECLIENT_ID, so name-match only)
# ---------------------------------------------------------------------------


def resolve_holder_id(cur, holder: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the UUID of a matching ``holders`` row; insert if missing.

    Patent applicants don't carry a ``tpe_client_id`` (CD's HOLDER
    table doesn't expose one for patents the way the trademark CD
    does). Resolution falls back to case-insensitive exact-name match,
    inserting a new row when nothing matches.

    This duplicates holder rows for the same legal entity when names
    differ (e.g. "ACME A.Ş." vs "ACME ANONIM ŞIRKETI") — accepted
    trade-off; canonicalising holder names is a separate task.
    """
    if not holder:
        return None
    name = (holder.get("name") or "").strip()
    if not name:
        return None

    cur.execute(
        "SELECT id FROM holders WHERE LOWER(name) = LOWER(%s) LIMIT 1",
        (name,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        """
        INSERT INTO holders (name, address, city, country, postal_code)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            name,
            holder.get("address"),
            holder.get("city"),
            holder.get("country"),
            holder.get("postal_code"),
        ),
    )
    return cur.fetchone()[0]

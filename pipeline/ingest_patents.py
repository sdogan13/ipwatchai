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


# ---------------------------------------------------------------------------
# Patents row upsert
# ---------------------------------------------------------------------------

# Column list mirrors migrations/patents.sql §3 (patents main table).
# Order matters because INSERT/UPDATE share the same dict keys.
PATENT_UPSERT_COLS = (
    "registry_type",
    "application_no", "publication_no", "kind_code", "record_type",
    "application_date", "publication_date", "grant_date",
    "bulletin_no", "bulletin_date",
    "title", "abstract", "ipc_classes", "patent_type",
    "title_abstract_embedding", "primary_figure_embedding",
    "source_format", "source_archive", "source_pdf",
    "bulletin_folder",
    "page_range_start", "page_range_end",
)

# Halfvec columns need ::halfvec casting. Array column needs no cast.
_HALFVEC_COLS = frozenset({
    "title_abstract_embedding", "primary_figure_embedding",
})

# Allowed values for the patent_record_type enum (mirrors patents.sql).
_VALID_RECORD_TYPES = frozenset({
    "GRANTED_PATENT", "GRANTED_UM",
    "PUBLISHED_APP", "PUBLISHED_UM_APP",
    "EP_FASCICLE", "LEGACY", "UNKNOWN",
})


def _patent_row(
    record: Dict[str, Any],
    doc: Dict[str, Any],
    *,
    bulletin_folder: str,
) -> Dict[str, Any]:
    """Project one unified-record dict + parent doc into a patents-row dict.

    ``record`` is one entry from ``metadata.json["records"]`` (after
    Stage 4 reconcile + Stage 6 embedding). ``doc`` is the parent
    metadata.json (provides bulletin_no/bulletin_date/source_archive/
    source_pdf — denorm'd onto every row for query convenience).
    ``bulletin_folder`` is the PT_*/ folder name, stored verbatim so
    figure paths can be resolved without re-deriving from bulletin_no.

    record_type values that aren't recognised collapse to 'UNKNOWN'
    (defensive — Stage 3's classifier may emit values the schema
    doesn't allow if the enum is ever extended on one side without
    the other).
    """
    rt = record.get("record_type") or "UNKNOWN"
    if rt not in _VALID_RECORD_TYPES:
        rt = "UNKNOWN"

    page_range = record.get("page_range") or [None, None]
    if not isinstance(page_range, list) or len(page_range) < 2:
        page_range = [None, None]

    return {
        "registry_type": "patent",
        "application_no": record.get("application_no"),
        "publication_no": record.get("publication_no"),
        "kind_code": record.get("kind_code"),
        "record_type": rt,
        "application_date": parse_date_safe(record.get("application_date")),
        "publication_date": parse_date_safe(record.get("publication_date")),
        "grant_date": parse_date_safe(record.get("grant_date")),
        "bulletin_no": doc.get("bulletin_no"),
        "bulletin_date": parse_date_safe(doc.get("bulletin_date")),
        "title": record.get("title"),
        "abstract": record.get("abstract"),
        "ipc_classes": list(record.get("ipc_classes") or []),
        "patent_type": record.get("patent_type"),
        "title_abstract_embedding": to_halfvec_literal(
            record.get("title_abstract_embedding"),
        ),
        "primary_figure_embedding": to_halfvec_literal(
            record.get("primary_figure_embedding"),
        ),
        "source_format": (record.get("source_format") or "CD"),
        "source_archive": doc.get("source_archive"),
        "source_pdf": doc.get("source_pdf"),
        "bulletin_folder": bulletin_folder,
        "page_range_start": page_range[0],
        "page_range_end": page_range[1],
    }


def upsert_patent(cur, row: Dict[str, Any]) -> str:
    """Find-or-insert a patents row by natural key. Returns the patent UUID.

    Natural unique key is ``publication_no`` (per the
    patent_publication_no_natural_key memory: same application can
    ship multiple publications in one bulletin, so application_no
    can't be the dedup key). For the rare records with blank
    publication_no (142 in bulletin 2019/11 — see kind-code-gap
    memory), falls back to ``(application_no, kind_code, bulletin_no)``
    so re-ingest doesn't duplicate them.
    """
    if row.get("publication_no"):
        cur.execute(
            "SELECT id FROM patents WHERE publication_no = %s",
            (row["publication_no"],),
        )
    else:
        # Defensive fallback for malformed records (no publication_no).
        cur.execute(
            """
            SELECT id FROM patents
            WHERE application_no = %s
              AND COALESCE(kind_code, '') = COALESCE(%s, '')
              AND bulletin_no = %s
            """,
            (
                row["application_no"],
                row.get("kind_code"),
                row["bulletin_no"],
            ),
        )
    existing = cur.fetchone()

    placeholders_list = []
    for c in PATENT_UPSERT_COLS:
        if c in _HALFVEC_COLS:
            placeholders_list.append(f"%({c})s::halfvec")
        else:
            placeholders_list.append(f"%({c})s")
    placeholders = ", ".join(placeholders_list)
    cols_sql = ", ".join(PATENT_UPSERT_COLS)

    if existing:
        update_assignments = ", ".join(
            f"{c} = %({c})s::halfvec" if c in _HALFVEC_COLS
            else f"{c} = %({c})s"
            for c in PATENT_UPSERT_COLS
        ) + ", updated_at = NOW()"
        params = dict(row)
        params["__id__"] = existing[0]
        cur.execute(
            f"UPDATE patents SET {update_assignments} WHERE id = %(__id__)s RETURNING id",
            params,
        )
        return cur.fetchone()[0]

    cur.execute(
        f"INSERT INTO patents ({cols_sql}) VALUES ({placeholders}) RETURNING id",
        row,
    )
    return cur.fetchone()[0]

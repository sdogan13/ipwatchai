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
import re
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


def _scrub_nul(value: Any) -> Any:
    """Recursively strip NUL (U+0000) characters from string values in
    a JSON-loaded structure. PostgreSQL TEXT columns reject NUL bytes
    and the upsert raises ``ValueError('A string literal cannot contain
    NUL (0x00) characters.')``. PyMuPDF occasionally surfaces a NUL
    when an OCR'd glyph fails to map (saw it on bulletin 2025/2 in the
    abstract field of one record). Apply this to ``payload`` and the
    events doc right after ``json.loads`` so every downstream row
    builder sees clean strings without per-builder fix-up.
    """
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, list):
        return [_scrub_nul(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_nul(v) for k, v in value.items()}
    return value


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

    # Conservative-normalization dedup (lower + strip punct + collapse
    # spaces). Plain LOWER(name)=LOWER(%s) used to leak duplicates for
    # "CO. LTD." vs "CO.  LTD." etc. — see holders_consolidate_dups_no_tpe.
    from pipeline.holder_helpers import find_holder_id_by_normalized_name
    existing_id = find_holder_id_by_normalized_name(cur, name)
    if existing_id:
        return existing_id

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


# ---------------------------------------------------------------------------
# Child-table upserts (replace-style: DELETE + INSERT)
# ---------------------------------------------------------------------------
#
# Each child table is keyed on (patent_id, seq). On re-ingest we
# DELETE all child rows for the patent and re-INSERT from the JSON.
# Cleaner than per-row UPSERT because it handles changes in row count
# (e.g., a record gaining a 2nd holder between runs) without leaving
# stale rows. Costs more INSERTs on re-ingest but the alternative
# (UPSERT + manual stale-row deletion) is more code and easier to
# get wrong.


def replace_holders(
    cur,
    patent_id: str,
    holders: List[Dict[str, Any]],
) -> int:
    """Delete + re-insert this patent's holder rows. Returns count inserted."""
    cur.execute("DELETE FROM patent_holders WHERE patent_id = %s", (patent_id,))
    inserted = 0
    for seq, holder in enumerate(holders or [], start=1):
        name = (holder.get("name") or "").strip()
        if not name:
            continue
        holder_id = resolve_holder_id(cur, holder)
        cur.execute(
            """
            INSERT INTO patent_holders
                (patent_id, holder_id, seq, name, address, city, state,
                 postal_code, country)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                patent_id, holder_id, seq, name,
                holder.get("address"), holder.get("city"),
                holder.get("state"), holder.get("postal_code"),
                holder.get("country"),
            ),
        )
        inserted += 1
    return inserted


def replace_inventors(
    cur,
    patent_id: str,
    inventors: List[Dict[str, Any]],
) -> int:
    """Delete + re-insert this patent's inventor rows."""
    cur.execute("DELETE FROM patent_inventors WHERE patent_id = %s", (patent_id,))
    inserted = 0
    for seq, inv in enumerate(inventors or [], start=1):
        name = (inv.get("name") or "").strip()
        if not name:
            continue
        cur.execute(
            """
            INSERT INTO patent_inventors
                (patent_id, seq, name, address, city, state,
                 postal_code, country)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                patent_id, seq, name,
                inv.get("address"), inv.get("city"),
                inv.get("state"), inv.get("postal_code"),
                inv.get("country"),
            ),
        )
        inserted += 1
    return inserted


def replace_attorneys(
    cur,
    patent_id: str,
    attorneys: List[Dict[str, Any]],
) -> int:
    """Delete + re-insert this patent's attorney rows.

    JSON ships ``no`` (CD-only TPE patent-attorney registry ID); the
    schema column is ``agent_no`` to avoid the SQL keyword collision.
    """
    cur.execute("DELETE FROM patent_attorneys WHERE patent_id = %s", (patent_id,))
    inserted = 0
    for seq, att in enumerate(attorneys or [], start=1):
        name = (att.get("name") or "").strip()
        if not name:
            continue
        cur.execute(
            """
            INSERT INTO patent_attorneys
                (patent_id, seq, agent_no, name, firm, address)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                patent_id, seq,
                att.get("no"), name, att.get("firm"), att.get("address"),
            ),
        )
        inserted += 1
    return inserted


def replace_priorities(
    cur,
    patent_id: str,
    priorities: List[Dict[str, Any]],
) -> int:
    """Delete + re-insert this patent's priority rows."""
    cur.execute("DELETE FROM patent_priorities WHERE patent_id = %s", (patent_id,))
    inserted = 0
    for seq, p in enumerate(priorities or [], start=1):
        cur.execute(
            """
            INSERT INTO patent_priorities
                (patent_id, seq, priority_no, priority_date, country)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                patent_id, seq,
                p.get("priority_no"),
                parse_date_safe(p.get("priority_date")),
                p.get("country"),
            ),
        )
        inserted += 1
    return inserted


def replace_figures(
    cur,
    patent_id: str,
    figures: List[Dict[str, Any]],
) -> int:
    """Delete + re-insert this patent's figure rows.

    Figure dicts come in two shapes after the unified-folder + dedup
    refactor:
      - CD TIFF kept: ``{"image_path": "figures/{Y}_{N}.tif",
        "embeddings": {dinov2_vitl14, clip_vitb32}}``
      - PDF figure dedup'd against CD: ``{"page": <n>}`` (image_path
        absent because the PNG was deleted; page/xref preserved for
        traceability)
      - PDF figure kept (no CD): full
        ``{"image_path", "page", "xref", "bbox", "width", "height",
        "embeddings"}``
    All three land cleanly in patent_figures with NULL fields where
    metadata is absent.
    """
    cur.execute("DELETE FROM patent_figures WHERE patent_id = %s", (patent_id,))
    inserted = 0
    for seq, fig in enumerate(figures or [], start=1):
        emb = fig.get("embeddings") or {}
        cur.execute(
            """
            INSERT INTO patent_figures
                (patent_id, seq, source, image_path, page, image_xref,
                 bbox, width, height, dinov2_vitl14, clip_vitb32)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::halfvec, %s::halfvec)
            """,
            (
                patent_id, seq,
                figure_source(fig.get("image_path")),
                fig.get("image_path"),
                fig.get("page"),
                # JSON sometimes uses 'xref'; schema column is image_xref.
                fig.get("xref") or fig.get("image_xref"),
                fig.get("bbox"),
                fig.get("width"),
                fig.get("height"),
                to_halfvec_literal(emb.get("dinov2_vitl14")),
                to_halfvec_literal(emb.get("clip_vitb32")),
            ),
        )
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Event ingest (Stage 7 patent_events)
# ---------------------------------------------------------------------------


_BULLETIN_NO_FORMAT_RE = re.compile(r"^\s*(\d{4})[/-](\d{1,2})\s*$")


def _normalise_bulletin_no(raw: Optional[str]) -> Optional[str]:
    """Canonicalise bulletin_no across CD/PDF/events shapes.

    CD writes ``"2025/8"``, PDF writes ``"2025-08"``, events.json
    inherits from the PDF cover. Both canonicalise to ``"2025/8"``
    so the patents.bulletin_no column (set during metadata ingest)
    matches patent_events.bulletin_no (set here).

    Same logic as pipeline.reconcile_patent._normalise_bulletin_no —
    duplicated rather than imported to avoid circular dependency
    (reconcile imports nothing from ingest, ingest mustn't depend on
    reconcile to keep them independent stages).
    """
    if not raw:
        return None
    match = _BULLETIN_NO_FORMAT_RE.match(raw)
    if not match:
        return raw.strip() or None
    year, month = match.group(1), match.group(2).lstrip("0") or "0"
    return f"{year}/{month}"


def _resolve_patent_id_for_event(cur, application_no: Optional[str]) -> Optional[str]:
    """Look up a patents.id for an event by its application_no.

    Same application can have multiple publications in a bulletin
    (B grant + A1 republication), so a 1:1 link isn't always
    possible. Returns the UUID only when the lookup is unambiguous;
    NULL otherwise. Stage 5 already has this for figures so reuse
    the precedent: patent_events.patent_id is also nullable per the
    Stage 0 schema (ON DELETE SET NULL).
    """
    if not application_no:
        return None
    cur.execute(
        "SELECT id FROM patents WHERE application_no = %s LIMIT 2",
        (application_no,),
    )
    rows = cur.fetchall()
    return rows[0][0] if len(rows) == 1 else None


def replace_events(cur, events_doc: Dict[str, Any]) -> int:
    """Replace this bulletin's patent_events rows from a parsed
    events.json doc.

    DELETE all rows where bulletin_no = events_doc.bulletin_no,
    then INSERT every event. Same replace-style pattern as the
    child-table upserts: handles re-extraction (phrase table
    extended → some events change event_type → fingerprint changes)
    without leaving stale rows.

    event_date defaults to bulletin_date when the per-event date is
    absent (which is always for the index-page events: they have
    no per-event date, only the bulletin's publication date).
    """
    bulletin_no = _normalise_bulletin_no(events_doc.get("bulletin_no"))
    bulletin_date = parse_date_safe(events_doc.get("bulletin_date"))
    if not bulletin_no:
        # Don't DELETE-FROM-WHERE-NULL — that would no-op silently.
        # If we got here without a bulletin_no, the events.json is
        # malformed; surface the failure via 0 inserted.
        return 0

    cur.execute(
        "DELETE FROM patent_events WHERE bulletin_no = %s",
        (bulletin_no,),
    )
    inserted = 0
    for ev in events_doc.get("events", []):
        application_no = ev.get("application_no")
        patent_id = _resolve_patent_id_for_event(cur, application_no)
        cur.execute(
            """
            INSERT INTO patent_events
                (patent_id, application_no, event_type, event_date,
                 bulletin_no, bulletin_date, page, free_text,
                 event_fingerprint)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_fingerprint) DO NOTHING
            """,
            (
                patent_id,
                application_no,
                ev.get("event_type", "UNKNOWN"),
                bulletin_date,                  # event_date inherits from bulletin
                bulletin_no,
                bulletin_date,
                ev.get("page"),
                ev.get("free_text"),
                ev.get("fingerprint"),
            ),
        )
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Per-bulletin orchestration
# ---------------------------------------------------------------------------


def _is_bulletin_fresh(cur, bulletin_folder: Path) -> bool:
    """True when the DB already holds rows for this bulletin whose
    most recent ``updated_at`` is at least as new as the latest
    source file (``metadata.json`` and, if present, ``events.json``).

    Skip-if-fresh check for ``--all`` re-runs. Comparing ``MAX(updated_at)``
    against source mtimes lets us avoid re-upserting hundreds of
    thousands of identical rows on a no-op pass; ``--force`` overrides.

    The transaction-wrapped ingest commits all-or-nothing per bulletin,
    so a partially-ingested bulletin leaves no rows behind and the DB
    side of the check returns NULL → not fresh → ingest runs.
    """
    metadata_path = bulletin_folder / "metadata.json"
    if not metadata_path.is_file():
        return False
    sources_mtime = metadata_path.stat().st_mtime
    events_path = bulletin_folder / "events.json"
    if events_path.is_file():
        try:
            sources_mtime = max(sources_mtime, events_path.stat().st_mtime)
        except OSError:
            pass

    cur.execute(
        """
        SELECT EXTRACT(EPOCH FROM MAX(updated_at))
        FROM patents
        WHERE bulletin_folder = %s
        """,
        (bulletin_folder.name,),
    )
    row = cur.fetchone()
    if not row or row[0] is None:
        return False
    return float(row[0]) >= sources_mtime


def ingest_bulletin(
    bulletin_folder: Path,
    *,
    conn=None,
    force: bool = False,
) -> Dict[str, Any]:
    """Ingest one ``PT_*/metadata.json`` into the patent tables.

    Wraps the entire bulletin in a single transaction: either every
    record's patent row + children land, or none do. Re-running on
    the same folder is a no-op for unchanged records (UPDATE on
    matching publication_no) and safely refreshes children via
    DELETE+INSERT.

    ``force=False`` (default) skips the bulletin entirely when the DB
    already holds rows whose ``MAX(updated_at)`` is at least as new as
    ``metadata.json`` / ``events.json`` mtimes. Pass ``force=True`` to
    re-upsert unconditionally.

    ``conn`` is optional; if absent, opens a fresh connection. Tests
    pass an in-memory mock or pre-opened conn for fixture reuse.

    Returns a stats dict for CLI logging:
      {bulletin, records_processed, holders, inventors, attorneys,
       priorities, figures}.
    """
    metadata_path = bulletin_folder / "metadata.json"
    if not metadata_path.is_file():
        return {"status": "no_metadata", "bulletin": bulletin_folder.name}

    payload = _scrub_nul(json.loads(metadata_path.read_text(encoding="utf-8")))
    records = payload.get("records", [])
    if not records:
        return {"status": "empty", "bulletin": bulletin_folder.name}

    owns_connection = conn is None
    if owns_connection:
        conn = _connect()

    stats = {
        "bulletin": bulletin_folder.name,
        "records_processed": 0,
        "holders_inserted": 0,
        "inventors_inserted": 0,
        "attorneys_inserted": 0,
        "priorities_inserted": 0,
        "figures_inserted": 0,
        "events_inserted": 0,
        "skipped": 0,
        "watchlist_alerts": 0,
    }
    upserted_patent_ids: List[str] = []

    try:
        with conn.cursor() as cur:
            if not force and _is_bulletin_fresh(cur, bulletin_folder):
                if owns_connection:
                    conn.close()
                return {
                    "status": "fresh_skip",
                    "bulletin": bulletin_folder.name,
                }
            for record in records:
                row = _patent_row(
                    record, payload, bulletin_folder=bulletin_folder.name,
                )
                # Records with no application_no AND no publication_no can't
                # be deduped reliably — skip them rather than create
                # rows that re-ingest can't recognise.
                if not row.get("publication_no") and not row.get("application_no"):
                    stats["skipped"] += 1
                    continue

                patent_id = upsert_patent(cur, row)
                if patent_id:
                    upserted_patent_ids.append(str(patent_id))
                stats["holders_inserted"] += replace_holders(
                    cur, patent_id, record.get("holders", []),
                )
                stats["inventors_inserted"] += replace_inventors(
                    cur, patent_id, record.get("inventors", []),
                )
                stats["attorneys_inserted"] += replace_attorneys(
                    cur, patent_id, record.get("attorneys", []),
                )
                stats["priorities_inserted"] += replace_priorities(
                    cur, patent_id, record.get("priorities", []),
                )
                stats["figures_inserted"] += replace_figures(
                    cur, patent_id, record.get("figures", []),
                )
                stats["records_processed"] += 1

            # Events come from a sibling events.json (Stage 7 output).
            # Optional — bulletin extracts that haven't run the events
            # pass yet just skip event ingest with no error. Once
            # patents are upserted we look up patent_id by app_no
            # within this same transaction so the FK resolution sees
            # rows we just inserted in this loop.
            events_path = bulletin_folder / "events.json"
            if events_path.is_file():
                events_doc = _scrub_nul(
                    json.loads(events_path.read_text(encoding="utf-8"))
                )
                stats["events_inserted"] = replace_events(cur, events_doc)
        conn.commit()
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()

    # Post-ingest watchlist scan. Runs against active patent watchlists,
    # scoped to the IDs we just upserted so cost is O(watchlists * new_ids)
    # rather than O(watchlists * full_corpus). Wrapped in try/except so a
    # failed scan never poisons a successful ingest.
    if upserted_patent_ids:
        try:
            from services.patent_scanner_service import trigger_patent_watchlist_scan
            stats["watchlist_alerts"] = trigger_patent_watchlist_scan(
                upserted_patent_ids,
                source_type="bulletin",
                source_reference=bulletin_folder.name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[!] %s: patent-watchlist scan failed: %r",
                bulletin_folder.name, exc,
            )

    stats["status"] = "ok"
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def find_bulletin_folders(
    bulletins_dir: Path,
    *,
    only: Optional[List[str]] = None,
) -> List[Path]:
    """Walk bulletins_dir for PT_-prefixed folders. ``only`` filters
    to a specific list of folder names."""
    if only:
        return [bulletins_dir / name for name in only]
    return sorted(
        p for p in bulletins_dir.iterdir()
        if p.is_dir() and p.name.startswith("PT_")
    )


def parse_argv(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pipeline.ingest_patents",
        description="Upsert reconciled patent metadata into the patent tables.",
    )
    parser.add_argument(
        "--bulletins-dir", type=Path, default=_LOCAL_DEFAULT_BULLETINS_DIR,
        help="Root containing PT_{Y}_{M}_{date}/ folders.",
    )
    parser.add_argument(
        "--bulletin", action="append", default=[],
        help="Specific PT_{Y}_{M}_{date} folder name. Repeat for multiple.",
    )
    parser.add_argument(
        "--all", action="store_true", dest="all_mode",
        help="Process every PT_*/metadata.json under --bulletins-dir.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-upsert even when the DB already holds rows whose "
             "MAX(updated_at) is newer than the source metadata files.",
    )
    ns = parser.parse_args(argv)
    if ns.all_mode and ns.bulletin:
        parser.error("--all is mutually exclusive with --bulletin")
    if not ns.all_mode and not ns.bulletin:
        parser.error("provide --bulletin (one or more) or --all")
    return ns


def main(argv=None) -> int:
    args = parse_argv(argv)
    folders = find_bulletin_folders(
        args.bulletins_dir,
        only=None if args.all_mode else args.bulletin,
    )
    if not folders:
        logger.warning("no bulletin folders to ingest")
        return 1

    succeeded: List[str] = []
    skipped: List[str] = []
    failed: List[str] = []

    for folder in folders:
        try:
            result = ingest_bulletin(folder, force=args.force)
        except Exception as exc:
            logger.error("[!] %s: %r", folder.name, exc)
            failed.append(folder.name)
            continue

        status = result.get("status", "?")
        if status == "ok":
            logger.info(
                "[+] %s: %d records (skipped=%d) — "
                "holders=%d inventors=%d attorneys=%d priorities=%d "
                "figures=%d events=%d",
                result["bulletin"], result["records_processed"], result["skipped"],
                result["holders_inserted"], result["inventors_inserted"],
                result["attorneys_inserted"], result["priorities_inserted"],
                result["figures_inserted"], result["events_inserted"],
            )
            succeeded.append(folder.name)
        elif status == "fresh_skip":
            logger.info(
                "[=] %s is fresh, skipping (use --force to override)",
                folder.name,
            )
            skipped.append(folder.name)
        else:
            logger.warning("[~] %s: %s", folder.name, status)

    logger.info(
        "Done: %d succeeded, %d skipped, %d failed",
        len(succeeded), len(skipped), len(failed),
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

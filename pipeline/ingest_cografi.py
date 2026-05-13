"""Coğrafi İşaret ve Geleneksel Ürün Adı DB ingest.

Reads each ``bulletins/Cografi_Isaret_ve_Geleneksel_Urun_Adi/CI_*/metadata.json``
and upserts to the cografi tables created by ``migrations/cografi.sql``.
Idempotent — re-running is a no-op (records match on the natural key
``(bulletin_no, section_key, COALESCE(application_no, registration_no::text, name))``;
child tables match on ``(record_id, seq)`` or ``(record_id, role, seq)``).

Reuses the existing ``holders`` table for applicants, registrants, and
agents (TPECLIENT IDs are shared across all four registries — locked
decision in patent_processing_decisions memory).

Typical workflow:
  1. ``python pdf_extract_cografi.py --all``           # produces metadata.json
  2. ``python embeddings_cografi.py --all``            # adds embeddings (optional;
                                                         records ingest with NULL
                                                         vectors otherwise)
  3. ``python -m pipeline.ingest_cografi --all``       # JSON → DB rows

CLI::

    python -m pipeline.ingest_cografi                       # all CI_*/metadata.json
    python -m pipeline.ingest_cografi --issue 220
    python -m pipeline.ingest_cografi --bulletins-root ...
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv()

_LOCAL_DEFAULT_BULLETINS_DIR = (
    PROJECT_ROOT / "bulletins" / "Cografi_Isaret_ve_Geleneksel_Urun_Adi"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [CI-INGEST] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.cografi_ingest")


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


def scrub_nul(value: Any) -> Any:
    """Recursively strip NUL (U+0000) characters from string values.

    PostgreSQL TEXT columns reject NUL bytes; PyMuPDF occasionally
    surfaces a NUL when an OCR'd glyph fails to map. Apply this right
    after ``json.loads`` so every downstream row builder sees clean
    strings without per-builder fix-up.
    """
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, list):
        return [scrub_nul(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub_nul(v) for k, v in value.items()}
    return value


def to_halfvec_literal(values: Optional[Iterable[float]]) -> Optional[str]:
    """``List[float]`` → ``'[v1,v2,...]'`` for casting to halfvec(N).

    Returns ``None`` for empty/None input so the caller can pass
    ``None`` straight into a nullable halfvec column.
    """
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


def parse_iso_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 timestamp; return ``None`` on missing/malformed."""
    if not value:
        return None
    try:
        # Accept the various ISO shapes the extractor might emit.
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (TypeError, ValueError):
        return None


_VALID_SECTION_KEYS = frozenset({
    "examined", "registered", "article_40_modified",
    "article_42_change_requests", "article_42_finalized",
    "article_43_modified", "corrections", "gazette_only_announcements",
})

_VALID_RECORD_TYPES = frozenset({"GI", "TPN", "UNKNOWN"})


def normalise_section_key(value: Optional[str]) -> str:
    """Defensive: collapse unknown section_key values to ``examined``.

    Should never happen given the extractor's enum, but the schema's
    ENUM type would reject an unknown value and the upsert would
    cascade-fail the whole bulletin.
    """
    if value in _VALID_SECTION_KEYS:
        return value
    logger.warning("unknown section_key %r — coercing to 'examined'", value)
    return "examined"


def normalise_record_type(value: Optional[str]) -> str:
    if value in _VALID_RECORD_TYPES:
        return value
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Holder resolution (mirrors ingest_patents.resolve_holder_id)
# ---------------------------------------------------------------------------


def resolve_holder_id(cur, name: str, address: Optional[str] = None) -> Optional[str]:
    """Find or create a row in the global ``holders`` table by name.

    Cografi applicants are typically public bodies (Belediye, Ticaret
    ve Sanayi Odası, Kaymakamlık, İl Tarım ve Orman Müdürlüğü) without
    a TPECLIENT_ID; we match case-insensitive on name only and create
    a new row when nothing matches. Same trade-off as patent ingest:
    duplicates the same legal entity if the bulletin spells the name
    differently — canonicalising holder names is a separate task.
    """
    name = (name or "").strip()
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
        INSERT INTO holders (name, address)
        VALUES (%s, %s)
        RETURNING id
        """,
        (name, address),
    )
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Records row upsert
# ---------------------------------------------------------------------------

# Column list mirrors migrations/cografi.sql §4 (cografi_records main table).
RECORD_UPSERT_COLS = (
    "registry_type",
    "bulletin_no", "bulletin_date",
    "section_key", "record_type",
    "application_no", "registration_no",
    "name", "application_date", "registration_date",
    "product_group", "gi_type", "geographical_boundary",
    "usage_description", "agent",
    "body_sections", "raw_text",
    "existing_registration_no",
    "correction_referenced_bulletin_no",
    "correction_referenced_bulletin_date",
    "correction_referenced_record_id",
    "correction_old_text", "correction_new_text",
    "text_embedding", "primary_figure_embedding",
    "bulletin_folder", "start_page",
    "extractor_version", "extracted_at", "embeddings_at",
)

# Halfvec columns need ``::halfvec`` casting on UPDATE/INSERT.
_HALFVEC_COLS = frozenset({"text_embedding", "primary_figure_embedding"})
# JSONB columns need explicit ``::jsonb`` cast since psycopg2 binds
# Python dicts as PostgreSQL TEXT by default for our adapter version.
_JSONB_COLS = frozenset({"body_sections"})


def _record_row(
    record: Dict[str, Any],
    bulletin: Dict[str, Any],
    *,
    bulletin_folder: str,
) -> Dict[str, Any]:
    """Project one extracted record + parent metadata into the row dict.

    ``record`` is one entry from ``metadata.json["records"][section_key]``
    after B2/C1. ``bulletin`` is the parent metadata.json (gives
    bulletin_no, bulletin_date, extracted_at, embeddings_at).
    """
    section_key = normalise_section_key(record.get("__section_key"))

    body_sections = record.get("body_sections") or {}
    if not isinstance(body_sections, dict):
        body_sections = {}

    return {
        "registry_type": "cografi",
        "bulletin_no": bulletin.get("bulletin_no"),
        "bulletin_date": parse_date_safe(bulletin.get("bulletin_date")),
        "section_key": section_key,
        "record_type": normalise_record_type(record.get("record_type")),
        "application_no": record.get("application_no"),
        "registration_no": record.get("registration_no"),
        "name": record.get("name") or "(unnamed)",
        "application_date": parse_date_safe(record.get("application_date")),
        "registration_date": parse_date_safe(record.get("registration_date")),
        "product_group": record.get("product_group"),
        "gi_type": record.get("gi_type"),
        "geographical_boundary": record.get("geographical_boundary"),
        "usage_description": record.get("usage_description"),
        "agent": record.get("agent"),
        "body_sections": json.dumps(body_sections, ensure_ascii=False),
        "raw_text": record.get("raw_text"),
        "existing_registration_no": record.get("existing_registration_no"),
        "correction_referenced_bulletin_no": record.get("referenced_bulletin_no"),
        "correction_referenced_bulletin_date": parse_date_safe(
            record.get("referenced_bulletin_date"),
        ),
        "correction_referenced_record_id": record.get("referenced_record_id"),
        "correction_old_text": record.get("correction_old"),
        "correction_new_text": record.get("correction_new"),
        "text_embedding": to_halfvec_literal(record.get("text_embedding")),
        "primary_figure_embedding": to_halfvec_literal(
            record.get("primary_figure_embedding"),
        ),
        "bulletin_folder": bulletin_folder,
        "start_page": record.get("start_page"),
        "extractor_version": bulletin.get("extractor_version"),
        "extracted_at": parse_iso_timestamp(bulletin.get("extracted_at")),
        "embeddings_at": parse_iso_timestamp(bulletin.get("embeddings_at")),
    }


def _build_upsert_sql(
    table: str,
    cols: Tuple[str, ...],
    *,
    halfvec_cols: frozenset = frozenset(),
    jsonb_cols: frozenset = frozenset(),
    conflict_target: str,
    update_cols: Optional[Tuple[str, ...]] = None,
    returning: str = "id",
) -> str:
    """Build a parameterised INSERT ... ON CONFLICT ... DO UPDATE SQL.

    Halfvec-typed columns get a ``%s::halfvec`` cast; JSONB columns get
    ``%s::jsonb``. ``conflict_target`` is the literal text of the
    ON CONFLICT clause (e.g. ``"(publication_no)"`` or
    ``"(bulletin_no, section_key, COALESCE(application_no, registration_no::text, name))"``).
    """
    placeholders = []
    for c in cols:
        if c in halfvec_cols:
            placeholders.append("%s::halfvec")
        elif c in jsonb_cols:
            placeholders.append("%s::jsonb")
        else:
            placeholders.append("%s")
    update_cols = update_cols or cols
    set_clause = ",\n        ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    return (
        f"INSERT INTO {table} (\n        " + ",\n        ".join(cols) + ")\n"
        f"VALUES (" + ", ".join(placeholders) + ")\n"
        f"ON CONFLICT {conflict_target} DO UPDATE SET\n        {set_clause},\n"
        f"        updated_at = NOW()\n"
        f"RETURNING {returning}"
    )


_RECORD_UPSERT_SQL = _build_upsert_sql(
    "cografi_records",
    RECORD_UPSERT_COLS,
    halfvec_cols=_HALFVEC_COLS,
    jsonb_cols=_JSONB_COLS,
    conflict_target=(
        "(bulletin_no, section_key, "
        "(COALESCE(application_no, registration_no::text, name)))"
    ),
    # updated_at is set by the trailing literal; everything else is from EXCLUDED.
)


def upsert_record(cur, row: Dict[str, Any]) -> str:
    """Find-or-update a cografi_records row by natural key.

    Returns the record UUID. Re-running with the same row updates
    every column to its current value (including embeddings, so a
    re-embed pass refreshes them) and bumps updated_at.
    """
    values = [row[c] for c in RECORD_UPSERT_COLS]
    cur.execute(_RECORD_UPSERT_SQL, values)
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Holders / change_requests / figures upserts
# ---------------------------------------------------------------------------


def _replace_record_holders(cur, record_id: str, record: Dict[str, Any]) -> int:
    """Refresh cografi_holders rows for ``record_id`` from the JSON record.

    Cografi has at most three roles per record (applicant_name +
    address, registrant_name + address — only one of those depending
    on section, plus optional agent). Replacement-style refresh keeps
    re-ingest idempotent without per-row natural-key gymnastics.
    Returns the number of rows inserted.
    """
    cur.execute(
        "DELETE FROM cografi_holders WHERE record_id = %s",
        (record_id,),
    )
    inserted = 0

    for role, name_key, addr_key in (
        ("APPLICANT", "applicant_name", "applicant_address"),
        ("REGISTRANT", "registrant_name", "registrant_address"),
        ("AGENT", "agent", None),
    ):
        name = record.get(name_key)
        if not isinstance(name, str) or not name.strip():
            continue
        address = record.get(addr_key) if addr_key else None
        if isinstance(address, str):
            address = address.strip() or None
        holder_id = resolve_holder_id(cur, name, address)
        cur.execute(
            """
            INSERT INTO cografi_holders (
                record_id, holder_id, role, seq, name, address
            ) VALUES (%s, %s, %s::cografi_holder_role, 1, %s, %s)
            """,
            (record_id, holder_id, role, name.strip(), address),
        )
        inserted += 1
    return inserted


def _replace_change_requests(cur, record_id: str, record: Dict[str, Any]) -> int:
    """Refresh cografi_change_requests rows for an Article 42 record."""
    cur.execute(
        "DELETE FROM cografi_change_requests WHERE record_id = %s",
        (record_id,),
    )
    changes = record.get("changes") or []
    if not isinstance(changes, list):
        return 0
    inserted = 0
    for seq, change in enumerate(changes, start=1):
        if not isinstance(change, dict):
            continue
        cur.execute(
            """
            INSERT INTO cografi_change_requests (
                record_id, seq, field, old_text, new_text
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                record_id,
                seq,
                (change.get("field") or "").strip() or "(unknown)",
                change.get("old"),
                change.get("new"),
            ),
        )
        inserted += 1
    return inserted


_FIGURE_COLS = (
    "record_id", "seq", "image_path", "page", "bbox",
    "width", "height", "dinov2_vitl14", "clip_vitb32",
)
_FIGURE_HALFVEC_COLS = frozenset({"dinov2_vitl14", "clip_vitb32"})


def _replace_figures(cur, record_id: str, record: Dict[str, Any]) -> int:
    """Refresh cografi_figures rows from the record's ``figures`` array.

    Replacement-style refresh — same rationale as holders. Per-figure
    embeddings ride along with the figure row so a re-embed pass also
    refreshes them when the metadata.json is re-ingested.
    """
    cur.execute("DELETE FROM cografi_figures WHERE record_id = %s", (record_id,))
    figures = record.get("figures") or []
    if not isinstance(figures, list):
        return 0
    inserted = 0
    for seq, fig in enumerate(figures, start=1):
        if not isinstance(fig, dict):
            continue
        image_path = fig.get("image_path")
        if not isinstance(image_path, str) or not image_path:
            continue
        emb = fig.get("embeddings") or {}
        bbox = fig.get("bbox")
        if not isinstance(bbox, list):
            bbox = None
        cur.execute(
            """
            INSERT INTO cografi_figures (
                record_id, seq, image_path, page, bbox,
                width, height, dinov2_vitl14, clip_vitb32
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::halfvec, %s::halfvec)
            """,
            (
                record_id,
                seq,
                image_path,
                fig.get("page"),
                bbox,
                fig.get("width"),
                fig.get("height"),
                to_halfvec_literal(emb.get("dinov2_vitl14")),
                to_halfvec_literal(emb.get("clip_vitb32")),
            ),
        )
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Per-bulletin orchestration
# ---------------------------------------------------------------------------


def ingest_metadata(cur, metadata_path: Path) -> Dict[str, Any]:
    """Ingest one bulletin's metadata.json into the cografi_* tables.

    Returns counters + the list of upserted record_ids so the caller
    can scope a post-ingest watchlist scan to just the rows that
    landed (avoids re-scanning the full corpus on every ingest run).
    """
    raw = metadata_path.read_text(encoding="utf-8")
    metadata = scrub_nul(json.loads(raw))
    bulletin_folder = metadata_path.parent.name

    counters: Dict[str, Any] = {
        "records": 0, "holders": 0, "change_requests": 0, "figures": 0,
        "record_ids": [],
    }

    records_by_section = metadata.get("records") or {}
    if not isinstance(records_by_section, dict):
        return counters

    for section_key, items in records_by_section.items():
        if not isinstance(items, list):
            continue
        for record in items:
            if not isinstance(record, dict):
                continue
            record = dict(record)
            record["__section_key"] = section_key

            row = _record_row(record, metadata, bulletin_folder=bulletin_folder)
            if not row.get("name"):
                logger.warning(
                    "[skip] %s: %s record without name", bulletin_folder, section_key,
                )
                continue
            try:
                record_id = upsert_record(cur, row)
            except Exception as exc:
                logger.warning(
                    "[skip] %s: %s record %r upsert failed: %r",
                    bulletin_folder, section_key, row.get("name"), exc,
                )
                continue
            counters["records"] += 1
            counters["record_ids"].append(record_id)
            counters["holders"] += _replace_record_holders(cur, record_id, record)
            if section_key in ("article_42_change_requests", "article_42_finalized"):
                counters["change_requests"] += _replace_change_requests(
                    cur, record_id, record,
                )
            counters["figures"] += _replace_figures(cur, record_id, record)
    return counters


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _iter_metadata(bulletins_root: Path) -> List[Path]:
    out: List[Path] = []
    if not bulletins_root.is_dir():
        return out
    for entry in sorted(bulletins_root.iterdir()):
        if entry.is_dir() and entry.name.startswith("CI_"):
            md = entry / "metadata.json"
            if md.is_file():
                out.append(md)
    return out


def parse_argv(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ingest_cografi", add_help=True)
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--issue", type=int, help="ingest a single bulletin by issue number")
    src.add_argument("--all", action="store_true",
                     help="ingest every CI_*/metadata.json under --bulletins-root")
    parser.add_argument(
        "--bulletins-root", type=Path, default=_LOCAL_DEFAULT_BULLETINS_DIR,
        help=f"bulletins root (default: {_LOCAL_DEFAULT_BULLETINS_DIR})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="parse and project rows; do NOT commit to DB",
    )
    parser.add_argument(
        "--skip-watchlist-scan", action="store_true",
        help="skip the post-ingest watchlist scan (still useful for backfills)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_argv(argv)

    if args.issue is not None:
        matches = sorted(args.bulletins_root.glob(f"CI_{args.issue}_*/metadata.json"))
        if not matches:
            logger.error("no metadata.json found for issue %d", args.issue)
            return 1
        paths = matches[:1]
    else:
        # Default behaviour matches patent + design ingest: --all is implicit.
        paths = _iter_metadata(args.bulletins_root)
    if not paths:
        logger.warning("no metadata.json inputs found under %s", args.bulletins_root)
        return 0

    started_at = datetime.now()
    totals = {"records": 0, "holders": 0, "change_requests": 0, "figures": 0}
    all_record_ids: List[str] = []
    failures = 0

    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                for p in paths:
                    try:
                        c = ingest_metadata(cur, p)
                    except Exception as exc:
                        logger.error("[!] %s: %r", p.relative_to(args.bulletins_root), exc)
                        failures += 1
                        if not args.dry_run:
                            raise
                        continue
                    for k in totals:
                        totals[k] += c[k]
                    all_record_ids.extend(c.get("record_ids") or [])
                    logger.info(
                        "[+] %s | records=%d holders=%d chreq=%d figures=%d",
                        p.parent.name, c["records"], c["holders"],
                        c["change_requests"], c["figures"],
                    )
                if args.dry_run:
                    logger.info("--dry-run: rolling back")
                    conn.rollback()
    finally:
        conn.close()

    elapsed = (datetime.now() - started_at).total_seconds()
    logger.info("done in %.1fs | totals: %s | failures=%d", elapsed, totals, failures)

    # Post-ingest watchlist scan — fan out scans for newly upserted
    # record_ids against every active cografi watchlist item. Failures
    # here are logged but never propagate; a busted watchlist row
    # should not poison a successful ingest run.
    if all_record_ids and not args.dry_run and not args.skip_watchlist_scan:
        try:
            from services.cografi_scanner_service import trigger_cografi_watchlist_scan
            new_alerts = trigger_cografi_watchlist_scan(
                all_record_ids,
                source_type="ingest",
                source_reference=str(args.issue) if args.issue else "all",
            )
            logger.info("post-ingest watchlist scan: %d new alerts", new_alerts)
        except Exception:
            logger.exception("post-ingest watchlist scan failed")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Tasarım (industrial design) DB ingest.

Reads each ``bulletins/Tasarim/TS_*/metadata.json`` and ``events.json`` and
upserts to ``designs``, ``design_views``, ``design_events``. Idempotent —
re-running is a no-op (designs match on natural keys; events are
fingerprint-deduped).

Reuses the existing ``holders`` table for applicants (TPECLIENT IDs are
shared across the trademark and design registries).

CLI::

    python -m pipeline.ingest_designs                          # all issues with metadata.json
    python -m pipeline.ingest_designs --issue TS_483_2026-04-24
    python -m pipeline.ingest_designs --bulletins-root ...
    python -m pipeline.ingest_designs --skip-events            # designs/views only
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv()

_LOCAL_DEFAULT_BULLETINS_DIR = PROJECT_ROOT / "bulletins" / "Tasarim"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [TASARIM-INGEST] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.tasarim_ingest")


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
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

# section -> current_status mapping
SECTION_STATUS_MAP = {
    "tr_native": "Yayında",
    "deferred_lifted": "Yayında",
    "republished": "Yayında",
    "hague": "Yayında",
    "deferred": "Yayım Ertelendi",
}


def status_for_section(section: str) -> str:
    return SECTION_STATUS_MAP.get(section, "Bilinmiyor")


def opposition_end_date(bulletin_date: Optional[str]) -> Optional[date]:
    """Bulletin date + 3 months. Returns None if bulletin_date is unparseable."""
    if not bulletin_date:
        return None
    try:
        d = date.fromisoformat(bulletin_date)
    except (TypeError, ValueError):
        return None
    return d + timedelta(days=90)


def to_halfvec_literal(values: Optional[Iterable[float]]) -> Optional[str]:
    """List[float] -> ``'[v1,v2,...]'`` for casting to halfvec(N) in SQL."""
    if values is None:
        return None
    materialized = list(values)
    if not materialized:
        return None
    return "[" + ",".join(f"{float(v):.6f}" for v in materialized) + "]"


def parse_date_safe(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _first_applicant(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    apps = record.get("applicants") or []
    return apps[0] if apps else None


# ---------------------------------------------------------------------------
# Holder resolution
# ---------------------------------------------------------------------------

def resolve_holder_id(cur, applicant: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return UUID of a matching holders row; insert if missing.

    Resolution order:
      1. by ``tpe_client_id`` (the applicant's ``id`` field) when present
      2. by exact name match (case-insensitive) — used for Hague records that
         have no TPECLIENT id
      3. insert a new row otherwise
    """
    if not applicant:
        return None
    name = (applicant.get("name") or "").strip()
    if not name:
        return None
    tpe_id = applicant.get("id")

    if tpe_id:
        cur.execute("SELECT id FROM holders WHERE tpe_client_id = %s", (str(tpe_id),))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            """
            INSERT INTO holders (tpe_client_id, name, address, country)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tpe_client_id) DO UPDATE
                SET name = EXCLUDED.name, address = EXCLUDED.address, country = EXCLUDED.country,
                    updated_at = NOW()
            RETURNING id
            """,
            (str(tpe_id), name, applicant.get("address"), applicant.get("country")),
        )
        return cur.fetchone()[0]

    cur.execute(
        "SELECT id FROM holders WHERE LOWER(name) = LOWER(%s) AND tpe_client_id IS NULL LIMIT 1",
        (name,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "INSERT INTO holders (name, address, country) VALUES (%s, %s, %s) RETURNING id",
        (name, applicant.get("address"), applicant.get("country")),
    )
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Design upsert
# ---------------------------------------------------------------------------

_PRODUCT_NAME_MAX_LEN = 500


def _truncate_500(value: Optional[str]) -> Optional[str]:
    """Trim a product_name string to fit the VARCHAR(500) column.

    Hague designs sometimes ship comma-joined multi-part descriptions
    (e.g. "Hood for vehicle, Radiator grille for vehicle, Front bumper
    for vehicle, ...") that easily exceed 500 chars. Returns None for
    None/empty input. Truncation preserves the leading prefix; the full
    string is still kept in merged_metadata.json for downstream export.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:_PRODUCT_NAME_MAX_LEN]


DESIGN_UPSERT_COLS = (
    "registry_type",
    "application_no", "design_index", "registration_no",
    "section", "current_status",
    "application_date", "filing_date", "registration_date",
    "bulletin_no", "bulletin_date", "opposition_end",
    "product_name_tr", "product_name_en",
    "locarno_classes", "design_count",
    "holder_id", "designers", "attorney_name", "attorney_firm",
    "priorities", "hague_reference", "deferred_publication",
    "dinov2_vitl14_mean", "clip_vitb32_mean",
    "source_issue_folder", "page_range_start", "page_range_end",
)


def _design_row(
    record: Dict[str, Any],
    design: Dict[str, Any],
    *,
    holder_id: Optional[str],
    source_folder: str,
    doc_bulletin_no: Any = None,
    doc_bulletin_date: Any = None,
) -> Dict[str, Any]:
    # bulletin_no / bulletin_date live at the doc level in pdf_extract_tasarim's
    # output (payload["bulletin_no"], payload["bulletin_date"]) — the per-record
    # dicts don't carry them. Without falling back to the doc level, every
    # design row in the DB ends up with NULL bulletin_no/_date, which makes
    # SQL queries by bulletin impossible and forces string-matching on
    # source_issue_folder. Real-data finding from the Phase-1 end-to-end
    # ingest: 410K rows shipped with NULL bulletin_no.
    bulletin_no = record.get("bulletin_no") or doc_bulletin_no
    bulletin_date = record.get("bulletin_date") or doc_bulletin_date
    opp_end = opposition_end_date(bulletin_date)
    page_range = record.get("page_range") or [None, None]
    designers = [d.get("name") for d in (record.get("designers") or []) if d.get("name")]
    attorney = record.get("attorney") or {}

    return {
        "registry_type": "design",
        "application_no": record.get("application_no"),
        "design_index": int(design.get("design_index") or 1),
        "registration_no": record.get("registration_no"),
        "section": record.get("section") or "tr_native",
        "current_status": status_for_section(record.get("section") or "tr_native"),
        "application_date": parse_date_safe(record.get("filing_date")),
        "filing_date": parse_date_safe(record.get("filing_date")),
        "registration_date": parse_date_safe(record.get("registration_date")),
        "bulletin_no": str(bulletin_no) if bulletin_no else None,
        "bulletin_date": parse_date_safe(bulletin_date),
        "opposition_end": opp_end,
        # Both product_name_* columns are VARCHAR(500). Hague records whose
        # WIPO entry describes a multi-part design ship a comma-joined list
        # that can run to 1300+ chars (e.g. "Hood, Radiator grille, Bumper,
        # …"). Truncate so the row is insertable; the merged_metadata.json
        # still preserves the full string for later display/export.
        "product_name_tr": _truncate_500(design.get("product_name_tr")),
        "product_name_en": _truncate_500(
            (record.get("hague_reference") or {}).get("product_name_en")
        ),
        "locarno_classes": list(record.get("locarno_classes") or []),
        "design_count": int(record.get("design_count") or 1),
        "holder_id": holder_id,
        "designers": designers,
        "attorney_name": attorney.get("name") if isinstance(attorney, dict) else None,
        "attorney_firm": attorney.get("firm") if isinstance(attorney, dict) else None,
        "priorities": json.dumps(record.get("priorities") or [], ensure_ascii=False),
        "hague_reference": json.dumps(record["hague_reference"], ensure_ascii=False) if record.get("hague_reference") else None,
        "deferred_publication": json.dumps(record["deferred_publication"], ensure_ascii=False) if record.get("deferred_publication") else None,
        "dinov2_vitl14_mean": to_halfvec_literal(
            (design.get("design_aggregates") or {}).get("dinov2_vitl14_mean")
        ),
        "clip_vitb32_mean": to_halfvec_literal(
            (design.get("design_aggregates") or {}).get("clip_vitb32_mean")
        ),
        "source_issue_folder": source_folder,
        "page_range_start": page_range[0] if isinstance(page_range, list) and len(page_range) >= 1 else None,
        "page_range_end": page_range[1] if isinstance(page_range, list) and len(page_range) >= 2 else None,
    }


def upsert_design(cur, row: Dict[str, Any]) -> str:
    """Find-or-insert a design row by natural key. Returns the design UUID."""
    if row["application_no"]:
        cur.execute(
            """
            SELECT id FROM designs
            WHERE application_no = %s AND design_index = %s AND section = %s
            """,
            (row["application_no"], row["design_index"], row["section"]),
        )
    else:
        cur.execute(
            """
            SELECT id FROM designs
            WHERE registration_no = %s AND section = %s AND application_no IS NULL
            """,
            (row["registration_no"], row["section"]),
        )
    existing = cur.fetchone()

    update_assignments = ", ".join(f"{c} = %({c})s" for c in DESIGN_UPSERT_COLS) + ", updated_at = NOW()"
    params = dict(row)
    if existing:
        params["__id__"] = existing[0]
        cur.execute(
            f"""
            UPDATE designs SET {update_assignments}
            WHERE id = %(__id__)s
            RETURNING id
            """.replace(
                "dinov2_vitl14_mean = %(dinov2_vitl14_mean)s",
                "dinov2_vitl14_mean = %(dinov2_vitl14_mean)s::halfvec",
            ).replace(
                "clip_vitb32_mean = %(clip_vitb32_mean)s",
                "clip_vitb32_mean = %(clip_vitb32_mean)s::halfvec",
            ),
            params,
        )
        return cur.fetchone()[0]

    cols_sql = ", ".join(DESIGN_UPSERT_COLS)
    placeholders_list = []
    for c in DESIGN_UPSERT_COLS:
        if c in {"dinov2_vitl14_mean", "clip_vitb32_mean"}:
            placeholders_list.append(f"%({c})s::halfvec")
        elif c in {"priorities", "hague_reference", "deferred_publication"}:
            placeholders_list.append(f"%({c})s::jsonb")
        else:
            placeholders_list.append(f"%({c})s")
    placeholders = ", ".join(placeholders_list)
    cur.execute(
        f"INSERT INTO designs ({cols_sql}) VALUES ({placeholders}) RETURNING id",
        params,
    )
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# View upsert
# ---------------------------------------------------------------------------

def upsert_views(cur, design_id: str, views: List[Dict[str, Any]]) -> int:
    """Upsert all views for a design. Returns count of rows touched."""
    n = 0
    for v in views:
        view_index = int(v.get("view_index") or 1)
        emb = v.get("embeddings") or {}
        params = {
            "design_id": design_id,
            "view_index": view_index,
            "page": v.get("page"),
            "image_xref": v.get("image_xref"),
            "bbox": v.get("bbox") if isinstance(v.get("bbox"), list) else None,
            "image_path": v.get("image_path"),
            "dinov2_vitl14": to_halfvec_literal(emb.get("dinov2_vitl14")),
            "clip_vitb32": to_halfvec_literal(emb.get("clip_vitb32")),
            "color_hsv": to_halfvec_literal(emb.get("color_hsv")),
        }
        cur.execute(
            """
            INSERT INTO design_views
                (design_id, view_index, page, image_xref, bbox, image_path,
                 dinov2_vitl14, clip_vitb32, color_hsv)
            VALUES
                (%(design_id)s, %(view_index)s, %(page)s, %(image_xref)s, %(bbox)s, %(image_path)s,
                 %(dinov2_vitl14)s::halfvec, %(clip_vitb32)s::halfvec, %(color_hsv)s::halfvec)
            ON CONFLICT (design_id, view_index) DO UPDATE
                SET page = EXCLUDED.page,
                    image_xref = EXCLUDED.image_xref,
                    bbox = EXCLUDED.bbox,
                    image_path = EXCLUDED.image_path,
                    dinov2_vitl14 = EXCLUDED.dinov2_vitl14,
                    clip_vitb32 = EXCLUDED.clip_vitb32,
                    color_hsv = EXCLUDED.color_hsv
            """,
            params,
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# Event upsert
# ---------------------------------------------------------------------------

def upsert_event(cur, event: Dict[str, Any], *, bulletin_no: Optional[str], bulletin_date: Optional[date]) -> bool:
    """Insert one event row (skip if event_fingerprint already present).
    Returns True on insert, False on conflict-skip."""
    fingerprint = event.get("fingerprint")
    if not fingerprint:
        return False

    application_no = event.get("application_no")
    registration_no = event.get("registration_no")

    # Try to link to an existing design row by natural key
    design_id: Optional[str] = None
    if application_no:
        cur.execute(
            "SELECT id FROM designs WHERE application_no = %s ORDER BY design_index ASC LIMIT 1",
            (application_no,),
        )
        row = cur.fetchone()
        if row:
            design_id = row[0]
    if design_id is None and registration_no:
        cur.execute(
            "SELECT id FROM designs WHERE registration_no = %s ORDER BY design_index ASC LIMIT 1",
            (registration_no,),
        )
        row = cur.fetchone()
        if row:
            design_id = row[0]

    details: Dict[str, Any] = {}
    for k in ("previous_holder", "new_holder", "court", "design_indices",
              "decision_date", "decision_no",
              "referenced_bulletin_no", "referenced_bulletin_date"):
        if event.get(k) not in (None, [], {}):
            details[k] = event[k]
    holder = event.get("holder")
    if isinstance(holder, dict) and holder.get("name") and "holder" not in details:
        details["holder"] = holder

    cur.execute(
        """
        INSERT INTO design_events
            (design_id, application_no, registration_no, event_type, event_date,
             bulletin_no, bulletin_date, page, details, free_text, event_fingerprint)
        VALUES
            (%(design_id)s, %(application_no)s, %(registration_no)s, %(event_type)s, %(event_date)s,
             %(bulletin_no)s, %(bulletin_date)s, %(page)s, %(details)s::jsonb, %(free_text)s, %(fingerprint)s)
        ON CONFLICT (event_fingerprint) DO NOTHING
        RETURNING id
        """,
        {
            "design_id": design_id,
            "application_no": application_no,
            "registration_no": registration_no,
            "event_type": event.get("event_type"),
            "event_date": parse_date_safe(event.get("event_date")),
            "bulletin_no": bulletin_no,
            "bulletin_date": bulletin_date,
            "page": event.get("page"),
            "details": json.dumps(details, ensure_ascii=False),
            "free_text": event.get("free_text"),
            "fingerprint": fingerprint,
        },
    )
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Issue-level orchestration
# ---------------------------------------------------------------------------

def ingest_issue(conn, issue_folder: Path, *, skip_events: bool = False, run_watchlist_scan: bool = True) -> Dict[str, Any]:
    metadata_path = issue_folder / "metadata.json"
    if not metadata_path.is_file():
        return {"status": "no_metadata", "issue": issue_folder.name}

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    designs_inserted = 0
    views_inserted = 0
    events_inserted = 0
    events_seen = 0
    inserted_design_ids: List[str] = []

    bulletin_no = str(payload.get("bulletin_no")) if payload.get("bulletin_no") else None
    bulletin_date = parse_date_safe(payload.get("bulletin_date"))

    with conn.cursor() as cur:
        for record in payload.get("records") or []:
            holder = resolve_holder_id(cur, _first_applicant(record))
            designs_in_record = record.get("designs") or []
            if not designs_in_record:
                # Hague (and any other image-less section) ships records with
                # no per-design view structure. Synthesize one design row per
                # such record so the entry still lands in the table.
                product_name = (record.get("hague_reference") or {}).get("product_name_en")
                designs_in_record = [{
                    "design_index": 1,
                    "product_name_tr": product_name,
                    "views": [],
                    "design_aggregates": {},
                }]
            for design in designs_in_record:
                row = _design_row(
                    record, design,
                    holder_id=holder,
                    source_folder=issue_folder.name,
                    doc_bulletin_no=payload.get("bulletin_no"),
                    doc_bulletin_date=payload.get("bulletin_date"),
                )
                design_id = upsert_design(cur, row)
                designs_inserted += 1
                inserted_design_ids.append(str(design_id))
                views_inserted += upsert_views(cur, design_id, design.get("views") or [])

        if not skip_events:
            events_path = issue_folder / "events.json"
            if events_path.is_file():
                events_payload = json.loads(events_path.read_text(encoding="utf-8"))
                events_seen = len(events_payload.get("events") or [])
                for event in events_payload.get("events") or []:
                    if upsert_event(cur, event, bulletin_no=bulletin_no, bulletin_date=bulletin_date):
                        events_inserted += 1
    conn.commit()

    if run_watchlist_scan and inserted_design_ids:
        try:
            from watchlist.design_scanner import trigger_design_watchlist_scan

            scan_ref = f"BLT_{bulletin_no}" if bulletin_no else issue_folder.name
            alerts = trigger_design_watchlist_scan(
                inserted_design_ids,
                source_type="bulletin",
                source_reference=scan_ref,
            )
            logger.info("[+] %s: design-watchlist scan emitted %d alert(s)", issue_folder.name, alerts)
        except Exception as exc:  # noqa: BLE001
            # Never let a failed watchlist scan poison a successful ingest.
            logger.warning("[!] %s: design-watchlist scan failed: %r", issue_folder.name, exc)

    logger.info(
        "[+] %s: designs=%d views=%d events_inserted=%d (events_seen=%d)",
        issue_folder.name, designs_inserted, views_inserted, events_inserted, events_seen,
    )
    return {
        "status": "ok",
        "issue": issue_folder.name,
        "designs": designs_inserted,
        "views": views_inserted,
        "events_inserted": events_inserted,
        "events_seen": events_seen,
        "design_ids": inserted_design_ids,
    }


def find_issue_folders(bulletins_root: Path) -> List[Path]:
    if not bulletins_root.is_dir():
        return []
    return sorted(p for p in bulletins_root.iterdir() if p.is_dir() and p.name.startswith("TS_"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_argv(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ingest_designs", add_help=True)
    p.add_argument("--issue", type=str, default=None,
                   help="single issue folder (e.g. TS_483_2026-04-24)")
    p.add_argument("--bulletins-root", type=Path, default=_LOCAL_DEFAULT_BULLETINS_DIR)
    p.add_argument("--skip-events", action="store_true",
                   help="ingest designs+views only, skip events.json")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_argv(argv)
    folders = ([args.bulletins_root / args.issue] if args.issue
               else find_issue_folders(args.bulletins_root))
    if not folders:
        logger.warning("no TS_* folders under %s", args.bulletins_root)
        return 0

    failed = 0
    with _connect() as conn:
        for folder in folders:
            try:
                ingest_issue(conn, folder, skip_events=args.skip_events)
            except Exception as e:
                logger.exception("issue %s failed: %r", folder.name, e)
                conn.rollback()
                failed += 1
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

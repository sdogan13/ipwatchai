"""
Ingest extracted events from events.json files into trademark_events.

Pass 1 - reconcile per-bulletin scope:
  Replace the DB rows for each BLT/GZ bulletin with the current events.json
  payload, using a full-payload event fingerprint to preserve distinct rows.

Pass 2 - materialize trademark state:
  Recompute event-derived columns on trademarks from chronological events.

Pass 3 - event alerts:
  Scan watched trademarks for newly ingested event rows.

Usage:
    python ingest_events.py
    python ingest_events.py --folder GZ_499_2026-01-30
    python ingest_events.py --insert-only
    python ingest_events.py --materialize-only
    python ingest_events.py --prune-missing-scopes
    python ingest_events.py --dry-run
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv
from psycopg2.extras import Json, execute_values


_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"
_PRE_DOTENV_PIPELINE_BULLETINS_ROOT = os.environ.get("PIPELINE_BULLETINS_ROOT")
_PRE_DOTENV_DATA_ROOT = os.environ.get("DATA_ROOT")


def _resolve_local_ingest_events_root(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


load_dotenv()

from db.pool import connection_context

ROOT_DIR = _resolve_local_ingest_events_root(
    _PRE_DOTENV_PIPELINE_BULLETINS_ROOT
    or _PRE_DOTENV_DATA_ROOT
    or os.environ.get("PIPELINE_BULLETINS_ROOT")
    or os.environ.get("DATA_ROOT"),
    _LOCAL_DEFAULT_BULLETINS_ROOT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)

if sys.platform == "win32" and "pytest" not in sys.modules:
    try:
        sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)
        sys.stderr = open(sys.stderr.fileno(), mode="w", encoding="utf-8", buffering=1)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    date_str = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_text(value: Any, *, max_len: Optional[int] = None, required: bool = False) -> Optional[str]:
    if value is None:
        return "" if required else None

    if not isinstance(value, str):
        value = str(value)

    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if max_len is not None:
        value = value[:max_len]

    if not value:
        return "" if required else None
    return value


def _normalize_jsonish(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize_jsonish(val)
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list):
        return [_normalize_jsonish(item) for item in value]
    if isinstance(value, str):
        return value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return value


def _load_events(folder: Path) -> Optional[dict]:
    events_file = folder / "events.json"
    if not events_file.exists():
        return None

    try:
        with open(events_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Failed to read {events_file}: {exc}")
        return None

    if data.get("status") != "success":
        return None

    events = data.get("events")
    if events is None:
        data["events"] = []
    elif not isinstance(events, list):
        logger.warning(f"Invalid events payload in {events_file}: events is not a list")
        return None

    return data


def _extract_scope_from_data(folder_name: str, data: dict) -> Optional[Tuple[str, str]]:
    source_type = _normalize_text(data.get("source_type"), max_len=3, required=True)
    bulletin_no = _normalize_text(
        data.get("gazette_no") or data.get("bulletin_no"),
        max_len=10,
        required=True,
    )

    if not source_type or not bulletin_no:
        for event in data.get("events", []):
            source_type = source_type or _normalize_text(event.get("source_type"), max_len=3, required=True)
            bulletin_no = bulletin_no or _normalize_text(
                event.get("gazette_no") or event.get("bulletin_no"),
                max_len=10,
                required=True,
            )
            if source_type and bulletin_no:
                break

    if not source_type or not bulletin_no:
        match = re.match(r"^(BLT|GZ)_(\d+)", folder_name.upper())
        if match:
            source_type = source_type or match.group(1)
            bulletin_no = bulletin_no or match.group(2)

    if not source_type or not bulletin_no:
        return None
    return source_type, bulletin_no


def _resolve_trademark_ids(conn, app_nos: List[str]) -> Dict[str, str]:
    if not app_nos:
        return {}

    cur = conn.cursor()
    result: Dict[str, str] = {}
    try:
        for i in range(0, len(app_nos), 1000):
            chunk = app_nos[i:i + 1000]
            placeholders = ",".join(["%s"] * len(chunk))
            cur.execute(
                f"SELECT application_no, id FROM trademarks "
                f"WHERE application_no IN ({placeholders})",
                chunk,
            )
            for row in cur.fetchall():
                result[row[0]] = str(row[1])
    finally:
        cur.close()
    return result


def _compute_event_fingerprint(
    *,
    application_no: str,
    registration_no: Optional[str],
    event_type: str,
    event_subtype: Optional[str],
    source_type: str,
    bulletin_no: str,
    bulletin_date: Optional[date],
    page_number: Optional[int],
    old_value: Optional[str],
    new_value: Optional[str],
    details: Dict[str, Any],
    raw_text: Optional[str],
) -> str:
    payload = {
        "application_no": application_no,
        "registration_no": registration_no or "",
        "event_type": event_type,
        "event_subtype": event_subtype or "",
        "source_type": source_type,
        "bulletin_no": bulletin_no,
        "bulletin_date": bulletin_date.isoformat() if bulletin_date else "",
        "page_number": page_number,
        "old_value": old_value or "",
        "new_value": new_value or "",
        "details": _normalize_jsonish(details or {}),
        "raw_text": raw_text or "",
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prepare_event_row(ev: dict, app_id_map: Dict[str, str]) -> Optional[tuple]:
    application_no = _normalize_text(ev.get("application_no"), max_len=20, required=True)
    event_type = _normalize_text(ev.get("event_type"), max_len=50, required=True)
    source_type = _normalize_text(ev.get("source_type"), max_len=3, required=True)
    bulletin_no = _normalize_text(
        ev.get("gazette_no") or ev.get("bulletin_no"),
        max_len=10,
        required=True,
    )

    if not application_no or not event_type or not source_type or not bulletin_no:
        return None

    registration_no = _normalize_text(ev.get("registration_no"), max_len=20)
    event_subtype = _normalize_text(ev.get("event_subtype"), max_len=50)
    bulletin_date = _parse_date(
        _normalize_text(ev.get("gazette_date") or ev.get("bulletin_date"))
    )

    page_number = ev.get("page_number")
    try:
        page_number = int(page_number) if page_number is not None else None
    except (TypeError, ValueError):
        page_number = None

    old_value = _normalize_text(ev.get("old_value"), max_len=2000)
    new_value = _normalize_text(ev.get("new_value"), max_len=2000)
    raw_text = _normalize_text(ev.get("raw_text"), max_len=2000)
    details = _normalize_jsonish(ev.get("details") or {})

    event_fingerprint = _compute_event_fingerprint(
        application_no=application_no,
        registration_no=registration_no,
        event_type=event_type,
        event_subtype=event_subtype,
        source_type=source_type,
        bulletin_no=bulletin_no,
        bulletin_date=bulletin_date,
        page_number=page_number,
        old_value=old_value,
        new_value=new_value,
        details=details,
        raw_text=raw_text,
    )

    return (
        app_id_map.get(application_no),
        application_no,
        registration_no,
        event_type,
        event_subtype,
        source_type,
        bulletin_no,
        bulletin_date,
        page_number,
        old_value,
        new_value,
        Json(details),
        raw_text,
        event_fingerprint,
    )


def prepare_event_rows(events: List[dict], app_id_map: Dict[str, str]) -> Tuple[List[tuple], dict, Set[str]]:
    rows: List[tuple] = []
    app_nos: Set[str] = set()
    seen_fingerprints: Set[str] = set()
    stats = {"prepared": 0, "deduped": 0, "skipped_invalid": 0}

    for event in events:
        row = _prepare_event_row(event, app_id_map)
        if row is None:
            stats["skipped_invalid"] += 1
            continue

        app_nos.add(row[1])
        fingerprint = row[-1]
        if fingerprint in seen_fingerprints:
            stats["deduped"] += 1
            continue

        seen_fingerprints.add(fingerprint)
        rows.append(row)
        stats["prepared"] += 1

    return rows, stats, app_nos


def _replace_scope_events(
    conn,
    *,
    source_type: str,
    bulletin_no: str,
    rows: List[tuple],
    dry_run: bool = False,
) -> Tuple[dict, Set[str]]:
    stats = {"deleted": 0, "inserted": 0, "skipped": 0, "errors": 0}
    cur = conn.cursor()
    existing_app_nos: Set[str] = set()

    try:
        cur.execute(
            """
            SELECT application_no, COUNT(*)
            FROM trademark_events
            WHERE source_type = %s AND bulletin_no = %s
            GROUP BY application_no
            """,
            (source_type, bulletin_no),
        )
        existing_rows = cur.fetchall()
        existing_app_nos = {row[0] for row in existing_rows if row[0]}
        stats["deleted"] = sum(int(row[1]) for row in existing_rows)

        if dry_run:
            stats["inserted"] = len(rows)
            return stats, existing_app_nos

        cur.execute(
            "DELETE FROM trademark_events WHERE source_type = %s AND bulletin_no = %s",
            (source_type, bulletin_no),
        )
        stats["deleted"] = cur.rowcount

        if rows:
            sql = """
                INSERT INTO trademark_events (
                    trademark_id, application_no, registration_no, event_type, event_subtype,
                    source_type, bulletin_no, bulletin_date, page_number,
                    old_value, new_value, details, raw_text, event_fingerprint
                ) VALUES %s
            """
            execute_values(
                cur,
                sql,
                rows,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                page_size=500,
            )
            stats["inserted"] = len(rows)

        conn.commit()
        return stats, existing_app_nos

    except Exception as exc:
        conn.rollback()
        logger.error(f"Scope replace failed for {source_type} {bulletin_no}: {exc}")
        stats["errors"] = len(rows)
        return stats, existing_app_nos
    finally:
        cur.close()


def _iter_event_folders(root_dir: Path, source_filter: str):
    for folder in sorted(root_dir.iterdir()):
        if not folder.is_dir():
            continue
        if source_filter == "GZ" and not folder.name.startswith("GZ_"):
            continue
        if source_filter == "BLT" and not folder.name.startswith("BLT_"):
            continue
        if not (folder / "events.json").exists():
            continue
        yield folder


def _collect_local_scopes(root_dir: Path, source_filter: str) -> Set[Tuple[str, str]]:
    scopes: Set[Tuple[str, str]] = set()
    for folder in _iter_event_folders(root_dir, source_filter):
        data = _load_events(folder)
        if not data:
            continue
        scope = _extract_scope_from_data(folder.name, data)
        if scope:
            scopes.add(scope)
    return scopes


def _prune_missing_scopes(
    conn,
    *,
    local_scopes: Set[Tuple[str, str]],
    source_filter: str,
    dry_run: bool = False,
) -> Tuple[dict, Set[str]]:
    stats = {"scopes_deleted": 0, "rows_deleted": 0}
    affected_app_nos: Set[str] = set()
    cur = conn.cursor()

    try:
        where_clause = ""
        params: List[Any] = []
        if source_filter in {"BLT", "GZ"}:
            where_clause = "WHERE source_type = %s"
            params = [source_filter]

        cur.execute(
            f"""
            SELECT source_type, bulletin_no, COUNT(*), array_agg(DISTINCT application_no)
            FROM trademark_events
            {where_clause}
            GROUP BY source_type, bulletin_no
            """,
            params,
        )
        db_scopes = cur.fetchall()

        missing_scopes = []
        for source_type, bulletin_no, row_count, app_nos in db_scopes:
            scope = (source_type, bulletin_no)
            if scope not in local_scopes:
                missing_scopes.append((scope, int(row_count), app_nos or []))

        if dry_run:
            stats["scopes_deleted"] = len(missing_scopes)
            stats["rows_deleted"] = sum(row_count for _, row_count, _ in missing_scopes)
            for _, _, app_nos in missing_scopes:
                affected_app_nos.update(app_no for app_no in app_nos if app_no)
            return stats, affected_app_nos

        for (source_type, bulletin_no), _, app_nos in missing_scopes:
            cur.execute(
                "DELETE FROM trademark_events WHERE source_type = %s AND bulletin_no = %s",
                (source_type, bulletin_no),
            )
            stats["scopes_deleted"] += 1
            stats["rows_deleted"] += cur.rowcount
            affected_app_nos.update(app_no for app_no in app_nos if app_no)

        conn.commit()
        return stats, affected_app_nos

    except Exception as exc:
        conn.rollback()
        logger.error(f"Pruning missing scopes failed: {exc}")
        raise
    finally:
        cur.close()


# ===========================================================================
# PASS 1 - Event insertion / scope reconciliation
# ===========================================================================
def process_folder(folder: Path, conn, dry_run: bool = False) -> dict:
    folder_name = folder.name
    data = _load_events(folder)
    if not data:
        return {"folder": folder_name, "status": "skipped", "reason": "no usable events.json"}

    scope = _extract_scope_from_data(folder_name, data)
    if not scope:
        return {"folder": folder_name, "status": "skipped", "reason": "missing source scope"}

    source_type, bulletin_no = scope
    events = data.get("events", [])
    total = data.get("total", len(events))

    logger.info(f"Processing {folder_name}: {total} events ({source_type} {bulletin_no})")

    app_nos_to_resolve = sorted({
        app_no
        for app_no in (
            _normalize_text(ev.get("application_no"), max_len=20, required=True)
            for ev in events
        )
        if app_no and app_no not in {"UNKNOWN", "MADRID_UNKNOWN"} and not app_no.startswith("MADRID_")
    })

    app_id_map = _resolve_trademark_ids(conn, app_nos_to_resolve)
    resolved_pct = len(app_id_map) * 100 // max(len(app_nos_to_resolve), 1)
    logger.info(f"  Resolved {len(app_id_map)}/{len(app_nos_to_resolve)} app_nos ({resolved_pct}%)")

    rows, prep_stats, current_app_nos = prepare_event_rows(events, app_id_map)
    replace_stats, previous_app_nos = _replace_scope_events(
        conn,
        source_type=source_type,
        bulletin_no=bulletin_no,
        rows=rows,
        dry_run=dry_run,
    )
    replace_stats["skipped"] += prep_stats["deduped"] + prep_stats["skipped_invalid"]

    logger.info(
        "  Deleted: %s, Inserted: %s, Skipped: %s",
        replace_stats["deleted"],
        replace_stats["inserted"],
        replace_stats["skipped"],
    )

    return {
        "folder": folder_name,
        "status": "success" if replace_stats["errors"] == 0 else "error",
        "source_type": source_type,
        "bulletin_no": bulletin_no,
        "total_events": total,
        "resolved": len(app_id_map),
        "unresolved": len(app_nos_to_resolve) - len(app_id_map),
        "prepared": prep_stats,
        "insert": replace_stats,
        "materialize_app_nos": sorted({
            app_no for app_no in previous_app_nos | current_app_nos if app_no
        }),
    }


# ===========================================================================
# PASS 2 - Chronological materialization
# ===========================================================================
RESTRICTION_TYPES = {"seizure", "precautionary_seizure", "injunction", "precautionary_injunction"}
RESTRICTION_LIFT_TYPES = {"seizure_lift", "injunction_lift", "restriction_lift"}
TRANSFER_TYPES = {"transfer", "merger", "partial_transfer"}
FLAG_TYPES = {
    "license": "has_license",
    "bankruptcy": "has_bankruptcy",
    "correction": "has_correction",
    "madrid_registration": "madrid_protected",
    "madrid_renewal": "madrid_protected",
}


def _empty_materialized_state() -> dict:
    return {
        "effective_status": None,
        "active_restriction_count": 0,
        "current_holder_name": None,
        "holder_changed_at": None,
        "renewal_expiry": None,
        "last_event_type": None,
        "last_event_date": None,
        "has_restrictions": False,
        "event_flags": {},
        "total_event_count": 0,
    }


def _compute_state_from_events(events_rows: list) -> dict:
    state = _empty_materialized_state()

    for (event_type, event_subtype, old_value, new_value, details, bulletin_date) in events_rows:
        state["last_event_type"] = event_type
        state["last_event_date"] = bulletin_date

        if event_type in TRANSFER_TYPES:
            if new_value:
                holder_clean = new_value.split("(")[0].strip()
                if holder_clean:
                    state["current_holder_name"] = holder_clean
                    state["holder_changed_at"] = bulletin_date
            state["effective_status"] = "Devredildi"

        elif event_type == "cancellation":
            state["effective_status"] = "İptal Edildi"

        elif event_type == "withdrawal":
            state["effective_status"] = "Geri Çekildi"

        elif event_type == "renewal":
            det = details if isinstance(details, dict) else {}
            renewal_date_str = det.get("renewal_date") or new_value
            renewal_date = _parse_date(str(renewal_date_str)) if renewal_date_str else None
            if renewal_date:
                state["renewal_expiry"] = renewal_date.replace(year=renewal_date.year + 10)
            state["effective_status"] = "Yenilendi"

        elif event_type in RESTRICTION_TYPES:
            state["active_restriction_count"] += 1

        elif event_type in RESTRICTION_LIFT_TYPES:
            state["active_restriction_count"] = max(0, state["active_restriction_count"] - 1)

        if event_type in FLAG_TYPES:
            state["event_flags"][FLAG_TYPES[event_type]] = True

        if event_subtype and "court" in event_subtype.lower():
            state["event_flags"]["has_court_order"] = True

    state["has_restrictions"] = state["active_restriction_count"] > 0
    state["total_event_count"] = len(events_rows)
    return state


def _flush_materialize_batch(cur, batch: list):
    sql = """
        UPDATE trademarks SET
            effective_status = data.effective_status::tm_status,
            active_restriction_count = data.active_restriction_count,
            current_holder_name = data.current_holder_name,
            holder_changed_at = data.holder_changed_at::date,
            renewal_expiry = data.renewal_expiry::date,
            last_event_type = data.last_event_type,
            last_event_date = data.last_event_date::date,
            has_restrictions = data.has_restrictions,
            event_flags = data.event_flags::jsonb,
            total_event_count = data.total_event_count
        FROM (VALUES %s) AS data(
            effective_status, active_restriction_count, current_holder_name,
            holder_changed_at, renewal_expiry, last_event_type, last_event_date,
            has_restrictions, event_flags, total_event_count, tm_id
        )
        WHERE trademarks.id = data.tm_id::uuid
    """
    execute_values(
        cur,
        sql,
        batch,
        template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        page_size=500,
    )


def _reset_orphaned_materialized_state(conn, dry_run: bool = False) -> int:
    cur = conn.cursor()
    try:
        cur.execute("DROP TABLE IF EXISTS tmp_event_app_nos")
        cur.execute(
            """
            CREATE TEMP TABLE tmp_event_app_nos AS
            SELECT DISTINCT application_no
            FROM trademark_events
            """
        )
        cur.execute("CREATE INDEX tmp_event_app_nos_app_no_idx ON tmp_event_app_nos(application_no)")
        cur.execute("ANALYZE tmp_event_app_nos")

        predicate = """
            tea.application_no IS NULL
            AND (
                t.effective_status IS NOT NULL
                OR t.active_restriction_count <> 0
                OR t.current_holder_name IS NOT NULL
                OR t.holder_changed_at IS NOT NULL
                OR t.renewal_expiry IS NOT NULL
                OR t.last_event_type IS NOT NULL
                OR t.last_event_date IS NOT NULL
                OR t.has_restrictions IS TRUE
                OR t.event_flags <> '{}'::jsonb
                OR t.total_event_count <> 0
            )
        """

        cur.execute(
            f"""
            CREATE TEMP TABLE tmp_orphaned_trademark_ids AS
            SELECT t.id
            FROM trademarks t
            LEFT JOIN tmp_event_app_nos tea ON tea.application_no = t.application_no
            WHERE {predicate}
            """
        )
        cur.execute("CREATE INDEX tmp_orphaned_trademark_ids_id_idx ON tmp_orphaned_trademark_ids(id)")
        cur.execute("SELECT COUNT(*) FROM tmp_orphaned_trademark_ids")
        count = cur.fetchone()[0]
        if dry_run or count == 0:
            return count

        updated = 0
        batch_size = 50000
        while True:
            cur.execute(
                f"""
                WITH batch AS (
                    SELECT ctid, id
                    FROM tmp_orphaned_trademark_ids
                    LIMIT {batch_size}
                ),
                removed AS (
                    DELETE FROM tmp_orphaned_trademark_ids o
                    USING batch
                    WHERE o.ctid = batch.ctid
                    RETURNING batch.id
                )
                UPDATE trademarks t
                SET
                    effective_status = NULL,
                    active_restriction_count = 0,
                    current_holder_name = NULL,
                    holder_changed_at = NULL,
                    renewal_expiry = NULL,
                    last_event_type = NULL,
                    last_event_date = NULL,
                    has_restrictions = FALSE,
                    event_flags = '{{}}'::jsonb,
                    total_event_count = 0
                FROM removed
                WHERE t.id = removed.id
                """
            )
            batch_updated = cur.rowcount
            conn.commit()
            if batch_updated == 0:
                break
            updated += batch_updated
            logger.info(f"Reset orphaned event state batch: {updated}/{count}")

        return updated
    finally:
        cur.close()


def materialize_all(conn, app_nos: Optional[List[str]] = None, dry_run: bool = False) -> dict:
    stats = {
        "trademarks_processed": 0,
        "trademarks_reset": 0,
        "effective_status_set": 0,
        "holders_updated": 0,
        "restrictions_set": 0,
        "renewals_set": 0,
        "flags_set": 0,
    }

    cur = conn.cursor()
    try:
        if app_nos:
            requested = [app_no for app_no in dict.fromkeys(app_nos) if app_no]
            if not requested:
                return stats
            tm_map: Dict[str, str] = {}
            for i in range(0, len(requested), 1000):
                chunk = requested[i:i + 1000]
                placeholders = ",".join(["%s"] * len(chunk))
                cur.execute(
                    f"SELECT application_no, id FROM trademarks WHERE application_no IN ({placeholders})",
                    chunk,
                )
                tm_map.update({row[0]: str(row[1]) for row in cur.fetchall()})
        else:
            cur.execute(
                """
                SELECT DISTINCT t.application_no, t.id
                FROM trademarks t
                JOIN trademark_events te ON te.application_no = t.application_no
                """
            )
            tm_map = {row[0]: str(row[1]) for row in cur.fetchall()}
        logger.info(f"Materializing state for {len(tm_map)} trademarks...")

        if not tm_map:
            return stats

        all_app_nos = list(tm_map.keys())
        events_by_app: Dict[str, list] = {app_no: [] for app_no in all_app_nos}

        for i in range(0, len(all_app_nos), 1000):
            chunk = all_app_nos[i:i + 1000]
            placeholders = ",".join(["%s"] * len(chunk))
            cur.execute(
                f"""
                SELECT application_no, event_type, event_subtype,
                       old_value, new_value, details, bulletin_date
                FROM trademark_events
                WHERE application_no IN ({placeholders})
                ORDER BY bulletin_date ASC NULLS FIRST, created_at ASC, id ASC
                """,
                chunk,
            )
            for row in cur.fetchall():
                events_by_app[row[0]].append(row[1:])

        if dry_run:
            for events_rows in events_by_app.values():
                stats["trademarks_processed"] += 1
                if not events_rows:
                    stats["trademarks_reset"] += 1
                    continue
                state = _compute_state_from_events(events_rows)
                if state["effective_status"]:
                    stats["effective_status_set"] += 1
                if state["current_holder_name"]:
                    stats["holders_updated"] += 1
                if state["active_restriction_count"] > 0:
                    stats["restrictions_set"] += 1
                if state["renewal_expiry"]:
                    stats["renewals_set"] += 1
                if state["event_flags"]:
                    stats["flags_set"] += 1
            if not app_nos:
                stats["trademarks_reset"] += _reset_orphaned_materialized_state(conn, dry_run=True)
            return stats

        batch_size = 500
        update_batch = []

        for app_no, events_rows in events_by_app.items():
            if events_rows:
                state = _compute_state_from_events(events_rows)
            else:
                state = _empty_materialized_state()
                stats["trademarks_reset"] += 1

            update_batch.append((
                state["effective_status"],
                state["active_restriction_count"],
                state["current_holder_name"],
                state["holder_changed_at"],
                state["renewal_expiry"],
                state["last_event_type"],
                state["last_event_date"],
                state["has_restrictions"],
                Json(state["event_flags"]),
                state["total_event_count"],
                tm_map[app_no],
            ))

            stats["trademarks_processed"] += 1
            if state["effective_status"]:
                stats["effective_status_set"] += 1
            if state["current_holder_name"]:
                stats["holders_updated"] += 1
            if state["active_restriction_count"] > 0:
                stats["restrictions_set"] += 1
            if state["renewal_expiry"]:
                stats["renewals_set"] += 1
            if state["event_flags"]:
                stats["flags_set"] += 1

            if len(update_batch) >= batch_size:
                _flush_materialize_batch(cur, update_batch)
                conn.commit()
                update_batch = []

        if update_batch:
            _flush_materialize_batch(cur, update_batch)
            conn.commit()

        if not app_nos:
            stats["trademarks_reset"] += _reset_orphaned_materialized_state(conn, dry_run=False)

        logger.info(f"Materialization complete: {json.dumps(stats, indent=2)}")
        return stats
    finally:
        cur.close()


# ===========================================================================
# Main
# ===========================================================================
def _recompute_final_status(conn, app_nos: Optional[List[str]]) -> None:
    from utils.status_reconciler import update_final_status_batch

    if not app_nos or len(app_nos) <= 10000:
        update_final_status_batch(conn, app_nos=app_nos)
        return

    logger.info(
        "final_status app set is large (%s); using chunked scoped reconciliation",
        len(app_nos),
    )

    total_updated = 0
    for i in range(0, len(app_nos), 10000):
        chunk = app_nos[i:i + 10000]
        total_updated += update_final_status_batch(conn, app_nos=chunk)
    logger.info(f"final_status recomputed for {total_updated} trademarks across chunks")


def ensure_event_ingest_schema() -> None:
    """Ensure the trademark_events schema is ready before ingest work starts."""
    from migrations.run_trademark_events_migration import ensure_trademark_events_schema

    if not ensure_trademark_events_schema():
        raise RuntimeError("trademark_events schema is not ready")


def _resolve_event_ingest_root_dir(root_dir: Optional[str | Path]) -> Path:
    if root_dir is None:
        return ROOT_DIR
    return _resolve_local_ingest_events_root(str(root_dir), ROOT_DIR)


def _append_summary_error(summary: dict, message: str) -> None:
    summary["errors"].append(message)
    if summary.get("error"):
        summary["error"] = f"{summary['error']}; {message}"
    else:
        summary["error"] = message


def _accumulate_insert_result(summary: dict, result: dict) -> None:
    prepared = result.get("prepared") or {}
    inserted = result.get("insert") or {}

    summary["total_deleted"] += inserted.get("deleted", 0)
    summary["total_inserted"] += inserted.get("inserted", 0)
    summary["total_resolved"] += result.get("resolved", 0)
    summary["total_unresolved"] += result.get("unresolved", 0)
    summary["total_prepared"] += prepared.get("prepared", 0)
    summary["total_deduped"] += prepared.get("deduped", 0)
    summary["total_invalid"] += prepared.get("skipped_invalid", 0)


def run_event_ingest(
    *,
    root_dir: Optional[str | Path] = None,
    folder: Optional[str] = None,
    source: str = "all",
    insert_only: bool = False,
    materialize_only: bool = False,
    prune_missing_scopes: bool = False,
    dry_run: bool = False,
    conn=None,
    run_alerts: bool = True,
) -> dict:
    """
    Reconcile local events.json files into trademark_events and materialized state.

    Returns a structured summary suitable for worker/API use.
    """
    if source not in {"BLT", "GZ", "all"}:
        raise ValueError(f"Invalid source filter: {source}")

    ensure_event_ingest_schema()

    root_path = _resolve_event_ingest_root_dir(root_dir)
    if not root_path.exists():
        raise RuntimeError(f"Events root not found: {root_path}")

    summary = {
        "status": "success",
        "root_dir": str(root_path),
        "source": source,
        "folder": folder,
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "error": None,
        "errors": [],
        "folder_errors": [],
        "total_deleted": 0,
        "total_inserted": 0,
        "total_resolved": 0,
        "total_unresolved": 0,
        "total_prepared": 0,
        "total_deduped": 0,
        "total_invalid": 0,
        "pruned_scopes": 0,
        "pruned_rows": 0,
        "materialize": None,
        "alerts_generated": None,
        "alert_error": None,
    }

    def _run_with_connection(active_conn) -> dict:
        insert_result = None
        affected_app_nos: Set[str] = set()

        if not materialize_only:
            if folder:
                target_folder = root_path / folder
                if not target_folder.exists():
                    raise RuntimeError(f"Folder not found: {target_folder}")

                try:
                    insert_result = process_folder(target_folder, active_conn, dry_run=dry_run)
                except Exception as exc:
                    summary["failed"] += 1
                    message = f"{target_folder.name}: {exc}"
                    summary["folder_errors"].append(message)
                    _append_summary_error(summary, message)
                else:
                    if insert_result["status"] == "success":
                        summary["processed"] += 1
                        affected_app_nos.update(insert_result.get("materialize_app_nos", []))
                    elif insert_result["status"] == "skipped":
                        summary["skipped"] += 1
                    else:
                        summary["failed"] += 1
                        message = (
                            f"{target_folder.name}: "
                            f"{insert_result.get('reason') or insert_result.get('insert', {}).get('errors') or 'event scope failed'}"
                        )
                        summary["folder_errors"].append(message)
                        _append_summary_error(summary, message)
                    _accumulate_insert_result(summary, insert_result)
            else:
                for event_folder in _iter_event_folders(root_path, source):
                    try:
                        result = process_folder(event_folder, active_conn, dry_run=dry_run)
                    except Exception as exc:
                        summary["failed"] += 1
                        message = f"{event_folder.name}: {exc}"
                        summary["folder_errors"].append(message)
                        _append_summary_error(summary, message)
                        continue

                    if result["status"] == "success":
                        summary["processed"] += 1
                        affected_app_nos.update(result.get("materialize_app_nos", []))
                    elif result["status"] == "skipped":
                        summary["skipped"] += 1
                    else:
                        summary["failed"] += 1
                        message = (
                            f"{event_folder.name}: "
                            f"{result.get('reason') or result.get('insert', {}).get('errors') or 'event scope failed'}"
                        )
                        summary["folder_errors"].append(message)
                        _append_summary_error(summary, message)

                    _accumulate_insert_result(summary, result)

                if prune_missing_scopes:
                    local_scopes = _collect_local_scopes(root_path, source)
                    prune_stats, pruned_app_nos = _prune_missing_scopes(
                        active_conn,
                        local_scopes=local_scopes,
                        source_filter=source,
                        dry_run=dry_run,
                    )
                    affected_app_nos.update(pruned_app_nos)
                    summary["pruned_scopes"] = prune_stats["scopes_deleted"]
                    summary["pruned_rows"] = prune_stats["rows_deleted"]

                logger.info("=" * 60)
                logger.info(
                    "PASS 1 (Reconcile): %s",
                    json.dumps(
                        {
                            "processed": summary["processed"],
                            "skipped": summary["skipped"],
                            "failed": summary["failed"],
                            "total_deleted": summary["total_deleted"],
                            "total_inserted": summary["total_inserted"],
                            "total_resolved": summary["total_resolved"],
                            "total_unresolved": summary["total_unresolved"],
                            "total_prepared": summary["total_prepared"],
                            "total_deduped": summary["total_deduped"],
                            "total_invalid": summary["total_invalid"],
                            "pruned_scopes": summary["pruned_scopes"],
                            "pruned_rows": summary["pruned_rows"],
                        },
                        indent=2,
                    ),
                )

        if not insert_only:
            logger.info("=" * 60)
            logger.info("PASS 2: Chronological materialization...")

            mat_app_nos = None
            should_materialize = True
            if folder and not materialize_only and insert_result and insert_result.get("status") == "success":
                mat_app_nos = insert_result.get("materialize_app_nos")
            elif folder and not materialize_only:
                should_materialize = False
            elif not folder and affected_app_nos and not materialize_only:
                mat_app_nos = sorted(affected_app_nos)
            elif not folder and not materialize_only:
                should_materialize = False
            elif folder and materialize_only:
                data = _load_events(root_path / folder)
                if data:
                    mat_app_nos = sorted(
                        {
                            app_no
                            for app_no in (
                                _normalize_text(ev.get("application_no"), max_len=20, required=True)
                                for ev in data.get("events", [])
                            )
                            if app_no
                        }
                    )
                else:
                    should_materialize = False
                    summary["skipped"] += 1

            if should_materialize:
                mat_stats = materialize_all(active_conn, app_nos=mat_app_nos, dry_run=dry_run)
                summary["materialize"] = mat_stats
                logger.info(f"PASS 2 (Materialize): {json.dumps(mat_stats, indent=2)}")

                if not dry_run:
                    _recompute_final_status(active_conn, mat_app_nos)
            else:
                logger.info("PASS 2 (Materialize): skipped because no actionable scope succeeded")

        if not insert_only and not dry_run and run_alerts:
            logger.info("=" * 60)
            logger.info("PASS 3: Scanning for event-based watchlist alerts...")
            try:
                from watchlist.scanner import scan_events_for_watchlist

                summary["alerts_generated"] = scan_events_for_watchlist(active_conn)
                logger.info(
                    "PASS 3 (Event Alerts): %s alerts generated",
                    summary["alerts_generated"],
                )
            except Exception as exc:
                summary["alert_error"] = str(exc)
                _append_summary_error(summary, f"event alerts: {exc}")
                logger.warning(f"Event alert scan skipped: {exc}")

        if summary["alert_error"] or summary["failed"] > 0:
            summary["status"] = "partial"

        return summary

    if conn is not None:
        return _run_with_connection(conn)

    with connection_context() as managed_conn:
        return _run_with_connection(managed_conn)


def main():
    parser = argparse.ArgumentParser(description="Ingest trademark events into DB")
    parser.add_argument("--folder", type=str, default=None, help="Process single folder (e.g. GZ_499_2026-01-30)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without DB writes")
    parser.add_argument("--source", type=str, choices=["GZ", "BLT", "all"], default="all",
                        help="Process only GZ or BLT folders")
    parser.add_argument("--insert-only", action="store_true", help="Only reconcile event rows, skip materialization")
    parser.add_argument("--materialize-only", action="store_true", help="Skip insertion, only recompute materialized columns")
    parser.add_argument("--prune-missing-scopes", action="store_true",
                        help="Delete trademark_events scopes that are no longer present under the local events corpus")
    args = parser.parse_args()

    try:
        summary = run_event_ingest(
            folder=args.folder,
            dry_run=args.dry_run,
            source=args.source,
            insert_only=args.insert_only,
            materialize_only=args.materialize_only,
            prune_missing_scopes=args.prune_missing_scopes,
        )
        logger.info("=" * 60)
        logger.info(f"EVENT INGEST SUMMARY: {json.dumps(summary, indent=2, default=str)}")
        if summary.get("status") == "failed":
            sys.exit(1)
    except Exception as exc:
        logger.error(f"Event ingest failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()

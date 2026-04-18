"""
Ingest extracted events from events.json files into the trademark_events table.

Two-pass approach:
  Pass 1 — Insert: Load events.json from each bulletin folder and INSERT into
           trademark_events (ON CONFLICT skip for dedup).
  Pass 2 — Materialize: For every trademark that has events, walk ALL events
           ordered by bulletin_date ASC to compute final state. Write event-derived
           columns (effective_status, active_restriction_count, current_holder_name,
           holder_changed_at, renewal_expiry, last_event_type, event_flags,
           total_event_count) onto trademarks table.

           These columns are SEPARATE from current_status (owned by ingest.py's
           source priority system). We never touch current_status here.

Usage:
    python ingest_events.py                             # insert + materialize all
    python ingest_events.py --folder GZ_499_2026-01-30  # single folder insert + materialize
    python ingest_events.py --insert-only               # skip materialization
    python ingest_events.py --materialize-only           # skip insertion, just recompute
    python ingest_events.py --dry-run                    # preview without DB writes
"""

import json
import os
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Optional

import psycopg2
from psycopg2.extras import Json, execute_values
from dotenv import load_dotenv

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

# Force UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_date(date_str: Optional[str]) -> Optional[date]:
    """Parse date string in various formats to date object."""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _load_events(folder: Path) -> Optional[dict]:
    """Load events.json from a bulletin folder."""
    events_file = folder / "events.json"
    if not events_file.exists():
        return None
    try:
        with open(events_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("status") != "success" or not data.get("events"):
            return None
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read {events_file}: {e}")
        return None


def _resolve_trademark_ids(conn, app_nos: List[str]) -> Dict[str, str]:
    """Bulk-resolve application_no → trademark_id (UUID)."""
    if not app_nos:
        return {}
    cur = conn.cursor()
    result = {}
    for i in range(0, len(app_nos), 1000):
        chunk = app_nos[i:i+1000]
        placeholders = ",".join(["%s"] * len(chunk))
        cur.execute(
            f"SELECT application_no, id FROM trademarks "
            f"WHERE application_no IN ({placeholders})",
            chunk,
        )
        for row in cur.fetchall():
            result[row[0]] = str(row[1])
    cur.close()
    return result


# ===========================================================================
# PASS 1 — Event insertion
# ===========================================================================
def insert_events(conn, events: List[dict], app_id_map: Dict[str, str],
                  dry_run: bool = False) -> dict:
    """Insert events into trademark_events table.

    Returns stats dict with counts.
    """
    stats = {"inserted": 0, "skipped": 0, "errors": 0}

    if dry_run:
        stats["inserted"] = len(events)
        return stats

    cur = conn.cursor()

    rows = []
    for ev in events:
        app_no = ev.get("application_no", "")
        if not app_no or app_no in ("UNKNOWN", "MADRID_UNKNOWN"):
            stats["skipped"] += 1
            continue

        trademark_id = app_id_map.get(app_no)
        reg_no = ev.get("registration_no")
        event_type = ev.get("event_type", "")
        event_subtype = ev.get("event_subtype")
        source_type = ev.get("source_type", "")

        bulletin_no = ev.get("gazette_no") or ev.get("bulletin_no", "")
        bulletin_date_str = ev.get("gazette_date") or ev.get("bulletin_date", "")
        bulletin_date = _parse_date(bulletin_date_str)

        page_number = ev.get("page_number")
        old_value = ev.get("old_value")
        new_value = ev.get("new_value")
        details = ev.get("details") or {}
        raw_text = ev.get("raw_text", "")

        if old_value and len(old_value) > 2000:
            old_value = old_value[:2000]
        if new_value and len(new_value) > 2000:
            new_value = new_value[:2000]
        if raw_text and len(raw_text) > 2000:
            raw_text = raw_text[:2000]

        rows.append((
            trademark_id, app_no, reg_no, event_type, event_subtype,
            source_type, bulletin_no, bulletin_date, page_number,
            old_value, new_value, Json(details), raw_text,
        ))

    if not rows:
        return stats

    sql = """
        INSERT INTO trademark_events (
            trademark_id, application_no, registration_no, event_type, event_subtype,
            source_type, bulletin_no, bulletin_date, page_number,
            old_value, new_value, details, raw_text
        ) VALUES %s
        ON CONFLICT (application_no, event_type, source_type, bulletin_no,
                     COALESCE(old_value, ''), COALESCE(new_value, ''))
        DO NOTHING
    """

    try:
        execute_values(
            cur, sql, rows,
            template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            page_size=500,
        )
        inserted = cur.rowcount
        stats["inserted"] = inserted
        stats["skipped"] += len(rows) - inserted
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Batch insert failed: {e}")
        stats["errors"] = len(rows)

    cur.close()
    return stats


def process_folder(folder: Path, conn, dry_run: bool = False) -> dict:
    """Insert events from a single bulletin folder."""
    folder_name = folder.name
    data = _load_events(folder)
    if not data:
        return {"folder": folder_name, "status": "skipped", "reason": "no events.json"}

    events = data["events"]
    source_type = data.get("source_type", "")
    total = data.get("total", len(events))

    logger.info(f"Processing {folder_name}: {total} events ({source_type})")

    app_nos = list({
        ev["application_no"] for ev in events
        if ev.get("application_no") and ev["application_no"] not in ("UNKNOWN", "MADRID_UNKNOWN")
        and not ev["application_no"].startswith("MADRID_")
    })

    app_id_map = _resolve_trademark_ids(conn, app_nos)
    resolved_pct = len(app_id_map) * 100 // max(len(app_nos), 1)
    logger.info(f"  Resolved {len(app_id_map)}/{len(app_nos)} app_nos ({resolved_pct}%)")

    insert_stats = insert_events(conn, events, app_id_map, dry_run=dry_run)
    logger.info(f"  Inserted: {insert_stats['inserted']}, Skipped: {insert_stats['skipped']}")

    return {
        "folder": folder_name,
        "status": "success",
        "source_type": source_type,
        "total_events": total,
        "resolved": len(app_id_map),
        "unresolved": len(app_nos) - len(app_id_map),
        "insert": insert_stats,
    }


# ===========================================================================
# PASS 2 — Chronological materialization
# ===========================================================================

# Event types that affect restrictions
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


def _compute_state_from_events(events_rows: list) -> dict:
    """Walk events chronologically and compute final state.

    events_rows: list of tuples from DB query, ordered by bulletin_date ASC:
        (event_type, event_subtype, old_value, new_value, details, bulletin_date)

    Returns dict of column values for trademarks table update.
    """
    effective_status = None
    active_restrictions = 0
    current_holder_name = None
    holder_changed_at = None
    renewal_expiry = None
    last_event_type = None
    last_event_date = None
    event_flags = {}
    total_count = len(events_rows)

    for (event_type, event_subtype, old_value, new_value, details, bulletin_date) in events_rows:
        last_event_type = event_type
        last_event_date = bulletin_date

        # Transfer/merger → update holder
        if event_type in TRANSFER_TYPES:
            if new_value:
                holder_clean = new_value.split("(")[0].strip()
                if holder_clean:
                    current_holder_name = holder_clean
                    holder_changed_at = bulletin_date
            effective_status = "Devredildi"

        # Cancellation
        elif event_type == "cancellation":
            effective_status = "İptal Edildi"

        # Withdrawal
        elif event_type == "withdrawal":
            effective_status = "Geri Çekildi"

        # Renewal → compute expiry (+10 years)
        elif event_type == "renewal":
            det = details if isinstance(details, dict) else {}
            renewal_date_str = det.get("renewal_date") or new_value
            renewal_date = _parse_date(str(renewal_date_str)) if renewal_date_str else None
            if renewal_date:
                renewal_expiry = renewal_date.replace(year=renewal_date.year + 10)
            effective_status = "Yenilendi"

        # Seizure/injunction → increment
        elif event_type in RESTRICTION_TYPES:
            active_restrictions += 1

        # Lift → decrement (floor at 0)
        elif event_type in RESTRICTION_LIFT_TYPES:
            active_restrictions = max(0, active_restrictions - 1)

        # Flag-bearing events
        if event_type in FLAG_TYPES:
            event_flags[FLAG_TYPES[event_type]] = True

        # Court-related subtype → flag
        if event_subtype and "court" in event_subtype.lower():
            event_flags["has_court_order"] = True

    return {
        "effective_status": effective_status,
        "active_restriction_count": active_restrictions,
        "current_holder_name": current_holder_name,
        "holder_changed_at": holder_changed_at,
        "renewal_expiry": renewal_expiry,
        "last_event_type": last_event_type,
        "last_event_date": last_event_date,
        "has_restrictions": active_restrictions > 0,
        "event_flags": event_flags,
        "total_event_count": total_count,
    }


def materialize_all(conn, app_nos: Optional[List[str]] = None,
                    dry_run: bool = False) -> dict:
    """Recompute event-derived columns for all (or specified) trademarks.

    Queries trademark_events ordered by bulletin_date ASC, walks events
    per trademark, and batch-updates trademarks table.
    """
    stats = {
        "trademarks_processed": 0,
        "effective_status_set": 0,
        "holders_updated": 0,
        "restrictions_set": 0,
        "renewals_set": 0,
        "flags_set": 0,
    }

    cur = conn.cursor()

    # Get all distinct application_nos that have events + their trademark_id
    if app_nos:
        placeholders = ",".join(["%s"] * len(app_nos))
        cur.execute(
            f"""SELECT DISTINCT te.application_no, t.id
                FROM trademark_events te
                JOIN trademarks t ON t.application_no = te.application_no
                WHERE te.application_no IN ({placeholders})""",
            app_nos,
        )
    else:
        cur.execute(
            """SELECT DISTINCT te.application_no, t.id
               FROM trademark_events te
               JOIN trademarks t ON t.application_no = te.application_no"""
        )

    tm_map = {}  # app_no → trademark_id
    for row in cur.fetchall():
        tm_map[row[0]] = str(row[1])

    logger.info(f"Materializing state for {len(tm_map)} trademarks with events...")

    if not tm_map or dry_run:
        stats["trademarks_processed"] = len(tm_map)
        cur.close()
        return stats

    # Fetch ALL events for these trademarks, ordered chronologically
    all_app_nos = list(tm_map.keys())
    events_by_app: Dict[str, list] = {a: [] for a in all_app_nos}

    for i in range(0, len(all_app_nos), 1000):
        chunk = all_app_nos[i:i+1000]
        placeholders = ",".join(["%s"] * len(chunk))
        cur.execute(
            f"""SELECT application_no, event_type, event_subtype,
                       old_value, new_value, details, bulletin_date
                FROM trademark_events
                WHERE application_no IN ({placeholders})
                ORDER BY bulletin_date ASC NULLS FIRST, created_at ASC""",
            chunk,
        )
        for row in cur.fetchall():
            app_no = row[0]
            events_by_app[app_no].append(row[1:])  # (event_type, subtype, old, new, details, date)

    # Process each trademark and batch updates
    batch_size = 500
    update_batch = []

    for app_no, events_rows in events_by_app.items():
        if not events_rows:
            continue

        state = _compute_state_from_events(events_rows)
        tm_id = tm_map[app_no]

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
            tm_id,
        ))

        # Track stats
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

        # Flush batch
        if len(update_batch) >= batch_size:
            _flush_materialize_batch(cur, update_batch)
            conn.commit()
            update_batch = []

    # Final flush
    if update_batch:
        _flush_materialize_batch(cur, update_batch)
        conn.commit()

    cur.close()
    logger.info(f"Materialization complete: {json.dumps(stats, indent=2)}")
    return stats


def _flush_materialize_batch(cur, batch: list):
    """Execute batch UPDATE for materialized state."""
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
        cur, sql, batch,
        template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        page_size=500,
    )


# ===========================================================================
# Main
# ===========================================================================
def main():
    parser = argparse.ArgumentParser(description="Ingest trademark events into DB")
    parser.add_argument("--folder", type=str, default=None,
                        help="Process single folder (e.g. GZ_499_2026-01-30)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without DB writes")
    parser.add_argument("--source", type=str, choices=["GZ", "BLT", "all"], default="all",
                        help="Process only GZ or BLT folders")
    parser.add_argument("--insert-only", action="store_true",
                        help="Only insert events, skip materialization")
    parser.add_argument("--materialize-only", action="store_true",
                        help="Skip insertion, only recompute materialized columns")
    args = parser.parse_args()

    with connection_context() as conn:
        # --- Pass 1: Insert events ---
        if not args.materialize_only:
            if args.folder:
                folder = ROOT_DIR / args.folder
                if not folder.exists():
                    logger.error(f"Folder not found: {folder}")
                    sys.exit(1)
                result = process_folder(folder, conn, dry_run=args.dry_run)
                logger.info(f"Insert result: {json.dumps(result, indent=2, default=str)}")
            else:
                folders = sorted(ROOT_DIR.iterdir())
                total_stats = {
                    "processed": 0, "skipped": 0, "total_inserted": 0,
                    "total_resolved": 0, "total_unresolved": 0,
                }

                for folder in folders:
                    if not folder.is_dir():
                        continue
                    if args.source == "GZ" and not folder.name.startswith("GZ_"):
                        continue
                    if args.source == "BLT" and not folder.name.startswith("BLT_"):
                        continue
                    if not (folder / "events.json").exists():
                        continue

                    result = process_folder(folder, conn, dry_run=args.dry_run)
                    if result["status"] == "success":
                        total_stats["processed"] += 1
                        total_stats["total_inserted"] += result["insert"]["inserted"]
                        total_stats["total_resolved"] += result["resolved"]
                        total_stats["total_unresolved"] += result["unresolved"]
                    else:
                        total_stats["skipped"] += 1

                logger.info("=" * 60)
                logger.info(f"PASS 1 (Insert): {json.dumps(total_stats, indent=2)}")

        # --- Pass 2: Materialize ---
        if not args.insert_only:
            logger.info("=" * 60)
            logger.info("PASS 2: Chronological materialization...")

            # If single folder, only materialize affected app_nos
            mat_app_nos = None
            if args.folder and not args.materialize_only:
                data = _load_events(ROOT_DIR / args.folder)
                if data:
                    mat_app_nos = list({
                        ev["application_no"] for ev in data["events"]
                        if ev.get("application_no")
                        and ev["application_no"] not in ("UNKNOWN", "MADRID_UNKNOWN")
                    })

            mat_stats = materialize_all(conn, app_nos=mat_app_nos, dry_run=args.dry_run)
            logger.info(f"PASS 2 (Materialize): {json.dumps(mat_stats, indent=2)}")

            # Recompute final_status for affected trademarks
            if not args.dry_run:
                try:
                    from utils.status_reconciler import update_final_status_batch
                    update_final_status_batch(conn, app_nos=mat_app_nos)
                except Exception as fs_err:
                    logger.warning(f"final_status recompute skipped: {fs_err}")

        # --- Pass 3: Event alerts for watched trademarks ---
        if not args.insert_only and not args.dry_run:
            logger.info("=" * 60)
            logger.info("PASS 3: Scanning for event-based watchlist alerts...")
            try:
                from watchlist.scanner import scan_events_for_watchlist
                event_alerts = scan_events_for_watchlist(conn)
                logger.info(f"PASS 3 (Event Alerts): {event_alerts} alerts generated")
            except Exception as e:
                logger.warning(f"Event alert scan skipped: {e}")


if __name__ == "__main__":
    main()

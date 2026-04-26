"""
Benchmark, refresh, snapshot, and restore trademark name_tr translations.

This is the canonical offline translation refresh script.

Default behavior:
  - benchmark MADLAD against the current NLLB baseline on a curated fixture
  - export a snapshot of affected rows
  - refresh name_tr/detected_lang plus provenance fields for all named rows
  - sync matching on-disk metadata.json records back from the refreshed DB state

Examples:
  python scripts/regenerate_name_tr.py --benchmark-only
  python scripts/regenerate_name_tr.py --dry-run --limit 5000
  python scripts/regenerate_name_tr.py
  python scripts/regenerate_name_tr.py --null-only
  python scripts/regenerate_name_tr.py --restore-from artifacts/translation_refresh/.../snapshot.jsonl.gz
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Sequence

import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()
os.environ.setdefault("DB_PORT", "5433")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "translation_refresh"
DEFAULT_BENCHMARK_FIXTURE = PROJECT_ROOT / "scripts" / "fixtures" / "madlad_translation_benchmark.json"
DEFAULT_METADATA_ROOT = PROJECT_ROOT / "bulletins" / "Marka"
DEFAULT_PROGRESS_STATE_NAME = "name_tr_refresh_progress.json"
DEFAULT_PROGRESS_VERSION = 4
DEFAULT_BASELINE_BACKEND = "nllb"
DEFAULT_CANDIDATE_BACKEND = "madlad"
ORDERING_MODE_APPLICATION_DATE_DESC = "application_date_desc"
ORDERING_MODE_ID_ASC = "id_asc"
BATCH_ORDERING_CHOICES = {
    ORDERING_MODE_APPLICATION_DATE_DESC,
    ORDERING_MODE_ID_ASC,
}
BATCH_UPDATE_SIZE = 500
METADATA_QUERY_BATCH_SIZE = 1000

try:
    from config.settings import settings
except Exception:
    settings = None

from utils.idf_scoring import turkish_lower
from utils.translation import (
    OFFLINE_TRANSLATION_BACKEND,
    TRANSLATION_BACKEND,
    batch_translate_to_turkish,
    build_translation_provenance,
    detect_language_fasttext,
    get_translation_backend_info,
    get_translations,
    has_prompt_leakage,
    initialize,
)


def get_db_connection():
    kwargs = {
        "host": os.environ.get("DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("DB_PORT", "5433")),
        "dbname": os.environ.get("DB_NAME", "trademark_db"),
        "user": os.environ.get("DB_USER", "turk_patent"),
        "password": os.environ.get("DB_PASSWORD", ""),
        "connect_timeout": 30,
    }
    if settings is not None:
        kwargs.update(
            {
                "host": settings.database.host,
                "port": settings.database.port,
                "dbname": settings.database.name,
                "user": settings.database.user,
                "password": settings.database.password,
            }
        )
    return psycopg2.connect(**kwargs)


def _ensure_output_root(path: Path | None = None) -> Path:
    root = path or DEFAULT_OUTPUT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def _create_run_dir(root: Path | None = None) -> Path:
    output_root = _ensure_output_root(root)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"name_tr_refresh_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _progress_state_path(root: Path | None = None) -> Path:
    output_root = _ensure_output_root(root)
    return output_root / DEFAULT_PROGRESS_STATE_NAME


def _utcnow_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_ordering_mode(value: str | None) -> str:
    candidate = (value or ORDERING_MODE_APPLICATION_DATE_DESC).strip().lower()
    if candidate not in BATCH_ORDERING_CHOICES:
        raise ValueError(f"Unsupported ordering mode: {candidate}")
    return candidate


def _normalize_campaign_watermark(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_progress_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_progress_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _utcnow_z()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_resumable_progress_state(
    state: dict | None,
    *,
    backend: str,
    null_only: bool,
    limit: int | None,
    dry_run: bool,
    metadata_root: Path,
    ordering_mode: str,
    campaign_watermark: str | None,
) -> bool:
    if not state:
        return False
    if state.get("status") == "completed":
        return False
    if state.get("version") != DEFAULT_PROGRESS_VERSION:
        return False
    return (
        state.get("backend") == backend
        and bool(state.get("null_only")) == bool(null_only)
        and state.get("limit") == limit
        and bool(state.get("dry_run")) == bool(dry_run)
        and state.get("metadata_root") == str(metadata_root)
        and state.get("ordering_mode", ORDERING_MODE_ID_ASC) == ordering_mode
        and state.get("campaign_watermark") == campaign_watermark
    )


def _merge_resume_from_id(explicit_resume_from_id: str | None, state: dict | None) -> str | None:
    candidates = [item for item in (explicit_resume_from_id, (state or {}).get("last_processed_id")) if item]
    if not candidates:
        return None
    return max(candidates)


def _resolve_run_dir(output_root: Path, state: dict | None) -> Path:
    if state:
        run_dir = state.get("run_dir")
        if run_dir:
            run_path = Path(run_dir)
            if run_path.exists():
                return run_path
    return _create_run_dir(output_root)


def _build_initial_progress_state(
    *,
    backend: str,
    null_only: bool,
    limit: int | None,
    dry_run: bool,
    metadata_root: Path,
    run_dir: Path,
    snapshot_path: Path,
    ordering_mode: str,
    campaign_watermark: str | None,
    translate_batch_size: int | None,
    resume_from_id: str | None,
) -> dict:
    return {
        "version": DEFAULT_PROGRESS_VERSION,
        "status": "running",
        "backend": backend,
        "null_only": bool(null_only),
        "limit": limit,
        "dry_run": bool(dry_run),
        "metadata_root": str(metadata_root),
        "run_dir": str(run_dir),
        "snapshot_path": str(snapshot_path),
        "ordering_mode": ordering_mode,
        "campaign_watermark": campaign_watermark,
        "translate_batch_size": translate_batch_size,
        "started_at": _utcnow_z(),
        "updated_at": _utcnow_z(),
        "last_processed_id": resume_from_id,
        "summary": {},
        "metadata_sync": {
            "completed_files": [],
        },
    }


def _query_scope(
    limit: int | None = None,
    null_only: bool = False,
    resume_from_id: str | None = None,
    *,
    ordering_mode: str = ORDERING_MODE_APPLICATION_DATE_DESC,
    campaign_backend: str | None = None,
    campaign_model_name: str | None = None,
    campaign_watermark: str | None = None,
) -> tuple[str, list]:
    where = ["name IS NOT NULL", "name != ''"]
    params: list = []
    if null_only:
        where.append("(name_tr IS NULL OR detected_lang IS NULL)")
    if ordering_mode == ORDERING_MODE_ID_ASC and resume_from_id:
        where.append("id > %s::uuid")
        params.append(resume_from_id)
    if campaign_watermark:
        where.append(
            """
            NOT (
                name_tr_backend = %s
                AND name_tr_model = %s
                AND name_tr_updated_at IS NOT NULL
                AND name_tr_updated_at >= %s::timestamptz
            )
            """.strip()
        )
        params.extend([campaign_backend, campaign_model_name, campaign_watermark])
    order_by = "application_date DESC NULLS LAST, id DESC"
    if ordering_mode == ORDERING_MODE_ID_ASC:
        order_by = "id"
    sql = f"""
        SELECT id::text, name, name_tr, detected_lang, name_tr_backend, name_tr_model, name_tr_updated_at
        FROM trademarks
        WHERE {' AND '.join(where)}
        ORDER BY {order_by}
    """
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    return sql, params


def _resolve_metadata_root(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    if settings is not None:
        return Path(settings.paths.data_root).resolve()
    return DEFAULT_METADATA_ROOT.resolve()


def _normalize_application_no(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _serialize_timestamp(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _chunked(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _fetch_translation_rows_by_application_nos(conn, application_nos: Sequence[str]) -> dict[str, dict]:
    normalized = []
    seen = set()
    for item in application_nos:
        app_no = _normalize_application_no(item)
        if app_no and app_no not in seen:
            seen.add(app_no)
            normalized.append(app_no)
    if not normalized:
        return {}

    rows_by_app_no: dict[str, dict] = {}
    with conn.cursor() as cur:
        for chunk in _chunked(normalized, METADATA_QUERY_BATCH_SIZE):
            cur.execute(
                """
                SELECT application_no, name_tr, detected_lang, name_tr_backend, name_tr_model, name_tr_updated_at
                FROM trademarks
                WHERE application_no = ANY(%s)
                """,
                (list(chunk),),
            )
            for row in cur.fetchall():
                app_no = _normalize_application_no(row[0])
                if not app_no:
                    continue
                rows_by_app_no[app_no] = {
                    "name_tr": row[1],
                    "detected_lang": row[2],
                    "name_tr_backend": row[3],
                    "name_tr_model": row[4],
                    "name_tr_updated_at": _serialize_timestamp(row[5]),
                }
    return rows_by_app_no


def sync_metadata_file(conn, metadata_path: Path) -> dict:
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"metadata root is not a list: {metadata_path}")

    application_nos = [
        app_no
        for rec in data
        if (app_no := _normalize_application_no(rec.get("APPLICATIONNO")))
    ]
    translations = _fetch_translation_rows_by_application_nos(conn, application_nos)

    records_updated = 0
    records_missing_db = 0
    for rec in data:
        app_no = _normalize_application_no(rec.get("APPLICATIONNO"))
        if not app_no:
            continue
        current = translations.get(app_no)
        if current is None:
            records_missing_db += 1
            continue

        changed = False
        for field in ("name_tr", "detected_lang"):
            if rec.get(field) != current[field]:
                rec[field] = current[field]
                changed = True

        for field in ("name_tr_backend", "name_tr_model", "name_tr_updated_at"):
            new_value = current[field]
            if new_value is None:
                if field in rec:
                    rec.pop(field)
                    changed = True
                continue
            if rec.get(field) != new_value:
                rec[field] = new_value
                changed = True

        if changed:
            records_updated += 1

    if records_updated:
        metadata_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "record_count": len(data),
        "records_updated": records_updated,
        "records_missing_db": records_missing_db,
        "file_updated": bool(records_updated),
    }


def sync_metadata_from_db(
    conn,
    *,
    root_dir: Path | str | None = None,
    progress_state_path: Path | None = None,
    progress_state: dict | None = None,
) -> dict:
    metadata_root = _resolve_metadata_root(root_dir)
    metadata_paths = sorted(metadata_root.glob("*/metadata.json"))
    completed_files = set((progress_state or {}).get("metadata_sync", {}).get("completed_files", []))
    summary = {
        "metadata_root": str(metadata_root),
        "files_scanned": len(metadata_paths),
        "files_updated": 0,
        "files_failed": 0,
        "files_skipped_from_progress": 0,
        "records_scanned": 0,
        "records_updated": 0,
        "records_missing_db": 0,
    }

    for metadata_path in metadata_paths:
        relative_path = metadata_path.relative_to(metadata_root).as_posix()
        if relative_path in completed_files:
            summary["files_skipped_from_progress"] += 1
            continue
        try:
            result = sync_metadata_file(conn, metadata_path)
        except Exception as exc:
            logger.warning("Metadata sync failed for %s: %s", metadata_path, exc)
            summary["files_failed"] += 1
            continue

        summary["records_scanned"] += result["record_count"]
        summary["records_updated"] += result["records_updated"]
        summary["records_missing_db"] += result["records_missing_db"]
        if result["file_updated"]:
            summary["files_updated"] += 1
        if progress_state is not None:
            progress_state.setdefault("metadata_sync", {}).setdefault("completed_files", []).append(relative_path)
            progress_state["metadata_sync"]["summary"] = summary.copy()
            if progress_state_path is not None:
                _write_progress_state(progress_state_path, progress_state)

    logger.info(
        "Metadata sync complete: files=%s updated=%s failed=%s skipped=%s records_updated=%s",
        summary["files_scanned"],
        summary["files_updated"],
        summary["files_failed"],
        summary["files_skipped_from_progress"],
        summary["records_updated"],
    )
    return summary


def load_benchmark_cases(path: Path | None = None) -> list[dict]:
    fixture_path = Path(path or DEFAULT_BENCHMARK_FIXTURE)
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _case_passes(case: dict, actual_lang: str, actual_tr: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    expected_lang = case.get("expected_lang")
    if expected_lang and actual_lang != expected_lang:
        reasons.append(f"lang:{actual_lang}!={expected_lang}")

    acceptable = [turkish_lower(item) for item in case.get("acceptable_tr", [])]
    actual_norm = turkish_lower(actual_tr or "")
    mode = case.get("mode", "exact")
    if mode == "preserve":
        if actual_norm not in acceptable:
            reasons.append(f"preserve:{actual_norm}")
    elif acceptable and actual_norm not in acceptable:
        reasons.append(f"translation:{actual_norm}")

    return (len(reasons) == 0, reasons)


def run_benchmark(
    fixture_path: Path | None = None,
    baseline_backend: str = DEFAULT_BASELINE_BACKEND,
    candidate_backend: str = DEFAULT_CANDIDATE_BACKEND,
    report_path: Path | None = None,
) -> dict:
    cases = load_benchmark_cases(fixture_path)
    logger.info("Running translation benchmark on %s curated cases", len(cases))

    if not initialize(backend=baseline_backend):
        raise RuntimeError(f"Failed to initialize baseline backend: {baseline_backend}")
    if not initialize(backend=candidate_backend):
        raise RuntimeError(f"Failed to initialize candidate backend: {candidate_backend}")

    details = []
    baseline_passes = 0
    candidate_passes = 0
    for case in cases:
        baseline_result = get_translations(case["name"], backend=baseline_backend)
        candidate_result = get_translations(case["name"], backend=candidate_backend)
        baseline_ok, baseline_reasons = _case_passes(
            case,
            baseline_result.get("detected_lang", "unknown"),
            baseline_result.get("tr") or "",
        )
        candidate_ok, candidate_reasons = _case_passes(
            case,
            candidate_result.get("detected_lang", "unknown"),
            candidate_result.get("tr") or "",
        )
        baseline_passes += int(baseline_ok)
        candidate_passes += int(candidate_ok)
        details.append(
            {
                "case": case,
                "baseline": baseline_result,
                "candidate": candidate_result,
                "baseline_pass": baseline_ok,
                "candidate_pass": candidate_ok,
                "baseline_reasons": baseline_reasons,
                "candidate_reasons": candidate_reasons,
            }
        )

    total = len(cases)
    report = {
        "fixture_path": str(Path(fixture_path or DEFAULT_BENCHMARK_FIXTURE)),
        "baseline_backend": baseline_backend,
        "candidate_backend": candidate_backend,
        "baseline_passes": baseline_passes,
        "candidate_passes": candidate_passes,
        "baseline_pass_rate": baseline_passes / total if total else 0.0,
        "candidate_pass_rate": candidate_passes / total if total else 0.0,
        "candidate_meets_gate": candidate_passes >= baseline_passes and (candidate_passes / total if total else 0.0) >= 0.75,
        "details": details,
    }

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "Benchmark complete: baseline=%s/%s candidate=%s/%s gate=%s",
        baseline_passes,
        total,
        candidate_passes,
        total,
        report["candidate_meets_gate"],
    )
    return report


def export_snapshot(
    conn,
    output_path: Path,
    *,
    null_only: bool = False,
    limit: int | None = None,
    resume_from_id: str | None = None,
    ordering_mode: str = ORDERING_MODE_APPLICATION_DATE_DESC,
    campaign_backend: str | None = None,
    campaign_model_name: str | None = None,
    campaign_watermark: str | None = None,
) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sql, params = _query_scope(
        limit=limit,
        null_only=null_only,
        resume_from_id=resume_from_id,
        ordering_mode=ordering_mode,
        campaign_backend=campaign_backend,
        campaign_model_name=campaign_model_name,
        campaign_watermark=campaign_watermark,
    )
    row_count = 0
    started = time.time()

    with conn.cursor(name="name_tr_snapshot_cursor") as cur, gzip.open(output_path, "wt", encoding="utf-8") as handle:
        cur.itersize = 5000
        cur.execute(sql, params)
        while True:
            rows = cur.fetchmany(5000)
            if not rows:
                break
            for row in rows:
                payload = {
                    "id": row[0],
                    "name": row[1],
                    "name_tr": row[2],
                    "detected_lang": row[3],
                    "name_tr_backend": row[4],
                    "name_tr_model": row[5],
                    "name_tr_updated_at": row[6].isoformat() if row[6] else None,
                }
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                row_count += 1

    duration = time.time() - started
    summary = {
        "snapshot_path": str(output_path),
        "row_count": row_count,
        "duration_seconds": round(duration, 1),
    }
    logger.info("Snapshot export complete: %s rows -> %s", row_count, output_path)
    return summary


def restore_snapshot(conn, snapshot_path: Path, *, dry_run: bool = False) -> dict:
    if not snapshot_path.exists():
        raise FileNotFoundError(snapshot_path)

    started = time.time()
    restored = 0
    batch = []
    with gzip.open(snapshot_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            payload = json.loads(line)
            updated_at = payload.get("name_tr_updated_at")
            parsed_updated_at = None
            if updated_at:
                parsed_updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            batch.append(
                (
                    payload.get("name_tr"),
                    payload.get("detected_lang"),
                    payload.get("name_tr_backend"),
                    payload.get("name_tr_model"),
                    parsed_updated_at,
                    payload["id"],
                )
            )
            if len(batch) >= 1000:
                restored += _apply_restore_batch(conn, batch, dry_run=dry_run)
                batch = []
        if batch:
            restored += _apply_restore_batch(conn, batch, dry_run=dry_run)

    duration = time.time() - started
    summary = {
        "restored": restored,
        "snapshot_path": str(snapshot_path),
        "duration_seconds": round(duration, 1),
        "dry_run": dry_run,
    }
    logger.info("Restore complete: %s rows from %s", restored, snapshot_path)
    return summary


def _apply_restore_batch(conn, batch: Sequence[tuple], *, dry_run: bool = False) -> int:
    if not batch:
        return 0
    if dry_run:
        return len(batch)
    with conn.cursor() as cur:
        execute_batch(
            cur,
            """
            UPDATE trademarks
            SET name_tr = %s,
                detected_lang = %s,
                name_tr_backend = %s,
                name_tr_model = %s,
                name_tr_updated_at = %s
            WHERE id = %s::uuid
            """,
            batch,
            page_size=BATCH_UPDATE_SIZE,
        )
    conn.commit()
    return len(batch)


def _prepare_updates(rows: Sequence[tuple], translations: Sequence[tuple[str, str]], backend: str, updated_at: datetime) -> tuple[list[tuple], dict]:
    provenance = build_translation_provenance(backend=backend, updated_at=updated_at)
    updates: list[tuple] = []
    stats = {
        "processed": 0,
        "translation_changed": 0,
        "lang_changed": 0,
        "provenance_changed": 0,
        "translated": 0,
        "preserved": 0,
    }

    for row, (new_name_tr, new_lang) in zip(rows, translations):
        row_id, name, old_name_tr, old_lang, old_backend, old_model, old_updated_at = row
        if not name or not str(name).strip():
            continue

        normalized_tr = turkish_lower(new_name_tr) if new_name_tr else turkish_lower(name)
        final_lang = new_lang if new_lang and new_lang != "unknown" else (old_lang or "unknown")
        stats["processed"] += 1
        if normalized_tr != turkish_lower(name):
            stats["translated"] += 1
        else:
            stats["preserved"] += 1
        if old_name_tr != normalized_tr:
            stats["translation_changed"] += 1
        if old_lang != final_lang:
            stats["lang_changed"] += 1
        if old_backend != provenance["name_tr_backend"] or old_model != provenance["name_tr_model"]:
            stats["provenance_changed"] += 1

        updates.append(
            (
                normalized_tr,
                final_lang,
                provenance["name_tr_backend"],
                provenance["name_tr_model"],
                updated_at,
                row_id,
            )
        )

    return updates, stats


def _row_needs_model_refresh(row: tuple, backend: str, model_name: str) -> bool:
    del backend, model_name
    _, name, current_name_tr, _, _, _, _ = row
    if not name or not str(name).strip():
        return False
    if has_prompt_leakage(current_name_tr):
        return True
    return True


def run_refresh(
    conn,
    *,
    backend: str,
    batch_size: int,
    translate_batch_size: int | None = None,
    null_only: bool = False,
    limit: int | None = None,
    resume_from_id: str | None = None,
    ordering_mode: str = ORDERING_MODE_APPLICATION_DATE_DESC,
    campaign_watermark: str | None = None,
    dry_run: bool = False,
    progress_state_path: Path | None = None,
    progress_state: dict | None = None,
) -> dict:
    if not initialize(backend=backend):
        raise RuntimeError(f"Failed to initialize translation backend: {backend}")

    logger.info("Loading FastText LangID model...")
    iso, nllb_code, confidence = detect_language_fasttext("test")
    if iso == "unknown":
        logger.warning(
            "FastText LangID unavailable or inconclusive (iso=%s nllb=%s conf=%.3f); "
            "MADLAD refresh will still evaluate every trademark name and preserve an existing detected_lang when detection is unknown.",
            iso,
            nllb_code,
            confidence,
        )
    else:
        logger.info("FastText ready (iso=%s nllb=%s conf=%.3f)", iso, nllb_code, confidence)

    started = time.time()
    backend_info = get_translation_backend_info(backend)
    model_name = backend_info["model_name"]
    summary = {
        "processed": 0,
        "translation_changed": 0,
        "lang_changed": 0,
        "provenance_changed": 0,
        "translated": 0,
        "preserved": 0,
        "model_rows": 0,
        "errors": 0,
        "dry_run": dry_run,
        "backend": backend,
        "ordering_mode": ordering_mode,
        "campaign_watermark": campaign_watermark,
        "translate_batch_size": translate_batch_size,
    }

    last_seen_id = resume_from_id if ordering_mode == ORDERING_MODE_ID_ASC else None
    while True:
        page_sql, page_params = _query_scope(
            limit=batch_size if limit is None else min(batch_size, limit - summary["processed"]),
            null_only=null_only,
            resume_from_id=last_seen_id,
            ordering_mode=ordering_mode,
            campaign_backend=backend,
            campaign_model_name=model_name,
            campaign_watermark=campaign_watermark,
        )
        with conn.cursor() as cur:
            cur.execute(page_sql, page_params)
            rows = cur.fetchall()
        if not rows:
            break

        translations: list[tuple[str, str] | None] = [None] * len(rows)
        model_names: list[str] = []
        model_indexes: list[int] = []

        for index, row in enumerate(rows):
            name = row[1] or ""
            if _row_needs_model_refresh(row, backend, model_name):
                model_indexes.append(index)
                model_names.append(name)
                continue

            detected_lang, _, _ = detect_language_fasttext(name)
            preserved_translation = row[2] if row[2] else turkish_lower(name)
            translations[index] = (turkish_lower(preserved_translation), detected_lang)

        if model_names:
            try:
                model_results = batch_translate_to_turkish(
                    model_names,
                    backend=backend,
                    batch_size=translate_batch_size,
                )
            except Exception as exc:
                logger.error("Batch translation failed: %s", exc)
                summary["errors"] += len(rows)
                if ordering_mode == ORDERING_MODE_ID_ASC:
                    last_seen_id = rows[-1][0]
                continue
            for target_index, result in zip(model_indexes, model_results):
                translations[target_index] = result

        completed_translations = [
            item if item is not None else (turkish_lower(row[1] or ""), detect_language_fasttext(row[1] or "")[0])
            for row, item in zip(rows, translations)
        ]

        batch_time = datetime.now(timezone.utc).replace(microsecond=0)
        updates, batch_stats = _prepare_updates(rows, completed_translations, backend, batch_time)

        if updates and not dry_run:
            with conn.cursor() as write_cur:
                execute_batch(
                    write_cur,
                    """
                    UPDATE trademarks
                    SET name_tr = %s,
                        detected_lang = %s,
                        name_tr_backend = %s,
                        name_tr_model = %s,
                        name_tr_updated_at = %s
                    WHERE id = %s::uuid
                    """,
                    updates,
                    page_size=BATCH_UPDATE_SIZE,
                )
            conn.commit()

        for key, value in batch_stats.items():
            summary[key] += value
        summary["model_rows"] += len(model_names)

        elapsed = time.time() - started
        rate = summary["processed"] / elapsed if elapsed > 0 else 0
        logger.info(
            "Progress: processed=%s translated=%s preserved=%s changed=%s lang_changed=%s model_rows=%s rate=%.1f/s",
            f"{summary['processed']:,}",
            f"{summary['translated']:,}",
            f"{summary['preserved']:,}",
            f"{summary['translation_changed']:,}",
            f"{summary['lang_changed']:,}",
            f"{summary['model_rows']:,}",
            rate,
        )
        last_seen_id = rows[-1][0]
        if progress_state is not None:
            progress_state["last_processed_id"] = last_seen_id
            progress_state["summary"] = summary.copy()
            if progress_state_path is not None:
                _write_progress_state(progress_state_path, progress_state)
        if limit is not None and summary["processed"] >= limit:
            break

    summary["duration_seconds"] = round(time.time() - started, 1)
    return summary


def write_summary(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh trademark name_tr translations")
    parser.add_argument("--backend", default=OFFLINE_TRANSLATION_BACKEND or DEFAULT_CANDIDATE_BACKEND, help="Candidate backend for refresh")
    parser.add_argument("--baseline-backend", default=TRANSLATION_BACKEND or DEFAULT_BASELINE_BACKEND, help="Baseline backend for benchmark")
    parser.add_argument("--benchmark-fixture", default=str(DEFAULT_BENCHMARK_FIXTURE), help="Curated benchmark fixture JSON")
    parser.add_argument("--benchmark-only", action="store_true", help="Run benchmark and stop")
    parser.add_argument("--skip-benchmark", action="store_true", help="Skip the benchmark gate and run refresh directly")
    parser.add_argument("--dry-run", action="store_true", help="Run benchmark/refresh without DB updates")
    parser.add_argument("--null-only", action="store_true", help="Only process rows missing name_tr or detected_lang")
    parser.add_argument("--batch-size", type=int, default=512, help="Number of rows per translation batch")
    parser.add_argument("--translate-batch-size", type=int, default=None, help="MADLAD generation microbatch size override")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit for bounded runs")
    parser.add_argument("--resume-from-id", type=str, default=None, help="Resume processing after this UUID")
    parser.add_argument(
        "--ordering-mode",
        type=str,
        default=ORDERING_MODE_APPLICATION_DATE_DESC,
        choices=sorted(BATCH_ORDERING_CHOICES),
        help="Refresh row ordering mode",
    )
    parser.add_argument("--campaign-watermark", type=str, default=None, help="Skip MADLAD rows updated at or after this UTC timestamp")
    parser.add_argument("--output-root", type=str, default=str(DEFAULT_OUTPUT_ROOT), help="Directory for benchmark/snapshot/summary artifacts")
    parser.add_argument("--metadata-root", type=str, default=None, help="Optional bulletins root for metadata.json sync")
    parser.add_argument("--restore-from", type=str, default=None, help="Restore from a snapshot .jsonl.gz file instead of refreshing")
    return parser


def main(argv: list[str] | None = None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    output_root = Path(args.output_root)
    metadata_root = _resolve_metadata_root(args.metadata_root)
    ordering_mode = _normalize_ordering_mode(args.ordering_mode)
    campaign_watermark = _normalize_campaign_watermark(args.campaign_watermark)
    if campaign_watermark is None and args.restore_from is None and args.backend == DEFAULT_CANDIDATE_BACKEND:
        campaign_watermark = _utcnow_z()
    progress_path = _progress_state_path(output_root)
    existing_progress = _load_progress_state(progress_path)
    resumable_progress = (
        existing_progress
        if _is_resumable_progress_state(
            existing_progress,
            backend=args.backend,
            null_only=args.null_only,
            limit=args.limit,
            dry_run=args.dry_run,
            metadata_root=metadata_root,
            ordering_mode=ordering_mode,
            campaign_watermark=campaign_watermark,
        )
        else None
    )
    effective_resume_from_id = (
        _merge_resume_from_id(args.resume_from_id, resumable_progress)
        if ordering_mode == ORDERING_MODE_ID_ASC
        else None
    )
    run_dir = _resolve_run_dir(output_root, resumable_progress)
    benchmark_report_path = run_dir / "benchmark_report.json"
    summary_path = run_dir / "refresh_summary.json"
    snapshot_path = run_dir / "snapshot.jsonl.gz"
    if resumable_progress:
        saved_snapshot_path = resumable_progress.get("snapshot_path")
        if saved_snapshot_path and Path(saved_snapshot_path).exists():
            snapshot_path = Path(saved_snapshot_path)

    logger.info("Artifacts will be written to %s", run_dir)
    if effective_resume_from_id:
        logger.info("Effective resume point: %s", effective_resume_from_id)
    if campaign_watermark:
        logger.info("Active MADLAD campaign watermark: %s", campaign_watermark)

    progress_state = _build_initial_progress_state(
        backend=args.backend,
        null_only=args.null_only,
        limit=args.limit,
        dry_run=args.dry_run,
        metadata_root=metadata_root,
        run_dir=run_dir,
        snapshot_path=snapshot_path,
        ordering_mode=ordering_mode,
        campaign_watermark=campaign_watermark,
        translate_batch_size=args.translate_batch_size,
        resume_from_id=effective_resume_from_id,
    )
    if resumable_progress:
        progress_state["started_at"] = resumable_progress.get("started_at", progress_state["started_at"])
        progress_state["metadata_sync"] = resumable_progress.get("metadata_sync", {"completed_files": []})
        progress_state["summary"] = resumable_progress.get("summary", {})
    _write_progress_state(progress_path, progress_state)

    conn = get_db_connection()
    conn.autocommit = False
    try:
        if args.restore_from:
            restore_summary = restore_snapshot(conn, Path(args.restore_from), dry_run=args.dry_run)
            if not args.dry_run:
                restore_summary["metadata_sync"] = sync_metadata_from_db(
                    conn,
                    root_dir=metadata_root,
                    progress_state_path=progress_path,
                    progress_state=progress_state,
                )
            progress_state["status"] = "completed"
            progress_state["summary"] = restore_summary
            _write_progress_state(progress_path, progress_state)
            write_summary(summary_path, restore_summary)
            logger.info("Restore summary written to %s", summary_path)
            return 0

        benchmark = None
        if not args.skip_benchmark:
            benchmark = run_benchmark(
                fixture_path=Path(args.benchmark_fixture),
                baseline_backend=args.baseline_backend,
                candidate_backend=args.backend,
                report_path=benchmark_report_path,
            )
            if not benchmark["candidate_meets_gate"]:
                raise RuntimeError(
                    f"Benchmark gate failed for {args.backend}: "
                    f"{benchmark['candidate_passes']}/{len(benchmark['details'])} "
                    f"vs baseline {benchmark['baseline_passes']}/{len(benchmark['details'])}"
                )
            logger.info("Benchmark report written to %s", benchmark_report_path)
        if args.benchmark_only:
            return 0

        if snapshot_path.exists() and resumable_progress:
            snapshot = {
                "snapshot_path": str(snapshot_path),
                "row_count": resumable_progress.get("snapshot_row_count"),
                "duration_seconds": resumable_progress.get("snapshot_duration_seconds"),
                "reused": True,
            }
            logger.info("Reusing existing snapshot: %s", snapshot_path)
        else:
            snapshot = export_snapshot(
                conn,
                snapshot_path,
                null_only=args.null_only,
                limit=args.limit,
                resume_from_id=effective_resume_from_id,
                ordering_mode=ordering_mode,
                campaign_backend=args.backend,
                campaign_model_name=get_translation_backend_info(args.backend)["model_name"],
                campaign_watermark=campaign_watermark,
            )
            progress_state["snapshot_path"] = str(snapshot_path)
            progress_state["snapshot_row_count"] = snapshot.get("row_count")
            progress_state["snapshot_duration_seconds"] = snapshot.get("duration_seconds")
            _write_progress_state(progress_path, progress_state)
            logger.info("Snapshot summary: %s", snapshot)

        refresh_summary = run_refresh(
            conn,
            backend=args.backend,
            batch_size=args.batch_size,
            translate_batch_size=args.translate_batch_size,
            null_only=args.null_only,
            limit=args.limit,
            resume_from_id=effective_resume_from_id,
            ordering_mode=ordering_mode,
            campaign_watermark=campaign_watermark,
            dry_run=args.dry_run,
            progress_state_path=progress_path,
            progress_state=progress_state,
        )
        if not args.dry_run:
            refresh_summary["metadata_sync"] = sync_metadata_from_db(
                conn,
                root_dir=metadata_root,
                progress_state_path=progress_path,
                progress_state=progress_state,
            )
        refresh_summary["benchmark_report"] = str(benchmark_report_path)
        refresh_summary["snapshot_path"] = str(snapshot_path)
        progress_state["status"] = "completed"
        progress_state["summary"] = refresh_summary
        _write_progress_state(progress_path, progress_state)
        write_summary(summary_path, refresh_summary)
        logger.info("Refresh summary written to %s", summary_path)
        logger.info("Refresh complete: %s", refresh_summary)
        return 0
    except Exception as exc:
        conn.rollback()
        logger.error("name_tr refresh failed: %s", exc)
        progress_state["status"] = "failed"
        progress_state["error"] = str(exc)
        _write_progress_state(progress_path, progress_state)
        error_summary = {
            "error": str(exc),
            "benchmark_report": str(benchmark_report_path),
            "snapshot_path": str(snapshot_path),
        }
        write_summary(summary_path, error_summary)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

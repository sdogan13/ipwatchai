"""Canonical packaged parallel GPU + DB pipeline."""

import argparse
import importlib
import logging
import os
import queue
import threading
import time
from pathlib import Path

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"


def _resolve_local_parallel_root(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


ROOT_DIR = _resolve_local_parallel_root(
    os.environ.get("PIPELINE_BULLETINS_ROOT") or os.environ.get("DATA_ROOT"),
    _LOCAL_DEFAULT_BULLETINS_ROOT,
)
LOG_FMT = "%(asctime)s [%(name)-6s] %(levelname)-5s %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger("PIPE")

SENTINEL = "__DONE__"
FEATURES = [
    "image_embedding",
    "dinov2_embedding",
    "text_embedding",
    "color_histogram",
    "logo_ocr_text",
    "name_tr",
    "detected_lang",
]


def load_ai_module():
    """Load the canonical packaged AI module."""
    return importlib.import_module("pipeline.ai")


def _extract_folder_number(name: str) -> int:
    """Extract the numeric bulletin or gazette number from a folder name."""
    import re

    match = re.search(r"_(\d+)", name)
    return int(match.group(1)) if match else 0


def folder_sort_key(name: str) -> tuple:
    """Sort BLT first, then GZ, then APP, with newest bulletin numbers first."""
    upper = name.upper()
    num = _extract_folder_number(name)
    if upper.startswith("BLT"):
        return (0, -num, name)
    if upper.startswith("GZ"):
        return (1, -num, name)
    return (2, -num, name)


def survey_folder(folder_path: Path) -> dict:
    """Quick feature check on a folder metadata.json using only the first 50KB."""
    meta = folder_path / "metadata.json"
    if not meta.exists():
        return {"status": "no_metadata"}

    try:
        with open(meta, "r", encoding="utf-8") as handle:
            head = handle.read(50_000)
        present = {feat: (f'"{feat}"' in head) for feat in FEATURES}
        all_present = all(present.values())
        any_present = any(present.values())

        if all_present:
            return {"status": "complete", "features": present}
        if any_present:
            return {"status": "partial", "features": present}
        return {"status": "none", "features": present}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def gpu_worker(folders: list, ready_queue: queue.Queue, stats: dict):
    """Process folders through ai.py."""
    ai_mod = load_ai_module()
    total = len(folders)
    stats["gpu_total"] = total

    for i, folder in enumerate(folders, 1):
        folder_path = ROOT_DIR / folder
        started = time.time()
        log.info(f"[GPU {i}/{total}] Processing {folder}...")

        try:
            ai_mod.process_folder(folder_path)
            elapsed = time.time() - started
            log.info(f"[GPU {i}/{total}] {folder} done in {elapsed:.1f}s")
            stats["gpu_ok"] += 1
        except Exception as exc:
            elapsed = time.time() - started
            log.error(f"[GPU {i}/{total}] {folder} FAILED in {elapsed:.1f}s: {exc}")
            stats["gpu_fail"] += 1

        ready_queue.put(folder)

    ready_queue.put(SENTINEL)
    log.info(
        f"[GPU] All {total} folders processed. OK={stats['gpu_ok']}, FAIL={stats['gpu_fail']}"
    )


def db_worker(ready_queue: queue.Queue, stats: dict, force: bool = False):
    """Ingest folders as they become ready."""
    from db.pool import get_connection, release_connection
    from pipeline.ingest import (
        check_and_migrate_schema,
        load_nice_classes,
        process_file_batch,
    )

    conn = get_connection()
    check_and_migrate_schema(conn)
    load_nice_classes(conn)

    ingested = 0
    while True:
        folder = ready_queue.get()
        if folder == SENTINEL:
            break

        meta_path = ROOT_DIR / folder / "metadata.json"
        if not meta_path.exists():
            log.warning(f"[DB] {folder}: no metadata.json, skipping")
            stats["db_skip"] += 1
            continue

        started = time.time()
        try:
            process_file_batch(conn, meta_path, force)
            elapsed = time.time() - started
            ingested += 1
            log.info(f"[DB] {folder} ingested in {elapsed:.1f}s (total: {ingested})")
            stats["db_ok"] += 1
        except Exception as exc:
            elapsed = time.time() - started
            log.error(f"[DB] {folder} FAILED in {elapsed:.1f}s: {exc}")
            stats["db_fail"] += 1
            try:
                release_connection(conn)
                conn = get_connection()
            except Exception:
                pass

    release_connection(conn)
    log.info(
        f"[DB] Ingestion complete. OK={stats['db_ok']}, FAIL={stats['db_fail']}, "
        f"SKIP={stats['db_skip']}"
    )


def main():
    parser = argparse.ArgumentParser(description="Parallel GPU + DB Pipeline")
    parser.add_argument("--skip-ai", action="store_true", help="Skip AI processing (ingest only)")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip ingestion (AI only)")
    parser.add_argument("--force", action="store_true", help="Force re-ingest existing records")
    parser.add_argument("--dry-run", action="store_true", help="Survey folders only")
    parser.add_argument("--folders", nargs="+", help="Process only these folders")
    args = parser.parse_args()

    if args.folders:
        all_folders = sorted(args.folders, key=folder_sort_key)
    else:
        all_folders = sorted(
            [
                d.name
                for d in ROOT_DIR.iterdir()
                if d.is_dir() and d.name != "LOGOS" and (d / "metadata.json").exists()
            ],
            key=folder_sort_key,
        )

    log.info(f"Pipeline starting: {len(all_folders)} folders in {ROOT_DIR}")

    if args.dry_run:
        complete = partial = none = errors = 0
        for folder in all_folders:
            result = survey_folder(ROOT_DIR / folder)
            if result["status"] == "complete":
                complete += 1
            elif result["status"] == "partial":
                partial += 1
            elif result["status"] == "none":
                none += 1
            else:
                errors += 1

        log.info(
            f"Survey: {len(all_folders)} folders - complete={complete}, "
            f"partial={partial}, none={none}, errors={errors}"
        )
        return

    stats = {
        "gpu_total": 0,
        "gpu_ok": 0,
        "gpu_fail": 0,
        "db_ok": 0,
        "db_fail": 0,
        "db_skip": 0,
    }
    started = time.time()

    ready_queue = queue.Queue(maxsize=10)

    if args.skip_ai:
        log.info("AI skipped - ingesting all folders directly")
        ingest_queue = queue.Queue()
        for folder in all_folders:
            ingest_queue.put(folder)
        ingest_queue.put(SENTINEL)
        db_worker(ingest_queue, stats, force=args.force)
    elif args.skip_ingest:
        log.info("Ingest skipped - running AI only")
        dummy_queue = queue.Queue()
        gpu_worker(all_folders, dummy_queue, stats)
    else:
        log.info("Starting parallel pipeline: GPU + DB threads")

        db_thread = threading.Thread(
            target=db_worker,
            args=(ready_queue, stats, args.force),
            name="DB-Worker",
            daemon=True,
        )
        gpu_thread = threading.Thread(
            target=gpu_worker,
            args=(all_folders, ready_queue, stats),
            name="GPU-Worker",
            daemon=True,
        )

        db_thread.start()
        gpu_thread.start()
        gpu_thread.join()
        db_thread.join()

    elapsed = time.time() - started
    log.info(f"Pipeline complete in {elapsed:.0f}s ({elapsed/60:.1f}min)")
    log.info(f"GPU: {stats['gpu_ok']}/{stats['gpu_total']} ok, {stats['gpu_fail']} failed")
    log.info(f"DB:  {stats['db_ok']} ingested, {stats['db_fail']} failed, {stats['db_skip']} skipped")


if __name__ == "__main__":
    main()

"""
pipeline_parallel.py — Parallel GPU + DB Pipeline

Thread 1 (GPU):  ai.py process_folder() on each folder → marks as ready
Thread 2 (DB):   ingest.py process_file_batch() on ready folders

Usage:
    python pipeline_parallel.py                    # process all folders
    python pipeline_parallel.py --skip-ai          # skip AI, ingest only
    python pipeline_parallel.py --skip-ingest      # AI only, no DB
    python pipeline_parallel.py --dry-run          # survey without processing
"""

import os
import sys
import time
import json
import queue
import logging
import argparse
import threading
import importlib.util
from pathlib import Path
from datetime import datetime

# ──────────────────────── Config ────────────────────────
ROOT_DIR = Path(os.getenv("DATA_ROOT", r"C:\Users\701693\turk_patent\bulletins\Marka"))
LOG_FMT = "%(asctime)s [%(name)-6s] %(levelname)-5s %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger("PIPE")

SENTINEL = "__DONE__"
FEATURES = [
    "image_embedding", "dinov2_embedding", "text_embedding",
    "color_histogram", "logo_ocr_text", "name_tr", "detected_lang",
]


# ──────────────────────── Helpers ────────────────────────
def load_ai_module():
    """Load ai.py via importlib to avoid conflict with ai/ package."""
    spec = importlib.util.spec_from_file_location(
        "ai_module", str(Path(__file__).parent / "ai.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _extract_folder_number(name: str) -> int:
    """Extract the numeric bulletin/gazette number from a folder name.
    E.g. 'GZ_499' -> 499, 'BLT_127' -> 127, 'GZ_449_2017-09-30' -> 449, 'APP_1' -> 1.
    """
    import re
    m = re.search(r'_(\d+)', name)
    return int(m.group(1)) if m else 0


def folder_sort_key(name: str) -> tuple:
    """Sort order: BLT_ first (lowest authority), then GZ_, then APP_ last (highest authority).
    Within each group, latest bulletin numbers come first (descending).
    This ensures higher-authority sources overwrite lower ones during upsert.
    """
    upper = name.upper()
    num = _extract_folder_number(name)
    if upper.startswith("BLT"): return (0, -num, name)
    if upper.startswith("GZ"):  return (1, -num, name)
    return (2, -num, name)  # APP


def survey_folder(folder_path: Path) -> dict:
    """Quick feature check on a folder's metadata.json (first 50KB)."""
    meta = folder_path / "metadata.json"
    if not meta.exists():
        return {"status": "no_metadata"}

    try:
        with open(meta, "r", encoding="utf-8") as f:
            head = f.read(50_000)
        present = {feat: (f'"{feat}"' in head) for feat in FEATURES}
        all_present = all(present.values())
        any_present = any(present.values())

        if all_present:
            return {"status": "complete", "features": present}
        elif any_present:
            return {"status": "partial", "features": present}
        else:
            return {"status": "none", "features": present}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ──────────────────────── GPU Thread ────────────────────────
def gpu_worker(folders: list, ready_queue: queue.Queue, stats: dict):
    """Process folders through ai.py (GPU-bound)."""
    ai_mod = load_ai_module()
    total = len(folders)
    stats["gpu_total"] = total

    for i, folder in enumerate(folders, 1):
        folder_path = ROOT_DIR / folder
        t0 = time.time()
        log.info(f"[GPU {i}/{total}] Processing {folder}...")

        try:
            ai_mod.process_folder(folder_path)
            elapsed = time.time() - t0
            log.info(f"[GPU {i}/{total}] {folder} done in {elapsed:.1f}s")
            stats["gpu_ok"] += 1
        except Exception as e:
            elapsed = time.time() - t0
            log.error(f"[GPU {i}/{total}] {folder} FAILED in {elapsed:.1f}s: {e}")
            stats["gpu_fail"] += 1

        # Signal DB thread that this folder is ready
        ready_queue.put(folder)

    # Signal completion
    ready_queue.put(SENTINEL)
    log.info(f"[GPU] All {total} folders processed. OK={stats['gpu_ok']}, FAIL={stats['gpu_fail']}")


# ──────────────────────── DB Thread ────────────────────────
def db_worker(ready_queue: queue.Queue, stats: dict, force: bool = False):
    """Ingest folders as they become ready (DB-bound)."""
    from db.pool import get_connection, release_connection
    from ingest import process_file_batch, check_and_migrate_schema, load_nice_classes

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

        t0 = time.time()
        try:
            process_file_batch(conn, meta_path, force)
            elapsed = time.time() - t0
            ingested += 1
            log.info(f"[DB] {folder} ingested in {elapsed:.1f}s (total: {ingested})")
            stats["db_ok"] += 1
        except Exception as e:
            elapsed = time.time() - t0
            log.error(f"[DB] {folder} FAILED in {elapsed:.1f}s: {e}")
            stats["db_fail"] += 1
            # Reconnect on failure
            try:
                release_connection(conn)
                conn = get_connection()
            except Exception:
                pass

    release_connection(conn)
    log.info(f"[DB] Ingestion complete. OK={stats['db_ok']}, FAIL={stats['db_fail']}, SKIP={stats['db_skip']}")


# ──────────────────────── Main ────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Parallel GPU + DB Pipeline")
    parser.add_argument("--skip-ai", action="store_true", help="Skip AI processing (ingest only)")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip ingestion (AI only)")
    parser.add_argument("--force", action="store_true", help="Force re-ingest existing records")
    parser.add_argument("--dry-run", action="store_true", help="Survey folders only")
    parser.add_argument("--folders", nargs="+", help="Process only these folders")
    args = parser.parse_args()

    # Discover folders
    if args.folders:
        all_folders = sorted(args.folders, key=folder_sort_key)
    else:
        all_folders = sorted(
            [d.name for d in ROOT_DIR.iterdir()
             if d.is_dir() and d.name != "LOGOS" and (d / "metadata.json").exists()],
            key=folder_sort_key
        )

    log.info(f"Pipeline starting: {len(all_folders)} folders in {ROOT_DIR}")

    # ── Dry run: survey only ──
    if args.dry_run:
        complete = partial = none = errors = 0
        for folder in all_folders:
            result = survey_folder(ROOT_DIR / folder)
            if result["status"] == "complete": complete += 1
            elif result["status"] == "partial": partial += 1
            elif result["status"] == "none": none += 1
            else: errors += 1

        log.info(f"Survey: {len(all_folders)} folders — "
                 f"complete={complete}, partial={partial}, none={none}, errors={errors}")
        return

    # ── Stats ──
    stats = {
        "gpu_total": 0, "gpu_ok": 0, "gpu_fail": 0,
        "db_ok": 0, "db_fail": 0, "db_skip": 0,
    }
    t_start = time.time()

    ready_queue = queue.Queue(maxsize=10)  # backpressure: GPU waits if DB is 10 behind

    # ── Skip AI: directly feed all folders to DB ──
    if args.skip_ai:
        log.info("AI skipped — ingesting all folders directly")
        # Use unlimited queue to avoid deadlock (maxsize=10 would block put())
        ingest_queue = queue.Queue()
        for f in all_folders:
            ingest_queue.put(f)
        ingest_queue.put(SENTINEL)

        db_worker(ingest_queue, stats, force=args.force)

    # ── Skip ingest: run AI only ──
    elif args.skip_ingest:
        log.info("Ingest skipped — running AI only")
        dummy_queue = queue.Queue()
        gpu_worker(all_folders, dummy_queue, stats)

    # ── Full parallel: GPU + DB threads ──
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

        # Wait for GPU to finish (it sends SENTINEL to DB)
        gpu_thread.join()
        # Wait for DB to drain the queue
        db_thread.join()

    elapsed = time.time() - t_start
    log.info(f"Pipeline complete in {elapsed:.0f}s ({elapsed/60:.1f}min)")
    log.info(f"GPU: {stats['gpu_ok']}/{stats['gpu_total']} ok, {stats['gpu_fail']} failed")
    log.info(f"DB:  {stats['db_ok']} ingested, {stats['db_fail']} failed, {stats['db_skip']} skipped")


if __name__ == "__main__":
    main()

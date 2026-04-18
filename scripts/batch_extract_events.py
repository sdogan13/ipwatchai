"""
Batch extract events from all BLT/GZ PDFs that don't yet have events.json.

Usage:
    python scripts/batch_extract_events.py              # all missing
    python scripts/batch_extract_events.py --source GZ  # GZ only
    python scripts/batch_extract_events.py --force       # re-extract even if events.json exists
"""
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pdf_extract_events import extract_events_from_pdf, extract_events_from_folder, _detect_source_type, _parse_folder_info

# Force UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)

BULLETINS_ROOT = PROJECT_ROOT / "bulletins" / "Marka"


def has_any_pdf(folder: Path) -> bool:
    """Check if a bulletin folder has at least one non-empty PDF."""
    return any(p.stat().st_size > 0 for p in folder.glob("*.pdf"))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Batch extract events from bulletin PDFs")
    parser.add_argument("--source", choices=["GZ", "BLT", "all"], default="all")
    parser.add_argument("--force", action="store_true", help="Re-extract even if events.json exists")
    args = parser.parse_args()

    folders = sorted(BULLETINS_ROOT.iterdir())
    to_process = []

    for folder in folders:
        if not folder.is_dir():
            continue
        name = folder.name
        if args.source == "GZ" and not name.startswith("GZ_"):
            continue
        if args.source == "BLT" and not name.startswith("BLT_"):
            continue
        if not name.startswith(("GZ_", "BLT_")):
            continue

        # Check if any PDF exists in the folder
        if not has_any_pdf(folder):
            continue

        # Check if events.json already exists
        if not args.force and (folder / "events.json").exists():
            continue

        to_process.append(folder)

    logger.info(f"Found {len(to_process)} folders to process")

    stats = {"processed": 0, "failed": 0, "total_events": 0}
    start_time = time.time()

    for i, folder in enumerate(to_process):
        folder_name = folder.name
        source_type = _detect_source_type(folder_name)
        _, bulletin_no, bulletin_date = _parse_folder_info(folder_name)

        if not bulletin_no or not source_type:
            logger.warning(f"[{i+1}/{len(to_process)}] Skip {folder_name}: can't detect source/number")
            continue

        pdf_count = len(list(folder.glob("*.pdf")))
        logger.info(f"[{i+1}/{len(to_process)}] {folder_name} ({source_type} {bulletin_no}, {pdf_count} PDFs)...")

        try:
            result = extract_events_from_folder(folder, source_type, bulletin_no, bulletin_date)
            total = result.get("total", 0)
            stats["total_events"] += total

            # Save events.json
            out_path = folder / "events.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2, default=str)

            # Log stats
            type_counts = result.get("stats", {})
            top_types = sorted(type_counts.items(), key=lambda x: -x[1])[:3]
            top_str = ", ".join(f"{t}={c}" for t, c in top_types)
            logger.info(f"  -> {total} events ({top_str})")
            stats["processed"] += 1

        except Exception as e:
            logger.error(f"  FAILED: {e}")
            stats["failed"] += 1

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info(f"DONE in {elapsed:.0f}s: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    main()

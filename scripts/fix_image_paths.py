"""
Fix image_path values in trademarks table.

Replaces legacy 'bulletins/Marka/LOGOS/...' paths with actual per-folder paths.
Prioritizes GZ folders over BLT when the same image exists in both.

Usage:
    python scripts/fix_image_paths.py           # fix all LOGOS paths
    python scripts/fix_image_paths.py --dry-run # preview without changes
"""
import os
import sys
import glob
import logging
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2.extras
from dotenv import load_dotenv
load_dotenv()
from db.pool import connection_context

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger(__name__)

MARKA_ROOT = PROJECT_ROOT / "bulletins" / "Marka"


def build_image_index():
    """Build filename->path index, prioritizing GZ over BLT sources."""
    index = {}  # basename_no_ext -> relative path

    # First pass: BLT folders (lower priority)
    for images_dir in sorted(glob.glob(str(MARKA_ROOT / "BLT_*" / "images"))):
        folder = os.path.basename(os.path.dirname(images_dir))
        for f in os.listdir(images_dir):
            basename = os.path.splitext(f)[0]
            index[basename] = f"bulletins/Marka/{folder}/images/{f}"

    blt_count = len(index)

    # Second pass: GZ folders (higher priority — overwrites BLT)
    for images_dir in sorted(glob.glob(str(MARKA_ROOT / "GZ_*" / "images"))):
        folder = os.path.basename(os.path.dirname(images_dir))
        for f in os.listdir(images_dir):
            basename = os.path.splitext(f)[0]
            index[basename] = f"bulletins/Marka/{folder}/images/{f}"

    # Third pass: APP folders (highest priority)
    for images_dir in sorted(glob.glob(str(MARKA_ROOT / "APP_*" / "images"))):
        folder = os.path.basename(os.path.dirname(images_dir))
        for f in os.listdir(images_dir):
            basename = os.path.splitext(f)[0]
            index[basename] = f"bulletins/Marka/{folder}/images/{f}"

    logger.info(f"Image index: {len(index)} images ({blt_count} from BLT, {len(index) - blt_count} overwritten by GZ/APP)")
    return index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    index = build_image_index()

    with connection_context() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, image_path FROM trademarks WHERE image_path LIKE '%LOGOS%'")
        rows = cur.fetchall()
        logger.info(f"Records to fix: {len(rows)}")

        fixed = 0
        not_found = 0
        batch = []

        for row in rows:
            tm_id = str(row[0])
            old_path = row[1]
            basename = os.path.splitext(os.path.basename(old_path))[0]
            new_path = index.get(basename)

            if new_path:
                batch.append((new_path, tm_id))
                fixed += 1
            else:
                not_found += 1

            if len(batch) >= 5000:
                if not args.dry_run:
                    psycopg2.extras.execute_batch(
                        cur,
                        "UPDATE trademarks SET image_path = %s WHERE id = %s::uuid",
                        batch, page_size=1000,
                    )
                    conn.commit()
                batch = []
                if fixed % 50000 == 0:
                    logger.info(f"  Progress: {fixed} fixed...")

        if batch and not args.dry_run:
            psycopg2.extras.execute_batch(
                cur,
                "UPDATE trademarks SET image_path = %s WHERE id = %s::uuid",
                batch, page_size=1000,
            )
            conn.commit()

        logger.info(f"Done. Fixed: {fixed}, Not found (no image on disk): {not_found}")


if __name__ == "__main__":
    main()

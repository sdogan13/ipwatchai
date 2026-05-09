"""One-shot folder-hygiene fix for Tasarım issue folders with drifting dates.

The PDF collector (``data_collection_tasarim.py``) writes each bulletin
into ``bulletins/Tasarim/TS_{issue_no}_{date}/``. When the collector
runs ``--full`` against the live page it sometimes stamps the folder
with the *run* date instead of the issue's actual publication date —
the symptom seen in the 2026-05-09 pairing survey was 17 PDFs landing
in folders dated ``2026-04-24`` (today) when the corresponding CD
archives' ``idbulletin.inf`` declared their real publication dates
in 2016.

This script reads each ``bulletins/Tasarim/*_CD.rar`` archive's small
``idbulletin.inf`` file (extracted via 7-Zip without unpacking the
rest), looks up the matching ``TS_{N}_*/`` folder, and renames it to
``TS_{N}_{inf_DATE}/`` when:

  - a TS_{N}_*/ folder exists for that bulletin number
  - that folder's date suffix differs from the inf DATE
  - the folder contains a real ``bulletin.pdf`` (not just a stub)
  - the target ``TS_{N}_{inf_DATE}/`` folder doesn't already exist
    (would conflict — caller resolves manually)

Default mode is dry-run. Pass ``--apply`` to perform the renames.

Usage::

    python scripts/fix_tasarim_folder_dates.py            # dry-run, prints plan
    python scripts/fix_tasarim_folder_dates.py --apply    # actually rename

Idempotent: re-running after --apply is a clean no-op (the drift is
gone, nothing to do).
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cd_extract_tasarim import _all_cd_rars, _resolve_seven_zip  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [TASARIM-FIX-DATES] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.tasarim_fix_dates")


def extract_inf_no_and_date(archive: Path, seven_zip: Path) -> Tuple[Optional[str], Optional[str]]:
    """Extract ``idbulletin.inf`` from an archive and parse NO + DATE.

    Returns ``(inf_no, inf_date_iso)``. Either may be ``None`` if the
    archive is corrupt or missing the file. Inf DATE is converted from
    ``DD.MM.YYYY`` to ``YYYY-MM-DD``.
    """
    list_proc = subprocess.run(
        [str(seven_zip), "l", str(archive)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if list_proc.returncode not in (0, 1):
        return None, None

    paths: List[str] = []
    for line in list_proc.stdout.splitlines():
        m = re.search(r"(\S*idbulletin\.inf)\s*$", line)
        if m:
            paths.append(m.group(1).replace("\\", "/"))
    if not paths:
        return None, None

    shallowest = min(paths, key=lambda p: len(p.split("/")))
    extract_proc = subprocess.run(
        [str(seven_zip), "x", str(archive), shallowest, "-so", "-y"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if extract_proc.returncode not in (0, 1):
        return None, None
    content = extract_proc.stdout

    no_m = re.search(r"NO\s*=\s*(\S+)", content)
    date_m = re.search(r"DATE\s*=\s*(\S+)", content)
    inf_no = no_m.group(1) if no_m else None

    inf_date_iso: Optional[str] = None
    if date_m:
        m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", date_m.group(1))
        if m:
            inf_date_iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return inf_no, inf_date_iso


def plan_renames(bulletins_root: Path, seven_zip: Path) -> List[Tuple[Path, Path, str]]:
    """Walk every CD archive and decide which folders to rename.

    Returns a list of ``(src_folder, dst_folder, reason)``. ``reason`` is
    a short string explaining why the rename is being scheduled (used
    for the dry-run report).

    Skips:
      - archives that can't yield NO + DATE (corrupt / no inf)
      - bulletins with no existing TS_{N}_*/ folder
      - bulletins with multiple TS_{N}_*/ folders (real ambiguity)
      - folders whose date suffix already matches the inf DATE
      - folders that don't contain a real bulletin.pdf (no PDF -> nothing
        to preserve, the new TS_{N}_{inf_DATE}/ from cd_extract is fine)
      - cases where the target TS_{N}_{inf_DATE}/ already exists (would
        clobber it; surface as a manual-resolve case)
    """
    rars = _all_cd_rars(bulletins_root)
    plan: List[Tuple[Path, Path, str]] = []

    for rar in rars:
        inf_no, inf_date = extract_inf_no_and_date(rar, seven_zip)
        if not inf_no or not inf_date:
            logger.warning("[skip] %s: corrupt or no inf", rar.name)
            continue

        matches = sorted(p for p in bulletins_root.glob(f"TS_{inf_no}_*") if p.is_dir())
        if not matches:
            continue
        if len(matches) > 1:
            logger.warning(
                "[skip] %s -> bulletin %s: %d existing folders (manual resolve): %s",
                rar.name, inf_no, len(matches), [p.name for p in matches],
            )
            continue

        existing = matches[0]
        existing_date = existing.name.split("_", 2)[-1]
        if existing_date == inf_date:
            continue  # already correct, nothing to do

        if not (existing / "bulletin.pdf").is_file():
            # No PDF inside — folder is just an empty stub from a broken
            # download. Future cd_extract will write into it via P.1's
            # _find_existing_issue_folder, so renaming gains nothing.
            continue

        target = bulletins_root / f"TS_{inf_no}_{inf_date}"
        if target.exists():
            logger.warning(
                "[conflict] %s -> %s already exists; manual resolve",
                existing.name, target.name,
            )
            continue

        plan.append((existing, target, f"inf says {inf_date}, folder is {existing_date}"))

    return plan


def apply_plan(plan: List[Tuple[Path, Path, str]]) -> int:
    """Rename folders. Returns count of successful renames."""
    done = 0
    for src, dst, _reason in plan:
        try:
            src.rename(dst)
            logger.info("[+] renamed %s -> %s", src.name, dst.name)
            done += 1
        except OSError as e:
            logger.error("[!] rename failed %s -> %s: %r", src.name, dst.name, e)
    return done


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fix_tasarim_folder_dates",
        description="Rename Tasarim TS_{N}_*/ folders to match inf DATE.",
    )
    parser.add_argument(
        "--bulletins-root",
        type=Path,
        default=PROJECT_ROOT / "bulletins" / "Tasarim",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the renames (default: dry-run, just print plan).",
    )
    args = parser.parse_args(argv)

    seven = _resolve_seven_zip()
    if not seven.is_file():
        logger.error("7-Zip not found at %s", seven)
        return 1

    if not args.bulletins_root.is_dir():
        logger.error("bulletins-root not found: %s", args.bulletins_root)
        return 1

    logger.info("scanning %s", args.bulletins_root)
    plan = plan_renames(args.bulletins_root, seven)

    if not plan:
        logger.info("nothing to do — no drifting TS_{N}_*/ folders detected")
        return 0

    print()
    print(f"== {len(plan)} folder rename(s) planned ==")
    for src, dst, reason in plan:
        print(f"  {src.name:<28} ->  {dst.name}    ({reason})")
    print()

    if not args.apply:
        print("dry-run only. Re-run with --apply to perform the renames.")
        return 0

    done = apply_plan(plan)
    print(f"\n{done}/{len(plan)} renames complete")
    return 0 if done == len(plan) else 1


if __name__ == "__main__":
    raise SystemExit(main())

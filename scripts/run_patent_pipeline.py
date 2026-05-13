"""Full Patent / Faydalı Model pipeline runner — chains all seven stages.

Stages and their underlying scripts (idempotent by default; each stage's
own ``--force`` controls re-running):

  1. collect    data_collection_patent.py
  2. cd         cd_extract_patent.py        --all
  3. pdf        pdf_extract_patent.py       --all
  4. events     pdf_extract_patent_events.py --all
  5. reconcile  python -m pipeline.reconcile_patent --all
  6. embed      embeddings_patent.py        --all
  7. ingest     python -m pipeline.ingest_patents --all

Default behavior is fully idempotent: every stage skips work that is
already on disk / in the DB. Pass ``--force`` to re-run every stage
end-to-end, ignoring on-disk and in-DB freshness.

Examples
--------

Idempotent run (skip already-processed bulletins)::

    python scripts/run_patent_pipeline.py

Force re-run all 7 stages::

    python scripts/run_patent_pipeline.py --force

Skip the network-heavy collector and only process bulletins already
present on disk::

    python scripts/run_patent_pipeline.py --stages 2,3,4,5,6,7

Force-re-run a single stage::

    python scripts/run_patent_pipeline.py --stages 6 --force
"""
from __future__ import annotations

import argparse
import logging
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BULLETINS_DIR = _PROJECT_ROOT / "bulletins" / "Patent__Faydali_Model"

logger = logging.getLogger("run_patent_pipeline")

STAGE_ORDER = (1, 2, 3, 4, 5, 6, 7)

STAGE_NAMES = {
    1: "collect",
    2: "cd",
    3: "pdf",
    4: "events",
    5: "reconcile",
    6: "embed",
    7: "ingest",
}


def _build_stage_command(
    stage: int,
    *,
    bulletins_dir: Path,
    force: bool,
) -> List[str]:
    """Return the argv for a stage's underlying CLI."""
    py = sys.executable
    if stage == 1:
        # The collector ignores --bulletins-dir naming: it uses its own
        # --bulletins-root flag and walks the TÜRKPATENT site for every
        # available card.
        cmd = [py, "data_collection_patent.py",
               "--bulletins-root", str(bulletins_dir)]
        if force:
            cmd.append("--force")
        return cmd
    if stage == 2:
        cmd = [py, "cd_extract_patent.py", "--all",
               "--bulletins-dir", str(bulletins_dir)]
        if force:
            cmd.append("--force")
        return cmd
    if stage == 3:
        cmd = [py, "pdf_extract_patent.py", "--all",
               "--bulletins-dir", str(bulletins_dir)]
        if force:
            cmd.append("--force")
        return cmd
    if stage == 4:
        cmd = [py, "pdf_extract_patent_events.py", "--all",
               "--bulletins-dir", str(bulletins_dir)]
        if force:
            cmd.append("--force")
        return cmd
    if stage == 5:
        cmd = [py, "-m", "pipeline.reconcile_patent", "--all",
               "--bulletins-dir", str(bulletins_dir)]
        if force:
            cmd.append("--force")
        return cmd
    if stage == 6:
        cmd = [py, "embeddings_patent.py", "--all",
               "--bulletins-dir", str(bulletins_dir)]
        if force:
            cmd.append("--force")
        return cmd
    if stage == 7:
        cmd = [py, "-m", "pipeline.ingest_patents", "--all",
               "--bulletins-dir", str(bulletins_dir)]
        if force:
            cmd.append("--force")
        return cmd
    raise ValueError(f"unknown stage {stage}")


def _parse_stages(raw: str) -> List[int]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    stages: List[int] = []
    for p in parts:
        try:
            n = int(p)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"--stages expects integers, got {p!r}"
            ) from exc
        if n not in STAGE_ORDER:
            raise argparse.ArgumentTypeError(
                f"--stages value {n} out of range; valid: {list(STAGE_ORDER)}"
            )
        stages.append(n)
    if not stages:
        raise argparse.ArgumentTypeError("--stages cannot be empty")
    # Preserve user-supplied order but dedupe.
    seen: set = set()
    out: List[int] = []
    for s in stages:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def parse_argv(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_patent_pipeline",
        description=(
            "Run every Patent pipeline stage in sequence. Idempotent by "
            "default; --force forwards a re-run override to every stage."
        ),
    )
    parser.add_argument(
        "--bulletins-dir", type=Path, default=_DEFAULT_BULLETINS_DIR,
        help=f"Bulletins root (default: {_DEFAULT_BULLETINS_DIR}).",
    )
    parser.add_argument(
        "--stages", type=_parse_stages, default=list(STAGE_ORDER),
        help=("Comma-separated stage numbers to run, in order. "
              f"Default: all of {list(STAGE_ORDER)}. "
              "Stage map: " + ", ".join(f"{n}={STAGE_NAMES[n]}" for n in STAGE_ORDER) + "."),
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Forward --force to every selected stage (ignore on-disk / "
             "in-DB freshness and re-run end-to-end).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the per-stage commands but do not execute them.",
    )
    parser.add_argument(
        "--stop-on-error", action="store_true",
        help="Abort the pipeline on the first non-zero exit code "
             "(default: continue with the next stage).",
    )
    return parser.parse_args(argv)


def _run_stage(stage: int, cmd: List[str], *, dry_run: bool) -> int:
    name = STAGE_NAMES[stage]
    printable = " ".join(shlex.quote(p) for p in cmd)
    logger.info("[stage %d / %s] %s", stage, name, printable)
    if dry_run:
        return 0
    t0 = time.time()
    completed = subprocess.run(cmd, cwd=str(_PROJECT_ROOT))
    dt = time.time() - t0
    if completed.returncode == 0:
        logger.info("[stage %d / %s] ok (%.1fs)", stage, name, dt)
    else:
        logger.error("[stage %d / %s] exit=%d (%.1fs)",
                     stage, name, completed.returncode, dt)
    return completed.returncode


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        level=logging.INFO,
    )
    args = parse_argv(argv)

    if not args.bulletins_dir.exists():
        # Stage 1 will create it; later stages need it to exist for
        # --all to match anything. Log but don't bail — the collector
        # itself will mkdir.
        logger.warning("bulletins-dir does not exist yet: %s",
                       args.bulletins_dir)

    failures: List[int] = []
    for stage in args.stages:
        cmd = _build_stage_command(
            stage,
            bulletins_dir=args.bulletins_dir,
            force=args.force,
        )
        rc = _run_stage(stage, cmd, dry_run=args.dry_run)
        if rc != 0:
            failures.append(stage)
            if args.stop_on_error:
                logger.error("[abort] stage %d failed; --stop-on-error set",
                             stage)
                return rc

    if failures:
        logger.error("pipeline finished with failures in stages: %s",
                     failures)
        return 1
    logger.info("pipeline finished cleanly (%d stages, force=%s)",
                len(args.stages), args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())

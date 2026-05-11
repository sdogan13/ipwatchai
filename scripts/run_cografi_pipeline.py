"""Full Coğrafi İşaret ve Geleneksel Ürün Adı pipeline runner — chains all four stages.

Stages and their underlying scripts (idempotent by default; each stage's
own ``--force`` controls re-running where supported):

  1. collect    data_collection_cografi.py
  2. extract    pdf_extract_cografi.py     --all
  3. embed      embeddings_cografi.py      --all
  4. ingest     python -m pipeline.ingest_cografi --all

Cografi has no separate CD / events / reconcile stages — the extractor
emits events (art42 changes + corrections) inline as part of B2, the
single PDF is the only source, and ingest is naturally idempotent via
UPSERT (no ``--force`` to forward).

Default behavior is fully idempotent: every stage skips work that is
already on disk / in the DB. Pass ``--force`` to re-run stages 1-3 end
to end (ingest is silently re-run unconditionally because UPSERT is
the natural reset).

Examples
--------

Idempotent run (skip already-processed bulletins)::

    python scripts/run_cografi_pipeline.py

Force re-run all 4 stages::

    python scripts/run_cografi_pipeline.py --force

Skip the network-heavy collector and only process bulletins already
present on disk::

    python scripts/run_cografi_pipeline.py --stages 2,3,4

Force-re-run a single stage::

    python scripts/run_cografi_pipeline.py --stages 3 --force
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
_DEFAULT_BULLETINS_DIR = (
    _PROJECT_ROOT / "bulletins" / "Cografi_Isaret_ve_Geleneksel_Urun_Adi"
)

logger = logging.getLogger("run_cografi_pipeline")

STAGE_ORDER = (1, 2, 3, 4)

STAGE_NAMES = {
    1: "collect",
    2: "extract",
    3: "embed",
    4: "ingest",
}

# Stages whose underlying CLI accepts --force. Ingest is idempotent via
# UPSERT — there is no --force flag to forward.
_FORCE_CAPABLE_STAGES = frozenset({1, 2, 3})


def _build_stage_command(
    stage: int,
    *,
    bulletins_dir: Path,
    force: bool,
) -> List[str]:
    """Return the argv for a stage's underlying CLI."""
    py = sys.executable
    if stage == 1:
        cmd = [py, "data_collection_cografi.py",
               "--bulletins-root", str(bulletins_dir)]
        if force:
            cmd.append("--force")
        return cmd
    if stage == 2:
        cmd = [py, "pdf_extract_cografi.py", "--all",
               "--bulletins-root", str(bulletins_dir)]
        if force:
            cmd.append("--force")
        return cmd
    if stage == 3:
        cmd = [py, "embeddings_cografi.py", "--all",
               "--bulletins-root", str(bulletins_dir)]
        if force:
            cmd.append("--force")
        return cmd
    if stage == 4:
        # ingest_cografi has no --force; UPSERT is naturally idempotent.
        return [py, "-m", "pipeline.ingest_cografi", "--all",
                "--bulletins-root", str(bulletins_dir)]
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
    seen: set = set()
    out: List[int] = []
    for s in stages:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def parse_argv(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_cografi_pipeline",
        description=(
            "Run every Cografi pipeline stage in sequence. Idempotent by "
            "default; --force forwards a re-run override to stages 1-3 "
            "(ingest is always idempotent via UPSERT)."
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
        help="Forward --force to stages 1-3 (ingest has no --force flag; "
             "UPSERT-based ingest is always idempotent).",
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
        logger.warning("bulletins-dir does not exist yet: %s",
                       args.bulletins_dir)

    if args.force and any(s not in _FORCE_CAPABLE_STAGES for s in args.stages):
        ignored = [STAGE_NAMES[s] for s in args.stages
                   if s not in _FORCE_CAPABLE_STAGES]
        logger.info("--force does not apply to: %s (UPSERT-idempotent)",
                    ", ".join(ignored))

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

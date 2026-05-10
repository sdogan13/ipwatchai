"""Tasarım end-to-end pipeline orchestrator.

Runs the six pipeline stages in sequence:

    1. data_collection_tasarim       — download bulletin .pdf files
    2. cd_extract_tasarim --all      — parse CD .rar archives -> cd_metadata.json
    3. pdf_extract_tasarim           — parse bulletin.pdf      -> metadata.json + images/
    4. pipeline.merge_into_metadata  — CD-wins merge into metadata.json
    5. embeddings_tasarim            — DINOv2 + CLIP + HSV per-view embeddings
    6. pipeline.ingest_designs       — UPSERT into PostgreSQL

Each stage already has its own folder-level idempotency check, so a
re-run on a clean state is a near-zero-cost no-op.

CLI::

    python scripts/run_pipeline_tasarim.py                 # all stages, all bulletins
    python scripts/run_pipeline_tasarim.py --issue 246     # scope to one bulletin
    python scripts/run_pipeline_tasarim.py --force         # re-run every stage forcefully
    python scripts/run_pipeline_tasarim.py --skip-stage 1  # skip stages by index (repeatable)
    python scripts/run_pipeline_tasarim.py --only-stage 6  # run only stages 6 (repeatable)

``--issue NNN`` takes a bare bulletin number. Stages that expect a folder
name receive the orchestrator's resolved ``TS_NNN_YYYY-MM-DD`` form.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BULLETINS_DIR = PROJECT_ROOT / "bulletins" / "Tasarim"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [TASARIM-PIPELINE] - %(levelname)s - %(message)s",
)
logger = logging.getLogger("turkpatent.tasarim_pipeline")


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stage:
    index: int
    name: str
    description: str


STAGES: Sequence[Stage] = (
    Stage(1, "collect",     "data_collection_tasarim — download PDFs"),
    Stage(2, "cd_extract",  "cd_extract_tasarim — parse CD bundles"),
    Stage(3, "pdf_extract", "pdf_extract_tasarim — parse PDFs + extract images"),
    Stage(4, "merge",       "pipeline.merge_into_metadata — CD-wins merge"),
    Stage(5, "embed",       "embeddings_tasarim — per-view embeddings"),
    Stage(6, "ingest",      "pipeline.ingest_designs — DB UPSERT"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_issue_folder(bulletins_root: Path, issue_no: str) -> Optional[str]:
    """Find the canonical TS_{issue_no}_YYYY-MM-DD/ folder name on disk.

    Returns the folder *name* (not full path) if exactly one match;
    ``None`` if no folder exists yet (stage 1 hasn't downloaded it).
    Raises ``ValueError`` if multiple folders match — that's a state
    inconsistency the user should resolve before re-running.
    """
    matches = sorted(p for p in bulletins_root.glob(f"TS_{issue_no}_*") if p.is_dir())
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(
            f"Multiple TS_{issue_no}_* folders found in {bulletins_root}: "
            f"{[p.name for p in matches]}. Resolve before re-running."
        )
    return matches[0].name


def _build_stage_command(
    stage_name: str,
    *,
    bulletins_root: Path,
    issue_no: Optional[str],
    issue_folder_name: Optional[str],
    force: bool,
) -> List[str]:
    """Build the argv for a single stage's CLI invocation.

    ``issue_no`` is the bare bulletin number (e.g. ``"246"``) used by
    stage 1. ``issue_folder_name`` is the resolved ``TS_NNN_YYYY-MM-DD``
    used by stages 2b–6 that operate on folders.

    cd_extract (stage 2a) takes neither — it always runs in ``--all``
    mode and relies on its own per-file skip logic (which also honors
    ``--force``).
    """
    python = sys.executable
    root_args = ["--bulletins-root", str(bulletins_root)]

    if stage_name == "collect":
        cmd = [python, str(PROJECT_ROOT / "data_collection_tasarim.py")] + root_args
        if issue_no:
            cmd += ["--issue", issue_no]
        if force:
            cmd += ["--force"]
        return cmd

    if stage_name == "cd_extract":
        cmd = [python, str(PROJECT_ROOT / "cd_extract_tasarim.py"),
               "--all", "--bulletins-dir", str(bulletins_root)]
        if force:
            cmd += ["--force"]
        return cmd

    if stage_name == "pdf_extract":
        cmd = [python, str(PROJECT_ROOT / "pdf_extract_tasarim.py")] + root_args
        if issue_folder_name:
            cmd += ["--issue", issue_folder_name]
        if force:
            cmd += ["--force"]
        return cmd

    if stage_name == "merge":
        cmd = [python, "-m", "pipeline.merge_into_metadata"] + root_args
        if issue_folder_name:
            cmd += ["--issue", issue_folder_name]
        else:
            cmd += ["--all"]
        if force:
            cmd += ["--force"]
        return cmd

    if stage_name == "embed":
        cmd = [python, str(PROJECT_ROOT / "embeddings_tasarim.py")] + root_args
        if issue_folder_name:
            cmd += ["--issue", issue_folder_name]
        if force:
            cmd += ["--force"]
        return cmd

    if stage_name == "ingest":
        cmd = [python, "-m", "pipeline.ingest_designs"] + root_args
        if issue_folder_name:
            cmd += ["--issue", issue_folder_name]
        if force:
            cmd += ["--force"]
        return cmd

    raise ValueError(f"unknown stage: {stage_name!r}")


def _selected_stages(args: argparse.Namespace) -> List[Stage]:
    """Filter STAGES by --only-stage / --skip-stage flags."""
    if args.only_stage:
        wanted = set(args.only_stage)
        return [s for s in STAGES if s.index in wanted]
    skipped = set(args.skip_stage or ())
    return [s for s in STAGES if s.index not in skipped]


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def parse_argv(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_pipeline_tasarim",
        description="Run the Tasarım end-to-end pipeline (stages 1–6).",
    )
    p.add_argument("--issue", type=str, default=None,
                   help="Scope the run to one bulletin (bare number, "
                        "e.g. --issue 246). Default: all bulletins.")
    p.add_argument("--bulletins-root", type=Path, default=DEFAULT_BULLETINS_DIR,
                   help=f"Bulletins root (default: {DEFAULT_BULLETINS_DIR}).")
    p.add_argument("--force", action="store_true",
                   help="Pass --force to every stage so each stage re-runs "
                        "regardless of its own up-to-date check.")
    p.add_argument("--skip-stage", type=int, action="append",
                   help="Stage index to skip (1-6). Repeatable.")
    p.add_argument("--only-stage", type=int, action="append",
                   help="Stage index to run, all others skipped. Repeatable. "
                        "Mutually exclusive with --skip-stage.")
    p.add_argument("--continue-on-error", action="store_true",
                   help="Run subsequent stages even if an earlier stage "
                        "exits non-zero (default: stop on first failure).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the commands that would run; don't execute.")
    ns = p.parse_args(argv)

    if ns.only_stage and ns.skip_stage:
        p.error("--only-stage and --skip-stage are mutually exclusive")

    return ns


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_argv(argv)

    if not args.bulletins_root.is_dir():
        logger.error("bulletins root not found: %s", args.bulletins_root)
        return 1

    issue_folder_name: Optional[str] = None
    if args.issue:
        try:
            issue_folder_name = _resolve_issue_folder(args.bulletins_root, args.issue)
        except ValueError as e:
            logger.error(str(e))
            return 1
        if issue_folder_name is None:
            logger.info(
                "[i] no TS_%s_* folder on disk yet — stages 2b-6 will be "
                "skipped until stage 1 downloads it.", args.issue,
            )

    stages = _selected_stages(args)
    if not stages:
        logger.warning("no stages selected — nothing to do")
        return 0

    logger.info(
        "Pipeline run: %d stage(s) selected, issue=%s, force=%s",
        len(stages), args.issue or "all", args.force,
    )

    overall_start = time.time()
    results: List[tuple] = []   # (stage_name, rc, duration)
    for stage in stages:
        # Stages 2b–6 need an existing TS folder when --issue is scoped.
        if args.issue and stage.name in {"pdf_extract", "merge", "embed", "ingest"}:
            if issue_folder_name is None:
                logger.warning(
                    "[~] stage %d (%s): TS_%s_* folder not present, skipping",
                    stage.index, stage.name, args.issue,
                )
                results.append((stage.name, None, 0.0))
                continue

        cmd = _build_stage_command(
            stage.name,
            bulletins_root=args.bulletins_root,
            issue_no=args.issue,
            issue_folder_name=issue_folder_name,
            force=args.force,
        )

        logger.info("[*] stage %d (%s): %s", stage.index, stage.name, " ".join(cmd))

        if args.dry_run:
            results.append((stage.name, 0, 0.0))
            continue

        stage_start = time.time()
        try:
            proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=False)
            rc = proc.returncode
        except Exception as e:
            logger.exception("[!] stage %d (%s) crashed: %r", stage.index, stage.name, e)
            rc = 1
        duration = time.time() - stage_start
        results.append((stage.name, rc, duration))

        if rc == 0:
            logger.info("[+] stage %d (%s) done in %.1fs", stage.index, stage.name, duration)
        else:
            logger.error("[!] stage %d (%s) exit=%d after %.1fs",
                          stage.index, stage.name, rc, duration)
            if not args.continue_on_error:
                logger.error("aborting pipeline run — pass --continue-on-error to keep going")
                _print_summary(results, time.time() - overall_start)
                return rc

    return _print_summary(results, time.time() - overall_start)


def _print_summary(results: List[tuple], total_duration: float) -> int:
    """Log a per-stage summary table; return 0 iff every stage succeeded."""
    logger.info("=" * 60)
    logger.info("Pipeline summary (%.1fs total):", total_duration)
    any_failed = False
    for name, rc, dur in results:
        if rc is None:
            status = "SKIPPED"
        elif rc == 0:
            status = "OK"
        else:
            status = f"FAIL(rc={rc})"
            any_failed = True
        logger.info("  %-12s %-12s %.1fs", name, status, dur)
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

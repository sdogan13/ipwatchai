"""Helpers for launching detached pipeline worker processes."""

import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_pipeline_worker_command(
    *,
    triggered_by: str,
    skip_download: bool,
    single_step: Optional[str] = None,
    run_id: Optional[str] = None,
) -> list[str]:
    """Build the worker CLI command for a pipeline run."""
    command = [
        sys.executable,
        "-m",
        "workers.pipeline_worker",
        "--triggered-by",
        triggered_by,
    ]
    if run_id:
        command.extend(["--run-id", run_id])
    if skip_download:
        command.append("--skip-download")
    if single_step:
        command.extend(["--step", single_step])
    return command


def launch_pipeline_process(
    *,
    triggered_by: str,
    skip_download: bool,
    single_step: Optional[str] = None,
    run_id: Optional[str] = None,
    process_runner=subprocess.Popen,
    service_logger=None,
    working_directory: Path | None = None,
    env: dict | None = None,
):
    """Launch the pipeline worker as a detached OS process."""
    pipeline_logger = service_logger or logger
    command = build_pipeline_worker_command(
        triggered_by=triggered_by,
        skip_download=skip_download,
        single_step=single_step,
        run_id=run_id,
    )
    popen_kwargs = {
        "cwd": str(working_directory or PROJECT_ROOT),
        "env": env or os.environ.copy(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }

    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    process = process_runner(command, **popen_kwargs)
    pipeline_logger.info(
        "Launched detached pipeline worker pid=%s (triggered_by=%s, run_id=%s, step=%s, skip_download=%s)",
        getattr(process, "pid", None),
        triggered_by,
        run_id or "auto",
        single_step or "full",
        skip_download,
    )
    return process

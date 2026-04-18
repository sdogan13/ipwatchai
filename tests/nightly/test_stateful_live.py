"""
Nightly stateful/destructive live suite.

Delegates to the heavier live suites that create state, consume quotas, or
exercise background-task-adjacent flows.

Run directly:
    python tests/nightly/test_stateful_live.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.live.helpers.assertions import LiveReporter
from tests.nightly.helpers.config import load_nightly_config


CONFIG = load_nightly_config()
REPORTER = LiveReporter()
pytestmark = pytest.mark.skip(reason="Nightly live script; run directly with python tests/nightly/test_stateful_live.py")


def _run_delegate(name: str, script_path: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["TEST_BASE_URL"] = CONFIG.base_url
    env["TEST_EMAIL"] = CONFIG.email
    env["TEST_PASSWORD"] = CONFIG.password

    proc = subprocess.run(
        [sys.executable, script_path],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode == 0:
        REPORTER.ok(f"{name} -> passed")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> failed")
    REPORTER.record(name, False, proc.stdout[-1200:] + proc.stderr[-1200:])
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr)


def _wait_for_server_recovery(label: str, timeout_seconds: int = 180, poll_seconds: int = 5) -> bool:
    deadline = time.time() + timeout_seconds
    last_detail = "not started"
    consecutive_healthy = 0

    REPORTER.info(f"stateful recovery check -> waiting for server after {label}")
    while time.time() < deadline:
        try:
            started = time.perf_counter()
            response = requests.get(f"{CONFIG.base_url}/health", timeout=20)
            elapsed = time.perf_counter() - started
            if response.status_code == 200 and elapsed <= 20:
                consecutive_healthy += 1
                last_detail = f"healthy in {elapsed:.2f}s"
                if consecutive_healthy >= 2:
                    REPORTER.ok(f"stateful recovery check -> server recovered after {label} ({elapsed:.2f}s)")
                    return True
            else:
                consecutive_healthy = 0
                last_detail = f"status={response.status_code}, elapsed={elapsed:.2f}s"
        except Exception as exc:
            consecutive_healthy = 0
            last_detail = str(exc)
        time.sleep(poll_seconds)

    REPORTER.fail(f"stateful recovery check -> timed out after {label}: {last_detail}")
    REPORTER.record("stateful recovery check", False, last_detail)
    return False


def main() -> None:
    REPORTER.print_heading("NIGHTLY STATEFUL LIVE SUITE", server=CONFIG.base_url, user=CONFIG.email)

    _run_delegate("watchlist deep stateful suite", "tests/test_watchlist_e2e.py")
    if not _wait_for_server_recovery("watchlist deep stateful suite"):
        sys.exit(1)
    _run_delegate("reports feature stateful suite", "tests/live/features/test_reports_live.py")
    if not _wait_for_server_recovery("reports feature stateful suite"):
        sys.exit(1)
    _run_delegate("applications feature stateful suite", "tests/live/features/test_applications_live.py")

    sys.exit(0 if REPORTER.summary("NIGHTLY STATEFUL SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

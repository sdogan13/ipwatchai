"""
Aggregate live smoke suite for the main app surface.

Delegates to the persona suites, feature suites, and the deep watchlist E2E suite.

Run directly:
    python tests/test_live_app_e2e.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.config import load_live_config, should_run_env_flag, should_run_watchlist_e2e


CONFIG = load_live_config()
REPORTER = LiveReporter()
RUN_WATCHLIST_E2E = should_run_watchlist_e2e()
RUN_REPORTS_FEATURE = should_run_env_flag("RUN_REPORTS_FEATURE")
RUN_APPLICATIONS_FEATURE = should_run_env_flag("RUN_APPLICATIONS_FEATURE")
pytestmark = pytest.mark.skip(reason="Live E2E script; run directly with python tests/test_live_app_e2e.py")


def _safe_echo(text: str) -> None:
    if not text:
        return
    target = getattr(sys.stdout, "buffer", None)
    if target is not None:
        target.write(text.encode("utf-8", errors="replace"))
        target.write(b"\n")
        target.flush()
        return
    print(text.encode("ascii", errors="replace").decode("ascii"))


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
        _safe_echo(proc.stdout)
    if proc.stderr:
        _safe_echo(proc.stderr)


def run_public_persona() -> None:
    _run_delegate("public persona live suite", "tests/live/personas/test_public_live.py")


def run_member_persona() -> None:
    _run_delegate("member persona live suite", "tests/live/personas/test_member_live.py")


def run_free_persona() -> None:
    _run_delegate("free persona live suite", "tests/live/personas/test_free_user_live.py")


def run_paid_persona() -> None:
    _run_delegate("paid persona live suite", "tests/live/personas/test_paid_user_live.py")


def run_business_persona() -> None:
    _run_delegate("business persona live suite", "tests/live/personas/test_business_user_live.py")


def run_admin_persona() -> None:
    _run_delegate("admin persona live suite", "tests/live/personas/test_admin_live.py")


def run_superadmin_persona() -> None:
    _run_delegate("superadmin persona live suite", "tests/live/personas/test_superadmin_live.py")


def run_search_feature() -> None:
    _run_delegate("search feature live suite", "tests/live/features/test_search_live.py")


def run_dashboard_feature() -> None:
    _run_delegate("dashboard feature live suite", "tests/live/features/test_dashboard_live.py")


def run_watchlist_feature() -> None:
    _run_delegate("watchlist feature live suite", "tests/live/features/test_watchlist_live.py")


def run_billing_feature() -> None:
    _run_delegate("billing feature live suite", "tests/live/features/test_billing_live.py")


def run_reports_feature() -> None:
    if not RUN_REPORTS_FEATURE:
        REPORTER.warn("reports feature live suite -> skipped by RUN_REPORTS_FEATURE=0")
        REPORTER.record("reports feature live suite", True, "skipped")
        return
    _run_delegate("reports feature live suite", "tests/live/features/test_reports_live.py")


def run_applications_feature() -> None:
    if not RUN_APPLICATIONS_FEATURE:
        REPORTER.warn("applications feature live suite -> skipped by RUN_APPLICATIONS_FEATURE=0")
        REPORTER.record("applications feature live suite", True, "skipped")
        return
    _run_delegate("applications feature live suite", "tests/live/features/test_applications_live.py")


def run_watchlist_e2e() -> None:
    if not RUN_WATCHLIST_E2E:
        REPORTER.warn("watchlist E2E delegate -> skipped by RUN_WATCHLIST_E2E=0")
        REPORTER.record("watchlist E2E delegate", True, "skipped")
        return
    _run_delegate("watchlist E2E delegate", "tests/test_watchlist_e2e.py")


def main() -> None:
    REPORTER.print_heading("LIVE APP END-TO-END SMOKE", server=CONFIG.base_url, user=CONFIG.email)

    run_public_persona()
    run_member_persona()
    run_free_persona()
    run_paid_persona()
    run_business_persona()
    run_admin_persona()
    run_superadmin_persona()
    run_search_feature()
    run_dashboard_feature()
    run_watchlist_feature()
    run_billing_feature()
    run_reports_feature()
    run_applications_feature()
    run_watchlist_e2e()

    sys.exit(0 if REPORTER.summary("LIVE APP E2E SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

"""
Aggregate browser smoke suite for the main app surface.

Delegates to the public, member smoke, member feature, search, live-search,
business, watchlist assets, alerts, opposition, billing, and admin browser suites.

Run directly:
    python tests/test_browser_e2e.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.browser.helpers.config import load_browser_config
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.personas import (
    BUSINESS_PLANS,
    PAID_PLANS,
    resolve_free_persona_session,
    resolve_plan_persona_session,
)


CONFIG = load_browser_config()
REPORTER = LiveReporter()
pytestmark = pytest.mark.skip(reason="Browser E2E script; run directly with python tests/test_browser_e2e.py")


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


def _run_delegate(name: str, script_path: str, extra_env: dict[str, str] | None = None) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["TEST_BASE_URL"] = CONFIG.base_url
    env["TEST_EMAIL"] = CONFIG.email
    env["TEST_PASSWORD"] = CONFIG.password
    if extra_env:
        env.update(extra_env)

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


def _wait_for_server_recovery(label: str, timeout_seconds: int = 180, poll_seconds: int = 5) -> bool:
    deadline = time.time() + timeout_seconds
    last_detail = "not started"
    consecutive_healthy = 0

    REPORTER.info(f"browser recovery check -> waiting for server after {label}")
    while time.time() < deadline:
        try:
            started = time.perf_counter()
            response = requests.get(f"{CONFIG.base_url}/health", timeout=10)
            elapsed = time.perf_counter() - started
            if response.status_code == 200 and elapsed <= 5:
                consecutive_healthy += 1
                last_detail = f"healthy in {elapsed:.2f}s"
                if consecutive_healthy >= 2:
                    REPORTER.ok(f"browser recovery check -> server recovered after {label} ({elapsed:.2f}s)")
                    return True
            else:
                consecutive_healthy = 0
                last_detail = f"status={response.status_code}, elapsed={elapsed:.2f}s"
        except Exception as exc:
            consecutive_healthy = 0
            last_detail = str(exc)
        time.sleep(poll_seconds)

    REPORTER.fail(f"browser recovery check -> timed out after {label}: {last_detail}")
    REPORTER.record("browser recovery check", False, last_detail)
    return False


def _build_shared_persona_env() -> dict[str, str]:
    shared_env: dict[str, str] = {}
    setup_reporter = LiveReporter()

    free_session = resolve_free_persona_session(setup_reporter, label="browser aggregate free user")
    if free_session is not None:
        shared_env.update(
            {
                "TEST_FREE_EMAIL": free_session.config.email,
                "TEST_FREE_PASSWORD": free_session.config.password,
                "TEST_SEARCH_FREE_EMAIL": free_session.config.email,
                "TEST_SEARCH_FREE_PASSWORD": free_session.config.password,
                "TEST_LIVE_SEARCH_FREE_EMAIL": free_session.config.email,
                "TEST_LIVE_SEARCH_FREE_PASSWORD": free_session.config.password,
            }
        )

    paid_session, _ = resolve_plan_persona_session(
        setup_reporter,
        label="browser aggregate paid user",
        email_env="TEST_PAID_EMAIL",
        password_env="TEST_PAID_PASSWORD",
        required_plans=PAID_PLANS,
        fallback_to_default=False,
        provision_plan="starter",
    )
    if paid_session is not None:
        shared_env.update(
            {
                "TEST_PAID_EMAIL": paid_session.config.email,
                "TEST_PAID_PASSWORD": paid_session.config.password,
                "TEST_SEARCH_PAID_EMAIL": paid_session.config.email,
                "TEST_SEARCH_PAID_PASSWORD": paid_session.config.password,
            }
        )

    business_session, _ = resolve_plan_persona_session(
        setup_reporter,
        label="browser aggregate business user",
        email_env="TEST_BUSINESS_EMAIL",
        password_env="TEST_BUSINESS_PASSWORD",
        required_plans=BUSINESS_PLANS,
        fallback_to_default=False,
        provision_plan="professional",
    )
    if business_session is not None:
        shared_env.update(
            {
                "TEST_BUSINESS_EMAIL": business_session.config.email,
                "TEST_BUSINESS_PASSWORD": business_session.config.password,
                "TEST_LIVE_SEARCH_BUSINESS_EMAIL": business_session.config.email,
                "TEST_LIVE_SEARCH_BUSINESS_PASSWORD": business_session.config.password,
            }
        )

    return shared_env


def main() -> None:
    REPORTER.print_heading("BROWSER END-TO-END SMOKE", server=CONFIG.base_url, user=CONFIG.email)
    shared_env = _build_shared_persona_env()
    delegates = [
        ("public browser suite", "tests/browser/test_public_browser_smoke.py"),
        ("member browser suite", "tests/browser/test_member_browser_smoke.py"),
        ("member feature browser suite", "tests/browser/test_member_feature_browser.py"),
        ("search browser suite", "tests/browser/test_search_browser.py"),
        ("live-search browser suite", "tests/browser/test_live_search_browser.py"),
        ("business browser suite", "tests/browser/test_business_browser.py"),
        ("watchlist assets browser suite", "tests/browser/test_watchlist_assets_browser.py"),
        ("alerts browser suite", "tests/browser/test_alerts_browser.py"),
        ("opposition browser suite", "tests/browser/test_opposition_browser.py"),
        ("billing browser suite", "tests/browser/test_billing_browser.py"),
        ("admin browser suite", "tests/browser/test_admin_browser.py"),
    ]

    for index, (name, script_path) in enumerate(delegates):
        _run_delegate(name, script_path, extra_env=shared_env)
        if index < len(delegates) - 1 and not _wait_for_server_recovery(name):
            break

    sys.exit(0 if REPORTER.summary("BROWSER E2E SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

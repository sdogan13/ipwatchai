"""
Browser journeys for the superadmin admin panel.

Run directly:
    python tests/browser/test_admin_browser.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.config import load_browser_config, with_live_credentials
from tests.browser.helpers.session import launch_browser_page, login_via_modal, open_url
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import load_live_config
from tests.live.helpers.personas import fetch_authenticated_json


CONFIG = load_browser_config()
REPORTER = LiveReporter()
pytestmark = pytest.mark.skip(reason="Browser E2E script; run directly with python tests/browser/test_admin_browser.py")


def _resolve_superadmin_browser_config():
    if not (os.environ.get("TEST_SUPERADMIN_EMAIL") and os.environ.get("TEST_SUPERADMIN_PASSWORD")):
        return None

    live_config = load_live_config(
        email_env="TEST_SUPERADMIN_EMAIL",
        password_env="TEST_SUPERADMIN_PASSWORD",
    )
    client = LiveClient(live_config)
    login_response = None
    for attempt in range(1, 6):
        login_response = client.post(
            "/api/v1/auth/login",
            json_data={"email": live_config.email, "password": live_config.password},
            token=False,
        )
        if login_response.status_code == 200:
            break
        if login_response.status_code == 429 and attempt < 5:
            retry_after = login_response.headers.get("Retry-After")
            try:
                delay = max(1.0, float(retry_after)) if retry_after else 15.0
            except ValueError:
                delay = 15.0
            REPORTER.warn(
                f"superadmin browser bootstrap login -> 429 rate limited, retrying in {delay:.0f}s "
                f"(attempt {attempt}/5)"
            )
            time.sleep(delay)
            continue
        break
    if login_response.status_code != 200:
        REPORTER.fail(f"superadmin browser bootstrap login -> {login_response.status_code}: {login_response.text[:200]}")
        REPORTER.record("superadmin browser bootstrap login", False, login_response.text[:200])
        return None

    token = login_response.json().get("access_token")
    if not token:
        REPORTER.fail("superadmin browser bootstrap login -> missing access_token")
        REPORTER.record("superadmin browser bootstrap login", False, "missing access_token")
        return None

    client.token = token
    profile = fetch_authenticated_json(
        client,
        REPORTER,
        "/api/v1/auth/me",
        name="superadmin browser bootstrap profile",
    )
    if profile is None:
        return None
    if not profile.get("is_superadmin"):
        REPORTER.fail("superadmin browser bootstrap profile -> authenticated user is not superadmin")
        REPORTER.record("superadmin browser bootstrap profile", False, "not superadmin")
        return None

    REPORTER.ok(f"superadmin browser bootstrap profile -> {live_config.email}")
    REPORTER.record("superadmin browser bootstrap profile", True)
    return with_live_credentials(CONFIG, live_config)


def main() -> None:
    REPORTER.print_heading("ADMIN BROWSER", server=CONFIG.base_url)

    browser_config = _resolve_superadmin_browser_config()
    if browser_config is None:
        REPORTER.warn("superadmin admin browser journey -> skipped (no valid TEST_SUPERADMIN_* credentials)")
        REPORTER.record("superadmin admin browser journey", True, "skipped: no valid TEST_SUPERADMIN_* credentials")
        sys.exit(0)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(playwright, browser_config)
        try:
            def admin_panel_navigation() -> None:
                login_via_modal(page, browser_config, monitor)
                open_url(page, browser_config, "/admin")
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.authorized === true);
                    }""",
                    timeout=browser_config.timeout_ms,
                )
                page.get_by_text("Admin Panel", exact=False).first.wait_for()
                page.get_by_role("button", name="Organizations").click()
                page.get_by_role("heading", name="Organizations").wait_for()
                page.get_by_role("button", name="Users").click()
                page.get_by_role("heading", name="Users").wait_for()
                page.get_by_role("button", name="Plans & Limits").click()
                page.get_by_role("heading", name="Plans & Limits").wait_for()
                page.get_by_role("button", name="Analytics").click()
                page.get_by_role("heading", name="Usage Analytics").wait_for()
                page.get_by_role("button", name="All Settings").click()
                page.get_by_role("heading", name="All Settings").wait_for()

            run_browser_step(
                "superadmin admin browser journey",
                REPORTER,
                page,
                monitor,
                browser_config,
                admin_panel_navigation,
            )
        finally:
            context.close()
            browser.close()

    sys.exit(0 if REPORTER.summary("ADMIN BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

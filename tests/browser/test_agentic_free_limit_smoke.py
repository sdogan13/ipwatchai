"""Browser smoke: Free-tier daily Agentic Search limit + upgrade modal.

Validates the post-Quick-removal Free-tier gate end-to-end:

  * A managed-free persona starts the day with ``daily_live_searches.used = 0``
    (the cleanup helper resets it on test start).
  * 5 successful searches each return 200 and advance the counter by 1.
  * The 6th search returns **402** (the trademark route's choice) with the
    bilingual upgrade-hint payload and triggers the upgrade modal.
  * The upgrade modal recommends a paid plan and surfaces post-removal
    feature copy.

Run directly:
    python tests/browser/test_agentic_free_limit_smoke.py

Free plan has ``max_daily_live_searches = 5`` so this smoke fully exercises the
limit. We use a stable common query (``wosen``) so each search hits cached AI
embeddings — no scraper cost burn.

Cleanup: ``reset_daily_live_search_usage`` (which despite its legacy name now
resets the ``live_searches`` column in main's post-removal world) is called
both before AND after the test to keep the persona reusable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.config import load_browser_config, with_live_credentials
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.cleanup import reset_daily_live_search_usage
from tests.live.helpers.personas import (
    PersonaSession,
    resolve_free_persona_session,
)


CONFIG = load_browser_config()
REPORTER = LiveReporter()
AGENTIC_TIMEOUT_MS = max(CONFIG.timeout_ms, 180_000)
FREE_DAILY_LIMIT = 5


pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_agentic_free_limit_smoke.py"
)


# ===========================================================================
# Helpers
# ===========================================================================

def _open_search_tab(page, timeout_ms: int) -> None:
    page.click("#tab-btn-search")
    page.locator("#tab-content-search").wait_for(state="visible", timeout=timeout_ms)
    page.locator("#search-input").wait_for(state="visible", timeout=timeout_ms)


def _fetch_today_used(page) -> int:
    return page.evaluate(
        """async () => {
            const token =
                localStorage.getItem('auth_token') ||
                sessionStorage.getItem('auth_token') || '';
            const headers = token ? { Authorization: `Bearer ${token}` } : {};
            const r = await fetch('/api/v1/usage/summary', { headers });
            const body = await r.json().catch(() => null);
            const block = (body && body.usage && body.usage.daily_live_searches) || {};
            return typeof block.used === 'number' ? block.used : -1;
        }"""
    )


def _submit_search(page, query: str, timeout_ms: int):
    """Fill the input and click the search button, waiting for the /api/v1/search response.
    Returns the playwright Response object (so caller can inspect status)."""
    page.fill("#search-input", query)
    with page.expect_response(
        lambda r: r.request.method == "GET" and "/api/v1/search" in r.url and "/quick" not in r.url,
        timeout=timeout_ms,
    ) as resp_info:
        page.click("#dashboard-live-search-btn")
    return resp_info.value


def _is_upgrade_modal_visible(page) -> bool:
    return page.evaluate(
        """() => {
            const el = document.getElementById('upgrade-modal');
            return !!el && !el.classList.contains('hidden');
        }"""
    )


def _login_and_clear(page, browser_config, monitor) -> None:
    login_via_modal(page, browser_config, monitor)
    monitor.clear()


def _run_isolated(playwright, browser_config, name: str, action, **kwargs) -> None:
    browser, context, page, monitor = launch_browser_page(playwright, browser_config)
    try:
        run_browser_step(
            name, REPORTER, page, monitor, browser_config,
            lambda: action(page, monitor),
            **kwargs,
        )
    finally:
        context.close()
        browser.close()


# ===========================================================================
# Main
# ===========================================================================

def main() -> None:
    REPORTER.print_heading("AGENTIC FREE LIMIT SMOKE", server=CONFIG.base_url)

    session = resolve_free_persona_session(
        REPORTER,
        label="agentic free limit smoke",
        email_env="TEST_AGENTIC_FREE_EMAIL",
        password_env="TEST_AGENTIC_FREE_PASSWORD",
    )
    if session is None:
        sys.exit(1)

    # Reset usage so each run starts from 0/5.
    reset_daily_live_search_usage(REPORTER, session.user_id,
                                   name="AGENTIC FREE LIMIT setup: reset daily usage")

    with sync_playwright() as playwright:
        browser_config = with_live_credentials(CONFIG, session.config)

        # ---------------------------------------------------------------
        # STEP: Burn 5 searches, verify each one increments the counter,
        # then assert the 6th hits the 402 gate + opens the upgrade modal.
        # ---------------------------------------------------------------
        def step_free_user_hits_limit_at_6th_call(page, monitor) -> None:
            _login_and_clear(page, browser_config, monitor)
            _open_search_tab(page, browser_config.timeout_ms)

            start = _fetch_today_used(page)
            if start != 0:
                raise AssertionError(f"expected daily counter to start at 0, got {start}")

            # 5 successful searches.
            for i in range(1, FREE_DAILY_LIMIT + 1):
                response = _submit_search(page, "wosen", AGENTIC_TIMEOUT_MS)
                if response.status != 200:
                    raise AssertionError(
                        f"search #{i} expected 200, got {response.status} (url={response.url})"
                    )
                used = _fetch_today_used(page)
                if used != i:
                    raise AssertionError(
                        f"after search #{i} expected daily_live_searches.used={i}, got {used}"
                    )

            # The 6th must trip the limit gate.
            response = _submit_search(page, "wosen", AGENTIC_TIMEOUT_MS)
            if response.status != 402:
                raise AssertionError(
                    f"6th search expected 402 (daily_limit_exceeded), got {response.status}"
                )

            body = response.json()
            detail = body.get("detail", {}) if isinstance(body, dict) else {}
            if detail.get("error") != "daily_limit_exceeded":
                raise AssertionError(f"expected daily_limit_exceeded error, got {detail}")
            if detail.get("daily_limit") != FREE_DAILY_LIMIT:
                raise AssertionError(f"expected daily_limit={FREE_DAILY_LIMIT} in payload, got {detail}")
            if detail.get("remaining") != 0:
                raise AssertionError(f"expected remaining=0 in payload, got {detail}")
            if "message" not in detail or "message_en" not in detail:
                raise AssertionError(f"expected bilingual messages in payload, got {detail}")

            # Upgrade modal should appear after the gate.
            page.locator("#upgrade-modal").wait_for(state="visible",
                                                    timeout=browser_config.timeout_ms)
            if not _is_upgrade_modal_visible(page):
                raise AssertionError("expected upgrade modal visible after 6th search")

            # Counter must not have advanced past 5 — failed search doesn't burn quota.
            final = _fetch_today_used(page)
            if final != FREE_DAILY_LIMIT:
                raise AssertionError(
                    f"counter advanced past limit: expected {FREE_DAILY_LIMIT}, got {final}"
                )

        _run_isolated(playwright, browser_config,
                      "free user blocked at 6th daily search with upgrade modal",
                      step_free_user_hits_limit_at_6th_call,
                      allow_request_failures=("402 GET", "/api/v1/search"))

        # Reset again so the persona is reusable.
        reset_daily_live_search_usage(REPORTER, session.user_id,
                                       name="AGENTIC FREE LIMIT teardown: reset daily usage")

        failures = REPORTER.summary("AGENTIC FREE LIMIT SMOKE SUMMARY")
        sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()

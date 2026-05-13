"""Browser smoke: unified Agentic Search on the main dashboard.

Validates the post-Quick-removal search experience end-to-end against a real
running backend:

  * The dashboard Search tab has a single search input (``#search-input``) and
    a single search button (``#dashboard-live-search-btn``).
  * Submitting fires ``GET /api/v1/search`` (the bare canonical endpoint,
    NOT ``/api/v1/search/quick`` and NOT ``/api/v1/search/intelligent``).
  * The usage summary payload exposes ``daily_live_searches: {used, limit}``;
    the legacy ``daily_quick_searches`` / ``monthly_live_searches`` keys are
    gone.
  * After a successful search, today's counter increments by exactly 1.

Run directly:
    python tests/browser/test_agentic_search_smoke.py

Uses the managed-professional persona (auto-provisioned via
``tests/live/helpers/test_accounts.py``); Professional has
``max_daily_live_searches = 2000`` so the smoke runs comfortably without
burning into Free's 5/day budget.
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
from tests.live.helpers.personas import (
    BUSINESS_PLANS,
    PersonaSession,
    canonical_plan_name,
    resolve_plan_persona_session,
)


CONFIG = load_browser_config()
REPORTER = LiveReporter()
AGENTIC_TIMEOUT_MS = max(CONFIG.timeout_ms, 180_000)


pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_agentic_search_smoke.py"
)


# ===========================================================================
# Probes (run inside the page via page.evaluate)
# ===========================================================================

def _fetch_usage_contract(page) -> dict:
    """Pull the usage summary + search credits payloads and surface only the
    fields this smoke cares about."""
    return page.evaluate(
        """async () => {
            const token =
                localStorage.getItem('auth_token') ||
                localStorage.getItem('access_token') ||
                sessionStorage.getItem('auth_token') ||
                sessionStorage.getItem('access_token') ||
                '';
            const headers = token ? { Authorization: `Bearer ${token}` } : {};

            async function get(path) {
                const r = await fetch(path, { headers });
                let body = null;
                try { body = await r.json(); } catch (_) { body = null; }
                return { status: r.status, body };
            }

            const [usage, credits] = await Promise.all([
                get('/api/v1/usage/summary'),
                get('/api/v1/search/credits'),
            ]);
            const block = (usage.body && usage.body.usage) || {};
            return {
                usageStatus: usage.status,
                creditsStatus: credits.status,
                plan: usage.body ? usage.body.plan : null,
                dailyLive: block.daily_live_searches || null,
                hasDailyQuickKey: 'daily_quick_searches' in block,
                hasMonthlyLiveKey: 'monthly_live_searches' in block,
                creditsCanUse: credits.body ? credits.body.can_use_live_search : null,
                creditsDailyLimit: credits.body ? credits.body.daily_limit : null,
                creditsRemaining: credits.body ? credits.body.remaining : null,
            };
        }"""
    )


def _open_search_tab(page, timeout_ms: int) -> None:
    page.click("#tab-btn-search")
    page.locator("#tab-content-search").wait_for(state="visible", timeout=timeout_ms)
    page.locator("#search-input").wait_for(state="visible", timeout=timeout_ms)
    page.locator("#dashboard-live-search-btn").wait_for(state="visible", timeout=timeout_ms)


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
    REPORTER.print_heading("AGENTIC SEARCH SMOKE", server=CONFIG.base_url)

    session, skipped = resolve_plan_persona_session(
        REPORTER,
        label="agentic search smoke business user",
        email_env="TEST_AGENTIC_SEARCH_EMAIL",
        password_env="TEST_AGENTIC_SEARCH_PASSWORD",
        required_plans=BUSINESS_PLANS,
        fallback_to_default=False,
        provision_plan="professional",
    )
    if session is None:
        REPORTER.warn("AGENTIC SEARCH SMOKE -> skipped (no business persona)")
        sys.exit(0)

    with sync_playwright() as playwright:
        browser_config = with_live_credentials(CONFIG, session.config)

        # ---------------------------------------------------------------
        # STEP 1: usage payload uses the post-Quick-removal shape.
        # ---------------------------------------------------------------
        def step_usage_payload_shape(page, monitor) -> None:
            _login_and_clear(page, browser_config, monitor)
            contract = _fetch_usage_contract(page)

            if canonical_plan_name(contract["plan"]) not in BUSINESS_PLANS:
                raise AssertionError(f"expected business plan, got {contract['plan']}")

            if contract["dailyLive"] is None:
                raise AssertionError(
                    f"expected usage.daily_live_searches in payload, contract={contract}"
                )
            if not isinstance(contract["dailyLive"].get("used"), int):
                raise AssertionError(f"daily_live_searches.used must be int, got {contract['dailyLive']}")
            if not isinstance(contract["dailyLive"].get("limit"), int):
                raise AssertionError(f"daily_live_searches.limit must be int, got {contract['dailyLive']}")

            if contract["hasDailyQuickKey"]:
                raise AssertionError("legacy 'daily_quick_searches' key still present in usage payload")
            if contract["hasMonthlyLiveKey"]:
                raise AssertionError("legacy 'monthly_live_searches' key still present in usage payload")

            if contract["creditsStatus"] != 200:
                raise AssertionError(f"/search/credits returned {contract['creditsStatus']}")
            if not isinstance(contract["creditsDailyLimit"], int):
                raise AssertionError(f"credits.daily_limit must be int, got {contract}")
            if contract["creditsCanUse"] is not True:
                raise AssertionError(f"business plan must have can_use_live_search=True, got {contract}")

        _run_isolated(playwright, browser_config,
                      "usage payload uses daily_live_searches (post-Quick-removal shape)",
                      step_usage_payload_shape)

        # ---------------------------------------------------------------
        # STEP 2: search fires GET /api/v1/search and increments counter.
        # ---------------------------------------------------------------
        def step_search_uses_bare_endpoint(page, monitor) -> None:
            _login_and_clear(page, browser_config, monitor)
            before = _fetch_usage_contract(page)
            before_used = before["dailyLive"]["used"]

            _open_search_tab(page, browser_config.timeout_ms)
            page.fill("#search-input", "wosen")

            with page.expect_response(
                lambda r: r.request.method == "GET" and "/api/v1/search" in r.url and "/quick" not in r.url and "/intelligent" not in r.url,
                timeout=AGENTIC_TIMEOUT_MS,
            ) as resp_info:
                page.click("#dashboard-live-search-btn")

            response = resp_info.value
            if response.status != 200:
                raise AssertionError(
                    f"expected 200 from /api/v1/search, got {response.status} (url={response.url})"
                )
            # The URL must be the bare endpoint, NOT a legacy variant.
            if "/api/v1/search/quick" in response.url:
                raise AssertionError(f"search fired against deleted /quick endpoint: {response.url}")
            if "/api/v1/search/intelligent" in response.url:
                raise AssertionError(f"search fired against deleted /intelligent endpoint: {response.url}")

            # Counter must have advanced by exactly 1.
            after = _fetch_usage_contract(page)
            after_used = after["dailyLive"]["used"]
            delta = after_used - before_used
            if delta != 1:
                raise AssertionError(
                    f"expected daily_live_searches.used to advance by 1, got delta={delta} "
                    f"(before={before_used}, after={after_used})"
                )

        _run_isolated(playwright, browser_config,
                      "search button fires GET /api/v1/search and increments counter",
                      step_search_uses_bare_endpoint,
                      allow_request_failures=("/api/v1/search",))

        failures = REPORTER.summary("AGENTIC SEARCH SMOKE SUMMARY")
        sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()

"""
Browser journeys for dashboard live-search coverage.

Run directly:
    python tests/browser/test_live_search_browser.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from database.crud import Database, UserCRUD
from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.config import load_browser_config, with_live_credentials
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.personas import (
    BUSINESS_PLANS,
    PersonaSession,
    canonical_plan_name,
    resolve_free_persona_session,
    resolve_plan_persona_session,
)


CONFIG = load_browser_config()
REPORTER = LiveReporter()
FREE_SESSION: PersonaSession | None = None
FREE_RESOLVED = False
BUSINESS_SESSION: PersonaSession | None = None
BUSINESS_RESOLVED = False
LIVE_SEARCH_TIMEOUT_MS = max(CONFIG.timeout_ms, 180000)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_live_search_browser.py"
)


def ensure_free_session() -> PersonaSession | None:
    global FREE_SESSION
    global FREE_RESOLVED
    if FREE_SESSION is None and not FREE_RESOLVED:
        FREE_RESOLVED = True
        FREE_SESSION = resolve_free_persona_session(
            REPORTER,
            label="live-search browser free user",
            email_env="TEST_LIVE_SEARCH_FREE_EMAIL",
            password_env="TEST_LIVE_SEARCH_FREE_PASSWORD",
        )
    return FREE_SESSION


def ensure_business_session() -> tuple[PersonaSession | None, bool]:
    global BUSINESS_SESSION
    global BUSINESS_RESOLVED
    if BUSINESS_SESSION is None and not BUSINESS_RESOLVED:
        BUSINESS_RESOLVED = True
        BUSINESS_SESSION, skipped = resolve_plan_persona_session(
            REPORTER,
            label="live-search browser business user",
            email_env="TEST_LIVE_SEARCH_BUSINESS_EMAIL",
            password_env="TEST_LIVE_SEARCH_BUSINESS_PASSWORD",
            required_plans=BUSINESS_PLANS,
            fallback_to_default=False,
            provision_plan="professional",
        )
        return BUSINESS_SESSION, skipped
    return BUSINESS_SESSION, False


def _ensure_email_verified(session: PersonaSession) -> None:
    if not session.user_id:
        return

    try:
        with Database() as db:
            user = UserCRUD.get_by_id(db, session.user_id)
            if not user or user.get("is_email_verified"):
                return

            UserCRUD.verify_email(db, session.user_id)
            cur = db.cursor()
            cur.execute(
                "UPDATE email_verification_tokens SET used_at = NOW() WHERE user_id = %s AND used_at IS NULL",
                (str(session.user_id),),
            )
            db.commit()
            REPORTER.info(f"{session.label} email verification -> marked verified for browser setup")
    except Exception as exc:
        REPORTER.warn(f"{session.label} email verification -> setup failed ({exc})")


def _login_and_clear_monitor(page, browser_config, monitor) -> None:
    login_via_modal(page, browser_config, monitor)
    monitor.clear()


def _open_search_tab(page, timeout_ms: int) -> None:
    page.click("#tab-btn-search")
    page.locator("#tab-content-search").wait_for(state="visible", timeout=timeout_ms)
    page.locator('input[name="trademark-search"]').wait_for(state="visible", timeout=timeout_ms)
    page.locator("#dashboard-live-search-btn").wait_for(state="visible", timeout=timeout_ms)


def _live_search_contract(page) -> dict:
    return page.evaluate(
        """async () => {
            function authHeaders() {
                const token =
                    localStorage.getItem('auth_token') ||
                    localStorage.getItem('access_token') ||
                    sessionStorage.getItem('auth_token') ||
                    sessionStorage.getItem('access_token') ||
                    '';
                return token ? { Authorization: `Bearer ${token}` } : {};
            }

            async function fetchJson(path) {
                const response = await fetch(path, { headers: authHeaders() });
                let payload = null;
                try {
                    payload = await response.json();
                } catch (_error) {
                    payload = null;
                }
                return { ok: response.ok, status: response.status, payload };
            }

            const [usageRes, creditsRes] = await Promise.all([
                fetchJson('/api/v1/usage/summary'),
                fetchJson('/api/v1/search/credits'),
            ]);

            const usage = (usageRes.payload || {}).usage || {};
            const liveUsage = usage.monthly_live_searches || {};
            const credits = creditsRes.payload || {};

            return {
                usageStatus: usageRes.status,
                creditsStatus: creditsRes.status,
                plan: usageRes.payload ? usageRes.payload.plan : null,
                liveUsed: liveUsage.used,
                liveLimit: liveUsage.limit,
                canUseLiveSearch: credits.can_use_live_search,
                remainingCredits: credits.remaining,
            };
        }"""
    )


def _search_state(page) -> dict:
    return page.evaluate(
        """() => {
            const state = document.body._x_dataStack && document.body._x_dataStack[0];
            const hidden = (id) => document.getElementById(id)?.classList.contains('hidden') ?? true;
            return {
                searchError: state ? (state.searchError || '') : 'missing alpine state',
                searchLoading: state ? !!state.searchLoading : false,
                searchType: state ? (state.searchType || '') : '',
                resultsCount: state ? (state.searchResults || []).length : -1,
                scrapeTriggered: state && state.searchMeta ? !!state.searchMeta.scrape_triggered : false,
                source: state && state.searchMeta ? (state.searchMeta.source || '') : '',
                creditsRemaining: state && state.searchMeta ? state.searchMeta.credits_remaining : null,
                upgradeModalVisible: !hidden('upgrade-modal'),
                creditsModalVisible: !hidden('credits-modal'),
                loadingModalVisible: !hidden('agentic-loading-modal'),
            };
        }"""
    )


def _run_isolated_step(playwright, browser_config, name: str, action, **kwargs) -> None:
    browser, context, page, monitor = launch_browser_page(playwright, browser_config)
    try:
        run_browser_step(
            name,
            REPORTER,
            page,
            monitor,
            browser_config,
            lambda: action(page, monitor),
            **kwargs,
        )
    finally:
        context.close()
        browser.close()


def main() -> None:
    REPORTER.print_heading("LIVE SEARCH BROWSER", server=CONFIG.base_url)

    free_session = ensure_free_session()
    if free_session is None:
        sys.exit(1)
    _ensure_email_verified(free_session)

    business_session, business_skipped = ensure_business_session()
    if business_session is not None:
        _ensure_email_verified(business_session)

    with sync_playwright() as playwright:
        free_browser_config = with_live_credentials(CONFIG, free_session.config)

        def free_live_search_upgrade_gate(page, monitor) -> None:
            _login_and_clear_monitor(page, free_browser_config, monitor)
            contract = _live_search_contract(page)
            if canonical_plan_name(contract["plan"]) != "free":
                raise AssertionError(f"expected free plan, got {contract['plan']}")
            if contract["canUseLiveSearch"] is not False:
                raise AssertionError(f"expected free live-search gate, got {contract}")

            _open_search_tab(page, free_browser_config.timeout_ms)
            page.fill('input[name="trademark-search"]', "wosen")

            with page.expect_response(
                lambda response: response.request.method == "GET" and "/api/v1/search/intelligent" in response.url,
                timeout=LIVE_SEARCH_TIMEOUT_MS,
            ) as response_info:
                page.click("#dashboard-live-search-btn")
            response = response_info.value
            if response.status != 403:
                raise AssertionError(f"expected free live-search 403 gate, got {response.status}")

            page.locator("#upgrade-modal").wait_for(state="visible", timeout=free_browser_config.timeout_ms)
            state = _search_state(page)
            if state["resultsCount"] != 0:
                raise AssertionError(f"expected no live-search results for free gate, got {state['resultsCount']}")
            if not state["upgradeModalVisible"]:
                raise AssertionError("expected upgrade modal to be visible after free live-search gate")
            recommended_plan = (page.locator("#upgrade-plan-code").text_content() or "").strip().lower()
            if recommended_plan != "starter":
                raise AssertionError(f"expected starter upgrade recommendation for free live-search gate, got {recommended_plan!r}")
            modal_offer = page.evaluate(
                """() => ({
                    title: (document.getElementById('upgrade-modal-title')?.textContent || '').trim(),
                    description: (document.getElementById('upgrade-modal-description')?.textContent || '').trim(),
                    price: (document.getElementById('upgrade-plan-price')?.textContent || '').trim(),
                    features: Array.from(document.querySelectorAll('#upgrade-feature-list li span:last-child')).map((el) => (el.textContent || '').trim())
                })"""
            )
            expected_copy = page.evaluate(
                """() => ({
                    title: window.AppI18n.t('upgrade.live_search_title'),
                    description: window.AppI18n.t('upgrade.live_search_description')
                })"""
            )
            if modal_offer["title"] != expected_copy["title"] or modal_offer["description"] != expected_copy["description"]:
                raise AssertionError(f"expected live-search upgrade copy, got {modal_offer}, expected {expected_copy}")
            if "499" not in modal_offer["price"]:
                raise AssertionError(f"expected starter monthly price in live-search upgrade modal, got {modal_offer}")
            if not any("50" in feature for feature in modal_offer["features"]):
                raise AssertionError(f"expected starter quick-search highlight in live-search upgrade modal, got {modal_offer}")
            if not any("10" in feature for feature in modal_offer["features"]):
                raise AssertionError(f"expected starter live-search highlight in live-search upgrade modal, got {modal_offer}")
            if state["loadingModalVisible"]:
                raise AssertionError("expected agentic loading modal to close after free live-search gate")

        _run_isolated_step(
            playwright,
            free_browser_config,
            "free live-search upgrade gate browser journey",
            free_live_search_upgrade_gate,
            allow_console_errors=("status of 403",),
            allow_request_failures=("/api/v1/search/intelligent",),
        )

        if business_session is None:
            if business_skipped:
                REPORTER.warn(
                    "business live-search browser journey -> skipped "
                    "(no business persona or superadmin provisioning available)"
                )
                REPORTER.record(
                    "business live-search browser journey",
                    True,
                    "skipped: no business persona or superadmin provisioning available",
                )
            else:
                REPORTER.fail("business live-search browser journey -> unable to resolve business persona")
                REPORTER.record("business live-search browser journey", False, "business persona resolution failed")
            sys.exit(0 if REPORTER.summary("LIVE SEARCH BROWSER SUMMARY") == 0 else 1)

        business_browser_config = with_live_credentials(CONFIG, business_session.config)

        def business_live_search_success(page, monitor) -> None:
            _login_and_clear_monitor(page, business_browser_config, monitor)
            before = _live_search_contract(page)
            if canonical_plan_name(before["plan"]) not in BUSINESS_PLANS:
                raise AssertionError(f"expected business plan, got {before['plan']}")
            if before["canUseLiveSearch"] is not True:
                raise AssertionError(f"expected live-search access, got {before}")
            if not isinstance(before["remainingCredits"], int) or before["remainingCredits"] <= 0:
                raise AssertionError(f"expected positive live-search credits, got {before['remainingCredits']}")

            _open_search_tab(page, business_browser_config.timeout_ms)
            page.fill('input[name="trademark-search"]', "wosen")

            with page.expect_response(
                lambda response: response.request.method == "GET" and "/api/v1/search/intelligent" in response.url,
                timeout=LIVE_SEARCH_TIMEOUT_MS,
            ) as response_info:
                page.click("#dashboard-live-search-btn")
            response = response_info.value
            if response.status != 200:
                raise AssertionError(f"unexpected business live-search status: {response.status}")
            if "query=wosen" not in response.url:
                raise AssertionError(f"expected intelligent search query in request URL, got {response.url}")

            page.wait_for_function(
                """() => {
                    const state = document.body._x_dataStack && document.body._x_dataStack[0];
                    return !!(state && !state.searchLoading && (state.searchResults || []).length > 0);
                }""",
                timeout=LIVE_SEARCH_TIMEOUT_MS,
            )

            state = _search_state(page)
            after = _live_search_contract(page)
            if state["searchError"]:
                raise AssertionError(f"unexpected live-search error: {state['searchError']}")
            if state["resultsCount"] <= 0:
                raise AssertionError(f"expected business live-search results > 0, got {state['resultsCount']}")
            if state["source"] != "live" or not state["scrapeTriggered"]:
                raise AssertionError(f"expected live scraped results, got {state}")
            if state["creditsRemaining"] is None:
                raise AssertionError("expected searchMeta credits_remaining after live search")
            if state["upgradeModalVisible"] or state["creditsModalVisible"]:
                raise AssertionError("expected no gating modals during successful business live search")
            if state["loadingModalVisible"]:
                raise AssertionError("expected agentic loading modal to close after successful live search")
            if not isinstance(after["liveUsed"], int) or after["liveUsed"] < int(before["liveUsed"] or 0) + 1:
                raise AssertionError(f"expected live-search usage to increment, before={before}, after={after}")
            if (
                isinstance(after["remainingCredits"], int)
                and isinstance(before["remainingCredits"], int)
                and after["remainingCredits"] >= before["remainingCredits"]
            ):
                raise AssertionError(
                    f"expected remaining live-search credits to decrease, before={before['remainingCredits']}, "
                    f"after={after['remainingCredits']}"
                )

        _run_isolated_step(
            playwright,
            business_browser_config,
            "business live-search browser journey",
            business_live_search_success,
        )

    sys.exit(0 if REPORTER.summary("LIVE SEARCH BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

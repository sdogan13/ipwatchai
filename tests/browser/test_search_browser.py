"""
Browser journeys for quick-search coverage split by free, paid, and image paths.

Run directly:
    python tests/browser/test_search_browser.py
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
from tests.live.helpers.config import PNG_1X1
from tests.live.helpers.personas import (
    PAID_PLANS,
    PersonaSession,
    canonical_plan_name,
    resolve_free_persona_session,
    resolve_plan_persona_session,
)


CONFIG = load_browser_config()
REPORTER = LiveReporter()
FREE_SESSION: PersonaSession | None = None
FREE_RESOLVED = False
PAID_SESSION: PersonaSession | None = None
PAID_RESOLVED = False
FREE_BROWSER_QUICK_LIMIT: int | None = None

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_search_browser.py"
)


def ensure_free_session() -> PersonaSession | None:
    global FREE_SESSION
    global FREE_RESOLVED
    if FREE_SESSION is None and not FREE_RESOLVED:
        FREE_RESOLVED = True
        FREE_SESSION = resolve_free_persona_session(
            REPORTER,
            label="search browser free user",
            email_env="TEST_SEARCH_FREE_EMAIL",
            password_env="TEST_SEARCH_FREE_PASSWORD",
        )
    return FREE_SESSION


def ensure_paid_session() -> tuple[PersonaSession | None, bool]:
    global PAID_SESSION
    global PAID_RESOLVED
    if PAID_SESSION is None and not PAID_RESOLVED:
        PAID_RESOLVED = True
        PAID_SESSION, skipped = resolve_plan_persona_session(
            REPORTER,
            label="search browser paid user",
            email_env="TEST_SEARCH_PAID_EMAIL",
            password_env="TEST_SEARCH_PAID_PASSWORD",
            required_plans=PAID_PLANS,
            fallback_to_default=False,
            provision_plan="starter",
        )
        return PAID_SESSION, skipped
    return PAID_SESSION, False


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


def _usage_summary(page) -> dict:
    return page.evaluate(
        """async () => {
            const token =
                localStorage.getItem('auth_token') ||
                localStorage.getItem('access_token') ||
                sessionStorage.getItem('auth_token') ||
                sessionStorage.getItem('access_token') ||
                '';
            const response = await fetch('/api/v1/usage/summary', {
                headers: token ? { Authorization: `Bearer ${token}` } : {},
            });
            let payload = null;
            try {
                payload = await response.json();
            } catch (_error) {
                payload = null;
            }
            const bucket = payload && payload.usage ? payload.usage.daily_quick_searches || {} : {};
            return {
                status: response.status,
                plan: payload ? payload.plan : null,
                used: bucket.used,
                limit: bucket.limit,
            };
        }"""
    )


def _search_state(page) -> dict:
    return page.evaluate(
        """() => {
            const state = document.body._x_dataStack && document.body._x_dataStack[0];
            return {
                searchError: state ? (state.searchError || '') : 'missing alpine state',
                searchLoading: state ? !!state.searchLoading : false,
                resultsCount: state ? (state.searchResults || []).length : -1,
                imageUsed: state && state.searchMeta ? !!state.searchMeta.image_used : false,
                source: state && state.searchMeta ? (state.searchMeta.source || '') : '',
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
    global FREE_BROWSER_QUICK_LIMIT

    REPORTER.print_heading("SEARCH BROWSER", server=CONFIG.base_url)

    free_session = ensure_free_session()
    if free_session is None:
        sys.exit(1)
    _ensure_email_verified(free_session)

    paid_session, paid_skipped = ensure_paid_session()
    if paid_session is not None:
        _ensure_email_verified(paid_session)

    with sync_playwright() as playwright:
        free_browser_config = with_live_credentials(CONFIG, free_session.config)

        def free_text_search(page, monitor) -> None:
            _login_and_clear_monitor(page, free_browser_config, monitor)
            usage = _usage_summary(page)
            if usage["status"] != 200:
                raise AssertionError(f"free usage summary status {usage['status']}")
            if canonical_plan_name(usage["plan"]) != "free":
                raise AssertionError(f"expected free plan, got {usage['plan']}")
            if not isinstance(usage["limit"], int) or usage["limit"] <= 0:
                raise AssertionError(f"expected positive free quick-search limit, got {usage['limit']}")
            FREE_BROWSER_QUICK_LIMIT = usage["limit"]

            _open_search_tab(page, free_browser_config.timeout_ms)
            with page.expect_response(
                lambda response: response.request.method == "GET" and "/api/v1/search/quick" in response.url,
                timeout=free_browser_config.timeout_ms,
            ) as response_info:
                page.fill('input[name="trademark-search"]', "wosen")
                page.press('input[name="trademark-search"]', "Enter")
            response = response_info.value
            if response.status != 200:
                raise AssertionError(f"unexpected free quick-search status: {response.status}")

            page.wait_for_function(
                "() => document.body._x_dataStack && document.body._x_dataStack[0] && !document.body._x_dataStack[0].searchLoading",
                timeout=free_browser_config.timeout_ms,
            )
            state = _search_state(page)
            if state["searchError"]:
                raise AssertionError(f"unexpected free quick-search error: {state['searchError']}")
            if state["resultsCount"] <= 0:
                raise AssertionError(f"expected free quick-search results > 0, got {state['resultsCount']}")
            if state["imageUsed"]:
                raise AssertionError("expected free text quick search to keep imageUsed=false")

        _run_isolated_step(
            playwright,
            free_browser_config,
            "free quick search text browser journey",
            free_text_search,
        )

        if paid_session is None:
            if paid_skipped:
                REPORTER.warn(
                    "paid quick search browser journeys -> skipped "
                    "(no paid persona or superadmin provisioning available)"
                )
                REPORTER.record(
                    "paid quick search browser journeys",
                    True,
                    "skipped: no paid persona or superadmin provisioning available",
                )
            else:
                REPORTER.fail("paid quick search browser journeys -> unable to resolve paid persona")
                REPORTER.record("paid quick search browser journeys", False, "paid persona resolution failed")
            sys.exit(0 if REPORTER.summary("SEARCH BROWSER SUMMARY") == 0 else 1)

        paid_browser_config = with_live_credentials(CONFIG, paid_session.config)

        def paid_text_search(page, monitor) -> None:
            _login_and_clear_monitor(page, paid_browser_config, monitor)
            usage = _usage_summary(page)
            if usage["status"] != 200:
                raise AssertionError(f"paid usage summary status {usage['status']}")
            if canonical_plan_name(usage["plan"]) not in PAID_PLANS:
                raise AssertionError(f"expected paid plan, got {usage['plan']}")
            if not isinstance(usage["limit"], int) or usage["limit"] <= 0:
                raise AssertionError(f"expected positive paid quick-search limit, got {usage['limit']}")
            if FREE_BROWSER_QUICK_LIMIT is not None and usage["limit"] <= FREE_BROWSER_QUICK_LIMIT:
                raise AssertionError(
                    f"expected paid quick-search limit > free quick-search limit "
                    f"({FREE_BROWSER_QUICK_LIMIT}), got {usage['limit']}"
                )

            _open_search_tab(page, paid_browser_config.timeout_ms)
            with page.expect_response(
                lambda response: response.request.method == "GET" and "/api/v1/search/quick" in response.url,
                timeout=paid_browser_config.timeout_ms,
            ) as response_info:
                page.fill('input[name="trademark-search"]', "wosen")
                page.press('input[name="trademark-search"]', "Enter")
            response = response_info.value
            if response.status != 200:
                raise AssertionError(f"unexpected paid quick-search status: {response.status}")

            page.wait_for_function(
                "() => document.body._x_dataStack && document.body._x_dataStack[0] && !document.body._x_dataStack[0].searchLoading",
                timeout=paid_browser_config.timeout_ms,
            )
            state = _search_state(page)
            if state["searchError"]:
                raise AssertionError(f"unexpected paid quick-search error: {state['searchError']}")
            if state["resultsCount"] <= 0:
                raise AssertionError(f"expected paid quick-search results > 0, got {state['resultsCount']}")
            if state["imageUsed"]:
                raise AssertionError("expected paid text quick search to keep imageUsed=false")

        _run_isolated_step(
            playwright,
            paid_browser_config,
            "paid quick search text browser journey",
            paid_text_search,
        )

        def paid_image_search(page, monitor) -> None:
            _login_and_clear_monitor(page, paid_browser_config, monitor)
            _open_search_tab(page, paid_browser_config.timeout_ms)

            upload_input = page.locator('#tab-content-search input[type="file"]').first
            upload_input.set_input_files([{"name": "search-test.png", "mimeType": "image/png", "buffer": PNG_1X1}])
            page.fill('input[name="trademark-search"]', "wosen")

            with page.expect_response(
                lambda response: response.request.method == "POST" and "/api/v1/search/quick" in response.url,
                timeout=paid_browser_config.timeout_ms,
            ) as response_info:
                page.press('input[name="trademark-search"]', "Enter")
            response = response_info.value
            if response.status != 200:
                raise AssertionError(f"unexpected paid quick-search image status: {response.status}")

            page.wait_for_function(
                "() => document.body._x_dataStack && document.body._x_dataStack[0] && !document.body._x_dataStack[0].searchLoading",
                timeout=paid_browser_config.timeout_ms,
            )
            state = _search_state(page)
            if state["searchError"]:
                raise AssertionError(f"unexpected paid image quick-search error: {state['searchError']}")
            if state["resultsCount"] <= 0:
                raise AssertionError(f"expected paid image quick-search results > 0, got {state['resultsCount']}")
            if not state["imageUsed"]:
                raise AssertionError("expected paid image quick-search to set imageUsed=true")
            if state["source"] != "database":
                raise AssertionError(f"expected quick-search source database, got {state['source']}")

        _run_isolated_step(
            playwright,
            paid_browser_config,
            "paid quick search image browser journey",
            paid_image_search,
        )

    sys.exit(0 if REPORTER.summary("SEARCH BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

"""
Browser journeys for quick-search coverage split by free, paid, and image paths.

Run directly:
    python tests/browser/test_search_browser.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from uuid import uuid4

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
from tests.live.helpers.cleanup import reset_daily_quick_search_usage
from tests.live.helpers.config import PNG_1X1
from tests.live.helpers.personas import (
    PAID_PLANS,
    PersonaSession,
    canonical_plan_name,
    resolve_free_persona_session,
    resolve_plan_persona_session,
)
from utils.subscription import PLAN_FEATURES


CONFIG = load_browser_config()
REPORTER = LiveReporter()
FREE_SESSION: PersonaSession | None = None
FREE_RESOLVED = False
PAID_SESSION: PersonaSession | None = None
PAID_RESOLVED = False
FREE_BROWSER_QUICK_LIMIT: int | None = None
WATCHLIST_PREFIX = "SEARCH WL"

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


def _upgrade_modal_state(page) -> dict:
    return page.evaluate(
        """() => ({
            visible: !!(document.getElementById('upgrade-modal') && !document.getElementById('upgrade-modal').classList.contains('hidden')),
            recommendedPlan: (document.getElementById('upgrade-plan-code')?.textContent || '').trim().toLowerCase()
        })"""
    )


def _list_watchlist_items(session: PersonaSession, *, page_size: int = 100) -> list[dict]:
    response = session.client.get("/api/v1/watchlist", params={"page_size": page_size})
    if response.status_code != 200:
        raise AssertionError(f"unexpected watchlist list status: {response.status_code}")
    return response.json().get("items") or []


def _watchlist_usage(session: PersonaSession) -> tuple[int, int]:
    response = session.client.get("/api/v1/usage/summary")
    if response.status_code != 200:
        raise AssertionError(f"unexpected usage summary status: {response.status_code}")
    usage = (response.json().get("usage") or {}).get("watchlist_items") or {}
    return int(usage.get("used") or 0), int(usage.get("limit") or 0)


def _delete_watchlist_ids(session: PersonaSession, item_ids: list[str]) -> None:
    for item_id in item_ids:
        response = session.client.delete(f"/api/v1/watchlist/{item_id}")
        if response.status_code not in (200, 404):
            raise AssertionError(f"unexpected watchlist delete status during cleanup: {response.status_code}")


def _trim_watchlist_items(session: PersonaSession, target_used: int) -> None:
    items = _list_watchlist_items(session)
    overflow = items[target_used:]
    if not overflow:
        return
    _delete_watchlist_ids(session, [str(item.get("id")) for item in overflow if item.get("id")])


def _create_watchlist_item(session: PersonaSession, brand_name: str) -> str:
    payload = {
        "brand_name": brand_name,
        "nice_class_numbers": [9],
        "similarity_threshold": 0.7,
        "description": "Search browser watchlist setup",
        "monitor_text": True,
        "monitor_visual": False,
        "monitor_phonetic": False,
        "alert_frequency": "daily",
        "alert_email": False,
    }
    response = session.client.post("/api/v1/watchlist", json_data=payload)
    if response.status_code not in (200, 201):
        raise AssertionError(f"unexpected watchlist create status during setup: {response.status_code} -> {response.text[:200]}")
    item_id = response.json().get("id")
    if not item_id:
        raise AssertionError("watchlist setup create response missing id")
    return str(item_id)


def _perform_quick_search(page, browser_config, query: str) -> None:
    _open_search_tab(page, browser_config.timeout_ms)
    with page.expect_response(
        lambda response: response.request.method == "GET" and "/api/v1/search/quick" in response.url,
        timeout=browser_config.timeout_ms,
    ) as response_info:
        page.fill('input[name="trademark-search"]', query)
        page.press('input[name="trademark-search"]', "Enter")
    response = response_info.value
    if response.status != 200:
        raise AssertionError(f"unexpected quick-search status: {response.status}")

    page.wait_for_function(
        "() => document.body._x_dataStack && document.body._x_dataStack[0] && !document.body._x_dataStack[0].searchLoading",
        timeout=browser_config.timeout_ms,
    )
    state = _search_state(page)
    if state["searchError"]:
        raise AssertionError(f"unexpected quick-search error: {state['searchError']}")
    if state["resultsCount"] <= 0:
        raise AssertionError(f"expected quick-search results > 0, got {state['resultsCount']}")


def _expand_first_search_result(page, timeout_ms: int) -> None:
    page.wait_for_function(
        """() => {
            const state = document.body._x_dataStack && document.body._x_dataStack[0];
            if (!state || !Array.isArray(state.searchResults) || state.searchResults.length === 0) {
                return false;
            }
            state.expandedResult = 0;
            return true;
        }""",
        timeout=timeout_ms,
    )


def _result_watchlist_button(page):
    return page.get_by_role(
        "button",
        name=re.compile(r"Add to Watchlist|Takip Listesine Ekle|أضف إلى قائمة المراقبة"),
    ).first


def _visible_success_toast_text(page, expected_text: str | None = None) -> str:
    return page.evaluate(
        """expected => {
            const toasts = Array.from(document.querySelectorAll('body > div')).reverse();
            const toast = toasts.find((el) =>
                el.className.includes('bg-green-600') &&
                el.textContent &&
                (!expected || el.textContent.includes(expected))
            ) || toasts.find((el) =>
                el.className.includes('bg-green-600') &&
                el.textContent &&
                el.textContent.trim().length > 0
            );
            return toast ? toast.textContent.trim() : '';
        }""",
        expected_text,
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
            expected_limit = PLAN_FEATURES["free"]["max_daily_quick_searches"]
            if usage["limit"] != expected_limit:
                raise AssertionError(f"expected free quick-search limit {expected_limit}, got {usage['limit']}")
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

        reset_daily_quick_search_usage(
            REPORTER,
            free_session.user_id,
            name="RESET free quick-search usage before browser happy path",
        )
        _run_isolated_step(
            playwright,
            free_browser_config,
            "free quick search text browser journey",
            free_text_search,
        )
        reset_daily_quick_search_usage(
            REPORTER,
            free_session.user_id,
            name="RESET free quick-search usage after browser happy path",
        )

        def free_single_add_watchlist_toast(page, monitor) -> None:
            created_item_ids: list[str] = []
            try:
                _trim_watchlist_items(free_session, 0)
                _login_and_clear_monitor(page, free_browser_config, monitor)
                _perform_quick_search(page, free_browser_config, "wosen")
                _expand_first_search_result(page, free_browser_config.timeout_ms)

                expected_toast = page.evaluate("() => window.AppI18n.t('watchlist.added_toast')")
                add_button = _result_watchlist_button(page)
                add_button.wait_for(state="visible", timeout=free_browser_config.timeout_ms)

                with page.expect_response(
                    lambda response: response.request.method == "POST" and "/api/v1/watchlist" in response.url,
                    timeout=free_browser_config.timeout_ms,
                ) as response_info:
                    add_button.click()
                response = response_info.value
                if response.status not in (200, 201):
                    raise AssertionError(f"unexpected single-result watchlist create status: {response.status}")

                payload = response.json()
                item_id = payload.get("id")
                if not item_id:
                    raise AssertionError(f"expected created watchlist item id in response, got {payload}")
                created_item_ids.append(str(item_id))

                page.wait_for_function(
                    """expected => {
                        return Array.from(document.querySelectorAll('body > div')).some((el) =>
                            el.className.includes('bg-green-600') &&
                            el.textContent &&
                            el.textContent.includes(expected)
                        );
                    }""",
                    arg=expected_toast,
                    timeout=free_browser_config.timeout_ms,
                )
                toast_text = _visible_success_toast_text(page, expected_toast)
                if "watchlist." in toast_text:
                    raise AssertionError(f"expected localized watchlist add toast, got raw key toast {toast_text!r}")
                if expected_toast not in toast_text:
                    raise AssertionError(f"expected localized watchlist add toast {expected_toast!r}, got {toast_text!r}")
            finally:
                if created_item_ids:
                    _delete_watchlist_ids(free_session, created_item_ids)
                _trim_watchlist_items(free_session, 0)

        _run_isolated_step(
            playwright,
            free_browser_config,
            "free single-result watchlist add localized toast browser journey",
            free_single_add_watchlist_toast,
        )

        def free_daily_limit_gate(page, monitor) -> None:
            _login_and_clear_monitor(page, free_browser_config, monitor)
            usage = _usage_summary(page)
            expected_limit = PLAN_FEATURES["free"]["max_daily_quick_searches"]
            if usage["status"] != 200 or usage["limit"] != expected_limit:
                raise AssertionError(f"expected free usage summary limit {expected_limit}, got {usage}")

            _open_search_tab(page, free_browser_config.timeout_ms)
            page.fill('input[name="trademark-search"]', "wosen")

            for attempt in range(expected_limit + 1):
                with page.expect_response(
                    lambda response: response.request.method == "GET" and "/api/v1/search/quick" in response.url,
                    timeout=free_browser_config.timeout_ms,
                ) as response_info:
                    page.press('input[name="trademark-search"]', "Enter")
                response = response_info.value
                page.wait_for_function(
                    "() => document.body._x_dataStack && document.body._x_dataStack[0] && !document.body._x_dataStack[0].searchLoading",
                    timeout=free_browser_config.timeout_ms,
                )
                if attempt < expected_limit:
                    if response.status != 200:
                        raise AssertionError(f"expected 200 before free quick-search limit, got {response.status}")
                    state = _upgrade_modal_state(page)
                    if state["visible"]:
                        raise AssertionError("upgrade modal should stay hidden before the free quick-search limit is exhausted")
                    continue

                if response.status != 429:
                    raise AssertionError(f"expected 429 on free quick-search attempt {attempt + 1}, got {response.status}")

                page.locator("#upgrade-modal").wait_for(state="visible", timeout=free_browser_config.timeout_ms)
                state = _upgrade_modal_state(page)
                if not state["visible"] or state["recommendedPlan"] != "starter":
                    raise AssertionError(f"expected starter upgrade modal after free quick-search limit, got {state}")

        reset_daily_quick_search_usage(
            REPORTER,
            free_session.user_id,
            name="RESET free quick-search usage before browser limit gate",
        )
        _run_isolated_step(
            playwright,
            free_browser_config,
            "free quick search daily limit browser gate",
            free_daily_limit_gate,
            allow_console_errors=("status of 429",),
            allow_request_failures=(f"429 GET {CONFIG.base_url}/api/v1/search/quick",),
        )
        reset_daily_quick_search_usage(
            REPORTER,
            free_session.user_id,
            name="RESET free quick-search usage after browser limit gate",
        )

        def free_single_add_watchlist_limit_gate(page, monitor) -> None:
            filler_ids: list[str] = []
            try:
                _trim_watchlist_items(free_session, 0)
                _used, limit = _watchlist_usage(free_session)
                if limit <= 0:
                    raise AssertionError(f"expected a positive free watchlist limit, got {limit}")
                for index in range(limit):
                    filler_ids.append(_create_watchlist_item(free_session, f"{WATCHLIST_PREFIX} LIMIT {index} {uuid4().hex[:8].upper()}"))

                _login_and_clear_monitor(page, free_browser_config, monitor)
                _perform_quick_search(page, free_browser_config, "wosen")
                _expand_first_search_result(page, free_browser_config.timeout_ms)

                add_button = _result_watchlist_button(page)
                add_button.wait_for(state="visible", timeout=free_browser_config.timeout_ms)

                with page.expect_response(
                    lambda response: response.request.method == "POST" and "/api/v1/watchlist" in response.url,
                    timeout=free_browser_config.timeout_ms,
                ) as response_info:
                    add_button.click()
                response = response_info.value
                if response.status != 403:
                    raise AssertionError(f"expected single-result watchlist limit gate 403, got {response.status}")

                page.locator("#upgrade-modal").wait_for(state="visible", timeout=free_browser_config.timeout_ms)
                state = _upgrade_modal_state(page)
                if not state["visible"] or state["recommendedPlan"] != "starter":
                    raise AssertionError(f"expected starter upgrade modal after single-result watchlist limit, got {state}")
            finally:
                if filler_ids:
                    _delete_watchlist_ids(free_session, filler_ids)
                _trim_watchlist_items(free_session, 0)

        _run_isolated_step(
            playwright,
            free_browser_config,
            "free single-result watchlist limit browser gate",
            free_single_add_watchlist_limit_gate,
            allow_console_errors=("status of 403", "Failed to load resource: the server responded with a status of 403"),
            allow_request_failures=("/api/v1/watchlist",),
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
            plan_name = canonical_plan_name(usage["plan"])
            if plan_name not in PAID_PLANS:
                raise AssertionError(f"expected paid plan, got {usage['plan']}")
            expected_limit = PLAN_FEATURES[plan_name]["max_daily_quick_searches"]
            if usage["limit"] != expected_limit:
                raise AssertionError(f"expected paid quick-search limit {expected_limit} for {plan_name}, got {usage['limit']}")
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

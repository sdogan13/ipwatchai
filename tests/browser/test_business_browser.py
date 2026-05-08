"""
Browser journeys for business portfolio holder/attorney flows.

Run directly:
    python tests/browser/test_business_browser.py
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
    resolve_plan_persona_session,
)


CONFIG = load_browser_config()
REPORTER = LiveReporter()
BUSINESS_SESSION: PersonaSession | None = None
BUSINESS_RESOLVED = False

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_business_browser.py"
)


def ensure_business_session() -> PersonaSession | None:
    global BUSINESS_SESSION
    global BUSINESS_RESOLVED
    if BUSINESS_SESSION is None and not BUSINESS_RESOLVED:
        BUSINESS_RESOLVED = True
        BUSINESS_SESSION, _skipped = resolve_plan_persona_session(
            REPORTER,
            label="business browser user",
            email_env="TEST_BUSINESS_EMAIL",
            password_env="TEST_BUSINESS_PASSWORD",
            required_plans=BUSINESS_PLANS,
            fallback_to_default=False,
            provision_plan="professional",
        )
    return BUSINESS_SESSION


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


def _fetch_entity_seed(page, entity_type: str, query: str) -> dict:
    return page.evaluate(
        """async ({ entityType, query }) => {
            function authHeaders() {
                const token =
                    localStorage.getItem('auth_token') ||
                    localStorage.getItem('access_token') ||
                    sessionStorage.getItem('auth_token') ||
                    sessionStorage.getItem('access_token') ||
                    '';
                return token ? { Authorization: `Bearer ${token}` } : {};
            }

            const path = entityType === 'holder'
                ? `/api/v1/holders/search?query=${encodeURIComponent(query)}`
                : `/api/v1/attorneys/search?query=${encodeURIComponent(query)}`;
            const response = await fetch(path, { headers: authHeaders() });
            let payload = null;
            try {
                payload = await response.json();
            } catch (_error) {
                payload = null;
            }

            const results = payload && Array.isArray(payload.results) ? payload.results : [];
            const first = results[0] || {};
            return {
                status: response.status,
                resultsCount: results.length,
                id: entityType === 'holder' ? first.holder_tpe_client_id || '' : first.attorney_no || '',
                name: entityType === 'holder' ? first.holder_name || '' : first.attorney_name || '',
            };
        }""",
        {"entityType": entity_type, "query": query},
    )


def _open_entity_portfolio(page, entity_type: str, entity_id: str, entity_name: str) -> None:
    page.evaluate(
        """({ entityType, entityId, entityName }) => {
            if (entityType === 'holder') {
                window.showHolderPortfolio(entityId, entityName);
                return;
            }
            window.showAttorneyPortfolio(entityId, entityName);
        }""",
        {"entityType": entity_type, "entityId": entity_id, "entityName": entity_name},
    )


def _login_and_clear_monitor(page, browser_config, monitor) -> None:
    login_via_modal(page, browser_config, monitor)
    monitor.clear()


def _wait_for_entity_modal_loaded(page, timeout_ms: int) -> None:
    page.locator("#entityPortfolioModal").wait_for(state="visible", timeout=timeout_ms)
    page.wait_for_function(
        """() => {
            const loading = document.getElementById('entityPortfolioLoading');
            const results = document.getElementById('entityPortfolioResults');
            const error = document.getElementById('entityPortfolioError');
            const listItems = document.querySelectorAll('#entityTrademarksList > div').length;
            return !!(
                loading &&
                results &&
                error &&
                loading.classList.contains('hidden') &&
                !results.classList.contains('hidden') &&
                error.classList.contains('hidden') &&
                listItems > 0
            );
        }""",
        timeout=timeout_ms,
    )


def _read_entity_modal_contract(page) -> dict:
    return page.evaluate(
        """() => {
            const text = (selector) => {
                const el = document.querySelector(selector);
                return el ? el.textContent.trim() : '';
            };
            const list = document.getElementById('entityTrademarksList');
            return {
                title: text('#entityModalTitle'),
                subtitle: text('#entityModalSubtitle'),
                totalCount: text('#entityTotalCount'),
                registeredCount: text('#entityRegisteredCount'),
                pendingCount: text('#entityPendingCount'),
                itemCount: document.querySelectorAll('#entityTrademarksList > div').length,
                footerVisible: !document.getElementById('entityPortfolioFooter')?.classList.contains('hidden'),
                watchAllVisible: !!document.getElementById('entityWatchAllBtn'),
                csvVisible: !!document.getElementById('entityCsvBtn'),
                searchPlaceholder: document.getElementById('entitySearchInput')?.getAttribute('placeholder') || '',
                eventBadgeHelpersLoaded: (
                    typeof window.renderEventDerivedBadges === 'function'
                    && typeof window.renderLastEventLine === 'function'
                    && typeof window.renderHolderChangedBadge === 'function'
                    && typeof window.renderRestrictionBadge === 'function'
                ),
                holderChangedBadgeCount: list ? list.querySelectorAll('[data-event-badge="holder-changed"]').length : 0,
                restrictionBadgeCount: list ? list.querySelectorAll('[data-event-badge="restriction"]').length : 0,
                lastEventLineCount: list ? list.querySelectorAll('[data-event-line="last"]').length : 0,
            };
        }"""
    )


def _assert_entity_modal_contract(contract: dict, expected_id: str) -> None:
    if not contract["title"]:
        raise AssertionError("expected entity modal title to be populated")
    if not contract["subtitle"]:
        raise AssertionError("expected entity modal subtitle to be populated")
    if expected_id and expected_id not in contract["subtitle"]:
        raise AssertionError(f'expected modal subtitle to contain "{expected_id}", got "{contract["subtitle"]}"')
    if not contract["searchPlaceholder"]:
        raise AssertionError("expected entity modal search placeholder to be populated")
    if not contract["footerVisible"]:
        raise AssertionError("expected entity modal footer to be visible")
    if not contract["watchAllVisible"] or not contract["csvVisible"]:
        raise AssertionError("expected entity modal action buttons to be visible")
    if contract["itemCount"] <= 0:
        raise AssertionError("expected entity portfolio list to contain results")
    if not contract["eventBadgeHelpersLoaded"]:
        raise AssertionError("expected event-derived badge helpers to be loaded on the page")

    for label in ("totalCount", "registeredCount", "pendingCount"):
        if not str(contract[label]).isdigit():
            raise AssertionError(f'expected {label} to be numeric, got "{contract[label]}"')


def _exercise_entity_search(page, entity_type: str, query: str, timeout_ms: int) -> None:
    endpoint = "/api/v1/holders/search" if entity_type == "holder" else "/api/v1/attorneys/search"
    page.fill("#entitySearchInput", query)
    with page.expect_response(
        lambda response: response.request.method == "GET" and endpoint in response.url,
        timeout=timeout_ms,
    ) as search_response_info:
        page.click("#entitySearchBtn")
    search_response = search_response_info.value
    if search_response.status != 200:
        raise AssertionError(f"unexpected {entity_type} entity search status: {search_response.status}")

    page.locator("#entitySearchResults").wait_for(state="visible", timeout=timeout_ms)
    first_result = page.locator("#entitySearchResults div[onclick]").first
    first_result.wait_for(state="visible", timeout=timeout_ms)
    selected_label = first_result.locator("div.font-medium").text_content() or ""
    selected_id = ""
    if " (" in selected_label and selected_label.endswith(")"):
        selected_id = selected_label.rsplit(" (", 1)[1][:-1].strip()
    first_result.click()
    _wait_for_entity_modal_loaded(page, timeout_ms)
    contract = _read_entity_modal_contract(page)
    _assert_entity_modal_contract(contract, selected_id)


def _assert_entity_csv_fetch(page, entity_type: str, timeout_ms: int) -> None:
    segment = "/api/v1/holders/" if entity_type == "holder" else "/api/v1/attorneys/"
    with page.expect_response(
        lambda response: response.request.method == "GET"
        and segment in response.url
        and response.url.endswith("/trademarks/csv"),
        timeout=timeout_ms,
    ) as csv_response_info:
        page.click("#entityCsvBtn")
    csv_response = csv_response_info.value
    if csv_response.status != 200:
        raise AssertionError(f"unexpected {entity_type} csv status: {csv_response.status}")


def main() -> None:
    REPORTER.print_heading("BUSINESS BROWSER", server=CONFIG.base_url)

    session = ensure_business_session()
    if session is None:
        REPORTER.warn("business portfolio browser journeys -> skipped (no business persona available)")
        REPORTER.record("business portfolio browser journeys", True, "skipped: no business persona available")
        sys.exit(0)

    _ensure_email_verified(session)
    browser_config = with_live_credentials(CONFIG, session.config)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(playwright, browser_config)
        try:
            run_browser_step(
                "business browser login",
                REPORTER,
                page,
                monitor,
                browser_config,
                lambda: _login_and_clear_monitor(page, browser_config, monitor),
            )

            def holder_portfolio_journey() -> None:
                seed = _fetch_entity_seed(page, "holder", "te")
                if seed["status"] != 200 or not seed["id"] or not seed["name"]:
                    raise AssertionError(f"holder seed lookup failed: {seed}")

                _open_entity_portfolio(page, "holder", seed["id"], seed["name"])
                _wait_for_entity_modal_loaded(page, browser_config.timeout_ms)
                _assert_entity_modal_contract(_read_entity_modal_contract(page), seed["id"])
                _assert_entity_csv_fetch(page, "holder", browser_config.timeout_ms)
                _exercise_entity_search(page, "holder", "te", browser_config.timeout_ms)
                page.click('#entityPortfolioModal button[aria-label="Close"]')
                page.locator("#entityPortfolioModal").wait_for(state="hidden", timeout=browser_config.timeout_ms)

            run_browser_step(
                "business holder portfolio browser journey",
                REPORTER,
                page,
                monitor,
                browser_config,
                holder_portfolio_journey,
            )

            def attorney_portfolio_journey() -> None:
                seed = _fetch_entity_seed(page, "attorney", "pa")
                if seed["status"] != 200 or not seed["id"] or not seed["name"]:
                    raise AssertionError(f"attorney seed lookup failed: {seed}")

                _open_entity_portfolio(page, "attorney", seed["id"], seed["name"])
                _wait_for_entity_modal_loaded(page, browser_config.timeout_ms)
                _assert_entity_modal_contract(_read_entity_modal_contract(page), seed["id"])
                _assert_entity_csv_fetch(page, "attorney", browser_config.timeout_ms)
                _exercise_entity_search(page, "attorney", "pa", browser_config.timeout_ms)
                page.click('#entityPortfolioModal button[aria-label="Close"]')
                page.locator("#entityPortfolioModal").wait_for(state="hidden", timeout=browser_config.timeout_ms)

            run_browser_step(
                "business attorney portfolio browser journey",
                REPORTER,
                page,
                monitor,
                browser_config,
                attorney_portfolio_journey,
            )
        finally:
            context.close()
            browser.close()

    sys.exit(0 if REPORTER.summary("BUSINESS BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

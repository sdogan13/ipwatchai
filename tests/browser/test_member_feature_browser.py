"""
Deeper browser journeys for authenticated member features.

Run directly:
    python tests/browser/test_member_feature_browser.py
"""

from __future__ import annotations

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
from tests.live.helpers.cleanup import (
    cleanup_applications_by_prefix,
    cleanup_reports_by_prefix,
    cleanup_watchlist_items_by_prefix,
)
from tests.live.helpers.config import PNG_1X1
from tests.live.helpers.personas import (
    PAID_PLANS,
    PersonaSession,
    resolve_free_persona_session,
    resolve_plan_persona_session,
)


CONFIG = load_browser_config()
REPORTER = LiveReporter()
FREE_SESSION: PersonaSession | None = None
FREE_RESOLVED = False
PAID_SESSION: PersonaSession | None = None
PAID_RESOLVED = False

WATCHLIST_PREFIX = "BROWSER WL"
REPORT_PREFIX = "BROWSER REPORT"
APPLICATION_PREFIX = "BROWSER APP"

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_member_feature_browser.py"
)


def ensure_free_session() -> PersonaSession | None:
    global FREE_SESSION
    global FREE_RESOLVED
    if FREE_SESSION is None and not FREE_RESOLVED:
        FREE_RESOLVED = True
        FREE_SESSION = resolve_free_persona_session(REPORTER, label="member feature browser free user")
    return FREE_SESSION


def ensure_paid_session() -> PersonaSession | None:
    global PAID_SESSION
    global PAID_RESOLVED
    if PAID_SESSION is None and not PAID_RESOLVED:
        PAID_RESOLVED = True
        PAID_SESSION, _skipped = resolve_plan_persona_session(
            REPORTER,
            label="member feature browser paid user",
            email_env="TEST_PAID_EMAIL",
            password_env="TEST_PAID_PASSWORD",
            required_plans=PAID_PLANS,
            fallback_to_default=False,
            provision_plan="starter",
        )
    return PAID_SESSION


def _accept_next_dialog(page) -> None:
    page.once("dialog", lambda dialog: dialog.accept())


def _wait_for_text(page, selector: str, text: str, timeout_ms: int) -> None:
    page.wait_for_function(
        """
        ([targetSelector, targetText]) => {
            const el = document.querySelector(targetSelector);
            return !!(el && el.textContent && el.textContent.includes(targetText));
        }
        """,
        arg=[selector, text],
        timeout=timeout_ms,
    )


def _wait_for_text_absent(page, selector: str, text: str, timeout_ms: int) -> None:
    page.wait_for_function(
        """
        ([targetSelector, targetText]) => {
            const el = document.querySelector(targetSelector);
            return !el || !el.textContent || !el.textContent.includes(targetText);
        }
        """,
        arg=[selector, text],
        timeout=timeout_ms,
    )


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


def _close_report_generate_modal_if_open(page) -> None:
    modal = page.locator("#report-generate-modal")
    if not modal.count():
        return
    if not modal.is_visible():
        return

    page.evaluate(
        """() => {
            if (typeof hideReportGenerateModal === 'function') {
                hideReportGenerateModal();
                return;
            }
            const modal = document.getElementById('report-generate-modal');
            if (modal) {
                modal.style.display = 'none';
            }
        }"""
    )
    modal.wait_for(state="hidden")


def _close_upgrade_modal_if_open(page) -> None:
    modal = page.locator("#upgrade-modal")
    if not modal.count():
        return
    if not modal.is_visible():
        return

    page.evaluate(
        """() => {
            if (typeof hideUpgradeModal === 'function') {
                hideUpgradeModal();
            }
        }"""
    )
    modal.wait_for(state="hidden")


def _list_watchlist_items(session: PersonaSession, *, page_size: int = 100) -> list[dict]:
    response = session.client.get("/api/v1/watchlist", params={"page_size": page_size})
    if response.status_code != 200:
        raise AssertionError(f"unexpected watchlist list status: {response.status_code}")
    return response.json().get("items", [])


def _watchlist_usage(session: PersonaSession) -> tuple[int, int]:
    response = session.client.get("/api/v1/usage/summary")
    if response.status_code != 200:
        raise AssertionError(f"unexpected usage summary status: {response.status_code}")
    usage = (response.json().get("usage") or {}).get("watchlist_items") or {}
    return int(usage.get("used") or 0), int(usage.get("limit") or 0)


def _trim_watchlist_items(session: PersonaSession, target_used: int) -> None:
    items = _list_watchlist_items(session)
    while len(items) > target_used:
        item = items.pop()
        item_id = item.get("id")
        if not item_id:
            continue
        delete_response = session.client.delete(f"/api/v1/watchlist/{item_id}")
        if delete_response.status_code not in (200, 404):
            raise AssertionError(f"unexpected watchlist delete status while trimming: {delete_response.status_code}")


def _watchlist_item_ids(session: PersonaSession) -> set[str]:
    return {str(item.get("id")) for item in _list_watchlist_items(session) if item.get("id")}


def _delete_watchlist_ids(session: PersonaSession, item_ids: set[str]) -> None:
    for item_id in item_ids:
        delete_response = session.client.delete(f"/api/v1/watchlist/{item_id}")
        if delete_response.status_code not in (200, 404):
            raise AssertionError(f"unexpected watchlist delete status during cleanup: {delete_response.status_code}")


def _resolve_holder_with_bulk_overflow(
    session: PersonaSession,
    remaining_slots: int,
    *,
    minimum_candidate_items: int | None = None,
    maximum_candidate_items: int | None = None,
) -> tuple[str, str, int]:
    required_candidate_items = max(remaining_slots + 1, int(minimum_candidate_items or 0))
    minimum_total_count = max(remaining_slots + 1, required_candidate_items)

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT
                holder_tpe_client_id,
                MAX(holder_name) AS holder_name,
                COUNT(*) AS total_count
            FROM trademarks
            WHERE holder_tpe_client_id IS NOT NULL
              AND holder_tpe_client_id <> ''
              AND holder_name IS NOT NULL
              AND holder_name <> ''
            GROUP BY holder_tpe_client_id
            HAVING COUNT(*) >= %s
            ORDER BY COUNT(*) DESC, MAX(holder_name) ASC
            LIMIT 120
            """,
            (minimum_total_count,),
        )
        candidates = cur.fetchall()

    for holder in candidates:
        holder_id = holder.get("holder_tpe_client_id") or ""
        holder_name = holder.get("holder_name") or ""
        if not holder_id or not holder_name:
            continue

        preview = session.client.post("/api/v1/watchlist/portfolio-preview", json_data={"holder_id": holder_id})
        if preview.status_code != 200:
            continue

        preview_data = preview.json()
        total_items = int(preview_data.get("total_items") or 0)
        candidate_items = int(preview_data.get("can_add") or 0)
        if maximum_candidate_items is not None and candidate_items > maximum_candidate_items:
            continue
        if candidate_items >= required_candidate_items and candidate_items > remaining_slots:
            return holder_id, holder_name, total_items

    raise AssertionError(
        f"unable to find holder portfolio large enough to exceed the current watchlist capacity "
        f"and satisfy the requested candidate range ({required_candidate_items}"
        f"{'' if maximum_candidate_items is None else f'-{maximum_candidate_items}'})"
    )


def _open_bulk_watchlist_modal(page, holder_id: str, holder_name: str, total_items: int) -> None:
    page.evaluate(
        """({ entityId, entityName, totalCount }) => {
            window.dispatchEvent(new CustomEvent('open-bulk-watchlist', {
                detail: {
                    type: 'holder',
                    id: entityId,
                    name: entityName,
                    totalCount: totalCount
                }
            }));
        }""",
        {"entityId": holder_id, "entityName": holder_name, "totalCount": total_items},
    )


def _open_holder_portfolio(page, holder_id: str, holder_name: str) -> None:
    page.evaluate(
        """({ entityId, entityName }) => {
            window.showHolderPortfolio(entityId, entityName);
        }""",
        {"entityId": holder_id, "entityName": holder_name},
    )


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


def _exercise_bulk_watchlist_capacity_aware_recommendation(
    page,
    browser_config,
    session: PersonaSession,
    expected_plan: str,
    *,
    target_used: int,
    minimum_candidate_items: int,
) -> None:
    _close_upgrade_modal_if_open(page)

    _trim_watchlist_items(session, target_used)
    used, limit = _watchlist_usage(session)
    remaining_slots = max(0, limit - used)

    holder_id, holder_name, total_items = _resolve_holder_with_bulk_overflow(
        session,
        remaining_slots,
        minimum_candidate_items=minimum_candidate_items,
        maximum_candidate_items=999,
    )

    try:
        _open_bulk_watchlist_modal(page, holder_id, holder_name, total_items)
        page.locator("#bulk-watchlist-modal").wait_for(state="visible", timeout=browser_config.timeout_ms)
        page.wait_for_function(
            """() => {
                const modal = document.getElementById('bulk-watchlist-modal');
                const state = modal && modal._x_dataStack ? modal._x_dataStack[0] : null;
                return !!(state && !state.loading && state.upgradeOffer);
            }""",
            timeout=browser_config.timeout_ms,
        )
        page.locator("#bulk-upgrade-offer").wait_for(state="visible", timeout=browser_config.timeout_ms)

        state = page.evaluate(
            """() => {
                const modal = document.getElementById('bulk-watchlist-modal');
                const data = modal && modal._x_dataStack ? modal._x_dataStack[0] : null;
                return data ? {
                    usageUsed: data.usageUsed,
                    usageLimit: data.usageLimit,
                    totalItems: data.totalItems,
                    alreadyWatchlisted: data.alreadyWatchlisted,
                    canAdd: data.canAdd,
                    cannotAdd: data.cannotAdd,
                    requiredCapacity: typeof data.requiredWatchlistCapacity === 'function' ? data.requiredWatchlistCapacity() : null,
                    recommendedPlan: data.upgradeOffer ? data.upgradeOffer.recommendedPlan : '',
                    checkoutUrl: data.upgradeOffer ? data.upgradeOffer.checkoutUrl : ''
                } : null;
            }"""
        )
        if not state:
            raise AssertionError("expected bulk watchlist modal Alpine state for capacity-aware recommendation check")
        eligible_total = state["totalItems"] - state["alreadyWatchlisted"]
        if eligible_total < minimum_candidate_items:
            raise AssertionError(
                f"expected holder portfolio to expose at least {minimum_candidate_items} addable items, got {state}"
            )
        if state["requiredCapacity"] != state["usageUsed"] + eligible_total:
            raise AssertionError(f"expected bulk modal required capacity to reflect current usage plus addable items, got {state}")
        if state["recommendedPlan"] != expected_plan:
            raise AssertionError(
                f"expected {expected_plan} capacity-aware recommendation for bulk watchlist gate, got {state['recommendedPlan']!r}"
            )
        if not state["checkoutUrl"] or f"plan={expected_plan}" not in state["checkoutUrl"]:
            raise AssertionError(
                f"expected inline bulk upgrade CTA to target {expected_plan} checkout, got {state['checkoutUrl']!r}"
            )
    finally:
        if page.locator("#bulk-watchlist-modal").count() and page.locator("#bulk-watchlist-modal").is_visible():
            page.evaluate(
                """() => {
                    const modal = document.getElementById('bulk-watchlist-modal');
                    const state = modal && modal._x_dataStack ? modal._x_dataStack[0] : null;
                    if (state) state.open = false;
                }"""
            )
            page.locator("#bulk-watchlist-modal").wait_for(state="hidden", timeout=browser_config.timeout_ms)
        _close_upgrade_modal_if_open(page)


def _exercise_bulk_watchlist_inline_upgrade(page, browser_config, session: PersonaSession, expected_plan: str) -> None:
    _close_upgrade_modal_if_open(page)

    _trim_watchlist_items(session, 1)
    used, limit = _watchlist_usage(session)
    remaining_slots = max(0, limit - used)
    if remaining_slots <= 0:
        raise AssertionError("expected at least one remaining watchlist slot before bulk modal test")

    holder_id, holder_name, _total_items = _resolve_holder_with_bulk_overflow(
        session,
        remaining_slots,
        maximum_candidate_items=999,
    )
    before_ids = _watchlist_item_ids(session)

    try:
        _open_holder_portfolio(page, holder_id, holder_name)
        _wait_for_entity_modal_loaded(page, browser_config.timeout_ms)

        page.click("#entityWatchAllBtn")
        page.locator("#bulk-watchlist-modal").wait_for(state="visible", timeout=browser_config.timeout_ms)
        page.locator("#bulk-upgrade-offer").wait_for(state="visible", timeout=browser_config.timeout_ms)

        initial_state = page.evaluate(
            """() => {
                const modal = document.getElementById('bulk-watchlist-modal');
                const state = modal && modal._x_dataStack ? modal._x_dataStack[0] : null;
                const sharedUpgrade = document.getElementById('upgrade-modal');
                return state ? {
                    canAdd: state.canAdd,
                    cannotAdd: state.cannotAdd,
                    recommendedPlan: state.upgradeOffer ? state.upgradeOffer.recommendedPlan : '',
                    checkoutUrl: state.upgradeOffer ? state.upgradeOffer.checkoutUrl : '',
                    sharedUpgradeVisible: !!(sharedUpgrade && !sharedUpgrade.classList.contains('hidden'))
                } : null;
            }"""
        )
        if not initial_state:
            raise AssertionError("expected bulk watchlist modal Alpine state")
        if initial_state["cannotAdd"] <= 0:
            raise AssertionError(f"expected bulk modal to expose blocked items, got {initial_state}")
        if initial_state["recommendedPlan"] != expected_plan:
            raise AssertionError(
                f"expected {expected_plan} inline recommendation for bulk watchlist gate, got {initial_state['recommendedPlan']!r}"
            )
        if not initial_state["checkoutUrl"] or f"plan={expected_plan}" not in initial_state["checkoutUrl"]:
            raise AssertionError(
                f"expected inline upgrade CTA to target {expected_plan} checkout, got {initial_state['checkoutUrl']!r}"
            )
        if initial_state["sharedUpgradeVisible"]:
            raise AssertionError("shared upgrade modal should stay hidden while bulk inline upgrade card is visible")

        with page.expect_response(
            lambda response: response.request.method == "POST"
            and "/api/v1/watchlist/bulk-from-portfolio" in response.url,
            timeout=browser_config.timeout_ms,
        ) as bulk_response_info:
            page.click("#bulk-watchlist-confirm-btn")
        bulk_response = bulk_response_info.value
        if bulk_response.status != 200:
            raise AssertionError(f"unexpected bulk watchlist import status: {bulk_response.status}")

        page.locator("#bulk-watchlist-modal").wait_for(state="visible", timeout=browser_config.timeout_ms)
        page.locator("#bulk-upgrade-offer").wait_for(state="visible", timeout=browser_config.timeout_ms)

        post_submit_state = page.evaluate(
            """() => {
                const modal = document.getElementById('bulk-watchlist-modal');
                const state = modal && modal._x_dataStack ? modal._x_dataStack[0] : null;
                const sharedUpgrade = document.getElementById('upgrade-modal');
                const notice = document.getElementById('bulk-watchlist-notice');
                return state ? {
                    canAdd: state.canAdd,
                    cannotAdd: state.cannotAdd,
                    notice: state.notice || '',
                    recommendedPlan: state.upgradeOffer ? state.upgradeOffer.recommendedPlan : '',
                    sharedUpgradeVisible: !!(sharedUpgrade && !sharedUpgrade.classList.contains('hidden')),
                    noticeVisible: !!(notice && notice.offsetParent !== null)
                } : null;
            }"""
        )
        if not post_submit_state:
            raise AssertionError("expected post-submit bulk modal Alpine state")
        if post_submit_state["canAdd"] != 0:
            raise AssertionError(f"expected inline bulk modal to disable further adds after reaching the limit, got {post_submit_state}")
        if post_submit_state["cannotAdd"] <= 0:
            raise AssertionError(f"expected remaining blocked items after partial bulk add, got {post_submit_state}")
        if post_submit_state["recommendedPlan"] != expected_plan:
            raise AssertionError(
                f"expected {expected_plan} inline recommendation after partial bulk add, got {post_submit_state['recommendedPlan']!r}"
            )
        if not post_submit_state["noticeVisible"] or not post_submit_state["notice"]:
            raise AssertionError("expected inline bulk limit notice after partial bulk add")
        if post_submit_state["sharedUpgradeVisible"]:
            raise AssertionError("shared upgrade modal should not open for the bulk watchlist limit flow")
    finally:
        new_ids = _watchlist_item_ids(session) - before_ids
        if new_ids:
            _delete_watchlist_ids(session, new_ids)
        if page.locator("#bulk-watchlist-modal").count() and page.locator("#bulk-watchlist-modal").is_visible():
            page.evaluate(
                """() => {
                    const modal = document.getElementById('bulk-watchlist-modal');
                    const state = modal && modal._x_dataStack ? modal._x_dataStack[0] : null;
                    if (state) state.open = false;
                }"""
            )
            page.locator("#bulk-watchlist-modal").wait_for(state="hidden", timeout=browser_config.timeout_ms)
        if page.locator("#entityPortfolioModal").count() and page.locator("#entityPortfolioModal").is_visible():
            page.click('#entityPortfolioModal button[aria-label="Close"]')
            page.locator("#entityPortfolioModal").wait_for(state="hidden", timeout=browser_config.timeout_ms)
        _close_upgrade_modal_if_open(page)


def _run_free_persona_flows(playwright, session: PersonaSession) -> None:
    _ensure_email_verified(session)
    browser_config = with_live_credentials(CONFIG, session.config)
    browser, context, page, monitor = launch_browser_page(playwright, browser_config)
    watchlist_brand = f"{WATCHLIST_PREFIX} {uuid4().hex[:8].upper()}"
    watchlist_description = f"{watchlist_brand} created"
    watchlist_updated_description = f"{watchlist_brand} edited"
    report_title = f"{REPORT_PREFIX} {uuid4().hex[:8].upper()}"

    try:
        run_browser_step(
            "free member feature login",
            REPORTER,
            page,
            monitor,
            browser_config,
            lambda: _login_and_clear_monitor(page, browser_config, monitor),
        )

        run_browser_step(
            "free member bulk watchlist capacity-aware upgrade recommendation",
            REPORTER,
            page,
            monitor,
            browser_config,
            lambda: _exercise_bulk_watchlist_capacity_aware_recommendation(
                page,
                browser_config,
                session,
                "professional",
                target_used=0,
                minimum_candidate_items=16,
            ),
        )

        def watchlist_crud() -> None:
            page.click("#tab-btn-watchlist")
            page.locator("#tab-content-watchlist").wait_for(state="visible")

            page.locator('#tab-content-watchlist button[onclick="openQuickWatchlistAdd({})"]').click()
            page.locator('input[x-model="formData.brand_name"]').wait_for(state="visible")
            page.fill('input[x-model="formData.brand_name"]', watchlist_brand)
            page.fill('input[x-model="classesInput"]', "9, 35")
            page.fill('input[x-model="formData.description"]', watchlist_description)

            with page.expect_response(
                lambda response: response.request.method == "POST" and "/api/v1/watchlist" in response.url,
                timeout=browser_config.timeout_ms,
            ) as create_response_info:
                page.locator('div[x-show="open"] button.bg-blue-600').first.click()
            create_response = create_response_info.value
            if create_response.status not in (200, 201):
                raise AssertionError(f"unexpected watchlist create status: {create_response.status}")

            page.locator('input[x-model="formData.brand_name"]').wait_for(state="hidden")

            with page.expect_response(
                lambda response: response.request.method == "GET"
                and "/api/v1/watchlist?" in response.url
                and "search=" in response.url,
                timeout=browser_config.timeout_ms,
            ):
                page.fill("#wl-search-input", watchlist_brand)
                page.wait_for_timeout(450)

            _wait_for_text(page, "#portfolio-grid", watchlist_brand, browser_config.timeout_ms)
            _wait_for_text(page, "#portfolio-grid", watchlist_description, browser_config.timeout_ms)

            card = page.locator("#portfolio-grid .card-base").filter(has_text=watchlist_brand).first
            card.locator("button").nth(1).click()
            page.locator("#watchlist-edit-modal").wait_for(state="visible")
            page.fill("#edit-wl-description", watchlist_updated_description)

            with page.expect_response(
                lambda response: response.request.method == "PUT" and "/api/v1/watchlist/" in response.url,
                timeout=browser_config.timeout_ms,
            ) as update_response_info:
                page.click("#edit-wl-submit-btn")
            update_response = update_response_info.value
            if update_response.status != 200:
                raise AssertionError(f"unexpected watchlist update status: {update_response.status}")

            page.locator("#watchlist-edit-modal").wait_for(state="hidden")
            _wait_for_text(page, "#portfolio-grid", watchlist_updated_description, browser_config.timeout_ms)

            card = page.locator("#portfolio-grid .card-base").filter(has_text=watchlist_brand).first
            _accept_next_dialog(page)
            with page.expect_response(
                lambda response: response.request.method == "DELETE" and "/api/v1/watchlist/" in response.url,
                timeout=browser_config.timeout_ms,
            ) as delete_response_info:
                card.locator("button").nth(2).click()
            delete_response = delete_response_info.value
            if delete_response.status != 200:
                raise AssertionError(f"unexpected watchlist delete status: {delete_response.status}")

            _wait_for_text_absent(page, "#portfolio-grid", watchlist_brand, browser_config.timeout_ms)

        run_browser_step(
            "free member watchlist CRUD browser journey",
            REPORTER,
            page,
            monitor,
            browser_config,
            watchlist_crud,
        )

        def report_generation() -> None:
            page.click("#tab-btn-reports")
            page.locator("#tab-content-reports").wait_for(state="visible")
            page.locator("#reports-list").wait_for(state="attached")
            page.locator('#tab-content-reports button[onclick="showReportGenerateModal()"]').click()
            page.locator("#report-generate-modal").wait_for(state="visible")
            page.fill("#reportTitleInput", report_title)

            with page.expect_response(
                lambda response: response.request.method == "POST" and "/api/v1/reports/generate" in response.url,
                timeout=browser_config.timeout_ms,
            ) as generate_response_info:
                page.click("#reportSubmitBtn")
            generate_response = generate_response_info.value
            if generate_response.status != 200:
                raise AssertionError(f"unexpected report generate status: {generate_response.status}")

            page.locator("#report-generate-modal").wait_for(state="hidden")
            _wait_for_text(page, "#reports-list", report_title, browser_config.timeout_ms)

        run_browser_step(
            "free member report generation browser journey",
            REPORTER,
            page,
            monitor,
            browser_config,
            report_generation,
        )

        def free_application_gate() -> None:
            _close_report_generate_modal_if_open(page)
            page.click("#tab-btn-applications")
            page.locator("#tab-content-applications").wait_for(state="visible")
            page.locator("#applications-list-view").wait_for(state="visible")
            page.locator('#tab-content-applications button[onclick="showApplicationForm()"]').first.click()
            page.locator("#applications-form-view").wait_for(state="visible")
            page.fill("#app-brand-name", f"{APPLICATION_PREFIX} FREE {uuid4().hex[:8].upper()}")
            page.locator("#app-nice-class-select option[value='25']").wait_for(state="attached")
            page.select_option("#app-nice-class-select", "25")
            page.locator('button[onclick="addAppNiceClass()"]').click()
            page.fill("#app-goods-services", "Clothing and footwear")

            with page.expect_response(
                lambda response: response.request.method == "POST"
                and "/api/v1/applications/" in response.url
                and not response.url.endswith("/logo"),
                timeout=browser_config.timeout_ms,
            ) as create_response_info:
                page.click("#app-btn-save-draft")
            create_response = create_response_info.value
            if create_response.status != 403:
                raise AssertionError(f"expected free application gate 403, got {create_response.status}")

            page.locator("#applications-form-view").wait_for(state="visible")
            page.locator("#upgrade-modal").wait_for(state="visible", timeout=browser_config.timeout_ms)
            recommended_plan = (page.locator("#upgrade-plan-code").text_content() or "").strip().lower()
            if recommended_plan != "starter":
                raise AssertionError(f"expected starter recommendation for free application gate, got {recommended_plan!r}")

        run_browser_step(
            "free member application gate browser journey",
            REPORTER,
            page,
            monitor,
            browser_config,
            free_application_gate,
            allow_console_errors=("status of 403", "Failed to load resource: the server responded with a status of 403"),
            allow_request_failures=("/api/v1/applications/",),
        )

        def profile_and_avatar() -> None:
            _close_report_generate_modal_if_open(page)
            _close_upgrade_modal_if_open(page)
            user_menu = page.locator('div[x-data*="userMenuOpen"]').first
            user_menu.locator("> button").click()
            user_menu.locator("button").nth(1).click()
            page.locator('div[x-show="showProfileModal"]').wait_for(state="visible")

            updated_phone = f"+44 77{uuid4().hex[:8]}"
            updated_title = f"Browser Title {uuid4().hex[:6]}"
            page.fill('input[x-model="profileData.phone"]', updated_phone)
            page.fill('input[x-model="profileData.title"]', updated_title)

            with page.expect_response(
                lambda response: response.request.method == "PUT" and "/api/v1/user/profile" in response.url,
                timeout=browser_config.timeout_ms,
            ) as profile_response_info:
                page.locator('button[x-text="profileSaving ? t(\'profile.saving\') : t(\'profile.save\')"]').click()
            profile_response = profile_response_info.value
            if profile_response.status != 200:
                raise AssertionError(f"unexpected profile save status: {profile_response.status}")

            page.wait_for_function(
                """() => {
                    const state = document.body._x_dataStack && document.body._x_dataStack[0];
                    return !!(state && state.profileMessageType === 'success');
                }""",
                timeout=browser_config.timeout_ms,
            )

            avatar_input = page.locator('input[x-ref="avatarFileInput"]')
            with page.expect_response(
                lambda response: response.request.method == "POST" and "/api/v1/user/avatar" in response.url,
                timeout=browser_config.timeout_ms,
            ) as avatar_response_info:
                avatar_input.set_input_files(
                    [{"name": "browser-avatar.png", "mimeType": "image/png", "buffer": PNG_1X1}]
                )
            avatar_response = avatar_response_info.value
            if avatar_response.status != 200:
                raise AssertionError(f"unexpected avatar upload status: {avatar_response.status}")

            page.wait_for_function(
                """() => {
                    const state = document.body._x_dataStack && document.body._x_dataStack[0];
                    return !!(state && state.profileAvatar && state.profileAvatar.includes('/static/avatars/'));
                }""",
                timeout=browser_config.timeout_ms,
            )

        run_browser_step(
            "free member profile and avatar browser journey",
            REPORTER,
            page,
            monitor,
            browser_config,
            profile_and_avatar,
        )
    finally:
        context.close()
        browser.close()
        cleanup_watchlist_items_by_prefix(session.client, REPORTER, WATCHLIST_PREFIX)
        cleanup_reports_by_prefix(REPORTER, session.organization_id, REPORT_PREFIX)


def _run_paid_persona_flows(playwright, session: PersonaSession | None) -> None:
    name = "paid member application create/edit/delete browser journey"
    if session is None:
        REPORTER.warn(f"{name} -> skipped (no paid persona available)")
        REPORTER.record(name, True, "skipped: no paid persona available")
        return

    _ensure_email_verified(session)
    browser_config = with_live_credentials(CONFIG, session.config)
    browser, context, page, monitor = launch_browser_page(playwright, browser_config)
    application_brand = f"{APPLICATION_PREFIX} {uuid4().hex[:8].upper()}"
    application_updated_brand = f"{application_brand} EDITED"

    try:
        run_browser_step(
            "paid member feature login",
            REPORTER,
            page,
            monitor,
            browser_config,
            lambda: _login_and_clear_monitor(page, browser_config, monitor),
        )

        run_browser_step(
            "starter member bulk watchlist inline upgrade browser journey",
            REPORTER,
            page,
            monitor,
            browser_config,
            lambda: _exercise_bulk_watchlist_inline_upgrade(page, browser_config, session, "professional"),
        )

        def application_crud() -> None:
            page.click("#tab-btn-applications")
            page.locator("#tab-content-applications").wait_for(state="visible")
            page.locator("#applications-list-view").wait_for(state="visible")
            page.locator('#tab-content-applications button[onclick="showApplicationForm()"]').first.click()

            page.locator("#applications-form-view").wait_for(state="visible")
            page.fill("#app-brand-name", application_brand)
            page.locator("#app-nice-class-select option[value='25']").wait_for(state="attached")
            page.select_option("#app-nice-class-select", "25")
            page.locator('button[onclick="addAppNiceClass()"]').click()
            page.fill("#app-goods-services", "Clothing and footwear")

            with page.expect_response(
                lambda response: response.request.method == "POST"
                and "/api/v1/applications/" in response.url
                and not response.url.endswith("/logo"),
                timeout=browser_config.timeout_ms,
            ) as create_response_info:
                page.click("#app-btn-save-draft")
            create_response = create_response_info.value
            if create_response.status != 200:
                raise AssertionError(f"unexpected application create status: {create_response.status}")

            page.locator("#applications-list-view").wait_for(state="visible")
            _wait_for_text(page, "#applications-list", application_brand, browser_config.timeout_ms)

            card = page.locator("#applications-list > div").filter(has_text=application_brand).first
            card.locator("button").nth(0).click()
            page.locator("#applications-form-view").wait_for(state="visible")
            page.fill("#app-brand-name", application_updated_brand)

            with page.expect_response(
                lambda response: response.request.method == "PUT" and "/api/v1/applications/" in response.url,
                timeout=browser_config.timeout_ms,
            ) as update_response_info:
                page.click("#app-btn-save-draft")
            update_response = update_response_info.value
            if update_response.status != 200:
                raise AssertionError(f"unexpected application update status: {update_response.status}")

            page.locator("#applications-list-view").wait_for(state="visible")
            _wait_for_text(page, "#applications-list", application_updated_brand, browser_config.timeout_ms)

            card = page.locator("#applications-list > div").filter(has_text=application_updated_brand).first
            _accept_next_dialog(page)
            with page.expect_response(
                lambda response: response.request.method == "DELETE" and "/api/v1/applications/" in response.url,
                timeout=browser_config.timeout_ms,
            ) as delete_response_info:
                card.locator("button").nth(1).click()
            delete_response = delete_response_info.value
            if delete_response.status != 200:
                raise AssertionError(f"unexpected application delete status: {delete_response.status}")

            _wait_for_text_absent(page, "#applications-list", application_updated_brand, browser_config.timeout_ms)

        run_browser_step(
            name,
            REPORTER,
            page,
            monitor,
            browser_config,
            application_crud,
        )
    finally:
        context.close()
        browser.close()
        cleanup_applications_by_prefix(session.client, REPORTER, APPLICATION_PREFIX)


def main() -> None:
    REPORTER.print_heading("MEMBER FEATURE BROWSER", server=CONFIG.base_url)

    free_session = ensure_free_session()
    paid_session = ensure_paid_session()

    with sync_playwright() as playwright:
        if free_session is not None:
            _run_free_persona_flows(playwright, free_session)
        if free_session is None and not any(not result["passed"] for result in REPORTER.results):
            REPORTER.warn("free member feature browser -> skipped (no free persona available)")
            REPORTER.record("free member feature browser", True, "skipped: no free persona available")

        _run_paid_persona_flows(playwright, paid_session)

    sys.exit(0 if REPORTER.summary("MEMBER FEATURE BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

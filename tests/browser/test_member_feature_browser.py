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

"""
Browser journeys for watchlist logo upload, rendering, and cleanup.

Run directly:
    python tests/browser/test_watchlist_assets_browser.py
"""

from __future__ import annotations

import time
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
from tests.live.helpers.cleanup import cleanup_watchlist_items_by_prefix
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

FREE_PREFIX = "BROWSER ASSET FREE WL"
PAID_PREFIX = "BROWSER ASSET PAID WL"

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_watchlist_assets_browser.py"
)


class _QuietAttemptReporter(LiveReporter):
    def _emit(self, msg: str) -> None:  # pragma: no cover - keeps retry attempts quiet
        return


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


def ensure_free_session() -> PersonaSession | None:
    global FREE_SESSION
    global FREE_RESOLVED
    if FREE_SESSION is None and not FREE_RESOLVED:
        FREE_RESOLVED = True
        FREE_SESSION = resolve_free_persona_session(REPORTER, label="watchlist assets browser free user")
    return FREE_SESSION


def ensure_paid_session() -> tuple[PersonaSession | None, bool]:
    global PAID_SESSION
    global PAID_RESOLVED
    if PAID_SESSION is None and not PAID_RESOLVED:
        PAID_RESOLVED = True
        PAID_SESSION, skipped = resolve_plan_persona_session(
            REPORTER,
            label="watchlist assets browser paid user",
            email_env="TEST_PAID_EMAIL",
            password_env="TEST_PAID_PASSWORD",
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


def _create_watchlist_item(session: PersonaSession, prefix: str, *, monitor_visual: bool) -> tuple[str, str]:
    brand_name = f"{prefix} {uuid4().hex[:8].upper()}"
    payload = {
        "brand_name": brand_name,
        "nice_class_numbers": [9, 35],
        "similarity_threshold": 0.75,
        "description": "Browser asset test item",
        "monitor_text": True,
        "monitor_visual": monitor_visual,
        "monitor_phonetic": False,
        "alert_frequency": "daily",
        "alert_email": False,
    }
    response = session.client.post("/api/v1/watchlist", json_data=payload)
    if response.status_code not in (200, 201):
        raise AssertionError(f"unexpected watchlist create status: {response.status_code}")

    data = response.json()
    item_id = data.get("id")
    if not item_id:
        raise AssertionError("watchlist create response missing id")
    return item_id, brand_name


def _open_watchlist_card(page, browser_config, brand_name: str):
    page.click("#tab-btn-watchlist")
    page.locator("#tab-content-watchlist").wait_for(state="visible")
    page.fill("#wl-search-input", brand_name)
    page.wait_for_timeout(450)
    card = page.locator("#portfolio-grid .card-base").filter(has_text=brand_name).first
    card.wait_for(state="visible", timeout=browser_config.timeout_ms)
    return card


def _wait_for_logo_rendered(page, browser_config, brand_name: str, item_id: str) -> None:
    deadline = time.time() + (browser_config.timeout_ms / 1000.0)
    while time.time() < deadline:
        _open_watchlist_card(page, browser_config, brand_name)
        rendered = page.evaluate(
            """
            ([targetBrand, targetItemId]) => {
                const cards = Array.from(document.querySelectorAll('#portfolio-grid .card-base'));
                const card = cards.find((node) => node.textContent && node.textContent.includes(targetBrand));
                if (!card) return false;
                const img = card.querySelector(`img[src*="/api/v1/watchlist/${targetItemId}/logo"]`);
                const deleteButton = card.querySelector(`button[onclick*="deleteWatchlistLogo('${targetItemId}')"]`);
                return !!((img && img.complete && img.naturalWidth > 0) || deleteButton);
            }
            """,
            [brand_name, item_id],
        )
        if rendered:
            return
        page.wait_for_timeout(1000)
    raise AssertionError("timed out waiting for uploaded watchlist logo to render")


def _wait_for_logo_absent(page, browser_config, brand_name: str, item_id: str) -> None:
    deadline = time.time() + (browser_config.timeout_ms / 1000.0)
    while time.time() < deadline:
        _open_watchlist_card(page, browser_config, brand_name)
        absent = page.evaluate(
            """
            ([targetBrand, targetItemId]) => {
                const cards = Array.from(document.querySelectorAll('#portfolio-grid .card-base'));
                const card = cards.find((node) => node.textContent && node.textContent.includes(targetBrand));
                if (!card) return false;
                const img = card.querySelector(`img[src*="/api/v1/watchlist/${targetItemId}/logo"]`);
                const deleteButton = card.querySelector(`button[onclick*="deleteWatchlistLogo('${targetItemId}')"]`);
                return !img && !deleteButton;
            }
            """,
            [brand_name, item_id],
        )
        if absent:
            return
        page.wait_for_timeout(1000)
    raise AssertionError("timed out waiting for watchlist logo removal to reach the UI")


def _assert_logo_fetch(session: PersonaSession, item_id: str, expected_status: int) -> None:
    response = session.client.get(f"/api/v1/watchlist/{item_id}/logo", token=False)
    if response.status_code != expected_status:
        raise AssertionError(f"unexpected logo fetch status: {response.status_code}")
    if expected_status == 200 and response.content != PNG_1X1:
        raise AssertionError("watchlist logo fetch content did not match uploaded image")


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


def _run_isolated_step_with_retry(
    playwright,
    browser_config,
    name: str,
    action,
    *,
    max_attempts: int = 2,
    retry_delay_seconds: float = 15.0,
    **kwargs,
) -> None:
    last_detail = ""
    for attempt in range(1, max_attempts + 1):
        browser, context, page, monitor = launch_browser_page(playwright, browser_config)
        try:
            attempt_reporter = _QuietAttemptReporter()
            passed = run_browser_step(
                name,
                attempt_reporter,
                page,
                monitor,
                browser_config,
                lambda: action(page, monitor),
                **kwargs,
            )
            result = attempt_reporter.results[-1] if attempt_reporter.results else {
                "passed": passed,
                "detail": "",
            }
        finally:
            context.close()
            browser.close()

        if result["passed"]:
            if attempt > 1:
                REPORTER.info(f"{name} -> recovered after transient 429 retry")
            REPORTER.ok(name)
            REPORTER.record(name, True)
            return

        last_detail = result.get("detail", "")
        if "429" in last_detail and attempt < max_attempts:
            REPORTER.warn(
                f"{name} -> transient 429 throttle, retrying in {int(retry_delay_seconds)}s "
                f"(attempt {attempt}/{max_attempts})"
            )
            time.sleep(retry_delay_seconds)
            continue

        REPORTER.fail(f"{name} -> {last_detail.split(' | screenshot=')[0]}")
        REPORTER.record(name, False, last_detail)
        return


def main() -> None:
    REPORTER.print_heading("WATCHLIST ASSETS BROWSER", server=CONFIG.base_url)

    free_session = ensure_free_session()
    if free_session is None:
        sys.exit(1)

    _ensure_email_verified(free_session)
    cleanup_watchlist_items_by_prefix(free_session.client, REPORTER, FREE_PREFIX)
    free_item_id, free_brand = _create_watchlist_item(free_session, FREE_PREFIX, monitor_visual=False)

    paid_session = None
    paid_skipped = False
    paid_item_id = None
    paid_brand = None

    try:
        paid_session, paid_skipped = ensure_paid_session()
        if paid_session is not None:
            _ensure_email_verified(paid_session)
            cleanup_watchlist_items_by_prefix(paid_session.client, REPORTER, PAID_PREFIX)
            paid_item_id, paid_brand = _create_watchlist_item(paid_session, PAID_PREFIX, monitor_visual=True)

        with sync_playwright() as playwright:
            free_browser_config = with_live_credentials(CONFIG, free_session.config)

            def free_logo_plan_gate(page, monitor) -> None:
                login_via_modal(page, free_browser_config, monitor)
                card = _open_watchlist_card(page, free_browser_config, free_brand)
                upload_input = card.locator('input[type="file"]').first
                with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and f"/api/v1/watchlist/{free_item_id}/logo" in response.url,
                    timeout=free_browser_config.timeout_ms,
                ) as upload_response_info:
                    upload_input.set_input_files(
                        [{"name": "watchlist-logo.png", "mimeType": "image/png", "buffer": PNG_1X1}]
                    )
                upload_response = upload_response_info.value
                if upload_response.status != 403:
                    raise AssertionError(f"expected free logo upload gate 403, got {upload_response.status}")
                page.locator("#upgrade-modal").wait_for(state="visible", timeout=free_browser_config.timeout_ms)
                recommended_plan = (page.locator("#upgrade-plan-code").text_content() or "").strip().lower()
                if recommended_plan != "starter":
                    raise AssertionError(f"expected starter recommendation for free watchlist logo gate, got {recommended_plan!r}")
                _close_upgrade_modal_if_open(page)
                _wait_for_logo_absent(page, free_browser_config, free_brand, free_item_id)
                _assert_logo_fetch(free_session, free_item_id, 404)

            _run_isolated_step_with_retry(
                playwright,
                free_browser_config,
                "free member watchlist logo plan gate browser journey",
                free_logo_plan_gate,
                allow_console_errors=(
                    "status of 403",
                    "Failed to load resource: the server responded with a status of 403",
                ),
                allow_request_failures=(f"/api/v1/watchlist/{free_item_id}/logo",),
            )

            if paid_session is None:
                if paid_skipped:
                    REPORTER.warn(
                        "paid watchlist logo browser journey -> skipped "
                        "(no paid persona or superadmin provisioning available)"
                    )
                    REPORTER.record(
                        "paid watchlist logo browser journey",
                        True,
                        "skipped: no paid persona or superadmin provisioning available",
                    )
                else:
                    REPORTER.fail("paid watchlist logo browser journey -> unable to resolve paid persona")
                    REPORTER.record("paid watchlist logo browser journey", False, "paid persona resolution failed")
            else:
                paid_browser_config = with_live_credentials(CONFIG, paid_session.config)

                def paid_logo_upload_and_delete(page, monitor) -> None:
                    login_via_modal(page, paid_browser_config, monitor)
                    card = _open_watchlist_card(page, paid_browser_config, paid_brand)
                    upload_input = card.locator('input[type="file"]').first
                    with page.expect_response(
                        lambda response: response.request.method == "POST"
                        and f"/api/v1/watchlist/{paid_item_id}/logo" in response.url,
                        timeout=paid_browser_config.timeout_ms,
                    ) as upload_response_info:
                        upload_input.set_input_files(
                            [{"name": "watchlist-logo.png", "mimeType": "image/png", "buffer": PNG_1X1}]
                        )
                    upload_response = upload_response_info.value
                    if upload_response.status != 200:
                        raise AssertionError(f"unexpected paid logo upload status: {upload_response.status}")

                    _wait_for_logo_rendered(page, paid_browser_config, paid_brand, paid_item_id)
                    _assert_logo_fetch(paid_session, paid_item_id, 200)

                    card = _open_watchlist_card(page, paid_browser_config, paid_brand)
                    delete_button = card.locator(
                        f"xpath=.//button[contains(@onclick, \"deleteWatchlistLogo('{paid_item_id}')\")]"
                    )
                    delete_button.wait_for(state="visible", timeout=paid_browser_config.timeout_ms)
                    with page.expect_response(
                        lambda response: response.request.method == "DELETE"
                        and f"/api/v1/watchlist/{paid_item_id}/logo" in response.url,
                        timeout=paid_browser_config.timeout_ms,
                    ) as delete_response_info:
                        delete_button.click()
                    delete_response = delete_response_info.value
                    if delete_response.status != 200:
                        raise AssertionError(f"unexpected paid logo delete status: {delete_response.status}")

                    _wait_for_logo_absent(page, paid_browser_config, paid_brand, paid_item_id)
                    _assert_logo_fetch(paid_session, paid_item_id, 404)

                _run_isolated_step_with_retry(
                    playwright,
                    paid_browser_config,
                    "paid watchlist logo upload and delete browser journey",
                    paid_logo_upload_and_delete,
                )
    finally:
        cleanup_watchlist_items_by_prefix(free_session.client, REPORTER, FREE_PREFIX)
        if paid_session is not None:
            cleanup_watchlist_items_by_prefix(paid_session.client, REPORTER, PAID_PREFIX)

    sys.exit(0 if REPORTER.summary("WATCHLIST ASSETS BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

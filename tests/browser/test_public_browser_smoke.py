"""
Browser smoke suite for public-facing journeys.

Run directly:
    python tests/browser/test_public_browser_smoke.py
"""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.auth_state import (
    create_verified_browser_account,
    delete_browser_test_account,
    lookup_email_verification_code,
    lookup_password_reset_code,
)
from tests.browser.helpers.config import load_browser_config
from tests.browser.helpers.session import launch_browser_page, open_url
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.config import DEFAULT_PASSWORD


CONFIG = load_browser_config()
REPORTER = LiveReporter()
FORGOT_SUCCESS_EMAIL = os.environ.get(
    "TEST_BROWSER_FORGOT_SUCCESS_EMAIL",
    "managed-browser-forgot-success@example.com",
)
REGISTRATION_EMAIL = os.environ.get(
    "TEST_BROWSER_REGISTER_EMAIL",
    "managed-browser-register@example.com",
)
pytestmark = pytest.mark.skip(reason="Browser E2E script; run directly with python tests/browser/test_public_browser_smoke.py")


def _get_body_state(page):
    return page.evaluate(
        """() => {
            const state = document.body._x_dataStack && document.body._x_dataStack[0];
            const upgradeModal = document.getElementById('upgrade-modal');
            return {
                searchResults: state ? (state.searchResults || []).length : -1,
                searchError: state ? (state.searchError || '') : 'missing alpine state',
                searchLoading: state ? !!state.searchLoading : false,
                selectedClasses: state ? (state.selectedClasses || []) : [],
                imageName: state ? (state.imageName || '') : '',
                searchQuery: state ? (state.searchQuery || '') : '',
                upgradeModalVisible: !!(upgradeModal && !upgradeModal.classList.contains('hidden'))
            };
        }"""
    )


def _retry_after_seconds(response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass
    return 15.0


def _clear_rate_limit_artifacts(monitor, endpoint: str) -> None:
    monitor.console_errors = [
        error
        for error in monitor.console_errors
        if "status of 429" not in error
    ]
    monitor.request_failures = [
        failure
        for failure in monitor.request_failures
        if not (failure.startswith("429 ") and endpoint in failure)
    ]


def _clear_auth(page) -> None:
    page.goto(f"{CONFIG.base_url}/", wait_until="domcontentloaded", timeout=CONFIG.timeout_ms)
    page.evaluate(
        """() => {
            localStorage.removeItem('auth_token');
            localStorage.removeItem('access_token');
            localStorage.removeItem('refresh_token');
            sessionStorage.removeItem('auth_token');
            sessionStorage.removeItem('access_token');
            sessionStorage.removeItem('refresh_token');
        }"""
    )


def _submit_with_rate_limit_retry(page, monitor, endpoint: str, submit, *, success_statuses: tuple[int, ...]) -> object:
    response = None
    for attempt in range(1, 4):
        with page.expect_response(lambda candidate: endpoint in candidate.url, timeout=CONFIG.timeout_ms) as response_info:
            submit()
        response = response_info.value
        if response.status in success_statuses:
            return response
        if response.status == 429 and attempt < 3:
            _clear_rate_limit_artifacts(monitor, endpoint)
            time.sleep(_retry_after_seconds(response))
            continue
        break
    raise AssertionError(f"unexpected {endpoint} status: {response.status}")


def _wait_for_public_search_idle(page, timeout_ms: int | None = None) -> None:
    page.wait_for_function(
        "() => document.body._x_dataStack && document.body._x_dataStack[0] && !document.body._x_dataStack[0].searchLoading",
        timeout=timeout_ms or CONFIG.timeout_ms,
    )


def _submit_public_search_with_retry(page, monitor, trigger) -> object:
    return _submit_with_rate_limit_retry(
        page,
        monitor,
        "/api/v1/search/public",
        trigger,
        success_statuses=(200,),
    )


def _invoke_public_search(page) -> None:
    page.wait_for_function(
        """() => {
            const state = document.body._x_dataStack && document.body._x_dataStack[0];
            return !!(state && typeof state.publicSearch === 'function');
        }""",
        timeout=CONFIG.timeout_ms,
    )
    page.evaluate(
        """() => {
            const state = document.body._x_dataStack && document.body._x_dataStack[0];
            if (!state || typeof state.publicSearch !== 'function') {
                throw new Error('landing publicSearch unavailable');
            }
            state.publicSearch();
        }"""
    )


def _assert_public_search_success(page, *, timeout_ms: int | None = None) -> dict:
    _wait_for_public_search_idle(page, timeout_ms=timeout_ms)
    state = _get_body_state(page)
    if state["searchError"]:
        raise AssertionError(f"unexpected public search error: {state['searchError']}")
    if state["searchResults"] <= 0:
        raise AssertionError(f"expected public search results > 0, got {state['searchResults']}")
    return state


def _build_valid_public_search_png() -> bytes:
    image = Image.new("RGB", (2, 2), color=(99, 102, 241))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def main() -> None:
    REPORTER.print_heading("PUBLIC BROWSER SMOKE", server=CONFIG.base_url)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(playwright, CONFIG)
        try:
            run_browser_step(
                "landing page bootstrap",
                REPORTER,
                page,
                monitor,
                CONFIG,
                lambda: (
                    open_url(page, CONFIG, "/"),
                    page.locator("#search-input").wait_for(state="visible"),
                    "IP WAT" in page.title() or (_ for _ in ()).throw(AssertionError(f"unexpected title: {page.title()}")),
                ),
            )

            def public_search() -> None:
                open_url(page, CONFIG, "/")
                page.fill("#search-input", "wosen")
                _invoke_public_search(page)
                _assert_public_search_success(page, timeout_ms=max(CONFIG.timeout_ms, 60000))

            run_browser_step(
                "public search journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                public_search,
                allow_console_errors=("status of 429",),
                allow_request_failures=(f"429 GET {CONFIG.base_url}/api/v1/search/public",),
            )

            def public_search_edge_validation() -> None:
                open_url(page, CONFIG, "/")
                page.fill("#search-input", "w")
                _invoke_public_search(page)
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && !state.searchLoading && state.searchError);
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                state = _get_body_state(page)
                if not state["searchError"]:
                    raise AssertionError("expected short-query validation error")
                if state["searchResults"] != 0:
                    raise AssertionError(f"expected no results for short-query validation, got {state['searchResults']}")

            run_browser_step(
                "public search short-query validation",
                REPORTER,
                page,
                monitor,
                CONFIG,
                public_search_edge_validation,
            )

            def public_search_with_class_filter() -> None:
                open_url(page, CONFIG, "/")
                page.locator("div.max-w-2xl.mx-auto.mb-5 button").first.click()
                page.locator('input[x-model="classInput"]').wait_for(state="visible")
                page.fill('input[x-model="classInput"]', "9")
                page.press('input[x-model="classInput"]', "Enter")
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && Array.isArray(state.selectedClasses) && state.selectedClasses.includes(9));
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                page.fill("#search-input", "wosen")
                _invoke_public_search(page)
                state = _assert_public_search_success(page, timeout_ms=max(CONFIG.timeout_ms, 60000))
                if 9 not in state["selectedClasses"]:
                    raise AssertionError(f"expected selected class 9 to persist, got {state['selectedClasses']}")

            run_browser_step(
                "public search class-filter journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                public_search_with_class_filter,
                allow_console_errors=("status of 429",),
                allow_request_failures=(f"429 POST {CONFIG.base_url}/api/v1/search/public",),
            )

            def public_search_with_image() -> None:
                open_url(page, CONFIG, "/")
                page.fill("#search-input", "wosen")
                image_bytes = _build_valid_public_search_png()
                page.locator('input[x-ref="landingImageInput"]').set_input_files(
                    [{"name": "public-search.png", "mimeType": "image/png", "buffer": image_bytes}]
                )
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.imageName === 'public-search.png');
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                _invoke_public_search(page)
                state = _assert_public_search_success(page, timeout_ms=max(CONFIG.timeout_ms, 60000))
                if state["imageName"] != "public-search.png":
                    raise AssertionError(f"expected uploaded image name to persist, got {state['imageName']!r}")

            run_browser_step(
                "public search image journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                public_search_with_image,
                allow_console_errors=("status of 429",),
                allow_request_failures=(f"429 POST {CONFIG.base_url}/api/v1/search/public",),
            )

            def upgrade_modal_plan_handoff_rules() -> None:
                open_url(page, CONFIG, "/")
                page.wait_for_function("() => !!(window.AppUpgradeModal && window.AppUpgradeModal.resolveOffer)", timeout=CONFIG.timeout_ms)
                resolved = page.evaluate(
                    """() => ({
                        freeLeads: window.AppUpgradeModal.resolveOffer({ current_plan: 'free' }, 'leads').recommendedPlan,
                        starterApi: window.AppUpgradeModal.resolveOffer({ current_plan: 'starter' }, 'api_access').recommendedPlan,
                        freeWatchlistLogo: window.AppUpgradeModal.resolveOffer({ current_plan: 'free' }, 'watchlist_logo').recommendedPlan,
                        dailyLimitHandled: window.AppUpgradeModal.maybeHandle({ error: 'daily_limit_exceeded', current_plan: 'free' }, 'quick_search'),
                        dailyLimitPlan: (document.getElementById('upgrade-plan-code')?.textContent || '').trim().toLowerCase(),
                        genericRateHandled: (() => {
                            hideUpgradeModal();
                            return window.AppUpgradeModal.maybeHandle({ message: 'Rate limit exceeded' }, 'quick_search');
                        })(),
                        modalVisibleAfterGenericRate: !!(document.getElementById('upgrade-modal') && !document.getElementById('upgrade-modal').classList.contains('hidden'))
                    })"""
                )
                if resolved["freeLeads"] != "professional":
                    raise AssertionError(f"expected free leads gate to recommend professional, got {resolved}")
                if resolved["starterApi"] != "enterprise":
                    raise AssertionError(f"expected starter api gate to recommend enterprise, got {resolved}")
                if resolved["freeWatchlistLogo"] != "starter":
                    raise AssertionError(f"expected free watchlist-logo gate to recommend starter, got {resolved}")
                if resolved["dailyLimitHandled"] is not True or resolved["dailyLimitPlan"] != "starter":
                    raise AssertionError(f"expected daily quick-search limit to open starter upgrade modal, got {resolved}")
                if resolved["genericRateHandled"] is not False or resolved["modalVisibleAfterGenericRate"]:
                    raise AssertionError(f"expected generic rate-limit payloads to avoid upgrade modal, got {resolved}")

            run_browser_step(
                "upgrade modal plan handoff rules",
                REPORTER,
                page,
                monitor,
                CONFIG,
                upgrade_modal_plan_handoff_rules,
            )

            def public_search_daily_limit_upgrade_gate() -> None:
                context.clear_cookies()
                page.evaluate(
                    """() => {
                        localStorage.removeItem('auth_token');
                        localStorage.removeItem('access_token');
                        localStorage.removeItem('refresh_token');
                        sessionStorage.removeItem('auth_token');
                        sessionStorage.removeItem('access_token');
                        sessionStorage.removeItem('refresh_token');
                    }"""
                )
                open_url(page, CONFIG, "/")
                page.fill("#search-input", "wosen")

                response = None
                for attempt in range(6):
                    with page.expect_response(lambda candidate: "/api/v1/search/public" in candidate.url, timeout=CONFIG.timeout_ms) as response_info:
                        _invoke_public_search(page)
                    response = response_info.value
                    _wait_for_public_search_idle(page, timeout_ms=max(CONFIG.timeout_ms, 60000))
                    if attempt < 5:
                        if response.status != 200:
                            raise AssertionError(f"expected 200 before daily free limit, got {response.status}")
                        state = _get_body_state(page)
                        if state["upgradeModalVisible"]:
                            raise AssertionError("upgrade modal should stay hidden before the free daily limit is exhausted")
                        continue
                    if response.status != 429:
                        raise AssertionError(f"expected 429 on sixth public search, got {response.status}")

                page.locator("#upgrade-modal").wait_for(state="visible", timeout=CONFIG.timeout_ms)
                recommended_plan = (page.locator("#upgrade-plan-code").text_content() or "").strip().lower()
                state = _get_body_state(page)
                if not state["upgradeModalVisible"]:
                    raise AssertionError("expected upgrade modal after the sixth public search")
                if recommended_plan != "starter":
                    raise AssertionError(f"expected starter recommendation after the public daily limit, got {recommended_plan!r}")
                modal_offer = page.evaluate(
                    """() => ({
                        price: (document.getElementById('upgrade-plan-price')?.textContent || '').trim(),
                        features: Array.from(document.querySelectorAll('#upgrade-feature-list li span:last-child')).map((el) => (el.textContent || '').trim())
                    })"""
                )
                if "499" not in modal_offer["price"]:
                    raise AssertionError(f"expected starter monthly price after public daily limit, got {modal_offer}")
                if not any("50" in feature for feature in modal_offer["features"]):
                    raise AssertionError(f"expected starter quick-search highlight after public daily limit, got {modal_offer}")

            run_browser_step(
                "public search daily free limit upgrade gate",
                REPORTER,
                page,
                monitor,
                CONFIG,
                public_search_daily_limit_upgrade_gate,
                allow_console_errors=("status of 429",),
                allow_request_failures=(f"429 GET {CONFIG.base_url}/api/v1/search/public",),
            )

            def pricing_to_checkout() -> None:
                open_url(page, CONFIG, "/pricing")
                page.locator('a[href^="/checkout?plan="]').first.wait_for(state="visible")
                with page.expect_navigation(url="**/checkout?**", timeout=CONFIG.timeout_ms):
                    page.locator('a[href^="/checkout?plan="]').first.click()
                page.wait_for_load_state("networkidle", timeout=CONFIG.timeout_ms)
                if "/checkout" not in page.url:
                    raise AssertionError(f"expected checkout URL, got {page.url}")
                if "plan=" not in page.url:
                    raise AssertionError(f"expected checkout plan query string, got {page.url}")

            run_browser_step(
                "pricing to checkout navigation",
                REPORTER,
                page,
                monitor,
                CONFIG,
                pricing_to_checkout,
            )

            forgot_email = f"browser-forgot-{uuid4().hex[:10]}@example.com"
            forgot_success_email = FORGOT_SUCCESS_EMAIL
            forgot_success_password = DEFAULT_PASSWORD
            forgot_success_new_password = "Reset9876!"
            create_verified_browser_account(
                forgot_success_email,
                forgot_success_password,
                organization_name=f"Browser Reset {uuid4().hex[:8]}",
            )

            def forgot_password_request() -> None:
                open_url(page, CONFIG, "/?login=1")
                page.locator('input[x-model="loginEmail"]').wait_for(state="visible")
                page.locator('button[x-text="t(\'auth.forgot_password\')"]').click()
                page.locator('input[x-model="forgotEmail"]').wait_for(state="visible")
                page.fill('input[x-model="forgotEmail"]', forgot_email)

                response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/forgot-password",
                    lambda: page.locator('div[x-show="showForgotPassword"] button[type="submit"]').click(),
                    success_statuses=(200,),
                )
                if response.status != 200:
                    raise AssertionError(f"unexpected forgot-password status: {response.status}")

                page.locator('input[x-model="forgotCode"]').wait_for(state="visible")
                success_box = page.locator('div[x-show="forgotSuccess"]')
                success_box.wait_for(state="visible")
                success_text = success_box.text_content() or ""
                if not success_text.strip():
                    raise AssertionError("expected forgot-password success message")

            run_browser_step(
                "forgot password request journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                forgot_password_request,
            )

            def forgot_password_invalid_code() -> None:
                page.locator('input[x-model="forgotCode"]').wait_for(state="visible")
                page.fill('input[x-model="forgotCode"]', "000000")
                page.fill('input[x-model="forgotNewPassword"]', "Reset1234!")
                page.fill('input[x-model="forgotConfirmPassword"]', "Reset1234!")

                with page.expect_response(lambda response: "/api/v1/auth/reset-password" in response.url, timeout=CONFIG.timeout_ms) as response_info:
                    page.locator('div[x-show="showForgotPassword"] button[type="submit"]').click()
                response = response_info.value
                if response.status != 400:
                    raise AssertionError(f"expected invalid reset code 400, got {response.status}")

                page.locator('div[x-show="forgotError"]').wait_for(state="visible")
                error_text = page.locator('div[x-show="forgotError"]').text_content() or ""
                if not error_text.strip():
                    raise AssertionError("expected forgot-password error message")

            run_browser_step(
                "forgot password invalid code handling",
                REPORTER,
                page,
                monitor,
                CONFIG,
                forgot_password_invalid_code,
                allow_console_errors=("status of 400",),
                allow_request_failures=("/api/v1/auth/reset-password",),
            )

            def forgot_password_success() -> None:
                open_url(page, CONFIG, "/?login=1")
                page.locator('input[x-model="loginEmail"]').wait_for(state="visible")
                page.locator('button[x-text="t(\'auth.forgot_password\')"]').click()
                page.locator('input[x-model="forgotEmail"]').wait_for(state="visible")
                page.fill('input[x-model="forgotEmail"]', forgot_success_email)

                request_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/forgot-password",
                    lambda: page.locator('div[x-show="showForgotPassword"] button[type="submit"]').click(),
                    success_statuses=(200,),
                )
                if request_response.status != 200:
                    raise AssertionError(f"unexpected forgot-password status: {request_response.status}")

                page.locator('input[x-model="forgotCode"]').wait_for(state="visible")
                reset_code = lookup_password_reset_code(forgot_success_email)
                page.fill('input[x-model="forgotCode"]', reset_code)
                page.fill('input[x-model="forgotNewPassword"]', forgot_success_new_password)
                page.fill('input[x-model="forgotConfirmPassword"]', forgot_success_new_password)

                reset_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/reset-password",
                    lambda: page.locator('div[x-show="showForgotPassword"] button[type="submit"]').click(),
                    success_statuses=(200,),
                )
                if reset_response.status != 200:
                    raise AssertionError(f"unexpected reset-password status: {reset_response.status}")

                success_box = page.locator('div[x-show="forgotSuccess"]')
                success_box.wait_for(state="visible")
                success_text = success_box.text_content() or ""
                if not success_text.strip():
                    raise AssertionError("expected reset-password success message")

                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.showLogin === true && state.showForgotPassword === false);
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                page.locator('input[x-model="loginEmail"]').wait_for(state="visible")
                page.fill('input[x-model="loginEmail"]', forgot_success_email)
                page.fill('input[x-model="loginPassword"]', forgot_success_new_password)

                login_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/login",
                    lambda: page.locator('[role="dialog"] button[type="submit"]').first.click(),
                    success_statuses=(200,),
                )
                if login_response.status != 200:
                    raise AssertionError(f"unexpected post-reset login status: {login_response.status}")

                page.wait_for_url("**/dashboard", timeout=CONFIG.timeout_ms)
                page.locator("#tab-btn-overview").wait_for(state="visible")
                token = page.evaluate("() => localStorage.getItem('auth_token')")
                if not token:
                    raise AssertionError("expected auth_token after post-reset login")

            run_browser_step(
                "forgot password success and login journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                forgot_password_success,
                allow_console_errors=("status of 429",),
                allow_request_failures=(
                    "/api/v1/auth/forgot-password",
                    "/api/v1/auth/reset-password",
                    "/api/v1/auth/login",
                ),
            )

            registration_email = REGISTRATION_EMAIL
            registration_password = DEFAULT_PASSWORD
            delete_browser_test_account(registration_email)

            def register_account() -> None:
                _clear_auth(page)
                open_url(page, CONFIG, "/?register=1")
                page.locator('input[x-model="regFirstName"]').wait_for(state="visible")
                page.fill('input[x-model="regFirstName"]', "Browser")
                page.fill('input[x-model="regLastName"]', "Signup")
                page.fill('input[x-model="regEmail"]', registration_email)
                page.fill('input[x-model="regPassword"]', registration_password)
                page.fill('input[x-model="regConfirmPassword"]', registration_password)
                page.fill('input[x-model="regOrgName"]', f"Browser Signup {uuid4().hex[:8]}")

                response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/register",
                    lambda: page.locator('div[x-show="showRegister"] button[type="submit"]').click(),
                    success_statuses=(200,),
                )
                if response.status != 200:
                    raise AssertionError(f"unexpected register status: {response.status}")

                page.wait_for_url("**/dashboard", timeout=CONFIG.timeout_ms)
                page.locator("#tab-btn-overview").wait_for(state="visible")
                page.locator('input[x-model="verificationCode"]').wait_for(state="visible")
                monitor.clear()
                token = page.evaluate("() => localStorage.getItem('auth_token')")
                if not token:
                    raise AssertionError("expected auth_token after registration")

            run_browser_step(
                "registration modal journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                register_account,
            )

            def verify_email_modal() -> None:
                page.locator('input[x-model="verificationCode"]').wait_for(state="visible")

                resend_button = page.locator('div[x-show="showEmailVerification"] button').nth(1)
                resend_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/resend-verification",
                    lambda: resend_button.click(),
                    success_statuses=(200,),
                )
                if resend_response.status != 200:
                    raise AssertionError(f"unexpected resend-verification status: {resend_response.status}")

                page.locator('div[x-show="verificationSuccess"]').wait_for(state="visible")
                cooldown = page.evaluate(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return state ? state.verificationResendCooldown : -1;
                    }"""
                )
                if cooldown <= 0:
                    raise AssertionError(f"expected resend cooldown > 0, got {cooldown}")

                verification_code = lookup_email_verification_code(registration_email)
                page.fill('input[x-model="verificationCode"]', verification_code)

                verify_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/verify-email",
                    lambda: page.locator('div[x-show="showEmailVerification"] button').first.click(),
                    success_statuses=(200,),
                )
                if verify_response.status != 200:
                    raise AssertionError(f"unexpected verify-email status: {verify_response.status}")

                page.locator('div[x-show="verificationSuccess"]').wait_for(state="visible")
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.showEmailVerification === false);
                    }""",
                    timeout=CONFIG.timeout_ms,
                )

                profile = page.evaluate(
                    """async () => {
                        const token = localStorage.getItem('auth_token');
                        const res = await fetch('/api/v1/auth/me', {
                            headers: { Authorization: 'Bearer ' + token }
                        });
                        let data = null;
                        try {
                            data = await res.json();
                        } catch (error) {
                            data = null;
                        }
                        return {
                            status: res.status,
                            is_verified: data ? data.is_verified : null
                        };
                    }"""
                )
                if profile["status"] != 200:
                    raise AssertionError(f"unexpected auth/me status after email verification: {profile['status']}")
                if profile["is_verified"] is not True:
                    raise AssertionError(f"expected is_verified after email verification, got {profile['is_verified']!r}")

            run_browser_step(
                "email verification modal journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                verify_email_modal,
            )
        finally:
            context.close()
            browser.close()
            delete_browser_test_account(REGISTRATION_EMAIL)

    sys.exit(0 if REPORTER.summary("PUBLIC BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

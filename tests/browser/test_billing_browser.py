"""
Browser journeys for checkout, billing auth, and payment initialization.

Run directly:
    python tests/browser/test_billing_browser.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.auth_state import (
    create_verified_browser_account,
    delete_browser_test_account,
    lookup_password_reset_code,
)
from tests.browser.helpers.config import load_browser_config
from tests.browser.helpers.session import launch_browser_page, open_url
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import DEFAULT_PASSWORD, load_live_config


CONFIG = load_browser_config()
LIVE_CONFIG = load_live_config()
REPORTER = LiveReporter()
CHECKOUT_EMAIL = os.environ.get(
    "TEST_BROWSER_CHECKOUT_EMAIL",
    "managed-browser-checkout@example.com",
)
CHECKOUT_FORGOT_EMAIL = os.environ.get(
    "TEST_BROWSER_CHECKOUT_FORGOT_EMAIL",
    "managed-browser-checkout-forgot@example.com",
)
MOBILE_VIEWPORT = {"width": 390, "height": 844}
pytestmark = pytest.mark.skip(reason="Browser E2E script; run directly with python tests/browser/test_billing_browser.py")


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


def _submit_with_rate_limit_retry(page, monitor, endpoint: str, submit, *, success_statuses: tuple[int, ...]):
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


def _issue_auth_token(email: str, password: str) -> str:
    client = LiveClient(LIVE_CONFIG)
    response = None
    for attempt in range(1, 4):
        response = client.post(
            "/api/v1/auth/login",
            json_data={"email": email, "password": password},
            token=False,
        )
        if response.status_code == 200:
            break
        if response.status_code == 429 and attempt < 3:
            time.sleep(_retry_after_seconds(response))
            continue
        raise AssertionError(f"unexpected /api/v1/auth/login status: {response.status_code}")

    data = response.json()
    token = data.get("access_token")
    if not token:
        raise AssertionError("login response missing access_token")
    return token


def _assert_no_horizontal_overflow(page, label: str) -> None:
    metrics = page.evaluate(
        """() => ({
            clientWidth: document.documentElement.clientWidth,
            scrollWidth: document.documentElement.scrollWidth,
            bodyScrollWidth: document.body ? document.body.scrollWidth : 0
        })"""
    )
    widest = max(metrics["scrollWidth"], metrics["bodyScrollWidth"])
    if widest > metrics["clientWidth"] + 1:
        raise AssertionError(
            f"{label} overflows horizontally on mobile: "
            f"clientWidth={metrics['clientWidth']}, scrollWidth={metrics['scrollWidth']}, "
            f"bodyScrollWidth={metrics['bodyScrollWidth']}"
        )


def main() -> None:
    REPORTER.print_heading("BILLING BROWSER", server=CONFIG.base_url)

    with sync_playwright() as playwright:
        checkout_email = CHECKOUT_EMAIL
        checkout_forgot_email = CHECKOUT_FORGOT_EMAIL
        delete_browser_test_account(checkout_email)
        delete_browser_test_account(checkout_forgot_email)
        create_verified_browser_account(
            checkout_forgot_email,
            DEFAULT_PASSWORD,
            organization_name=f"Managed Billing Reset {uuid4().hex[:8]}",
        )
        try:
            def run_isolated_browser_step(
                name: str,
                action,
                *,
                allow_console_errors: tuple[str, ...] = (),
                allow_request_failures: tuple[str, ...] = (),
            ) -> None:
                browser, context, page, monitor = launch_browser_page(playwright, CONFIG)
                try:
                    run_browser_step(
                        name,
                        REPORTER,
                        page,
                        monitor,
                        CONFIG,
                        lambda: action(page, monitor),
                        allow_console_errors=allow_console_errors,
                        allow_request_failures=allow_request_failures,
                    )
                finally:
                    context.close()
                    browser.close()

            def billing_locale_render(page, _monitor) -> None:
                def read_page_state() -> dict:
                    return page.evaluate(
                        """() => ({
                            dir: document.documentElement.getAttribute('dir') || '',
                            title: document.title || '',
                            hero: (document.querySelector('main h1')?.textContent || '').trim(),
                            rawKeys: document.body.innerText.match(/(?:pricing|checkout)\\.[\\w_]+/g) || []
                        })"""
                    )

                localized_names = {
                    "en": {
                        "starter": "Starter",
                        "professional": "Professional",
                        "enterprise": "Enterprise",
                        "popular": "Most popular",
                        "annual_prefix": "Billed annually:",
                    },
                    "tr": {
                        "starter": "Başlangıç",
                        "professional": "Profesyonel",
                        "enterprise": "Kurumsal",
                        "popular": "En çok tercih edilen",
                        "annual_prefix": "Yıllık fatura:",
                    },
                    "ar": {
                        "starter": "أساسي",
                        "professional": "احترافي",
                        "enterprise": "مؤسسات",
                        "popular": "الأكثر شيوعًا",
                        "annual_prefix": "الفاتورة السنوية:",
                    },
                }

                for locale, expected_dir in (("en", "ltr"), ("tr", "ltr"), ("ar", "rtl")):
                    open_url(page, CONFIG, "/")
                    page.evaluate("(locale) => localStorage.setItem('app_locale', locale)", locale)

                    open_url(page, CONFIG, "/pricing")
                    page.wait_for_function(
                        "(dir) => document.documentElement.getAttribute('dir') === dir",
                        arg=expected_dir,
                        timeout=CONFIG.timeout_ms,
                    )
                    pricing_state = read_page_state()
                    if pricing_state["dir"] != expected_dir:
                        raise AssertionError(f"expected pricing page dir={expected_dir}, got {pricing_state['dir']!r}")
                    if not pricing_state["title"] or pricing_state["title"].startswith("pricing."):
                        raise AssertionError(f"unexpected pricing page title for {locale}: {pricing_state['title']!r}")
                    if not pricing_state["hero"] or pricing_state["hero"].startswith("pricing."):
                        raise AssertionError(f"unexpected pricing hero text for {locale}: {pricing_state['hero']!r}")
                    if pricing_state["rawKeys"]:
                        raise AssertionError(f"unexpected raw pricing locale keys for {locale}: {pricing_state['rawKeys']}")
                    expected_names = localized_names[locale]
                    if (page.locator("#pricing-plan-starter-name").text_content() or "").strip() != expected_names["starter"]:
                        raise AssertionError(
                            f"unexpected starter plan name for {locale}: "
                            f"{(page.locator('#pricing-plan-starter-name').text_content() or '').strip()!r}"
                        )
                    if (page.locator("#pricing-plan-professional-name").text_content() or "").strip() != expected_names["professional"]:
                        raise AssertionError(
                            f"unexpected professional plan name for {locale}: "
                            f"{(page.locator('#pricing-plan-professional-name').text_content() or '').strip()!r}"
                        )
                    if (page.locator("#pricing-plan-enterprise-name").text_content() or "").strip() != expected_names["enterprise"]:
                        raise AssertionError(
                            f"unexpected enterprise plan name for {locale}: "
                            f"{(page.locator('#pricing-plan-enterprise-name').text_content() or '').strip()!r}"
                        )
                    popular_badge = page.locator("#pricing-popular-badge")
                    popular_badge.wait_for(state="visible")
                    if (popular_badge.text_content() or "").strip() != expected_names["popular"]:
                        raise AssertionError(
                            f"unexpected popular badge text for {locale}: {(popular_badge.text_content() or '').strip()!r}"
                        )
                    page.locator("#pricing-billing-toggle").click()
                    page.wait_for_function(
                        """() => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            return !!(state && state.annual === true);
                        }""",
                        timeout=CONFIG.timeout_ms,
                    )
                    annual_note = (page.locator("#pricing-starter-billed-annually").text_content() or "").strip()
                    if not annual_note.startswith(expected_names["annual_prefix"]):
                        raise AssertionError(f"unexpected annual billing note for {locale}: {annual_note!r}")
                    if "?" in annual_note:
                        raise AssertionError(f"broken annual billing note for {locale}: {annual_note!r}")
                    if locale == "tr":
                        badge_text = (page.locator("#pricing-annual-discount-badge").text_content() or "").strip()
                        if badge_text != "-20%":
                            raise AssertionError(f"unexpected pricing annual discount badge: {badge_text!r}")
                        pricing_body_text = page.locator("body").text_content() or ""
                        if "₺₺" in pricing_body_text:
                            raise AssertionError(f"unexpected duplicated currency symbol in pricing annual view: {pricing_body_text!r}")

                    open_url(page, CONFIG, "/checkout?plan=starter&billing=monthly")
                    page.locator('input[x-model="regFirstName"]').wait_for(state="visible")
                    page.wait_for_function(
                        "(dir) => document.documentElement.getAttribute('dir') === dir",
                        arg=expected_dir,
                        timeout=CONFIG.timeout_ms,
                    )
                    checkout_state = read_page_state()
                    if checkout_state["dir"] != expected_dir:
                        raise AssertionError(f"expected checkout page dir={expected_dir}, got {checkout_state['dir']!r}")
                    if not checkout_state["title"] or checkout_state["title"].startswith("checkout."):
                        raise AssertionError(f"unexpected checkout page title for {locale}: {checkout_state['title']!r}")
                    if not checkout_state["hero"] or checkout_state["hero"].startswith("checkout."):
                        raise AssertionError(f"unexpected checkout hero text for {locale}: {checkout_state['hero']!r}")
                    if checkout_state["rawKeys"]:
                        raise AssertionError(f"unexpected raw checkout locale keys for {locale}: {checkout_state['rawKeys']}")
                    if locale == "tr":
                        checkout_badge_text = (page.locator("#checkout-annual-discount-badge").text_content() or "").strip()
                        if checkout_badge_text != "-20%":
                            raise AssertionError(f"unexpected checkout annual discount badge: {checkout_badge_text!r}")

                    open_url(page, CONFIG, "/checkout?plan=enterprise&billing=monthly")
                    page.locator("#checkout-billing-monthly").wait_for(state="visible")
                    checkout_plan_name = (page.locator("#checkout-plan-display-name").text_content() or "").strip()
                    if checkout_plan_name != expected_names["enterprise"]:
                        raise AssertionError(
                            f"unexpected checkout enterprise plan name for {locale}: {checkout_plan_name!r}"
                        )
                    enterprise_text = page.locator("ul.space-y-3.text-sm.text-slate-700").last.text_content() or ""
                    if "999999" in enterprise_text:
                        raise AssertionError(f"unexpected raw enterprise sentinel limit in checkout for {locale}: {enterprise_text!r}")

                    unlimited_labels = page.evaluate(
                        """() => [
                            window.AppI18n.t('pricing.f_unlimited_searches'),
                            window.AppI18n.t('pricing.f_unlimited_watchlist'),
                            window.AppI18n.t('pricing.f_unlimited_live')
                        ]"""
                    )
                    for label in unlimited_labels:
                        if label not in enterprise_text:
                            raise AssertionError(
                                f"expected enterprise checkout summary label {label!r} for {locale}, "
                                f"got {enterprise_text!r}"
                            )

            run_isolated_browser_step(
                "billing locale render journey",
                billing_locale_render,
            )

            def billing_mobile_layout(page, _monitor) -> None:
                page.set_viewport_size(MOBILE_VIEWPORT)
                page.wait_for_function(
                    "(width) => window.innerWidth === width",
                    arg=MOBILE_VIEWPORT["width"],
                    timeout=CONFIG.timeout_ms,
                )

                for locale, expected_dir in (("tr", "ltr"), ("ar", "rtl")):
                    open_url(page, CONFIG, "/")
                    page.evaluate("(locale) => localStorage.setItem('app_locale', locale)", locale)

                    open_url(page, CONFIG, "/pricing")
                    page.wait_for_function(
                        "(dir) => document.documentElement.getAttribute('dir') === dir",
                        arg=expected_dir,
                        timeout=CONFIG.timeout_ms,
                    )
                    page.locator("#pricing-billing-toggle").wait_for(state="visible")
                    page.locator('a[href*="/checkout?plan=starter"]').first.wait_for(state="visible")
                    _assert_no_horizontal_overflow(page, f"pricing[{locale}]")

                    open_url(page, CONFIG, "/checkout?plan=starter&billing=monthly")
                    page.locator('input[x-model="regFirstName"]').wait_for(state="visible")
                    page.locator("#checkout-billing-monthly").wait_for(state="visible")
                    page.locator("#checkout-billing-annual").wait_for(state="visible")
                    _assert_no_horizontal_overflow(page, f"checkout[{locale}]")

                    summary_box = page.locator("#checkout-billing-monthly").bounding_box()
                    register_box = page.locator("#checkout-register-submit").bounding_box()
                    if not summary_box or not register_box:
                        raise AssertionError(f"missing mobile checkout controls for {locale}")
                    if summary_box["y"] >= register_box["y"]:
                        raise AssertionError(
                            f"expected mobile order summary ahead of account form for {locale}, "
                            f"got summary_y={summary_box['y']} register_y={register_box['y']}"
                        )

            run_isolated_browser_step(
                "billing mobile viewport journey",
                billing_mobile_layout,
            )

            def free_checkout_registration_and_activation(page, monitor) -> None:
                _clear_auth(page)
                open_url(page, CONFIG, "/checkout?plan=free&billing=monthly")
                page.locator('input[x-model="regFirstName"]').wait_for(state="visible")
                page.fill('input[x-model="regFirstName"]', "Billing")
                page.fill('input[x-model="regLastName"]', "Browser")
                page.fill('input[x-model="regEmail"]', checkout_email)
                page.fill('input[x-model="regOrgName"]', "Managed Billing Browser")
                page.fill('input[x-model="regPassword"]', DEFAULT_PASSWORD)

                response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/register",
                    lambda: page.locator('div[x-show="authTab === \'register\'"] button.w-full').click(),
                    success_statuses=(200,),
                )
                if response.status != 200:
                    raise AssertionError(f"unexpected checkout register status: {response.status}")

                page.wait_for_function(
                    "() => document.body._x_dataStack && document.body._x_dataStack[0] && document.body._x_dataStack[0].step === 'payment'",
                    timeout=CONFIG.timeout_ms,
                )

                with page.expect_response(
                    lambda candidate: "/api/v1/payments/activate-free" in candidate.url,
                    timeout=CONFIG.timeout_ms,
                ) as activate_response_info:
                    page.locator('div[x-show="step === \'payment\'"] button.px-8').click()
                activate_response = activate_response_info.value
                if activate_response.status != 200:
                    raise AssertionError(f"unexpected free activation status: {activate_response.status}")

                page.wait_for_url("**/dashboard?payment=success", timeout=CONFIG.timeout_ms)

            run_isolated_browser_step(
                "checkout free registration and activation journey",
                free_checkout_registration_and_activation,
            )

            def authenticated_paid_checkout_initialization(page, _monitor) -> None:
                token = _issue_auth_token(CONFIG.email, CONFIG.password)
                open_url(page, CONFIG, "/")
                page.evaluate(
                    """(token) => {
                        localStorage.setItem('auth_token', token);
                        localStorage.setItem('access_token', token);
                    }""",
                    token,
                )
                open_url(page, CONFIG, "/checkout?plan=starter&billing=monthly")
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.isLoggedIn && state.step === 'payment' && (!state.paymentLoading || state.checkoutFormHtml || state.paymentError));
                    }""",
                    timeout=CONFIG.timeout_ms,
                )

                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && (state.checkoutFormHtml || state.paymentError));
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                state = page.evaluate(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return {
                            checkoutFormHtml: state ? (state.checkoutFormHtml || '') : '',
                            paymentError: state ? (state.paymentError || '') : ''
                        };
                    }"""
                )
                if not state["checkoutFormHtml"] and not state["paymentError"]:
                    raise AssertionError("expected checkout form HTML or payment error after initialization")

            run_isolated_browser_step(
                "authenticated paid checkout initialization journey",
                authenticated_paid_checkout_initialization,
                allow_console_errors=("status of 502", "Failed to load resource: the server responded with a status of 502"),
                allow_request_failures=("/api/v1/payments/initialize",),
            )

            def checkout_login_flow(page, monitor) -> None:
                _clear_auth(page)
                open_url(page, CONFIG, "/checkout?plan=free&billing=monthly")
                page.locator('button[x-text="t(\'checkout.tab_login\')"]').click()
                page.locator('input[x-model="loginEmail"]').wait_for(state="visible")
                page.fill('input[x-model="loginEmail"]', CONFIG.email)
                page.fill('input[x-model="loginPassword"]', CONFIG.password)

                response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/login",
                    lambda: page.locator('#checkout-login-submit').click(),
                    success_statuses=(200,),
                )
                if response.status != 200:
                    raise AssertionError(f"unexpected checkout login status: {response.status}")

                page.wait_for_function(
                    "() => document.body._x_dataStack && document.body._x_dataStack[0] && document.body._x_dataStack[0].step === 'payment'",
                    timeout=CONFIG.timeout_ms,
                )
                page.wait_for_function(
                    """(email) => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.isLoggedIn && state.userEmail === email);
                    }""",
                    arg=CONFIG.email,
                    timeout=CONFIG.timeout_ms,
                )

            run_isolated_browser_step(
                "checkout login auth journey",
                checkout_login_flow,
                allow_console_errors=("status of 429", "Too Many Requests"),
                allow_request_failures=("/api/v1/auth/login",),
            )

            def checkout_forgot_password_flow(page, monitor) -> None:
                reset_password = "CheckoutReset9876!"

                _clear_auth(page)
                open_url(page, CONFIG, "/checkout?plan=free&billing=monthly")
                page.locator('button[x-text="t(\'checkout.tab_login\')"]').click()
                page.locator('input[x-model="loginEmail"]').wait_for(state="visible")
                page.fill('input[x-model="loginEmail"]', checkout_forgot_email)
                page.locator('#checkout-forgot-link').click()
                page.locator('input[x-model="forgotEmail"]').wait_for(state="visible")
                page.fill('input[x-model="forgotEmail"]', checkout_forgot_email)

                request_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/forgot-password",
                    lambda: page.locator('#checkout-forgot-request').click(),
                    success_statuses=(200,),
                )
                if request_response.status != 200:
                    raise AssertionError(f"unexpected checkout forgot-password status: {request_response.status}")

                page.locator('input[x-model="forgotCode"]').wait_for(state="visible")
                reset_code = lookup_password_reset_code(checkout_forgot_email)
                page.fill('input[x-model="forgotCode"]', reset_code)
                page.fill('input[x-model="forgotNewPassword"]', reset_password)
                page.fill('input[x-model="forgotConfirmPassword"]', reset_password)

                reset_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/reset-password",
                    lambda: page.locator('#checkout-forgot-reset').click(),
                    success_statuses=(200,),
                )
                if reset_response.status != 200:
                    raise AssertionError(f"unexpected checkout reset-password status: {reset_response.status}")

                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.showForgotPassword === false);
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                page.locator('input[x-model="loginEmail"]').wait_for(state="visible")
                page.fill('input[x-model="loginPassword"]', reset_password)

                login_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/login",
                    lambda: page.locator('#checkout-login-submit').click(),
                    success_statuses=(200,),
                )
                if login_response.status != 200:
                    raise AssertionError(f"unexpected checkout post-reset login status: {login_response.status}")

                page.wait_for_function(
                    "() => document.body._x_dataStack && document.body._x_dataStack[0] && document.body._x_dataStack[0].step === 'payment'",
                    timeout=CONFIG.timeout_ms,
                )
                page.wait_for_function(
                    """(email) => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.isLoggedIn && state.userEmail === email);
                    }""",
                    arg=checkout_forgot_email,
                    timeout=CONFIG.timeout_ms,
                )

            run_isolated_browser_step(
                "checkout forgot password reset journey",
                checkout_forgot_password_flow,
                allow_console_errors=("status of 429", "Too Many Requests"),
                allow_request_failures=(
                    "/api/v1/auth/forgot-password",
                    "/api/v1/auth/reset-password",
                    "/api/v1/auth/login",
                ),
            )
        finally:
            delete_browser_test_account(checkout_email)
            delete_browser_test_account(checkout_forgot_email)

    sys.exit(0 if REPORTER.summary("BILLING BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

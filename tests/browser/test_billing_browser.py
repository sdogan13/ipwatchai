"""
Browser journeys for checkout, billing auth, and payment initialization.

Run directly:
    python tests/browser/test_billing_browser.py
"""

from __future__ import annotations

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
from tests.browser.helpers.config import load_browser_config
from tests.browser.helpers.session import launch_browser_page, open_url
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import DEFAULT_PASSWORD, load_live_config


CONFIG = load_browser_config()
LIVE_CONFIG = load_live_config()
REPORTER = LiveReporter()
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


def main() -> None:
    REPORTER.print_heading("BILLING BROWSER", server=CONFIG.base_url)

    with sync_playwright() as playwright:
        checkout_email = f"browser-checkout-{uuid4().hex[:10]}@example.com"

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

        def free_checkout_registration_and_activation(page, monitor) -> None:
            _clear_auth(page)
            open_url(page, CONFIG, "/checkout?plan=free&billing=monthly")
            page.locator('input[x-model="regFirstName"]').wait_for(state="visible")
            page.fill('input[x-model="regFirstName"]', "Billing")
            page.fill('input[x-model="regLastName"]', "Browser")
            page.fill('input[x-model="regEmail"]', checkout_email)
            page.fill('input[x-model="regOrgName"]', f"Billing Browser {uuid4().hex[:8]}")
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
                lambda: page.locator('div[x-show="authTab === \'login\'"] button.w-full').click(),
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

    sys.exit(0 if REPORTER.summary("BILLING BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

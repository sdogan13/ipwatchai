from __future__ import annotations

import time
from dataclasses import dataclass, field

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency. Run: pip install playwright") from exc

from tests.browser.helpers.config import BrowserConfig


@dataclass
class BrowserMonitor:
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    request_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def attach(self, page) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        page.on("requestfailed", self._on_requestfailed)
        page.on("response", self._on_response)

    def clear(self) -> None:
        self.console_errors.clear()
        self.page_errors.clear()
        self.request_failures.clear()
        self.warnings.clear()

    def _on_console(self, message) -> None:
        text = message.text.strip()
        if message.type == "error":
            self.console_errors.append(text)
        elif message.type == "warning":
            self.warnings.append(text)

    def _on_pageerror(self, exc: Exception) -> None:
        self.page_errors.append(str(exc))

    def _on_requestfailed(self, request) -> None:
        failure_obj = request.failure
        if isinstance(failure_obj, str):
            failure = failure_obj
        elif failure_obj is None:
            failure = "unknown"
        else:
            failure = getattr(failure_obj, "error_text", str(failure_obj))
        if "ERR_ABORTED" in failure:
            return
        self.request_failures.append(f"REQUESTFAILED {request.method} {request.url} -> {failure}")

    def _on_response(self, response) -> None:
        if response.status >= 400:
            self.request_failures.append(f"{response.status} {response.request.method} {response.url}")


def launch_browser_page(playwright, config: BrowserConfig):
    browser = playwright.chromium.launch(channel=config.browser_channel, headless=config.headless)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1080})
    page = context.new_page()
    page.set_default_timeout(config.timeout_ms)
    page.set_default_navigation_timeout(config.timeout_ms)

    monitor = BrowserMonitor()
    monitor.attach(page)
    return browser, context, page, monitor


def open_url(page, config: BrowserConfig, path: str) -> None:
    url = f"{config.base_url}{path}"
    last_error = None
    for attempt, wait_until in enumerate(("networkidle", "domcontentloaded", "domcontentloaded"), start=1):
        try:
            page.goto(url, wait_until=wait_until, timeout=config.timeout_ms)
            if wait_until != "networkidle":
                page.wait_for_load_state("domcontentloaded", timeout=config.timeout_ms)
            return
        except PlaywrightTimeoutError as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(1.0)
    raise last_error


def _retry_after_seconds(response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass
    return 15.0


def _clear_rate_limit_artifacts(monitor: BrowserMonitor | None, endpoint: str) -> None:
    if monitor is None:
        return
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


def login_via_modal(page, config: BrowserConfig, monitor: BrowserMonitor | None = None) -> None:
    open_url(page, config, "/?login=1")
    overview_tab = page.locator("#tab-btn-overview")
    if overview_tab.count() > 0 and overview_tab.first.is_visible():
        return

    page.locator('input[x-model="loginEmail"]').wait_for(state="visible")
    page.locator('input[x-model="loginEmail"]').fill(config.email)
    page.locator('input[x-model="loginPassword"]').fill(config.password)
    response = None
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        with page.expect_response(
            lambda candidate: "/api/v1/auth/login" in candidate.url,
            timeout=config.timeout_ms,
        ) as response_info:
            page.locator('[role="dialog"] button[type="submit"]').first.click()
        response = response_info.value
        if response.status == 200:
            break
        if response.status == 429 and attempt < max_attempts:
            _clear_rate_limit_artifacts(monitor, "/api/v1/auth/login")
            time.sleep(_retry_after_seconds(response))
            continue
        raise AssertionError(f"unexpected /api/v1/auth/login status: {response.status}")

    try:
        page.wait_for_url("**/dashboard", timeout=config.timeout_ms)
    except PlaywrightTimeoutError:
        overview_tab.wait_for(state="visible", timeout=config.timeout_ms)

    try:
        page.wait_for_load_state("networkidle", timeout=config.timeout_ms)
    except PlaywrightTimeoutError:
        page.wait_for_load_state("domcontentloaded", timeout=config.timeout_ms)
    overview_tab.wait_for(state="visible")

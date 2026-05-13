"""
Live smoke for the Reports tab view-inline behavior.

Proves that clicking a completed report row opens the file in a new tab
(via blob: URL) instead of forcing a download. Run directly:

    python tests/browser/test_reports_view_inline_browser.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.browser.helpers.config import load_browser_config
from tests.browser.helpers.session import launch_browser_page, login_via_modal

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_reports_view_inline_browser.py"
)


REPORT_TITLE = f"BROWSER VIEW INLINE {uuid4().hex[:8].upper()}"
CLEANUP_PREFIX = "BROWSER VIEW INLINE"


def _wait_for_report_row(page, title: str, timeout_ms: int) -> None:
    deadline = time.monotonic() + timeout_ms / 1000.0
    locator = page.locator("#reports-list").get_by_text(title, exact=False)
    while time.monotonic() < deadline:
        if locator.count() > 0:
            return
        time.sleep(0.5)
    raise AssertionError(f"report row with title {title!r} did not appear in #reports-list")


def _cleanup_reports(page, base_url: str, title_prefix: str) -> None:
    token = page.evaluate(
        "() => localStorage.getItem('auth_token') "
        "|| localStorage.getItem('access_token') "
        "|| localStorage.getItem('token')"
    )
    if not token:
        return
    api_request = page.context.request
    listing = api_request.get(
        f"{base_url}/api/v1/reports?page=1&page_size=100",
        headers={"Authorization": f"Bearer {token}"},
    )
    if not listing.ok:
        return
    body = listing.json()
    for report in body.get("reports", []):
        if (report.get("title") or "").startswith(title_prefix):
            api_request.delete(
                f"{base_url}/api/v1/reports/{report['id']}",
                headers={"Authorization": f"Bearer {token}"},
            )


def run_view_smoke() -> None:
    config = load_browser_config()
    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(playwright, config)
        try:
            login_via_modal(page, config, monitor)

            page.click("#tab-btn-reports")
            page.locator("#tab-content-reports").wait_for(state="visible")
            page.locator("#reports-list").wait_for(state="attached")

            page.locator(
                '#tab-content-reports button[onclick="showReportGenerateModal()"]'
            ).click()
            page.locator("#report-generate-modal").wait_for(state="visible")
            page.fill("#reportTitleInput", REPORT_TITLE)

            with page.expect_response(
                lambda response: response.request.method == "POST"
                and "/api/v1/reports/generate" in response.url,
                timeout=config.timeout_ms,
            ) as generate_response_info:
                page.click("#reportSubmitBtn")
            generate_response = generate_response_info.value
            if generate_response.status != 200:
                raise AssertionError(
                    f"unexpected report generate status: {generate_response.status}"
                )

            page.locator("#report-generate-modal").wait_for(state="hidden")
            _wait_for_report_row(page, REPORT_TITLE, config.timeout_ms)

            row = page.locator(
                f'#reports-list div[role="button"]:has-text("{REPORT_TITLE}")'
            ).first
            row.wait_for(state="visible", timeout=config.timeout_ms)

            row_class = row.get_attribute("class") or ""
            if "cursor-pointer" not in row_class:
                raise AssertionError(
                    "report row should be styled cursor-pointer when completed"
                )

            with context.expect_page(timeout=config.timeout_ms) as new_page_info:
                row.click()
            new_page = new_page_info.value
            try:
                new_page.wait_for_url("blob:**", timeout=config.timeout_ms)
            except PlaywrightTimeoutError as exc:
                raise AssertionError(
                    f"expected new tab to navigate to a blob: URL within "
                    f"{config.timeout_ms}ms, last URL was {new_page.url!r}"
                ) from exc

            new_url = new_page.url or ""
            if not new_url.startswith("blob:"):
                raise AssertionError(
                    f"expected new tab URL to be a blob: URL, got {new_url!r}"
                )
            new_page.close()

            if monitor.page_errors:
                raise AssertionError(
                    f"page errors during view smoke: {monitor.page_errors}"
                )
            view_failures = [
                failure
                for failure in monitor.request_failures
                if "/api/v1/reports/" in failure and "/download" in failure
            ]
            if view_failures:
                raise AssertionError(
                    f"report download request failed during view: {view_failures}"
                )

            print(f"OK: report row click opened new tab at {new_url[:48]}...")
        finally:
            try:
                _cleanup_reports(page, config.base_url, CLEANUP_PREFIX)
            except Exception as exc:  # pragma: no cover - best effort cleanup
                print(f"cleanup warning: {exc}")
            context.close()
            browser.close()


if __name__ == "__main__":
    run_view_smoke()

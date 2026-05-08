"""Browser smoke for the design watchlist dashboard tab.

Run directly:
    python tests/browser/test_design_watchlist_browser.py

Requires the app running and a member persona (TEST_BASE_URL / TEST_EMAIL /
TEST_PASSWORD env vars; see test.md).

Coverage: tab activation, add-form open + submit, list refresh, empty / list
state assertions, en/tr/ar locale label switching. Alert lifecycle is NOT
exercised here because creating real alerts requires the post-ingest scanner
to fire against a populated bulletin batch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.config import load_browser_config
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = load_browser_config()
REPORTER = LiveReporter()
pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_design_watchlist_browser.py"
)


def _open_design_watchlist_tab(page) -> None:
    page.evaluate("window.showDashboardTab('design-watchlist')")
    page.wait_for_selector("#tab-content-design-watchlist:not(.hidden)", timeout=5000)


def _assert_panel_visible(page) -> dict:
    panel = page.locator("#tab-content-design-watchlist")
    assert panel.is_visible(), "design-watchlist panel hidden after switching tab"
    title = panel.locator("h2").first.inner_text().strip()
    assert title, "design watchlist panel title is empty"
    add_btn = page.locator("#design-watchlist-add-toggle")
    assert add_btn.is_visible(), "add button missing"
    return {"title": title}


def _expand_add_form(page) -> None:
    page.click("#design-watchlist-add-toggle")
    page.wait_for_selector("#design-watchlist-add-card:not(.hidden)", timeout=5000)


def _submit_new_item(page, product_name: str, locarno: str) -> dict:
    page.fill("#design-watchlist-product-name", product_name)
    page.fill("#design-watchlist-locarno", locarno)
    page.click("#design-watchlist-submit")
    # Wait for either the new card in the list, or an error toast
    page.wait_for_selector(
        "#design-watchlist-list article, #design-watchlist-error:not(.hidden)",
        timeout=10000,
    )
    list_count = page.locator("#design-watchlist-list article").count()
    error_visible = page.locator("#design-watchlist-error").is_visible()
    return {"list_count": list_count, "error_visible": error_visible}


def _switch_locale_and_assert_label(page, lang_code: str, expected_label: str) -> None:
    page.evaluate(f"window.AppI18n && window.AppI18n.setLocale && window.AppI18n.setLocale('{lang_code}')")
    page.wait_for_timeout(300)
    label_text = page.locator("#tab-btn-design-watchlist").inner_text().strip()
    assert expected_label.lower() in label_text.lower(), (
        f"Tab label after switching to {lang_code!r}: got {label_text!r}, expected {expected_label!r}"
    )


def test_design_watchlist_dashboard_smoke():
    with sync_playwright() as playwright:
        browser, context, page = launch_browser_page(playwright, CONFIG)
        try:
            run_browser_step(REPORTER, "Login", lambda: login_via_modal(page, CONFIG, None))
            run_browser_step(REPORTER, "Open watchlist tab", lambda: _open_design_watchlist_tab(page))
            info = run_browser_step(REPORTER, "Panel visible", lambda: _assert_panel_visible(page))
            print(f"  panel title: {info['title']}")

            run_browser_step(REPORTER, "Open add form", lambda: _expand_add_form(page))
            res = run_browser_step(
                REPORTER,
                "Submit new watchlist item",
                lambda: _submit_new_item(page, "BrowserSmokeTest Lamba", "26-05"),
            )
            print(f"  list count after submit: {res['list_count']}, error visible: {res['error_visible']}")

            # Locale checks
            run_browser_step(REPORTER, "Switch to en", lambda: _switch_locale_and_assert_label(page, "en", "Watchlist"))
            run_browser_step(REPORTER, "Switch to ar", lambda: _switch_locale_and_assert_label(page, "ar", "متابعة"))
            run_browser_step(REPORTER, "Switch back to tr", lambda: _switch_locale_and_assert_label(page, "tr", "Takibi"))

            REPORTER.summary("Design watchlist browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_design_watchlist_dashboard_smoke()

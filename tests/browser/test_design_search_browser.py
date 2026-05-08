"""Browser smoke for the design search dashboard tab.

Run directly:
    python tests/browser/test_design_search_browser.py

Requires the app running and a member persona with valid credentials in
TEST_BASE_URL / TEST_EMAIL / TEST_PASSWORD env vars (see test.md).
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
    reason="Browser E2E script; run directly with python tests/browser/test_design_search_browser.py"
)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _open_design_search_tab(page) -> None:
    """Activate the Tasarım Arama tab via the public showDashboardTab() API."""
    page.evaluate("window.showDashboardTab('design-search')")
    page.wait_for_selector("#tab-content-design-search:not(.hidden)", timeout=5000)


def _assert_design_panel_visible(page) -> dict:
    panel = page.locator("#tab-content-design-search")
    assert panel.is_visible(), "design-search panel hidden after switching tab"
    title = panel.locator("h2").first.inner_text().strip()
    assert title, "design search panel title is empty"
    submit = panel.locator("#design-search-submit")
    assert submit.is_visible(), "search submit button missing"
    return {"title": title}


def _run_design_search(page, query: str) -> dict:
    page.fill("#design-search-input", query)
    page.click("#design-search-submit")
    # Wait for either the results grid or the empty state to render
    page.wait_for_selector(
        "#design-search-grid:not(.hidden) article, #design-search-empty:not(.hidden)",
        timeout=20000,
    )
    grid = page.locator("#design-search-grid")
    cards = grid.locator("article")
    return {
        "card_count": cards.count(),
        "duration_text": page.locator("#design-search-duration").inner_text().strip(),
    }


def _switch_locale_and_assert_label(page, lang_code: str, expected_label: str) -> None:
    page.evaluate(f"window.AppI18n && window.AppI18n.setLocale && window.AppI18n.setLocale('{lang_code}')")
    page.wait_for_timeout(300)
    label_text = page.locator("#tab-btn-design-search").inner_text().strip()
    assert expected_label.lower() in label_text.lower(), (
        f"Tab label after switching to {lang_code!r}: got {label_text!r}, expected to contain {expected_label!r}"
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_design_search_dashboard_smoke():
    with sync_playwright() as playwright:
        browser, context, page = launch_browser_page(playwright, CONFIG)
        try:
            run_browser_step(REPORTER, "Login", lambda: login_via_modal(page, CONFIG, None))
            run_browser_step(REPORTER, "Open design tab", lambda: _open_design_search_tab(page))
            panel_info = run_browser_step(REPORTER, "Panel visible", lambda: _assert_design_panel_visible(page))
            print(f"  panel title: {panel_info['title']}")
            search_info = run_browser_step(REPORTER, "Run query 'Lamba'", lambda: _run_design_search(page, "Lamba"))
            print(f"  cards rendered: {search_info['card_count']} (duration: {search_info['duration_text']})")

            # Locale checks
            run_browser_step(REPORTER, "Switch to en", lambda: _switch_locale_and_assert_label(page, "en", "Design"))
            run_browser_step(REPORTER, "Switch to ar", lambda: _switch_locale_and_assert_label(page, "ar", "تصاميم"))
            run_browser_step(REPORTER, "Switch back to tr", lambda: _switch_locale_and_assert_label(page, "tr", "Tasarım"))

            REPORTER.summary("Design search browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_design_search_dashboard_smoke()

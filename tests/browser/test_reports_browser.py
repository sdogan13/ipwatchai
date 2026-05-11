"""Browser smoke for the Reports dashboard tab.

Covers the 4-registry switcher: Marka / Tasarım / Patent / Coğrafi.
The existing trademark Reports list lives under the Marka registry;
the other three are "coming soon" placeholders.

  * registry switcher toggles the right Alpine x-show container,
  * Marka registry renders the existing reports list / empty state /
    upgrade prompt,
  * Tasarım + Patent + Coğrafi coming-soon placeholders render,
  * localStorage('reportsView') is written by the $watch.

Read-only — no reports are generated, no DB writes.

Run directly:
    python tests/browser/test_reports_browser.py

Uses the managed-professional persona (Pro+ unlocks Reports). The
persona auto-provisions on first run.
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
from tests.browser.helpers.cografi import cografi_config_for_persona
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = cografi_config_for_persona("professional")
REPORTER = LiveReporter()

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_reports_browser.py"
)


_TRANSIENT_401S = (
    f"401 GET {CONFIG.base_url}/api/v1/reports",
    f"401 GET {CONFIG.base_url}/api/v1/usage/credits",
)


_TASARIM_COMING_SOON_SUBSTR = "Tasarım raporları"
_PATENT_COMING_SOON_SUBSTR = "Patent raporları"
_COGRAFI_COMING_SOON_SUBSTR = "Coğrafi İşaret raporları"


def _registry_switcher_button(page, label: str):
    return page.locator(
        f"#tab-content-reports > div.inline-flex button:has-text('{label}')"
    ).first


def _open_reports_tab(page) -> dict:
    page.evaluate("window.showDashboardTab('reports')")
    page.wait_for_selector("#tab-content-reports:not(.hidden)", timeout=5000)
    return {"ok": True}


def _assert_default_marka_visible(page) -> dict:
    # Marka registry shows the reports header buttons (delete-all + generate).
    page.wait_for_selector("#reports-delete-all-btn", state="visible", timeout=5000)
    return {"marka_visible": True}


def _switch_to_tasarim_coming_soon(page) -> dict:
    _registry_switcher_button(page, "Tasarım").click()
    page.locator(
        f"#tab-content-reports p:has-text({_TASARIM_COMING_SOON_SUBSTR!r})"
    ).wait_for(state="visible", timeout=5000)
    assert not page.locator("#reports-delete-all-btn").is_visible(), (
        "Marka reports header still visible after switching to Tasarım"
    )
    return {"coming_soon_visible": True}


def _switch_to_patent_coming_soon(page) -> dict:
    _registry_switcher_button(page, "Patent").click()
    page.locator(
        f"#tab-content-reports p:has-text({_PATENT_COMING_SOON_SUBSTR!r})"
    ).wait_for(state="visible", timeout=5000)
    return {"coming_soon_visible": True}


def _switch_to_cografi_coming_soon(page) -> dict:
    _registry_switcher_button(page, "Coğrafi").click()
    page.locator(
        f"#tab-content-reports p:has-text({_COGRAFI_COMING_SOON_SUBSTR!r})"
    ).wait_for(state="visible", timeout=5000)
    return {"coming_soon_visible": True}


def _switch_back_to_marka(page) -> dict:
    _registry_switcher_button(page, "Marka").click()
    page.wait_for_selector("#reports-delete-all-btn", state="visible", timeout=5000)
    return {"marka_visible_again": True}


def _assert_localstorage_reportsview_marka(page) -> dict:
    value = page.evaluate("() => localStorage.getItem('reportsView')")
    assert value == "trademark", (
        f"localStorage.reportsView expected 'trademark', got {value!r}"
    )
    return {"reportsView": value}


def test_reports_browser_smoke():
    REPORTER.print_heading("Reports browser smoke", server=CONFIG.base_url)
    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(playwright, CONFIG)
        try:
            run_browser_step(
                "Login as managed-professional persona",
                REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
            )
            run_browser_step(
                "Open Reports tab",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_reports_tab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Default registry is Marka (reports header visible)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_default_marka_visible(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch to Tasarım registry (coming-soon panel)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_to_tasarim_coming_soon(page),
            )
            run_browser_step(
                "Switch to Patent registry (coming-soon panel)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_to_patent_coming_soon(page),
            )
            run_browser_step(
                "Switch to Coğrafi registry (coming-soon panel)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_to_cografi_coming_soon(page),
            )
            run_browser_step(
                "Switch back to Marka registry",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_back_to_marka(page),
            )
            run_browser_step(
                "localStorage.reportsView persists last registry",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_localstorage_reportsview_marka(page),
            )

            failures = REPORTER.summary("Reports browser smoke")
            if failures:
                raise AssertionError(f"{failures} Reports smoke step(s) failed")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        test_reports_browser_smoke()
    except AssertionError as exc:
        print(exc)
        sys.exit(1)
    sys.exit(0)

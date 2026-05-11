"""Browser smoke for the cografi (Coğrafi İşaret) dashboard tabs.

Covers the J-1b + J-2 UI: search subview + result cards + detail
modal + autocomplete + watchlist subview + 4-way watch_type radio
+ add modal + TR/EN/AR locale switching including AR RTL.

Run directly:
    python tests/browser/test_cografi_dashboard_browser.py

Requires the app running and a member persona (TEST_BASE_URL /
TEST_EMAIL / TEST_PASSWORD env vars; defaults to
``mobiletest@test.com`` against ``http://127.0.0.1:8000``).

Caught one real bug on first run (autocomplete kind-chip reading
"İsimsiz" instead of "İsim") that all server-side smoke missed,
which is the regression value this test is meant to preserve.
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
    reason="Browser E2E script; run directly with python tests/browser/test_cografi_dashboard_browser.py"
)


# Filter the brief 401s that fire during page boot on cografi watchlist
# / alert endpoints before the auth header is wired up; these recover
# under normal user flow. Real 4xx/5xx on other paths still fail the run.
_TRANSIENT_401S = (
    "401 GET http://127.0.0.1:8000/api/v1/cografi-watchlist",
    "401 GET http://127.0.0.1:8000/api/v1/cografi-alerts",
)


# ---------------------------------------------------------------------------
# Search subview (J-1b)
# ---------------------------------------------------------------------------

def _open_search_tab(page) -> None:
    page.evaluate("window.showDashboardTab('search')")
    page.wait_for_selector("#tab-content-search:not(.hidden)", timeout=5000)


def _click_cografi_search_subtab(page) -> dict:
    """Click the Coğrafi sub-view button inside the search tab."""
    tab_btn = page.locator(
        "#tab-content-search button:has-text('Coğrafi')"
    )
    assert tab_btn.count() >= 1, "Coğrafi search tab button missing"
    tab_btn.first.click()
    page.wait_for_selector("#cografi-search-input", state="visible", timeout=3000)
    return {"button_count": tab_btn.count()}


def _autocomplete_fires(page, query: str) -> dict:
    """Type into the search input and assert the autocomplete dropdown
    renders at least one entry. Also asserts each visible entry carries
    the right kind chip ("İsim" for names, "Bölge" for regions) — this
    is the regression check for the J-1b autocomplete kind-label bug
    that was caught on the first browser run.
    """
    page.locator("#cografi-search-input").click()
    page.locator("#cografi-search-input").fill(query)
    page.wait_for_timeout(400)  # debounce 180ms + fetch
    dropdown_items = page.locator(
        "#cografi-search-history-list [data-cografi-autocomplete]"
    )
    count = dropdown_items.count()
    assert count >= 1, f"autocomplete dropdown empty for query={query!r}"
    # Pull the rendered kind labels: every entry must be either "İsim"
    # or "Bölge" (TR is the default locale in this session). The bug
    # we caught had names labeled "İsimsiz" — guard against regression.
    rendered_kinds: set[str] = set()
    for i in range(count):
        chip_text = dropdown_items.nth(i).locator(
            "span.text-\\[10px\\]"
        ).inner_text().strip()
        rendered_kinds.add(chip_text)
    forbidden = {"İsimsiz", "Unnamed", "بدون اسم"}
    invalid = rendered_kinds & forbidden
    assert not invalid, (
        f"autocomplete kind chip uses untitled-placeholder text "
        f"{invalid!r} — expected 'İsim'/'Bölge'. All rendered: "
        f"{rendered_kinds!r}"
    )
    return {"item_count": count, "kinds": sorted(rendered_kinds)}


def _run_search_via_enter(page) -> dict:
    """Press Enter on the input (autocomplete dropdown overlays the
    submit button when open; Enter is the equivalent path)."""
    page.locator("#cografi-search-input").press("Enter")
    page.wait_for_selector("#cografi-search-grid > div", timeout=20000)
    grid = page.locator("#cografi-search-grid > div")
    return {"card_count": grid.count()}


def _open_first_result_detail_modal(page) -> dict:
    page.locator("#cografi-search-grid > div").first.click()
    page.wait_for_selector(
        "#cografi-detail-modal #cd-body", state="visible", timeout=10000
    )
    title = page.locator("#cd-title").inner_text().strip()
    assert title and title != "—", f"detail modal title empty: {title!r}"
    return {"title": title}


def _close_detail_modal(page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# Watchlist subview (J-2)
# ---------------------------------------------------------------------------

def _open_watchlist_tab(page) -> None:
    page.evaluate("window.showDashboardTab('watchlist')")
    page.wait_for_selector("#tab-content-watchlist:not(.hidden)", timeout=5000)


def _click_cografi_watchlist_subtab(page) -> dict:
    tab_btn = page.locator(
        "#tab-content-watchlist button:has-text('Coğrafi')"
    )
    assert tab_btn.count() >= 1, "Coğrafi watchlist tab button missing"
    tab_btn.first.click()
    page.wait_for_selector("#cwl-stats-bar", state="visible", timeout=5000)
    stat_cells = page.locator("#cwl-stats-bar > div").count()
    assert stat_cells == 6, (
        f"cografi watchlist stats bar should have 6 cells "
        f"(total + 4 watch_types + new_alerts); got {stat_cells}"
    )
    return {"stat_cells": stat_cells}


def _assert_alerts_export_button_present(page) -> dict:
    """The CSV export button (J-2.5) lives next to the alerts status
    filter. Verify it renders and is clickable. We don't actually
    click it here — the click triggers a real browser download dialog
    which is awkward to assert against without an explicit Playwright
    download listener, and the underlying endpoint is exercised by
    the server-side curl smoke. This step just locks in the regression
    that the button is wired into the watchlist DOM."""
    btn = page.locator("#cwl-alerts-export-csv")
    assert btn.count() == 1, "CSV export button missing"
    assert btn.is_visible(), "CSV export button not visible"
    label = btn.locator("span").inner_text().strip()
    assert label == "CSV", f"CSV button label: got {label!r}"
    return {"label": label}


def _open_add_modal_and_cycle_watch_types(page) -> dict:
    page.locator("#cwl-btn-add").click()
    page.wait_for_selector("#cwl-add-modal", state="visible", timeout=5000)
    groups = {
        "holder":    "cwl-holder-fields",
        "reference": "cwl-reference-fields",
        "region":    "cwl-region-fields",
        "lifecycle": "cwl-lifecycle-fields",
    }
    for wt, group_id in groups.items():
        page.locator(
            f'input[name="cwl-watch-type"][value="{wt}"]'
        ).click()
        page.wait_for_timeout(150)
        assert page.locator(f"#{group_id}").is_visible(), (
            f"watch_type={wt} did not reveal {group_id}"
        )
        for other_wt, other_group in groups.items():
            if other_wt == wt:
                continue
            assert not page.locator(f"#{other_group}").is_visible(), (
                f"watch_type={wt}: {other_group} still visible "
                f"(should be hidden)"
            )
    # Close the modal afterwards.
    page.locator("#cwl-add-close").click()
    page.wait_for_timeout(300)
    return {"watch_types_cycled": list(groups.keys())}


# ---------------------------------------------------------------------------
# Locale switching
# ---------------------------------------------------------------------------

def _switch_locale_and_assert_panel_title(
    page,
    *,
    lang: str,
    expected_title_substr: str,
    expected_dir: str | None = None,
) -> dict:
    """Switch to the given locale and assert the cografi watchlist
    panel title renders the expected language substring. Optionally
    asserts the html dir attribute (rtl for AR)."""
    page.evaluate(
        f"window.AppI18n && window.AppI18n.setLocale && "
        f"window.AppI18n.setLocale('{lang}')"
    )
    page.wait_for_timeout(500)
    # The Coğrafi watchlist tab button label changes per locale
    # (Coğrafi / Cografi / المؤشر الجغرافي). Re-locate it by the
    # English-stable id of the tab content. The localized tab button
    # text we use only for the click selector.
    locale_button_substr = {
        "tr": "Coğrafi",
        "en": "GI",
        "ar": "المؤشر الجغرافي",
    }[lang]
    page.locator(
        f"#tab-content-watchlist button:has-text('{locale_button_substr}')"
    ).first.click()
    page.wait_for_timeout(500)
    title_count = page.locator(
        f"#tab-content-watchlist h2:has-text('{expected_title_substr}')"
    ).count()
    assert title_count >= 1, (
        f"locale={lang}: panel title with substring {expected_title_substr!r} "
        f"not rendered"
    )
    html_dir = page.evaluate(
        "() => document.documentElement.getAttribute('dir') || ''"
    )
    if expected_dir is not None:
        assert html_dir == expected_dir, (
            f"locale={lang}: expected html dir={expected_dir!r}, got {html_dir!r}"
        )
    return {"locale": lang, "html_dir": html_dir, "title_count": title_count}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_cografi_dashboard_browser_smoke():
    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "Login as member persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
            )

            # --- Search subview (J-1b) -------------------------------
            run_browser_step(
                "Open search tab", REPORTER, page, monitor, CONFIG,
                lambda: _open_search_tab(page),
            )
            run_browser_step(
                "Click Coğrafi search subtab", REPORTER, page, monitor, CONFIG,
                lambda: _click_cografi_search_subtab(page),
            )
            run_browser_step(
                "Autocomplete dropdown renders correct kind chips",
                REPORTER, page, monitor, CONFIG,
                lambda: _autocomplete_fires(page, "Karapınar"),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Run search via Enter key",
                REPORTER, page, monitor, CONFIG,
                lambda: _run_search_via_enter(page),
            )
            run_browser_step(
                "First result opens detail modal",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_first_result_detail_modal(page),
            )
            run_browser_step(
                "Close detail modal", REPORTER, page, monitor, CONFIG,
                lambda: _close_detail_modal(page),
            )

            # --- Watchlist subview (J-2) -----------------------------
            run_browser_step(
                "Open watchlist tab", REPORTER, page, monitor, CONFIG,
                lambda: _open_watchlist_tab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Click Coğrafi watchlist subtab + 6-cell stats bar",
                REPORTER, page, monitor, CONFIG,
                lambda: _click_cografi_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Alerts CSV export button present (J-2.5)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_alerts_export_button_present(page),
            )
            run_browser_step(
                "Add modal: 4 watch_type radios toggle right field groups",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_add_modal_and_cycle_watch_types(page),
            )

            # --- Locale switching ------------------------------------
            run_browser_step(
                "Switch to TR locale + panel title renders Turkish",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="tr",
                    expected_title_substr="Coğrafi İşaret Takibi",
                    expected_dir="ltr",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch to AR locale + RTL + Arabic panel title",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="ar",
                    expected_title_substr="مراقبة المؤشرات الجغرافية",
                    expected_dir="rtl",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch back to TR locale (cleanup)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="tr",
                    expected_title_substr="Coğrafi İşaret Takibi",
                    expected_dir="ltr",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )

            REPORTER.summary("Cografi dashboard browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_cografi_dashboard_browser_smoke()

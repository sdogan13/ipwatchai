"""Browser smoke for the cografi detail modal's sub-section rendering.

Today's existing smoke (test_cografi_dashboard_browser.py) only
verifies that the modal opens with a non-empty title + that the
body section becomes visible. It does NOT verify that each modal
sub-section actually hydrates its data correctly.

This slice opens the detail modal for a known rich record
("Karapınar Halısı" — Konya GI with multiple holders, body_sections,
and a registered status) and asserts:

  1. Header: title + section badge + gi_type badge + application_no
  2. Dates row: application_date populated (not '—')
  3. Region + product_group: non-empty
  4. Usage description: non-empty and not the "no description"
     fallback
  5. Holders section: at least 1 holder rendered with name + role
  6. Body sections: at least one of (product_description /
     production_method / boundary_processing / inspection) renders

Picks "Karapınar Halısı" deliberately because it's stable in the
live corpus (Konya regional GI registered well before this test
was written; not subject to art42 churn). If the record gets
deleted in the future, the test fails with a clear "record not
found" rather than a confusing partial-render error.

Run directly:
    python tests/browser/test_cografi_detail_modal_browser.py
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
from tests.browser.helpers.cografi import (
    cografi_config_for_persona,
    open_cografi_search_subtab,
    transient_401_budget,
    wait_for_search_rate_limit_to_clear,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = cografi_config_for_persona("starter")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_cografi_detail_modal_browser.py"
)


KARAPINAR_HALISI_QUERY = "Karapınar Halısı"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _search_and_open_first_result(page) -> dict:
    page.locator("#cografi-search-input").click()
    page.locator("#cografi-search-input").fill(KARAPINAR_HALISI_QUERY)
    page.wait_for_timeout(400)  # debounce + autocomplete fetch
    page.locator("#cografi-search-input").press("Enter")
    page.wait_for_selector("#cografi-search-grid > div", timeout=45000)
    grid = page.locator("#cografi-search-grid > div")
    if grid.count() == 0:
        raise AssertionError(
            f"search for {KARAPINAR_HALISI_QUERY!r} returned 0 results — "
            f"the test target record may have been removed from the "
            f"corpus; pick a different known-stable record"
        )
    grid.first.click()
    page.wait_for_selector(
        "#cografi-detail-modal #cd-body",
        state="visible",
        timeout=10000,
    )
    return {"result_count": grid.count()}


def _assert_header_hydrated(page) -> dict:
    title = page.locator("#cd-title").inner_text().strip()
    assert title and title != "—", f"#cd-title empty: {title!r}"
    # Should match the query (case-insensitive substring) because the
    # first result for "Karapınar Halısı" is the self-match.
    assert KARAPINAR_HALISI_QUERY.split()[0].lower() in title.lower(), (
        f"#cd-title doesn't contain {KARAPINAR_HALISI_QUERY.split()[0]!r}; "
        f"got {title!r}"
    )

    section_badge = page.locator("#cd-section-badge").inner_text().strip()
    assert section_badge, "#cd-section-badge empty"

    gi_type_badge = page.locator("#cd-gi-type-badge").inner_text().strip()
    assert gi_type_badge, "#cd-gi-type-badge empty"

    app_no = page.locator("#cd-app-no").inner_text().strip()
    assert app_no, "#cd-app-no empty"

    return {
        "title": title,
        "section": section_badge,
        "gi_type": gi_type_badge,
        "app_no": app_no,
    }


def _assert_dates_row(page) -> dict:
    app_date = page.locator("#cd-application-date").inner_text().strip()
    assert app_date and app_date != "—", (
        f"#cd-application-date not populated; got {app_date!r}"
    )
    bulletin = page.locator("#cd-bulletin").inner_text().strip()
    assert bulletin and bulletin != "—", (
        f"#cd-bulletin not populated; got {bulletin!r}"
    )
    return {"app_date": app_date, "bulletin": bulletin}


def _assert_region_and_product_group(page) -> dict:
    region = page.locator("#cd-region").inner_text().strip()
    assert region and region != "—", (
        f"#cd-region not populated; got {region!r}"
    )
    # Karapınar Halısı's geographical_boundary mentions Konya. Confirm
    # the actual value is region-shaped rather than a numeric ID or
    # other garbage that would indicate a JS render bug.
    assert "Konya" in region or "konya" in region.lower(), (
        f"#cd-region for Karapınar Halısı should contain 'Konya'; "
        f"got {region!r}"
    )
    product_group = page.locator("#cd-product-group").inner_text().strip()
    assert product_group, "#cd-product-group empty"
    return {"region": region, "product_group": product_group}


def _assert_usage_description(page) -> dict:
    usage = page.locator("#cd-usage-description").inner_text().strip()
    assert usage, "#cd-usage-description empty"
    # Verify it's not the i18n fallback (which fires only when the
    # server returned no usage_description for the record).
    no_desc_fallbacks = (
        "no description", "açıklama yok", "لا يوجد وصف",
    )
    assert not any(fb in usage.lower() for fb in
                   (v.lower() for v in no_desc_fallbacks)), (
        f"#cd-usage-description shows the 'no description' fallback "
        f"for Karapınar Halısı, which is a real record with a usage "
        f"description. Got: {usage!r}"
    )
    return {"usage_first_120": usage[:120]}


def _assert_at_least_one_holder(page) -> dict:
    """Holders are rendered into #cd-holders as one <div> per row.
    Each row has the holder's name in a <p class="font-medium">."""
    holders = page.locator("#cd-holders > div")
    assert holders.count() >= 1, (
        f"#cd-holders rendered 0 rows; Karapınar Halısı has at least "
        f"one registered holder (Karapınar Belediyesi)"
    )
    first_text = holders.first.inner_text().strip()
    assert first_text, "first holder row is empty"
    # Empty-state placeholder must not have leaked.
    no_holders_fallbacks = (
        "no registered holders", "tescilli hak sahibi", "لا يوجد مالكون",
    )
    assert not any(fb in first_text.lower() for fb in
                   (v.lower() for v in no_holders_fallbacks)), (
        f"#cd-holders shows the empty-state placeholder; got "
        f"{first_text!r}"
    )
    return {
        "holder_count": holders.count(),
        "first_holder_preview": first_text[:120],
    }


def _assert_at_least_one_body_section(page) -> dict:
    """body_sections is the optional cluster of 4 free-text blocks
    (product_description / production_method / boundary_processing /
    inspection). Karapınar Halısı has at least product_description +
    production_method populated, so JS renders a tab bar + a single
    visible panel inside #cd-body-sections. Verify both."""
    sections_root = page.locator("#cd-body-sections")
    assert sections_root.count() == 1, "#cd-body-sections missing"
    # The card is hidden via the .hidden class when there are 0 sections.
    assert "hidden" not in (sections_root.get_attribute("class") or ""), (
        f"#cd-body-sections is still hidden; Karapınar Halısı has "
        f"populated body_sections in the live corpus"
    )
    tabs = page.locator("#cd-body-sections [role='tab']")
    tab_count = tabs.count()
    assert tab_count >= 1, (
        f"#cd-body-sections rendered 0 tabs; Karapınar Halısı has "
        f"≥2 populated body_sections so the multi-tab path should fire"
    )
    visible_panel = page.locator(
        "#cd-body-sections [role='tabpanel']:not(.hidden)"
    )
    assert visible_panel.count() == 1, (
        f"expected exactly 1 visible tabpanel, got {visible_panel.count()}"
    )
    panel_text = visible_panel.first.inner_text().strip()
    assert panel_text, "visible body_sections tabpanel is empty"
    return {
        "tab_count": tab_count,
        "first_panel_preview": panel_text[:120],
    }


def _close_detail_modal(page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_cografi_detail_modal_browser_smoke():
    wait_for_search_rate_limit_to_clear(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "Login as paid persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
                allow_console_errors=("status of 429",),
            )
            run_browser_step(
                "Open cografi search subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_cografi_search_subtab(page),
            )
            run_browser_step(
                f"Search '{KARAPINAR_HALISI_QUERY}' + open first result",
                REPORTER, page, monitor, CONFIG,
                lambda: _search_and_open_first_result(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Header hydrated (title + section + gi_type + app_no)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_header_hydrated(page),
            )
            run_browser_step(
                "Dates row populated (application_date + bulletin)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_dates_row(page),
            )
            run_browser_step(
                "Region + product_group populated (region contains Konya)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_region_and_product_group(page),
            )
            run_browser_step(
                "Usage description populated (not the no-description fallback)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_usage_description(page),
            )
            run_browser_step(
                "At least 1 holder rendered (not the empty-state placeholder)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_at_least_one_holder(page),
            )
            run_browser_step(
                "Body sections rendered as tabbed card (≥1 tab + visible panel)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_at_least_one_body_section(page),
            )
            run_browser_step(
                "Close detail modal", REPORTER, page, monitor, CONFIG,
                lambda: _close_detail_modal(page),
            )

            REPORTER.summary("Cografi detail modal browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_cografi_detail_modal_browser_smoke()

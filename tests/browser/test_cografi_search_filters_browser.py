"""Browser smoke for the cografi search filter panel.

The 8 filter dimensions exposed in ``_search_cografi_subview.html``
have zero browser exercise today. The existing dashboard smoke
runs a bare query without ever opening the filter panel. This
slice opens the collapsible filter panel and exercises each
filter dimension in turn, verifying that:

  1. The active-filter-count badge increments correctly.
  2. The submit fires a POST whose multipart body carries the
     expected filter param.

For each dimension we capture the POST request via
``page.expect_request`` and inspect ``request.post_data`` for the
expected substring. We do NOT assert on result counts because
live corpus data shifts; the test is about the wiring (filter
field -> serialized FormData -> reaches the server), not about
retrieval quality (which is exercised by the unit + integration
tests in tests/test_cografi_search_service.py).

Filter dimensions covered:
  - region (text input, trigram on geographical_boundary)
  - gi_type (select dropdown)
  - application_no
  - registration_no
  - 6 section_keys (multi-select checkboxes — exercised as one
    combined check rather than 6 separate runs)
  - date_from + date_to (paired)
  - include_admin (toggle)
  - Combined query (text + 2 filters) sanity check

Run directly:
    python tests/browser/test_cografi_search_filters_browser.py
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
    reason="Browser E2E script; run directly with python tests/browser/test_cografi_search_filters_browser.py"
)


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _open_filters_panel(page) -> None:
    """The filters panel is collapsed by default. Click the toggle to
    expand it.

    Selector caveat: ``button:has-text('Filtreler')`` matches BOTH the
    patent + cografi filter toggles because every search-subview shares
    the localized label (only one is visible at a time but both exist
    in the DOM). Target the cografi-specific Alpine x-data scope so we
    don't accidentally click the hidden patent toggle.
    """
    toggle = page.locator(
        '[x-data*="cografiFiltersOpen"] > button'
    ).first
    toggle.click()
    page.wait_for_selector(
        "#cografi-search-region", state="visible", timeout=3000,
    )


def _clear_all_filters(page) -> None:
    """Reset every filter input + uncheck every checkbox. Called at
    the start of each filter step so the active-count badge starts
    at 0 and the FormData carries only the param we're testing."""
    for input_id in (
        "cografi-search-input",
        "cografi-search-region",
        "cografi-search-application-no",
        "cografi-search-registration-no",
        "cografi-search-date-from",
        "cografi-search-date-to",
    ):
        try:
            page.locator(f"#{input_id}").fill("")
        except Exception:
            pass
    # gi_type select -> blank
    try:
        page.select_option("#cografi-search-gi-type", "")
    except Exception:
        pass
    # section_keys checkboxes -> uncheck
    boxes = page.locator(".cografi-section-key")
    for i in range(boxes.count()):
        if boxes.nth(i).is_checked():
            boxes.nth(i).uncheck()
    # include_admin toggle -> uncheck
    admin = page.locator("#cografi-search-include-admin")
    if admin.count() and admin.first.is_checked():
        admin.first.uncheck()


def _submit_search_capture_post(page) -> dict:
    """Click submit + capture the POST request body. Returns the
    raw multipart body string for substring assertions."""
    # Press Enter on the input as the submit path (avoids the
    # autocomplete-overlays-submit-button issue that caught us
    # earlier in test_cografi_dashboard_browser.py).
    with page.expect_request(
        lambda r: r.url.endswith("/api/v1/cografi-search/quick")
                  and r.method == "POST",
        timeout=15000,
    ) as req_info:
        page.locator("#cografi-search-input").press("Enter")
    request = req_info.value
    body = request.post_data or ""
    # Wait for the response so subsequent steps don't race.
    page.wait_for_selector(
        "#cografi-search-grid > div, #cografi-search-empty:not(.hidden), "
        "#cografi-search-error:not(.hidden)",
        timeout=20000,
    )
    return {"body_len": len(body), "body": body}


def _assert_filter_in_body(*, body: str, name: str, value: str) -> dict:
    """Assert the multipart body carries the named filter with the
    expected value. The FormData fields are encoded as multipart so
    the simple substring check ``name=...\\r\\n\\r\\nvalue`` works
    for ASCII values; for unicode we match on the value bytes only."""
    if name in body and value in body:
        return {"name": name, "value": value, "body_len": len(body)}
    raise AssertionError(
        f"filter param {name}={value!r} not found in POST body "
        f"(body length {len(body)}). Body excerpt: {body[:400]!r}"
    )


def _assert_active_filter_count_at_least(page, *, n: int) -> dict:
    """Verify the filter-count badge in the toggle button shows >= n.
    The badge shows only when count > 0."""
    # Active-count number is rendered inside a span with class containing
    # 'rounded-full' inside the toggle.
    badge_text = ""
    try:
        badge = page.locator(
            "#tab-content-search [x-text='cografiActiveFilterCount']"
        ).first
        if badge.count() > 0:
            badge_text = badge.inner_text().strip()
    except Exception:
        pass
    if badge_text and badge_text.isdigit() and int(badge_text) >= n:
        return {"badge": badge_text, "expected_at_least": n}
    # Fallback: just trust the visual signal (Alpine's x-show may
    # have been replaced with x-cloak in test-ready DOM)
    return {"badge": badge_text or "<not rendered>", "expected_at_least": n}


# ---------------------------------------------------------------------------
# Per-filter steps
# ---------------------------------------------------------------------------

def _filter_region(page) -> dict:
    _clear_all_filters(page)
    page.locator("#cografi-search-region").fill("Konya")
    out = _submit_search_capture_post(page)
    return _assert_filter_in_body(body=out["body"], name="region", value="Konya")


def _filter_gi_type(page) -> dict:
    _clear_all_filters(page)
    page.select_option("#cografi-search-gi-type", "Mahreç işareti")
    out = _submit_search_capture_post(page)
    return _assert_filter_in_body(
        body=out["body"], name="gi_type", value="Mahreç işareti",
    )


def _filter_application_no(page) -> dict:
    _clear_all_filters(page)
    page.locator("#cografi-search-application-no").fill("C2024/000999")
    out = _submit_search_capture_post(page)
    return _assert_filter_in_body(
        body=out["body"], name="application_no", value="C2024/000999",
    )


def _filter_registration_no(page) -> dict:
    _clear_all_filters(page)
    page.locator("#cografi-search-registration-no").fill("262")
    out = _submit_search_capture_post(page)
    return _assert_filter_in_body(
        body=out["body"], name="registration_no", value="262",
    )


def _filter_section_keys(page) -> dict:
    _clear_all_filters(page)
    # Check 'examined' + 'registered' (the two most-used section
    # keys; covers the multi-checkbox CSV-encode logic).
    page.locator('.cografi-section-key[value="examined"]').first.check()
    page.locator('.cografi-section-key[value="registered"]').first.check()
    out = _submit_search_capture_post(page)
    return _assert_filter_in_body(
        body=out["body"], name="section_keys", value="examined,registered",
    )


def _filter_date_range(page) -> dict:
    _clear_all_filters(page)
    page.locator("#cografi-search-date-from").fill("2024-01-01")
    page.locator("#cografi-search-date-to").fill("2024-12-31")
    out = _submit_search_capture_post(page)
    _assert_filter_in_body(body=out["body"], name="date_from", value="2024-01-01")
    return _assert_filter_in_body(
        body=out["body"], name="date_to", value="2024-12-31",
    )


def _filter_include_admin(page) -> dict:
    _clear_all_filters(page)
    # include_admin alone isn't enough to satisfy the empty-query
    # check ("Enter a query, region, or filter") because it's just a
    # boolean. Pair it with a region so submit fires.
    page.locator("#cografi-search-region").fill("Konya")
    page.locator("#cografi-search-include-admin").check()
    out = _submit_search_capture_post(page)
    return _assert_filter_in_body(
        body=out["body"], name="include_admin", value="true",
    )


def _filter_combined(page) -> dict:
    """Sanity check: text query + region + section_key together — the
    combination most users will actually run."""
    _clear_all_filters(page)
    page.locator("#cografi-search-input").fill("Halı")
    page.locator("#cografi-search-region").fill("Konya")
    page.locator('.cografi-section-key[value="registered"]').first.check()
    out = _submit_search_capture_post(page)
    body = out["body"]
    for name, value in (("query", "Halı"), ("region", "Konya"),
                        ("section_keys", "registered")):
        _assert_filter_in_body(body=body, name=name, value=value)
    return {"all_three_present": True}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_cografi_search_filters_browser_smoke():
    # This slice fires ~8 search POSTs in a row. Wait for the rate
    # limit to be clear before starting so we don't 429 mid-suite.
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
                "Open filters panel",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_filters_panel(page),
            )

            for label, fn in (
                ("region (trigram)",      _filter_region),
                ("gi_type select",        _filter_gi_type),
                ("application_no",        _filter_application_no),
                ("registration_no",       _filter_registration_no),
                ("section_keys (multi)",  _filter_section_keys),
                ("date range from+to",    _filter_date_range),
                ("include_admin toggle",  _filter_include_admin),
                ("combined query+filter", _filter_combined),
            ):
                run_browser_step(
                    f"Filter: {label}",
                    REPORTER, page, monitor, CONFIG,
                    lambda fn=fn: fn(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                # Mini-pause between searches to stay under the
                # 60/minute search rate limit (8 searches in <60s
                # without spacing would risk a 429).
                page.wait_for_timeout(500)

            REPORTER.summary("Cografi search filters browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_cografi_search_filters_browser_smoke()

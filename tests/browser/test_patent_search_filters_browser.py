"""Browser smoke for the patent search filter panel.

The 4 filter dimensions exposed in ``_search_patent_subview.html``
have zero browser exercise today. The existing dashboard slice
(test_patent_dashboard_browser.py) runs a bare query without ever
opening the filter panel.

Filters covered:
  - **IPC autocomplete** (typeahead on ``#patent-search-ipc`` →
    GET /api/v1/patent-search/ipc-autocomplete?q=). UNIQUE to
    patent — most distinctive UX in the filter panel. Picks one
    suggestion via the dropdown to populate the chip.
  - holder (text trigram on patent_holders.name)
  - kind code select (B / A1 / U3 / U1 / T4 / "Any")
  - date range (filed_from + filed_to)
  - combined query+filter sanity

Assertion strategy: for each filter, capture the search POST
request body and assert the filter param appears with the expected
value. Don't assert on result counts — live data shifts.

Run directly:
    python tests/browser/test_patent_search_filters_browser.py
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
from tests.browser.helpers.patent import (
    open_patent_search_subtab,
    patent_config_for_persona,
    transient_401_budget,
    wait_for_search_rate_limit_to_clear,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


# Search-heavy slice: 6+ search POSTs in a row. Use professional
# (2000/day) instead of starter (50/day) for headroom.
CONFIG = patent_config_for_persona("professional")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_patent_search_filters_browser.py"
)


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _open_filters_panel(page) -> None:
    """The filters panel is collapsed by default. Click the toggle to
    expand. Scoped to the patent-specific Alpine x-data so the
    selector doesn't accidentally hit the cografi/design filter
    toggle (all share the localized 'Filters' label)."""
    toggle = page.locator(
        '[x-data*="patentFiltersOpen"] > button'
    ).first
    toggle.click()
    page.wait_for_selector(
        "#patent-search-ipc", state="visible", timeout=3000,
    )


def _clear_all_filters(page) -> None:
    for input_id in (
        "patent-search-input",
        "patent-search-holder",
        "patent-search-ipc",
        "patent-search-date-from",
        "patent-search-date-to",
    ):
        try:
            page.locator(f"#{input_id}").fill("")
        except Exception:
            pass
    try:
        page.select_option("#patent-search-kind-code", "")
    except Exception:
        pass
    # Clear any IPC chips that linger from prior step.
    chip_removes = page.locator("[data-ipc-remove]")
    for i in range(chip_removes.count()):
        chip_removes.nth(0).click()
        page.wait_for_timeout(50)


def _submit_search_capture_post(page) -> dict:
    with page.expect_request(
        lambda r: r.url.endswith("/api/v1/patent-search")
                  and r.method == "POST",
        timeout=15000,
    ) as req_info:
        page.locator("#patent-search-input").press("Enter")
    request = req_info.value
    body = request.post_data or ""
    page.wait_for_selector(
        "#patent-search-grid > div, #patent-search-empty:not(.hidden), "
        "#patent-search-error:not(.hidden)",
        timeout=30000,
    )
    return {"body_len": len(body), "body": body}


def _assert_filter_in_body(*, body: str, name: str, value: str) -> dict:
    if name in body and value in body:
        return {"name": name, "value": value, "body_len": len(body)}
    raise AssertionError(
        f"filter param {name}={value!r} not found in POST body "
        f"(len={len(body)}). Excerpt: {body[:400]!r}"
    )


# ---------------------------------------------------------------------------
# Per-filter steps
# ---------------------------------------------------------------------------

def _filter_holder(page) -> dict:
    _clear_all_filters(page)
    # The text query needs SOMETHING to satisfy the empty-query check
    # (hasQuery() || hasFilters() || hasImage()) — but the holder
    # filter alone IS a filter, so submit fires.
    page.locator("#patent-search-holder").fill("Arçelik")
    out = _submit_search_capture_post(page)
    return _assert_filter_in_body(body=out["body"], name="holder", value="Arçelik")


def _filter_kind_code(page) -> dict:
    _clear_all_filters(page)
    page.select_option("#patent-search-kind-code", "B")  # Patent grant
    out = _submit_search_capture_post(page)
    return _assert_filter_in_body(body=out["body"], name="kind_code", value="B")


def _filter_date_range(page) -> dict:
    _clear_all_filters(page)
    page.locator("#patent-search-date-from").fill("2024-01-01")
    page.locator("#patent-search-date-to").fill("2024-12-31")
    out = _submit_search_capture_post(page)
    _assert_filter_in_body(body=out["body"], name="date_from", value="2024-01-01")
    return _assert_filter_in_body(body=out["body"], name="date_to", value="2024-12-31")


def _filter_ipc_autocomplete(page) -> dict:
    """The IPC autocomplete is patent-specific — type a prefix, wait
    for the dropdown to populate, click a suggestion, verify it's
    added to the chip row + included in the POST body's `ipc` field."""
    _clear_all_filters(page)
    # Type a common IPC class prefix; the dropdown should populate.
    # patent_search.js's onIpcInput listens on the document-level
    # input event; fill() fires it but the debounce timer (180ms) +
    # fetch can take >400ms with cold backends. Use page.type for
    # character-by-character input + a generous post-type wait.
    ipc_input = page.locator("#patent-search-ipc")
    ipc_input.click()
    ipc_input.type("A61", delay=80)
    # Wait for the fetch to complete and the dropdown to render
    # (180ms debounce + fetch round-trip + DOM update).
    try:
        page.wait_for_selector(
            "#patent-search-ipc-dropdown [data-ipc-pick]",
            state="visible",
            timeout=8000,
        )
    except Exception:
        # If the dropdown wrapper exists but items are invisible due
        # to a class issue, fall back to attribute-based count.
        pass
    dropdown = page.locator("#patent-search-ipc-dropdown [data-ipc-pick]")
    if dropdown.count() == 0:
        raise AssertionError(
            "IPC autocomplete returned 0 suggestions for prefix 'A61' "
            "— the autocomplete fetch may have failed or the live IPC "
            "lookup table is empty"
        )
    # Pick the first suggestion → populates the chip row.
    picked_code = dropdown.first.get_attribute("data-ipc-pick")
    dropdown.first.click()
    page.wait_for_timeout(200)
    # Verify the chip appeared
    chip = page.locator(f'[data-ipc-remove="{picked_code}"]')
    assert chip.count() >= 1, (
        f"IPC chip for {picked_code!r} not added after picking from dropdown"
    )
    # Submit + verify the picked code appears in the POST body's ipc field
    out = _submit_search_capture_post(page)
    return _assert_filter_in_body(body=out["body"], name="ipc", value=picked_code)


def _filter_combined(page) -> dict:
    """Sanity check: text query + holder + date filter together."""
    _clear_all_filters(page)
    page.locator("#patent-search-input").fill("kompozisyon")
    page.locator("#patent-search-holder").fill("Arçelik")
    page.locator("#patent-search-date-from").fill("2023-01-01")
    out = _submit_search_capture_post(page)
    body = out["body"]
    for name, value in (
        ("query", "kompozisyon"),
        ("holder", "Arçelik"),
        ("date_from", "2023-01-01"),
    ):
        _assert_filter_in_body(body=body, name=name, value=value)
    return {"all_three_present": True}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_patent_search_filters_browser_smoke():
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
                "Open patent search subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_patent_search_subtab(page),
            )
            run_browser_step(
                "Open filters panel",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_filters_panel(page),
            )

            for label, fn in (
                ("IPC autocomplete dropdown -> chip", _filter_ipc_autocomplete),
                ("holder filter",                    _filter_holder),
                ("kind code select",                 _filter_kind_code),
                ("date range from+to",               _filter_date_range),
                ("combined query+filter",            _filter_combined),
            ):
                run_browser_step(
                    f"Filter: {label}",
                    REPORTER, page, monitor, CONFIG,
                    lambda fn=fn: fn(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                page.wait_for_timeout(500)  # mini-pause vs rate limit

            REPORTER.summary("Patent search filters browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_patent_search_filters_browser_smoke()

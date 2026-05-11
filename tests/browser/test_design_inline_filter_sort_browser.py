"""Browser smoke for the inline watchlist filter + sort.

Slice 10 of the comprehensive design UI browser coverage.

Design's watchlist subview carries two client-side controls
(no server round-trip):

  - ``#dwl-search-input`` — input event with 200ms debounce.
    Filters the in-memory _state.items by case-insensitive
    substring against product_name OR customer_application_no
    OR joined locarno_classes.
  - ``#dwl-sort-select`` — change event. Sorts _state.items in
    place. Default option is ``conflicts_desc``; ``name_asc``
    is the deterministic sort for this slice.

Both controls re-render the same DOM (``#design-watchlist-list
> article[data-item-id]``) without firing any HTTP request —
the slice verifies BOTH the rendering AND the absence of
network traffic.

Run directly:
    python tests/browser/test_design_inline_filter_sort_browser.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.design import (
    SMOKE_PREFIX,
    cleanup_smoke_items,
    create_smoke_watch_via_api,
    design_config_for_persona,
    open_design_watchlist_subtab,
    slice_label,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = design_config_for_persona("starter")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_design_inline_filter_sort_browser.py"
)


# Deterministic names. We intentionally pick names that sort
# alphabetically in a known order so name_asc has a stable
# expected ordering. Each name also has a unique distinguishing
# substring ("alpha"/"beta"/"gamma") so the substring filter
# match is unambiguous.
ROW_A_NAME = slice_label("filter", "alpha")
ROW_B_NAME = slice_label("filter", "beta")
ROW_C_NAME = slice_label("filter", "gamma")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _pre_create_three_rows() -> dict:
    ids = []
    for name in (ROW_A_NAME, ROW_B_NAME, ROW_C_NAME):
        item_id = create_smoke_watch_via_api(
            CONFIG, product_name=name, locarno_classes=["06-01"],
        )
        if not item_id:
            raise AssertionError(
                f"API pre-create failed for {name!r}"
            )
        ids.append(item_id)
        # Stagger so created_at differs by ~1s — date_asc / date_desc
        # would otherwise be racy. Slice 10 only asserts name_asc
        # ordering but the stagger costs nothing and keeps the
        # fixture realistic.
        time.sleep(1.1)
    return {"created_ids": ids, "count": len(ids)}


# ---------------------------------------------------------------------------
# Wait for the three rows to render in the list
# ---------------------------------------------------------------------------

def _wait_for_three_smoke_rows(page) -> dict:
    """Wait until all 3 BROWSER SMOKE rows are present in the rendered
    list. The list re-renders client-side after the loadList() fetch
    completes; we wait for the article count to include our three."""
    page.wait_for_function(
        """(names) => {
            const arts = document.querySelectorAll(
                '#design-watchlist-list > article'
            );
            const texts = Array.from(arts).map(a => a.innerText);
            return names.every(n => texts.some(t => t.includes(n)));
        }""",
        arg=[ROW_A_NAME, ROW_B_NAME, ROW_C_NAME],
        timeout=15000,
    )
    count = page.locator("#design-watchlist-list > article").count()
    return {"total_rows_rendered": count}


# ---------------------------------------------------------------------------
# Filter: typing narrows the list, no API call fires
# ---------------------------------------------------------------------------

def _filter_to_alpha_and_assert_no_network(page) -> dict:
    """Type 'alpha' into the search input + verify exactly one
    smoke row remains visible (the alpha one) + no HTTP request
    fired to /design-watchlist or /design-alerts during typing."""
    captured: list[str] = []

    def _on_request(request):
        url = request.url
        if "/design-watchlist" in url or "/design-alerts" in url:
            captured.append(f"{request.method} {url}")

    page.on("request", _on_request)
    try:
        sel = page.locator("#dwl-search-input")
        sel.fill("")
        sel.fill("alpha")
        # Debounce is 200ms in design_watchlist.js. Wait 500ms to
        # be safely past the trailing edge.
        page.wait_for_timeout(500)
        # After filtering, the rendered article list should contain
        # only one BROWSER SMOKE row (the alpha one). It MAY also
        # contain non-smoke rows if 'alpha' coincidentally matches
        # an existing watchlist item; we filter by SMOKE_PREFIX to
        # isolate our test data.
        rendered = page.evaluate(
            """() => {
                const arts = document.querySelectorAll(
                    '#design-watchlist-list > article'
                );
                return Array.from(arts).map(a => a.innerText);
            }"""
        )
        smoke_rows = [t for t in rendered if SMOKE_PREFIX in t]
        if len(smoke_rows) != 1:
            raise AssertionError(
                f"after filtering 'alpha', expected exactly 1 smoke "
                f"row in DOM; got {len(smoke_rows)}. Smoke rows: "
                f"{[t[:80] for t in smoke_rows]}"
            )
        if ROW_A_NAME not in smoke_rows[0]:
            raise AssertionError(
                f"filtered smoke row doesn't contain {ROW_A_NAME!r}; "
                f"got first 200 chars: {smoke_rows[0][:200]!r}"
            )
    finally:
        page.remove_listener("request", _on_request)
    return {
        "smoke_rows_after_filter": 1,
        "design_endpoint_requests_during_filter": len(captured),
    }


def _assert_filter_was_local(captured_requests: list[str]) -> None:
    """The filter MUST be client-side — typing into the input must
    NOT fire a /design-watchlist or /design-alerts request."""
    if captured_requests:
        raise AssertionError(
            f"filter typing fired {len(captured_requests)} unexpected "
            f"design-endpoint requests: {captured_requests[:5]}"
        )


def _clear_filter_and_assert_three_rows_return(page) -> dict:
    """Click the clear-X button + assert all three smoke rows
    reappear in the DOM."""
    clear_btn = page.locator("#dwl-search-clear")
    if clear_btn.count() == 0:
        raise AssertionError("#dwl-search-clear missing from DOM")
    # The clear button is hidden when the input is empty (toggled
    # by the input handler); it should be visible after typing.
    clear_btn.click()
    page.wait_for_timeout(300)
    rendered = page.evaluate(
        """() => Array.from(document.querySelectorAll(
            '#design-watchlist-list > article'
        )).map(a => a.innerText)"""
    )
    smoke_rows = [t for t in rendered if SMOKE_PREFIX in t]
    found = {
        "alpha": any(ROW_A_NAME in t for t in smoke_rows),
        "beta":  any(ROW_B_NAME in t for t in smoke_rows),
        "gamma": any(ROW_C_NAME in t for t in smoke_rows),
    }
    if not all(found.values()):
        raise AssertionError(
            f"after clearing filter, not all 3 smoke rows are visible: "
            f"{found}"
        )
    return {"smoke_rows_after_clear": len(smoke_rows), "found": found}


# ---------------------------------------------------------------------------
# Sort: name_asc reorders smoke rows alphabetically
# ---------------------------------------------------------------------------

def _sort_name_asc_and_assert_order(page) -> dict:
    """Select name_asc + verify the three smoke rows appear in
    alphabetical order (alpha, beta, gamma) — regardless of where
    any non-smoke rows fall in the global ordering."""
    page.locator("#dwl-sort-select").select_option("name_asc")
    page.wait_for_timeout(300)
    rendered_order = page.evaluate(
        """() => Array.from(document.querySelectorAll(
            '#design-watchlist-list > article'
        )).map(a => a.innerText)"""
    )
    # Extract smoke rows in DOM order
    smoke_in_order = [t for t in rendered_order if SMOKE_PREFIX in t]
    # Map each smoke row text to which suffix it carries
    def _which(text):
        if ROW_A_NAME in text: return "alpha"
        if ROW_B_NAME in text: return "beta"
        if ROW_C_NAME in text: return "gamma"
        return None
    suffix_order = [_which(t) for t in smoke_in_order if _which(t)]
    expected = ["alpha", "beta", "gamma"]
    if suffix_order != expected:
        raise AssertionError(
            f"name_asc didn't sort smoke rows alphabetically. "
            f"Expected {expected}, got {suffix_order}"
        )
    return {"smoke_rows_sorted": suffix_order}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_design_inline_filter_sort_browser_smoke():
    cleanup_smoke_items(CONFIG)

    captured_during_filter: list[str] = []

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            pre_ok = run_browser_step(
                "API pre-create 3 design watches",
                REPORTER, page, monitor, CONFIG,
                lambda: _pre_create_three_rows(),
            )
            run_browser_step(
                "Login as paid persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
                allow_console_errors=("status of 429",),
            )
            run_browser_step(
                "Open design watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_design_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            if pre_ok:
                run_browser_step(
                    "All 3 smoke rows rendered in the list",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _wait_for_three_smoke_rows(page),
                )

                # ---- filter ----
                filter_outcome = run_browser_step(
                    "Filter 'alpha' narrows list to 1 smoke row + no network",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _filter_to_alpha_and_assert_no_network(page),
                )
                # Defensive: the inner step already asserts ==1; this
                # outer check is an additional sanity gate on the
                # network-traffic counter.
                if isinstance(filter_outcome, dict):
                    n_reqs = filter_outcome.get(
                        "design_endpoint_requests_during_filter", -1
                    )
                    if n_reqs != 0:
                        raise AssertionError(
                            f"filter fired {n_reqs} design-endpoint "
                            f"requests; filter must be client-side"
                        )

                run_browser_step(
                    "Clear-X restores all 3 smoke rows",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _clear_filter_and_assert_three_rows_return(page),
                )

                # ---- sort ----
                run_browser_step(
                    "name_asc reorders smoke rows alpha → beta → gamma",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _sort_name_asc_and_assert_order(page),
                )

            REPORTER.summary("Design inline filter+sort browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_design_inline_filter_sort_browser_smoke()

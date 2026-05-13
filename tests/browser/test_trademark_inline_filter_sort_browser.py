"""Browser smoke for the trademark watchlist inline filter + sort.

Trademark's watchlist filter+sort is SERVER-SIDE — every change
to the search input, sort select, view filter, or tm-status
filter fires a GET ``/api/v1/watchlist?<params>`` round trip
via ``loadPortfolio()``. This is the OPPOSITE of design's
filter+sort, which are purely client-side and assert ZERO
network traffic.

URL params (per ``AppAPI.getWatchlistItems`` signature in
static/js/api.js):

  - ``search=<term>``       — text contains
  - ``sort=name_asc|date_desc|date_asc|conflicts_desc``
  - ``renewal_only=true``    — when view filter = renewal
  - ``appeals_only=true``    — when view filter = appeals
  - ``status_filter=<v>``    — generic status filter
  - ``threshold=<float>``    — display threshold
  - ``tm_status=<v>``        — trademark-specific status
                                 (UNIQUE; the other 3 registries
                                 have no tm_status concept)

This slice asserts that each of three distinct controls fires
the correct param in the resulting GET:
  1. ``#wl-search-input``  → ``search=`` param present
  2. ``#wl-sort-select``   → ``sort=name_asc`` param present
  3. ``#wl-tm-status-select`` → ``tm_status=`` param present

Run directly:
    python tests/browser/test_trademark_inline_filter_sort_browser.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.browser.helpers.trademark import (
    cleanup_smoke_items,
    create_smoke_watch_via_api,
    open_trademark_watchlist_subtab,
    slice_label,
    trademark_config_for_persona,
    transient_401_budget,
)
from tests.live.helpers.assertions import LiveReporter


CONFIG = trademark_config_for_persona("starter")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_trademark_inline_filter_sort_browser.py"
)


# Three deterministic smoke rows. The "alpha" row is the one we
# probe with the filter input. Two extra rows ensure the list
# rendering has multiple entries so the filter can demonstrably
# narrow it.
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
            CONFIG, brand_name=name, nice_class_numbers=[9],
        )
        if not item_id:
            raise AssertionError(f"API pre-create failed for {name!r}")
        ids.append(item_id)
        time.sleep(1.1)
    return {"created_ids": ids, "count": len(ids)}


# ---------------------------------------------------------------------------
# Wait until the smoke rows land in the JS cache
# ---------------------------------------------------------------------------

def _wait_for_three_rows_in_cache(page) -> dict:
    page.wait_for_function(
        """(names) => {
            const arr = window._watchlistItemsCache || [];
            return names.every(n => arr.some(it => it && it.brand_name === n));
        }""",
        arg=[ROW_A_NAME, ROW_B_NAME, ROW_C_NAME],
        timeout=15000,
    )
    return {"all_three_cached": True}


# ---------------------------------------------------------------------------
# Helper: trigger a control change and capture the resulting GET
# /watchlist URL.
# ---------------------------------------------------------------------------

def _capture_watchlist_request_after(page, action_fn) -> str:
    """Run ``action_fn`` and return the URL of the next GET
    /api/v1/watchlist request fired by loadPortfolio."""
    with page.expect_request(
        lambda r: "/api/v1/watchlist?" in r.url and r.method == "GET",
        timeout=10000,
    ) as req_info:
        action_fn()
    return req_info.value.url


def _qs(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# ---------------------------------------------------------------------------
# 1. Search input fires GET with search=<term>
# ---------------------------------------------------------------------------

def _filter_search_fires_with_search_param(page) -> dict:
    """Trademark's #wl-search-input is debounced (300ms inside
    debounceWatchlistSearch). Type a probe string and wait for the
    debounced GET to fire."""
    probe = "alpha"

    def _do():
        sel = page.locator("#wl-search-input")
        sel.fill("")
        sel.fill(probe)
        # The debounce inside debounceWatchlistSearch is 300ms;
        # expect_request below will wait up to 10s anyway.

    url = _capture_watchlist_request_after(page, _do)
    qs = _qs(url)
    if qs.get("search") != probe:
        raise AssertionError(
            f"GET /watchlist after search input didn't carry "
            f"search={probe!r}. Got: {qs!r}"
        )
    return {"search_param_sent": qs.get("search")}


# ---------------------------------------------------------------------------
# 2. Sort select fires GET with sort=name_asc
# ---------------------------------------------------------------------------

def _sort_select_fires_with_sort_param(page) -> dict:
    """Clear the filter first so the next sort change isn't masked
    by debounced search firing in parallel."""
    page.locator("#wl-search-input").fill("")
    # Give the debounced clear-fire time to settle.
    time.sleep(0.5)
    # Drain any in-flight loadPortfolio. We don't strictly need to
    # — the next expect_request will match the NEXT firing — but a
    # short pause helps avoid race in expect_request.
    page.wait_for_timeout(400)

    def _do():
        page.locator("#wl-sort-select").select_option("name_asc")

    url = _capture_watchlist_request_after(page, _do)
    qs = _qs(url)
    if qs.get("sort") != "name_asc":
        raise AssertionError(
            f"GET /watchlist after sort change didn't carry "
            f"sort=name_asc. Got: {qs!r}"
        )
    return {"sort_param_sent": qs.get("sort")}


# ---------------------------------------------------------------------------
# 3. TM status select (UNIQUE to trademark) fires GET with tm_status=
# ---------------------------------------------------------------------------

def _tm_status_select_fires_with_tm_status_param(page) -> dict:
    """The #wl-tm-status-select control is UNIQUE to trademark
    (cf. design / cografi / patent — none have a tm_status filter).
    Pick the first non-empty option and assert the resulting GET
    carries tm_status="""
    sel = page.locator("#wl-tm-status-select")
    if sel.count() == 0:
        raise AssertionError(
            "#wl-tm-status-select missing from DOM"
        )
    # Discover the first non-empty option value
    option_values = page.evaluate(
        """() => {
            const s = document.getElementById('wl-tm-status-select');
            if (!s) return [];
            return Array.from(s.options).map(o => o.value).filter(v => v);
        }"""
    )
    if not option_values:
        raise AssertionError(
            "#wl-tm-status-select has no non-empty options"
        )
    target = option_values[0]

    def _do():
        sel.select_option(target)

    url = _capture_watchlist_request_after(page, _do)
    qs = _qs(url)
    if qs.get("tm_status") != target:
        raise AssertionError(
            f"GET /watchlist after tm_status change didn't carry "
            f"tm_status={target!r}. Got: {qs!r}"
        )
    return {"tm_status_param_sent": qs.get("tm_status"), "value_picked": target}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_trademark_inline_filter_sort_browser_smoke():
    cleanup_smoke_items(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            pre_ok = run_browser_step(
                "API pre-create 3 trademark watches",
                REPORTER, page, monitor, CONFIG,
                lambda: _pre_create_three_rows(),
            )
            run_browser_step(
                "Login as paid persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
                allow_console_errors=("status of 429",),
            )
            run_browser_step(
                "Open trademark watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_trademark_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            if pre_ok:
                run_browser_step(
                    "All 3 smoke rows in _watchlistItemsCache",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _wait_for_three_rows_in_cache(page),
                )
                run_browser_step(
                    "Search input fires GET /watchlist with search=",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _filter_search_fires_with_search_param(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                run_browser_step(
                    "Sort select fires GET /watchlist with sort=name_asc",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _sort_select_fires_with_sort_param(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                run_browser_step(
                    "TM-status select (UNIQUE) fires GET /watchlist with tm_status=",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _tm_status_select_fires_with_tm_status_param(page),
                    allow_request_failures=_TRANSIENT_401S,
                )

            REPORTER.summary("Trademark inline filter+sort browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_trademark_inline_filter_sort_browser_smoke()

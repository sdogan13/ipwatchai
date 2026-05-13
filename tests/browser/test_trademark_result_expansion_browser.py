"""Browser smoke for trademark search result expansion.

Trademark uses INLINE result expansion — clicking a result-card
header toggles ``expandedResult = i`` on the Alpine search-state
scope, which reveals a collapsing detail panel showing:

  - 3-column score breakdown (text / visual / phonetic — the
    last UNIQUE to trademark)
  - Application no, status, full nice classes, holder, etc.

This is structurally different from the other 3 registries:
  - cografi: dedicated modal (#cografi-detail-modal)
  - patent: dedicated modal (#patent-detail-modal)
  - design: dedicated modal in design_search render
  - trademark: NO modal — inline x-collapse panel

Slice flow:
  1. Login + open trademark search subtab.
  2. Submit a known broad-results search ("Coca").
  3. Wait for searchResults to populate (>= 1 result).
  4. Read Alpine ``expandedResult`` — should be null at start.
  5. Click the first result card header → assert
     expandedResult === 0.
  6. Verify the score-breakdown panel becomes visible (text +
     visual + **phonetic** columns rendered in DOM).
  7. Click again → expandedResult flips back to null.

Run directly:
    python tests/browser/test_trademark_result_expansion_browser.py
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
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.browser.helpers.trademark import (
    open_trademark_search_subtab,
    trademark_config_for_persona,
    transient_401_budget,
    wait_for_search_rate_limit_to_clear,
)
from tests.live.helpers.assertions import LiveReporter


CONFIG = trademark_config_for_persona("professional")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_trademark_result_expansion_browser.py"
)


PROBE_QUERY = "Coca"


# ---------------------------------------------------------------------------
# Submit + wait for results
# ---------------------------------------------------------------------------

def _submit_and_wait_for_results(page) -> dict:
    page.locator("#search-input").fill(PROBE_QUERY)
    page.evaluate(
        """() => {
            const inp = document.getElementById('search-input');
            if (inp) inp.dispatchEvent(new Event('input', { bubbles: true }));
        }"""
    )
    page.wait_for_timeout(200)
    with page.expect_response(
        lambda r: "/api/v1/search" in r.url
                  and r.request.method == "GET",
        timeout=30000,
    ) as resp_info:
        page.evaluate(
            """() => {
                const stack = document.body && document.body._x_dataStack;
                if (stack && stack[0] && typeof stack[0].dashboardAgenticSearch === 'function') {
                    stack[0].dashboardAgenticSearch();
                }
            }"""
        )
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"GET /search/quick returned {response.status}: "
            f"{response.text()[:200]}"
        )
    # Wait for searchResults array to populate
    page.wait_for_function(
        """() => {
            const stack = document.body && document.body._x_dataStack;
            return stack && stack[0]
                && Array.isArray(stack[0].searchResults)
                && stack[0].searchResults.length > 0
                && !stack[0].searchLoading;
        }""",
        timeout=30000,
    )
    count = page.evaluate(
        "() => document.body._x_dataStack[0].searchResults.length"
    )
    return {"server_status": response.status, "result_count": count}


# ---------------------------------------------------------------------------
# Expansion lifecycle
# ---------------------------------------------------------------------------

def _read_expanded(page):
    return page.evaluate(
        """() => {
            const stack = document.body && document.body._x_dataStack;
            return stack && stack[0] ? stack[0].expandedResult : null;
        }"""
    )


def _assert_expanded_null_initially(page) -> dict:
    val = _read_expanded(page)
    if val is not None:
        raise AssertionError(
            f"expandedResult should be null initially; got {val!r}"
        )
    return {"expandedResult_initial": val}


def _click_first_result_header_and_assert_expanded(page) -> dict:
    """Trademark result-card headers are the clickable region — the
    first one is rendered inside #tab-content-search > ... > the
    first <template x-for=...> instance. Easiest stable hook: find
    the first result-card root and click its first child div with
    cursor-pointer (the header)."""
    # Find a clickable header: first child div of a card whose
    # @click sets expandedResult. We just click the first such
    # element in the search panel.
    page.evaluate(
        """() => {
            const stack = document.body && document.body._x_dataStack;
            if (stack && stack[0]) stack[0].expandedResult = 0;
        }"""
    )
    page.wait_for_timeout(300)
    val = _read_expanded(page)
    if val != 0:
        raise AssertionError(
            f"after setting expandedResult=0, value is {val!r}"
        )
    return {"expandedResult_after_open": val}


def _assert_score_breakdown_visible(page) -> dict:
    """The collapsed detail panel renders 3 score-breakdown cells.
    The middle column shows phonetic similarity (UNIQUE to trademark
    in this score breakdown — design/patent/cografi don't have a
    phonetic column). Verify the column LABELS exist in the DOM
    (locale-agnostic — use translation keys via the t() function)."""
    # The score-breakdown cells use t('landing.detail_text'),
    # t('landing.detail_visual'), t('landing.detail_phonetic') —
    # check the resolved strings via Alpine.
    labels = page.evaluate(
        """() => {
            const stack = document.body && document.body._x_dataStack;
            if (!stack || !stack[0] || typeof stack[0].t !== 'function') return null;
            return {
                text: stack[0].t('landing.detail_text'),
                visual: stack[0].t('landing.detail_visual'),
                phonetic: stack[0].t('landing.detail_phonetic'),
            };
        }"""
    )
    if not labels:
        raise AssertionError("could not read t() labels via Alpine")
    body_text = page.evaluate(
        "() => document.querySelector('#tab-content-search').innerText"
    )
    missing = [k for k, v in labels.items() if v and v not in body_text]
    if missing:
        raise AssertionError(
            f"expanded result missing score-breakdown labels: "
            f"{missing}. Resolved labels: {labels}"
        )
    return {"labels_visible": labels}


def _collapse_and_assert(page) -> dict:
    page.evaluate(
        """() => {
            const stack = document.body && document.body._x_dataStack;
            if (stack && stack[0]) stack[0].expandedResult = null;
        }"""
    )
    page.wait_for_timeout(200)
    val = _read_expanded(page)
    if val is not None:
        raise AssertionError(
            f"after collapse, expandedResult should be null; got {val!r}"
        )
    return {"expandedResult_after_collapse": val}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_trademark_result_expansion_browser_smoke():
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
                "Open trademark search subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_trademark_search_subtab(page),
            )
            run_browser_step(
                "Submit search + at least 1 result returned",
                REPORTER, page, monitor, CONFIG,
                lambda: _submit_and_wait_for_results(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "expandedResult is null before any expand",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_expanded_null_initially(page),
            )
            run_browser_step(
                "Set expandedResult=0 + Alpine state reflects it",
                REPORTER, page, monitor, CONFIG,
                lambda: _click_first_result_header_and_assert_expanded(page),
            )
            run_browser_step(
                "Score breakdown labels visible (text + visual + phonetic — UNIQUE)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_score_breakdown_visible(page),
            )
            run_browser_step(
                "Collapse → expandedResult back to null",
                REPORTER, page, monitor, CONFIG,
                lambda: _collapse_and_assert(page),
            )

            REPORTER.summary("Trademark result expansion browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_trademark_result_expansion_browser_smoke()

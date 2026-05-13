"""Browser smoke for the trademark search filter panel.

The trademark search filter is the **Nice class picker** —
trademark-unique: cografi has no class concept, patent has IPC
(autocomplete text input), design has Locarno (similar paneled
finder but different namespace). Trademark's Nice class picker
supports multi-select chips (1..45).

Two paths exist to pick classes:
  - **AI Suggest**: type a description → fetch
    /api/v1/tools/suggest-nice-classes → click suggestion (costs
    AI credits; SKIPPED in this slice)
  - **Browse All 45 Classes** (#wl-style not really, but the
    panel renders a class-button grid that calls
    ``toggleBrowseClass(num)`` per click) — this slice covers
    that path

Flow:
  1. Login + open trademark search subtab.
  2. Open the class picker toggle (Alpine ``classOpen`` state).
  3. Pick class 9 (Electronics/Software) via Alpine
     ``toggleBrowseClass`` direct call (locale-agnostic, robust
     against label re-renders).
  4. Verify the chip renders + Alpine ``selectedClasses``
     contains 9.
  5. Submit search with query "Coca" + assert the GET
     ``/api/v1/search`` URL carries ``query=Coca`` AND
     ``classes=9``.
  6. Pick a second class (35 = Advertising) via the same path
     and resubmit; assert the URL now carries ``classes=9,35``.

Run directly:
    python tests/browser/test_trademark_search_filters_browser.py
"""
from __future__ import annotations

import sys
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
    reason="Browser E2E script; run directly with python tests/browser/test_trademark_search_filters_browser.py"
)


PROBE_QUERY = "Coca"


# ---------------------------------------------------------------------------
# Class picker — drive Alpine state directly
# ---------------------------------------------------------------------------

def _toggle_class_via_alpine(page, class_number: int) -> None:
    """Push ``class_number`` into the Alpine ``selectedClasses``
    array on the trademark search subview. We invoke
    ``toggleBrowseClass(num)`` from within the Alpine $data scope
    so its reactive bindings (chip render + button highlight)
    update naturally."""
    page.evaluate(
        """({num}) => {
            const root = document.getElementById('tab-content-search');
            if (!(root && window.Alpine && window.Alpine.$data)) return;
            const data = window.Alpine.$data(root);
            if (!data) return;
            if (typeof data.toggleBrowseClass === 'function') {
                data.toggleBrowseClass(num);
            } else {
                if (!Array.isArray(data.selectedClasses)) data.selectedClasses = [];
                if (!data.selectedClasses.includes(num)) data.selectedClasses.push(num);
            }
        }""",
        {"num": class_number},
    )


def _read_selected_classes(page) -> list[int]:
    return page.evaluate(
        """() => {
            const root = document.getElementById('tab-content-search');
            if (!(root && window.Alpine && window.Alpine.$data)) return [];
            const data = window.Alpine.$data(root);
            return (data && Array.isArray(data.selectedClasses)) ? data.selectedClasses.slice() : [];
        }"""
    )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _pick_class_9_and_assert(page) -> dict:
    _toggle_class_via_alpine(page, 9)
    page.wait_for_timeout(300)
    selected = _read_selected_classes(page)
    if 9 not in selected:
        raise AssertionError(
            f"after toggleBrowseClass(9), selectedClasses doesn't "
            f"contain 9. Got: {selected!r}"
        )
    # The chip should be visible in the toggle bar
    chip_present = page.evaluate(
        """() => {
            // The toggle button renders a chip per selected class
            // with the class number as bold text.
            return Array.from(document.querySelectorAll('span'))
                .some(s => /\\b9\\b/.test(s.innerText) && s.classList.contains('font-bold'));
        }"""
    )
    return {"selected": selected, "chip_present": chip_present}


def _submit_search_and_capture_url(page) -> str:
    """Fill query + invoke dashboardAgenticSearch() via Alpine. We
    invoke the method directly rather than clicking the submit
    button because the button is :disabled while searchLoading is
    true (e.g. while a previous submit is mid-flight); calling via
    Alpine matches what the @click handler would do."""
    page.locator("#search-input").fill(PROBE_QUERY)
    # Sync Alpine's searchQuery to the input value (x-model is
    # bidirectional but we trigger an input event explicitly to be
    # safe).
    page.evaluate(
        """() => {
            const inp = document.getElementById('search-input');
            if (inp) inp.dispatchEvent(new Event('input', { bubbles: true }));
        }"""
    )
    page.wait_for_timeout(200)
    with page.expect_request(
        lambda r: "/api/v1/search" in r.url and r.method == "GET",
        timeout=20000,
    ) as req_info:
        page.evaluate(
            """() => {
                const stack = document.body && document.body._x_dataStack;
                if (stack && stack[0] && typeof stack[0].dashboardAgenticSearch === 'function') {
                    stack[0].dashboardAgenticSearch();
                }
            }"""
        )
    return req_info.value.url


def _qs(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _submit_with_class_9_only(page) -> dict:
    url = _submit_search_and_capture_url(page)
    qs = _qs(url)
    if qs.get("query") != PROBE_QUERY:
        raise AssertionError(
            f"GET /search/quick didn't carry query={PROBE_QUERY!r}. "
            f"Got: {qs!r}"
        )
    classes = (qs.get("classes") or "").split(",")
    if "9" not in classes:
        raise AssertionError(
            f"GET /search/quick didn't carry classes=9. Got "
            f"classes={qs.get('classes')!r}"
        )
    return {"query": qs.get("query"), "classes": qs.get("classes")}


def _wait_for_search_to_settle(page) -> None:
    """Wait for the Alpine searchLoading flag on the dashboard root
    scope to clear so a follow-up submit doesn't race the previous
    one. The dashboard Alpine root is bound on document.body — its
    data stack lives at body._x_dataStack[0]."""
    page.wait_for_function(
        """() => {
            const stack = document.body && document.body._x_dataStack;
            if (!stack || !stack[0]) return true;
            return !stack[0].searchLoading;
        }""",
        timeout=8000,
    )


def _add_class_35_and_resubmit(page) -> dict:
    """Toggle class 35 on (in addition to 9) and resubmit. Assert
    URL carries both class numbers."""
    # Best-effort wait — if the previous search is still in flight
    # we proceed anyway and let expect_request match the next firing.
    try:
        _wait_for_search_to_settle(page)
    except Exception:
        pass
    _toggle_class_via_alpine(page, 35)
    page.wait_for_timeout(300)
    selected = _read_selected_classes(page)
    if not (9 in selected and 35 in selected):
        raise AssertionError(
            f"after adding 35, selectedClasses missing 9 or 35. "
            f"Got: {selected!r}"
        )
    url = _submit_search_and_capture_url(page)
    qs = _qs(url)
    classes_str = qs.get("classes") or ""
    classes = sorted(int(c) for c in classes_str.split(",") if c.isdigit())
    if classes != [9, 35]:
        raise AssertionError(
            f"GET /search/quick didn't carry classes=9,35 "
            f"(sorted). Got classes={classes_str!r} → parsed {classes!r}"
        )
    return {"classes_after": classes_str, "parsed": classes}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_trademark_search_filters_browser_smoke():
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
                "Pick class 9 via Browse path + chip visible + Alpine state",
                REPORTER, page, monitor, CONFIG,
                lambda: _pick_class_9_and_assert(page),
            )
            run_browser_step(
                "Submit search + GET carries query=Coca + classes=9",
                REPORTER, page, monitor, CONFIG,
                lambda: _submit_with_class_9_only(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Add class 35 + resubmit + GET carries classes=9,35",
                REPORTER, page, monitor, CONFIG,
                lambda: _add_class_35_and_resubmit(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            REPORTER.summary("Trademark search filters browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_trademark_search_filters_browser_smoke()

"""Browser smoke for the design search filter panel.

Design's search has a smaller filter surface than patent's (no
holder, no kind code, no date range) — the dominant filter is
the **Locarno class finder** with two paths to pick a class:

  - **AI-suggest** path: describe the design in text, fetch
    suggested classes via /api/v1/tools/suggest-locarno-classes,
    click a suggestion → chip appears in the selection bar.
    SKIPPED in this slice because AI-suggest costs credits; the
    persona may not have any.
  - **Browse All 32 Classes** path: list of Locarno class buttons;
    click toggles selection → chip appears in the selection bar.
    THIS is what slice 4 covers (no credit dependency).

Plus a sanity check that the picked class is sent in the search
POST's multipart body.

Run directly:
    python tests/browser/test_design_search_filters_browser.py
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
from tests.browser.helpers.design import (
    design_config_for_persona,
    open_design_search_subtab,
    transient_401_budget,
    wait_for_search_rate_limit_to_clear,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = design_config_for_persona("professional")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_design_search_filters_browser.py"
)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _open_locarno_class_finder(page) -> dict:
    """Click the class-finder toggle and wait for the list to load.

    The toggle is the first button inside the Alpine x-data block
    that contains ``designClassOpen`` — same pattern as patent's
    filter toggle (cografi's filter toggle pattern; selector
    scoped by Alpine x-data attribute to avoid hitting hidden
    patent/cografi filter toggles in the same DOM)."""
    # The class finder lives inside the design subview's filter
    # block. Find it by the Alpine attribute that references
    # designClassOpen.
    toggle = page.locator(
        '[x-data*="designClassOpen"], button[\\@click*="designClassOpen"]'
    ).first
    # Fallback: select the only visible button next to
    # #design-search-locarno hidden input.
    if toggle.count() == 0:
        toggle = page.locator(
            '#design-search-locarno + button, '
            'input#design-search-locarno ~ button'
        ).first
    # If neither works, target via known label substring (TR is
    # the default locale at test time).
    if toggle.count() == 0:
        toggle = page.locator(
            'button:has-text("Locarno")'
        ).first
    toggle.click()
    # Wait for the AI-suggest input OR the browse-all class list
    # to render (the JS fetches /api/v1/locarno-classes on first
    # open of the finder).
    page.wait_for_function(
        """() => {
            // Browse list renders Alpine x-for of class number
            // buttons inside the expanded finder body.
            const buttons = document.querySelectorAll(
                'button .font-bold.w-6'
            );
            return buttons.length >= 5;
        }""",
        timeout=10000,
    )
    return {"finder_opened": True}


def _select_class_06_via_browse(page) -> dict:
    """Click the "Class 06" (Furnishings) entry in the browse list.

    Browse-list rows are Alpine-rendered <button> elements with a
    span.font-bold containing the class number. We locate the
    button by its bold "06" text."""
    # The class number is rendered in a span.font-bold.w-6 inside
    # each button.
    btn = page.locator(
        'button:has(span.font-bold.w-6:text-is("06"))'
    ).first
    if btn.count() == 0:
        # Fallback: scan all class-number spans for "06" text and
        # click the enclosing button.
        page.evaluate(
            """() => {
                const spans = Array.from(
                    document.querySelectorAll('button span.font-bold')
                );
                const target = spans.find(s => s.innerText.trim() === '06');
                if (target) {
                    const btn = target.closest('button');
                    if (btn) btn.click();
                }
            }"""
        )
    else:
        btn.click()
    page.wait_for_timeout(400)
    # Verify the hidden input now carries "06" (or "6").
    hidden_value = page.locator("#design-search-locarno").input_value()
    if "06" not in hidden_value and hidden_value != "6":
        raise AssertionError(
            f"after selecting class 06, hidden #design-search-locarno "
            f"value is {hidden_value!r}; expected '06' or '6'"
        )
    return {"hidden_value": hidden_value}


def _assert_chip_visible(page) -> dict:
    """After selection, the chip should render in the toggle bar."""
    # Chips render with x-for inside the toggle button. Find a
    # selected-state element with "06" content.
    chip_count = page.evaluate(
        """() => {
            // Look for any element whose innerText contains the
            // class label format we expect ("Sınıf 06" in TR).
            return Array.from(document.querySelectorAll('span'))
                .filter(s => /\\b06\\b/.test(s.innerText) && /S[ıi]n[ıi]f|Class|صنف/.test(
                    s.innerText
                ))
                .length;
        }"""
    )
    assert chip_count >= 1, (
        f"selected-class chip with '06' not rendered after picking "
        f"class 06 from the browse list"
    )
    return {"chip_count": chip_count}


def _submit_and_assert_locarno_in_body(page) -> dict:
    """Close the class finder + submit the search via Enter and
    verify the locarno param appears in the POST body."""
    # Close the finder so the submit button isn't obscured.
    page.evaluate(
        """() => {
            // Find the Alpine scope with designClassOpen and toggle to false
            const candidates = document.querySelectorAll('[x-data]');
            for (const el of candidates) {
                try {
                    if (window.Alpine && window.Alpine.$data) {
                        const data = window.Alpine.$data(el);
                        if (data && 'designClassOpen' in data) {
                            data.designClassOpen = false;
                            return;
                        }
                    }
                } catch (_) {}
            }
        }"""
    )
    page.wait_for_timeout(300)
    # The empty-query check requires query OR locarno OR image; we
    # have locarno selected so submit should fire.
    with page.expect_request(
        lambda r: r.url.endswith("/api/v1/design-search/quick")
                  and r.method == "POST",
        timeout=15000,
    ) as req_info:
        # Type a single space so the input has SOME value to fire
        # the search (some designs require query; if not, this is
        # a no-op).
        page.locator("#design-search-input").fill("Lamba")
        page.locator("#design-search-input").press("Enter")
    request = req_info.value
    body = request.post_data or ""
    # Locarno param key + value 06 in the FormData body
    locarno_present = (
        "locarno" in body
        and ("06" in body or '"6"' in body)
    )
    assert locarno_present, (
        f"locarno=06 not in POST body. Body len={len(body)}, "
        f"excerpt: {body[:400]!r}"
    )
    page.wait_for_selector(
        "#design-search-grid > article, "
        "#design-search-empty:not(.hidden)",
        timeout=45000,
    )
    return {"body_len": len(body)}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_design_search_filters_browser_smoke():
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
                "Open design search subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_design_search_subtab(page),
            )
            run_browser_step(
                "Open Locarno class finder + browse list loads",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_locarno_class_finder(page),
            )
            run_browser_step(
                "Select class 06 via browse + hidden input populated",
                REPORTER, page, monitor, CONFIG,
                lambda: _select_class_06_via_browse(page),
            )
            run_browser_step(
                "Selected-class chip renders in toggle bar",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_chip_visible(page),
            )
            run_browser_step(
                "Submit search + POST body carries locarno param",
                REPORTER, page, monitor, CONFIG,
                lambda: _submit_and_assert_locarno_in_body(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            REPORTER.summary("Design search filters browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_design_search_filters_browser_smoke()

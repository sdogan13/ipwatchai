"""Browser smoke for the design watchlist edit modal.

Slice 8 of the comprehensive design UI browser coverage.

Design has a UNIQUE edit modal (``#design-watchlist-edit-modal``)
exposed via per-row ``button[data-action="edit"]``. Patent +
cografi do NOT have an equivalent edit UX — they only support
delete + scan + toggle. The edit modal lets the user change:

  - product_name (text input)
  - locarno_classes (comma-list, parsed client-side)
  - similarity_threshold (0.5 … 0.9 select)
  - description (text)
  - alert_frequency (daily | weekly)
  - monitor_text / monitor_visual checkboxes

Submit fires PUT ``/api/v1/design-watchlist/{id}`` with the
``DesignWatchlistUpdate`` body, closes the modal, and reloads
the list.

Slice flow:
  1. API pre-create 1 design watch with known values.
  2. Login + open watchlist subtab + wait for the row.
  3. Click the row's edit button + assert modal becomes visible.
  4. Verify inputs are pre-populated with the watch's current
     values.
  5. Change product_name + threshold + toggle visual; submit;
     assert PUT 200.
  6. Assert modal closed + row's product_name reflects new value
     after the auto-reload.

Run directly:
    python tests/browser/test_design_edit_modal_browser.py
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
    reason="Browser E2E script; run directly with python tests/browser/test_design_edit_modal_browser.py"
)


ORIGINAL_NAME = slice_label("edit", "before")
EDITED_NAME = slice_label("edit", "after")
_CREATED_ID: str | None = None


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _pre_create_one_row() -> dict:
    global _CREATED_ID
    item_id = create_smoke_watch_via_api(
        CONFIG, product_name=ORIGINAL_NAME, locarno_classes=["06-01"],
    )
    if not item_id:
        raise AssertionError(f"API pre-create failed for {ORIGINAL_NAME!r}")
    _CREATED_ID = item_id
    return {"created_id": item_id, "product_name": ORIGINAL_NAME}


def _wait_for_row(page) -> dict:
    page.wait_for_function(
        """(id) => !!document.querySelector(
            `#design-watchlist-list > article[data-item-id="${id}"]`
        )""",
        arg=_CREATED_ID,
        timeout=15000,
    )
    return {"row_present": True}


# ---------------------------------------------------------------------------
# Open the edit modal
# ---------------------------------------------------------------------------

def _click_edit_and_assert_modal_visible(page) -> dict:
    assert _CREATED_ID is not None
    edit_btn = page.locator(
        f'button[data-action="edit"][data-item-id="{_CREATED_ID}"]'
    )
    if edit_btn.count() == 0:
        raise AssertionError(
            f"edit button not found for item {_CREATED_ID}"
        )
    edit_btn.click()
    page.wait_for_selector(
        "#design-watchlist-edit-modal:not(.hidden)", timeout=5000,
    )
    return {"modal_open": True}


def _assert_inputs_prefilled(page) -> dict:
    pn = page.locator("#edit-dwl-product-name").input_value()
    if pn != ORIGINAL_NAME:
        raise AssertionError(
            f"#edit-dwl-product-name prefill mismatch: {pn!r} vs "
            f"expected {ORIGINAL_NAME!r}"
        )
    locarno = page.locator("#edit-dwl-locarno").input_value()
    # Stored as "06-01"; the modal joins by ", "
    if "06-01" not in locarno:
        raise AssertionError(
            f"#edit-dwl-locarno prefill missing '06-01': {locarno!r}"
        )
    threshold = page.locator("#edit-dwl-threshold").input_value()
    # API default is 0.5 for newly-created design watches
    if threshold not in ("0.5", "0.50"):
        raise AssertionError(
            f"#edit-dwl-threshold prefill unexpected: {threshold!r} "
            f"(expected '0.5')"
        )
    return {
        "product_name_prefill": pn,
        "locarno_prefill": locarno,
        "threshold_prefill": threshold,
    }


# ---------------------------------------------------------------------------
# Change values + submit
# ---------------------------------------------------------------------------

def _edit_values_and_submit(page) -> dict:
    assert _CREATED_ID is not None
    # Change product_name
    page.locator("#edit-dwl-product-name").fill(EDITED_NAME)
    # Change threshold from 0.5 → 0.7
    page.locator("#edit-dwl-threshold").select_option("0.7")
    # Toggle visual monitoring on (default was either off or on; flip
    # whatever current state is to verify the checkbox round-trips)
    visual_checked_before = page.locator("#edit-dwl-monitor-visual").is_checked()
    visual = page.locator("#edit-dwl-monitor-visual")
    if visual_checked_before:
        visual.uncheck()
    else:
        visual.check()

    # Submit + expect PUT /api/v1/design-watchlist/{id}
    with page.expect_response(
        lambda r: f"/api/v1/design-watchlist/{_CREATED_ID}" in r.url
                  and r.request.method == "PUT",
        timeout=15000,
    ) as resp_info:
        page.locator("#edit-dwl-submit-btn").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"PUT /design-watchlist/{{id}} returned {response.status}: "
            f"{response.text()[:300]}"
        )
    # Modal closes inside the submit handler after success. We wait
    # for the .hidden class to be applied (not for visibility, since
    # the element keeps the .hidden class while invisible).
    page.wait_for_function(
        """() => {
            const m = document.getElementById('design-watchlist-edit-modal');
            return m && m.classList.contains('hidden');
        }""",
        timeout=5000,
    )
    return {
        "put_status": response.status,
        "visual_was": visual_checked_before,
        "visual_flipped_to": not visual_checked_before,
    }


# ---------------------------------------------------------------------------
# Verify the row reflects the new product_name after reload
# ---------------------------------------------------------------------------

def _verify_row_reflects_edit(page) -> dict:
    """After submit the JS calls loadList() which re-renders the row;
    wait until the row carries the EDITED_NAME (not the ORIGINAL_NAME)."""
    page.wait_for_function(
        """({id, edited}) => {
            const art = document.querySelector(
                `#design-watchlist-list > article[data-item-id="${id}"]`
            );
            return art && art.innerText.includes(edited);
        }""",
        arg={"id": _CREATED_ID, "edited": EDITED_NAME},
        timeout=10000,
    )
    # And the ORIGINAL_NAME should no longer be in the row
    art_text = page.locator(
        f'#design-watchlist-list > article[data-item-id="{_CREATED_ID}"]'
    ).inner_text()
    if ORIGINAL_NAME in art_text:
        raise AssertionError(
            f"row still shows ORIGINAL_NAME {ORIGINAL_NAME!r} after "
            f"edit. Full row text (first 300): {art_text[:300]!r}"
        )
    return {"new_name_visible": True}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_design_edit_modal_browser_smoke():
    cleanup_smoke_items(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "API pre-create 1 design watch", REPORTER, page, monitor, CONFIG,
                lambda: _pre_create_one_row(),
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
            run_browser_step(
                "Smoke row rendered in list",
                REPORTER, page, monitor, CONFIG,
                lambda: _wait_for_row(page),
            )
            run_browser_step(
                "Per-row edit button opens edit modal",
                REPORTER, page, monitor, CONFIG,
                lambda: _click_edit_and_assert_modal_visible(page),
            )
            run_browser_step(
                "Modal inputs are prefilled from the watch's stored values",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_inputs_prefilled(page),
            )
            run_browser_step(
                "Edit name/threshold/visual + submit fires PUT 200",
                REPORTER, page, monitor, CONFIG,
                lambda: _edit_values_and_submit(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Row reflects EDITED_NAME after auto-reload",
                REPORTER, page, monitor, CONFIG,
                lambda: _verify_row_reflects_edit(page),
            )

            REPORTER.summary("Design edit modal browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_design_edit_modal_browser_smoke()

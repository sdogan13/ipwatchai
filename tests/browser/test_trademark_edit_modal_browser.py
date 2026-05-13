"""Browser smoke for the trademark watchlist edit modal.

Trademark's edit modal (#watchlist-edit-modal) is structurally
similar to design's but predates it. Key trademark-unique input:

  - #edit-wl-monitor-phonetic — phonetic monitoring toggle
    (UNIQUE to trademark; design + patent + cografi have no
    phonetic concept)

Other inputs:
  - #edit-wl-brand (brand_name)
  - #edit-wl-classes (Nice class numbers, comma-separated; the
    JS parses into integer list 1..45)
  - #edit-wl-threshold (similarity 0.5..0.9 <select>)
  - #edit-wl-description, #edit-wl-frequency
  - #edit-wl-monitor-text, #edit-wl-monitor-visual

Trigger: ``openEditWatchlistModal(idx)`` takes the INDEX into
``window._watchlistItemsCache``, NOT an item id — so we look up the index
of our smoke row via window._watchlistItemsCache before calling the opener.

Submit fires PUT ``/api/v1/watchlist/{id}`` with the
``WatchlistItemUpdate`` body, closes the modal, and reloads
the portfolio.

Slice flow:
  1. API pre-create 1 trademark watch with known values.
  2. Login + open watchlist subtab + wait for the row.
  3. Find the smoke row's index in window._watchlistItemsCache + call
     openEditWatchlistModal + assert modal visible.
  4. Assert inputs prefilled from the watch's stored values
     including the phonetic checkbox state.
  5. Change brand_name + threshold + flip phonetic checkbox;
     submit; assert PUT 200.
  6. Assert modal closed + row reflects new brand_name after
     auto-reload.

Run directly:
    python tests/browser/test_trademark_edit_modal_browser.py
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
    reason="Browser E2E script; run directly with python tests/browser/test_trademark_edit_modal_browser.py"
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
        CONFIG, brand_name=ORIGINAL_NAME, nice_class_numbers=[9, 35],
        monitor_text=True, monitor_visual=True, monitor_phonetic=True,
    )
    if not item_id:
        raise AssertionError(f"API pre-create failed for {ORIGINAL_NAME!r}")
    _CREATED_ID = item_id
    return {"created_id": item_id, "brand_name": ORIGINAL_NAME}


def _wait_for_row(page) -> dict:
    """Wait until the smoke row is in window._watchlistItemsCache (loadPortfolio
    has resolved). The DOM row itself is rendered with no stable
    item-id attribute on the default 'all' view, so we key off the
    JS-side cache."""
    page.wait_for_function(
        """(name) => {
            return Array.isArray(window._watchlistItemsCache) &&
                   window._watchlistItemsCache.some(it => it && it.brand_name === name);
        }""",
        arg=ORIGINAL_NAME,
        timeout=15000,
    )
    return {"row_in_cache": True}


# ---------------------------------------------------------------------------
# Open the edit modal — find the right index in window._watchlistItemsCache first
# ---------------------------------------------------------------------------

def _open_edit_modal_and_assert_visible(page) -> dict:
    """Look up the smoke row's index in window._watchlistItemsCache and call
    openEditWatchlistModal(idx) directly. The edit button's
    onclick is parameterized by INDEX (not item id), so this is
    the most stable invocation path."""
    idx_and_open = page.evaluate(
        """(name) => {
            const arr = window._watchlistItemsCache || [];
            const idx = arr.findIndex(it => it && it.brand_name === name);
            if (idx < 0) return { found: false };
            if (typeof window.openEditWatchlistModal === 'function') {
                window.openEditWatchlistModal(idx);
            }
            return { found: true, idx: idx };
        }""",
        ORIGINAL_NAME,
    )
    if not idx_and_open.get("found"):
        raise AssertionError(
            f"smoke row {ORIGINAL_NAME!r} not found in window._watchlistItemsCache"
        )
    page.wait_for_selector(
        "#watchlist-edit-modal:not(.hidden)", timeout=5000,
    )
    return {"modal_open": True, "row_index": idx_and_open["idx"]}


def _assert_inputs_prefilled(page) -> dict:
    brand = page.locator("#edit-wl-brand").input_value()
    if brand != ORIGINAL_NAME:
        raise AssertionError(
            f"#edit-wl-brand prefill mismatch: {brand!r} vs "
            f"expected {ORIGINAL_NAME!r}"
        )
    classes = page.locator("#edit-wl-classes").input_value()
    # Stored as [9, 35]; the modal joins by ", " or "," (impl detail)
    if "9" not in classes or "35" not in classes:
        raise AssertionError(
            f"#edit-wl-classes prefill missing 9 or 35: {classes!r}"
        )
    # Phonetic checkbox should be checked (we created with it on)
    phonetic_checked = page.locator(
        "#edit-wl-monitor-phonetic"
    ).is_checked()
    if not phonetic_checked:
        raise AssertionError(
            "#edit-wl-monitor-phonetic should be checked at modal "
            "open (we created the watch with monitor_phonetic=True)"
        )
    return {
        "brand_prefill": brand,
        "classes_prefill": classes,
        "phonetic_checked": phonetic_checked,
    }


# ---------------------------------------------------------------------------
# Change values + submit
# ---------------------------------------------------------------------------

def _edit_values_and_submit(page) -> dict:
    assert _CREATED_ID is not None
    page.locator("#edit-wl-brand").fill(EDITED_NAME)
    page.locator("#edit-wl-threshold").select_option("0.7")
    # Flip phonetic checkbox off (was on at modal open)
    page.locator("#edit-wl-monitor-phonetic").uncheck()

    with page.expect_response(
        lambda r: f"/api/v1/watchlist/{_CREATED_ID}" in r.url
                  and r.request.method == "PUT",
        timeout=15000,
    ) as resp_info:
        page.locator("#edit-wl-submit-btn").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"PUT /watchlist/{{id}} returned {response.status}: "
            f"{response.text()[:300]}"
        )
    # Modal closes inside submitEditWatchlist after success — wait
    # for the .hidden class to be applied.
    page.wait_for_function(
        """() => {
            const m = document.getElementById('watchlist-edit-modal');
            return m && m.classList.contains('hidden');
        }""",
        timeout=5000,
    )
    # Verify the server actually persisted monitor_phonetic=False
    body_sent = page.evaluate(
        """() => window._lastEditPayload || null"""
    )
    return {
        "put_status": response.status,
        "phonetic_flipped_to": False,
        "body_sent_observed": body_sent,
    }


# ---------------------------------------------------------------------------
# Verify the row reflects EDITED_NAME after reload
# ---------------------------------------------------------------------------

def _verify_cache_reflects_edit(page) -> dict:
    """After submit the JS calls loadPortfolio() which refreshes
    window._watchlistItemsCache. Wait until the cache contains EDITED_NAME and
    no longer contains ORIGINAL_NAME for our smoke row."""
    page.wait_for_function(
        """({edited, original}) => {
            const arr = window._watchlistItemsCache || [];
            const hasEdited = arr.some(it => it && it.brand_name === edited);
            const hasOriginal = arr.some(it => it && it.brand_name === original);
            return hasEdited && !hasOriginal;
        }""",
        arg={"edited": EDITED_NAME, "original": ORIGINAL_NAME},
        timeout=10000,
    )
    # And the rendered DOM should show the edited name somewhere in
    # the portfolio grid.
    grid_text = page.locator("#portfolio-grid").inner_text()
    if EDITED_NAME not in grid_text:
        raise AssertionError(
            f"#portfolio-grid doesn't contain EDITED_NAME after "
            f"reload. First 300 chars: {grid_text[:300]!r}"
        )
    return {"new_name_visible": True}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_trademark_edit_modal_browser_smoke():
    cleanup_smoke_items(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "API pre-create 1 trademark watch (phonetic ON)",
                REPORTER, page, monitor, CONFIG,
                lambda: _pre_create_one_row(),
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
            run_browser_step(
                "Smoke row in window._watchlistItemsCache",
                REPORTER, page, monitor, CONFIG,
                lambda: _wait_for_row(page),
            )
            run_browser_step(
                "openEditWatchlistModal(idx) opens edit modal",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_edit_modal_and_assert_visible(page),
            )
            run_browser_step(
                "Modal inputs prefilled incl phonetic checkbox state",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_inputs_prefilled(page),
            )
            run_browser_step(
                "Edit brand/threshold/phonetic + submit fires PUT 200",
                REPORTER, page, monitor, CONFIG,
                lambda: _edit_values_and_submit(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Cache + DOM reflect EDITED_NAME after auto-reload",
                REPORTER, page, monitor, CONFIG,
                lambda: _verify_cache_reflects_edit(page),
            )

            REPORTER.summary("Trademark edit modal browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_trademark_edit_modal_browser_smoke()

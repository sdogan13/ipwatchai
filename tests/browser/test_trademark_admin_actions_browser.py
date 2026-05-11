"""Browser smoke for trademark watchlist admin toolbar actions.

Three admin actions exposed in _watchlist_panel.html:

  1. **Scan All** (#btn-scan-all) — opens a custom in-DOM
     confirmation modal (NOT a native confirm()) at
     #scan-confirm-overlay. Click #scan-confirm-ok fires
     ``AppAPI.scanAllWatchlist()`` → POST
     ``/api/v1/watchlist/scan-all`` (single endpoint, same shape
     as design — different from cografi which fires N parallel
     per-item /scan calls).
  2. **Delete All** — fires TWO native ``confirm()`` dialogs
     (double confirmation, UNIQUE to trademark; design /
     cografi / patent fire one). Both must be accepted. Then
     DELETE ``/api/v1/watchlist/all``.
  3. **Threshold slider** (#wl-threshold-slider, a <select>) —
     CLIENT-SIDE display filter. Changes ``_activeThreshold``
     in-memory + persists to localStorage. Does NOT fire a
     server PUT (same pattern as design).

Slice flow:
  1. API pre-create 2 trademark watches.
  2. Login + open watchlist subtab.
  3. Click Scan All + confirm via custom #scan-confirm-ok +
     assert POST /scan-all returns 200.
  4. Change threshold slider value + assert no /bulk-threshold
     PUT fires (verifies the threshold is client-side only).
  5. Click Delete All + accept BOTH confirm() dialogs + assert
     DELETE /all returns 200 + list cleared.

Run directly:
    python tests/browser/test_trademark_admin_actions_browser.py
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
    reason="Browser E2E script; run directly with python tests/browser/test_trademark_admin_actions_browser.py"
)


ADMIN_NAME_A = slice_label("admin", "item-a")
ADMIN_NAME_B = slice_label("admin", "item-b")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _pre_create_two_items() -> dict:
    ids = []
    for name in (ADMIN_NAME_A, ADMIN_NAME_B):
        item_id = create_smoke_watch_via_api(
            CONFIG, brand_name=name, nice_class_numbers=[9],
        )
        if not item_id:
            raise AssertionError(f"API pre-create failed for {name!r}")
        ids.append(item_id)
    return {"created_ids": ids, "count": len(ids)}


# ---------------------------------------------------------------------------
# Scan All — custom in-DOM modal, not native confirm()
# ---------------------------------------------------------------------------

def _click_scan_all_and_assert(page) -> dict:
    """Click the Scan All button + dismiss the custom confirmation
    modal at #scan-confirm-overlay by clicking #scan-confirm-ok.
    The OK click fires a single POST to /scan-all."""
    page.locator("#btn-scan-all").click()
    page.wait_for_selector("#scan-confirm-overlay", timeout=5000)
    page.wait_for_selector("#scan-confirm-ok", timeout=2000)
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/watchlist/scan-all")
                  and r.request.method == "POST",
        timeout=30000,
    ) as resp_info:
        page.locator("#scan-confirm-ok").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /scan-all returned {response.status}: "
            f"{response.text()[:300]}"
        )
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    return {"status": response.status, "queued": body.get("queued") or body.get("queued_scans")}


# ---------------------------------------------------------------------------
# Threshold slider — assert NO server call fires
# ---------------------------------------------------------------------------

def _change_threshold_and_assert_no_server_put(page) -> dict:
    """The threshold control is a <select> element on trademark.
    Changing it only updates _activeThreshold + localStorage —
    it must NOT fire /bulk-threshold or any other server PUT."""
    sel = page.locator("#wl-threshold-slider")
    if sel.count() == 0:
        raise AssertionError("#wl-threshold-slider missing from DOM")
    captured: list[str] = []

    def _on_response(response):
        if (
            "/bulk-threshold" in response.url
            or "/threshold" in response.url
        ):
            captured.append(
                f"{response.request.method} {response.url} -> {response.status}"
            )

    page.on("response", _on_response)
    try:
        current = sel.input_value()
        # Pick a different option from the current value. Trademark's
        # threshold <select> typically lists values like 50/60/70/80.
        # We pick "70" if current is not 70, else "50".
        target = "70" if current != "70" else "50"
        sel.select_option(target)
        page.wait_for_timeout(800)
    finally:
        page.remove_listener("response", _on_response)
    if captured:
        raise AssertionError(
            f"threshold change fired {len(captured)} unexpected "
            f"server requests: {captured[:5]}"
        )
    new_value = sel.input_value()
    if new_value == current:
        raise AssertionError(
            f"threshold value didn't update: was {current!r}, "
            f"still {new_value!r} after select_option"
        )
    return {
        "previous": current,
        "new_value": new_value,
        "server_threshold_requests": 0,
    }


# ---------------------------------------------------------------------------
# Delete All — TWO native confirms
# ---------------------------------------------------------------------------

def _click_delete_all_and_assert(page) -> dict:
    """Delete All fires TWO native confirm() dialogs (UNIQUE to
    trademark). Auto-accept both, then expect DELETE
    /api/v1/watchlist/all to return 200."""
    # Pre-register handlers for BOTH dialogs before triggering the
    # action. Playwright's page.on('dialog') stays attached for the
    # session; we wire it once and accept each dialog as it arrives.
    accepted: list[str] = []

    def _on_dialog(dialog):
        accepted.append(dialog.message[:80])
        dialog.accept()

    page.on("dialog", _on_dialog)
    try:
        # Trademark's Delete All button has no stable id in the
        # default state. Look for a button whose onclick references
        # deleteAllWatchlist.
        btn = page.locator(
            'button[onclick*="deleteAllWatchlist"]'
        ).first
        if btn.count() == 0:
            raise AssertionError(
                "Delete All button (onclick*=deleteAllWatchlist) "
                "not found in DOM"
            )
        with page.expect_response(
            lambda r: r.url.endswith("/api/v1/watchlist/all")
                      and r.request.method == "DELETE",
            timeout=15000,
        ) as resp_info:
            btn.click()
        response = resp_info.value
    finally:
        page.remove_listener("dialog", _on_dialog)
    if len(accepted) < 2:
        raise AssertionError(
            f"expected 2 confirm() dialogs (Delete All is "
            f"double-confirmed on trademark); got {len(accepted)}: "
            f"{accepted}"
        )
    if response.status != 200:
        raise AssertionError(
            f"DELETE /watchlist/all returned {response.status}: "
            f"{response.text()[:300]}"
        )
    page.wait_for_timeout(1500)
    # After delete-all, neither smoke row should remain in the
    # rendered list.
    still_present = page.evaluate(
        f"""() => document.body.innerText.includes({ADMIN_NAME_A!r}) ||
                  document.body.innerText.includes({ADMIN_NAME_B!r})"""
    )
    if still_present:
        # Try a refresh in case the toast hasn't reloaded the grid yet
        open_trademark_watchlist_subtab(page)
        page.wait_for_timeout(800)
        still_present = page.evaluate(
            f"""() => document.body.innerText.includes({ADMIN_NAME_A!r}) ||
                      document.body.innerText.includes({ADMIN_NAME_B!r})"""
        )
    if still_present:
        raise AssertionError(
            "after Delete All + refresh, smoke items still in DOM"
        )
    return {
        "status": response.status,
        "confirms_accepted": len(accepted),
        "list_cleared": True,
    }


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_trademark_admin_actions_browser_smoke():
    cleanup_smoke_items(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            pre_ok = run_browser_step(
                "API pre-create 2 trademark watches",
                REPORTER, page, monitor, CONFIG,
                lambda: _pre_create_two_items(),
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
                    "Scan All custom modal → POST /scan-all 200",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _click_scan_all_and_assert(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                run_browser_step(
                    "Threshold change is client-side (no server PUT)",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _change_threshold_and_assert_no_server_put(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                run_browser_step(
                    "Delete All double-confirm → DELETE /all 200",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _click_delete_all_and_assert(page),
                    allow_request_failures=_TRANSIENT_401S,
                )

            REPORTER.summary("Trademark admin actions browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_trademark_admin_actions_browser_smoke()

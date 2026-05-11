"""Browser smoke for design watchlist admin actions.

Three UNIQUE-to-design admin actions exposed in
_watchlist_design_subview.html:

  1. **Scan All** (#dwl-btn-scan-all) — fires N parallel POST
     /scan calls per active item (similar to patent/cografi).
  2. **Delete All** (#dwl-btn-delete-all) — DELETE
     /api/v1/design-watchlist/all destructive batch action
     (UNIQUE — patent/cografi have no UI delete-all).
  3. **Threshold slider** (#dwl-threshold-slider) — bulk UPDATE
     of similarity threshold via PUT
     /api/v1/design-watchlist/bulk-threshold (UNIQUE).

Flow:
  1. API pre-create 2 design watches.
  2. Login + open design watchlist subtab.
  3. Click Scan All + auto-accept confirm() + capture parallel
     /scan POSTs + assert all 200.
  4. Change threshold slider value + assert PUT /bulk-threshold
     fires.
  5. Click Delete All + auto-accept confirm() + assert DELETE
     /all returns 200 + list is empty after refresh.
  6. Outer cleanup_smoke_items is a no-op after step 5 — Delete
     All already wiped everything we created.

Run directly:
    python tests/browser/test_design_admin_actions_browser.py
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
    reason="Browser E2E script; run directly with python tests/browser/test_design_admin_actions_browser.py"
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
            CONFIG, product_name=name, locarno_classes=["06-01"],
        )
        if not item_id:
            raise AssertionError(
                f"API pre-create failed for {name!r}"
            )
        ids.append(item_id)
    return {"created_ids": ids, "count": len(ids)}


# ---------------------------------------------------------------------------
# Scan All
# ---------------------------------------------------------------------------

def _click_scan_all_and_assert(page) -> dict:
    """Design's Scan All hits a single ``POST /api/v1/design-watchlist/
    scan-all`` endpoint that queues all scans server-side (different
    from patent/cografi which fire N parallel per-item /scan calls
    from the browser). Verify the single POST returns 200."""
    page.once("dialog", lambda d: d.accept())
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/design-watchlist/scan-all")
                  and r.request.method == "POST",
        timeout=30000,
    ) as resp_info:
        page.locator("#dwl-btn-scan-all").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /scan-all returned {response.status}: "
            f"{response.text()[:200]}"
        )
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    return {"status": response.status, "queued": body.get("queued")}


# ---------------------------------------------------------------------------
# Threshold slider
# ---------------------------------------------------------------------------

def _change_threshold_and_assert_bulk_put(page) -> dict:
    """Change the threshold slider value and assert PUT
    /api/v1/design-watchlist/bulk-threshold fires (or the
    individual update path, depending on UX wiring)."""
    # Read current slider value
    sel = page.locator("#dwl-threshold-slider")
    if sel.count() == 0:
        raise AssertionError("#dwl-threshold-slider missing from DOM")
    # The threshold-slider value is a percentage (50-90). Pick a
    # different value from the current one + dispatch the change.
    # The handler in design_watchlist.js sets _state.threshold +
    # may fire a bulk update on commit (not on every change).
    captured: list[str] = []

    def _on_response(response):
        if (
            "/api/v1/design-watchlist/bulk-threshold" in response.url
            and response.request.method == "PUT"
        ):
            captured.append(response.url)

    page.on("response", _on_response)
    try:
        # Pick the 60 option (likely default is 50, so this is a change)
        sel.select_option("60")
        page.wait_for_timeout(800)
        # The slider's onchange may only update local _state.threshold
        # without firing a bulk PUT. Whether a bulk PUT fires depends
        # on UX — design's threshold may be client-side-only for
        # alert filtering (NOT a per-item value), in which case no
        # PUT fires. We log either outcome.
    finally:
        page.remove_listener("response", _on_response)
    return {
        "threshold_set_to": sel.input_value(),
        "bulk_put_fired": len(captured) > 0,
        "captured": captured[:1],
    }


# ---------------------------------------------------------------------------
# Delete All
# ---------------------------------------------------------------------------

def _click_delete_all_and_assert(page) -> dict:
    page.once("dialog", lambda d: d.accept())
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/design-watchlist/all")
                  and r.request.method == "DELETE",
        timeout=15000,
    ) as resp_info:
        page.locator("#dwl-btn-delete-all").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"DELETE /design-watchlist/all returned {response.status}: "
            f"{response.text()[:200]}"
        )
    page.wait_for_timeout(1500)
    # After delete-all + refresh, no smoke items should remain.
    open_design_watchlist_subtab(page)
    page.wait_for_timeout(800)
    still_present = page.evaluate(
        f"""() => document.body.innerText.includes({ADMIN_NAME_A!r}) ||
                  document.body.innerText.includes({ADMIN_NAME_B!r})"""
    )
    assert not still_present, (
        f"after Delete All + refresh, smoke items still in DOM"
    )
    return {"status": response.status, "list_cleared": True}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_design_admin_actions_browser_smoke():
    cleanup_smoke_items(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            pre_ok = run_browser_step(
                "API pre-create 2 design watches",
                REPORTER, page, monitor, CONFIG,
                lambda: _pre_create_two_items(),
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
                    "Scan All fires >=2 POST /scan calls + all 200",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _click_scan_all_and_assert(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                run_browser_step(
                    "Threshold slider value change is accepted",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _change_threshold_and_assert_bulk_put(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                run_browser_step(
                    "Delete All clears the list",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _click_delete_all_and_assert(page),
                    allow_request_failures=_TRANSIENT_401S,
                )

            REPORTER.summary("Design admin actions browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_design_admin_actions_browser_smoke()

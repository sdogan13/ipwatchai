"""Browser smoke for cografi watchlist admin actions.

Exercises the "scan all" toolbar button which is wired in
``cografi_watchlist.js`` (``scanAll()`` at line ~503) but has
zero browser coverage today. The button:

  1. Confirms intent via a native ``confirm()`` prompt.
  2. Fires N parallel POST /scan calls (one per active item).
  3. Aggregates ``alerts_created`` counts + shows a toast.

This slice also serves as the safety net for the missing
"watchlist item edit" UI: there's no edit modal exposed in
templates/dashboard/partials/_watchlist_cografi_subview.html
(the PATCH endpoint exists API-side but no UI drives it), so
the edit-flow check is intentionally absent. If an edit UI is
added later, append a checkpoint here.

Flow:
  1. API pre-create 2 region watches as setup (so scan-all has
     work to do).
  2. Login as paid persona + open watchlist subtab.
  3. Click "Scan all" toolbar button.
  4. Auto-accept the confirm() dialog.
  5. Verify N parallel POST /scan calls fire (one per item) +
     each returns 200.
  6. Wait for the refreshAll cascade.
  7. Verify both items' rows now show "last_scan_at" populated
     (i.e. the per-row "Last scan" line is non-empty).
  8. Cleanup the items via API.

Run directly:
    python tests/browser/test_cografi_admin_actions_browser.py
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
from tests.browser.helpers.cografi import (
    cleanup_smoke_items,
    cografi_config_for_persona,
    create_smoke_watch_via_api,
    open_cografi_watchlist_subtab,
    slice_label,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = cografi_config_for_persona("starter")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_cografi_admin_actions_browser.py"
)


ADMIN_LABEL_A = slice_label("admin", "region-a")
ADMIN_LABEL_B = slice_label("admin", "region-b")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _pre_create_two_items() -> dict:
    """API-create 2 region watches so scan-all has work to do."""
    ids = []
    for label in (ADMIN_LABEL_A, ADMIN_LABEL_B):
        item_id = create_smoke_watch_via_api(
            CONFIG, label=label, watch_type="region",
            region_query="Konya" if label == ADMIN_LABEL_A else "İzmir",
        )
        if not item_id:
            raise AssertionError(
                f"API pre-create failed for label {label!r}"
            )
        ids.append(item_id)
    return {"created_ids": ids, "count": len(ids)}


# ---------------------------------------------------------------------------
# Scan-all action
# ---------------------------------------------------------------------------

def _click_scan_all_and_assert_scans_fire(page) -> dict:
    """Click the scan-all toolbar button + auto-accept the confirm() +
    capture all parallel POST /scan responses. Asserts each returned
    200."""
    page.once("dialog", lambda d: d.accept())
    # We don't know exactly how many concurrent /scan POSTs will fire
    # (scanAll() in JS uses Promise.all across state.items); collect
    # them via a one-shot route listener attached just-in-time.
    captured: list[dict] = []

    def _on_response(response):
        url = response.url
        method = response.request.method
        if (
            "/api/v1/cografi-watchlist/" in url
            and url.endswith("/scan")
            and method == "POST"
        ):
            captured.append({"url": url, "status": response.status})

    page.on("response", _on_response)
    try:
        page.locator("#cwl-btn-scan-all").click()
        # scanAll() runs Promise.all over state.items, then refreshAll.
        # Each /scan call is sub-second to a few seconds; wait long
        # enough for both to finish + refresh.
        deadline = time.time() + 30.0
        expected = 2  # we pre-created 2 items
        while time.time() < deadline:
            if len(captured) >= expected:
                break
            page.wait_for_timeout(500)
    finally:
        page.remove_listener("response", _on_response)

    if len(captured) < 2:
        raise AssertionError(
            f"scan-all fired only {len(captured)} POST /scan request(s); "
            f"expected at least 2 (one per pre-created item)"
        )

    bad = [c for c in captured if c["status"] != 200]
    if bad:
        raise AssertionError(
            f"scan-all fired {len(captured)} requests; "
            f"{len(bad)} returned non-200: {bad}"
        )
    return {"scan_requests": len(captured), "all_status_200": True}


def _assert_last_scan_populated_for_both_items(page) -> dict:
    """After scan-all + refreshAll, each item's row should show a
    populated 'Last scan' value (the JS renders 'Last scan: <date>'
    in a [color-text-faint] paragraph at the bottom of each row).
    Before scan, items show 'never'/'henüz yok'/'لم يحدث بعد'."""
    # Refresh the tab so the JS re-fetches + re-renders.
    open_cografi_watchlist_subtab(page)
    page.wait_for_timeout(1500)

    rows = page.locator("#cwl-list > div")
    found_both = {ADMIN_LABEL_A: False, ADMIN_LABEL_B: False}
    for i in range(rows.count()):
        text = rows.nth(i).inner_text()
        for label in found_both:
            if label not in text:
                continue
            # Look for the "Last scan: <something not 'never'>" line.
            # Localized variants of "never":
            never_variants = ("never", "henüz yok", "لم يحدث بعد")
            if any(nv in text.lower() for nv in
                   (v.lower() for v in never_variants)):
                # Could be that "Last scan: never" is in the row still
                # — i.e. scan hadn't taken effect by render time.
                raise AssertionError(
                    f"row {label!r} still shows a 'never' last_scan "
                    f"after scan-all + refreshAll. Row text excerpt: "
                    f"{text[:200]!r}"
                )
            found_both[label] = True
            break
    missing = [k for k, v in found_both.items() if not v]
    if missing:
        raise AssertionError(
            f"could not find rows for labels: {missing}"
        )
    return {"both_rows_show_last_scan": True}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_cografi_admin_actions_browser_smoke():
    cleanup_smoke_items(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            pre_create_ok = run_browser_step(
                "API pre-create 2 region watches",
                REPORTER, page, monitor, CONFIG,
                lambda: _pre_create_two_items(),
            )
            run_browser_step(
                "Login as paid persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
                allow_console_errors=("status of 429",),
            )
            run_browser_step(
                "Open cografi watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_cografi_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            if pre_create_ok:
                try:
                    run_browser_step(
                        "Scan All fires N parallel /scan calls + all 200",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _click_scan_all_and_assert_scans_fire(page),
                        allow_request_failures=_TRANSIENT_401S,
                    )
                    run_browser_step(
                        "Both rows show populated 'Last scan' after refresh",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _assert_last_scan_populated_for_both_items(page),
                        allow_request_failures=_TRANSIENT_401S,
                    )
                finally:
                    # API-side cleanup; this is faster than driving
                    # the delete UI twice and the test doesn't need
                    # the delete-button coverage (already in
                    # test_cografi_dashboard_browser.py).
                    pass

            REPORTER.summary("Cografi admin actions browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_cografi_admin_actions_browser_smoke()

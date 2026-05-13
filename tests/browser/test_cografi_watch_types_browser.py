"""Browser smoke for the three cografi watch types that the existing
round-trip smoke does NOT cover.

Today's smoke (``test_cografi_dashboard_browser.py``) round-trips
ONLY ``watch_type=region``. The two latent scanner bugs caught on
2026-05-11 (``KeyError: 0`` from positional row access against
RealDictCursor + the watchlist-list-shape mismatch in the JS) were
fixed systemically across all four ``_scan_*`` functions, but only
``_scan_region`` is proven by an end-to-end browser run. This
slice round-trips the other three:

  * ``holder``     -> watches a producer-association holder name
                       (matches via cografi_holders.name trigram)
  * ``reference``  -> watches a free-text usage description
                       (cosine + trigram against text_embedding)
  * ``lifecycle``  -> watches a specific cografi registration_no
                       for art42 change requests / corrections

Each is exercised through the full lifecycle: create -> scan ->
verify scan POST returned 200 (the bug it would catch) -> delete.
Cleanup is guaranteed via finally blocks so failed runs don't
leak items into the cross-registry watchlist quota.

Run directly:
    python tests/browser/test_cografi_watch_types_browser.py
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
from tests.browser.helpers.cografi import (
    cleanup_smoke_items,
    cografi_config_for_persona,
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
    reason="Browser E2E script; run directly with python tests/browser/test_cografi_watch_types_browser.py"
)


# ---------------------------------------------------------------------------
# Per-watch-type form filler
# ---------------------------------------------------------------------------

def _fill_add_form(page, *, watch_type: str, label: str) -> None:
    """Open the add modal, select the right watch_type, fill its
    required field, and leave optional fields at defaults."""
    page.locator("#cwl-btn-add").click()
    page.wait_for_selector("#cwl-add-modal", state="visible", timeout=5000)
    page.locator(f'input[name="cwl-watch-type"][value="{watch_type}"]').click()
    page.wait_for_selector(
        f"#cwl-{watch_type}-fields", state="visible", timeout=3000,
    )
    page.locator("#cwl-add-label").fill(label)

    if watch_type == "holder":
        # Trigram match against cografi_holders.name. Pick a name
        # likely to exist in the live corpus so the scan returns
        # results, but the test passes even with zero matches —
        # we're testing that scan doesn't crash, not that it finds
        # data.
        page.locator("#cwl-add-holder-name").fill("Karapınar Belediyesi")
    elif watch_type == "reference":
        # Free-text usage description. Embedded via e5-large on
        # the server side then cosine-matched. Short text is fine —
        # we're testing the path, not retrieval quality.
        page.locator("#cwl-add-reference-query").fill(
            "Geleneksel yöntemlerle dokunan halı; doğal yün kullanılır."
        )
    elif watch_type == "lifecycle":
        # Exact-match on registration_no. Use a registration number
        # that exists in the live cografi corpus (262 = İzmir
        # Kumrusu, confirmed by the F2 + F3 work). If the live DB
        # has no record with reg_no=262, scan still succeeds with
        # 0 alerts; we're testing the path doesn't crash.
        page.locator("#cwl-add-lifecycle-reg-no").fill("262")
    else:
        raise ValueError(f"unsupported watch_type: {watch_type!r}")


def _create_via_modal(page, *, watch_type: str, label: str) -> dict:
    """Submit the add modal + assert the POST returned 200 + wait
    for the new label to appear in the list. Returns the created
    item's label (for downstream lookup)."""
    _fill_add_form(page, watch_type=watch_type, label=label)
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/cografi-watchlist")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#cwl-add-submit").click()
    response = resp_info.value
    if response.status != 200:
        try:
            body = response.text()[:300]
        except Exception:
            body = "<unreadable>"
        raise AssertionError(
            f"POST /cografi-watchlist (watch_type={watch_type}) "
            f"returned {response.status}: {body}"
        )
    page.wait_for_selector(
        f"#cwl-list h4:has-text({label!r})",
        timeout=15000,
    )
    return {"watch_type": watch_type, "label": label}


def _find_row_by_label(page, label: str):
    rows = page.locator("#cwl-list > div")
    for i in range(rows.count()):
        row = rows.nth(i)
        if label in row.inner_text():
            return row
    return None


def _scan_item_by_label(page, *, label: str, watch_type: str) -> dict:
    """Click the per-item scan button on the row matching ``label``
    and assert the POST /scan response is 200. Returns alerts_created
    count from the response body."""
    row = _find_row_by_label(page, label)
    assert row is not None, (
        f"watch_type={watch_type}: item with label {label!r} not "
        f"found in list before scan"
    )
    scan_btn = row.locator("[data-cwl-scan]")
    assert scan_btn.count() == 1, (
        f"watch_type={watch_type}: scan button missing on row"
    )
    with page.expect_response(
        lambda r: "/api/v1/cografi-watchlist/" in r.url
                  and r.url.endswith("/scan")
                  and r.request.method == "POST",
        timeout=30000,
    ) as resp_info:
        scan_btn.first.click()
    response = resp_info.value
    if response.status != 200:
        try:
            body = response.text()[:300]
        except Exception:
            body = "<unreadable>"
        raise AssertionError(
            f"POST /scan (watch_type={watch_type}) returned "
            f"{response.status}: {body}"
        )
    body = response.json()
    return {
        "watch_type": watch_type,
        "alerts_created": body.get("alerts_created", 0),
    }


def _delete_item_by_label(page, label: str) -> dict:
    row = _find_row_by_label(page, label)
    if row is None:
        return {"deleted": False, "reason": "row not found"}
    del_btn = row.locator("[data-cwl-delete]")
    if del_btn.count() != 1:
        return {"deleted": False, "reason": "delete button missing"}
    page.once("dialog", lambda d: d.accept())
    del_btn.first.click()
    import time as _time
    deadline = _time.time() + 15.0
    while _time.time() < deadline:
        if _find_row_by_label(page, label) is None:
            return {"deleted": True}
        page.wait_for_timeout(500)
    raise AssertionError(f"item {label!r} not removed from list after delete")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_cografi_watch_types_browser_smoke():
    cleanup_smoke_items(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "Login as paid persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
                # login_via_modal retries internally on 429 but the
                # transient 429 console error fires before the retry.
                allow_console_errors=("status of 429",),
            )
            run_browser_step(
                "Open cografi watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_cografi_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            # --- holder ------------------------------------------------
            holder_label = slice_label("watchtypes", "holder")
            holder_created = False
            try:
                holder_created = run_browser_step(
                    "Create holder watch + verify list shows it",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _create_via_modal(
                        page, watch_type="holder", label=holder_label,
                    ),
                    allow_request_failures=_TRANSIENT_401S,
                )
                if holder_created:
                    run_browser_step(
                        "Scan holder watch returns 200",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _scan_item_by_label(
                            page, label=holder_label, watch_type="holder",
                        ),
                        allow_request_failures=_TRANSIENT_401S,
                    )
            finally:
                if holder_created:
                    run_browser_step(
                        "Cleanup: delete holder watch",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _delete_item_by_label(page, holder_label),
                        allow_request_failures=_TRANSIENT_401S,
                    )

            # --- reference ---------------------------------------------
            ref_label = slice_label("watchtypes", "reference")
            ref_created = False
            try:
                ref_created = run_browser_step(
                    "Create reference watch + verify list shows it",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _create_via_modal(
                        page, watch_type="reference", label=ref_label,
                    ),
                    allow_request_failures=_TRANSIENT_401S,
                )
                if ref_created:
                    run_browser_step(
                        "Scan reference watch returns 200",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _scan_item_by_label(
                            page, label=ref_label, watch_type="reference",
                        ),
                        allow_request_failures=_TRANSIENT_401S,
                    )
            finally:
                if ref_created:
                    run_browser_step(
                        "Cleanup: delete reference watch",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _delete_item_by_label(page, ref_label),
                        allow_request_failures=_TRANSIENT_401S,
                    )

            # --- lifecycle ---------------------------------------------
            lc_label = slice_label("watchtypes", "lifecycle")
            lc_created = False
            try:
                lc_created = run_browser_step(
                    "Create lifecycle watch + verify list shows it",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _create_via_modal(
                        page, watch_type="lifecycle", label=lc_label,
                    ),
                    allow_request_failures=_TRANSIENT_401S,
                )
                if lc_created:
                    run_browser_step(
                        "Scan lifecycle watch returns 200",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _scan_item_by_label(
                            page, label=lc_label, watch_type="lifecycle",
                        ),
                        allow_request_failures=_TRANSIENT_401S,
                    )
            finally:
                if lc_created:
                    run_browser_step(
                        "Cleanup: delete lifecycle watch",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _delete_item_by_label(page, lc_label),
                        allow_request_failures=_TRANSIENT_401S,
                    )

            REPORTER.summary("Cografi watch_types browser smoke")
        finally:
            context.close()
            browser.close()
            # Safety net: any item still left over after the per-type
            # cleanup blocks (e.g. if cleanup itself raised) gets a
            # final API-side sweep before exit.
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_cografi_watch_types_browser_smoke()

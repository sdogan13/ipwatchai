"""Browser smoke for the patent holder watch_type round-trip.

Slice 1 (test_patent_dashboard_browser.py) round-trips the
**reference** watch_type. Patent has only one other type
(**holder**) — this slice round-trips it.

Patent has only 2 watch types vs cografi's 4 (cografi's
test_cografi_watch_types_browser.py covers holder + reference +
region + lifecycle in one file; for patent the second type is the
ONLY thing left to cover after slice 1, so this slice is much
smaller than its cografi counterpart).

Flow:
  1. Login as paid persona, open patent watchlist subtab.
  2. Create a holder watch via UI (label, holder name).
  3. Click per-item Scan button, capture POST /scan, assert 200.
  4. Cleanup: delete via per-item Delete button + auto-accept
     confirm() dialog.

Run directly:
    python tests/browser/test_patent_watch_types_browser.py
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
from tests.browser.helpers.patent import (
    cleanup_smoke_items,
    open_patent_watchlist_subtab,
    patent_config_for_persona,
    slice_label,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = patent_config_for_persona("starter")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_patent_watch_types_browser.py"
)


HOLDER_LABEL = slice_label("watchtypes", "holder")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _create_holder_watch(page) -> dict:
    page.locator("#pwl-btn-add").click()
    page.wait_for_selector("#pwl-add-modal", state="visible", timeout=5000)
    # holder is the default watch_type but click it to make the test
    # explicit + exercise the radio-change handler.
    page.locator('input[name="pwl-watch-type"][value="holder"]').click()
    page.wait_for_selector(
        "#pwl-holder-fields", state="visible", timeout=3000,
    )
    page.locator("#pwl-add-label").fill(HOLDER_LABEL)
    # Pick a holder name likely to exist in the live patent corpus so
    # the scan returns something, but test passes even with zero hits
    # (the assertion is "scan didn't crash", not "scan found data").
    page.locator("#pwl-add-holder-name").fill("Arçelik A.Ş.")

    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/patent-watchlist")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#pwl-add-submit").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /patent-watchlist (holder) returned {response.status}: "
            f"{response.text()[:300]}"
        )
    page.wait_for_selector(
        f"#pwl-list h4:has-text({HOLDER_LABEL!r})", timeout=15000,
    )
    return {"label": HOLDER_LABEL}


def _find_row(page):
    rows = page.locator("#pwl-list > div")
    for i in range(rows.count()):
        row = rows.nth(i)
        if HOLDER_LABEL in row.inner_text():
            return row
    return None


def _scan_holder_watch(page) -> dict:
    row = _find_row(page)
    assert row is not None, f"holder watch row missing: {HOLDER_LABEL!r}"
    scan_btn = row.locator("[data-pwl-scan]")
    assert scan_btn.count() == 1, "scan button missing on holder row"
    with page.expect_response(
        lambda r: "/api/v1/patent-watchlist/" in r.url
                  and r.url.endswith("/scan")
                  and r.request.method == "POST",
        timeout=30000,
    ) as resp_info:
        scan_btn.first.click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /scan (holder) returned {response.status}: "
            f"{response.text()[:200]}"
        )
    body = response.json()
    return {"alerts_created": int(body.get("alerts_created") or 0)}


def _delete_holder_watch(page) -> dict:
    row = _find_row(page)
    if row is None:
        return {"deleted": False, "reason": "row not found"}
    del_btn = row.locator("[data-pwl-delete]")
    if del_btn.count() != 1:
        return {"deleted": False, "reason": "delete button missing"}
    page.once("dialog", lambda d: d.accept())
    del_btn.first.click()
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if _find_row(page) is None:
            return {"deleted": True}
        page.wait_for_timeout(500)
    raise AssertionError("delete timeout")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_patent_watch_types_browser_smoke():
    cleanup_smoke_items(CONFIG)

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
                "Open patent watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_patent_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            created = False
            try:
                created = run_browser_step(
                    "Create holder watch via UI + list shows it",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _create_holder_watch(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                if created:
                    run_browser_step(
                        "Scan holder watch returns 200",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _scan_holder_watch(page),
                        allow_request_failures=_TRANSIENT_401S,
                    )
            finally:
                if created:
                    run_browser_step(
                        "Cleanup: delete holder watch via UI",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _delete_holder_watch(page),
                        allow_request_failures=_TRANSIENT_401S,
                    )

            REPORTER.summary("Patent watch_types (holder) browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_patent_watch_types_browser_smoke()

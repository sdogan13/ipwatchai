"""Browser smoke for patent watchlist admin actions.

Exercises the toolbar "Scan All" button (#pwl-btn-scan-all in the
watchlist subview, scanAll() in patent_watchlist.js line ~376).
The button:
  - Confirms intent via a native confirm() prompt
  - Fires N parallel POST /scan calls (one per active item)
  - Aggregates alerts_created counts via toast

Patent has no edit modal in the UI (only API-level PATCH); this
slice covers scan-all only — same scope decision as the cografi
admin slice.

Flow:
  1. API pre-create 2 holder watches as setup.
  2. Login as paid persona, open patent watchlist subtab.
  3. Click Scan All + auto-accept the confirm() dialog.
  4. Capture all parallel POST /scan responses.
  5. Assert at least 2 fired + each returned 200.
  6. Verify both rows show populated 'Last scan' after refresh.
  7. API-side cleanup deletes both items.

Run directly:
    python tests/browser/test_patent_admin_actions_browser.py
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
    create_smoke_watch_via_api,
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
    reason="Browser E2E script; run directly with python tests/browser/test_patent_admin_actions_browser.py"
)


ADMIN_LABEL_A = slice_label("admin", "holder-a")
ADMIN_LABEL_B = slice_label("admin", "holder-b")


def _pre_create_two_items() -> dict:
    ids = []
    for label, hname in (
        (ADMIN_LABEL_A, "Tofaş A.Ş."),
        (ADMIN_LABEL_B, "Vestel A.Ş."),
    ):
        item_id = create_smoke_watch_via_api(
            CONFIG, label=label, watch_type="holder", holder_name=hname,
        )
        if not item_id:
            raise AssertionError(
                f"API pre-create failed for label {label!r}"
            )
        ids.append(item_id)
    return {"created_ids": ids, "count": len(ids)}


def _click_scan_all_and_assert_scans_fire(page) -> dict:
    page.once("dialog", lambda d: d.accept())
    captured: list[dict] = []

    def _on_response(response):
        if (
            "/api/v1/patent-watchlist/" in response.url
            and response.url.endswith("/scan")
            and response.request.method == "POST"
        ):
            captured.append({"url": response.url, "status": response.status})

    page.on("response", _on_response)
    try:
        page.locator("#pwl-btn-scan-all").click()
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if len(captured) >= 2:
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
            f"scan-all fired {len(captured)}; {len(bad)} non-200: {bad}"
        )
    return {"scan_requests": len(captured), "all_status_200": True}


def _assert_last_scan_populated_for_both_items(page) -> dict:
    open_patent_watchlist_subtab(page)
    page.wait_for_timeout(1500)
    rows = page.locator("#pwl-list > div")
    found = {ADMIN_LABEL_A: False, ADMIN_LABEL_B: False}
    for i in range(rows.count()):
        text = rows.nth(i).inner_text()
        for label in found:
            if label not in text:
                continue
            never_variants = ("never", "henüz yok", "لم يحدث بعد")
            if any(nv.lower() in text.lower() for nv in never_variants):
                raise AssertionError(
                    f"row {label!r} still shows 'never' last_scan after "
                    f"scan-all + refresh. Excerpt: {text[:200]!r}"
                )
            found[label] = True
            break
    missing = [k for k, v in found.items() if not v]
    if missing:
        raise AssertionError(f"could not find rows for: {missing}")
    return {"both_rows_show_last_scan": True}


def test_patent_admin_actions_browser_smoke():
    cleanup_smoke_items(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            pre_create_ok = run_browser_step(
                "API pre-create 2 holder watches",
                REPORTER, page, monitor, CONFIG,
                lambda: _pre_create_two_items(),
            )
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

            if pre_create_ok:
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

            REPORTER.summary("Patent admin actions browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_patent_admin_actions_browser_smoke()

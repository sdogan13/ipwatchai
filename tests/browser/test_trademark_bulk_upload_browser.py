"""Browser smoke for trademark watchlist bulk CSV upload (happy path).

Existing coverage in test_member_feature_browser
.watchlist_bulk_upload_limit_gate exercises the FREE-tier
plan-cap response (403 → upgrade-offer aside renders). This
slice covers the orthogonal HAPPY PATH on a paid persona:
end-to-end 3-step flow lands rows in the watchlist.

#watchlist-upload-modal flow:
  Step 1: file pick (#upload-wl-file accepts .xlsx/.xls/.csv)
          + click #upload-wl-detect-btn → POST
          /api/v1/watchlist/upload/detect-columns
  Step 2: mapping selects (#upload-map-brand_name +
          #upload-map-application_no etc) + click
          #upload-wl-submit-btn → POST
          /api/v1/watchlist/upload/with-mapping
  Step 3: result body rendered into #upload-wl-result + list
          auto-reloads via refreshWatchlistAndStats

Slice flow:
  1. Cleanup leftover smoke items.
  2. Login + open watchlist subtab.
  3. Write a 2-row CSV to a temp file with realistic columns
     (Marka Adı, Başvuru No, Nice Sınıfları).
  4. Click the Bulk Upload toolbar button (onclick=
     openBulkUploadModal) + assert step-1 visible.
  5. Attach CSV + click Detect Columns → POST 200 + step-2
     mapping rendered + brand_name select auto-mapped.
  6. Click Submit → POST 200 with summary.added >= 1 +
     step-3 visible.
  7. Both new rows visible in #portfolio-grid after the auto
     reload.

Run directly:
    python tests/browser/test_trademark_bulk_upload_browser.py
"""
from __future__ import annotations

import sys
import tempfile
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
    reason="Browser E2E script; run directly with python tests/browser/test_trademark_bulk_upload_browser.py"
)


ROW1_NAME = slice_label("bulk", "row-1")
ROW2_NAME = slice_label("bulk", "row-2")
_CSV_TEMP_PATH: Path | None = None


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _write_temp_csv() -> dict:
    """Realistic Turkish trademark CSV — column headers match what
    the server's column detector recognizes (brand_name +
    application_no + nice_classes). Application numbers are
    UNIQUE-ish per row using the slice timestamp."""
    global _CSV_TEMP_PATH
    import time
    ts = int(time.time())
    csv_body = (
        "brand_name,application_no,nice_classes\n"
        f"{ROW1_NAME},{ts}/000001,9\n"
        f"{ROW2_NAME},{ts}/000002,42\n"
    )
    tmp = tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False, mode="w", encoding="utf-8", newline="",
    )
    tmp.write(csv_body)
    tmp.close()
    _CSV_TEMP_PATH = Path(tmp.name)
    return {"csv_bytes": len(csv_body), "rows": 2}


# ---------------------------------------------------------------------------
# Step 1: open + attach + detect
# ---------------------------------------------------------------------------

def _open_modal_and_assert_step_one(page) -> dict:
    """Trademark's Bulk Upload toolbar button has no stable id —
    select by onclick attribute."""
    btn = page.locator(
        'button[onclick="openBulkUploadModal()"]'
    ).first
    if btn.count() == 0:
        raise AssertionError(
            "Bulk Upload button (onclick=openBulkUploadModal()) "
            "not found in DOM"
        )
    btn.click()
    page.wait_for_selector(
        "#watchlist-upload-modal:not(.hidden)", timeout=5000,
    )
    page.wait_for_selector(
        "#upload-wl-step-1:not(.hidden)", timeout=2000,
    )
    return {"modal_open": True, "step_one_visible": True}


def _attach_csv_and_detect(page) -> dict:
    assert _CSV_TEMP_PATH is not None and _CSV_TEMP_PATH.exists()
    page.locator("#upload-wl-file").set_input_files(str(_CSV_TEMP_PATH))
    page.wait_for_selector(
        "#upload-wl-filename:not(.hidden)", timeout=3000,
    )
    filename_shown = page.locator("#upload-wl-filename").inner_text()
    if _CSV_TEMP_PATH.name not in filename_shown:
        raise AssertionError(
            f"filename UI doesn't show {_CSV_TEMP_PATH.name!r}; "
            f"got {filename_shown!r}"
        )
    with page.expect_response(
        lambda r: r.url.endswith(
            "/api/v1/watchlist/upload/detect-columns"
        ) and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#upload-wl-detect-btn").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /upload/detect-columns returned {response.status}: "
            f"{response.text()[:300]}"
        )
    payload = response.json()
    return {
        "status": response.status,
        "columns": payload.get("columns", []),
        "total_rows": payload.get("total_rows"),
    }


def _assert_step_two_rendered_with_brand_name_mapped(page) -> dict:
    page.wait_for_selector(
        "#upload-wl-step-2:not(.hidden)", timeout=5000,
    )
    # The trademark mapping selects use ids like #upload-map-brand_name
    # (per submitBulkUpload in app.js).
    sel = page.locator("#upload-map-brand_name")
    if sel.count() == 0:
        raise AssertionError(
            "no #upload-map-brand_name select in step-2"
        )
    val = sel.input_value()
    if val != "brand_name":
        raise AssertionError(
            f"brand_name mapping select didn't auto-pick "
            f"'brand_name' column from suggested_mapping; got {val!r}"
        )
    return {
        "step_two_visible": True,
        "brand_name_auto_mapped_to": val,
    }


# ---------------------------------------------------------------------------
# Step 3: submit + result
# ---------------------------------------------------------------------------

def _submit_and_assert_result(page) -> dict:
    with page.expect_response(
        lambda r: r.url.endswith(
            "/api/v1/watchlist/upload/with-mapping"
        ) and r.request.method == "POST",
        timeout=20000,
    ) as resp_info:
        page.locator("#upload-wl-submit-btn").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /upload/with-mapping returned {response.status}: "
            f"{response.text()[:300]}"
        )
    payload = response.json()
    summary = payload.get("summary") or {}
    added = summary.get("added", 0)
    if added < 1:
        raise AssertionError(
            f"bulk upload reported added={added}; expected >= 1. "
            f"Full payload: {payload!r}"
        )
    page.wait_for_selector(
        "#upload-wl-result:not(.hidden)", timeout=5000,
    )
    result_body = page.locator("#upload-wl-result").inner_text()
    if not result_body.strip():
        raise AssertionError(
            "#upload-wl-result is empty after upload completed"
        )
    return {
        "status": response.status,
        "added": added,
        "skipped": summary.get("skipped"),
        "errors": summary.get("errors"),
        "total_rows": summary.get("total_rows"),
    }


def _close_modal_and_verify_rows(page) -> dict:
    """Close modal via the in-result Close button (no stable id;
    text-content match), then verify both rows rendered in
    #portfolio-grid via the JS cache (the click handler on submit
    triggers refreshWatchlistAndStats which calls loadPortfolio)."""
    close_btn = page.locator(
        '#upload-wl-result button:has-text("Close"), '
        '#upload-wl-result button:has-text("Kapat"), '
        '#upload-wl-result button:has-text("إغلاق")'
    ).first
    if close_btn.count() > 0:
        close_btn.click()
        page.wait_for_function(
            """() => {
                const m = document.getElementById('watchlist-upload-modal');
                return m && m.classList.contains('hidden');
            }""",
            timeout=3000,
        )
    # The submit handler shows the watchlist tab automatically. Wait
    # for both names in the JS cache.
    page.wait_for_function(
        """(names) => {
            const arr = window._watchlistItemsCache || [];
            return names.every(n => arr.some(it => it && it.brand_name === n));
        }""",
        arg=[ROW1_NAME, ROW2_NAME],
        timeout=15000,
    )
    return {"both_rows_in_cache": True}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_trademark_bulk_upload_browser_smoke():
    cleanup_smoke_items(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "Write 2-row CSV to temp file",
                REPORTER, page, monitor, CONFIG,
                lambda: _write_temp_csv(),
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
                "Open bulk upload modal + step-1 visible",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_modal_and_assert_step_one(page),
            )
            run_browser_step(
                "Attach CSV + detect-columns 200",
                REPORTER, page, monitor, CONFIG,
                lambda: _attach_csv_and_detect(page),
            )
            run_browser_step(
                "Step-2 rendered + brand_name auto-mapped",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_step_two_rendered_with_brand_name_mapped(page),
            )
            run_browser_step(
                "Submit upload + /with-mapping 200 + step-3 result body",
                REPORTER, page, monitor, CONFIG,
                lambda: _submit_and_assert_result(page),
            )
            run_browser_step(
                "Close modal + both new rows in cache",
                REPORTER, page, monitor, CONFIG,
                lambda: _close_modal_and_verify_rows(page),
            )

            REPORTER.summary("Trademark bulk upload browser smoke")
        finally:
            context.close()
            browser.close()
            if _CSV_TEMP_PATH and _CSV_TEMP_PATH.exists():
                try:
                    _CSV_TEMP_PATH.unlink()
                except OSError:
                    pass
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_trademark_bulk_upload_browser_smoke()

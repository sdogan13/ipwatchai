"""Browser smoke for the design watchlist bulk CSV upload modal.

Slice 9 of the comprehensive design UI browser coverage. This
is the LARGEST design-unique surface — a 3-step modal flow with
no patent/cografi equivalent.

Step 1 (file pick):
  - User clicks ``#dwl-btn-bulk-upload`` → modal opens to step 1.
  - User picks a CSV via ``#dwl-upload-file``.
  - Filename appears in ``#dwl-upload-filename``.
  - User clicks ``#dwl-upload-detect-btn`` → POST
    /api/v1/design-watchlist/upload/detect-columns.

Step 2 (column mapping):
  - Server returns ``{columns, suggested_mapping, total_rows}``.
  - JS renders one ``<select data-dwl-map-field="<field>">`` per
    target field (product_name, locarno_classes, description,
    customer_application_no, customer_registration_no,
    similarity_threshold).
  - Suggested mapping auto-selects matching columns.
  - User clicks ``#dwl-upload-submit-btn`` → POST
    /upload/with-mapping (multipart: file + column_mapping JSON).

Step 3 (result):
  - Server returns ``{added, skipped, errors, total}``.
  - JS renders summary into ``#dwl-upload-result-body`` and
    auto-reloads the list so the new rows appear immediately.

Slice flow:
  1. Cleanup leftover smoke items.
  2. Login + open watchlist subtab.
  3. Write a 2-row CSV to a temp file.
  4. Click Bulk Upload + assert modal step-1 visible.
  5. Attach the CSV + click Detect Columns + assert
     /detect-columns 200 + step-2 visible + ``product_name``
     mapping select auto-populated.
  6. Click Upload + assert /upload/with-mapping 200 with
     ``added >= 1`` + step-3 result body shows the summary.
  7. Close modal + verify both new rows now render in the list.

Run directly:
    python tests/browser/test_design_bulk_upload_browser.py
"""
from __future__ import annotations

import sys
import tempfile
import time
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
    reason="Browser E2E script; run directly with python tests/browser/test_design_bulk_upload_browser.py"
)


ROW1_NAME = slice_label("bulk", "row-1")
ROW2_NAME = slice_label("bulk", "row-2")
_CSV_TEMP_PATH: Path | None = None


# ---------------------------------------------------------------------------
# Setup — write a 2-row CSV with column headers that match the
# server's expected fields so the suggested_mapping auto-fills both.
# ---------------------------------------------------------------------------

def _write_temp_csv() -> dict:
    global _CSV_TEMP_PATH
    # Use UTF-8; the upload endpoint expects UTF-8 per the modal UX.
    # Headers chosen to match the server's known field names so the
    # column detector returns a high-confidence suggested mapping.
    csv_body = (
        "product_name,locarno_classes,description\n"
        f"{ROW1_NAME},06-01,smoke row 1\n"
        f"{ROW2_NAME},06-01,smoke row 2\n"
    )
    tmp = tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False, mode="w", encoding="utf-8", newline="",
    )
    tmp.write(csv_body)
    tmp.close()
    _CSV_TEMP_PATH = Path(tmp.name)
    return {"csv_bytes": len(csv_body), "rows": 2}


# ---------------------------------------------------------------------------
# Open the modal + step-1 ready
# ---------------------------------------------------------------------------

def _open_bulk_upload_modal(page) -> dict:
    page.locator("#dwl-btn-bulk-upload").click()
    page.wait_for_selector(
        "#design-watchlist-upload-modal:not(.hidden)", timeout=5000,
    )
    page.wait_for_selector(
        "#dwl-upload-step-1:not(.hidden)", timeout=2000,
    )
    return {"modal_open": True, "step_one_visible": True}


# ---------------------------------------------------------------------------
# Attach CSV + detect columns
# ---------------------------------------------------------------------------

def _attach_csv_and_detect(page) -> dict:
    assert _CSV_TEMP_PATH is not None and _CSV_TEMP_PATH.exists()
    page.locator("#dwl-upload-file").set_input_files(str(_CSV_TEMP_PATH))
    page.wait_for_selector(
        "#dwl-upload-filename:not(.hidden)", timeout=3000,
    )
    filename_shown = page.locator("#dwl-upload-filename").inner_text()
    if _CSV_TEMP_PATH.name not in filename_shown:
        raise AssertionError(
            f"filename UI doesn't show {_CSV_TEMP_PATH.name!r}; "
            f"got {filename_shown!r}"
        )
    with page.expect_response(
        lambda r: r.url.endswith(
            "/api/v1/design-watchlist/upload/detect-columns"
        ) and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#dwl-upload-detect-btn").click()
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
        "suggested_mapping": payload.get("suggested_mapping", {}),
    }


def _assert_step_two_rendered_and_product_name_mapped(page) -> dict:
    page.wait_for_selector(
        "#dwl-upload-step-2:not(.hidden)", timeout=5000,
    )
    # Mapping selects rendered by JS; expect at least the product_name
    # mapping select to exist and to have been auto-set to 'product_name'.
    pn_select = page.locator('select[data-dwl-map-field="product_name"]')
    if pn_select.count() == 0:
        raise AssertionError(
            "no select[data-dwl-map-field='product_name'] in step-2"
        )
    pn_value = pn_select.input_value()
    if pn_value != "product_name":
        raise AssertionError(
            f"product_name mapping select didn't auto-pick "
            f"'product_name' column; got {pn_value!r}"
        )
    row_count_text = page.locator("#dwl-upload-row-count").inner_text()
    return {
        "step_two_visible": True,
        "product_name_auto_mapped_to": pn_value,
        "row_count_text": row_count_text,
    }


# ---------------------------------------------------------------------------
# Submit + step-3 result + verify rows
# ---------------------------------------------------------------------------

def _submit_upload_and_assert_result(page) -> dict:
    assert _CSV_TEMP_PATH is not None
    with page.expect_response(
        lambda r: r.url.endswith(
            "/api/v1/design-watchlist/upload/with-mapping"
        ) and r.request.method == "POST",
        timeout=20000,
    ) as resp_info:
        page.locator("#dwl-upload-submit-btn").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /upload/with-mapping returned {response.status}: "
            f"{response.text()[:300]}"
        )
    payload = response.json()
    added = payload.get("added", 0)
    if added < 1:
        raise AssertionError(
            f"bulk upload reported added={added}; expected at least 1. "
            f"Full payload: {payload!r}"
        )
    page.wait_for_selector(
        "#dwl-upload-result:not(.hidden)", timeout=5000,
    )
    result_body = page.locator("#dwl-upload-result-body").inner_text()
    if not result_body.strip():
        raise AssertionError(
            "#dwl-upload-result-body is empty after upload completed"
        )
    return {
        "status": response.status,
        "added": added,
        "total": payload.get("total"),
        "skipped": payload.get("skipped"),
        "errors": payload.get("errors"),
    }


def _close_modal_and_verify_rows(page) -> dict:
    # The result panel has a Close button at the bottom; use it.
    close_btn = page.locator(
        '#dwl-upload-result button:has-text("Close"), '
        '#dwl-upload-result button:has-text("Kapat"), '
        '#dwl-upload-result button:has-text("إغلاق")'
    ).first
    if close_btn.count() > 0:
        close_btn.click()
        page.wait_for_function(
            """() => {
                const m = document.getElementById('design-watchlist-upload-modal');
                return m && m.classList.contains('hidden');
            }""",
            timeout=3000,
        )
    # Both uploaded rows should now be visible in the rendered list.
    # The list auto-reloaded inside submitDesignBulkUpload after success.
    page.wait_for_function(
        """(names) => {
            const arts = document.querySelectorAll(
                '#design-watchlist-list > article'
            );
            const texts = Array.from(arts).map(a => a.innerText);
            return names.every(n => texts.some(t => t.includes(n)));
        }""",
        arg=[ROW1_NAME, ROW2_NAME],
        timeout=10000,
    )
    return {"both_rows_visible": True}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_design_bulk_upload_browser_smoke():
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
                "Open design watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_design_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Click Bulk Upload + step-1 visible",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_bulk_upload_modal(page),
            )
            run_browser_step(
                "Attach CSV + detect-columns 200",
                REPORTER, page, monitor, CONFIG,
                lambda: _attach_csv_and_detect(page),
            )
            run_browser_step(
                "Step-2 rendered + product_name auto-mapped",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_step_two_rendered_and_product_name_mapped(page),
            )
            run_browser_step(
                "Submit upload + /with-mapping 200 + step-3 result body",
                REPORTER, page, monitor, CONFIG,
                lambda: _submit_upload_and_assert_result(page),
            )
            run_browser_step(
                "Close modal + both new rows visible in list",
                REPORTER, page, monitor, CONFIG,
                lambda: _close_modal_and_verify_rows(page),
            )

            REPORTER.summary("Design bulk-upload browser smoke")
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
    test_design_bulk_upload_browser_smoke()

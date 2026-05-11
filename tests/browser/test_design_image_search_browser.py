"""Browser smoke for the design image-upload search.

Design is the most image-first registry — its result cards
typically carry a primary thumbnail and the search service is
explicitly designed for visual similarity (DINOv2 + CLIP + HSV
histogram triple-signal scoring per the design_search service).

This slice exercises the drag-drop / file upload path:
  1. API search to find a design record carrying an image URL.
  2. Download that record's image to a temp file.
  3. Attach the file to #design-search-image (hidden file input
     that the drag-drop zone delegates to).
  4. Submit search.
  5. Assert POST 200 + non-empty results + source record self-
     matches.

Run directly:
    python tests/browser/test_design_image_search_browser.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.design import (
    _api_login,
    design_config_for_persona,
    open_design_search_subtab,
    transient_401_budget,
    wait_for_search_rate_limit_to_clear,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = design_config_for_persona("professional")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_design_image_search_browser.py"
)


_FIGURE_TEMP_PATH: Path | None = None
_SOURCE_RECORD_NAME: str | None = None
_SOURCE_RECORD_ID: str | None = None


def _find_record_with_image_and_download() -> dict:
    global _FIGURE_TEMP_PATH, _SOURCE_RECORD_NAME, _SOURCE_RECORD_ID

    token = _api_login(CONFIG)
    if not token:
        raise AssertionError("API login failed; can't bootstrap")

    boundary = "----imgsmoke"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="query"\r\n\r\n'
        f"Lamba\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="limit"\r\n\r\n'
        f"10\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{CONFIG.base_url}/api/v1/design-search/quick",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        data=body,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    results = payload.get("results", [])
    if not results:
        raise AssertionError(
            "search returned 0 results for 'Lamba' — pick a different "
            "known-stable query"
        )

    # Designs commonly carry an image_url. Find the first result that
    # has one.
    target = None
    for r in results:
        if r.get("image_url") or r.get("primary_image_url"):
            target = r
            break
    if target is None:
        raise AssertionError(
            "no design results carry image_url; the design search "
            "service stopped returning thumbnail URLs"
        )

    image_url = target.get("image_url") or target.get("primary_image_url")
    if image_url.startswith("/"):
        image_url = CONFIG.base_url + image_url
    _SOURCE_RECORD_NAME = target.get("title") or target.get("product_name") or ""
    _SOURCE_RECORD_ID = target.get("id", "")

    req = urllib.request.Request(
        image_url,
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        img_bytes = resp.read()
    if len(img_bytes) < 512:
        raise AssertionError(
            f"image download returned only {len(img_bytes)} bytes"
        )
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.write(img_bytes)
    tmp.close()
    _FIGURE_TEMP_PATH = Path(tmp.name)
    return {
        "record_name_first_60": (_SOURCE_RECORD_NAME or "")[:60],
        "record_id": _SOURCE_RECORD_ID,
        "image_bytes": len(img_bytes),
    }


def _upload_image_and_submit(page) -> dict:
    assert _FIGURE_TEMP_PATH is not None and _FIGURE_TEMP_PATH.exists()
    page.locator("#design-search-image").set_input_files(
        str(_FIGURE_TEMP_PATH)
    )
    files_count = page.evaluate(
        """() => {
            const el = document.getElementById('design-search-image');
            return (el && el.files) ? el.files.length : 0;
        }"""
    )
    if files_count != 1:
        raise AssertionError(
            f"set_input_files didn't attach: files.length={files_count}"
        )
    # Designs accept image-only or text+image. Use hybrid for
    # robustness (image-only on cografi returned 0 results in the
    # earlier work; design may behave differently but hybrid is
    # the realistic user flow).
    page.locator("#design-search-input").fill("Lamba")
    page.wait_for_timeout(200)

    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/design-search/quick")
                  and r.request.method == "POST",
        timeout=60000,
    ) as resp_info:
        page.locator("#design-search-submit").click(force=True)
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST returned {response.status}: {response.text()[:300]}"
        )
    payload = response.json()
    result_count = len(payload.get("results", []))
    page.wait_for_selector(
        "#design-search-grid > article, #design-search-empty:not(.hidden)",
        timeout=15000,
    )
    return {
        "response_status": response.status,
        "server_result_count": result_count,
        "files_attached": files_count,
    }


def _verify_source_record_in_results(page) -> dict:
    grid = page.locator("#design-search-grid > article")
    count = grid.count()
    if count == 0:
        raise AssertionError(
            "image search returned 0 result cards"
        )
    found = False
    for i in range(count):
        card = grid.nth(i)
        text = card.inner_text()
        if _SOURCE_RECORD_NAME and _SOURCE_RECORD_NAME in text:
            found = True
            break
        if _SOURCE_RECORD_ID:
            data_id = card.get_attribute("data-design-id") or ""
            if _SOURCE_RECORD_ID in data_id:
                found = True
                break
    if not found:
        raise AssertionError(
            f"source record (name={(_SOURCE_RECORD_NAME or '')[:60]!r}, "
            f"id={_SOURCE_RECORD_ID}) not in {count} image-search results"
        )
    return {"result_count": count}


def test_design_image_search_browser_smoke():
    wait_for_search_rate_limit_to_clear(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "Find design with image + download",
                REPORTER, page, monitor, CONFIG,
                lambda: _find_record_with_image_and_download(),
            )
            run_browser_step(
                "Login as paid persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
                allow_console_errors=("status of 429",),
            )
            run_browser_step(
                "Open design search subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_design_search_subtab(page),
            )
            run_browser_step(
                "Upload image + submit hybrid text+image search",
                REPORTER, page, monitor, CONFIG,
                lambda: _upload_image_and_submit(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Source record appears in results",
                REPORTER, page, monitor, CONFIG,
                lambda: _verify_source_record_in_results(page),
            )

            REPORTER.summary("Design image-search browser smoke")
        finally:
            context.close()
            browser.close()
            if _FIGURE_TEMP_PATH and _FIGURE_TEMP_PATH.exists():
                try:
                    _FIGURE_TEMP_PATH.unlink()
                except OSError:
                    pass


if __name__ == "__main__":
    test_design_image_search_browser_smoke()

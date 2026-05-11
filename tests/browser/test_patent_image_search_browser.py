"""Browser smoke for the patent image-upload search (DINOv2 figure
similarity, hybrid text+image mode).

The drag-drop zone in ``_search_patent_subview.html`` accepts a
PNG/JPG/WebP/TIFF figure and encodes it via DINOv2 ViT-L/14
server-side (same path cografi uses). Today's existing dashboard
slice uses only text queries.

Per the cografi image-search smoke discovery: image-only queries
on cografi return 0 results. Patent may behave differently (its
service may have a non-trivial image-only path), but the
realistic user flow is hybrid text+image anyway. This slice runs
hybrid: a text query + an attached figure.

Flow:
  1. API search to find a patent record carrying ``image_url``.
  2. Download that record's figure JPEG to a temp file.
  3. Attach the file to the hidden #patent-search-image input.
  4. Type a relevant text query.
  5. Submit + assert POST /patent-search/quick returns 200 + the
     server returns >0 results.
  6. Verify the source record self-matches in the rendered grid.

Run directly:
    python tests/browser/test_patent_image_search_browser.py
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
from tests.browser.helpers.patent import (
    _api_login,
    open_patent_search_subtab,
    patent_config_for_persona,
    transient_401_budget,
    wait_for_search_rate_limit_to_clear,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = patent_config_for_persona("professional")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_patent_image_search_browser.py"
)

_FIGURE_TEMP_PATH: Path | None = None
_SOURCE_RECORD_TITLE: str | None = None
_SOURCE_RECORD_ID: str | None = None


# ---------------------------------------------------------------------------
# Setup: discover a record with a figure + download it
# ---------------------------------------------------------------------------

def _find_record_with_figure_and_download() -> dict:
    global _FIGURE_TEMP_PATH, _SOURCE_RECORD_TITLE, _SOURCE_RECORD_ID

    token = _api_login(CONFIG)
    if not token:
        raise AssertionError("API login failed; can't bootstrap")

    boundary = "----imgsmoke"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="query"\r\n\r\n'
        f"kompozisyon\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="limit"\r\n\r\n'
        f"10\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{CONFIG.base_url}/api/v1/patent-search/quick",
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
            "search returned 0 results for 'kompozisyon' — pick a "
            "different known-stable query"
        )

    target = next((r for r in results if r.get("image_url")), None)
    if target is None:
        raise AssertionError(
            "no results carry image_url; the corpus may have lost "
            "its primary figures or the search service stopped "
            "returning them"
        )

    image_url = target["image_url"]
    if image_url.startswith("/"):
        image_url = CONFIG.base_url + image_url
    _SOURCE_RECORD_TITLE = target.get("title", "")
    _SOURCE_RECORD_ID = target.get("id", "")

    req = urllib.request.Request(
        image_url,
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        img_bytes = resp.read()
    if len(img_bytes) < 1024:
        raise AssertionError(
            f"figure download returned only {len(img_bytes)} bytes"
        )
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.write(img_bytes)
    tmp.close()
    _FIGURE_TEMP_PATH = Path(tmp.name)
    return {
        "record_title_first_60": _SOURCE_RECORD_TITLE[:60] if _SOURCE_RECORD_TITLE else "",
        "record_id": _SOURCE_RECORD_ID,
        "image_bytes": len(img_bytes),
    }


# ---------------------------------------------------------------------------
# Browser steps
# ---------------------------------------------------------------------------

def _upload_figure_and_submit_hybrid(page) -> dict:
    """Attach the downloaded figure + add a text query (hybrid path)."""
    assert _FIGURE_TEMP_PATH is not None and _FIGURE_TEMP_PATH.exists(), (
        f"figure temp file missing: {_FIGURE_TEMP_PATH!r}"
    )
    page.locator("#patent-search-image").set_input_files(
        str(_FIGURE_TEMP_PATH)
    )
    files_count = page.evaluate(
        """() => {
            const el = document.getElementById('patent-search-image');
            return (el && el.files) ? el.files.length : 0;
        }"""
    )
    if files_count != 1:
        raise AssertionError(
            f"set_input_files didn't attach: input.files.length={files_count}"
        )
    page.locator("#patent-search-input").fill("kompozisyon")
    page.wait_for_timeout(200)

    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/patent-search/quick")
                  and r.request.method == "POST",
        timeout=60000,  # image search is heavier
    ) as resp_info:
        page.locator("#patent-search-submit").click(force=True)
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /patent-search/quick (image) returned "
            f"{response.status}: {response.text()[:300]}"
        )
    payload = response.json()
    result_count = len(payload.get("results", []))
    page.wait_for_selector(
        "#patent-search-grid > div, #patent-search-empty:not(.hidden)",
        timeout=15000,
    )
    return {
        "response_status": response.status,
        "server_result_count": result_count,
        "files_attached": files_count,
    }


def _verify_source_record_in_results(page) -> dict:
    grid = page.locator("#patent-search-grid > div")
    count = grid.count()
    if count == 0:
        raise AssertionError(
            "image search returned 0 result cards; the source record "
            "should self-match"
        )
    found = False
    matched_pos = -1
    for i in range(count):
        card = grid.nth(i)
        text = card.inner_text()
        if _SOURCE_RECORD_TITLE and _SOURCE_RECORD_TITLE in text:
            found = True
            matched_pos = i
            break
        if _SOURCE_RECORD_ID:
            attr = card.get_attribute("data-pd-open") or ""
            if _SOURCE_RECORD_ID in attr:
                found = True
                matched_pos = i
                break
    if not found:
        raise AssertionError(
            f"source record (title={(_SOURCE_RECORD_TITLE or '')[:60]!r}, "
            f"id={_SOURCE_RECORD_ID}) did not appear in the {count} "
            f"image-search results"
        )
    return {
        "result_count": count,
        "matched_position": matched_pos,
    }


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_patent_image_search_browser_smoke():
    wait_for_search_rate_limit_to_clear(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "Find record with figure + download JPEG",
                REPORTER, page, monitor, CONFIG,
                lambda: _find_record_with_figure_and_download(),
            )
            run_browser_step(
                "Login as paid persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
                allow_console_errors=("status of 429",),
            )
            run_browser_step(
                "Open patent search subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_patent_search_subtab(page),
            )
            run_browser_step(
                "Upload figure + submit hybrid text+image search",
                REPORTER, page, monitor, CONFIG,
                lambda: _upload_figure_and_submit_hybrid(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Source record appears in image-search results",
                REPORTER, page, monitor, CONFIG,
                lambda: _verify_source_record_in_results(page),
            )

            REPORTER.summary("Patent image-search browser smoke")
        finally:
            context.close()
            browser.close()
            if _FIGURE_TEMP_PATH and _FIGURE_TEMP_PATH.exists():
                try:
                    _FIGURE_TEMP_PATH.unlink()
                except OSError:
                    pass


if __name__ == "__main__":
    test_patent_image_search_browser_smoke()

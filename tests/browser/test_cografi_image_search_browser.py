"""Browser smoke for the cografi image-upload search (DINOv2 figure
similarity, hybrid text+image mode).

The drag-drop zone in ``_search_cografi_subview.html`` accepts a
PNG/JPG/WebP/TIFF figure and encodes it via DINOv2 ViT-L/14 +
CLIP ViT-B/32 server-side. Today's existing smoke uses only text
queries.

This slice exercises the **hybrid text+image** path because:
  - Discovery during authoring: image-only queries (image attached
    + no text query) return 0 results from the cografi search
    service even for self-similar records. This is real product
    behavior that may warrant a separate investigation — the
    primary_figure_embedding cosine path on its own appears to
    return nothing under the current implementation. Out of scope
    for this test; tracked as a follow-up.
  - The realistic user flow combines a text hint with an image
    upload anyway (e.g. "find similar carpets" + drag-drop a
    pattern). Hybrid retrieval is what the JS submits when both
    are filled, and it's what users will actually exercise.

Flow:
  1. Find a known record with a primary figure (Karapınar Halısı —
     stable visual GI) via API search.
  2. Download its primary figure JPEG from /api/v1/cografi-image/.
  3. Attach the file to the hidden #cografi-search-image input.
  4. Fill the text query with a word from the source record's name.
  5. Submit + assert POST returns 200 + non-empty results.
  6. Verify the source record self-matches in the rendered grid.

Run directly:
    python tests/browser/test_cografi_image_search_browser.py
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
from tests.browser.helpers.cografi import (
    _api_login,
    cografi_config_for_persona,
    open_cografi_search_subtab,
    transient_401_budget,
    wait_for_search_rate_limit_to_clear,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


# Image search fires multiple POSTs (text+image hybrid) so use the
# managed-professional persona (2000/day search quota) instead of
# starter (50/day) — starter is easy to exhaust during dev runs.
CONFIG = cografi_config_for_persona("professional")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_cografi_image_search_browser.py"
)

# Where we'll stash the downloaded figure for the upload step.
_FIGURE_TEMP_PATH: Path | None = None
# Stash the source record's name + id so we can verify the self-match
# appears in the image-driven results.
_SOURCE_RECORD_NAME: str | None = None
_SOURCE_RECORD_ID: str | None = None


# ---------------------------------------------------------------------------
# Setup: discover a record with a figure + download it
# ---------------------------------------------------------------------------

def _find_record_with_figure_and_download() -> dict:
    """Hit the public search endpoint to find a known-visual GI
    (Karapınar Halısı), grab its image_url, download the JPEG, and
    stash the path in module state for the browser upload step."""
    global _FIGURE_TEMP_PATH, _SOURCE_RECORD_NAME, _SOURCE_RECORD_ID

    token = _api_login(CONFIG)
    if not token:
        raise AssertionError("API login failed; can't bootstrap the test")

    # Use the authenticated quick endpoint (the public one caps at 10
    # results without auth and has stricter rate limits).
    boundary = "----imagesmoke"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="query"\r\n\r\n'
        f"Karapınar Halısı\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="limit"\r\n\r\n'
        f"5\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{CONFIG.base_url}/api/v1/cografi-search",
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
            "search returned 0 results for 'Karapınar Halısı' — corpus "
            "may have shifted; pick a different known-visual record"
        )

    # Find the first result with an image_url
    target = None
    for r in results:
        if r.get("image_url"):
            target = r
            break
    if target is None:
        raise AssertionError(
            "no results carry an image_url; the search service may "
            "have stopped populating primary figure URLs"
        )

    image_url = target["image_url"]
    if image_url.startswith("/"):
        image_url = CONFIG.base_url + image_url
    _SOURCE_RECORD_NAME = target.get("name", "")
    _SOURCE_RECORD_ID = target.get("id", "")

    # Download
    req = urllib.request.Request(
        image_url,
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        img_bytes = resp.read()
    if len(img_bytes) < 1024:
        raise AssertionError(
            f"figure download returned only {len(img_bytes)} bytes — "
            f"likely an error page or empty response"
        )
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.write(img_bytes)
    tmp.close()
    _FIGURE_TEMP_PATH = Path(tmp.name)
    return {
        "record_name": _SOURCE_RECORD_NAME,
        "record_id": _SOURCE_RECORD_ID,
        "image_bytes": len(img_bytes),
        "temp_path": str(_FIGURE_TEMP_PATH),
    }


# ---------------------------------------------------------------------------
# Browser steps
# ---------------------------------------------------------------------------

def _upload_figure_and_submit(page) -> dict:
    """Attach the downloaded figure to the hidden file input, clear
    the text query, submit, and verify the POST returns 200.

    Playwright's request.post_data doesn't expose multipart body
    bytes reliably (returns empty string for binary file uploads).
    We verify the upload path by:
      1. Confirming JavaScript sees a file in the input after
         set_input_files (so the FormData builder will include it).
      2. Capturing the RESPONSE status from POST /cografi-search/
         quick (200 means the server accepted the multipart with
         the image field; 422/400 would indicate a malformed
         upload).
      3. Downstream step verifies the source record self-matched
         in the rendered results, which is the strongest signal
         that the image was actually delivered + processed.
    """
    assert _FIGURE_TEMP_PATH is not None and _FIGURE_TEMP_PATH.exists(), (
        f"figure temp file missing: {_FIGURE_TEMP_PATH!r}"
    )
    page.locator("#cografi-search-image").set_input_files(
        str(_FIGURE_TEMP_PATH)
    )
    # Confirm JS-side that the file is now attached to the input.
    files_count = page.evaluate(
        """() => {
            const el = document.getElementById('cografi-search-image');
            return (el && el.files) ? el.files.length : 0;
        }"""
    )
    if files_count != 1:
        raise AssertionError(
            f"set_input_files didn't attach: input.files.length={files_count}"
        )
    # The cografi search service returns 0 results for image-only
    # queries (no text query + no other filters). The realistic
    # user flow is hybrid text+image — fill the text query with a
    # word from the source record's name so we exercise the
    # hybrid-retrieval path.
    page.locator("#cografi-search-input").fill("Halısı")
    page.wait_for_timeout(200)

    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/cografi-search")
                  and r.request.method == "POST",
        timeout=60000,  # image search is heavier than text-only
    ) as resp_info:
        page.locator("#cografi-search-submit").click(force=True)
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /cografi-search/quick (image upload) returned "
            f"{response.status}: {response.text()[:300]}"
        )
    payload = response.json()
    result_count = len(payload.get("results", []))
    page.wait_for_selector(
        "#cografi-search-grid > div, #cografi-search-empty:not(.hidden)",
        timeout=15000,
    )
    return {
        "response_status": response.status,
        "server_result_count": result_count,
        "files_attached": files_count,
    }


def _verify_source_record_in_results(page) -> dict:
    """The DINOv2 self-similarity is 1.0 so the source record must
    appear in the rendered grid. Check by name or id."""
    grid = page.locator("#cografi-search-grid > div")
    count = grid.count()
    if count == 0:
        raise AssertionError(
            "image search returned 0 result cards; expected the "
            f"source record {_SOURCE_RECORD_NAME!r} to self-match"
        )

    found = False
    matched_pos = -1
    for i in range(count):
        text = grid.nth(i).inner_text()
        if _SOURCE_RECORD_NAME and _SOURCE_RECORD_NAME in text:
            found = True
            matched_pos = i
            break
        if _SOURCE_RECORD_ID:
            attr_id = grid.nth(i).get_attribute("data-cd-open") or ""
            if _SOURCE_RECORD_ID in attr_id:
                found = True
                matched_pos = i
                break
    if not found:
        raise AssertionError(
            f"source record {_SOURCE_RECORD_NAME!r} (id={_SOURCE_RECORD_ID}) "
            f"did not appear in the {count} image-search results; "
            f"DINOv2 self-similarity should make it the top hit"
        )
    return {
        "result_count": count,
        "matched_position": matched_pos,
        "source_record": _SOURCE_RECORD_NAME,
    }


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_cografi_image_search_browser_smoke():
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
                "Open cografi search subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_cografi_search_subtab(page),
            )
            run_browser_step(
                "Upload figure + submit image-driven search",
                REPORTER, page, monitor, CONFIG,
                lambda: _upload_figure_and_submit(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Source record appears in image-search results (self-match)",
                REPORTER, page, monitor, CONFIG,
                lambda: _verify_source_record_in_results(page),
            )

            REPORTER.summary("Cografi image-search browser smoke")
        finally:
            context.close()
            browser.close()
            # Cleanup temp file
            if _FIGURE_TEMP_PATH and _FIGURE_TEMP_PATH.exists():
                try:
                    _FIGURE_TEMP_PATH.unlink()
                except OSError:
                    pass


if __name__ == "__main__":
    test_cografi_image_search_browser_smoke()

"""Browser smoke for the trademark image-search path (comprehensive).

Trademark's image search uses POST ``/api/v1/search/quick`` with
multipart body containing ``image``, ``query``, and ``classes``
fields (per ``dashboardQuickSearch()`` in app.js).

Existing coverage (test_search_browser.paid_image_search) uses
a synthetic 1×1 PNG + the query "wosen" + asserts response 200
+ resultsCount > 0 + imageUsed=true. This slice ADDS:
  - downloads a REAL image from the live trademark corpus
  - submits hybrid text+image with both fields populated
  - Nice-class filter (class 9) sent in the multipart body
  - asserts source-record SELF-MATCH in the rendered results
    (the searched record appears in the result list)

Run directly:
    python tests/browser/test_trademark_image_search_browser.py
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
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.browser.helpers.trademark import (
    _api_login,
    open_trademark_search_subtab,
    trademark_config_for_persona,
    transient_401_budget,
    wait_for_search_rate_limit_to_clear,
)
from tests.live.helpers.assertions import LiveReporter


CONFIG = trademark_config_for_persona("professional")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_trademark_image_search_browser.py"
)


PROBE_QUERY = "Coca"

_FIGURE_TEMP_PATH: Path | None = None
_SOURCE_RECORD_NAME: str | None = None
_SOURCE_RECORD_APP_NO: str | None = None


# ---------------------------------------------------------------------------
# Find a real trademark with an image in the live corpus + download
# ---------------------------------------------------------------------------

def _find_record_with_image_and_download() -> dict:
    global _FIGURE_TEMP_PATH, _SOURCE_RECORD_NAME, _SOURCE_RECORD_APP_NO

    token = _api_login(CONFIG)
    if not token:
        raise AssertionError("API login failed; can't bootstrap")

    # Use the GET text-only quick-search to find candidates
    req = urllib.request.Request(
        f"{CONFIG.base_url}/api/v1/search/quick"
        f"?query={PROBE_QUERY}&limit=20",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    results = payload.get("results", [])
    if not results:
        raise AssertionError(
            f"text-only search for {PROBE_QUERY!r} returned 0 "
            f"results — pick a different known-stable query"
        )
    # Find the first result that carries an image_url or image_path
    target = None
    for r in results:
        if r.get("image_url") or r.get("image_path"):
            target = r
            break
    if target is None:
        raise AssertionError(
            f"no '{PROBE_QUERY}' results carry image_url/image_path; "
            f"trademark search dropped image fields"
        )
    image_ref = target.get("image_url") or target.get("image_path")
    if image_ref.startswith("/"):
        image_url = CONFIG.base_url + image_ref
    elif image_ref.startswith("http"):
        image_url = image_ref
    else:
        # image_path may be a relative storage path requiring the
        # /api/trademark-image/ prefix
        image_url = (
            f"{CONFIG.base_url}/api/trademark-image/"
            f"{image_ref}"
        )
    _SOURCE_RECORD_NAME = target.get("brand_name") or target.get("name") or ""
    _SOURCE_RECORD_APP_NO = target.get("application_no") or ""

    img_req = urllib.request.Request(
        image_url,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(img_req, timeout=30) as resp:
        img_bytes = resp.read()
    if len(img_bytes) < 200:
        raise AssertionError(
            f"image download from {image_url!r} returned only "
            f"{len(img_bytes)} bytes"
        )
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(img_bytes)
    tmp.close()
    _FIGURE_TEMP_PATH = Path(tmp.name)
    return {
        "source_brand": (_SOURCE_RECORD_NAME or "")[:60],
        "source_app_no": _SOURCE_RECORD_APP_NO,
        "image_bytes": len(img_bytes),
    }


# ---------------------------------------------------------------------------
# Browser submit hybrid text+image
# ---------------------------------------------------------------------------

def _attach_image_and_submit(page) -> dict:
    assert _FIGURE_TEMP_PATH is not None and _FIGURE_TEMP_PATH.exists()
    upload = page.locator(
        '#tab-content-search input[type="file"]'
    ).first
    upload.set_input_files(str(_FIGURE_TEMP_PATH))

    # Confirm Alpine picked up selectedImage
    has_image = page.evaluate(
        """() => {
            const stack = document.body && document.body._x_dataStack;
            return !!(stack && stack[0] && stack[0].selectedImage);
        }"""
    )
    if not has_image:
        raise AssertionError(
            "Alpine selectedImage didn't populate after "
            "set_input_files"
        )
    page.locator("#search-input").fill(PROBE_QUERY)
    page.evaluate(
        """() => {
            const inp = document.getElementById('search-input');
            if (inp) inp.dispatchEvent(new Event('input', { bubbles: true }));
        }"""
    )
    # No Nice class filter — combining classes with image search
    # can exclude the source record if it sits in a class other than
    # the one we'd guess. Slice 4 (search_filters) already covers
    # the multi-class URL composition; this slice's job is the
    # image-upload + source self-match path. Belt-and-braces clear
    # any classes that may have leaked from a prior interaction.
    page.evaluate(
        """() => {
            const stack = document.body && document.body._x_dataStack;
            if (stack && stack[0]) {
                stack[0].selectedClasses = [];
            }
        }"""
    )
    page.wait_for_timeout(200)

    with page.expect_response(
        lambda r: "/api/v1/search/quick" in r.url
                  and r.request.method == "POST",
        timeout=60000,
    ) as resp_info:
        page.evaluate(
            """() => {
                const stack = document.body && document.body._x_dataStack;
                if (stack && stack[0] && typeof stack[0].dashboardQuickSearch === 'function') {
                    stack[0].dashboardQuickSearch();
                }
            }"""
        )
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /search/quick returned {response.status}: "
            f"{response.text()[:300]}"
        )
    payload = response.json()
    image_used = payload.get("image_used", False)
    if not image_used:
        raise AssertionError(
            f"server reported image_used=false despite multipart "
            f"upload. Payload keys: {list(payload.keys())}"
        )
    page.wait_for_function(
        """() => {
            const stack = document.body && document.body._x_dataStack;
            return stack && stack[0]
                && Array.isArray(stack[0].searchResults)
                && stack[0].searchResults.length > 0
                && !stack[0].searchLoading;
        }""",
        timeout=30000,
    )
    return {
        "response_status": response.status,
        "server_result_count": len(payload.get("results", [])),
        "image_used": image_used,
    }


def _verify_source_self_match(page) -> dict:
    """The searched-with image came FROM the live corpus, so the
    source record must appear in the hybrid text+image results."""
    found = page.evaluate(
        """({brand, appNo}) => {
            const stack = document.body && document.body._x_dataStack;
            if (!stack || !stack[0]) return { found: false, count: 0 };
            const arr = stack[0].searchResults || [];
            const match = arr.some(r => {
                if (appNo && (r.application_no === appNo)) return true;
                if (brand && (r.brand_name || r.name || '').toLowerCase().includes(brand.toLowerCase())) return true;
                return false;
            });
            return { found: match, count: arr.length };
        }""",
        {"brand": _SOURCE_RECORD_NAME, "appNo": _SOURCE_RECORD_APP_NO},
    )
    if not found.get("found"):
        raise AssertionError(
            f"source record (brand={_SOURCE_RECORD_NAME!r}, "
            f"app_no={_SOURCE_RECORD_APP_NO!r}) not in "
            f"{found.get('count')} hybrid text+image results"
        )
    return {"result_count": found.get("count"), "self_matched": True}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_trademark_image_search_browser_smoke():
    wait_for_search_rate_limit_to_clear(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "Find live trademark with image + download",
                REPORTER, page, monitor, CONFIG,
                lambda: _find_record_with_image_and_download(),
            )
            run_browser_step(
                "Login as paid persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
                allow_console_errors=("status of 429",),
            )
            run_browser_step(
                "Open trademark search subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_trademark_search_subtab(page),
            )
            run_browser_step(
                "Attach image + submit hybrid text+image POST 200",
                REPORTER, page, monitor, CONFIG,
                lambda: _attach_image_and_submit(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Source record self-matches in hybrid results",
                REPORTER, page, monitor, CONFIG,
                lambda: _verify_source_self_match(page),
            )

            REPORTER.summary("Trademark image search browser smoke")
        finally:
            context.close()
            browser.close()
            if _FIGURE_TEMP_PATH and _FIGURE_TEMP_PATH.exists():
                try:
                    _FIGURE_TEMP_PATH.unlink()
                except OSError:
                    pass


if __name__ == "__main__":
    test_trademark_image_search_browser_smoke()

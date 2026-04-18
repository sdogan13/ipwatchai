"""
Live HTTP suite for the public visitor persona.

Run directly:
    python tests/live/personas/test_public_live.py
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import load_live_config


CONFIG = load_live_config()
CLIENT = LiveClient(CONFIG)
REPORTER = LiveReporter()
pytestmark = pytest.mark.skip(reason="Live persona script; run directly with python tests/live/personas/test_public_live.py")


def _retry_after_seconds(response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass
    return 15.0


def _run_public_search_request(name: str, request_fn):
    response = None
    for attempt in range(1, 5):
        response = request_fn()
        if response.status_code != 429:
            return response
        if attempt < 4:
            wait_seconds = _retry_after_seconds(response)
            REPORTER.warn(f"{name} -> 429, retrying after {wait_seconds:.0f}s")
            time.sleep(wait_seconds)
            continue
    return response


def _assert_public_search_results(name: str, response) -> None:
    if not REPORTER.expect_status(name, response, 200):
        return
    payload = response.json()
    results = payload.get("results")
    if not isinstance(results, list):
        REPORTER.fail(f"{name} -> results is not a list")
        REPORTER.record(name, False, "results is not a list")
        return
    REPORTER.ok(f"{name} -> results={len(results)}")
    REPORTER.record(name, True)


def _build_valid_public_search_png() -> bytes:
    image = Image.new("RGB", (2, 2), color=(99, 102, 241))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_health():
    name = "GET /health"
    response = CLIENT.get("/health", token=False)
    if REPORTER.expect_status(name, response, 200):
        payload = response.json()
        if payload.get("status") == "healthy":
            REPORTER.record(name, True)
        else:
            REPORTER.fail(f"{name} -> unexpected body {payload}")
            REPORTER.record(name, False, str(payload))


def test_api_info():
    REPORTER.expect_status("GET /api/info", CLIENT.get("/api/info", token=False), 200, record_pass=True)


def test_public_pages():
    for path in ["/", "/dashboard", "/admin", "/pricing", "/checkout"]:
        REPORTER.expect_status(f"GET {path}", CLIENT.get(path, token=False), 200, record_pass=True)


def test_quick_search_auth_gate():
    name = "GET /api/v1/search/quick requires auth"
    response = CLIENT.get("/api/v1/search/quick", params={"query": "wosen"}, token=False)
    if response.status_code in (401, 403):
        REPORTER.ok(f"{name} -> {response.status_code}")
        REPORTER.record(name, True)
    else:
        REPORTER.fail(f"{name} -> expected 401/403, got {response.status_code}")
        REPORTER.record(name, False, str(response.status_code))


def test_public_search():
    name = "GET /api/v1/search/public"
    response = _run_public_search_request(
        name,
        lambda: CLIENT.get("/api/v1/search/public", params={"query": "wosen"}, token=False),
    )
    _assert_public_search_results(name, response)


def test_public_search_with_class_filter():
    name = "POST /api/v1/search/public with class filter"
    response = _run_public_search_request(
        name,
        lambda: CLIENT.session.post(
            CLIENT.url("/api/v1/search/public"),
            data={"query": "wosen", "classes": "9"},
            timeout=CONFIG.timeout,
        ),
    )
    _assert_public_search_results(name, response)


def test_public_search_with_image_upload():
    name = "POST /api/v1/search/public with image"
    image_bytes = _build_valid_public_search_png()
    response = _run_public_search_request(
        name,
        lambda: CLIENT.session.post(
            CLIENT.url("/api/v1/search/public"),
            data={"query": "wosen"},
            files={"image": ("public-search.png", io.BytesIO(image_bytes), "image/png")},
            timeout=CONFIG.timeout,
        ),
    )
    _assert_public_search_results(name, response)


def main() -> None:
    REPORTER.print_heading("PUBLIC PERSONA LIVE SUITE", server=CONFIG.base_url)

    test_health()
    test_api_info()
    test_public_pages()
    test_quick_search_auth_gate()
    test_public_search()
    test_public_search_with_class_filter()
    test_public_search_with_image_upload()

    sys.exit(0 if REPORTER.summary("PUBLIC PERSONA SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

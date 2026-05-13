"""
Live HTTP suite for the authenticated member persona.

Run directly:
    python tests/live/personas/test_member_live.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.auth import login_user
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import PNG_1X1, load_live_config


CONFIG = load_live_config()
CLIENT = LiveClient(CONFIG)
REPORTER = LiveReporter()
pytestmark = pytest.mark.skip(reason="Live persona script; run directly with python tests/live/personas/test_member_live.py")


def _record_daily_limit_skip(name: str, response) -> bool:
    if response.status_code != 429:
        return False

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    detail = payload.get("detail", {})
    if isinstance(detail, dict) and detail.get("error") == "daily_limit_exceeded":
        REPORTER.warn(f"{name} -> 429 daily limit reached on default member account")
        REPORTER.record(name, True, "skipped: daily limit reached")
        return True

    return False


def test_login():
    login_user(CLIENT, REPORTER, CONFIG.email, CONFIG.password)


def test_dashboard_stats():
    name = "GET /api/v1/dashboard/stats"
    response = CLIENT.get("/api/v1/dashboard/stats")
    if REPORTER.expect_status(name, response, 200):
        payload = response.json()
        required = [
            "watchlist_count",
            "active_watchlist",
            "total_alerts",
            "new_alerts",
            "critical_alerts",
            "searches_this_month",
            "plan_usage",
        ]
        missing = [key for key in required if key not in payload]
        if missing:
            REPORTER.fail(f"{name} -> missing keys: {missing}")
            REPORTER.record(name, False, f"missing keys: {missing}")
        else:
            REPORTER.record(name, True)


def test_usage_summary():
    name = "GET /api/v1/usage/summary"
    response = CLIENT.get("/api/v1/usage/summary")
    if REPORTER.expect_status(name, response, 200):
        payload = response.json()
        usage = payload.get("usage", {})
        required = ["daily_quick_searches", "monthly_live_searches", "watchlist_items"]
        missing = [key for key in required if key not in usage]
        if missing:
            REPORTER.fail(f"{name} -> missing usage keys: {missing}")
            REPORTER.record(name, False, f"missing usage keys: {missing}")
        else:
            REPORTER.record(name, True)


def test_search_credits():
    name = "GET /api/v1/search/credits"
    response = CLIENT.get("/api/v1/search/credits")
    if REPORTER.expect_status(name, response, 200):
        payload = response.json()
        required = ["plan", "can_use_live_search", "remaining"]
        missing = [key for key in required if key not in payload]
        if missing:
            REPORTER.fail(f"{name} -> missing keys: {missing}")
            REPORTER.record(name, False, f"missing keys: {missing}")
        else:
            REPORTER.record(name, True)


def test_quick_search_text():
    name = "GET /api/v1/search"
    response = CLIENT.get("/api/v1/search", params={"query": "wosen"})
    if _record_daily_limit_skip(name, response):
        return
    if REPORTER.expect_status(name, response, 200):
        payload = response.json()
        if not isinstance(payload.get("results"), list):
            REPORTER.fail(f"{name} -> results is not a list")
            REPORTER.record(name, False, "results is not a list")
            return
        REPORTER.ok(f"{name} -> results={len(payload.get('results', []))}, source={payload.get('source')}")
        REPORTER.record(name, True)


def test_quick_search_with_classes():
    name = "GET /api/v1/search with classes"
    response = CLIENT.get("/api/v1/search", params={"query": "wosen", "classes": "9,35"})
    if _record_daily_limit_skip(name, response):
        return
    if REPORTER.expect_status(name, response, 200):
        payload = response.json()
        REPORTER.ok(f"{name} -> results={len(payload.get('results', []))}")
        REPORTER.record(name, True)


def test_quick_search_with_image():
    name = "POST /api/v1/search with image"
    files = {"image": ("test.png", io.BytesIO(PNG_1X1), "image/png")}
    response = CLIENT.post(
        "/api/v1/search",
        data={"query": "wosen", "classes": "9,35"},
        files=files,
    )
    if _record_daily_limit_skip(name, response):
        return
    if REPORTER.expect_status(name, response, 200):
        payload = response.json()
        REPORTER.ok(
            f"{name} -> results={len(payload.get('results', []))}, image_used={payload.get('image_used')}"
        )
        REPORTER.record(name, True)


def main() -> None:
    REPORTER.print_heading("MEMBER PERSONA LIVE SUITE", server=CONFIG.base_url, user=CONFIG.email)

    test_login()
    test_dashboard_stats()
    test_usage_summary()
    test_search_credits()
    test_quick_search_text()
    test_quick_search_with_classes()
    test_quick_search_with_image()

    sys.exit(0 if REPORTER.summary("MEMBER PERSONA SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

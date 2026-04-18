"""
Live HTTP suite for the dashboard and usage feature surface.

Run directly:
    python tests/live/features/test_dashboard_live.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.auth import login_user
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import load_live_config
from tests.live.helpers.personas import fetch_authenticated_json


CONFIG = load_live_config()
REPORTER = LiveReporter()
CLIENT: LiveClient | None = None
pytestmark = pytest.mark.skip(reason="Live feature script; run directly with python tests/live/features/test_dashboard_live.py")


def ensure_client() -> LiveClient | None:
    global CLIENT
    if CLIENT is not None:
        return CLIENT

    client = LiveClient(CONFIG)
    if not login_user(client, REPORTER, CONFIG.email, CONFIG.password, name="dashboard feature login"):
        return None

    CLIENT = client
    return CLIENT


def test_dashboard_stats_auth_gate():
    name = "GET /api/v1/dashboard/stats requires auth"
    response = LiveClient(CONFIG).get("/api/v1/dashboard/stats", token=False)
    if response.status_code in (401, 403):
        REPORTER.ok(f"{name} -> {response.status_code}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 401/403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_usage_summary_auth_gate():
    name = "GET /api/v1/usage/summary requires auth"
    response = LiveClient(CONFIG).get("/api/v1/usage/summary", token=False)
    if response.status_code in (401, 403):
        REPORTER.ok(f"{name} -> {response.status_code}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 401/403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_dashboard_stats_happy_path():
    client = ensure_client()
    if client is None:
        return

    name = "GET /api/v1/dashboard/stats"
    payload = fetch_authenticated_json(client, REPORTER, "/api/v1/dashboard/stats", name=name)
    if payload is None:
        return

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
    if not missing:
        REPORTER.ok(f"{name} -> watchlist_count={payload.get('watchlist_count')}, new_alerts={payload.get('new_alerts')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing keys: {missing}")
    REPORTER.record(name, False, f"missing keys: {missing}")


def test_usage_summary_happy_path():
    client = ensure_client()
    if client is None:
        return

    name = "GET /api/v1/usage/summary"
    payload = fetch_authenticated_json(client, REPORTER, "/api/v1/usage/summary", name=name)
    if payload is None:
        return

    usage = payload.get("usage", {})
    required_usage = ["daily_quick_searches", "monthly_live_searches", "watchlist_items"]
    missing_usage = [key for key in required_usage if key not in usage]
    if payload.get("plan") and payload.get("display_name") and not missing_usage:
        REPORTER.ok(f"{name} -> plan={payload.get('plan')}, display_name={payload.get('display_name')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing usage keys: {missing_usage} or plan/display_name")
    REPORTER.record(name, False, str(payload))


def main() -> None:
    REPORTER.print_heading("DASHBOARD FEATURE LIVE SUITE", server=CONFIG.base_url, user=CONFIG.email)

    test_dashboard_stats_auth_gate()
    test_usage_summary_auth_gate()
    test_dashboard_stats_happy_path()
    test_usage_summary_happy_path()

    sys.exit(0 if REPORTER.summary("DASHBOARD FEATURE SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

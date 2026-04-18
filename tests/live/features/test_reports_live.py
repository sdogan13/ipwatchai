"""
Live HTTP suite for the reports feature surface.

Run directly:
    python tests/live/features/test_reports_live.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import load_live_config
from tests.live.helpers.personas import PersonaSession, fetch_authenticated_json, resolve_free_persona_session


CONFIG = load_live_config()
REPORTER = LiveReporter()
FREE_SESSION: PersonaSession | None = None
FREE_RESOLVED = False
GENERATED_REPORT_ID: str | None = None
pytestmark = pytest.mark.skip(reason="Live feature script; run directly with python tests/live/features/test_reports_live.py")


def ensure_free_session() -> PersonaSession | None:
    global FREE_SESSION
    global FREE_RESOLVED
    if FREE_SESSION is None and not FREE_RESOLVED:
        FREE_RESOLVED = True
        FREE_SESSION = resolve_free_persona_session(REPORTER, label="reports free user")
    return FREE_SESSION


def test_reports_auth_gate():
    name = "GET /api/v1/reports requires auth"
    response = LiveClient(CONFIG).get("/api/v1/reports", token=False)
    if response.status_code in (401, 403):
        REPORTER.ok(f"{name} -> {response.status_code}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 401/403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_reports_list_happy_path():
    session = ensure_free_session()
    if session is None:
        return

    name = "GET /api/v1/reports"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/reports", name=name)
    if payload is None:
        return

    required = ["reports", "total", "page", "page_size", "total_pages", "usage"]
    usage_required = ["reports_used", "reports_limit", "can_export"]
    missing = [key for key in required if key not in payload]
    missing_usage = [key for key in usage_required if key not in payload.get("usage", {})]
    if not missing and not missing_usage:
        REPORTER.ok(
            f"{name} -> total={payload.get('total')}, reports_limit={payload.get('usage', {}).get('reports_limit')}"
        )
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing keys: {missing}, missing usage keys: {missing_usage}")
    REPORTER.record(name, False, str(payload))


def test_generate_report_invalid_payload():
    session = ensure_free_session()
    if session is None:
        return

    name = "POST /api/v1/reports/generate invalid payload"
    response = session.client.post("/api/v1/reports/generate", json_data={"file_format": "pdf"})
    if response.status_code == 422:
        REPORTER.ok(f"{name} -> 422 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 422, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_generate_report_happy_path():
    global GENERATED_REPORT_ID

    session = ensure_free_session()
    if session is None:
        return

    name = "POST /api/v1/reports/generate"
    response = session.client.post(
        "/api/v1/reports/generate",
        json_data={"report_type": "watchlist_summary", "file_format": "pdf"},
    )
    if response.status_code != 200:
        REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return

    payload = response.json()
    GENERATED_REPORT_ID = payload.get("report_id") or payload.get("id")
    if GENERATED_REPORT_ID and payload.get("status") == "completed":
        REPORTER.ok(f"{name} -> report_id={GENERATED_REPORT_ID}, status={payload.get('status')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing report identifier or completed status in payload {payload}")
    REPORTER.record(name, False, str(payload))


def test_get_generated_report():
    session = ensure_free_session()
    if session is None or not GENERATED_REPORT_ID:
        return

    name = "GET /api/v1/reports/{report_id}"
    payload = fetch_authenticated_json(
        session.client,
        REPORTER,
        f"/api/v1/reports/{GENERATED_REPORT_ID}",
        name=name,
    )
    if payload is None:
        return

    if str(payload.get("id")) == GENERATED_REPORT_ID and payload.get("status"):
        REPORTER.ok(f"{name} -> status={payload.get('status')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> unexpected payload {payload}")
    REPORTER.record(name, False, str(payload))


def test_download_report_permission_path():
    session = ensure_free_session()
    if session is None or not GENERATED_REPORT_ID:
        return

    name = "GET /api/v1/reports/{report_id}/download free export gate"
    response = session.client.get(f"/api/v1/reports/{GENERATED_REPORT_ID}/download")
    if response.status_code == 403:
        REPORTER.ok(f"{name} -> 403 as expected")
        REPORTER.record(name, True)
        return

    if response.status_code == 200 and response.content:
        REPORTER.ok(f"{name} -> download available")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 403 or 200, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def main() -> None:
    REPORTER.print_heading("REPORTS FEATURE LIVE SUITE", server=CONFIG.base_url)

    test_reports_auth_gate()
    test_reports_list_happy_path()
    test_generate_report_invalid_payload()
    test_generate_report_happy_path()
    test_get_generated_report()
    test_download_report_permission_path()

    sys.exit(0 if REPORTER.summary("REPORTS FEATURE SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

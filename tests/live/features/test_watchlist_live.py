"""
Live HTTP suite for the watchlist feature surface.

Run directly:
    python tests/live/features/test_watchlist_live.py
"""

from __future__ import annotations

import io
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.auth import login_user
from tests.live.helpers.client import LiveClient
from tests.live.helpers.cleanup import cleanup_watchlist_items_by_prefix
from tests.live.helpers.config import PNG_1X1, load_live_config
from tests.live.helpers.personas import (
    PAID_PLANS,
    PersonaSession,
    fetch_authenticated_json,
    resolve_free_persona_session,
    resolve_plan_persona_session,
)


CONFIG = load_live_config()
REPORTER = LiveReporter()
DEFAULT_CLIENT: LiveClient | None = None
FREE_SESSION: PersonaSession | None = None
FREE_RESOLVED = False
PAID_SESSION: PersonaSession | None = None
PAID_RESOLVED = False
CREATED_ITEM_ID: str | None = None
TEST_BRAND_PREFIX = "LIVE FEATURE WATCHLIST"
pytestmark = pytest.mark.skip(reason="Live feature script; run directly with python tests/live/features/test_watchlist_live.py")


def ensure_default_client() -> LiveClient | None:
    global DEFAULT_CLIENT
    if DEFAULT_CLIENT is not None:
        return DEFAULT_CLIENT

    client = LiveClient(CONFIG)
    if not login_user(client, REPORTER, CONFIG.email, CONFIG.password, name="watchlist feature login"):
        return None

    DEFAULT_CLIENT = client
    return DEFAULT_CLIENT


def ensure_free_session() -> PersonaSession | None:
    global FREE_SESSION
    global FREE_RESOLVED
    if FREE_SESSION is None and not FREE_RESOLVED:
        FREE_RESOLVED = True
        FREE_SESSION = resolve_free_persona_session(REPORTER, label="watchlist free user")
    return FREE_SESSION


def ensure_paid_session() -> PersonaSession | None:
    global PAID_SESSION
    global PAID_RESOLVED
    if PAID_SESSION is None and not PAID_RESOLVED:
        PAID_RESOLVED = True
        PAID_SESSION, _skipped = resolve_plan_persona_session(
            REPORTER,
            label="watchlist paid user",
            email_env="TEST_PAID_EMAIL",
            password_env="TEST_PAID_PASSWORD",
            required_plans=PAID_PLANS,
            fallback_to_default=False,
            provision_plan="starter",
        )
    return PAID_SESSION


def cleanup_paid_item() -> None:
    session = ensure_paid_session()
    if session is None:
        return

    if CREATED_ITEM_ID:
        response = session.client.delete(f"/api/v1/watchlist/{CREATED_ITEM_ID}")
        if response.status_code in (200, 404):
            REPORTER.info(f"DELETE /api/v1/watchlist/{CREATED_ITEM_ID} -> cleanup complete")
        else:
            REPORTER.warn(f"DELETE /api/v1/watchlist/{CREATED_ITEM_ID} -> cleanup returned {response.status_code}")

    cleanup_watchlist_items_by_prefix(session.client, REPORTER, TEST_BRAND_PREFIX)


def test_watchlist_stats_auth_gate():
    name = "GET /api/v1/watchlist/stats requires auth"
    response = LiveClient(CONFIG).get("/api/v1/watchlist/stats", token=False)
    if response.status_code in (401, 403):
        REPORTER.ok(f"{name} -> {response.status_code}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 401/403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_watchlist_list_happy_path():
    client = ensure_default_client()
    if client is None:
        return

    name = "GET /api/v1/watchlist"
    payload = fetch_authenticated_json(client, REPORTER, "/api/v1/watchlist", name=name)
    if payload is None:
        return

    required = ["items", "total", "page", "page_size", "total_pages"]
    missing = [key for key in required if key not in payload]
    if not missing:
        REPORTER.ok(f"{name} -> total={payload.get('total')}, page={payload.get('page')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing keys: {missing}")
    REPORTER.record(name, False, f"missing keys: {missing}")


def test_watchlist_stats_happy_path():
    client = ensure_default_client()
    if client is None:
        return

    name = "GET /api/v1/watchlist/stats"
    payload = fetch_authenticated_json(client, REPORTER, "/api/v1/watchlist/stats", name=name)
    if payload is None:
        return

    required = ["total_items", "active_items", "items_with_threats", "critical_threats", "new_alerts"]
    missing = [key for key in required if key not in payload]
    if not missing:
        REPORTER.ok(f"{name} -> total_items={payload.get('total_items')}, new_alerts={payload.get('new_alerts')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing keys: {missing}")
    REPORTER.record(name, False, f"missing keys: {missing}")


def test_watchlist_missing_item_404():
    client = ensure_default_client()
    if client is None:
        return

    name = "GET /api/v1/watchlist/{id} missing"
    response = client.get(f"/api/v1/watchlist/{uuid.uuid4()}")
    if response.status_code == 404:
        REPORTER.ok(f"{name} -> 404 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 404, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_free_visual_gate():
    session = ensure_free_session()
    if session is None:
        return

    name = "POST /api/v1/watchlist free visual gate"
    payload = {
        "brand_name": f"{TEST_BRAND_PREFIX} FREE {uuid.uuid4().hex[:8].upper()}",
        "nice_class_numbers": [9],
        "similarity_threshold": 0.7,
        "description": "Watchlist free-plan gate check",
        "monitor_text": True,
        "monitor_visual": True,
        "monitor_phonetic": False,
        "alert_frequency": "daily",
        "alert_email": False,
    }
    response = session.client.post("/api/v1/watchlist", json_data=payload)
    if response.status_code == 403:
        REPORTER.ok(f"{name} -> 403 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_paid_visual_happy_path():
    global CREATED_ITEM_ID

    session = ensure_paid_session()
    if session is None:
        name = "POST /api/v1/watchlist paid visual happy path"
        REPORTER.warn(f"{name} -> skipped (no paid persona available)")
        REPORTER.record(name, True, "skipped: no paid persona available")
        return

    name = "POST /api/v1/watchlist paid visual happy path"
    payload = {
        "brand_name": f"{TEST_BRAND_PREFIX} PAID {uuid.uuid4().hex[:8].upper()}",
        "nice_class_numbers": [9, 35],
        "similarity_threshold": 0.75,
        "description": "Watchlist paid-plan feature coverage",
        "monitor_text": True,
        "monitor_visual": True,
        "monitor_phonetic": False,
        "alert_frequency": "daily",
        "alert_email": False,
    }
    response = session.client.post("/api/v1/watchlist", json_data=payload)
    if response.status_code not in (200, 201):
        REPORTER.fail(f"{name} -> expected 200/201, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return

    payload = response.json()
    CREATED_ITEM_ID = payload.get("id")
    if CREATED_ITEM_ID:
        REPORTER.ok(f"{name} -> created item {CREATED_ITEM_ID}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing item id in response")
    REPORTER.record(name, False, str(payload))


def test_paid_logo_upload_cycle():
    session = ensure_paid_session()
    if session is None or not CREATED_ITEM_ID:
        name = "Watchlist paid logo upload cycle"
        REPORTER.warn(f"{name} -> skipped (no paid item available)")
        REPORTER.record(name, True, "skipped: no paid item available")
        return

    upload_name = "POST /api/v1/watchlist/{id}/logo"
    files = {"logo": ("watchlist-feature.png", io.BytesIO(PNG_1X1), "image/png")}
    upload_response = session.client.post(f"/api/v1/watchlist/{CREATED_ITEM_ID}/logo", files=files)
    if upload_response.status_code != 200:
        REPORTER.fail(f"{upload_name} -> expected 200, got {upload_response.status_code}: {upload_response.text[:200]}")
        REPORTER.record(upload_name, False, upload_response.text[:200])
        return

    REPORTER.ok(f"{upload_name} -> uploaded")
    REPORTER.record(upload_name, True)

    fetch_name = "GET /api/v1/watchlist/{id}/logo"
    fetch_response = session.client.get(f"/api/v1/watchlist/{CREATED_ITEM_ID}/logo", token=False)
    if fetch_response.status_code == 200 and fetch_response.content:
        REPORTER.ok(f"{fetch_name} -> bytes={len(fetch_response.content)}")
        REPORTER.record(fetch_name, True)
    else:
        REPORTER.fail(f"{fetch_name} -> expected 200 with content, got {fetch_response.status_code}")
        REPORTER.record(fetch_name, False, fetch_response.text[:200])
        return

    delete_name = "DELETE /api/v1/watchlist/{id}/logo"
    delete_response = session.client.delete(f"/api/v1/watchlist/{CREATED_ITEM_ID}/logo")
    if delete_response.status_code == 200:
        REPORTER.ok(f"{delete_name} -> deleted")
        REPORTER.record(delete_name, True)
        return

    REPORTER.fail(f"{delete_name} -> expected 200, got {delete_response.status_code}: {delete_response.text[:200]}")
    REPORTER.record(delete_name, False, delete_response.text[:200])


def main() -> None:
    REPORTER.print_heading("WATCHLIST FEATURE LIVE SUITE", server=CONFIG.base_url, user=CONFIG.email)

    try:
        test_watchlist_stats_auth_gate()
        test_watchlist_list_happy_path()
        test_watchlist_stats_happy_path()
        test_watchlist_missing_item_404()
        test_free_visual_gate()
        test_paid_visual_happy_path()
        test_paid_logo_upload_cycle()
    finally:
        cleanup_paid_item()

    sys.exit(0 if REPORTER.summary("WATCHLIST FEATURE SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

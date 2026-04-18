"""
Live HTTP suite for the free-plan persona.

Run directly:
    python tests/live/personas/test_free_user_live.py
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
from tests.live.helpers.config import PNG_1X1
from tests.live.helpers.personas import (
    PersonaSession,
    canonical_plan_name,
    fetch_authenticated_json,
    resolve_free_persona_session,
)


REPORTER = LiveReporter()
SESSION: PersonaSession | None = None
SESSION_RESOLVED = False
pytestmark = pytest.mark.skip(reason="Live persona script; run directly with python tests/live/personas/test_free_user_live.py")


def ensure_session() -> PersonaSession | None:
    global SESSION
    global SESSION_RESOLVED
    if SESSION is None and not SESSION_RESOLVED:
        SESSION_RESOLVED = True
        SESSION = resolve_free_persona_session(REPORTER, label="free user")
    return SESSION


def test_usage_summary_plan_free():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/usage/summary (free plan)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/usage/summary", name=name)
    if payload is None:
        return

    if canonical_plan_name(payload.get("plan")) == "free":
        REPORTER.ok(f"{name} -> plan=free")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected free, got {payload.get('plan')}")
    REPORTER.record(name, False, str(payload.get("plan")))


def test_search_credits_gate():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/search/credits (free live-search gate)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/search/credits", name=name)
    if payload is None:
        return

    can_use_live_search = bool(payload.get("can_use_live_search"))
    remaining = payload.get("remaining")
    if can_use_live_search is False and remaining == 0:
        REPORTER.ok(f"{name} -> live search blocked as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(
        f"{name} -> expected can_use_live_search=False and remaining=0, "
        f"got can_use_live_search={can_use_live_search}, remaining={remaining}"
    )
    REPORTER.record(name, False, str(payload))


def test_quick_search_available():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/search/quick (free quick search)"
    response = session.client.get("/api/v1/search/quick", params={"query": "wosen"})
    if response.status_code != 200:
        REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return

    payload = response.json()
    if isinstance(payload.get("results"), list):
        REPORTER.ok(f"{name} -> results={len(payload.get('results', []))}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> results is not a list")
    REPORTER.record(name, False, "results is not a list")


def test_lead_credits_denied():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/leads/credits (free gate)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/leads/credits", name=name)
    if payload is None:
        return

    if payload.get("can_access") is False and int(payload.get("daily_limit", 0)) == 0:
        REPORTER.ok(f"{name} -> lead access denied as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected can_access=false and daily_limit=0, got {payload}")
    REPORTER.record(name, False, str(payload))


def test_holder_search_denied():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/holders/search (free gate)"
    response = session.client.get("/api/v1/holders/search", params={"query": "te"})
    if response.status_code == 403:
        REPORTER.ok(f"{name} -> 403 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_visual_watchlist_create_denied():
    session = ensure_session()
    if session is None:
        return

    name = "POST /api/v1/watchlist (free visual tracking gate)"
    payload = {
        "brand_name": f"LIVE FREE VISUAL {uuid.uuid4().hex[:8].upper()}",
        "nice_class_numbers": [9],
        "similarity_threshold": 0.7,
        "description": "Free-plan visual tracking gate check",
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


def test_watchlist_logo_upload_denied():
    session = ensure_session()
    if session is None:
        return

    item_id = None
    name = "POST /api/v1/watchlist/{id}/logo (free logo gate)"
    payload = {
        "brand_name": f"LIVE FREE LOGO {uuid.uuid4().hex[:8].upper()}",
        "nice_class_numbers": [9],
        "similarity_threshold": 0.7,
        "description": "Free-plan logo gate check",
        "monitor_text": True,
        "monitor_visual": False,
        "monitor_phonetic": False,
        "alert_frequency": "daily",
        "alert_email": False,
    }

    try:
        create_response = session.client.post("/api/v1/watchlist", json_data=payload)
        if create_response.status_code not in (200, 201):
            REPORTER.fail(
                f"{name} -> watchlist setup failed with {create_response.status_code}: "
                f"{create_response.text[:200]}"
            )
            REPORTER.record(name, False, create_response.text[:200])
            return

        item_id = create_response.json().get("id")
        if not item_id:
            REPORTER.fail(f"{name} -> watchlist setup missing item id")
            REPORTER.record(name, False, "missing item id")
            return

        files = {"logo": ("free-logo.png", io.BytesIO(PNG_1X1), "image/png")}
        response = session.client.post(f"/api/v1/watchlist/{item_id}/logo", files=files)
        if response.status_code == 403:
            REPORTER.ok(f"{name} -> 403 as expected")
            REPORTER.record(name, True)
            return

        REPORTER.fail(f"{name} -> expected 403, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
    finally:
        if item_id:
            cleanup_response = session.client.delete(f"/api/v1/watchlist/{item_id}")
            if cleanup_response.status_code not in (200, 404):
                REPORTER.warn(
                    f"DELETE /api/v1/watchlist/{item_id} -> cleanup returned {cleanup_response.status_code}"
                )


def main() -> None:
    REPORTER.print_heading("FREE PLAN PERSONA LIVE SUITE")

    test_usage_summary_plan_free()
    test_search_credits_gate()
    test_quick_search_available()
    test_lead_credits_denied()
    test_holder_search_denied()
    test_visual_watchlist_create_denied()
    test_watchlist_logo_upload_denied()

    sys.exit(0 if REPORTER.summary("FREE PLAN PERSONA SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

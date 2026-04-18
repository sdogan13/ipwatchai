"""
Live HTTP suite for the paid-user persona.

Run directly:
    python tests/live/personas/test_paid_user_live.py
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
    PAID_PLANS,
    PersonaSession,
    canonical_plan_name,
    fetch_authenticated_json,
    resolve_plan_persona_session,
)


REPORTER = LiveReporter()
SESSION: PersonaSession | None = None
SESSION_SKIPPED = False
CREATED_ITEM_ID: str | None = None
pytestmark = pytest.mark.skip(reason="Live persona script; run directly with python tests/live/personas/test_paid_user_live.py")


def ensure_session() -> PersonaSession | None:
    global SESSION
    global SESSION_SKIPPED
    if SESSION is None and not SESSION_SKIPPED:
        SESSION, SESSION_SKIPPED = resolve_plan_persona_session(
            REPORTER,
            label="paid user",
            email_env="TEST_PAID_EMAIL",
            password_env="TEST_PAID_PASSWORD",
            required_plans=PAID_PLANS,
            fallback_to_default=False,
            provision_plan="starter",
        )
    return SESSION


def test_usage_summary_paid():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/usage/summary (paid plan)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/usage/summary", name=name)
    if payload is None:
        return

    plan_name = canonical_plan_name(payload.get("plan"))
    can_track_logos = bool(payload.get("usage", {}).get("can_track_logos"))
    if plan_name in PAID_PLANS and can_track_logos:
        REPORTER.ok(f"{name} -> plan={plan_name}, can_track_logos={can_track_logos}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected paid plan with can_track_logos=true, got {payload}")
    REPORTER.record(name, False, str(payload))


def test_search_credits_paid():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/search/credits (paid plan visibility)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/search/credits", name=name)
    if payload is None:
        return

    plan_name = canonical_plan_name(payload.get("plan"))
    can_use_live_search = payload.get("can_use_live_search")
    monthly_limit = payload.get("monthly_limit")
    remaining = payload.get("remaining")

    if plan_name not in PAID_PLANS:
        REPORTER.fail(f"{name} -> expected paid plan, got {payload}")
        REPORTER.record(name, False, str(payload))
        return

    if not isinstance(monthly_limit, int) or not isinstance(remaining, int):
        REPORTER.fail(f"{name} -> expected integer credit fields, got {payload}")
        REPORTER.record(name, False, str(payload))
        return

    if can_use_live_search is True and monthly_limit > 0:
        REPORTER.ok(
            f"{name} -> plan={plan_name}, can_use_live_search={can_use_live_search}, remaining={remaining}"
        )
        REPORTER.record(name, True)
        return

    if can_use_live_search is False and monthly_limit == 0 and remaining == 0:
        REPORTER.ok(
            f"{name} -> plan={plan_name}, live_search_disabled_on_current_paid_plan"
        )
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> inconsistent search credits payload: {payload}")
    REPORTER.record(name, False, str(payload))


def test_create_visual_watchlist_item():
    global CREATED_ITEM_ID

    session = ensure_session()
    if session is None:
        return

    name = "POST /api/v1/watchlist (paid visual tracking)"
    payload = {
        "brand_name": f"LIVE PAID VISUAL {uuid.uuid4().hex[:8].upper()}",
        "nice_class_numbers": [9, 35],
        "similarity_threshold": 0.75,
        "description": "Paid-plan visual tracking coverage",
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

    created = response.json()
    CREATED_ITEM_ID = created.get("id")
    if CREATED_ITEM_ID:
        REPORTER.ok(f"{name} -> created item {CREATED_ITEM_ID}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing item id in response")
    REPORTER.record(name, False, str(created))


def test_upload_logo_asset():
    session = ensure_session()
    if session is None or not CREATED_ITEM_ID:
        return

    name = "POST /api/v1/watchlist/{id}/logo (paid asset upload)"
    files = {"logo": ("paid-test.png", io.BytesIO(PNG_1X1), "image/png")}
    response = session.client.post(f"/api/v1/watchlist/{CREATED_ITEM_ID}/logo", files=files)
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> uploaded")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_get_logo_asset():
    session = ensure_session()
    if session is None or not CREATED_ITEM_ID:
        return

    name = "GET /api/v1/watchlist/{id}/logo (paid asset fetch)"
    response = session.client.get(f"/api/v1/watchlist/{CREATED_ITEM_ID}/logo", token=False)
    if response.status_code == 200 and response.content:
        REPORTER.ok(f"{name} -> bytes={len(response.content)}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 200 with content, got {response.status_code}")
    REPORTER.record(name, False, response.text[:200])


def test_delete_logo_asset():
    session = ensure_session()
    if session is None or not CREATED_ITEM_ID:
        return

    name = "DELETE /api/v1/watchlist/{id}/logo (paid asset cleanup)"
    response = session.client.delete(f"/api/v1/watchlist/{CREATED_ITEM_ID}/logo")
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> deleted")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def cleanup_created_item() -> None:
    session = ensure_session()
    if session is None or not CREATED_ITEM_ID:
        return

    response = session.client.delete(f"/api/v1/watchlist/{CREATED_ITEM_ID}")
    if response.status_code in (200, 404):
        REPORTER.info(f"DELETE /api/v1/watchlist/{CREATED_ITEM_ID} -> cleanup complete")
    else:
        REPORTER.warn(
            f"DELETE /api/v1/watchlist/{CREATED_ITEM_ID} -> cleanup returned {response.status_code}"
        )


def main() -> None:
    REPORTER.print_heading("PAID USER PERSONA LIVE SUITE")

    try:
        test_usage_summary_paid()
        test_search_credits_paid()
        test_create_visual_watchlist_item()
        test_upload_logo_asset()
        test_get_logo_asset()
        test_delete_logo_asset()
    finally:
        cleanup_created_item()

    sys.exit(0 if REPORTER.summary("PAID USER PERSONA SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

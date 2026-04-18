"""
Live HTTP suite for the trademark applications feature surface.

Run directly:
    python tests/live/features/test_applications_live.py
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
CREATED_APP_ID: str | None = None
pytestmark = pytest.mark.skip(reason="Live feature script; run directly with python tests/live/features/test_applications_live.py")


def ensure_default_client() -> LiveClient | None:
    global DEFAULT_CLIENT
    if DEFAULT_CLIENT is not None:
        return DEFAULT_CLIENT

    client = LiveClient(CONFIG)
    if not login_user(client, REPORTER, CONFIG.email, CONFIG.password, name="applications feature login"):
        return None

    DEFAULT_CLIENT = client
    return DEFAULT_CLIENT


def ensure_free_session() -> PersonaSession | None:
    global FREE_SESSION
    global FREE_RESOLVED
    if FREE_SESSION is None and not FREE_RESOLVED:
        FREE_RESOLVED = True
        FREE_SESSION = resolve_free_persona_session(REPORTER, label="applications free user")
    return FREE_SESSION


def ensure_paid_session() -> PersonaSession | None:
    global PAID_SESSION
    global PAID_RESOLVED
    if PAID_SESSION is None and not PAID_RESOLVED:
        PAID_RESOLVED = True
        PAID_SESSION, _skipped = resolve_plan_persona_session(
            REPORTER,
            label="applications paid user",
            email_env="TEST_PAID_EMAIL",
            password_env="TEST_PAID_PASSWORD",
            required_plans=PAID_PLANS,
            fallback_to_default=False,
            provision_plan="starter",
        )
    return PAID_SESSION


def cleanup_created_application() -> None:
    session = ensure_paid_session()
    if session is None or not CREATED_APP_ID:
        return

    response = session.client.delete(f"/api/v1/applications/{CREATED_APP_ID}")
    if response.status_code in (200, 400, 404):
        REPORTER.info(f"DELETE /api/v1/applications/{CREATED_APP_ID} -> cleanup attempted")
    else:
        REPORTER.warn(f"DELETE /api/v1/applications/{CREATED_APP_ID} -> cleanup returned {response.status_code}")


def test_applications_auth_gate():
    name = "GET /api/v1/applications/ requires auth"
    response = LiveClient(CONFIG).get("/api/v1/applications/", token=False)
    if response.status_code in (401, 403):
        REPORTER.ok(f"{name} -> {response.status_code}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 401/403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_applications_list_happy_path():
    client = ensure_default_client()
    if client is None:
        return

    name = "GET /api/v1/applications/"
    payload = fetch_authenticated_json(client, REPORTER, "/api/v1/applications/", name=name)
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


def test_applications_invalid_create_validation():
    client = ensure_default_client()
    if client is None:
        return

    name = "POST /api/v1/applications/ invalid payload"
    response = client.post(
        "/api/v1/applications/",
        json_data={"brand_name": "", "nice_class_numbers": [0]},
    )
    if response.status_code == 422:
        REPORTER.ok(f"{name} -> 422 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 422, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_free_plan_create_gate():
    session = ensure_free_session()
    if session is None:
        return

    name = "POST /api/v1/applications/ free plan gate"
    response = session.client.post(
        "/api/v1/applications/",
        json_data={"brand_name": f"LIVE FREE APP {uuid.uuid4().hex[:8].upper()}", "nice_class_numbers": [25]},
    )
    if response.status_code == 403:
        REPORTER.ok(f"{name} -> 403 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_paid_application_happy_path():
    global CREATED_APP_ID

    session = ensure_paid_session()
    if session is None:
        name = "POST /api/v1/applications/ paid draft create"
        REPORTER.warn(f"{name} -> skipped (no paid persona available)")
        REPORTER.record(name, True, "skipped: no paid persona available")
        return

    name = "POST /api/v1/applications/ paid draft create"
    response = session.client.post(
        "/api/v1/applications/",
        json_data={
            "brand_name": f"LIVE PAID APP {uuid.uuid4().hex[:8].upper()}",
            "nice_class_numbers": [25],
            "goods_services_description": "Clothing",
        },
    )
    if response.status_code != 200:
        REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return

    payload = response.json()
    CREATED_APP_ID = payload.get("id")
    if CREATED_APP_ID and payload.get("status") == "draft":
        REPORTER.ok(f"{name} -> app_id={CREATED_APP_ID}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> unexpected payload {payload}")
    REPORTER.record(name, False, str(payload))


def test_paid_application_follow_up_paths():
    session = ensure_paid_session()
    if session is None or not CREATED_APP_ID:
        name = "Paid application follow-up paths"
        REPORTER.warn(f"{name} -> skipped (no paid application created)")
        REPORTER.record(name, True, "skipped: no paid application created")
        return

    list_name = "GET /api/v1/applications/ paid list"
    list_payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/applications/", name=list_name)
    if list_payload is None:
        return
    if any(str(item.get("id")) == CREATED_APP_ID for item in list_payload.get("items", [])):
        REPORTER.ok(f"{list_name} -> created draft present")
        REPORTER.record(list_name, True)
    else:
        REPORTER.fail(f"{list_name} -> created draft not present in list")
        REPORTER.record(list_name, False, str(list_payload))
        return

    get_name = "GET /api/v1/applications/{id}"
    get_payload = fetch_authenticated_json(
        session.client,
        REPORTER,
        f"/api/v1/applications/{CREATED_APP_ID}",
        name=get_name,
    )
    if get_payload is None:
        return
    if str(get_payload.get("id")) == CREATED_APP_ID and get_payload.get("status") == "draft":
        REPORTER.ok(f"{get_name} -> draft loaded")
        REPORTER.record(get_name, True)
    else:
        REPORTER.fail(f"{get_name} -> unexpected payload {get_payload}")
        REPORTER.record(get_name, False, str(get_payload))
        return

    update_name = "PUT /api/v1/applications/{id}"
    update_response = session.client.put(
        f"/api/v1/applications/{CREATED_APP_ID}",
        data={"brand_name": "LIVE PAID APP UPDATED"},
    )
    if update_response.status_code == 200 and update_response.json().get("brand_name") == "LIVE PAID APP UPDATED":
        REPORTER.ok(f"{update_name} -> brand updated")
        REPORTER.record(update_name, True)
    else:
        REPORTER.fail(
            f"{update_name} -> expected 200 with updated brand, got {update_response.status_code}: "
            f"{update_response.text[:200]}"
        )
        REPORTER.record(update_name, False, update_response.text[:200])
        return

    logo_upload_name = "POST /api/v1/applications/{id}/logo"
    files = {"file": ("application-feature.png", io.BytesIO(PNG_1X1), "image/png")}
    upload_response = session.client.post(f"/api/v1/applications/{CREATED_APP_ID}/logo", files=files)
    if upload_response.status_code == 200:
        REPORTER.ok(f"{logo_upload_name} -> uploaded")
        REPORTER.record(logo_upload_name, True)
    else:
        REPORTER.fail(f"{logo_upload_name} -> expected 200, got {upload_response.status_code}: {upload_response.text[:200]}")
        REPORTER.record(logo_upload_name, False, upload_response.text[:200])
        return

    logo_get_name = "GET /api/v1/applications/{id}/logo"
    logo_get_response = session.client.get(f"/api/v1/applications/{CREATED_APP_ID}/logo")
    if logo_get_response.status_code == 200 and logo_get_response.content:
        REPORTER.ok(f"{logo_get_name} -> bytes={len(logo_get_response.content)}")
        REPORTER.record(logo_get_name, True)
    else:
        REPORTER.fail(f"{logo_get_name} -> expected 200, got {logo_get_response.status_code}: {logo_get_response.text[:200]}")
        REPORTER.record(logo_get_name, False, logo_get_response.text[:200])
        return

    submit_name = "POST /api/v1/applications/{id}/submit missing fields"
    submit_response = session.client.post(f"/api/v1/applications/{CREATED_APP_ID}/submit")
    if submit_response.status_code == 422:
        REPORTER.ok(f"{submit_name} -> 422 as expected")
        REPORTER.record(submit_name, True)
        return

    REPORTER.fail(f"{submit_name} -> expected 422, got {submit_response.status_code}: {submit_response.text[:200]}")
    REPORTER.record(submit_name, False, submit_response.text[:200])


def main() -> None:
    REPORTER.print_heading("APPLICATIONS FEATURE LIVE SUITE", server=CONFIG.base_url, user=CONFIG.email)

    try:
        test_applications_auth_gate()
        test_applications_list_happy_path()
        test_applications_invalid_create_validation()
        test_free_plan_create_gate()
        test_paid_application_happy_path()
        test_paid_application_follow_up_paths()
    finally:
        cleanup_created_application()

    sys.exit(0 if REPORTER.summary("APPLICATIONS FEATURE SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

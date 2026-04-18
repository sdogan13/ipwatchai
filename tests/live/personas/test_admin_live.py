"""
Live HTTP suite for the organization-admin persona.

Run directly:
    python tests/live/personas/test_admin_live.py
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
from tests.live.helpers.personas import PersonaSession, fetch_authenticated_json


CONFIG = load_live_config()
REPORTER = LiveReporter()
SESSION: PersonaSession | None = None
SELF_USER_ID: str | None = None
pytestmark = pytest.mark.skip(reason="Live persona script; run directly with python tests/live/personas/test_admin_live.py")


def ensure_session() -> PersonaSession | None:
    global SESSION
    global SELF_USER_ID
    if SESSION is not None:
        return SESSION

    client = LiveClient(CONFIG)
    if not login_user(client, REPORTER, CONFIG.email, CONFIG.password, name="admin login"):
        return None

    profile = fetch_authenticated_json(
        client,
        REPORTER,
        "/api/v1/auth/me",
        name="admin bootstrap profile",
    )
    if profile is None:
        return None

    role = profile.get("role")
    is_superadmin = bool(profile.get("is_superadmin"))
    if role != "admin" or is_superadmin:
        REPORTER.fail(
            f"admin persona resolution -> expected role=admin and is_superadmin=false, "
            f"got role={role}, is_superadmin={is_superadmin}"
        )
        REPORTER.record("admin persona resolution", False, f"role={role}, is_superadmin={is_superadmin}")
        return None

    SELF_USER_ID = profile.get("id")
    SESSION = PersonaSession(
        label="admin",
        config=CONFIG,
        client=client,
        email=CONFIG.email,
        plan="unknown",
        display_name="admin",
        source="default",
        user_id=profile.get("id"),
        organization_id=profile.get("organization_id"),
        role=role,
        is_superadmin=is_superadmin,
    )
    REPORTER.ok(f"admin persona resolution -> {CONFIG.email} (role=admin)")
    REPORTER.record("admin persona resolution", True)
    return SESSION


def test_auth_me_admin():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/auth/me (admin persona)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/auth/me", name=name)
    if payload is None:
        return

    if payload.get("role") == "admin" and payload.get("is_superadmin") is False:
        REPORTER.ok(f"{name} -> role=admin, is_superadmin=false")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> unexpected role/superadmin flags: {payload}")
    REPORTER.record(name, False, str(payload))


def test_list_users():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/users (org admin user list)"
    response = session.client.get("/api/v1/users")
    if response.status_code != 200:
        REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return

    payload = response.json()
    if isinstance(payload, list) and payload:
        REPORTER.ok(f"{name} -> users={len(payload)}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected non-empty user list, got {payload}")
    REPORTER.record(name, False, str(payload))


def test_get_self_user():
    session = ensure_session()
    if session is None or not SELF_USER_ID:
        return

    name = "GET /api/v1/users/{self} (org admin detail)"
    payload = fetch_authenticated_json(
        session.client,
        REPORTER,
        f"/api/v1/users/{SELF_USER_ID}",
        name=name,
    )
    if payload is None:
        return

    if payload.get("id") == SELF_USER_ID and payload.get("role") == "admin":
        REPORTER.ok(f"{name} -> id={SELF_USER_ID}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> unexpected payload {payload}")
    REPORTER.record(name, False, str(payload))


def test_get_organization():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/organization"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/organization", name=name)
    if payload is None:
        return

    if payload.get("id") and payload.get("name"):
        REPORTER.ok(f"{name} -> org={payload.get('name')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing organization id/name")
    REPORTER.record(name, False, str(payload))


def test_get_organization_stats():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/organization/stats"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/organization/stats", name=name)
    if payload is None:
        return

    required = ["user_count", "active_watchlist_items", "new_alerts", "critical_alerts", "searches_this_month"]
    missing = [key for key in required if key not in payload]
    if not missing:
        REPORTER.ok(f"{name} -> user_count={payload.get('user_count')}, searches_this_month={payload.get('searches_this_month')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing keys: {missing}")
    REPORTER.record(name, False, f"missing keys: {missing}")


def test_get_organization_settings():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/organization/settings"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/organization/settings", name=name)
    if payload is None:
        return

    required = ["organization_id", "name", "default_alert_threshold"]
    missing = [key for key in required if key not in payload]
    if not missing:
        REPORTER.ok(f"{name} -> threshold={payload.get('default_alert_threshold')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing keys: {missing}")
    REPORTER.record(name, False, f"missing keys: {missing}")


def test_superadmin_overview_denied():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/admin/overview denied for org admin"
    response = session.client.get("/api/v1/admin/overview")
    if response.status_code == 403:
        REPORTER.ok(f"{name} -> 403 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_pipeline_status_denied():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/pipeline/status denied for org admin"
    response = session.client.get("/api/v1/pipeline/status", params={"limit": 3})
    if response.status_code == 403:
        REPORTER.ok(f"{name} -> 403 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def main() -> None:
    REPORTER.print_heading("ADMIN PERSONA LIVE SUITE", server=CONFIG.base_url, user=CONFIG.email)

    test_auth_me_admin()
    test_list_users()
    test_get_self_user()
    test_get_organization()
    test_get_organization_stats()
    test_get_organization_settings()
    test_superadmin_overview_denied()
    test_pipeline_status_denied()

    sys.exit(0 if REPORTER.summary("ADMIN PERSONA SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

"""
Live HTTP suite for the superadmin persona.

Run directly:
    python tests/live/personas/test_superadmin_live.py
"""

from __future__ import annotations

import os
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


REPORTER = LiveReporter()
SESSION: PersonaSession | None = None
SESSION_RESOLVED = False
pytestmark = pytest.mark.skip(reason="Live persona script; run directly with python tests/live/personas/test_superadmin_live.py")


def ensure_session() -> PersonaSession | None:
    global SESSION
    global SESSION_RESOLVED
    if SESSION is not None or SESSION_RESOLVED:
        return SESSION
    SESSION_RESOLVED = True

    if not (os.environ.get("TEST_SUPERADMIN_EMAIL") and os.environ.get("TEST_SUPERADMIN_PASSWORD")):
        REPORTER.warn(
            "superadmin persona resolution -> skipped "
            "(set TEST_SUPERADMIN_EMAIL and TEST_SUPERADMIN_PASSWORD)"
        )
        REPORTER.record(
            "superadmin persona resolution",
            True,
            "skipped: missing TEST_SUPERADMIN_EMAIL/TEST_SUPERADMIN_PASSWORD",
        )
        return None

    config = load_live_config(
        email_env="TEST_SUPERADMIN_EMAIL",
        password_env="TEST_SUPERADMIN_PASSWORD",
    )
    client = LiveClient(config)
    if not login_user(client, REPORTER, config.email, config.password, name="superadmin login"):
        return None

    profile = fetch_authenticated_json(
        client,
        REPORTER,
        "/api/v1/auth/me",
        name="superadmin bootstrap profile",
    )
    if profile is None:
        return None

    if not profile.get("is_superadmin"):
        REPORTER.fail("superadmin persona resolution -> authenticated user is not superadmin")
        REPORTER.record("superadmin persona resolution", False, str(profile))
        return None

    SESSION = PersonaSession(
        label="superadmin",
        config=config,
        client=client,
        email=config.email,
        plan="superadmin",
        display_name="Super Admin",
        source="explicit",
        user_id=profile.get("id"),
        organization_id=profile.get("organization_id"),
        role=profile.get("role"),
        is_superadmin=True,
    )
    REPORTER.ok(f"superadmin persona resolution -> {config.email}")
    REPORTER.record("superadmin persona resolution", True)
    return SESSION


def test_auth_me_superadmin():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/auth/me (superadmin persona)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/auth/me", name=name)
    if payload is None:
        return

    if payload.get("is_superadmin") is True:
        REPORTER.ok(f"{name} -> is_superadmin=true")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected is_superadmin=true, got {payload}")
    REPORTER.record(name, False, str(payload))


def test_admin_overview():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/admin/overview"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/admin/overview", name=name)
    if payload is None:
        return

    required = ["total_active_users", "total_active_orgs", "mrr"]
    missing = [key for key in required if key not in payload]
    if not missing:
        REPORTER.ok(f"{name} -> orgs={payload.get('total_active_orgs')}, users={payload.get('total_active_users')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing keys: {missing}")
    REPORTER.record(name, False, f"missing keys: {missing}")


def test_admin_settings():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/admin/settings"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/admin/settings", name=name)
    if payload is None:
        return

    if isinstance(payload, (dict, list)):
        REPORTER.ok(f"{name} -> settings_count={len(payload)}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected dict or list payload")
    REPORTER.record(name, False, str(type(payload)))


def test_admin_plans():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/admin/plans"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/admin/plans", name=name)
    if payload is None:
        return

    plans = payload.get("plans")
    if isinstance(plans, list):
        REPORTER.ok(f"{name} -> plans={len(plans)}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected plans list")
    REPORTER.record(name, False, str(payload))


def test_admin_users():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/admin/users"
    payload = fetch_authenticated_json(
        session.client,
        REPORTER,
        "/api/v1/admin/users",
        name=name,
        params={"limit": 5, "offset": 0},
    )
    if payload is None:
        return

    users = payload.get("users")
    if isinstance(users, list):
        REPORTER.ok(f"{name} -> users={len(users)}, total={payload.get('total')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected users list")
    REPORTER.record(name, False, str(payload))


def test_admin_organizations():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/admin/organizations"
    payload = fetch_authenticated_json(
        session.client,
        REPORTER,
        "/api/v1/admin/organizations",
        name=name,
        params={"limit": 5, "offset": 0},
    )
    if payload is None:
        return

    orgs = payload.get("organizations")
    if isinstance(orgs, list):
        REPORTER.ok(f"{name} -> organizations={len(orgs)}, total={payload.get('total')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected organizations list")
    REPORTER.record(name, False, str(payload))


def test_usage_analytics():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/admin/analytics/usage"
    payload = fetch_authenticated_json(
        session.client,
        REPORTER,
        "/api/v1/admin/analytics/usage",
        name=name,
        params={"days": 7},
    )
    if payload is None:
        return

    if payload.get("period_days") == 7:
        REPORTER.ok(f"{name} -> period_days=7")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected period_days=7, got {payload}")
    REPORTER.record(name, False, str(payload))


def test_pipeline_status():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/pipeline/status"
    payload = fetch_authenticated_json(
        session.client,
        REPORTER,
        "/api/v1/pipeline/status",
        name=name,
        params={"limit": 3},
    )
    if payload is None:
        return

    required = ["is_running", "recent_runs"]
    missing = [key for key in required if key not in payload]
    if not missing:
        REPORTER.ok(f"{name} -> current_step={payload.get('current_step')}, runs={len(payload.get('recent_runs', []))}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing keys: {missing}")
    REPORTER.record(name, False, f"missing keys: {missing}")


def main() -> None:
    user = os.environ.get("TEST_SUPERADMIN_EMAIL")
    REPORTER.print_heading("SUPERADMIN PERSONA LIVE SUITE", user=user)

    test_auth_me_superadmin()
    test_admin_overview()
    test_admin_settings()
    test_admin_plans()
    test_admin_users()
    test_admin_organizations()
    test_usage_analytics()
    test_pipeline_status()

    sys.exit(0 if REPORTER.summary("SUPERADMIN PERSONA SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

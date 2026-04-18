"""
Live HTTP suite for the billing feature surface.

Run directly:
    python tests/live/features/test_billing_live.py
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


CONFIG = load_live_config()
REPORTER = LiveReporter()
CLIENT: LiveClient | None = None
pytestmark = pytest.mark.skip(reason="Live feature script; run directly with python tests/live/features/test_billing_live.py")


def ensure_client() -> LiveClient | None:
    global CLIENT
    if CLIENT is not None:
        return CLIENT

    client = LiveClient(CONFIG)
    if not login_user(client, REPORTER, CONFIG.email, CONFIG.password, name="billing feature login"):
        return None

    CLIENT = client
    return CLIENT


def test_validate_discount_auth_gate():
    name = "POST /api/v1/billing/validate-discount requires auth"
    response = LiveClient(CONFIG).post(
        "/api/v1/billing/validate-discount",
        json_data={"code": "LAUNCH20", "plan": "professional"},
        token=False,
    )
    if response.status_code in (401, 403):
        REPORTER.ok(f"{name} -> {response.status_code}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 401/403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_validate_discount_missing_code():
    client = ensure_client()
    if client is None:
        return

    name = "POST /api/v1/billing/validate-discount missing code"
    response = client.post("/api/v1/billing/validate-discount", json_data={"code": "", "plan": "professional"})
    if response.status_code == 400:
        REPORTER.ok(f"{name} -> 400 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 400, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_validate_discount_invalid_code():
    client = ensure_client()
    if client is None:
        return

    name = "POST /api/v1/billing/validate-discount invalid code"
    response = client.post(
        "/api/v1/billing/validate-discount",
        json_data={"code": "INVALID-CODE-XYZ", "plan": "professional"},
    )
    if response.status_code == 404:
        REPORTER.ok(f"{name} -> 404 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 404, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_validate_discount_happy_path():
    client = ensure_client()
    if client is None:
        return

    name = "POST /api/v1/billing/validate-discount valid code"
    code = os.environ.get("TEST_VALID_DISCOUNT_CODE", "").strip()
    plan = os.environ.get("TEST_VALID_DISCOUNT_PLAN", "professional").strip() or "professional"
    if not code:
        REPORTER.warn(f"{name} -> skipped (TEST_VALID_DISCOUNT_CODE not configured)")
        REPORTER.record(name, True, "skipped: TEST_VALID_DISCOUNT_CODE not configured")
        return

    response = client.post(
        "/api/v1/billing/validate-discount",
        json_data={"code": code, "plan": plan},
    )
    if response.status_code != 200:
        REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return

    payload = response.json()
    required = ["valid", "code", "discount_type", "discount_value", "applies_to_plan"]
    missing = [key for key in required if key not in payload]
    if payload.get("valid") is True and not missing:
        REPORTER.ok(f"{name} -> code={payload.get('code')}, plan={payload.get('applies_to_plan')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing keys: {missing} or invalid payload {payload}")
    REPORTER.record(name, False, str(payload))


def main() -> None:
    REPORTER.print_heading("BILLING FEATURE LIVE SUITE", server=CONFIG.base_url, user=CONFIG.email)

    test_validate_discount_auth_gate()
    test_validate_discount_missing_code()
    test_validate_discount_invalid_code()
    test_validate_discount_happy_path()

    sys.exit(0 if REPORTER.summary("BILLING FEATURE SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

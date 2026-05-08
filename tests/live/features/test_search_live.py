"""
Live HTTP suite for the search feature surface.

Run directly:
    python tests/live/features/test_search_live.py
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
from tests.live.helpers.cleanup import reset_daily_quick_search_usage
from tests.live.helpers.config import PNG_1X1, load_live_config
from tests.live.helpers.personas import (
    PAID_PLANS,
    PersonaSession,
    canonical_plan_name,
    fetch_authenticated_json,
    resolve_free_persona_session,
    resolve_plan_persona_session,
)
from utils.subscription import PLAN_FEATURES


CONFIG = load_live_config()
REPORTER = LiveReporter()
DEFAULT_CLIENT: LiveClient | None = None
FREE_SESSION: PersonaSession | None = None
FREE_SESSION_RESOLVED = False
PAID_SESSION: PersonaSession | None = None
PAID_SESSION_SKIPPED = False
FREE_QUICK_SEARCH_LIMIT: int | None = None
pytestmark = pytest.mark.skip(reason="Live feature script; run directly with python tests/live/features/test_search_live.py")


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


def ensure_default_client() -> LiveClient | None:
    global DEFAULT_CLIENT
    if DEFAULT_CLIENT is not None:
        return DEFAULT_CLIENT

    client = LiveClient(CONFIG)
    if not login_user(client, REPORTER, CONFIG.email, CONFIG.password, name="search feature login"):
        return None

    DEFAULT_CLIENT = client
    return DEFAULT_CLIENT


def ensure_free_session() -> PersonaSession | None:
    global FREE_SESSION
    global FREE_SESSION_RESOLVED
    if FREE_SESSION is None and not FREE_SESSION_RESOLVED:
        FREE_SESSION_RESOLVED = True
        FREE_SESSION = resolve_free_persona_session(
            REPORTER,
            label="search free user",
            email_env="TEST_SEARCH_FREE_EMAIL",
            password_env="TEST_SEARCH_FREE_PASSWORD",
        )
    return FREE_SESSION


def ensure_paid_session() -> PersonaSession | None:
    global PAID_SESSION
    global PAID_SESSION_SKIPPED
    if PAID_SESSION is None and not PAID_SESSION_SKIPPED:
        PAID_SESSION, PAID_SESSION_SKIPPED = resolve_plan_persona_session(
            REPORTER,
            label="search paid user",
            email_env="TEST_SEARCH_PAID_EMAIL",
            password_env="TEST_SEARCH_PAID_PASSWORD",
            required_plans=PAID_PLANS,
            fallback_to_default=False,
            provision_plan="starter",
        )
    return PAID_SESSION


def test_quick_search_auth_gate():
    name = "GET /api/v1/search/quick requires auth"
    response = LiveClient(CONFIG).get("/api/v1/search/quick", params={"query": "wosen"}, token=False)
    if response.status_code in (401, 403):
        REPORTER.ok(f"{name} -> {response.status_code}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 401/403, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_public_search_happy_path():
    name = "GET /api/v1/search/public"
    response = LiveClient(CONFIG).get("/api/v1/search/public", params={"query": "wosen"}, token=False)
    if response.status_code == 429:
        REPORTER.warn(f"{name} -> skipped (public-search limiter already consumed by earlier public-suite checks)")
        REPORTER.record(name, True, "skipped: public-search rate limited")
        return
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


def test_public_search_missing_query_validation():
    name = "GET /api/v1/search/public missing query"
    response = LiveClient(CONFIG).get("/api/v1/search/public", token=False)
    if response.status_code == 422:
        REPORTER.ok(f"{name} -> 422 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 422, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_quick_search_missing_query_validation():
    client = ensure_default_client()
    if client is None:
        return

    name = "GET /api/v1/search/quick missing query"
    response = client.get("/api/v1/search/quick")
    if response.status_code == 422:
        REPORTER.ok(f"{name} -> 422 as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected 422, got {response.status_code}: {response.text[:200]}")
    REPORTER.record(name, False, response.text[:200])


def test_quick_search_text_happy_path():
    client = ensure_default_client()
    if client is None:
        return

    name = "GET /api/v1/search/quick"
    response = client.get("/api/v1/search/quick", params={"query": "wosen"})
    if _record_daily_limit_skip(name, response):
        return
    if response.status_code != 200:
        REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return

    payload = response.json()
    results = payload.get("results")
    if isinstance(results, list):
        REPORTER.ok(f"{name} -> results={len(results)}, source={payload.get('source')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> results is not a list")
    REPORTER.record(name, False, "results is not a list")


def test_quick_search_class_filter():
    client = ensure_default_client()
    if client is None:
        return

    name = "GET /api/v1/search/quick with classes"
    response = client.get("/api/v1/search/quick", params={"query": "wosen", "classes": "9,35"})
    if _record_daily_limit_skip(name, response):
        return
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


def test_quick_search_image_happy_path():
    client = ensure_default_client()
    if client is None:
        return

    name = "POST /api/v1/search/quick with image"
    files = {"image": ("search-test.png", io.BytesIO(PNG_1X1), "image/png")}
    response = client.post(
        "/api/v1/search/quick",
        data={"query": "wosen", "classes": "9,35"},
        files=files,
    )
    if _record_daily_limit_skip(name, response):
        return
    if response.status_code != 200:
        REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return

    payload = response.json()
    if isinstance(payload.get("results"), list):
        REPORTER.ok(f"{name} -> results={len(payload.get('results', []))}, image_used={payload.get('image_used')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> results is not a list")
    REPORTER.record(name, False, "results is not a list")


def test_search_credits_shape():
    client = ensure_default_client()
    if client is None:
        return

    name = "GET /api/v1/search/credits"
    payload = fetch_authenticated_json(client, REPORTER, "/api/v1/search/credits", name=name)
    if payload is None:
        return

    required = ["plan", "display_name", "can_use_live_search", "remaining"]
    missing = [key for key in required if key not in payload]
    if not missing:
        REPORTER.ok(f"{name} -> plan={payload.get('plan')}, remaining={payload.get('remaining')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> missing keys: {missing}")
    REPORTER.record(name, False, f"missing keys: {missing}")


def test_free_plan_live_search_gate():
    session = ensure_free_session()
    if session is None:
        return

    name = "GET /api/v1/search/credits (free gate)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/search/credits", name=name)
    if payload is None:
        return

    if payload.get("can_use_live_search") is False and payload.get("remaining") == 0:
        REPORTER.ok(f"{name} -> blocked as expected")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected free gate, got {payload}")
    REPORTER.record(name, False, str(payload))


def test_free_quick_search_usage_shape():
    global FREE_QUICK_SEARCH_LIMIT

    session = ensure_free_session()
    if session is None:
        return

    reset_daily_quick_search_usage(
        REPORTER,
        session.user_id,
        name="RESET free quick-search usage before live limit check",
    )

    name = "GET /api/v1/usage/summary (free quick search limit)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/usage/summary", name=name)
    if payload is None:
        return

    plan_name = canonical_plan_name(payload.get("plan"))
    quick_usage = payload.get("usage", {}).get("daily_quick_searches", {})
    used = quick_usage.get("used")
    limit = quick_usage.get("limit")
    expected_limit = PLAN_FEATURES["free"]["max_daily_quick_searches"]
    if plan_name == "free" and limit == expected_limit and used == 0:
        FREE_QUICK_SEARCH_LIMIT = limit
        REPORTER.ok(f"{name} -> plan=free, used={used}, limit={limit}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected free used=0, limit={expected_limit}, got {payload}")
    REPORTER.record(name, False, str(payload))


def test_free_quick_search_text_happy_path():
    session = ensure_free_session()
    if session is None:
        return

    name = "GET /api/v1/search/quick (free text search)"
    response = session.client.get("/api/v1/search/quick", params={"query": "wosen"})
    if _record_daily_limit_skip(name, response):
        return
    if response.status_code != 200:
        REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return

    payload = response.json()
    results = payload.get("results")
    if isinstance(results, list):
        REPORTER.ok(f"{name} -> results={len(results)}, source={payload.get('source')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> results is not a list")
    REPORTER.record(name, False, "results is not a list")


def test_paid_quick_search_usage_shape():
    session = ensure_paid_session()
    if session is None:
        return

    name = "GET /api/v1/usage/summary (paid quick search limit)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/usage/summary", name=name)
    if payload is None:
        return

    plan_name = canonical_plan_name(payload.get("plan"))
    quick_usage = payload.get("usage", {}).get("daily_quick_searches", {})
    used = quick_usage.get("used")
    limit = quick_usage.get("limit")
    expected_limit = PLAN_FEATURES[plan_name]["max_daily_quick_searches"] if plan_name in PLAN_FEATURES else None
    if (
        plan_name in PAID_PLANS
        and isinstance(used, int)
        and isinstance(limit, int)
        and expected_limit is not None
        and limit == expected_limit
        and (FREE_QUICK_SEARCH_LIMIT is None or limit > FREE_QUICK_SEARCH_LIMIT)
    ):
        REPORTER.ok(f"{name} -> plan={plan_name}, used={used}, limit={limit}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(
        f"{name} -> expected paid quick-search limit {expected_limit} on plan {plan_name} "
        f"and greater than free limit {FREE_QUICK_SEARCH_LIMIT}, got {payload}"
    )
    REPORTER.record(name, False, str(payload))


def test_free_quick_search_daily_limit_gate():
    session = ensure_free_session()
    if session is None:
        return

    reset_daily_quick_search_usage(
        REPORTER,
        session.user_id,
        name="RESET free quick-search usage before live limit gate",
    )

    name = "GET /api/v1/search/quick (free daily limit gate)"
    try:
        expected_limit = PLAN_FEATURES["free"]["max_daily_quick_searches"]
        for attempt in range(expected_limit + 1):
            response = session.client.get("/api/v1/search/quick", params={"query": "wosen"})
            if attempt < expected_limit:
                if response.status_code != 200:
                    REPORTER.fail(f"{name} -> expected 200 before free limit, got {response.status_code}: {response.text[:200]}")
                    REPORTER.record(name, False, response.text[:200])
                    return
                continue

            if response.status_code != 429:
                REPORTER.fail(f"{name} -> expected 429 on search {attempt + 1}, got {response.status_code}: {response.text[:200]}")
                REPORTER.record(name, False, response.text[:200])
                return

            detail = response.json().get("detail", {})
            if (
                isinstance(detail, dict)
                and detail.get("error") == "daily_limit_exceeded"
                and canonical_plan_name(detail.get("current_plan")) == "free"
                and detail.get("daily_limit") == expected_limit
                and detail.get("remaining") == 0
            ):
                REPORTER.ok(f"{name} -> blocked on attempt {attempt + 1} with daily_limit={expected_limit}")
                REPORTER.record(name, True)
                return

            REPORTER.fail(f"{name} -> unexpected limit detail {detail}")
            REPORTER.record(name, False, str(detail))
            return
    finally:
        reset_daily_quick_search_usage(
            REPORTER,
            session.user_id,
            name="RESET free quick-search usage after live limit gate",
        )


def test_paid_quick_search_text_happy_path():
    session = ensure_paid_session()
    if session is None:
        return

    name = "GET /api/v1/search/quick (paid text search)"
    response = session.client.get("/api/v1/search/quick", params={"query": "wosen"})
    if _record_daily_limit_skip(name, response):
        return
    if response.status_code != 200:
        REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return

    payload = response.json()
    results = payload.get("results")
    if isinstance(results, list):
        REPORTER.ok(f"{name} -> results={len(results)}, source={payload.get('source')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> results is not a list")
    REPORTER.record(name, False, "results is not a list")


def test_paid_quick_search_image_happy_path():
    session = ensure_paid_session()
    if session is None:
        return

    name = "POST /api/v1/search/quick (paid image search)"
    files = {"image": ("search-test.png", io.BytesIO(PNG_1X1), "image/png")}
    response = session.client.post(
        "/api/v1/search/quick",
        data={"query": "wosen", "classes": "9,35"},
        files=files,
    )
    if _record_daily_limit_skip(name, response):
        return
    if response.status_code != 200:
        REPORTER.fail(f"{name} -> expected 200, got {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return

    payload = response.json()
    results = payload.get("results")
    if isinstance(results, list) and payload.get("image_used") is True:
        REPORTER.ok(f"{name} -> results={len(results)}, image_used={payload.get('image_used')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected image_used=true with list results, got {payload}")
    REPORTER.record(name, False, str(payload))


def main() -> None:
    REPORTER.print_heading("SEARCH FEATURE LIVE SUITE", server=CONFIG.base_url, user=CONFIG.email)

    test_quick_search_auth_gate()
    test_public_search_happy_path()
    test_public_search_missing_query_validation()
    test_quick_search_missing_query_validation()
    test_quick_search_text_happy_path()
    test_quick_search_class_filter()
    test_quick_search_image_happy_path()
    test_search_credits_shape()
    test_free_plan_live_search_gate()
    test_free_quick_search_usage_shape()
    test_free_quick_search_text_happy_path()
    test_free_quick_search_daily_limit_gate()
    test_paid_quick_search_usage_shape()
    test_paid_quick_search_text_happy_path()
    test_paid_quick_search_image_happy_path()

    sys.exit(0 if REPORTER.summary("SEARCH FEATURE SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

"""
Live HTTP suite for the business/professional persona.

Run directly:
    python tests/live/personas/test_business_user_live.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.personas import (
    BUSINESS_PLANS,
    PersonaSession,
    canonical_plan_name,
    fetch_authenticated_json,
    resolve_plan_persona_session,
)


REPORTER = LiveReporter()
SESSION: PersonaSession | None = None
SESSION_SKIPPED = False
HOLDER_ID: str | None = None
ATTORNEY_NO: str | None = None
pytestmark = pytest.mark.skip(reason="Live persona script; run directly with python tests/live/personas/test_business_user_live.py")


def ensure_session() -> PersonaSession | None:
    global SESSION
    global SESSION_SKIPPED
    if SESSION is None and not SESSION_SKIPPED:
        SESSION, SESSION_SKIPPED = resolve_plan_persona_session(
            REPORTER,
            label="business user",
            email_env="TEST_BUSINESS_EMAIL",
            password_env="TEST_BUSINESS_PASSWORD",
            required_plans=BUSINESS_PLANS,
            fallback_to_default=False,
            provision_plan="professional",
        )
    return SESSION


def test_usage_summary_business():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/usage/summary (business plan)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/usage/summary", name=name)
    if payload is None:
        return

    plan_name = canonical_plan_name(payload.get("plan"))
    if plan_name in BUSINESS_PLANS:
        REPORTER.ok(f"{name} -> plan={plan_name}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected one of {sorted(BUSINESS_PLANS)}, got {plan_name}")
    REPORTER.record(name, False, str(payload))


def test_lead_credits_enabled():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/leads/credits (business lead access)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/leads/credits", name=name)
    if payload is None:
        return

    if payload.get("can_access") is True and int(payload.get("daily_limit", 0)) > 0:
        REPORTER.ok(f"{name} -> daily_limit={payload.get('daily_limit')}, remaining={payload.get('remaining')}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected lead access, got {payload}")
    REPORTER.record(name, False, str(payload))


def test_search_credits_enabled():
    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/search/credits (business live-search access)"
    payload = fetch_authenticated_json(session.client, REPORTER, "/api/v1/search/credits", name=name)
    if payload is None:
        return

    if payload.get("can_use_live_search") is True and int(payload.get("monthly_limit", 0)) > 0:
        REPORTER.ok(
            f"{name} -> monthly_limit={payload.get('monthly_limit')}, remaining={payload.get('remaining')}"
        )
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> expected live-search access, got {payload}")
    REPORTER.record(name, False, str(payload))


def test_holder_search():
    global HOLDER_ID

    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/holders/search (business portfolio access)"
    payload = fetch_authenticated_json(
        session.client,
        REPORTER,
        "/api/v1/holders/search",
        name=name,
        params={"query": "te"},
    )
    if payload is None:
        return

    results = payload.get("results")
    if not isinstance(results, list):
        REPORTER.fail(f"{name} -> results is not a list")
        REPORTER.record(name, False, "results is not a list")
        return

    if results:
        HOLDER_ID = results[0].get("holder_tpe_client_id")
    REPORTER.ok(f"{name} -> results={len(results)}")
    REPORTER.record(name, True)


def test_holder_portfolio_page():
    session = ensure_session()
    if session is None or not HOLDER_ID:
        return

    name = "GET /api/v1/holders/{id}/trademarks (business portfolio page)"
    payload = fetch_authenticated_json(
        session.client,
        REPORTER,
        f"/api/v1/holders/{HOLDER_ID}/trademarks",
        name=name,
        params={"page": 1, "page_size": 5},
    )
    if payload is None:
        return

    trademarks = payload.get("trademarks")
    if isinstance(trademarks, list):
        REPORTER.ok(f"{name} -> total_count={payload.get('total_count')}, page_items={len(trademarks)}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> trademarks is not a list")
    REPORTER.record(name, False, "trademarks is not a list")


def test_attorney_search():
    global ATTORNEY_NO

    session = ensure_session()
    if session is None:
        return

    name = "GET /api/v1/attorneys/search (business portfolio access)"
    payload = fetch_authenticated_json(
        session.client,
        REPORTER,
        "/api/v1/attorneys/search",
        name=name,
        params={"query": "pa"},
    )
    if payload is None:
        return

    results = payload.get("results")
    if not isinstance(results, list):
        REPORTER.fail(f"{name} -> results is not a list")
        REPORTER.record(name, False, "results is not a list")
        return

    if results:
        ATTORNEY_NO = results[0].get("attorney_no")
    REPORTER.ok(f"{name} -> results={len(results)}")
    REPORTER.record(name, True)


def test_attorney_portfolio_page():
    session = ensure_session()
    if session is None or not ATTORNEY_NO:
        return

    name = "GET /api/v1/attorneys/{id}/trademarks (business portfolio page)"
    payload = fetch_authenticated_json(
        session.client,
        REPORTER,
        f"/api/v1/attorneys/{ATTORNEY_NO}/trademarks",
        name=name,
        params={"page": 1, "page_size": 5},
    )
    if payload is None:
        return

    trademarks = payload.get("trademarks")
    if isinstance(trademarks, list):
        REPORTER.ok(f"{name} -> total_count={payload.get('total_count')}, page_items={len(trademarks)}")
        REPORTER.record(name, True)
        return

    REPORTER.fail(f"{name} -> trademarks is not a list")
    REPORTER.record(name, False, "trademarks is not a list")


def main() -> None:
    REPORTER.print_heading("BUSINESS USER PERSONA LIVE SUITE")

    test_usage_summary_business()
    test_lead_credits_enabled()
    test_search_credits_enabled()
    test_holder_search()
    test_holder_portfolio_page()
    test_attorney_search()
    test_attorney_portfolio_page()

    sys.exit(0 if REPORTER.summary("BUSINESS USER PERSONA SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()

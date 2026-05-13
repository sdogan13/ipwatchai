"""Usage summary payload shape after Quick Search removal.

Validates the dict returned by ``services.usage_service.get_usage_summary_data``
— the response surface that backs ``GET /api/v1/users/usage``:

  * ``usage.daily_live_searches`` is present as ``{used, limit}``.
  * Legacy keys are gone: no ``usage.daily_quick_searches``, no
    ``usage.monthly_live_searches``.
  * ``limit`` reflects the user's plan's ``max_daily_live_searches``.

All collaborators (DB, plan getter, usage getter, eligibility checkers) are
injected, so this test is offline.
"""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

from services.usage_service import get_usage_summary_data


def _stub_user():
    return SimpleNamespace(id="user-uuid", organization_id="org-uuid", is_superadmin=False)


@contextmanager
def _stub_db_context():
    cursor = MagicMock()
    # First fetchone: watchlist count. Second fetchone: AI cost-weighted sum.
    cursor.fetchone.side_effect = [{"cnt": 2}, {"ai_used": 0}]
    db = MagicMock()
    db.cursor.return_value = cursor
    yield db


def _stub_db_factory():
    def _factory():
        return _stub_db_context()
    return _factory


_BASE_KWARGS = dict(
    ai_credit_eligibility_checker=lambda db, oid, cost=1: (True, "ok", {"total_remaining": 0}),
    report_eligibility_checker=lambda db, plan, oid: {
        "eligible": True,
        "reports_used": 0,
        "reports_limit": 1,
        "reports_remaining": 1,
        "saved_reports": 0,
        "inline_reports": 0,
    },
    monthly_name_generations_getter=lambda db, oid: 0,
    monthly_applications_getter=lambda db, oid: 0,
)


def _run(plan_name, daily_limit, used_today):
    """Invoke the service with overrides for the search-relevant collaborators."""
    plan_limits = {
        "max_daily_live_searches": daily_limit,
        "monthly_ai_credits": 0,
        "monthly_applications": 0,
        "can_track_logos": False,
        "max_watchlist_items": 3,
    }
    return asyncio.run(get_usage_summary_data(
        current_user=_stub_user(),
        database_factory=_stub_db_factory(),
        user_plan_getter=lambda db, uid: {
            "plan_name": plan_name,
            "display_name": plan_name.capitalize(),
            "can_use_live_search": daily_limit > 0,
            "daily_limit": daily_limit,
        },
        plan_limit_getter=lambda plan, feat: plan_limits.get(feat, 0),
        daily_live_search_usage_getter=lambda db, uid: used_today,
        **_BASE_KWARGS,
    ))


def test_payload_includes_daily_live_searches_key():
    result = _run("free", daily_limit=5, used_today=2)
    assert "daily_live_searches" in result["usage"]


def test_daily_live_searches_carries_used_and_limit():
    result = _run("free", daily_limit=5, used_today=2)
    assert result["usage"]["daily_live_searches"] == {"used": 2, "limit": 5}


def test_payload_drops_legacy_daily_quick_searches():
    result = _run("free", daily_limit=5, used_today=0)
    assert "daily_quick_searches" not in result["usage"]


def test_payload_drops_legacy_monthly_live_searches():
    result = _run("free", daily_limit=5, used_today=0)
    assert "monthly_live_searches" not in result["usage"]


def test_professional_payload_shows_2000_limit():
    result = _run("professional", daily_limit=2000, used_today=37)
    assert result["usage"]["daily_live_searches"] == {"used": 37, "limit": 2000}


def test_unlimited_plan_keeps_999999_sentinel():
    """Enterprise gets the unlimited sentinel; frontend converts to '∞'."""
    result = _run("enterprise", daily_limit=999999, used_today=0)
    assert result["usage"]["daily_live_searches"]["limit"] == 999999


def test_plan_metadata_passes_through():
    result = _run("starter", daily_limit=50, used_today=10)
    assert result["plan"] == "starter"
    assert result["display_name"] == "Starter"

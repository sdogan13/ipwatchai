"""Dashboard `searches_this_month` aggregation after Quick Search removal.

Verifies:
  * The aggregation reads only ``api_usage.live_searches`` — not the dropped
    ``quick_searches`` column.
  * ``plan_usage.searches.limit`` reflects the user's plan's
    ``max_daily_live_searches`` (the unified daily Agentic Search budget).

Mocks the DB to capture and assert on the issued SQL.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock
import asyncio

from services.dashboard_service import get_dashboard_stats_data


_FETCHONE_RESULTS = [
    {"total": 10, "active": 7},                                  # watchlist
    {"total": 3, "new": 1, "critical": 0, "this_week": 1},       # alerts
    {"active_deadlines": 2, "pre_publication": 1},               # deadlines
    {"cnt": 42},                                                 # searches_this_month
    {"cnt": 1},                                                  # user count
]


def _stub_user():
    return SimpleNamespace(id="user-uuid", organization_id="org-uuid")


@contextmanager
def _stub_db_context(captured_sql):
    cursor = MagicMock()
    cursor.fetchone.side_effect = list(_FETCHONE_RESULTS)

    def _capture_execute(sql, *args, **kwargs):
        captured_sql.append(sql)
        return None

    cursor.execute.side_effect = _capture_execute
    db = MagicMock()
    db.cursor.return_value = cursor
    yield db


def _stub_db_factory(captured_sql):
    def _factory():
        return _stub_db_context(captured_sql)
    return _factory


def _run(daily_limit):
    captured_sql = []
    result = asyncio.run(get_dashboard_stats_data(
        current_user=_stub_user(),
        database_factory=_stub_db_factory(captured_sql),
        user_plan_getter=lambda db, uid: {
            "plan_name": "professional",
            "display_name": "Professional",
            "can_use_live_search": True,
            "daily_limit": daily_limit,
        },
        plan_limit_getter=lambda plan, feat: {
            "max_watchlist_items": 1000,
            "max_users": 10,
            "max_daily_live_searches": daily_limit,
            "monthly_reports": 30,
        }.get(feat, 0),
        report_eligibility_checker=lambda db, plan, oid: {
            "eligible": True,
            "reports_used": 0,
            "reports_limit": 30,
            "reports_remaining": 30,
            "saved_reports": 0,
            "inline_reports": 0,
        },
    ))
    return result, captured_sql


def test_searches_this_month_returns_aggregated_count():
    result, _ = _run(daily_limit=2000)
    assert result.searches_this_month == 42


def test_searches_aggregation_sql_reads_only_live_searches():
    """The SUM must hit only `live_searches` — not the dropped `quick_searches` column."""
    _, sqls = _run(daily_limit=2000)
    search_sql = next((s for s in sqls if "SUM(" in s and "live_searches" in s), None)
    assert search_sql is not None, f"no SUM(live_searches) query found in: {sqls}"
    assert "quick_searches" not in search_sql, "aggregation still references dropped quick_searches column"


def test_plan_usage_searches_limit_is_daily_live_searches():
    """The `plan_usage.searches.limit` field is the daily Agentic budget."""
    result, _ = _run(daily_limit=2000)
    assert result.plan_usage["searches"]["limit"] == 2000


def test_unlimited_plan_passes_through_999999_sentinel():
    result, _ = _run(daily_limit=999999)
    assert result.plan_usage["searches"]["limit"] == 999999

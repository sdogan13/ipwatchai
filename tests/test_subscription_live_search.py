"""Eligibility helpers for Agentic Search (post-Quick-removal).

Validates the daily-only Agentic Search surface on `main`:

  * ``get_user_plan(db, user_id)`` returns ``daily_limit`` (not ``monthly_limit``)
    and derives ``can_use_live_search`` from ``daily_limit > 0``.
  * ``get_daily_live_search_usage(db, user_id)`` reads ``api_usage.live_searches``
    for today only — not month-to-date.
  * ``increment_live_search_usage`` upserts on ``(user_id, usage_date)`` and
    returns today's new count.
  * ``check_live_search_eligibility`` returns ``(True, "ok", …)`` under limit,
    ``(False, "daily_limit_exceeded", …)`` at/above limit. There is NO
    ``upgrade_required`` reason anymore — every plan has ``daily_limit > 0``.

All scenarios mock the DB; no real connection required.
"""
import uuid
from unittest.mock import MagicMock, patch

from utils.subscription import (
    PLAN_FEATURES,
    check_live_search_eligibility,
    get_daily_live_search_usage,
    get_user_plan,
    increment_live_search_usage,
)


def _make_db(fetchone_return=None):
    db = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone_return
    db.cursor.return_value = cursor
    db.commit = MagicMock()
    return db, cursor


# ===========================================================================
# get_user_plan
# ===========================================================================

class TestGetUserPlan:
    def test_professional_user_gets_daily_limit_2000(self):
        db, _ = _make_db({
            "plan_name": "professional",
            "display_name": "Professional",
            "is_superadmin": False,
            "subscription_end_date": None,
        })
        result = get_user_plan(db, str(uuid.uuid4()))
        assert result["plan_name"] == "professional"
        assert result["daily_limit"] == 2000
        assert result["can_use_live_search"] is True

    def test_no_row_defaults_to_free_with_5_daily(self):
        db, _ = _make_db(None)
        result = get_user_plan(db, str(uuid.uuid4()))
        assert result["plan_name"] == "free"
        assert result["daily_limit"] == 5
        # Free now has live search access (post-removal).
        assert result["can_use_live_search"] is True

    def test_legacy_monthly_limit_key_is_not_in_response(self):
        db, _ = _make_db({
            "plan_name": "starter",
            "display_name": "Starter",
            "is_superadmin": False,
            "subscription_end_date": None,
        })
        result = get_user_plan(db, str(uuid.uuid4()))
        assert "monthly_limit" not in result

    def test_superadmin_overrides_db_plan(self):
        db, _ = _make_db({
            "plan_name": "free",
            "display_name": "Free",
            "is_superadmin": True,
            "subscription_end_date": None,
        })
        result = get_user_plan(db, str(uuid.uuid4()))
        assert result["plan_name"] == "superadmin"
        assert result["daily_limit"] == 999999


# ===========================================================================
# get_daily_live_search_usage
# ===========================================================================

class TestGetDailyLiveSearchUsage:
    def test_reads_today_only_not_month_to_date(self):
        db, cursor = _make_db({"total": 7})
        result = get_daily_live_search_usage(db, "user-uuid")
        assert result == 7
        executed_sql = cursor.execute.call_args[0][0]
        # Must be a single-day query, not SUM-since-month-start
        assert "usage_date = %s" in executed_sql
        assert "month" not in executed_sql.lower()
        assert "SUM(" not in executed_sql

    def test_returns_zero_when_no_row(self):
        db, _ = _make_db(None)
        assert get_daily_live_search_usage(db, "user-uuid") == 0


# ===========================================================================
# increment_live_search_usage
# ===========================================================================

class TestIncrementLiveSearchUsage:
    def test_upsert_returns_new_today_count(self):
        db, cursor = _make_db({"live_searches": 4})
        result = increment_live_search_usage(db, "user-uuid", "org-uuid")
        assert result == 4
        executed_sql = cursor.execute.call_args[0][0]
        assert "INSERT INTO api_usage" in executed_sql
        assert "ON CONFLICT (user_id, usage_date)" in executed_sql
        assert "live_searches = api_usage.live_searches + 1" in executed_sql
        db.commit.assert_called_once()

    def test_no_quick_searches_column_referenced(self):
        """Regression: the upsert must not touch the dropped `quick_searches` column."""
        db, cursor = _make_db({"live_searches": 1})
        increment_live_search_usage(db, "user-uuid")
        executed_sql = cursor.execute.call_args[0][0]
        assert "quick_searches" not in executed_sql


# ===========================================================================
# check_live_search_eligibility
# ===========================================================================

class TestCheckLiveSearchEligibility:
    @patch("utils.subscription.get_daily_live_search_usage", return_value=0)
    @patch("utils.subscription.get_user_plan")
    def test_eligible_fresh_user(self, mock_plan, mock_usage):
        mock_plan.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
            "can_use_live_search": True,
            "daily_limit": 2000,
        }
        can, reason, details = check_live_search_eligibility(MagicMock(), "u1")
        assert can is True
        assert reason == "ok"
        assert details["remaining"] == 2000
        assert details["used_today"] == 0
        assert details["daily_limit"] == 2000

    @patch("utils.subscription.get_daily_live_search_usage", return_value=2000)
    @patch("utils.subscription.get_user_plan")
    def test_daily_limit_exceeded_at_exact_limit(self, mock_plan, mock_usage):
        mock_plan.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
            "can_use_live_search": True,
            "daily_limit": 2000,
        }
        can, reason, details = check_live_search_eligibility(MagicMock(), "u1")
        assert can is False
        assert reason == "daily_limit_exceeded"
        assert details["error"] == "daily_limit_exceeded"
        assert details["remaining"] == 0
        assert details["used_today"] == 2000
        # Bilingual messages preserved for upgrade-modal UX
        assert "message" in details and "message_en" in details

    @patch("utils.subscription.get_daily_live_search_usage", return_value=5)
    @patch("utils.subscription.get_user_plan")
    def test_free_user_blocked_after_5_searches(self, mock_plan, mock_usage):
        mock_plan.return_value = {
            "plan_name": "free",
            "display_name": "Free Trial",
            "can_use_live_search": True,
            "daily_limit": 5,
        }
        can, reason, details = check_live_search_eligibility(MagicMock(), "u1")
        assert can is False
        assert reason == "daily_limit_exceeded"

    @patch("utils.subscription.get_daily_live_search_usage", return_value=4)
    @patch("utils.subscription.get_user_plan")
    def test_free_user_has_1_remaining_at_4_used(self, mock_plan, mock_usage):
        mock_plan.return_value = {
            "plan_name": "free",
            "display_name": "Free Trial",
            "can_use_live_search": True,
            "daily_limit": 5,
        }
        can, reason, details = check_live_search_eligibility(MagicMock(), "u1")
        assert can is True
        assert details["remaining"] == 1

    def test_no_upgrade_required_path_is_reachable(self):
        """Post-removal there is no plan with daily_limit == 0, so
        ``check_live_search_eligibility`` never returns 'upgrade_required'."""
        for plan_name in PLAN_FEATURES:
            assert PLAN_FEATURES[plan_name]["max_daily_live_searches"] > 0, (
                f"plan {plan_name} would trigger upgrade_required path"
            )

"""
Tests for subscription eligibility checks with mocked DB.

Covers:
- get_user_plan() with mocked cursor
- check_live_search_eligibility()
- check_quick_search_eligibility()
- check_name_generation_eligibility()
- check_logo_generation_eligibility()
- check_report_eligibility()
- deduct_name_credit(), deduct_logo_credit(), refund_logo_credit()
"""
import sys
import os
import uuid
from unittest.mock import patch, MagicMock
from datetime import date

import pytest


from utils.subscription import (
    PLAN_FEATURES,
    get_plan_limit,
    get_user_plan,
    check_live_search_eligibility,
    check_quick_search_eligibility,
    check_name_generation_eligibility,
    check_logo_generation_eligibility,
    check_report_eligibility,
    decrement_report_usage,
    deduct_name_credit,
    deduct_logo_credit,
    increment_report_usage,
    refund_logo_credit,
)


def _make_db(fetchone_return=None):
    """Create a mock db with cursor that returns fetchone_return."""
    db = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone_return
    db.cursor.return_value = cursor
    db.commit = MagicMock()
    return db, cursor


# ============================================================
# get_user_plan
# ============================================================

class TestGetUserPlan:
    def test_returns_plan_dict(self):
        db, cursor = _make_db({
            "plan_name": "professional",
            "display_name": "Professional",
            "can_use_live_search": True,
        })
        result = get_user_plan(db, str(uuid.uuid4()))
        assert result["plan_name"] == "professional"
        assert result["can_use_live_search"] is True
        assert "monthly_limit" in result

    def test_no_row_defaults_to_free(self):
        db, cursor = _make_db(None)
        result = get_user_plan(db, str(uuid.uuid4()))
        assert result["plan_name"] == "free"
        assert result["can_use_live_search"] is False
        assert result["monthly_limit"] == 0

    @patch("utils.subscription.get_plan_limit")
    def test_uses_canonical_live_search_limits_even_if_db_flag_is_stale(self, mock_get_plan_limit):
        db, cursor = _make_db({
            "plan_name": "starter",
            "display_name": "Starter",
            "can_use_live_search": False,
            "is_superadmin": False,
            "subscription_end_date": None,
        })
        mock_get_plan_limit.side_effect = lambda plan_name, feature: {
            "monthly_live_searches": 10,
            "can_use_live_scraping": True,
        }[feature]

        result = get_user_plan(db, str(uuid.uuid4()))

        assert result["plan_name"] == "starter"
        assert result["can_use_live_search"] is True
        assert result["monthly_limit"] == 10


# ============================================================
# check_live_search_eligibility
# ============================================================

class TestCheckLiveSearchEligibility:
    @patch("utils.subscription.get_live_search_usage", return_value=0)
    @patch("utils.subscription.get_user_plan")
    def test_eligible_professional(self, mock_plan, mock_usage):
        mock_plan.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
            "can_use_live_search": True,
            "monthly_limit": 50,
        }
        db = MagicMock()
        can, reason, details = check_live_search_eligibility(db, "user1")
        assert can is True
        assert reason == "ok"
        assert details["remaining"] == 50

    @patch("utils.subscription.get_user_plan")
    def test_free_plan_denied(self, mock_plan):
        mock_plan.return_value = {
            "plan_name": "free",
            "display_name": "Free Trial",
            "can_use_live_search": False,
            "monthly_limit": 0,
        }
        db = MagicMock()
        can, reason, details = check_live_search_eligibility(db, "user1")
        assert can is False
        assert reason == "upgrade_required"

    @patch("utils.subscription.get_live_search_usage", return_value=50)
    @patch("utils.subscription.get_user_plan")
    def test_limit_exceeded(self, mock_plan, mock_usage):
        mock_plan.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
            "can_use_live_search": True,
            "monthly_limit": 50,
        }
        db = MagicMock()
        can, reason, details = check_live_search_eligibility(db, "user1")
        assert can is False
        assert reason == "limit_exceeded"
        assert details["remaining"] == 0


# ============================================================
# check_quick_search_eligibility
# ============================================================

class TestCheckQuickSearchEligibility:
    @patch("utils.subscription.get_daily_quick_searches", return_value=0)
    @patch("utils.subscription.get_user_plan")
    def test_eligible(self, mock_plan, mock_usage):
        mock_plan.return_value = {
            "plan_name": "free",
            "display_name": "Free Trial",
            "can_use_live_search": False,
            "monthly_limit": 0,
        }
        db = MagicMock()
        can, reason, details = check_quick_search_eligibility(db, "user1")
        assert can is True
        assert reason == "ok"
        assert details["remaining"] == 5

    @patch("utils.subscription.get_daily_quick_searches", return_value=50)
    @patch("utils.subscription.get_user_plan")
    def test_daily_limit_exceeded(self, mock_plan, mock_usage):
        mock_plan.return_value = {
            "plan_name": "free",
            "display_name": "Free Trial",
            "can_use_live_search": False,
            "monthly_limit": 0,
        }
        db = MagicMock()
        can, reason, details = check_quick_search_eligibility(db, "user1")
        assert can is False
        assert reason == "daily_limit_exceeded"


# ============================================================
# check_name_generation_eligibility
# ============================================================

class TestCheckNameGenerationEligibility:
    @patch("utils.subscription.check_ai_credit_eligibility", return_value=(True, "ok", {"monthly_limit": 50, "total_remaining": 50}))
    @patch("utils.subscription.get_monthly_name_generations", return_value=0)
    @patch("utils.subscription.get_org_plan")
    def test_eligible_under_both_limits(self, mock_plan, mock_monthly, mock_ai):
        mock_plan.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
            "name_suggestions_per_session": 50,
            "logo_runs_per_month": 15,
        }
        db = MagicMock()
        can, reason, details = check_name_generation_eligibility(db, "org1", session_count=0)
        assert can is True
        assert reason == "ok"

    @patch("utils.subscription.get_monthly_name_generations", return_value=200)
    @patch("utils.subscription.get_org_plan")
    def test_monthly_limit_exceeded(self, mock_plan, mock_monthly):
        mock_plan.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
            "name_suggestions_per_session": 50,
            "logo_runs_per_month": 15,
        }
        db = MagicMock()
        can, reason, details = check_name_generation_eligibility(db, "org1", session_count=0)
        assert can is False
        assert reason == "monthly_limit_exceeded"

    @patch("utils.subscription.get_monthly_name_generations", return_value=0)
    @patch("utils.subscription.get_org_plan")
    def test_session_limit_exceeded_no_credits(self, mock_plan, mock_monthly):
        mock_plan.return_value = {
            "plan_name": "free",
            "display_name": "Free Trial",
            "name_suggestions_per_session": 5,
            "logo_runs_per_month": 1,
        }
        db, cursor = _make_db({"name_credits_purchased": 0})
        can, reason, details = check_name_generation_eligibility(db, "org1", session_count=5)
        assert can is False
        assert reason == "upgrade_required"

    @patch("utils.subscription.get_monthly_name_generations", return_value=0)
    @patch("utils.subscription.get_org_plan")
    def test_session_limit_exceeded_with_purchased_credits(self, mock_plan, mock_monthly):
        mock_plan.return_value = {
            "plan_name": "free",
            "display_name": "Free Trial",
            "name_suggestions_per_session": 5,
            "logo_runs_per_month": 1,
        }
        db, cursor = _make_db({"name_credits_purchased": 10})
        can, reason, details = check_name_generation_eligibility(db, "org1", session_count=5)
        assert can is True
        assert details.get("using_purchased_credits") is True

    @patch("utils.subscription.check_ai_credit_eligibility", return_value=(True, "ok", {"monthly_limit": 999999, "total_remaining": 999999}))
    @patch("utils.subscription.get_monthly_name_generations", return_value=0)
    @patch("utils.subscription.get_org_plan")
    def test_enterprise_unlimited_session(self, mock_plan, mock_monthly, mock_ai):
        mock_plan.return_value = {
            "plan_name": "enterprise",
            "display_name": "Enterprise",
            "name_suggestions_per_session": 999999,
            "logo_runs_per_month": 50,
        }
        db = MagicMock()
        can, reason, details = check_name_generation_eligibility(db, "org1", session_count=500)
        assert can is True


# ============================================================
# check_logo_generation_eligibility
# ============================================================

class TestCheckLogoGenerationEligibility:
    @patch("utils.subscription._reset_monthly_logo_credits_if_needed")
    @patch("utils.subscription.get_org_plan")
    def test_eligible_with_monthly_credits(self, mock_plan, mock_reset):
        mock_plan.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
        }
        db, cursor = _make_db({
            "logo_credits_monthly": 10,
            "logo_credits_purchased": 0,
        })
        can, reason, details = check_logo_generation_eligibility(db, "org1")
        assert can is True
        assert reason == "ok"
        assert details["total_remaining"] == 10

    @patch("utils.subscription._reset_monthly_logo_credits_if_needed")
    @patch("utils.subscription.get_org_plan")
    def test_exhausted_no_credits(self, mock_plan, mock_reset):
        mock_plan.return_value = {
            "plan_name": "free",
            "display_name": "Free Trial",
        }
        db, cursor = _make_db({
            "logo_credits_monthly": 0,
            "logo_credits_purchased": 0,
        })
        can, reason, details = check_logo_generation_eligibility(db, "org1")
        assert can is False
        assert reason == "credits_exhausted"

    @patch("utils.subscription._reset_monthly_logo_credits_if_needed")
    @patch("utils.subscription.get_org_plan")
    def test_org_not_found(self, mock_plan, mock_reset):
        mock_plan.return_value = {
            "plan_name": "free",
            "display_name": "Free Trial",
        }
        db, cursor = _make_db(None)
        can, reason, details = check_logo_generation_eligibility(db, "org1")
        assert can is False
        assert reason == "upgrade_required"


# ============================================================
# check_report_eligibility
# ============================================================

class TestCheckReportEligibility:
    def test_eligible(self):
        db, cursor = _make_db({"cnt": 0})
        result = check_report_eligibility(db, "free", "org1")
        assert result["eligible"] is True
        assert result["reports_limit"] == 1  # free plan

    def test_limit_reached(self):
        db, cursor = _make_db({"cnt": 1})
        result = check_report_eligibility(db, "free", "org1")
        assert result["eligible"] is False

    def test_counts_only_inline_risk_reports(self):
        db, cursor = _make_db({"saved_reports": 0, "inline_reports": 2, "cnt": 2})
        result = check_report_eligibility(db, "starter", "org1")
        assert result["eligible"] is True
        assert result["reports_used"] == 2
        assert result["saved_reports"] == 0
        assert result["inline_reports"] == 2

    def test_professional_higher_limit(self):
        db, cursor = _make_db({"cnt": 5})
        result = check_report_eligibility(db, "professional", "org1")
        assert result["eligible"] is True  # limit is 20

    def test_can_export_by_plan(self):
        db, cursor = _make_db({"cnt": 0})
        free_result = check_report_eligibility(db, "free", "org1")
        assert free_result["can_export"] is True

        ent_result = check_report_eligibility(db, "enterprise", "org1")
        assert ent_result["can_export"] is True

    def test_increment_and_decrement_inline_report_usage(self):
        db, cursor = _make_db({"reports_generated": 1})
        assert increment_report_usage(db, "user1", "org1") is True
        assert decrement_report_usage(db, "user1", "org1") is True
        assert cursor.execute.call_count == 2
        assert db.commit.call_count == 2


# ============================================================
# Credit deduction / refund
# ============================================================

class TestCreditDeduction:
    def test_deduct_name_credit_success(self):
        db, cursor = _make_db({"name_credits_purchased": 9})
        result = deduct_name_credit(db, "org1")
        assert result is True

    def test_deduct_name_credit_none(self):
        db, cursor = _make_db(None)
        result = deduct_name_credit(db, "org1")
        assert result is False

    def test_deduct_logo_credit_monthly_first(self):
        """First call tries monthly credits."""
        db, cursor = _make_db({"logo_credits_monthly": 9})
        result = deduct_logo_credit(db, "org1")
        assert result is True

    def test_deduct_logo_credit_purchased_fallback(self):
        """Monthly fails (None), falls back to purchased."""
        db = MagicMock()
        cursor = MagicMock()
        # First fetchone: monthly update fails, second: balance check succeeds,
        # third: purchased-credit update succeeds.
        cursor.fetchone.side_effect = [None, {"logo_credits_purchased": 6}, {"ai_credits_purchased": 1}]
        db.cursor.return_value = cursor
        db.commit = MagicMock()
        result = deduct_logo_credit(db, "org1")
        assert result is True

    def test_deduct_logo_credit_both_empty(self):
        db = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.side_effect = [None, None]
        db.cursor.return_value = cursor
        db.commit = MagicMock()
        result = deduct_logo_credit(db, "org1")
        assert result is False

    def test_refund_logo_credit_success(self):
        db, cursor = _make_db({"logo_credits_monthly": 6})
        result = refund_logo_credit(db, "org1")
        assert result is True

    def test_refund_logo_credit_not_found(self):
        db, cursor = _make_db(None)
        result = refund_logo_credit(db, "org1")
        assert result is False

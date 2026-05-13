"""
Tests for subscription eligibility checks with mocked DB.

Covers:
- get_user_plan() with mocked cursor
- check_live_search_eligibility()
- check_name_generation_eligibility()
- check_logo_generation_eligibility()
- check_report_eligibility()
- deduct_name_credit(), deduct_logo_credit(), refund_logo_credit()
"""
import uuid
from unittest.mock import patch, MagicMock



from utils.subscription import (
    get_user_plan,
    check_live_search_eligibility,
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
            "is_superadmin": False,
            "subscription_end_date": None,
        })
        result = get_user_plan(db, str(uuid.uuid4()))
        assert result["plan_name"] == "professional"
        assert result["can_use_live_search"] is True
        assert "daily_limit" in result
        assert result["daily_limit"] == 2000

    def test_no_row_defaults_to_free(self):
        db, cursor = _make_db(None)
        result = get_user_plan(db, str(uuid.uuid4()))
        assert result["plan_name"] == "free"
        assert result["can_use_live_search"] is True
        assert result["daily_limit"] == 5


# ============================================================
# check_live_search_eligibility
# ============================================================

class TestCheckLiveSearchEligibility:
    @patch("utils.subscription.get_daily_live_search_usage", return_value=0)
    @patch("utils.subscription.get_user_plan")
    def test_eligible_professional(self, mock_plan, mock_usage):
        mock_plan.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
            "can_use_live_search": True,
            "daily_limit": 2000,
        }
        db = MagicMock()
        can, reason, details = check_live_search_eligibility(db, "user1")
        assert can is True
        assert reason == "ok"
        assert details["remaining"] == 2000

    @patch("utils.subscription.get_daily_live_search_usage", return_value=2000)
    @patch("utils.subscription.get_user_plan")
    def test_daily_limit_exceeded(self, mock_plan, mock_usage):
        mock_plan.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
            "can_use_live_search": True,
            "daily_limit": 2000,
        }
        db = MagicMock()
        can, reason, details = check_live_search_eligibility(db, "user1")
        assert can is False
        assert reason == "daily_limit_exceeded"
        assert details["remaining"] == 0


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
        mock_ai.assert_called_once_with(db, "org1", cost=2)

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

    # ----- Regression: Free user with purchased AI credits must not be -----
    # ----- blocked by the historic monthly-limit short-circuit (Block 2). --
    @patch("utils.subscription.check_ai_credit_eligibility",
           return_value=(True, "ok", {"monthly_limit": 0, "total_remaining": 25}))
    @patch("utils.subscription.get_monthly_name_generations", return_value=0)
    @patch("utils.subscription.get_org_plan")
    def test_free_user_with_purchased_ai_credits_can_generate(self, mock_plan, mock_monthly, mock_ai):
        mock_plan.return_value = {
            "plan_name": "free",
            "display_name": "Free Trial",
            "name_suggestions_per_session": 3,
            "logo_runs_per_month": 0,
        }
        db = MagicMock()
        can, reason, details = check_name_generation_eligibility(db, "org1", session_count=0)
        # Before fix: Block 2 returned monthly_limit_exceeded because
        # monthly_used (0) >= monthly_limit (0) and legacy_purchased < 2,
        # ignoring ai_credits_purchased entirely.
        assert can is True
        assert reason == "ok"

    # ----- Regression: Free user past the per-session cap can keep -----
    # ----- generating when they hold purchased AI credits (Block 1). ------
    @patch("utils.subscription.check_ai_credit_eligibility",
           return_value=(True, "ok", {"monthly_limit": 0, "total_remaining": 23}))
    @patch("utils.subscription.get_monthly_name_generations", return_value=0)
    @patch("utils.subscription.get_org_plan")
    def test_session_cap_bypassed_by_purchased_ai_credits(self, mock_plan, mock_monthly, mock_ai):
        mock_plan.return_value = {
            "plan_name": "free",
            "display_name": "Free Trial",
            "name_suggestions_per_session": 3,
            "logo_runs_per_month": 0,
        }
        db, cursor = _make_db({
            "name_credits_purchased": 0,
            "ai_credits_purchased": 23,
        })
        can, reason, details = check_name_generation_eligibility(db, "org1", session_count=3)
        # Before fix: Block 1 returned upgrade_required because the per-
        # session-cap check only looked at name_credits_purchased.
        assert can is True
        assert reason == "ok"


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

    def test_deduct_name_credit_custom_cost(self):
        db = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.side_effect = [None, None, {"name_credits_purchased": 7}]
        db.cursor.return_value = cursor

        result = deduct_name_credit(db, "org1", cost=2)

        assert result is True
        assert cursor.execute.call_args.args[1] == (2, "org1", 2)

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


# ============================================================
# Credit Packs (one-shot AI credit top-ups)
# ============================================================

class TestCreditPacks:
    def test_get_credit_pack_small(self):
        from utils.subscription import get_credit_pack
        pack = get_credit_pack("small")
        assert pack is not None
        assert pack["credits"] == 25
        assert pack["price_try"] == 200

    def test_get_credit_pack_medium(self):
        from utils.subscription import get_credit_pack
        pack = get_credit_pack("medium")
        assert pack["credits"] == 100
        assert pack["price_try"] == 800

    def test_get_credit_pack_large(self):
        from utils.subscription import get_credit_pack
        pack = get_credit_pack("large")
        assert pack["credits"] == 500
        assert pack["price_try"] == 4000

    def test_get_credit_pack_unknown_returns_none(self):
        from utils.subscription import get_credit_pack
        assert get_credit_pack("xl") is None
        assert get_credit_pack(None) is None
        assert get_credit_pack("") is None

    def test_get_credit_pack_case_insensitive(self):
        from utils.subscription import get_credit_pack
        assert get_credit_pack("SMALL")["credits"] == 25
        assert get_credit_pack(" Medium ")["credits"] == 100

    def test_list_credit_packs_order(self):
        from utils.subscription import list_credit_packs
        packs = list_credit_packs()
        assert [p["id"] for p in packs] == ["small", "medium", "large"]

    def test_pack_price_matches_usd_at_40_try(self):
        """Sanity: $0.20/credit * pack_credits * 40 TRY/USD == price_try."""
        from utils.subscription import list_credit_packs
        for pack in list_credit_packs():
            expected = pack["credits"] * 0.20 * 40
            assert pack["price_try"] == expected, (
                f"Pack {pack['id']}: expected {expected} TRY, got {pack['price_try']}"
            )

    def test_add_purchased_ai_credits_increments(self):
        from utils.subscription import add_purchased_ai_credits
        db, cursor = _make_db({"ai_credits_purchased": 125})
        result = add_purchased_ai_credits(db, "org1", 100)
        assert result is True
        # Verify the UPDATE SQL was called with the right credits arg
        assert cursor.execute.called

    def test_add_purchased_ai_credits_rejects_zero(self):
        from utils.subscription import add_purchased_ai_credits
        db, cursor = _make_db({"ai_credits_purchased": 0})
        assert add_purchased_ai_credits(db, "org1", 0) is False
        assert add_purchased_ai_credits(db, "org1", -5) is False

    def test_add_purchased_ai_credits_missing_org(self):
        from utils.subscription import add_purchased_ai_credits
        db, cursor = _make_db(None)
        assert add_purchased_ai_credits(db, "org1", 50) is False

"""Tests for plan feature configuration. Runs without server."""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.subscription import PLAN_FEATURES, get_plan_limit


def test_all_plans_exist():
    assert set(PLAN_FEATURES.keys()) == {
        "free", "starter", "professional", "business", "enterprise", "superadmin"
    }


def test_all_plans_have_same_keys():
    free_keys = set(PLAN_FEATURES["free"].keys())
    for plan_name, plan in PLAN_FEATURES.items():
        assert set(plan.keys()) == free_keys, f"{plan_name} has different keys than free"


def test_free_plan_is_most_restrictive():
    free = PLAN_FEATURES["free"]
    for plan_name, plan in PLAN_FEATURES.items():
        if plan_name == "free":
            continue
        for key, value in free.items():
            if isinstance(value, bool):
                if free[key] is True:
                    assert plan[key] is True
            elif isinstance(value, (int, float)):
                assert plan[key] >= value, f"{plan_name}.{key} ({plan[key]}) < free ({value})"


def test_enterprise_is_least_restrictive():
    enterprise = PLAN_FEATURES["enterprise"]
    for plan_name, plan in PLAN_FEATURES.items():
        if plan_name == "superadmin":
            continue
        for key, value in enterprise.items():
            if isinstance(value, (int, float)):
                assert enterprise[key] >= plan[key], (
                    f"enterprise.{key} ({enterprise[key]}) < {plan_name}.{key} ({plan[key]})"
                )


def test_enterprise_fully_unlimited():
    """Enterprise should have 999999 for all numeric limits and True for all booleans."""
    enterprise = PLAN_FEATURES["enterprise"]
    for key, value in enterprise.items():
        if key in ("price_monthly", "price_annual_monthly", "auto_scan_frequency"):
            continue
        if isinstance(value, bool):
            assert value is True, f"enterprise.{key} should be True"
        elif isinstance(value, int):
            assert value == 999999, f"enterprise.{key} should be 999999, got {value}"


def test_get_plan_limit_defaults_to_free():
    assert get_plan_limit("nonexistent", "max_watchlist_items") == PLAN_FEATURES["free"]["max_watchlist_items"]


def test_get_plan_limit_unknown_feature_returns_0():
    assert get_plan_limit("free", "nonexistent_feature") == 0


def test_free_watchlist_is_3():
    assert get_plan_limit("free", "max_watchlist_items") == 3


def test_free_max_users_is_1():
    assert get_plan_limit("free", "max_users") == 1


def test_free_csv_export_disabled():
    assert get_plan_limit("free", "can_export_csv_leads") is False


def test_enterprise_csv_export_enabled():
    assert get_plan_limit("enterprise", "can_export_csv_leads") is True


def test_free_has_no_live_search():
    assert get_plan_limit("free", "monthly_live_searches") == 0
    assert get_plan_limit("free", "can_use_live_scraping") is False


def test_business_has_live_search():
    assert get_plan_limit("business", "monthly_live_searches") > 0
    assert get_plan_limit("business", "can_use_live_scraping") is True


def test_starter_has_live_search():
    assert get_plan_limit("starter", "monthly_live_searches") == 10
    assert get_plan_limit("starter", "can_use_live_scraping") is True


def test_free_auto_scan_disabled():
    assert get_plan_limit("free", "auto_scan_max_items") == 0
    assert get_plan_limit("free", "auto_scan_frequency") is None


def test_daily_quick_search_limits_ascending():
    plans = ["free", "starter", "business", "professional", "enterprise"]
    limits = [get_plan_limit(p, "max_daily_quick_searches") for p in plans]
    assert limits == sorted(limits), f"Quick search limits not ascending: {limits}"


def test_monthly_ai_credits_ascending():
    plans = ["free", "starter", "business", "professional", "enterprise"]
    limits = [get_plan_limit(p, "monthly_ai_credits") for p in plans]
    assert limits == sorted(limits), f"AI credits not ascending: {limits}"


def test_monthly_applications_ascending():
    plans = ["free", "starter", "business", "professional", "enterprise"]
    limits = [get_plan_limit(p, "monthly_applications") for p in plans]
    assert limits == sorted(limits), f"Applications not ascending: {limits}"


def test_can_track_logos():
    assert get_plan_limit("free", "can_track_logos") is False
    assert get_plan_limit("starter", "can_track_logos") is True
    assert get_plan_limit("professional", "can_track_logos") is True
    assert get_plan_limit("business", "can_track_logos") is True
    assert get_plan_limit("enterprise", "can_track_logos") is True


def test_business_between_starter_and_professional():
    """Business tier values should be between Starter and Professional."""
    business = PLAN_FEATURES["business"]
    starter = PLAN_FEATURES["starter"]
    professional = PLAN_FEATURES["professional"]

    for key in ("max_daily_quick_searches", "max_watchlist_items", "monthly_live_searches",
                "monthly_ai_credits", "monthly_applications", "max_users"):
        assert starter[key] <= business[key] <= professional[key], (
            f"business.{key} ({business[key]}) not between starter ({starter[key]}) and professional ({professional[key]})"
        )


def test_business_price():
    assert PLAN_FEATURES["business"]["price_monthly"] == 799
    assert PLAN_FEATURES["business"]["price_annual_monthly"] == 639


def test_professional_updated_limits():
    assert get_plan_limit("professional", "max_watchlist_items") == 1000
    assert get_plan_limit("professional", "max_daily_quick_searches") == 2000


def test_free_no_applications():
    assert get_plan_limit("free", "monthly_applications") == 0


def test_free_no_ai_credits():
    assert get_plan_limit("free", "monthly_ai_credits") == 0

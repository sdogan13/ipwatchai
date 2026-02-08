"""Tests for plan feature configuration. Runs without server."""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.subscription import PLAN_FEATURES, get_plan_limit


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
        for key, value in enterprise.items():
            if isinstance(value, (int, float)):
                assert enterprise[key] >= plan[key], (
                    f"enterprise.{key} ({enterprise[key]}) < {plan_name}.{key} ({plan[key]})"
                )


def test_get_plan_limit_defaults_to_free():
    assert get_plan_limit("nonexistent", "max_watchlist_items") == PLAN_FEATURES["free"]["max_watchlist_items"]


def test_get_plan_limit_unknown_feature_returns_0():
    assert get_plan_limit("free", "nonexistent_feature") == 0


def test_free_watchlist_is_5():
    assert get_plan_limit("free", "max_watchlist_items") == 5


def test_free_max_users_is_3():
    assert get_plan_limit("free", "max_users") == 3


def test_free_monthly_name_gen_is_20():
    assert get_plan_limit("free", "monthly_name_generations") == 20


def test_free_csv_export_disabled():
    assert get_plan_limit("free", "can_export_csv_leads") is False


def test_enterprise_csv_export_enabled():
    assert get_plan_limit("enterprise", "can_export_csv_leads") is True


def test_all_four_plans_exist():
    assert set(PLAN_FEATURES.keys()) == {"free", "starter", "professional", "enterprise"}


def test_free_has_no_live_search():
    assert get_plan_limit("free", "monthly_live_searches") == 0
    assert get_plan_limit("free", "can_use_live_scraping") is False


def test_professional_has_live_search():
    assert get_plan_limit("professional", "monthly_live_searches") > 0
    assert get_plan_limit("professional", "can_use_live_scraping") is True


def test_starter_no_live_search():
    assert get_plan_limit("starter", "monthly_live_searches") == 0
    assert get_plan_limit("starter", "can_use_live_scraping") is False


def test_free_auto_scan_disabled():
    assert get_plan_limit("free", "auto_scan_max_items") == 0
    assert get_plan_limit("free", "auto_scan_frequency") is None


def test_daily_quick_search_limits_ascending():
    limits = [get_plan_limit(p, "max_daily_quick_searches") for p in ["free", "starter", "professional", "enterprise"]]
    assert limits == sorted(limits), f"Quick search limits not ascending: {limits}"

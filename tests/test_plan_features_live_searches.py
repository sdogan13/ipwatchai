"""PLAN_FEATURES schema after Quick Search removal.

Verifies that the unified daily Agentic Search quota replaced the old split of
`max_daily_quick_searches` + `monthly_live_searches`:

  * Every plan exposes ``max_daily_live_searches``.
  * Neither legacy key (``max_daily_quick_searches`` / ``monthly_live_searches``)
    survives on any plan.
  * Free 5/day, Starter 50, Professional 2000, Enterprise/Superadmin unlimited.
  * Every plan has ``daily_limit > 0`` — no plan is gated out of search anymore.

These are pure-Python sanity checks; no DB, no HTTP. Fast.
"""
from utils.subscription import PLAN_FEATURES, get_plan_limit

PLAN_NAMES = ["free", "starter", "professional", "enterprise", "superadmin"]
EXPECTED_DAILY_LIVE = {
    "free": 5,
    "starter": 50,
    "professional": 2000,
    "enterprise": 999999,
    "superadmin": 999999,
}
UNLIMITED = 999999


def test_every_plan_has_max_daily_live_searches():
    for name in PLAN_NAMES:
        assert "max_daily_live_searches" in PLAN_FEATURES[name], (
            f"plan '{name}' missing 'max_daily_live_searches'"
        )


def test_no_plan_has_max_daily_quick_searches():
    for name in PLAN_NAMES:
        assert "max_daily_quick_searches" not in PLAN_FEATURES[name], (
            f"plan '{name}' still has legacy 'max_daily_quick_searches' key"
        )


def test_no_plan_has_monthly_live_searches_legacy_key():
    for name in PLAN_NAMES:
        assert "monthly_live_searches" not in PLAN_FEATURES[name], (
            f"plan '{name}' still has legacy 'monthly_live_searches' key"
        )


def test_no_plan_has_can_use_live_scraping_legacy_flag():
    """`can_use_live_scraping` was the old hard-gate for Free. Free now has 5/day,
    so the boolean is derived from `daily_limit > 0` and the field is gone."""
    for name in PLAN_NAMES:
        assert "can_use_live_scraping" not in PLAN_FEATURES[name], (
            f"plan '{name}' still has legacy 'can_use_live_scraping' flag"
        )


def test_daily_live_search_values_per_plan():
    for name, expected in EXPECTED_DAILY_LIVE.items():
        actual = PLAN_FEATURES[name]["max_daily_live_searches"]
        assert actual == expected, (
            f"plan '{name}': expected {expected} daily Agentic searches, got {actual}"
        )


def test_get_plan_limit_resolves_via_helper():
    assert get_plan_limit("free", "max_daily_live_searches") == 5
    assert get_plan_limit("starter", "max_daily_live_searches") == 50
    assert get_plan_limit("professional", "max_daily_live_searches") == 2000
    assert get_plan_limit("enterprise", "max_daily_live_searches") == UNLIMITED


def test_get_plan_limit_unknown_plan_falls_back_to_free():
    assert get_plan_limit("nonexistent_plan", "max_daily_live_searches") == 5


def test_business_alias_maps_to_professional():
    """`business` is a legacy plan name kept as an alias of `professional`."""
    assert get_plan_limit("business", "max_daily_live_searches") == 2000


def test_unlimited_sentinel_is_999999():
    """Both Enterprise and Superadmin use 999999 as the unlimited marker."""
    assert PLAN_FEATURES["enterprise"]["max_daily_live_searches"] == UNLIMITED
    assert PLAN_FEATURES["superadmin"]["max_daily_live_searches"] == UNLIMITED


def test_every_plan_has_positive_daily_limit():
    """Post-removal every plan gets ≥ Free's 5/day. No plan is gated out of search."""
    for name in PLAN_NAMES:
        assert PLAN_FEATURES[name]["max_daily_live_searches"] >= 5, (
            f"plan '{name}' has daily Agentic limit < 5"
        )


def test_daily_limits_ascend_through_paid_tiers():
    """Free < Starter < Professional ≤ Enterprise."""
    ordered = ["free", "starter", "professional", "enterprise"]
    limits = [PLAN_FEATURES[p]["max_daily_live_searches"] for p in ordered]
    assert limits == sorted(limits)

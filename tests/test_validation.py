"""
Tests for input validation across the system.

Covers:
- Pydantic model validation (UserRegister, PasswordChange)
- PLAN_FEATURES structure integrity
- Risk threshold boundary values
- IDF classification boundaries
- Class 99 edge cases
"""
import sys
import os
import uuid
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================
# Auth Model Validation
# ============================================================

class TestAuthValidation:
    """Validate auth Pydantic models reject bad input."""

    def test_register_short_password(self):
        from auth.authentication import UserRegister
        with pytest.raises(Exception):
            UserRegister(email="a@b.com", password="Ab1!", first_name="A", last_name="B")

    def test_register_no_uppercase(self):
        from auth.authentication import UserRegister
        with pytest.raises(Exception):
            UserRegister(email="a@b.com", password="alllowercase1!", first_name="A", last_name="B")

    def test_register_no_digit(self):
        from auth.authentication import UserRegister
        with pytest.raises(Exception):
            UserRegister(email="a@b.com", password="NoDigitHere!", first_name="A", last_name="B")

    def test_register_invalid_email(self):
        from auth.authentication import UserRegister
        with pytest.raises(Exception):
            UserRegister(email="not-an-email", password="ValidPass1!", first_name="A", last_name="B")

    def test_register_valid(self):
        from auth.authentication import UserRegister
        u = UserRegister(email="valid@test.com", password="ValidPass1!", first_name="A", last_name="B")
        assert u.email == "valid@test.com"

    def test_password_change_different(self):
        from auth.authentication import PasswordChange
        pc = PasswordChange(current_password="OldPass1!", new_password="NewPass1!")
        assert pc.current_password == "OldPass1!"


# ============================================================
# Plan Features Structure Validation
# ============================================================

class TestPlanFeaturesValidation:
    """Validate PLAN_FEATURES structure is consistent."""

    def test_all_plans_present(self):
        from utils.subscription import PLAN_FEATURES
        assert set(PLAN_FEATURES.keys()) == {"free", "starter", "professional", "enterprise"}

    def test_all_plans_have_consistent_keys(self):
        from utils.subscription import PLAN_FEATURES
        base_keys = set(PLAN_FEATURES["free"].keys())
        for name, plan in PLAN_FEATURES.items():
            assert set(plan.keys()) == base_keys, f"{name} keys mismatch"

    def test_monthly_live_searches_is_int(self):
        from utils.subscription import PLAN_FEATURES
        for name, plan in PLAN_FEATURES.items():
            assert isinstance(plan["monthly_live_searches"], int), f"{name} live_searches not int"

    def test_boolean_features_are_bool(self):
        from utils.subscription import PLAN_FEATURES
        bool_keys = ["can_export_reports", "can_view_holder_portfolio",
                     "can_export_csv_leads", "can_use_live_scraping"]
        for name, plan in PLAN_FEATURES.items():
            for key in bool_keys:
                assert isinstance(plan[key], bool), f"{name}.{key} not bool"

    def test_plan_hierarchy_numeric_ascending(self):
        """Numeric limits should be non-decreasing across plan tiers."""
        from utils.subscription import PLAN_FEATURES
        order = ["free", "starter", "professional", "enterprise"]
        numeric_keys = [
            "monthly_live_searches", "daily_lead_views", "monthly_reports",
            "name_suggestions_per_session", "monthly_name_generations",
            "monthly_logo_runs", "max_users", "max_watchlist_items",
            "max_daily_quick_searches", "auto_scan_max_items",
        ]
        for key in numeric_keys:
            values = [PLAN_FEATURES[p][key] for p in order]
            for i in range(1, len(values)):
                assert values[i] >= values[i - 1], (
                    f"{key}: {order[i]}({values[i]}) < {order[i-1]}({values[i-1]})"
                )


# ============================================================
# Risk Threshold Validation
# ============================================================

class TestRiskThresholdValidation:
    """Ensure risk thresholds are properly ordered."""

    def test_thresholds_are_ordered(self):
        from risk_engine import RISK_THRESHOLDS
        assert RISK_THRESHOLDS["critical"] > RISK_THRESHOLDS["very_high"]
        assert RISK_THRESHOLDS["very_high"] > RISK_THRESHOLDS["high"]
        assert RISK_THRESHOLDS["high"] > RISK_THRESHOLDS["medium"]
        assert RISK_THRESHOLDS["medium"] > RISK_THRESHOLDS["low"]

    def test_thresholds_in_0_to_1(self):
        from risk_engine import RISK_THRESHOLDS
        for name, val in RISK_THRESHOLDS.items():
            assert 0.0 <= val <= 1.0, f"{name} threshold {val} out of range"

    def test_five_levels_exist(self):
        from risk_engine import RISK_THRESHOLDS
        assert set(RISK_THRESHOLDS.keys()) == {"critical", "very_high", "high", "medium", "low"}


# ============================================================
# IDF Boundary Validation
# ============================================================

class TestIDFBoundaryValidation:
    """Validate IDF classification boundaries."""

    def test_generic_boundary(self):
        """Words with IDF < 5.3 are classified 'generic'."""
        from idf_lookup import IDFLookup
        # 've' is seeded with idf=2.0 → generic
        assert IDFLookup.get_word_class("ve") == "generic"

    def test_semi_generic_boundary(self):
        """Words with 5.3 <= IDF < 6.9 are 'semi_generic'."""
        from idf_lookup import IDFLookup
        # 'patent' is seeded with idf=6.0
        assert IDFLookup.get_word_class("patent") == "semi_generic"

    def test_distinctive_boundary(self):
        """Words with IDF >= 6.9 are 'distinctive'."""
        from idf_lookup import IDFLookup
        # 'nike' is seeded with idf=8.0
        assert IDFLookup.get_word_class("nike") == "distinctive"

    def test_weight_multiplier_generic(self):
        from idf_lookup import IDFLookup
        assert IDFLookup.get_weight_multiplier("ve") == 0.1

    def test_weight_multiplier_semi_generic(self):
        from idf_lookup import IDFLookup
        assert IDFLookup.get_weight_multiplier("patent") == 0.5

    def test_weight_multiplier_distinctive(self):
        from idf_lookup import IDFLookup
        assert IDFLookup.get_weight_multiplier("nike") == 1.0

    def test_unknown_word_default_idf(self):
        from idf_lookup import IDFLookup
        # Unknown words not in cache → _default_idf = 5.0 → generic
        idf = IDFLookup.get_idf("xyznonexistent")
        assert idf == IDFLookup._default_idf


# ============================================================
# Nice Class Validation
# ============================================================

class TestNiceClassValidation:
    """Validate Nice class edge cases."""

    def test_classes_1_through_45_are_valid(self):
        from utils.class_utils import ALL_NICE_CLASSES
        for i in range(1, 46):
            assert i in ALL_NICE_CLASSES

    def test_class_0_not_valid(self):
        from utils.class_utils import ALL_NICE_CLASSES
        assert 0 not in ALL_NICE_CLASSES

    def test_class_46_not_valid(self):
        from utils.class_utils import ALL_NICE_CLASSES
        assert 46 not in ALL_NICE_CLASSES

    def test_class_99_not_in_standard_classes(self):
        from utils.class_utils import ALL_NICE_CLASSES, GLOBAL_CLASS
        assert GLOBAL_CLASS not in ALL_NICE_CLASSES

    def test_expand_empty_returns_empty(self):
        from utils.class_utils import expand_classes
        assert expand_classes([]) == set()

    def test_overlap_score_symmetric(self):
        from utils.class_utils import calculate_class_overlap_score
        a, b = [5, 10, 15], [10, 15, 20]
        assert calculate_class_overlap_score(a, b) == calculate_class_overlap_score(b, a)


# ============================================================
# Deadline Input Validation
# ============================================================

class TestDeadlineValidation:
    """Test deadline calculator handles all input edge cases."""

    def test_none_input(self):
        from utils.deadline import calculate_appeal_deadline
        assert calculate_appeal_deadline(None) is None

    def test_empty_string(self):
        from utils.deadline import calculate_appeal_deadline
        assert calculate_appeal_deadline("") is None

    def test_integer_input(self):
        from utils.deadline import calculate_appeal_deadline
        assert calculate_appeal_deadline(42) is None

    def test_float_input(self):
        from utils.deadline import calculate_appeal_deadline
        assert calculate_appeal_deadline(3.14) is None

    def test_list_input(self):
        from utils.deadline import calculate_appeal_deadline
        assert calculate_appeal_deadline([2025, 1, 1]) is None

    def test_garbage_string(self):
        from utils.deadline import calculate_appeal_deadline
        assert calculate_appeal_deadline("garbage") is None

    def test_partial_iso_string(self):
        from utils.deadline import calculate_appeal_deadline
        # "2025-01" is not a valid date
        assert calculate_appeal_deadline("2025-01") is None

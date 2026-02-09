"""
Tests for Nice class utility functions (Class 99 / Global Brand handling).

Covers:
- is_global_class()
- expand_classes()
- classes_overlap()
- get_overlapping_classes()
- format_class_display()
- should_include_in_class_filter()
- get_class_sql_condition()
- calculate_class_overlap_score()
- is_class_conflict_high_risk()
"""
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.class_utils import (
    GLOBAL_CLASS,
    ALL_NICE_CLASSES,
    is_global_class,
    expand_classes,
    classes_overlap,
    get_overlapping_classes,
    format_class_display,
    should_include_in_class_filter,
    get_class_sql_condition,
    calculate_class_overlap_score,
    is_class_conflict_high_risk,
)


# ============================================================
# Constants
# ============================================================

class TestConstants:
    def test_global_class_is_99(self):
        assert GLOBAL_CLASS == 99

    def test_all_nice_classes_is_1_to_45(self):
        assert ALL_NICE_CLASSES == set(range(1, 46))
        assert len(ALL_NICE_CLASSES) == 45


# ============================================================
# is_global_class
# ============================================================

class TestIsGlobalClass:
    def test_99_is_global(self):
        assert is_global_class(99) is True

    def test_string_99_is_global(self):
        assert is_global_class("99") is True

    def test_other_class_not_global(self):
        assert is_global_class(25) is False

    def test_zero_not_global(self):
        assert is_global_class(0) is False

    def test_none_returns_false(self):
        assert is_global_class(None) is False

    def test_invalid_string_returns_false(self):
        assert is_global_class("abc") is False


# ============================================================
# expand_classes
# ============================================================

class TestExpandClasses:
    def test_normal_classes_unchanged(self):
        assert expand_classes([5, 10, 35]) == {5, 10, 35}

    def test_class_99_expands_to_all(self):
        assert expand_classes([99]) == ALL_NICE_CLASSES

    def test_99_with_others_expands_to_all(self):
        assert expand_classes([5, 10, 99]) == ALL_NICE_CLASSES

    def test_empty_returns_empty(self):
        assert expand_classes([]) == set()

    def test_single_class(self):
        assert expand_classes([25]) == {25}

    def test_returns_set_copy(self):
        """Returned set should be independent of ALL_NICE_CLASSES."""
        result = expand_classes([99])
        result.add(100)
        assert 100 not in ALL_NICE_CLASSES


# ============================================================
# classes_overlap
# ============================================================

class TestClassesOverlap:
    def test_overlapping(self):
        assert classes_overlap([5, 10], [10, 20]) is True

    def test_no_overlap(self):
        assert classes_overlap([5, 10], [20, 30]) is False

    def test_class_99_overlaps_with_anything(self):
        assert classes_overlap([99], [5]) is True

    def test_anything_overlaps_with_99(self):
        assert classes_overlap([5], [99]) is True

    def test_both_99(self):
        assert classes_overlap([99], [99]) is True

    def test_empty_a_no_overlap(self):
        assert classes_overlap([], [5]) is False

    def test_empty_b_no_overlap(self):
        assert classes_overlap([5], []) is False

    def test_both_empty_no_overlap(self):
        assert classes_overlap([], []) is False


# ============================================================
# get_overlapping_classes
# ============================================================

class TestGetOverlappingClasses:
    def test_partial_overlap(self):
        assert get_overlapping_classes([5, 10, 15], [10, 15, 20]) == {10, 15}

    def test_no_overlap(self):
        assert get_overlapping_classes([5, 10], [20, 30]) == set()

    def test_99_with_specific(self):
        """Class 99 expands → overlap is the specific classes."""
        assert get_overlapping_classes([99], [5, 10]) == {5, 10}

    def test_specific_with_99(self):
        assert get_overlapping_classes([5, 10], [99]) == {5, 10}

    def test_empty_returns_empty(self):
        assert get_overlapping_classes([], [5]) == set()


# ============================================================
# format_class_display
# ============================================================

class TestFormatClassDisplay:
    def test_normal_classes(self):
        assert format_class_display([5, 10, 35]) == "5, 10, 35"

    def test_sorted_output(self):
        assert format_class_display([35, 5, 10]) == "5, 10, 35"

    def test_class_99_shows_global(self):
        assert format_class_display([99]) == "Global (All Classes)"

    def test_99_with_others_shows_global(self):
        assert format_class_display([5, 99, 10]) == "Global (All Classes)"

    def test_empty_shows_none(self):
        assert format_class_display([]) == "None"

    def test_single_class(self):
        assert format_class_display([25]) == "25"


# ============================================================
# should_include_in_class_filter
# ============================================================

class TestShouldIncludeInClassFilter:
    def test_normal_overlap_included(self):
        assert should_include_in_class_filter([5, 10], [5]) is True

    def test_no_overlap_excluded(self):
        assert should_include_in_class_filter([5, 10], [20]) is False

    def test_global_brand_in_any_filter(self):
        assert should_include_in_class_filter([99], [5]) is True

    def test_class_99_filter_returns_everything(self):
        assert should_include_in_class_filter([5, 10], [99]) is True


# ============================================================
# get_class_sql_condition
# ============================================================

class TestGetClassSQLCondition:
    def test_empty_returns_true(self):
        assert get_class_sql_condition([]) == "TRUE"

    def test_class_99_returns_true(self):
        assert get_class_sql_condition([99]) == "TRUE"

    def test_normal_classes_generate_sql(self):
        sql = get_class_sql_condition([5, 10])
        assert "ARRAY[5, 10]" in sql
        assert "99 = ANY" in sql

    def test_custom_column_name(self):
        sql = get_class_sql_condition([5], column_name="my_classes")
        assert "my_classes" in sql


# ============================================================
# calculate_class_overlap_score
# ============================================================

class TestCalculateClassOverlapScore:
    def test_identical_returns_1(self):
        assert calculate_class_overlap_score([5, 10], [5, 10]) == 1.0

    def test_no_overlap_returns_0(self):
        assert calculate_class_overlap_score([5, 10], [20, 30]) == 0.0

    def test_class_99_returns_1(self):
        assert calculate_class_overlap_score([99], [5]) == 1.0

    def test_other_has_99_returns_1(self):
        assert calculate_class_overlap_score([5], [99]) == 1.0

    def test_partial_overlap(self):
        # [5, 10] vs [10, 20] → 1 overlap / 2 (smaller) = 0.5
        assert calculate_class_overlap_score([5, 10], [10, 20]) == 0.5

    def test_superset(self):
        # [5] vs [5, 10, 20] → 1/1 = 1.0
        assert calculate_class_overlap_score([5], [5, 10, 20]) == 1.0

    def test_empty_query_returns_0(self):
        assert calculate_class_overlap_score([], [5]) == 0.0

    def test_empty_trademark_returns_0(self):
        assert calculate_class_overlap_score([5], []) == 0.0

    def test_score_between_0_and_1(self):
        score = calculate_class_overlap_score([1, 2, 3, 4], [3, 4, 5, 6])
        assert 0.0 <= score <= 1.0


# ============================================================
# is_class_conflict_high_risk
# ============================================================

class TestIsClassConflictHighRisk:
    def test_full_overlap_high_risk(self):
        assert is_class_conflict_high_risk([5, 10], [5, 10]) is True

    def test_no_overlap_not_high_risk(self):
        assert is_class_conflict_high_risk([5], [20]) is False

    def test_class_99_always_high_risk(self):
        assert is_class_conflict_high_risk([99], [5]) is True

    def test_partial_at_threshold(self):
        # 0.5 overlap with default threshold 0.5 → True
        assert is_class_conflict_high_risk([5, 10], [10, 20]) is True

    def test_below_custom_threshold(self):
        # 0.5 overlap with threshold 0.75 → False
        assert is_class_conflict_high_risk([5, 10], [10, 20], threshold=0.75) is False

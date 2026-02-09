"""
Tests for the Turkish trademark opposition deadline calculator.

Covers:
- calculate_appeal_deadline(): date input types, 2-month rule, edge cases
- classify_deadline_status(): status-based routing, urgency levels, days_remaining
"""
import sys
import os
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.deadline import calculate_appeal_deadline, classify_deadline_status


# ============================================================
# calculate_appeal_deadline
# ============================================================

class TestCalculateAppealDeadline:
    """Test the 2-month deadline calculation per KHK m.42."""

    def test_simple_date(self):
        assert calculate_appeal_deadline(date(2025, 1, 15)) == date(2025, 3, 15)

    def test_end_of_year(self):
        """Dec 31 → Feb 28 (non-leap) — end-of-month clamping."""
        assert calculate_appeal_deadline(date(2025, 12, 31)) == date(2026, 2, 28)

    def test_end_of_year_leap(self):
        """Dec 31 2027 → Feb 28 2028 (leap year)."""
        assert calculate_appeal_deadline(date(2027, 12, 31)) == date(2028, 2, 29)

    def test_jan_31(self):
        """Jan 31 → Mar 31."""
        assert calculate_appeal_deadline(date(2025, 1, 31)) == date(2025, 3, 31)

    def test_mar_31(self):
        """Mar 31 → May 31."""
        assert calculate_appeal_deadline(date(2025, 3, 31)) == date(2025, 5, 31)

    def test_iso_string_input(self):
        assert calculate_appeal_deadline("2025-06-15") == date(2025, 8, 15)

    def test_datetime_input(self):
        dt = datetime(2025, 6, 15, 10, 30, 0)
        assert calculate_appeal_deadline(dt) == date(2025, 8, 15)

    def test_none_returns_none(self):
        assert calculate_appeal_deadline(None) is None

    def test_empty_string_returns_none(self):
        assert calculate_appeal_deadline("") is None

    def test_whitespace_string_returns_none(self):
        assert calculate_appeal_deadline("   ") is None

    def test_invalid_string_returns_none(self):
        assert calculate_appeal_deadline("not-a-date") is None

    def test_invalid_type_returns_none(self):
        assert calculate_appeal_deadline(12345) is None

    def test_february_28_non_leap(self):
        """Feb 28 → Apr 28."""
        assert calculate_appeal_deadline(date(2025, 2, 28)) == date(2025, 4, 28)

    def test_february_29_leap_year(self):
        """Feb 29 2024 (leap) → Apr 29."""
        assert calculate_appeal_deadline(date(2024, 2, 29)) == date(2024, 4, 29)

    def test_november_30(self):
        """Nov 30 → Jan 30."""
        assert calculate_appeal_deadline(date(2025, 11, 30)) == date(2026, 1, 30)


# ============================================================
# classify_deadline_status
# ============================================================

class TestClassifyDeadlineStatus:
    """Test the UI deadline classification."""

    # --- Status-based shortcuts ---

    def test_refused_status(self):
        result = classify_deadline_status("Refused", None, None)
        assert result["status"] == "resolved"
        assert result["urgency"] == "none"

    def test_withdrawn_status(self):
        result = classify_deadline_status("Withdrawn", None, None)
        assert result["status"] == "resolved"

    def test_opposed_status(self):
        result = classify_deadline_status("Opposed", None, None)
        assert result["status"] == "opposed"
        assert result["urgency"] == "info"

    def test_registered_status(self):
        result = classify_deadline_status("Registered", None, None)
        assert result["status"] == "registered"
        assert result["urgency"] == "low"

    def test_renewed_status(self):
        result = classify_deadline_status("Renewed", None, None)
        assert result["status"] == "registered"

    def test_expired_status(self):
        result = classify_deadline_status("Expired", None, None)
        assert result["status"] == "expired"
        assert result["urgency"] == "none"

    def test_no_bulletin_date(self):
        result = classify_deadline_status("Published", None, None)
        assert result["status"] == "pre_publication"
        assert result["urgency"] == "info"

    def test_applied_no_bulletin(self):
        result = classify_deadline_status("Applied", None, None)
        assert result["status"] == "pre_publication"

    # --- Deadline-based urgency ---

    def test_expired_deadline(self):
        """Deadline in the past → expired."""
        past = date.today() - timedelta(days=10)
        result = classify_deadline_status("Published", date.today() - timedelta(days=70), past)
        assert result["status"] == "expired"
        assert result["days_remaining"] < 0

    def test_critical_deadline(self):
        """≤ 7 days remaining → critical."""
        soon = date.today() + timedelta(days=3)
        result = classify_deadline_status("Published", date.today() - timedelta(days=57), soon)
        assert result["status"] == "active_critical"
        assert result["urgency"] == "critical"
        assert result["days_remaining"] == 3

    def test_urgent_deadline(self):
        """8-30 days remaining → urgent."""
        mid = date.today() + timedelta(days=15)
        result = classify_deadline_status("Published", date.today() - timedelta(days=45), mid)
        assert result["status"] == "active_urgent"
        assert result["urgency"] == "urgent"

    def test_normal_deadline(self):
        """> 30 days remaining → normal."""
        far = date.today() + timedelta(days=45)
        result = classify_deadline_status("Published", date.today() - timedelta(days=15), far)
        assert result["status"] == "active"
        assert result["urgency"] == "normal"

    def test_zero_days_is_critical(self):
        """Exactly 0 days remaining → critical (not expired)."""
        today = date.today()
        result = classify_deadline_status("Published", today - timedelta(days=60), today)
        assert result["status"] == "active_critical"
        assert result["days_remaining"] == 0

    def test_seven_days_is_critical(self):
        """Exactly 7 days → still critical."""
        deadline = date.today() + timedelta(days=7)
        result = classify_deadline_status("Published", date.today() - timedelta(days=53), deadline)
        assert result["status"] == "active_critical"

    def test_eight_days_is_urgent(self):
        """Exactly 8 days → urgent, not critical."""
        deadline = date.today() + timedelta(days=8)
        result = classify_deadline_status("Published", date.today() - timedelta(days=52), deadline)
        assert result["status"] == "active_urgent"

    def test_thirty_days_is_urgent(self):
        """Exactly 30 days → still urgent."""
        deadline = date.today() + timedelta(days=30)
        result = classify_deadline_status("Published", date.today() - timedelta(days=30), deadline)
        assert result["status"] == "active_urgent"

    def test_thirtyone_days_is_normal(self):
        """31 days → normal."""
        deadline = date.today() + timedelta(days=31)
        result = classify_deadline_status("Published", date.today() - timedelta(days=29), deadline)
        assert result["status"] == "active"

    def test_string_deadline_parsed(self):
        """ISO string deadline should be parsed correctly."""
        deadline_str = (date.today() + timedelta(days=5)).isoformat()
        result = classify_deadline_status("Published", date.today() - timedelta(days=55), deadline_str)
        assert result["status"] == "active_critical"

    def test_none_status_treated_as_empty(self):
        """None status doesn't crash."""
        result = classify_deadline_status(None, None, None)
        assert result["status"] == "pre_publication"

    def test_unknown_fallback(self):
        """Published with bulletin but no deadline → unknown."""
        result = classify_deadline_status("Published", date.today(), None)
        assert result["status"] == "unknown"
        assert result["urgency"] == "none"

    # --- Label content checks ---

    def test_all_results_have_required_keys(self):
        """Every result dict should have 4 required keys."""
        scenarios = [
            ("Refused", None, None),
            ("Opposed", None, None),
            ("Registered", None, None),
            ("Expired", None, None),
            ("Published", None, None),
            ("Published", date.today(), date.today() + timedelta(days=20)),
        ]
        required_keys = {"status", "days_remaining", "label_tr", "urgency"}
        for args in scenarios:
            result = classify_deadline_status(*args)
            assert required_keys.issubset(result.keys()), f"Missing keys in {result}"

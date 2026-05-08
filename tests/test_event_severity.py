"""Unit tests for utils.event_severity.classify_event_severity."""

import pytest

from utils.event_severity import (
    DEFAULT_EVENT_SEVERITY,
    EVENT_SEVERITY_MAP,
    classify_event_severity,
)


@pytest.mark.parametrize(
    "event_type, expected",
    [
        ("cancellation", "critical"),
        ("seizure", "critical"),
        ("precautionary_seizure", "critical"),
        ("bankruptcy", "critical"),
        ("injunction", "high"),
        ("precautionary_injunction", "high"),
        ("transfer", "high"),
        ("merger", "high"),
        ("partial_transfer", "high"),
        ("withdrawal", "high"),
        ("renewal", "medium"),
        ("license", "medium"),
        ("seizure_lift", "low"),
        ("injunction_lift", "low"),
        ("restriction_lift", "low"),
        ("correction", "low"),
        ("address_change", "low"),
        ("name_change", "low"),
    ],
)
def test_known_event_types_classify_to_expected_tier(event_type, expected):
    assert classify_event_severity(event_type) == expected


def test_unknown_event_type_falls_back_to_default():
    assert classify_event_severity("unobtainium_event") == DEFAULT_EVENT_SEVERITY


def test_empty_input_returns_none():
    assert classify_event_severity(None) is None
    assert classify_event_severity("") is None


def test_classifier_is_case_insensitive():
    assert classify_event_severity("TRANSFER") == "high"
    assert classify_event_severity("Cancellation") == "critical"


def test_severity_map_has_no_duplicates_and_only_known_tiers():
    valid_tiers = {"critical", "high", "medium", "low"}
    assert all(tier in valid_tiers for tier in EVENT_SEVERITY_MAP.values())

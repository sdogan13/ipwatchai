"""Unit tests for ``services.cografi_scanner_service`` pure helpers + filter SQL builder.

DB-touching scan + alert-store paths are exercised manually via the
on-demand /scan endpoint; these tests pin the pure-Python pieces:
severity bucketing, filter clause builder, dedup keys, alert
hydration shape.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from services.cografi_scanner_service import (
    ALERTS_PER_SCAN_CAP,
    CONFLICT_FLOOR,
    DEFAULT_EXCLUDED_SECTIONS,
    SEVERITY_THRESHOLDS,
    ScanMatch,
    _common_filter_clauses,
    _overlapping_section_keys,
    severity_for,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_severity_thresholds_sorted_descending():
    """Higher buckets must come first so severity_for picks the
    highest matching tier."""
    levels = [t for t, _ in SEVERITY_THRESHOLDS]
    assert levels == sorted(levels, reverse=True)


def test_default_excluded_sections_block_admin_records():
    assert "corrections" in DEFAULT_EXCLUDED_SECTIONS
    assert "gazette_only_announcements" in DEFAULT_EXCLUDED_SECTIONS


def test_alert_storage_floor_and_cap_are_sensible():
    assert 0.0 < CONFLICT_FLOOR < 1.0
    assert ALERTS_PER_SCAN_CAP >= 1


# ---------------------------------------------------------------------------
# severity_for
# ---------------------------------------------------------------------------

def test_severity_for_picks_critical_at_top():
    assert severity_for(0.99) == "critical"
    assert severity_for(0.85) == "critical"


def test_severity_for_picks_high_in_band():
    assert severity_for(0.84) == "high"
    assert severity_for(0.70) == "high"


def test_severity_for_picks_medium_in_band():
    assert severity_for(0.69) == "medium"
    assert severity_for(0.55) == "medium"


def test_severity_for_picks_low_for_below_medium():
    assert severity_for(0.54) == "low"
    assert severity_for(0.0) == "low"


def test_severity_for_clamps_out_of_range():
    assert severity_for(2.0) == "critical"
    assert severity_for(-1.0) == "low"


# ---------------------------------------------------------------------------
# _common_filter_clauses
# ---------------------------------------------------------------------------

def test_filter_clauses_excludes_admin_sections_by_default():
    sql, params = _common_filter_clauses(
        section_keys=None, record_types=None, gi_type=None,
        customer_application_no=None, customer_registration_no=None,
    )
    assert "section_key::text NOT IN" in sql
    assert params["_excluded"] == DEFAULT_EXCLUDED_SECTIONS


def test_filter_clauses_skips_default_exclusion_when_section_keys_given():
    """When the watch explicitly filters by section_keys, don't also
    add the default-exclusion list — the user's filter is authoritative."""
    sql, params = _common_filter_clauses(
        section_keys=["examined", "registered"],
        record_types=None, gi_type=None,
        customer_application_no=None, customer_registration_no=None,
    )
    assert "section_key::text = ANY" in sql
    assert "NOT IN" not in sql
    assert params["_sec"] == ["examined", "registered"]


def test_filter_clauses_lifecycle_includes_admin_sections():
    """Lifecycle scans must look at correction records, so the default
    exclusion is suppressed via include_admin_sections=True."""
    sql, _ = _common_filter_clauses(
        section_keys=None, record_types=None, gi_type=None,
        customer_application_no=None, customer_registration_no=None,
        include_admin_sections=True,
    )
    assert "NOT IN" not in sql
    assert "ANY" not in sql


def test_filter_clauses_self_conflict_exclusion():
    sql, params = _common_filter_clauses(
        section_keys=None, record_types=None, gi_type=None,
        customer_application_no="C2025/000010",
        customer_registration_no=1838,
    )
    assert "application_no IS DISTINCT FROM" in sql
    assert "registration_no IS DISTINCT FROM" in sql
    assert params["_self_app"] == "C2025/000010"
    assert params["_self_reg"] == 1838


def test_filter_clauses_candidate_id_scoping():
    """Post-ingest hook scopes scans to the new record_ids only."""
    sql, params = _common_filter_clauses(
        section_keys=None, record_types=None, gi_type=None,
        customer_application_no=None, customer_registration_no=None,
        candidate_record_ids=["uuid-1", "uuid-2", "uuid-3"],
    )
    assert "id::text = ANY" in sql
    assert params["_candidates"] == ["uuid-1", "uuid-2", "uuid-3"]


def test_filter_clauses_record_type_filter_uses_ANY():
    sql, params = _common_filter_clauses(
        section_keys=None, record_types=["GI"], gi_type=None,
        customer_application_no=None, customer_registration_no=None,
    )
    assert "record_type::text = ANY" in sql
    assert params["_rt"] == ["GI"]


def test_filter_clauses_gi_type_filter_case_insensitive():
    sql, params = _common_filter_clauses(
        section_keys=None, record_types=None,
        gi_type="Mahreç işareti",
        customer_application_no=None, customer_registration_no=None,
    )
    assert "LOWER(r.gi_type) = LOWER" in sql
    assert params["_gi"] == "Mahreç işareti"


# ---------------------------------------------------------------------------
# _overlapping_section_keys
# ---------------------------------------------------------------------------

def test_overlapping_section_keys_when_match():
    out = _overlapping_section_keys(["examined", "registered"], "examined")
    assert out == ["examined"]


def test_overlapping_section_keys_when_no_match():
    out = _overlapping_section_keys(["examined", "registered"], "article_42_change_requests")
    assert out == []


def test_overlapping_section_keys_safe_with_none_inputs():
    assert _overlapping_section_keys(None, "examined") == []
    assert _overlapping_section_keys(["examined"], None) == []
    assert _overlapping_section_keys([], None) == []


# ---------------------------------------------------------------------------
# ScanMatch dataclass
# ---------------------------------------------------------------------------

def test_scanmatch_defaults_to_zero_per_signal_sims():
    m = ScanMatch(record_id="x", overall_score=0.5)
    assert m.text_sim == 0.0
    assert m.embedding_sim == 0.0
    assert m.region_sim == 0.0
    assert m.match_type == "reference_embedding"


def test_scanmatch_holder_match_carries_default_match_type_when_overridden():
    m = ScanMatch(record_id="y", overall_score=1.0, match_type="holder")
    assert m.match_type == "holder"

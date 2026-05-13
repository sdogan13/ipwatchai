"""Unit tests for ``services.patent_scanner_service`` pure helpers.

DB-touching paths (scan + store) are exercised by route smoke layer
and live runs in the verification report.
"""
from __future__ import annotations

import pytest

from services.patent_scanner_service import (
    ALERTS_PER_SCAN_CAP,
    CONFLICT_FLOOR,
    SEVERITY_THRESHOLDS,
    WEIGHTS_REF_HYBRID,
    _common_filter_clauses,
    _overlapping_ipc,
    severity_for,
)


def test_severity_for_buckets():
    assert severity_for(0.95) == "critical"
    assert severity_for(0.85) == "critical"
    assert severity_for(0.75) == "high"
    assert severity_for(0.70) == "high"
    assert severity_for(0.60) == "medium"
    assert severity_for(0.55) == "medium"
    assert severity_for(0.30) == "low"
    assert severity_for(0.0) == "low"


def test_severity_for_clamps():
    assert severity_for(2.0) == "critical"
    assert severity_for(-1.0) == "low"
    assert severity_for(None) == "low"


def test_conflict_floor_below_medium():
    # Floor must be ≤ medium threshold so default-threshold items still
    # surface medium-severity matches.
    medium_thresh = next(t for t, name in SEVERITY_THRESHOLDS if name == "medium")
    assert CONFLICT_FLOOR <= medium_thresh


def test_alerts_cap_is_reasonable():
    assert 1 < ALERTS_PER_SCAN_CAP <= 50


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS_REF_HYBRID.values()) - 1.0) < 1e-9


def test_overlapping_ipc_basic():
    assert _overlapping_ipc(["A61M", "B65D"], ["A61M", "C07K"]) == ["A61M"]


def test_overlapping_ipc_case_insensitive():
    assert _overlapping_ipc(["a61m"], ["A61M"]) == ["A61M"]


def test_overlapping_ipc_handles_none_and_empty():
    assert _overlapping_ipc(None, ["A61M"]) == []
    assert _overlapping_ipc(["A61M"], None) == []
    assert _overlapping_ipc([], ["A61M"]) == []


def test_common_filter_clauses_excluded_record_types_always_added():
    sql, params = _common_filter_clauses(
        ipc_classes=None, kind_codes=None, customer_application_no=None,
    )
    assert "record_type NOT IN" in sql
    assert "_excluded" in params


def test_common_filter_clauses_optional_filters():
    sql, params = _common_filter_clauses(
        ipc_classes=["A61M"], kind_codes=["B"], customer_application_no="2024/000746",
    )
    assert "ipc_classes && %(_ipc)s" in sql
    assert "kind_code = ANY(%(_kinds)s" in sql
    assert "application_no <> %(_self)s" in sql
    assert params["_ipc"] == ["A61M"]
    assert params["_kinds"] == ["B"]
    assert params["_self"] == "2024/000746"


def test_common_filter_clauses_skip_when_filter_absent():
    sql, params = _common_filter_clauses(
        ipc_classes=None, kind_codes=None, customer_application_no=None,
    )
    assert "ipc_classes" not in sql
    assert "kind_code" not in sql
    assert "application_no" not in sql

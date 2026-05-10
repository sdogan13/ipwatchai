"""Unit tests for the patent search service helpers.

Pure helpers and SQL-builder behavior are tested here; full DB queries
are covered by manual smoke runs against Postgres (verification report).
Mirrors ``tests/test_design_search.py`` in shape.
"""
from __future__ import annotations

import pytest

from services.patent_search_service import (
    DEFAULT_LIMIT,
    EXCLUDED_RECORD_TYPES,
    MAX_LIMIT,
    PUBLIC_RESULT_CAP,
    WEIGHTS,
    cap_limit,
    combine_scores,
    normalize_ipc_filter,
    parse_id_query,
    to_halfvec_literal,
)


# ---------------------------------------------------------------------------
# to_halfvec_literal
# ---------------------------------------------------------------------------

def test_to_halfvec_literal_basic():
    assert to_halfvec_literal([1.0, 2.5, -3.7]) == "[1.000000,2.500000,-3.700000]"


def test_to_halfvec_literal_none_or_empty():
    assert to_halfvec_literal(None) is None
    assert to_halfvec_literal([]) is None


# ---------------------------------------------------------------------------
# combine_scores
# ---------------------------------------------------------------------------

def test_combine_scores_text_only():
    score = combine_scores(text=1.0, embedding=0.0)
    assert abs(score - WEIGHTS["text"]) < 1e-9


def test_combine_scores_embedding_only():
    score = combine_scores(text=0.0, embedding=1.0)
    assert abs(score - WEIGHTS["embedding"]) < 1e-9


def test_combine_scores_caps_at_one():
    score = combine_scores(text=1.0, embedding=1.0)
    assert score == 1.0


def test_combine_scores_negative_signals_treated_as_zero():
    score = combine_scores(text=-0.5, embedding=1.0)
    assert abs(score - WEIGHTS["embedding"]) < 1e-9


def test_combine_scores_weights_sum_balances():
    # Both perfect → close to 1.0; both half → close to 0.5
    assert combine_scores(text=0.5, embedding=0.5) == pytest.approx(0.5, abs=1e-9)


# ---------------------------------------------------------------------------
# normalize_ipc_filter
# ---------------------------------------------------------------------------

def test_normalize_ipc_filter_basic():
    assert normalize_ipc_filter(["a61m", " B65D "]) == ["A61M", "B65D"]


def test_normalize_ipc_filter_dedupes_and_strips_blanks():
    assert normalize_ipc_filter([" A61M ", "", "a61m", None]) == ["A61M"]


def test_normalize_ipc_filter_empty_returns_none():
    assert normalize_ipc_filter(None) is None
    assert normalize_ipc_filter([]) is None
    assert normalize_ipc_filter(["", "  "]) is None


# ---------------------------------------------------------------------------
# cap_limit
# ---------------------------------------------------------------------------

def test_cap_limit_within_range():
    assert cap_limit(20) == 20
    assert cap_limit("50") == 50


def test_cap_limit_caps_at_max():
    assert cap_limit(500) == MAX_LIMIT
    assert cap_limit(MAX_LIMIT + 1) == MAX_LIMIT


def test_cap_limit_caps_lower_for_public():
    assert cap_limit(50, public=True) == PUBLIC_RESULT_CAP
    assert cap_limit(5, public=True) == 5


def test_cap_limit_handles_garbage_with_default():
    assert cap_limit(None) == DEFAULT_LIMIT
    assert cap_limit("not-a-number") == DEFAULT_LIMIT


def test_cap_limit_minimum_one():
    assert cap_limit(0) == 1
    assert cap_limit(-5) == 1


# ---------------------------------------------------------------------------
# parse_id_query — exact-ID shortcut detection
# ---------------------------------------------------------------------------

def test_parse_id_query_application_no_with_slash():
    out = parse_id_query("2017/15048")
    assert out == {"application_no": "2017/15048"}


def test_parse_id_query_application_no_with_space():
    out = parse_id_query("2017 15048")
    assert out == {"application_no": "2017/15048"}


def test_parse_id_query_publication_with_kind():
    out = parse_id_query("2017/15048 U3")
    assert out == {"application_no": "2017/15048", "kind_code": "U3"}


def test_parse_id_query_with_country_prefix():
    out = parse_id_query("TR 2017 15048 B")
    assert out == {"application_no": "2017/15048", "kind_code": "B"}


def test_parse_id_query_kind_uppercased():
    out = parse_id_query("2017/15048 a1")
    assert out == {"application_no": "2017/15048", "kind_code": "A1"}


def test_parse_id_query_handles_whitespace():
    out = parse_id_query("  2017/15048  ")
    assert out == {"application_no": "2017/15048"}


def test_parse_id_query_rejects_plain_text():
    assert parse_id_query("solar panel") is None
    assert parse_id_query("widget device") is None


def test_parse_id_query_rejects_empty():
    assert parse_id_query("") is None
    assert parse_id_query(None) is None


def test_parse_id_query_rejects_partial_match():
    # Free-text containing numbers shouldn't accidentally lookup
    assert parse_id_query("solar 2017 widget") is None


# ---------------------------------------------------------------------------
# Sanity: excluded record types
# ---------------------------------------------------------------------------

def test_excluded_record_types_includes_unknown_and_legacy():
    # UNKNOWN catches kind codes the classifier doesn't yet map (A3/U3/T7
    # gap memory) — they must be excluded from default search results.
    # LEGACY rows lack INID-coded fields so titles/abstracts are unreliable.
    assert "UNKNOWN" in EXCLUDED_RECORD_TYPES
    assert "LEGACY" in EXCLUDED_RECORD_TYPES

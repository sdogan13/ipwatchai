"""Unit tests for ``services.patent_watchlist_service`` pure helpers and
validators. DB-touching paths are exercised via the route smoke layer
and live runs (no mocked Postgres here)."""
from __future__ import annotations

import pytest

from services.patent_watchlist_service import (
    WATCH_TYPES,
    _normalize_str_list,
    _validate_holder_payload,
    to_halfvec_literal,
)
from fastapi import HTTPException


def test_watch_types_constant():
    assert WATCH_TYPES == ("holder", "reference")


def test_to_halfvec_literal():
    assert to_halfvec_literal([1.0, 2.5]) == "[1.000000,2.500000]"
    assert to_halfvec_literal(None) is None
    assert to_halfvec_literal([]) is None


def test_normalize_str_list_basic():
    assert _normalize_str_list(["a", "b"]) == ["a", "b"]


def test_normalize_str_list_strips_dedupes_drops_empty():
    assert _normalize_str_list([" a ", "", "a", None, "b"]) == ["a", "b"]


def test_normalize_str_list_uppercase():
    assert _normalize_str_list(["a61m", " b65d "], upper=True) == ["A61M", "B65D"]


def test_normalize_str_list_empty_inputs():
    assert _normalize_str_list(None) == []
    assert _normalize_str_list([]) == []


def test_validate_holder_accepts_name():
    out = _validate_holder_payload({"holder_name": "ACME Corp"})
    assert out["holder_name"] == "ACME Corp"
    assert out["holder_id"] is None


def test_validate_holder_accepts_id():
    out = _validate_holder_payload({"holder_id": "abc-123"})
    assert out["holder_id"] == "abc-123"


def test_validate_holder_accepts_tpe():
    out = _validate_holder_payload({"holder_tpe_client_id": "TPE-9999"})
    assert out["holder_tpe_client_id"] == "TPE-9999"


def test_validate_holder_rejects_empty():
    with pytest.raises(HTTPException) as exc:
        _validate_holder_payload({})
    assert exc.value.status_code == 400


def test_validate_holder_rejects_blank_strings():
    # All three empty/whitespace-only -> 400
    with pytest.raises(HTTPException):
        _validate_holder_payload({"holder_name": "  ", "holder_tpe_client_id": ""})

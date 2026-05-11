"""Unit tests for ``services.cografi_watchlist_service`` pure helpers + validators.

DB-touching CRUD is exercised manually via the API; these tests pin the
pure-Python validators, normalisation helpers, and constants. Keeps the
suite fast (<1s) and runnable without Postgres.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from services.cografi_watchlist_service import (
    WATCH_TYPES,
    _normalize_str_list,
    _validate_holder_payload,
    _validate_lifecycle_payload,
    _validate_reference_payload,
    _validate_region_payload,
    to_halfvec_literal,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_watch_types_are_the_documented_four():
    assert set(WATCH_TYPES) == {"holder", "reference", "region", "lifecycle"}


# ---------------------------------------------------------------------------
# to_halfvec_literal
# ---------------------------------------------------------------------------

def test_to_halfvec_literal_serialises_floats():
    out = to_halfvec_literal([0.1, -0.2, 0.3])
    assert out is not None and out.startswith("[")
    assert out.count(",") == 2


def test_to_halfvec_literal_returns_none_for_empty():
    assert to_halfvec_literal(None) is None
    assert to_halfvec_literal([]) is None


# ---------------------------------------------------------------------------
# _normalize_str_list
# ---------------------------------------------------------------------------

def test_normalize_str_list_dedupes_and_trims():
    out = _normalize_str_list([" Konya ", "konya", "Karaman", "", None, "Konya "])
    assert out == ["Konya", "konya", "Karaman"]


def test_normalize_str_list_uppercase_normalises_for_dedup():
    out = _normalize_str_list(["gi", "GI", "tpn"], upper=True)
    assert out == ["GI", "TPN"]


def test_normalize_str_list_lowercase_normalises_for_dedup():
    out = _normalize_str_list(["EXAMINED", "Examined", "registered"], lower=True)
    assert out == ["examined", "registered"]


def test_normalize_str_list_empty_inputs_return_empty():
    assert _normalize_str_list(None) == []
    assert _normalize_str_list([]) == []
    assert _normalize_str_list(["", " "]) == []


# ---------------------------------------------------------------------------
# _validate_holder_payload
# ---------------------------------------------------------------------------

def test_validate_holder_accepts_name_only():
    out = _validate_holder_payload({"holder_name": " Karapınar Belediyesi "})
    assert out["holder_name"] == "Karapınar Belediyesi"
    assert out["holder_id"] is None
    assert out["holder_tpe_client_id"] is None


def test_validate_holder_accepts_id_only():
    out = _validate_holder_payload({"holder_id": "00000000-0000-0000-0000-000000000001"})
    assert out["holder_id"] == "00000000-0000-0000-0000-000000000001"
    assert out["holder_name"] is None


def test_validate_holder_accepts_tpe_only():
    out = _validate_holder_payload({"holder_tpe_client_id": " 12345 "})
    assert out["holder_tpe_client_id"] == "12345"


def test_validate_holder_rejects_empty_payload():
    with pytest.raises(HTTPException) as exc:
        _validate_holder_payload({})
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# _validate_reference_payload
# ---------------------------------------------------------------------------

def test_validate_reference_accepts_query_without_db_lookup():
    """Free-text query path doesn't need the DB at all."""
    out = _validate_reference_payload({"reference_query": "Karapınar Halısı"}, db=None)
    assert out["reference_query"] == "Karapınar Halısı"
    assert out["reference_record_id"] is None
    assert out["reference_embedding"] is None  # route layer encodes it


def test_validate_reference_accepts_provided_embedding():
    """Caller can pass a precomputed embedding."""
    fake_emb = [0.1] * 1024
    out = _validate_reference_payload(
        {"reference_query": "x", "reference_embedding": fake_emb}, db=None,
    )
    # Falls through to to_halfvec_literal when reference_record_id is not set.
    assert out["reference_embedding"] is not None
    assert out["reference_embedding"].startswith("[")


def test_validate_reference_clones_record_embedding_when_record_id_given():
    """When reference_record_id is set, the validator looks up the
    record's text_embedding and clones it as reference_embedding."""
    cur = MagicMock()
    cur.fetchone.return_value = {"emb": "[0.1,0.2,0.3]", "title_or_id": "Karapınar Halısı"}
    db = MagicMock()
    db.cursor.return_value = cur
    out = _validate_reference_payload(
        {"reference_record_id": "00000000-0000-0000-0000-000000000001"}, db=db,
    )
    assert out["reference_embedding"] == "[0.1,0.2,0.3]"
    assert out["reference_record_id"] == "00000000-0000-0000-0000-000000000001"


def test_validate_reference_rejects_unknown_record_id():
    cur = MagicMock()
    cur.fetchone.return_value = None
    db = MagicMock()
    db.cursor.return_value = cur
    with pytest.raises(HTTPException) as exc:
        _validate_reference_payload(
            {"reference_record_id": "00000000-0000-0000-0000-deadbeefdead"}, db=db,
        )
    assert exc.value.status_code == 400


def test_validate_reference_rejects_empty_payload():
    with pytest.raises(HTTPException) as exc:
        _validate_reference_payload({}, db=None)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# _validate_region_payload
# ---------------------------------------------------------------------------

def test_validate_region_accepts_query_only():
    out = _validate_region_payload({"region_query": " Konya ili "})
    assert out["region_query"] == "Konya ili"
    assert out["region_terms"] == []


def test_validate_region_accepts_terms_only():
    out = _validate_region_payload({"region_terms": ["Konya", " Karaman ", "Aksaray", ""]})
    assert out["region_query"] is None
    assert out["region_terms"] == ["Konya", "Karaman", "Aksaray"]


def test_validate_region_accepts_both():
    out = _validate_region_payload({
        "region_query": "Konya bölgesi",
        "region_terms": ["Konya", "Karaman"],
    })
    assert out["region_query"] == "Konya bölgesi"
    assert out["region_terms"] == ["Konya", "Karaman"]


def test_validate_region_rejects_empty_payload():
    with pytest.raises(HTTPException) as exc:
        _validate_region_payload({})
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        _validate_region_payload({"region_terms": []})


# ---------------------------------------------------------------------------
# _validate_lifecycle_payload
# ---------------------------------------------------------------------------

def test_validate_lifecycle_accepts_int_or_str_int():
    assert _validate_lifecycle_payload({"lifecycle_registration_no": 268})["lifecycle_registration_no"] == 268
    assert _validate_lifecycle_payload({"lifecycle_registration_no": "268"})["lifecycle_registration_no"] == 268


def test_validate_lifecycle_rejects_non_positive():
    with pytest.raises(HTTPException):
        _validate_lifecycle_payload({"lifecycle_registration_no": 0})
    with pytest.raises(HTTPException):
        _validate_lifecycle_payload({"lifecycle_registration_no": -5})
    with pytest.raises(HTTPException):
        _validate_lifecycle_payload({"lifecycle_registration_no": None})


def test_validate_lifecycle_rejects_garbage():
    with pytest.raises(HTTPException):
        _validate_lifecycle_payload({"lifecycle_registration_no": "not-a-number"})

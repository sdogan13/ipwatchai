"""Unit tests for ``pipeline.ingest_cografi`` pure helpers.

DB-touching code is exercised manually via the CLI; these tests cover
the pure-Python row builders, halfvec literal serialization, NUL-byte
scrubbing, date parsing, and section-key / record-type normalisation.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict

import pytest

from pipeline.ingest_cografi import (
    RECORD_UPSERT_COLS,
    _record_row,
    normalise_record_type,
    normalise_section_key,
    parse_date_safe,
    parse_iso_timestamp,
    scrub_nul,
    to_halfvec_literal,
)


# ---------------------------------------------------------------------------
# scrub_nul
# ---------------------------------------------------------------------------

def test_scrub_nul_strips_from_strings():
    assert scrub_nul("hello\x00world") == "helloworld"
    assert scrub_nul("clean") == "clean"


def test_scrub_nul_recurses_through_dicts_and_lists():
    payload = {
        "name": "Karapınar\x00 Halısı",
        "tags": ["a", "b\x00c"],
        "nested": {"k": "v\x00\x00"},
    }
    cleaned = scrub_nul(payload)
    assert cleaned == {
        "name": "Karapınar Halısı",
        "tags": ["a", "bc"],
        "nested": {"k": "v"},
    }


def test_scrub_nul_passes_through_non_strings():
    assert scrub_nul(42) == 42
    assert scrub_nul(None) is None
    assert scrub_nul(True) is True


# ---------------------------------------------------------------------------
# to_halfvec_literal
# ---------------------------------------------------------------------------

def test_to_halfvec_literal_formats_floats_for_pgvector_cast():
    out = to_halfvec_literal([0.1, -0.25, 1.0])
    assert out.startswith("[") and out.endswith("]")
    parts = out[1:-1].split(",")
    assert len(parts) == 3
    # Values are 6-decimal formatted to keep halfvec precision useful.
    assert all("." in p for p in parts)


def test_to_halfvec_literal_returns_none_for_empty():
    assert to_halfvec_literal(None) is None
    assert to_halfvec_literal([]) is None


def test_to_halfvec_literal_handles_iterables():
    """Generators and tuples are accepted; only the materialised list matters."""
    out = to_halfvec_literal((x * 0.1 for x in range(3)))
    assert out is not None
    assert out.count(",") == 2


# ---------------------------------------------------------------------------
# parse_date_safe / parse_iso_timestamp
# ---------------------------------------------------------------------------

def test_parse_date_safe_parses_iso_dates():
    assert parse_date_safe("2026-05-04") == date(2026, 5, 4)


def test_parse_date_safe_returns_none_for_garbage():
    assert parse_date_safe("") is None
    assert parse_date_safe(None) is None
    assert parse_date_safe("nope") is None
    assert parse_date_safe("04.05.2026") is None  # not ISO


def test_parse_iso_timestamp_handles_both_z_and_offset():
    t1 = parse_iso_timestamp("2026-05-11T12:34:56Z")
    t2 = parse_iso_timestamp("2026-05-11T12:34:56+00:00")
    assert isinstance(t1, datetime)
    assert isinstance(t2, datetime)
    assert t1 == t2


def test_parse_iso_timestamp_returns_none_for_garbage():
    assert parse_iso_timestamp(None) is None
    assert parse_iso_timestamp("") is None
    assert parse_iso_timestamp("not-a-timestamp") is None


# ---------------------------------------------------------------------------
# normalise_section_key / normalise_record_type
# ---------------------------------------------------------------------------

def test_normalise_section_key_passes_through_known_values():
    for key in [
        "examined", "registered", "article_40_modified",
        "article_42_change_requests", "article_42_finalized",
        "article_43_modified", "corrections", "gazette_only_announcements",
    ]:
        assert normalise_section_key(key) == key


def test_normalise_section_key_coerces_unknown_to_examined():
    """Defensive — unknown values would crash the schema's ENUM cast.
    Coercing to ``examined`` keeps the row ingestable; the warning log
    surfaces the surprise for follow-up."""
    assert normalise_section_key("totally_made_up") == "examined"
    assert normalise_section_key(None) == "examined"


def test_normalise_record_type_passes_through_known_values():
    for key in ["GI", "TPN", "UNKNOWN"]:
        assert normalise_record_type(key) == key


def test_normalise_record_type_unknown_collapses_to_unknown():
    assert normalise_record_type("OTHER") == "UNKNOWN"
    assert normalise_record_type(None) == "UNKNOWN"


# ---------------------------------------------------------------------------
# _record_row
# ---------------------------------------------------------------------------

KARAPINAR_RECORD = {
    "__section_key": "examined",
    "record_type": "GI",
    "name": "Karapınar Halısı",
    "start_page": 8,
    "application_no": "C2022/000469",
    "application_date": "2022-12-28",
    "product_group": "Halı / Halılar ve kilimler",
    "gi_type": "Mahreç işareti",
    "applicant_name": "Karapınar Ticaret ve Sanayi Odası",
    "applicant_address": "Hankapı Mah. Konya Cad. ... KONYA",
    "agent": "Hasan ATASEVEN (Söz Patent Ltd. Şti)",
    "geographical_boundary": "Konya ili Karapınar ilçesi",
    "usage_description": "Karapınar Halısı ibaresi ve mahreç işareti amblemi ...",
    "body_sections": {
        "product_description": "Karapınar Halısı; saf yün kullanılarak ...",
        "production_method": "Halı dokumacılığı yünün eğirilmesinden ...",
    },
    "text_embedding": [0.01] * 1024,
    "primary_figure_embedding": [0.02] * 1024,
}

PARENT_BULLETIN = {
    "bulletin_no": 220,
    "bulletin_date": "2026-05-04",
    "extracted_at": "2026-05-11T07:14:05+00:00",
    "embeddings_at": "2026-05-11T10:22:53Z",
    "extractor_version": 3,
}


def test_record_row_includes_all_upsert_cols():
    row = _record_row(KARAPINAR_RECORD, PARENT_BULLETIN, bulletin_folder="CI_220_2026-05-04")
    for col in RECORD_UPSERT_COLS:
        assert col in row, f"row missing column {col!r}"


def test_record_row_normalises_dates_and_section_key():
    row = _record_row(KARAPINAR_RECORD, PARENT_BULLETIN, bulletin_folder="CI_220_2026-05-04")
    assert row["section_key"] == "examined"
    assert row["bulletin_date"] == date(2026, 5, 4)
    assert row["application_date"] == date(2022, 12, 28)
    assert isinstance(row["extracted_at"], datetime)
    assert isinstance(row["embeddings_at"], datetime)


def test_record_row_serialises_body_sections_as_jsonb_string():
    """body_sections is bound as a string with a ``::jsonb`` cast;
    the row builder must already serialise it so the upsert's parameter
    is a JSON-text payload, not a raw dict."""
    row = _record_row(KARAPINAR_RECORD, PARENT_BULLETIN, bulletin_folder="CI_220_2026-05-04")
    body = row["body_sections"]
    assert isinstance(body, str)
    parsed = json.loads(body)
    assert parsed["product_description"].startswith("Karapınar Halısı; saf yün")


def test_record_row_passes_embeddings_as_halfvec_literals():
    row = _record_row(KARAPINAR_RECORD, PARENT_BULLETIN, bulletin_folder="CI_220_2026-05-04")
    assert isinstance(row["text_embedding"], str)
    assert row["text_embedding"].startswith("[")
    assert isinstance(row["primary_figure_embedding"], str)
    assert row["primary_figure_embedding"].startswith("[")


def test_record_row_handles_missing_optional_fields():
    """Bare art42 stub (only record_type + name + section_key) must
    produce a complete row with NULL columns where appropriate."""
    record = {
        "__section_key": "article_42_change_requests",
        "record_type": "GI",
        "name": "İzmir Boyozu",
        "existing_registration_no": 268,
    }
    row = _record_row(record, PARENT_BULLETIN, bulletin_folder="CI_220_2026-05-04")
    assert row["section_key"] == "article_42_change_requests"
    assert row["existing_registration_no"] == 268
    assert row["application_no"] is None
    assert row["registration_no"] is None
    assert row["text_embedding"] is None
    assert row["primary_figure_embedding"] is None
    assert json.loads(row["body_sections"]) == {}


def test_record_row_picks_up_correction_fields_for_corrections_section():
    record = {
        "__section_key": "corrections",
        "record_type": "GI",
        "name": "Aksaray Tahinlisi",
        "referenced_bulletin_no": 94,
        "referenced_bulletin_date": "2021-02-01",
        "referenced_record_id": "C2020/219",
        "correction_old": "Aksaray",
        "correction_new": "Aksaray Tahinli",
    }
    row = _record_row(record, PARENT_BULLETIN, bulletin_folder="CI_95_2021-02-15")
    assert row["correction_referenced_bulletin_no"] == 94
    assert row["correction_referenced_bulletin_date"] == date(2021, 2, 1)
    assert row["correction_referenced_record_id"] == "C2020/219"
    assert row["correction_old_text"] == "Aksaray"
    assert row["correction_new_text"] == "Aksaray Tahinli"


def test_record_row_falls_back_when_record_type_unknown():
    record = {
        "__section_key": "examined",
        "record_type": "WEIRD",
        "name": "Test",
    }
    row = _record_row(record, PARENT_BULLETIN, bulletin_folder="CI_X")
    assert row["record_type"] == "UNKNOWN"

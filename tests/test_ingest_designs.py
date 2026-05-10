"""Unit tests for ``pipeline.ingest_designs`` pure helpers.

DB-level tests (insert/select against Postgres) require a real database
fixture and live with the live test suite. These tests cover the
pure helpers: status mapping, opposition window, halfvec serialization,
date parsing, and row-shaping.
"""

from datetime import date

import pytest

from pipeline.ingest_designs import (
    SECTION_STATUS_MAP,
    _design_row,
    _first_applicant,
    _truncate_500,
    opposition_end_date,
    parse_date_safe,
    status_for_section,
    to_halfvec_literal,
)


# ---------------------------------------------------------------------------
# status_for_section + SECTION_STATUS_MAP
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("section,expected", [
    ("tr_native", "Yayında"),
    ("deferred_lifted", "Yayında"),
    ("republished", "Yayında"),
    ("hague", "Yayında"),
    ("deferred", "Yayım Ertelendi"),
])
def test_status_for_section_known(section, expected):
    assert status_for_section(section) == expected


def test_status_for_section_unknown_returns_bilinmiyor():
    assert status_for_section("never_heard_of") == "Bilinmiyor"
    assert status_for_section("") == "Bilinmiyor"


def test_section_status_map_has_all_section_types_we_emit():
    expected = {"tr_native", "deferred", "deferred_lifted", "republished", "hague"}
    assert expected.issubset(set(SECTION_STATUS_MAP.keys()))


# ---------------------------------------------------------------------------
# opposition_end_date
# ---------------------------------------------------------------------------

def test_opposition_end_date_adds_90_days():
    assert opposition_end_date("2026-04-24") == date(2026, 7, 23)
    assert opposition_end_date("2026-01-09") == date(2026, 4, 9)


def test_opposition_end_date_garbage():
    assert opposition_end_date(None) is None
    assert opposition_end_date("") is None
    assert opposition_end_date("not-a-date") is None


# ---------------------------------------------------------------------------
# parse_date_safe
# ---------------------------------------------------------------------------

def test_parse_date_safe_iso():
    assert parse_date_safe("2026-04-24") == date(2026, 4, 24)


def test_parse_date_safe_garbage():
    assert parse_date_safe(None) is None
    assert parse_date_safe("") is None
    assert parse_date_safe("06.04.2026") is None  # we expect ISO; non-ISO returns None


# ---------------------------------------------------------------------------
# to_halfvec_literal
# ---------------------------------------------------------------------------

def test_to_halfvec_literal_basic():
    assert to_halfvec_literal([1.0, 2.5, -3.7]) == "[1.000000,2.500000,-3.700000]"


def test_to_halfvec_literal_handles_iterable():
    # generators / numpy-like iterables should work
    assert to_halfvec_literal(iter([0.1, 0.2])) == "[0.100000,0.200000]"


def test_to_halfvec_literal_empty_or_none():
    assert to_halfvec_literal(None) is None
    assert to_halfvec_literal([]) is None


# ---------------------------------------------------------------------------
# _truncate_500 — fits product_name_* into VARCHAR(500)
# ---------------------------------------------------------------------------

def test_truncate_500_short_string_passthrough():
    assert _truncate_500("Lamba") == "Lamba"
    assert _truncate_500("a" * 500) == "a" * 500


def test_truncate_500_strips_whitespace():
    assert _truncate_500("  Lamba  ") == "Lamba"


def test_truncate_500_clips_at_500_chars():
    """Real-world Phase-1 finding: 13 Hague designs ship product_name_en
    over 500 chars (longest 1373) describing multi-part designs as
    comma-joined lists. Truncate so the row fits VARCHAR(500)."""
    s = "Hood for vehicle, " * 100   # ~1800 chars
    out = _truncate_500(s)
    assert out is not None
    assert len(out) == 500
    assert out.startswith("Hood for vehicle,")


def test_truncate_500_handles_none_and_empty():
    assert _truncate_500(None) is None
    assert _truncate_500("") is None
    assert _truncate_500("   ") is None
    assert _truncate_500(42) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _first_applicant
# ---------------------------------------------------------------------------

def test_first_applicant_returns_first():
    record = {"applicants": [{"name": "A"}, {"name": "B"}]}
    assert _first_applicant(record) == {"name": "A"}


def test_first_applicant_empty():
    assert _first_applicant({"applicants": []}) is None
    assert _first_applicant({}) is None


# ---------------------------------------------------------------------------
# _design_row
# ---------------------------------------------------------------------------

def _sample_record_with_one_design():
    return {
        "section": "tr_native",
        "record_index": 17,
        "application_no": "2024/007254",
        "registration_no": "2024 007254",
        "filing_date": "2024-09-06",
        "registration_date": "2024-09-06",
        "design_count": 4,
        "locarno_classes": ["26-05"],
        "applicants": [{"name": "TIM MIMARLIK", "id": "7610221", "country": "TÜRKİYE"}],
        "designers": [{"name": "ŞEBNEM SULTAN"}],
        "attorney": {"name": "IŞIK ÖZDOĞAN", "firm": "MOROĞLU"},
        "priorities": [{"date": "2025-06-27", "number": "30/010,422", "country": "US"}],
        "designs": [
            {
                "design_index": 1,
                "product_name_tr": "Lamba",
                "views": [],
                "design_aggregates": {
                    "dinov2_vitl14_mean": [0.1] * 1024,
                    "clip_vitb32_mean": [0.2] * 512,
                },
            }
        ],
        "page_range": [17, 17],
        "bulletin_no": 483,
        "bulletin_date": "2026-04-24",
    }


def test_design_row_basic_fields():
    rec = _sample_record_with_one_design()
    row = _design_row(rec, rec["designs"][0], holder_id="abc-uuid", source_folder="TS_483_2026-04-24")
    assert row["registry_type"] == "design"
    assert row["application_no"] == "2024/007254"
    assert row["design_index"] == 1
    assert row["registration_no"] == "2024 007254"
    assert row["section"] == "tr_native"
    assert row["current_status"] == "Yayında"
    assert row["filing_date"] == date(2024, 9, 6)
    assert row["registration_date"] == date(2024, 9, 6)
    assert row["bulletin_no"] == "483"
    assert row["bulletin_date"] == date(2026, 4, 24)
    assert row["opposition_end"] == date(2026, 7, 23)
    assert row["product_name_tr"] == "Lamba"
    assert row["locarno_classes"] == ["26-05"]
    assert row["design_count"] == 4
    assert row["holder_id"] == "abc-uuid"
    assert row["designers"] == ["ŞEBNEM SULTAN"]
    assert row["attorney_name"] == "IŞIK ÖZDOĞAN"
    assert row["attorney_firm"] == "MOROĞLU"
    assert row["page_range_start"] == 17
    assert row["page_range_end"] == 17
    assert row["source_issue_folder"] == "TS_483_2026-04-24"
    # priorities serialized to JSON string
    assert "30/010,422" in row["priorities"]
    # halfvec literals are dimensionless strings
    assert row["dinov2_vitl14_mean"].startswith("[") and row["dinov2_vitl14_mean"].endswith("]")
    assert row["clip_vitb32_mean"].startswith("[") and row["clip_vitb32_mean"].endswith("]")


def test_design_row_deferred_section_maps_to_deferred_status():
    rec = _sample_record_with_one_design()
    rec["section"] = "deferred"
    rec["deferred_publication"] = {"period_months": 30}
    row = _design_row(rec, rec["designs"][0], holder_id=None, source_folder="TS_483")
    assert row["current_status"] == "Yayım Ertelendi"
    assert row["deferred_publication"]
    assert "30" in row["deferred_publication"]


def test_design_row_hague_no_application_no():
    rec = _sample_record_with_one_design()
    rec["section"] = "hague"
    rec["application_no"] = None
    rec["registration_no"] = "DM 244882"
    rec["hague_reference"] = {
        "wipo_bulletin": "13/2025",
        "designated_states": ["CH", "TR"],
        "product_name_en": "Jewelry for swim wear",
    }
    row = _design_row(rec, rec["designs"][0], holder_id=None, source_folder="TS_483")
    assert row["application_no"] is None
    assert row["registration_no"] == "DM 244882"
    assert row["product_name_en"] == "Jewelry for swim wear"
    assert row["hague_reference"]
    assert "13/2025" in row["hague_reference"]


def test_design_row_no_aggregate_embeddings_returns_none():
    rec = _sample_record_with_one_design()
    rec["designs"][0]["design_aggregates"] = {}
    row = _design_row(rec, rec["designs"][0], holder_id=None, source_folder="TS_483")
    assert row["dinov2_vitl14_mean"] is None
    assert row["clip_vitb32_mean"] is None


def test_design_row_multi_designers():
    rec = _sample_record_with_one_design()
    rec["designers"] = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
    row = _design_row(rec, rec["designs"][0], holder_id=None, source_folder="TS_483")
    assert row["designers"] == ["A", "B", "C"]

"""Phase-3 service tests for design watchlist CSV bulk upload helpers.

Cover the pure pieces (template, decode, detect, mapping, row coercion).
The end-to-end import path is exercised separately by the live HTTP smoke
test in the build session because it touches the real DB.
"""
from __future__ import annotations

from services.design_watchlist_service import (
    _coerce_bool,
    _coerce_threshold,
    _decode_csv_bytes,
    _DWL_TEMPLATE_HEADERS,
    _row_to_create_payload,
    _split_list,
    _suggest_mapping,
    build_design_csv_template,
    detect_design_csv_columns,
)


# ---------------------------------------------------------------------------
# build_design_csv_template
# ---------------------------------------------------------------------------

def test_template_is_utf8_bom_csv_with_canonical_headers():
    out = build_design_csv_template()
    # UTF-8 BOM lets Excel render Turkish chars correctly.
    assert out.startswith(b"\xef\xbb\xbf")
    text = out.decode("utf-8-sig")
    first_line = text.splitlines()[0]
    cols = [c.strip() for c in first_line.split(",")]
    assert cols == _DWL_TEMPLATE_HEADERS
    # And there's at least one example row to guide the user.
    assert len(text.splitlines()) >= 2


# ---------------------------------------------------------------------------
# _decode_csv_bytes
# ---------------------------------------------------------------------------

def test_decode_strips_utf8_bom():
    raw = b"\xef\xbb\xbfproduct_name\nLamba\n"
    assert _decode_csv_bytes(raw).splitlines()[0] == "product_name"


def test_decode_falls_back_to_latin1_on_invalid_utf8():
    raw = b"\xff\xfeinvalid"
    out = _decode_csv_bytes(raw)
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# _suggest_mapping
# ---------------------------------------------------------------------------

def test_suggest_mapping_picks_aliases_case_insensitively():
    headers = ["Ürün Adı", "LOCARNO", "App No", "Eşik", "Notes"]
    out = _suggest_mapping(headers)
    assert out["product_name"] == "Ürün Adı"
    assert out["locarno_classes"] == "LOCARNO"
    assert out["customer_application_no"] == "App No"
    assert out["similarity_threshold"] == "Eşik"
    assert out["description"] == "Notes"
    # Fields with no match get None
    assert out["alert_frequency"] is None


# ---------------------------------------------------------------------------
# detect_design_csv_columns
# ---------------------------------------------------------------------------

def test_detect_columns_handles_typical_csv_with_sample():
    raw = (
        b"\xef\xbb\xbfproduct_name,locarno_classes,similarity_threshold,description\n"
        b"Lamba,26-05;26-04,0.7,Modern lamba\n"
        b"Sandalye,06-01,0.5,Mutfak sandalyesi\n"
    )
    out = detect_design_csv_columns(raw)
    assert out["columns"] == ["product_name", "locarno_classes", "similarity_threshold", "description"]
    assert out["total_rows"] == 2
    # Suggested mapping uses canonical headers as-is
    assert out["suggested_mapping"]["product_name"] == "product_name"
    assert out["suggested_mapping"]["locarno_classes"] == "locarno_classes"
    assert len(out["sample_rows"]) == 2
    assert out["sample_rows"][0]["product_name"] == "Lamba"


def test_detect_columns_empty_csv_safe():
    out = detect_design_csv_columns(b"")
    assert out == {"columns": [], "sample_rows": [], "total_rows": 0, "suggested_mapping": {}}


# ---------------------------------------------------------------------------
# _row_to_create_payload
# ---------------------------------------------------------------------------

def test_row_to_payload_minimum_required_field():
    mapping = {"product_name": "name"}
    payload, err = _row_to_create_payload({"name": "Sandalye"}, mapping)
    assert err is None
    assert payload == {"product_name": "Sandalye"}


def test_row_to_payload_rejects_blank_name():
    mapping = {"product_name": "name"}
    payload, err = _row_to_create_payload({"name": "  "}, mapping)
    assert payload is None
    assert err is not None


def test_row_to_payload_normalizes_locarno_priority_threshold_tags():
    mapping = {
        "product_name": "Ürün Adı",
        "locarno_classes": "LOCARNO",
        "similarity_threshold": "Eşik",
        "priority": "Öncelik",
        "tags": "Etiketler",
        "alert_frequency": "Bildirim Sıklığı",
        "alert_email": "E-posta",
    }
    row = {
        "Ürün Adı": "Lamba",
        "LOCARNO": "26-05;26-04 ;",
        "Eşik": "70",                 # percent-style accepted
        "Öncelik": "High",            # case-insensitive
        "Etiketler": "iç-mekan,aydınlatma",
        "Bildirim Sıklığı": "Weekly",
        "E-posta": "Yes",
    }
    payload, err = _row_to_create_payload(row, mapping)
    assert err is None
    assert payload["product_name"] == "Lamba"
    assert payload["locarno_classes"] == ["26-05", "26-04"]
    assert payload["similarity_threshold"] == 0.70
    assert payload["priority"] == "high"
    assert sorted(payload["tags"]) == sorted(["iç-mekan", "aydınlatma"])
    assert payload["alert_frequency"] == "weekly"
    assert payload["alert_email"] is True


def test_row_to_payload_rejects_invalid_priority_and_frequency_silently():
    mapping = {"product_name": "name", "priority": "p", "alert_frequency": "f"}
    payload, err = _row_to_create_payload(
        {"name": "X", "p": "garbage", "f": "yearly"}, mapping
    )
    assert err is None
    assert payload == {"product_name": "X"}  # garbage discarded, payload stays minimal


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

def test_coerce_bool_variants():
    assert _coerce_bool("true") is True
    assert _coerce_bool("Yes") is True
    assert _coerce_bool("evet") is True
    assert _coerce_bool("FALSE") is False
    assert _coerce_bool("hayır") is False
    assert _coerce_bool("maybe") is None
    assert _coerce_bool(None) is None


def test_coerce_threshold_clamps_or_rejects():
    assert _coerce_threshold("0.5") == 0.5
    assert _coerce_threshold("0,7") == 0.7         # comma-style decimal accepted
    assert _coerce_threshold("70") == 0.70         # percent accepted
    assert _coerce_threshold("999") is None
    assert _coerce_threshold("-0.1") is None
    assert _coerce_threshold("") is None
    assert _coerce_threshold(None) is None


def test_split_list_handles_mixed_separators_and_blanks():
    assert _split_list("a, b ; c , , d ") == ["a", "b", "c", "d"]
    assert _split_list("") == []
    assert _split_list(None) == []

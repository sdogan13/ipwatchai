"""Unit tests for ``cd_extract_patent`` helpers.

Built one helper at a time. Each step adds its own test block so failures
point cleanly at the unit under test.
"""

import pytest

from cd_extract_patent import decode_hsqldb_escapes


# ---------------------------------------------------------------------------
# Step 2.1 — decode_hsqldb_escapes
# ---------------------------------------------------------------------------

def test_decode_hsqldb_escapes_passthrough_ascii():
    """ASCII strings come through untouched."""
    assert decode_hsqldb_escapes("hello world") == "hello world"
    assert decode_hsqldb_escapes("2017/15048") == "2017/15048"


def test_decode_hsqldb_escapes_handles_none_and_empty():
    """None and empty input return empty string (no NULL leakage)."""
    assert decode_hsqldb_escapes(None) == ""
    assert decode_hsqldb_escapes("") == ""


def test_decode_hsqldb_escapes_decodes_real_turkish_chars():
    """Captured directly from 2025_12 ptbulletin.log:

        EMN\\u0130YET BEL\\u0130RTE\\u00c7L\\u0130 ENJEKT\\u00d6R K\\u0130L\\u0130D\\u0130
        -> EMNİYET BELİRTEÇLİ ENJEKTÖR KİLİDİ
    """
    raw = "EMN\\u0130YET BEL\\u0130RTE\\u00c7L\\u0130 ENJEKT\\u00d6R K\\u0130L\\u0130D\\u0130"
    assert decode_hsqldb_escapes(raw) == "EMNİYET BELİRTEÇLİ ENJEKTÖR KİLİDİ"


def test_decode_hsqldb_escapes_handles_full_lower_alphabet():
    """All five Turkish-specific lower-case chars in one string."""
    raw = "\\u0131 \\u011f \\u00fc \\u015f \\u00f6 \\u00e7"
    # ı ğ ü ş ö ç
    assert decode_hsqldb_escapes(raw) == "ı ğ ü ş ö ç"


def test_decode_hsqldb_escapes_decodes_newline_inside_abstract():
    """The abstract field uses \\u000a for embedded line breaks."""
    raw = "ya\\u011f s\\u0131y\\u0131rma \\u00fcnitesidir.\\u000a\\u000a\\u015eekil 1\\u000a"
    decoded = decode_hsqldb_escapes(raw)
    assert "\n\n" in decoded
    assert decoded.endswith("\n")
    assert "Şekil 1" in decoded
    assert "ya\\u011f" not in decoded  # no literal escape left


def test_decode_hsqldb_escapes_decodes_holder_address():
    """Real holder-row sample from 2025_12, mixed Turkish + spaces."""
    raw = "\\u0130BN\\u0130 MELEK OSB MAH. TOSB\\u0130 YOL 4 SK. 29 "
    assert decode_hsqldb_escapes(raw) == "İBNİ MELEK OSB MAH. TOSBİ YOL 4 SK. 29 "


def test_decode_hsqldb_escapes_handles_uppercase_hex_digits():
    """Hex digit case must not matter — Java accepts both."""
    assert decode_hsqldb_escapes("\\u00FC") == "ü"
    assert decode_hsqldb_escapes("\\u00fc") == "ü"
    assert decode_hsqldb_escapes("\\u00fC") == "ü"


def test_decode_hsqldb_escapes_does_not_decode_uppercase_u_prefix():
    """``\\Uxxxx`` is NOT a Java escape (only lowercase ``\\u``).

    HSQLDB never writes the uppercase form. If we accepted it we'd
    silently corrupt any string that happens to contain that pattern.
    """
    assert decode_hsqldb_escapes("\\U0130") == "\\U0130"


def test_decode_hsqldb_escapes_leaves_non_escape_backslashes_alone():
    """A backslash that isn't part of a \\uXXXX run survives."""
    assert decode_hsqldb_escapes(r"foo\bar") == r"foo\bar"
    assert decode_hsqldb_escapes(r"path\to\file") == r"path\to\file"
    # And a \u that isn't followed by 4 hex digits
    assert decode_hsqldb_escapes(r"\u012") == r"\u012"


def test_decode_hsqldb_escapes_decodes_attorney_company_name():
    """Real attorney-row sample from 2025_12."""
    raw = "ERDEM KAYA PATENT VE DAN. A.\\u015e."
    assert decode_hsqldb_escapes(raw) == "ERDEM KAYA PATENT VE DAN. A.Ş."

    raw2 = "DI\\u015e PATENT MARKA TESC\\u0130L ve DANI\\u015eMANLIK H\\u0130Z. LTD. \\u015eT\\u0130."
    assert decode_hsqldb_escapes(raw2) == "DIŞ PATENT MARKA TESCİL ve DANIŞMANLIK HİZ. LTD. ŞTİ."


def test_decode_hsqldb_escapes_idempotent_on_already_decoded_text():
    """Decoding decoded text is a no-op."""
    decoded = "EMNİYET BELİRTEÇLİ"
    assert decode_hsqldb_escapes(decoded) == decoded

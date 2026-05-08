"""Unit tests for ``cd_extract_patent`` helpers.

Built one helper at a time. Each step adds its own test block so failures
point cleanly at the unit under test.
"""

import pytest

from cd_extract_patent import decode_hsqldb_escapes, strip_ipc_html


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


# ---------------------------------------------------------------------------
# Step 2.2 — strip_ipc_html
# ---------------------------------------------------------------------------

def test_strip_ipc_html_handles_real_multi_class_value():
    """Real 4-class IPCCODE captured from 2025_12 ptbulletin.log."""
    raw = "<html><p>A61M 5/31</p><p>A61J 1/14</p><p>A61M 39/02</p><p>A61M 5/50</p></html>"
    assert strip_ipc_html(raw) == ["A61M 5/31", "A61J 1/14", "A61M 39/02", "A61M 5/50"]


def test_strip_ipc_html_handles_real_single_class_value():
    """Single-class records are common (e.g. utility models)."""
    raw = "<html><p>B01D 21/24</p></html>"
    assert strip_ipc_html(raw) == ["B01D 21/24"]


def test_strip_ipc_html_preserves_no_space_codes():
    """Codes legitimately appear without internal whitespace.

    Real samples from 2025_12: ``G01H13/00``, ``A46B5/00``, ``A47L13/255``.
    """
    raw = "<html><p>G01H13/00</p><p>A46B5/00</p><p>A47L13/255</p></html>"
    assert strip_ipc_html(raw) == ["G01H13/00", "A46B5/00", "A47L13/255"]


def test_strip_ipc_html_handles_5_class_record():
    """Real 5-class IPCCODE — checks order is preserved."""
    raw = "<html><p>H02N 2/00</p><p>G06N 3/06</p><p>H04M 1/00</p><p>G06F 3/00</p><p>A61B 5/00</p></html>"
    assert strip_ipc_html(raw) == [
        "H02N 2/00", "G06N 3/06", "H04M 1/00", "G06F 3/00", "A61B 5/00",
    ]


def test_strip_ipc_html_returns_empty_list_for_none_or_empty():
    assert strip_ipc_html(None) == []
    assert strip_ipc_html("") == []
    assert strip_ipc_html("   ") == []


def test_strip_ipc_html_returns_empty_for_wrapper_with_no_p_tags():
    """Defensive: malformed HTML (wrapper present but no <p>) → []."""
    assert strip_ipc_html("<html></html>") == []
    assert strip_ipc_html("<html>A61M 5/31</html>") == []
    assert strip_ipc_html("<div>A61M 5/31</div>") == []


def test_strip_ipc_html_passes_through_plain_text_as_single_element():
    """Forward-defense: callers passing already-extracted code → [code].

    Not produced by the CD bundle, but lets the helper compose with
    other call sites without surprising None-equivalent behaviour.
    """
    assert strip_ipc_html("A61M 5/31") == ["A61M 5/31"]
    assert strip_ipc_html("  G01H13/00  ") == ["G01H13/00"]


def test_strip_ipc_html_trims_inner_whitespace_only_at_edges():
    """Whitespace inside the code itself (between subgroup parts) stays."""
    raw = "<html><p>  E04C 3/34  </p></html>"
    assert strip_ipc_html(raw) == ["E04C 3/34"]


def test_strip_ipc_html_skips_empty_p_tags():
    """A bare <p></p> in the wrapper is filtered out, not surfaced as ''."""
    raw = "<html><p>A61M 5/31</p><p></p><p>A61J 1/14</p></html>"
    assert strip_ipc_html(raw) == ["A61M 5/31", "A61J 1/14"]


def test_strip_ipc_html_is_case_insensitive_on_tags():
    """Defensive: HTML tag case is irrelevant in the spec."""
    raw = "<HTML><P>A61M 5/31</P><P>A61J 1/14</P></HTML>"
    assert strip_ipc_html(raw) == ["A61M 5/31", "A61J 1/14"]

"""Unit tests for ``cd_extract_tasarim`` helpers.

Built one helper at a time. Each step adds its own test block so failures
point cleanly at the unit under test.
"""

from cd_extract_tasarim import (
    decode_hsqldb_escapes,
    split_locarno_codes,
)


# ---------------------------------------------------------------------------
# Step 2.1 — decode_hsqldb_escapes
# ---------------------------------------------------------------------------

def test_decode_hsqldb_escapes_passthrough_ascii():
    """ASCII strings come through untouched."""
    assert decode_hsqldb_escapes("hello world") == "hello world"
    assert decode_hsqldb_escapes("2016/01059") == "2016/01059"


def test_decode_hsqldb_escapes_handles_none_and_empty():
    """None and empty input return empty string (no NULL leakage)."""
    assert decode_hsqldb_escapes(None) == ""
    assert decode_hsqldb_escapes("") == ""


def test_decode_hsqldb_escapes_decodes_real_tasarim_holder_title():
    """Captured directly from 240/idbulletin.log IDHOLDER row 2016/01059::

        B\\u0130RL\\u0130K MENFEZ HAV. EK\\u0130P. SANAY\\u0130 T\\u0130CARET
        -> BİRLİK MENFEZ HAV. EKİP. SANAYİ TİCARET
    """
    raw = "B\\u0130RL\\u0130K MENFEZ HAV. EK\\u0130P. SANAY\\u0130 T\\u0130CARET"
    assert decode_hsqldb_escapes(raw) == "BİRLİK MENFEZ HAV. EKİP. SANAYİ TİCARET"


def test_decode_hsqldb_escapes_decodes_country_field():
    """``T\\u00dcRK\\u0130YE`` -> ``TÜRKİYE`` — real value from IDHOLDER.COUNTRY."""
    assert decode_hsqldb_escapes("T\\u00dcRK\\u0130YE") == "TÜRKİYE"


def test_decode_hsqldb_escapes_handles_full_lower_alphabet():
    """All five Turkish-specific lower-case chars in one string."""
    raw = "\\u0131 \\u011f \\u00fc \\u015f \\u00f6 \\u00e7"
    # ı ğ ü ş ö ç
    assert decode_hsqldb_escapes(raw) == "ı ğ ü ş ö ç"


def test_decode_hsqldb_escapes_handles_tab_and_newline():
    """``\\u0009`` -> tab, ``\\u000a`` -> newline. The Tasarim log contains
    both — tab characters appear in DDL lines (CREATE TABLE column
    separators) inside ``idbulletin.log``."""
    raw = "col1\\u0009col2\\u000aline2"
    assert decode_hsqldb_escapes(raw) == "col1\tcol2\nline2"


# ---------------------------------------------------------------------------
# Step 2.2 — split_locarno_codes
# ---------------------------------------------------------------------------

def test_split_locarno_codes_empty_inputs():
    """Empty / None / whitespace-only -> empty list (no NULL or [''] leakage)."""
    assert split_locarno_codes(None) == []
    assert split_locarno_codes("") == []
    assert split_locarno_codes("   ") == []


def test_split_locarno_codes_single_code():
    """Most common shape across 240_CD.rar: a single ``NN-NN`` code."""
    assert split_locarno_codes("25-02") == ["25-02"]


def test_split_locarno_codes_two_codes_no_space():
    """Real value from 240_CD.rar IDDOSSIER for application 2016/01186."""
    assert split_locarno_codes("12-16,12-05") == ["12-16", "12-05"]


def test_split_locarno_codes_comma_space_separator():
    """Real edge case from 240_CD.rar IDDOSSIER for application 2016/00576::

        '07-01, 32-00' -> ["07-01", "32-00"]

    The CD intermittently inserts a space after the comma; the helper
    strips per-entry whitespace rather than splitting on a strict
    ``","`` boundary.
    """
    assert split_locarno_codes("07-01, 32-00") == ["07-01", "32-00"]


def test_split_locarno_codes_three_codes():
    """Real value from 240_CD.rar IDDOSSIER for application 2016/01173."""
    assert split_locarno_codes("06-04,06-02,06-05") == ["06-04", "06-02", "06-05"]


def test_split_locarno_codes_filters_empty_entries():
    """Trailing comma / consecutive commas: empties dropped, real codes kept."""
    assert split_locarno_codes("06-04,") == ["06-04"]
    assert split_locarno_codes(",06-04") == ["06-04"]
    assert split_locarno_codes("06-04,,06-02") == ["06-04", "06-02"]


def test_split_locarno_codes_preserves_dotted_variant():
    """Helper does not normalise ``NN-NN`` vs ``NN.NN``. ``pdf_extract_tasarim``
    accepts both shapes; the CD ships dashed but defensively pass dotted
    through verbatim if it ever appears."""
    assert split_locarno_codes("06.01,26-05") == ["06.01", "26-05"]

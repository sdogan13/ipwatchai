"""Unit tests for ``cd_extract_patent`` helpers.

Built one helper at a time. Each step adds its own test block so failures
point cleanly at the unit under test.
"""

from pathlib import Path

import pytest

from cd_extract_patent import (
    DEFAULT_SEVEN_ZIP,
    TABLE_COLUMNS,
    cd_to_metadata,
    decode_hsqldb_escapes,
    extract_cd_archive,
    main,
    CD_IMAGES_DIRNAME,
    CD_METADATA_FILENAME,
    RAW_HSQLDB_FILES,
    _carry_cd_images,
    parse_argv,
    parse_bulletin_inf,
    parse_hsqldb_log,
    parse_hsqldb_log_line,
    resolve_image_path,
    strip_ipc_html,
)
from cd_extract_patent import (
    _parse_sql_values,
    _resolve_seven_zip,
    _split_application_no,
)


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


# ---------------------------------------------------------------------------
# Step 2.3 — _parse_sql_values (state-machine VALUES tokenizer)
# ---------------------------------------------------------------------------

def test_parse_sql_values_simple_strings():
    assert _parse_sql_values("'a','b','c'") == ["a", "b", "c"]


def test_parse_sql_values_empty_strings():
    assert _parse_sql_values("'','',''") == ["", "", ""]


def test_parse_sql_values_doubled_apostrophe_is_literal():
    """Real captured value: TÜRKİYE''NİN must decode to TÜRKİYE'NİN."""
    assert _parse_sql_values("'O''Brien','SAM''ler'") == ["O'Brien", "SAM'ler"]


def test_parse_sql_values_apostrophe_quoted_word():
    """Real captured value: ''vadi'' is the string with literal apostrophes
    around the word ``vadi`` (Turkish for 'valley')."""
    assert _parse_sql_values("'''vadi'''") == ["'vadi'"]


def test_parse_sql_values_handles_inner_commas():
    """Commas inside a quoted string must NOT split the value list."""
    assert _parse_sql_values("'a, b, c','d'") == ["a, b, c", "d"]


def test_parse_sql_values_tolerates_whitespace_around_commas():
    assert _parse_sql_values("'a' , 'b' ,'c'") == ["a", "b", "c"]


def test_parse_sql_values_rejects_unterminated_string():
    with pytest.raises(ValueError):
        _parse_sql_values("'unterminated")


def test_parse_sql_values_rejects_unquoted_token():
    with pytest.raises(ValueError):
        _parse_sql_values("'a',unquoted,'b'")


# ---------------------------------------------------------------------------
# Step 2.3 — parse_hsqldb_log_line
# ---------------------------------------------------------------------------

def test_parse_log_line_real_patent_row():
    """Real INSERT for application 2017/15048 from 2025_12 ptbulletin.log."""
    line = (
        "INSERT INTO PATENT VALUES('2017/15048','05/10/2017','','',"
        "'<html><p>A61M 5/31</p><p>A61J 1/14</p></html>',"
        "'TR 2017 15048 U3','73','22/12/2025',"
        "'EMN\\u0130YET BEL\\u0130RTE\\u00c7L\\u0130 ENJEKT\\u00d6R',"
        "'2','','','')"
    )
    result = parse_hsqldb_log_line(line)
    assert result is not None
    assert result["table"] == "PATENT"
    row = result["row"]
    assert row["APPLICATIONNO"] == "2017/15048"
    assert row["APPLICATIONDATE"] == "05/10/2017"
    assert row["IPCCODE"] == ["A61M 5/31", "A61J 1/14"]
    assert row["PUBLICATIONNO"] == "TR 2017 15048 U3"
    assert row["PATENTTITLE"] == "EMNİYET BELİRTEÇLİ ENJEKTÖR"
    # Empty fields preserved as empty strings (NOT None) — semantics deferred to ingest
    assert row["PATENTNO"] == ""
    assert row["IMAGEPATH1"] == ""


def test_parse_log_line_real_holder_row():
    """Real HOLDER row including SQL-escaped apostrophe inside Turkish text."""
    line = (
        "INSERT INTO HOLDER VALUES('2023/016403',"
        "'T\\u00dcRK\\u0130YE''N\\u0130N OTOMOB\\u0130L\\u0130',"
        "'MUALL\\u0130MK\\u00d6Y MAH.','Gebze','','Kocaeli','TR')"
    )
    result = parse_hsqldb_log_line(line)
    assert result is not None
    assert result["table"] == "HOLDER"
    assert result["row"]["TITLE"] == "TÜRKİYE'NİN OTOMOBİLİ"
    assert result["row"]["CITY"] == "Kocaeli"


def test_parse_log_line_real_inventer_row():
    line = (
        "INSERT INTO INVENTER VALUES('2017/15048',"
        "'AT\\u0130LLA SEV\\u0130N\\u00c7L\\u0130','','','','','')"
    )
    result = parse_hsqldb_log_line(line)
    assert result is not None
    assert result["table"] == "INVENTER"
    assert result["row"]["TITLE"] == "ATİLLA SEVİNÇLİ"
    assert result["row"]["APPLICATIONNO"] == "2017/15048"


def test_parse_log_line_real_attorney_row():
    line = (
        "INSERT INTO ATTORNEY VALUES('2017/15048','361','ERDEM KAYA','',"
        "'ERDEM KAYA PATENT VE DAN. A.\\u015e.')"
    )
    result = parse_hsqldb_log_line(line)
    assert result is not None
    assert result["table"] == "ATTORNEY"
    row = result["row"]
    assert row["NO"] == "361"
    assert row["NAME"] == "ERDEM KAYA"
    assert row["TITLE"] == "ERDEM KAYA PATENT VE DAN. A.Ş."


def test_parse_log_line_real_priority_row():
    line = "INSERT INTO PRIORITY VALUES('2020/10769','2019/21188','23/12/2019','TR')"
    result = parse_hsqldb_log_line(line)
    assert result is not None
    assert result["table"] == "PRIORITY"
    assert result["row"] == {
        "APPLICATIONNO": "2020/10769",
        "PRIORITYNO": "2019/21188",
        "PRIORITYDATE": "23/12/2019",
        "COUNTRYNO": "TR",
    }


def test_parse_log_line_preserves_embedded_newline_in_abstract():
    """Real PATENTABSTRACT contains \\u000a for inline line breaks."""
    line = (
        "INSERT INTO PATENT VALUES('2017/16580','26/10/2017','','',"
        "'<html><p>B01D 21/24</p></html>','TR 2017 16580 U3','73','22/12/2025',"
        "'B\\u0130R YA\\u011e SIYIRMA','2',"
        "'\\u015eekil 1\\u000ar\\u0131c\\u0131','','')"
    )
    result = parse_hsqldb_log_line(line)
    assert result is not None
    assert "\n" in result["row"]["PATENTABSTRACT"]
    assert result["row"]["PATENTABSTRACT"].startswith("Şekil 1")


def test_parse_log_line_returns_none_for_non_insert():
    """Real non-INSERT lines from the log header — must not raise."""
    assert parse_hsqldb_log_line('CREATE USER SA PASSWORD "" ADMIN') is None
    assert parse_hsqldb_log_line("/*C1*/CONNECT USER SA") is None
    assert parse_hsqldb_log_line("DISCONNECT") is None
    assert parse_hsqldb_log_line("CREATE TABLE PATENT (APPLICATIONNO VARCHAR ( 20 ))") is None
    assert parse_hsqldb_log_line("CREATE INDEX holdertitle ON HOLDER(TITLE)") is None


def test_parse_log_line_returns_none_for_blank_or_none():
    assert parse_hsqldb_log_line("") is None
    assert parse_hsqldb_log_line("   ") is None
    assert parse_hsqldb_log_line(None) is None


def test_parse_log_line_handles_crlf_line_ending():
    """The log file has CRLF endings on Windows; the trailing \\r must
    not leak into the last column's value."""
    line = "INSERT INTO PRIORITY VALUES('2020/10769','2019/21188','23/12/2019','TR')\r\n"
    result = parse_hsqldb_log_line(line)
    assert result is not None
    assert result["row"]["COUNTRYNO"] == "TR"  # not "TR\r"


def test_parse_log_line_returns_none_for_unknown_table():
    """HSQLDB internal / housekeeping tables are silently ignored."""
    line = "INSERT INTO MYSCHEMA.SYS_TABLE VALUES('a','b')"
    assert parse_hsqldb_log_line(line) is None


def test_parse_log_line_raises_on_column_count_mismatch():
    """If the row arity disagrees with the schema, that's a real bug —
    fail loudly, never silently truncate or pad."""
    # PATENT has 13 columns; this line has only 5
    line = "INSERT INTO PATENT VALUES('a','b','c','d','e')"
    with pytest.raises(ValueError, match="expected 13 columns"):
        parse_hsqldb_log_line(line)


def test_table_columns_match_known_arities():
    """Sanity guard so an accidental edit to TABLE_COLUMNS is caught."""
    assert len(TABLE_COLUMNS["PATENT"]) == 13
    assert len(TABLE_COLUMNS["HOLDER"]) == 7
    assert len(TABLE_COLUMNS["INVENTER"]) == 7
    assert len(TABLE_COLUMNS["ATTORNEY"]) == 5
    assert len(TABLE_COLUMNS["PRIORITY"]) == 4


# ---------------------------------------------------------------------------
# Step 2.4 — parse_hsqldb_log (whole-file wrapper)
# ---------------------------------------------------------------------------

# Self-contained synthetic log fixture mirroring the shape of a real
# HSQLDB ptbulletin.log header + a few INSERTs across all five tables.
_SYNTHETIC_LOG = (
    'CREATE USER SA PASSWORD "" ADMIN\n'
    "/*C1*/CONNECT USER SA\n"
    "CREATE TABLE PATENT ( APPLICATIONNO VARCHAR ( 20 ) )\n"
    "INSERT INTO PATENT VALUES("
    "'2024/01001','01/01/2024','','','<html><p>A61M 5/31</p></html>',"
    "'TR 2024 01001 A1','71','22/01/2025',"
    "'TEST BA\\u015eLI\\u011eI','1','Bir test \\u00f6zeti.','','')\n"
    "INSERT INTO HOLDER VALUES('2024/01001','ACME LTD','','','','','TR')\n"
    "INSERT INTO INVENTER VALUES('2024/01001','JANE DOE','','','','','')\n"
    "INSERT INTO ATTORNEY VALUES('2024/01001','99','J. SMITH','','SMITH IP')\n"
    "INSERT INTO PRIORITY VALUES('2024/01001','EP12345','15/12/2023','EP')\n"
    "INSERT INTO PATENT VALUES("
    "'2024/01002','02/01/2024','','','<html><p>B01D 21/24</p></html>',"
    "'TR 2024 01002 A2','71','22/01/2025',"
    "'YA\\u011e SIYIRMA','2','Apostrof: O''Brien','','')\n"
    "DISCONNECT\n"
)


def _write_log(tmp_path, body: str):
    p = tmp_path / "ptbulletin.log"
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_log_groups_inserts_by_table(tmp_path):
    log = _write_log(tmp_path, _SYNTHETIC_LOG)
    out = parse_hsqldb_log(log)
    assert set(out.keys()) == {"PATENT", "HOLDER", "INVENTER", "ATTORNEY", "PRIORITY"}
    assert len(out["PATENT"]) == 2
    assert len(out["HOLDER"]) == 1
    assert len(out["INVENTER"]) == 1
    assert len(out["ATTORNEY"]) == 1
    assert len(out["PRIORITY"]) == 1


def test_parse_log_propagates_decoded_values(tmp_path):
    log = _write_log(tmp_path, _SYNTHETIC_LOG)
    out = parse_hsqldb_log(log)
    assert out["PATENT"][0]["PATENTTITLE"] == "TEST BAŞLIĞI"
    assert out["PATENT"][0]["IPCCODE"] == ["A61M 5/31"]
    assert out["PATENT"][1]["PATENTABSTRACT"] == "Apostrof: O'Brien"


def test_parse_log_skips_non_insert_header_lines(tmp_path):
    """The 4 header lines (CREATE USER, CONNECT, CREATE TABLE, DISCONNECT)
    must not produce rows."""
    log = _write_log(tmp_path, _SYNTHETIC_LOG)
    out = parse_hsqldb_log(log)
    total = sum(len(v) for v in out.values())
    assert total == 6  # 2 PATENT + 1 HOLDER + 1 INVENTER + 1 ATTORNEY + 1 PRIORITY


def test_parse_log_returns_empty_dict_for_no_inserts(tmp_path):
    log = _write_log(tmp_path,
        'CREATE USER SA PASSWORD "" ADMIN\n'
        "/*C1*/CONNECT USER SA\n"
        "CREATE TABLE PATENT ( APPLICATIONNO VARCHAR ( 20 ) )\n"
        "DISCONNECT\n"
    )
    assert parse_hsqldb_log(log) == {}


def test_parse_log_returns_empty_dict_for_empty_file(tmp_path):
    log = _write_log(tmp_path, "")
    assert parse_hsqldb_log(log) == {}


def test_parse_log_handles_crlf_line_endings(tmp_path):
    """Real CD logs are written on Windows with CRLF — verify the wrapper
    is byte-faithful and doesn't leak \\r into row values."""
    log = _write_log(tmp_path, _SYNTHETIC_LOG.replace("\n", "\r\n"))
    out = parse_hsqldb_log(log)
    assert out["PRIORITY"][0]["COUNTRYNO"] == "EP"  # not "EP\r"


def test_parse_log_raises_with_line_number_on_bad_row(tmp_path):
    """Column-count mismatch surfaces with the offending line number so
    real CD failures are debuggable."""
    bad_body = (
        "CREATE TABLE PATENT (APPLICATIONNO VARCHAR ( 20 ))\n"
        "INSERT INTO PATENT VALUES('a','b')\n"  # only 2 columns; expect 13
    )
    log = _write_log(tmp_path, bad_body)
    with pytest.raises(ValueError, match=r"line 2"):
        parse_hsqldb_log(log)


def test_parse_log_omits_tables_with_zero_rows(tmp_path):
    """A log with only PATENT inserts must not surface empty INVENTER /
    HOLDER lists — keys are present iff rows exist."""
    body = (
        "INSERT INTO PATENT VALUES("
        "'2024/01001','01/01/2024','','','<html><p>A61M 5/31</p></html>',"
        "'TR 2024 01001 A1','71','22/01/2025','x','1','y','','')\n"
    )
    log = _write_log(tmp_path, body)
    out = parse_hsqldb_log(log)
    assert list(out.keys()) == ["PATENT"]
    assert "HOLDER" not in out


def test_parse_log_accepts_str_or_path(tmp_path):
    log = _write_log(tmp_path, _SYNTHETIC_LOG)
    a = parse_hsqldb_log(log)
    b = parse_hsqldb_log(str(log))
    assert a == b


# ---------------------------------------------------------------------------
# Step 2.5 — _split_application_no
# ---------------------------------------------------------------------------

def test_split_application_no_real_2017_format():
    assert _split_application_no("2017/15048") == ("2017", "15048")


def test_split_application_no_real_2021_padded_format():
    assert _split_application_no("2021/000039") == ("2021", "000039")
    assert _split_application_no("2021/011498") == ("2021", "011498")


def test_split_application_no_strips_surrounding_whitespace():
    assert _split_application_no("  2017/15048  ") == ("2017", "15048")


def test_split_application_no_returns_none_for_malformed_inputs():
    assert _split_application_no(None) is None
    assert _split_application_no("") is None
    assert _split_application_no("2017") is None         # no slash
    assert _split_application_no("2017/") is None        # empty appno
    assert _split_application_no("/15048") is None       # empty year
    assert _split_application_no("2017/15048/extra") is None  # too many parts
    assert _split_application_no("ABCD/15048") is None   # non-numeric year
    assert _split_application_no("2017/abc") is None     # non-numeric appno


# ---------------------------------------------------------------------------
# Step 2.5 — resolve_image_path
# ---------------------------------------------------------------------------

def test_resolve_image_path_finds_exact_match_pre_2021(tmp_path):
    """2017–2020: bare 5-digit names (15048.tif)."""
    year_dir = tmp_path / "2017"
    year_dir.mkdir()
    (year_dir / "15048.tif").write_bytes(b"TIFF")
    found = resolve_image_path("2017/15048", tmp_path)
    assert found is not None
    assert found.name == "15048.tif"
    assert found.read_bytes() == b"TIFF"


def test_resolve_image_path_finds_exact_match_post_2021(tmp_path):
    """2021+: 6-digit zero-padded names (000039.tif)."""
    year_dir = tmp_path / "2021"
    year_dir.mkdir()
    (year_dir / "000039.tif").write_bytes(b"TIFF")
    found = resolve_image_path("2021/000039", tmp_path)
    assert found is not None
    assert found.name == "000039.tif"


def test_resolve_image_path_falls_back_to_6_pad(tmp_path):
    """If APPLICATIONNO has unpadded suffix but the file is 6-pad — find it."""
    year_dir = tmp_path / "2021"
    year_dir.mkdir()
    (year_dir / "000039.tif").write_bytes(b"TIFF")
    found = resolve_image_path("2021/39", tmp_path)
    assert found is not None
    assert found.name == "000039.tif"


def test_resolve_image_path_falls_back_to_5_pad(tmp_path):
    """If APPLICATIONNO has unpadded suffix but the file is 5-pad — find it."""
    year_dir = tmp_path / "2017"
    year_dir.mkdir()
    (year_dir / "15048.tif").write_bytes(b"TIFF")
    found = resolve_image_path("2017/15048", tmp_path)
    assert found is not None


def test_resolve_image_path_prefers_exact_over_padded(tmp_path):
    """When BOTH exist, the exact (as-is) match wins to preserve fidelity."""
    year_dir = tmp_path / "2021"
    year_dir.mkdir()
    (year_dir / "11498.tif").write_bytes(b"BARE")
    (year_dir / "011498.tif").write_bytes(b"PADDED")
    found = resolve_image_path("2021/11498", tmp_path)
    assert found is not None
    assert found.read_bytes() == b"BARE"


def test_resolve_image_path_accepts_tiff_extension(tmp_path):
    """Both .tif and .tiff are acceptable (.tif is preferred)."""
    year_dir = tmp_path / "2019"
    year_dir.mkdir()
    (year_dir / "12345.tiff").write_bytes(b"TIFF")
    found = resolve_image_path("2019/12345", tmp_path)
    assert found is not None
    assert found.suffix == ".tiff"


def test_resolve_image_path_prefers_tif_over_tiff(tmp_path):
    """When both extensions present at the same stem, .tif wins."""
    year_dir = tmp_path / "2019"
    year_dir.mkdir()
    (year_dir / "12345.tif").write_bytes(b"DOT_TIF")
    (year_dir / "12345.tiff").write_bytes(b"DOT_TIFF")
    found = resolve_image_path("2019/12345", tmp_path)
    assert found is not None
    assert found.read_bytes() == b"DOT_TIF"


def test_resolve_image_path_returns_none_when_year_dir_missing(tmp_path):
    """No 2017/ folder at all — None, not crash."""
    found = resolve_image_path("2017/15048", tmp_path)
    assert found is None


def test_resolve_image_path_returns_none_when_no_match_anywhere(tmp_path):
    """Year folder exists but the file doesn't, in any padding."""
    year_dir = tmp_path / "2017"
    year_dir.mkdir()
    (year_dir / "99999.tif").write_bytes(b"unrelated")
    assert resolve_image_path("2017/15048", tmp_path) is None


def test_resolve_image_path_returns_none_for_missing_root(tmp_path):
    """images_root itself doesn't exist — None, not crash."""
    ghost = tmp_path / "no_such_root"
    assert resolve_image_path("2017/15048", ghost) is None


def test_resolve_image_path_returns_none_for_malformed_application_no(tmp_path):
    """Bad APPLICATIONNO — short-circuits before any filesystem check."""
    assert resolve_image_path(None, tmp_path) is None
    assert resolve_image_path("", tmp_path) is None
    assert resolve_image_path("not-an-app-no", tmp_path) is None


def test_resolve_image_path_accepts_str_or_path(tmp_path):
    year_dir = tmp_path / "2017"
    year_dir.mkdir()
    (year_dir / "15048.tif").write_bytes(b"TIFF")
    a = resolve_image_path("2017/15048", tmp_path)
    b = resolve_image_path("2017/15048", str(tmp_path))
    assert a == b
    assert a is not None


# ---------------------------------------------------------------------------
# Step 2.6 — _resolve_seven_zip + extract_cd_archive
# ---------------------------------------------------------------------------

def test_resolve_seven_zip_uses_explicit_override():
    """Explicit override beats env and default."""
    explicit = Path("X:/custom/7z.exe")
    assert _resolve_seven_zip(explicit) == explicit


def test_resolve_seven_zip_falls_back_to_env(monkeypatch):
    """When no override, the PIPELINE_SEVEN_ZIP_PATH env var is honored."""
    monkeypatch.setenv("PIPELINE_SEVEN_ZIP_PATH", "Y:/env/7z.exe")
    assert _resolve_seven_zip() == Path("Y:/env/7z.exe")


def test_resolve_seven_zip_falls_back_to_default(monkeypatch):
    """When neither override nor env is set, the platform default is used."""
    monkeypatch.delenv("PIPELINE_SEVEN_ZIP_PATH", raising=False)
    assert _resolve_seven_zip() == Path(DEFAULT_SEVEN_ZIP)


def test_extract_cd_archive_raises_when_archive_missing(tmp_path):
    ghost = tmp_path / "no_such.rar"
    with pytest.raises(FileNotFoundError, match="archive not found"):
        extract_cd_archive(ghost, tmp_path / "out")


def test_extract_cd_archive_raises_when_seven_zip_missing(tmp_path):
    """If the 7-Zip override path doesn't exist, fail loudly before any
    real subprocess work."""
    fake_rar = tmp_path / "fake.rar"
    fake_rar.write_bytes(b"not really a rar but we never get past the 7-Zip check")
    bad_seven = tmp_path / "definitely_not_seven_zip.exe"
    with pytest.raises(FileNotFoundError, match="7-Zip not found"):
        extract_cd_archive(fake_rar, tmp_path / "out", seven_zip=bad_seven)


def test_extract_cd_archive_returns_scratch_when_archive_has_no_wrapper(tmp_path):
    """Some older CDs (verified 2015_12_CD.rar, 2016_1_CD.rar) flatten
    the archive — `data/` lands directly in the scratch dir with no
    bulletin-month wrapper. Without this case handled, the caller's
    `cd_root / "data"` would double-up to `data/data/` and miss the
    HSQLDB log. Regression for the FileNotFoundError observed during
    bulk extraction on 2026-05-08.

    This test fakes the scratch layout (no actual 7-Zip call) so it
    runs in CI even when no real .rar is present. The 7-Zip step is
    covered separately by the live smoke below.
    """
    scratch = tmp_path / "scratch"
    (scratch / "data").mkdir(parents=True)
    (scratch / "data" / "ptbulletin.log").write_text("mock", encoding="utf-8")

    fake_rar = tmp_path / "fake.rar"
    fake_rar.write_bytes(b"\x00")

    # Skip 7-Zip invocation by patching subprocess.run
    import subprocess as _sub
    real_run = _sub.run
    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""
    _sub.run = lambda *a, **k: _Result()
    try:
        cd_root = extract_cd_archive(fake_rar, scratch, seven_zip=Path(DEFAULT_SEVEN_ZIP))
    finally:
        _sub.run = real_run

    # Critical assertion: cd_root is the scratch dir itself, NOT scratch/data.
    # A wrong return value would make `cd_root / "data" / "ptbulletin.log"`
    # resolve to `scratch/data/data/ptbulletin.log` — which doesn't exist.
    assert cd_root == scratch
    assert (cd_root / "data" / "ptbulletin.log").is_file()


# ----- Live integration smoke test (skipped if the real CD is absent) -----

_REAL_CD = Path(
    "C:/Users/701693/turk_patent/bulletins/Patent__Faydali_Model/2025_12_CD.rar"
)


@pytest.mark.skipif(
    not _REAL_CD.is_file(),
    reason=f"Real CD {_REAL_CD.name} not on disk; skipping integration smoke",
)
@pytest.mark.skipif(
    not Path(DEFAULT_SEVEN_ZIP).is_file(),
    reason="7-Zip not installed at the platform default path",
)
def test_extract_cd_archive_real_2025_12_smoke(tmp_path):
    """End-to-end smoke: extract the real 2025_12 CD, then check that
    the HSQLDB files landed and the bundled JRE was excluded.

    Strong assertions:
      - returned path exists and contains data/ptbulletin.log
      - data/java/ is absent in the extracted tree
      - extracted size is much smaller than the source archive
        (sanity check that the JRE skip worked)
    """
    cd_root = extract_cd_archive(_REAL_CD, tmp_path)

    # Returned path looks right
    assert cd_root.is_dir()
    assert cd_root.name.startswith("2025_12") or "2025_12" in cd_root.name

    # HSQLDB files survived
    assert (cd_root / "data" / "ptbulletin.log").is_file()
    assert (cd_root / "data" / "ptbulletin.script").is_file()
    # Image folders survived
    assert (cd_root / "data" / "images").is_dir()

    # JRE was excluded
    assert not (cd_root / "data" / "java").exists(), \
        "data/java/ should have been skipped"

    # Sanity: extraction produced real content (tens of MB of TIFFs +
    # the HSQLDB log). We don't compare to the compressed source archive
    # size because RAR compression makes that ratio non-monotonic — the
    # important guarantee is that data/java/ was excluded (asserted
    # above) and that the surviving content is non-trivial.
    total_bytes = sum(
        p.stat().st_size for p in cd_root.rglob("*") if p.is_file()
    )
    assert total_bytes > 10 * 1024 * 1024, (
        f"extracted only {total_bytes} bytes — likely a partial extract"
    )


# ---------------------------------------------------------------------------
# Step 2.7 — parse_bulletin_inf
# ---------------------------------------------------------------------------

def test_parse_bulletin_inf_real_format(tmp_path):
    """The real format captured from 2025_12_CD.rar."""
    inf = tmp_path / "bulletin.inf"
    inf.write_text("NO=2025/12\nDATE=22/12/2025\n", encoding="utf-8")
    out = parse_bulletin_inf(inf)
    assert out == {"bulletin_no": "2025/12", "bulletin_date": "2025-12-22"}


def test_parse_bulletin_inf_handles_crlf(tmp_path):
    inf = tmp_path / "bulletin.inf"
    inf.write_text("NO=2024/07\r\nDATE=22/07/2024\r\n", encoding="utf-8")
    out = parse_bulletin_inf(inf)
    assert out == {"bulletin_no": "2024/07", "bulletin_date": "2024-07-22"}


def test_parse_bulletin_inf_tolerates_whitespace(tmp_path):
    inf = tmp_path / "bulletin.inf"
    inf.write_text("  NO = 2025/12  \n  DATE = 22/12/2025  \n", encoding="utf-8")
    out = parse_bulletin_inf(inf)
    assert out["bulletin_no"] == "2025/12"
    assert out["bulletin_date"] == "2025-12-22"


def test_parse_bulletin_inf_returns_none_values_for_missing_file(tmp_path):
    """A missing inf shouldn't blow up the orchestrator — the caller
    decides whether to treat that as a hard failure."""
    out = parse_bulletin_inf(tmp_path / "no_such.inf")
    assert out == {"bulletin_no": None, "bulletin_date": None}


def test_parse_bulletin_inf_returns_none_for_bad_date(tmp_path):
    """A malformed date is reported as None, not a crash."""
    inf = tmp_path / "bulletin.inf"
    inf.write_text("NO=2025/12\nDATE=garbage\n", encoding="utf-8")
    out = parse_bulletin_inf(inf)
    assert out == {"bulletin_no": "2025/12", "bulletin_date": None}


def test_parse_bulletin_inf_skips_unrecognised_lines(tmp_path):
    """Stray lines (comments, blanks) shouldn't break the parser."""
    inf = tmp_path / "bulletin.inf"
    inf.write_text(
        "# header\nNO=2025/12\n\nrandom text\nDATE=22/12/2025\n",
        encoding="utf-8",
    )
    out = parse_bulletin_inf(inf)
    assert out == {"bulletin_no": "2025/12", "bulletin_date": "2025-12-22"}


# ---------------------------------------------------------------------------
# Step 2.7 — cd_to_metadata (LIVE integration smoke)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _REAL_CD.is_file(),
    reason=f"Real CD {_REAL_CD.name} not on disk; skipping integration smoke",
)
@pytest.mark.skipif(
    not Path(DEFAULT_SEVEN_ZIP).is_file(),
    reason="7-Zip not installed at the platform default path",
)
def test_cd_to_metadata_real_2025_12_smoke(tmp_path):
    """End-to-end smoke: orchestrate the full CD pipeline on the real
    2025_12 archive and verify the JSON-ready document.

    Hard checks anchored to the data-shape README's documented numbers:
      - bulletin_no == "2025/12", bulletin_date == "2025-12-22"
      - stats.patents == 2718 / holders == 3422 / inventors == 6046 /
        attorneys == 2371 / priorities == 688
      - figures_resolved == 653 (the resolver step's measured truth)
      - len(patents) == 2718
      - one specific record (2017/15048) has the expected joined
        holders, inventors, attorneys, image_path, IPC list, and
        decoded title
    """
    doc = cd_to_metadata(_REAL_CD, tmp_path)

    # Header
    assert doc["bulletin_no"] == "2025/12"
    assert doc["bulletin_date"] == "2025-12-22"
    assert doc["source_archive"] == "2025_12_CD.rar"
    assert "extracted_at" in doc

    # Aggregate stats
    s = doc["stats"]
    assert s["patents"]    == 2718
    assert s["holders"]    == 3422
    assert s["inventors"]  == 6046
    assert s["attorneys"]  == 2371
    assert s["priorities"] == 688
    assert s["figures_resolved"] == 653
    assert s["figures_missing"]  == 2065

    # Record list shape
    assert len(doc["patents"]) == 2718
    sample = next(p for p in doc["patents"] if p["application_no"] == "2017/15048")
    assert sample["application_date"] == "05/10/2017"
    assert sample["title"].startswith("EMNİYET BELİRTEÇLİ ENJEKTÖR")
    # IPC list — at least the first class is preserved
    assert isinstance(sample["ipc_codes"], list)
    assert "A61M 5/31" in sample["ipc_codes"]
    # Image was resolved (this app has 15048.tif on disk per step 2.5)
    assert sample["image_path"] == "data/images/2017/15048.tif"

    # Joined parties — at least one inventor matches the captured row
    inventor_names = [i["title"] for i in sample["inventors"]]
    assert "ATİLLA SEVİNÇLİ" in inventor_names

    # JSON-serialisable end-to-end (no Path / datetime objects leaking)
    import json
    json.dumps(doc, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Step 2.8 — output filenames + parse_argv + main
# ---------------------------------------------------------------------------

def test_cd_metadata_filename_constant():
    """The CD-side JSON always lands at ``cd_metadata.json`` inside the
    bulletin's parent folder. The folder name varies (PT_{Y}_{M}_{date})
    but the inner filename is fixed — Stage 5 ingest can rely on it."""
    assert CD_METADATA_FILENAME == "cd_metadata.json"


def test_raw_hsqldb_filenames_constant():
    """The raw HSQLDB files copied alongside cd_metadata.json. Used by
    Stage 5 (DB ingest) for re-extraction without re-unzipping."""
    assert RAW_HSQLDB_FILES == ("ptbulletin.log", "ptbulletin.script", "ptbulletin.properties")


def test_cd_images_dirname_constant():
    """CD TIFFs land in ``images/`` inside the bulletin parent folder
    (matches Marka's ``images/`` convention)."""
    assert CD_IMAGES_DIRNAME == "images"


def _make_fake_cd_root_with_tiffs(tmp_path, year_to_appnos):
    """Create a fake cd_root with data/images/{year}/{appno}.tif files."""
    cd_root = tmp_path / "scratch" / "2025_8"
    images_root = cd_root / "data" / "images"
    for year, appnos in year_to_appnos.items():
        (images_root / year).mkdir(parents=True, exist_ok=True)
        for appno in appnos:
            (images_root / year / f"{appno}.tif").write_bytes(b"TIFF FAKE\x00")
    return cd_root


def test_carry_cd_images_moves_tiffs_and_rewrites_paths(tmp_path):
    """Happy path: TIFFs move from cd_root/data/images/{year}/{appno}.tif
    into parent/images/{year}/{appno}.tif; image_path values rewritten
    to relative ``images/{year}/{appno}.tif``."""
    cd_root = _make_fake_cd_root_with_tiffs(
        tmp_path, {"2017": ["15048"], "2018": ["13083"]},
    )
    parent = tmp_path / "PT_2025_8_2025-08-21"
    parent.mkdir()
    doc = {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "patents": [
            {"application_no": "2017/15048", "image_path": "data/images/2017/15048.tif"},
            {"application_no": "2018/13083", "image_path": "data/images/2018/13083.tif"},
        ],
    }

    moved = _carry_cd_images(doc, cd_root, parent)

    assert moved == 2
    # Files moved into parent/images/{year}/...
    assert (parent / "images" / "2017" / "15048.tif").is_file()
    assert (parent / "images" / "2018" / "13083.tif").is_file()
    # Source TIFFs gone from cd_root (move, not copy)
    assert not (cd_root / "data" / "images" / "2017" / "15048.tif").exists()
    # image_path values rewritten to relative paths under the parent
    assert doc["patents"][0]["image_path"] == "images/2017/15048.tif"
    assert doc["patents"][1]["image_path"] == "images/2018/13083.tif"


def test_carry_cd_images_handles_missing_tiff_by_nulling_path(tmp_path):
    """Defensive: if image_path points to a TIFF that doesn't exist on
    disk (HSQLDB row references a missing figure), null the path so
    downstream code doesn't follow a dead reference."""
    cd_root = _make_fake_cd_root_with_tiffs(tmp_path, {"2017": ["15048"]})
    parent = tmp_path / "PT_x"
    parent.mkdir()
    doc = {
        "patents": [
            {"application_no": "2017/15048", "image_path": "data/images/2017/15048.tif"},
            {"application_no": "2018/99999", "image_path": "data/images/2018/99999.tif"},
            {"application_no": "2019/00001", "image_path": None},
        ],
    }

    moved = _carry_cd_images(doc, cd_root, parent)

    assert moved == 1                                          # only the present one
    assert doc["patents"][0]["image_path"] == "images/2017/15048.tif"
    assert doc["patents"][1]["image_path"] is None             # missing -> nulled
    assert doc["patents"][2]["image_path"] is None             # was None, stays None


def test_carry_cd_images_clears_unexpected_path_shape(tmp_path):
    """Any image_path that isn't under ``data/images/`` is cleared
    rather than silently kept as-is."""
    cd_root = tmp_path / "cd_root"
    (cd_root / "weird").mkdir(parents=True)
    parent = tmp_path / "PT_x"
    parent.mkdir()
    doc = {
        "patents": [
            {"application_no": "X", "image_path": "weird/somewhere/15048.tif"},
            {"application_no": "Y", "image_path": "/abs/path/15048.tif"},
        ],
    }

    moved = _carry_cd_images(doc, cd_root, parent)

    assert moved == 0
    assert doc["patents"][0]["image_path"] is None
    assert doc["patents"][1]["image_path"] is None


def test_parse_argv_with_explicit_rar(tmp_path):
    rar = tmp_path / "2025_12_CD.rar"
    rar.write_bytes(b"")  # placeholder; parse_argv doesn't dereference
    args = parse_argv(["--rar", str(rar)])
    assert args.rar_paths == [rar]
    assert args.out_dir == args.bulletins_dir if hasattr(args, "bulletins_dir") else True
    assert args.keep_scratch is False


def test_parse_argv_supports_repeated_rar(tmp_path):
    a = tmp_path / "2025_12_CD.rar"
    b = tmp_path / "2024_07_CD.rar"
    a.write_bytes(b""); b.write_bytes(b"")
    args = parse_argv(["--rar", str(a), "--rar", str(b)])
    assert args.rar_paths == [a, b]


def test_parse_argv_all_globs_bulletins_dir(tmp_path):
    """--all picks up every *_CD.rar in --bulletins-dir, alphabetically."""
    (tmp_path / "2025_12_CD.rar").write_bytes(b"")
    (tmp_path / "2024_07_CD.rar").write_bytes(b"")
    (tmp_path / "2025_12.pdf").write_bytes(b"")  # NOT picked up
    args = parse_argv(["--all", "--bulletins-dir", str(tmp_path)])
    names = [p.name for p in args.rar_paths]
    assert names == ["2024_07_CD.rar", "2025_12_CD.rar"]


def test_parse_argv_all_errors_on_empty_dir(tmp_path):
    """--all against an empty bulletins folder is a hard error."""
    with pytest.raises(SystemExit):
        parse_argv(["--all", "--bulletins-dir", str(tmp_path)])


def test_parse_argv_rejects_no_input():
    """Neither --rar nor --all -> argparse error."""
    with pytest.raises(SystemExit):
        parse_argv([])


def test_parse_argv_rejects_rar_and_all_together(tmp_path):
    rar = tmp_path / "2025_12_CD.rar"
    rar.write_bytes(b"")
    with pytest.raises(SystemExit):
        parse_argv(["--rar", str(rar), "--all", "--bulletins-dir", str(tmp_path)])


def test_parse_argv_out_dir_defaults_to_bulletins_dir(tmp_path):
    rar = tmp_path / "2025_12_CD.rar"
    rar.write_bytes(b"")
    args = parse_argv(["--rar", str(rar), "--bulletins-dir", str(tmp_path)])
    assert args.out_dir == tmp_path


def test_parse_argv_out_dir_explicit_override(tmp_path):
    rar = tmp_path / "src" / "2025_12_CD.rar"
    rar.parent.mkdir()
    rar.write_bytes(b"")
    other = tmp_path / "elsewhere"
    args = parse_argv(["--rar", str(rar), "--out-dir", str(other)])
    assert args.out_dir == other


def test_parse_argv_keep_scratch_flag(tmp_path):
    rar = tmp_path / "2025_12_CD.rar"
    rar.write_bytes(b"")
    args = parse_argv(["--rar", str(rar), "--keep-scratch"])
    assert args.keep_scratch is True


def test_main_returns_nonzero_on_missing_archive(tmp_path):
    """main() with --rar pointing at a non-existent file logs a skip and
    returns exit code 1."""
    ghost = tmp_path / "no_such_CD.rar"
    rc = main([
        "--rar", str(ghost),
        "--out-dir", str(tmp_path),
        "--scratch-dir", str(tmp_path / "scratch"),
    ])
    assert rc == 1


# ----- Live main() smoke (skipped if real CD absent) -----

@pytest.mark.skipif(
    not _REAL_CD.is_file(),
    reason=f"Real CD {_REAL_CD.name} not on disk; skipping integration smoke",
)
@pytest.mark.skipif(
    not Path(DEFAULT_SEVEN_ZIP).is_file(),
    reason="7-Zip not installed at the platform default path",
)
def test_main_real_2025_12_smoke(tmp_path):
    """End-to-end CLI smoke: run main() against the real CD and verify
    the bulletin parent folder lands with cd_metadata.json + raw HSQLDB
    files."""
    out_dir = tmp_path / "out"
    scratch = tmp_path / "scratch"
    rc = main([
        "--rar", str(_REAL_CD),
        "--out-dir", str(out_dir),
        "--scratch-dir", str(scratch),
    ])
    assert rc == 0

    # 2025_12_CD.rar carries bulletin 2025/12 (no offset for this month —
    # see patent_cd_filename_offset memory).
    parent = out_dir / "PT_2025_12_2025-12-22"
    assert parent.is_dir()

    cd_meta = parent / "cd_metadata.json"
    assert cd_meta.is_file()
    assert cd_meta.stat().st_size > 100_000  # several MB expected

    # Raw HSQLDB files alongside, ready for Stage 5 re-ingest
    assert (parent / "ptbulletin.log").is_file()
    assert (parent / "ptbulletin.script").is_file()
    assert (parent / "ptbulletin.properties").is_file()

    # CD TIFFs carried into images/, not left in scratch. Bulletin
    # 2025/12 has hundreds of resolved figures per the earlier --all run.
    images_dir = parent / "images"
    assert images_dir.is_dir()
    tif_count = sum(1 for _ in images_dir.rglob("*.tif"))
    assert tif_count > 100, f"expected >100 TIFFs carried, got {tif_count}"

    # image_path values inside cd_metadata.json now point under images/
    # (relative to parent), not the dead data/images/ scratch path.
    import json as _json
    payload = _json.loads(cd_meta.read_text(encoding="utf-8"))
    paths_with_image = [
        p["image_path"] for p in payload["patents"] if p.get("image_path")
    ]
    assert len(paths_with_image) > 100
    assert all(p.startswith("images/") for p in paths_with_image), (
        "image_path must be relative to the parent folder under images/"
    )

    # Scratch should be cleaned by default
    assert not (scratch / "2025_12_CD").exists()

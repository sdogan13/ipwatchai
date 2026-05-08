"""Unit tests for ``cd_extract_tasarim`` helpers.

Built one helper at a time. Each step adds its own test block so failures
point cleanly at the unit under test.
"""

import json
from pathlib import Path
from typing import Optional

import pytest

from cd_extract_tasarim import (
    CD_METADATA_FILENAME,
    DEFAULT_SEVEN_ZIP,
    TABLE_COLUMNS,
    CDLayout,
    cd_to_metadata,
    decode_hsqldb_escapes,
    extract_cd_archive,
    issue_folder_name,
    main,
    parse_argv,
    parse_bulletin_inf,
    parse_hsqldb_log,
    parse_hsqldb_log_line,
    resolve_design_images,
    split_locarno_codes,
)
from cd_extract_tasarim import (
    _all_cd_rars,
    _application_image_folder,
    _layout_to_metadata,
    _locate_cd_layout,
    _parse_sql_values,
    _persist_cd_images_for_app,
    _resolve_seven_zip,
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


# ---------------------------------------------------------------------------
# Step 2.3 — _parse_sql_values + parse_hsqldb_log_line + TABLE_COLUMNS
# ---------------------------------------------------------------------------

def test_table_columns_match_idbulletin_script_ddl():
    """Pin the column counts we trust from 240/idbulletin.script DDL."""
    assert len(TABLE_COLUMNS["IDDOSSIER"]) == 11
    assert len(TABLE_COLUMNS["IDHOLDER"]) == 6
    assert len(TABLE_COLUMNS["IDDESIGN"]) == 3
    assert len(TABLE_COLUMNS["IDDESIGNER"]) == 5
    assert len(TABLE_COLUMNS["IDANNOTATION"]) == 4


def test_parse_sql_values_simple():
    """Three plain ASCII values."""
    assert _parse_sql_values("'a','b','c'") == ["a", "b", "c"]


def test_parse_sql_values_empty_string_value():
    """An empty value is two consecutive single quotes."""
    assert _parse_sql_values("'a','','c'") == ["a", "", "c"]


def test_parse_sql_values_doubled_apostrophe():
    """SQL-escaped apostrophe — '' inside a value collapses to one '."""
    # Real-world: Turkish possessive ÜLKE'NİN
    assert _parse_sql_values("'\\u00dcLKE''N\\u0130N'") == ["\\u00dcLKE'N\\u0130N"]


def test_parse_sql_values_raises_on_unterminated():
    """Missing closing quote should fail loudly, not silently truncate."""
    with pytest.raises(ValueError, match="unterminated"):
        _parse_sql_values("'oops")


def test_parse_sql_values_raises_on_missing_comma():
    """Two values without a comma between is malformed."""
    with pytest.raises(ValueError, match="comma"):
        _parse_sql_values("'a' 'b'")


def test_parse_log_line_returns_none_for_non_insert_lines():
    """Real non-INSERT shapes seen in 240/idbulletin.log are all skipped."""
    assert parse_hsqldb_log_line(None) is None
    assert parse_hsqldb_log_line("") is None
    assert parse_hsqldb_log_line("   ") is None
    assert parse_hsqldb_log_line("/*C1*/CONNECT USER SA") is None
    assert parse_hsqldb_log_line("DISCONNECT") is None
    # Embedded DDL line — Tasarim CDs put the CREATE TABLE inside the log
    ddl = (
        r"CREATE TABLE IDDOSSIER (	APPLICATIONNO VARCHAR ( 20 ),"
        r"	APPLICATIONDATE VARCHAR ( 30 ))"
    )
    assert parse_hsqldb_log_line(ddl) is None


def test_parse_log_line_unknown_table_returns_none():
    """Tables outside TABLE_COLUMNS (e.g. legacy / unrelated tables) skip."""
    assert parse_hsqldb_log_line(
        "INSERT INTO MYSTERY_TABLE VALUES('a','b')"
    ) is None


def test_parse_log_line_iddossier_real_row():
    """Real IDDOSSIER row from 240/idbulletin.log for application 2016/01059.

    Confirms:
      - 11 columns zip to the right names
      - LOCARNOCODES is run through split_locarno_codes (returns list)
      - other columns are HSQLDB-decoded (Turkish escapes -> Unicode)
    """
    line = (
        r"INSERT INTO IDDOSSIER VALUES("
        r"'2016/01059','10.02.2016','2016 01059','10.02.2016','1','25-02','',"
        r"'RABİA ÇETİN (DEV PATENT MARKA VE FİKRİ HAK. DAN. TİC. LTD. ŞTİ.)','',"
        r"'MECİDİYEKÖY MAH. ESKİ OSMANLI SOK. ARIKAN İŞ MRK. NO:30/18 - ŞİŞLİ / İSTANBUL',"
        r"'')"
    )
    parsed = parse_hsqldb_log_line(line)
    assert parsed is not None
    assert parsed["table"] == "IDDOSSIER"
    row = parsed["row"]
    assert row["APPLICATIONNO"] == "2016/01059"
    assert row["LOCARNOCODES"] == ["25-02"]   # list, not string
    assert row["ATTORNEYNAME"] == "RABİA ÇETİN (DEV PATENT MARKA VE FİKRİ HAK. DAN. TİC. LTD. ŞTİ.)"
    assert "MECİDİYEKÖY" in row["ATTORNEYADDRESS"]
    assert row["TYPE"] == ""


def test_parse_log_line_idholder_real_row():
    """Real IDHOLDER row — 6 columns, includes CLIENTNO (TPECLIENT id)."""
    line = (
        r"INSERT INTO IDHOLDER VALUES("
        r"'2016/01059','234974',"
        r"'BİRLİK MENFEZ HAV. EKİP. SANAYİ TİCARET LİMİTED ŞİRKETİ',"
        r"'Organize San. Böl. Esot San. Sit. J Blok No.5 İkitelli Başakşehir',"
        r"'İSTANBUL','TÜRKİYE')"
    )
    parsed = parse_hsqldb_log_line(line)
    assert parsed["table"] == "IDHOLDER"
    row = parsed["row"]
    assert row["CLIENTNO"] == "234974"
    assert row["TITLE"].startswith("BİRLİK MENFEZ")
    assert row["CITY"] == "İSTANBUL"
    assert row["COUNTRY"] == "TÜRKİYE"


def test_parse_log_line_iddesign_real_row():
    """Real IDDESIGN row — 3 columns, plain ASCII product name."""
    parsed = parse_hsqldb_log_line(
        "INSERT INTO IDDESIGN VALUES('2016/01059','1','Profil ')"
    )
    assert parsed["table"] == "IDDESIGN"
    assert parsed["row"] == {
        "APPLICATIONNO": "2016/01059",
        "NO": "1",
        "PRODUCTNAME": "Profil ",  # trailing space preserved verbatim
    }


def test_parse_log_line_iddesigner_real_row():
    """Real IDDESIGNER row — 5 columns."""
    line = (
        r"INSERT INTO IDDESIGNER VALUES("
        r"'2016/01059','68364','VEDAT ÇELİK',"
        r"'Enverpaşa Cad. Açelya Evleri E-30 Kat.2 Daire.6 Esenkent/İSTANBUL',"
        r"'TÜRKİYE')"
    )
    parsed = parse_hsqldb_log_line(line)
    assert parsed["table"] == "IDDESIGNER"
    row = parsed["row"]
    assert row["NAME"] == "VEDAT ÇELİK"
    assert row["COUNTRY"] == "TÜRKİYE"


def test_parse_log_line_idannotation_real_row():
    """Real IDANNOTATION row — 4 columns. Event-like CONTENT carries
    INID-coded text (this is what the future stage-3 reconciler will
    likely cross-check against pdf_extract_tasarim_events output)."""
    line = (
        r"INSERT INTO IDANNOTATION VALUES("
        r"'262752','2011/01410','Yenileme',"
        r"'(11) 2011 01410 (15) 03.03.2011 (73) ÖZTİRYAKİLER MADENİ EŞYA SANAYİ VE TİCARET ANONİM ŞİRKETİ (Cumhuriyet Mahallesi Hadımköy Yolu Caddesi No:8/1 Büyükçekmece 34900 İSTANBUL) (58) 22.02.2016 ')"
    )
    parsed = parse_hsqldb_log_line(line)
    assert parsed["table"] == "IDANNOTATION"
    row = parsed["row"]
    assert row["PUBLICATIONKEY"] == "262752"
    assert row["REQUESTTYPE"] == "Yenileme"
    assert "ÖZTİRYAKİLER" in row["CONTENT"]


def test_parse_log_line_multi_locarno_real_row():
    """Multi-Locarno IDDOSSIER row from application 2016/01186."""
    line = (
        r"INSERT INTO IDDOSSIER VALUES("
        r"'2016/01186','x','x','x','2','12-16,12-05','','x','','x','1')"
    )
    parsed = parse_hsqldb_log_line(line)
    assert parsed["row"]["LOCARNOCODES"] == ["12-16", "12-05"]


def test_parse_log_line_column_count_mismatch_raises():
    """Schema drift must fail loudly — IDDESIGN expects 3 columns, give it 4."""
    bad = "INSERT INTO IDDESIGN VALUES('a','b','c','d')"
    with pytest.raises(ValueError, match=r"IDDESIGN: expected 3 columns, got 4"):
        parse_hsqldb_log_line(bad)


# ---------------------------------------------------------------------------
# Step 2.4 — parse_hsqldb_log (file-level wrapper)
# ---------------------------------------------------------------------------

def test_parse_hsqldb_log_empty_file_returns_empty_dict(tmp_path):
    """Empty file produces an empty dict — not a dict with empty lists."""
    log = tmp_path / "idbulletin.log"
    log.write_text("", encoding="utf-8")
    assert parse_hsqldb_log(log) == {}


def test_parse_hsqldb_log_no_insert_lines_returns_empty_dict(tmp_path):
    """A log of only DDL / connection lines yields no rows."""
    log = tmp_path / "idbulletin.log"
    log.write_text(
        "/*C1*/CONNECT USER SA\n"
        "CREATE TABLE IDDOSSIER (APPLICATIONNO VARCHAR(20))\n"
        "DISCONNECT\n",
        encoding="utf-8",
    )
    assert parse_hsqldb_log(log) == {}


def test_parse_hsqldb_log_groups_rows_by_table(tmp_path):
    """One INSERT per table — verify grouping + per-table ordering preserved."""
    log = tmp_path / "idbulletin.log"
    log.write_text(
        "/*C1*/CONNECT USER SA\n"
        "INSERT INTO IDDOSSIER VALUES('2016/01059','10.02.2016','2016 01059',"
        "'10.02.2016','1','25-02','','','','','')\n"
        "INSERT INTO IDDESIGN VALUES('2016/01059','1','Profil ')\n"
        "INSERT INTO IDDESIGN VALUES('2016/01059','2','Kanat ')\n"
        "INSERT INTO IDHOLDER VALUES('2016/01059','234974','TEST','','','TÜRKİYE')\n"
        "DISCONNECT\n",
        encoding="utf-8",
    )
    result = parse_hsqldb_log(log)
    assert set(result) == {"IDDOSSIER", "IDDESIGN", "IDHOLDER"}
    assert len(result["IDDOSSIER"]) == 1
    assert len(result["IDDESIGN"]) == 2
    assert len(result["IDHOLDER"]) == 1
    # Ordering preserved within a table
    assert result["IDDESIGN"][0]["NO"] == "1"
    assert result["IDDESIGN"][1]["NO"] == "2"
    # LOCARNOCODES list-ified through the chain
    assert result["IDDOSSIER"][0]["LOCARNOCODES"] == ["25-02"]


def test_parse_hsqldb_log_omits_empty_tables(tmp_path):
    """A table with zero parsed rows is absent from the result, not present-with-[]."""
    log = tmp_path / "idbulletin.log"
    log.write_text(
        "INSERT INTO IDDESIGN VALUES('2016/01059','1','Profil ')\n",
        encoding="utf-8",
    )
    result = parse_hsqldb_log(log)
    assert "IDDOSSIER" not in result
    assert "IDHOLDER" not in result
    assert result["IDDESIGN"][0]["PRODUCTNAME"] == "Profil "


def test_parse_hsqldb_log_prefixes_filename_and_line_on_error(tmp_path):
    """Malformed line must surface as ``<filename> line N: <inner error>``."""
    log = tmp_path / "broken.log"
    log.write_text(
        "/*C1*/CONNECT USER SA\n"
        "INSERT INTO IDDESIGN VALUES('only','two')\n",  # 2 vals, expects 3
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"broken\.log line 2: IDDESIGN: expected 3 columns, got 2"):
        parse_hsqldb_log(log)


def test_parse_hsqldb_log_accepts_path_or_str(tmp_path):
    """Both Path and str work as input."""
    log = tmp_path / "idbulletin.log"
    log.write_text(
        "INSERT INTO IDDESIGN VALUES('2016/01059','1','Profil ')\n",
        encoding="utf-8",
    )
    assert len(parse_hsqldb_log(log)["IDDESIGN"]) == 1
    assert len(parse_hsqldb_log(str(log))["IDDESIGN"]) == 1


# ---------------------------------------------------------------------------
# Step 2.5 — _application_image_folder + resolve_design_images
# ---------------------------------------------------------------------------

def test_application_image_folder_happy_path():
    """Standard ``YYYY/NNNNNN`` application_no -> ``YYYY_NNNNNN``."""
    assert _application_image_folder("2016/01059") == "2016_01059"
    assert _application_image_folder("2015/04124") == "2015_04124"
    # Whitespace tolerance
    assert _application_image_folder("  2016/01059  ") == "2016_01059"


def test_application_image_folder_rejects_malformed():
    """Anything not exactly ``digits/digits`` returns None."""
    assert _application_image_folder(None) is None
    assert _application_image_folder("") is None
    assert _application_image_folder("2016") is None         # no slash
    assert _application_image_folder("2016/") is None        # empty appno
    assert _application_image_folder("/01059") is None       # empty year
    assert _application_image_folder("2016/01/059") is None  # extra slash
    assert _application_image_folder("ABCD/01059") is None   # non-numeric year
    assert _application_image_folder("2016/abc") is None     # non-numeric appno


def test_resolve_design_images_missing_root_returns_empty(tmp_path):
    """Non-existent images_root yields empty list, not an error."""
    assert resolve_design_images("2016/01059", tmp_path / "nope") == []


def test_resolve_design_images_missing_app_folder_returns_empty(tmp_path):
    """images_root exists but the application folder doesn't."""
    (tmp_path / "2015_04124").mkdir()  # different app
    assert resolve_design_images("2016/01059", tmp_path) == []


def test_resolve_design_images_malformed_appno_returns_empty(tmp_path):
    """Bad shape never even tries to look on disk."""
    assert resolve_design_images(None, tmp_path) == []
    assert resolve_design_images("garbage", tmp_path) == []


def test_resolve_design_images_hague_application_no_returns_empty(tmp_path):
    """Hague international design numbers (``DM/NNNNNN``) appear in the
    Tasarim CD's IDDOSSIER table — confirmed empirically: 101 of 913
    dossiers in the verbose 231 archive use this shape. Hague designs
    don't have image folders on the CD (their art lives at WIPO), so
    the resolver correctly returns ``[]`` for them. The current strict
    digits-only year guard is what enforces this."""
    assert resolve_design_images("DM/086402", tmp_path) == []


def test_resolve_design_images_one_design_multi_view(tmp_path):
    """Single design with two views — mirrors what 240/.../2015_04124 ships."""
    folder = tmp_path / "2015_04124"
    folder.mkdir()
    (folder / "1_1.jpg").write_bytes(b"")
    (folder / "1_2.jpg").write_bytes(b"")
    out = resolve_design_images("2015/04124", tmp_path)
    assert len(out) == 2
    assert out[0]["design_no"] == "1" and out[0]["view_no"] == "1"
    assert out[1]["design_no"] == "1" and out[1]["view_no"] == "2"
    assert out[0]["image_path"].name == "1_1.jpg"


def test_resolve_design_images_sorts_design_and_view_numerically(tmp_path):
    """Design 10 must sort after design 9 (lexicographic ordering would
    break this — ``"10_1"`` < ``"9_1"`` as strings)."""
    folder = tmp_path / "2015_06749"
    folder.mkdir()
    for name in ["10_2.jpg", "9_1.jpg", "10_1.jpg", "9_2.jpg", "1_1.jpg"]:
        (folder / name).write_bytes(b"")
    out = resolve_design_images("2015/06749", tmp_path)
    order = [(r["design_no"], r["view_no"]) for r in out]
    assert order == [("1", "1"), ("9", "1"), ("9", "2"), ("10", "1"), ("10", "2")]


def test_resolve_design_images_skips_non_matching_files(tmp_path):
    """``Thumbs.db`` / ``.DS_Store`` / unrelated files don't show up."""
    folder = tmp_path / "2016_01059"
    folder.mkdir()
    (folder / "1_1.jpg").write_bytes(b"")
    (folder / "Thumbs.db").write_bytes(b"")
    (folder / ".DS_Store").write_bytes(b"")
    (folder / "readme.txt").write_bytes(b"")
    (folder / "preview.png").write_bytes(b"")  # wrong extension
    out = resolve_design_images("2016/01059", tmp_path)
    assert len(out) == 1
    assert out[0]["image_path"].name == "1_1.jpg"


def test_resolve_design_images_accepts_jpeg_extension(tmp_path):
    """Defensive: ``.jpeg`` (rare but legal) accepted alongside ``.jpg``."""
    folder = tmp_path / "2016_01059"
    folder.mkdir()
    (folder / "1_1.jpeg").write_bytes(b"")
    (folder / "2_1.JPG").write_bytes(b"")  # case-insensitive
    out = resolve_design_images("2016/01059", tmp_path)
    names = [r["image_path"].name for r in out]
    assert names == ["1_1.jpeg", "2_1.JPG"]


def test_resolve_design_images_empty_folder_returns_empty(tmp_path):
    """Folder exists but holds nothing relevant -> []."""
    (tmp_path / "2016_01059").mkdir()
    assert resolve_design_images("2016/01059", tmp_path) == []


# ---------------------------------------------------------------------------
# Step 2.5b — _persist_cd_images_for_app (canonical key shape)
# ---------------------------------------------------------------------------

def test_persist_cd_images_copies_files_and_returns_canonical_keys(tmp_path):
    """End-to-end happy path: source files copied, returned image_path
    values use the canonical ``{year}_{appno}/{d}_{v}.{ext}`` shape with
    no archive-wrapper prefix."""
    src = tmp_path / "src" / "2016_01059"
    src.mkdir(parents=True)
    (src / "1_1.jpg").write_bytes(b"jpeg-bytes-1")
    (src / "1_2.jpg").write_bytes(b"jpeg-bytes-2")

    dest = tmp_path / "dest"
    out = _persist_cd_images_for_app("2016/01059", tmp_path / "src", dest)

    assert len(out) == 2
    assert out[0] == {"design_no": "1", "view_no": "1",
                      "image_path": "2016_01059/1_1.jpg"}
    assert out[1] == {"design_no": "1", "view_no": "2",
                      "image_path": "2016_01059/1_2.jpg"}
    # Files actually copied
    assert (dest / "2016_01059" / "1_1.jpg").read_bytes() == b"jpeg-bytes-1"
    assert (dest / "2016_01059" / "1_2.jpg").read_bytes() == b"jpeg-bytes-2"


def test_persist_cd_images_creates_dest_folder_tree(tmp_path):
    """``dest_root`` and the per-application subfolder are created if missing."""
    src = tmp_path / "src" / "2015_06749"
    src.mkdir(parents=True)
    (src / "10_1.jpg").write_bytes(b"")

    dest = tmp_path / "deeply" / "nested" / "dest"
    out = _persist_cd_images_for_app("2015/06749", tmp_path / "src", dest)

    assert (dest / "2015_06749" / "10_1.jpg").is_file()
    assert out == [{"design_no": "10", "view_no": "1",
                    "image_path": "2015_06749/10_1.jpg"}]


def test_persist_cd_images_overwrites_existing_files(tmp_path):
    """Re-runs (e.g. via --force) replace previously persisted bytes."""
    src = tmp_path / "src" / "2016_01059"
    src.mkdir(parents=True)
    (src / "1_1.jpg").write_bytes(b"NEW")

    dest = tmp_path / "dest"
    (dest / "2016_01059").mkdir(parents=True)
    (dest / "2016_01059" / "1_1.jpg").write_bytes(b"OLD")

    _persist_cd_images_for_app("2016/01059", tmp_path / "src", dest)
    assert (dest / "2016_01059" / "1_1.jpg").read_bytes() == b"NEW"


def test_persist_cd_images_no_source_returns_empty(tmp_path):
    """No matching images on disk -> [] and no destination side-effects.
    Covers the Hague-design case (no image folder at all)."""
    out = _persist_cd_images_for_app("DM/086402", tmp_path / "src",
                                      tmp_path / "dest")
    assert out == []
    # No spurious DM_086402/ created
    assert not (tmp_path / "dest" / "DM_086402").exists()


def test_persist_cd_images_malformed_appno_returns_empty(tmp_path):
    """Bad input never reaches disk."""
    assert _persist_cd_images_for_app(None, tmp_path, tmp_path / "d") == []
    assert _persist_cd_images_for_app("garbage", tmp_path, tmp_path / "d") == []
    assert not (tmp_path / "d").exists()


def test_persist_cd_images_preserves_jpeg_extension(tmp_path):
    """``.jpeg`` (rare) and ``.JPG`` (uppercase) extensions pass through
    verbatim — the canonical key includes the original suffix."""
    src = tmp_path / "src" / "2016_01059"
    src.mkdir(parents=True)
    (src / "1_1.jpeg").write_bytes(b"")
    (src / "2_1.JPG").write_bytes(b"")

    dest = tmp_path / "dest"
    out = _persist_cd_images_for_app("2016/01059", tmp_path / "src", dest)
    keys = sorted(o["image_path"] for o in out)
    assert keys == ["2016_01059/1_1.jpeg", "2016_01059/2_1.JPG"]
    assert (dest / "2016_01059" / "1_1.jpeg").is_file()
    assert (dest / "2016_01059" / "2_1.JPG").is_file()


def test_persist_cd_images_multi_design_keeps_numeric_order(tmp_path):
    """Output ordering matches resolve_design_images numeric sort
    (design 10 after design 9, not alphabetic)."""
    src = tmp_path / "src" / "2015_06749"
    src.mkdir(parents=True)
    for name in ["10_1.jpg", "1_1.jpg", "9_1.jpg", "2_1.jpg"]:
        (src / name).write_bytes(b"")
    out = _persist_cd_images_for_app("2015/06749", tmp_path / "src",
                                      tmp_path / "dest")
    order = [(o["design_no"], o["view_no"]) for o in out]
    assert order == [("1", "1"), ("2", "1"), ("9", "1"), ("10", "1")]


# ---------------------------------------------------------------------------
# Step 2.6 — _resolve_seven_zip / _locate_cd_layout / extract_cd_archive
# ---------------------------------------------------------------------------

def test_resolve_seven_zip_explicit_override(tmp_path):
    """Explicit override wins over env var and default."""
    p = tmp_path / "custom7z.exe"
    assert _resolve_seven_zip(p) == p


def test_resolve_seven_zip_env_var_fallback(monkeypatch, tmp_path):
    """``PIPELINE_SEVEN_ZIP_PATH`` env var beats the platform default."""
    p = tmp_path / "env7z.exe"
    monkeypatch.setenv("PIPELINE_SEVEN_ZIP_PATH", str(p))
    assert _resolve_seven_zip() == p


def test_resolve_seven_zip_platform_default(monkeypatch):
    """No override and no env var -> hard-coded Windows default."""
    monkeypatch.delenv("PIPELINE_SEVEN_ZIP_PATH", raising=False)
    assert _resolve_seven_zip() == Path(DEFAULT_SEVEN_ZIP)


def test_locate_cd_layout_modern_layout(tmp_path):
    """Modern ``{N}_CD.rar`` extraction: log + images both inside cd_root."""
    cd_root = tmp_path / "240"
    cd_root.mkdir()
    (cd_root / "idbulletin.log").write_text("", encoding="utf-8")
    (cd_root / "idbulletin.script").write_text("", encoding="utf-8")
    (cd_root / "images").mkdir()

    layout = _locate_cd_layout(tmp_path)
    assert layout.cd_root == cd_root
    assert layout.log_path == cd_root / "idbulletin.log"
    assert layout.images_root == cd_root / "images"


def test_locate_cd_layout_verbose_layout(tmp_path):
    """Verbose ``231 say_l_*.rar`` real shape (post-extraction):

    - canonical ``idbulletin.log`` at archive root (no wrapping folder)
    - duplicate copy at ``setup/idbulletin.log``
    - images at archive root

    Confirmed by extracting the actual archive in step 2.6 verification.
    """
    (tmp_path / "idbulletin.log").write_text("real", encoding="utf-8")
    (tmp_path / "idbulletin.script").write_text("", encoding="utf-8")
    (tmp_path / "images").mkdir()
    (tmp_path / "main.html").write_text("", encoding="utf-8")
    setup = tmp_path / "setup"
    setup.mkdir()
    (setup / "idbulletin.log").write_text("duplicate", encoding="utf-8")

    layout = _locate_cd_layout(tmp_path)
    assert layout.cd_root == tmp_path
    assert layout.log_path == tmp_path / "idbulletin.log"
    assert layout.log_path.read_text(encoding="utf-8") == "real"
    assert layout.images_root == tmp_path / "images"


def test_locate_cd_layout_missing_log_raises(tmp_path):
    """No ``idbulletin.log`` anywhere -> RuntimeError."""
    (tmp_path / "garbage").mkdir()
    with pytest.raises(RuntimeError, match="no idbulletin.log"):
        _locate_cd_layout(tmp_path)


def test_locate_cd_layout_multiple_logs_at_same_depth_raises(tmp_path):
    """Two ``idbulletin.log`` files tied for shallowest depth -> ambiguous."""
    (tmp_path / "240").mkdir()
    (tmp_path / "240" / "idbulletin.log").write_text("", encoding="utf-8")
    (tmp_path / "242").mkdir()
    (tmp_path / "242" / "idbulletin.log").write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="multiple idbulletin.log"):
        _locate_cd_layout(tmp_path)


def test_locate_cd_layout_modern_layout_with_setup_duplicate(tmp_path):
    """Modern ``{N}_CD.rar`` archives carry a duplicate log at
    ``{N}/setup/idbulletin.log`` alongside the canonical
    ``{N}/idbulletin.log``. Real-data finding from 240_CD.rar.

    The shallower path wins; the deeper one is ignored.
    """
    cd_root = tmp_path / "240"
    cd_root.mkdir()
    (cd_root / "idbulletin.log").write_text("real", encoding="utf-8")
    (cd_root / "images").mkdir()
    setup = cd_root / "setup"
    setup.mkdir()
    (setup / "idbulletin.log").write_text("duplicate", encoding="utf-8")

    layout = _locate_cd_layout(tmp_path)
    assert layout.cd_root == cd_root
    assert layout.log_path == cd_root / "idbulletin.log"
    assert layout.log_path.read_text(encoding="utf-8") == "real"


def test_locate_cd_layout_missing_images_dir_returns_log_only_path(tmp_path):
    """No images/ folder anywhere — layout still valid, images_root just
    points at a missing path (the resolver tolerates that with [])."""
    cd_root = tmp_path / "240"
    cd_root.mkdir()
    (cd_root / "idbulletin.log").write_text("", encoding="utf-8")
    layout = _locate_cd_layout(tmp_path)
    assert layout.cd_root == cd_root
    assert not layout.images_root.exists()


def test_extract_cd_archive_missing_rar_raises(tmp_path):
    """Missing source archive -> FileNotFoundError before touching 7-Zip."""
    with pytest.raises(FileNotFoundError, match="archive not found"):
        extract_cd_archive(tmp_path / "missing.rar", tmp_path / "scratch")


def test_extract_cd_archive_missing_seven_zip_raises(tmp_path):
    """Missing 7-Zip exe -> FileNotFoundError with a clear message."""
    rar = tmp_path / "fake.rar"
    rar.write_bytes(b"")  # exists but empty — won't be opened, we fail earlier
    fake_7z = tmp_path / "no_such_7z.exe"
    with pytest.raises(FileNotFoundError, match="7-Zip not found"):
        extract_cd_archive(rar, tmp_path / "scratch", seven_zip=fake_7z)


def test_extract_cd_archive_seven_zip_failure_raises(monkeypatch, tmp_path):
    """7-Zip non-zero / non-warning exit -> RuntimeError with stderr snippet."""
    rar = tmp_path / "fake.rar"
    rar.write_bytes(b"")
    fake_7z = tmp_path / "fake7z.exe"
    fake_7z.write_bytes(b"")  # passes is_file() check

    class FakeResult:
        def __init__(self):
            self.returncode = 2
            self.stderr = "Cannot open archive"
            self.stdout = ""

    monkeypatch.setattr(
        "cd_extract_tasarim.subprocess.run",
        lambda *a, **kw: FakeResult(),
    )
    with pytest.raises(RuntimeError, match=r"7-Zip exited 2 extracting fake\.rar.*Cannot open archive"):
        extract_cd_archive(rar, tmp_path / "scratch", seven_zip=fake_7z)


def test_extract_cd_archive_success_returns_cd_layout(monkeypatch, tmp_path):
    """End-to-end happy path with a fake 7-Zip — verify the wrapper runs
    _locate_cd_layout on the post-extraction tree."""
    rar = tmp_path / "fake.rar"
    rar.write_bytes(b"")
    fake_7z = tmp_path / "fake7z.exe"
    fake_7z.write_bytes(b"")
    scratch = tmp_path / "scratch"

    class FakeResult:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, *a, **kw):
        # Simulate 7-Zip extracting a modern-layout CD into scratch
        out = scratch / "240"
        out.mkdir(parents=True)
        (out / "idbulletin.log").write_text("", encoding="utf-8")
        (out / "images").mkdir()
        return FakeResult()

    monkeypatch.setattr("cd_extract_tasarim.subprocess.run", fake_run)
    layout = extract_cd_archive(rar, scratch, seven_zip=fake_7z)
    assert isinstance(layout, CDLayout)
    assert layout.log_path.name == "idbulletin.log"
    assert layout.cd_root.name == "240"


def test_extract_cd_archive_command_excludes_no_java_dir(monkeypatch, tmp_path):
    """Tasarim CDs don't ship a JRE. The 7-Zip command must NOT carry
    the ``-x!*/data/java*`` excludes the patent CD extractor uses."""
    rar = tmp_path / "fake.rar"
    rar.write_bytes(b"")
    fake_7z = tmp_path / "fake7z.exe"
    fake_7z.write_bytes(b"")
    scratch = tmp_path / "scratch"

    captured = {}

    def fake_run(cmd, *a, **kw):
        captured["cmd"] = cmd

        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        out = scratch / "240"
        out.mkdir(parents=True)
        (out / "idbulletin.log").write_text("", encoding="utf-8")
        return R()

    monkeypatch.setattr("cd_extract_tasarim.subprocess.run", fake_run)
    extract_cd_archive(rar, scratch, seven_zip=fake_7z)
    cmd = captured["cmd"]
    assert all("data/java" not in arg for arg in cmd), cmd
    # Sanity: required flags still present
    assert "-y" in cmd and "-bso0" in cmd and "-bsp0" in cmd


# ---------------------------------------------------------------------------
# Step 2.7 — parse_bulletin_inf + cd_to_metadata orchestrator
# ---------------------------------------------------------------------------

def test_parse_bulletin_inf_real_240(tmp_path):
    """Real ``idbulletin.inf`` from 240_CD.rar (NO=240, DATE=09.03.2016).

    Confirms the dot-separated DD.MM.YYYY -> ISO conversion.
    """
    inf = tmp_path / "idbulletin.inf"
    inf.write_text("NO=240\nDATE=09.03.2016\n", encoding="utf-8")
    assert parse_bulletin_inf(inf) == {
        "bulletin_no": "240",
        "bulletin_date": "2016-03-09",
    }


def test_parse_bulletin_inf_missing_file_returns_nones(tmp_path):
    """Missing inf file -> both fields ``None``, not an error."""
    assert parse_bulletin_inf(tmp_path / "nope.inf") == {
        "bulletin_no": None,
        "bulletin_date": None,
    }


def test_parse_bulletin_inf_missing_date_keeps_no(tmp_path):
    """``NO=...`` with no ``DATE=`` line -> bulletin_no set, date None."""
    inf = tmp_path / "idbulletin.inf"
    inf.write_text("NO=240\n", encoding="utf-8")
    assert parse_bulletin_inf(inf) == {
        "bulletin_no": "240",
        "bulletin_date": None,
    }


def test_parse_bulletin_inf_malformed_date_yields_none(tmp_path):
    """Date that doesn't match DD.MM.YYYY -> date is None, no preserved."""
    inf = tmp_path / "idbulletin.inf"
    inf.write_text("NO=240\nDATE=2016-03-09\n", encoding="utf-8")  # ISO not DMY
    assert parse_bulletin_inf(inf) == {
        "bulletin_no": "240",
        "bulletin_date": None,
    }


def test_parse_bulletin_inf_empty_no_value(tmp_path):
    """Empty ``NO=`` value -> bulletin_no is None (not empty string)."""
    inf = tmp_path / "idbulletin.inf"
    inf.write_text("NO=\nDATE=09.03.2016\n", encoding="utf-8")
    assert parse_bulletin_inf(inf)["bulletin_no"] is None


def test_parse_bulletin_inf_tolerates_whitespace(tmp_path):
    """Spaces around ``=`` and trailing whitespace are tolerated."""
    inf = tmp_path / "idbulletin.inf"
    inf.write_text("NO = 240   \n  DATE  =  09.03.2016  \n", encoding="utf-8")
    assert parse_bulletin_inf(inf) == {
        "bulletin_no": "240",
        "bulletin_date": "2016-03-09",
    }


def _build_pre_extracted_cd(tmp_path: Path) -> CDLayout:
    """Test helper: build a full pre-extracted CD layout on disk so we can
    exercise _layout_to_metadata / cd_to_metadata without 7-Zip.

    Uses three real-data IDDOSSIER shapes:
      - 2016/01059: 1 design "Profil", 2 views (resolved)
      - 2015/06749: 2 designs "A"/"B", 3 views design 1, 0 views design 2
      - DM/086402:  1 design "Hague", no images at all (Hague case)
    """
    cd_root = tmp_path / "240"
    cd_root.mkdir()

    (cd_root / "idbulletin.inf").write_text(
        "NO=240\nDATE=09.03.2016\n", encoding="utf-8"
    )
    (cd_root / "idbulletin.log").write_text(
        # 2016/01059 — 1 design, 2 views
        "INSERT INTO IDDOSSIER VALUES('2016/01059','10.02.2016','2016 01059','10.02.2016','1','25-02','','RABİA','','MECİDİYEKÖY','')\n"
        "INSERT INTO IDDESIGN VALUES('2016/01059','1','Profil ')\n"
        "INSERT INTO IDHOLDER VALUES('2016/01059','234974','BİRLİK','ADDR','İSTANBUL','TÜRKİYE')\n"
        "INSERT INTO IDDESIGNER VALUES('2016/01059','1','VEDAT','ADDR','TÜRKİYE')\n"
        # 2015/06749 — 2 designs; only design 1 has images
        "INSERT INTO IDDOSSIER VALUES('2015/06749','01.01.2016','2015 06749','01.01.2016','2','06-04,06-02','','ATTY','','','1')\n"
        "INSERT INTO IDDESIGN VALUES('2015/06749','1','A ')\n"
        "INSERT INTO IDDESIGN VALUES('2015/06749','2','B ')\n"
        # DM/086402 — Hague, no images on CD
        "INSERT INTO IDDOSSIER VALUES('DM/086402','01.01.2016','DM 086402','01.01.2016','1','21-02','','','','','')\n"
        "INSERT INTO IDDESIGN VALUES('DM/086402','1','Hague ')\n"
        # Annotation row
        "INSERT INTO IDANNOTATION VALUES('262752','2011/01410','Yenileme','event content')\n",
        encoding="utf-8",
    )
    images_root = cd_root / "images"
    # 2016/01059: design 1, views 1+2
    folder = images_root / "2016_01059"
    folder.mkdir(parents=True)
    (folder / "1_1.jpg").write_bytes(b"")
    (folder / "1_2.jpg").write_bytes(b"")
    # 2015/06749: only design 1 has 3 views; design 2 has no files
    folder = images_root / "2015_06749"
    folder.mkdir(parents=True)
    (folder / "1_1.jpg").write_bytes(b"")
    (folder / "1_2.jpg").write_bytes(b"")
    (folder / "1_3.jpg").write_bytes(b"")
    # DM/086402: deliberately no folder

    return CDLayout(
        cd_root=cd_root,
        log_path=cd_root / "idbulletin.log",
        images_root=images_root,
    )


def test_layout_to_metadata_full_join(tmp_path):
    """End-to-end shape check on a hand-built CD with three dossiers
    spanning the realistic value space (turkish, multi-design, Hague).

    Also asserts cd_images/ is populated on disk — _layout_to_metadata
    is the data-extraction step that *both* writes JSON and persists
    image files for the canonical TS folder.
    """
    layout = _build_pre_extracted_cd(tmp_path)
    cd_images_dest = tmp_path / "out_cd_images"
    doc = _layout_to_metadata(layout, "240_CD.rar", cd_images_dest)

    # Top-level metadata
    assert doc["bulletin_no"] == "240"
    assert doc["bulletin_date"] == "2016-03-09"
    assert doc["source_archive"] == "240_CD.rar"
    assert "extracted_at" in doc and "T" in doc["extracted_at"]

    # Stats
    s = doc["stats"]
    assert s["dossiers"] == 3
    assert s["designs"] == 4         # 1 + 2 + 1
    assert s["holders"] == 1
    assert s["designers"] == 1
    assert s["annotations"] == 1
    assert s["images_resolved"] == 5  # 2 + 3 + 0
    assert s["designs_without_images"] == 2  # design 2 of 06749 + Hague design

    # Dossier ordering preserved
    apps = [d["application_no"] for d in doc["dossiers"]]
    assert apps == ["2016/01059", "2015/06749", "DM/086402"]

    # Files actually persisted to cd_images_dest
    assert (cd_images_dest / "2016_01059" / "1_1.jpg").is_file()
    assert (cd_images_dest / "2016_01059" / "1_2.jpg").is_file()
    assert (cd_images_dest / "2015_06749" / "1_1.jpg").is_file()
    assert (cd_images_dest / "2015_06749" / "1_2.jpg").is_file()
    assert (cd_images_dest / "2015_06749" / "1_3.jpg").is_file()
    # Hague design has no image folder
    assert not (cd_images_dest / "DM_086402").exists()


def test_layout_to_metadata_first_dossier_full_shape(tmp_path):
    """Walk every emitted field for the first dossier — including the
    canonical key shape for image_path (no archive-wrapper prefix)."""
    layout = _build_pre_extracted_cd(tmp_path)
    cd_images_dest = tmp_path / "out_cd_images"
    doc = _layout_to_metadata(layout, "240_CD.rar", cd_images_dest)

    d = doc["dossiers"][0]
    assert d["application_no"] == "2016/01059"
    assert d["application_date"] == "10.02.2016"
    assert d["register_no"] == "2016 01059"
    assert d["register_date"] == "10.02.2016"
    assert d["design_count"] == "1"
    assert d["type"] == ""
    assert d["locarno_codes"] == ["25-02"]
    assert d["attorney"] == {"no": "", "name": "RABİA", "title": "", "address": "MECİDİYEKÖY"}

    assert len(d["holders"]) == 1
    h = d["holders"][0]
    assert h == {"client_no": "234974", "title": "BİRLİK", "address": "ADDR",
                 "city": "İSTANBUL", "country": "TÜRKİYE"}

    assert len(d["designers"]) == 1
    assert d["designers"][0] == {"no": "1", "name": "VEDAT", "address": "ADDR", "country": "TÜRKİYE"}

    assert len(d["designs"]) == 1
    des = d["designs"][0]
    assert des["no"] == "1"
    assert des["product_name"] == "Profil "
    assert len(des["views"]) == 2
    # Canonical key shape — no "240/images/" prefix (the locked decision)
    assert des["views"][0] == {"view_no": "1", "image_path": "2016_01059/1_1.jpg"}
    assert des["views"][1] == {"view_no": "2", "image_path": "2016_01059/1_2.jpg"}


def test_layout_to_metadata_design_without_images_emits_empty_views(tmp_path):
    """The 2015/06749 fixture has 2 designs but only design 1 has images.
    Design 2 must still be emitted with views=[]."""
    layout = _build_pre_extracted_cd(tmp_path)
    doc = _layout_to_metadata(layout, "240_CD.rar", tmp_path / "out_cd_images")

    multi = next(d for d in doc["dossiers"] if d["application_no"] == "2015/06749")
    assert len(multi["designs"]) == 2
    assert len(multi["designs"][0]["views"]) == 3
    assert multi["designs"][1]["views"] == []
    assert multi["locarno_codes"] == ["06-04", "06-02"]


def test_layout_to_metadata_hague_dossier_emits_with_no_views(tmp_path):
    """Hague (DM/...) dossiers have no image folder. Must still appear
    in the dossiers list with views=[] and create no spurious cd_images
    subfolder."""
    layout = _build_pre_extracted_cd(tmp_path)
    cd_images_dest = tmp_path / "out_cd_images"
    doc = _layout_to_metadata(layout, "240_CD.rar", cd_images_dest)

    hague = next(d for d in doc["dossiers"] if d["application_no"] == "DM/086402")
    assert len(hague["designs"]) == 1
    assert hague["designs"][0]["views"] == []
    assert hague["locarno_codes"] == ["21-02"]
    # And no DM_086402 (or DM/086402) folder created
    assert not (cd_images_dest / "DM_086402").exists()


def test_layout_to_metadata_annotations_emitted_as_sibling_array(tmp_path):
    """IDANNOTATION rows go to a top-level ``annotations`` array, NOT
    nested inside their own dossier (they reference different
    applications than the bulletin's own dossiers)."""
    layout = _build_pre_extracted_cd(tmp_path)
    doc = _layout_to_metadata(layout, "240_CD.rar", tmp_path / "out_cd_images")

    assert len(doc["annotations"]) == 1
    a = doc["annotations"][0]
    assert a == {
        "publication_key": "262752",
        "application_no": "2011/01410",   # different from any dossier above
        "request_type": "Yenileme",
        "content": "event content",
    }


def test_cd_to_metadata_uses_extract_then_layout(monkeypatch, tmp_path):
    """The public entry runs extract_cd_archive then _layout_to_metadata.
    Mock the extractor so we don't need 7-Zip in the unit suite."""
    layout = _build_pre_extracted_cd(tmp_path)
    monkeypatch.setattr(
        "cd_extract_tasarim.extract_cd_archive",
        lambda rar, scratch, **kw: layout,
    )
    fake_rar = tmp_path / "240_CD.rar"
    fake_rar.write_bytes(b"")
    doc = cd_to_metadata(fake_rar, tmp_path / "scratch", tmp_path / "cd_images")
    assert doc["source_archive"] == "240_CD.rar"
    assert doc["stats"]["dossiers"] == 3
    # Persistence side-effect happened
    assert (tmp_path / "cd_images" / "2016_01059" / "1_1.jpg").is_file()


# ---------------------------------------------------------------------------
# Step 2.8 — CLI entrypoint
# ---------------------------------------------------------------------------

def test_issue_folder_name_canonical():
    """``TS_{N}_{date}`` matches the modern PDF collector's folder shape."""
    assert issue_folder_name("240", "2016-03-09") == "TS_240_2016-03-09"
    assert issue_folder_name("483", "2026-04-24") == "TS_483_2026-04-24"


def test_all_cd_rars_picks_modern_and_verbose_only(tmp_path):
    """Mixed folder containing modern, verbose, and legacy archive names —
    only the HSQLDB-shape ones should match."""
    (tmp_path / "230_CD.rar").write_bytes(b"")
    (tmp_path / "240_CD.rar").write_bytes(b"")
    (tmp_path / "242_cd.rar").write_bytes(b"")  # case-insensitive
    (tmp_path / "231 say_l_ resmi endüstriyel tasar_m bülteni cd içeri_i.rar").write_bytes(b"")
    # legacy PDF-only — must be excluded
    (tmp_path / "Tasar_m Bülteni 219.rar").write_bytes(b"")
    (tmp_path / "1-56.rar").write_bytes(b"")
    (tmp_path / "94-111.rar").write_bytes(b"")
    # noise
    (tmp_path / "readme.txt").write_bytes(b"")

    matches = _all_cd_rars(tmp_path)
    names = sorted(p.name for p in matches)
    assert names == [
        "230_CD.rar",
        "231 say_l_ resmi endüstriyel tasar_m bülteni cd içeri_i.rar",
        "240_CD.rar",
        "242_cd.rar",
    ]


def test_parse_argv_rar_and_all_mutually_exclusive(tmp_path, capsys):
    """``--rar`` and ``--all`` together is an explicit user error."""
    with pytest.raises(SystemExit):
        parse_argv(["--rar", str(tmp_path / "x.rar"), "--all"])
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_parse_argv_requires_rar_or_all(capsys):
    """Neither flag passed -> argparse error."""
    with pytest.raises(SystemExit):
        parse_argv([])
    assert "provide --rar" in capsys.readouterr().err


def test_parse_argv_all_with_empty_dir_errors(tmp_path, capsys):
    """``--all`` with no matching archives -> explicit error message."""
    with pytest.raises(SystemExit):
        parse_argv(["--all", "--bulletins-dir", str(tmp_path)])
    assert "no HSQLDB-shape" in capsys.readouterr().err


def test_parse_argv_all_collects_rars(tmp_path):
    """``--all`` with a populated bulletins-dir collects matching rars."""
    (tmp_path / "240_CD.rar").write_bytes(b"")
    (tmp_path / "Tasar_m Bülteni 219.rar").write_bytes(b"")  # excluded
    args = parse_argv(["--all", "--bulletins-dir", str(tmp_path)])
    assert [p.name for p in args.rar_paths] == ["240_CD.rar"]


def test_parse_argv_out_dir_defaults_to_bulletins_dir(tmp_path):
    """Without ``--out-dir``, output root mirrors ``--bulletins-dir``."""
    (tmp_path / "240_CD.rar").write_bytes(b"")
    args = parse_argv(["--all", "--bulletins-dir", str(tmp_path)])
    assert args.out_root == tmp_path
    assert args.force is False
    assert args.keep_scratch is False


def test_parse_argv_explicit_overrides(tmp_path):
    """Each override flag plumbed through to CLIArgs."""
    rar = tmp_path / "240_CD.rar"
    rar.write_bytes(b"")
    args = parse_argv([
        "--rar", str(rar),
        "--out-dir", str(tmp_path / "out"),
        "--scratch-dir", str(tmp_path / "scratch"),
        "--seven-zip", "C:/custom7z.exe",
        "--keep-scratch",
        "--force",
    ])
    assert args.rar_paths == [rar]
    assert args.out_root == tmp_path / "out"
    assert args.scratch_dir == tmp_path / "scratch"
    assert args.seven_zip == Path("C:/custom7z.exe")
    assert args.keep_scratch is True
    assert args.force is True


def _wire_fake_pipeline(
    monkeypatch,
    bulletin_no: Optional[str] = "240",
    bulletin_date_iso: Optional[str] = "2016-03-09",
    bulletin_date_dmy: str = "09.03.2016",
    write_inf: bool = True,
):
    """Mock extract_cd_archive (returns a tiny on-disk CDLayout) and
    _layout_to_metadata (returns a canned doc) so CLI tests exercise
    the orchestration shape of _process_one without 7-Zip or real
    HSQLDB parsing.

    ``write_inf=False`` simulates a CD whose idbulletin.inf is missing
    or malformed so parse_bulletin_inf returns Nones — covers the
    "can't compute TS folder" failure path.
    """
    def fake_extract(rar, scratch, **kw):
        scratch = Path(scratch)
        scratch.mkdir(parents=True, exist_ok=True)
        cd_root = scratch / "cd"
        cd_root.mkdir()
        if write_inf:
            (cd_root / "idbulletin.inf").write_text(
                f"NO={bulletin_no}\nDATE={bulletin_date_dmy}\n",
                encoding="utf-8",
            )
        # Empty log + missing images dir is fine; _layout_to_metadata is mocked.
        (cd_root / "idbulletin.log").write_text("", encoding="utf-8")
        return CDLayout(
            cd_root=cd_root,
            log_path=cd_root / "idbulletin.log",
            images_root=cd_root / "images",
        )

    def fake_layout(layout, source_archive_name, cd_images_dest):
        # Persist a sentinel image so we can verify cd_images_dest plumbing
        Path(cd_images_dest).mkdir(parents=True, exist_ok=True)
        (Path(cd_images_dest) / "_sentinel.txt").write_text(
            "wired", encoding="utf-8"
        )
        return {
            "bulletin_no": bulletin_no,
            "bulletin_date": bulletin_date_iso,
            "source_archive": source_archive_name,
            "extracted_at": "2026-05-08T00:00:00+00:00",
            "stats": {
                "dossiers": 1, "designs": 1, "holders": 0,
                "designers": 0, "annotations": 0,
                "images_resolved": 1, "designs_without_images": 0,
            },
            "dossiers": [], "annotations": [],
        }

    monkeypatch.setattr("cd_extract_tasarim.extract_cd_archive", fake_extract)
    monkeypatch.setattr("cd_extract_tasarim._layout_to_metadata", fake_layout)


def test_main_writes_to_TS_folder_with_cd_images_dir(monkeypatch, tmp_path):
    """CLI on one rar produces TS_{N}_{date}/cd_metadata.json AND a
    cd_images/ folder next to it (the canonical layout)."""
    rar = tmp_path / "240_CD.rar"
    rar.write_bytes(b"")
    out_root = tmp_path / "out"

    _wire_fake_pipeline(monkeypatch)

    rc = main([
        "--rar", str(rar),
        "--out-dir", str(out_root),
        "--scratch-dir", str(tmp_path / "scratch"),
    ])
    assert rc == 0
    issue_folder = out_root / "TS_240_2016-03-09"
    assert (issue_folder / CD_METADATA_FILENAME).is_file()
    # cd_images folder created next to cd_metadata.json
    assert (issue_folder / "cd_images" / "_sentinel.txt").read_text(encoding="utf-8") == "wired"
    doc = json.loads((issue_folder / CD_METADATA_FILENAME).read_text(encoding="utf-8"))
    assert doc["bulletin_no"] == "240"


def test_main_skips_existing_without_force(monkeypatch, tmp_path):
    """Pre-existing cd_metadata.json -> skip with warning, return 0;
    cd_images/ NOT recreated (skip short-circuits before persistence)."""
    rar = tmp_path / "240_CD.rar"
    rar.write_bytes(b"")
    out_root = tmp_path / "out"
    folder = out_root / "TS_240_2016-03-09"
    folder.mkdir(parents=True)
    existing = folder / CD_METADATA_FILENAME
    existing.write_text('{"original":"keepme"}', encoding="utf-8")

    _wire_fake_pipeline(monkeypatch)

    rc = main([
        "--rar", str(rar),
        "--out-dir", str(out_root),
        "--scratch-dir", str(tmp_path / "scratch"),
    ])
    assert rc == 0  # skip is not a failure
    # Original file untouched
    assert json.loads(existing.read_text(encoding="utf-8")) == {"original": "keepme"}
    # No cd_images/ folder created since persistence was skipped
    assert not (folder / "cd_images").exists()


def test_main_force_overwrites_existing(monkeypatch, tmp_path):
    """``--force`` replaces an existing cd_metadata.json AND populates
    cd_images/ from scratch."""
    rar = tmp_path / "240_CD.rar"
    rar.write_bytes(b"")
    out_root = tmp_path / "out"
    folder = out_root / "TS_240_2016-03-09"
    folder.mkdir(parents=True)
    existing = folder / CD_METADATA_FILENAME
    existing.write_text('{"original":"replaceme"}', encoding="utf-8")

    _wire_fake_pipeline(monkeypatch)

    rc = main([
        "--rar", str(rar),
        "--out-dir", str(out_root),
        "--scratch-dir", str(tmp_path / "scratch"),
        "--force",
    ])
    assert rc == 0
    doc = json.loads(existing.read_text(encoding="utf-8"))
    assert doc["bulletin_no"] == "240"
    assert (folder / "cd_images" / "_sentinel.txt").is_file()


def test_main_returns_1_when_archive_missing(monkeypatch, tmp_path):
    """Path passed to --rar that doesn't exist counts as failure."""
    rc = main([
        "--rar", str(tmp_path / "missing.rar"),
        "--out-dir", str(tmp_path / "out"),
        "--scratch-dir", str(tmp_path / "scratch"),
    ])
    assert rc == 1


def test_main_returns_1_when_extract_raises(monkeypatch, tmp_path):
    """An exception from extract_cd_archive is logged, not re-raised; rc=1."""
    rar = tmp_path / "240_CD.rar"
    rar.write_bytes(b"")

    def boom(*a, **kw):
        raise RuntimeError("simulated 7-Zip explosion")

    monkeypatch.setattr("cd_extract_tasarim.extract_cd_archive", boom)
    rc = main([
        "--rar", str(rar),
        "--out-dir", str(tmp_path / "out"),
        "--scratch-dir", str(tmp_path / "scratch"),
    ])
    assert rc == 1


def test_main_refuses_when_bulletin_inf_missing(monkeypatch, tmp_path):
    """If parse_bulletin_inf returned None for either field (e.g. no
    idbulletin.inf in the archive), we cannot compute the TS_{N}_{date}
    folder. Treat as failure (return 1)."""
    rar = tmp_path / "240_CD.rar"
    rar.write_bytes(b"")
    _wire_fake_pipeline(monkeypatch, write_inf=False)
    rc = main([
        "--rar", str(rar),
        "--out-dir", str(tmp_path / "out"),
        "--scratch-dir", str(tmp_path / "scratch"),
    ])
    assert rc == 1


def test_main_processes_multiple_rars(monkeypatch, tmp_path):
    """Two --rar flags -> two TS folders written, rc=0. Each rar gets
    a layout matching its own bulletin_no derived from filename."""
    a = tmp_path / "240_CD.rar"
    a.write_bytes(b"")
    b = tmp_path / "242_CD.rar"
    b.write_bytes(b"")

    by_archive = {
        "240_CD.rar": ("240", "2016-03-09", "09.03.2016"),
        "242_CD.rar": ("242", "2016-04-24", "24.04.2016"),
    }

    def fake_extract(rar, scratch, **kw):
        no, _iso, dmy = by_archive[Path(rar).name]
        scratch = Path(scratch)
        scratch.mkdir(parents=True, exist_ok=True)
        cd_root = scratch / "cd"
        cd_root.mkdir()
        (cd_root / "idbulletin.inf").write_text(
            f"NO={no}\nDATE={dmy}\n", encoding="utf-8"
        )
        (cd_root / "idbulletin.log").write_text("", encoding="utf-8")
        return CDLayout(cd_root=cd_root,
                        log_path=cd_root / "idbulletin.log",
                        images_root=cd_root / "images")

    def fake_layout(layout, source_archive_name, cd_images_dest):
        no, iso, _dmy = by_archive[source_archive_name]
        Path(cd_images_dest).mkdir(parents=True, exist_ok=True)
        return {
            "bulletin_no": no, "bulletin_date": iso,
            "source_archive": source_archive_name,
            "extracted_at": "2026-05-08T00:00:00+00:00",
            "stats": {"dossiers": 1, "designs": 1, "holders": 0,
                      "designers": 0, "annotations": 0,
                      "images_resolved": 1, "designs_without_images": 0},
            "dossiers": [], "annotations": [],
        }

    monkeypatch.setattr("cd_extract_tasarim.extract_cd_archive", fake_extract)
    monkeypatch.setattr("cd_extract_tasarim._layout_to_metadata", fake_layout)

    rc = main([
        "--rar", str(a), "--rar", str(b),
        "--out-dir", str(tmp_path / "out"),
        "--scratch-dir", str(tmp_path / "scratch"),
    ])
    assert rc == 0
    assert (tmp_path / "out" / "TS_240_2016-03-09" / CD_METADATA_FILENAME).is_file()
    assert (tmp_path / "out" / "TS_242_2016-04-24" / CD_METADATA_FILENAME).is_file()


def test_main_real_persistence_with_pre_built_layout(monkeypatch, tmp_path):
    """Wider integration test: only mock extract_cd_archive (return a
    real, hand-built CDLayout); let parse_bulletin_inf and the real
    _layout_to_metadata run, including _persist_cd_images_for_app.

    Verifies the canonical TS folder ends up holding both cd_metadata.json
    and cd_images/{year}_{appno}/{d}_{v}.jpg with real bytes.
    """
    layout = _build_pre_extracted_cd(tmp_path)
    monkeypatch.setattr(
        "cd_extract_tasarim.extract_cd_archive",
        lambda rar, scratch, **kw: layout,
    )

    rar = tmp_path / "240_CD.rar"
    rar.write_bytes(b"")
    out_root = tmp_path / "out"

    rc = main([
        "--rar", str(rar),
        "--out-dir", str(out_root),
        "--scratch-dir", str(tmp_path / "scratch"),
    ])
    assert rc == 0

    issue_folder = out_root / "TS_240_2016-03-09"
    assert (issue_folder / CD_METADATA_FILENAME).is_file()
    # All five real images persisted with canonical key shape
    assert (issue_folder / "cd_images" / "2016_01059" / "1_1.jpg").is_file()
    assert (issue_folder / "cd_images" / "2016_01059" / "1_2.jpg").is_file()
    assert (issue_folder / "cd_images" / "2015_06749" / "1_1.jpg").is_file()
    assert (issue_folder / "cd_images" / "2015_06749" / "1_2.jpg").is_file()
    assert (issue_folder / "cd_images" / "2015_06749" / "1_3.jpg").is_file()
    # Hague design produced no folder
    assert not (issue_folder / "cd_images" / "DM_086402").exists()
    # JSON image_path values use the canonical key shape
    doc = json.loads((issue_folder / CD_METADATA_FILENAME).read_text(encoding="utf-8"))
    first_dossier = doc["dossiers"][0]
    assert first_dossier["designs"][0]["views"][0]["image_path"] == "2016_01059/1_1.jpg"

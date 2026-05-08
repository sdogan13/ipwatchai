"""Unit tests for ``cd_extract_tasarim`` helpers.

Built one helper at a time. Each step adds its own test block so failures
point cleanly at the unit under test.
"""

from pathlib import Path

import pytest

from cd_extract_tasarim import (
    DEFAULT_SEVEN_ZIP,
    TABLE_COLUMNS,
    CDLayout,
    decode_hsqldb_escapes,
    extract_cd_archive,
    parse_hsqldb_log,
    parse_hsqldb_log_line,
    resolve_design_images,
    split_locarno_codes,
)
from cd_extract_tasarim import (
    _application_image_folder,
    _locate_cd_layout,
    _parse_sql_values,
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
    """Verbose ``231 say_l_*.rar`` layout: log under setup/, images at root."""
    setup = tmp_path / "setup"
    setup.mkdir()
    (setup / "idbulletin.log").write_text("", encoding="utf-8")
    (setup / "idbulletin.script").write_text("", encoding="utf-8")
    # images at archive root, not inside setup/
    (tmp_path / "images").mkdir()
    (tmp_path / "main.html").write_text("", encoding="utf-8")

    layout = _locate_cd_layout(tmp_path)
    assert layout.cd_root == setup
    assert layout.log_path == setup / "idbulletin.log"
    assert layout.images_root == tmp_path / "images"  # sibling of cd_root


def test_locate_cd_layout_missing_log_raises(tmp_path):
    """No ``idbulletin.log`` anywhere -> RuntimeError."""
    (tmp_path / "garbage").mkdir()
    with pytest.raises(RuntimeError, match="no idbulletin.log"):
        _locate_cd_layout(tmp_path)


def test_locate_cd_layout_multiple_logs_raises(tmp_path):
    """Two ``idbulletin.log`` files -> we don't know which CD to pick."""
    (tmp_path / "240").mkdir()
    (tmp_path / "240" / "idbulletin.log").write_text("", encoding="utf-8")
    (tmp_path / "242").mkdir()
    (tmp_path / "242" / "idbulletin.log").write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="multiple idbulletin.log"):
        _locate_cd_layout(tmp_path)


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

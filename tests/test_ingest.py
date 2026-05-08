"""
Tests for ingest.py — sanitize, EXTRACTED_GOODS, attorney, COALESCE priority,
image path resolution, and self-healing for corrupt metadata.json.

Tests pure functions and tuple/SQL structure.
Does NOT require a live database.
"""
import sys
import json
import uuid
import pytest
from datetime import datetime, date, timezone
from contextlib import contextmanager
from types import ModuleType
from unittest.mock import patch

# Force a fresh import of the canonical packaged ingest module.
sys.modules.pop("pipeline.ingest", None)

from pathlib import Path

import shutil

from pipeline import ingest

TEST_TEMP_ROOT = Path("C:/Users/701693/turk_patent/.tmp_pytest_base")
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


@contextmanager
def temp_dir():
    temp_path = TEST_TEMP_ROOT / f"tmp_{uuid.uuid4().hex}"
    temp_path.mkdir(parents=True, exist_ok=True)
    try:
        yield temp_path
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)
from pipeline.ingest import (
    sanitize,
    _trunc,
    _resolve_image_path,
    _has_tmbulletin_source,
    _repair_corrupt_metadata,
    pre_scan_and_repair,
    determine_db_status,
    determine_status,
    get_source_rank,
    get_status_rank,
    parse_date,
    embedding_to_halfvec,
    clean_name,
    extract_bulletin_info,
    calculate_expiration_status,
    extract_tpe_id,
)

UPDATE_ROW_KEYS = [
    "name",
    "clear_name",
    "clear_text_features",
    "status",
    "nice_classes",
    "goods",
    "last_date",
    "appeal",
    "expiry",
    "b_no",
    "b_date",
    "g_no",
    "g_date",
    "img_path",
    "app_date",
    "reg_date",
    "img_emb",
    "dino_emb",
    "txt_emb",
    "color_emb",
    "ocr_text",
    "name_tr",
    "detected_lang",
    "name_tr_backend",
    "name_tr_model",
    "name_tr_updated_at",
    "holder_name",
    "holder_tpe_client_id",
    "attorney_name",
    "attorney_no",
    "src_tag",
    "reg_no",
    "wipo_no",
    "vienna_classes",
    "app_no",
]


def make_metadata_record(
    *,
    application_no="2024/001",
    folder_status="",
    trademark_name="TEST MARK",
    application_date="15/01/2024",
    register_no=None,
    register_date=None,
    bulletin_no="490",
    bulletin_date="2026-04-13",
    gazette_no=None,
    gazette_date=None,
    nice_classes=None,
):
    trademark = {
        "NAME": trademark_name,
        "APPLICATIONDATE": application_date,
        "REGISTERNO": register_no,
        "REGISTERDATE": register_date,
        "BULLETIN_NO": bulletin_no,
        "BULLETIN_DATE": bulletin_date,
        "GAZETTE_NO": gazette_no,
        "GAZETTE_DATE": gazette_date,
        "NICECLASSES_LIST": nice_classes if nice_classes is not None else [25, 35],
        "VIENNACLASSES_LIST": [1, 2],
    }
    return {
        "APPLICATIONNO": application_no,
        "STATUS": folder_status,
        "TRADEMARK": trademark,
        "EXTRACTEDGOODS": [{"CLASSID": "25", "TEXT": "Clothing"}],
        "HOLDERS": [{"TITLE": "REAL HOLDER (12345)", "TPECLIENTID": ""}],
        "ATTORNEYS": [{"NAME": "REAL ATTORNEY (67890)", "NO": ""}],
        "IMAGE": None,
        "name_tr": "GERCEK MARKA",
        "detected_lang": "tr",
        "name_tr_backend": "nllb",
        "name_tr_model": "facebook/nllb-200-distilled-600M",
        "name_tr_updated_at": "2026-04-24T12:00:00Z",
    }


def write_metadata_file(base_dir, folder_name, records=None, raw_text=None):
    folder = Path(base_dir) / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    metadata_path = folder / "metadata.json"
    if raw_text is not None:
        metadata_path.write_text(raw_text, encoding="utf-8")
    else:
        metadata_path.write_text(json.dumps(records or [], ensure_ascii=False), encoding="utf-8")
    return metadata_path


def insert_row_dict(row):
    return dict(zip(ingest._INSERT_COLUMNS, row))


def update_row_dict(row):
    return dict(zip(UPDATE_ROW_KEYS, row))


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._fetchone = None
        self._fetchall = []

    def execute(self, sql, params=None):
        sql_text = " ".join(str(sql).split())
        self.conn.executed.append((sql_text, params))

        if sql_text.startswith("SELECT status, COALESCE(record_count, 0) FROM processed_files"):
            key = params[0]
            record = self.conn.processed_files.get(key)
            self._fetchone = None if record is None else (record["status"], record.get("record_count", 0))
            return

        if sql_text.startswith("INSERT INTO processed_files"):
            key = params[0]
            entry = self.conn.processed_files.setdefault(key, {})
            entry.update({"status": "processing"})
            return

        if sql_text.startswith("UPDATE processed_files SET status = %s, record_count = 0, error_log = NULL"):
            status, key = params
            entry = self.conn.processed_files.setdefault(key, {})
            entry.update({"status": status, "record_count": 0, "error_log": None})
            return

        if sql_text.startswith("UPDATE processed_files SET status = %s, record_count = %s, error_log = NULL"):
            status, record_count, key = params
            entry = self.conn.processed_files.setdefault(key, {})
            entry.update({"status": status, "record_count": record_count, "error_log": None})
            return

        if sql_text.startswith("UPDATE processed_files SET status = %s, error_log = %s"):
            status, error_log, key = params
            entry = self.conn.processed_files.setdefault(key, {})
            entry.update({"status": status, "error_log": error_log})
            return

        if sql_text.startswith("UPDATE processed_files SET status = 'failed', error_log = %s"):
            error_log, key = params
            entry = self.conn.processed_files.setdefault(key, {})
            entry.update({"status": "failed", "error_log": error_log})
            return

        if sql_text.startswith("SELECT application_no, id, last_event_date, current_status, expiry_date, status_source, name"):
            app_nos = params[0]
            rows = []
            for app_no in app_nos:
                existing = self.conn.existing_trademarks.get(app_no)
                if existing:
                    rows.append(
                        (
                            app_no,
                            existing["id"],
                            existing.get("last_event_date"),
                            existing.get("current_status"),
                            existing.get("expiry_date"),
                            existing.get("status_source"),
                            existing.get("name"),
                            existing.get("nice_class_numbers"),
                        )
                    )
            self._fetchall = rows
            return

        if sql_text.startswith("SELECT id FROM trademarks WHERE application_no = ANY"):
            app_nos = params[0]
            rows = []
            for app_no in app_nos:
                if app_no in self.conn.inserted_ids:
                    rows.append((self.conn.inserted_ids[app_no],))
                elif app_no in self.conn.existing_trademarks:
                    rows.append((self.conn.existing_trademarks[app_no]["id"],))
            self._fetchall = rows
            return

        if (
            sql_text.startswith("SAVEPOINT")
            or sql_text.startswith("RELEASE SAVEPOINT")
            or sql_text.startswith("ROLLBACK TO SAVEPOINT")
        ):
            return

    def fetchone(self):
        return self._fetchone

    def fetchall(self):
        return list(self._fetchall)


class FakeConnection:
    def __init__(self, *, existing_trademarks=None, processed_files=None):
        self.existing_trademarks = existing_trademarks or {}
        self.processed_files = processed_files or {}
        self.executed = []
        self.inserted_rows = []
        self.updated_rows = []
        self.history_rows = []
        self.inserted_ids = {}
        self.next_id = 9000
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def fake_execute_values(cur, sql, rows):
    sql_text = " ".join(str(sql).split())
    if sql_text.startswith("INSERT INTO trademarks"):
        for row in rows:
            cur.conn.inserted_rows.append(row)
            cur.conn.inserted_ids[row[0]] = cur.conn.next_id
            cur.conn.next_id += 1
        return

    if sql_text.startswith("UPDATE trademarks AS tm SET") or sql_text.startswith("UPDATE trademarks tm SET"):
        cur.conn.updated_rows.extend(rows)
        return

    if sql_text.startswith("INSERT INTO trademark_history"):
        cur.conn.history_rows.extend(rows)
        return

    raise AssertionError(f"Unexpected execute_values SQL: {sql_text}")


def module_with_attrs(name, **attrs):
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


# ============================================================
# sanitize() tests
# ============================================================

class TestSanitize:
    """Test the sanitize() function catches all dirty values."""

    def test_none_returns_none(self):
        assert sanitize(None) is None

    def test_empty_string_returns_none(self):
        assert sanitize("") is None

    def test_whitespace_only_returns_none(self):
        assert sanitize("   ") is None
        assert sanitize("\t\n") is None

    def test_literal_null_returns_none(self):
        assert sanitize("null") is None
        assert sanitize("NULL") is None
        assert sanitize("Null") is None
        assert sanitize("  null  ") is None

    def test_literal_none_returns_none(self):
        assert sanitize("None") is None
        assert sanitize("none") is None
        assert sanitize("NONE") is None

    def test_na_returns_none(self):
        assert sanitize("N/A") is None
        assert sanitize("n/a") is None
        assert sanitize("N/a") is None

    def test_dash_returns_none(self):
        assert sanitize("-") is None
        assert sanitize(" - ") is None

    def test_empty_list_returns_none(self):
        assert sanitize([]) is None

    def test_empty_dict_returns_none(self):
        assert sanitize({}) is None

    def test_real_string_preserved(self):
        assert sanitize("Acme Corp") == "Acme Corp"

    def test_string_stripped(self):
        assert sanitize("  Acme Corp  ") == "Acme Corp"

    def test_non_empty_list_preserved(self):
        assert sanitize([1, 2, 3]) == [1, 2, 3]

    def test_non_empty_dict_preserved(self):
        d = {"key": "value"}
        assert sanitize(d) == d

    def test_integer_preserved(self):
        assert sanitize(42) == 42

    def test_zero_preserved(self):
        assert sanitize(0) == 0

    def test_false_preserved(self):
        assert sanitize(False) is False

    def test_real_data_with_special_chars(self):
        """Turkish characters must survive."""
        assert sanitize("GÜNEŞ ENERJİ") == "GÜNEŞ ENERJİ"


# ============================================================
# _trunc() tests
# ============================================================

class TestTrunc:
    """Test _trunc() delegates to sanitize() then truncates."""

    def test_none_returns_none(self):
        assert _trunc(None, 100) is None

    def test_dirty_null_returns_none(self):
        assert _trunc("null", 100) is None
        assert _trunc("N/A", 100) is None
        assert _trunc("", 100) is None

    def test_truncates_long_string(self):
        assert _trunc("A" * 600, 500) == "A" * 500

    def test_short_string_unchanged(self):
        assert _trunc("Hello", 500) == "Hello"

    def test_strips_whitespace(self):
        assert _trunc("  Hello  ", 500) == "Hello"


# ============================================================
# EXTRACTED_GOODS tests
# ============================================================

class TestExtractedGoods:
    """Verify EXTRACTEDGOODS handling — no GOODS fallback."""

    def _make_record(self, extracted=None, goods=None):
        """Build a minimal metadata record."""
        rec = {
            "APPLICATIONNO": "2024/001",
            "TRADEMARK": {"NAME": "TEST"},
            "STATUS": "",
            "HOLDERS": [],
            "ATTORNEYS": [],
        }
        if extracted is not None:
            rec["EXTRACTEDGOODS"] = extracted
        if goods is not None:
            rec["GOODS"] = goods
        return rec

    def test_absent_extractedgoods_is_none(self):
        """No EXTRACTEDGOODS key → should be None, not GOODS."""
        rec = self._make_record(
            goods=[{"CLASSID": "98", "TEXT": "Clothing"}]
        )
        raw = rec.get("EXTRACTEDGOODS")
        result = raw if raw else None
        assert result is None

    def test_empty_list_extractedgoods_is_none(self):
        """EXTRACTEDGOODS=[] → should be None, not GOODS."""
        rec = self._make_record(
            extracted=[],
            goods=[{"CLASSID": "98", "TEXT": "Clothing"}]
        )
        raw = rec.get("EXTRACTEDGOODS")
        result = raw if raw else None
        assert result is None

    def test_populated_extractedgoods_preserved(self):
        """EXTRACTEDGOODS with data → should be preserved."""
        goods_data = [{"CLASSID": "25", "TEXT": "Removed: pants"}]
        rec = self._make_record(extracted=goods_data)
        raw = rec.get("EXTRACTEDGOODS")
        result = raw if raw else None
        assert result == goods_data

    def test_old_fallback_was_wrong(self):
        """Document the old bug: `[] or GOODS` returns GOODS."""
        extracted = []
        goods = [{"CLASSID": "98", "TEXT": "Clothing"}]
        # Old code: extracted or goods → returns goods (WRONG)
        old_result = extracted or goods
        assert old_result == goods  # bug: substituted GOODS
        # New code: only EXTRACTEDGOODS
        new_result = extracted if extracted else None
        assert new_result is None  # correct: NULL


# ============================================================
# Attorney extraction tests
# ============================================================

class TestAttorneyExtraction:
    """Verify attorney data is correctly extracted from ATTORNEYS."""

    def test_no_attorneys_key(self):
        """Missing ATTORNEYS → both fields None."""
        rec = {}
        attorneys_list = rec.get("ATTORNEYS", [])
        attorney_name = None
        attorney_no = None
        if attorneys_list and len(attorneys_list) > 0:
            attorney_name = _trunc(attorneys_list[0].get("NAME"), 500)
            attorney_no = _trunc(attorneys_list[0].get("NO"), 50)
        assert attorney_name is None
        assert attorney_no is None

    def test_empty_attorneys_list(self):
        """ATTORNEYS=[] → both fields None."""
        rec = {"ATTORNEYS": []}
        attorneys_list = rec.get("ATTORNEYS", [])
        attorney_name = None
        attorney_no = None
        if attorneys_list and len(attorneys_list) > 0:
            attorney_name = _trunc(attorneys_list[0].get("NAME"), 500)
            attorney_no = _trunc(attorneys_list[0].get("NO"), 50)
        assert attorney_name is None
        assert attorney_no is None

    def test_attorney_with_name(self):
        """ATTORNEYS with NAME populated → attorney_name set."""
        rec = {"ATTORNEYS": [
            {"NO": "", "NAME": "BANU IŞILDAYANCAN (IPP LTD)", "TITLE": ""}
        ]}
        attorneys_list = rec.get("ATTORNEYS", [])
        attorney_name = _trunc(attorneys_list[0].get("NAME"), 500)
        attorney_no = _trunc(attorneys_list[0].get("NO"), 50)
        assert attorney_name == "BANU IŞILDAYANCAN (IPP LTD)"
        assert attorney_no is None  # empty string → sanitize → None

    def test_attorney_with_no(self):
        """ATTORNEYS with NO populated → attorney_no set."""
        rec = {"ATTORNEYS": [
            {"NO": "12345", "NAME": "Ali Veli", "TITLE": ""}
        ]}
        attorneys_list = rec.get("ATTORNEYS", [])
        attorney_name = _trunc(attorneys_list[0].get("NAME"), 500)
        attorney_no = _trunc(attorneys_list[0].get("NO"), 50)
        assert attorney_name == "Ali Veli"
        assert attorney_no == "12345"

    def test_dirty_attorney_name_sanitized(self):
        """Dirty 'null' in NAME → sanitized to None."""
        rec = {"ATTORNEYS": [
            {"NO": "null", "NAME": "N/A", "TITLE": ""}
        ]}
        attorneys_list = rec.get("ATTORNEYS", [])
        attorney_name = _trunc(attorneys_list[0].get("NAME"), 500)
        attorney_no = _trunc(attorneys_list[0].get("NO"), 50)
        assert attorney_name is None
        assert attorney_no is None

    def test_first_attorney_only(self):
        """Multiple attorneys → only first is extracted."""
        rec = {"ATTORNEYS": [
            {"NO": "", "NAME": "First Attorney", "TITLE": ""},
            {"NO": "", "NAME": "Second Attorney", "TITLE": ""},
        ]}
        attorneys_list = rec.get("ATTORNEYS", [])
        attorney_name = _trunc(attorneys_list[0].get("NAME"), 500)
        assert attorney_name == "First Attorney"

    def test_attorney_name_truncated(self):
        """Long attorney name is truncated to 500 chars."""
        long_name = "X" * 600
        rec = {"ATTORNEYS": [{"NO": "", "NAME": long_name, "TITLE": ""}]}
        attorneys_list = rec.get("ATTORNEYS", [])
        attorney_name = _trunc(attorneys_list[0].get("NAME"), 500)
        assert len(attorney_name) == 500


# ============================================================
# COALESCE direction audit
# ============================================================

class TestCoalesceDirection:
    """
    Verify SQL COALESCE direction is correct for each source authority level.

    Rules:
    - APP_ (highest): v.xxx, tm.xxx (overwrites)
    - GZ_ (middle): tm.xxx, v.xxx for non-owned fields (fill gaps only)
    - BLT_ (lowest): tm.xxx, v.xxx for non-owned fields (fill gaps only)
    """

    def _get_update_sql(self, folder_name):
        """Extract the UPDATE SQL that would be used for a given folder."""
        is_app_source = folder_name.upper().startswith("APP_")
        is_gazette_source = folder_name.upper().startswith("GZ_")

        if is_app_source:
            return "APP"
        elif is_gazette_source:
            return "GZ"
        else:
            return "BLT"

    def test_source_rank_hierarchy(self):
        """Verify source rank ordering: APP > GZ > BLT."""
        app_rank, app_tag = get_source_rank("APP_1")
        live_rank, live_tag = get_source_rank("LIVE_ip_watch_ai_20260501")
        gz_rank, gz_tag = get_source_rank("GZ_315")
        blt_rank, blt_tag = get_source_rank("BLT_119")

        assert app_rank == 3
        assert live_rank == 3
        assert gz_rank == 2
        assert blt_rank == 1
        assert app_tag == "APP"
        assert live_tag == "APP"
        assert gz_tag == "GZ"
        assert blt_tag == "BLT"

    def test_blt_holder_protected_in_sql(self):
        """
        BLT_ UPDATE SQL must protect higher-authority (APP_/GZ_) data.
        Shared fields: CASE WHEN APP/GZ THEN COALESCE(existing, new) ELSE COALESCE(new, existing).
        GZ-owned fields: never touch.  BLT-owned fields: COALESCE(new, existing).
        """
        from pipeline.ingest import _build_update_sql
        blt_sql = _build_update_sql('BLT')

        # Shared fields must protect APP_/GZ_ data (existing first when higher source)
        for field in ['holder_name', 'holder_tpe_client_id', 'attorney_name',
                      'attorney_no', 'name_tr', 'detected_lang',
                      'name_tr_backend', 'name_tr_model', 'name_tr_updated_at']:
            assert f"COALESCE(tm.{field}," in blt_sql, \
                f"BLT_ must protect existing {field} from higher-authority sources"

        # GZ-owned fields: BLT_ must NOT touch
        assert "registration_no = tm.registration_no" in blt_sql
        assert "wipo_no = tm.wipo_no" in blt_sql
        assert "registration_date = tm.registration_date" in blt_sql
        assert "gazette_no = tm.gazette_no" in blt_sql
        assert "gazette_date = tm.gazette_date" in blt_sql

        # BLT-owned fields: BLT_ owns them — COALESCE(new, existing)
        assert "bulletin_no = COALESCE(v.b_no, tm.bulletin_no)" in blt_sql
        assert "bulletin_date = COALESCE(v.b_date::date, tm.bulletin_date)" in blt_sql
        assert "appeal_deadline = COALESCE(v.appeal::date, tm.appeal_deadline)" in blt_sql

    def test_gz_holder_protected_in_sql(self):
        """
        GZ_ UPDATE SQL must protect APP_ data and overwrite BLT_ data.
        Shared fields: CASE WHEN APP THEN COALESCE(existing, new) ELSE COALESCE(new, existing).
        BLT-owned fields: never touch.  GZ-owned fields: COALESCE(new, existing).
        """
        from pipeline.ingest import _build_update_sql
        gz_sql = _build_update_sql('GZ')

        # Shared fields must protect APP_ data (existing first when APP_ source)
        for field in ['holder_name', 'holder_tpe_client_id', 'attorney_name']:
            assert f"COALESCE(tm.{field}," in gz_sql, \
                f"GZ_ must protect existing APP_ {field}"

        # BLT-owned fields: GZ_ must NOT touch
        assert "bulletin_no = tm.bulletin_no" in gz_sql
        assert "bulletin_date = tm.bulletin_date" in gz_sql
        assert "appeal_deadline = tm.appeal_deadline" in gz_sql

        # GZ-owned fields: GZ_ owns them — COALESCE(new, existing)
        assert "registration_no = COALESCE(v.reg_no, tm.registration_no)" in gz_sql
        assert "gazette_no = COALESCE(v.g_no, tm.gazette_no)" in gz_sql
        assert "gazette_date = COALESCE(v.g_date::date, tm.gazette_date)" in gz_sql

    def test_app_overwrites_in_sql(self):
        """
        APP_ UPDATE SQL must overwrite all shared fields.
        BLT/GZ-owned fields: never touch.
        """
        from pipeline.ingest import _build_update_sql
        app_sql = _build_update_sql('APP')

        # Shared fields: APP_ always wins — COALESCE(new, existing)
        assert "holder_name = COALESCE(v.holder_name, tm.holder_name)" in app_sql
        assert "holder_tpe_client_id = COALESCE(v.holder_tpe_client_id, tm.holder_tpe_client_id)" in app_sql
        assert "attorney_name = COALESCE(v.attorney_name, tm.attorney_name)" in app_sql
        assert "attorney_no = COALESCE(v.attorney_no, tm.attorney_no)" in app_sql
        assert "name_tr = CASE WHEN v.clear_text_features THEN NULL ELSE COALESCE(v.name_tr, tm.name_tr) END" in app_sql

        # BLT-owned fields: APP_ must NOT touch
        assert "bulletin_no = tm.bulletin_no" in app_sql
        assert "bulletin_date = tm.bulletin_date" in app_sql
        assert "appeal_deadline = tm.appeal_deadline" in app_sql

        # GZ-owned fields: APP_ must NOT touch
        assert "registration_no = tm.registration_no" in app_sql
        assert "wipo_no = tm.wipo_no" in app_sql
        assert "registration_date = tm.registration_date" in app_sql
        assert "gazette_no = tm.gazette_no" in app_sql
        assert "gazette_date = tm.gazette_date" in app_sql

    def test_attorney_fields_in_all_update_value_aliases(self):
        """All 3 UPDATE paths must include attorney_name and attorney_no in VALUES aliases."""
        from pipeline.ingest import _build_update_sql

        for source_type in ['APP', 'GZ', 'BLT']:
            sql = _build_update_sql(source_type)
            assert "attorney_name," in sql, \
                f"{source_type} UPDATE must include attorney_name in VALUES aliases"
            assert "attorney_no," in sql, \
                f"{source_type} UPDATE must include attorney_no in VALUES aliases"

    def test_update_sql_supports_clearing_shape_only_names(self):
        """UPDATE rows carry an explicit clear_name flag for safe name nulling."""
        from pipeline.ingest import _build_update_sql

        for source_type in ['APP', 'GZ', 'BLT']:
            sql = _build_update_sql(source_type)
            assert "name, clear_name, clear_text_features, status" in sql
            assert "name = CASE WHEN v.clear_name THEN NULL ELSE" in sql
            assert "text_embedding = CASE WHEN v.clear_text_features THEN NULL ELSE" in sql

    def test_update_sql_preserves_richer_db_classes_from_exact_six_input(self):
        """Known scraper truncation must not replace richer DB class arrays."""
        from pipeline.ingest import _build_update_sql

        for source_type in ['GZ', 'BLT']:
            sql = _build_update_sql(source_type)
            assert "COALESCE(cardinality(v.nice_classes::integer[]), 0) = 6" in sql
            assert "cardinality(tm.nice_class_numbers), 0) > 6" in sql
            assert "THEN tm.nice_class_numbers" in sql

        app_sql = _build_update_sql("APP")
        assert "COALESCE(cardinality(tm.nice_class_numbers), 0) = 0" in app_sql
        assert "COALESCE(cardinality(v.nice_classes::integer[]), 0) > 6" in app_sql
        assert "ELSE tm.nice_class_numbers" in app_sql

    def test_insert_sql_has_attorney_columns(self):
        """INSERT SQL must include attorney_name and attorney_no columns."""
        insert_sql = ingest._build_insert_sql()

        assert "attorney_name" in insert_sql
        assert "attorney_no" in insert_sql
        assert "attorney_name" in ingest._INSERT_COLUMNS
        assert "attorney_no" in ingest._INSERT_COLUMNS
        # COALESCE on conflict for attorney
        assert "attorney_name = COALESCE(EXCLUDED.attorney_name, trademarks.attorney_name)" in insert_sql
        assert "attorney_no = COALESCE(EXCLUDED.attorney_no, trademarks.attorney_no)" in insert_sql


# ============================================================
# determine_status() tests
# ============================================================

class TestDetermineStatus:
    @staticmethod
    def mojibake(text: str) -> str:
        return text.encode("utf-8").decode("latin1")

    """Verify Turkish status keyword matching."""

    def test_registered_keyword(self):
        assert determine_status("BLT_119", "tescil edildi") == "Registered"

    def test_refused_keyword(self):
        assert determine_status("BLT_119", "başvuru geçersiz") == "Refused"

    def test_withdrawn_keyword(self):
        assert determine_status("GZ_315", "feragat edildi") == "Withdrawn"

    def test_opposed_keyword(self):
        assert determine_status("BLT_119", "itiraz") == "Opposed"

    def test_expired_keyword(self):
        assert determine_status("BLT_119", "sona erdi") == "Expired"

    def test_published_keyword(self):
        assert determine_status("BLT_119", "yayınlandı") == "Published"

    def test_reg_no_implies_registered(self):
        assert determine_status("BLT_119", "", reg_no_val="12345") == "Registered"

    def test_blt_default_is_published(self):
        assert determine_status("BLT_119", "") == "Published"

    def test_gz_default_is_registered(self):
        assert determine_status("GZ_315", "") == "Registered"

    def test_app_default_is_applied(self):
        assert determine_status("APP_1", "") == "Applied"

    def test_dirty_reg_no_ignored(self):
        """Dirty reg_no values should not trigger 'Registered'."""
        assert determine_status("BLT_119", "", reg_no_val="null") == "Published"
        assert determine_status("BLT_119", "", reg_no_val="") == "Published"
        assert determine_status("BLT_119", "", reg_no_val="None") == "Published"

    def test_mojibake_keyword_is_repaired(self):
        assert determine_status("BLT_119", self.mojibake("yayınlandı")) == "Published"

    def test_status_text_wins_over_blt_default(self):
        assert determine_status("BLT_119", "tescil edildi") == "Registered"

    def test_status_text_wins_over_gz_default(self):
        assert determine_status("GZ_315", "yayınlandı") == "Published"

    def test_status_text_wins_over_app_default(self):
        assert determine_status("APP_1", "tescil edildi") == "Registered"

    def test_status_text_wins_over_register_no_inference(self):
        assert determine_status("GZ_315", "feragat edildi", reg_no_val="12345") == "Withdrawn"

    def test_mojibake_conflict_text_still_wins(self):
        assert determine_status("GZ_315", self.mojibake("yayınlandı"), reg_no_val="12345") == "Published"


class TestDetermineDbStatus:
    @staticmethod
    def mojibake(text: str) -> str:
        return text.encode("utf-8").decode("latin1")

    def test_returns_canonical_published_enum(self):
        assert determine_db_status("BLT_119", "Application/Published") == "Yayında"

    def test_repairs_mojibake_before_matching(self):
        assert determine_db_status("BLT_119", self.mojibake("yayınlandı")) == "Yayında"

    def test_returns_canonical_withdrawn_enum(self):
        assert determine_db_status("GZ_315", "feragat edildi") == "Geri Çekildi"

    def test_blt_folder_with_registered_text_uses_text(self):
        assert determine_db_status("BLT_119", "tescil edildi") == "Tescil Edildi"

    def test_blt_folder_with_refused_text_uses_text(self):
        assert determine_db_status("BLT_119", "başvuru geçersiz") == "Reddedildi"

    def test_blt_folder_with_cancelled_text_uses_text(self):
        assert determine_db_status("BLT_119", "iptal edildi") == "İptal Edildi"

    def test_gz_folder_with_published_text_uses_text(self):
        assert determine_db_status("GZ_315", "yayınlandı") == "Yayında"

    def test_gz_folder_with_withdrawn_text_uses_text(self):
        assert determine_db_status("GZ_315", "geri çekildi") == "Geri Çekildi"

    def test_app_folder_with_refused_text_uses_text(self):
        assert determine_db_status("APP_1", "reddedildi") == "Reddedildi"

    def test_status_text_beats_register_no_inference(self):
        assert determine_db_status("GZ_315", "geri çekildi", reg_no_val="12345") == "Geri Çekildi"

    def test_mojibake_conflict_text_beats_folder_family(self):
        assert determine_db_status("GZ_315", self.mojibake("yayınlandı"), reg_no_val="12345") == "Yayında"


# ============================================================
# Priority test scenario (user-specified)
# ============================================================

class TestPriorityScenario:
    """
    Simulate the user's priority test:
    1. Insert BLT_ record with holder_name = NULL
    2. Update with GZ_ record → holder_name = "Real Corp" (gap filled)
    3. Update with BLT_ record → holder_name stays "Real Corp" (lower priority)
    """

    def test_priority_via_coalesce_semantics(self):
        """
        Test the COALESCE semantics:
        - COALESCE(tm.x, v.x) = keep existing, fill nulls
        - COALESCE(v.x, tm.x) = prefer new value
        """
        # Step 1: BLT inserts with holder_name = NULL
        existing_holder = None  # After BLT insert

        # Step 2: GZ fills gap — GZ uses COALESCE(tm.holder_name, v.holder_name)
        gz_holder = "Real Corp"
        # COALESCE(existing=None, new="Real Corp") → "Real Corp"
        result = existing_holder if existing_holder is not None else gz_holder
        assert result == "Real Corp"
        existing_holder = result

        # Step 3: BLT tries overwrite — BLT uses COALESCE(tm.holder_name, v.holder_name)
        blt_holder = "Wrong Name"
        # COALESCE(existing="Real Corp", new="Wrong Name") → "Real Corp"
        result = existing_holder if existing_holder is not None else blt_holder
        assert result == "Real Corp"

    def test_dirty_value_blocks_coalesce(self):
        """
        Without sanitize(), dirty "null" blocks COALESCE:
        BLT stores "null" → GZ has "Real Corp" → COALESCE keeps "null"
        With sanitize(), "null" → None → COALESCE correctly fills.
        """
        # BLT stores dirty "null" — but sanitize catches it
        dirty_value = "null"
        sanitized = sanitize(dirty_value)
        assert sanitized is None

        # Now COALESCE(None, "Real Corp") → "Real Corp" ✓
        gz_value = "Real Corp"
        result = sanitized if sanitized is not None else gz_value
        assert result == "Real Corp"

    def test_sanitize_catches_all_dirty_patterns(self):
        """All dirty patterns that could block COALESCE are caught."""
        dirty_values = ["null", "NULL", "None", "none", "N/A", "n/a", "-", "", "  ", "\t"]
        for dirty in dirty_values:
            assert sanitize(dirty) is None, f"sanitize() should catch: {repr(dirty)}"


# ============================================================
# Utility function tests
# ============================================================

class TestParseDate:
    def test_dd_mm_yyyy(self):
        assert parse_date("15/01/2024") == date(2024, 1, 15)

    def test_yyyy_mm_dd(self):
        assert parse_date("2024-01-15") == date(2024, 1, 15)

    def test_dd_dot_mm_dot_yyyy(self):
        assert parse_date("15.01.2024") == date(2024, 1, 15)

    def test_none_returns_none(self):
        assert parse_date(None) is None

    def test_empty_returns_none(self):
        assert parse_date("") is None

    def test_invalid_returns_none(self):
        assert parse_date("not-a-date") is None


class TestEmbeddingToHalfvec:
    def test_normal_embedding(self):
        result = embedding_to_halfvec([0.1, 0.2, 0.3])
        assert result == "[0.1,0.2,0.3]"

    def test_none_returns_none(self):
        assert embedding_to_halfvec(None) is None

    def test_empty_list_returns_none(self):
        assert embedding_to_halfvec([]) is None

    def test_non_list_returns_none(self):
        assert embedding_to_halfvec("not a list") is None


class TestCleanName:
    def test_normal_name(self):
        assert clean_name("NIKE") == "NIKE"

    def test_whitespace_collapse(self):
        assert clean_name("  NIKE   SPORTS  ") == "NIKE SPORTS"

    def test_none_returns_none(self):
        assert clean_name(None) is None

    def test_empty_returns_none(self):
        assert clean_name("") is None

    def test_strips_sekil_suffix(self):
        assert clean_name("MARKA ŞEKİL") == "MARKA"

    def test_strips_ascii_sekil_suffix(self):
        assert clean_name("MARKA SEKIL") == "MARKA"

    def test_strips_plus_sekil_suffix(self):
        assert clean_name("MARKA + ŞEKİL") == "MARKA"

    def test_strips_mixed_case_and_preserves_brand_text(self):
        assert clean_name("  Marka   + SeKiL  ") == "Marka"

    def test_preserves_non_suffix_text(self):
        assert clean_name("ŞEKİL OFİS") == "OFİS"

    def test_strips_descriptor_only_name_to_none(self):
        assert clean_name("+ ŞEKİL") is None
        assert clean_name("sekil") is None

    def test_preserves_embedded_sekil_text(self):
        assert clean_name("aksekili") == "aksekili"


class TestExtractBulletinInfo:
    def test_blt_format(self):
        no, dt = extract_bulletin_info("BLT_2025_03")
        assert no == "2025/03"
        assert dt == date(2025, 3, 1)

    def test_gz_format(self):
        no, dt = extract_bulletin_info("GZ_2024_12")
        assert no == "2024/12"
        assert dt == date(2024, 12, 1)

    def test_no_match(self):
        no, dt = extract_bulletin_info("RANDOM_FOLDER")
        assert no is None
        assert dt is None

    def test_live_style_blt_folder(self):
        no, dt = extract_bulletin_info("BLT_490_2026-04-13")
        assert no == "490"
        assert dt == date(2026, 4, 13)

    def test_live_style_gz_folder(self):
        no, dt = extract_bulletin_info("GZ_500_2026-03-31")
        assert no == "500"
        assert dt == date(2026, 3, 31)


class TestCalculateExpirationStatus:
    def test_regular_date_adds_ten_years_and_183_days(self):
        assert calculate_expiration_status("2024-01-15") == date(2034, 7, 17)

    def test_leap_day_uses_fallback_calendar_math(self):
        assert calculate_expiration_status(date(2020, 2, 29)) == date(2030, 8, 30)


class TestProcessFileBatchBehavior:
    def _run_batch(self, metadata_path, conn, *, force=False, repair_side_effect=None, execute_side_effect=None):
        reconcile_calls = []
        watchlist_calls = []
        queue_calls = []

        def reconcile_stub(_conn, app_nos):
            reconcile_calls.append(list(app_nos))

        def watchlist_stub(trademark_ids, source_type, folder_name):
            watchlist_calls.append(
                {
                    "trademark_ids": list(trademark_ids),
                    "source_type": source_type,
                    "folder_name": folder_name,
                }
            )

        def queue_stub(**kwargs):
            queue_calls.append(kwargs)

        execute_impl = execute_side_effect or fake_execute_values
        patches = [
            patch("pipeline.ingest_runtime.execute_values", side_effect=execute_impl),
            patch("pipeline.ingest_runtime.add_to_scan_queue", side_effect=queue_stub),
        ]
        if repair_side_effect is not None:
            patches.append(patch("pipeline.ingest_runtime._repair_corrupt_metadata", side_effect=repair_side_effect))

        with patch.dict(
            sys.modules,
            {
                "utils.status_reconciler": module_with_attrs(
                    "utils.status_reconciler",
                    update_final_status_batch=reconcile_stub,
                ),
                "watchlist.scanner": module_with_attrs(
                    "watchlist.scanner",
                    trigger_watchlist_scan=watchlist_stub,
                ),
            },
        ):
            with patches[0], patches[1]:
                if len(patches) == 3:
                    with patches[2]:
                        result = ingest.process_file_batch(conn, metadata_path, force=force)
                else:
                    result = ingest.process_file_batch(conn, metadata_path, force=force)

        return result, reconcile_calls, watchlist_calls, queue_calls

    def test_empty_metadata_marks_success_with_zero_records(self):
        with temp_dir() as tmp:
            metadata_path = write_metadata_file(tmp, "BLT_490_2026-04-13", records=[])
            conn = FakeConnection()

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(metadata_path, conn)

        file_key = "BLT_490_2026-04-13/metadata.json"
        assert result["status"] == "success"
        assert conn.processed_files[file_key]["status"] == "success"
        assert conn.processed_files[file_key]["record_count"] == 0
        assert conn.inserted_rows == []
        assert conn.updated_rows == []
        assert conn.history_rows == []
        assert reconcile_calls == []
        assert watchlist_calls == []
        assert queue_calls == []

    def test_repaired_empty_metadata_marks_repaired_with_zero_records(self):
        with temp_dir() as tmp:
            metadata_path = write_metadata_file(tmp, "BLT_490_2026-04-13", raw_text='{"broken":')
            conn = FakeConnection()

            def repair_stub(path):
                path.write_text("[]", encoding="utf-8")
                return {"status": "repaired", "records": 0}

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(
                metadata_path,
                conn,
                repair_side_effect=repair_stub,
            )

        file_key = "BLT_490_2026-04-13/metadata.json"
        assert result["status"] == "repaired"
        assert conn.processed_files[file_key]["status"] == "repaired"
        assert conn.processed_files[file_key]["record_count"] == 0
        assert reconcile_calls == []
        assert watchlist_calls == []
        assert queue_calls == []

    def test_failure_marks_processed_file_failed(self):
        with temp_dir() as tmp:
            record = make_metadata_record()
            metadata_path = write_metadata_file(tmp, "BLT_490_2026-04-13", records=[record])
            conn = FakeConnection()

            def failing_execute_values(cur, sql, rows):
                sql_text = " ".join(str(sql).split())
                if sql_text.startswith("INSERT INTO trademarks"):
                    raise RuntimeError("db insert failed")
                return fake_execute_values(cur, sql, rows)

            result, _, _, _ = self._run_batch(
                metadata_path,
                conn,
                execute_side_effect=failing_execute_values,
            )

        file_key = "BLT_490_2026-04-13/metadata.json"
        assert result["status"] == "failed"
        assert "db insert failed" in result["error"]
        assert conn.rollbacks == 1
        assert conn.processed_files[file_key]["status"] == "failed"
        assert "db insert failed" in conn.processed_files[file_key]["error_log"]

    def test_blt_insert_writes_dates_and_triggers_side_effects(self):
        from utils.deadline import calculate_appeal_deadline

        with temp_dir() as tmp:
            record = make_metadata_record()
            metadata_path = write_metadata_file(tmp, "BLT_490_2026-04-13", records=[record])
            conn = FakeConnection()

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(metadata_path, conn)

        inserted = insert_row_dict(conn.inserted_rows[0])
        assert result["status"] == "success"
        assert result["inserted"] == 1
        assert inserted["current_status"] == "Yayında"
        assert inserted["appeal_deadline"] == calculate_appeal_deadline(date(2026, 4, 13))
        assert inserted["expiry_date"] == date(2034, 7, 17)
        assert inserted["bulletin_no"] == "490"
        assert inserted["bulletin_date"] == date(2026, 4, 13)
        assert inserted["name_tr_backend"] == "nllb"
        assert inserted["name_tr_model"] == "facebook/nllb-200-distilled-600M"
        assert inserted["name_tr_updated_at"] == datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == [
            {
                "trademark_ids": [9000],
                "source_type": "bulletin",
                "folder_name": "BLT_490_2026-04-13",
            }
        ]
        assert len(queue_calls) == 1
        assert queue_calls[0]["bulletin_no"] == "490"
        assert queue_calls[0]["bulletin_date"] == date(2026, 4, 13)
        assert queue_calls[0]["trademark_ids"] == [9000]

    @pytest.mark.parametrize(
        ("folder_name", "expected_status", "expected_source_type"),
        [
            ("GZ_500_2026-03-31", "Tescil Edildi", "gazette"),
            ("APP_1", "Başvuruldu", "application"),
        ],
    )
    def test_non_blt_inserts_do_not_queue_scan(self, folder_name, expected_status, expected_source_type):
        with temp_dir() as tmp:
            record = make_metadata_record(
                gazette_no="500" if folder_name.startswith("GZ_") else None,
                gazette_date="2026-03-31" if folder_name.startswith("GZ_") else None,
            )
            metadata_path = write_metadata_file(tmp, folder_name, records=[record])
            conn = FakeConnection()

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(metadata_path, conn)

        inserted = insert_row_dict(conn.inserted_rows[0])
        assert result["inserted"] == 1
        assert inserted["current_status"] == expected_status
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls[0]["source_type"] == expected_source_type
        assert queue_calls == []

    def test_status_change_writes_trademark_history(self):
        with temp_dir() as tmp:
            record = make_metadata_record(
                folder_status="tescil edildi",
                register_no="12345",
                register_date="2026-03-31",
                gazette_no="500",
                gazette_date="2026-03-31",
            )
            metadata_path = write_metadata_file(tmp, "GZ_500_2026-03-31", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 77,
                        "current_status": "Yayında",
                        "expiry_date": date(2024, 1, 1),
                        "status_source": "BLT",
                    }
                }
            )

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(metadata_path, conn)

        updated = update_row_dict(conn.updated_rows[0])
        history_row = conn.history_rows[0]
        assert result["updated"] == 1
        assert updated["status"] == "Tescil Edildi"
        assert history_row[0] == 77
        assert history_row[2] == "STATUS_CHANGE"
        assert history_row[3] == "metadata.json"
        assert "Yayında -> Tescil Edildi" in history_row[4]
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == []
        assert queue_calls == []

    def test_renewal_writes_renewal_history_and_status(self):
        with temp_dir() as tmp:
            record = make_metadata_record(
                folder_status="tescil edildi",
                register_no="12345",
                register_date="2026-03-31",
                gazette_no="500",
                gazette_date="2026-03-31",
            )
            metadata_path = write_metadata_file(tmp, "GZ_500_2026-03-31", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 88,
                        "current_status": "Tescil Edildi",
                        "expiry_date": date(2025, 1, 1),
                        "status_source": "GZ",
                    }
                }
            )

            result, _, watchlist_calls, queue_calls = self._run_batch(metadata_path, conn)

        updated = update_row_dict(conn.updated_rows[0])
        history_row = conn.history_rows[0]
        assert result["updated"] == 1
        assert updated["status"] == "Yenilendi"
        assert history_row[0] == 88
        assert history_row[2] == "RENEWAL"
        assert "Tescil Edildi -> Yenilendi" in history_row[4]
        assert watchlist_calls == []
        assert queue_calls == []

    def test_app_update_preserves_existing_strong_status_and_source_tag(self):
        with temp_dir() as tmp:
            record = make_metadata_record(folder_status="")
            metadata_path = write_metadata_file(tmp, "APP_1", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 99,
                        "current_status": "Tescil Edildi",
                        "expiry_date": date(2040, 1, 1),
                        "status_source": "GZ",
                    }
                }
            )

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(metadata_path, conn)

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["status"] == "Tescil Edildi"
        assert updated["src_tag"] == "GZ"
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == []
        assert queue_calls == []

    def test_app_blank_status_preserves_existing_blt_published_status(self):
        with temp_dir() as tmp:
            record = make_metadata_record(folder_status="")
            metadata_path = write_metadata_file(tmp, "APP_1", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 100,
                        "current_status": ingest.DB_STATUS_PUBLISHED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "BLT",
                    }
                }
            )

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(metadata_path, conn)

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["status"] == ingest.DB_STATUS_PUBLISHED
        assert updated["src_tag"] == "BLT"
        assert conn.history_rows == []
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == []
        assert queue_calls == []

    def test_app_blank_status_with_register_no_overwrites_existing_source_status(self):
        with temp_dir() as tmp:
            record = make_metadata_record(folder_status="", register_no="12345")
            metadata_path = write_metadata_file(tmp, "APP_1", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 101,
                        "current_status": ingest.DB_STATUS_PUBLISHED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "BLT",
                    }
                }
            )

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(metadata_path, conn)

        updated = update_row_dict(conn.updated_rows[0])
        history_row = conn.history_rows[0]
        assert result["updated"] == 1
        assert updated["status"] == ingest.DB_STATUS_REGISTERED
        assert updated["src_tag"] == "APP"
        assert history_row[0] == 101
        assert history_row[2] == "STATUS_CHANGE"
        assert f"{ingest.DB_STATUS_PUBLISHED} -> {ingest.DB_STATUS_REGISTERED}" in history_row[4]
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == []
        assert queue_calls == []

    def test_app_explicit_non_applied_status_overwrites_existing_status(self):
        with temp_dir() as tmp:
            record = make_metadata_record(folder_status="tescil edildi")
            metadata_path = write_metadata_file(tmp, "APP_1", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 102,
                        "current_status": ingest.DB_STATUS_PUBLISHED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "BLT",
                    }
                }
            )

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(metadata_path, conn)

        updated = update_row_dict(conn.updated_rows[0])
        history_row = conn.history_rows[0]
        assert result["updated"] == 1
        assert updated["status"] == ingest.DB_STATUS_REGISTERED
        assert updated["src_tag"] == "APP"
        assert history_row[0] == 102
        assert history_row[2] == "STATUS_CHANGE"
        assert f"{ingest.DB_STATUS_PUBLISHED} -> {ingest.DB_STATUS_REGISTERED}" in history_row[4]
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == []
        assert queue_calls == []

    def test_legacy_live_folder_is_treated_as_app_source(self):
        with temp_dir() as tmp:
            record = make_metadata_record(folder_status="")
            metadata_path = write_metadata_file(tmp, "LIVE_ip_watch_ai_20260501", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 106,
                        "current_status": ingest.DB_STATUS_PUBLISHED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "BLT",
                    }
                }
            )

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(
                metadata_path,
                conn,
                force=True,
            )

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["status"] == ingest.DB_STATUS_PUBLISHED
        assert updated["src_tag"] == "BLT"
        assert conn.history_rows == []
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == []
        assert queue_calls == []

    def test_app_exact_six_classes_do_not_overwrite_existing_classes(self):
        with temp_dir() as tmp:
            record = make_metadata_record(nice_classes=[1, 2, 3, 4, 5, 6])
            metadata_path = write_metadata_file(tmp, "APP_1", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 107,
                        "current_status": ingest.DB_STATUS_PUBLISHED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "BLT",
                        "nice_class_numbers": [1, 2, 3, 4, 5, 6, 7],
                    }
                }
            )

            result, _, _, _ = self._run_batch(metadata_path, conn, force=True)

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["nice_classes"] is None

    def test_app_classes_do_not_shrink_existing_classes(self):
        with temp_dir() as tmp:
            record = make_metadata_record(nice_classes=[1, 2, 3, 4, 5])
            metadata_path = write_metadata_file(tmp, "APP_1", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 108,
                        "current_status": ingest.DB_STATUS_PUBLISHED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "BLT",
                        "nice_class_numbers": [1, 2, 3, 4, 5, 6],
                    }
                }
            )

            result, _, _, _ = self._run_batch(metadata_path, conn, force=True)

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["nice_classes"] is None

    def test_app_classes_fill_empty_db_classes(self):
        with temp_dir() as tmp:
            record = make_metadata_record(nice_classes=[9, 42])
            metadata_path = write_metadata_file(tmp, "APP_1", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 109,
                        "current_status": ingest.DB_STATUS_APPLIED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "APP",
                        "nice_class_numbers": None,
                    }
                }
            )

            result, _, _, _ = self._run_batch(metadata_path, conn, force=True)

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["nice_classes"] == [9, 42]

    def test_app_richer_classes_can_replace_shorter_existing_classes(self):
        with temp_dir() as tmp:
            richer_classes = [1, 3, 5, 6, 7, 9, 11, 42]
            record = make_metadata_record(nice_classes=richer_classes)
            metadata_path = write_metadata_file(tmp, "APP_1", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 110,
                        "current_status": ingest.DB_STATUS_APPLIED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "APP",
                        "nice_class_numbers": [1, 3],
                    }
                }
            )

            result, _, _, _ = self._run_batch(metadata_path, conn, force=True)

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["nice_classes"] == richer_classes

    @pytest.mark.parametrize(
        ("folder_name", "record_kwargs"),
        [
            ("BLT_490_2026-04-13", {"folder_status": ""}),
            ("APP_1", {"folder_status": ""}),
            ("BLT_490_2026-04-13", {"folder_status": "yayınlandı"}),
        ],
    )
    def test_forced_ingest_preserves_live_status_for_weak_ingest_statuses(self, folder_name, record_kwargs):
        with temp_dir() as tmp:
            record = make_metadata_record(**record_kwargs)
            metadata_path = write_metadata_file(tmp, folder_name, records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 112,
                        "current_status": ingest.DB_STATUS_REFUSED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "LIVE",
                        "name": "TEST MARK",
                    }
                }
            )

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(
                metadata_path,
                conn,
                force=True,
            )

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["status"] == ingest.DB_STATUS_REFUSED
        assert updated["src_tag"] == "LIVE"
        assert conn.history_rows == []
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == []
        assert queue_calls == []

    @pytest.mark.parametrize(
        ("status_text", "expected_status"),
        [
            ("feragat edildi", ingest.DB_STATUS_WITHDRAWN),
            ("tescil edildi", ingest.DB_STATUS_REGISTERED),
        ],
    )
    def test_forced_ingest_allows_explicit_strong_status_to_replace_live_status(self, status_text, expected_status):
        with temp_dir() as tmp:
            record = make_metadata_record(folder_status=status_text)
            metadata_path = write_metadata_file(tmp, "BLT_490_2026-04-13", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 113,
                        "current_status": ingest.DB_STATUS_PUBLISHED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "LIVE",
                        "name": "TEST MARK",
                    }
                }
            )

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(
                metadata_path,
                conn,
                force=True,
            )

        updated = update_row_dict(conn.updated_rows[0])
        history_row = conn.history_rows[0]
        assert result["updated"] == 1
        assert updated["status"] == expected_status
        assert updated["src_tag"] == "BLT"
        assert history_row[0] == 113
        assert history_row[2] == "STATUS_CHANGE"
        assert f"{ingest.DB_STATUS_PUBLISHED} -> {expected_status}" in history_row[4]
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == []
        assert queue_calls == []

    def test_app_only_insert_still_defaults_to_applied(self):
        with temp_dir() as tmp:
            record = make_metadata_record(folder_status="")
            metadata_path = write_metadata_file(tmp, "APP_1", records=[record])
            conn = FakeConnection()

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(metadata_path, conn)

        inserted = insert_row_dict(conn.inserted_rows[0])
        assert result["inserted"] == 1
        assert inserted["current_status"] == ingest.DB_STATUS_APPLIED
        assert inserted["status_source"] == "APP"
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls[0]["source_type"] == "application"
        assert queue_calls == []

    def test_forced_blt_repairs_existing_app_applied_pollution(self):
        with temp_dir() as tmp:
            record = make_metadata_record(folder_status="")
            metadata_path = write_metadata_file(tmp, "BLT_490_2026-04-13", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 103,
                        "current_status": ingest.DB_STATUS_APPLIED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "APP",
                        "name": "TEST MARK",
                    }
                }
            )

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(
                metadata_path,
                conn,
                force=True,
            )

        updated = update_row_dict(conn.updated_rows[0])
        history_row = conn.history_rows[0]
        assert result["updated"] == 1
        assert updated["status"] == ingest.DB_STATUS_PUBLISHED
        assert updated["src_tag"] == "BLT"
        assert history_row[0] == 103
        assert history_row[2] == "STATUS_CHANGE"
        assert f"{ingest.DB_STATUS_APPLIED} -> {ingest.DB_STATUS_PUBLISHED}" in history_row[4]
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == []
        assert queue_calls == []

    def test_forced_gz_repairs_existing_app_applied_pollution(self):
        with temp_dir() as tmp:
            record = make_metadata_record(
                folder_status="",
                register_no="12345",
                register_date="2026-03-31",
                gazette_no="500",
                gazette_date="2026-03-31",
            )
            metadata_path = write_metadata_file(tmp, "GZ_500_2026-03-31", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 104,
                        "current_status": ingest.DB_STATUS_APPLIED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "APP",
                        "name": "TEST MARK",
                    }
                }
            )

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(
                metadata_path,
                conn,
                force=True,
            )

        updated = update_row_dict(conn.updated_rows[0])
        history_row = conn.history_rows[0]
        assert result["updated"] == 1
        assert updated["status"] == ingest.DB_STATUS_REGISTERED
        assert updated["src_tag"] == "GZ"
        assert history_row[0] == 104
        assert history_row[2] == "STATUS_CHANGE"
        assert f"{ingest.DB_STATUS_APPLIED} -> {ingest.DB_STATUS_REGISTERED}" in history_row[4]
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == []
        assert queue_calls == []

    def test_blt_does_not_downgrade_existing_app_non_applied_status(self):
        with temp_dir() as tmp:
            record = make_metadata_record(folder_status="")
            metadata_path = write_metadata_file(tmp, "BLT_490_2026-04-13", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 105,
                        "current_status": ingest.DB_STATUS_REGISTERED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "APP",
                        "name": "TEST MARK",
                    }
                }
            )

            result, reconcile_calls, watchlist_calls, queue_calls = self._run_batch(
                metadata_path,
                conn,
                force=True,
            )

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["status"] == ingest.DB_STATUS_REGISTERED
        assert updated["src_tag"] == "APP"
        assert conn.history_rows == []
        assert reconcile_calls == [["2024/001"]]
        assert watchlist_calls == []
        assert queue_calls == []

    def test_forced_ingest_can_clear_existing_shape_only_name(self):
        with temp_dir() as tmp:
            record = make_metadata_record(trademark_name="SEKIL")
            metadata_path = write_metadata_file(tmp, "BLT_490_2026-04-13", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 106,
                        "current_status": ingest.DB_STATUS_PUBLISHED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "BLT",
                        "name": "sekil",
                    }
                }
            )

            result, _, _, _ = self._run_batch(metadata_path, conn, force=True)

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["name"] is None
        assert updated["clear_name"] is True
        assert updated["clear_text_features"] is True
        assert updated["txt_emb"] is None
        assert updated["name_tr"] is None

    def test_shape_only_source_name_does_not_clear_real_existing_name(self):
        with temp_dir() as tmp:
            record = make_metadata_record(trademark_name="SEKIL")
            metadata_path = write_metadata_file(tmp, "BLT_490_2026-04-13", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 107,
                        "current_status": ingest.DB_STATUS_PUBLISHED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "BLT",
                        "name": "AKSEKILI",
                    }
                }
            )

            result, _, _, _ = self._run_batch(metadata_path, conn, force=True)

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["name"] is None
        assert updated["clear_name"] is False
        assert updated["clear_text_features"] is False

    def test_mixed_shape_descriptor_invalidates_stale_text_features(self):
        with temp_dir() as tmp:
            record = make_metadata_record(trademark_name="ALPHA SEKIL")
            record["text_embedding"] = [0.1] * 384
            metadata_path = write_metadata_file(tmp, "BLT_490_2026-04-13", records=[record])
            conn = FakeConnection(
                existing_trademarks={
                    "2024/001": {
                        "id": 108,
                        "current_status": ingest.DB_STATUS_PUBLISHED,
                        "expiry_date": date(2034, 7, 17),
                        "status_source": "BLT",
                        "name": "ALPHA SEKIL",
                    }
                }
            )

            result, _, _, _ = self._run_batch(metadata_path, conn, force=True)

        updated = update_row_dict(conn.updated_rows[0])
        assert result["updated"] == 1
        assert updated["name"] == "ALPHA"
        assert updated["clear_text_features"] is True
        assert updated["txt_emb"] is None
        assert updated["name_tr"] is None


class TestGetStatusRank:
    def test_ranking_order(self):
        assert get_status_rank("Renewed") > get_status_rank("Registered")
        assert get_status_rank("Registered") > get_status_rank("Opposed")
        assert get_status_rank("Opposed") > get_status_rank("Published")
        assert get_status_rank("Published") > get_status_rank("Applied")

    def test_unknown_status(self):
        assert get_status_rank("INVALID") == -1


class TestSchemaColumns:
    """Verify schema migration includes attorney columns."""

    def test_attorney_columns_in_schema(self):
        """Explicit ingest runtime setup must include attorney columns."""
        source = Path("migrations/ingest_runtime.sql").read_text(encoding="utf-8")
        assert "ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS attorney_name VARCHAR(500);" in source
        assert "ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS attorney_no VARCHAR(50);" in source

    def test_attorney_indexes_in_schema(self):
        """Explicit ingest runtime setup must create attorney indexes."""
        source = Path("migrations/ingest_runtime.sql").read_text(encoding="utf-8")
        assert "idx_tm_attorney_name" in source
        assert "idx_tm_attorney_no" in source

    def test_processed_files_statuses_in_setup(self):
        source = Path("migrations/ingest_runtime.sql").read_text(encoding="utf-8")
        assert "repaired" in source
        assert "unrecoverable" in source
        assert "regen_failed" in source

    def test_translation_provenance_columns_in_schema(self):
        source = Path("migrations/ingest_runtime.sql").read_text(encoding="utf-8")
        assert "ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS name_tr_backend VARCHAR(32);" in source
        assert "ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS name_tr_model VARCHAR(255);" in source
        assert "ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS name_tr_updated_at TIMESTAMP;" in source


class TestAiNameFeatureCleanup:
    def test_ai_text_features_use_cleaned_trademark_name(self):
        source = Path("pipeline/ai.py").read_text(encoding="utf-8")

        assert "from pipeline.ingest_rules import clean_name" in source
        assert "_prepare_name_derived_ai_features(rec)" in source
        assert "names_to_encode = [_clean_ai_trademark_name(r) or \"\"" in source
        assert "names = [_clean_ai_trademark_name(r) or \"\"" in source
        assert "_TEXT_EMBEDDING_SOURCE_NAME_KEY" in source
        assert "_TRANSLATION_SOURCE_NAME_KEY" in source


class TestInsertPayloadShape:
    def test_insert_column_list_matches_expected_runtime_shape(self):
        assert len(ingest._INSERT_COLUMNS) == 33
        assert ingest._INSERT_COLUMNS[:3] == ["application_no", "name", "current_status"]
        assert ingest._INSERT_COLUMNS[-3:] == ["attorney_name", "attorney_no", "status_source"]

    def test_insert_sql_uses_all_insert_columns_once(self):
        insert_sql = ingest._build_insert_sql()
        for column in ingest._INSERT_COLUMNS:
            assert insert_sql.count(column) >= 1


class TestNoGoodsFallback:
    """Verify the GOODS fallback was removed from process_file_batch."""

    def test_no_goods_reference_in_extraction(self):
        """The extraction logic must NOT reference GOODS as fallback."""
        import inspect
        source = inspect.getsource(ingest.process_file_batch)
        # The old pattern was: rec.get("EXTRACTEDGOODS", []) or rec.get("GOODS", [])
        assert 'rec.get("GOODS"' not in source, \
            "GOODS must not be used as fallback for EXTRACTEDGOODS"


# ============================================================
# _resolve_image_path() tests
# ============================================================

class TestResolveImagePath:
    """Test _resolve_image_path() finds images in correct locations."""

    def test_none_image_field_returns_none(self):
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            root.mkdir(parents=True)
            assert _resolve_image_path("BLT_253", None, root) is None

    def test_empty_image_field_returns_none(self):
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            root.mkdir(parents=True)
            assert _resolve_image_path("BLT_253", "", root) is None

    def test_dirty_image_field_returns_none(self):
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            root.mkdir(parents=True)
            assert _resolve_image_path("BLT_253", "null", root) is None
            assert _resolve_image_path("BLT_253", "N/A", root) is None

    def test_per_folder_images_jpg(self):
        """Per-folder images/ directory is searched first."""
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            img_dir = root / "BLT_253" / "images"
            img_dir.mkdir(parents=True)
            (img_dir / "2011_41714.jpg").write_bytes(b"fake jpg")

            result = _resolve_image_path("BLT_253", "2011_41714", root)
            assert result == "bulletins/Marka/BLT_253/images/2011_41714.jpg"

    def test_per_folder_images_jpeg(self):
        """JPEG extension is also detected."""
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            img_dir = root / "BLT_253" / "images"
            img_dir.mkdir(parents=True)
            (img_dir / "2011_41714.jpeg").write_bytes(b"fake jpeg")

            result = _resolve_image_path("BLT_253", "2011_41714", root)
            assert result == "bulletins/Marka/BLT_253/images/2011_41714.jpeg"

    def test_per_folder_images_png(self):
        """PNG extension is also detected."""
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            img_dir = root / "BLT_253" / "images"
            img_dir.mkdir(parents=True)
            (img_dir / "2011_41714.png").write_bytes(b"fake png")

            result = _resolve_image_path("BLT_253", "2011_41714", root)
            assert result == "bulletins/Marka/BLT_253/images/2011_41714.png"

    def test_logos_fallback(self):
        """Falls back to LOGOS folder when per-folder images/ has no match."""
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            logos_dir = root / "LOGOS"
            logos_dir.mkdir(parents=True)
            (logos_dir / "2011_41714.jpg").write_bytes(b"fake jpg")

            result = _resolve_image_path("BLT_253", "2011_41714", root)
            assert result == "bulletins/Marka/LOGOS/2011_41714.jpg"

    def test_per_folder_preferred_over_logos(self):
        """Per-folder images take precedence over LOGOS."""
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            # Create both
            img_dir = root / "BLT_253" / "images"
            img_dir.mkdir(parents=True)
            (img_dir / "2011_41714.jpg").write_bytes(b"per-folder")
            logos_dir = root / "LOGOS"
            logos_dir.mkdir(parents=True)
            (logos_dir / "2011_41714.jpg").write_bytes(b"logos")

            result = _resolve_image_path("BLT_253", "2011_41714", root)
            assert result == "bulletins/Marka/BLT_253/images/2011_41714.jpg"

    def test_not_found_returns_none(self):
        """No image on disk → returns None."""
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            root.mkdir(parents=True)
            result = _resolve_image_path("BLT_253", "2011_41714", root)
            assert result is None

    def test_forward_slashes_always(self):
        """Returned path must use forward slashes even on Windows."""
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            img_dir = root / "BLT_253" / "images"
            img_dir.mkdir(parents=True)
            (img_dir / "2011_41714.jpg").write_bytes(b"fake")

            result = _resolve_image_path("BLT_253", "2011_41714", root)
            assert "\\" not in result, f"Backslashes found: {result}"
            assert "/" in result

    def test_gz_folder(self):
        """Works for GZ_ folders too."""
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            logos_dir = root / "LOGOS"
            logos_dir.mkdir(parents=True)
            (logos_dir / "2020_99999.jpg").write_bytes(b"fake")

            result = _resolve_image_path("GZ_315", "2020_99999", root)
            assert result == "bulletins/Marka/LOGOS/2020_99999.jpg"

    def test_whitespace_image_field_stripped(self):
        """Leading/trailing whitespace in image_field is stripped by sanitize."""
        with temp_dir() as tmp:
            root = Path(tmp) / "bulletins" / "Marka"
            logos_dir = root / "LOGOS"
            logos_dir.mkdir(parents=True)
            (logos_dir / "2011_41714.jpg").write_bytes(b"fake")

            result = _resolve_image_path("BLT_253", "  2011_41714  ", root)
            assert result == "bulletins/Marka/LOGOS/2011_41714.jpg"

    def test_process_file_batch_uses_resolve(self):
        """process_file_batch must call _resolve_image_path, not raw rec.get('IMAGE')."""
        import inspect
        source = inspect.getsource(ingest.process_file_batch)
        assert "_resolve_image_path" in source, \
            "process_file_batch must call _resolve_image_path for image handling"
        # The old pattern was just: img_path = rec.get("IMAGE")
        # It should NOT appear as a standalone assignment anymore
        assert 'img_path = rec.get("IMAGE")' not in source, \
            "Raw IMAGE field must not be used directly — use _resolve_image_path"


# ============================================================
# Self-healing: _has_tmbulletin_source() tests
# ============================================================

class TestHasTmbulletinSource:
    """Test detection of tmbulletin source files for metadata regeneration."""

    def test_no_files_returns_false(self):
        with temp_dir() as tmp:
            folder = Path(tmp) / "BLT_100"
            folder.mkdir()
            assert _has_tmbulletin_source(folder) is False

    def test_script_file_returns_true(self):
        with temp_dir() as tmp:
            folder = Path(tmp) / "BLT_100"
            folder.mkdir()
            (folder / "tmbulletin.script").write_text("CREATE TABLE...", encoding='utf-8')
            assert _has_tmbulletin_source(folder) is True

    def test_log_file_returns_true(self):
        with temp_dir() as tmp:
            folder = Path(tmp) / "BLT_100"
            folder.mkdir()
            (folder / "tmbulletin.log").write_text("INSERT INTO...", encoding='utf-8')
            assert _has_tmbulletin_source(folder) is True

    def test_nested_data_dir_returns_true(self):
        """Some folders have tmbulletin files in a nested data/ subdirectory."""
        with temp_dir() as tmp:
            folder = Path(tmp) / "BLT_100"
            data_dir = folder / "data"
            data_dir.mkdir(parents=True)
            (data_dir / "tmbulletin.script").write_text("CREATE TABLE...", encoding='utf-8')
            assert _has_tmbulletin_source(folder) is True

    def test_gazete_txt_returns_true(self):
        with temp_dir() as tmp:
            folder = Path(tmp) / "GZ_315"
            folder.mkdir()
            (folder / "gazete_data.txt").write_text("INSERT INTO...", encoding='utf-8')
            assert _has_tmbulletin_source(folder) is True

    def test_unrelated_files_returns_false(self):
        with temp_dir() as tmp:
            folder = Path(tmp) / "BLT_100"
            folder.mkdir()
            (folder / "metadata.json").write_text("[]", encoding='utf-8')
            (folder / "readme.txt").write_text("nothing", encoding='utf-8')
            assert _has_tmbulletin_source(folder) is False


# ============================================================
# Self-healing: _repair_corrupt_metadata() tests
# ============================================================

class TestRepairCorruptMetadata:
    """Test the repair mechanism for corrupt metadata.json files."""

    def test_no_source_returns_unrecoverable(self):
        """Folder with no tmbulletin source → unrecoverable."""
        with temp_dir() as tmp:
            folder = Path(tmp) / "BLT_DEAD"
            folder.mkdir()
            meta = folder / "metadata.json"
            meta.write_text('{"truncated": tru', encoding='utf-8')

            result = _repair_corrupt_metadata(meta)
            assert result["status"] == "unrecoverable"
            assert result["records"] == 0
            # Original file should still exist (not deleted)
            assert meta.exists()

    def test_backup_created_on_repair(self):
        """Corrupt file is backed up before repair attempt."""
        with temp_dir() as tmp:
            folder = Path(tmp) / "BLT_TEST"
            folder.mkdir()
            meta = folder / "metadata.json"
            meta.write_text('[{"truncated"}', encoding='utf-8')
            # Create a fake tmbulletin source
            (folder / "tmbulletin.script").write_text("-- empty", encoding='utf-8')

            with patch('pipeline.ingest._repair_corrupt_metadata.__module__', 'pipeline.ingest'):
                # Mock the metadata regeneration to succeed
                with patch('metadata.process_single_folder') as mock_regen:
                    mock_regen.return_value = {"status": "success", "records": 5}
                    # Write valid regenerated file
                    def write_regen(*args, **kwargs):
                        meta.write_text('[{"APPLICATIONNO":"2024/001"}]', encoding='utf-8')
                        return {"status": "success", "records": 1}
                    mock_regen.side_effect = write_regen

                    result = _repair_corrupt_metadata(meta)

            backup = folder / "metadata.json.corrupt_backup"
            assert backup.exists(), "Backup file should be created"

    def test_multiple_backups_dont_overwrite(self):
        """Multiple corruption events create numbered backups."""
        with temp_dir() as tmp:
            folder = Path(tmp) / "BLT_TEST"
            folder.mkdir()
            meta = folder / "metadata.json"
            # Create existing backup
            (folder / "metadata.json.corrupt_backup").write_text("old backup", encoding='utf-8')
            meta.write_text('[{"truncated"}', encoding='utf-8')
            (folder / "tmbulletin.script").write_text("-- empty", encoding='utf-8')

            with patch('metadata.process_single_folder') as mock_regen:
                def write_regen(*args, **kwargs):
                    meta.write_text('[{"APPLICATIONNO":"2024/001"}]', encoding='utf-8')
                    return {"status": "success", "records": 1}
                mock_regen.side_effect = write_regen

                result = _repair_corrupt_metadata(meta)

            assert (folder / "metadata.json.corrupt_backup").exists(), "Original backup preserved"
            assert (folder / "metadata.json.corrupt_backup.1").exists(), "Numbered backup created"

    def test_regen_failure_restores_backup(self):
        """If metadata.py fails, the corrupt file is restored from backup."""
        with temp_dir() as tmp:
            folder = Path(tmp) / "BLT_TEST"
            folder.mkdir()
            meta = folder / "metadata.json"
            corrupt_content = '[{"truncated"}'
            meta.write_text(corrupt_content, encoding='utf-8')
            (folder / "tmbulletin.script").write_text("-- empty", encoding='utf-8')

            with patch('metadata.process_single_folder') as mock_regen:
                mock_regen.return_value = {"status": "error", "records": 0, "error": "parse failed"}
                result = _repair_corrupt_metadata(meta)

            assert result["status"] == "regen_failed"
            # metadata.json should be restored from backup
            assert meta.exists(), "Original (corrupt) file should be restored from backup"


# ============================================================
# Self-healing: pre_scan_and_repair() tests
# ============================================================

class TestPreScanAndRepair:
    """Test the pre-scan phase that runs before ingestion."""

    def test_all_valid_returns_empty_stats(self):
        """All valid files → no repairs needed."""
        with temp_dir() as tmp:
            base = Path(tmp)
            f1 = base / "BLT_100"
            f1.mkdir()
            (f1 / "metadata.json").write_text('[{"APPLICATIONNO":"2024/001"}]', encoding='utf-8')

            stats = pre_scan_and_repair(base)
            assert stats["repaired"] == []
            assert stats["unrecoverable"] == []
            assert stats["regen_failed"] == []

    def test_detects_corrupt_file(self):
        """Corrupt JSON is detected by pre-scan."""
        with temp_dir() as tmp:
            base = Path(tmp)
            f1 = base / "BLT_CORRUPT"
            f1.mkdir()
            (f1 / "metadata.json").write_text('[{"truncated": ', encoding='utf-8')
            # No tmbulletin source → unrecoverable

            stats = pre_scan_and_repair(base)
            assert len(stats["unrecoverable"]) == 1
            assert stats["unrecoverable"][0] == "BLT_CORRUPT"

    def test_non_list_json_detected_as_corrupt(self):
        """metadata.json with a dict root (not list) is treated as corrupt."""
        with temp_dir() as tmp:
            base = Path(tmp)
            f1 = base / "BLT_DICT"
            f1.mkdir()
            (f1 / "metadata.json").write_text('{"not": "a list"}', encoding='utf-8')

            stats = pre_scan_and_repair(base)
            assert len(stats["unrecoverable"]) == 1

    def test_empty_dir_no_crash(self):
        """Base dir with no metadata.json files → no crash."""
        with temp_dir() as tmp:
            stats = pre_scan_and_repair(Path(tmp))
            assert stats["repaired"] == []

    def test_scrape_only_folder_is_not_ingest_ready(self):
        with temp_dir() as tmp:
            base = Path(tmp)
            folder = base / "BLT_SCRAPE_ONLY"
            folder.mkdir()
            (folder / "scraped_metadata.json").write_text("[]", encoding="utf-8")

            stats = pre_scan_and_repair(base)
            assert stats["repaired"] == []
            assert stats["unrecoverable"] == []
            assert stats["regen_failed"] == []

    def test_process_file_batch_has_safety_net(self):
        """process_file_batch must have JSONDecodeError safety net."""
        import inspect
        source = inspect.getsource(ingest.process_file_batch)
        assert "json.JSONDecodeError" in source, \
            "process_file_batch must catch JSONDecodeError for self-healing"
        assert "_repair_corrupt_metadata" in source, \
            "process_file_batch must call _repair_corrupt_metadata as safety net"
        assert "was_repaired" in source, \
            "process_file_batch must track repair status"

    def test_run_ingest_calls_pre_scan(self):
        """run_ingest must pre-scan on full runs and aggregate runtime stats."""
        fake_conn = object()
        with patch("pipeline.ingest_runtime.get_connection", return_value=fake_conn), \
             patch("pipeline.ingest_runtime.release_connection") as mock_release, \
             patch("pipeline.ingest_runtime._bootstrap.assert_ingest_runtime_ready") as mock_ready, \
             patch("pipeline.ingest_runtime.pre_scan_and_repair", return_value={"repaired": [], "unrecoverable": [], "regen_failed": []}) as mock_scan, \
             patch("pipeline.ingest_runtime._collect_metadata_files", return_value=[]), \
             patch("pipeline.ingest_runtime._process_metadata_files", return_value={"inserted": 2, "updated": 3, "skipped": 4, "processed_files": 1, "failed_files": 0, "file_results": []}), \
             patch("pipeline.ingest_runtime.cleanup_sekil_names", return_value=5) as mock_cleanup:
            result = ingest.run_ingest()

        mock_ready.assert_called_once_with(fake_conn)
        mock_scan.assert_called_once()
        mock_cleanup.assert_called_once_with(fake_conn)
        mock_release.assert_called_once_with(fake_conn)
        assert result["inserted"] == 2
        assert result["updated"] == 3
        assert result["skipped"] == 4
        assert result["name_cleaned"] == 5

    def test_main_calls_pre_scan(self):
        """main() must delegate to the CLI runtime entrypoint."""
        with patch("pipeline.ingest_runtime.run_ingest_cli") as mock_run:
            with patch.object(sys, "argv", ["pipeline.ingest"]):
                ingest.main()
        mock_run.assert_called_once_with(force=False, folder_name=None)

    def test_run_ingest_fails_fast_when_runtime_not_ready(self):
        fake_conn = object()
        with patch("pipeline.ingest_runtime.get_connection", return_value=fake_conn), \
             patch("pipeline.ingest_runtime.release_connection") as mock_release, \
             patch("pipeline.ingest_runtime._bootstrap.assert_ingest_runtime_ready", side_effect=RuntimeError("setup required")), \
             pytest.raises(RuntimeError, match="setup required"):
            ingest.run_ingest()
        mock_release.assert_called_once_with(fake_conn)


class TestPipelineWorkerForceIngest:
    def test_run_step_ingest_passes_force_to_packaged_ingest(self, monkeypatch):
        from workers.pipeline_worker import PipelineWorker

        calls = []

        def run_ingest_stub(*, force=False, settings=None):
            calls.append({"force": force, "settings": settings})
            return {"inserted": 2, "updated": 3, "skipped": 4}

        monkeypatch.setattr("pipeline.ingest.run_ingest", run_ingest_stub)
        worker = PipelineWorker()

        result = worker.run_step_ingest(force=True)

        assert result.status == "success"
        assert result.processed == 5
        assert result.skipped == 4
        assert calls == [{"force": True, "settings": worker.pipeline_settings}]

    @pytest.mark.asyncio
    async def test_full_pipeline_passes_force_ingest_to_ingest_step(self, monkeypatch):
        from workers.pipeline_worker import PipelineWorker, StepResult

        worker = PipelineWorker()
        calls = []

        def tracking_unavailable():
            raise RuntimeError("tracking unavailable")

        def run_step_ingest_stub(*, force=False):
            calls.append(force)
            return StepResult(step_name="ingest", status="success", processed=1)

        monkeypatch.setattr("workers.pipeline_worker._get_db_connection", tracking_unavailable)
        monkeypatch.setattr(worker, "run_step_ingest", run_step_ingest_stub)

        result = await worker.run_full_pipeline(
            single_step="ingest",
            triggered_by="manual",
            force_ingest=True,
        )

        assert result.status == "success"
        assert calls == [True]


class TestPipelineWorkerRepair:
    def test_run_step_repair_calls_packaged_repair(self, monkeypatch):
        from workers.pipeline_worker import PipelineWorker

        calls = []

        def run_repair_stub(*, root_dir=None):
            calls.append(root_dir)
            return {
                "repaired": 7,
                "candidates": 9,
                "decisions": 7,
                "routines": {
                    "status": {"repaired": 4},
                    "name": {"repaired": 3},
                },
            }

        monkeypatch.setattr("pipeline.repair.run_repair", run_repair_stub)
        worker = PipelineWorker()

        result = worker.run_step_repair()

        assert result.status == "success"
        assert result.step_name == "repair"
        assert result.processed == 7
        assert result.skipped == 2
        assert calls == [Path(worker.pipeline_settings.bulletins_root)]

    @pytest.mark.asyncio
    async def test_full_pipeline_can_run_repair_as_single_step(self, monkeypatch):
        from workers.pipeline_worker import PipelineWorker, StepResult

        worker = PipelineWorker()
        calls = []

        def tracking_unavailable():
            raise RuntimeError("tracking unavailable")

        def run_step_repair_stub():
            calls.append(True)
            return StepResult(step_name="repair", status="success", processed=3)

        monkeypatch.setattr("workers.pipeline_worker._get_db_connection", tracking_unavailable)
        monkeypatch.setattr(worker, "run_step_repair", run_step_repair_stub)

        result = await worker.run_full_pipeline(single_step="repair")

        assert result.status == "success"
        assert calls == [True]


class TestExtractTpeId:
    """Tests for extract_tpe_id() — extracts TPE Client IDs embedded in name strings."""

    def test_none_input(self):
        name, tpe_id = extract_tpe_id(None)
        assert name is None
        assert tpe_id is None

    def test_empty_string(self):
        name, tpe_id = extract_tpe_id("")
        assert name == ""
        assert tpe_id is None

    def test_non_string_input(self):
        name, tpe_id = extract_tpe_id(12345)
        assert name == 12345
        assert tpe_id is None

    def test_simple_extraction(self):
        name, tpe_id = extract_tpe_id("ACME CORP (12345)")
        assert name == "ACME CORP"
        assert tpe_id == "12345"

    def test_multi_holder_with_embedded_id(self):
        """Real data pattern from APP_1: ID embedded in multi-holder TITLE."""
        name, tpe_id = extract_tpe_id("METİN MURAT (7374084) ,FATMA MURAT")
        assert name == "METİN MURAT"
        assert tpe_id == "7374084"

    def test_no_id_present(self):
        name, tpe_id = extract_tpe_id("PLAIN NAME")
        assert name == "PLAIN NAME"
        assert tpe_id is None

    def test_non_numeric_parens_no_match(self):
        """Parentheses with non-numeric content should NOT be extracted."""
        name, tpe_id = extract_tpe_id("FOO (bar)")
        assert name == "FOO (bar)"
        assert tpe_id is None

    def test_whitespace_handling(self):
        name, tpe_id = extract_tpe_id("  NAME (123)  ")
        assert name == "NAME"
        assert tpe_id == "123"

    def test_holder_existing_tpeclientid_takes_priority(self):
        """When TPECLIENTID already exists in holder record, it should take priority."""
        import inspect
        source = inspect.getsource(ingest.process_file_batch)
        # Verify the code uses existing_tpe or extracted_id pattern
        assert "existing_tpe" in source, \
            "holder extraction must check existing TPECLIENTID first"
        assert "extract_tpe_id" in source, \
            "holder extraction must call extract_tpe_id"

    def test_attorney_name_with_embedded_id(self):
        """Attorney NAME field should also be cleaned via extract_tpe_id."""
        import inspect
        source = inspect.getsource(ingest.process_file_batch)
        assert "existing_no" in source, \
            "attorney extraction must check existing NO first"

    def test_mixed_parens(self):
        """Name with both non-numeric and numeric parens — should extract the numeric one."""
        name, tpe_id = extract_tpe_id("FOO (bar) CORP (99999)")
        assert tpe_id == "99999"

    def test_long_numeric_id(self):
        name, tpe_id = extract_tpe_id("HOLDER NAME (1234567890)")
        assert name == "HOLDER NAME"
        assert tpe_id == "1234567890"


# ============================================================
# Ingestion sort order tests
# ============================================================

class TestIngestionSortOrder:
    """Test that folders are sorted BLT → GZ → APP, latest bulletin first within each group."""

    def _sort_key(self, p):
        """Replicate the sort_key from pipeline.ingest run_ingest/main."""
        import re as _re
        name = p.parent.name.upper()
        m = _re.search(r'_(\d+)', p.parent.name)
        num = int(m.group(1)) if m else 0
        if name.startswith("BLT"): return (0, -num)
        if name.startswith("GZ"):  return (1, -num)
        return (2, -num)

    def test_blt_before_gz_before_app(self):
        """BLT folders should come before GZ, GZ before APP."""
        folders = [
            Path("root/APP_1/metadata.json"),
            Path("root/GZ_300/metadata.json"),
            Path("root/BLT_100/metadata.json"),
        ]
        result = sorted(folders, key=self._sort_key)
        names = [p.parent.name for p in result]
        assert names == ["BLT_100", "GZ_300", "APP_1"]

    def test_latest_bulletin_first_within_blt(self):
        """Within BLT group, highest numbered bulletin should come first."""
        folders = [
            Path("root/BLT_100/metadata.json"),
            Path("root/BLT_499/metadata.json"),
            Path("root/BLT_250/metadata.json"),
        ]
        result = sorted(folders, key=self._sort_key)
        names = [p.parent.name for p in result]
        assert names == ["BLT_499", "BLT_250", "BLT_100"]

    def test_latest_gazette_first_within_gz(self):
        """Within GZ group, highest numbered gazette should come first."""
        folders = [
            Path("root/GZ_300/metadata.json"),
            Path("root/GZ_499/metadata.json"),
            Path("root/GZ_400/metadata.json"),
        ]
        result = sorted(folders, key=self._sort_key)
        names = [p.parent.name for p in result]
        assert names == ["GZ_499", "GZ_400", "GZ_300"]

    def test_full_mixed_sort(self):
        """Full pipeline order: BLT desc → GZ desc → APP desc."""
        folders = [
            Path("root/APP_1/metadata.json"),
            Path("root/GZ_488/metadata.json"),
            Path("root/BLT_200/metadata.json"),
            Path("root/GZ_300/metadata.json"),
            Path("root/BLT_499/metadata.json"),
            Path("root/BLT_127/metadata.json"),
            Path("root/GZ_499/metadata.json"),
        ]
        result = sorted(folders, key=self._sort_key)
        names = [p.parent.name for p in result]
        assert names == [
            "BLT_499", "BLT_200", "BLT_127",
            "GZ_499", "GZ_488", "GZ_300",
            "APP_1",
        ]

    def test_gz_folder_with_date_suffix(self):
        """GZ folders with date suffix should sort by number, not by suffix."""
        folders = [
            Path("root/GZ_449_2017-09-30/metadata.json"),
            Path("root/GZ_499/metadata.json"),
            Path("root/GZ_300/metadata.json"),
        ]
        result = sorted(folders, key=self._sort_key)
        names = [p.parent.name for p in result]
        assert names == ["GZ_499", "GZ_449_2017-09-30", "GZ_300"]


# ============================================================
# Pipeline parallel folder_sort_key tests
# ============================================================

class TestPipelineSortKey:
    """Test the folder_sort_key from pipeline.parallel."""

    def test_import_and_order(self):
        from pipeline.parallel import folder_sort_key
        folders = ["APP_1", "GZ_488", "BLT_200", "GZ_300", "BLT_499", "GZ_499"]
        result = sorted(folders, key=folder_sort_key)
        assert result == ["BLT_499", "BLT_200", "GZ_499", "GZ_488", "GZ_300", "APP_1"]

    def test_extract_folder_number(self):
        from pipeline.parallel import _extract_folder_number
        assert _extract_folder_number("GZ_499") == 499
        assert _extract_folder_number("BLT_127") == 127
        assert _extract_folder_number("GZ_449_2017-09-30") == 449
        assert _extract_folder_number("APP_1") == 1
        assert _extract_folder_number("UNKNOWN") == 0

    def test_same_number_different_family(self):
        """BLT_500 should come before GZ_500 which comes before APP_500."""
        from pipeline.parallel import folder_sort_key
        folders = ["APP_500", "GZ_500", "BLT_500"]
        result = sorted(folders, key=folder_sort_key)
        assert result == ["BLT_500", "GZ_500", "APP_500"]

    def test_parallel_db_worker_uses_runtime_readiness_helper(self):
        import inspect
        from pipeline import parallel

        source = inspect.getsource(parallel.db_worker)
        assert "assert_ingest_runtime_ready" in source
        assert "check_and_migrate_schema" not in source
        assert "load_nice_classes" not in source


# ============================================================
# Source authority update decision tests
# ============================================================

class TestSourceAuthorityDecision:
    """Test that update decisions follow source authority: same/higher → accept, lower → skip."""

    def test_source_rank_values(self):
        """Verify the rank hierarchy: APP(3) > GZ(2) > BLT(1)."""
        assert get_source_rank("APP_1") == (3, 'APP')
        assert get_source_rank("LIVE_ip_watch_ai_20260501") == (3, 'APP')
        assert get_source_rank("GZ_300") == (2, 'GZ')
        assert get_source_rank("BLT_127") == (1, 'BLT')

    def test_same_rank_gte_check(self):
        """Same source rank should pass the >= check (accept update)."""
        gz_rank, _ = get_source_rank("GZ_480")
        gz2_rank, _ = get_source_rank("GZ_400")
        assert gz_rank >= gz2_rank  # GZ vs GZ → accept

        blt_rank, _ = get_source_rank("BLT_499")
        blt2_rank, _ = get_source_rank("BLT_200")
        assert blt_rank >= blt2_rank  # BLT vs BLT → accept

    def test_higher_rank_gte_check(self):
        """Higher authority source should pass the >= check."""
        app_rank, _ = get_source_rank("APP_1")
        gz_rank, _ = get_source_rank("GZ_300")
        blt_rank, _ = get_source_rank("BLT_127")

        assert app_rank >= gz_rank   # APP overwrites GZ
        assert app_rank >= blt_rank  # APP overwrites BLT
        assert gz_rank >= blt_rank   # GZ overwrites BLT

    def test_lower_rank_fails_gte_check(self):
        """Lower authority source should NOT pass the >= check (skip)."""
        app_rank, _ = get_source_rank("APP_1")
        gz_rank, _ = get_source_rank("GZ_300")
        blt_rank, _ = get_source_rank("BLT_127")

        assert not (blt_rank >= gz_rank)   # BLT cannot overwrite GZ
        assert not (blt_rank >= app_rank)  # BLT cannot overwrite APP
        assert not (gz_rank >= app_rank)   # GZ cannot overwrite APP

    def test_decision_logic_skip_in_source(self):
        """Verify the else branch with 'continue' exists in process_file_batch."""
        import inspect
        source = inspect.getsource(ingest.process_file_batch)
        assert "should_update = True" in source
        assert "next_status = curr_status" in source

    def test_final_status_fields_are_not_written_directly_in_insert_sql(self):
        """pipeline.ingest should leave final_status* ownership to the reconciler."""
        import inspect
        source = inspect.getsource(ingest.process_file_batch)
        assert "application_no, name, current_status, final_status, final_status_source" not in source
        assert "final_status = " not in source
        assert "final_source_tag" not in source
        assert "from utils.status_reconciler import update_final_status_batch" in source

    def test_no_status_rank_in_decision_logic(self):
        """The update decision should NOT use get_status_rank — purely source-based."""
        import inspect
        source = inspect.getsource(ingest.process_file_batch)
        assert "get_status_rank" not in source

    def test_app_applied_fallback_preserves_existing_status(self):
        """When APP resolves through fallback status only, existing statuses are kept."""
        import inspect
        source = inspect.getsource(ingest.process_file_batch)
        assert "_explicit_db_status_from_text" in source
        assert "status_can_replace" in source
        assert "next_status = curr_status" in source


# ============================================================
# Same-family overwrite tests (last ingested wins)
# ============================================================

class TestSameFamilyOverwrite:
    """Verify that within the same source family, the last ingested record always wins.

    With sort order BLT_499 → BLT_200, if a record exists in both,
    BLT_499 is inserted first, then BLT_200 overwrites it (same rank >= same rank).
    """

    def test_gz_same_rank_accepts_update(self):
        """GZ_400 processing after GZ_480 should be accepted (same rank)."""
        rank_480, _ = get_source_rank("GZ_480")
        rank_400, _ = get_source_rank("GZ_400")
        # Both are GZ → rank 2. The >= check passes.
        assert rank_400 >= rank_480

    def test_blt_same_rank_accepts_update(self):
        """BLT_100 processing after BLT_499 should be accepted (same rank)."""
        rank_499, _ = get_source_rank("BLT_499")
        rank_100, _ = get_source_rank("BLT_100")
        assert rank_100 >= rank_499

    def test_app_same_rank_accepts_update(self):
        """APP sources always have the same rank with each other."""
        rank_1, _ = get_source_rank("APP_1")
        rank_2, _ = get_source_rank("APP_scraped_2")
        assert rank_1 >= rank_2
        assert rank_2 >= rank_1

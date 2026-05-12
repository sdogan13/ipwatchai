from pipeline import repair
from pipeline.ingest_rules import (
    DB_STATUS_PUBLISHED,
    DB_STATUS_REGISTERED,
    DB_STATUS_REFUSED,
)


def _write_metadata(root, folder_name, records):
    folder = root / folder_name
    folder.mkdir(parents=True)
    path = folder / "metadata.json"
    path.write_text(__import__("json").dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _metadata_record(app_no, classes=None, raw=None):
    trademark = {}
    if raw is not None:
        trademark["NICECLASSES_RAW"] = raw
    if classes is not None:
        trademark["NICECLASSES_LIST"] = [str(value) for value in classes]
    return {"APPLICATIONNO": app_no, "TRADEMARK": trademark}


class DummyConnection:
    def __init__(self):
        self.commits = 0

    def cursor(self, *args, **kwargs):
        return object()

    def commit(self):
        self.commits += 1


class CaptureCursor:
    def __init__(self):
        self.sql = ""
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return []

    def fetchone(self):
        return {}


class CaptureConnection:
    def __init__(self):
        self.last_cursor = None

    def cursor(self, *args, **kwargs):
        self.last_cursor = CaptureCursor()
        return self.last_cursor


def test_name_repair_removes_exact_sekil_word(monkeypatch):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_name_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2026/001",
                "name": "alpha sekil beta",
            }
        ],
    )

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_name_repair(conn=conn)

    assert summary["repaired"] == 1
    assert summary["samples"][0]["to"] == "alpha beta"
    assert updates == [("11111111-1111-1111-1111-111111111111", "alpha beta", True)]
    assert conn.commits == 1


def test_name_repair_removes_terminal_attached_sekil_suffix(monkeypatch):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_name_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2026/002",
                "name": "cansigortaşekil",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "application_no": "2026/003",
                "name": "g81SEKIL",
            },
        ],
    )

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_name_repair(conn=conn)

    assert summary["repaired"] == 2
    assert summary["samples"] == [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "application_no": "2026/002",
            "from": "cansigortaşekil",
            "to": "cansigorta",
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "application_no": "2026/003",
            "from": "g81SEKIL",
            "to": "g81",
        },
    ]
    assert updates == [
        ("11111111-1111-1111-1111-111111111111", "cansigorta", True),
        ("22222222-2222-2222-2222-222222222222", "g81", True),
    ]
    assert conn.commits == 1


def test_name_repair_preserves_embedded_sekil_words(monkeypatch):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_name_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2026/004",
                "name": "sekili kristal kaya tuzu",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "application_no": "2026/005",
                "name": "geleceği şekillendir",
            },
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "application_no": "2026/006",
                "name": "otosekil.com oto aksesuar",
            },
        ],
    )

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_name_repair(conn=conn)

    assert summary["repaired"] == 0
    assert summary["decisions"] == 0
    assert updates == []
    assert conn.commits == 0


def test_name_tr_repair_clears_shape_only_translation(monkeypatch):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_name_tr_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2026/007",
                "name_tr": "sekil",
            }
        ],
    )

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_name_tr_repair(conn=conn)

    assert summary["repaired"] == 1
    assert summary["samples"][0]["to"] is None
    assert updates == [("11111111-1111-1111-1111-111111111111", None, True)]
    assert conn.commits == 1


def test_name_tr_repair_removes_plus_sekil_and_preserves_embedded_terms(monkeypatch):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_name_tr_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2026/008",
                "name_tr": "+cafesebastian+sekil",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "application_no": "2026/009",
                "name_tr": "sekili kristal kaya tuzu",
            },
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "application_no": "2026/010",
                "name_tr": "otosekil.com oto aksesuar",
            },
        ],
    )

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_name_tr_repair(conn=conn)

    assert summary["repaired"] == 1
    assert summary["samples"] == [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "application_no": "2026/008",
            "from": "+cafesebastian+sekil",
            "to": "+cafesebastian",
        }
    ]
    assert updates == [("11111111-1111-1111-1111-111111111111", "+cafesebastian", True)]
    assert conn.commits == 1


def test_name_tr_repair_removes_empty_parens_after_shape_descriptor(monkeypatch):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_name_tr_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2026/011",
                "name_tr": "bitkisel çayı (şekil)",
            }
        ],
    )

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_name_tr_repair(conn=conn)

    assert summary["repaired"] == 1
    assert summary["samples"][0]["to"] == "bitkisel çayı"
    assert updates == [("11111111-1111-1111-1111-111111111111", "bitkisel çayı", True)]


def test_name_tr_repair_does_not_trim_separator_for_embedded_sekilde(monkeypatch):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_name_tr_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2026/012",
                "name_tr": "dramatik bir şekilde farklı +",
            }
        ],
    )

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_name_tr_repair(conn=conn)

    assert summary["repaired"] == 0
    assert updates == []
    assert conn.commits == 0


def test_name_tr_repair_clears_translation_features_when_original_name_is_real(monkeypatch):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_name_tr_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2026/013",
                "name": "real brand",
                "name_tr": "sekil",
            }
        ],
    )

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_name_tr_repair(conn=conn)

    assert summary["repaired"] == 1
    assert updates == [("11111111-1111-1111-1111-111111111111", None, True)]
    assert summary["text_embeddings_cleared"] == 0


def test_logo_only_text_feature_repair_clears_stale_name_derived_features(monkeypatch):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_logo_only_text_feature_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2026/014",
            }
        ],
    )

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)
        assert "text_embedding" not in sql
        assert "logo_ocr_text" not in sql
        assert "image_embedding" not in sql

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_logo_only_text_feature_repair(conn=conn)

    assert summary["repaired"] == 1
    assert summary["text_embeddings_cleared"] == 0
    assert updates == [("11111111-1111-1111-1111-111111111111",)]
    assert conn.commits == 1


def test_class_repair_updates_exact_six_from_richer_blt_metadata(monkeypatch, tmp_path):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_class_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2025/001",
                "nice_class_numbers": [1, 2, 3, 4, 5, 6],
                "bulletin_no": "490",
                "gazette_no": None,
            }
        ],
    )
    _write_metadata(tmp_path, "BLT_490_2026-04-13", [_metadata_record("2025/001", [1, 2, 3, 4, 5, 6, 7])])

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_class_repair(conn=conn, root_dir=tmp_path)

    assert summary["repaired"] == 1
    assert summary["metadata_matches"] == 1
    assert summary["samples"][0]["to"] == [1, 2, 3, 4, 5, 6, 7]
    assert updates == [("11111111-1111-1111-1111-111111111111", [1, 2, 3, 4, 5, 6, 7])]
    assert conn.commits == 1


def test_class_repair_prefers_gz_over_richer_blt_metadata(monkeypatch, tmp_path):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_class_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2025/002",
                "nice_class_numbers": [1, 2, 3, 4, 5, 6],
                "bulletin_no": "490",
                "gazette_no": "500",
            }
        ],
    )
    _write_metadata(tmp_path, "BLT_490_2026-04-13", [_metadata_record("2025/002", [1, 2, 3, 4, 5, 6, 7, 8])])
    _write_metadata(tmp_path, "GZ_500_2026-03-31", [_metadata_record("2025/002", [3, 5, 35, 41, 42, 44, 45])])

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_class_repair(conn=conn, root_dir=tmp_path)

    assert summary["repaired"] == 1
    assert summary["samples"][0]["source"] == "GZ"
    assert summary["samples"][0]["to"] == [3, 5, 35, 41, 42, 44, 45]
    assert updates == [("11111111-1111-1111-1111-111111111111", [3, 5, 35, 41, 42, 44, 45])]


def test_class_repair_ignores_app_metadata(monkeypatch, tmp_path):
    conn = DummyConnection()

    monkeypatch.setattr(
        repair,
        "_class_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2025/003",
                "nice_class_numbers": [1, 2, 3, 4, 5, 6],
                "bulletin_no": "490",
                "gazette_no": None,
            }
        ],
    )
    _write_metadata(tmp_path, "APP_490", [_metadata_record("2025/003", [1, 2, 3, 4, 5, 6, 7])])

    summary = repair.run_class_repair(conn=conn, root_dir=tmp_path)

    assert summary["source_files_scanned"] == 0
    assert summary["decisions"] == 0
    assert summary["missing_source_rows"] == 1
    assert conn.commits == 0


def test_class_repair_skips_metadata_with_six_or_fewer_valid_classes(monkeypatch, tmp_path):
    conn = DummyConnection()

    monkeypatch.setattr(
        repair,
        "_class_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2025/004",
                "nice_class_numbers": [1, 2, 3, 4, 5, 6],
                "bulletin_no": "490",
                "gazette_no": None,
            }
        ],
    )
    _write_metadata(tmp_path, "BLT_490_2026-04-13", [_metadata_record("2025/004", [1, 2, 3, 4, 5, 6])])

    summary = repair.run_class_repair(conn=conn, root_dir=tmp_path)

    assert summary["metadata_matches"] == 1
    assert summary["decisions"] == 0
    assert conn.commits == 0


def test_class_repair_excludes_invalid_class_numbers(monkeypatch, tmp_path):
    conn = DummyConnection()

    monkeypatch.setattr(
        repair,
        "_class_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2025/005",
                "nice_class_numbers": [1, 2, 3, 4, 5, 6],
                "bulletin_no": "490",
                "gazette_no": None,
            }
        ],
    )
    _write_metadata(tmp_path, "BLT_490_2026-04-13", [_metadata_record("2025/005", [0, 1, 2, 3, 4, 5, 6, 98, 99])])

    summary = repair.run_class_repair(conn=conn, root_dir=tmp_path)

    assert summary["metadata_matches"] == 1
    assert summary["decisions"] == 0
    assert conn.commits == 0


def test_class_repair_dry_run_does_not_write(monkeypatch, tmp_path):
    conn = DummyConnection()
    updates = []

    monkeypatch.setattr(
        repair,
        "_class_repair_candidates",
        lambda conn, app_no=None, limit=None: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2025/006",
                "nice_class_numbers": [1, 2, 3, 4, 5, 6],
                "bulletin_no": "490",
                "gazette_no": None,
            }
        ],
    )
    _write_metadata(tmp_path, "BLT_490_2026-04-13", [_metadata_record("2025/006", None, "01 / 02 / 03 / 04 / 05 / 06 / 07")])

    def fake_execute_values(cur, sql, rows):
        updates.extend(rows)

    monkeypatch.setattr(repair, "execute_values", fake_execute_values)

    summary = repair.run_class_repair(conn=conn, root_dir=tmp_path, dry_run=True)

    assert summary["repaired"] == 0
    assert summary["would_repair"] == 1
    assert summary["samples"][0]["to"] == [1, 2, 3, 4, 5, 6, 7]
    assert updates == []
    assert conn.commits == 0


def test_live_detail_nice_classes_parser_uses_nice_siniflari_label():
    text = """
    Marka Bilgileri
    Başvuru Numarası
    2026/019871
    Nice Sınıfları
    09 / 42 / 45 /
    Türü
    Ticaret-Hizmet
    Mal ve Hizmet Bilgileri
    Sınıf
    99
    """

    assert repair._parse_live_detail_nice_classes(text) == [9, 42, 45]


def test_live_detail_nice_classes_parser_ignores_search_grid_header():
    text = """
    Marka Araştırma
    # Başvuru Numarası Marka Adı Durumu Nice Sınıfları Şekil İşlem
    1
    2018/115109 systemair 06 / 11 / 35 / 37 / 41 / 42 / DETAY
    Marka Bilgileri
    Başvuru Numarası 2018/115109 Başvuru Tarihi 17.12.2018
    Nice Sınıfları 06 / 11 / 35 / 37 / 41 / 42 / Türü Ticaret-Hizmet
    Marka Adı systemair
    Mal ve Hizmet Bilgileri
    Sınıf Mal ve Hizmetler
    06 goods
    """

    assert repair._parse_live_detail_nice_classes(text) == [6, 11, 35, 37, 41, 42]


def test_live_detail_nice_classes_parser_excludes_invalid_values():
    text = "Nice Sınıfları\n00 / 01 / 06 / 45 / 98 / 99 /\nTürü"

    assert repair._parse_live_detail_nice_classes(text) == [1, 6, 45]


def test_resolve_live_status_uses_explicit_non_published_status_only():
    assert repair._resolve_live_status("tescil edildi") == DB_STATUS_REGISTERED
    assert repair._resolve_live_status("", "2021 160894") == DB_STATUS_REGISTERED
    assert repair._resolve_live_status(None, "2021 160894") == DB_STATUS_REGISTERED
    assert repair._resolve_live_status("marka başvurusu/tescili geçersiz sayıldı") == DB_STATUS_REFUSED
    assert repair._resolve_live_status("yayınlandı") is None
    assert repair._resolve_live_status("Başvuruldu") is None
    assert repair._resolve_live_status("") is None
    assert repair._resolve_live_status("", "-") is None


def test_live_status_candidates_use_four_month_threshold_priority_window_and_newest_order():
    conn = CaptureConnection()

    repair._live_status_candidates(conn, limit=10)

    sql = conn.last_cursor.sql
    assert "INTERVAL '4 months'" in sql
    assert "INTERVAL '1 year'" not in sql
    assert "INTERVAL '5 months'" not in sql
    assert sql.count("tm.application_date >= CURRENT_DATE - INTERVAL '11 years'") == 2
    assert "chk.live_registration_no IS NOT NULL" in sql
    assert "tm.bulletin_date DESC NULLS LAST" in sql
    assert "tm.application_date DESC NULLS LAST" in sql
    assert "tm.id DESC" in sql
    assert conn.last_cursor.params[:3] == [
        DB_STATUS_PUBLISHED,
        DB_STATUS_REFUSED,
        repair.LIVE_PROVISIONAL_SOURCE,
    ]
    assert "tm.status_source = %s" in sql
    assert "no_decision" not in conn.last_cursor.params[3]


def test_live_status_candidates_can_include_older_priority_records():
    conn = CaptureConnection()

    repair._live_status_candidates(conn, limit=10, include_older_than_11_years=True)

    sql = conn.last_cursor.sql
    assert "INTERVAL '4 months'" in sql
    assert sql.count("tm.application_date >= CURRENT_DATE - INTERVAL '11 years'") == 1


def test_live_status_provisional_mark_uses_same_pending_filter(monkeypatch):
    conn = CaptureConnection()
    monkeypatch.setattr(repair, "_ensure_live_check_table", lambda conn: None)
    monkeypatch.setattr(repair, "_ensure_live_provisional_table", lambda conn: None)

    summary = repair.run_live_status_provisional_refusal_mark(conn=conn, dry_run=True, limit=5)

    sql = conn.last_cursor.sql
    assert summary["dry_run"] is True
    assert summary["source"] == repair.LIVE_PROVISIONAL_SOURCE
    assert "tm.current_status = %s::tm_status" in sql
    assert "tm.bulletin_date < CURRENT_DATE - INTERVAL '1 year'" in sql
    assert "tm.bulletin_date IS NULL" not in sql
    assert "repair_live_trademark_checks" in sql
    assert "chk.live_registration_no IS NOT NULL" in sql
    assert "tm.application_date >= CURRENT_DATE - INTERVAL '11 years'" in sql
    assert conn.last_cursor.params[0] == DB_STATUS_PUBLISHED
    assert "no_decision" not in conn.last_cursor.params[1]


def test_fetch_live_status_evidence_uses_grid_only():
    calls = []

    class FakeScraper:
        def fetch_live_grid_evidence(self, application_no, name, **kwargs):
            calls.append((application_no, name, kwargs))
            return {
                "matched": True,
                "status_text": "",
                "registration_no": "2021 160894",
                "detail_opened": False,
            }

        def fetch_live_detail_evidence(self, *args, **kwargs):
            raise AssertionError("status repair should not open DETAY")

    evidence = repair._fetch_live_status_evidence(
        {"application_no": "2021/160894", "name": "ip"},
        scraper=FakeScraper(),
    )

    assert evidence["registration_no"] == "2021 160894"
    assert evidence["detail_opened"] is False
    assert calls[0][0:2] == ("2021/160894", "ip")


def test_live_class_candidates_default_to_priority_window():
    conn = CaptureConnection()

    repair._live_class_candidates(conn, limit=10)

    sql = conn.last_cursor.sql
    assert sql.count("tm.application_date >= CURRENT_DATE - INTERVAL '11 years'") == 2
    assert "cardinality(tm.nice_class_numbers) = 6" in sql
    assert "tm.application_date DESC NULLS LAST" in sql
    assert "tm.id DESC" in sql


def test_live_status_repair_updates_published_row_from_live_status(monkeypatch):
    conn = DummyConnection()
    applied = []
    progress = []
    reconciled = []

    monkeypatch.setattr(repair, "_ensure_live_check_table", lambda conn: None)
    monkeypatch.setattr(
        repair,
        "_live_status_candidates",
        lambda conn, app_no=None, limit=None, **kwargs: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2020/001",
                "name": "alpha",
                "current_status": DB_STATUS_PUBLISHED,
            }
        ],
    )
    monkeypatch.setattr(
        repair,
        "_live_status_skip_counts",
        lambda conn, app_no=None, **kwargs: {"skipped_no_name": 0, "skipped_recent": 0, "skipped_older_than_11_years": 0},
    )
    monkeypatch.setattr(repair, "_apply_live_status_decisions", lambda conn, decisions: applied.extend(decisions))
    monkeypatch.setattr(repair, "_upsert_live_check_rows", lambda conn, rows: progress.extend(rows))
    monkeypatch.setattr(
        "utils.status_reconciler.update_final_status_batch",
        lambda conn, app_nos: reconciled.extend(app_nos),
    )

    summary = repair.run_live_status_repair(
        conn=conn,
        live_fetcher=lambda candidate: {
            "matched": True,
            "status_text": "tescil edildi",
            "nice_classes": [1, 2, 3, 4, 5, 6],
            "artifact_dir": "artifacts/live/2020_001",
        },
    )

    assert summary["repaired"] == 1
    assert applied[0]["to"] == DB_STATUS_REGISTERED
    assert progress[0][4] == "updated"
    assert reconciled == ["2020/001"]
    assert conn.commits == 1


def test_live_status_repair_uses_registration_no_when_status_text_blank(monkeypatch):
    conn = DummyConnection()
    applied = []
    progress = []

    monkeypatch.setattr(repair, "_ensure_live_check_table", lambda conn: None)
    monkeypatch.setattr(
        repair,
        "_live_status_candidates",
        lambda conn, app_no=None, limit=None, **kwargs: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2021/160894",
                "name": "ip",
                "current_status": DB_STATUS_PUBLISHED,
            }
        ],
    )
    monkeypatch.setattr(
        repair,
        "_live_status_skip_counts",
        lambda conn, app_no=None, **kwargs: {"skipped_no_name": 0, "skipped_recent": 0, "skipped_older_than_11_years": 0},
    )
    monkeypatch.setattr(repair, "_apply_live_status_decisions", lambda conn, decisions: applied.extend(decisions))
    monkeypatch.setattr(repair, "_upsert_live_check_rows", lambda conn, rows: progress.extend(rows))

    summary = repair.run_live_status_repair(
        conn=conn,
        live_fetcher=lambda candidate: {
            "matched": True,
            "status_text": "",
            "registration_no": "2021 160894",
            "nice_classes": [9, 42],
            "artifact_dir": "artifacts/live/2021_160894",
        },
    )

    assert summary["repaired"] == 1
    assert summary["no_decision"] == 0
    assert applied[0]["to"] == DB_STATUS_REGISTERED
    assert applied[0]["registration_no"] == "2021 160894"
    assert progress[0][4] == "updated"
    assert progress[0][6] == "2021 160894"


def test_live_status_repair_does_not_downgrade_to_applied(monkeypatch):
    conn = DummyConnection()
    applied = []
    progress = []

    monkeypatch.setattr(repair, "_ensure_live_check_table", lambda conn: None)
    monkeypatch.setattr(
        repair,
        "_live_status_candidates",
        lambda conn, app_no=None, limit=None, **kwargs: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2020/002",
                "name": "beta",
                "current_status": DB_STATUS_PUBLISHED,
            }
        ],
    )
    monkeypatch.setattr(
        repair,
        "_live_status_skip_counts",
        lambda conn, app_no=None, **kwargs: {"skipped_no_name": 0, "skipped_recent": 0, "skipped_older_than_11_years": 0},
    )
    monkeypatch.setattr(repair, "_apply_live_status_decisions", lambda conn, decisions: applied.extend(decisions))
    monkeypatch.setattr(repair, "_upsert_live_check_rows", lambda conn, rows: progress.extend(rows))

    summary = repair.run_live_status_repair(
        conn=conn,
        live_fetcher=lambda candidate: {"matched": True, "status_text": "Başvuruldu", "nice_classes": []},
    )

    assert summary["repaired"] == 0
    assert applied == []
    assert progress[0][4] == "no_decision"


def test_live_status_repair_reports_recent_and_no_name_skips(monkeypatch):
    conn = DummyConnection()

    monkeypatch.setattr(repair, "_ensure_live_check_table", lambda conn: None)
    monkeypatch.setattr(repair, "_live_status_candidates", lambda conn, app_no=None, limit=None, **kwargs: [])
    monkeypatch.setattr(
        repair,
        "_live_status_skip_counts",
        lambda conn, app_no=None, **kwargs: {"skipped_no_name": 3, "skipped_recent": 5, "skipped_older_than_11_years": 7},
    )

    summary = repair.run_live_status_repair(conn=conn, live_fetcher=lambda candidate: {})

    assert summary["skipped_no_name"] == 3
    assert summary["skipped_recent"] == 5
    assert summary["skipped_older_than_11_years"] == 7
    assert summary["checked"] == 0


def test_live_class_repair_updates_exact_six_from_live_detail(monkeypatch):
    conn = DummyConnection()
    applied = []
    progress = []

    monkeypatch.setattr(repair, "_ensure_live_check_table", lambda conn: None)
    monkeypatch.setattr(
        repair,
        "_live_class_candidates",
        lambda conn, app_no=None, limit=None, **kwargs: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2020/003",
                "name": "gamma",
                "nice_class_numbers": [1, 2, 3, 4, 5, 6],
            }
        ],
    )
    monkeypatch.setattr(repair, "_apply_live_class_decisions", lambda conn, decisions: applied.extend(decisions))
    monkeypatch.setattr(repair, "_upsert_live_check_rows", lambda conn, rows: progress.extend(rows))

    summary = repair.run_live_class_repair(
        conn=conn,
        live_fetcher=lambda candidate: {
            "matched": True,
            "status_text": "Yayında",
            "nice_classes": [1, 2, 3, 4, 5, 6, 7],
            "artifact_dir": "artifacts/live/2020_003",
        },
    )

    assert summary["repaired"] == 1
    assert applied[0]["to"] == [1, 2, 3, 4, 5, 6, 7]
    assert progress[0][4] == "updated"
    assert conn.commits == 1


def test_live_class_repair_dry_run_does_not_write(monkeypatch):
    conn = DummyConnection()
    applied = []
    progress = []

    monkeypatch.setattr(repair, "_ensure_live_check_table", lambda conn: None)
    monkeypatch.setattr(
        repair,
        "_live_class_candidates",
        lambda conn, app_no=None, limit=None, **kwargs: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2020/004",
                "name": "delta",
                "nice_class_numbers": [1, 2, 3, 4, 5, 6],
            }
        ],
    )
    monkeypatch.setattr(repair, "_apply_live_class_decisions", lambda conn, decisions: applied.extend(decisions))
    monkeypatch.setattr(repair, "_upsert_live_check_rows", lambda conn, rows: progress.extend(rows))

    summary = repair.run_live_class_repair(
        conn=conn,
        dry_run=True,
        live_fetcher=lambda candidate: {"matched": True, "nice_classes": [1, 2, 3, 4, 5, 6, 7]},
    )

    assert summary["repaired"] == 0
    assert summary["would_repair"] == 1
    assert applied == []
    assert progress == []
    assert conn.commits == 0


def test_live_status_repair_stops_batch_on_safety_stop(monkeypatch):
    conn = DummyConnection()
    applied = []
    progress = []

    monkeypatch.setattr(repair, "_ensure_live_check_table", lambda conn: None)
    monkeypatch.setattr(
        repair,
        "_live_status_candidates",
        lambda conn, app_no=None, limit=None, **kwargs: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2020/005",
                "name": "epsilon",
                "current_status": DB_STATUS_PUBLISHED,
            }
        ],
    )
    monkeypatch.setattr(
        repair,
        "_live_status_skip_counts",
        lambda conn, app_no=None, **kwargs: {"skipped_no_name": 0, "skipped_recent": 0, "skipped_older_than_11_years": 0},
    )
    monkeypatch.setattr(repair, "_apply_live_status_decisions", lambda conn, decisions: applied.extend(decisions))
    monkeypatch.setattr(repair, "_upsert_live_check_rows", lambda conn, rows: progress.extend(rows))

    summary = repair.run_live_status_repair(
        conn=conn,
        live_fetcher=lambda candidate: {
            "safety_stop": True,
            "safety_reason": "safety_rate_limited",
            "artifact_error": "safety_rate_limited",
            "next_allowed_at": "2026-04-30T12:00:00+00:00",
        },
    )

    assert summary["checked"] == 0
    assert summary["safety_stopped"] is True
    assert summary["safety_reason"] == "safety_rate_limited"
    assert summary["next_allowed_at"] == "2026-04-30T12:00:00+00:00"
    assert summary["repaired"] == 0
    assert applied == []
    assert progress[0][4] == "safety_stop"


def test_live_class_repair_stops_batch_on_safety_stop(monkeypatch):
    conn = DummyConnection()
    applied = []
    progress = []

    monkeypatch.setattr(repair, "_ensure_live_check_table", lambda conn: None)
    monkeypatch.setattr(
        repair,
        "_live_class_candidates",
        lambda conn, app_no=None, limit=None, **kwargs: [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "application_no": "2020/006",
                "name": "zeta",
                "nice_class_numbers": [1, 2, 3, 4, 5, 6],
            }
        ],
    )
    monkeypatch.setattr(repair, "_apply_live_class_decisions", lambda conn, decisions: applied.extend(decisions))
    monkeypatch.setattr(repair, "_upsert_live_check_rows", lambda conn, rows: progress.extend(rows))

    summary = repair.run_live_class_repair(
        conn=conn,
        live_fetcher=lambda candidate: {
            "safety_stop": True,
            "safety_reason": "safety_blocked",
            "artifact_error": "safety_blocked",
            "next_allowed_at": "2026-05-01T12:00:00+00:00",
        },
    )

    assert summary["checked"] == 0
    assert summary["safety_stopped"] is True
    assert summary["safety_reason"] == "safety_blocked"
    assert summary["repaired"] == 0
    assert applied == []
    assert progress[0][4] == "safety_stop"


def test_run_repair_aggregates_status_and_name_routines(monkeypatch):
    conn = DummyConnection()

    monkeypatch.setattr(
        repair,
        "run_status_repair",
        lambda **kwargs: {
            "repaired": 2,
            "would_repair": 0,
            "candidates": 3,
            "decisions": 2,
        },
    )
    monkeypatch.setattr(
        repair,
        "run_name_repair",
        lambda **kwargs: {
            "repaired": 4,
            "would_repair": 0,
            "candidates": 5,
            "decisions": 4,
        },
    )
    monkeypatch.setattr(
        repair,
        "run_name_tr_repair",
        lambda **kwargs: {
            "repaired": 6,
            "would_repair": 0,
            "candidates": 7,
            "decisions": 6,
        },
    )
    monkeypatch.setattr(
        repair,
        "run_logo_only_text_feature_repair",
        lambda **kwargs: {
            "repaired": 0,
            "would_repair": 0,
            "candidates": 0,
            "decisions": 0,
        },
    )
    monkeypatch.setattr(
        repair,
        "run_class_repair",
        lambda **kwargs: {
            "repaired": 8,
            "would_repair": 0,
            "candidates": 9,
            "decisions": 8,
        },
    )
    monkeypatch.setattr(
        repair,
        "run_live_status_repair",
        lambda **kwargs: {
            "repaired": 8,
            "would_repair": 0,
            "candidates": 9,
            "decisions": 8,
        },
    )
    monkeypatch.setattr(
        repair,
        "run_live_class_repair",
        lambda **kwargs: {
            "repaired": 10,
            "would_repair": 0,
            "candidates": 11,
            "decisions": 10,
        },
    )

    summary = repair.run_repair(conn=conn, dry_run=False)

    assert summary["repaired"] == 38
    assert summary["candidates"] == 44
    assert summary["decisions"] == 38
    assert summary["routines"]["status"]["repaired"] == 2
    assert summary["routines"]["name"]["repaired"] == 4
    assert summary["routines"]["name_tr"]["repaired"] == 6
    assert summary["routines"]["logo_only_text_features"]["repaired"] == 0
    assert summary["routines"]["classes"]["repaired"] == 8
    assert summary["routines"]["live_status"]["repaired"] == 8
    assert summary["routines"]["live_classes"]["repaired"] == 10

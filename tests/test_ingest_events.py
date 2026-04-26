import sys
import uuid

import pytest


sys.modules.pop("ingest_events", None)

import ingest_events


def _make_event(**overrides):
    event = {
        "application_no": "2024/001",
        "registration_no": "123456",
        "event_type": "transfer",
        "event_subtype": "holder_change",
        "source_type": "GZ",
        "gazette_no": "500",
        "gazette_date": "2026-03-31",
        "page_number": 100,
        "old_value": "OLD HOLDER",
        "new_value": "NEW HOLDER",
        "details": {"trademark_name": "ACME"},
        "raw_text": "TRANSFER RAW TEXT",
    }
    event.update(overrides)
    return event


class TestPrepareEventRows:
    def test_keeps_events_distinct_by_full_payload(self):
        events = [
            _make_event(page_number=100, details={"trademark_name": "ACME"}),
            _make_event(page_number=101, details={"trademark_name": "ACME", "case_no": "2026/1"}),
        ]

        rows, stats, app_nos = ingest_events.prepare_event_rows(
            events,
            {"2024/001": "11111111-1111-1111-1111-111111111111"},
        )

        assert len(rows) == 2
        assert stats["prepared"] == 2
        assert stats["deduped"] == 0
        assert app_nos == {"2024/001"}
        assert rows[0][-1] != rows[1][-1], "full-payload fingerprint must distinguish rows"

    def test_dedupes_exact_duplicate_events(self):
        event = _make_event()

        rows, stats, app_nos = ingest_events.prepare_event_rows(
            [event, dict(event)],
            {"2024/001": "11111111-1111-1111-1111-111111111111"},
        )

        assert len(rows) == 1
        assert stats["prepared"] == 1
        assert stats["deduped"] == 1
        assert app_nos == {"2024/001"}

    def test_keeps_unknown_application_numbers_for_storage(self):
        rows, stats, app_nos = ingest_events.prepare_event_rows(
            [_make_event(application_no="UNKNOWN", registration_no=None)],
            {},
        )

        assert len(rows) == 1
        assert stats["skipped_invalid"] == 0
        assert app_nos == {"UNKNOWN"}
        assert rows[0][1] == "UNKNOWN"
        assert rows[0][0] is None

    def test_strips_nul_characters_from_event_strings(self):
        rows, stats, _ = ingest_events.prepare_event_rows(
            [
                _make_event(
                    raw_text="TRANSFER\x00 RAW\x00 TEXT",
                    old_value="OLD\x00 HOLDER",
                    details={"trademark_name": "AC\x00ME"},
                )
            ],
            {"2024/001": "11111111-1111-1111-1111-111111111111"},
        )

        assert len(rows) == 1
        assert stats["skipped_invalid"] == 0
        assert rows[0][9] == "OLD HOLDER"
        assert rows[0][12] == "TRANSFER RAW TEXT"
        assert rows[0][11].adapted["trademark_name"] == "ACME"


class TestScopeExtraction:
    def test_extracts_scope_from_top_level_fields_when_events_are_empty(self):
        data = {
            "status": "success",
            "source_type": "BLT",
            "bulletin_no": "298",
            "bulletin_date": "2018-04-12",
            "events": [],
            "total": 0,
        }

        assert ingest_events._extract_scope_from_data("BLT_298_2018-04-12", data) == ("BLT", "298")


class FakeCursor:
    def __init__(self, trademark_rows=None, event_rows=None):
        self.trademark_rows = trademark_rows or []
        self.event_rows = event_rows or []
        self.current_rows = []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "FROM trademarks" in sql and "WHERE application_no IN" in sql:
            self.current_rows = self.trademark_rows
        elif "FROM trademark_events" in sql and "ORDER BY bulletin_date" in sql:
            self.current_rows = self.event_rows
        else:
            self.current_rows = []
            self.rowcount = 0

    def fetchall(self):
        return list(self.current_rows)

    def close(self):
        return None


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commit_count = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commit_count += 1


class TestMaterializeAll:
    def test_resets_requested_trademark_when_it_has_no_remaining_events(self, monkeypatch):
        trademark_id = str(uuid.uuid4())
        cursor = FakeCursor(trademark_rows=[("2024/001", trademark_id)], event_rows=[])
        conn = FakeConn(cursor)
        flushed_batches = []

        monkeypatch.setattr(
            ingest_events,
            "_flush_materialize_batch",
            lambda cur, batch: flushed_batches.extend(batch),
        )

        stats = ingest_events.materialize_all(conn, app_nos=["2024/001"], dry_run=False)

        assert stats["trademarks_processed"] == 1
        assert stats["trademarks_reset"] == 1
        assert len(flushed_batches) == 1
        update_row = flushed_batches[0]
        assert update_row[0] is None
        assert update_row[1] == 0
        assert update_row[2] is None
        assert update_row[8].adapted == {}
        assert update_row[9] == 0
        assert update_row[10] == trademark_id
        assert conn.commit_count == 1


class TestRecomputeFinalStatus:
    def test_large_app_sets_stay_scoped_and_chunked(self, monkeypatch):
        calls = []

        def fake_update_final_status_batch(conn, app_nos=None):
            calls.append(app_nos)
            return len(app_nos or [])

        monkeypatch.setattr(
            "utils.status_reconciler.update_final_status_batch",
            fake_update_final_status_batch,
        )

        app_nos = [f"2026/{idx:06d}" for idx in range(25001)]

        ingest_events._recompute_final_status(object(), app_nos)

        assert len(calls) == 3
        assert [len(call) for call in calls] == [10000, 10000, 5001]
        assert all(call is not None for call in calls)
        assert calls[0][0] == "2026/000000"
        assert calls[-1][-1] == "2026/025000"


class TestRunEventIngest:
    def test_accumulates_folder_failures_without_aborting(self, tmp_path, monkeypatch):
        good_folder = tmp_path / "BLT_100_2026-01-01"
        bad_folder = tmp_path / "GZ_200_2026-01-02"
        good_folder.mkdir()
        bad_folder.mkdir()
        (good_folder / "events.json").write_text('{"status":"success","events":[]}', encoding="utf-8")
        (bad_folder / "events.json").write_text('{"status":"success","events":[]}', encoding="utf-8")

        materialize_calls = []
        final_status_calls = []

        monkeypatch.setattr(ingest_events, "ensure_event_ingest_schema", lambda: None)

        def fake_process_folder(folder, conn, dry_run=False):
            if folder.name == bad_folder.name:
                raise RuntimeError("bad scope")
            return {
                "folder": folder.name,
                "status": "success",
                "resolved": 1,
                "unresolved": 0,
                "prepared": {"prepared": 3, "deduped": 1, "skipped_invalid": 0},
                "insert": {"deleted": 2, "inserted": 3, "skipped": 1, "errors": 0},
                "materialize_app_nos": ["2024/001"],
            }

        def fake_materialize_all(conn, app_nos=None, dry_run=False):
            materialize_calls.append(list(app_nos or []))
            return {"trademarks_processed": len(app_nos or []), "trademarks_reset": 0}

        monkeypatch.setattr(ingest_events, "process_folder", fake_process_folder)
        monkeypatch.setattr(ingest_events, "materialize_all", fake_materialize_all)
        monkeypatch.setattr(
            ingest_events,
            "_recompute_final_status",
            lambda conn, app_nos: final_status_calls.append(list(app_nos or [])),
        )

        summary = ingest_events.run_event_ingest(
            root_dir=tmp_path,
            conn=object(),
            run_alerts=False,
        )

        assert summary["status"] == "partial"
        assert summary["processed"] == 1
        assert summary["failed"] == 1
        assert summary["skipped"] == 0
        assert "bad scope" in summary["error"]
        assert materialize_calls == [["2024/001"]]
        assert final_status_calls == [["2024/001"]]
        assert summary["materialize"]["trademarks_processed"] == 1

    def test_alert_failure_returns_partial_without_losing_materialization(self, tmp_path, monkeypatch):
        folder = tmp_path / "BLT_101_2026-01-03"
        folder.mkdir()
        (folder / "events.json").write_text('{"status":"success","events":[]}', encoding="utf-8")

        final_status_calls = []

        monkeypatch.setattr(ingest_events, "ensure_event_ingest_schema", lambda: None)
        monkeypatch.setattr(
            ingest_events,
            "process_folder",
            lambda folder, conn, dry_run=False: {
                "folder": folder.name,
                "status": "success",
                "resolved": 2,
                "unresolved": 0,
                "prepared": {"prepared": 4, "deduped": 0, "skipped_invalid": 0},
                "insert": {"deleted": 1, "inserted": 4, "skipped": 0, "errors": 0},
                "materialize_app_nos": ["2024/002", "2024/003"],
            },
        )
        monkeypatch.setattr(
            ingest_events,
            "materialize_all",
            lambda conn, app_nos=None, dry_run=False: {
                "trademarks_processed": len(app_nos or []),
                "effective_status_set": len(app_nos or []),
            },
        )
        monkeypatch.setattr(
            ingest_events,
            "_recompute_final_status",
            lambda conn, app_nos: final_status_calls.append(list(app_nos or [])),
        )
        monkeypatch.setattr(
            "watchlist.scanner.scan_events_for_watchlist",
            lambda conn: (_ for _ in ()).throw(RuntimeError("alerts unavailable")),
        )

        summary = ingest_events.run_event_ingest(
            root_dir=tmp_path,
            conn=object(),
        )

        assert summary["status"] == "partial"
        assert summary["processed"] == 1
        assert summary["failed"] == 0
        assert summary["alert_error"] == "alerts unavailable"
        assert summary["materialize"]["trademarks_processed"] == 2
        assert final_status_calls == [["2024/002", "2024/003"]]

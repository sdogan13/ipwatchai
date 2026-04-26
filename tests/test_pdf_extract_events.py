import json
import subprocess
import sys
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pdf_extract_events


def test_find_unprocessed_event_targets_detects_folder_and_top_level_repair(tmp_path):
    folder_target = tmp_path / "BLT_490_2026-04-13"
    folder_target.mkdir()
    (folder_target / "bulletin.pdf").write_bytes(b"%PDF-folder")

    repair_folder = tmp_path / "GZ_500_2026-04-13"
    repair_folder.mkdir()
    (repair_folder / "metadata.json").write_text("[]", encoding="utf-8")
    repair_pdf = tmp_path / "GZ_500_2026-04-13.pdf"
    repair_pdf.write_bytes(b"%PDF-top-level")

    completed = tmp_path / "BLT_491_2026-04-27"
    completed.mkdir()
    (completed / "bulletin.pdf").write_bytes(b"%PDF-complete")
    (completed / "events.json").write_text("{}", encoding="utf-8")

    targets = pdf_extract_events.find_unprocessed_event_targets(tmp_path)

    assert [
        (target["kind"], target["folder"].name, target["source_type"], target["bulletin_no"], target["bulletin_date"])
        for target in targets
    ] == [
        ("folder", "BLT_490_2026-04-13", "BLT", "490", "2026-04-13"),
        ("top_level_pdf", "GZ_500_2026-04-13", "GZ", "500", "2026-04-13"),
    ]


def test_find_unprocessed_event_targets_force_includes_completed_targets(tmp_path):
    completed_folder = tmp_path / "BLT_490_2026-04-13"
    completed_folder.mkdir()
    (completed_folder / "bulletin.pdf").write_bytes(b"%PDF-folder")
    (completed_folder / "events.json").write_text("{}", encoding="utf-8")

    repair_folder = tmp_path / "GZ_500_2026-04-13"
    repair_folder.mkdir()
    (repair_folder / "metadata.json").write_text("[]", encoding="utf-8")
    (repair_folder / "events.json").write_text("{}", encoding="utf-8")
    repair_pdf = tmp_path / "GZ_500_2026-04-13.pdf"
    repair_pdf.write_bytes(b"%PDF-top-level")

    targets = pdf_extract_events.find_unprocessed_event_targets(tmp_path, force=True)

    assert [
        (target["kind"], target["folder"].name, target["source_type"], target["bulletin_no"], target["bulletin_date"])
        for target in targets
    ] == [
        ("folder", "BLT_490_2026-04-13", "BLT", "490", "2026-04-13"),
        ("top_level_pdf", "GZ_500_2026-04-13", "GZ", "500", "2026-04-13"),
    ]


def test_run_event_extraction_writes_events_json_for_folder_targets(tmp_path, monkeypatch):
    folder = tmp_path / "BLT_490_2026-04-13"
    folder.mkdir()
    (folder / "bulletin.pdf").write_bytes(b"%PDF-folder")

    def fake_extract_events_from_folder(target_folder, source_type, bulletin_no, bulletin_date):
        assert target_folder == folder
        assert source_type == "BLT"
        assert bulletin_no == "490"
        assert bulletin_date == "2026-04-13"
        return {
            "status": "success",
            "source_type": "BLT",
            "bulletin_no": "490",
            "bulletin_date": "2026-04-13",
            "events": [{"event_type": "withdrawal"}],
            "stats": {"withdrawal": 1},
            "total": 1,
            "errors": [],
        }

    monkeypatch.setattr(pdf_extract_events, "extract_events_from_folder", fake_extract_events_from_folder)

    summary = pdf_extract_events.run_event_extraction(root_dir=tmp_path)

    assert summary["processed"] == 1
    assert summary["failed"] == 0
    assert summary["total_events"] == 1
    assert json.loads((folder / "events.json").read_text(encoding="utf-8"))["total"] == 1


def test_run_event_extraction_force_rewrites_existing_events_json(tmp_path, monkeypatch):
    folder = tmp_path / "BLT_490_2026-04-13"
    folder.mkdir()
    (folder / "bulletin.pdf").write_bytes(b"%PDF-folder")
    (folder / "events.json").write_text('{"status":"success","total":99}', encoding="utf-8")

    def fake_extract_events_from_folder(target_folder, source_type, bulletin_no, bulletin_date):
        assert target_folder == folder
        assert source_type == "BLT"
        assert bulletin_no == "490"
        assert bulletin_date == "2026-04-13"
        return {
            "status": "success",
            "source_type": "BLT",
            "bulletin_no": "490",
            "bulletin_date": "2026-04-13",
            "events": [{"event_type": "withdrawal"}, {"event_type": "seizure_lift"}],
            "stats": {"withdrawal": 1, "seizure_lift": 1},
            "total": 2,
            "errors": [],
        }

    monkeypatch.setattr(pdf_extract_events, "extract_events_from_folder", fake_extract_events_from_folder)

    summary = pdf_extract_events.run_event_extraction(root_dir=tmp_path, force=True)

    assert summary["processed"] == 1
    assert summary["failed"] == 0
    assert summary["total_events"] == 2
    assert json.loads((folder / "events.json").read_text(encoding="utf-8"))["total"] == 2


def test_parse_simple_records_uses_default_page_for_first_block():
    text = "(210) 2024/000001 (566) FIRST MARK <<PAGE:11>> (210) 2024/000002 (566) SECOND MARK"

    events = pdf_extract_events.parse_simple_records(
        text,
        "withdrawal",
        "BLT",
        "490",
        "2026-04-13",
        default_page_no=10,
    )

    assert [event["page_number"] for event in events] == [10, 11]


def test_parse_renewal_list_tracks_page_markers():
    text = "\n".join(
        [
            "<<PAGE:50>>",
            "2005 42226",
            "30/09/2015",
            "MARK A",
            "<<PAGE:51>>",
            "2005 42227",
            "01/10/2015",
            "MARK B",
        ]
    )

    events = pdf_extract_events.parse_renewal_list(
        text,
        "GZ",
        "500",
        "2026-03-31",
        default_page_no=49,
    )

    assert [event["page_number"] for event in events] == [50, 51]


def test_materialize_pdf_source_extracts_zip_wrapped_pdf(tmp_path):
    archive_path = tmp_path / "bulletin.pdf"
    inner_name = "wrapped.pdf"

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(inner_name, b"%PDF-1.7 test payload")

    with pdf_extract_events._materialize_pdf_source(archive_path) as resolved_path:
        assert resolved_path.name == inner_name
        assert resolved_path.read_bytes().startswith(b"%PDF")


def test_materialize_pdf_source_extracts_rar_wrapped_pdf_via_7zip(tmp_path, monkeypatch):
    archive_path = tmp_path / "bulletin.pdf"
    archive_path.write_bytes(b"Rar!\x1a\x07\x00\xcf\x90")

    monkeypatch.setattr(pdf_extract_events, "_get_seven_zip_executable", lambda: "fake7z")

    def fake_run(command, capture_output, text, check):
        out_dir = None
        for part in command:
            if isinstance(part, str) and part.startswith("-o"):
                out_dir = Path(part[2:])
                break
        assert out_dir is not None
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "wrapped.pdf").write_bytes(b"%PDF-1.7 extracted payload")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(pdf_extract_events.subprocess, "run", fake_run)

    with pdf_extract_events._materialize_pdf_source(archive_path) as resolved_path:
        assert resolved_path.name == "wrapped.pdf"
        assert resolved_path.read_bytes().startswith(b"%PDF")


def test_run_event_extraction_repairs_top_level_pdf_targets(tmp_path, monkeypatch):
    folder = tmp_path / "GZ_500_2026-04-13"
    folder.mkdir()
    (folder / "metadata.json").write_text("[]", encoding="utf-8")
    top_level_pdf = tmp_path / "GZ_500_2026-04-13.pdf"
    top_level_pdf.write_bytes(b"%PDF-top-level")

    def fake_extract_events_from_folder(target_folder, source_type, bulletin_no, bulletin_date):
        assert target_folder == folder
        assert (target_folder / "bulletin.pdf").exists()
        assert source_type == "GZ"
        assert bulletin_no == "500"
        assert bulletin_date == "2026-04-13"
        return {
            "status": "success",
            "source_type": "GZ",
            "gazette_no": "500",
            "gazette_date": "2026-04-13",
            "events": [{"event_type": "renewal"}],
            "stats": {"renewal": 1},
            "total": 1,
            "errors": [],
        }

    monkeypatch.setattr(pdf_extract_events, "extract_events_from_folder", fake_extract_events_from_folder)

    summary = pdf_extract_events.run_event_extraction(root_dir=tmp_path)

    assert summary["processed"] == 1
    assert summary["failed"] == 0
    assert not top_level_pdf.exists()
    assert (folder / "bulletin.pdf").exists()
    assert json.loads((folder / "events.json").read_text(encoding="utf-8"))["total"] == 1


def test_pipeline_worker_run_step_extract_includes_pdf_event_extraction(tmp_path, monkeypatch):
    fake_zip = types.ModuleType("zip")
    fake_pdf = types.ModuleType("pdf_extract")
    fake_events = types.ModuleType("pdf_extract_events")
    calls = {}

    def fake_run_extraction(*, settings=None):
        calls["zip_settings"] = settings
        return {"extracted": 1, "skipped": 2, "failed": 0}

    def fake_run_pdf_extraction(*, root_dir=None):
        calls["pdf_root_dir"] = root_dir
        return {"processed": 1, "failed": 0, "total_records": 10}

    def fake_run_event_extraction(*, root_dir=None, settings=None):
        calls["event_root_dir"] = root_dir
        calls["event_settings"] = settings
        return {"processed": 1, "skipped": 0, "failed": 0, "total_events": 5}

    fake_zip.run_extraction = fake_run_extraction
    fake_pdf.run_pdf_extraction = fake_run_pdf_extraction
    fake_events.run_event_extraction = fake_run_event_extraction

    monkeypatch.setitem(sys.modules, "zip", fake_zip)
    monkeypatch.setitem(sys.modules, "pdf_extract", fake_pdf)
    monkeypatch.setitem(sys.modules, "pdf_extract_events", fake_events)

    from workers.pipeline_worker import PipelineWorker

    worker = PipelineWorker()
    worker.pipeline_settings = SimpleNamespace(bulletins_root=str(tmp_path))

    result = worker.run_step_extract()

    assert result.status == "success"
    assert result.processed == 3
    assert result.skipped == 2
    assert result.failed == 0
    assert Path(calls["pdf_root_dir"]) == tmp_path
    assert Path(calls["event_root_dir"]) == tmp_path
    assert calls["event_settings"] == worker.pipeline_settings

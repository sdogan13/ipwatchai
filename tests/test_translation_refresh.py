import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import scripts.launch_name_tr_refresh_background as bg_refresh
import scripts.regenerate_name_tr as refresh


def _workspace_tmp_dir(label: str) -> Path:
    path = Path(".tmp_translation_refresh_tests") / f"{label}_{uuid4().hex}"
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_load_benchmark_cases_reads_fixture():
    cases = refresh.load_benchmark_cases()
    assert isinstance(cases, list)
    assert any(case["id"] == "en_apple" for case in cases)


def test_run_benchmark_writes_report_and_meets_gate():
    def fake_get_translations(text, backend=None):
        outputs = {
            ("APPLE", "nllb"): {"original": "APPLE", "detected_lang": "en", "tr": "elma"},
            ("APPLE", "madlad"): {"original": "APPLE", "detected_lang": "en", "tr": "elma"},
            ("ŞEKER", "nllb"): {"original": "ŞEKER", "detected_lang": "tr", "tr": "şeker"},
            ("ŞEKER", "madlad"): {"original": "ŞEKER", "detected_lang": "tr", "tr": "şeker"},
            ("DOĞAN electronics", "nllb"): {"original": "DOĞAN electronics", "detected_lang": "en", "tr": "doğan electronics"},
            ("DOĞAN electronics", "madlad"): {"original": "DOĞAN electronics", "detected_lang": "en", "tr": "doğan elektronik"},
        }
        return outputs.get((text, backend), {"original": text, "detected_lang": "en", "tr": text.lower()})

    benchmark_cases = [
        {"id": "en_apple", "name": "APPLE", "expected_lang": "en", "mode": "exact", "acceptable_tr": ["elma"]},
        {"id": "tr_seker", "name": "ŞEKER", "expected_lang": "tr", "mode": "preserve", "acceptable_tr": ["şeker"]},
        {"id": "mixed", "name": "DOĞAN electronics", "expected_lang": "en", "mode": "exact", "acceptable_tr": ["doğan elektronik"]},
    ]
    report_path = Path("benchmark.json")

    with patch.object(refresh, "initialize", return_value=True), \
         patch.object(refresh, "get_translations", side_effect=fake_get_translations), \
         patch.object(refresh, "load_benchmark_cases", return_value=benchmark_cases), \
         patch.object(Path, "write_text", return_value=1) as mock_write_text:
        report = refresh.run_benchmark(
            fixture_path=Path("unused.json"),
            baseline_backend="nllb",
            candidate_backend="madlad",
            report_path=report_path,
        )

    assert report["baseline_passes"] == 2
    assert report["candidate_passes"] == 3
    assert report["candidate_meets_gate"] is True
    assert mock_write_text.called


def test_prepare_updates_sets_provenance_and_stats():
    rows = [
        ("a", "APPLE", "apple", "en", None, None, None),
        ("b", "ŞEKER", "şeker", "tr", "nllb", "facebook/nllb-200-distilled-600M", None),
    ]
    translations = [("elma", "en"), ("şeker", "tr")]
    updated_at = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)

    updates, stats = refresh._prepare_updates(rows, translations, "madlad", updated_at)

    assert len(updates) == 2
    assert updates[0][0] == "elma"
    assert updates[0][2] == "madlad"
    assert "madlad400-3b-mt" in updates[0][3]
    assert updates[0][4] == updated_at
    assert stats["processed"] == 2
    assert stats["translation_changed"] == 1
    assert stats["lang_changed"] == 0
    assert stats["provenance_changed"] == 2


def test_prepare_updates_preserves_existing_lang_when_new_detection_unknown():
    rows = [
        ("a", "APPLE", "apple", "en", None, None, None),
        ("b", "MARKA", "marka", "tr", None, None, None),
    ]
    translations = [("elma", "unknown"), ("marka", "unknown")]
    updated_at = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)

    updates, stats = refresh._prepare_updates(rows, translations, "madlad", updated_at)

    assert updates[0][1] == "en"
    assert updates[1][1] == "tr"
    assert stats["lang_changed"] == 0


def test_apply_restore_batch_dry_run_returns_batch_size():
    restored = refresh._apply_restore_batch(None, [("elma", "en", "madlad", "model", None, "id-1")], dry_run=True)
    assert restored == 1


def test_row_needs_model_refresh_for_prompt_leakage_even_if_same_backend():
    row = (
        "id-1",
        "d.r.m atilla durmaz",
        "türkçeye çeviren: d.r.m atilla durmaz",
        "en",
        "madlad",
        "google/madlad400-3b-mt",
        None,
    )
    assert refresh._row_needs_model_refresh(row, "madlad", "google/madlad400-3b-mt") is True


def test_row_needs_model_refresh_when_translation_matches_original_without_madlad_provenance():
    row = (
        "id-2",
        "ORIGINAL",
        "ORIGINAL",
        "unknown",
        "nllb",
        "facebook/nllb-200-distilled-600M",
        None,
    )
    assert refresh._row_needs_model_refresh(row, "madlad", "google/madlad400-3b-mt") is True


def test_row_needs_model_refresh_even_if_row_already_has_madlad_provenance():
    row = (
        "id-3",
        "APPLE",
        "elma",
        "en",
        "madlad",
        "google/madlad400-3b-mt",
        None,
    )
    assert refresh._row_needs_model_refresh(row, "madlad", "google/madlad400-3b-mt") is True


def test_merge_resume_from_id_prefers_furthest_checkpoint():
    state = {"last_processed_id": "b0000000-0000-0000-0000-000000000000"}
    assert refresh._merge_resume_from_id("a0000000-0000-0000-0000-000000000000", state) == "b0000000-0000-0000-0000-000000000000"
    assert refresh._merge_resume_from_id(None, state) == "b0000000-0000-0000-0000-000000000000"


def test_is_resumable_progress_state_requires_matching_scope():
    metadata_root = _workspace_tmp_dir("resumable_state") / "bulletins" / "Marka"
    metadata_root.mkdir(parents=True)
    state = {
        "version": refresh.DEFAULT_PROGRESS_VERSION,
        "status": "running",
        "backend": "madlad",
        "null_only": False,
        "limit": None,
        "dry_run": False,
        "metadata_root": str(metadata_root),
        "ordering_mode": refresh.ORDERING_MODE_APPLICATION_DATE_DESC,
        "campaign_watermark": "2026-04-24T21:34:29Z",
    }

    assert refresh._is_resumable_progress_state(
        state,
        backend="madlad",
        null_only=False,
        limit=None,
        dry_run=False,
        metadata_root=metadata_root,
        ordering_mode=refresh.ORDERING_MODE_APPLICATION_DATE_DESC,
        campaign_watermark="2026-04-24T21:34:29Z",
    ) is True
    assert refresh._is_resumable_progress_state(
        state,
        backend="nllb",
        null_only=False,
        limit=None,
        dry_run=False,
        metadata_root=metadata_root,
        ordering_mode=refresh.ORDERING_MODE_APPLICATION_DATE_DESC,
        campaign_watermark="2026-04-24T21:34:29Z",
    ) is False

    stale_version = dict(state)
    stale_version["version"] = refresh.DEFAULT_PROGRESS_VERSION - 1
    assert refresh._is_resumable_progress_state(
        stale_version,
        backend="madlad",
        null_only=False,
        limit=None,
        dry_run=False,
        metadata_root=metadata_root,
        ordering_mode=refresh.ORDERING_MODE_APPLICATION_DATE_DESC,
        campaign_watermark="2026-04-24T21:34:29Z",
    ) is False

    assert refresh._is_resumable_progress_state(
        state,
        backend="madlad",
        null_only=False,
        limit=None,
        dry_run=False,
        metadata_root=metadata_root,
        ordering_mode=refresh.ORDERING_MODE_ID_ASC,
        campaign_watermark="2026-04-24T21:34:29Z",
    ) is False

    assert refresh._is_resumable_progress_state(
        state,
        backend="madlad",
        null_only=False,
        limit=None,
        dry_run=False,
        metadata_root=metadata_root,
        ordering_mode=refresh.ORDERING_MODE_APPLICATION_DATE_DESC,
        campaign_watermark="2026-04-25T00:00:00Z",
    ) is False


def test_query_scope_orders_by_newest_application_date_and_skips_campaign_rows():
    sql, params = refresh._query_scope(
        limit=128,
        ordering_mode=refresh.ORDERING_MODE_APPLICATION_DATE_DESC,
        campaign_backend="madlad",
        campaign_model_name="google/madlad400-3b-mt",
        campaign_watermark="2026-04-24T21:34:29Z",
    )

    assert "ORDER BY application_date DESC NULLS LAST, id DESC" in sql
    assert "name_tr_backend = %s" in sql
    assert "name_tr_model = %s" in sql
    assert "name_tr_updated_at >= %s::timestamptz" in sql
    assert params == ["madlad", "google/madlad400-3b-mt", "2026-04-24T21:34:29Z", 128]


def test_sync_metadata_from_db_updates_matching_records():
    metadata_root = _workspace_tmp_dir("sync_metadata") / "bulletins" / "Marka"
    folder = metadata_root / "BLT_490_2026-04-13"
    folder.mkdir(parents=True)
    metadata_path = folder / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "APPLICATIONNO": "2024/001",
                    "name_tr": "eski",
                    "detected_lang": "en",
                    "name_tr_backend": "nllb",
                    "name_tr_model": "old-model",
                    "name_tr_updated_at": "2026-01-01T00:00:00Z",
                },
                {
                    "APPLICATIONNO": "2024/002",
                    "name_tr": "ayni",
                    "detected_lang": "tr",
                },
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with patch.object(
        refresh,
        "_fetch_translation_rows_by_application_nos",
        return_value={
            "2024/001": {
                "name_tr": "yeni",
                "detected_lang": "en",
                "name_tr_backend": "madlad",
                "name_tr_model": "google/madlad400-3b-mt",
                "name_tr_updated_at": "2026-04-24T18:00:00Z",
            }
        },
    ):
        summary = refresh.sync_metadata_from_db(object(), root_dir=metadata_root)

    synced = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert synced[0]["name_tr"] == "yeni"
    assert synced[0]["detected_lang"] == "en"
    assert synced[0]["name_tr_backend"] == "madlad"
    assert synced[0]["name_tr_model"] == "google/madlad400-3b-mt"
    assert synced[0]["name_tr_updated_at"] == "2026-04-24T18:00:00Z"
    assert synced[1]["name_tr"] == "ayni"
    assert summary["files_scanned"] == 1
    assert summary["files_updated"] == 1
    assert summary["records_updated"] == 1
    assert summary["records_missing_db"] == 1


def test_sync_metadata_from_db_skips_completed_files_from_progress():
    metadata_root = _workspace_tmp_dir("sync_skip_progress") / "bulletins" / "Marka"
    folder_a = metadata_root / "BLT_490_2026-04-13"
    folder_b = metadata_root / "GZ_500_2026-03-31"
    folder_a.mkdir(parents=True)
    folder_b.mkdir(parents=True)
    (folder_a / "metadata.json").write_text(json.dumps([{"APPLICATIONNO": "2024/001"}]), encoding="utf-8")
    (folder_b / "metadata.json").write_text(json.dumps([{"APPLICATIONNO": "2024/002"}]), encoding="utf-8")

    progress_state = {
        "metadata_sync": {
            "completed_files": ["BLT_490_2026-04-13/metadata.json"],
        }
    }

    with patch.object(
        refresh,
        "sync_metadata_file",
        return_value={
            "record_count": 1,
            "records_updated": 1,
            "records_missing_db": 0,
            "file_updated": True,
        },
    ) as mock_sync_file:
        summary = refresh.sync_metadata_from_db(
            object(),
            root_dir=metadata_root,
            progress_state=progress_state,
        )

    assert summary["files_scanned"] == 2
    assert summary["files_skipped_from_progress"] == 1
    assert summary["files_updated"] == 1
    assert mock_sync_file.call_count == 1
    assert "GZ_500_2026-03-31/metadata.json" in progress_state["metadata_sync"]["completed_files"]


def test_sync_metadata_file_removes_stale_provenance_when_db_row_has_none():
    metadata_path = _workspace_tmp_dir("sync_remove_provenance") / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "APPLICATIONNO": "2024/001",
                    "name_tr": "ornek",
                    "detected_lang": "tr",
                    "name_tr_backend": "madlad",
                    "name_tr_model": "google/madlad400-3b-mt",
                    "name_tr_updated_at": "2026-04-24T18:00:00Z",
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with patch.object(
        refresh,
        "_fetch_translation_rows_by_application_nos",
        return_value={
            "2024/001": {
                "name_tr": "ornek",
                "detected_lang": "tr",
                "name_tr_backend": None,
                "name_tr_model": None,
                "name_tr_updated_at": None,
            }
        },
    ):
        result = refresh.sync_metadata_file(object(), metadata_path)

    synced = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert "name_tr_backend" not in synced[0]
    assert "name_tr_model" not in synced[0]
    assert "name_tr_updated_at" not in synced[0]
    assert result["records_updated"] == 1


def test_background_launcher_builds_refresh_args():
    args = bg_refresh.build_arg_parser().parse_args(
        [
            "--backend",
            "madlad",
            "--skip-benchmark",
            "--limit",
            "50",
            "--batch-size",
            "64",
            "--translate-batch-size",
            "16",
            "--ordering-mode",
            "application_date_desc",
            "--campaign-watermark",
            "2026-04-24T21:34:29Z",
            "--metadata-root",
            "C:/tmp/metadata",
        ]
    )
    command = bg_refresh.build_refresh_args(args)

    assert command[0].endswith("python.exe") or command[0].endswith("python") or command[0].endswith("pythonw.exe")
    assert "regenerate_name_tr.py" in command[2]
    assert "--skip-benchmark" in command
    assert "--limit" in command
    assert "50" in command
    assert "--translate-batch-size" in command
    assert "16" in command
    assert "--ordering-mode" in command
    assert "application_date_desc" in command
    assert "--campaign-watermark" in command
    assert "2026-04-24T21:34:29Z" in command
    assert "--metadata-root" in command


def test_background_launcher_writes_manifest():
    tmp_path = _workspace_tmp_dir("background_manifest")
    args = bg_refresh.build_arg_parser().parse_args(
        [
            "--backend",
            "madlad",
            "--skip-benchmark",
            "--output-root",
            str(tmp_path),
        ]
    )
    fake_process = MagicMock()
    fake_process.pid = 4242

    with patch.object(bg_refresh.subprocess, "Popen", return_value=fake_process):
        payload = bg_refresh.launch_background(args)

    assert payload["pid"] == 4242
    assert Path(payload["stdout_path"]).exists()
    assert Path(payload["stderr_path"]).exists()
    assert Path(tmp_path / "name_tr_refresh_bg_latest.json").exists()

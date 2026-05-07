from pathlib import Path


def _agentic_source() -> str:
    return (Path(__file__).resolve().parents[1] / "agentic_search.py").read_text(
        encoding="utf-8"
    )


def test_agentic_live_ingest_uses_canonical_app_source_without_app_live_json():
    source = _agentic_source()

    assert "APP_LIVE" not in source
    assert "scraped_data_dir" not in source
    assert "process_records_batch" in source
    assert "save_to_json(records, target_file=metadata_path)" in source
    assert "LIVE_{safe_query}" not in source

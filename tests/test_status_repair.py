import json

from pipeline import status_repair
from pipeline.ingest_rules import (
    DB_STATUS_PUBLISHED,
    DB_STATUS_REFUSED,
    DB_STATUS_REGISTERED,
    DB_STATUS_WITHDRAWN,
)


def candidate(**overrides):
    values = {
        "id": "11111111-1111-1111-1111-111111111111",
        "application_no": "2026/019871",
        "current_status": "Başvuruldu",
        "status_source": "APP",
        "bulletin_no": None,
        "bulletin_date": None,
        "gazette_no": None,
        "gazette_date": None,
        "registration_no": None,
        "registration_date": None,
    }
    values.update(overrides)
    return status_repair.RepairCandidate(**values)


def test_bulletin_evidence_defaults_to_published_without_app_text():
    decision = status_repair.decide_repair(candidate(bulletin_no="489"))

    assert decision.target_status == DB_STATUS_PUBLISHED
    assert decision.target_source == "BLT"
    assert decision.reason == "bulletin_evidence"


def test_gazette_evidence_defaults_to_registered_without_app_text():
    decision = status_repair.decide_repair(candidate(gazette_no="500"))

    assert decision.target_status == DB_STATUS_REGISTERED
    assert decision.target_source == "GZ"
    assert decision.reason == "gazette_or_registration_evidence"


def test_app_explicit_refused_text_wins_over_bulletin_default():
    evidence = status_repair.AppStatusEvidence(
        status_text="marka başvurusu/tescili geçersiz sayıldı",
        source_file="APP_LIVE/live.json",
        resolved_status=DB_STATUS_REFUSED,
    )

    decision = status_repair.decide_repair(candidate(bulletin_no="489"), evidence)

    assert decision.target_status == DB_STATUS_REFUSED
    assert decision.target_source == "APP"
    assert decision.reason == "app_explicit_status"


def test_app_explicit_withdrawn_text_wins_over_registration_default():
    evidence = status_repair.AppStatusEvidence(
        status_text="feragat edildi",
        source_file="APP_LIVE/live.json",
        resolved_status=DB_STATUS_WITHDRAWN,
    )

    decision = status_repair.decide_repair(candidate(gazette_no="500"), evidence)

    assert decision.target_status == DB_STATUS_WITHDRAWN
    assert decision.target_source == "APP"
    assert decision.reason == "app_explicit_status"


def test_app_lookup_ignores_non_app_metadata_and_keeps_explicit_app_status(tmp_path):
    app_dir = tmp_path / "APP_LIVE"
    app_dir.mkdir()
    blt_dir = tmp_path / "BLT_489_2026-03-27"
    blt_dir.mkdir()

    (app_dir / "metadata.json").write_text(
        json.dumps(
            [
                {
                    "APPLICATIONNO": "2026/019871",
                    "STATUS": "feragat edildi",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (blt_dir / "metadata.json").write_text(
        json.dumps(
            [
                {
                    "APPLICATIONNO": "2026/019871",
                    "STATUS": "Application/Published",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    lookup = status_repair.build_app_status_lookup(tmp_path, ["2026/019871"])

    assert lookup["2026/019871"].resolved_status == DB_STATUS_WITHDRAWN
    assert "APP_LIVE" in lookup["2026/019871"].source_file

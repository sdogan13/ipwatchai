"""Unit tests for ``pipeline.reconcile_patent`` helpers.

Built one helper at a time. Each step adds its own test block so failures
point cleanly at the unit under test.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from pipeline.reconcile_patent import (
    CanonicalRecord,
    _cd_figures,
    _dmy_to_iso,
    _normalize_cd_attorney,
    _normalize_cd_party,
    _normalize_cd_priority,
    load_cd_metadata,
    load_pdf_metadata,
    normalize_cd_record,
)


# ---------------------------------------------------------------------------
# Step 4.1 — JSON loaders + CanonicalRecord dataclass
# ---------------------------------------------------------------------------


def _write_json(tmp_path: Path, name: str, doc: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_load_cd_metadata_returns_doc(tmp_path: Path) -> None:
    doc = {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "patents": [{"application_no": "2017/15048"}],
        "stats": {"patents": 1},
    }
    path = _write_json(tmp_path, "cd.json", doc)

    assert load_cd_metadata(path) == doc


def test_load_pdf_metadata_returns_doc(tmp_path: Path) -> None:
    doc = {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "records": [{"application_no": "2021/011498"}],
        "stats": {"records": 1},
    }
    path = _write_json(tmp_path, "pdf.json", doc)

    assert load_pdf_metadata(path) == doc


def test_load_cd_metadata_rejects_pdf_shape(tmp_path: Path) -> None:
    """A swapped --cd-json/--pdf-json must fail loud, not silently merge."""
    pdf_doc = {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "records": [],          # PDF key
        "stats": {},
    }
    path = _write_json(tmp_path, "swapped.json", pdf_doc)

    with pytest.raises(ValueError, match="not a CD metadata doc"):
        load_cd_metadata(path)


def test_load_pdf_metadata_rejects_cd_shape(tmp_path: Path) -> None:
    cd_doc = {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "patents": [],          # CD key
        "stats": {},
    }
    path = _write_json(tmp_path, "swapped.json", cd_doc)

    with pytest.raises(ValueError, match="not a PDF metadata doc"):
        load_pdf_metadata(path)


def test_load_cd_metadata_rejects_non_object(tmp_path: Path) -> None:
    path = _write_json(tmp_path, "list.json", ["not", "a", "dict"])
    with pytest.raises(ValueError, match="expected JSON object"):
        load_cd_metadata(path)


def test_load_cd_metadata_rejects_non_list_patents(tmp_path: Path) -> None:
    doc = {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "patents": {},          # wrong type
        "stats": {},
    }
    path = _write_json(tmp_path, "bad.json", doc)
    with pytest.raises(ValueError, match="'patents' must be a list"):
        load_cd_metadata(path)


def test_canonical_record_defaults() -> None:
    """All collection fields default to empty list — no None-vs-[] traps."""
    rec = CanonicalRecord(application_no="2017/15048")

    assert rec.application_no == "2017/15048"
    assert rec.ipc_classes == []
    assert rec.holders == []
    assert rec.inventors == []
    assert rec.attorneys == []
    assert rec.priorities == []
    assert rec.figures == []
    assert rec.source_format == "CD"
    assert rec.title is None
    assert rec.page_range is None


def test_canonical_record_asdict_is_jsonable() -> None:
    """asdict() output must roundtrip through json.dumps without TypeError."""
    rec = CanonicalRecord(
        application_no="2021/011498",
        application_date="2014-01-10",
        publication_no="TR 2021 011498 B",
        kind_code="B",
        record_type="GRANTED_PATENT",
        title="Test",
        ipc_classes=["A61M 5/31"],
        holders=[{"name": "ACME", "country": "TR"}],
        page_range=[117, 117],
        source_format="BOTH",
    )

    payload = asdict(rec)
    encoded = json.dumps(payload, ensure_ascii=False)

    decoded = json.loads(encoded)
    assert decoded["application_no"] == "2021/011498"
    assert decoded["holders"] == [{"name": "ACME", "country": "TR"}]
    assert decoded["page_range"] == [117, 117]
    assert decoded["source_format"] == "BOTH"


# ---------------------------------------------------------------------------
# Step 4.2 — normalize_cd_record
# ---------------------------------------------------------------------------


def test_dmy_to_iso_happy_path() -> None:
    assert _dmy_to_iso("22/12/2025") == "2025-12-22"
    assert _dmy_to_iso("05/10/2017") == "2017-10-05"
    assert _dmy_to_iso(" 1/3/2020 ") == "2020-03-01"  # tolerates 1-digit + outer ws


def test_dmy_to_iso_returns_none_on_garbage() -> None:
    assert _dmy_to_iso(None) is None
    assert _dmy_to_iso("") is None
    assert _dmy_to_iso("not-a-date") is None
    assert _dmy_to_iso("2025-12-22") is None      # ISO not accepted (use is for CD only)
    assert _dmy_to_iso("31/02/2020") is None      # invalid Feb 31


def test_normalize_cd_party_renames_title_to_name() -> None:
    party = {
        "title": "ACME ANONİM ŞİRKETİ",
        "address": "A St 1",
        "state": "Tire",
        "postal_code": "",                # empty — should be dropped
        "city": "İzmir",
        "country": "TR",
    }
    out = _normalize_cd_party(party)
    assert out == {
        "name": "ACME ANONİM ŞİRKETİ",
        "address": "A St 1",
        "state": "Tire",
        "city": "İzmir",
        "country": "TR",
    }
    assert "postal_code" not in out
    assert "title" not in out


def test_normalize_cd_party_handles_empty_dict() -> None:
    """Defensive: stays-shaped even if CD ships an empty holder."""
    out = _normalize_cd_party({})
    assert out == {"name": ""}


def test_normalize_cd_attorney_drops_empty_fields() -> None:
    attorney = {
        "no": "361",
        "name": "ERDEM KAYA",
        "address": "",                    # empty — dropped
        "firm": "ERDEM KAYA PATENT VE DAN. A.Ş.",
    }
    out = _normalize_cd_attorney(attorney)
    assert out == {
        "no": "361",
        "name": "ERDEM KAYA",
        "firm": "ERDEM KAYA PATENT VE DAN. A.Ş.",
    }
    assert "address" not in out


def test_normalize_cd_priority_iso_date() -> None:
    out = _normalize_cd_priority({
        "priority_no": "2020/05105",
        "priority_date": "31/03/2020",
        "country": "TR",
    })
    assert out == {
        "priority_no": "2020/05105",
        "priority_date": "2020-03-31",
        "country": "TR",
    }


def test_normalize_cd_priority_skips_unparseable_date() -> None:
    out = _normalize_cd_priority({
        "priority_no": "X",
        "priority_date": "",
        "country": "TR",
    })
    assert "priority_date" not in out
    assert out["priority_no"] == "X"


def test_cd_figures_wraps_image_path() -> None:
    assert _cd_figures("data/images/2017/15048.tif") == [
        {"image_path": "data/images/2017/15048.tif"},
    ]


def test_cd_figures_empty_for_missing_path() -> None:
    assert _cd_figures("") == []
    assert _cd_figures(None) == []
    assert _cd_figures("   ") == []


def test_normalize_cd_record_full() -> None:
    """Real shape pulled from 2025_07_metadata.json (bulletin 2025/8)."""
    cd = {
        "application_no": "2017/15048",
        "application_date": "05/10/2017",
        "patent_no": "",
        "patent_date": "",
        "ipc_codes": ["A61M 5/31", "A61J 1/14"],
        "publication_no": "TR 2017 15048 U3",
        "publication_type": "73",
        "publication_date": "22/12/2025",
        "title": "EMNİYET BELİRTEÇLİ ENJEKTÖR KİLİDİ VE KİLİTLEME YÖNTEMİ",
        "patent_type": "2",
        "abstract": "Kısa özet.",
        "image_path": "data/images/2017/15048.tif",
        "holders": [{
            "title": "ACME",
            "address": "X St 1",
            "state": "",
            "postal_code": "",
            "city": "İzmir",
            "country": "TR",
        }],
        "inventors": [{
            "title": "JANE DOE",
            "address": "",
            "state": "",
            "postal_code": "",
            "city": "",
            "country": "",
        }],
        "attorneys": [{
            "no": "361",
            "name": "ERDEM KAYA",
            "address": "",
            "firm": "ERDEM KAYA PATENT VE DAN. A.Ş.",
        }],
        "priorities": [{
            "priority_no": "2020/05105",
            "priority_date": "31/03/2020",
            "country": "TR",
        }],
    }

    rec = normalize_cd_record(cd)

    assert rec.application_no == "2017/15048"
    assert rec.application_date == "2017-10-05"          # DD/MM -> ISO
    assert rec.publication_no == "TR 2017 15048 U3"
    assert rec.publication_date == "2025-12-22"
    assert rec.kind_code == "U3"                          # extracted
    assert rec.record_type == "UNKNOWN"                   # gap: U3 not mapped (memory note)
    assert rec.title.startswith("EMNİYET")
    assert rec.abstract == "Kısa özet."
    assert rec.ipc_classes == ["A61M 5/31", "A61J 1/14"]  # renamed key
    assert rec.holders == [{
        "name": "ACME",
        "address": "X St 1",
        "city": "İzmir",
        "country": "TR",
    }]
    assert rec.inventors == [{"name": "JANE DOE"}]
    assert rec.attorneys == [{
        "no": "361",
        "name": "ERDEM KAYA",
        "firm": "ERDEM KAYA PATENT VE DAN. A.Ş.",
    }]
    assert rec.priorities == [{
        "priority_no": "2020/05105",
        "priority_date": "2020-03-31",
        "country": "TR",
    }]
    assert rec.figures == [{"image_path": "data/images/2017/15048.tif"}]
    assert rec.patent_type == "2"
    assert rec.source_format == "CD"
    assert rec.page_range is None                          # PDF-only field


def test_normalize_cd_record_classify_known_kind() -> None:
    """T4 should classify cleanly even though A3/U3/T7 don't (gap memory)."""
    cd = {
        "application_no": "2022/014462",
        "publication_no": "TR 2022 014462 T4",
        "publication_date": "21/08/2025",
        "application_date": "10/01/2014",
        "ipc_codes": [],
    }
    rec = normalize_cd_record(cd)
    assert rec.kind_code == "T4"
    assert rec.record_type == "GRANTED_PATENT"


def test_normalize_cd_record_handles_missing_publication_no() -> None:
    """Some CD rows ship blank publication_no — must not crash."""
    cd = {
        "application_no": "2017/15048",
        "publication_no": "",
        "ipc_codes": [],
    }
    rec = normalize_cd_record(cd)
    assert rec.publication_no is None
    assert rec.kind_code is None
    assert rec.record_type is None
    assert rec.application_date is None
    assert rec.holders == []
    assert rec.figures == []

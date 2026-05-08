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
    load_cd_metadata,
    load_pdf_metadata,
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

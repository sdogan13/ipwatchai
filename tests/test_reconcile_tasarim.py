"""Unit tests for ``pipeline.reconcile_tasarim``.

Built one helper at a time, mirroring the patent-reconcile pattern.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from pipeline.reconcile_tasarim import (
    CanonicalDesign,
    CanonicalDesignRecord,
    CanonicalDesignView,
    load_cd_metadata,
    load_pdf_metadata,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _minimal_cd_doc() -> dict:
    """Smallest valid CD metadata doc — the four required top-level keys."""
    return {
        "bulletin_no": "240",
        "bulletin_date": "2016-03-09",
        "stats": {"dossiers": 0, "designs": 0},
        "dossiers": [],
    }


def _minimal_pdf_doc() -> dict:
    """Smallest valid PDF metadata doc."""
    return {
        "bulletin_no": 240,
        "bulletin_date": "2016-03-09",
        "records": [],
    }


def _write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Step 3.1 — load_cd_metadata
# ---------------------------------------------------------------------------

def test_load_cd_metadata_minimal_valid(tmp_path):
    p = tmp_path / "cd_metadata.json"
    _write(p, _minimal_cd_doc())
    doc = load_cd_metadata(p)
    assert doc["bulletin_no"] == "240"
    assert doc["dossiers"] == []


def test_load_cd_metadata_accepts_str_path(tmp_path):
    p = tmp_path / "cd_metadata.json"
    _write(p, _minimal_cd_doc())
    doc = load_cd_metadata(str(p))
    assert doc["bulletin_date"] == "2016-03-09"


def test_load_cd_metadata_rejects_pdf_doc(tmp_path):
    """Catches a swapped --cd-json/--pdf-json before any merge work."""
    p = tmp_path / "metadata.json"
    _write(p, _minimal_pdf_doc())
    with pytest.raises(ValueError, match=r"not a CD metadata doc"):
        load_cd_metadata(p)


def test_load_cd_metadata_rejects_non_dict_root(tmp_path):
    p = tmp_path / "cd.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match=r"expected JSON object"):
        load_cd_metadata(p)


def test_load_cd_metadata_rejects_non_list_dossiers(tmp_path):
    p = tmp_path / "cd.json"
    bad = _minimal_cd_doc()
    bad["dossiers"] = {"not": "a list"}  # type: ignore[assignment]
    _write(p, bad)
    with pytest.raises(ValueError, match=r"'dossiers' must be a list"):
        load_cd_metadata(p)


def test_load_cd_metadata_partial_keys_listed_in_error(tmp_path):
    """Error message names exactly which keys are missing."""
    p = tmp_path / "cd.json"
    _write(p, {"bulletin_no": "240"})
    with pytest.raises(ValueError, match=r"missing.*bulletin_date.*dossiers.*stats"):
        load_cd_metadata(p)


# ---------------------------------------------------------------------------
# Step 3.1 — load_pdf_metadata
# ---------------------------------------------------------------------------

def test_load_pdf_metadata_minimal_valid(tmp_path):
    p = tmp_path / "metadata.json"
    _write(p, _minimal_pdf_doc())
    doc = load_pdf_metadata(p)
    assert doc["bulletin_no"] == 240
    assert doc["records"] == []


def test_load_pdf_metadata_rejects_cd_doc(tmp_path):
    """Symmetric swap detection — CD doc has no 'records' key."""
    p = tmp_path / "cd_metadata.json"
    _write(p, _minimal_cd_doc())
    with pytest.raises(ValueError, match=r"not a PDF metadata doc"):
        load_pdf_metadata(p)


def test_load_pdf_metadata_rejects_non_dict_root(tmp_path):
    p = tmp_path / "pdf.json"
    p.write_text('"a string"', encoding="utf-8")
    with pytest.raises(ValueError, match=r"expected JSON object"):
        load_pdf_metadata(p)


def test_load_pdf_metadata_rejects_non_list_records(tmp_path):
    p = tmp_path / "pdf.json"
    bad = _minimal_pdf_doc()
    bad["records"] = "not a list"  # type: ignore[assignment]
    _write(p, bad)
    with pytest.raises(ValueError, match=r"'records' must be a list"):
        load_pdf_metadata(p)


# ---------------------------------------------------------------------------
# Step 3.1 — CanonicalDesignView / CanonicalDesign / CanonicalDesignRecord
# ---------------------------------------------------------------------------

def test_canonical_view_defaults():
    v = CanonicalDesignView(view_no="1")
    assert v.view_no == "1"
    assert v.image_path is None
    assert v.image_source is None


def test_canonical_view_with_provenance():
    v = CanonicalDesignView(
        view_no="1",
        image_path="2016_01059/1_1.jpg",
        image_source="cd",
    )
    assert v.image_source == "cd"


def test_canonical_design_defaults():
    d = CanonicalDesign(no="1")
    assert d.product_name == ""
    assert d.views == []


def test_canonical_record_defaults_match_cd_priority_shape():
    """Default source_format is 'CD' so a freshly-built record is treated
    as CD-derived until the merger says otherwise."""
    r = CanonicalDesignRecord()
    assert r.source_format == "CD"
    assert r.locarno_codes == []
    assert r.holders == []
    assert r.designers == []
    assert r.priorities == []
    assert r.designs == []
    assert r.hague_reference is None
    assert r.page_range is None
    assert r.deferred_publication is None
    assert r.application_no is None
    assert r.design_count is None


def test_canonical_record_serializes_via_asdict():
    """asdict round-trip works — needed for the merged JSON output."""
    r = CanonicalDesignRecord(
        application_no="2016/01059",
        application_date="2016-02-10",
        design_count=1,
        locarno_codes=["25-02"],
        designs=[
            CanonicalDesign(
                no="1",
                product_name="Profil",
                views=[CanonicalDesignView(view_no="1",
                                            image_path="2016_01059/1_1.jpg",
                                            image_source="cd")],
            ),
        ],
        source_format="BOTH",
    )
    d = asdict(r)
    assert d["application_no"] == "2016/01059"
    assert d["designs"][0]["views"][0]["image_path"] == "2016_01059/1_1.jpg"
    assert d["designs"][0]["views"][0]["image_source"] == "cd"
    assert d["source_format"] == "BOTH"


def test_canonical_record_source_format_values_explicit():
    """All three documented source_format values are supported."""
    for tag in ("CD", "PDF", "BOTH"):
        r = CanonicalDesignRecord(source_format=tag)
        assert r.source_format == tag

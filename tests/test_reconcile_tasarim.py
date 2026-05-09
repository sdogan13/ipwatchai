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
    normalize_cd_dossier,
)
from pipeline.reconcile_tasarim import (
    _clean_str,
    _dmy_to_iso,
    _parse_design_count,
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


# ---------------------------------------------------------------------------
# Step 3.2 — small helpers
# ---------------------------------------------------------------------------

def test_dmy_to_iso_dotted_format():
    """Tasarim uses dot-separated DD.MM.YYYY (different from patent's slash)."""
    assert _dmy_to_iso("10.02.2016") == "2016-02-10"
    assert _dmy_to_iso("9.3.2016") == "2016-03-09"        # no leading zero
    assert _dmy_to_iso("  10.02.2016  ") == "2016-02-10"  # whitespace


def test_dmy_to_iso_invalid_inputs():
    assert _dmy_to_iso(None) is None
    assert _dmy_to_iso("") is None
    assert _dmy_to_iso("garbage") is None
    assert _dmy_to_iso("32.02.2016") is None              # bad day
    assert _dmy_to_iso("10/02/2016") is None              # slash, not dot
    assert _dmy_to_iso(12345) is None                      # not a string


def test_clean_str_strips_or_returns_none():
    assert _clean_str("  hello  ") == "hello"
    assert _clean_str("") is None
    assert _clean_str("   ") is None
    assert _clean_str(None) is None
    assert _clean_str(42) is None  # not a string


def test_parse_design_count_string_to_int():
    """CD ships design_count as a string; merge wants int."""
    assert _parse_design_count("1") == 1
    assert _parse_design_count("34") == 34
    assert _parse_design_count("  2  ") == 2
    assert _parse_design_count(5) == 5  # PDF int passes through
    assert _parse_design_count("") is None
    assert _parse_design_count(None) is None
    assert _parse_design_count("not-a-number") is None


# ---------------------------------------------------------------------------
# Step 3.2 — normalize_cd_dossier
# ---------------------------------------------------------------------------

def _real_cd_dossier_2016_01059() -> dict:
    """Real dossier shape from cd_extract_tasarim's 240_CD.rar output."""
    return {
        "application_no": "2016/01059",
        "application_date": "10.02.2016",
        "register_no": "2016 01059",
        "register_date": "10.02.2016",
        "design_count": "1",
        "type": "",
        "locarno_codes": ["25-02"],
        "attorney": {
            "no": "",
            "name": "RABİA ÇETİN (DEV PATENT MARKA VE FİKRİ HAK. DAN. TİC. LTD. ŞTİ.)",
            "title": "",
            "address": "MECİDİYEKÖY MAH. ESKİ OSMANLI SOK.",
        },
        "holders": [{
            "client_no": "234974",
            "title": "BİRLİK MENFEZ HAV. EKİP. SANAYİ TİCARET LİMİTED ŞİRKETİ",
            "address": "Organize San. Böl.",
            "city": "İSTANBUL",
            "country": "TÜRKİYE",
        }],
        "designers": [{
            "no": "1",
            "name": "VEDAT ÇELİK",
            "address": "Enverpaşa Cad.",
            "country": "TÜRKİYE",
        }],
        "designs": [{
            "no": "1",
            "product_name": "Profil ",  # trailing space verbatim from IDDESIGN.PRODUCTNAME
            "views": [
                {"view_no": "1", "image_path": "2016_01059/1_1.jpg"},
            ],
        }],
    }


def test_normalize_cd_dossier_real_record_full_shape():
    """End-to-end shape check against a real-data dossier."""
    rec = normalize_cd_dossier(_real_cd_dossier_2016_01059())
    assert rec.application_no == "2016/01059"
    assert rec.registration_no == "2016 01059"
    assert rec.application_date == "2016-02-10"
    assert rec.registration_date == "2016-02-10"
    assert rec.design_count == 1
    assert rec.type is None  # empty CD type collapses to None
    assert rec.locarno_codes == ["25-02"]
    assert rec.section is None  # CD has no section concept
    assert rec.priorities == []  # CD doesn't carry priorities
    assert rec.hague_reference is None
    assert rec.page_range is None
    assert rec.deferred_publication is None
    assert rec.source_format == "CD"

    # Attorney: empty 'no' and 'title' fields dropped, name + address retained
    assert rec.attorney == {
        "name": "RABİA ÇETİN (DEV PATENT MARKA VE FİKRİ HAK. DAN. TİC. LTD. ŞTİ.)",
        "address": "MECİDİYEKÖY MAH. ESKİ OSMANLI SOK.",
    }

    # Holders / designers: every present field carried, in declared key order
    assert len(rec.holders) == 1
    h = rec.holders[0]
    assert h["client_no"] == "234974"
    assert h["country"] == "TÜRKİYE"

    assert len(rec.designers) == 1
    d = rec.designers[0]
    assert d["name"] == "VEDAT ÇELİK"

    # Designs: trailing whitespace in product_name stripped
    assert len(rec.designs) == 1
    des = rec.designs[0]
    assert des.no == "1"
    assert des.product_name == "Profil"  # trailing space gone
    assert len(des.views) == 1
    v = des.views[0]
    assert v.view_no == "1"
    assert v.image_path == "2016_01059/1_1.jpg"
    assert v.image_source == "cd"


def test_normalize_cd_dossier_hague_no_image():
    """Hague dossier (DM/...) carries no images. Views still present
    but with image_path=None and image_source=None."""
    dossier = {
        "application_no": "DM/086402",
        "application_date": "01.01.2016",
        "register_no": "DM 086402",
        "register_date": "01.01.2016",
        "design_count": "1",
        "type": "",
        "locarno_codes": ["21-02"],
        "attorney": {"no": "", "name": "", "title": "", "address": ""},
        "holders": [],
        "designers": [],
        "designs": [{
            "no": "1",
            "product_name": "Hague design",
            "views": [],
        }],
    }
    rec = normalize_cd_dossier(dossier)
    assert rec.application_no == "DM/086402"
    assert rec.registration_no == "DM 086402"
    assert rec.attorney is None  # all four sub-fields were empty
    assert rec.holders == []
    assert rec.designs[0].views == []


def test_normalize_cd_dossier_drops_empty_optional_fields():
    """Empty strings for date / type collapse to None — JSON stays tidy."""
    dossier = {
        "application_no": "2016/00001",
        "application_date": "",
        "register_no": "",
        "register_date": "",
        "design_count": "",
        "type": "",
        "locarno_codes": [],
        "attorney": None,
        "holders": [],
        "designers": [],
        "designs": [],
    }
    rec = normalize_cd_dossier(dossier)
    assert rec.application_no == "2016/00001"
    assert rec.registration_no is None
    assert rec.application_date is None
    assert rec.registration_date is None
    assert rec.design_count is None
    assert rec.type is None
    assert rec.locarno_codes == []
    assert rec.attorney is None


def test_normalize_cd_dossier_multi_design_multi_view():
    """Multi-design dossier preserves design + view ordering."""
    dossier = {
        "application_no": "2015/06749",
        "application_date": "01.01.2016",
        "register_no": "",
        "register_date": "01.01.2016",
        "design_count": "2",
        "type": "1",
        "locarno_codes": ["06-04", "06-02"],
        "holders": [],
        "designers": [],
        "designs": [
            {
                "no": "1",
                "product_name": "A",
                "views": [
                    {"view_no": "1", "image_path": "2015_06749/1_1.jpg"},
                    {"view_no": "2", "image_path": "2015_06749/1_2.jpg"},
                ],
            },
            {
                "no": "2",
                "product_name": "B",
                "views": [],  # design without images (but still emitted)
            },
        ],
    }
    rec = normalize_cd_dossier(dossier)
    assert rec.design_count == 2
    assert rec.type == "1"  # non-empty type preserved
    assert len(rec.designs) == 2
    assert rec.designs[0].no == "1"
    assert len(rec.designs[0].views) == 2
    assert rec.designs[0].views[0].image_source == "cd"
    assert rec.designs[1].no == "2"
    assert rec.designs[1].views == []


def test_normalize_cd_dossier_filters_empty_holders():
    """Holders that are entirely blank don't appear in the merged output."""
    dossier = {
        "application_no": "2016/00001",
        "application_date": "01.01.2016",
        "register_no": "",
        "register_date": "01.01.2016",
        "design_count": "1",
        "type": "",
        "locarno_codes": [],
        "holders": [
            {"client_no": "234974", "title": "REAL HOLDER",
             "address": "", "city": "", "country": ""},
            {"client_no": "", "title": "", "address": "",
             "city": "", "country": ""},  # all empty -> filtered
        ],
        "designers": [],
        "designs": [],
    }
    rec = normalize_cd_dossier(dossier)
    assert len(rec.holders) == 1
    assert rec.holders[0]["title"] == "REAL HOLDER"

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
    normalize_pdf_record,
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

    # Holders / designers: every present field carried, in declared key order.
    # Note: CD's "title" (entity name) collapses to canonical "name" so the
    # merged shape matches PDF's applicants.
    assert len(rec.holders) == 1
    h = rec.holders[0]
    assert h["client_no"] == "234974"
    assert h["name"] == "BİRLİK MENFEZ HAV. EKİP. SANAYİ TİCARET LİMİTED ŞİRKETİ"
    assert "title" not in h  # title -> name rename
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
    """Holders that are entirely blank don't appear in the merged output.
    Note: CD's "title" field collapses to canonical "name"."""
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
    assert rec.holders[0]["name"] == "REAL HOLDER"  # title -> name


# ---------------------------------------------------------------------------
# Step 3.3 — normalize_pdf_record
# ---------------------------------------------------------------------------

def _real_pdf_record_2024_007254() -> dict:
    """Real record shape from pdf_extract_tasarim's TS_483 output."""
    return {
        "section": "tr_native",
        "record_index": 1,
        "application_no": "2024/007254",
        "registration_no": "2024 007254",
        "filing_date": "2024-09-06",
        "registration_date": "2024-09-06",
        "design_count": 4,
        "locarno_classes": ["26-05"],
        "applicants": [{
            "name": "TİM MİMARLIK DEKORASYON İNŞAAT TURİZM LİMİTED ŞİRKETİ",
            "id": "7610221",
            "address": "HARBİYE MAH. ABDİ İPEKÇİ",
            "country": "TÜRKİYE",
        }],
        "designers": [{"name": "ŞEBNEM SULTAN BUHARA GÜLEN"}],
        "attorney": {
            "name": "IŞIK ÖZDOĞAN",
            "firm": "MOROĞLU ARSEVEN DANIŞMANLIK A.Ş.",
        },
        "priorities": [],
        "designs": [{
            "design_index": 1,
            "product_name_tr": "Lamba",
            "views": [{
                "view_index": 1, "page": 17, "image_xref": 156,
                "bbox": [66.0, 100.0, 200.0, 280.0],
                "image_path": "2024_007254/1_1.jpg",
                "image_source": "pdf",
                "embeddings": {"dinov2_vitl14": [0.1] * 1024},  # gets DROPPED
            }],
        }],
        "page_range": [17, 17],
    }


def test_normalize_pdf_record_real_full_shape():
    """End-to-end shape check on a real PDF record."""
    rec = normalize_pdf_record(_real_pdf_record_2024_007254())
    assert rec.application_no == "2024/007254"
    assert rec.registration_no == "2024 007254"
    assert rec.application_date == "2024-09-06"   # filing_date renamed
    assert rec.registration_date == "2024-09-06"
    assert rec.design_count == 4
    assert rec.type is None  # PDF has no IDDOSSIER.TYPE
    assert rec.section == "tr_native"
    assert rec.locarno_codes == ["26-05"]
    assert rec.priorities == []
    assert rec.hague_reference is None
    assert rec.page_range == [17, 17]
    assert rec.deferred_publication is None
    assert rec.source_format == "PDF"

    assert rec.attorney == {
        "name": "IŞIK ÖZDOĞAN",
        "firm": "MOROĞLU ARSEVEN DANIŞMANLIK A.Ş.",
    }

    assert len(rec.holders) == 1
    h = rec.holders[0]
    # PDF.id collapses to canonical client_no (matches CD's TPECLIENT id field)
    assert h["client_no"] == "7610221"
    assert h["name"] == "TİM MİMARLIK DEKORASYON İNŞAAT TURİZM LİMİTED ŞİRKETİ"
    assert h["country"] == "TÜRKİYE"
    assert "id" not in h  # rename happened

    assert len(rec.designers) == 1
    assert rec.designers[0] == {"name": "ŞEBNEM SULTAN BUHARA GÜLEN"}

    # Designs: design_index -> no (str), product_name_tr -> product_name
    assert len(rec.designs) == 1
    des = rec.designs[0]
    assert des.no == "1"
    assert des.product_name == "Lamba"

    # Views: view_index -> view_no (str); embeddings/bbox/xref/page DROPPED
    assert len(des.views) == 1
    v = des.views[0]
    assert v.view_no == "1"
    assert v.image_path == "2024_007254/1_1.jpg"
    assert v.image_source == "pdf"


def test_normalize_pdf_record_drops_extraction_artefacts():
    """Pin the locked decision: bbox / image_xref / page / embeddings
    must NOT appear in the canonical view dict."""
    rec = normalize_pdf_record(_real_pdf_record_2024_007254())
    view_dict = asdict(rec.designs[0].views[0])
    assert set(view_dict.keys()) == {"view_no", "image_path", "image_source"}
    assert "bbox" not in view_dict
    assert "image_xref" not in view_dict
    assert "page" not in view_dict
    assert "embeddings" not in view_dict


def test_normalize_pdf_record_image_source_carries_through():
    """View image_source defaults to None when no image_path; otherwise
    preserves the value pdf_extract_tasarim wrote ("pdf" or "cd")."""
    record = _real_pdf_record_2024_007254()
    record["designs"][0]["views"][0]["image_source"] = "cd"
    rec = normalize_pdf_record(record)
    assert rec.designs[0].views[0].image_source == "cd"

    # If image_path is missing, image_source collapses to None even if set.
    record2 = _real_pdf_record_2024_007254()
    del record2["designs"][0]["views"][0]["image_path"]
    record2["designs"][0]["views"][0]["image_source"] = "pdf"
    rec2 = normalize_pdf_record(record2)
    assert rec2.designs[0].views[0].image_path is None
    assert rec2.designs[0].views[0].image_source is None


def test_normalize_pdf_record_hague_section():
    """Hague-section PDF record carries hague_reference; designs have no
    images. registration_no is the DM-style id we'll pair on later."""
    record = {
        "section": "hague",
        "record_index": 250,
        "registration_no": "DM 244882",
        "filing_date": "2024-02-15",
        "registration_date": "2024-02-15",
        "design_count": 1,
        "locarno_classes": ["11-01"],
        "applicants": [{"name": "RAYE ROCKS LLC", "id": "8022625", "country": "US"}],
        "designers": [{"name": "Erika Rayman"}],
        "attorney": {"name": "Sullivan Worcester"},
        "priorities": [],
        "designs": [{"design_index": 1, "product_name_tr": "Jewelry", "views": []}],
        "hague_reference": {
            "wipo_bulletin": "13/2025",
            "designated_states": ["CH", "DE", "TR", "US"],
            "product_name_en": "Jewelry for swim wear",
        },
        "page_range": [477, 477],
    }
    rec = normalize_pdf_record(record)
    assert rec.section == "hague"
    assert rec.application_no is None  # PDF Hague records carry no application_no
    assert rec.registration_no == "DM 244882"
    assert rec.hague_reference == {
        "wipo_bulletin": "13/2025",
        "designated_states": ["CH", "DE", "TR", "US"],
        "product_name_en": "Jewelry for swim wear",
    }


def test_normalize_pdf_record_deferred_publication():
    """Deferred-section record carries the deferred_publication block."""
    record = {
        "section": "deferred",
        "application_no": "2026/001807",
        "filing_date": "2026-01-15",
        "registration_date": "2026-01-15",
        "design_count": 2,
        "locarno_classes": ["06-01"],
        "applicants": [], "designers": [], "priorities": [],
        "designs": [],
        "deferred_publication": {"period_months": 30},
    }
    rec = normalize_pdf_record(record)
    assert rec.section == "deferred"
    assert rec.deferred_publication == {"period_months": 30}


def test_normalize_pdf_record_handles_missing_optional_blocks():
    """attorney / hague_reference / deferred_publication absent on most
    records; canonical record carries None for each."""
    record = {
        "section": "tr_native",
        "application_no": "2026/000001",
        "filing_date": "2026-01-01",
        "registration_date": "2026-01-01",
        "design_count": 1,
        "locarno_classes": [],
        "applicants": [], "designers": [], "priorities": [],
        "designs": [],
    }
    rec = normalize_pdf_record(record)
    assert rec.attorney is None
    assert rec.hague_reference is None
    assert rec.deferred_publication is None
    assert rec.page_range is None


def test_normalize_pdf_record_invalid_page_range_collapses_to_none():
    """page_range must be a 2-element int list — anything else -> None."""
    base = {
        "section": "tr_native",
        "application_no": "2026/000002",
        "filing_date": "2026-01-01",
        "registration_date": "2026-01-01",
        "design_count": 1,
        "locarno_classes": [],
        "applicants": [], "designers": [], "priorities": [], "designs": [],
    }
    for bad in ([17], [17, 18, 19], "17-18", None, [17, "18"]):
        rec = normalize_pdf_record({**base, "page_range": bad})
        assert rec.page_range is None
    rec = normalize_pdf_record({**base, "page_range": [17, 18]})
    assert rec.page_range == [17, 18]


def test_normalize_pdf_record_locarno_classes_renamed_to_codes():
    """Pin the rename: PDF.locarno_classes -> canonical.locarno_codes
    so the merged shape uses one name shared with CD."""
    record = {
        "section": "tr_native",
        "application_no": "2026/000003",
        "filing_date": "2026-01-01",
        "registration_date": "2026-01-01",
        "design_count": 1,
        "locarno_classes": ["12-16", "12-05"],
        "applicants": [], "designers": [], "priorities": [], "designs": [],
    }
    rec = normalize_pdf_record(record)
    assert rec.locarno_codes == ["12-16", "12-05"]

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
    MERGED_METADATA_FILENAME,
    dedupe_images_on_disk,
    load_cd_metadata,
    load_pdf_metadata,
    main,
    merge_records,
    normalize_cd_dossier,
    normalize_pdf_record,
    parse_argv,
    reconcile_metadata,
)
from pipeline.reconcile_tasarim import (
    _clean_str,
    _dmy_to_iso,
    _normalise_bulletin_no,
    _normalise_registration_no,
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


# ---------------------------------------------------------------------------
# Step 3.4 — _normalise_registration_no
# ---------------------------------------------------------------------------

def test_normalise_registration_no_collapses_whitespace():
    """PDF "DM 244882" and CD "DM244882" both collapse to "DM244882"."""
    assert _normalise_registration_no("DM 244882") == "DM244882"
    assert _normalise_registration_no("DM244882") == "DM244882"
    assert _normalise_registration_no("DM  244882") == "DM244882"  # multiple spaces


def test_normalise_registration_no_strip_and_uppercase():
    assert _normalise_registration_no("  DM 244882  ") == "DM244882"
    assert _normalise_registration_no("dm 244882") == "DM244882"


def test_normalise_registration_no_empty_inputs():
    assert _normalise_registration_no(None) is None
    assert _normalise_registration_no("") is None
    assert _normalise_registration_no("   ") is None
    assert _normalise_registration_no(42) is None  # not a string


# ---------------------------------------------------------------------------
# Step 3.4 — merge_records
# ---------------------------------------------------------------------------

def _cd_record(**overrides) -> CanonicalDesignRecord:
    """Lightweight CD-side record builder for merge tests."""
    base = dict(
        application_no="2016/01059",
        registration_no="2016 01059",
        application_date="2016-02-10",
        registration_date="2016-02-10",
        design_count=1,
        type="1",
        section=None,
        locarno_codes=["25-02"],
        attorney={"no": "12345", "name": "RABİA", "title": "Patent Vekili",
                   "address": "MECİDİYEKÖY"},
        holders=[{"client_no": "234974", "name": "BİRLİK", "country": "TÜRKİYE"}],
        designers=[{"no": "1", "name": "VEDAT"}],
        priorities=[],
        designs=[
            CanonicalDesign(
                no="1", product_name="Profil",
                views=[CanonicalDesignView(view_no="1",
                                            image_path="2016_01059/1_1.jpg",
                                            image_source="cd")],
            ),
        ],
        hague_reference=None,
        page_range=None,
        deferred_publication=None,
        source_format="CD",
    )
    base.update(overrides)
    return CanonicalDesignRecord(**base)


def _pdf_record(**overrides) -> CanonicalDesignRecord:
    """Lightweight PDF-side record builder for merge tests."""
    base = dict(
        application_no="2016/01059",
        registration_no="2016 01059",
        application_date="2016-02-10",
        registration_date="2016-02-10",
        design_count=1,
        type=None,
        section="tr_native",
        locarno_codes=["25-02"],
        attorney={"name": "IŞIK ÖZDOĞAN", "firm": "MOROĞLU ARSEVEN"},
        holders=[{"client_no": "234974", "name": "PDF-OCR-name"}],
        designers=[{"name": "VEDAT"}],
        priorities=[{"date": "2025-06-27", "number": "30/010,422", "country": "US"}],
        designs=[
            CanonicalDesign(
                no="1", product_name="Lamba (PDF noisy)",
                views=[CanonicalDesignView(view_no="1",
                                            image_path="2016_01059/1_1.jpg",
                                            image_source="pdf")],
            ),
        ],
        hague_reference=None,
        page_range=[17, 17],
        deferred_publication=None,
        source_format="PDF",
    )
    base.update(overrides)
    return CanonicalDesignRecord(**base)


def test_merge_records_cd_wins_on_overlap():
    """The headline rule: CD beats PDF for every overlapping scalar."""
    cd = _cd_record()
    pdf = _pdf_record()
    out = merge_records(cd, pdf)

    assert out.application_no == "2016/01059"
    assert out.registration_no == "2016 01059"
    assert out.application_date == "2016-02-10"
    assert out.design_count == 1
    assert out.locarno_codes == ["25-02"]
    assert out.designs[0].product_name == "Profil"  # CD wins, not "Lamba (PDF noisy)"
    assert out.designs[0].views[0].image_source == "cd"
    assert out.holders[0]["name"] == "BİRLİK"  # CD's clean name, not PDF-OCR-name
    assert out.source_format == "BOTH"


def test_merge_records_pdf_fills_gaps_when_cd_is_none():
    """When CD is missing/null/empty, PDF fills."""
    cd = _cd_record(
        application_no=None,
        application_date=None,
        registration_date=None,
        design_count=None,
        locarno_codes=[],
        attorney=None,
        holders=[],
        designers=[],
        designs=[],
    )
    pdf = _pdf_record(
        application_date="2024-09-06",
        application_no="2024/007254",
        design_count=4,
    )
    out = merge_records(cd, pdf)

    assert out.application_no == "2024/007254"
    assert out.application_date == "2024-09-06"
    assert out.design_count == 4
    assert out.locarno_codes == ["25-02"]  # from PDF (CD was [])
    assert len(out.holders) == 1            # from PDF (CD was [])
    assert out.holders[0]["name"] == "PDF-OCR-name"
    assert out.attorney is not None         # PDF's attorney kept


def test_merge_records_attorney_combines_cd_and_pdf_fields():
    """CD attorney has {no, name, title, address}; PDF has {name, firm}.
    Merged dict has all five fields, with CD's name winning on overlap."""
    out = merge_records(_cd_record(), _pdf_record())
    a = out.attorney
    assert a is not None
    assert a["name"] == "RABİA"           # CD wins
    assert a["title"] == "Patent Vekili"  # CD-only field
    assert a["no"] == "12345"             # CD-only field
    assert a["address"] == "MECİDİYEKÖY"  # CD-only field
    assert a["firm"] == "MOROĞLU ARSEVEN"  # PDF-only field, preserved


def test_merge_records_pdf_only_fields_preserved():
    """section / page_range / hague_reference / deferred_publication
    come from PDF unchanged."""
    cd = _cd_record()
    pdf = _pdf_record(
        section="tr_native",
        page_range=[17, 18],
        hague_reference={"wipo_bulletin": "13/2025"},
        deferred_publication={"period_months": 30},
    )
    out = merge_records(cd, pdf)
    assert out.section == "tr_native"
    assert out.page_range == [17, 18]
    assert out.hague_reference == {"wipo_bulletin": "13/2025"}
    assert out.deferred_publication == {"period_months": 30}


def test_merge_records_views_cd_wins_on_duplicate_view_no():
    """Same (design_no, view_no) on both sides -> CD wins. The PDF view's
    image_source ("pdf") is replaced by CD's "cd" tag so the consumer
    knows to look in cd_images/."""
    cd = _cd_record()
    pdf = _pdf_record()
    out = merge_records(cd, pdf)
    assert len(out.designs) == 1
    assert len(out.designs[0].views) == 1
    v = out.designs[0].views[0]
    assert v.view_no == "1"
    assert v.image_source == "cd"
    assert v.image_path == "2016_01059/1_1.jpg"


def test_merge_records_pdf_only_view_added_to_shared_design():
    """If CD's design has views {1} and PDF's has {1, 2}, the merged
    design carries views {1 (from CD), 2 (from PDF)}. Numeric sort."""
    cd = _cd_record()
    pdf = _pdf_record(designs=[
        CanonicalDesign(
            no="1", product_name="x",
            views=[
                CanonicalDesignView(view_no="1",
                                     image_path="2016_01059/1_1.jpg",
                                     image_source="pdf"),
                CanonicalDesignView(view_no="2",
                                     image_path="2016_01059/1_2.jpg",
                                     image_source="pdf"),
            ],
        ),
    ])
    out = merge_records(cd, pdf)
    views = out.designs[0].views
    assert [v.view_no for v in views] == ["1", "2"]
    assert views[0].image_source == "cd"   # CD's view 1 wins
    assert views[1].image_source == "pdf"  # PDF-only view 2


def test_merge_records_pdf_only_design_appended():
    """PDF has a design CD didn't ship -> appended with PDF's data."""
    cd = _cd_record(designs=[CanonicalDesign(no="1", product_name="A")])
    pdf = _pdf_record(designs=[
        CanonicalDesign(no="1", product_name="A-pdf"),
        CanonicalDesign(no="2", product_name="B-pdf"),
    ])
    out = merge_records(cd, pdf)
    assert [d.no for d in out.designs] == ["1", "2"]
    assert out.designs[0].product_name == "A"     # CD wins
    assert out.designs[1].product_name == "B-pdf"  # PDF-only


def test_merge_records_designs_sorted_numerically():
    """Design 10 comes after design 9, not after design 1 (lex sort
    would break this for multi-design dossiers >=10 designs)."""
    cd = _cd_record(designs=[
        CanonicalDesign(no="1", product_name="a"),
        CanonicalDesign(no="10", product_name="j"),
        CanonicalDesign(no="2", product_name="b"),
        CanonicalDesign(no="9", product_name="i"),
    ])
    pdf = _pdf_record(designs=[])
    out = merge_records(cd, pdf)
    assert [d.no for d in out.designs] == ["1", "2", "9", "10"]


def test_merge_records_priorities_pdf_only_field():
    """CD never carries priorities (IDDOSSIER has no such columns);
    PDF's priorities pass through to the merged record."""
    cd = _cd_record()
    pdf = _pdf_record(priorities=[
        {"date": "2025-06-27", "number": "30/010,422", "country": "US"},
    ])
    out = merge_records(cd, pdf)
    assert out.priorities == [
        {"date": "2025-06-27", "number": "30/010,422", "country": "US"},
    ]


def test_merge_records_source_format_is_BOTH():
    """Tag the merged record so downstream knows it came from a real pair."""
    out = merge_records(_cd_record(), _pdf_record())
    assert out.source_format == "BOTH"


# ---------------------------------------------------------------------------
# Step 3.5 — _normalise_bulletin_no + reconcile_metadata
# ---------------------------------------------------------------------------

def test_normalise_bulletin_no_handles_str_and_int():
    """CD ships str ('240'), PDF ships int (240) — both equal '240'."""
    assert _normalise_bulletin_no("240") == "240"
    assert _normalise_bulletin_no(240) == "240"
    assert _normalise_bulletin_no("  240  ") == "240"
    assert _normalise_bulletin_no(None) is None
    assert _normalise_bulletin_no("") is None


def test_reconcile_metadata_requires_at_least_one_doc():
    with pytest.raises(ValueError, match=r"requires at least one"):
        reconcile_metadata()


def test_reconcile_metadata_bulletin_no_mismatch_raises():
    cd = _minimal_cd_doc()
    cd["bulletin_no"] = "240"
    pdf = _minimal_pdf_doc()
    pdf["bulletin_no"] = 250
    with pytest.raises(ValueError, match=r"bulletin_no mismatch"):
        reconcile_metadata(cd_doc=cd, pdf_doc=pdf)


def test_reconcile_metadata_str_int_bulletin_compare_equal():
    """CD '240' (str) and PDF 240 (int) MUST not raise — they describe
    the same bulletin."""
    cd = _minimal_cd_doc()
    pdf = _minimal_pdf_doc()
    out = reconcile_metadata(cd_doc=cd, pdf_doc=pdf)
    assert out["bulletin_no"] == "240"
    assert out["records"] == []


def test_reconcile_metadata_cd_only_passes_records_through():
    """Single-side CD reconcile: every record carries source_format='CD'."""
    cd = _minimal_cd_doc()
    cd["dossiers"] = [_real_cd_dossier_2016_01059()]
    out = reconcile_metadata(cd_doc=cd)
    assert len(out["records"]) == 1
    assert out["records"][0]["source_format"] == "CD"
    assert out["records"][0]["application_no"] == "2016/01059"
    assert out["source_archive"] is None or out["source_archive"] == cd.get("source_archive")
    assert out["source_pdf"] is None
    assert out["stats"]["by_source_format"] == {"CD": 1, "PDF": 0, "BOTH": 0}


def test_reconcile_metadata_pdf_only_passes_records_through():
    pdf = _minimal_pdf_doc()
    pdf["records"] = [_real_pdf_record_2024_007254()]
    out = reconcile_metadata(pdf_doc=pdf)
    assert len(out["records"]) == 1
    assert out["records"][0]["source_format"] == "PDF"
    assert out["records"][0]["section"] == "tr_native"
    assert out["stats"]["by_source_format"] == {"CD": 0, "PDF": 1, "BOTH": 0}


def test_reconcile_metadata_pairs_tr_records_by_application_no():
    """The headline test: CD dossier and PDF record with the same
    application_no get merged into one BOTH-tagged record."""
    cd = _minimal_cd_doc()
    cd["dossiers"] = [_real_cd_dossier_2016_01059()]
    pdf = _minimal_pdf_doc()
    pdf_record = _real_pdf_record_2024_007254()
    pdf_record["application_no"] = "2016/01059"  # match the CD dossier
    pdf["records"] = [pdf_record]

    out = reconcile_metadata(cd_doc=cd, pdf_doc=pdf)
    assert len(out["records"]) == 1
    assert out["records"][0]["source_format"] == "BOTH"
    # CD wins on registration_no (CD ships "2016 01059", PDF "2024 007254"
    # for this fabricated test — CD's value is what survives the merge)
    assert out["records"][0]["registration_no"] == "2016 01059"


def test_reconcile_metadata_unmatched_records_kept_as_single_side():
    """CD records with no PDF counterpart stay as 'CD'; PDF records
    with no CD counterpart stay as 'PDF'. Mixed inputs allowed."""
    cd = _minimal_cd_doc()
    cd["dossiers"] = [_real_cd_dossier_2016_01059()]  # 2016/01059
    pdf = _minimal_pdf_doc()
    pdf["records"] = [_real_pdf_record_2024_007254()]  # 2024/007254

    out = reconcile_metadata(cd_doc=cd, pdf_doc=pdf)
    by_app = {r["application_no"]: r for r in out["records"]}
    assert by_app["2016/01059"]["source_format"] == "CD"
    assert by_app["2024/007254"]["source_format"] == "PDF"
    assert out["stats"]["by_source_format"] == {"CD": 1, "PDF": 1, "BOTH": 0}


def test_reconcile_metadata_pairs_hague_by_normalised_registration_no():
    """Hague pairing key: registration_no with whitespace/case removed.
    PDF 'DM 244882' and CD with register_no 'DM244882' must pair."""
    cd_dossier = {
        "application_no": "DM/244882",       # CD-side Hague form
        "application_date": "01.01.2024",
        "register_no": "DM244882",            # CD's Hague reg no without space
        "register_date": "01.01.2024",
        "design_count": "1",
        "type": "",
        "locarno_codes": ["11-01"],
        "holders": [],
        "designers": [],
        "designs": [{"no": "1", "product_name": "Jewelry", "views": []}],
    }
    pdf_record = {
        "section": "hague",
        "registration_no": "DM 244882",        # PDF-side Hague reg no with space
        "filing_date": "2024-02-15",
        "registration_date": "2024-02-15",
        "design_count": 1,
        "locarno_classes": ["11-01"],
        "applicants": [], "designers": [], "priorities": [],
        "designs": [{"design_index": 1, "product_name_tr": "Jewelry", "views": []}],
        "hague_reference": {"wipo_bulletin": "13/2025"},
    }
    cd_doc = _minimal_cd_doc()
    cd_doc["dossiers"] = [cd_dossier]
    pdf_doc = _minimal_pdf_doc()
    pdf_doc["records"] = [pdf_record]

    out = reconcile_metadata(cd_doc=cd_doc, pdf_doc=pdf_doc)
    assert len(out["records"]) == 1
    assert out["records"][0]["source_format"] == "BOTH"
    assert out["records"][0]["registration_no"] == "DM244882"   # CD won
    # PDF-only fields preserved
    assert out["records"][0]["section"] == "hague"
    assert out["records"][0]["hague_reference"] == {"wipo_bulletin": "13/2025"}


def test_reconcile_metadata_passes_through_cd_annotations():
    """CD annotations array goes verbatim into the merged doc as
    'cd_annotations' (separate top-level array per locked Q3 decision)."""
    cd = _minimal_cd_doc()
    cd["annotations"] = [
        {
            "publication_key": "262752", "application_no": "2011/01410",
            "request_type": "Yenileme",
            "content": "(11) 2011 01410 (15) 03.03.2011 ...",
        },
    ]
    out = reconcile_metadata(cd_doc=cd)
    assert out["cd_annotations"] == cd["annotations"]


def test_reconcile_metadata_no_cd_annotations_when_pdf_only():
    """No CD doc -> empty cd_annotations array."""
    pdf = _minimal_pdf_doc()
    out = reconcile_metadata(pdf_doc=pdf)
    assert out["cd_annotations"] == []


def test_reconcile_metadata_top_level_provenance_fields():
    """source_archive comes from CD doc, source_pdf from PDF doc."""
    cd = _minimal_cd_doc()
    cd["source_archive"] = "240_CD.rar"
    pdf = _minimal_pdf_doc()
    pdf["source"] = "bulletin.pdf"

    out = reconcile_metadata(cd_doc=cd, pdf_doc=pdf)
    assert out["source_archive"] == "240_CD.rar"
    assert out["source_pdf"] == "bulletin.pdf"
    assert "reconciled_at" in out and "T" in out["reconciled_at"]


def test_reconcile_metadata_records_sorted_deterministically():
    """Records are sorted by (application_no, registration_no) so
    re-running on identical input yields byte-for-byte identical output."""
    cd = _minimal_cd_doc()
    cd["dossiers"] = [
        {**_real_cd_dossier_2016_01059(), "application_no": "2016/05000"},
        {**_real_cd_dossier_2016_01059(), "application_no": "2016/01059"},
        {**_real_cd_dossier_2016_01059(), "application_no": "2016/03000"},
    ]
    out = reconcile_metadata(cd_doc=cd)
    apps = [r["application_no"] for r in out["records"]]
    assert apps == ["2016/01059", "2016/03000", "2016/05000"]


def test_reconcile_metadata_stats_aggregate_correctly():
    """Stats counters reflect the merged dataset, not the source docs."""
    cd = _minimal_cd_doc()
    cd["dossiers"] = [_real_cd_dossier_2016_01059()]  # 1 design, 1 view (cd)
    pdf = _minimal_pdf_doc()
    pdf["records"] = [_real_pdf_record_2024_007254()]  # 1 design, 1 view (pdf)

    out = reconcile_metadata(cd_doc=cd, pdf_doc=pdf)
    s = out["stats"]
    assert s["records"] == 2
    assert s["by_source_format"] == {"CD": 1, "PDF": 1, "BOTH": 0}
    assert s["designs_total"] == 2
    assert s["views_total"] == 2
    assert s["views_by_source"] == {"cd": 1, "pdf": 1, "none": 0}


# ---------------------------------------------------------------------------
# Step 3.6 — dedupe_images_on_disk
# ---------------------------------------------------------------------------

def test_dedupe_images_no_op_when_either_folder_missing(tmp_path):
    """No images/ or no cd_images/ -> no work to do."""
    out = dedupe_images_on_disk(tmp_path)
    assert out == {"unlinked": 0, "pdf_only_remaining": 0}

    (tmp_path / "cd_images").mkdir()
    out = dedupe_images_on_disk(tmp_path)
    assert out == {"unlinked": 0, "pdf_only_remaining": 0}


def test_dedupe_images_unlinks_pdf_duplicates(tmp_path):
    """Same key in both -> PDF copy unlinked, CD copy kept."""
    cd = tmp_path / "cd_images" / "2016_01059"
    cd.mkdir(parents=True)
    (cd / "1_1.jpg").write_bytes(b"CD")

    pdf = tmp_path / "images" / "2016_01059"
    pdf.mkdir(parents=True)
    (pdf / "1_1.jpg").write_bytes(b"PDF")

    out = dedupe_images_on_disk(tmp_path)
    assert out["unlinked"] == 1
    assert out["pdf_only_remaining"] == 0
    assert (cd / "1_1.jpg").read_bytes() == b"CD"  # CD survives
    assert not (pdf / "1_1.jpg").exists()           # PDF gone


def test_dedupe_images_keeps_pdf_only_files(tmp_path):
    """PDF views that aren't in cd_images/ stay where they are."""
    cd = tmp_path / "cd_images" / "2016_01059"
    cd.mkdir(parents=True)
    (cd / "1_1.jpg").write_bytes(b"")

    pdf = tmp_path / "images" / "2016_01059"
    pdf.mkdir(parents=True)
    (pdf / "1_1.jpg").write_bytes(b"")  # dup with CD
    (pdf / "1_2.jpg").write_bytes(b"")  # PDF-only — stays

    out = dedupe_images_on_disk(tmp_path)
    assert out["unlinked"] == 1
    assert out["pdf_only_remaining"] == 1
    assert (pdf / "1_2.jpg").exists()


def test_dedupe_images_multi_application(tmp_path):
    """Dedup walks every {appno}/ subfolder under cd_images/."""
    for app in ("2016_01059", "2016_01205", "2015_06749"):
        cd = tmp_path / "cd_images" / app
        cd.mkdir(parents=True)
        (cd / "1_1.jpg").write_bytes(b"")
        pdf = tmp_path / "images" / app
        pdf.mkdir(parents=True)
        (pdf / "1_1.jpg").write_bytes(b"")

    out = dedupe_images_on_disk(tmp_path)
    assert out["unlinked"] == 3
    assert out["pdf_only_remaining"] == 0


def test_dedupe_images_no_unlinks_when_no_overlap(tmp_path):
    """Different keys on each side -> nothing to unlink, all PDF kept."""
    (tmp_path / "cd_images" / "2016_01059").mkdir(parents=True)
    (tmp_path / "cd_images" / "2016_01059" / "1_1.jpg").write_bytes(b"")
    (tmp_path / "images" / "2024_007254").mkdir(parents=True)
    (tmp_path / "images" / "2024_007254" / "1_1.jpg").write_bytes(b"")

    out = dedupe_images_on_disk(tmp_path)
    assert out["unlinked"] == 0
    assert out["pdf_only_remaining"] == 1


def test_dedupe_images_handles_str_path(tmp_path):
    """Accepts str path as well as Path."""
    out = dedupe_images_on_disk(str(tmp_path))
    assert isinstance(out, dict)


# ---------------------------------------------------------------------------
# Step 3.7 — CLI (parse_argv + main)
# ---------------------------------------------------------------------------

def _seed_issue_folder(tmp_path: Path, name: str = "TS_240_2016-03-09",
                       *, with_cd: bool = True, with_pdf: bool = False) -> Path:
    """Build a TS issue folder for CLI tests."""
    folder = tmp_path / name
    folder.mkdir()
    if with_cd:
        cd = _minimal_cd_doc()
        cd["dossiers"] = [_real_cd_dossier_2016_01059()]
        (folder / "cd_metadata.json").write_text(
            json.dumps(cd, ensure_ascii=False), encoding="utf-8",
        )
    if with_pdf:
        pdf = _minimal_pdf_doc()
        pdf["records"] = [_real_pdf_record_2024_007254()]
        (folder / "metadata.json").write_text(
            json.dumps(pdf, ensure_ascii=False), encoding="utf-8",
        )
    return folder


def test_parse_argv_issue_and_all_mutually_exclusive(tmp_path, capsys):
    folder = _seed_issue_folder(tmp_path)
    with pytest.raises(SystemExit):
        parse_argv([
            "--issue", folder.name,
            "--all",
            "--bulletins-root", str(tmp_path),
        ])
    assert "mutually exclusive" in capsys.readouterr().err


def test_parse_argv_requires_issue_or_all(capsys, tmp_path):
    with pytest.raises(SystemExit):
        parse_argv(["--bulletins-root", str(tmp_path)])
    assert "provide --issue or --all" in capsys.readouterr().err


def test_parse_argv_all_with_no_ts_folders_errors(capsys, tmp_path):
    with pytest.raises(SystemExit):
        parse_argv(["--all", "--bulletins-root", str(tmp_path)])
    assert "no TS_* folders" in capsys.readouterr().err


def test_parse_argv_all_collects_ts_folders(tmp_path):
    _seed_issue_folder(tmp_path, "TS_240_2016-03-09")
    _seed_issue_folder(tmp_path, "TS_241_2016-03-24")
    (tmp_path / "not_a_ts_folder").mkdir()
    args = parse_argv(["--all", "--bulletins-root", str(tmp_path)])
    assert sorted(p.name for p in args.issue_folders) == [
        "TS_240_2016-03-09", "TS_241_2016-03-24",
    ]


def test_parse_argv_issue_resolves_subfolder(tmp_path):
    _seed_issue_folder(tmp_path, "TS_240_2016-03-09")
    args = parse_argv([
        "--issue", "TS_240_2016-03-09",
        "--bulletins-root", str(tmp_path),
    ])
    assert len(args.issue_folders) == 1
    assert args.issue_folders[0].name == "TS_240_2016-03-09"


def test_parse_argv_unknown_issue_errors(capsys, tmp_path):
    with pytest.raises(SystemExit):
        parse_argv([
            "--issue", "TS_999_2099-01-01",
            "--bulletins-root", str(tmp_path),
        ])
    assert "issue folder not found" in capsys.readouterr().err


def test_main_writes_merged_metadata_for_cd_only(tmp_path):
    folder = _seed_issue_folder(tmp_path, with_cd=True, with_pdf=False)
    rc = main(["--issue", folder.name, "--bulletins-root", str(tmp_path)])
    assert rc == 0
    out = json.loads((folder / MERGED_METADATA_FILENAME).read_text(encoding="utf-8"))
    assert out["bulletin_no"] == "240"
    assert len(out["records"]) == 1
    assert out["records"][0]["source_format"] == "CD"


def test_main_writes_merged_metadata_for_pdf_only(tmp_path):
    folder = _seed_issue_folder(tmp_path, with_cd=False, with_pdf=True)
    rc = main(["--issue", folder.name, "--bulletins-root", str(tmp_path)])
    assert rc == 0
    out = json.loads((folder / MERGED_METADATA_FILENAME).read_text(encoding="utf-8"))
    assert len(out["records"]) == 1
    assert out["records"][0]["source_format"] == "PDF"


def test_main_writes_merged_metadata_for_both(tmp_path):
    """Real reconcile path: both CD and PDF present, application_no
    matches across them, expect a BOTH-tagged merged record."""
    folder = tmp_path / "TS_240_2016-03-09"
    folder.mkdir()
    cd = _minimal_cd_doc()
    cd["dossiers"] = [_real_cd_dossier_2016_01059()]
    (folder / "cd_metadata.json").write_text(
        json.dumps(cd, ensure_ascii=False), encoding="utf-8",
    )
    pdf = _minimal_pdf_doc()
    pdf_record = _real_pdf_record_2024_007254()
    pdf_record["application_no"] = "2016/01059"   # match the CD dossier
    pdf["records"] = [pdf_record]
    (folder / "metadata.json").write_text(
        json.dumps(pdf, ensure_ascii=False), encoding="utf-8",
    )

    rc = main(["--issue", folder.name, "--bulletins-root", str(tmp_path)])
    assert rc == 0
    out = json.loads((folder / MERGED_METADATA_FILENAME).read_text(encoding="utf-8"))
    assert len(out["records"]) == 1
    assert out["records"][0]["source_format"] == "BOTH"


def test_main_skips_when_no_source_files(tmp_path):
    """An empty TS folder with no metadata.json or cd_metadata.json:
    skip with warning, return 0."""
    folder = tmp_path / "TS_999_2099-01-01"
    folder.mkdir()
    rc = main(["--issue", folder.name, "--bulletins-root", str(tmp_path)])
    assert rc == 0
    assert not (folder / MERGED_METADATA_FILENAME).exists()


def test_main_skips_existing_without_force(tmp_path):
    folder = _seed_issue_folder(tmp_path)
    (folder / MERGED_METADATA_FILENAME).write_text(
        '{"original":"keepme"}', encoding="utf-8",
    )
    rc = main(["--issue", folder.name, "--bulletins-root", str(tmp_path)])
    assert rc == 0
    # Original file preserved
    assert json.loads(
        (folder / MERGED_METADATA_FILENAME).read_text(encoding="utf-8")
    ) == {"original": "keepme"}


def test_main_force_overwrites_existing(tmp_path):
    folder = _seed_issue_folder(tmp_path)
    (folder / MERGED_METADATA_FILENAME).write_text(
        '{"original":"replaceme"}', encoding="utf-8",
    )
    rc = main([
        "--issue", folder.name,
        "--bulletins-root", str(tmp_path),
        "--force",
    ])
    assert rc == 0
    out = json.loads((folder / MERGED_METADATA_FILENAME).read_text(encoding="utf-8"))
    assert out["bulletin_no"] == "240"


def test_main_returns_1_on_bulletin_no_mismatch(tmp_path):
    """A mismatched bulletin_no across the two source files surfaces as
    a per-folder failure (caught + reported, rc=1)."""
    folder = tmp_path / "TS_240_2016-03-09"
    folder.mkdir()
    cd = _minimal_cd_doc()
    cd["bulletin_no"] = "240"
    pdf = _minimal_pdf_doc()
    pdf["bulletin_no"] = 999  # different bulletin
    (folder / "cd_metadata.json").write_text(
        json.dumps(cd, ensure_ascii=False), encoding="utf-8",
    )
    (folder / "metadata.json").write_text(
        json.dumps(pdf, ensure_ascii=False), encoding="utf-8",
    )

    rc = main(["--issue", folder.name, "--bulletins-root", str(tmp_path)])
    assert rc == 1


def test_main_all_processes_every_ts_folder(tmp_path):
    _seed_issue_folder(tmp_path, "TS_240_2016-03-09")
    _seed_issue_folder(tmp_path, "TS_241_2016-03-24")
    rc = main(["--all", "--bulletins-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "TS_240_2016-03-09" / MERGED_METADATA_FILENAME).is_file()
    assert (tmp_path / "TS_241_2016-03-24" / MERGED_METADATA_FILENAME).is_file()


def test_main_dedupe_images_runs_when_flag_set(tmp_path):
    """--dedupe-images triggers the disk dedup pass after writing JSON."""
    folder = _seed_issue_folder(tmp_path)

    # Set up a dedup-able state: same key in both folders
    cd_dir = folder / "cd_images" / "2016_01059"
    cd_dir.mkdir(parents=True)
    (cd_dir / "1_1.jpg").write_bytes(b"CD")

    pdf_dir = folder / "images" / "2016_01059"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "1_1.jpg").write_bytes(b"PDF")

    rc = main([
        "--issue", folder.name,
        "--bulletins-root", str(tmp_path),
        "--dedupe-images",
    ])
    assert rc == 0
    # PDF dup gone, CD copy survives
    assert not (pdf_dir / "1_1.jpg").exists()
    assert (cd_dir / "1_1.jpg").read_bytes() == b"CD"

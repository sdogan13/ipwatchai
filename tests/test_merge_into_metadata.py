"""Unit tests for ``pipeline.merge_into_metadata``.

Built one helper at a time. Each step adds its own test block.
"""

from __future__ import annotations

import pytest

from pipeline.merge_into_metadata import (
    merge_pdf_record_with_cd_dossier,
    merge_to_pdf_shape,
    synthesize_cd_record_in_pdf_shape,
)
from pipeline.merge_into_metadata import (
    _attach_existing_embeddings,
    _cd_attorney_to_pdf_attorney,
    _cd_design_to_pdf_design,
    _cd_designer_to_pdf_designer,
    _cd_holder_to_pdf_applicant,
    _cd_view_to_pdf_view,
    _coerce_bulletin_no,
    _index_existing_embeddings,
    _merge_design_views,
    _merge_designs,
)


# ---------------------------------------------------------------------------
# _cd_holder_to_pdf_applicant
# ---------------------------------------------------------------------------

def test_cd_holder_to_pdf_applicant_real_row():
    """Real-data row from 240_CD.rar IDHOLDER. CD's 'title' becomes
    canonical 'name'; CD's 'client_no' becomes PDF's 'id' (TPECLIENT)."""
    cd = {
        "client_no": "234974",
        "title": "BİRLİK MENFEZ HAV. EKİP. SANAYİ TİCARET LİMİTED ŞİRKETİ",
        "address": "Organize San. Böl.",
        "city": "İSTANBUL",
        "country": "TÜRKİYE",
    }
    pdf = _cd_holder_to_pdf_applicant(cd)
    assert pdf == {
        "name": "BİRLİK MENFEZ HAV. EKİP. SANAYİ TİCARET LİMİTED ŞİRKETİ",
        "id": "234974",
        "address": "Organize San. Böl., İSTANBUL",
        "country": "TÜRKİYE",
    }


def test_cd_holder_to_pdf_applicant_address_without_city():
    """City absent -> address has no comma suffix."""
    pdf = _cd_holder_to_pdf_applicant({
        "title": "X", "client_no": "1", "address": "Some street", "country": "TR",
    })
    assert pdf["address"] == "Some street"


def test_cd_holder_to_pdf_applicant_empty_fields_collapse_to_none():
    """All-empty fields produce None entries (not empty strings) so the
    output dict can be filtered by 'name or id' in the synthesizer."""
    pdf = _cd_holder_to_pdf_applicant({
        "title": "", "client_no": "", "address": "", "city": "", "country": "",
    })
    assert pdf["name"] is None
    assert pdf["id"] is None
    assert pdf["address"] is None
    assert pdf["country"] is None


def test_cd_holder_to_pdf_applicant_non_dict_input():
    assert _cd_holder_to_pdf_applicant(None) == {}
    assert _cd_holder_to_pdf_applicant("string") == {}


# ---------------------------------------------------------------------------
# _cd_designer_to_pdf_designer
# ---------------------------------------------------------------------------

def test_cd_designer_to_pdf_designer_real_row():
    """PDF designer is name-only; CD's address+country are dropped (they
    have no PDF column at the design-record level)."""
    cd = {"no": "1", "name": "VEDAT ÇELİK",
           "address": "Enverpaşa Cad.", "country": "TÜRKİYE"}
    assert _cd_designer_to_pdf_designer(cd) == {"name": "VEDAT ÇELİK"}


def test_cd_designer_to_pdf_designer_empty_name():
    assert _cd_designer_to_pdf_designer({"no": "1", "name": ""}) == {"name": None}


# ---------------------------------------------------------------------------
# _cd_attorney_to_pdf_attorney
# ---------------------------------------------------------------------------

def test_cd_attorney_to_pdf_attorney_real_row():
    """CD attorney has no/name/title/address; PDF has just name+firm.
    Synthesis keeps name + null firm (CD doesn't pre-split firm out;
    the BOTH-merge case is where PDF's clean (name, firm) wins)."""
    cd = {"no": "12345", "name": "RABİA ÇETİN (DEV PATENT MARKA)",
           "title": "Patent Vekili", "address": "MECİDİYEKÖY"}
    pdf = _cd_attorney_to_pdf_attorney(cd)
    assert pdf == {"name": "RABİA ÇETİN (DEV PATENT MARKA)", "firm": None}


def test_cd_attorney_to_pdf_attorney_empty_returns_none():
    """All-empty CD attorney block -> None so the record doesn't carry
    a cosmetic empty attorney."""
    assert _cd_attorney_to_pdf_attorney({
        "no": "", "name": "", "title": "", "address": "",
    }) is None
    assert _cd_attorney_to_pdf_attorney(None) is None
    assert _cd_attorney_to_pdf_attorney("string") is None


# ---------------------------------------------------------------------------
# _cd_view_to_pdf_view
# ---------------------------------------------------------------------------

def test_cd_view_to_pdf_view_canonical_key_carries_through():
    """The canonical image_path + image_source='cd' must survive the
    translation so D.1's resolve_view_image_path keeps working."""
    cd = {"view_no": "1", "image_path": "2016_01059/1_1.jpg"}
    pdf = _cd_view_to_pdf_view(cd)
    assert pdf == {
        "view_index": 1,
        "page": None,
        "image_xref": None,
        "bbox": None,
        "image_path": "2016_01059/1_1.jpg",
        "image_source": "cd",
    }


def test_cd_view_to_pdf_view_no_image_path():
    """Hague-style view with no image: image_source stays None."""
    pdf = _cd_view_to_pdf_view({"view_no": "1", "image_path": ""})
    assert pdf["image_path"] is None
    assert pdf["image_source"] is None


def test_cd_view_to_pdf_view_view_no_cast_to_int():
    """CD ships view_no as str; PDF wants int."""
    assert _cd_view_to_pdf_view({"view_no": "12", "image_path": "x.jpg"})["view_index"] == 12


# ---------------------------------------------------------------------------
# _cd_design_to_pdf_design
# ---------------------------------------------------------------------------

def test_cd_design_to_pdf_design_real_row():
    """Field renames: no -> design_index (int), product_name -> product_name_tr.
    Trailing whitespace in product_name (verbatim from IDDESIGN.PRODUCTNAME)
    is stripped."""
    cd = {
        "no": "1",
        "product_name": "Profil ",  # trailing space verbatim
        "views": [
            {"view_no": "1", "image_path": "2016_01059/1_1.jpg"},
            {"view_no": "2", "image_path": "2016_01059/1_2.jpg"},
        ],
    }
    pdf = _cd_design_to_pdf_design(cd)
    assert pdf["design_index"] == 1
    assert pdf["product_name_tr"] == "Profil"  # trimmed
    assert len(pdf["views"]) == 2
    assert pdf["views"][0]["view_index"] == 1
    assert pdf["views"][0]["image_source"] == "cd"


def test_cd_design_to_pdf_design_no_views():
    """Hague designs: no views, but design entry still emitted."""
    pdf = _cd_design_to_pdf_design({"no": "1", "product_name": "Hague", "views": []})
    assert pdf["views"] == []


# ---------------------------------------------------------------------------
# synthesize_cd_record_in_pdf_shape
# ---------------------------------------------------------------------------

def _real_cd_dossier_2016_01059() -> dict:
    """Real CD dossier shape from 240_CD.rar, application 2016/01059."""
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
            "product_name": "Profil ",
            "views": [{"view_no": "1", "image_path": "2016_01059/1_1.jpg"}],
        }],
    }


def test_synthesize_cd_record_in_pdf_shape_full_shape_tr():
    """End-to-end on a real Turkish CD dossier; output shape matches what
    pdf_extract_tasarim would have produced for the same record."""
    rec = synthesize_cd_record_in_pdf_shape(_real_cd_dossier_2016_01059(),
                                             record_index=42)
    assert rec["section"] == "tr_native"
    assert rec["record_index"] == 42
    assert rec["application_no"] == "2016/01059"
    assert rec["registration_no"] == "2016 01059"
    assert rec["filing_date"] == "2016-02-10"          # ISO from DD.MM.YYYY
    assert rec["registration_date"] == "2016-02-10"
    assert rec["design_count"] == 1                    # str -> int
    assert rec["locarno_classes"] == ["25-02"]         # locarno_codes renamed
    assert rec["priorities"] == []                     # CD has none
    assert rec["page_range"] == []                     # no PDF page knowledge

    # Applicant: title->name, client_no->id, address+city joined
    assert len(rec["applicants"]) == 1
    a = rec["applicants"][0]
    assert a["name"] == "BİRLİK MENFEZ HAV. EKİP. SANAYİ TİCARET LİMİTED ŞİRKETİ"
    assert a["id"] == "234974"
    assert a["address"] == "Organize San. Böl., İSTANBUL"
    assert a["country"] == "TÜRKİYE"

    # Designer: name only
    assert rec["designers"] == [{"name": "VEDAT ÇELİK"}]

    # Attorney: CD's name preserved, firm null
    assert rec["attorney"]["name"].startswith("RABİA")
    assert rec["attorney"]["firm"] is None

    # Designs: CD's no->design_index, product_name->product_name_tr, view tagged cd
    assert len(rec["designs"]) == 1
    d = rec["designs"][0]
    assert d["design_index"] == 1
    assert d["product_name_tr"] == "Profil"
    assert len(d["views"]) == 1
    assert d["views"][0] == {
        "view_index": 1, "page": None, "image_xref": None, "bbox": None,
        "image_path": "2016_01059/1_1.jpg", "image_source": "cd",
    }


def test_synthesize_cd_record_in_pdf_shape_hague_section():
    """Hague application_no (DM/...) -> section='hague'."""
    cd = {
        "application_no": "DM/086402",
        "application_date": "01.01.2016",
        "register_no": "DM086402",
        "register_date": "01.01.2016",
        "design_count": "1",
        "locarno_codes": ["21-02"],
        "holders": [],
        "designers": [],
        "designs": [{
            "no": "1",
            "product_name": "Jewelry for swim wear",
            "views": [],   # Hague has no images on CD
        }],
    }
    rec = synthesize_cd_record_in_pdf_shape(cd, record_index=1)
    assert rec["section"] == "hague"
    assert rec["application_no"] == "DM/086402"
    assert rec["registration_no"] == "DM086402"
    assert rec["designs"][0]["views"] == []


def test_synthesize_cd_record_drops_empty_applicants_and_designers():
    """All-empty applicants/designers entries are filtered out so the
    record doesn't carry rows that resolve_holder_id would have to
    drop anyway."""
    cd = {
        "application_no": "2016/00001",
        "application_date": "01.01.2016",
        "register_no": "",
        "register_date": "01.01.2016",
        "design_count": "1",
        "locarno_codes": [],
        "holders": [
            {"client_no": "1", "title": "REAL", "country": "TR"},
            {"client_no": "", "title": "", "country": ""},  # all empty
        ],
        "designers": [
            {"name": "REAL"},
            {"name": ""},  # empty
        ],
        "designs": [],
    }
    rec = synthesize_cd_record_in_pdf_shape(cd, record_index=1)
    assert len(rec["applicants"]) == 1
    assert rec["applicants"][0]["name"] == "REAL"
    assert len(rec["designers"]) == 1
    assert rec["designers"][0]["name"] == "REAL"


def test_synthesize_cd_record_design_count_fallback():
    """Empty design_count string falls back to 1 (matches PDF default)."""
    cd = {
        "application_no": "2016/00002",
        "application_date": "01.01.2016",
        "register_no": "",
        "register_date": "01.01.2016",
        "design_count": "",
        "locarno_codes": [],
        "holders": [],
        "designers": [],
        "designs": [],
    }
    rec = synthesize_cd_record_in_pdf_shape(cd, record_index=1)
    assert rec["design_count"] == 1


def test_synthesize_cd_record_record_index_preserved():
    """Caller picks the record_index (so the doc-level numbering stays
    consistent across PDF + CD-only mix)."""
    cd = {"application_no": "2016/00001", "application_date": "01.01.2016",
          "register_no": "", "register_date": "01.01.2016", "design_count": "1",
          "locarno_codes": [], "holders": [], "designers": [], "designs": []}
    for idx in (1, 100, 9999):
        rec = synthesize_cd_record_in_pdf_shape(cd, record_index=idx)
        assert rec["record_index"] == idx


# ---------------------------------------------------------------------------
# _coerce_bulletin_no
# ---------------------------------------------------------------------------

def test_coerce_bulletin_no_str_to_int():
    """CD ships bulletin_no as str ('240'); PDF as int (240). Coerce
    digit-string to int so the merged top-level field is consistent."""
    assert _coerce_bulletin_no("240") == 240
    assert _coerce_bulletin_no("  240  ") == 240
    assert _coerce_bulletin_no(240) == 240


def test_coerce_bulletin_no_passes_unparseable_through():
    """Non-digit values pass through (e.g. 'NULL' or weird formats)."""
    assert _coerce_bulletin_no("2025/8") == "2025/8"
    assert _coerce_bulletin_no(None) is None


# ---------------------------------------------------------------------------
# _merge_design_views
# ---------------------------------------------------------------------------

def test_merge_design_views_cd_wins_on_duplicate_view_index():
    pdf = [{"view_index": 1, "page": 17, "image_path": "x/1_1.jpg",
            "image_source": "pdf"}]
    cd = [{"view_no": "1", "image_path": "x/1_1.jpg"}]
    out = _merge_design_views(pdf, cd)
    assert len(out) == 1
    assert out[0]["image_source"] == "cd"
    assert out[0]["page"] is None  # CD-shape clears page


def test_merge_design_views_pdf_only_kept():
    pdf = [{"view_index": 2, "image_path": "x/1_2.jpg", "image_source": "pdf"}]
    cd = [{"view_no": "1", "image_path": "x/1_1.jpg"}]
    out = _merge_design_views(pdf, cd)
    indices = sorted(v["view_index"] for v in out)
    assert indices == [1, 2]


def test_merge_design_views_sorted_numerically():
    pdf = [{"view_index": 10, "image_path": "x"}]
    cd = [{"view_no": "1", "image_path": "y"}, {"view_no": "9", "image_path": "z"}]
    out = _merge_design_views(pdf, cd)
    assert [v["view_index"] for v in out] == [1, 9, 10]


# ---------------------------------------------------------------------------
# _merge_designs
# ---------------------------------------------------------------------------

def test_merge_designs_cd_product_name_wins():
    """CD's IDDESIGN.PRODUCTNAME is the authoritative HSQLDB value;
    overrides PDF's regex parse on shared design_index."""
    pdf = [{"design_index": 1, "product_name_tr": "Lamba (PDF noisy)",
            "views": [{"view_index": 1, "image_path": "x/1_1.jpg",
                       "image_source": "pdf"}]}]
    cd = [{"no": "1", "product_name": "Lamba",
            "views": [{"view_no": "1", "image_path": "x/1_1.jpg"}]}]
    out = _merge_designs(pdf, cd)
    assert out[0]["product_name_tr"] == "Lamba"
    assert out[0]["views"][0]["image_source"] == "cd"


def test_merge_designs_cd_only_design_translated_and_appended():
    pdf = [{"design_index": 1, "product_name_tr": "A", "views": []}]
    cd = [{"no": "2", "product_name": "B", "views": []}]
    out = _merge_designs(pdf, cd)
    assert [d["design_index"] for d in out] == [1, 2]
    assert out[1]["product_name_tr"] == "B"


def test_merge_designs_pdf_only_design_kept():
    pdf = [{"design_index": 1, "product_name_tr": "PDF-only",
            "views": [{"view_index": 1, "image_path": "x"}]}]
    cd = []
    out = _merge_designs(pdf, cd)
    assert len(out) == 1
    assert out[0]["product_name_tr"] == "PDF-only"


# ---------------------------------------------------------------------------
# merge_pdf_record_with_cd_dossier
# ---------------------------------------------------------------------------

def _real_pdf_record_2016_01059() -> dict:
    """A PDF-shape record matching the CD dossier test fixture."""
    return {
        "section": "tr_native",
        "record_index": 1,
        "application_no": "2016/01059",
        "registration_no": "PDF guess",
        "filing_date": "2016-02-09",
        "registration_date": "2016-02-09",
        "design_count": 1,
        "locarno_classes": ["25-99"],   # PDF noisy parse
        "applicants": [{"name": "PDF noisy", "id": "234974"}],
        "designers": [{"name": "PDF noisy"}],
        "attorney": {"name": "PDF NAME", "firm": "MOROĞLU ARSEVEN"},
        "priorities": [{"date": "2016-01-01", "number": "X", "country": "TR"}],
        "designs": [{
            "design_index": 1,
            "product_name_tr": "Profil (PDF noisy)",
            "views": [{
                "view_index": 1, "page": 17, "image_xref": 156,
                "bbox": [1.0, 2.0, 3.0, 4.0],
                "image_path": "2016_01059/1_1.jpg",
                "image_source": "pdf",
            }],
        }],
        "page_range": [17, 17],
    }


def test_merge_pdf_record_cd_wins_overlapping_scalars():
    pdf = _real_pdf_record_2016_01059()
    cd = _real_cd_dossier_2016_01059()
    merged = merge_pdf_record_with_cd_dossier(pdf, cd)
    # CD wins: registration_no, filing_date, design_count, locarno
    assert merged["registration_no"] == "2016 01059"   # CD
    assert merged["filing_date"] == "2016-02-10"        # CD (was 09 in PDF)
    assert merged["registration_date"] == "2016-02-10"
    assert merged["design_count"] == 1
    assert merged["locarno_classes"] == ["25-02"]       # CD's clean
    # CD wins: applicants, designers
    assert merged["applicants"][0]["name"].startswith("BİRLİK")
    assert merged["designers"][0]["name"] == "VEDAT ÇELİK"


def test_merge_pdf_record_attorney_combines_cd_name_and_pdf_firm():
    """CD's clean name + PDF's pre-split firm. Best of both worlds."""
    pdf = _real_pdf_record_2016_01059()
    cd = _real_cd_dossier_2016_01059()
    merged = merge_pdf_record_with_cd_dossier(pdf, cd)
    a = merged["attorney"]
    assert a["name"].startswith("RABİA ÇETİN")  # CD wins on name
    assert a["firm"] == "MOROĞLU ARSEVEN"        # PDF's firm preserved


def test_merge_pdf_record_keeps_pdf_only_fields():
    """PDF-only fields (page_range, priorities, section, record_index)
    are preserved verbatim — CD has no equivalent for these."""
    pdf = _real_pdf_record_2016_01059()
    cd = _real_cd_dossier_2016_01059()
    merged = merge_pdf_record_with_cd_dossier(pdf, cd)
    assert merged["section"] == "tr_native"
    assert merged["record_index"] == 1
    assert merged["page_range"] == [17, 17]
    assert merged["priorities"] == [{"date": "2016-01-01", "number": "X", "country": "TR"}]


def test_merge_pdf_record_view_image_source_flips_to_cd():
    """When CD has the view, the merged view carries image_source='cd'
    so embeddings_tasarim resolves under cd_images/."""
    pdf = _real_pdf_record_2016_01059()
    cd = _real_cd_dossier_2016_01059()
    merged = merge_pdf_record_with_cd_dossier(pdf, cd)
    v = merged["designs"][0]["views"][0]
    assert v["image_source"] == "cd"
    assert v["image_path"] == "2016_01059/1_1.jpg"


def test_merge_pdf_record_no_cd_fields_keeps_pdf():
    """Empty CD dossier overlays nothing — PDF survives unchanged."""
    pdf = _real_pdf_record_2016_01059()
    empty_cd = {"application_no": "2016/01059", "register_no": "",
                 "application_date": "", "register_date": "", "design_count": "",
                 "locarno_codes": [], "holders": [], "designers": [],
                 "attorney": None, "designs": []}
    merged = merge_pdf_record_with_cd_dossier(pdf, empty_cd)
    assert merged["registration_no"] == "PDF guess"
    assert merged["locarno_classes"] == ["25-99"]
    assert merged["applicants"][0]["name"] == "PDF noisy"


# ---------------------------------------------------------------------------
# merge_to_pdf_shape
# ---------------------------------------------------------------------------

def _minimal_pdf_doc() -> dict:
    return {
        "bulletin_no": 240,
        "bulletin_date": "2016-03-09",
        "source": "bulletin.pdf",
        "page_count": 100,
        "record_count": 0,
        "records": [],
    }


def _minimal_cd_doc() -> dict:
    return {
        "bulletin_no": "240",
        "bulletin_date": "2016-03-09",
        "source_archive": "240_CD.rar",
        "stats": {"dossiers": 0},
        "dossiers": [],
        "annotations": [],
    }


def test_merge_to_pdf_shape_requires_at_least_one():
    with pytest.raises(ValueError, match="requires at least one"):
        merge_to_pdf_shape()


def test_merge_to_pdf_shape_pdf_only_passthrough():
    pdf = _minimal_pdf_doc()
    pdf["records"] = [_real_pdf_record_2016_01059()]
    out = merge_to_pdf_shape(pdf_doc=pdf)
    assert out["merge_source"] == "pdf_only"
    assert "merged_at" in out
    assert len(out["records"]) == 1
    assert out["records"][0]["application_no"] == "2016/01059"


def test_merge_to_pdf_shape_cd_only_synthesizes_records():
    """CD-only folder case: synthesize PDF-shape records from CD dossiers."""
    cd = _minimal_cd_doc()
    cd["dossiers"] = [_real_cd_dossier_2016_01059()]
    out = merge_to_pdf_shape(cd_doc=cd)
    assert out["merge_source"] == "cd_only"
    assert out["bulletin_no"] == 240               # str -> int coerced
    assert out["source"] == "240_CD.rar"
    assert out["page_count"] == 0
    assert len(out["records"]) == 1
    assert out["records"][0]["application_no"] == "2016/01059"
    assert out["records"][0]["section"] == "tr_native"


def test_merge_to_pdf_shape_both_pairs_by_application_no():
    """PDF and CD with same application_no -> merged record (CD wins)."""
    pdf = _minimal_pdf_doc()
    pdf["records"] = [_real_pdf_record_2016_01059()]
    cd = _minimal_cd_doc()
    cd["dossiers"] = [_real_cd_dossier_2016_01059()]
    out = merge_to_pdf_shape(pdf_doc=pdf, cd_doc=cd)
    assert out["merge_source"] == "both"
    assert len(out["records"]) == 1
    # CD wins on registration_no
    assert out["records"][0]["registration_no"] == "2016 01059"


def test_merge_to_pdf_shape_unmatched_cd_dossiers_appended():
    """CD has a dossier the PDF doesn't ship -> appended as synthesized
    record with its own record_index."""
    pdf = _minimal_pdf_doc()
    pdf["records"] = [_real_pdf_record_2016_01059()]
    cd = _minimal_cd_doc()
    cd["dossiers"] = [
        _real_cd_dossier_2016_01059(),
        {**_real_cd_dossier_2016_01059(), "application_no": "2016/99999"},
    ]
    out = merge_to_pdf_shape(pdf_doc=pdf, cd_doc=cd)
    apps = sorted(r["application_no"] for r in out["records"])
    assert apps == ["2016/01059", "2016/99999"]
    new_record = next(r for r in out["records"] if r["application_no"] == "2016/99999")
    assert new_record["record_index"] == 2  # next after PDF's 1


def test_merge_to_pdf_shape_unmatched_pdf_records_kept():
    """PDF has a record the CD doesn't ship -> kept verbatim."""
    pdf = _minimal_pdf_doc()
    pdf_record_orphan = {**_real_pdf_record_2016_01059(),
                          "application_no": "2024/007254"}
    pdf["records"] = [pdf_record_orphan]
    cd = _minimal_cd_doc()
    cd["dossiers"] = [_real_cd_dossier_2016_01059()]
    out = merge_to_pdf_shape(pdf_doc=pdf, cd_doc=cd)
    apps = sorted(r["application_no"] for r in out["records"])
    assert apps == ["2016/01059", "2024/007254"]


# ---------------------------------------------------------------------------
# Embedding preservation across merge
# ---------------------------------------------------------------------------

def test_index_existing_embeddings_keys_on_image_path():
    pdf = {"records": [{
        "designs": [{
            "design_index": 1,
            "views": [
                {"view_index": 1, "image_path": "x/1_1.jpg",
                  "embeddings": {"dinov2_vitl14": [0.1] * 1024}},
                {"view_index": 2, "image_path": "x/1_2.jpg"},  # no embeddings
            ],
        }],
    }]}
    idx = _index_existing_embeddings(pdf)
    assert "x/1_1.jpg" in idx
    assert "x/1_2.jpg" not in idx
    assert len(idx["x/1_1.jpg"]["dinov2_vitl14"]) == 1024


def test_index_existing_embeddings_skips_empty_inputs():
    assert _index_existing_embeddings(None) == {}
    assert _index_existing_embeddings({}) == {}
    assert _index_existing_embeddings({"records": []}) == {}


def test_attach_existing_embeddings_re_attaches_by_image_path():
    """Image_path is the canonical bridge across the merge — survives even
    when application_no / design_index / view_index might shift between
    pre-merge and post-merge."""
    merged = {"records": [{
        "designs": [{
            "design_index": 1,
            "views": [
                {"view_index": 1, "image_path": "x/1_1.jpg", "image_source": "cd"},
                {"view_index": 2, "image_path": "x/1_2.jpg", "image_source": "cd"},
            ],
        }],
    }]}
    existing = {
        "x/1_1.jpg": {"dinov2_vitl14": [0.1] * 1024},
        "y/9_9.jpg": {"dinov2_vitl14": [0.2] * 1024},  # no longer in merged
    }
    attached = _attach_existing_embeddings(merged, existing)
    assert attached == 1
    assert "embeddings" in merged["records"][0]["designs"][0]["views"][0]
    assert "embeddings" not in merged["records"][0]["designs"][0]["views"][1]


def test_attach_existing_embeddings_no_op_on_empty_index():
    merged = {"records": [{"designs": [{"views": [{"image_path": "x"}]}]}]}
    assert _attach_existing_embeddings(merged, {}) == 0
    # Original dict unchanged
    assert "embeddings" not in merged["records"][0]["designs"][0]["views"][0]

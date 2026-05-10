"""Unit tests for ``pipeline.merge_into_metadata``.

Built one helper at a time. Each step adds its own test block.
"""

from __future__ import annotations

from pipeline.merge_into_metadata import (
    synthesize_cd_record_in_pdf_shape,
)
from pipeline.merge_into_metadata import (
    _cd_attorney_to_pdf_attorney,
    _cd_design_to_pdf_design,
    _cd_designer_to_pdf_designer,
    _cd_holder_to_pdf_applicant,
    _cd_view_to_pdf_view,
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

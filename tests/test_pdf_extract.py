from pathlib import Path

import pdf_extract


class _FakePdfPage:
    def __init__(self, text: str):
        self._text = text

    def get_text(self):
        return self._text


class _FakePdfDoc:
    def __init__(self, pages):
        self._pages = [_FakePdfPage(text) for text in pages]
        self.page_count = len(self._pages)

    def __getitem__(self, idx):
        return self._pages[idx]


def test_infer_pdf_target_supports_canonical_and_legacy_filenames():
    canonical_bulletin = pdf_extract._infer_pdf_target(Path("BLT_490_2026-04-13.pdf"))
    canonical_gazette = pdf_extract._infer_pdf_target(Path("GZ_500_2026-04-13.pdf"))
    bulletin = pdf_extract._infer_pdf_target(Path("269_2026-03-12.pdf"))
    gazette = pdf_extract._infer_pdf_target(Path("269_Gazete_2026-03-27.pdf"))

    assert canonical_bulletin is not None
    assert canonical_bulletin["issue_number"] == "490"
    assert canonical_bulletin["issue_date"] == "2026-04-13"
    assert canonical_bulletin["is_gazette"] is False
    assert canonical_bulletin["output_dir"].name == "BLT_490_2026-04-13"

    assert canonical_gazette is not None
    assert canonical_gazette["issue_number"] == "500"
    assert canonical_gazette["issue_date"] == "2026-04-13"
    assert canonical_gazette["is_gazette"] is True
    assert canonical_gazette["output_dir"].name == "GZ_500_2026-04-13"

    assert bulletin is not None
    assert bulletin["issue_number"] == "269"
    assert bulletin["issue_date"] == "2026-03-12"
    assert bulletin["is_gazette"] is False
    assert bulletin["output_dir"].name == "BLT_269_2026-03-12"

    assert gazette is not None
    assert gazette["issue_number"] == "269"
    assert gazette["issue_date"] == "2026-03-27"
    assert gazette["is_gazette"] is True
    assert gazette["output_dir"].name == "GZ_269_2026-03-27"


def test_find_unprocessed_pdfs_supports_canonical_top_level_raw_pdfs(tmp_path):
    bulletin_pdf = tmp_path / "BLT_490_2026-04-13.pdf"
    bulletin_pdf.write_bytes(b"%PDF-bulletin")
    gazette_pdf = tmp_path / "GZ_500_2026-04-13.pdf"
    gazette_pdf.write_bytes(b"%PDF-gazette")

    targets = pdf_extract.find_unprocessed_pdfs(tmp_path)

    assert [target["output_dir"].name for target in targets] == [
        "BLT_490_2026-04-13",
        "GZ_500_2026-04-13",
    ]
    assert targets[0]["pdf_path"] == bulletin_pdf
    assert targets[0]["is_gazette"] is False
    assert targets[1]["pdf_path"] == gazette_pdf
    assert targets[1]["is_gazette"] is True


def test_move_pdf_into_issue_folder_moves_top_level_pdf_to_bulletin_name(tmp_path):
    source_pdf = tmp_path / "BLT_490_2026-04-13.pdf"
    source_pdf.write_bytes(b"%PDF-top-level")
    issue_folder = tmp_path / "BLT_490_2026-04-13"

    final_pdf = pdf_extract._move_pdf_into_issue_folder(source_pdf, issue_folder)

    assert final_pdf == issue_folder / "bulletin.pdf"
    assert final_pdf.read_bytes() == b"%PDF-top-level"
    assert not source_pdf.exists()


def test_move_pdf_into_issue_folder_keeps_existing_in_folder_pdf(tmp_path):
    issue_folder = tmp_path / "GZ_500_2026-04-13"
    issue_folder.mkdir()
    existing_pdf = issue_folder / "bulletin.pdf"
    existing_pdf.write_bytes(b"%PDF-existing")
    duplicate_top_level = tmp_path / "GZ_500_2026-04-13.pdf"
    duplicate_top_level.write_bytes(b"%PDF-existing")

    final_pdf = pdf_extract._move_pdf_into_issue_folder(duplicate_top_level, issue_folder)

    assert final_pdf == existing_pdf
    assert existing_pdf.read_bytes() == b"%PDF-existing"
    assert not duplicate_top_level.exists()


def test_find_unprocessed_pdfs_prefers_existing_issue_folder_pdf(tmp_path):
    issue_folder = tmp_path / "GZ_269_2026-03-27"
    issue_folder.mkdir()
    folder_pdf = issue_folder / "bulletin.pdf"
    folder_pdf.write_bytes(b"%PDF-folder")

    duplicate_top_level = tmp_path / "269_Gazete_2026-03-27.pdf"
    duplicate_top_level.write_bytes(b"%PDF-top-level")

    processed_folder = tmp_path / "BLT_270_2026-03-12"
    processed_folder.mkdir()
    (processed_folder / "metadata.json").write_text("[]", encoding="utf-8")
    (tmp_path / "270_2026-03-12.pdf").write_bytes(b"%PDF-processed")

    targets = pdf_extract.find_unprocessed_pdfs(tmp_path)

    assert len(targets) == 1
    assert targets[0]["pdf_path"] == folder_pdf
    assert targets[0]["output_dir"] == issue_folder
    assert targets[0]["is_gazette"] is True


def test_find_unprocessed_pdfs_repairs_legacy_date_less_issue_folders(tmp_path):
    issue_folder = tmp_path / "GZ_486"
    issue_folder.mkdir()
    folder_pdf = issue_folder / "bulletin.pdf"
    folder_pdf.write_bytes(b"%PDF-folder")

    targets = pdf_extract.find_unprocessed_pdfs(tmp_path)

    assert len(targets) == 1
    assert targets[0]["pdf_path"] == folder_pdf
    assert targets[0]["output_dir"] == issue_folder
    assert targets[0]["issue_number"] == "486"
    assert targets[0]["issue_date"] is None
    assert targets[0]["is_gazette"] is True


def test_build_metadata_record_uses_gazette_status_and_fields():
    fields = {
        "210": "2026/123456",
        "220": "21.04.2026",
        "511": "09",
        "510": "Software",
        "540": "TEST MARK",
        "731": "ACME LTD. (TR)",
    }

    gazette_record = pdf_extract._build_metadata_record(
        fields,
        "269",
        "2026-03-27",
        has_image=False,
        is_gazette=True,
    )
    bulletin_record = pdf_extract._build_metadata_record(
        fields,
        "488",
        "2026-03-12",
        has_image=False,
        is_gazette=False,
    )

    assert gazette_record is not None
    assert gazette_record["STATUS"] == "Registered"
    assert gazette_record["TRADEMARK"]["GAZETTE_NO"] == "269"
    assert gazette_record["TRADEMARK"]["GAZETTE_DATE"] == "2026-03-27"
    assert gazette_record["TRADEMARK"]["BULLETIN_NO"] is None

    assert bulletin_record is not None
    assert bulletin_record["STATUS"] == "Application/Published"
    assert bulletin_record["TRADEMARK"]["BULLETIN_NO"] == "488"
    assert bulletin_record["TRADEMARK"]["BULLETIN_DATE"] == "2026-03-12"
    assert bulletin_record["TRADEMARK"]["GAZETTE_NO"] is None


def test_parse_holder_keeps_multiline_corporate_title_and_country_name():
    raw = (
        "1875747-P&C TECHNOLOGIE ZÁRTKÖR?EN M?KÖD?\n"
        "RÉSZVÉNYTÁRSASÁG (MACARİSTAN)\n"
        "BAJCSY-ZSILINSZKY ÚT 48. 2. EM., H-1054\n"
        "BUDAPEST MACARİSTAN"
    )

    holders, attorneys = pdf_extract._parse_holder(raw)

    assert attorneys == []
    assert holders == [
        {
            "TPECLIENTID": "1875747",
            "TITLE": "P&C TECHNOLOGIE ZÁRTKÖRŰEN MŰKÖDŐ RÉSZVÉNYTÁRSASÁG",
            "ADDRESS": "BAJCSY-ZSILINSZKY ÚT 48. 2. EM., H-1054 BUDAPEST MACARİSTAN",
            "TOWN_DISTRICT": "",
            "POSTALCODE": "",
            "CITY_PROVINCE": "MACARİSTAN",
            "COUNTRY": "MACARİSTAN",
        }
    ]


def test_parse_holder_repairs_known_foreign_placeholder_glyphs():
    raw = "VANJA KATI? (HIRVATİSTAN)"

    holders, _ = pdf_extract._parse_holder(raw)

    assert holders[0]["TITLE"] == "VANJA KATIĆ"
    assert holders[0]["COUNTRY"] == "HIRVATİSTAN"


def test_parse_holder_strips_placeholder_quotes_from_company_titles():
    raw = "?LABORATORIA ANGIOPHARM? LIMITED (KIBRIS)"

    holders, _ = pdf_extract._parse_holder(raw)

    assert holders[0]["TITLE"] == "LABORATORIA ANGIOPHARM LIMITED"
    assert holders[0]["COUNTRY"] == "KIBRIS"


def test_parse_toc_scans_later_pages_for_gazette_contents():
    doc = _FakePdfDoc(
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "İçindekiler\n1. MARKA TESCİLLERİ ........ 7\nİLİŞKİN İLANLAR ........ 11695\n",
        ]
    )

    toc = pdf_extract._parse_toc(doc)

    assert toc == {
        "1. MARKA TESCİLLERİ": 7,
        "İLİŞKİN İLANLAR": 11695,
    }


def test_get_application_page_ranges_supports_gazette_registration_sections(monkeypatch):
    doc = _FakePdfDoc([""])
    doc.page_count = 12170

    monkeypatch.setattr(
        pdf_extract,
        "_parse_toc",
        lambda _doc: {
            "1. MARKA TESCİLLERİ": 7,
            "MARKA TESCİLLERİ": 11551,
            "İLİŞKİN İLANLAR": 11695,
            "DÜZELTMELER": 12105,
        },
    )

    ranges = pdf_extract._get_application_page_ranges(doc)

    assert ranges == [
        (6, 11550),
        (11550, 11694),
    ]


def test_clean_page_artifacts_strips_gazette_footer_noise():
    raw = (
        "uysal holding "
        "________________________________________________________________________________ "
        "248 Yayın Tarihi : 31.03.2026 Türk Patent ve Marka Kurumu 2026/500 Resmi Marka Gazetesi"
    )

    cleaned = pdf_extract._clean_page_artifacts(raw)

    assert cleaned == "uysal holding"

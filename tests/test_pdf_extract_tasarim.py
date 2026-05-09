"""Unit tests for ``pdf_extract_tasarim`` pure helpers.

The streaming PDF parsing is exercised by a smoke test against the real
``bulletins/Tasarim/TS_483_2026-04-24/bulletin.pdf`` fixture. Unit tests here
target the pure helpers that don't need PyMuPDF, plus a few extract_issue
tests that mock ``parse_pdf`` so the orchestrator's pre/post logic can be
exercised without the C library.
"""

from pathlib import Path

from pdf_extract_tasarim import (
    Attorney,
    Priority,
    View,
    clean_text,
    detect_deferred_period,
    detect_section_for_page,
    extract_bulletin_metadata,
    extract_issue,
    normalize_appno_for_filename,
    normalize_tr_date,
    parse_applicant,
    parse_attorney,
    parse_designers,
    parse_hague_record,
    parse_inid_fields,
    parse_locarno_list,
    parse_priorities,
    parse_tr_record,
    parse_view_labels,
    view_image_key,
)


# ---------------------------------------------------------------------------
# clean_text / normalize helpers
# ---------------------------------------------------------------------------

def test_clean_text_collapses_whitespace_and_drops_nulls():
    assert clean_text(" foo  bar  baz\n  ") == "foo bar baz"
    assert clean_text("") == ""
    assert clean_text(None) == ""


def test_normalize_tr_date():
    assert normalize_tr_date("06.09.2024") == "2024-09-06"
    assert normalize_tr_date("Yayın Tarihi: 24.04.2026") == "2026-04-24"
    assert normalize_tr_date(None) is None
    assert normalize_tr_date("garbage") is None


def test_normalize_appno_for_filename():
    assert normalize_appno_for_filename("2024/007254") == "2024_007254"
    assert normalize_appno_for_filename(None) == "unknown"
    assert normalize_appno_for_filename("DM 244882") == "DM_244882"


def test_view_image_key_canonical_shape():
    """Canonical key is ``{appno_norm}/{d}_{v}.jpg`` with no archive- or
    images/ wrapper prefix. Same shape cd_extract_tasarim emits, so a
    future stage-3 reconciler can match PDF and CD output by one string."""
    assert view_image_key("2024_007254", 1, 1) == "2024_007254/1_1.jpg"
    assert view_image_key("2024_007254", 4, 3) == "2024_007254/4_3.jpg"
    assert view_image_key("2016_01205", 18, 7) == "2016_01205/18_7.jpg"


def test_view_dataclass_image_source_field():
    """View now carries an image_source provenance tag. None by default
    (no image), "pdf" when the PDF extractor wrote the file, "cd" when
    the CD-side image was already on disk and the PDF skipped its own
    write to avoid a duplicate."""
    v = View(view_index=1, page=17)
    assert v.image_source is None
    assert v.image_path is None

    v_pdf = View(view_index=1, page=17, image_path="2024_007254/1_1.jpg",
                 image_source="pdf")
    assert v_pdf.image_source == "pdf"

    v_cd = View(view_index=1, page=17, image_path="2024_007254/1_1.jpg",
                image_source="cd")
    assert v_cd.image_source == "cd"


# ---------------------------------------------------------------------------
# extract_issue --force wipes images/ for clean slate
# ---------------------------------------------------------------------------

def test_extract_issue_force_wipes_existing_images_dir(tmp_path, monkeypatch):
    """--force must clear any prior images/ tree before re-extracting so
    legacy flat-named files don't coexist with the new per-application
    subfolder layout. parse_pdf is mocked so we don't need PyMuPDF."""
    issue = tmp_path / "TS_999_2026-01-01"
    issue.mkdir()
    (issue / "bulletin.pdf").write_bytes(b"not a real pdf")
    images_dir = issue / "images"
    images_dir.mkdir()
    # Stale legacy-layout files
    (images_dir / "2024_007254_1_1.jpg").write_bytes(b"OLD")
    (images_dir / "2024_007254_1_2.jpg").write_bytes(b"OLD")

    fake_payload_calls = {}

    def fake_parse_pdf(pdf, *, extract_images=True, images_dir=None):
        fake_payload_calls["images_dir_existed"] = Path(images_dir).exists()
        # Simulate what the real parse would write: per-app subfolder layout
        out = Path(images_dir) / "2024_007254"
        out.mkdir(parents=True, exist_ok=True)
        (out / "1_1.jpg").write_bytes(b"NEW")
        return {
            "bulletin_no": 999, "bulletin_date": "2026-01-01",
            "source": Path(pdf).name, "page_count": 0,
            "record_count": 0, "records": [],
        }

    monkeypatch.setattr("pdf_extract_tasarim.parse_pdf", fake_parse_pdf)

    extract_issue(issue, force=True, extract_images=True)

    # Old flat files gone, new per-app file present
    assert not (images_dir / "2024_007254_1_1.jpg").exists()
    assert not (images_dir / "2024_007254_1_2.jpg").exists()
    assert (images_dir / "2024_007254" / "1_1.jpg").read_bytes() == b"NEW"
    # parse_pdf saw an empty dir (we wiped it before invoking)
    assert fake_payload_calls["images_dir_existed"] is False


def test_extract_issue_no_force_does_not_wipe_when_re_extracting(tmp_path, monkeypatch):
    """If the PDF is newer than metadata.json (rare non-force re-extract),
    we leave any pre-existing images/ in place — the locked decision was
    that the wipe is a --force-only behavior."""
    issue = tmp_path / "TS_999_2026-01-01"
    issue.mkdir()
    pdf = issue / "bulletin.pdf"
    pdf.write_bytes(b"x")
    # Stale metadata older than pdf forces a re-extract path WITHOUT --force.
    meta = issue / "metadata.json"
    meta.write_text("{}", encoding="utf-8")
    import os
    older = pdf.stat().st_mtime - 60
    os.utime(meta, (older, older))

    images_dir = issue / "images"
    images_dir.mkdir()
    (images_dir / "stale.jpg").write_bytes(b"PRESERVED")

    def fake_parse_pdf(pdf, *, extract_images=True, images_dir=None):
        return {"bulletin_no": 999, "bulletin_date": "2026-01-01",
                "source": Path(pdf).name, "page_count": 0,
                "record_count": 0, "records": []}

    monkeypatch.setattr("pdf_extract_tasarim.parse_pdf", fake_parse_pdf)

    extract_issue(issue, force=False, extract_images=True)

    # Stale file untouched (no wipe outside --force)
    assert (images_dir / "stale.jpg").read_bytes() == b"PRESERVED"


def test_extract_issue_force_handles_missing_images_dir(tmp_path, monkeypatch):
    """First-time extraction (no prior images/) doesn't fail when --force is set."""
    issue = tmp_path / "TS_999_2026-01-01"
    issue.mkdir()
    (issue / "bulletin.pdf").write_bytes(b"x")

    def fake_parse_pdf(pdf, *, extract_images=True, images_dir=None):
        return {"bulletin_no": 999, "bulletin_date": "2026-01-01",
                "source": Path(pdf).name, "page_count": 0,
                "record_count": 0, "records": []}

    monkeypatch.setattr("pdf_extract_tasarim.parse_pdf", fake_parse_pdf)

    result = extract_issue(issue, force=True, extract_images=True)
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# parse_inid_fields
# ---------------------------------------------------------------------------

def test_parse_inid_fields_simple_record():
    text = (
        "(21)  2024/007254 (15)  06.09.2024\n"
        "(22)  06.09.2024\n"
        "(28)  4\n"
        "(51)  26-05\n"
        "(73)  TIM MIMARLIK (7610221) ADDRESS Şişli İstanbul TÜRKİYE\n"
        "(72)  ŞEBNEM SULTAN BUHARA GÜLEN\n"
        "(74)  IŞIK ÖZDOĞAN (MOROĞLU ARSEVEN DANIŞMANLIK A.Ş.)\n"
        "1.1 Lamba 2.1 Lamba 3.1 Lamba 4.1 Lamba"
    )
    fields = parse_inid_fields(text)
    assert fields["21"] == ["2024/007254"]
    assert fields["15"] == ["06.09.2024"]
    assert fields["22"] == ["06.09.2024"]
    assert fields["28"] == ["4"]
    assert fields["51"] == ["26-05"]
    # Applicant value contains parens around (7610221) — those are NOT INID codes
    # because the regex restricts to 2-3 digits, and 7-digit IDs are skipped.
    assert "TIM MIMARLIK" in fields["73"][0]
    assert "7610221" in fields["73"][0]
    assert fields["72"][0].startswith("ŞEBNEM")
    # (74) value also contains parens around the firm — those are not INID codes
    assert "MOROĞLU" in fields["74"][0]


def test_parse_inid_fields_handles_repeated_codes():
    text = "(72) NAME ONE\n(72) NAME TWO\n(72) NAME THREE"
    fields = parse_inid_fields(text)
    assert fields["72"] == ["NAME ONE", "NAME TWO", "NAME THREE"]


def test_parse_inid_fields_empty():
    assert parse_inid_fields("") == {}
    assert parse_inid_fields("no codes here") == {}


# ---------------------------------------------------------------------------
# Locarno
# ---------------------------------------------------------------------------

def test_parse_locarno_list_single():
    assert parse_locarno_list("26-05") == ["26-05"]
    assert parse_locarno_list("  26.05  ") == ["26-05"]


def test_parse_locarno_list_multiple_classes():
    # Real probe sample: "06-03 , 06-06 , 06-01"
    assert parse_locarno_list("06-03 , 06-06 , 06-01") == ["06-03", "06-06", "06-01"]


def test_parse_locarno_list_dedupes():
    assert parse_locarno_list("06-01, 06-01, 06-02") == ["06-01", "06-02"]


def test_parse_locarno_list_empty():
    assert parse_locarno_list("") == []
    assert parse_locarno_list("not a class") == []


# ---------------------------------------------------------------------------
# Priorities
# ---------------------------------------------------------------------------

def test_parse_priorities_single():
    raw = "27.06.2025  30/010,422  US"
    prio = parse_priorities(raw)
    assert prio == [Priority(date="2025-06-27", number="30/010,422", country="US")]


def test_parse_priorities_multiple():
    raw = "27.06.2025  30/010,422  US 28.06.2025  30/010,500  US 01.07.2025  98765  DE"
    prio = parse_priorities(raw)
    countries = [p.country for p in prio]
    assert countries.count("US") == 2
    assert "DE" in countries


def test_parse_priorities_empty():
    assert parse_priorities("") == []


# ---------------------------------------------------------------------------
# Attorney / Applicant / Designer
# ---------------------------------------------------------------------------

def test_parse_attorney_with_firm():
    a = parse_attorney("IŞIK ÖZDOĞAN (MOROĞLU ARSEVEN DANIŞMANLIK A.Ş.)")
    assert a == Attorney(name="IŞIK ÖZDOĞAN", firm="MOROĞLU ARSEVEN DANIŞMANLIK A.Ş.")


def test_parse_attorney_name_only():
    a = parse_attorney("MEHMET YILMAZ")
    assert a == Attorney(name="MEHMET YILMAZ")


def test_parse_attorney_empty():
    assert parse_attorney("") is None
    assert parse_attorney("   ") is None


def test_parse_applicant_with_id_and_country():
    a = parse_applicant("TIM MIMARLIK MUHENDISLIK (7610221) HARBIYE MAH. KASIM SOK Şişli İstanbul TÜRKİYE")
    assert a is not None
    assert "TIM MIMARLIK" in a.name
    assert a.id == "7610221"
    assert a.country == "TÜRKİYE"
    assert a.address and "HARBIYE" in a.address


def test_parse_applicant_name_only_when_no_id():
    a = parse_applicant("SAMET ÖZDEMİR")
    assert a is not None
    assert a.name == "SAMET ÖZDEMİR"
    assert a.id is None


def test_parse_designers_strips_view_labels():
    raw = ["ŞEBNEM SULTAN\n1.1 Lamba 2.1 Lamba"]
    designers = parse_designers(raw)
    names = [d.name for d in designers]
    assert "ŞEBNEM SULTAN" in names
    # The "1.1 Lamba" line should have been stripped, not stored as a designer
    assert not any("Lamba" in n for n in names)


# ---------------------------------------------------------------------------
# View labels
# ---------------------------------------------------------------------------

def test_parse_view_labels_one_per_line():
    text = "1.1 Lamba\n1.2 Lamba\n2.1 Sandalye"
    labels = parse_view_labels(text)
    assert (1, 1, "Lamba") in labels
    assert (1, 2, "Lamba") in labels
    assert (2, 1, "Sandalye") in labels


def test_parse_view_labels_multi_per_line():
    text = "1.1 Lamba 2.1 Lamba 3.1 Lamba 4.1 Lamba"
    labels = parse_view_labels(text)
    assert len(labels) == 4
    indices = [(d, v) for d, v, _ in labels]
    assert indices == [(1, 1), (2, 1), (3, 1), (4, 1)]
    assert all(name == "Lamba" for _, _, name in labels)


def test_parse_view_labels_ignores_non_label_text():
    text = "(21) 2024/007254\n(22) 06.09.2024\n(73) APPLICANT NAME"
    assert parse_view_labels(text) == []


# ---------------------------------------------------------------------------
# Section / deferred / bulletin metadata
# ---------------------------------------------------------------------------

def test_detect_section_for_page_sticky_default():
    assert detect_section_for_page("body content", "tr_native") == "tr_native"


def test_detect_section_for_page_transitions():
    assert detect_section_for_page("YAYIN ERTELEME TALEPLİ TASARIM", "tr_native") == "deferred"
    assert detect_section_for_page("YAYIN ERTELEME TALEBİ KALDIRILAN", "deferred") == "deferred_lifted"
    assert detect_section_for_page("LAHEY", "tr_native") == "hague"


def test_detect_deferred_period():
    assert detect_deferred_period("(ES) 30 Ay") == 30
    assert detect_deferred_period("(ES)  6   Ay") == 6
    assert detect_deferred_period("no marker") is None


def test_extract_bulletin_metadata_from_footer_text():
    text = (
        "some body content\n"
        "2026/483  Tasarımlar Bülteni / Yayın Tarihi : 24.04.2026\n"
        "more body content"
    )
    no, date = extract_bulletin_metadata(text)
    assert no == 483
    assert date == "2026-04-24"


def test_extract_bulletin_metadata_missing():
    no, date = extract_bulletin_metadata("nothing useful here")
    assert no is None
    assert date is None


# ---------------------------------------------------------------------------
# parse_tr_record (integration of helpers)
# ---------------------------------------------------------------------------

def test_parse_tr_record_simple():
    block = (
        "(21)  2024/007254 (15)  06.09.2024\n"
        "(22)  06.09.2024\n"
        "(28)  4\n"
        "(51)  26-05\n"
        "(73)  TIM MIMARLIK (7610221) ADDRESS Şişli İstanbul TÜRKİYE\n"
        "(72)  ŞEBNEM SULTAN BUHARA GÜLEN\n"
        "(74)  IŞIK ÖZDOĞAN (MOROĞLU ARSEVEN DANIŞMANLIK A.Ş.)\n"
        "1.1 Lamba 2.1 Lamba 3.1 Lamba 4.1 Lamba"
    )
    rec = parse_tr_record(block, section="tr_native", record_index=1, page_range=(17, 17))
    assert rec.application_no == "2024/007254"
    assert rec.filing_date == "2024-09-06"
    assert rec.registration_date == "2024-09-06"
    assert rec.design_count == 4
    assert rec.locarno_classes == ["26-05"]
    assert len(rec.applicants) == 1
    assert rec.applicants[0].id == "7610221"
    assert rec.applicants[0].country == "TÜRKİYE"
    assert len(rec.designers) == 1
    assert rec.designers[0].name == "ŞEBNEM SULTAN BUHARA GÜLEN"
    assert rec.attorney is not None
    assert rec.attorney.firm and "MOROĞLU" in rec.attorney.firm
    assert rec.deferred_publication is None
    assert rec.section == "tr_native"
    assert rec.page_range == [17, 17]


def test_parse_tr_record_deferred_marker():
    block = (
        "(21)  2026/001807\n"
        "(22)  15.01.2026\n"
        "(28)  2\n"
        "(51)  06-01\n"
        "(73)  ANY APPLICANT (1234567) ADDRESS TÜRKİYE\n"
        "(ES) 30 Ay"
    )
    rec = parse_tr_record(block, section="deferred", record_index=4, page_range=(431, 431))
    assert rec.deferred_publication is not None
    assert rec.deferred_publication.period_months == 30
    assert rec.section == "deferred"
    assert rec.designers == []  # deferred records have no (72)


def test_parse_tr_record_multiple_locarno():
    block = "(21) 2026/000001\n(22) 01.01.2026\n(28) 34\n(51) 06-03 , 06-06 , 06-01\n(73) X (1) Y TÜRKİYE"
    rec = parse_tr_record(block, section="tr_native", record_index=1, page_range=(50, 60))
    assert rec.locarno_classes == ["06-03", "06-06", "06-01"]
    assert rec.design_count == 34


# ---------------------------------------------------------------------------
# parse_hague_record
# ---------------------------------------------------------------------------

def test_parse_hague_record_basic():
    block = (
        "WIPO Bülten No: 13/2025\n"
        "(11) DM 244882  (15) 15.02.2024\n"
        "(22) 15.02.2024  (73) RAYE ROCKS LLC (8022625) 2600 S. Douglas Rd. US\n"
        "(74) Sullivan Worcester (5555555)  (72) Erika Rayman\n"
        "(28) 1  (51) 11-01  (54) Jewelry for swim wear  (81) III. CH, CN, DE, EM, FR, GB, SG, TR"
    )
    rec = parse_hague_record(block, record_index=1, page_range=(477, 477))
    assert rec.section == "hague"
    assert rec.registration_no.startswith("DM")
    assert rec.filing_date == "2024-02-15"
    assert rec.design_count == 1
    assert rec.locarno_classes == ["11-01"]
    assert rec.hague_reference is not None
    assert rec.hague_reference.wipo_bulletin == "13/2025"
    assert rec.hague_reference.product_name_en == "Jewelry for swim wear"
    assert "TR" in rec.hague_reference.designated_states
    assert "CH" in rec.hague_reference.designated_states


def test_parse_hague_record_no_states():
    block = "WIPO Bülten No: 5/2025\n(11) DM 100000\n(22) 01.01.2025\n(28) 1\n(51) 06-01"
    rec = parse_hague_record(block, record_index=1, page_range=(480, 480))
    assert rec.hague_reference is not None
    assert rec.hague_reference.designated_states == []

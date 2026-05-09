"""Unit tests for ``pdf_extract_patent`` helpers.

Built one helper at a time. Each step adds its own test block so failures
point cleanly at the unit under test.
"""

import json
from pathlib import Path

import pytest

from pdf_extract_patent import (
    PATENT_INID_CODES,
    Attorney,
    EPReference,
    Holder,
    Inventor,
    PageKind,
    PatentRecord,
    Priority,
    RecordType,
    classify_kind_code,
    clean_text,
    detect_page_kind,
    extract_bulletin_metadata,
    extract_bulletin_metadata_from_text,
    extract_kind_code,
    normalize_iso_date,
    normalize_tr_date,
    parse_abstract,
    parse_application_no,
    parse_attorney,
    parse_date_field,
    parse_ep_reference,
    parse_full_bibliographic_record,
    parse_holders,
    parse_inid_block,
    parse_inventors,
    parse_ipc_classes,
    parse_priorities,
    parse_publication_no,
    parse_title,
)
from pdf_extract_patent import (
    DEFAULT_BANNER_PAGE_THRESHOLD,
    CLIArgs,
    build_figure_inventory,
    detect_banner_xrefs,
    extract_record_figures,
    BULLETIN_PDF_FILENAME,
    FIGURES_DIRNAME,
    main,
    PDF_METADATA_FILENAME,
    parse_argv,
    parse_pdf,
)
from pdf_extract_patent import _metadata_is_fresh
from pdf_extract_patent import (
    _build_global_text,
    _char_pos_to_page,
    _dedup_pdf_pngs_against_cd_tifs,
    _find_record_boundaries,
    _normalize_appno_for_filename,
    _record_to_dict,
)


# ---------------------------------------------------------------------------
# Step 3.1 — clean_text
# ---------------------------------------------------------------------------

def test_clean_text_collapses_whitespace_runs():
    assert clean_text("  hello   world  ") == "hello world"
    assert clean_text("a\n\n\nb") == "a b"
    assert clean_text("\ta\t\tb") == "a b"


def test_clean_text_handles_none_and_empty():
    assert clean_text(None) == ""
    assert clean_text("") == ""
    assert clean_text("   ") == ""


def test_clean_text_drops_nul_bytes():
    """Defensive: PDF text extracts occasionally carry stray NULs."""
    assert clean_text("a\x00b") == "ab"


# ---------------------------------------------------------------------------
# Step 3.1 — normalize_iso_date
# ---------------------------------------------------------------------------

def test_normalize_iso_date_real_patent_pdf_format():
    """Captured from 2025_08.pdf body: '2024/04/22' -> '2024-04-22'."""
    assert normalize_iso_date("2024/04/22") == "2024-04-22"
    assert normalize_iso_date("2025/08/21") == "2025-08-21"


def test_normalize_iso_date_extracts_from_label_prefix():
    """INID values often have a Turkish label before the date.

        '(43) Başvuru Yayın Tarihi\\n2024/04/22, 2024/4 Nolu Bülten'

    -> we want '2024-04-22' from inside the value, not None.
    """
    raw = "Başvuru Yayın Tarihi\n2024/04/22, 2024/4 Nolu Bülten"
    assert normalize_iso_date(raw) == "2024-04-22"


def test_normalize_iso_date_returns_none_for_missing_or_bad():
    assert normalize_iso_date(None) is None
    assert normalize_iso_date("") is None
    assert normalize_iso_date("no date here") is None
    assert normalize_iso_date("garbage") is None


def test_normalize_iso_date_distinct_from_cd_dmy_format():
    """Sanity guard: the CD path uses DD/MM/YYYY ('22/12/2025'), the PDF
    path uses YYYY/MM/DD. We must NOT decode '22/12/2025' as if it were
    YYYY/MM/DD — that would give the impossible date '22-12-2025'.
    """
    # The CD format does not match the PDF regex.
    assert normalize_iso_date("22/12/2025") is None


# ---------------------------------------------------------------------------
# Step 3.1 — parse_inid_block (line-anchored, whitelist-bounded)
# ---------------------------------------------------------------------------

def test_parse_inid_block_real_granted_patent_record():
    """Real INID block captured from page 200 of 2025_08.pdf.

    Verifies:
      - all 2-digit INID codes are extracted
      - line-oriented values are preserved verbatim (newlines kept)
      - order is preserved
    """
    block = (
        "(11) TR 2022 014462 B\n"
        "(12) Patent Belgesi\n"
        "(43) Başvuru Yayın Tarihi\n"
        "2024/04/22, 2024/4 Nolu Bülten\n"
        "(10) Başvuru Yayın No\n"
        "TR 2022 014462 A2\n"
        "(21) Başvuru Numarası\n"
        "2022/014462\n"
        "(22) Başvuru Tarihi\n"
        "2022/09/20\n"
    )
    fields = parse_inid_block(block)
    assert fields["11"] == ["TR 2022 014462 B"]
    assert fields["12"] == ["Patent Belgesi"]
    # Multi-line values keep their newlines so per-INID sub-parsers can
    # decide whether the first line is a label.
    assert "2024/04/22" in fields["43"][0]
    assert fields["21"] == ["Başvuru Numarası\n2022/014462"]
    assert fields["22"] == ["Başvuru Tarihi\n2022/09/20"]
    # Ordering: (11) appears before (12) in the input
    keys = list(fields.keys())
    assert keys.index("11") < keys.index("12")


def test_parse_inid_block_groups_repeated_codes():
    """EP fascicle records have TWO (96) and TWO (97) values — the
    parser must keep both as an ordered list, not silently overwrite."""
    block = (
        "(96) Başvuru Tarihi\n2021/03/23\n"
        "(97) EP Yayın No\nEP3885497B1\n"
        "(97) EP Yayın Tarihi\n2025/06/04\n"
        "(96) EP Başvuru No\nEP21164305.1\n"
    )
    fields = parse_inid_block(block)
    assert len(fields["96"]) == 2
    assert "2021/03/23" in fields["96"][0]
    assert "EP21164305.1" in fields["96"][1]
    assert len(fields["97"]) == 2
    assert "EP3885497B1" in fields["97"][0]
    assert "2025/06/04" in fields["97"][1]


def test_parse_inid_block_immune_to_57_abstract_trap():
    """The (57) abstract body legitimately contains mid-sentence
    parenthesised numerals like '(2)', '(11)', '(20)' that point at
    figure call-outs. A naive INID regex would mistake those for field
    boundaries and split the abstract into nonsense fragments. The
    line-anchored regex must NOT.
    """
    block = (
        "(57) Özet\n"
        "Bu buluş, bir gövde (2), gövdeye (2) erişim sağlayan bir kapı (3),\n"
        "delikli örtü (10) ve delikli örtünün (11) üzerine yerleştirilen\n"
        "bir kapağa (20) sahip bir hazne (9) ile ilgilidir.\n"
        "(54) Buluş Başlığı\n"
        "NEM KONTROLLÜ HAZNEYE SAHİP BİR BUZDOLABI\n"
    )
    fields = parse_inid_block(block)
    # We must see exactly two top-level codes — (57) and (54), in that order
    assert set(fields.keys()) == {"57", "54"}
    assert len(fields["57"]) == 1
    # The whole abstract (including the (2)/(11)/(20) figure refs) lives
    # under (57) as a single value.
    abstract = fields["57"][0]
    assert "kapı (3)" in abstract
    assert "delikli örtünün (11)" in abstract  # mid-line (11) MUST be preserved
    assert "kapağa (20)" in abstract
    # The trailing (54) value must NOT include any abstract content.
    assert "kapı" not in fields["54"][0]
    assert "Buluş Başlığı" in fields["54"][0]


def test_parse_inid_block_only_whitelist_codes_match():
    """Codes outside the documented whitelist (e.g. (90), (99)) pass
    through as part of the surrounding value — they don't create
    spurious keys."""
    block = (
        "(11) TR 2025 010000 B\n"
        "(99) Unknown Code Label\n"
        "stray content\n"
        "(54) Real Title\n"
        "Bir Buluş\n"
    )
    fields = parse_inid_block(block)
    # (99) is NOT in the whitelist, so it stays inside (11)'s value
    assert "99" not in fields
    assert "(99)" in fields["11"][0]
    assert fields["54"] == ["Real Title\nBir Buluş"]


def test_parse_inid_block_handles_leading_whitespace_on_inid_lines():
    """Real PDFs often emit indented INID lines after a header — the
    regex must accept tabs / spaces before the open paren."""
    block = "  (11) TR 2025 000001 B\n\t(54) Buluş Başlığı\n  Test\n"
    fields = parse_inid_block(block)
    assert "11" in fields
    assert "54" in fields


def test_parse_inid_block_returns_empty_for_empty_input():
    assert parse_inid_block("") == {}
    assert parse_inid_block(None) == {}  # type: ignore[arg-type]


def test_parse_inid_block_returns_empty_when_no_inid_codes():
    """A page of plain Turkish prose with no INID markers -> {}.

    Important: must not raise, must not partial-parse."""
    page = (
        "AÇIKLAMALAR\n"
        "Bu bülten TÜRKPATENT tarafından aylık olarak yayımlanmaktadır.\n"
        "Bibliyografik bilgiler I.N.I.D. kodları kullanılarak verilmektedir.\n"
    )
    assert parse_inid_block(page) == {}


def test_parse_inid_block_value_preserves_newlines():
    """Per-INID sub-parsers (built in step 3.4) need the newline boundary
    between label and value, so we must not collapse whitespace here."""
    block = "(43) Başvuru Yayın Tarihi\n2024/04/22\n(21) Başvuru Numarası\n2022/014462\n"
    fields = parse_inid_block(block)
    assert "\n" in fields["43"][0]
    assert "\n" in fields["21"][0]


def test_patent_inid_codes_set_matches_documented_whitelist():
    """Sanity guard so an accidental edit doesn't broaden / narrow the
    whitelist — these are the 26 codes documented in
    bulletins/Patent__Faydali_Model/README.md §3."""
    expected = {
        "10", "11", "12", "19", "21", "22", "24",
        "30", "31", "32", "33",
        "43", "44", "45",
        "51", "54", "57",
        "71", "72", "73", "74",
        "86", "87", "88",
        "96", "97",
    }
    assert PATENT_INID_CODES == expected


# ---------------------------------------------------------------------------
# Step 3.2 — normalize_tr_date
# ---------------------------------------------------------------------------

def test_normalize_tr_date_real_cover_page_format():
    """Cover-page format on 2025_08.pdf page 1: '21.08.2025'."""
    assert normalize_tr_date("21.08.2025") == "2025-08-21"
    assert normalize_tr_date("01.01.2024") == "2024-01-01"


def test_normalize_tr_date_extracts_from_label_prefix():
    """The cover page renders 'Yayım Tarihi  21.08.2025' across lines."""
    raw = "Yayım Tarihi  \n21.08.2025"
    assert normalize_tr_date(raw) == "2025-08-21"


def test_normalize_tr_date_returns_none_for_missing_or_bad():
    assert normalize_tr_date(None) is None
    assert normalize_tr_date("") is None
    assert normalize_tr_date("no date here") is None


def test_normalize_tr_date_does_not_match_body_yyyy_format():
    """Body dates like '2024/04/22' are NOT a TR cover-page format and
    must NOT be decoded by this helper. Sanity guard against the two
    formats accidentally aliasing each other."""
    assert normalize_tr_date("2024/04/22") is None


def test_normalize_iso_date_does_not_match_cover_page_format():
    """And the reverse — the body parser must reject DD.MM.YYYY."""
    assert normalize_iso_date("21.08.2025") is None


# ---------------------------------------------------------------------------
# Step 3.2 — extract_bulletin_metadata_from_text (pure)
# ---------------------------------------------------------------------------

def test_extract_bulletin_metadata_real_cover_page_text():
    """Real text captured from page 1 of 2025_08.pdf."""
    page1 = (
        "\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n"
        "Sayı 2025-08 \n"
        "Yayım Tarihi  \n"
        "21.08.2025\n"
    )
    no, date = extract_bulletin_metadata_from_text(page1)
    assert no == "2025-08"
    assert date == "2025-08-21"


def test_extract_bulletin_metadata_handles_dotless_i():
    """The Turkish ı vs i variation must not break the match."""
    page = "Sayi 2024-12\nYayim Tarihi 22.12.2024\n"
    no, date = extract_bulletin_metadata_from_text(page)
    assert no == "2024-12"
    assert date == "2024-12-22"


def test_extract_bulletin_metadata_handles_pre_2023_uppercase_colon_format():
    """REGRESSION: 2019–2022 PDFs use uppercase + colon separator with
    the label and value on different lines.

    Captured shape from real 2022_09.pdf page 1::

        SAYI
                : 2022-09 (EYLÜL)
        YAYIN TARİHİ        : 21.09.2022
    """
    page1_old = (
        "ISSN  1301- 0395\n"
        "RESMİ PATENT BÜLTENİ\n"
        "OFFICIAL PATENT BULLETIN\n"
        "2022\n"
        "SAYI \n"
        "        : 2022-09 (EYLÜL) \n"
        "YAYIN TARİHİ        : 21.09.2022\n"
    )
    no, date = extract_bulletin_metadata_from_text(page1_old)
    assert no == "2022-09"
    assert date == "2022-09-21"


def test_extract_bulletin_metadata_handles_pre_2023_with_dotted_capital_I():
    """The 2019–2022 format spells ``TARİHİ`` with the dotted capital
    İ (Turkish), not ASCII I."""
    page = (
        "SAYI : 2020-07\n"
        "YAYIN TARİHİ : 21.07.2020\n"
    )
    no, date = extract_bulletin_metadata_from_text(page)
    assert no == "2020-07"
    assert date == "2020-07-21"


def test_extract_bulletin_metadata_returns_partial_when_only_one_present():
    no, date = extract_bulletin_metadata_from_text("Sayı 2025-08\n")
    assert no == "2025-08"
    assert date is None

    no, date = extract_bulletin_metadata_from_text("Yayım Tarihi 21.08.2025\n")
    assert no is None
    assert date == "2025-08-21"


def test_extract_bulletin_metadata_returns_both_none_for_unrelated_page():
    """A random body page (no Sayı / Yayım Tarihi) -> (None, None)."""
    body = "(11) TR 2022 014462 B\n(12) Patent Belgesi\n"
    assert extract_bulletin_metadata_from_text(body) == (None, None)


def test_extract_bulletin_metadata_handles_none_and_empty():
    assert extract_bulletin_metadata_from_text(None) == (None, None)
    assert extract_bulletin_metadata_from_text("") == (None, None)


# ---------------------------------------------------------------------------
# Step 3.2 — extract_bulletin_metadata (doc-level)
# ---------------------------------------------------------------------------

class _FakePage:
    def __init__(self, text: str):
        self._text = text
    def get_text(self, kind: str = "text") -> str:
        return self._text


class _FakeDoc:
    def __init__(self, pages):
        self._pages = [_FakePage(t) for t in pages]
    @property
    def page_count(self) -> int:
        return len(self._pages)
    def __getitem__(self, idx):
        return self._pages[idx]


def test_extract_bulletin_metadata_doc_finds_header_on_page_1():
    doc = _FakeDoc([
        "Sayı 2025-08 \nYayım Tarihi  \n21.08.2025\n",
        "(11) TR 2025 ... B\n",
        "more body\n",
    ])
    no, date = extract_bulletin_metadata(doc)
    assert no == "2025-08"
    assert date == "2025-08-21"


def test_extract_bulletin_metadata_doc_aggregates_across_pages():
    """When the bulletin number lives on page 1 but the date is on page 2
    (defensive — never observed in real bundles), both should still be
    found."""
    doc = _FakeDoc([
        "Sayı 2026-01\n",
        "Yayım Tarihi 22.01.2026\n",
        "more body\n",
    ])
    no, date = extract_bulletin_metadata(doc)
    assert no == "2026-01"
    assert date == "2026-01-22"


def test_extract_bulletin_metadata_doc_respects_max_pages():
    """If the header is past max_pages, it's not found — performance
    guardrail, not a hard correctness case."""
    doc = _FakeDoc([
        "blank cover\n", "blank inside\n", "blank toc\n",
        "Sayı 2025-08\nYayım Tarihi 21.08.2025\n",  # page 4 (index 3)
    ])
    no, date = extract_bulletin_metadata(doc, max_pages=3)
    assert no is None and date is None


def test_extract_bulletin_metadata_doc_handles_empty_document():
    doc = _FakeDoc([])
    assert extract_bulletin_metadata(doc) == (None, None)


# ---------------------------------------------------------------------------
# Step 3.3 — extract_kind_code
# ---------------------------------------------------------------------------

def test_extract_kind_code_real_granted_patent():
    """'TR 2022 014462 B' captured from page 200 of 2025_08.pdf."""
    assert extract_kind_code("TR 2022 014462 B") == "B"


def test_extract_kind_code_real_published_app():
    """'TR 2024 000746 A1' captured from page 1850 of 2025_08.pdf."""
    assert extract_kind_code("TR 2024 000746 A1") == "A1"


def test_extract_kind_code_real_ep_fascicle():
    """'TR 2025 010866 T4' captured from page 1000 of 2025_08.pdf."""
    assert extract_kind_code("TR 2025 010866 T4") == "T4"


def test_extract_kind_code_handles_um_kinds():
    """Real shapes for utility-model kinds."""
    assert extract_kind_code("TR 2024 020000 Y") == "Y"
    assert extract_kind_code("TR 2024 020000 U") == "U"
    assert extract_kind_code("TR 2024 020000 U4") == "U4"
    assert extract_kind_code("TR 2024 020000 U5") == "U5"


def test_extract_kind_code_handles_t_family():
    """T / T3 / T4 / T5 / T6 — EP fascicle kinds across record families."""
    for kind in ("T", "T3", "T4", "T5", "T6"):
        assert extract_kind_code(f"TR 2024 020000 {kind}") == kind


def test_extract_kind_code_returns_none_for_missing_or_unparseable():
    assert extract_kind_code(None) is None
    assert extract_kind_code("") is None
    assert extract_kind_code("just plain text") is None
    assert extract_kind_code("TR 2024") is None  # no app-no, no kind


def test_extract_kind_code_ignores_label_prefix():
    """Some (11) values may have stray label text — search, don't full-match."""
    raw = "Yayın No\nTR 2022 014462 B"
    assert extract_kind_code(raw) == "B"


# ---------------------------------------------------------------------------
# Step 3.3 — classify_kind_code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind, expected", [
    # Granted patents: B (regular), T4 (EP-fascicle Turkish translation of grant)
    ("B",  RecordType.GRANTED_PATENT),
    ("T4", RecordType.GRANTED_PATENT),
    # UM grant
    ("Y",  RecordType.GRANTED_UM),
    # Patent applications: A1, A2, T, T3
    ("A1", RecordType.PUBLISHED_APP),
    ("A2", RecordType.PUBLISHED_APP),
    ("T",  RecordType.PUBLISHED_APP),
    ("T3", RecordType.PUBLISHED_APP),
    # UM applications: U, U4, U5, T5, T6
    ("U",  RecordType.PUBLISHED_UM_APP),
    ("U4", RecordType.PUBLISHED_UM_APP),
    ("U5", RecordType.PUBLISHED_UM_APP),
    ("T5", RecordType.PUBLISHED_UM_APP),
    ("T6", RecordType.PUBLISHED_UM_APP),
])
def test_classify_kind_code_documented_mappings(kind, expected):
    assert classify_kind_code(kind) is expected


def test_classify_kind_code_unknown_kinds_fall_through():
    assert classify_kind_code("Z") is RecordType.UNKNOWN
    assert classify_kind_code("X9") is RecordType.UNKNOWN
    assert classify_kind_code("") is RecordType.UNKNOWN
    assert classify_kind_code(None) is RecordType.UNKNOWN


def test_classify_kind_code_is_case_insensitive():
    """Lower/mixed case kind codes still classify correctly."""
    assert classify_kind_code("b") is RecordType.GRANTED_PATENT
    assert classify_kind_code("a1") is RecordType.PUBLISHED_APP


# ---------------------------------------------------------------------------
# Step 3.3 — detect_page_kind
# ---------------------------------------------------------------------------

def test_detect_page_kind_inid_records_when_tokens_present():
    """A page with even one line-anchored INID token is INID_RECORDS."""
    page = "(11) TR 2022 014462 B\n(12) Patent Belgesi\n"
    assert detect_page_kind(page) is PageKind.INID_RECORDS


def test_detect_page_kind_event_index_when_only_appno_lines():
    """Event-index pages have appno lines + Turkish event phrases, no INIDs."""
    page = (
        "2024/010476\n"
        "Yayımlanmış Faydalı Model Başvurularının Araştırma Raporları (6769 SMK)\n"
        "2024/010507\n"
        "Yayımlanmış Patent Başvurularının Araştırma Raporları (6769 SMK)\n"
    )
    assert detect_page_kind(page) is PageKind.EVENT_INDEX


def test_detect_page_kind_skip_for_cover_page():
    page = (
        "Sayı 2025-08\n"
        "Yayım Tarihi  \n"
        "21.08.2025\n"
    )
    assert detect_page_kind(page) is PageKind.SKIP


def test_detect_page_kind_skip_for_toc():
    page = (
        "İÇİNDEKİLER\n"
        "AÇIKLAMALAR ........................................... 5\n"
        "YAYIN İNDEKSLERİ\n"
    )
    assert detect_page_kind(page) is PageKind.SKIP


def test_detect_page_kind_skip_for_empty_or_whitespace():
    assert detect_page_kind(None) is PageKind.SKIP
    assert detect_page_kind("") is PageKind.SKIP
    assert detect_page_kind("   \n\n   ") is PageKind.SKIP


def test_detect_page_kind_inid_takes_priority_over_appno_lines():
    """A page with BOTH INID tokens and appno lines is INID_RECORDS,
    not EVENT_INDEX. Real records have appnos in their (21) values."""
    page = (
        "(11) TR 2022 014462 B\n"
        "(21) Başvuru Numarası\n"
        "2022/014462\n"
    )
    assert detect_page_kind(page) is PageKind.INID_RECORDS


def test_detect_page_kind_event_index_ignores_random_parens():
    """The Turkish event phrase '(6769 SMK)' must NOT be misread as INID
    — it's a 4-digit number, not 2-digit, and it's mid-line anyway."""
    page = (
        "2024/010476\n"
        "Yayımlanmış Faydalı Model Başvurularının Araştırma Raporları (6769 SMK)\n"
    )
    # Confirm classification — (6769) is too long to match the 2-digit
    # whitelist, AND it's mid-line so line-anchoring rejects it anyway.
    assert detect_page_kind(page) is PageKind.EVENT_INDEX


# ---------------------------------------------------------------------------
# Step 3.4 — parse_publication_no
# ---------------------------------------------------------------------------

def test_parse_publication_no_real_grants():
    assert parse_publication_no("TR 2022 014462 B") == "TR 2022 014462 B"
    assert parse_publication_no("TR 2025 010866 T4") == "TR 2025 010866 T4"


def test_parse_publication_no_real_apps():
    assert parse_publication_no("TR 2024 000746 A1") == "TR 2024 000746 A1"
    assert parse_publication_no("TR 2024 000746 A2") == "TR 2024 000746 A2"


def test_parse_publication_no_strips_label_prefix():
    raw = "Yayın No\nTR 2022 014462 A2"
    assert parse_publication_no(raw) == "TR 2022 014462 A2"


def test_parse_publication_no_returns_none_for_missing():
    assert parse_publication_no(None) is None
    assert parse_publication_no("") is None
    assert parse_publication_no("just text, no pub no") is None


# ---------------------------------------------------------------------------
# Step 3.4 — parse_application_no
# ---------------------------------------------------------------------------

def test_parse_application_no_real_format():
    assert parse_application_no("Başvuru Numarası\n2022/014462") == "2022/014462"
    assert parse_application_no("2024/000746") == "2024/000746"


def test_parse_application_no_handles_missing():
    assert parse_application_no(None) is None
    assert parse_application_no("") is None
    assert parse_application_no("no app no here") is None


# ---------------------------------------------------------------------------
# Step 3.4 — parse_date_field
# ---------------------------------------------------------------------------

def test_parse_date_field_extracts_iso_date():
    assert parse_date_field("Başvuru Tarihi\n2022/09/20") == "2022-09-20"
    assert parse_date_field("2025/08/21") == "2025-08-21"


def test_parse_date_field_returns_none_for_missing():
    assert parse_date_field(None) is None
    assert parse_date_field("") is None
    assert parse_date_field("no date") is None


# ---------------------------------------------------------------------------
# Step 3.4 — parse_ipc_classes
# ---------------------------------------------------------------------------

def test_parse_ipc_classes_real_multi_class():
    raw = "Buluşun tasnif sınıfları\nF25B 9/14\nF25D 17/04\nF25D 23/04"
    assert parse_ipc_classes(raw) == ["F25B 9/14", "F25D 17/04", "F25D 23/04"]


def test_parse_ipc_classes_normalises_no_space_form():
    """Codes ship with or without internal whitespace; output is
    consistently ``[main] [sub]`` with one space."""
    raw = "Buluşun tasnif sınıfları\nH02G3/12\nE03C1/02"
    assert parse_ipc_classes(raw) == ["H02G 3/12", "E03C 1/02"]


def test_parse_ipc_classes_dedups_repeated_codes():
    raw = "F25B 9/14\nF25B 9/14\nF25B 9/14"
    assert parse_ipc_classes(raw) == ["F25B 9/14"]


def test_parse_ipc_classes_empty_input_returns_empty_list():
    assert parse_ipc_classes(None) == []
    assert parse_ipc_classes("") == []
    assert parse_ipc_classes("Buluşun tasnif sınıfları") == []  # label only


# ---------------------------------------------------------------------------
# Step 3.4 — parse_title / parse_abstract
# ---------------------------------------------------------------------------

def test_parse_title_drops_label_line():
    raw = "Buluş Başlığı\nNEM KONTROLLÜ HAZNEYE SAHİP BİR BUZDOLABI"
    assert parse_title(raw) == "NEM KONTROLLÜ HAZNEYE SAHİP BİR BUZDOLABI"


def test_parse_title_collapses_multi_line_titles():
    """Long titles wrap across lines in the PDF text-extract."""
    raw = "Buluş Başlığı\nUzatma manşonlu duvara monte\nbağlantı kutusu ünitesi."
    assert parse_title(raw) == "Uzatma manşonlu duvara monte bağlantı kutusu ünitesi."


def test_parse_title_handles_empty():
    assert parse_title(None) is None
    assert parse_title("") is None
    assert parse_title("Buluş Başlığı") is None


def test_parse_abstract_preserves_newlines_for_figure_callouts():
    """The (57) abstract must keep newlines so figure call-outs
    remain readable. Only intra-line whitespace is collapsed."""
    raw = (
        "Özet\n"
        "Bu buluş, bir gövde (2),\n"
        "gövdeye  (2)  erişim sağlayan bir kapı (3) ile ilgilidir.\n"
    )
    out = parse_abstract(raw)
    assert out is not None
    assert "Bu buluş, bir gövde (2)," in out
    assert "kapı (3)" in out
    assert "\n" in out  # newline boundary preserved


def test_parse_abstract_returns_none_for_label_only():
    assert parse_abstract("Özet") is None
    assert parse_abstract(None) is None


# ---------------------------------------------------------------------------
# Step 3.4 — parse_holders (single entity vs name list)
# ---------------------------------------------------------------------------

def test_parse_holders_single_entity_with_address():
    """Real (73) Patent Sahibi shape — single legal entity + address."""
    raw = (
        "Patent Sahibi\n"
        "ARÇELİK ANONİM ŞİRKETİ\n"
        "SÜTLÜCE MAH. KARAAĞAÇ CAD. 6  Beyoğlu\n"
        "İstanbul TÜRKİYE"
    )
    holders = parse_holders(raw)
    assert len(holders) == 1
    h = holders[0]
    assert h.name == "ARÇELİK ANONİM ŞİRKETİ"
    assert "SÜTLÜCE MAH" in h.address
    assert "Beyoğlu" in h.address
    assert "İstanbul" in h.address
    assert h.country == "TÜRKİYE"


def test_parse_holders_list_of_natural_persons():
    """Real (71) Başvuru Sahipleri shape — list of natural-person
    names, no addresses. Each line is one applicant."""
    raw = (
        "Başvuru Sahipleri\n"
        "EMİNE YILDIRIM\n"
        "ZEYNEP ERVA YILDIRIM\n"
        "AHMET ÇARHAN"
    )
    holders = parse_holders(raw)
    assert len(holders) == 3
    assert [h.name for h in holders] == [
        "EMİNE YILDIRIM",
        "ZEYNEP ERVA YILDIRIM",
        "AHMET ÇARHAN",
    ]
    assert all(h.address is None for h in holders)
    assert all(h.country is None for h in holders)


def test_parse_holders_empty_returns_empty_list():
    assert parse_holders(None) == []
    assert parse_holders("") == []
    assert parse_holders("Patent Sahibi") == []


# ---------------------------------------------------------------------------
# Step 3.4 — parse_inventors
# ---------------------------------------------------------------------------

def test_parse_inventors_list_of_names():
    """Real (72) shape: label + names, one per line."""
    raw = (
        "Buluşu Yapanlar\n"
        "NİHAL YILMAZ\n"
        "AYLİN MET ÖZYURT\n"
        "SEÇİL BAYDEMİR\n"
        "FATİH MÜMİNOĞLU\n"
        "ERSİN DÖNMEZ"
    )
    inventors = parse_inventors(raw)
    assert len(inventors) == 5
    assert inventors[0].name == "NİHAL YILMAZ"
    assert inventors[-1].name == "ERSİN DÖNMEZ"


def test_parse_inventors_handles_mixed_case_names():
    """EP fascicle inventors often appear in title case (German names)."""
    raw = "Buluşu Yapanlar\nGünther Lehmann\nThomas Doll\nJürgen Schorer"
    inv = parse_inventors(raw)
    assert [i.name for i in inv] == ["Günther Lehmann", "Thomas Doll", "Jürgen Schorer"]


def test_parse_inventors_empty_returns_empty_list():
    assert parse_inventors(None) == []
    assert parse_inventors("") == []
    assert parse_inventors("Buluşu Yapanlar") == []


# ---------------------------------------------------------------------------
# Step 3.4 — parse_attorney
# ---------------------------------------------------------------------------

def test_parse_attorney_real_two_line_firm():
    """Real (74) shape — name on line 2, firm clause line-wrapped
    across lines 2-3 with unbalanced parens."""
    raw = (
        "Vekil\n"
        "EMİN KORHAN DERİCİOĞLU (ANKARA PATENT\n"
        "BÜROSU ANONİM ŞİRKETİ)"
    )
    a = parse_attorney(raw)
    assert a is not None
    assert a.name == "EMİN KORHAN DERİCİOĞLU"
    assert a.firm == "ANKARA PATENT BÜROSU ANONİM ŞİRKETİ"


def test_parse_attorney_single_line_firm():
    raw = "Vekil\nFULYA SÜMERALP (SİMAJ PATENT DAN. LTD. ŞTİ.)"
    a = parse_attorney(raw)
    assert a is not None
    assert a.name == "FULYA SÜMERALP"
    assert a.firm == "SİMAJ PATENT DAN. LTD. ŞTİ."


def test_parse_attorney_returns_attorney_with_no_firm_when_parens_absent():
    raw = "Vekil\nJANE SMITH"
    a = parse_attorney(raw)
    assert a is not None
    assert a.name == "JANE SMITH"
    assert a.firm is None


def test_parse_attorney_returns_none_for_empty():
    assert parse_attorney(None) is None
    assert parse_attorney("") is None
    assert parse_attorney("Vekil") is None


# ---------------------------------------------------------------------------
# Step 3.4 — parse_priorities
# ---------------------------------------------------------------------------

def test_parse_priorities_real_single_priority():
    """Real (30) value from page 1000 of 2025_08.pdf."""
    raw = ["Rüçhan Bilgileri (32) (33) (31)\n2020/03/24  DE  DE 202010203797"]
    priorities = parse_priorities(raw)
    assert len(priorities) == 1
    p = priorities[0]
    assert p.priority_date == "2020-03-24"
    assert p.country == "DE"
    assert p.priority_no == "DE 202010203797"


def test_parse_priorities_handles_no_priority_data():
    """Common case — header line only, no actual priority claim."""
    raw = ["Rüçhan Bilgileri (32) (33) (31)"]
    assert parse_priorities(raw) == []


def test_parse_priorities_handles_empty():
    assert parse_priorities([]) == []
    assert parse_priorities([""]) == []


# ---------------------------------------------------------------------------
# Step 3.4 — parse_ep_reference (EP fascicle dual (96)/(97) quirk)
# ---------------------------------------------------------------------------

def test_parse_ep_reference_real_ep_fascicle():
    """Real EP fascicle from page 1000 of 2025_08.pdf:

        (96) Başvuru Tarihi          ->  date  in (96)
             2021/03/23
        (97) EP Yayın No             ->  number in (97)
             EP3885497B1
        (97) EP Yayın Tarihi         ->  date  in (97)
             2025/06/04
        (96) EP Başvuru No           ->  number in (96)
             EP21164305.1

    Each INID has TWO values; classify each by content shape."""
    values_96 = ["Başvuru Tarihi\n2021/03/23", "EP Başvuru No\nEP21164305.1"]
    values_97 = ["EP Yayın No\nEP3885497B1", "EP Yayın Tarihi\n2025/06/04"]
    ref = parse_ep_reference(values_96, values_97)
    assert ref is not None
    assert ref.ep_application_date == "2021-03-23"
    assert ref.ep_application_no == "EP21164305.1"
    assert ref.ep_publication_no == "EP3885497B1"
    assert ref.ep_publication_date == "2025-06-04"


def test_parse_ep_reference_handles_reversed_value_order():
    """The PDF doesn't guarantee the date-first ordering — classify by
    content shape, not position."""
    values_96 = ["EP Başvuru No\nEP21164305.1", "Başvuru Tarihi\n2021/03/23"]
    values_97 = ["EP Yayın Tarihi\n2025/06/04", "EP Yayın No\nEP3885497B1"]
    ref = parse_ep_reference(values_96, values_97)
    assert ref is not None
    assert ref.ep_application_date == "2021-03-23"
    assert ref.ep_application_no == "EP21164305.1"


def test_parse_ep_reference_returns_none_when_no_ep_data():
    """A non-EP record has no (96)/(97) values at all -> None."""
    assert parse_ep_reference([], []) is None
    assert parse_ep_reference(None, None) is None


def test_parse_ep_reference_strips_internal_whitespace_in_numbers():
    """An EP number with stray whitespace should still normalize."""
    values_97 = ["EP  3885497  B1"]
    ref = parse_ep_reference([], values_97)
    assert ref is not None
    assert ref.ep_publication_no == "EP3885497B1"


# ---------------------------------------------------------------------------
# Step 3.5 — _build_global_text + _char_pos_to_page
# ---------------------------------------------------------------------------

def test_build_global_text_concatenates_with_inter_page_newlines():
    pages = ["abc", "de", "f"]
    full, starts = _build_global_text(pages)
    # 'abc' + '\n' + 'de' + '\n' + 'f' = 9 chars
    assert full == "abc\nde\nf"
    assert starts == [0, 4, 7]


def test_build_global_text_empty_input():
    full, starts = _build_global_text([])
    assert full == ""
    assert starts == []


def test_build_global_text_preserves_inner_newlines():
    """A page's own newlines are kept (parse_inid_block depends on them)."""
    pages = ["a\nb", "c\nd"]
    full, starts = _build_global_text(pages)
    assert full == "a\nb\nc\nd"
    assert starts == [0, 4]


def test_char_pos_to_page_basic_lookup():
    """page_starts = [0, 4, 7] from _build_global_text(['abc','de','f'])."""
    starts = [0, 4, 7]
    assert _char_pos_to_page(0, starts) == 0
    assert _char_pos_to_page(2, starts) == 0
    assert _char_pos_to_page(3, starts) == 0  # last char of page 0 ('c')
    assert _char_pos_to_page(4, starts) == 1  # first char of page 1
    assert _char_pos_to_page(7, starts) == 2  # first char of page 2
    assert _char_pos_to_page(99, starts) == 2  # past end -> last page


def test_char_pos_to_page_empty_input():
    """Defensive: empty page_starts -> 0 (won't be reached on real docs)."""
    assert _char_pos_to_page(0, []) == 0


# ---------------------------------------------------------------------------
# Step 3.5 — _find_record_boundaries
# ---------------------------------------------------------------------------

def test_find_record_boundaries_finds_each_11_with_valid_pub_no():
    """Three records on three pages — boundaries returned in order, with
    correct page-range mapping."""
    pages = [
        # page 0 (1-indexed 1) — record A
        "(11) TR 2022 014462 B\n(12) Patent Belgesi\n(54) Title A\n",
        # page 1 (1-indexed 2) — record B
        "(11) TR 2024 000746 A1\n(12) Patent Başvurusu\n(54) Title B\n",
        # page 2 (1-indexed 3) — record C
        "(11) TR 2025 010866 T4\n(12) AVRUPA PATENT\n(54) Title C\n",
    ]
    full, starts = _build_global_text(pages)
    bounds = _find_record_boundaries(full, starts)
    assert len(bounds) == 3
    # Pages
    assert [b[2] for b in bounds] == [1, 2, 3]
    assert [b[3] for b in bounds] == [1, 2, 3]


def test_find_record_boundaries_rejects_legend_page_false_match():
    """Legend page has '(12) Başvurunun Türü' but no real (11) — must
    yield zero records. The boundary regex requires a publication-number
    shape after (11)."""
    legend_text = (
        "(11-10)\nYayın Numarası-Patent numarası\n"
        "(12)\nBaşvurunun Türü\n"
        "(21)\nBaşvuru Numarası\n"
    )
    full, starts = _build_global_text([legend_text])
    assert _find_record_boundaries(full, starts) == []


def test_find_record_boundaries_rejects_57_abstract_inline_11():
    """An abstract with mid-line '(11)' figure call-outs is NOT a record
    boundary — we already proved this in step 3.1, here we re-verify
    at the boundary-finder layer."""
    text = (
        "(11) TR 2022 014462 B\n"
        "(57) Özet\n"
        "Bu buluş bir kapı (11) ve bir gövde (12) içerir.\n"
    )
    full, starts = _build_global_text([text])
    bounds = _find_record_boundaries(full, starts)
    assert len(bounds) == 1
    # The single boundary is at the leading (11) — not at the abstract's
    # mid-line (11) reference.


def test_find_record_boundaries_handles_two_records_per_page():
    """Multiple records may share a page — the slice ends just before
    the NEXT record's (11)."""
    one_page = (
        "(11) TR 2022 014462 B\n(54) Title A\n(57) Özet A\n"
        "(11) TR 2024 000746 A1\n(54) Title B\n"
    )
    full, starts = _build_global_text([one_page])
    bounds = _find_record_boundaries(full, starts)
    assert len(bounds) == 2
    # Both boundaries report the same page
    assert all(b[2] == 1 and b[3] == 1 for b in bounds)
    # Slices don't overlap
    assert bounds[0][1] == bounds[1][0]


def test_find_record_boundaries_returns_empty_on_no_records():
    full, starts = _build_global_text(["AÇIKLAMALAR\nBu bülten...\n"])
    assert _find_record_boundaries(full, starts) == []


# ---------------------------------------------------------------------------
# Step 3.5 — parse_full_bibliographic_record
# ---------------------------------------------------------------------------

# Real granted-patent record block captured from page 200 of 2025_08.pdf.
# All real Turkish characters, all per-INID parsers exercised end-to-end.
_REAL_RECORD_BLOCK = (
    "(11) TR 2022 014462 B\n"
    "(12) Patent Belgesi\n"
    "(43) Başvuru Yayın Tarihi\n"
    "2024/04/22, 2024/4 Nolu Bülten\n"
    "(10) Başvuru Yayın No\n"
    "TR 2022 014462 A2\n"
    "(21) Başvuru Numarası\n"
    "2022/014462\n"
    "(22) Başvuru Tarihi\n"
    "2022/09/20\n"
    "(45) Patent Belgesinin Veriliş Tarihi\n"
    "2025/08/21\n"
    "(51) Buluşun tasnif sınıfları\n"
    "F25B 9/14\n"
    "F25D 17/04\n"
    "F25D 23/04\n"
    "(74) Vekil\n"
    "EMİN KORHAN DERİCİOĞLU (ANKARA PATENT\n"
    "BÜROSU ANONİM ŞİRKETİ)\n"
    "(73) Patent Sahibi\n"
    "ARÇELİK ANONİM ŞİRKETİ\n"
    "SÜTLÜCE MAH. KARAAĞAÇ CAD. 6  Beyoğlu\n"
    "İstanbul TÜRKİYE\n"
    "(72) Buluşu Yapanlar\n"
    "NİHAL YILMAZ\n"
    "AYLİN MET ÖZYURT\n"
    "(54) Buluş Başlığı\n"
    "NEM KONTROLLÜ HAZNEYE SAHİP BİR BUZDOLABI\n"
    "(57) Özet\n"
    "Bu buluş, bir gövde (2), gövdeye (2) erişim sağlayan bir kapı (3) ile ilgilidir.\n"
)


def test_parse_full_bibliographic_record_real_granted_patent():
    rec = parse_full_bibliographic_record(
        _REAL_RECORD_BLOCK, record_index=1, page_range=(200, 200),
    )
    assert rec is not None
    assert rec.record_index == 1
    assert rec.page_range == [200, 200]
    assert rec.publication_no == "TR 2022 014462 B"
    assert rec.kind_code == "B"
    assert rec.record_type is RecordType.GRANTED_PATENT
    assert rec.publication_kind_label == "Patent Belgesi"
    assert rec.application_no == "2022/014462"
    assert rec.application_date == "2022-09-20"
    assert rec.publication_date == "2024-04-22"
    assert rec.grant_date == "2025-08-21"
    assert rec.ipc_classes == ["F25B 9/14", "F25D 17/04", "F25D 23/04"]
    assert rec.title == "NEM KONTROLLÜ HAZNEYE SAHİP BİR BUZDOLABI"
    assert rec.abstract is not None and "kapı (3)" in rec.abstract
    # Holders
    assert len(rec.holders) == 1
    assert rec.holders[0].name == "ARÇELİK ANONİM ŞİRKETİ"
    assert rec.holders[0].country == "TÜRKİYE"
    # Inventors
    assert [i.name for i in rec.inventors] == ["NİHAL YILMAZ", "AYLİN MET ÖZYURT"]
    # Attorney
    assert rec.attorney is not None
    assert rec.attorney.name == "EMİN KORHAN DERİCİOĞLU"
    assert rec.attorney.firm == "ANKARA PATENT BÜROSU ANONİM ŞİRKETİ"
    # No priorities, no EP reference
    assert rec.priorities == []
    assert rec.ep_reference is None
    assert rec.figures == []  # populated in step 3.6


def test_parse_full_bibliographic_record_returns_none_for_invalid_block():
    """A block without a valid (11) publication number -> None."""
    bad_block = "(12) Some label\n(54) A title\n"
    rec = parse_full_bibliographic_record(
        bad_block, record_index=1, page_range=(1, 1),
    )
    assert rec is None


def test_parse_full_bibliographic_record_handles_pending_app_71():
    """Pending applications use (71) Başvuru Sahipleri instead of (73)."""
    block = (
        "(11) TR 2024 000746 A1\n"
        "(12) Patent Başvurusu\n"
        "(21) Başvuru Numarası\n"
        "2024/000746\n"
        "(22) Başvuru Tarihi\n"
        "2024/01/22\n"
        "(71) Başvuru Sahipleri\n"
        "EMİNE YILDIRIM\n"
        "AHMET ÇARHAN\n"
        "(54) Buluş Başlığı\n"
        "Test Başlığı\n"
    )
    rec = parse_full_bibliographic_record(block, record_index=1, page_range=(1850, 1850))
    assert rec is not None
    assert rec.record_type is RecordType.PUBLISHED_APP
    assert [h.name for h in rec.holders] == ["EMİNE YILDIRIM", "AHMET ÇARHAN"]


def test_parse_full_bibliographic_record_handles_ep_fascicle():
    """T4-kind records that are EP fascicles get an ep_reference populated."""
    block = (
        "(11) TR 2025 010866 T4\n"
        "(12) AVRUPA PATENT FASİKÜLÜ TÜRKÇE ÇEVİRİSİ\n"
        "(21) Başvuru Numarası\n"
        "2025/010866\n"
        "(96) Başvuru Tarihi\n"
        "2021/03/23\n"
        "(97) EP Yayın No\n"
        "EP3885497B1\n"
        "(97) EP Yayın Tarihi\n"
        "2025/06/04\n"
        "(96) EP Başvuru No\n"
        "EP21164305.1\n"
    )
    rec = parse_full_bibliographic_record(block, record_index=99, page_range=(1000, 1000))
    assert rec is not None
    assert rec.kind_code == "T4"
    assert rec.record_type is RecordType.GRANTED_PATENT
    ep = rec.ep_reference
    assert ep is not None
    assert ep.ep_application_date == "2021-03-23"
    assert ep.ep_application_no == "EP21164305.1"
    assert ep.ep_publication_no == "EP3885497B1"
    assert ep.ep_publication_date == "2025-06-04"


def test_patent_record_dataclass_fields_all_present():
    """Sanity: the dataclass exposes every field downstream stages need."""
    r = PatentRecord(
        record_index=1, page_range=[1, 1],
        publication_no="TR 2022 014462 B", kind_code="B",
        record_type=RecordType.GRANTED_PATENT,
    )
    # Defaults
    assert r.title is None and r.abstract is None
    assert r.ipc_classes == []
    assert r.holders == [] and r.inventors == []
    assert r.attorney is None
    assert r.priorities == []
    assert r.ep_reference is None
    assert r.figures == []


# ---------------------------------------------------------------------------
# Step 3.6 — _normalize_appno_for_filename
# ---------------------------------------------------------------------------

def test_normalize_appno_for_filename_replaces_slash():
    assert _normalize_appno_for_filename("2022/014462") == "2022_014462"
    assert _normalize_appno_for_filename("2024/000746") == "2024_000746"


def test_normalize_appno_for_filename_handles_none_and_empty():
    assert _normalize_appno_for_filename(None) == "unknown"
    assert _normalize_appno_for_filename("") == "unknown"
    assert _normalize_appno_for_filename("  ") == "unknown"


def test_normalize_appno_for_filename_strips_unsafe_chars():
    """Anything that isn't [0-9A-Za-z_] gets folded to underscore."""
    assert _normalize_appno_for_filename("2022/014462!") == "2022_014462_"
    # Real-world: the EP fascicle synthetic format. Letters preserved.
    assert _normalize_appno_for_filename("LEGACY_1996_6_p07") == "LEGACY_1996_6_p07"


# ---------------------------------------------------------------------------
# Step 3.6 — detect_banner_xrefs
# ---------------------------------------------------------------------------

def test_detect_banner_xrefs_above_threshold_classified_as_banner():
    """An xref appearing on >threshold pages is the page banner."""
    inventory = {
        0: [4204, 100],  # banner + figure
        1: [4204, 101],
        2: [4204],
        3: [4204],
        4: [4204],
        5: [4204, 102],  # banner present on 6 pages -> classified
    }
    banners = detect_banner_xrefs(inventory, threshold=5)
    assert banners == {4204}


def test_detect_banner_xrefs_at_threshold_kept_as_real_figure():
    """At-threshold (== threshold) xrefs are KEPT as real figures.
    Strict greater-than is the documented rule."""
    inventory = {i: [42] for i in range(5)}  # exactly 5 pages with xref=42
    banners = detect_banner_xrefs(inventory, threshold=5)
    assert banners == set()


def test_detect_banner_xrefs_handles_multiple_banners():
    """Some PDFs have two recurring header glyphs (e.g. a logo + a
    boilerplate strip). Both should be detected."""
    inventory = {
        0: [100, 200, 1000],
        1: [100, 200, 1001],
        2: [100, 200],
        3: [100, 200],
        4: [100, 200],
        5: [100, 200],
    }
    banners = detect_banner_xrefs(inventory, threshold=4)
    assert banners == {100, 200}


def test_detect_banner_xrefs_empty_inventory():
    assert detect_banner_xrefs({}) == set()


def test_detect_banner_xrefs_real_2025_08_proportions():
    """Synthetic inventory mimicking 2025_08.pdf's documented shape:
    one banner xref on ~1600 pages, ~190 unique drawings each on
    1-3 pages. Result: only the banner is classified."""
    inventory = {}
    inventory[0] = [4204, 1]
    for i in range(1, 1600):
        inventory[i] = [4204]
    for i in range(1600, 1700):
        inventory[i] = [200 + (i - 1600), 4204]
    banners = detect_banner_xrefs(inventory, threshold=5)
    assert banners == {4204}


# ---------------------------------------------------------------------------
# Step 3.6 — build_figure_inventory + extract_record_figures (live)
# ---------------------------------------------------------------------------

# Use the same real PDF the rest of the live tests rely on.
_REAL_PDF = Path(
    "C:/Users/701693/turk_patent/bulletins/Patent__Faydali_Model/2025_08.pdf"
)


@pytest.mark.skipif(
    not _REAL_PDF.is_file(),
    reason=f"Real PDF {_REAL_PDF.name} not on disk; skipping integration smoke",
)
def test_build_figure_inventory_real_2025_08_smoke():
    """End-to-end smoke: walk the real 1976-page 2025_08.pdf, count
    image references and uniques. Anchored to the README's documented
    numbers (~1,806 references / ~195 uniques) with reasonable slack."""
    import fitz
    doc = fitz.open(str(_REAL_PDF))
    inventory = build_figure_inventory(doc)
    assert len(inventory) == doc.page_count

    total_refs = sum(len(xrefs) for xrefs in inventory.values())
    unique_xrefs = {x for xrefs in inventory.values() for x in xrefs}

    # Refs / uniques should be in the documented ballpark
    assert 1500 <= total_refs <= 2200, f"unexpected total references: {total_refs}"
    assert 100 <= len(unique_xrefs) <= 250, f"unexpected unique xrefs: {len(unique_xrefs)}"

    banners = detect_banner_xrefs(inventory)
    # Exactly one banner xref expected (the header strip)
    assert 1 <= len(banners) <= 3
    doc.close()


@pytest.mark.skipif(
    not _REAL_PDF.is_file(),
    reason=f"Real PDF {_REAL_PDF.name} not on disk; skipping integration smoke",
)
def test_extract_record_figures_dry_run_real_pdf(tmp_path):
    """Dry run (save_images=False) on the first 50 records of the real
    PDF. Verifies the metadata shape without doing any disk I/O."""
    import fitz
    from pdf_extract_patent import (
        _build_global_text, _find_record_boundaries,
        parse_full_bibliographic_record,
    )
    doc = fitz.open(str(_REAL_PDF))

    page_texts = [doc[i].get_text("text") for i in range(doc.page_count)]
    full, starts = _build_global_text(page_texts)
    boundaries = _find_record_boundaries(full, starts)
    inventory = build_figure_inventory(doc)
    banners = detect_banner_xrefs(inventory)

    sampled_with_figures = 0
    for i, (start, end, sp, ep) in enumerate(boundaries[:50]):
        block = full[start:end]
        rec = parse_full_bibliographic_record(
            block, record_index=i + 1, page_range=(sp, ep),
        )
        if rec is None:
            continue
        figures = extract_record_figures(
            doc, rec, banner_xrefs=banners,
            figures_dir=None, save_images=False,
        )
        for f in figures:
            assert f["page"] in range(sp, ep + 1)
            assert f["xref"] not in banners
            assert f["image_path"] is None  # dry run
        if figures:
            sampled_with_figures += 1

    # Some records should have figures (granted patents typically do)
    assert sampled_with_figures > 0, \
        "expected at least 1 of the first 50 records to have a figure"
    doc.close()


# ---------------------------------------------------------------------------
# Step 3.7 — _record_to_dict
# ---------------------------------------------------------------------------

def test_record_to_dict_drops_none_optionals():
    """Unset attorney / ep_reference must NOT appear as 'attorney': null."""
    rec = PatentRecord(
        record_index=1, page_range=[1, 1],
        publication_no="TR 2022 014462 B", kind_code="B",
        record_type=RecordType.GRANTED_PATENT,
    )
    d = _record_to_dict(rec)
    assert "attorney" not in d
    assert "ep_reference" not in d


def test_record_to_dict_keeps_attorney_when_set():
    rec = PatentRecord(
        record_index=1, page_range=[1, 1],
        publication_no="TR 2022 014462 B", kind_code="B",
        record_type=RecordType.GRANTED_PATENT,
        attorney=Attorney(name="Jane Doe", firm="DOE LAW"),
    )
    d = _record_to_dict(rec)
    assert d["attorney"] == {"name": "Jane Doe", "firm": "DOE LAW"}


def test_record_to_dict_coerces_record_type_to_plain_string():
    """JSON consumers expect 'GRANTED_PATENT', not <RecordType.GRANTED_PATENT>."""
    rec = PatentRecord(
        record_index=1, page_range=[1, 1],
        publication_no="TR 2022 014462 B", kind_code="B",
        record_type=RecordType.GRANTED_PATENT,
    )
    d = _record_to_dict(rec)
    assert d["record_type"] == "GRANTED_PATENT"
    assert isinstance(d["record_type"], str)


def test_record_to_dict_serializes_full_record_to_json():
    """End-to-end: dataclass -> dict -> json.dumps without crashing."""
    import json
    rec = PatentRecord(
        record_index=42, page_range=[200, 200],
        publication_no="TR 2022 014462 B", kind_code="B",
        record_type=RecordType.GRANTED_PATENT,
        title="Test", abstract="Body",
        ipc_classes=["F25B 9/14"],
        holders=[Holder(name="ACME", country="TR")],
        inventors=[Inventor(name="JANE")],
        attorney=Attorney(name="DOE", firm="DOE LAW"),
        priorities=[Priority(priority_no="X", priority_date="2024-01-01", country="DE")],
        ep_reference=EPReference(
            ep_application_no="EP1", ep_application_date="2021-01-01",
            ep_publication_no="EP2", ep_publication_date="2022-01-01",
        ),
    )
    s = json.dumps(_record_to_dict(rec), ensure_ascii=False)
    assert "GRANTED_PATENT" in s
    assert "TR 2022 014462 B" in s


# ---------------------------------------------------------------------------
# Step 3.7 — parse_pdf (LIVE end-to-end)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _REAL_PDF.is_file(),
    reason=f"Real PDF {_REAL_PDF.name} not on disk; skipping integration smoke",
)
def test_parse_pdf_real_2025_08_dry_run():
    """End-to-end smoke: full 2025_08.pdf -> dict, no image files written.

    Hard checks anchored to numbers verified in earlier sub-steps:
      - bulletin_no == '2025-08', bulletin_date == '2025-08-21'
      - 1,613 records, 0 unparseable boundaries
      - 840 GRANTED_PATENT / 468 PUBLISHED_APP / 171 PUBLISHED_UM_APP /
        134 GRANTED_UM
      - 572 EP fascicles
      - 2 banner xrefs dropped
      - one specific record (2017/15048 first granted patent on page 200)
        has the expected populated fields
      - JSON-serialisable end-to-end
    """
    payload = parse_pdf(_REAL_PDF, save_images=False)

    # Header
    assert payload["bulletin_no"]   == "2025-08"
    assert payload["bulletin_date"] == "2025-08-21"
    assert payload["source_pdf"]    == "2025_08.pdf"
    assert payload["page_count"]    == 1976

    # Stats
    s = payload["stats"]
    assert s["records"]              == 1613
    assert s["by_record_type"]["GRANTED_PATENT"]   == 840
    assert s["by_record_type"]["PUBLISHED_APP"]    == 468
    assert s["by_record_type"]["PUBLISHED_UM_APP"] == 171
    assert s["by_record_type"]["GRANTED_UM"]       == 134
    assert s["ep_fascicles"]         == 572
    assert s["banner_xrefs_dropped"] == 2
    assert s["boundaries_unparseable"] == 0

    # Record list shape
    records = payload["records"]
    assert len(records) == 1613
    sample = next(r for r in records if r["publication_no"] == "TR 2022 014462 B")
    assert sample["record_type"] == "GRANTED_PATENT"
    assert sample["application_no"] == "2022/014462"
    assert sample["title"] == "NEM KONTROLLÜ HAZNEYE SAHİP BİR BUZDOLABI"
    assert sample["ipc_classes"] == ["F25B 9/14", "F25D 17/04", "F25D 23/04"]

    # JSON-serialisable
    import json
    out = json.dumps(payload, ensure_ascii=False)
    assert len(out) > 100_000  # several MB expected


@pytest.mark.skipif(
    not _REAL_PDF.is_file(),
    reason=f"Real PDF {_REAL_PDF.name} not on disk; skipping integration smoke",
)
def test_parse_pdf_real_2025_08_writes_figures(tmp_path):
    """parse_pdf with save_images=True actually writes some PNG files."""
    payload = parse_pdf(
        _REAL_PDF,
        figures_dir=tmp_path,
        save_images=True,
    )
    # At least 100 figures should land (real has ~190 unique drawings)
    written = list(tmp_path.glob("*.png"))
    assert len(written) >= 100, f"only {len(written)} figures written"
    # Each non-empty
    for p in written[:5]:
        assert p.stat().st_size > 0
    # Figure metadata in payload references real files
    sample_with_fig = next(
        (r for r in payload["records"] if r["figures"]), None
    )
    assert sample_with_fig is not None
    fig0 = sample_with_fig["figures"][0]
    assert fig0["image_path"] is not None
    assert (tmp_path / Path(fig0["image_path"]).name).is_file()


# ---------------------------------------------------------------------------
# Step 3.8 — metadata_filename + figures_dirname
# ---------------------------------------------------------------------------

def test_pdf_metadata_filename_constant_canonical():
    """The PDF JSON inside the bulletin parent folder is always
    pdf_metadata.json — distinct from CD's cd_metadata.json so the
    reconciler can read both as separate inputs."""
    assert PDF_METADATA_FILENAME == "pdf_metadata.json"


def test_figures_dirname_constant_canonical():
    """Figures land in figures/ inside the bulletin parent folder."""
    assert FIGURES_DIRNAME == "figures"


# ---------------------------------------------------------------------------
# Step 3.8 — _metadata_is_fresh
# ---------------------------------------------------------------------------

def test_metadata_is_fresh_returns_false_when_json_missing(tmp_path):
    pdf = tmp_path / "2025_08.pdf"
    pdf.write_bytes(b"%PDF-1.6")
    json_path = tmp_path / "2025_08_pdf_metadata.json"
    assert _metadata_is_fresh(pdf, json_path) is False


def test_metadata_is_fresh_returns_false_when_json_empty(tmp_path):
    pdf = tmp_path / "2025_08.pdf"
    pdf.write_bytes(b"%PDF-1.6")
    json_path = tmp_path / "2025_08_pdf_metadata.json"
    json_path.write_text("", encoding="utf-8")
    assert _metadata_is_fresh(pdf, json_path) is False


def test_metadata_is_fresh_returns_true_when_json_newer_than_pdf(tmp_path):
    pdf = tmp_path / "2025_08.pdf"
    pdf.write_bytes(b"%PDF-1.6")
    import os, time
    # Force JSON mtime > PDF mtime
    older = time.time() - 100
    os.utime(pdf, (older, older))
    json_path = tmp_path / "2025_08_pdf_metadata.json"
    json_path.write_text("{}", encoding="utf-8")
    assert _metadata_is_fresh(pdf, json_path) is True


def test_metadata_is_fresh_returns_false_when_json_older_than_pdf(tmp_path):
    import os, time
    pdf = tmp_path / "2025_08.pdf"
    pdf.write_bytes(b"%PDF-1.6")
    json_path = tmp_path / "2025_08_pdf_metadata.json"
    json_path.write_text("{}", encoding="utf-8")
    # Force JSON mtime < PDF mtime
    older = time.time() - 100
    os.utime(json_path, (older, older))
    assert _metadata_is_fresh(pdf, json_path) is False


# ---------------------------------------------------------------------------
# Step 3.8 — parse_argv
# ---------------------------------------------------------------------------

def test_parse_argv_with_explicit_pdf(tmp_path):
    pdf = tmp_path / "2025_08.pdf"
    pdf.write_bytes(b"")
    args = parse_argv(["--pdf", str(pdf)])
    assert args.pdf_paths == [pdf]
    assert args.save_images is True
    assert args.force is False


def test_parse_argv_supports_repeated_pdf(tmp_path):
    a = tmp_path / "2025_08.pdf"
    b = tmp_path / "2024_07.pdf"
    a.write_bytes(b""); b.write_bytes(b"")
    args = parse_argv(["--pdf", str(a), "--pdf", str(b)])
    assert args.pdf_paths == [a, b]


def test_parse_argv_all_globs_bulletins_dir(tmp_path):
    """--all picks up every *.pdf alphabetically."""
    (tmp_path / "2025_08.pdf").write_bytes(b"")
    (tmp_path / "2024_07.pdf").write_bytes(b"")
    (tmp_path / "2025_12_CD.rar").write_bytes(b"")  # NOT picked up
    args = parse_argv(["--all", "--bulletins-dir", str(tmp_path)])
    names = [p.name for p in args.pdf_paths]
    assert names == ["2024_07.pdf", "2025_08.pdf"]


def test_parse_argv_all_errors_on_empty_dir(tmp_path):
    """--all against an empty bulletins folder is a hard error."""
    with pytest.raises(SystemExit):
        parse_argv(["--all", "--bulletins-dir", str(tmp_path)])


def test_parse_argv_rejects_no_input():
    with pytest.raises(SystemExit):
        parse_argv([])


def test_parse_argv_rejects_pdf_and_all_together(tmp_path):
    pdf = tmp_path / "2025_08.pdf"
    pdf.write_bytes(b"")
    with pytest.raises(SystemExit):
        parse_argv(["--pdf", str(pdf), "--all", "--bulletins-dir", str(tmp_path)])


def test_parse_argv_no_images_flag(tmp_path):
    pdf = tmp_path / "2025_08.pdf"
    pdf.write_bytes(b"")
    args = parse_argv(["--pdf", str(pdf), "--no-images"])
    assert args.save_images is False


def test_parse_argv_force_flag(tmp_path):
    pdf = tmp_path / "2025_08.pdf"
    pdf.write_bytes(b"")
    args = parse_argv(["--pdf", str(pdf), "--force"])
    assert args.force is True


def test_parse_argv_out_dir_defaults_to_bulletins_dir(tmp_path):
    pdf = tmp_path / "2025_08.pdf"
    pdf.write_bytes(b"")
    args = parse_argv(["--pdf", str(pdf), "--bulletins-dir", str(tmp_path)])
    assert args.out_dir == tmp_path


# ---------------------------------------------------------------------------
# Step 3.8 — main returns nonzero on missing source
# ---------------------------------------------------------------------------

def test_dedup_pdf_pngs_drops_duplicates_and_removes_payload_entries(tmp_path):
    """When a CD TIFF for app X exists in figures/, any PDF PNG with
    matching {year}_{appno} prefix is dropped from disk AND its figure
    entry is removed from ``record['figures']`` so DB ingest does not
    see a stub row pointing at a non-existent file."""
    figures = tmp_path / "figures"
    figures.mkdir()
    # CD TIFFs (already there from a prior cd_extract run)
    (figures / "2017_15048.tif").write_bytes(b"TIFF")
    (figures / "2018_13083.tif").write_bytes(b"TIFF")
    # PDF PNGs (just written by parse_pdf)
    duplicate_a = figures / "2017_15048_p120_2.png"
    duplicate_a.write_bytes(b"PNG")
    duplicate_b = figures / "2018_13083_p200_3.png"
    duplicate_b.write_bytes(b"PNG")
    standalone = figures / "2099_99999_p1_1.png"  # no matching CD TIFF
    standalone.write_bytes(b"PNG")

    payload = {
        "records": [
            {
                "application_no": "2017/15048",
                "figures": [
                    # one duplicate, one standalone-on-same-record
                    {"image_path": "figures/2017_15048_p120_2.png",
                     "page": 120, "xref": 4204},
                    {"image_path": "figures/2017_15048_p121_1.png",
                     "page": 121, "xref": 4500},
                ],
            },
            {
                "application_no": "2099/99999",
                "figures": [
                    {"image_path": "figures/2099_99999_p1_1.png",
                     "page": 1, "xref": 5000},
                ],
            },
        ],
    }
    dropped = _dedup_pdf_pngs_against_cd_tifs(figures, payload)

    assert dropped == 2
    # Duplicate PNGs deleted
    assert not duplicate_a.exists()
    assert not duplicate_b.exists()
    # Standalone PNG (no CD match) survives
    assert standalone.exists()
    # CD TIFFs untouched
    assert (figures / "2017_15048.tif").is_file()
    # First record: duplicate entry removed, sibling non-dup entry preserved
    figs0 = payload["records"][0]["figures"]
    assert len(figs0) == 1
    assert figs0[0]["image_path"] == "figures/2017_15048_p121_1.png"
    assert figs0[0]["page"] == 121
    # Second record (no CD match) untouched
    figs1 = payload["records"][1]["figures"]
    assert len(figs1) == 1
    assert figs1[0]["image_path"] == "figures/2099_99999_p1_1.png"


def test_dedup_pdf_pngs_returns_zero_when_no_cd_tifs(tmp_path):
    """No CD TIFFs in the dir → no work, nothing dropped."""
    figures = tmp_path / "figures"
    figures.mkdir()
    (figures / "x_p1_1.png").write_bytes(b"PNG")
    payload = {"records": []}
    assert _dedup_pdf_pngs_against_cd_tifs(figures, payload) == 0
    assert (figures / "x_p1_1.png").is_file()


def test_main_returns_nonzero_on_missing_pdf(tmp_path):
    """main() with --pdf pointing at a non-existent file logs a skip
    and returns exit code 1."""
    ghost = tmp_path / "no_such.pdf"
    rc = main([
        "--pdf", str(ghost),
        "--out-dir", str(tmp_path),
        "--no-images",
    ])
    assert rc == 1


# ---------------------------------------------------------------------------
# Step 3.8 — LIVE main smoke (skipped if real PDF absent)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _REAL_PDF.is_file(),
    reason=f"Real PDF {_REAL_PDF.name} not on disk; skipping integration smoke",
)
def test_main_real_2025_08_smoke(tmp_path):
    """End-to-end CLI smoke against the real 2025_08.pdf, --no-images
    so the test stays fast (~10s).

    Verifies:
      - exit code 0
      - bulletin parent folder lands at PT_2025_8_2025-08-21/ under out_dir
      - pdf_metadata.json + bulletin.pdf inside; figures/ skipped due to
        --no-images
      - sidecar size in megabyte range, valid JSON, expected stats
      - re-running without --force is a no-op skip (exit 0, JSON mtime
        unchanged)
    """
    rc = main([
        "--pdf", str(_REAL_PDF),
        "--out-dir", str(tmp_path),
        "--no-images",
    ])
    assert rc == 0

    parent = tmp_path / "PT_2025_8_2025-08-21"
    assert parent.is_dir()

    json_path = parent / "pdf_metadata.json"
    assert json_path.is_file()
    assert json_path.stat().st_size > 1_000_000  # several MB
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["bulletin_no"] == "2025-08"
    assert payload["stats"]["records"] == 1613

    # bulletin.pdf is a copy of the source
    bulletin_pdf = parent / "bulletin.pdf"
    assert bulletin_pdf.is_file()
    assert bulletin_pdf.stat().st_size == _REAL_PDF.stat().st_size

    # No figures/ subdir because --no-images
    assert not (parent / "figures").exists()

    # Re-run: should skip-if-fresh (exit 0, JSON mtime unchanged)
    mtime_before = json_path.stat().st_mtime
    rc2 = main([
        "--pdf", str(_REAL_PDF),
        "--out-dir", str(tmp_path),
        "--no-images",
    ])
    assert rc2 == 0
    mtime_after = json_path.stat().st_mtime
    assert mtime_after == mtime_before  # not re-written

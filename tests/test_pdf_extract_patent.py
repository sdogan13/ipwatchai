"""Unit tests for ``pdf_extract_patent`` helpers.

Built one helper at a time. Each step adds its own test block so failures
point cleanly at the unit under test.
"""

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
    _build_global_text,
    _char_pos_to_page,
    _find_record_boundaries,
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

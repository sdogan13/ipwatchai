"""Unit tests for ``pdf_extract_patent`` helpers.

Built one helper at a time. Each step adds its own test block so failures
point cleanly at the unit under test.
"""

from pathlib import Path

import pytest

from pdf_extract_patent import (
    PATENT_INID_CODES,
    clean_text,
    extract_bulletin_metadata,
    extract_bulletin_metadata_from_text,
    normalize_iso_date,
    normalize_tr_date,
    parse_inid_block,
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

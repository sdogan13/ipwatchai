"""Unit tests for ``pdf_extract_patent_events``.

Pure helpers tested here. The PDF-walking + write-back paths are
covered by the live smoke at the bottom of step 7.3 (gated on the
real PDF being on disk).
"""
from __future__ import annotations

import pytest

from pdf_extract_patent_events import (
    EVENT_TYPE_UNKNOWN,
    _normalise_phrase,
    classify_event_phrase,
    event_fingerprint,
)


# ---------------------------------------------------------------------------
# _normalise_phrase
# ---------------------------------------------------------------------------


def test_normalise_phrase_collapses_whitespace() -> None:
    assert _normalise_phrase("  Hello   world  ") == "Hello world"


def test_normalise_phrase_collapses_pdf_line_breaks() -> None:
    """PyMuPDF inserts line breaks inside long phrases. Normalisation
    must collapse them so classifier finds the canonical match."""
    raw = (
        "6769 Sayılı SMK'nın 99 uncu Maddesi Hükmü Uyarınca YIDK Tarafından Patent Hakkının\n"
        "Değiştirilmiş Haliyle Devamına Karar Verilen Patentler"
    )
    assert _normalise_phrase(raw) == (
        "6769 Sayılı SMK'nın 99 uncu Maddesi Hükmü Uyarınca YIDK Tarafından "
        "Patent Hakkının Değiştirilmiş Haliyle Devamına Karar Verilen Patentler"
    )


def test_normalise_phrase_strips_trailing_punctuation() -> None:
    """Trailing dots/colons/semicolons don't differentiate phrases."""
    assert _normalise_phrase("Reddedilen Patent.") == "Reddedilen Patent"
    assert _normalise_phrase("Reddedilen Patent;") == "Reddedilen Patent"
    assert _normalise_phrase("Reddedilen Patent:") == "Reddedilen Patent"


def test_normalise_phrase_handles_empty() -> None:
    assert _normalise_phrase("") == ""
    assert _normalise_phrase(None) == ""
    assert _normalise_phrase("   ") == ""


# ---------------------------------------------------------------------------
# classify_event_phrase
# ---------------------------------------------------------------------------


def test_classify_known_phrase_exact_match() -> None:
    """All 16 canonical phrases classify correctly (exact strings from
    the lookup table)."""
    assert classify_event_phrase(
        "Verilen Patent / Faydalı Model İlanı (6769 SMK)"
    ) == "GRANT_ANNOUNCED"
    assert classify_event_phrase(
        "Kesinleşen Patent Verilme Kararının İlanı (6769 SMK)"
    ) == "GRANT_FINALIZED"
    assert classify_event_phrase(
        "Reddedilen Patent/Faydalı Model Başvurularının İlanı (6769 SMK)"
    ) == "APPLICATION_REJECTED"
    assert classify_event_phrase(
        "Devir İşlemi Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)"
    ) == "ASSIGNMENT_RECORDED"
    assert classify_event_phrase(
        "Birleşme İşlemi Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)"
    ) == "MERGER_RECORDED"
    assert classify_event_phrase(
        "Faydalı Modele Dönüşüm İlanı (6769 SMK)"
    ) == "CONVERSION_TO_UM"
    assert classify_event_phrase(
        "Patent/FM Model Başvurularında/Belgelerinde Yayından Sonraki Değişikliğin İlanı"
    ) == "POST_PUB_AMENDMENT"
    assert classify_event_phrase(
        "Verilen Patent/FM Belgelerinin Yıllık Ücretinin Ödenmemesi Nedeniyle Geçersizlik ilanı"
    ) == "GRANT_FEE_LAPSE"
    assert classify_event_phrase(
        "Patent/FM Başvurularının Yıllık Ücretinin Ödenmemesi Nedeniyle Geçersizlik ilanı"
    ) == "APPLICATION_FEE_LAPSE"
    assert classify_event_phrase(
        "Yıllık Ücretlerinin Ödenmemesi Nedeniyle Geçersiz Olan Patent/FM Belgelerinin Yeniden Geçerlilik İlanı"
    ) == "FEE_REVALIDATION"
    assert classify_event_phrase(
        "Yeniden Geçerlilik Kazanan Patent/Faydalı Model Başvurularının İlanı (İşlemlerin Devam Ettirilmesi)"
    ) == "PROCEDURAL_REVALIDATION"
    assert classify_event_phrase(
        "Kullanma/Kullanmama Beyanı Verilmemiş Olan Başvuru veya Patent/Faydalı Modellerin İlanı"
    ) == "USE_NONUSE_DECLARATION_MISSING"
    assert classify_event_phrase(
        "Kullanıldığı Beyanı Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)"
    ) == "USE_DECLARATION_RECORDED"
    assert classify_event_phrase(
        "Yayımlanmış Patent Başvurularının Araştırma Raporları (6769 SMK)"
    ) == "SEARCH_REPORT_PATENT"
    assert classify_event_phrase(
        "Yayımlanmış Faydalı Model Başvurularının Araştırma Raporları (6769 SMK)"
    ) == "SEARCH_REPORT_UM"


def test_classify_handles_pdf_line_break_in_phrase() -> None:
    """The YIDK phrase wraps to 2 lines in real PDF extraction; must
    still classify as YIDK_AMENDED_CONTINUATION."""
    assert classify_event_phrase(
        "6769 Sayılı SMK'nın 99 uncu Maddesi Hükmü Uyarınca YIDK Tarafından Patent Hakkının\n"
        "Değiştirilmiş Haliyle Devamına Karar Verilen Patentler"
    ) == "YIDK_AMENDED_CONTINUATION"


def test_classify_distinguishes_grant_vs_application_fee_lapse() -> None:
    """The two fee-lapse phrases differ only by 'Belgelerin' vs
    'Başvurularının' — must not collapse to the same event_type."""
    grant = classify_event_phrase(
        "Verilen Patent/FM Belgelerinin Yıllık Ücretinin Ödenmemesi Nedeniyle Geçersizlik ilanı"
    )
    app = classify_event_phrase(
        "Patent/FM Başvurularının Yıllık Ücretinin Ödenmemesi Nedeniyle Geçersizlik ilanı"
    )
    assert grant == "GRANT_FEE_LAPSE"
    assert app == "APPLICATION_FEE_LAPSE"
    assert grant != app


def test_classify_distinguishes_search_report_patent_vs_um() -> None:
    """Patent search reports vs UM search reports — separate types."""
    assert classify_event_phrase(
        "Yayımlanmış Patent Başvurularının Araştırma Raporları (6769 SMK)"
    ) == "SEARCH_REPORT_PATENT"
    assert classify_event_phrase(
        "Yayımlanmış Faydalı Model Başvurularının Araştırma Raporları (6769 SMK)"
    ) == "SEARCH_REPORT_UM"


def test_classify_unknown_phrase() -> None:
    """Phrase not in the table → UNKNOWN. Caller preserves free_text
    so the mapping can be extended later without re-extracting."""
    assert classify_event_phrase("Some Unknown Event Description") == EVENT_TYPE_UNKNOWN


def test_classify_empty_input() -> None:
    assert classify_event_phrase("") == EVENT_TYPE_UNKNOWN
    assert classify_event_phrase(None) == EVENT_TYPE_UNKNOWN
    assert classify_event_phrase("   ") == EVENT_TYPE_UNKNOWN


# ---------------------------------------------------------------------------
# event_fingerprint
# ---------------------------------------------------------------------------


def test_event_fingerprint_stable_across_calls() -> None:
    """Same inputs → same fingerprint. The patent_events UNIQUE
    constraint relies on this for re-ingest dedup."""
    a = event_fingerprint("2025/8", "2021/001903", "POST_PUB_AMENDMENT", "desc")
    b = event_fingerprint("2025/8", "2021/001903", "POST_PUB_AMENDMENT", "desc")
    assert a == b


def test_event_fingerprint_differs_on_different_event_types() -> None:
    """Same app + bulletin but different event_type → different fp.
    Critical for the case where one app has multiple events on one page."""
    a = event_fingerprint("2025/8", "2021/010013", "GRANT_FINALIZED", "G")
    b = event_fingerprint("2025/8", "2021/010013", "YIDK_AMENDED_CONTINUATION", "Y")
    assert a != b


def test_event_fingerprint_differs_on_different_apps() -> None:
    a = event_fingerprint("2025/8", "2021/000001", "GRANT_FINALIZED", "G")
    b = event_fingerprint("2025/8", "2021/000002", "GRANT_FINALIZED", "G")
    assert a != b


def test_event_fingerprint_differs_on_different_bulletins() -> None:
    """Same event in different bulletins should be distinct rows
    (re-emitted/re-published). Different fingerprints."""
    a = event_fingerprint("2025/8", "2021/000001", "GRANT_FINALIZED", "G")
    b = event_fingerprint("2025/9", "2021/000001", "GRANT_FINALIZED", "G")
    assert a != b


def test_event_fingerprint_truncates_freetext_for_stability() -> None:
    """Long free_text differing only past 200 chars → same fingerprint
    (PDF text-extraction whitespace artefacts shouldn't change dedup)."""
    long_a = "x" * 200 + "trailing-A-content"
    long_b = "x" * 200 + "trailing-B-content"
    a = event_fingerprint("2025/8", "X", "GRANT_FINALIZED", long_a)
    b = event_fingerprint("2025/8", "X", "GRANT_FINALIZED", long_b)
    assert a == b


def test_event_fingerprint_returns_16_hex_chars() -> None:
    fp = event_fingerprint("2025/8", "2021/001903", "POST_PUB_AMENDMENT", "x")
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)

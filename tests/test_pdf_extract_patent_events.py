"""Unit tests for ``pdf_extract_patent_events``.

Pure helpers tested here. The PDF-walking + write-back paths are
covered by the live smoke at the bottom of step 7.3 (gated on the
real PDF being on disk).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_extract_patent_events import (
    EVENT_TYPE_UNKNOWN,
    ParsedEvent,
    _normalise_phrase,
    classify_event_phrase,
    event_fingerprint,
    parse_event_index_page,
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
    ) == "GRANT_FEE_REVALIDATION"
    assert classify_event_phrase(
        "Yıllık Ücretlerinin Ödenmemesi Nedeniyle Geçersiz Olan Patent/FM Başvurularının Yeniden Geçerlilik İlanı"
    ) == "APPLICATION_FEE_REVALIDATION"
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


def test_classify_pre_2017_legacy_551_phrases() -> None:
    """Pre-2017 (551 KHK era) bulletins use a different vocabulary —
    two-track exam/non-exam system, simpler search-report wording, no
    "(6769 SMK)" suffix on the publication phrase. The classifier must
    map these to dedicated _LEGACY_551 event_types or to the modern
    canonical event_type when the meaning is identical."""
    # Spelling variant of APPLICATION_PUBLISHED (no SMK suffix).
    assert classify_event_phrase(
        "Başvuru Yayınının İlanı"
    ) == "APPLICATION_PUBLISHED"
    # Two-track system events (551-only concept, distinct event_type).
    assert classify_event_phrase(
        "İncelemeli Sistem Tercihinin İlanı"
    ) == "EXAM_SYSTEM_CHOICE_LEGACY_551"
    assert classify_event_phrase(
        "İncelemesiz Sistem Tercihinin İlanı"
    ) == "NONEXAM_SYSTEM_CHOICE_LEGACY_551"
    # Generic search-report announcement (legacy form).
    assert classify_event_phrase(
        "Araştırma Raporunun İlanı"
    ) == "SEARCH_REPORT_LEGACY_551"
    # POST_PUB_AMENDMENT spelling variant ("Patent/FM Model" no slash).
    assert classify_event_phrase(
        "Patent/FM Model Başvurularında Yayından Sonraki Değişikliğin İlanı"
    ) == "POST_PUB_AMENDMENT"
    # PCT phase II national entry (rare; legacy).
    assert classify_event_phrase(
        "PCT II. Kısımdan Gelen Başvuruların İlanı"
    ) == "PCT_PHASE_II_ENTRY"


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


# ---------------------------------------------------------------------------
# parse_event_index_page
# ---------------------------------------------------------------------------


def test_parse_event_index_page_real_page_7_excerpt() -> None:
    """Verbatim excerpt from 2025_08.pdf page 7 — header lines dropped,
    each app_no anchor produces one event."""
    page_text = (
        "BAŞVURU NUMARALARINA GÖRE BÜLTENDE YER ALAN YAYIN İNDEKSİ\n"
        "Başvuru No\n"
        "Yayın Açıklaması\n"
        "2021/001903\n"
        "Patent/FM Model Başvurularında/Belgelerinde Yayından Sonraki Değişikliğin İlanı\n"
        "2021/001947\n"
        "Kesinleşen Patent Verilme Kararının İlanı (6769 SMK)\n"
        "2021/002025\n"
        "Kullanma/Kullanmama Beyanı Verilmemiş Olan Başvuru veya Patent/Faydalı Modellerin İlanı\n"
    )
    events = parse_event_index_page(page_text, page_no=7, bulletin_no="2025/8")

    assert len(events) == 3
    assert events[0].application_no == "2021/001903"
    assert events[0].event_type == "POST_PUB_AMENDMENT"
    assert events[0].page == 7
    assert events[1].application_no == "2021/001947"
    assert events[1].event_type == "GRANT_FINALIZED"
    assert events[2].application_no == "2021/002025"
    assert events[2].event_type == "USE_NONUSE_DECLARATION_MISSING"
    # Each event has a non-empty 16-hex fingerprint
    assert all(len(e.fingerprint) == 16 for e in events)
    assert len({e.fingerprint for e in events}) == 3


def test_parse_event_index_page_handles_multi_line_descriptions() -> None:
    """The YIDK phrase wraps to 2 lines in real PDF text extraction.
    Joiner must concat them before classification."""
    page_text = (
        "BAŞVURU NUMARALARINA GÖRE BÜLTENDE YER ALAN YAYIN İNDEKSİ\n"
        "2021/010013\n"
        "6769 Sayılı SMK'nın 99 uncu Maddesi Hükmü Uyarınca YIDK Tarafından Patent Hakkının\n"
        "Değiştirilmiş Haliyle Devamına Karar Verilen Patentler\n"
        "2021/010013\n"
        "Kesinleşen Patent Verilme Kararının İlanı (6769 SMK)\n"
    )
    events = parse_event_index_page(page_text, page_no=8, bulletin_no="2025/8")

    # Same app appears twice → two events with different event_types.
    # Critical regression: app 2021/010013 verified to do this on page
    # 8 of 2025_08.pdf.
    assert len(events) == 2
    assert all(e.application_no == "2021/010013" for e in events)
    assert events[0].event_type == "YIDK_AMENDED_CONTINUATION"
    assert events[1].event_type == "GRANT_FINALIZED"
    # Different event_type → different fingerprints (dedup-safe)
    assert events[0].fingerprint != events[1].fingerprint


def test_parse_event_index_page_drops_header_lines() -> None:
    """The 3 header lines never match the app_no regex, so they don't
    produce events."""
    page_text = (
        "BAŞVURU NUMARALARINA GÖRE BÜLTENDE YER ALAN YAYIN İNDEKSİ\n"
        "Başvuru No\n"
        "Yayın Açıklaması\n"
        "2024/000001\n"
        "Verilen Patent / Faydalı Model İlanı (6769 SMK)\n"
    )
    events = parse_event_index_page(page_text, page_no=7, bulletin_no="2025/8")
    assert len(events) == 1
    assert events[0].application_no == "2024/000001"


def test_parse_event_index_page_skips_bare_anchor_with_no_description() -> None:
    """Defensive: if the last anchor has no description (rare,
    malformed page), skip it rather than emit an empty event."""
    page_text = (
        "2024/000001\n"
        "Verilen Patent / Faydalı Model İlanı (6769 SMK)\n"
        "2024/000002\n"           # no description follows
    )
    events = parse_event_index_page(page_text, page_no=7, bulletin_no="2025/8")
    assert len(events) == 1
    assert events[0].application_no == "2024/000001"


def test_parse_event_index_page_unknown_phrase_preserved_in_free_text() -> None:
    """Unknown event_type → caller can still recover the description
    from free_text and backfill the phrase mapping later."""
    page_text = (
        "2024/000001\n"
        "Some unprecedented event description not in our table\n"
    )
    events = parse_event_index_page(page_text, page_no=7, bulletin_no="2025/8")
    assert len(events) == 1
    assert events[0].event_type == EVENT_TYPE_UNKNOWN
    assert "unprecedented event description" in events[0].free_text


def test_parse_event_index_page_empty_returns_empty() -> None:
    assert parse_event_index_page("", page_no=7, bulletin_no="2025/8") == []
    assert parse_event_index_page("\n\n\n", page_no=7, bulletin_no="2025/8") == []


def test_parse_event_index_page_fingerprint_includes_page_independence() -> None:
    """Same event on different pages should get the SAME fingerprint
    (page is preserved as a column but doesn't affect dedup)."""
    text = "2024/000001\nVerilen Patent / Faydalı Model İlanı (6769 SMK)\n"
    e7 = parse_event_index_page(text, page_no=7, bulletin_no="2025/8")[0]
    e1500 = parse_event_index_page(text, page_no=1500, bulletin_no="2025/8")[0]
    assert e7.fingerprint == e1500.fingerprint
    assert e7.page == 7 and e1500.page == 1500


def test_parsed_event_serialisable_via_asdict() -> None:
    """ParsedEvent must be json.dumps-clean via dataclasses.asdict.
    events.json serialisation depends on this."""
    from dataclasses import asdict
    e = ParsedEvent(
        application_no="X", event_type="GRANT_ANNOUNCED",
        page=1, free_text="x", fingerprint="abcd1234efgh5678",
    )
    payload = asdict(e)
    import json as _json
    encoded = _json.dumps(payload)
    decoded = _json.loads(encoded)
    assert decoded["application_no"] == "X"


# ---------------------------------------------------------------------------
# parse_pdf_events — live smoke (skipped if real PDF absent)
# ---------------------------------------------------------------------------


_REAL_PDF = (
    Path(__file__).resolve().parent.parent / "bulletins"
    / "Patent__Faydali_Model" / "2025_08.pdf"
)


@pytest.mark.skipif(
    not _REAL_PDF.is_file(),
    reason=f"Real PDF {_REAL_PDF.name} not on disk; skipping live smoke",
)
def test_parse_pdf_events_real_2025_08() -> None:
    """End-to-end on the real bulletin 2025/8: walks 1976 pages,
    parses ~274 EVENT_INDEX pages, asserts plausible event counts +
    distribution."""
    from pdf_extract_patent_events import parse_pdf_events
    doc = parse_pdf_events(_REAL_PDF)

    assert doc["bulletin_no"] == "2025-08"
    assert doc["bulletin_date"] == "2025-08-21"
    assert doc["source_pdf"] == "2025_08.pdf"

    s = doc["stats"]
    # detect_page_kind earlier reported 274 EVENT_INDEX pages; allow
    # some slack since detect_page_kind may evolve.
    assert 200 < s["event_index_pages_scanned"] < 350, (
        f"event_index_pages_scanned={s['event_index_pages_scanned']} outside "
        "the 200-350 window — page-kind detection may have drifted"
    )
    # Empirical estimate: ~10–25 events per event-index page → 2K–7K
    # events for one bulletin. Hard floor at 1500 catches a parser
    # regression that drops most events.
    assert s["events_total"] > 1500, (
        f"events_total={s['events_total']} — parser likely regressed"
    )
    # Quality gate: <5% UNKNOWN means the phrase table covers the
    # event types present in this bulletin. Above 5% means new phrases
    # need adding (extend _PHRASE_TO_EVENT_TYPE).
    unknown_ratio = s["unknown_count"] / s["events_total"]
    assert unknown_ratio < 0.05, (
        f"unknown_count={s['unknown_count']} / {s['events_total']} = "
        f"{unknown_ratio:.3f} — phrase mapping is missing common events; "
        "review free_text on UNKNOWN entries and extend _PHRASE_TO_EVENT_TYPE"
    )

    # Spot-check first event has the documented shape
    assert len(doc["events"]) == s["events_total"]
    first = doc["events"][0]
    assert set(first.keys()) == {
        "application_no", "event_type", "page", "free_text", "fingerprint",
    }
    assert len(first["fingerprint"]) == 16


# ---------------------------------------------------------------------------
# CLI: parse_argv + main
# ---------------------------------------------------------------------------


def test_parse_argv_single_pdf() -> None:
    from pdf_extract_patent_events import parse_argv
    args = parse_argv(["--pdf", "x.pdf", "--out-dir", "/tmp/out"])
    assert args.pdf_paths == [Path("x.pdf")]
    assert args.out_dir == Path("/tmp/out")
    assert args.force is False


def test_parse_argv_no_args_errors() -> None:
    from pdf_extract_patent_events import parse_argv
    with pytest.raises(SystemExit):
        parse_argv([])


def test_parse_argv_pdf_and_all_mutex() -> None:
    from pdf_extract_patent_events import parse_argv
    with pytest.raises(SystemExit):
        parse_argv(["--pdf", "x.pdf", "--all"])


def test_parse_argv_all_skips_legacy_part_pdfs(tmp_path) -> None:
    """*_legacy_partNN.pdf files are RAR archives the collector saved with
    a .pdf extension. --all should filter them out before they reach
    PyMuPDF."""
    from pdf_extract_patent_events import parse_argv
    (tmp_path / "2025_08.pdf").write_bytes(b"")
    (tmp_path / "1996_6_legacy_part01.pdf").write_bytes(b"Rar!\x1a\x07\x00")
    args = parse_argv(["--all", "--bulletins-dir", str(tmp_path)])
    names = [p.name for p in args.pdf_paths]
    assert names == ["2025_08.pdf"]


def test_parse_argv_force_flag() -> None:
    from pdf_extract_patent_events import parse_argv
    args = parse_argv(["--pdf", "x.pdf", "--force"])
    assert args.force is True


@pytest.mark.skipif(
    not _REAL_PDF.is_file(),
    reason=f"Real PDF {_REAL_PDF.name} not on disk; skipping live smoke",
)
def test_main_writes_events_json_and_skips_on_rerun(tmp_path) -> None:
    """End-to-end CLI smoke: first run writes events.json, second run
    skips via the freshness check."""
    import json as _json
    import time as _time
    from pdf_extract_patent_events import main

    rc = main(["--pdf", str(_REAL_PDF), "--out-dir", str(tmp_path)])
    assert rc == 0

    events_path = tmp_path / "PT_2025_8_2025-08-21" / "events.json"
    assert events_path.is_file()
    payload = _json.loads(events_path.read_text(encoding="utf-8"))
    assert payload["bulletin_no"] == "2025-08"
    assert payload["stats"]["events_total"] > 1500
    mtime_before = events_path.stat().st_mtime

    # Sleep at least 1s so a re-write would change mtime perceptibly
    _time.sleep(1.1)
    rc2 = main(["--pdf", str(_REAL_PDF), "--out-dir", str(tmp_path)])
    assert rc2 == 0
    # File was NOT re-written
    assert events_path.stat().st_mtime == mtime_before

"""Patent / Faydalı Model PDF event-index extractor.

Sister to ``pdf_extract_patent.py`` (Stage 3 bibliographic) and
``pdf_extract_tasarim_events.py`` (the tasarim precedent). Reads each
``YYYY_M.pdf`` from ``bulletins/Patent__Faydali_Model/`` and produces
an ``events.json`` sidecar inside the bulletin's ``PT_*/`` parent
folder. Stage 5 ingest reads that sidecar and populates
``patent_events``.

Why a separate module from ``pdf_extract_patent``: the bibliographic
extractor walks ``INID_RECORDS`` pages and skips ``EVENT_INDEX`` pages
entirely. Events live on the skipped pages — a flat per-application
index ("BAŞVURU NUMARALARINA GÖRE BÜLTENDE YER ALAN YAYIN İNDEKSİ"
header). Splitting the parser keeps each module focused; running both
costs ~2× the PDF-open time, which is negligible.

EVENT_INDEX page format (verified 2026-05-09 against 2025_08.pdf):

    BAŞVURU NUMARALARINA GÖRE BÜLTENDE YER ALAN YAYIN İNDEKSİ
    Başvuru No
    Yayın Açıklaması
    2021/001903
    Patent/FM Model Başvurularında/Belgelerinde Yayından Sonraki ...
    2021/001947
    Kesinleşen Patent Verilme Kararının İlanı (6769 SMK)
    ...

Each entry is one ``^YYYY/NNNNNN$`` line followed by 1–2 description
lines. The same application_no can repeat on a page (verified: app
2021/010013 appears twice on page 8 with two distinct events). Every
description matches one of ~25 canonical phrases (the SMK 6769 event
catalog).

CLI (lands in step 7.4)::

    python pdf_extract_patent_events.py --pdf bulletins/Patent__Faydali_Model/2025_08.pdf
    python pdf_extract_patent_events.py --all
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


_LOCAL_PROJECT_ROOT_BOOT = Path(__file__).resolve().parent
if str(_LOCAL_PROJECT_ROOT_BOOT) not in sys.path:
    sys.path.insert(0, str(_LOCAL_PROJECT_ROOT_BOOT))

# Reuse Stage 3's page-kind detector + cover-page probe so this
# module agrees with pdf_extract_patent on what counts as an
# EVENT_INDEX page. PyMuPDF stays lazy via _get_fitz.
from pdf_extract_patent import (  # noqa: E402
    PageKind,
    _get_fitz,
    detect_page_kind,
    _resolve_cover_collision,
    extract_bulletin_metadata,
)


_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_DIR = _LOCAL_PROJECT_ROOT / "bulletins" / "Patent__Faydali_Model"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [PATENT-EVENTS] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.patent_events")


# ---------------------------------------------------------------------------
# Step 7.1 — Turkish-phrase → event_type lookup table
# ---------------------------------------------------------------------------
#
# Verbatim phrases observed in 2025_08.pdf event-index pages (sampled
# 2026-05-09). PDF text-extraction sometimes inserts line breaks
# inside long phrases ("…Değiştirilmiş Haliyle / Devamına Karar
# Verilen Patentler") so the matcher normalises whitespace before
# comparing.
#
# Order matters when multiple phrases share a prefix: more specific
# ones (longer, more qualifiers) come first so they match before
# generic prefixes.
#
# event_type names are UPPER_SNAKE so they read as enum constants —
# Stage 5 stores them in patent_events.event_type VARCHAR(50). Don't
# rename without coordinating with whatever consumer queries by
# event_type.
_PHRASE_TO_EVENT_TYPE: List[Tuple[str, str]] = [
    # ===== Application lifecycle =====
    # The A1 publication event. NOTE: bulletin ships a typo
    # ("Yayınıın" instead of "Yayınının") — preserved verbatim so the
    # match works on real text. If the office fixes the typo we can
    # add the corrected variant alongside.
    ("Başvuru Yayınıın İlanı (6769 SMK)",
     "APPLICATION_PUBLISHED"),
    ("Reddedilen Patent/Faydalı Model Başvurularının İlanı (6769 SMK)",
     "APPLICATION_REJECTED"),
    ("Geri Çekilmiş Sayılan Patent / Faydalı Model Başvurularının İlanı (6769 SMK)",
     "APPLICATION_WITHDRAWN"),
    ("Terk Edilen / Geri Çevrilen / Geri Çekilmiş Sayılan Başvuru / Belgelerin İlanı",
     "APPLICATION_ABANDONED"),

    # ===== Grant lifecycle =====
    ("Verilen Patent / Faydalı Model İlanı (6769 SMK)",
     "GRANT_ANNOUNCED"),
    ("Verilen Patent / Faydalı Model İlanı (Mülga 551 KHK)",
     "GRANT_ANNOUNCED_LEGACY_551"),
    ("Kesinleşen Patent Verilme Kararının İlanı (6769 SMK)",
     "GRANT_FINALIZED"),
    ("Koruma Süresi Dolan Patent/FM Belgelerinin İlanı",
     "GRANT_PROTECTION_EXPIRED"),

    # ===== Ownership transfers =====
    ("Devir İşlemi Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "ASSIGNMENT_RECORDED"),
    ("Birleşme İşlemi Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "MERGER_RECORDED"),
    ("Bölünme İşlemi Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "DIVISION_RECORDED"),
    ("Lisans Verme Teklifinin İlanı",
     "LICENSE_OFFER"),
    # Rehin = pledge / lien — released. The bulletin renders this with
    # a stray space inside "( 6769 SMK)" which the normaliser collapses
    # away, so the table entry uses the canonical no-space form.
    ("Rehin Kaldırılması İşlemi Sicile Kayıt Edilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "PLEDGE_RELEASED"),

    # ===== Conversion (UM ↔ Patent) =====
    ("Faydalı Modele Dönüşüm İlanı (6769 SMK)",
     "CONVERSION_TO_UM"),
    ("Patente Dönüşüm İlanı (6769 SMK)",
     "CONVERSION_TO_PATENT"),

    # ===== Post-publication amendments =====
    ("Patent/FM Model Başvurularında/Belgelerinde Yayından Sonraki Değişikliğin İlanı",
     "POST_PUB_AMENDMENT"),

    # ===== Fee lapses (granted vs application — distinct by phrasing) =====
    ("Verilen Patent/FM Belgelerinin Yıllık Ücretinin Ödenmemesi Nedeniyle Geçersizlik ilanı",
     "GRANT_FEE_LAPSE"),
    ("Patent/FM Başvurularının Yıllık Ücretinin Ödenmemesi Nedeniyle Geçersizlik ilanı",
     "APPLICATION_FEE_LAPSE"),

    # ===== Revalidations =====
    # Fee-paid revalidation: granted vs application — distinct rows
    ("Yıllık Ücretlerinin Ödenmemesi Nedeniyle Geçersiz Olan Patent/FM Belgelerinin Yeniden Geçerlilik İlanı",
     "GRANT_FEE_REVALIDATION"),
    ("Yıllık Ücretlerinin Ödenmemesi Nedeniyle Geçersiz Olan Patent/FM Başvurularının Yeniden Geçerlilik İlanı",
     "APPLICATION_FEE_REVALIDATION"),
    # Procedural resumption (separate from fee-paid)
    ("Yeniden Geçerlilik Kazanan Patent/Faydalı Model Başvurularının İlanı (İşlemlerin Devam Ettirilmesi)",
     "PROCEDURAL_REVALIDATION"),

    # ===== Use declarations =====
    ("Kullanma/Kullanmama Beyanı Verilmemiş Olan Başvuru veya Patent/Faydalı Modellerin İlanı",
     "USE_NONUSE_DECLARATION_MISSING"),
    ("Kullanıldığı Beyanı Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "USE_DECLARATION_RECORDED"),
    ("Kullanılmadığı Beyanı Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "NONUSE_DECLARATION_RECORDED"),

    # ===== Search reports (patent vs UM — separate event_types) =====
    # The "with-application-publication" variants are the section
    # headers for the listing pages where each row is app_no + title
    # + holder rather than a full event-phrase. Section-level
    # classification (set by _SECTION_TO_EVENT_TYPE further down)
    # picks these up for the per-row entries.
    ("Yayımlanmış Patent Başvurularının Araştırma Raporları (6769 SMK)",
     "SEARCH_REPORT_PATENT"),
    ("Yayımlanmış Faydalı Model Başvurularının Araştırma Raporları (6769 SMK)",
     "SEARCH_REPORT_UM"),
    ("Araştırma Raporu İle Birlikte Yayımlanan Patent Başvuruları (6769 SMK)",
     "SEARCH_REPORT_WITH_APPLICATION_PATENT"),
    ("Araştırma Raporu İle Birlikte Yayımlanan Faydalı Model Başvuruları (6769 SMK)",
     "SEARCH_REPORT_WITH_APPLICATION_UM"),

    # ===== EP fascicles =====
    ("Avrupa Patent Fasiküllerinin İlanı",
     "EP_FASCICLE_ANNOUNCED"),

    # ===== YİDK board decisions =====
    ("6769 Sayılı SMK'nın 99 uncu Maddesi Hükmü Uyarınca YIDK Tarafından Patent Hakkının "
     "Değiştirilmiş Haliyle Devamına Karar Verilen Patentler",
     "YIDK_AMENDED_CONTINUATION"),

    # ===== Pre-2017 (551 KHK era) phrases =====
    # The 551 KHK regime had a two-track system (incelemeli "examined" vs
    # incelemesiz "non-examined"). 6769 SMK (2017) collapsed both. These
    # phrases only appear on bulletins up to ~2017_5; modern parsers can
    # ignore the distinction but we want them mapped, not UNKNOWN, so
    # downstream queries can filter by event_type.
    ("Başvuru Yayınının İlanı",
     "APPLICATION_PUBLISHED"),
    ("İncelemeli Sistem Tercihinin İlanı",
     "EXAM_SYSTEM_CHOICE_LEGACY_551"),
    ("İncelemesiz Sistem Tercihinin İlanı",
     "NONEXAM_SYSTEM_CHOICE_LEGACY_551"),
    ("İncelemesiz Sistem Tercihini Kabul Etmiş Sayılan Başvuruların İlanı",
     "NONEXAM_SYSTEM_CHOICE_DEEMED_LEGACY_551"),
    ("İnceleme Ücretinin Ödenmemesi Nedeniyle İncelemesiz Sistem Tercihini "
     "Kabul Etmiş Sayılan Başvuruların İlanı",
     "NONEXAM_SYSTEM_CHOICE_DEEMED_LEGACY_551"),
    ("İncelemesiz Patent Sisteminden İncelemeli Patent Sistemine Dönüşen Başvuruların İlanı",
     "CONVERSION_NONEXAM_TO_EXAM_LEGACY_551"),
    ("Araştırma Raporunun İlanı",
     "SEARCH_REPORT_LEGACY_551"),
    ("Araştırma Raporu İle Birlikte Yayımlandığı İlan Edilen Başvurular",
     "SEARCH_REPORT_WITH_APPLICATION_LEGACY_551"),
    ("Düzeltme - İncelemesiz Sistem Tercihinin İlanı",
     "NONEXAM_SYSTEM_CHOICE_LEGACY_551"),
    # Spelling variants of POST_PUB_AMENDMENT — bulletin layout drifted
    # over the years between "Patent/FM Model" / "Patent/Faydalı Model"
    # with vs without "/Belgelerinde".
    ("Patent/FM Model Başvurularında Yayından Sonraki Değişikliğin İlanı",
     "POST_PUB_AMENDMENT"),
    ("Patent/Faydalı Model Başvurularında/Belgelerinde Yayından Sonraki Değişikliğin İlanı",
     "POST_PUB_AMENDMENT"),
    # Spelling variant of APPLICATION_FEE_LAPSE.
    ("Patent/Faydalı Model Başvurularının Yıllık Ücretinin Ödenmemesi Nedeniyle Geçersizlik ilanı",
     "APPLICATION_FEE_LAPSE"),
    # "İptal - " prefix marks an annulment of a previously announced
    # cancellation (re-validation event). Same event_type as the original.
    ("İptal - Verilen Patent/FM Belgelerinin Yıllık Ücretinin Ödenmemesi Nedeniyle Geçersizlik ilanı",
     "GRANT_FEE_LAPSE_CANCELLED"),
    ("İptal - Patent/FM Başvurularının Yıllık Ücretinin Ödenmemesi Nedeniyle Geçersizlik ilanı",
     "APPLICATION_FEE_LAPSE_CANCELLED"),
    # EP-fascicle correction announcement (rare; legacy bulletins).
    ("Düzeltme - Avrupa Patent Fasiküllerinin İlanı",
     "EP_FASCICLE_CORRECTION"),
    # EP claim-level publication (precedes the fascicle on PCT entries).
    ("Başvuru İstemlerinin Yayımlandığı İlan Edilen Avrupa Patent Başvuruları",
     "EP_CLAIMS_PUBLISHED"),
    # PCT international phase II (national entry) announcement.
    ("PCT II. Kısımdan Gelen Başvuruların İlanı",
     "PCT_PHASE_II_ENTRY"),
    # Force-majeure relief announcement (551-era, rarely used).
    ("Patent / Faydalı Model İçin Mücbir Sebep İlanı",
     "FORCE_MAJEURE_LEGACY_551"),
    # Annulment under 551 KHK Articles 129/165 (invalidation of granted right).
    ("551 Sayılı KHK'nin 129 uncu veya 165 inci Maddeleri Hükmü Uyarınca "
     "Hükümsüzlüğüne Karar Verilen Patent/Faydalı Model Belgeleri",
     "GRANT_INVALIDATED_LEGACY_551"),
    # Same phrase prefixed with "Mülga" (= "abolished") — used in newer
    # bulletins when referring to the now-superseded 551 KHK regime.
    ("Mülga 551 Sayılı KHK'nin 129 uncu veya 165 inci Maddeleri Hükmü Uyarınca "
     "Hükümsüzlüğüne Karar Verilen Patent/Faydalı Model Belgeleri",
     "GRANT_INVALIDATED_LEGACY_551"),

    # ===== Administrative corrections — "İptal -" (cancellation of a
    # prior announcement) and "Düzeltme -" (correction of a prior
    # announcement). Each gets its own event_type so callers can
    # filter / un-apply the original event downstream. =====
    ("İptal - Araştırma Raporunun İlanı",
     "SEARCH_REPORT_CANCELLED"),
    ("İptal - Başvuru Yayınının İlanı",
     "APPLICATION_PUBLICATION_CANCELLED"),
    ("Düzeltme - Başvuru Yayınının İlanı",
     "APPLICATION_PUBLICATION_CORRECTED"),
    ("Düzeltme - Verilen Patent / Faydalı Model İlanı",
     "GRANT_CORRECTED"),
    ("İptal - Terk Edilen / Geri Çevrilen / Geri Çekilmiş Sayılan Başvuru / Belgelerin İlanı",
     "APPLICATION_ABANDONED_CANCELLED"),
    ("İptal - İncelemeli Sistem Tercihinin İlanı",
     "EXAM_SYSTEM_CHOICE_LEGACY_551"),

    # Variant of APPLICATION_WITHDRAWN — modern bulletins occasionally
    # use "Geri Çekilen" (withdrawn) instead of the canonical
    # "Geri Çekilmiş Sayılan" (deemed withdrawn). Same effective event.
    ("Geri Çekilen Patent / Faydalı Model Başvurularının İlanı (6769 SMK)",
     "APPLICATION_WITHDRAWN"),
]

# Sentinel for descriptions that don't match any known phrase. The
# description text is preserved in events[].free_text so the mapping
# can be extended later without re-extracting.
EVENT_TYPE_UNKNOWN = "UNKNOWN"

# Section headers that appear ONCE on a page and govern the event_type
# of every (app_no, free_text) row on that page. Used by the section-
# state machine in parse_pdf_events: when a section header appears on
# page N, every UNKNOWN-classified event from page N onwards inherits
# the section's event_type until the next recognised section header
# (or until a row whose free_text matches a non-section phrase, which
# is treated as an inline override — happens on the flat event-index
# pages that intermix with section pages).
#
# Concrete real-data example (verified on 2025_08.pdf):
#   pp 1190-1844: "Araştırma Raporu İle Birlikte Yayımlanan Patent
#   Başvuruları (6769 SMK)" header → each row has the patent title +
#   holder rather than an event-phrase, so the row's free_text won't
#   classify, and the section state assigns SEARCH_REPORT_WITH_
#   APPLICATION_PATENT to the event.
# The real section headers are uppercase "page-banner" titles that
# appear ONCE on the first page of each section. Subsequent pages
# inherit until the next section header. Verified on 2025_08.pdf:
#   page 1151: 6769 SAYILI SMK'NIN 96 NCI MADDE HÜKMÜ UYARINCA ARAŞTIRMA RAPORU
#   page 1190: LİSANS VERME TEKLİFİNDE BULUNULAN PATENTLER
#   page 1232: YENİDEN GEÇERLİLİK KAZANAN PATENT/FAYDALI MODELLER
#   page 1235: YENİDEN GEÇERLİLİK KAZANAN PATENT/FAYDALI MODEL BAŞVURULARI
#   page 1279: GEÇERSİZ SAYILAN / REDDEDİLEN VE GERİ ÇEKİLMİŞ SAYILAN BAŞVURULAR
# Each header is followed by "Başvuru No" + "Buluş Başlığı" sub-
# headers and then a long list of (app_no, title, holder) rows.
#
# The "Yayımlanmış … Araştırma Raporları (6769 SMK)" phrases that
# appear INLINE on the flat-index pages (7-114) keep their existing
# per-row classification via _PHRASE_TO_EVENT_TYPE. Those are
# different events than the page-banner-style section.
_SECTION_HEADERS_TO_EVENT_TYPE: List[Tuple[str, str]] = [
    # ── Uppercase page banners (pp 1151+) ───────────────────────────
    ("6769 SAYILI SMK'NIN 96 NCI MADDE HÜKMÜ UYARINCA ARAŞTIRMA RAPORU",
     "SEARCH_REPORT_ARTICLE_96"),
    ("LİSANS VERME TEKLİFİNDE BULUNULAN PATENTLER",
     "LICENSE_OFFER"),
    ("YENİDEN GEÇERLİLİK KAZANAN PATENT/FAYDALI MODELLER",
     "GRANT_FEE_REVALIDATION"),
    ("YENİDEN GEÇERLİLİK KAZANAN PATENT/FAYDALI MODEL BAŞVURULARI",
     "APPLICATION_FEE_REVALIDATION"),
    ("GEÇERSİZ SAYILAN / REDDEDİLEN VE GERİ ÇEKİLMİŞ SAYILAN BAŞVURULAR",
     "APPLICATION_LAPSED_OR_REJECTED"),
    # ── Mixed-case section headers (pp 1300+) ──────────────────────
    # These same phrases appear as ROWS on the flat event-index pages
    # (7-114) where they classify per-row via _PHRASE_TO_EVENT_TYPE.
    # When they appear as the FIRST line of a SECTION page, they
    # govern all UNKNOWN rows on subsequent pages of the section
    # (where rows are structured fields like date + old_holder +
    # new_holder, not phrase-shaped). Listing them here makes the
    # section-state machine recognise the banner.
    ("Devir İşlemi Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "ASSIGNMENT_RECORDED"),
    ("Birleşme İşlemi Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "MERGER_RECORDED"),
    ("Bölünme İşlemi Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "DIVISION_RECORDED"),

    # ── Pre-2017 (551 KHK era) page-banner sections ───────────────
    # The same wording shape as the modern Article 96 header, but the
    # legacy regime cited Articles 57 / 59 of 551 KHK. Each section runs
    # for dozens of pages with rows like "KAVURMA MAKİNASI" (patent
    # title) — without these entries every such row leaked as UNKNOWN
    # through the per-row classifier. Verified on 2017_5 (p1439, p1471).
    ("551 SAYILI KHK'NİN 57 NCİ MADDE HÜKMÜ UYARINCA ARAŞTIRMA RAPORU",
     "SEARCH_REPORT_ARTICLE_57_LEGACY_551"),
    ("551 SAYILI KHK'NİN 59 uncu MADDE HÜKMÜ UYARINCA İNCELEMESİZ PATENT",
     "SEARCH_REPORT_ARTICLE_59_NONEXAM_LEGACY_551"),
    # Section listing apps whose description (tarifname) was amended.
    ("TARİFNAMESİNDE DEĞİŞİKLİK YAPILAN PATENT/FAYDALI MODEL BAŞVURULARI",
     "DESCRIPTION_AMENDED_LEGACY_551"),

    # ── Mixed-case sub-section headers that appear MID-PAGE on the
    # late-bulletin section pages (pp 1200+ for modern bulletins).
    # Each one defines its own sub-section of (app_no, title, holder)
    # rows below it on the same page.
    #
    # Verified visually on bulletin 2025/3 (PT_2025_3_2025-03-21)
    # page 1272 — multiple sub-headers per page, each governing the
    # rows immediately below until the next sub-header. Previously
    # only ONE banner was detected per page, so the rows after the
    # second sub-header got mis-classified as the first one's
    # event_type (e.g. finalized grants tagged as
    # APPLICATION_LAPSED_OR_REJECTED).
    ("Kesinleşen Patent Verilme Kararının İlanı (6769 SMK)",
     "GRANT_FINALIZED"),
    ("6769 SAYILI SMK'NIN 99 UNCU MADDE HÜKMÜ UYARINCA YAYIMLANAN KESİNLEŞMİŞ PATENTLER",
     "GRANT_FINALIZED"),
    ("Verilen Patent / Faydalı Model İlanı (6769 SMK)",
     "GRANT_ANNOUNCED"),
    ("Geri Çekilmiş Sayılan Patent / Faydalı Model Başvurularının İlanı (6769 SMK)",
     "APPLICATION_WITHDRAWN"),
    ("GERİ ÇEKMİŞ SAYILAN PATENT / FAYDALI MODEL BAŞVURULARI (6769 SMK)",
     "APPLICATION_WITHDRAWN"),
    ("Reddedilen Patent/Faydalı Model Başvurularının İlanı (6769 SMK)",
     "APPLICATION_REJECTED"),
    ("Yayımlanmış Patent Başvurularının Araştırma Raporları (6769 SMK)",
     "SEARCH_REPORT_PATENT"),
    ("Yayımlanmış Faydalı Model Başvurularının Araştırma Raporları (6769 SMK)",
     "SEARCH_REPORT_UM"),
    ("Araştırma Raporu İle Birlikte Yayımlanan Patent Başvuruları (6769 SMK)",
     "SEARCH_REPORT_WITH_APPLICATION_PATENT"),
    ("Araştırma Raporu İle Birlikte Yayımlanan Faydalı Model Başvuruları (6769 SMK)",
     "SEARCH_REPORT_WITH_APPLICATION_UM"),
    ("Patent / Faydalı Model Başvurularında / Belgelerinde Yayından Sonraki Düzeltmenin İlanı",
     "POST_PUB_AMENDMENT"),
    ("YİDK Kararı İle Yeniden İşleme Alınan Patent/FM Başvurusu/Belgesi İlanı",
     "YIDK_AMENDED_CONTINUATION"),
    ("Mülga 551 Sayılı KHK'nin 129 uncu veya 165 inci Maddeleri Hükmü Uyarınca Hükümsüzlüğüne Karar Verilen Patent/Faydalı Model Belgeleri",
     "GRANT_INVALIDATED_LEGACY_551"),
    ("Kullanıldığı Beyanı Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "USE_DECLARATION_RECORDED"),
    ("Kullanılmadığı Beyanı Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "NONUSE_DECLARATION_RECORDED"),
]


_WHITESPACE_RE = re.compile(r"\s+")


def _normalise_phrase(text: str) -> str:
    """Collapse all whitespace (including PDF line-break artefacts) to
    single spaces, strip ends. Trailing dots / colons / soft markers
    don't materially differentiate phrases — strip them too."""
    if not text:
        return ""
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()
    return collapsed.rstrip(" .:;,")


# Pre-normalise the lookup keys once so the classifier doesn't repeat
# the work on every call. ``_NORMALISED_PHRASES`` is the same shape
# as ``_PHRASE_TO_EVENT_TYPE`` but with normalised keys.
_NORMALISED_PHRASES: List[Tuple[str, str]] = [
    (_normalise_phrase(phrase), event_type)
    for phrase, event_type in _PHRASE_TO_EVENT_TYPE
]


def classify_event_phrase(text: Optional[str]) -> str:
    """Map a free-text event description → canonical event_type.

    Tries an exact normalised-string match first (case-insensitive),
    then a normalised-prefix match (handles trailing parenthetical
    SMK references on some variants). Returns ``EVENT_TYPE_UNKNOWN``
    for blank input or any phrase not in the table — caller preserves
    the raw text in ``events[].free_text`` so the mapping can be
    extended later without re-extracting.
    """
    if not text:
        return EVENT_TYPE_UNKNOWN
    normalised = _normalise_phrase(text)
    if not normalised:
        return EVENT_TYPE_UNKNOWN
    lowered = normalised.lower()

    # Exact normalised match (case-insensitive).
    for canonical, event_type in _NORMALISED_PHRASES:
        if lowered == canonical.lower():
            return event_type
    # Prefix match — handles "(6769 SMK)" suffix variants and trailing
    # punctuation. Longest canonical wins so a shorter prefix can't
    # shadow a more specific one.
    matches = [
        (canonical, event_type)
        for canonical, event_type in _NORMALISED_PHRASES
        if lowered.startswith(canonical.lower())
        or canonical.lower().startswith(lowered)
    ]
    if matches:
        matches.sort(key=lambda pair: -len(pair[0]))
        return matches[0][1]
    return EVENT_TYPE_UNKNOWN


# ---------------------------------------------------------------------------
# Step 7.1 — fingerprint
# ---------------------------------------------------------------------------


def event_fingerprint(
    bulletin_no: Optional[str],
    application_no: Optional[str],
    event_type: str,
    free_text: Optional[str],
) -> str:
    """Stable per-event dedup key.

    SHA256 over (bulletin_no, application_no, event_type, free_text
    truncated to 200 chars) → 16-char hex prefix. The truncation keeps
    the digest stable when PDF text extraction inserts incidental
    whitespace differences in the long descriptions.

    Mirrors ``pdf_extract_tasarim_events.fingerprint_event`` exactly
    so the patent_events.event_fingerprint UNIQUE constraint behaves
    consistently across registries.
    """
    parts = [
        (bulletin_no or "").strip(),
        (application_no or "").strip(),
        event_type or "",
        _normalise_phrase(free_text or "")[:200],
    ]
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Step 7.2 — per-page parser
# ---------------------------------------------------------------------------

# Anchored ^YYYY/NNNNNN$ — same pattern pdf_extract_patent uses for
# detecting EVENT_INDEX pages. Re-defined here (rather than imported)
# to keep the events module decoupled from the bibliographic one.
_APPNO_LINE_RE = re.compile(r"^\d{4}/\d{4,7}$")


@dataclass
class ParsedEvent:
    """One row in events.json. Stored as a dict via ``asdict`` at
    serialisation time."""
    application_no: str
    event_type: str
    page: int
    free_text: str
    fingerprint: str


def find_section_header_positions(
    lines: List[str],
) -> List[Tuple[int, str]]:
    """Locate every section header from `_SECTION_HEADERS_TO_EVENT_TYPE`
    inside a page's line list. Returns sorted `[(line_index, event_type)]`.

    Headers in the PDF text often wrap across two consecutive lines
    (PyMuPDF inserts line breaks at ~80 chars for long banners). Try
    matching the header in:
      - single line  : lines[i]
      - two-line join: lines[i] + " " + lines[i+1]
      - three-line   : lines[i] + " " + lines[i+1] + " " + lines[i+2]

    Multiple headers can appear on the same page in different
    positions — they each govern the anchors that follow until the
    next header. This is the fix for bulletin 2025/3 page 1272 (and
    others), which mixes 3-5 sub-sections per page.
    """
    if not lines:
        return []
    norm_headers = [
        (_normalise_phrase(h).lower(), et)
        for h, et in _SECTION_HEADERS_TO_EVENT_TYPE
    ]
    positions: List[Tuple[int, str]] = []
    seen_lines: set = set()
    for i in range(len(lines)):
        if i in seen_lines:
            continue
        for window in (1, 2, 3):
            if i + window > len(lines):
                break
            joined = _normalise_phrase(" ".join(lines[i:i + window])).lower()
            if not joined:
                continue
            matched = None
            for norm_h, et in norm_headers:
                if norm_h in joined:
                    matched = et
                    break
            if matched:
                positions.append((i, matched))
                # Don't re-detect the same header on its
                # continuation lines.
                for j in range(i, i + window):
                    seen_lines.add(j)
                break
    return positions


def parse_event_index_page(
    page_text: str,
    page_no: int,
    bulletin_no: Optional[str],
    initial_section_event_type: Optional[str] = None,
) -> List[ParsedEvent]:
    """Parse one EVENT_INDEX page text into a list of events.

    Algorithm:
      1. Split page text into lines, strip whitespace per line.
      2. Find every recognised section header position in the lines.
         Each header defines a sub-section that governs anchors
         until the next header.
      3. Find every line matching ``^YYYY/NNNNNN$`` — those are event
         anchors. Header lines ("BAŞVURU NUMARALARINA..." etc.) get
         dropped naturally because they don't match.
      4. For each consecutive pair of anchors (i, i+1), the lines
         between them are the description for the FIRST anchor.
         Multi-line descriptions (PDF text-extraction wraps long
         phrases) get joined with " " before classification.
      5. Walk anchors in order. Track the current section event_type
         as the most-recently-seen header. Use that to fill in
         UNKNOWN classifications.

    `initial_section_event_type` carries section state from the
    previous page — anchors at the top of the page (before any
    in-page header) inherit it.

    Same application_no can have multiple events on one page — each
    anchor produces one event. The fingerprint includes event_type +
    free_text so dedup at ingest time keeps both rows.

    page_no is 1-based.
    """
    if not page_text:
        return []

    # Strip the page footer template before line-by-line processing.
    # Footer lines are ~120 underscores followed by "{pageno} Yayın
    # Tarihi : {date}" and "2025/8 Resmi Patent Bülteni". When the
    # last app_no anchor on a page has no description before the
    # footer, the parser otherwise grabs the footer text and emits
    # it as an event description. Match defensively (8+ underscores
    # is enough to discriminate from any real text).
    lines: List[str] = []
    for raw in page_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("________"):
            # Footer separator and the two trailing footer lines
            # (page-no/date + bulletin tagline) get dropped.
            break
        lines.append(stripped)

    # In-page section header positions — defines per-anchor section
    # override. Section state at the very top of the page is the
    # caller-supplied initial value (last header on previous page).
    header_positions = find_section_header_positions(lines)

    # Find anchor positions
    anchors: List[Tuple[int, str]] = [
        (i, line) for i, line in enumerate(lines)
        if _APPNO_LINE_RE.match(line)
    ]
    if not anchors:
        return []

    events: List[ParsedEvent] = []
    current_section = initial_section_event_type
    next_header_idx = 0
    for k, (idx, app_no) in enumerate(anchors):
        # Consume any headers that precede this anchor — the most
        # recent one wins as the current section.
        while (next_header_idx < len(header_positions)
               and header_positions[next_header_idx][0] < idx):
            current_section = header_positions[next_header_idx][1]
            next_header_idx += 1

        # Description = lines between this anchor and the next (or end)
        next_idx = anchors[k + 1][0] if k + 1 < len(anchors) else len(lines)
        desc_lines = [line for line in lines[idx + 1:next_idx] if line]
        if not desc_lines:
            # An app_no anchor with no description (rare; possibly a
            # malformed page). Skip — emitting an event without a
            # description gives downstream code nothing to work with.
            continue
        free_text = " ".join(desc_lines)
        event_type = classify_event_phrase(free_text)

        # Section override: only apply when the per-row phrase
        # classifier returned UNKNOWN. Rows whose free_text is a
        # known phrase keep their classifier result (intermixed
        # event-index pages remain accurate even when wrapped in a
        # section).
        if event_type == EVENT_TYPE_UNKNOWN and current_section:
            event_type = current_section

        events.append(ParsedEvent(
            application_no=app_no,
            event_type=event_type,
            page=page_no,
            free_text=free_text,
            fingerprint=event_fingerprint(
                bulletin_no, app_no, event_type, free_text,
            ),
        ))
    return events


def last_section_event_type(
    page_text: str, initial: Optional[str] = None,
) -> Optional[str]:
    """Return the section event_type that should carry forward to the
    next page after processing this page. That's the last header
    found on this page, or the caller's `initial` value if no header
    appeared.

    Used by the orchestrator to flow section state across pages.
    """
    if not page_text:
        return initial
    lines: List[str] = []
    for raw in page_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("________"):
            break
        lines.append(stripped)
    positions = find_section_header_positions(lines)
    if positions:
        return positions[-1][1]
    return initial


# ---------------------------------------------------------------------------
# Step 7.3 — parse_pdf_events orchestrator
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def detect_section_event_type(page_text: str) -> Optional[str]:
    """If a page contains one of the canonical section headers, return
    the corresponding event_type. ``None`` otherwise.

    The header is looked up in the full page text (not just the first
    line) because PyMuPDF sometimes places it inline with the first
    app_no entry on long sections. See ``_SECTION_HEADERS_TO_EVENT_TYPE``.
    """
    if not page_text:
        return None
    normalised_page = _normalise_phrase(page_text).lower()
    for header, event_type in _SECTION_HEADERS_TO_EVENT_TYPE:
        if _normalise_phrase(header).lower() in normalised_page:
            return event_type
    return None


def parse_pdf_events(pdf_path: Path | str) -> Dict[str, Any]:
    """Walk a bulletin PDF and produce its full events.json doc.

    Pipeline (mirrors pdf_extract_patent.parse_pdf shape but for the
    EVENT_INDEX pages it skips):
      1. open PDF
      2. extract_bulletin_metadata (cover-page Sayı / Yayım Tarihi)
      3. iterate pages: detect_page_kind → if EVENT_INDEX, run
         parse_event_index_page on it
      4. concatenate events from all pages
      5. assemble doc with stats (events_total, by_event_type,
         unknown_count, event_index_pages_scanned)

    Returns a JSON-ready dict matching the ``events.json`` schema
    documented in the module docstring. Caller writes it to disk.

    On a missing/unparseable bulletin header, ``bulletin_no`` and
    ``bulletin_date`` are ``None`` — caller should refuse to write
    events.json in that case (events with ``bulletin_no=None`` can't
    fingerprint cleanly; better to fail loud).
    """
    pdf = Path(pdf_path)
    if not pdf.is_file():
        raise FileNotFoundError(f"pdf not found: {pdf}")

    fitz = _get_fitz()
    started = time.time()
    with fitz.open(str(pdf)) as doc:
        bulletin_no, bulletin_date = extract_bulletin_metadata(doc)

        events: List[ParsedEvent] = []
        event_index_pages_scanned = 0

        # Section-state machine. Each EVENT_INDEX page can contain
        # MULTIPLE sub-section headers (verified on bulletin 2025/3
        # page 1272). parse_event_index_page handles per-anchor
        # section override using the in-page header positions; the
        # orchestrator just carries the state across pages.
        #
        # State resets between INID-vs-event transitions so a section
        # doesn't bleed across the bulletin.
        current_section_event_type: Optional[str] = None

        for i in range(doc.page_count):
            page_text = doc[i].get_text("text")
            kind = detect_page_kind(page_text)
            if kind != PageKind.EVENT_INDEX:
                # Reset section state when leaving event-index pages
                # (otherwise the bulletin's first INID-records page
                # after a section would still inherit the section).
                current_section_event_type = None
                continue
            event_index_pages_scanned += 1

            page_events = parse_event_index_page(
                page_text, page_no=i + 1, bulletin_no=bulletin_no,
                initial_section_event_type=current_section_event_type,
            )
            events.extend(page_events)

            # Carry the page's final section state forward to the
            # next page (the last header seen on this page wins).
            current_section_event_type = last_section_event_type(
                page_text, initial=current_section_event_type,
            )

    by_event_type = Counter(e.event_type for e in events)

    return {
        "bulletin_no": bulletin_no,
        "bulletin_date": bulletin_date,
        "source_pdf": pdf.name,
        "extracted_at": _utcnow_iso(),
        "stats": {
            "events_total": len(events),
            "by_event_type": dict(by_event_type),
            "unknown_count": by_event_type.get(EVENT_TYPE_UNKNOWN, 0),
            "event_index_pages_scanned": event_index_pages_scanned,
            "extract_duration_seconds": round(time.time() - started, 1),
        },
        "events": [asdict(e) for e in events],
    }


# ---------------------------------------------------------------------------
# Step 7.4 — CLI + write events.json with idempotency
# ---------------------------------------------------------------------------
#
# Writes ``PT_{Y}_{M}_{date}/events.json`` alongside metadata.json
# inside each bulletin folder. Idempotent skip-if-fresh check matches
# pdf_extract_patent's pattern (events.json mtime ≥ source PDF mtime
# means we've already extracted; --force overrides).

EVENTS_FILENAME = "events.json"


@dataclass
class CLIArgs:
    pdf_paths: List[Path]
    out_dir: Path
    force: bool


def _events_filename_is_fresh(pdf_path: Path, events_path: Path) -> bool:
    """True when events.json exists, is non-empty, and is at least as
    recent as the source PDF. Identical pattern to
    pdf_extract_patent._metadata_is_fresh."""
    if not events_path.is_file():
        return False
    try:
        if events_path.stat().st_size == 0:
            return False
        return events_path.stat().st_mtime >= pdf_path.stat().st_mtime
    except OSError:
        return False


def _process_one(pdf: Path, out_dir: Path, *, force: bool) -> Dict[str, Any]:
    """Extract events for one PDF and write events.json into its
    bulletin parent folder. Mirrors the shape of
    pdf_extract_patent._process_one for consistency."""
    from patent_paths import bulletin_folder_path

    if not pdf.is_file():
        return {"status": "missing", "pdf": pdf.name}

    # Cheap probe: get bulletin_no/date BEFORE the full parse so we
    # know where events.json should land (fresh-check happens at the
    # destination path).
    fitz = _get_fitz()
    with fitz.open(str(pdf)) as doc:
        bulletin_no, bulletin_date = extract_bulletin_metadata(doc)
    if not bulletin_no or not bulletin_date:
        logger.error(
            "[!] %s: could not extract bulletin_no/date from cover page",
            pdf.name,
        )
        return {"status": "failed", "pdf": pdf.name,
                "error": "missing bulletin metadata"}

    parent = bulletin_folder_path(out_dir, bulletin_no, bulletin_date)
    # Same cover-page collision fallback as Stage 3 — when a previous
    # PDF already populated this folder under a different source_pdf,
    # route to the filename-derived folder so events.json doesn't
    # overwrite the prior PDF's events with this one's.
    parent, bulletin_no, bulletin_date = _resolve_cover_collision(
        parent, pdf, out_dir, bulletin_no, bulletin_date
    )
    events_path = parent / EVENTS_FILENAME

    if not force and _events_filename_is_fresh(pdf, events_path):
        logger.info("[=] %s is fresh, skipping (use --force to override)",
                    pdf.name)
        return {"status": "skipped", "pdf": pdf.name,
                "out": f"{parent.name}/{EVENTS_FILENAME}"}

    parent.mkdir(parents=True, exist_ok=True)
    payload = parse_pdf_events(pdf)
    # Override cover-derived bulletin_no/date in the payload — see
    # the matching override in pdf_extract_patent._process_one. When
    # the collision fallback corrected these we want the JSON to
    # match the resolved folder, not the (mislabeled) cover.
    payload["bulletin_no"] = bulletin_no
    payload["bulletin_date"] = bulletin_date
    events_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    s = payload["stats"]
    logger.info(
        "[+] %s: %d events (unknown=%d, %d index pages) -> %s/%s in %.1fs",
        pdf.name, s["events_total"], s["unknown_count"],
        s["event_index_pages_scanned"], parent.name, EVENTS_FILENAME,
        s["extract_duration_seconds"],
    )
    return {
        "status": "ok", "pdf": pdf.name,
        "out": f"{parent.name}/{EVENTS_FILENAME}",
        "stats": s,
    }


def parse_argv(argv: Optional[Sequence[str]] = None) -> CLIArgs:
    parser = argparse.ArgumentParser(
        prog="pdf_extract_patent_events",
        description="Extract Patent / Faydalı Model PDF event-index pages to events.json.",
    )
    parser.add_argument(
        "--pdf", action="append", type=Path, default=[],
        help="Path to a bulletin .pdf file. Repeat for multiple.",
    )
    parser.add_argument(
        "--all", action="store_true", dest="all_mode",
        help="Process every YYYY_M.pdf in --bulletins-dir.",
    )
    parser.add_argument(
        "--bulletins-dir", type=Path, default=_LOCAL_DEFAULT_BULLETINS_DIR,
        help=f"Bulletins directory for --all and default --out-dir "
             f"(default: {_LOCAL_DEFAULT_BULLETINS_DIR}).",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None,
        help="Bulletins root under which PT_{Y}_{M}_{date}/ folders "
             "are created (default: --bulletins-dir).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-extract even when events.json is newer than the source PDF.",
    )
    ns = parser.parse_args(argv)

    if ns.all_mode and ns.pdf:
        parser.error("--pdf and --all are mutually exclusive")
    if ns.all_mode:
        candidates = sorted(ns.bulletins_dir.glob("*.pdf"))
        # Skip legacy multi-UUID bundle parts (RAR payloads with .pdf names) —
        # see pdf_extract_patent for the same reasoning.
        candidates = [p for p in candidates if "_legacy_part" not in p.stem]
        if not candidates:
            parser.error(f"--all matched no *.pdf files in {ns.bulletins_dir}")
        pdf_paths = candidates
    elif ns.pdf:
        pdf_paths = list(ns.pdf)
    else:
        parser.error("provide --pdf (one or more) or --all")

    out_dir = ns.out_dir if ns.out_dir is not None else ns.bulletins_dir

    return CLIArgs(pdf_paths=pdf_paths, out_dir=out_dir, force=ns.force)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_argv(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    succeeded: List[str] = []
    skipped: List[str] = []
    failed: List[Tuple[str, str]] = []
    missing: List[str] = []

    for pdf in args.pdf_paths:
        try:
            result = _process_one(pdf, args.out_dir, force=args.force)
        except Exception as exc:
            logger.error("[!] %s: %r", pdf.name, exc)
            failed.append((pdf.name, repr(exc)))
            continue
        status = result.get("status")
        if status == "ok":
            succeeded.append(pdf.name)
        elif status == "skipped":
            skipped.append(pdf.name)
        elif status == "missing":
            missing.append(pdf.name)
            logger.warning("[skip] %s: not found", pdf.name)
        elif status == "failed":
            failed.append((pdf.name, result.get("error", "unknown")))

    duration = time.time() - started
    logger.info(
        "Done in %.1fs: %d ok, %d skipped, %d missing, %d failed",
        duration, len(succeeded), len(skipped), len(missing), len(failed),
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

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


def parse_event_index_page(
    page_text: str,
    page_no: int,
    bulletin_no: Optional[str],
) -> List[ParsedEvent]:
    """Parse one EVENT_INDEX page text into a list of events.

    Algorithm:
      1. Split page text into lines, strip whitespace per line.
      2. Find every line matching ``^YYYY/NNNNNN$`` — those are event
         anchors. Header lines ("BAŞVURU NUMARALARINA..." etc.) get
         dropped naturally because they don't match.
      3. For each consecutive pair of anchors (i, i+1), the lines
         between them are the description for the FIRST anchor.
         Multi-line descriptions (PDF text-extraction wraps long
         phrases) get joined with " " before classification.
      4. The last anchor's description runs to the end of the page;
         trailing blank lines are stripped.

    The same application_no can have multiple events on one page —
    each anchor produces one event regardless of duplicates. The
    fingerprint includes event_type + free_text so dedup at ingest
    time keeps both rows.

    page_no is 1-based (PyMuPDF doc[i] uses 0-based, callers pass
    i + 1). Stored in patent_events.page for traceability.
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

    # Find anchor positions
    anchors: List[Tuple[int, str]] = [
        (i, line) for i, line in enumerate(lines)
        if _APPNO_LINE_RE.match(line)
    ]
    if not anchors:
        return []

    events: List[ParsedEvent] = []
    for k, (idx, app_no) in enumerate(anchors):
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

        # Section-state machine: when the parser encounters a page
        # that contains a canonical section header, that header's
        # event_type becomes the default for all UNKNOWN rows on this
        # and subsequent pages until either (a) another recognised
        # section header appears, or (b) the page changes back to
        # INID_RECORDS / SKIP. State resets between INID-vs-event
        # transitions so a section doesn't bleed across the bulletin.
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

            section_hint = detect_section_event_type(page_text)
            if section_hint is not None:
                current_section_event_type = section_hint

            page_events = parse_event_index_page(
                page_text, page_no=i + 1, bulletin_no=bulletin_no,
            )
            # Apply section override to UNKNOWN-classified rows. Rows
            # whose free_text matched a phrase keep their classifier
            # result (handles intermixed sections where the flat-
            # event-index style still appears alongside).
            if current_section_event_type:
                for ev in page_events:
                    if ev.event_type == EVENT_TYPE_UNKNOWN:
                        ev.event_type = current_section_event_type
                        # Re-fingerprint to incorporate the assigned
                        # event_type — otherwise dedup at ingest time
                        # would still see UNKNOWN+free_text variants.
                        ev.fingerprint = event_fingerprint(
                            bulletin_no, ev.application_no,
                            ev.event_type, ev.free_text,
                        )
            events.extend(page_events)

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
    events_path = parent / EVENTS_FILENAME

    if not force and _events_filename_is_fresh(pdf, events_path):
        logger.info("[=] %s is fresh, skipping (use --force to override)",
                    pdf.name)
        return {"status": "skipped", "pdf": pdf.name,
                "out": f"{parent.name}/{EVENTS_FILENAME}"}

    parent.mkdir(parents=True, exist_ok=True)
    payload = parse_pdf_events(pdf)
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

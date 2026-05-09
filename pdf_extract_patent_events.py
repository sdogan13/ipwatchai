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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


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
    # Grant lifecycle
    ("Verilen Patent / Faydalı Model İlanı (6769 SMK)",
     "GRANT_ANNOUNCED"),
    ("Kesinleşen Patent Verilme Kararının İlanı (6769 SMK)",
     "GRANT_FINALIZED"),
    ("Reddedilen Patent/Faydalı Model Başvurularının İlanı (6769 SMK)",
     "APPLICATION_REJECTED"),
    # Ownership transfers
    ("Devir İşlemi Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "ASSIGNMENT_RECORDED"),
    ("Birleşme İşlemi Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "MERGER_RECORDED"),
    # UM conversion
    ("Faydalı Modele Dönüşüm İlanı (6769 SMK)",
     "CONVERSION_TO_UM"),
    # Post-publication amendments
    ("Patent/FM Model Başvurularında/Belgelerinde Yayından Sonraki Değişikliğin İlanı",
     "POST_PUB_AMENDMENT"),
    # Fee lapses (granted vs application — distinct by phrasing)
    ("Verilen Patent/FM Belgelerinin Yıllık Ücretinin Ödenmemesi Nedeniyle Geçersizlik ilanı",
     "GRANT_FEE_LAPSE"),
    ("Patent/FM Başvurularının Yıllık Ücretinin Ödenmemesi Nedeniyle Geçersizlik ilanı",
     "APPLICATION_FEE_LAPSE"),
    # Revalidations (fee-paid vs procedural-resumption — distinct)
    ("Yıllık Ücretlerinin Ödenmemesi Nedeniyle Geçersiz Olan Patent/FM Belgelerinin Yeniden Geçerlilik İlanı",
     "FEE_REVALIDATION"),
    ("Yeniden Geçerlilik Kazanan Patent/Faydalı Model Başvurularının İlanı (İşlemlerin Devam Ettirilmesi)",
     "PROCEDURAL_REVALIDATION"),
    # Use declarations
    ("Kullanma/Kullanmama Beyanı Verilmemiş Olan Başvuru veya Patent/Faydalı Modellerin İlanı",
     "USE_NONUSE_DECLARATION_MISSING"),
    ("Kullanıldığı Beyanı Sicile Kaydedilen Başvuru veya Patent/Faydalı Modellerin İlanı (6769 SMK)",
     "USE_DECLARATION_RECORDED"),
    # Search reports (patent vs UM — distinct)
    ("Yayımlanmış Patent Başvurularının Araştırma Raporları (6769 SMK)",
     "SEARCH_REPORT_PATENT"),
    ("Yayımlanmış Faydalı Model Başvurularının Araştırma Raporları (6769 SMK)",
     "SEARCH_REPORT_UM"),
    # YİDK board decisions
    ("6769 Sayılı SMK'nın 99 uncu Maddesi Hükmü Uyarınca YIDK Tarafından Patent Hakkının "
     "Değiştirilmiş Haliyle Devamına Karar Verilen Patentler",
     "YIDK_AMENDED_CONTINUATION"),
]

# Sentinel for descriptions that don't match any known phrase. The
# description text is preserved in events[].free_text so the mapping
# can be extended later without re-extracting.
EVENT_TYPE_UNKNOWN = "UNKNOWN"


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

    lines = [line.strip() for line in page_text.splitlines()]
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

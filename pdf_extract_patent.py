"""Patent / Faydalı Model bulletin PDF metadata extractor.

Sister to ``cd_extract_patent.py`` (Stage 2 of the patent ingest pipeline).
Reads a single ``YYYY_M.pdf`` from
``bulletins/Patent__Faydali_Model/`` and produces a ``YYYY_M_pdf_metadata.json``
sidecar that the Stage 4 reconciler can merge with the parallel
``YYYY_M_metadata.json`` produced from the CD bundle.

The patent PDF carries five record families (kind-coded via the suffix of
the ``(11)`` publication number):

  * ``GRANTED_PATENT``      — kind ``B``, ``T4``
  * ``GRANTED_UM``          — kind ``Y``
  * ``PUBLISHED_APP``       — kind ``A1``, ``A2``, ``T``, ``T3``
  * ``PUBLISHED_UM_APP``    — kind ``U``, ``U4``, ``U5``, ``T5``, ``T6``
  * ``EP_FASCICLE``         — any of the above with ``T`` family kind +
                               doubled ``(96)``/``(97)`` references

INID format is **line-oriented** (each ``(NN)`` starts a new physical
line, value is the lines that follow until the next ``(NN)``). This
differs from the inline INID layout used by Tasarım designs.

Built incrementally — each helper has its own unit-test block.

CLI (lands in step 3.8)::

    python pdf_extract_patent.py --pdf bulletins/Patent__Faydali_Model/2025_08.pdf
    python pdf_extract_patent.py --all
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Dict, List, Optional, Tuple


def _get_fitz():
    """Lazy PyMuPDF import so unit tests don't need libfitz at collection time."""
    import fitz  # type: ignore[import-not-found]
    return fitz


# ---------------------------------------------------------------------------
# Step 3.1 — clean_text, normalize_iso_date, parse_inid_block
# ---------------------------------------------------------------------------

# 2-digit INID codes the parser recognises. Captured from real records in
# 2025_08.pdf; matches the documented whitelist in
# bulletins/Patent__Faydali_Model/README.md §3.
PATENT_INID_CODES = frozenset({
    "10", "11", "12", "19",
    "21", "22", "24",
    "30", "31", "32", "33",
    "43", "44", "45",
    "51", "54", "57",
    "71", "72", "73", "74",
    "86", "87", "88",
    "96", "97",
})

# Line-anchored INID token regex. Matches a 2-digit INID code in
# parentheses ONLY when it appears at the start of a line (possibly with
# leading whitespace).  This is what protects against the (57)-abstract
# trap — abstracts routinely contain mid-sentence (2), (11), (20) etc.
# referring to figure call-outs, and a non-line-anchored regex would
# treat those as record-field boundaries.
_INID_CODE_GROUP = "|".join(sorted(PATENT_INID_CODES))
_INID_TOKEN_RE = re.compile(
    rf"(?:^|\n)[ \t]*\(({_INID_CODE_GROUP})\)",
    re.MULTILINE,
)

# Date in the patent PDF body: ``2024/04/22`` (YYYY/MM/DD).  Distinct from
# the CD's HSQLDB ``DD/MM/YYYY`` — that's why this lives next to the PDF
# extractor rather than being shared with cd_extract_patent.
_PATENT_PDF_DATE_RE = re.compile(r"\b(\d{4})/(\d{2})/(\d{2})\b")


def clean_text(text: Optional[str]) -> str:
    """Collapse all whitespace runs (including newlines) to single spaces.

    Returns the empty string for ``None`` so the caller can chain into
    string operations without ``Optional`` guards.
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\x00", "")).strip()


def normalize_iso_date(raw: Optional[str]) -> Optional[str]:
    """``2024/04/22`` -> ``2024-04-22``. ``None`` if no match.

    Searches the input rather than full-matching, because real INID
    values often carry the date embedded in surrounding label text
    (e.g. ``"Başvuru Yayın Tarihi\\n2024/04/22, 2024/4 Nolu Bülten"``).
    """
    if not raw:
        return None
    m = _PATENT_PDF_DATE_RE.search(raw)
    if not m:
        return None
    yyyy, mm, dd = m.groups()
    return f"{yyyy}-{mm}-{dd}"


def normalize_tr_date(raw: Optional[str]) -> Optional[str]:
    """``21.08.2025`` -> ``2025-08-21``. ``None`` if no match.

    Used for the cover-page ``Yayım Tarihi`` field, which renders the
    date in Turkish DD.MM.YYYY convention. The body of the PDF uses
    YYYY/MM/DD instead — see ``normalize_iso_date``.
    """
    if not raw:
        return None
    m = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", raw)
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return f"{yyyy}-{mm}-{dd}"


# Cover-page header patterns. Two layouts seen in the wild:
#
#   2023+:    Sayı 2025-08
#             Yayım Tarihi
#             21.08.2025
#
#   2019–2022 (uppercase + colon-separated):
#             SAYI
#                     : 2022-09 (EYLÜL)
#             YAYIN TARİHİ        : 21.09.2022
#
# The regexes are case-insensitive on ASCII letters, with explicit
# character classes for the Turkish I family (``ı``/``İ``/``I``/``i``)
# since Python's ``re.IGNORECASE`` doesn't fold those across cases.
# An optional colon separator and arbitrary whitespace (newlines included
# via ``\s*``) cover both layouts.
_BULLETIN_NO_RE = re.compile(
    r"SAY[Iıİ]\s*:?\s*(\d{4}-\d{1,2})",
    re.IGNORECASE,
)
_BULLETIN_DATE_RE = re.compile(
    r"YAY[Iıİ][NM]\s+TAR[İI]H[Iıİ]\s*:?\s*(\d{2})\.(\d{2})\.(\d{4})",
    re.IGNORECASE,
)


def extract_bulletin_metadata_from_text(
    text: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Pure helper: extract ``(bulletin_no, bulletin_date_iso)`` from one
    page's text.

    Returns ``(None, None)`` when neither pattern matches, or one side
    populated and the other ``None``. Splitting this from the doc-level
    wrapper keeps the logic unit-testable without a live PDF.
    """
    if not text:
        return None, None

    bulletin_no: Optional[str] = None
    m = _BULLETIN_NO_RE.search(text)
    if m:
        bulletin_no = m.group(1)

    bulletin_date: Optional[str] = None
    m = _BULLETIN_DATE_RE.search(text)
    if m:
        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
        bulletin_date = f"{yyyy}-{mm}-{dd}"

    return bulletin_no, bulletin_date


def extract_bulletin_metadata(
    doc,
    *,
    max_pages: int = 3,
) -> Tuple[Optional[str], Optional[str]]:
    """Scan the first ``max_pages`` of a PyMuPDF doc for the bulletin
    header: ``Sayı YYYY-M`` and ``Yayım Tarihi DD.MM.YYYY``.

    Either return value may be ``None`` if the corresponding pattern is
    absent — the caller decides whether that's a hard failure. Stops
    scanning as soon as both have been found.
    """
    bulletin_no: Optional[str] = None
    bulletin_date: Optional[str] = None

    pages_to_scan = min(max_pages, getattr(doc, "page_count", 0))
    for i in range(pages_to_scan):
        text = doc[i].get_text("text")
        no, date = extract_bulletin_metadata_from_text(text)
        if bulletin_no is None and no is not None:
            bulletin_no = no
        if bulletin_date is None and date is not None:
            bulletin_date = date
        if bulletin_no and bulletin_date:
            break

    return bulletin_no, bulletin_date


class RecordType(str, Enum):
    """Top-level record family, derived from the (11) publication-no kind code."""

    GRANTED_PATENT = "GRANTED_PATENT"
    GRANTED_UM = "GRANTED_UM"
    PUBLISHED_APP = "PUBLISHED_APP"
    PUBLISHED_UM_APP = "PUBLISHED_UM_APP"
    UNKNOWN = "UNKNOWN"


# Kind-code → record-type mapping (per bulletins/Patent__Faydali_Model/README.md §3).
# EP-fascicle status is orthogonal to record_type — that's detected
# separately via the dual (96)/(97) INID quirk in step 3.4.
_KIND_TO_RECORD_TYPE: Dict[str, RecordType] = {
    "B":  RecordType.GRANTED_PATENT,
    "T4": RecordType.GRANTED_PATENT,  # EP-fascicle Turkish translation of grant
    "Y":  RecordType.GRANTED_UM,
    "A1": RecordType.PUBLISHED_APP,
    "A2": RecordType.PUBLISHED_APP,
    "T":  RecordType.PUBLISHED_APP,
    "T3": RecordType.PUBLISHED_APP,
    "U":  RecordType.PUBLISHED_UM_APP,
    "U4": RecordType.PUBLISHED_UM_APP,
    "U5": RecordType.PUBLISHED_UM_APP,
    "T5": RecordType.PUBLISHED_UM_APP,
    "T6": RecordType.PUBLISHED_UM_APP,
}

# Publication-number pattern: ``TR YYYY NNNNNN [kind]``. The kind code is
# 1 letter optionally followed by 1 digit (so ``B``, ``Y``, ``A1``,
# ``T4``, ``U5`` all match).
_PUBLICATION_NO_RE = re.compile(r"\bTR\s+(\d{4})\s+(\d{4,7})\s+([A-Z]\d?)\b")


def extract_kind_code(publication_no_value: Optional[str]) -> Optional[str]:
    """Pull the trailing kind code from a (11) publication-number value.

    Examples:

      ``'TR 2022 014462 B'``    -> ``'B'``
      ``'TR 2024 000746 A1'``   -> ``'A1'``
      ``'TR 2025 010866 T4'``   -> ``'T4'``

    Returns ``None`` if the value doesn't match the publication-number
    shape — defensive, the caller can fall back to ``RecordType.UNKNOWN``.
    """
    if not publication_no_value:
        return None
    m = _PUBLICATION_NO_RE.search(publication_no_value)
    return m.group(3) if m else None


def classify_kind_code(kind: Optional[str]) -> RecordType:
    """Map a kind-code string to a ``RecordType``.

    Unknown / missing kinds return ``RecordType.UNKNOWN`` so the caller
    can keep parsing the rest of the record without losing the row.
    """
    if not kind:
        return RecordType.UNKNOWN
    return _KIND_TO_RECORD_TYPE.get(kind.upper(), RecordType.UNKNOWN)


class PageKind(str, Enum):
    """Coarse classification of a PDF page, used to gate parsing.

    The patent PDF interleaves three page kinds:

      * ``INID_RECORDS`` — pages we want to parse for full-bibliographic records
      * ``EVENT_INDEX``  — flat 'appno + Turkish phrase' pages (Stage 7, deferred)
      * ``SKIP``         — cover, TOC, section headers, blank pages
    """

    INID_RECORDS = "inid_records"
    EVENT_INDEX = "event_index"
    SKIP = "skip"


# Heuristics for identifying event-index pages. Empirically derived from
# 2025_08.pdf pages 7–114 + 1190–1844 — those pages are dominated by
# application-number + Turkish-phrase pairs, with no INID tokens at all.
# We require BOTH an application-number pattern AND the absence of
# line-anchored INID tokens, so a stray page with one numeral doesn't
# get misclassified.
_APPNO_LINE_RE = re.compile(r"^\d{4}/\d{4,7}\s*$", re.MULTILINE)


def detect_page_kind(page_text: Optional[str]) -> PageKind:
    """Classify a single page's text.

    Three-way result:

      * ``INID_RECORDS`` — at least one line-anchored 2-digit INID token
        from the documented whitelist.
      * ``EVENT_INDEX``  — no INID tokens, but at least one bare
        ``YYYY/NNNNNN`` application-number line.
      * ``SKIP``         — neither (cover, TOC, blank, etc.).
    """
    if not page_text:
        return PageKind.SKIP

    if _INID_TOKEN_RE.search(page_text):
        return PageKind.INID_RECORDS

    if _APPNO_LINE_RE.search(page_text):
        return PageKind.EVENT_INDEX

    return PageKind.SKIP


def parse_inid_block(text: str) -> Dict[str, List[str]]:
    """Tokenize a line-oriented INID-coded text block.

    Returns ``{code: [value, value, …]}``, where ``value`` is the raw
    text between the closing ``)`` of one INID code and the opening
    ``(`` of the next (or end of block).  Codes that recur (multiple
    inventors on (72), the EP-fascicle dual ``(96)``/``(97)`` pattern)
    appear as ordered lists under the same key.

    Two design decisions, both important:

    1. **Line anchored.** Only ``(NN)`` tokens appearing at the start of
       a line (after optional whitespace) are recognised. Stray
       parenthesised numerals inside the ``(57)`` abstract — which
       commonly say things like ``"…bir kapı (3) ve gövdeye (2)…"`` —
       are NOT treated as field boundaries.

    2. **Whitelist only.** The token regex matches just the 26 codes
       documented for patent bulletins (see ``PATENT_INID_CODES``).
       Future-unknown codes pass through silently as part of the
       previous field's value, which is the safe default.

    Returns an empty dict for empty / falsy input.
    """
    if not text:
        return {}

    matches = list(_INID_TOKEN_RE.finditer(text))
    if not matches:
        return {}

    out: Dict[str, List[str]] = {}
    for idx, m in enumerate(matches):
        code = m.group(1)
        value_start = m.end()
        value_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        value = text[value_start:value_end].strip()
        out.setdefault(code, []).append(value)
    return out

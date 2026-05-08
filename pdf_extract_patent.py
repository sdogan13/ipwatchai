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

import argparse
import json
import logging
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


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


# ---------------------------------------------------------------------------
# Step 3.4 — schema dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Holder:
    """A patent holder / applicant (INID 73 / 71)."""
    name: str
    address: Optional[str] = None
    country: Optional[str] = None


@dataclass
class Inventor:
    """A natural-person inventor (INID 72)."""
    name: str


@dataclass
class Attorney:
    """The agent / law firm representing the applicant (INID 74)."""
    name: str
    firm: Optional[str] = None


@dataclass
class Priority:
    """One priority claim (INID 30 + sub-codes 31/32/33)."""
    priority_no: Optional[str] = None
    priority_date: Optional[str] = None  # ISO YYYY-MM-DD
    country: Optional[str] = None


@dataclass
class EPReference:
    """European Patent fascicle metadata (dual (96) / (97) INID values)."""
    ep_application_no: Optional[str] = None
    ep_application_date: Optional[str] = None
    ep_publication_no: Optional[str] = None
    ep_publication_date: Optional[str] = None


# ---------------------------------------------------------------------------
# Step 3.4 — pure per-INID field parsers
# ---------------------------------------------------------------------------

# Address indicators that distinguish "single entity with a multi-line
# postal address" from "list of natural-person names". Captures common
# Turkish address abbreviations + the presence of any digit (street
# number, postal code).
_ADDRESS_HINT_RE = re.compile(
    r"\d|MAH\.|MH\.|CAD\.|SK\.|SOK\.|BLV\.|BLK\.|NO[:.]",
    re.IGNORECASE,
)

# IPC class shape: e.g. ``F25B 9/14`` (with space) or ``H02G3/12`` (no space).
_IPC_CODE_RE = re.compile(r"\b([A-H]\d{2}[A-Z])\s?(\d+/\d+)")

# EP publication-no shape: e.g. ``EP3885497B1``.
_EP_PUB_NO_RE = re.compile(r"\b(EP\s*\d{5,}\s*[A-Z]\d?)\b", re.IGNORECASE)

# EP application-no shape: e.g. ``EP21164305.1``.
_EP_APP_NO_RE = re.compile(r"\b(EP\s*\d{5,}(?:\.\d+)?)\b", re.IGNORECASE)


def _strip_label_line(value: Optional[str]) -> str:
    """Drop the first line if it looks like a Turkish field label.

    Many INID values render the human-readable label on the first line
    and the data on subsequent lines (e.g. ``Başvuru Tarihi\\n2022/09/20``).
    The label is alpha-only and reasonably short. Pure-data values
    (e.g. ``TR 2022 014462 B``) are returned unchanged.
    """
    if not value:
        return ""
    lines = value.splitlines()
    if not lines:
        return ""
    first = lines[0].strip()
    # Heuristic: a label line has no digits AND is short (< 60 chars).
    # The (51) IPC value's first line "Buluşun tasnif sınıfları" fits;
    # so does (54) "Buluş Başlığı", (57) "Özet", (73) "Patent Sahibi",
    # (72) "Buluşu Yapanlar", (74) "Vekil".
    if first and not any(c.isdigit() for c in first) and len(first) < 60:
        return "\n".join(lines[1:]).strip()
    return value.strip()


def parse_publication_no(value: Optional[str]) -> Optional[str]:
    """Extract the publication number from an (11) or (10) value.

    e.g. ``'TR 2022 014462 B'`` -> ``'TR 2022 014462 B'``
         ``'Yayın No\\nTR 2022 014462 A2'`` -> ``'TR 2022 014462 A2'``

    Returns ``None`` when the value doesn't contain the publication
    number shape — the caller can treat this as a parse failure.
    """
    if not value:
        return None
    m = _PUBLICATION_NO_RE.search(value)
    if not m:
        return None
    yyyy, num, kind = m.group(1), m.group(2), m.group(3)
    return f"TR {yyyy} {num} {kind}"


_APPLICATION_NO_RE = re.compile(r"\b(\d{4})/(\d{4,7})\b")


def parse_application_no(value: Optional[str]) -> Optional[str]:
    """Extract the application number from an (21) value.

    ``'Başvuru Numarası\\n2022/014462'`` -> ``'2022/014462'``.
    """
    if not value:
        return None
    m = _APPLICATION_NO_RE.search(value)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def parse_date_field(value: Optional[str]) -> Optional[str]:
    """Pull the first ``YYYY/MM/DD`` date out of an INID value and
    return ISO ``YYYY-MM-DD``. Wraps ``normalize_iso_date``.
    """
    return normalize_iso_date(value)


def parse_ipc_classes(value: Optional[str]) -> List[str]:
    """Extract IPC class strings from a (51) value.

    Real shapes from 2025_08.pdf:
      ``'Buluşun tasnif sınıfları\\nF25B 9/14\\nF25D 17/04\\nF25D 23/04'``
      -> ``['F25B 9/14', 'F25D 17/04', 'F25D 23/04']``

    Codes WITHOUT internal whitespace (``H02G3/12``) are normalised back
    to ``H02G 3/12`` so all output is consistent. Order is preserved;
    duplicates are dropped.
    """
    if not value:
        return []
    seen: List[str] = []
    for m in _IPC_CODE_RE.finditer(value):
        code = f"{m.group(1)} {m.group(2)}"
        if code not in seen:
            seen.append(code)
    return seen


def parse_title(value: Optional[str]) -> Optional[str]:
    """Extract the title from a (54) value.

    Drops the leading ``Buluş Başlığı`` label line (when present), joins
    any continuation lines into a single string, collapses whitespace.
    Returns ``None`` for empty input.
    """
    if not value:
        return None
    body = _strip_label_line(value)
    return clean_text(body) or None


def parse_abstract(value: Optional[str]) -> Optional[str]:
    """Extract the abstract from a (57) value.

    Drops the leading ``Özet`` label, but PRESERVES embedded newlines
    so figure call-outs like ``…bir kapı (3)\\n…`` keep their intended
    sentence structure when reading downstream.
    """
    if not value:
        return None
    body = _strip_label_line(value)
    if not body:
        return None
    # collapse runs of internal whitespace per-line, but keep newlines
    cleaned_lines = [re.sub(r"[ \t]+", " ", l).strip() for l in body.splitlines()]
    cleaned_lines = [l for l in cleaned_lines if l]
    return "\n".join(cleaned_lines) or None


def _is_likely_country_token(token: str) -> bool:
    """A trailing token looks like a country marker if it's all-caps
    and at least 4 chars (avoids matching house-numbers or 'NO')."""
    return bool(token) and token.isupper() and len(token) >= 4 and token.isalpha()


def parse_holders(value: Optional[str]) -> List[Holder]:
    """Parse a (71) / (73) holder block.

    Two real shapes:

      - Single entity with multi-line postal address (typical for (73)
        granted-patent rows)::

            Patent Sahibi
            ARÇELİK ANONİM ŞİRKETİ
            SÜTLÜCE MAH. KARAAĞAÇ CAD. 6  Beyoğlu
            İstanbul TÜRKİYE

      - List of natural-person names (typical for (71) pending-app rows)::

            Başvuru Sahipleri
            EMİNE YILDIRIM
            ZEYNEP ERVA YILDIRIM
            AHMET ÇARHAN

    Heuristic: if any line after the first looks like an address
    (digits or MAH./CAD./SK. abbreviations), treat the whole thing
    as a single entity. Otherwise treat each line as a separate holder.
    """
    body = _strip_label_line(value)
    if not body:
        return []

    lines = [clean_text(l) for l in body.splitlines() if l.strip()]
    if not lines:
        return []

    # Single entity with address?
    if len(lines) >= 2 and any(_ADDRESS_HINT_RE.search(l) for l in lines[1:]):
        name = lines[0]
        address_lines = lines[1:]
        country: Optional[str] = None
        if address_lines:
            tail_tokens = address_lines[-1].split()
            if tail_tokens and _is_likely_country_token(tail_tokens[-1]):
                country = tail_tokens[-1]
                # drop the country word from the address tail
                address_lines = address_lines[:-1] + [" ".join(tail_tokens[:-1])]
                address_lines = [l for l in address_lines if l]
        address = " ".join(address_lines).strip() or None
        return [Holder(name=name, address=address, country=country)]

    # Otherwise: list of name-only entities
    return [Holder(name=l) for l in lines]


def parse_inventors(value: Optional[str]) -> List[Inventor]:
    """Parse a (72) inventor block — a list of natural-person names.

    The block is always shaped as a label line followed by one name
    per line (no addresses). Empty input -> empty list.
    """
    body = _strip_label_line(value)
    if not body:
        return []
    out: List[Inventor] = []
    for raw in body.splitlines():
        name = clean_text(raw)
        if name:
            out.append(Inventor(name=name))
    return out


def parse_attorney(value: Optional[str]) -> Optional[Attorney]:
    """Parse a (74) ``Vekil`` block: ``NAME (FIRM)``.

    The firm clause often line-wraps mid-name, so we join all post-label
    lines into one string before regex-matching.
    """
    body = _strip_label_line(value)
    if not body:
        return None
    text = clean_text(body)
    if not text:
        return None
    m = re.match(r"^\s*(.*?)\s*\(([^)]*)\)?\s*$", text)
    if m:
        name = m.group(1).strip()
        firm = m.group(2).strip().rstrip(")") or None
        if name:
            return Attorney(name=name, firm=firm)
    return Attorney(name=text)


# Priority data row: ``2020/03/24  DE  DE 202010203797``
# Date | country (2-letter) | number-with-optional-prefix
_PRIORITY_ROW_RE = re.compile(
    r"(\d{4}/\d{2}/\d{2})\s+([A-Z]{2})\s+(.+?)(?=\s*$)",
    re.MULTILINE,
)


def parse_priorities(values_30: Sequence[str]) -> List[Priority]:
    """Parse priority claims from (30) Rüçhan Bilgileri values.

    Real shape (from real EP fascicle on page 1000 of 2025_08.pdf)::

        Rüçhan Bilgileri (32) (33) (31)
        2020/03/24  DE  DE 202010203797

    The (32)/(33)/(31) sub-codes in the header row are column labels
    for date / country / number. The data rows that follow may be
    empty (no priorities) or contain one or more rows.

    Returns an empty list for unparseable / empty input.
    """
    out: List[Priority] = []
    if not values_30:
        return out
    for raw in values_30:
        body = _strip_label_line(raw)
        if not body:
            continue
        for m in _PRIORITY_ROW_RE.finditer(body):
            date_raw, country, number = m.group(1), m.group(2), m.group(3).strip()
            iso = normalize_iso_date(date_raw)
            if iso is None:
                continue
            out.append(Priority(
                priority_no=number or None,
                priority_date=iso,
                country=country,
            ))
    return out


def parse_ep_reference(
    values_96: Sequence[str],
    values_97: Sequence[str],
) -> Optional[EPReference]:
    """Parse the EP-fascicle dual (96) / (97) values.

    These INID codes carry BOTH a date and a number, ordered by the PDF
    in unpredictable sequence. We classify each value by content
    shape — one with a ``YYYY/MM/DD`` date is the date, one with an
    ``EP…`` token is the number.

    Returns ``None`` when no EP-shape data is present in any value
    (i.e. this isn't an EP fascicle record).
    """
    ref = EPReference()
    found_any = False

    for raw in values_96 or []:
        date = normalize_iso_date(raw)
        if date and ref.ep_application_date is None:
            ref.ep_application_date = date
            found_any = True
            continue
        m = _EP_APP_NO_RE.search(raw)
        if m and ref.ep_application_no is None:
            ref.ep_application_no = re.sub(r"\s+", "", m.group(1)).upper()
            found_any = True

    for raw in values_97 or []:
        date = normalize_iso_date(raw)
        if date and ref.ep_publication_date is None:
            ref.ep_publication_date = date
            found_any = True
            continue
        m = _EP_PUB_NO_RE.search(raw)
        if m and ref.ep_publication_no is None:
            ref.ep_publication_no = re.sub(r"\s+", "", m.group(1)).upper()
            found_any = True

    return ref if found_any else None


# ---------------------------------------------------------------------------
# Step 3.5 — PatentRecord dataclass + record boundary finder + orchestrator
# ---------------------------------------------------------------------------


@dataclass
class PatentRecord:
    """One full-bibliographic patent record from the PDF body."""

    record_index: int
    page_range: List[int]                  # [start_page, end_page] 1-indexed inclusive
    publication_no: str                    # e.g. 'TR 2022 014462 B'
    kind_code: str                         # e.g. 'B'
    record_type: RecordType                # GRANTED_PATENT / GRANTED_UM / …
    publication_kind_label: Optional[str] = None  # (12) free text
    application_no: Optional[str] = None         # (21) 'YYYY/NNNNNN'
    application_date: Optional[str] = None       # (22) ISO YYYY-MM-DD
    publication_date: Optional[str] = None       # (43) for apps
    grant_date: Optional[str] = None             # (45) for grants
    title: Optional[str] = None                  # (54)
    abstract: Optional[str] = None               # (57) — newlines preserved
    ipc_classes: List[str] = field(default_factory=list)              # (51)
    holders: List[Holder] = field(default_factory=list)               # (73) or (71)
    inventors: List[Inventor] = field(default_factory=list)           # (72)
    attorney: Optional[Attorney] = None                               # (74)
    priorities: List[Priority] = field(default_factory=list)          # (30)
    ep_reference: Optional[EPReference] = None                        # dual (96)/(97)
    figures: List[Dict[str, object]] = field(default_factory=list)    # populated in step 3.6


# Boundary-detection regex. Anchored at the start of a line, validates
# the publication-number shape directly after (11). Legend-page false
# matches like '(12) Başvurunun Türü' are filtered out by construction.
# The capturing group around the (11) token lets us recover the actual
# token start via ``m.start(1)`` — the outer ``(?:^|\n)[ \t]*`` would
# otherwise shift ``m.start()`` onto the previous page's trailing \n.
_RECORD_BOUNDARY_RE = re.compile(
    r"(?:^|\n)[ \t]*(\(11\)[ \t]+TR[ \t]+\d{4}[ \t]+\d{4,7}[ \t]+[A-Z]\d?)",
    re.MULTILINE,
)


def _build_global_text(page_texts: Sequence[str]) -> Tuple[str, List[int]]:
    """Concatenate per-page text into a single string with ``\\n`` between pages.

    Returns ``(full_text, page_starts)`` where ``page_starts[i]`` is the
    character offset in ``full_text`` at which page ``i`` (0-indexed)
    begins. Inter-page newlines are accounted for in the offsets.
    """
    parts: List[str] = []
    page_starts: List[int] = []
    cursor = 0
    for i, text in enumerate(page_texts):
        page_starts.append(cursor)
        parts.append(text)
        cursor += len(text)
        if i + 1 < len(page_texts):
            parts.append("\n")
            cursor += 1
    return "".join(parts), page_starts


def _char_pos_to_page(pos: int, page_starts: Sequence[int]) -> int:
    """Binary-search the 0-indexed page that contains ``pos``."""
    if not page_starts:
        return 0
    lo, hi = 0, len(page_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if page_starts[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _find_record_boundaries(
    full_text: str,
    page_starts: Sequence[int],
) -> List[Tuple[int, int, int, int]]:
    """Return ``(start_pos, end_pos, start_page, end_page)`` per record.

    ``start_pos`` is the position of the leading ``(11)`` in
    ``full_text``. ``end_pos`` is the position of the next record's
    ``(11)`` (or ``len(full_text)`` for the last record).
    Pages are 1-indexed inclusive ranges.

    Boundary regex requires a valid publication number shape, so
    legend-page or stray ``(11)`` false matches drop out automatically.
    """
    matches = list(_RECORD_BOUNDARY_RE.finditer(full_text))
    out: List[Tuple[int, int, int, int]] = []
    for i, m in enumerate(matches):
        # m.start(1) is the position of the actual ``(`` token, not the
        # preceding ``\n`` that the outer alternation matched.
        start = m.start(1)
        end = matches[i + 1].start(1) if i + 1 < len(matches) else len(full_text)
        start_page = _char_pos_to_page(start, page_starts) + 1
        end_page = _char_pos_to_page(max(start, end - 1), page_starts) + 1
        out.append((start, end, start_page, end_page))
    return out


def parse_full_bibliographic_record(
    block_text: str,
    *,
    record_index: int,
    page_range: Tuple[int, int],
) -> Optional[PatentRecord]:
    """Parse one record block (slice from one ``(11)`` to the next) into
    a populated ``PatentRecord``.

    Returns ``None`` when the block doesn't yield a valid publication
    number — that's the second-line validation gate for spurious
    boundary matches that slipped through the regex.
    """
    fields = parse_inid_block(block_text)
    if "11" not in fields or not fields["11"]:
        return None

    pub_no = parse_publication_no(fields["11"][0])
    if not pub_no:
        return None

    kind = extract_kind_code(pub_no) or ""
    record = PatentRecord(
        record_index=record_index,
        page_range=[page_range[0], page_range[1]],
        publication_no=pub_no,
        kind_code=kind,
        record_type=classify_kind_code(kind),
    )

    if "12" in fields and fields["12"]:
        record.publication_kind_label = clean_text(fields["12"][0]) or None
    if "21" in fields and fields["21"]:
        record.application_no = parse_application_no(fields["21"][0])
    if "22" in fields and fields["22"]:
        record.application_date = parse_date_field(fields["22"][0])
    if "43" in fields and fields["43"]:
        record.publication_date = parse_date_field(fields["43"][0])
    if "45" in fields and fields["45"]:
        record.grant_date = parse_date_field(fields["45"][0])
    if "51" in fields and fields["51"]:
        record.ipc_classes = parse_ipc_classes(fields["51"][0])
    if "54" in fields and fields["54"]:
        record.title = parse_title(fields["54"][0])
    if "57" in fields and fields["57"]:
        record.abstract = parse_abstract(fields["57"][0])

    # (73) for granted records, (71) for pending apps — same shape.
    holders_raw = fields.get("73") or fields.get("71") or []
    if holders_raw:
        record.holders = parse_holders(holders_raw[0])

    if "72" in fields and fields["72"]:
        record.inventors = parse_inventors(fields["72"][0])
    if "74" in fields and fields["74"]:
        record.attorney = parse_attorney(fields["74"][0])
    if "30" in fields:
        record.priorities = parse_priorities(fields["30"])

    record.ep_reference = parse_ep_reference(
        fields.get("96", []), fields.get("97", []),
    )

    return record


# ---------------------------------------------------------------------------
# Step 3.6 — figure extraction + xref dedup
# ---------------------------------------------------------------------------

logger = logging.getLogger("turkpatent.patent_extract")

# Default threshold for "this xref is the page-banner image". The README
# documents a banner referenced ~1,600 times in 2025_08.pdf; real
# invention drawings appear on at most a handful of pages even when a
# record's figures span 2-3 pages. 5 is a comfortable cutoff.
DEFAULT_BANNER_PAGE_THRESHOLD = 5


def _normalize_appno_for_filename(application_no: Optional[str]) -> str:
    """``2022/014462`` -> ``2022_014462``. Used to make figure filenames
    safe across platforms while still being human-recognisable."""
    if not application_no:
        return "unknown"
    return re.sub(r"[^0-9A-Za-z_]", "_", application_no.strip()) or "unknown"


def build_figure_inventory(doc) -> Dict[int, List[int]]:
    """Walk every page of ``doc`` and return ``{page_index: [xref, …]}``.

    ``page_index`` is 0-indexed, matching PyMuPDF's own indexing.
    Each value is the list of unique image xrefs on that page (the
    same xref isn't double-counted on the same page even if it appears
    twice in PDF object dictionaries).
    """
    inventory: Dict[int, List[int]] = {}
    for i in range(doc.page_count):
        page = doc[i]
        seen: Set[int] = set()
        ordered: List[int] = []
        try:
            images = page.get_images(full=True)
        except Exception:
            images = []
        for info in images:
            xref = info[0] if info else None
            if isinstance(xref, int) and xref not in seen:
                seen.add(xref)
                ordered.append(xref)
        inventory[i] = ordered
    return inventory


def detect_banner_xrefs(
    inventory: Mapping[int, Iterable[int]],
    threshold: int = DEFAULT_BANNER_PAGE_THRESHOLD,
) -> Set[int]:
    """Return the set of xrefs that appear on more than ``threshold`` pages.

    These are almost certainly page-banner images (header / footer
    glyphs reused across the whole document). Real invention drawings
    appear on at most a handful of pages even for records that span
    multiple pages.

    Pure function over the inventory dict — no PDF library calls — so
    it's easy to unit-test against synthetic inventories.
    """
    page_counts: Counter = Counter()
    for page_xrefs in inventory.values():
        for xref in page_xrefs:
            page_counts[xref] += 1
    return {xref for xref, n in page_counts.items() if n > threshold}


def _save_image_from_xref(doc, xref: int, dest: Path) -> bool:
    """Write the image referenced by ``xref`` to ``dest``.

    Converts CMYK pixmaps to RGB (PNG/JPEG can't carry CMYK reliably).
    Returns ``True`` on success. On any error, logs a warning and
    returns ``False`` — figure extraction is best-effort, not a hard
    failure for the rest of the record's metadata.
    """
    fitz = _get_fitz()
    try:
        pix = fitz.Pixmap(doc, xref)
        if pix.n - pix.alpha >= 4:  # CMYK
            pix = fitz.Pixmap(fitz.csRGB, pix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(dest))
        return dest.is_file() and dest.stat().st_size > 0
    except Exception as e:
        logger.warning("image extract failed for xref=%d: %r", xref, e)
        return False


def extract_record_figures(
    doc,
    record: PatentRecord,
    *,
    banner_xrefs: Set[int],
    figures_dir: Optional[Path] = None,
    save_images: bool = True,
) -> List[Dict[str, Any]]:
    """Walk the record's page range, collect non-banner figure metadata.

    For each non-banner image on each page in ``record.page_range``,
    builds an entry of the form::

        {
          "page": 200,
          "xref": 1234,
          "image_path": "figures/2022_014462_p200_1.png",  # relative to figures_dir's parent
          "width":  400,
          "height": 300,
        }

    When ``save_images=True`` and ``figures_dir`` is given, the image
    bytes are written to disk under that folder. With ``save_images=False``
    the metadata is returned but no I/O happens — useful for stats /
    dry runs.

    The same xref appearing on multiple pages of the same record is
    extracted only once (first-page wins) so the page-banner exclusion
    is robust even if a banner xref happens to slip past the threshold.
    """
    if not record.page_range:
        return []
    start_page, end_page = record.page_range[0], record.page_range[-1]

    appno_norm = _normalize_appno_for_filename(record.application_no)
    figures: List[Dict[str, Any]] = []
    seen_xrefs: Set[int] = set()

    for page_no in range(start_page, end_page + 1):
        if page_no < 1 or page_no > doc.page_count:
            continue
        page = doc[page_no - 1]
        try:
            images = page.get_images(full=True)
        except Exception:
            images = []

        for idx, img_info in enumerate(images):
            xref = img_info[0] if img_info else None
            if not isinstance(xref, int):
                continue
            if xref in banner_xrefs:
                continue
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            # Width / height come straight from the PDF object stream;
            # they're cheap to read and useful for filtering tiny stamps.
            width = img_info[2] if len(img_info) > 2 else None
            height = img_info[3] if len(img_info) > 3 else None

            entry: Dict[str, Any] = {
                "page": page_no,
                "xref": xref,
                "width": width,
                "height": height,
                "image_path": None,
            }

            if save_images and figures_dir is not None:
                # Pick an extension that PyMuPDF will encode losslessly.
                ext = ".png"
                fname = f"{appno_norm}_p{page_no}_{idx + 1}{ext}"
                dest = figures_dir / fname
                if _save_image_from_xref(doc, xref, dest):
                    entry["image_path"] = f"figures/{fname}"

            figures.append(entry)

    return figures


# ---------------------------------------------------------------------------
# Step 3.7 — parse_pdf orchestrator
# ---------------------------------------------------------------------------


def _record_to_dict(record: PatentRecord) -> Dict[str, Any]:
    """Serialize a ``PatentRecord`` for JSON, dropping unset optionals.

    Notes:
      - ``RecordType`` is a ``str``-Enum so it survives ``json.dumps``,
        but ``asdict()`` keeps the Enum instance — we coerce to its
        plain string value here so downstream consumers can compare
        cleanly with ``record_type == "GRANTED_PATENT"``.
      - ``attorney`` and ``ep_reference`` get popped when ``None`` so
        the JSON stays focused; readers can ``record.get("attorney")``.
    """
    d = asdict(record)
    rt = d.get("record_type")
    if hasattr(rt, "value"):
        d["record_type"] = rt.value
    if d.get("attorney") is None:
        d.pop("attorney", None)
    if d.get("ep_reference") is None:
        d.pop("ep_reference", None)
    return d


def parse_pdf(
    pdf_path: str | Path,
    *,
    figures_dir: Optional[str | Path] = None,
    save_images: bool = True,
    banner_threshold: int = DEFAULT_BANNER_PAGE_THRESHOLD,
) -> Dict[str, Any]:
    """End-to-end Patent / Faydalı Model PDF -> JSON-ready metadata dict.

    Pipeline:
      1. open doc
      2. extract_bulletin_metadata (cover page Sayı / Yayım Tarihi)
      3. build global text + page_starts
      4. find record boundaries (validated against the (11) pub-no shape)
      5. build figure inventory + detect banner xrefs
      6. for each boundary: parse_full_bibliographic_record, extract
         non-banner figures, attach to record
      7. assemble payload + stats

    Returns a JSON-serialisable dict with ``bulletin_no``,
    ``bulletin_date``, ``source_pdf``, ``page_count``, ``extracted_at``,
    ``stats`` and ``records`` (a list of per-record dicts).

    ``save_images=False`` is a dry-run mode — figure metadata is still
    populated (page, xref, width, height) but no image files are
    written. Useful for stats / pipeline shape verification.

    ``figures_dir`` defaults to ``None`` (no images saved). Pass an
    explicit path to enable image extraction.
    """
    fitz = _get_fitz()
    pdf = Path(pdf_path)
    doc = fitz.open(str(pdf))

    figures_path = Path(figures_dir) if figures_dir is not None else None
    images_should_save = save_images and figures_path is not None

    try:
        bulletin_no, bulletin_date = extract_bulletin_metadata(doc)

        page_texts = [doc[i].get_text("text") for i in range(doc.page_count)]
        full_text, page_starts = _build_global_text(page_texts)
        boundaries = _find_record_boundaries(full_text, page_starts)

        inventory = build_figure_inventory(doc)
        banner_xrefs = detect_banner_xrefs(inventory, threshold=banner_threshold)

        records: List[PatentRecord] = []
        figure_total = 0
        for i, (start, end, start_page, end_page) in enumerate(boundaries):
            block = full_text[start:end]
            record = parse_full_bibliographic_record(
                block,
                record_index=i + 1,
                page_range=(start_page, end_page),
            )
            if record is None:
                continue
            record.figures = extract_record_figures(
                doc, record,
                banner_xrefs=banner_xrefs,
                figures_dir=figures_path,
                save_images=images_should_save,
            )
            figure_total += len(record.figures)
            records.append(record)

        type_counts: Dict[str, int] = {}
        for r in records:
            key = r.record_type.value
            type_counts[key] = type_counts.get(key, 0) + 1

        ep_fascicles = sum(1 for r in records if r.ep_reference is not None)

        payload: Dict[str, Any] = {
            "bulletin_no":     bulletin_no,
            "bulletin_date":   bulletin_date,
            "source_pdf":      pdf.name,
            "page_count":      doc.page_count,
            "extracted_at":    datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stats": {
                "records":               len(records),
                "by_record_type":        type_counts,
                "ep_fascicles":          ep_fascicles,
                "figures_total":         figure_total,
                "banner_xrefs_dropped":  len(banner_xrefs),
                "boundaries_found":      len(boundaries),
                "boundaries_unparseable": len(boundaries) - len(records),
            },
            "records": [_record_to_dict(r) for r in records],
        }
        return payload
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Step 3.8 — CLI entrypoint
# ---------------------------------------------------------------------------

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_DEFAULT_BULLETINS_DIR = _LOCAL_PROJECT_ROOT / "bulletins" / "Patent__Faydali_Model"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [PATENT-PDF] - %(levelname)s - %(message)s",
)
_cli_logger = logging.getLogger("turkpatent.pdf_extract_cli")


@dataclass
class CLIArgs:
    pdf_paths: List[Path]
    out_dir: Path
    figures_root: Optional[Path]
    save_images: bool
    force: bool


def metadata_filename(pdf_path: Path) -> str:
    """``2025_08.pdf`` -> ``2025_08_pdf_metadata.json``.

    The ``_pdf_`` infix distinguishes this from the CD-side
    ``2025_12_metadata.json`` produced by ``cd_extract_patent`` so the
    Stage 4 reconciler can read both as separate inputs.
    """
    return f"{pdf_path.stem}_pdf_metadata.json"


def figures_dirname(pdf_path: Path) -> str:
    """``2025_08.pdf`` -> ``2025_08_figures`` (folder name, no path)."""
    return f"{pdf_path.stem}_figures"


def _metadata_is_fresh(pdf_path: Path, json_path: Path) -> bool:
    """True when ``json_path`` exists, is non-empty, and is at least
    as recent as ``pdf_path``. Mirrors the tasarim freshness rule.
    """
    if not json_path.is_file():
        return False
    try:
        if json_path.stat().st_size == 0:
            return False
        return json_path.stat().st_mtime >= pdf_path.stat().st_mtime
    except OSError:
        return False


def parse_argv(argv: Optional[Sequence[str]] = None) -> CLIArgs:
    """Parse CLI arguments for the PDF-extractor entrypoint."""
    parser = argparse.ArgumentParser(
        prog="pdf_extract_patent",
        description=(
            "Extract Patent / Faydalı Model bulletin PDF metadata "
            "to JSON sidecars."
        ),
    )
    parser.add_argument(
        "--pdf",
        action="append",
        type=Path,
        help="Path to a bulletin .pdf file. Repeat for multiple.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process every YYYY_M.pdf in --bulletins-dir.",
    )
    parser.add_argument(
        "--bulletins-dir",
        type=Path,
        default=_DEFAULT_BULLETINS_DIR,
        help=f"Bulletins directory for --all and default --out-dir "
             f"(default: {_DEFAULT_BULLETINS_DIR}).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Where to write {YYYY_M}_pdf_metadata.json files "
             "(default: --bulletins-dir).",
    )
    parser.add_argument(
        "--figures-root",
        type=Path,
        default=None,
        help="Root directory for per-PDF figure folders. Defaults to "
             "the same folder as the JSON sidecar; figures land in "
             "{figures-root}/{YYYY_M}_figures/. Ignored when "
             "--no-images is given.",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip image extraction. Metadata is still produced with "
             "figure xref / page / bbox info, but no PNG files are "
             "written.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract even when {YYYY_M}_pdf_metadata.json is "
             "newer than the source PDF.",
    )
    ns = parser.parse_args(argv)

    if ns.all and ns.pdf:
        parser.error("--pdf and --all are mutually exclusive")

    if ns.all:
        candidates = sorted(ns.bulletins_dir.glob("*.pdf"))
        if not candidates:
            parser.error(f"--all matched no *.pdf files in {ns.bulletins_dir}")
        pdf_paths = candidates
    elif ns.pdf:
        pdf_paths = list(ns.pdf)
    else:
        parser.error("provide --pdf (one or more) or --all")

    out_dir = ns.out_dir if ns.out_dir is not None else ns.bulletins_dir
    figures_root = ns.figures_root if ns.figures_root is not None else out_dir

    return CLIArgs(
        pdf_paths=pdf_paths,
        out_dir=out_dir,
        figures_root=figures_root,
        save_images=not ns.no_images,
        force=ns.force,
    )


def _process_one(
    pdf: Path,
    out_dir: Path,
    figures_root: Optional[Path],
    *,
    save_images: bool,
    force: bool,
) -> Dict[str, Any]:
    """Run parse_pdf for a single PDF and write its JSON sidecar.

    Returns a small status dict so the top-level loop can report
    succeeded / skipped / failed counts.
    """
    if not pdf.is_file():
        return {"status": "missing", "pdf": pdf.name}

    json_path = out_dir / metadata_filename(pdf)
    if not force and _metadata_is_fresh(pdf, json_path):
        _cli_logger.info("[=] %s is fresh, skipping (use --force to override)",
                         pdf.name)
        return {"status": "skipped", "pdf": pdf.name, "out": json_path.name}

    figures_dir: Optional[Path] = None
    if save_images and figures_root is not None:
        figures_dir = figures_root / figures_dirname(pdf)
        figures_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    payload = parse_pdf(pdf, figures_dir=figures_dir, save_images=save_images)
    payload["extract_duration_seconds"] = round(time.time() - started, 1)

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    s = payload["stats"]
    _cli_logger.info(
        "[+] %s: %d records (EP=%d), %d figures, wrote %s in %.1fs",
        pdf.name, s["records"], s["ep_fascicles"], s["figures_total"],
        json_path.name, payload["extract_duration_seconds"],
    )
    return {
        "status": "ok",
        "pdf": pdf.name,
        "out": json_path.name,
        "stats": s,
    }


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
            result = _process_one(
                pdf,
                args.out_dir,
                args.figures_root,
                save_images=args.save_images,
                force=args.force,
            )
        except Exception as e:
            failed.append((pdf.name, repr(e)))
            _cli_logger.error("[!] %s: %r", pdf.name, e)
            continue

        status = result.get("status")
        if status == "ok":
            succeeded.append(pdf.name)
        elif status == "skipped":
            skipped.append(pdf.name)
        elif status == "missing":
            missing.append(pdf.name)
            _cli_logger.warning("[skip] %s: not found", pdf.name)

    duration = time.time() - started
    _cli_logger.info(
        "Done in %.1fs: %d ok, %d skipped, %d missing, %d failed",
        duration, len(succeeded), len(skipped), len(missing), len(failed),
    )
    return 0 if not (failed or missing) else 1


if __name__ == "__main__":
    raise SystemExit(main())

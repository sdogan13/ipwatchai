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
from typing import Dict, List, Optional


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

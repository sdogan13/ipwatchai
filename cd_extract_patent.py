"""Patent / Faydalı Model — CD bundle extractor.

Sister module to ``cd_extract_tasarim`` (none yet) and ``pdf_extract_patent``
(coming next). Targets the modern monthly CD archives downloaded by
``data_collection_patent.py``:

    bulletins/Patent__Faydali_Model/{YYYY_M}_CD.rar

Each CD is a 7-Zip-extractable RAR carrying:
  - data/ptbulletin.script   — HSQLDB DDL header
  - data/ptbulletin.log      — HSQLDB row inserts (the actual data)
  - data/ptbulletin.properties
  - data/images/{year}/*.tif — figure files (year-foldered)
  - data/java/...            — bundled JRE 1.4 (~80% of archive size, skip)

The output of this module is a per-issue JSON record bundle stored next to
the source archive, ready for the reconciler stage to merge with the
parallel modern-monthly PDF data.

Built incrementally. Each helper has its own unit-test file.
"""

from __future__ import annotations

import re
from typing import List, Optional


# ---------------------------------------------------------------------------
# Step 2.1 — HSQLDB Java-style \uXXXX escape decoder
# ---------------------------------------------------------------------------

_HSQLDB_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def decode_hsqldb_escapes(s: Optional[str]) -> str:
    """Decode Java-style ``\\uXXXX`` escapes into their Unicode characters.

    The HSQLDB 1.7.2 log-file format used in the TÜRKPATENT CD bundles
    encodes every non-ASCII character as a six-character escape
    ``\\u0130`` (= ``İ``), ``\\u015f`` (= ``ş``), and so on. Newlines
    inside abstracts appear as ``\\u000a``.

    Returns the empty string for ``None`` so the caller can pipe values
    straight into a Postgres NOT NULL TEXT column without extra guards.
    """
    if not s:
        return ""

    def _replace(match: "re.Match[str]") -> str:
        return chr(int(match.group(1), 16))

    return _HSQLDB_ESCAPE_RE.sub(_replace, s)


# ---------------------------------------------------------------------------
# Step 2.2 — IPCCODE HTML wrapper stripper
# ---------------------------------------------------------------------------

_HTML_P_RE = re.compile(r"<p>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_HTML_OUTER_RE = re.compile(r"</?html\s*/?>", re.IGNORECASE)


def strip_ipc_html(value: Optional[str]) -> List[str]:
    """Convert an HSQLDB IPCCODE HTML-wrapped string into a list of codes.

    The CD bundle stores IPC classifications as
    ``<html><p>A61M 5/31</p><p>A61J 1/14</p></html>``. This helper
    returns ``["A61M 5/31", "A61J 1/14"]`` — preserving the inner string
    verbatim. Codes legitimately appear with or without internal
    whitespace (``E04C 3/34`` and ``G01H13/00`` are both real), so the
    helper does not normalise spacing.

    Behaviour:
      - empty / None input -> ``[]``
      - well-formed ``<html><p>...</p>...</html>`` -> list of inner texts
      - plain code with no HTML at all (defensive) -> single-element list
      - malformed (HTML tags but no <p>) -> ``[]``
    """
    if not value:
        return []

    matches = _HTML_P_RE.findall(value)
    if matches:
        return [m.strip() for m in matches if m.strip()]

    # No <p> tags. If the input contains <html>/<…> tags but no <p>,
    # treat as malformed and return [] rather than passing HTML garbage
    # through as a code.
    if "<" in value and ">" in value:
        return []

    cleaned = value.strip()
    return [cleaned] if cleaned else []

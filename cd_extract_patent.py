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
from typing import Optional


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

"""Tasarım (industrial design) — CD bundle extractor.

Sister module to ``cd_extract_patent`` (HSQLDB CD bundles for the
Patent / Faydalı Model registry). Targets the legacy Tasarım CDs
stored at::

    bulletins/Tasarim/{N}_CD.rar
    bulletins/Tasarim/{N} say_l_ resmi endüstriyel tasar_m bülteni cd içeri_i.rar

Each CD is a 7-Zip-extractable RAR carrying:

  - ``{N}/idbulletin.script``    — HSQLDB DDL header
  - ``{N}/idbulletin.log``       — HSQLDB row inserts (the actual data)
  - ``{N}/idbulletin.inf``       — bulletin number + date (DD.MM.YYYY)
  - ``{N}/idbulletin.properties``
  - ``{N}/images/{year}_{appno}/{design_no}_{view_no}.jpg``

The verbose-named variant carries the same files under ``setup/``
instead of ``{N}/`` and stores ``images/`` at the archive root. The
extractor resolves the CD root by locating ``idbulletin.log``, not
by assuming a single top-level folder.

Tables (per ``idbulletin.script``):

  - ``IDDOSSIER``    — design application (attorney denormalized inline)
  - ``IDHOLDER``     — applicants (carries TPECLIENT ``CLIENTNO`` -> shared holders FK)
  - ``IDDESIGN``     — per-design rows under one application (1:N with views)
  - ``IDDESIGNER``   — designers (party rows)
  - ``IDANNOTATION`` — free-text annotation rows (event-like)

Output is a per-issue JSON document written to::

    bulletins/Tasarim/TS_{bulletin_no}_{bulletin_date}/cd_metadata.json

A future stage 3 will reconcile this with the parallel PDF metadata
where both exist.

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

    Identical wire format to the Patent CD bundles. The HSQLDB 1.7.2
    log-file format used in the Tasarim CDs encodes every non-ASCII
    character as a six-character escape: ``\\u0130`` (= ``İ``),
    ``\\u015f`` (= ``ş``), ``\\u00fc`` (= ``ü``), and so on. Tabs and
    newlines inside DDL or annotation fields appear as ``\\u0009`` and
    ``\\u000a``.

    Returns the empty string for ``None`` so callers can pipe values
    straight into a Postgres NOT NULL TEXT column without extra guards.
    """
    if not s:
        return ""

    def _replace(match: "re.Match[str]") -> str:
        return chr(int(match.group(1), 16))

    return _HSQLDB_ESCAPE_RE.sub(_replace, s)


# ---------------------------------------------------------------------------
# Step 2.2 — LOCARNOCODES splitter
# ---------------------------------------------------------------------------


def split_locarno_codes(value: Optional[str]) -> List[str]:
    """Split a comma-separated ``IDDOSSIER.LOCARNOCODES`` value into codes.

    The Tasarım CD stores Locarno classifications as a single
    ``VARCHAR(255)`` packed with comma-separated ``NN-NN`` codes:

      - ``"25-02"``                (single code)
      - ``"12-16,12-05"``          (no space)
      - ``"07-01, 32-00"``         (comma + space — real-data edge case)
      - ``"06-04,06-02,06-05"``    (three codes)

    Empirically across 240_CD.rar's 365 IDDOSSIER rows, comma is the
    only separator and codes are uniformly ``NN-NN``. The helper does
    not normalise the code shape — ``26-05`` and ``06.01`` (the dotted
    legacy variant ``pdf_extract_tasarim`` recognises) both pass
    through verbatim.

    Behaviour:
      - empty / None input -> ``[]``
      - whitespace-only input -> ``[]``
      - leading / trailing / inter-code whitespace stripped per code
      - empty entries (e.g. trailing comma) filtered out
    """
    if not value:
        return []
    return [code for part in value.split(",") if (code := part.strip())]

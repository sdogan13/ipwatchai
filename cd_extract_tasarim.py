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
from typing import Any, Dict, List, Optional


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


# ---------------------------------------------------------------------------
# Step 2.3 — INSERT-line parser
# ---------------------------------------------------------------------------

# DDL column order per CREATE TABLE in the .script header. The parser
# trusts these orders when zipping VALUES(...) to a row dict.
TABLE_COLUMNS: Dict[str, List[str]] = {
    "IDDOSSIER":    ["APPLICATIONNO", "APPLICATIONDATE", "REGISTERNO", "REGISTERDATE",
                     "DESIGNCOUNT", "LOCARNOCODES", "ATTORNEYNO", "ATTORNEYNAME",
                     "ATTORNEYTITLE", "ATTORNEYADDRESS", "TYPE"],
    "IDHOLDER":     ["APPLICATIONNO", "CLIENTNO", "TITLE", "ADDRESS", "CITY", "COUNTRY"],
    "IDDESIGN":     ["APPLICATIONNO", "NO", "PRODUCTNAME"],
    "IDDESIGNER":   ["APPLICATIONNO", "NO", "NAME", "ADDRESS", "COUNTRY"],
    "IDANNOTATION": ["PUBLICATIONKEY", "APPLICATIONNO", "REQUESTTYPE", "CONTENT"],
}

_INSERT_RE = re.compile(r"^INSERT\s+INTO\s+(\w+)\s+VALUES\s*\((.*)\)\s*$", re.IGNORECASE | re.DOTALL)


def _parse_sql_values(values_str: str) -> List[str]:
    """Parse a comma-separated list of single-quoted SQL string literals.

    Implements only the syntax the HSQLDB 1.7.2 log writer actually
    emits in these CD bundles:

      - every value is single-quoted: ``'text'``
      - an empty string is ``''``
      - an embedded apostrophe is doubled: ``'TÜRKİYE''NİN'`` -> ``TÜRKİYE'NİN``
      - the literal ``NULL`` keyword does not appear in any captured CD log

    Raises ``ValueError`` on any unexpected character (we want loud
    failure, not silent data loss).
    """
    out: List[str] = []
    i = 0
    n = len(values_str)

    while i < n:
        # Skip whitespace before next value
        while i < n and values_str[i] in " \t":
            i += 1
        if i >= n:
            break

        if values_str[i] != "'":
            raise ValueError(
                f"expected single quote at position {i}, "
                f"got {values_str[i:i+10]!r}"
            )
        i += 1  # consume opening quote

        chars: List[str] = []
        while i < n:
            ch = values_str[i]
            if ch == "'":
                # SQL-escape: doubled apostrophe == literal '
                if i + 1 < n and values_str[i + 1] == "'":
                    chars.append("'")
                    i += 2
                    continue
                # Unescaped quote = end of string
                i += 1
                break
            chars.append(ch)
            i += 1
        else:
            raise ValueError(f"unterminated string starting near {values_str[max(0,i-30):i]!r}")

        out.append("".join(chars))

        # Expect comma or end-of-string
        while i < n and values_str[i] in " \t":
            i += 1
        if i >= n:
            break
        if values_str[i] != ",":
            raise ValueError(
                f"expected comma at position {i}, "
                f"got {values_str[i:i+10]!r}"
            )
        i += 1  # consume comma

    return out


def parse_hsqldb_log_line(line: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse one INSERT line of an HSQLDB 1.7.2 ``idbulletin.log`` file.

    Returns ``{"table": "IDDOSSIER", "row": {...column->decoded value...}}``
    for recognised INSERT statements (one of the five Tasarım-CD tables).

    Returns ``None`` for:
      - blank / whitespace-only lines
      - non-INSERT lines (``CREATE TABLE``, ``/*C1*/CONNECT USER SA``,
        ``DISCONNECT``, ``SET ...`` — all real shapes seen in
        ``240/idbulletin.log``)
      - INSERT statements targeting a table not in ``TABLE_COLUMNS``

    Raises ``ValueError`` when an INSERT *is* recognised but the value
    count doesn't match the expected column count — that's a real schema
    drift signal and should fail loudly rather than be papered over.

    Per-column transforms are applied automatically:
      - ``LOCARNOCODES`` is comma-split to a list (see ``split_locarno_codes``)
      - all other columns get ``\\uXXXX`` escapes decoded
    """
    if not line:
        return None
    line = line.rstrip("\r\n")
    if not line.startswith("INSERT INTO ") and not line.startswith("INSERT into "):
        return None

    m = _INSERT_RE.match(line)
    if not m:
        return None

    table = m.group(1).upper()
    columns = TABLE_COLUMNS.get(table)
    if columns is None:
        return None

    raw_values = _parse_sql_values(m.group(2))
    if len(raw_values) != len(columns):
        raise ValueError(
            f"{table}: expected {len(columns)} columns, got {len(raw_values)} "
            f"in line {line[:100]!r}..."
        )

    row: Dict[str, Any] = {}
    for col, raw in zip(columns, raw_values):
        if col == "LOCARNOCODES":
            row[col] = split_locarno_codes(decode_hsqldb_escapes(raw))
        else:
            row[col] = decode_hsqldb_escapes(raw)

    return {"table": table, "row": row}

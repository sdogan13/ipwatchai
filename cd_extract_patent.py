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
from pathlib import Path
from typing import Any, Dict, List, Optional


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


# ---------------------------------------------------------------------------
# Step 2.3 — INSERT-line parser
# ---------------------------------------------------------------------------

# DDL column order per CREATE TABLE in the .script header. The parser
# trusts these orders when zipping VALUES(...) to a row dict.
TABLE_COLUMNS: Dict[str, List[str]] = {
    "PATENT": [
        "APPLICATIONNO", "APPLICATIONDATE", "PATENTNO", "PATENTDATE",
        "IPCCODE", "PUBLICATIONNO", "PUBLICATIONTYPE", "PUBLICATIONDATE",
        "PATENTTITLE", "PATENTTYPE", "PATENTABSTRACT",
        "IMAGEPATH1", "IMAGEPATH2",
    ],
    "HOLDER":   ["APPLICATIONNO", "TITLE", "ADDRESS", "STATE", "POSTALCODE", "CITY", "COUNTRYNO"],
    "INVENTER": ["APPLICATIONNO", "TITLE", "ADDRESS", "STATE", "POSTALCODE", "CITY", "COUNTRYNO"],
    "ATTORNEY": ["APPLICATIONNO", "NO", "NAME", "ADDRESS", "TITLE"],
    "PRIORITY": ["APPLICATIONNO", "PRIORITYNO", "PRIORITYDATE", "COUNTRYNO"],
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
    """Parse one INSERT line of an HSQLDB 1.7.2 ``ptbulletin.log`` file.

    Returns ``{"table": "PATENT", "row": {...column->decoded value...}}``
    for recognised INSERT statements (one of the five patent-CD tables).

    Returns ``None`` for:
      - blank / whitespace-only lines
      - non-INSERT lines (``CREATE TABLE``, ``CONNECT USER SA``, comments,
        ``DISCONNECT``)
      - INSERT statements targeting a table not in ``TABLE_COLUMNS``

    Raises ``ValueError`` when an INSERT *is* recognised but the value
    count doesn't match the expected column count — that's a real schema
    drift signal and should fail loudly rather than be papered over.

    Per-column transforms are applied automatically:
      - ``IPCCODE`` is HTML-stripped to a list (see ``strip_ipc_html``)
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
        if col == "IPCCODE":
            row[col] = strip_ipc_html(decode_hsqldb_escapes(raw))
        else:
            row[col] = decode_hsqldb_escapes(raw)

    return {"table": table, "row": row}


# ---------------------------------------------------------------------------
# Step 2.4 — full-file parser
# ---------------------------------------------------------------------------

def parse_hsqldb_log(log_path: str | Path) -> Dict[str, List[Dict[str, Any]]]:
    """Parse a complete HSQLDB ``ptbulletin.log`` file into per-table rows.

    Iterates lines through ``parse_hsqldb_log_line`` and groups recognised
    inserts by uppercase table name. Tables with zero parsed rows are
    omitted from the returned dict. Non-INSERT lines (CREATE TABLE,
    CONNECT, DISCONNECT, comments) are silently skipped.

    Returns ``{"PATENT": [row, ...], "HOLDER": [...], ...}``.

    Re-raises ``ValueError`` from the line parser, but with the offending
    line number prefixed so a malformed line is easy to locate in a
    15,000-line file. Reads the file with UTF-8 encoding (HSQLDB writes
    ASCII + ``\\uXXXX`` escapes, so this is safe).
    """
    path = Path(log_path)
    by_table: Dict[str, List[Dict[str, Any]]] = {}

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            try:
                result = parse_hsqldb_log_line(line)
            except ValueError as e:
                raise ValueError(f"{path.name} line {line_no}: {e}") from None
            if result is None:
                continue
            by_table.setdefault(result["table"], []).append(result["row"])

    return by_table

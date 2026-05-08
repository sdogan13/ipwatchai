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

import os
import re
import subprocess
from datetime import date, datetime, timezone
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


# ---------------------------------------------------------------------------
# Step 2.5 — image-path resolver
# ---------------------------------------------------------------------------

# Padding widths to try, in order. Empirically: 2017–2020 ship bare
# numbers, 2021+ ship 6-digit zero-padded names. The 5- and 7-pad
# entries are belt-and-braces against transitional bundles.
_IMAGE_PAD_WIDTHS: List[int] = [6, 5, 7]
_IMAGE_EXTENSIONS: List[str] = [".tif", ".tiff"]


def _split_application_no(application_no: Optional[str]) -> Optional[tuple[str, str]]:
    """Split ``2017/15048`` into ``("2017", "15048")``.

    Returns ``None`` for any malformed shape (missing slash, empty parts,
    extra slashes, non-numeric components).
    """
    if not application_no:
        return None
    parts = application_no.strip().split("/")
    if len(parts) != 2:
        return None
    year, appno = parts[0].strip(), parts[1].strip()
    if not year or not appno:
        return None
    if not year.isdigit() or not appno.isdigit():
        return None
    return year, appno


def resolve_image_path(
    application_no: Optional[str],
    images_root: str | Path,
) -> Optional[Path]:
    """Find the figure file for a patent application inside an extracted CD.

    The CD layout under ``data/`` is::

        images/{year}/{appno}.tif

    Naming convention varies by year: 2017–2020 use bare numbers
    (``15048.tif``); 2021+ are zero-padded to 6 digits (``000039.tif``).

    Resolution order (first match wins):
      1. exact ``{year}/{appno}.tif``
      2. ``{year}/{appno:0>6}.tif`` — modern 6-pad
      3. ``{year}/{appno:0>5}.tif`` — legacy 5-pad
      4. ``{year}/{appno:0>7}.tif`` — defensive 7-pad
      5. each of the above with ``.tiff`` extension

    Returns the resolved ``Path`` or ``None``. Empty / malformed
    ``APPLICATIONNO`` and missing root directories return ``None``
    rather than raising — figure presence is best-effort.

    ``images_root`` is the folder containing the ``{year}/`` subfolders
    (i.e. ``…/data/images/`` for an extracted CD).
    """
    parsed = _split_application_no(application_no)
    if parsed is None:
        return None
    year, appno = parsed

    root = Path(images_root)
    year_dir = root / year
    if not year_dir.is_dir():
        return None

    candidates: List[str] = []
    seen: set = set()

    def _add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            candidates.append(name)

    _add(appno)
    for width in _IMAGE_PAD_WIDTHS:
        _add(appno.zfill(width))

    for stem in candidates:
        for ext in _IMAGE_EXTENSIONS:
            candidate = year_dir / f"{stem}{ext}"
            if candidate.is_file():
                return candidate

    return None


# ---------------------------------------------------------------------------
# Step 2.6 — 7-Zip archive extractor
# ---------------------------------------------------------------------------

# Default 7-Zip executable on Windows. Override via env var
# ``PIPELINE_SEVEN_ZIP_PATH`` (matches the README's variable name).
DEFAULT_SEVEN_ZIP = "C:/Program Files/7-Zip/7z.exe"


def _resolve_seven_zip(override: Optional[str | Path] = None) -> Path:
    """Locate the 7-Zip executable, in priority:

      1. explicit ``override`` argument
      2. ``PIPELINE_SEVEN_ZIP_PATH`` environment variable
      3. the platform default (``C:/Program Files/7-Zip/7z.exe`` on Windows)
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("PIPELINE_SEVEN_ZIP_PATH")
    if env:
        return Path(env)
    return Path(DEFAULT_SEVEN_ZIP)


def extract_cd_archive(
    rar_path: str | Path,
    scratch_dir: str | Path,
    *,
    seven_zip: Optional[str | Path] = None,
    timeout: Optional[int] = 600,
) -> Path:
    """Extract a Patent CD ``.rar`` archive into ``scratch_dir``.

    Skips ``data/java/`` (~80% of the archive — the bundled JRE is not
    needed for ingestion). Returns the path to the extracted CD root,
    which is the single top-level folder inside the archive (e.g.
    ``scratch_dir/2025_12``).

    Raises:
      - ``FileNotFoundError`` if ``rar_path`` is missing.
      - ``FileNotFoundError`` if 7-Zip itself is not installed at the
        resolved path.
      - ``RuntimeError`` if 7-Zip exits with a fatal status (non-zero
        and non-warning) or if no top-level folder is found after
        extraction.

    7-Zip exit codes (per the official spec):
      - 0 = ok
      - 1 = warnings (non-fatal — usually file-locked or skipped items)
      - 2 = fatal error
      - 7 = command line error
      - 8 = not enough memory
      - 255 = user stopped
    Only 0 and 1 are accepted.
    """
    rar = Path(rar_path)
    if not rar.is_file():
        raise FileNotFoundError(f"archive not found: {rar}")

    seven = _resolve_seven_zip(seven_zip)
    if not seven.is_file():
        raise FileNotFoundError(f"7-Zip not found at {seven}")

    scratch = Path(scratch_dir)
    scratch.mkdir(parents=True, exist_ok=True)

    # The archive's top-level folder name varies (2025_12, 2024_07, …),
    # so we exclude with a leading wildcard. Both `*/data/java` (the
    # directory itself) and `*/data/java/*` (its contents) are needed.
    cmd = [
        str(seven), "x",
        str(rar),
        f"-o{scratch}",
        "-x!*/data/java",
        "-x!*/data/java/*",
        "-y",   # assume Yes for any 7-Zip prompts
        "-bso0",  # silence stdout
        "-bsp0",  # silence progress
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"7-Zip exited {result.returncode} extracting {rar.name}: "
            f"{(result.stderr or result.stdout).strip()[:500]}"
        )

    # Identify the extracted CD root. A patent CD has exactly one
    # top-level folder containing data/, autorun.inf, etc.
    candidates = [p for p in scratch.iterdir() if p.is_dir()]
    if not candidates:
        raise RuntimeError(f"no folders found in {scratch} after extracting {rar.name}")

    if len(candidates) == 1:
        return candidates[0]

    # Multiple top-level folders — pick the one that looks like a CD root
    # (contains data/ptbulletin.log).
    for c in candidates:
        if (c / "data" / "ptbulletin.log").is_file():
            return c

    raise RuntimeError(
        f"could not identify CD root in {scratch}: "
        f"{[c.name for c in candidates]}"
    )


# ---------------------------------------------------------------------------
# Step 2.7 — bulletin.inf header + cd_to_metadata orchestrator
# ---------------------------------------------------------------------------

_INF_LINE_RE = re.compile(r"^([A-Z]+)\s*=\s*(.*)$")


def _parse_dmy_to_iso(value: str) -> Optional[str]:
    """Convert ``22/12/2025`` to ``2025-12-22`` (or ``None`` if unparseable)."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def parse_bulletin_inf(inf_path: str | Path) -> Dict[str, Optional[str]]:
    """Parse the small ``data/bulletin.inf`` header file.

    Format::

        NO=2025/12
        DATE=22/12/2025

    Returns ``{"bulletin_no": "2025/12", "bulletin_date": "2025-12-22"}``.
    Missing fields and malformed lines surface as ``None`` values rather
    than exceptions — the caller is the right place to decide whether
    those gaps are acceptable.
    """
    out: Dict[str, Optional[str]] = {"bulletin_no": None, "bulletin_date": None}
    path = Path(inf_path)
    if not path.is_file():
        return out

    for raw in path.read_text(encoding="utf-8").splitlines():
        m = _INF_LINE_RE.match(raw.strip())
        if not m:
            continue
        key, value = m.group(1).upper(), m.group(2).strip()
        if key == "NO":
            out["bulletin_no"] = value or None
        elif key == "DATE":
            out["bulletin_date"] = _parse_dmy_to_iso(value)

    return out


# Per-table key remapping for the JSON output. Keeps DB column names
# uppercase inside parsed rows but presents party / priority lists as
# nested objects with friendlier snake_case keys.
_HOLDER_KEYS = {
    "TITLE": "title", "ADDRESS": "address", "STATE": "state",
    "POSTALCODE": "postal_code", "CITY": "city", "COUNTRYNO": "country",
}
_INVENTER_KEYS = _HOLDER_KEYS  # same shape
_ATTORNEY_KEYS = {
    "NO": "no", "NAME": "name", "ADDRESS": "address", "TITLE": "firm",
}
_PRIORITY_KEYS = {
    "PRIORITYNO": "priority_no", "PRIORITYDATE": "priority_date",
    "COUNTRYNO": "country",
}


def _project(row: Dict[str, Any], key_map: Dict[str, str]) -> Dict[str, Any]:
    """Pick + rename keys from a parsed row, dropping APPLICATIONNO."""
    return {new: row.get(old, "") for old, new in key_map.items()}


def _group_by_application_no(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("APPLICATIONNO", ""), []).append(row)
    return grouped


def cd_to_metadata(
    rar_path: str | Path,
    scratch_dir: str | Path,
    *,
    seven_zip: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Extract a Patent CD ``.rar`` and produce a fully joined JSON-ready dict.

    Pipeline:
      1. ``extract_cd_archive`` (drops ``data/java/``)
      2. ``parse_bulletin_inf`` for issue header
      3. ``parse_hsqldb_log`` over ``data/ptbulletin.log``
      4. group HOLDER / INVENTER / ATTORNEY / PRIORITY rows by APPLICATIONNO
      5. join + resolve figure path per patent via ``resolve_image_path``
      6. emit a single document

    The returned dict has this shape (snake_case)::

        {
          "bulletin_no":   "2025/12",
          "bulletin_date": "2025-12-22",
          "source_archive": "2025_12_CD.rar",
          "extracted_at":   "2026-05-08T12:34:56+00:00",
          "stats": { "patents": 2718, "holders": 3422, ..., "figures_resolved": 653 },
          "patents": [
            {
              "application_no": "2017/15048",
              "application_date": "05/10/2017",
              "patent_no": "", "patent_date": "",
              "ipc_codes": ["A61M 5/31", ...],
              "publication_no": "TR 2017 15048 U3",
              "publication_type": "73",
              "publication_date": "22/12/2025",
              "title": "EMNİYET BELİRTEÇLİ …",
              "patent_type": "2",
              "abstract": "Başvuru konusu …",
              "image_path": "data/images/2017/15048.tif",   # or null
              "holders":    [...], "inventors": [...],
              "attorneys":  [...], "priorities": [...],
            }, …
          ],
        }

    The CD's extracted folder is left in place under ``scratch_dir`` —
    the caller is responsible for cleanup, since downstream stages
    (figure embedding) typically still need the TIFFs on disk.
    """
    rar = Path(rar_path)
    cd_root = extract_cd_archive(rar, scratch_dir, seven_zip=seven_zip)

    inf = parse_bulletin_inf(cd_root / "data" / "bulletin.inf")
    log = parse_hsqldb_log(cd_root / "data" / "ptbulletin.log")
    images_root = cd_root / "data" / "images"

    holders_by_app   = _group_by_application_no(log.get("HOLDER", []))
    inventors_by_app = _group_by_application_no(log.get("INVENTER", []))
    attorneys_by_app = _group_by_application_no(log.get("ATTORNEY", []))
    priorities_by_app = _group_by_application_no(log.get("PRIORITY", []))

    figures_resolved = 0
    patents: List[Dict[str, Any]] = []

    for row in log.get("PATENT", []):
        app_no = row.get("APPLICATIONNO", "")
        image = resolve_image_path(app_no, images_root)
        if image is not None:
            figures_resolved += 1
            try:
                rel = image.relative_to(cd_root).as_posix()
            except ValueError:
                rel = str(image)
        else:
            rel = None

        patents.append({
            "application_no":    app_no,
            "application_date":  row.get("APPLICATIONDATE", ""),
            "patent_no":         row.get("PATENTNO", ""),
            "patent_date":       row.get("PATENTDATE", ""),
            "ipc_codes":         row.get("IPCCODE", []),
            "publication_no":    row.get("PUBLICATIONNO", ""),
            "publication_type":  row.get("PUBLICATIONTYPE", ""),
            "publication_date":  row.get("PUBLICATIONDATE", ""),
            "title":             row.get("PATENTTITLE", ""),
            "patent_type":       row.get("PATENTTYPE", ""),
            "abstract":          row.get("PATENTABSTRACT", ""),
            "image_path":        rel,
            "holders":    [_project(r, _HOLDER_KEYS)   for r in holders_by_app.get(app_no, [])],
            "inventors":  [_project(r, _INVENTER_KEYS) for r in inventors_by_app.get(app_no, [])],
            "attorneys":  [_project(r, _ATTORNEY_KEYS) for r in attorneys_by_app.get(app_no, [])],
            "priorities": [_project(r, _PRIORITY_KEYS) for r in priorities_by_app.get(app_no, [])],
        })

    return {
        "bulletin_no":   inf.get("bulletin_no"),
        "bulletin_date": inf.get("bulletin_date"),
        "source_archive": rar.name,
        "extracted_at":  datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": {
            "patents":           len(log.get("PATENT", [])),
            "holders":           len(log.get("HOLDER", [])),
            "inventors":         len(log.get("INVENTER", [])),
            "attorneys":         len(log.get("ATTORNEY", [])),
            "priorities":        len(log.get("PRIORITY", [])),
            "figures_resolved":  figures_resolved,
            "figures_missing":   len(log.get("PATENT", [])) - figures_resolved,
        },
        "patents": patents,
    }

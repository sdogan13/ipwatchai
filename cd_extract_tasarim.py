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

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Step 2.4 — full-file parser
# ---------------------------------------------------------------------------


def parse_hsqldb_log(log_path: str | Path) -> Dict[str, List[Dict[str, Any]]]:
    """Parse a complete HSQLDB ``idbulletin.log`` file into per-table rows.

    Iterates lines through ``parse_hsqldb_log_line`` and groups recognised
    inserts by uppercase table name. Tables with zero parsed rows are
    omitted from the returned dict. Non-INSERT lines (the embedded
    ``CREATE TABLE`` block, ``/*C1*/CONNECT USER SA``, ``DISCONNECT``)
    are silently skipped.

    Returns ``{"IDDOSSIER": [row, ...], "IDHOLDER": [...], ...}``.

    Re-raises ``ValueError`` from the line parser, but with the offending
    file name + line number prefixed so a malformed line is easy to
    locate in a multi-thousand-line log. Reads the file with UTF-8
    encoding (HSQLDB writes ASCII + ``\\uXXXX`` escapes, so this is safe).
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
# Step 2.5 — per-design image resolver
# ---------------------------------------------------------------------------

# Per-application image folder name: "2016/01059" -> "2016_01059".
# Per-image filename: "{design_no}_{view_no}.jpg" (or .jpeg defensively).
_VIEW_FILENAME_RE = re.compile(r"^(\d+)_(\d+)\.jpe?g$", re.IGNORECASE)


def _application_image_folder(application_no: Optional[str]) -> Optional[str]:
    """Convert ``"2016/01059"`` -> ``"2016_01059"``; return ``None`` for
    any malformed shape (missing slash, empty parts, extra slashes,
    non-numeric components).
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
    return f"{year}_{appno}"


def resolve_design_images(
    application_no: Optional[str],
    images_root: str | Path,
) -> List[Dict[str, Any]]:
    """Find every per-view image file for a Tasarım design application.

    Tasarım CD layout under the extracted CD root::

        images/{year}_{appno}/{design_no}_{view_no}.jpg

    A single application can carry multiple designs (``IDDESIGN.NO``)
    and each design can have multiple views, so the resolver returns
    a flat list rather than a single path::

        [
          {"design_no": "1", "view_no": "1", "image_path": Path(".../1_1.jpg")},
          {"design_no": "1", "view_no": "2", "image_path": Path(".../1_2.jpg")},
          {"design_no": "2", "view_no": "1", "image_path": Path(".../2_1.jpg")},
          ...
        ]

    Sorted by ``(int(design_no), int(view_no))`` so design 9 view 1 comes
    before design 10 view 1 (lexicographic sort would break this).

    Returns ``[]`` for any of:
      - empty / malformed ``application_no``
      - missing ``images_root`` directory
      - missing per-application folder
      - present folder containing zero files matching the
        ``{design}_{view}.jpg`` shape

    Files in the folder that don't match the expected shape are
    silently skipped — robust to stray ``Thumbs.db`` / ``.DS_Store``.

    ``images_root`` is the folder containing the ``{year}_{appno}/``
    subfolders (i.e. ``…/{N}/images/`` for a modern CD root, or
    ``…/images/`` for the verbose-named variant).
    """
    folder_name = _application_image_folder(application_no)
    if folder_name is None:
        return []

    folder = Path(images_root) / folder_name
    if not folder.is_dir():
        return []

    out: List[Dict[str, Any]] = []
    for entry in folder.iterdir():
        if not entry.is_file():
            continue
        m = _VIEW_FILENAME_RE.match(entry.name)
        if m is None:
            continue
        out.append({
            "design_no": m.group(1),
            "view_no": m.group(2),
            "image_path": entry,
        })

    out.sort(key=lambda r: (int(r["design_no"]), int(r["view_no"])))
    return out


# ---------------------------------------------------------------------------
# Step 2.6 — 7-Zip archive extractor + dynamic layout resolution
# ---------------------------------------------------------------------------

# Default 7-Zip executable on Windows. Override via env var
# ``PIPELINE_SEVEN_ZIP_PATH`` (matches the patent CD extractor's variable).
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


@dataclass(frozen=True)
class CDLayout:
    """Where the four interesting bits of a Tasarım CD landed after extraction.

    Attributes:
      cd_root:     directory containing ``idbulletin.log`` (and usually
                   ``idbulletin.{script,inf,properties}``).
      log_path:    full path to ``idbulletin.log``.
      images_root: directory containing ``{year}_{appno}/`` per-application
                   subfolders. May not exist if the CD ships without
                   any image folder; the resolver is tolerant of that.

    Why both ``cd_root`` and ``images_root``: the modern ``{N}_CD.rar``
    layout puts ``images/`` directly under ``cd_root``, but the verbose
    ``231 say_l_*.rar`` archive puts ``idbulletin.log`` under
    ``setup/`` while ``images/`` sits at the archive root. This struct
    captures both locations so the orchestrator can hand
    ``resolve_design_images`` the right path without re-deriving it.
    """

    cd_root: Path
    log_path: Path
    images_root: Path


def _locate_cd_layout(scratch_dir: str | Path) -> CDLayout:
    """Discover the CD layout under ``scratch_dir`` after a 7-Zip extract.

    Locates the single ``idbulletin.log`` file anywhere in the tree
    (modern layout: ``scratch/{N}/idbulletin.log``; verbose layout:
    ``scratch/setup/idbulletin.log``). The CD root is its parent.

    For ``images_root``, prefers ``cd_root/images`` if it exists; falls
    back to ``scratch_dir/images`` (the verbose layout); finally
    defaults to ``cd_root/images`` even if missing — the resolver
    handles a missing folder by returning ``[]``.

    Raises:
      RuntimeError: if no ``idbulletin.log`` is found.
      RuntimeError: if more than one ``idbulletin.log`` is found
                    (we wouldn't know which CD root to pick).
    """
    scratch = Path(scratch_dir)
    log_paths = sorted(scratch.rglob("idbulletin.log"))
    if not log_paths:
        raise RuntimeError(f"no idbulletin.log found under {scratch}")
    if len(log_paths) > 1:
        raise RuntimeError(
            f"multiple idbulletin.log files found under {scratch}: "
            f"{[str(p.relative_to(scratch)) for p in log_paths]}"
        )

    log_path = log_paths[0]
    cd_root = log_path.parent

    # images_root candidates, in preference order
    candidates = [cd_root / "images", scratch / "images"]
    images_root = next((c for c in candidates if c.is_dir()), candidates[0])

    return CDLayout(cd_root=cd_root, log_path=log_path, images_root=images_root)


def extract_cd_archive(
    rar_path: str | Path,
    scratch_dir: str | Path,
    *,
    seven_zip: Optional[str | Path] = None,
    timeout: Optional[int] = 600,
) -> CDLayout:
    """Extract a Tasarım CD ``.rar`` archive into ``scratch_dir``.

    Unlike the patent CD bundles, Tasarım archives don't ship a JRE,
    so there is no ``data/java/`` to exclude. Returns a ``CDLayout``
    describing where the log and images landed — the layout differs
    between the modern ``{N}_CD.rar`` archives and the verbose-named
    legacy variant (see ``_locate_cd_layout``).

    Raises:
      - ``FileNotFoundError`` if ``rar_path`` is missing.
      - ``FileNotFoundError`` if 7-Zip itself is not installed at the
        resolved path.
      - ``RuntimeError`` if 7-Zip exits with a fatal status (non-zero
        and non-warning) or if no ``idbulletin.log`` is found after
        extraction (or more than one).

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

    cmd = [
        str(seven), "x",
        str(rar),
        f"-o{scratch}",
        "-y",     # assume Yes for any 7-Zip prompts
        "-bso0",  # silence stdout
        "-bsp0",  # silence progress
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"7-Zip exited {result.returncode} extracting {rar.name}: "
            f"{(result.stderr or result.stdout).strip()[:500]}"
        )

    return _locate_cd_layout(scratch)

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

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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
# Step 2.5b — per-application image persistence (canonical key shape)
# ---------------------------------------------------------------------------


def _persist_cd_images_for_app(
    application_no: Optional[str],
    images_root: str | Path,
    dest_root: str | Path,
) -> List[Dict[str, str]]:
    """Copy every view JPEG for one application into the canonical
    ``cd_images/`` layout and return reference dicts for the orchestrator.

    Source files are at ``images_root/{year}_{appno}/{d}_{v}.{ext}``
    (whatever ``resolve_design_images`` finds). Destination layout
    mirrors the per-application folder shape — *without* any archive
    wrapper — so the resulting key is identical to what the PDF
    extractor will emit, letting a future stage-3 reconciler match
    PDF and CD images by a single string::

        dest_root/{year}_{appno}/{d}_{v}.{ext}

    Returns one dict per copied file::

        {"design_no": str, "view_no": str,
         "image_path": "{year}_{appno}/{d}_{v}.{ext}"}

    Returns ``[]`` for malformed ``application_no``, missing
    ``images_root``, or applications with zero matching images
    (e.g. Hague designs).

    File copies always overwrite — re-running the extractor with
    ``--force`` refreshes the persisted set.
    """
    images = resolve_design_images(application_no, images_root)
    if not images:
        return []

    folder_name = _application_image_folder(application_no)
    if folder_name is None:
        # Defensive — resolve_design_images already returned [] for this case.
        return []

    dest_folder = Path(dest_root) / folder_name
    dest_folder.mkdir(parents=True, exist_ok=True)

    out: List[Dict[str, str]] = []
    for img in images:
        src = img["image_path"]
        dst = dest_folder / src.name
        shutil.copyfile(src, dst)
        out.append({
            "design_no": img["design_no"],
            "view_no": img["view_no"],
            "image_path": f"{folder_name}/{src.name}",
        })
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

    Locates the canonical ``idbulletin.log`` anywhere in the tree:

      - modern ``{N}_CD.rar``:   ``scratch/{N}/idbulletin.log``
      - verbose ``231 say_l_*``: ``scratch/idbulletin.log`` (archive
        root; the bulletin files unpack with no wrapping folder)

    Both layouts ship a **duplicate** ``idbulletin.log`` one level
    deeper under ``setup/`` (modern: ``{N}/setup/idbulletin.log``;
    verbose: ``setup/idbulletin.log``). We pick the shallower path
    (fewest directory components from ``scratch``) as the canonical
    CD root and ignore deeper duplicates. If two logs sit at the
    same shallowest depth, that's a real ambiguity we refuse to guess.

    For ``images_root``, prefer ``cd_root/images`` if it exists; fall
    back to ``scratch/images`` (verbose layout); otherwise default
    to ``cd_root/images`` even if missing — the resolver tolerates a
    missing folder by returning ``[]``.

    Raises:
      RuntimeError: if no ``idbulletin.log`` is found.
      RuntimeError: if two or more ``idbulletin.log`` files are tied
                    for the shallowest path (we wouldn't know which
                    CD root to pick).
    """
    scratch = Path(scratch_dir)
    log_paths = list(scratch.rglob("idbulletin.log"))
    if not log_paths:
        raise RuntimeError(f"no idbulletin.log found under {scratch}")

    def depth(p: Path) -> int:
        return len(p.relative_to(scratch).parts)

    log_paths.sort(key=lambda p: (depth(p), str(p)))
    shallowest = depth(log_paths[0])
    tied = [p for p in log_paths if depth(p) == shallowest]
    if len(tied) > 1:
        raise RuntimeError(
            f"multiple idbulletin.log files at depth {shallowest} under "
            f"{scratch}: {[str(p.relative_to(scratch)) for p in tied]}"
        )

    log_path = log_paths[0]
    cd_root = log_path.parent

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


# ---------------------------------------------------------------------------
# Step 2.7 — bulletin.inf parser + cd_to_metadata orchestrator
# ---------------------------------------------------------------------------

_INF_LINE_RE = re.compile(r"^([A-Z]+)\s*=\s*(.*)$")


def _parse_dotted_dmy_to_iso(value: str) -> Optional[str]:
    """Convert ``"09.03.2016"`` to ``"2016-03-09"`` (or ``None`` if unparseable).

    Tasarım CDs use dot-separated DD.MM.YYYY in ``idbulletin.inf``,
    differing from the patent CDs' DD/MM/YYYY format.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def parse_bulletin_inf(inf_path: str | Path) -> Dict[str, Optional[str]]:
    """Parse the small ``idbulletin.inf`` header file.

    Format (real data from ``240_CD.rar``)::

        NO=240
        DATE=09.03.2016

    Returns ``{"bulletin_no": "240", "bulletin_date": "2016-03-09"}``.
    Missing fields, missing file, or malformed date all surface as
    ``None`` values rather than exceptions — the caller decides
    whether those gaps are acceptable.
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
            out["bulletin_date"] = _parse_dotted_dmy_to_iso(value)

    return out


# Per-table key remapping for the JSON output. Keeps DB column names
# uppercase inside parsed rows but presents party / annotation lists as
# nested objects with friendlier snake_case keys. Drops APPLICATIONNO
# from holders/designers (it's already on the parent dossier) but
# preserves it on annotations (where it points at a DIFFERENT
# application than the bulletin's own dossiers — annotations are
# events on existing registrations).
_HOLDER_KEYS = {
    "CLIENTNO": "client_no", "TITLE": "title", "ADDRESS": "address",
    "CITY": "city", "COUNTRY": "country",
}
_DESIGNER_KEYS = {
    "NO": "no", "NAME": "name", "ADDRESS": "address", "COUNTRY": "country",
}
_ANNOTATION_KEYS = {
    "PUBLICATIONKEY": "publication_key", "APPLICATIONNO": "application_no",
    "REQUESTTYPE": "request_type", "CONTENT": "content",
}


def _project(row: Dict[str, Any], key_map: Dict[str, str]) -> Dict[str, Any]:
    """Pick + rename a subset of keys from a parsed row."""
    return {new: row.get(old, "") for old, new in key_map.items()}


def _group_by_application_no(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group a list of HSQLDB-parsed rows by their ``APPLICATIONNO`` field."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("APPLICATIONNO", ""), []).append(row)
    return grouped


def _layout_to_metadata(
    layout: CDLayout,
    source_archive_name: str,
    cd_images_dest: str | Path,
) -> Dict[str, Any]:
    """Build the canonical metadata dict from a pre-extracted CD layout
    AND persist every resolved view JPEG into ``cd_images_dest``.

    Pure data-shaping over an already-extracted CD: parses the inf and
    log, joins parties + designs + images by ``APPLICATIONNO``, copies
    each design view from the scratch ``layout.images_root`` to
    ``cd_images_dest/{year}_{appno}/{d}_{v}.{ext}``, and assembles the
    final document. Factored out of ``cd_to_metadata`` so tests can
    exercise the full join without mocking 7-Zip.

    JSON ``image_path`` values use the canonical
    ``{year}_{appno}/{d}_{v}.{ext}`` key shape — same shape the PDF
    extractor will emit, so a future stage-3 reconciler can match
    PDF and CD images by a single string.
    """
    inf = parse_bulletin_inf(layout.cd_root / "idbulletin.inf")
    rows = parse_hsqldb_log(layout.log_path)

    holders_by_app   = _group_by_application_no(rows.get("IDHOLDER", []))
    designers_by_app = _group_by_application_no(rows.get("IDDESIGNER", []))
    designs_by_app   = _group_by_application_no(rows.get("IDDESIGN", []))

    images_resolved = 0
    designs_without_images = 0
    dossiers: List[Dict[str, Any]] = []

    for d in rows.get("IDDOSSIER", []):
        app_no = d.get("APPLICATIONNO", "")

        # Persist images for this application and bucket by design_no.
        # _persist_cd_images_for_app emits the canonical key shape and
        # handles the Hague (no-images) case by returning [].
        images_by_design: Dict[str, List[Dict[str, str]]] = {}
        for img in _persist_cd_images_for_app(app_no, layout.images_root, cd_images_dest):
            images_by_design.setdefault(img["design_no"], []).append({
                "view_no": img["view_no"],
                "image_path": img["image_path"],
            })
            images_resolved += 1

        emitted_designs: List[Dict[str, Any]] = []
        for des in designs_by_app.get(app_no, []):
            no = des.get("NO", "")
            views = images_by_design.get(no, [])
            if not views:
                designs_without_images += 1
            emitted_designs.append({
                "no": no,
                "product_name": des.get("PRODUCTNAME", ""),
                "views": views,
            })

        dossiers.append({
            "application_no":   app_no,
            "application_date": d.get("APPLICATIONDATE", ""),
            "register_no":      d.get("REGISTERNO", ""),
            "register_date":    d.get("REGISTERDATE", ""),
            "design_count":     d.get("DESIGNCOUNT", ""),
            "type":             d.get("TYPE", ""),
            "locarno_codes":    d.get("LOCARNOCODES", []),
            "attorney": {
                "no":      d.get("ATTORNEYNO", ""),
                "name":    d.get("ATTORNEYNAME", ""),
                "title":   d.get("ATTORNEYTITLE", ""),
                "address": d.get("ATTORNEYADDRESS", ""),
            },
            "holders":   [_project(r, _HOLDER_KEYS)   for r in holders_by_app.get(app_no, [])],
            "designers": [_project(r, _DESIGNER_KEYS) for r in designers_by_app.get(app_no, [])],
            "designs":   emitted_designs,
        })

    annotations = [_project(r, _ANNOTATION_KEYS) for r in rows.get("IDANNOTATION", [])]

    return {
        "bulletin_no":   inf["bulletin_no"],
        "bulletin_date": inf["bulletin_date"],
        "source_archive": source_archive_name,
        "extracted_at":  datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": {
            "dossiers":               len(rows.get("IDDOSSIER", [])),
            "designs":                len(rows.get("IDDESIGN", [])),
            "holders":                len(rows.get("IDHOLDER", [])),
            "designers":              len(rows.get("IDDESIGNER", [])),
            "annotations":            len(rows.get("IDANNOTATION", [])),
            "images_resolved":        images_resolved,
            "designs_without_images": designs_without_images,
        },
        "dossiers": dossiers,
        "annotations": annotations,
    }


def cd_to_metadata(
    rar_path: str | Path,
    scratch_dir: str | Path,
    cd_images_dest: str | Path,
    *,
    seven_zip: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Extract a Tasarım CD ``.rar``, persist its images, and produce
    a fully joined JSON-ready dict.

    Pipeline:
      1. ``extract_cd_archive`` (no Java exclude; dynamic layout)
      2. ``parse_bulletin_inf`` for issue header
      3. ``parse_hsqldb_log`` over ``idbulletin.log``
      4. group HOLDER / DESIGNER / DESIGN rows by APPLICATIONNO
      5. join + persist per-design view images via
         ``_persist_cd_images_for_app`` into ``cd_images_dest``
      6. emit IDANNOTATION as a sibling ``annotations`` array
      7. assemble the final document with canonical ``{year}_{appno}/{d}_{v}.{ext}``
         image_path keys

    The CD's extracted folder under ``scratch_dir`` becomes safe to
    delete after this call — every JPEG referenced by the returned
    document has been copied into ``cd_images_dest``.
    """
    rar = Path(rar_path)
    layout = extract_cd_archive(rar, scratch_dir, seven_zip=seven_zip)
    return _layout_to_metadata(layout, rar.name, cd_images_dest)


# ---------------------------------------------------------------------------
# Step 2.8 — CLI entrypoint
# ---------------------------------------------------------------------------

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_DEFAULT_BULLETINS_DIR = _LOCAL_PROJECT_ROOT / "bulletins" / "Tasarim"
_DEFAULT_SCRATCH_DIR = _LOCAL_PROJECT_ROOT / "_scratch_cd_tasarim"

CD_METADATA_FILENAME = "cd_metadata.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [TASARIM-CD] - %(levelname)s - %(message)s",
)
logger = logging.getLogger("turkpatent.tasarim_cd")


@dataclass
class CLIArgs:
    rar_paths: List[Path]
    out_root: Path
    scratch_dir: Path
    seven_zip: Optional[Path]
    keep_scratch: bool
    force: bool


def issue_folder_name(bulletin_no: str, bulletin_date: str) -> str:
    """Compute the canonical issue folder stem ``TS_{N}_{YYYY-MM-DD}``.

    Mirrors the naming the modern PDF collector (``data_collection_tasarim``)
    already uses, so a CD output drops alongside any existing PDF
    extraction for the same issue.
    """
    return f"TS_{bulletin_no}_{bulletin_date}"


def _find_existing_issue_folder(out_root: Path, bulletin_no: str) -> Optional[Path]:
    """Find a pre-existing ``TS_{bulletin_no}_*/`` folder under ``out_root``.

    The PDF collector and the CD extractor both compute their folder name
    from a ``(bulletin_no, bulletin_date)`` pair, but the date strings
    sometimes drift — e.g. an aborted ``data_collection_tasarim --full``
    walk stamped folders with the run date instead of the issue's
    publication date, leaving real PDFs in ``TS_241_2026-04-24/``
    when the CD's idbulletin.inf says they should be at
    ``TS_241_2016-03-24/``.

    Empirically (pair_survey 2026-05-09): 17 of 230 archives pair
    against an existing PDF folder whose date suffix disagrees with
    the CD's inf DATE. To avoid creating a second folder for the same
    bulletin, ``_process_one`` calls this helper first and reuses the
    existing folder when there's exactly one match — regardless of
    whose date string is "right".

    Returns:
      - ``Path`` of the matching folder when exactly one ``TS_{N}_*/``
        directory exists for ``bulletin_no``.
      - ``None`` when no existing folder matches (caller falls back to
        creating a fresh ``TS_{N}_{inf_DATE}/``).

    Raises ``RuntimeError`` when more than one ``TS_{N}_*/`` folder
    matches — that's a real ambiguity (e.g. both ``TS_240_2016-03-09``
    and ``TS_240_2026-04-24`` from the survey output) and we'd rather
    fail loud than guess.
    """
    if not bulletin_no:
        return None
    if not out_root.is_dir():
        return None
    matches = sorted(
        p for p in out_root.glob(f"TS_{bulletin_no}_*") if p.is_dir()
    )
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    raise RuntimeError(
        f"multiple TS_{bulletin_no}_* folders under {out_root}: "
        f"{[p.name for p in matches]}; resolve manually before re-running"
    )


def _all_cd_rars(bulletins_dir: Path) -> List[Path]:
    """Return sorted HSQLDB-shape Tasarım CD archives under ``bulletins_dir``.

    Two real naming patterns:

      - modern ``{N}_CD.rar``                    (case-insensitive ``_cd.rar`` suffix)
      - verbose ``{N} say_l_ ... cd içeri_i.rar`` (substring ``cd içeri``)

    Legacy PDF-only archives (``Tasar_m Bülteni N.rar``, ``N-N.rar``) are
    deliberately excluded — they are not HSQLDB CDs and cd_to_metadata
    cannot process them.
    """
    out: set[Path] = set()
    for p in bulletins_dir.glob("*.rar"):
        name = p.name.lower()
        if name.endswith("_cd.rar") or "cd içeri" in name:
            out.add(p)
    return sorted(out)


def parse_argv(argv: Optional[List[str]] = None) -> CLIArgs:
    """Parse CLI arguments for the Tasarım CD-extractor entrypoint."""
    parser = argparse.ArgumentParser(
        prog="cd_extract_tasarim",
        description="Extract a Tasarım (industrial design) CD bundle to JSON metadata.",
    )
    parser.add_argument(
        "--rar",
        action="append",
        type=Path,
        help="Path to a CD .rar archive. Repeat for multiple files.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process every HSQLDB-shape *_CD.rar / *cd içeri*.rar in --bulletins-dir.",
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
        help="Where to write per-issue TS_{N}_{date}/cd_metadata.json folders "
             "(default: --bulletins-dir).",
    )
    parser.add_argument(
        "--scratch-dir",
        type=Path,
        default=_DEFAULT_SCRATCH_DIR,
        help=f"Scratch folder for unrar output (default: {_DEFAULT_SCRATCH_DIR}; "
             f"per-CD subfolder cleaned up unless --keep-scratch).",
    )
    parser.add_argument(
        "--seven-zip",
        type=Path,
        default=None,
        help="Override path to 7z.exe.",
    )
    parser.add_argument(
        "--keep-scratch",
        action="store_true",
        help="Don't delete the per-CD scratch folder after extraction.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing cd_metadata.json (default: skip with warning).",
    )
    ns = parser.parse_args(argv)

    if ns.all and ns.rar:
        parser.error("--rar and --all are mutually exclusive")

    if ns.all:
        rar_paths = _all_cd_rars(ns.bulletins_dir)
        if not rar_paths:
            parser.error(
                f"--all matched no HSQLDB-shape *.rar files in {ns.bulletins_dir}"
            )
    elif ns.rar:
        rar_paths = list(ns.rar)
    else:
        parser.error("provide --rar (one or more) or --all")

    out_root = ns.out_dir if ns.out_dir is not None else ns.bulletins_dir

    return CLIArgs(
        rar_paths=rar_paths,
        out_root=out_root,
        scratch_dir=ns.scratch_dir,
        seven_zip=ns.seven_zip,
        keep_scratch=ns.keep_scratch,
        force=ns.force,
    )


def _process_one(
    rar: Path,
    out_root: Path,
    scratch_dir: Path,
    seven_zip: Optional[Path],
    keep_scratch: bool,
    force: bool,
) -> Dict[str, Any]:
    """Extract one CD .rar, persist its images and JSON sidecar to the
    canonical ``TS_{N}_{date}/`` folder, return a run-level summary dict.

    Returns ``{"rar": str, "out": str, "stats": dict|None, "skipped": bool}``.
    A skipped entry (existing file, no --force) sets ``skipped=True`` and
    omits ``stats`` (the orchestrator is not run).

    Order of operations:
      1. Extract the archive into scratch.
      2. Parse ``idbulletin.inf`` for bulletin_no + bulletin_date.
      3. Compute issue folder + out_path; honour --force/skip.
      4. Persist images into ``issue_folder/cd_images/`` and build the
         metadata document (single ``_layout_to_metadata`` call).
      5. Write ``cd_metadata.json`` next to ``cd_images/``.
      6. Wipe scratch unless --keep-scratch.
    """
    cd_scratch = scratch_dir / rar.stem
    if cd_scratch.exists():
        shutil.rmtree(cd_scratch, ignore_errors=True)
    cd_scratch.mkdir(parents=True, exist_ok=True)

    try:
        layout = extract_cd_archive(rar, cd_scratch, seven_zip=seven_zip)
        inf = parse_bulletin_inf(layout.cd_root / "idbulletin.inf")
        bulletin_no = inf["bulletin_no"]
        bulletin_date = inf["bulletin_date"]
        if not bulletin_no or not bulletin_date:
            raise RuntimeError(
                f"{rar.name}: cannot resolve TS_*_* folder — bulletin_no="
                f"{bulletin_no!r}, bulletin_date={bulletin_date!r}"
            )

        # Prefer an existing TS_{N}_*/ folder if there's exactly one — this
        # is what lets a CD output land alongside a PDF whose folder name
        # has a drifting date suffix (see _find_existing_issue_folder for
        # the empirical case). Multi-match raises; no match falls through
        # to a freshly-named folder using the inf DATE.
        existing = _find_existing_issue_folder(out_root, bulletin_no)
        if existing is not None:
            issue_folder = existing
        else:
            issue_folder = out_root / issue_folder_name(bulletin_no, bulletin_date)
        out_path = issue_folder / CD_METADATA_FILENAME

        if out_path.exists() and not force:
            logger.warning("[skip] %s already exists; pass --force to overwrite", out_path)
            return {"rar": rar.name, "out": str(out_path), "skipped": True}

        issue_folder.mkdir(parents=True, exist_ok=True)
        cd_images_dest = issue_folder / "cd_images"

        doc = _layout_to_metadata(layout, rar.name, cd_images_dest)
        out_path.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "rar": rar.name,
            "out": str(out_path),
            "stats": doc["stats"],
            "skipped": False,
        }
    finally:
        if not keep_scratch:
            shutil.rmtree(cd_scratch, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint. Returns 0 if no archive raised an exception, 1 otherwise.

    A "skipped" entry (existing cd_metadata.json + no --force) is **not**
    a failure — it's an idempotent no-op.
    """
    args = parse_argv(argv)
    args.out_root.mkdir(parents=True, exist_ok=True)
    args.scratch_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    succeeded: List[str] = []
    skipped: List[str] = []
    failed: List[tuple[str, str]] = []

    for rar in args.rar_paths:
        if not rar.is_file():
            logger.warning("[skip] %s: not found", rar)
            failed.append((rar.name, "not found"))
            continue

        logger.info("[*] %s", rar.name)
        try:
            result = _process_one(
                rar, args.out_root, args.scratch_dir,
                args.seven_zip, args.keep_scratch, args.force,
            )
        except Exception as e:
            failed.append((rar.name, repr(e)))
            logger.error("[!] %s: %r", rar.name, e)
            continue

        if result.get("skipped"):
            skipped.append(rar.name)
        else:
            succeeded.append(rar.name)
            s = result["stats"]
            logger.info(
                "[+] %s: %d dossiers, %d designs, %d images, wrote %s",
                rar.name, s["dossiers"], s["designs"], s["images_resolved"],
                result["out"],
            )

    duration = time.time() - started
    logger.info(
        "Done in %.1fs: %d succeeded, %d skipped, %d failed",
        duration, len(succeeded), len(skipped), len(failed),
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

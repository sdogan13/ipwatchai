"""Patent / Faydalı Model — CD ↔ PDF reconciler (Stage 4).

Reads the two per-bulletin JSON outputs produced by Stages 2 and 3:

  - ``{YYYY_M}_metadata.json``      from ``cd_extract_patent.py``
  - ``{YYYY_M}_pdf_metadata.json``  from ``pdf_extract_patent.py``

…and merges them by ``application_no`` into a single canonical
``{YYYY_M}_metadata.json`` (where ``YYYY_M`` is derived from
``bulletin_no``, NOT from the input filenames — see the
"CD filename offset" memory note: 2025_07_CD.rar carries bulletin 2025/8,
so filename-based pairing is wrong).

Precedence rules (canonical shape lives in ``CanonicalRecord``):

  - **CD wins** on structured fields (dates, parties, IPC, kind code, …)
    because the HSQLDB rows are typed.
  - **PDF wins** on ``abstract`` (CD truncates at VARCHAR(2000)) and the
    ``title`` if PDF's title is strictly longer (CD sometimes truncates).
  - **Figures** are unioned: CD TIFFs primary, PDF JPEGs supplemental.
  - ``record_type`` / ``kind_code`` come from PDF when available, else
    derived from CD's publication number via ``classify_kind_code``.

Output is pure JSON-on-disk. No DB connection — Stage 5 ingests it.

Built incrementally. Each helper has its own unit-test block.

CLI (lands in step 4.7)::

    python -m pipeline.reconcile_patent --cd-json X.json --pdf-json Y.json
    python -m pipeline.reconcile_patent --all
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Reuse Stage 3's kind-code helpers — same publication_no shape on both
# sources. Module-level import is safe: pdf_extract_patent's PyMuPDF
# dependency is lazy (loaded only when parse_pdf runs), so importing
# here doesn't drag in fitz.
from pdf_extract_patent import classify_kind_code, extract_kind_code  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [PATENT-RECONCILE] - %(levelname)s - %(message)s",
)
logger = logging.getLogger("turkpatent.patent_reconcile")


# ---------------------------------------------------------------------------
# Step 4.1 — JSON loaders + CanonicalRecord dataclass
# ---------------------------------------------------------------------------

# Top-level keys each side's JSON must carry. The loaders validate these
# so a swapped --cd-json / --pdf-json fails loud and early.
_CD_REQUIRED_KEYS = frozenset({"bulletin_no", "bulletin_date", "patents", "stats"})
_PDF_REQUIRED_KEYS = frozenset({"bulletin_no", "bulletin_date", "records", "stats"})


def load_cd_metadata(path: Path) -> Dict[str, Any]:
    """Load a CD-side ``{YYYY_M}_metadata.json`` and validate its shape.

    Raises ``ValueError`` if the file is missing the keys cd_extract_patent
    is documented to write (``patents`` array, ``bulletin_no``, etc.) — this
    catches accidental --cd-json/--pdf-json swaps at the CLI boundary.
    """
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected JSON object, got {type(doc).__name__}")
    missing = _CD_REQUIRED_KEYS - doc.keys()
    if missing:
        raise ValueError(f"{path}: not a CD metadata doc (missing {sorted(missing)})")
    if not isinstance(doc["patents"], list):
        raise ValueError(f"{path}: 'patents' must be a list, got {type(doc['patents']).__name__}")
    return doc


def load_pdf_metadata(path: Path) -> Dict[str, Any]:
    """Load a PDF-side ``{YYYY_M}_pdf_metadata.json`` and validate its shape.

    Raises ``ValueError`` on missing keys — symmetric counterpart to
    ``load_cd_metadata``. Catches CLI argument swaps.
    """
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected JSON object, got {type(doc).__name__}")
    missing = _PDF_REQUIRED_KEYS - doc.keys()
    if missing:
        raise ValueError(f"{path}: not a PDF metadata doc (missing {sorted(missing)})")
    if not isinstance(doc["records"], list):
        raise ValueError(f"{path}: 'records' must be a list, got {type(doc['records']).__name__}")
    return doc


@dataclass
class CanonicalRecord:
    """Unified per-patent record shape consumed by Stage 5 (DB ingest).

    Sub-collections (holders, inventors, attorneys, priorities, figures)
    stay as plain ``List[Dict[str, Any]]`` because the field set differs
    between CD and PDF (CD holders carry state/postal_code/city; PDF
    holders only carry name/address/country). Mapping that asymmetry into
    typed dataclasses would either lose CD detail or force PDF code to
    fill nullable fields it never knows about. Dicts are honest.

    ``source_format`` records which source the record came from:
      - ``"CD"``    — present in CD only
      - ``"PDF"``   — present in PDF only
      - ``"BOTH"``  — matched by application_no in both, merged
    """

    application_no: Optional[str] = None
    application_date: Optional[str] = None      # ISO YYYY-MM-DD
    publication_no: Optional[str] = None
    publication_date: Optional[str] = None      # ISO
    grant_date: Optional[str] = None            # ISO; PDF-only field
    kind_code: Optional[str] = None
    record_type: Optional[str] = None           # GRANTED_PATENT, GRANTED_UM, ...
    title: Optional[str] = None
    abstract: Optional[str] = None
    ipc_classes: List[str] = field(default_factory=list)
    holders: List[Dict[str, Any]] = field(default_factory=list)
    inventors: List[Dict[str, Any]] = field(default_factory=list)
    attorneys: List[Dict[str, Any]] = field(default_factory=list)
    priorities: List[Dict[str, Any]] = field(default_factory=list)
    figures: List[Dict[str, Any]] = field(default_factory=list)
    patent_type: Optional[str] = None           # CD-only numeric flag (e.g. "2")
    page_range: Optional[List[int]] = None      # PDF-only [start, end]
    source_format: str = "CD"


# ---------------------------------------------------------------------------
# Step 4.2 — normalize_cd_record
# ---------------------------------------------------------------------------
#
# Converts one entry from the CD-side ``patents[]`` array into a
# ``CanonicalRecord``. Renames CD's per-party ``title`` field to ``name``
# (PDF convention), parses DD/MM/YYYY date strings into ISO, derives
# ``kind_code`` + ``record_type`` from ``publication_no``, and wraps
# the lone CD ``image_path`` into a single-element ``figures`` list.
#
# CD ``IPCCODE`` is already HTML-stripped by ``cd_extract_patent.py``
# (Step 2.2 ``strip_ipc_html``), so ``ipc_codes`` arrives as a clean
# list of code strings — just rename to ``ipc_classes``.

_DMY_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")


def _dmy_to_iso(value: Optional[str]) -> Optional[str]:
    """Convert a ``DD/MM/YYYY`` string to ``YYYY-MM-DD``.

    Returns ``None`` for empty / unparseable input. Defensive — CD JSON
    sometimes ships empty strings for unset dates.
    """
    if not value:
        return None
    match = _DMY_RE.match(value)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0).strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def _clean_str(value: Any) -> Optional[str]:
    """Strip whitespace; return ``None`` for empty / non-string input."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_cd_party(party: Dict[str, Any]) -> Dict[str, Any]:
    """Rename CD per-party ``title`` -> ``name``, drop empty fields.

    CD holders/inventors carry ``title``, ``address``, ``state``,
    ``postal_code``, ``city``, ``country``. PDF holders/inventors carry
    ``name``, ``address``, ``country``. The canonical shape uses ``name``
    everywhere; CD-only fields (state/postal_code/city) are preserved
    when non-empty so Stage 5 can use them for holder dedup.
    """
    out: Dict[str, Any] = {"name": _clean_str(party.get("title")) or ""}
    for key in ("address", "state", "postal_code", "city", "country"):
        value = _clean_str(party.get(key))
        if value:
            out[key] = value
    return out


def _normalize_cd_attorney(attorney: Dict[str, Any]) -> Dict[str, Any]:
    """Drop empty fields from a CD attorney dict; preserve order.

    CD attorneys ship ``no``, ``name``, ``address``, ``firm``. All
    optional except ``name``. PDF attorneys ship ``name`` + ``firm``
    only — Stage 4 attorney precedence (CD list ⊃ PDF object) lives in
    ``merge_records`` (step 4.4); this helper just normalises shape.
    """
    out: Dict[str, Any] = {}
    for key in ("no", "name", "firm", "address"):
        value = _clean_str(attorney.get(key))
        if value:
            out[key] = value
    return out


def _normalize_cd_priority(priority: Dict[str, Any]) -> Dict[str, Any]:
    """Convert CD priority date DD/MM/YYYY -> ISO; drop empty fields."""
    out: Dict[str, Any] = {}
    for key in ("priority_no", "country"):
        value = _clean_str(priority.get(key))
        if value:
            out[key] = value
    iso = _dmy_to_iso(priority.get("priority_date"))
    if iso:
        out["priority_date"] = iso
    return out


def _cd_figures(image_path: Optional[str]) -> List[Dict[str, Any]]:
    """Wrap CD's lone ``image_path`` into the canonical ``figures`` list.

    CD records carry at most one figure (the title-page TIFF resolved
    by ``cd_extract_patent`` step 2.5). PDF records can carry many,
    so the canonical shape is always a list.
    """
    cleaned = _clean_str(image_path)
    if not cleaned:
        return []
    return [{"image_path": cleaned}]


def normalize_cd_record(cd_record: Dict[str, Any]) -> CanonicalRecord:
    """Convert a single CD ``patents[]`` entry into a ``CanonicalRecord``.

    Pure transformation — no I/O, no DB, no fitz. The result has
    ``source_format='CD'`` and is ready for ``merge_records`` to
    combine with a matching PDF-side record.
    """
    publication_no = _clean_str(cd_record.get("publication_no"))
    kind_code = extract_kind_code(publication_no)
    record_type = classify_kind_code(kind_code).value if kind_code else None

    return CanonicalRecord(
        application_no=_clean_str(cd_record.get("application_no")),
        application_date=_dmy_to_iso(cd_record.get("application_date")),
        publication_no=publication_no,
        publication_date=_dmy_to_iso(cd_record.get("publication_date")),
        kind_code=kind_code,
        record_type=record_type,
        title=_clean_str(cd_record.get("title")),
        abstract=_clean_str(cd_record.get("abstract")),
        ipc_classes=list(cd_record.get("ipc_codes") or []),
        holders=[_normalize_cd_party(h) for h in (cd_record.get("holders") or [])],
        inventors=[_normalize_cd_party(i) for i in (cd_record.get("inventors") or [])],
        attorneys=[_normalize_cd_attorney(a) for a in (cd_record.get("attorneys") or [])],
        priorities=[_normalize_cd_priority(p) for p in (cd_record.get("priorities") or [])],
        figures=_cd_figures(cd_record.get("image_path")),
        patent_type=_clean_str(cd_record.get("patent_type")),
        source_format="CD",
    )


# ---------------------------------------------------------------------------
# Step 4.3 — normalize_pdf_record
# ---------------------------------------------------------------------------
#
# Converts one entry from PDF-side ``records[]`` into a CanonicalRecord.
# PDF is closer to canonical than CD — dates already ISO, ``ipc_classes``
# already named correctly, party shape uses ``name``. Two real differences:
#
#   1. ``attorney`` is a single object, not a list. Wrap into a 1-element
#      ``attorneys`` list (or drop entirely if missing).
#   2. Empty / null per-field values must be elided so downstream code
#      doesn't have to ``if value is not None and value.strip()`` checks.


def _normalize_pdf_party(party: Dict[str, Any]) -> Dict[str, Any]:
    """Drop empty fields from a PDF holder/inventor dict.

    PDF parties already use ``name`` (no rename needed) and only ship
    ``name``, ``address``, ``country``. Same dict shape, just stripped.
    """
    out: Dict[str, Any] = {"name": _clean_str(party.get("name")) or ""}
    for key in ("address", "country"):
        value = _clean_str(party.get(key))
        if value:
            out[key] = value
    return out


def _normalize_pdf_attorney_to_list(
    attorney: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Wrap PDF's single-object ``attorney`` into a 1-element list.

    The canonical shape is ``attorneys`` (list) for both sources because
    CD ships multiple attorneys per record and DB ingest needs a single
    code path. Returns ``[]`` when PDF didn't extract an attorney
    (missing key or an empty dict).
    """
    if not attorney:
        return []
    cleaned: Dict[str, Any] = {}
    for key in ("name", "firm"):
        value = _clean_str(attorney.get(key))
        if value:
            cleaned[key] = value
    return [cleaned] if cleaned else []


def _normalize_pdf_priority(priority: Dict[str, Any]) -> Dict[str, Any]:
    """Drop empty fields from a PDF priority dict — date already ISO."""
    out: Dict[str, Any] = {}
    for key in ("priority_no", "country"):
        value = _clean_str(priority.get(key))
        if value:
            out[key] = value
    iso = _clean_str(priority.get("priority_date"))
    if iso:
        out["priority_date"] = iso
    return out


def _normalize_pdf_figure(figure: Dict[str, Any]) -> Dict[str, Any]:
    """Pass-through PDF figure dict, dropping empty string fields.

    PDF figures carry ``image_path``, ``page``, ``image_xref``, ``bbox``
    (all from pdf_extract_patent step 3.6). Numeric fields preserved
    even when zero; only empty/None strings stripped.
    """
    out: Dict[str, Any] = {}
    path = _clean_str(figure.get("image_path"))
    if path:
        out["image_path"] = path
    for key in ("page", "image_xref"):
        if figure.get(key) is not None:
            out[key] = figure[key]
    bbox = figure.get("bbox")
    if bbox:
        out["bbox"] = list(bbox)
    return out


def _page_range_or_none(value: Any) -> Optional[List[int]]:
    """Coerce PDF page_range into a clean list[int], or None."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return [int(value[0]), int(value[1])]
    except (TypeError, ValueError):
        return None


def normalize_pdf_record(pdf_record: Dict[str, Any]) -> CanonicalRecord:
    """Convert a single PDF ``records[]`` entry into a ``CanonicalRecord``.

    PDF source already uses canonical field names for most fields. The
    main work is wrapping the single-object ``attorney`` into a list,
    eliding empty strings, and preserving PDF-only fields
    (``grant_date``, ``page_range``).
    """
    return CanonicalRecord(
        application_no=_clean_str(pdf_record.get("application_no")),
        application_date=_clean_str(pdf_record.get("application_date")),
        publication_no=_clean_str(pdf_record.get("publication_no")),
        publication_date=_clean_str(pdf_record.get("publication_date")),
        grant_date=_clean_str(pdf_record.get("grant_date")),
        kind_code=_clean_str(pdf_record.get("kind_code")),
        record_type=_clean_str(pdf_record.get("record_type")),
        title=_clean_str(pdf_record.get("title")),
        abstract=_clean_str(pdf_record.get("abstract")),
        ipc_classes=list(pdf_record.get("ipc_classes") or []),
        holders=[_normalize_pdf_party(h) for h in (pdf_record.get("holders") or [])],
        inventors=[_normalize_pdf_party(i) for i in (pdf_record.get("inventors") or [])],
        attorneys=_normalize_pdf_attorney_to_list(pdf_record.get("attorney")),
        priorities=[_normalize_pdf_priority(p) for p in (pdf_record.get("priorities") or [])],
        figures=[_normalize_pdf_figure(f) for f in (pdf_record.get("figures") or [])],
        patent_type=None,                         # CD-only field, never set from PDF
        page_range=_page_range_or_none(pdf_record.get("page_range")),
        source_format="PDF",
    )

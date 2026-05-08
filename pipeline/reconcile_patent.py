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

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


# ---------------------------------------------------------------------------
# Step 4.4 — merge_records (CD ↔ PDF precedence)
# ---------------------------------------------------------------------------
#
# Precedence rule: **CD wins on every overlapping field.** PDF only fills
# gaps and supplies fields CD doesn't carry. Rationale: PDF text comes
# from PyMuPDF extraction which is noisy (encoding glitches, page-banner
# contamination, ligature splits). CD is a typed HSQLDB row — clean even
# when truncated. See ``patent_cd_first_precedence`` memory.
#
#                          | Source of truth
#   -----------------------+-------------------------------------------
#   every overlapping      | CD  (PDF used only when CD is empty/None)
#   field                  |
#   grant_date             | PDF only (CD has no grant_date concept)
#   page_range             | PDF only
#   patent_type            | CD only
#   figures                | union — CD TIFFs primary, PDF JPEGs added
#                          | (paths never collide: .tif vs .jpg)
#   source_format          | 'BOTH'


def _merge_figures(
    cd_figs: List[Dict[str, Any]],
    pdf_figs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Concatenate CD and PDF figure lists.

    CD ships at most one TIFF (``data/images/{year}/{appno}.tif``);
    PDF ships zero or many JPEGs (``2025_08_figures/...jpg``).
    Paths never collide so a simple concat is correct — but dedup on
    image_path defensively in case a future change surfaces overlap.
    """
    seen: set = set()
    merged: List[Dict[str, Any]] = []
    for fig in [*cd_figs, *pdf_figs]:
        path = fig.get("image_path")
        if path is None:
            merged.append(fig)
            continue
        if path in seen:
            continue
        seen.add(path)
        merged.append(fig)
    return merged


def merge_records(cd: CanonicalRecord, pdf: CanonicalRecord) -> CanonicalRecord:
    """Merge a matched CD/PDF pair into a single canonical record.

    Caller must guarantee both inputs share the same ``application_no`` —
    pairing happens in ``reconcile_metadata`` (step 4.5). This function
    is pure precedence application; it doesn't validate keys.

    Raises ``ValueError`` if the application_no differs — defensive guard
    against pairing bugs upstream.
    """
    if cd.application_no != pdf.application_no:
        raise ValueError(
            f"merge_records called with mismatched application_no: "
            f"cd={cd.application_no!r} vs pdf={pdf.application_no!r}"
        )

    # CD's attorneys list is the canonical when present; fall back to PDF
    # only when CD shipped nothing (rare — CD almost always carries the
    # attorney row).
    attorneys = cd.attorneys if cd.attorneys else pdf.attorneys

    return CanonicalRecord(
        application_no=cd.application_no,
        application_date=cd.application_date or pdf.application_date,
        publication_no=cd.publication_no or pdf.publication_no,
        publication_date=cd.publication_date or pdf.publication_date,
        grant_date=pdf.grant_date,                     # PDF-only field
        kind_code=cd.kind_code or pdf.kind_code,        # CD-first
        record_type=cd.record_type or pdf.record_type,  # CD-first
        title=cd.title or pdf.title,                    # CD-first
        abstract=cd.abstract or pdf.abstract,           # CD-first (PyMuPDF noise)
        ipc_classes=cd.ipc_classes or pdf.ipc_classes,
        holders=cd.holders or pdf.holders,
        inventors=cd.inventors or pdf.inventors,
        attorneys=attorneys,
        priorities=cd.priorities or pdf.priorities,
        figures=_merge_figures(cd.figures, pdf.figures),
        patent_type=cd.patent_type,                    # CD-only field
        page_range=pdf.page_range,                     # PDF-only field
        source_format="BOTH",
    )


# ---------------------------------------------------------------------------
# Step 4.5 — reconcile_metadata orchestrator
# ---------------------------------------------------------------------------
#
# Top-level reconciler: takes the two upstream JSON docs (already loaded
# by the loaders from step 4.1), validates that they describe the same
# bulletin, indexes by application_no, and produces the unified output
# document Stage 5 will ingest.
#
# Bulletin-no equality is the only cross-doc invariant the reconciler
# enforces. Pairing two unrelated bulletins is silently catastrophic
# (zero overlap, mixed dates) so we raise on mismatch.


_RECORD_TYPES_FOR_STATS = (
    "GRANTED_PATENT",
    "GRANTED_UM",
    "PUBLISHED_APP",
    "PUBLISHED_UM_APP",
    "EP_FASCICLE",
    "UNKNOWN",
)


def _normalise_bulletin_no(raw: Optional[str]) -> Optional[str]:
    """Canonicalise the two formats CD/PDF use into one shape.

    CD ships ``"2025/8"`` (HSQLDB-derived), PDF ships ``"2025-08"``
    (PDF cover-page-parsed). Reconcile uses ``"2025/8"`` (no zero-pad)
    since that's what cd_extract_patent emits and the rest of the
    pipeline already speaks.
    """
    if not raw:
        return None
    match = re.match(r"^\s*(\d{4})[/-](\d{1,2})\s*$", raw)
    if not match:
        return raw.strip() or None
    year, month = match.group(1), match.group(2).lstrip("0") or "0"
    return f"{year}/{month}"


def _utcnow_iso() -> str:
    """Current UTC time as ISO 8601 with seconds precision (matches upstream)."""
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _build_stats(records: List[CanonicalRecord]) -> Dict[str, Any]:
    """Aggregate stats over the unified records list.

    Mirrors the per-stage stats blocks in cd_extract_patent + pdf_extract_patent
    (records, by_record_type, figures_total) plus the new by_source_format
    distribution which is the headline Stage 4 quality signal.
    """
    by_source_format = {"CD": 0, "PDF": 0, "BOTH": 0}
    by_record_type = {key: 0 for key in _RECORD_TYPES_FOR_STATS}
    figures_total = 0

    for rec in records:
        by_source_format[rec.source_format] = by_source_format.get(rec.source_format, 0) + 1
        rt = rec.record_type or "UNKNOWN"
        by_record_type[rt] = by_record_type.get(rt, 0) + 1
        figures_total += len(rec.figures)

    return {
        "records": len(records),
        "by_source_format": by_source_format,
        "by_record_type": by_record_type,
        "figures_total": figures_total,
    }


def _record_to_dict(rec: CanonicalRecord) -> Dict[str, Any]:
    """Serialise a CanonicalRecord into JSON-ready dict, dropping None scalars.

    Empty lists are preserved (downstream reads them as "no holders"
    distinct from "field absent"); only None scalars are stripped to
    keep the unified JSON tidy for human eyeballing in Stage 4.8.
    """
    out = asdict(rec)
    return {k: v for k, v in out.items() if v is not None}


def reconcile_metadata(
    cd_doc: Optional[Dict[str, Any]] = None,
    pdf_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge two per-bulletin metadata docs into one canonical doc.

    Inputs are the dicts returned by ``load_cd_metadata`` and
    ``load_pdf_metadata``. Either can be ``None`` for single-side
    reconcile (CD-only month — common in 2015–2017 when PDFs hadn't
    started shipping yet; or PDF-only month — rare, but happens when
    the CD download failed).

    Output is a JSON-ready dict with:

      - ``bulletin_no``, ``bulletin_date``, ``source_archive``,
        ``source_pdf`` — provenance preserved from each upstream doc;
        absent side is reflected as ``None``.
      - ``records`` — list of canonical-shape dicts, sorted by
        ``application_no`` for deterministic output.
      - ``stats`` — record count, by_source_format distribution,
        by_record_type distribution, total figures.

    Raises:
      - ``ValueError`` when both docs are ``None``.
      - ``ValueError`` when the two docs describe different
        bulletins (``bulletin_no`` mismatch).
    """
    if cd_doc is None and pdf_doc is None:
        raise ValueError("reconcile_metadata requires at least one of cd_doc / pdf_doc")

    cd_bulletin = cd_doc.get("bulletin_no") if cd_doc else None
    pdf_bulletin = pdf_doc.get("bulletin_no") if pdf_doc else None

    # Bulletin equivalence check applies only when both sides are present.
    # CD ships "2025/8", PDF ships "2025-08" — both canonicalise to "2025/8".
    if cd_doc is not None and pdf_doc is not None:
        if _normalise_bulletin_no(cd_bulletin) != _normalise_bulletin_no(pdf_bulletin):
            raise ValueError(
                f"bulletin_no mismatch: CD={cd_bulletin!r} PDF={pdf_bulletin!r} "
                f"(reconcile would silently produce wrong overlap; aborting)"
            )

    cd_records = (
        [normalize_cd_record(p) for p in cd_doc.get("patents", [])]
        if cd_doc is not None else []
    )
    pdf_records = (
        [normalize_pdf_record(r) for r in pdf_doc.get("records", [])]
        if pdf_doc is not None else []
    )

    # Merge key is ``publication_no`` (e.g. "TR 2024 000746 A1"), NOT
    # ``application_no``. Reason: a single application can be re-published
    # at multiple lifecycle stages in the same bulletin (e.g. 2024/000746
    # appears as both B grant on p.312 and A1 publication on p.1850 in
    # 2025_08.pdf). Merging by application_no would double-count those
    # rows and produce inconsistent kind_code/record_type. Falls back
    # to application_no only when publication_no is blank on either side.
    def _merge_key(rec: CanonicalRecord) -> Optional[str]:
        return rec.publication_no or rec.application_no

    cd_by_pub: Dict[str, CanonicalRecord] = {}
    for rec in cd_records:
        key = _merge_key(rec)
        if key:
            cd_by_pub[key] = rec

    merged: List[CanonicalRecord] = []
    matched_keys: set = set()

    for pdf_rec in pdf_records:
        key = _merge_key(pdf_rec)
        cd_match = cd_by_pub.get(key) if key else None
        if cd_match is not None:
            merged.append(merge_records(cd_match, pdf_rec))
            matched_keys.add(key)
        else:
            merged.append(pdf_rec)

    # CD-only records are everything in cd_by_pub that PDF didn't claim.
    for key, cd_rec in cd_by_pub.items():
        if key not in matched_keys:
            merged.append(cd_rec)

    # Deterministic order by application_no — keeps diff-of-runs noise-free
    # and helps human eyeballing.
    merged.sort(key=lambda r: r.application_no or "")

    return {
        "bulletin_no": _normalise_bulletin_no(cd_bulletin or pdf_bulletin),
        "bulletin_date": (
            (cd_doc.get("bulletin_date") if cd_doc else None)
            or (pdf_doc.get("bulletin_date") if pdf_doc else None)
        ),
        "source_archive": cd_doc.get("source_archive") if cd_doc else None,
        "source_pdf": pdf_doc.get("source_pdf") if pdf_doc else None,
        "reconciled_at": _utcnow_iso(),
        "stats": _build_stats(merged),
        "records": [_record_to_dict(r) for r in merged],
    }


# ---------------------------------------------------------------------------
# Step 4.7 — CLI entrypoint + filename derivation
# ---------------------------------------------------------------------------
#
# CLI mirrors cd_extract_patent (step 2.8) and pdf_extract_patent (step 3.8):
#
#   python -m pipeline.reconcile_patent \
#     --cd-json bulletins/Patent__Faydali_Model/2025_07_metadata.json \
#     --pdf-json bulletins/Patent__Faydali_Model/2025_08_pdf_metadata.json
#
#   python -m pipeline.reconcile_patent --all
#
# Output filename comes from the *canonical bulletin_no* of the merged
# document (e.g. bulletin "2025/8" -> "2025_08_metadata.json"), NOT from
# either input's filename stem. This guarantees the unified file lands
# at the bulletin-month name even when the CD's filename has a 1-month
# offset (see patent_cd_filename_offset memory).
#
# The unified output COULD overwrite the CD-side intermediate (it shares
# the `_metadata.json` suffix with no infix). The locked JSON-naming
# decision permits that — once unified is written, Stage 5 ingests from
# unified, the CD intermediate is no longer needed. ``--force`` is
# required to overwrite an existing file.

_DEFAULT_BULLETINS_DIR = (
    PROJECT_ROOT / "bulletins" / "Patent__Faydali_Model"
)


@dataclass
class CLIArgs:
    cd_json: Optional[Path]
    pdf_json: Optional[Path]
    out_dir: Path
    bulletins_dir: Path
    all_mode: bool
    force: bool


# The unified output lives inside its bulletin's PT_{Y}_{M}_{date}/
# parent folder, so it can use the simple canonical filename without
# any infix — no collision is possible since each bulletin has its own
# folder. Stage 5 ingest reads metadata.json from each PT_ folder.
UNIFIED_METADATA_FILENAME = "metadata.json"
CD_METADATA_FILENAME = "cd_metadata.json"
PDF_METADATA_FILENAME = "pdf_metadata.json"


def classify_metadata_json(path: Path) -> str:
    """Discriminate file type by canonical filename inside a bulletin folder.

    Returns ``"cd"``, ``"pdf"``, or ``"unified"``.

    The Marka-style folder layout (one bulletin per PT_{Y}_{M}_{date}/)
    means every JSON in the pipeline has a fixed canonical name:
      - ``cd_metadata.json``      → ``"cd"``
      - ``pdf_metadata.json``     → ``"pdf"``
      - ``metadata.json``         → ``"unified"``

    Raises ``ValueError`` for any other filename so flat-layout
    leftovers (``*_pdf_metadata.json`` from a pre-refactor run) fail
    loudly instead of being silently misclassified.
    """
    name = path.name
    if name == CD_METADATA_FILENAME:
        return "cd"
    if name == PDF_METADATA_FILENAME:
        return "pdf"
    if name == UNIFIED_METADATA_FILENAME:
        return "unified"
    raise ValueError(
        f"{path}: not a canonical metadata filename "
        f"(expected one of {CD_METADATA_FILENAME!r}, "
        f"{PDF_METADATA_FILENAME!r}, {UNIFIED_METADATA_FILENAME!r})"
    )


def _group_by_bulletin(
    bulletins_dir: Path,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Walk PT_*/ folders under bulletins_dir, group CD + PDF docs by bulletin_no.

    Returns ``{bulletin_no: {"cd": <doc>, "pdf": <doc>}}`` mapping each
    bulletin's canonical number to its loaded source docs. Each
    ``PT_{Y}_{M}_{date}/`` folder may contain ``cd_metadata.json``,
    ``pdf_metadata.json``, and (after a prior reconcile) ``metadata.json``
    — the unified file is skipped here because feeding a previous
    unified back as input would compound stats blocks.

    The function pre-loads every doc into memory (rather than storing
    paths) because Stage 4's --all loop writes back into each PT_
    folder — re-reading paths after writes produces a unified doc when
    we wanted the original CD/PDF source.

    Memory cost: ~500 MB across the full 113-bulletin archive,
    acceptable for a dev workflow.
    """
    groups: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for parent in sorted(p for p in bulletins_dir.iterdir() if p.is_dir()):
        if not parent.name.startswith("PT_"):
            continue
        for kind, fname in (("cd", CD_METADATA_FILENAME),
                             ("pdf", PDF_METADATA_FILENAME)):
            path = parent / fname
            if not path.is_file():
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("[skip] %s: cannot read JSON (%s)", path, exc)
                continue
            bulletin = _normalise_bulletin_no(doc.get("bulletin_no"))
            if not bulletin:
                logger.warning("[skip] %s: missing bulletin_no", path)
                continue
            groups.setdefault(bulletin, {})[kind] = doc

    return groups


def _process_pair(
    cd_doc: Optional[Dict[str, Any]],
    pdf_doc: Optional[Dict[str, Any]],
    out_dir: Path,
    force: bool,
) -> Dict[str, Any]:
    """Reconcile already-loaded CD + PDF docs and write the unified output.

    Writes ``out_dir/PT_{Y}_{M}_{date}/metadata.json``. The parent
    folder is created if needed; existing CD / PDF metadata in the same
    folder is left untouched. ``--force`` is required to overwrite an
    existing unified file.

    At least one of ``cd_doc`` / ``pdf_doc`` must be set. Returns a
    summary dict so ``main`` can report aggregate progress.
    """
    from patent_paths import bulletin_folder_path  # local import; avoids cycles

    unified = reconcile_metadata(cd_doc=cd_doc, pdf_doc=pdf_doc)

    parent = bulletin_folder_path(
        out_dir, unified["bulletin_no"], unified["bulletin_date"],
    )
    out_path = parent / UNIFIED_METADATA_FILENAME
    if out_path.exists() and not force:
        raise FileExistsError(
            f"{out_path} already exists; pass --force to overwrite"
        )

    parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(unified, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "out": f"{parent.name}/{out_path.name}",
        "bulletin_no": unified["bulletin_no"],
        "stats": unified["stats"],
    }


def _process_one(
    cd_path: Optional[Path],
    pdf_path: Optional[Path],
    out_dir: Path,
    force: bool,
) -> Dict[str, Any]:
    """Single-pair reconcile entry point used by --cd-json/--pdf-json mode."""
    cd_doc = load_cd_metadata(cd_path) if cd_path else None
    pdf_doc = load_pdf_metadata(pdf_path) if pdf_path else None
    return _process_pair(cd_doc, pdf_doc, out_dir, force)


def parse_argv(argv: Optional[List[str]] = None) -> CLIArgs:
    parser = argparse.ArgumentParser(
        prog="pipeline.reconcile_patent",
        description="Reconcile Patent CD-side and PDF-side metadata JSON.",
    )
    parser.add_argument("--cd-json", type=Path, default=None,
                        help="Path to a CD-side {YYYY_M}_metadata.json.")
    parser.add_argument("--pdf-json", type=Path, default=None,
                        help="Path to a PDF-side {YYYY_M}_pdf_metadata.json.")
    parser.add_argument("--all", action="store_true", dest="all_mode",
                        help="Reconcile every bulletin in --bulletins-dir.")
    parser.add_argument("--bulletins-dir", type=Path,
                        default=_DEFAULT_BULLETINS_DIR,
                        help=f"Directory to scan for --all (default: "
                             f"{_DEFAULT_BULLETINS_DIR}).")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="Where to write unified {YYYY_M}_metadata.json "
                             "(default: --bulletins-dir).")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing unified output files.")
    ns = parser.parse_args(argv)

    if ns.all_mode and (ns.cd_json or ns.pdf_json):
        parser.error("--all is mutually exclusive with --cd-json/--pdf-json")
    if not ns.all_mode and not (ns.cd_json or ns.pdf_json):
        parser.error("provide --cd-json and/or --pdf-json, or use --all")

    out_dir = ns.out_dir if ns.out_dir is not None else ns.bulletins_dir

    return CLIArgs(
        cd_json=ns.cd_json,
        pdf_json=ns.pdf_json,
        out_dir=out_dir,
        bulletins_dir=ns.bulletins_dir,
        all_mode=ns.all_mode,
        force=ns.force,
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_argv(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    succeeded: List[str] = []
    failed: List[Tuple[str, str]] = []

    if args.all_mode:
        groups = _group_by_bulletin(args.bulletins_dir)
        if not groups:
            logger.warning("--all: no metadata JSON files found in %s", args.bulletins_dir)
            return 1
        for bulletin, docs in sorted(groups.items()):
            label = f"bulletin {bulletin}"
            try:
                result = _process_pair(
                    docs.get("cd"), docs.get("pdf"),
                    args.out_dir, args.force,
                )
                succeeded.append(label)
                logger.info(
                    "[+] %s -> %s (%d records: %s)",
                    label, result["out"], result["stats"]["records"],
                    result["stats"]["by_source_format"],
                )
            except Exception as exc:
                failed.append((label, str(exc)))
                logger.error("[!] %s: %r", label, exc)
    else:
        label = f"{args.cd_json or '(no CD)'} + {args.pdf_json or '(no PDF)'}"
        try:
            result = _process_one(args.cd_json, args.pdf_json, args.out_dir, args.force)
            succeeded.append(label)
            logger.info(
                "[+] %s -> %s (%d records: %s)",
                label, result["out"], result["stats"]["records"],
                result["stats"]["by_source_format"],
            )
        except Exception as exc:
            failed.append((label, str(exc)))
            logger.error("[!] %s: %r", label, exc)

    duration = time.time() - started
    logger.info(
        "Done in %.1fs: %d succeeded, %d failed", duration,
        len(succeeded), len(failed),
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())



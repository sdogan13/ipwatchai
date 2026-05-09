"""Tasarım — CD ↔ PDF reconciler (Stage 3).

Reads the two per-issue JSON outputs that stages 2 (CD) and PDF-extract
produce when both run on the same ``TS_{N}_{date}/`` folder:

  - ``cd_metadata.json``  from ``cd_extract_tasarim.py``
  - ``metadata.json``     from ``pdf_extract_tasarim.py``

…and merges them into a single canonical ``merged_metadata.json``.

Locked precedence rules:

  - **CD wins on every overlapping field**. PDF fills gaps where CD is
    null/empty/missing.
  - **CD images stay in cd_images/**, PDF duplicates at the canonical
    key shape are gone (already enforced proactively at extraction
    time by D.1 + D.2; step 3.6 mops up any pre-existing dual-source
    folders).
  - Embeddings stay in their source ``metadata.json`` (excluded from
    the merged document — keeps the merged file small and clean).
  - PDF events live separately in ``events.json`` and aren't touched.
    CD ``annotations`` pass through unchanged as a sibling top-level
    array in the merged doc.

Pairing key inside an issue:

  - TR records: pair by ``application_no``.
  - Hague records: pair by normalised ``registration_no`` (PDF
    ``"DM 244882"`` ↔ CD ``"DM 244882"`` after stripping whitespace
    and uppercasing — IDDOSSIER's REGISTERNO is the bridge).

Inputs are **always** read from the same TS folder. The pre-stage-3
hygiene work (P.1 in cd_extract + collector, P.2 rename script)
guarantees the by-folder pair is canonical.

Built incrementally. Each helper has its own unit-test block.

CLI (lands in step 3.7)::

    python -m pipeline.reconcile_tasarim --issue TS_240_2016-03-09
    python -m pipeline.reconcile_tasarim --all
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [TASARIM-RECONCILE] - %(levelname)s - %(message)s",
)
logger = logging.getLogger("turkpatent.tasarim_reconcile")


# ---------------------------------------------------------------------------
# Step 3.1 — JSON loaders + CanonicalDesignRecord dataclass
# ---------------------------------------------------------------------------

# Top-level keys each side's JSON must carry. The loaders validate these
# so a swapped --cd-json / --pdf-json fails loud and early.
_CD_REQUIRED_KEYS = frozenset({"bulletin_no", "bulletin_date", "dossiers", "stats"})
_PDF_REQUIRED_KEYS = frozenset({"bulletin_no", "bulletin_date", "records"})


def load_cd_metadata(path: str | Path) -> Dict[str, Any]:
    """Load a CD-side ``cd_metadata.json`` and validate its shape.

    Raises ``ValueError`` if the file is missing the keys
    ``cd_extract_tasarim`` is documented to write (``dossiers`` array,
    ``bulletin_no``, etc.) — this catches accidental --cd-json/--pdf-json
    swaps at the CLI boundary before any merge work happens.
    """
    p = Path(path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{p}: expected JSON object, got {type(doc).__name__}")
    missing = _CD_REQUIRED_KEYS - doc.keys()
    if missing:
        raise ValueError(f"{p}: not a CD metadata doc (missing {sorted(missing)})")
    if not isinstance(doc["dossiers"], list):
        raise ValueError(
            f"{p}: 'dossiers' must be a list, got {type(doc['dossiers']).__name__}"
        )
    return doc


def load_pdf_metadata(path: str | Path) -> Dict[str, Any]:
    """Load a PDF-side ``metadata.json`` and validate its shape.

    Raises ``ValueError`` on missing keys — symmetric counterpart to
    ``load_cd_metadata``. Catches CLI argument swaps.
    """
    p = Path(path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{p}: expected JSON object, got {type(doc).__name__}")
    missing = _PDF_REQUIRED_KEYS - doc.keys()
    if missing:
        raise ValueError(f"{p}: not a PDF metadata doc (missing {sorted(missing)})")
    if not isinstance(doc["records"], list):
        raise ValueError(
            f"{p}: 'records' must be a list, got {type(doc['records']).__name__}"
        )
    return doc


@dataclass
class CanonicalDesignView:
    """Per-view entry in a merged design.

    ``image_source`` is the provenance hint a consumer needs to locate
    the actual file on disk:

      - ``"cd"``  -> resolve ``image_path`` under ``cd_images/`` (CD wins)
      - ``"pdf"`` -> resolve under ``images/`` (CD didn't have this view)
      - ``None``  -> no image was located/persisted (e.g. Hague views,
                     or PDF view where image extraction couldn't pair a
                     bbox with a label)
    """

    view_no: str
    image_path: Optional[str] = None
    image_source: Optional[str] = None


@dataclass
class CanonicalDesign:
    """Per-design entry in a merged record."""

    no: str
    product_name: str = ""
    views: List[CanonicalDesignView] = field(default_factory=list)


@dataclass
class CanonicalDesignRecord:
    """Unified per-record shape produced by the reconciler.

    Sub-collections (holders, designers, priorities, attorney,
    hague_reference, deferred_publication) stay as plain ``Dict`` /
    ``List[Dict]`` because the field set differs between CD and PDF.
    Forcing them into typed dataclasses would either lose source
    detail or force one side to fill nullable fields it never knows
    about. Dicts here are honest.

    ``source_format`` records which source the record came from:
      - ``"CD"``    — present in CD only
      - ``"PDF"``   — present in PDF only
      - ``"BOTH"``  — paired by application_no (TR) or normalised
                       registration_no (Hague), then merged
    """

    application_no: Optional[str] = None
    registration_no: Optional[str] = None
    application_date: Optional[str] = None        # ISO YYYY-MM-DD
    registration_date: Optional[str] = None       # ISO
    design_count: Optional[int] = None
    type: Optional[str] = None                    # CD-only IDDOSSIER.TYPE flag
    section: Optional[str] = None                 # PDF-only ("tr_native", "hague", ...)
    locarno_codes: List[str] = field(default_factory=list)
    attorney: Optional[Dict[str, Any]] = None
    holders: List[Dict[str, Any]] = field(default_factory=list)
    designers: List[Dict[str, Any]] = field(default_factory=list)
    priorities: List[Dict[str, Any]] = field(default_factory=list)
    designs: List[CanonicalDesign] = field(default_factory=list)
    hague_reference: Optional[Dict[str, Any]] = None    # PDF-only object
    page_range: Optional[List[int]] = None              # PDF-only
    deferred_publication: Optional[Dict[str, Any]] = None  # PDF-only
    source_format: str = "CD"


# ---------------------------------------------------------------------------
# Step 3.2 — normalize_cd_dossier
# ---------------------------------------------------------------------------

# Tasarim CD dates ship as DD.MM.YYYY (the dotted form); patent CD ships
# DD/MM/YYYY (slash). Same idea, different separator — keep the regex
# Tasarim-specific so a slash here would be rejected as an honest signal
# something's off rather than silently parsed.
_DOTTED_DMY_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")


def _dmy_to_iso(value: Optional[str]) -> Optional[str]:
    """Convert ``DD.MM.YYYY`` to ``YYYY-MM-DD``; ``None`` on empty or
    unparseable input. Defensive — CD JSON sometimes ships empty
    strings for unset dates."""
    if not value or not isinstance(value, str):
        return None
    match = _DOTTED_DMY_RE.match(value)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0).strip(), "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def _clean_str(value: Any) -> Optional[str]:
    """Strip whitespace; return ``None`` for empty / non-string input."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_design_count(value: Any) -> Optional[int]:
    """CD ships ``design_count`` as a string (``'1'``, ``'34'``); PDF
    ships it as an int. Normalise to int (or ``None`` on unparseable)."""
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


# Field-map style: each map says ``{source_key: canonical_key}``. The
# canonical shape unifies CD and PDF — most notably, CD holders ship
# their entity name as ``title`` while PDF applicants ship it as ``name``;
# both collapse to canonical ``name`` so a downstream consumer doesn't
# need to know which side each holder came from.
_CD_HOLDER_FIELD_MAP: Dict[str, str] = {
    "client_no": "client_no",
    "title":     "name",            # rename: CD.title -> canonical.name
    "address":   "address",
    "city":      "city",
    "country":   "country",
}

_CD_DESIGNER_FIELD_MAP: Dict[str, str] = {
    "no":      "no",
    "name":    "name",
    "address": "address",
    "country": "country",
}

_CD_ATTORNEY_FIELD_MAP: Dict[str, str] = {
    "no":      "no",
    "name":    "name",
    "title":   "title",
    "address": "address",
}


def _normalize_party(party: Dict[str, Any], field_map: Dict[str, str]) -> Dict[str, Any]:
    """Pick + rename fields from a party dict using ``field_map``;
    drop empties so the merged JSON doesn't carry cosmetic blanks.

    Iteration order follows ``field_map`` insertion order so the
    output dict's key order is stable and matches the canonical shape.
    """
    out: Dict[str, Any] = {}
    for src_key, dst_key in field_map.items():
        value = _clean_str(party.get(src_key))
        if value:
            out[dst_key] = value
    return out


def _normalize_attorney(
    attorney: Optional[Dict[str, Any]],
    field_map: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """``_normalize_party`` for a single attorney object — collapses to
    ``None`` when every mapped field is empty so empty CD attorney
    blocks (common on Hague dossiers where IDDOSSIER's attorney columns
    are blank) don't decorate the merged record."""
    if not isinstance(attorney, dict):
        return None
    out = _normalize_party(attorney, field_map)
    return out or None


def _normalize_cd_view(view: Dict[str, Any]) -> CanonicalDesignView:
    """Convert a CD view dict to a CanonicalDesignView with
    ``image_source='cd'`` whenever an image_path is present (Hague
    designs and image-less rows leave both fields ``None``)."""
    image_path = _clean_str(view.get("image_path"))
    return CanonicalDesignView(
        view_no=str(view.get("view_no") or ""),
        image_path=image_path,
        image_source="cd" if image_path else None,
    )


def _normalize_cd_design(design: Dict[str, Any]) -> CanonicalDesign:
    """Convert a CD design dict (no / product_name / views) to a
    CanonicalDesign. Trailing whitespace in product_name is stripped —
    cd_extract preserves it verbatim from IDDESIGN.PRODUCTNAME (e.g.
    ``'Profil '``) but the merged shape doesn't need that artefact."""
    views = [
        _normalize_cd_view(v)
        for v in design.get("views", []) or []
        if isinstance(v, dict)
    ]
    return CanonicalDesign(
        no=str(design.get("no") or ""),
        product_name=(_clean_str(design.get("product_name")) or ""),
        views=views,
    )


def normalize_cd_dossier(dossier: Dict[str, Any]) -> CanonicalDesignRecord:
    """Convert one CD ``dossiers[]`` entry into a CanonicalDesignRecord.

    Field mapping:
      - ``register_no``       -> ``registration_no``
      - ``application_date``  -> ISO (DD.MM.YYYY -> YYYY-MM-DD)
      - ``register_date``     -> ``registration_date`` (ISO)
      - ``design_count``      -> int (string -> int, blank -> None)

    Empty / blank optional fields collapse to ``None`` so the merged
    JSON doesn't carry cosmetic empty-string fields. Per-design views
    are tagged ``image_source='cd'`` to drive consumer resolution.

    ``priorities`` is always ``[]`` for CD-derived records — IDDOSSIER
    has no priority columns; PDF carries that data on its (30) field.
    Leaving the slot empty lets ``merge_records`` fill from PDF cleanly.
    """
    holders = [
        h for h in (
            _normalize_party(item, _CD_HOLDER_FIELD_MAP)
            for item in dossier.get("holders", []) or []
            if isinstance(item, dict)
        ) if h
    ]
    designers = [
        d for d in (
            _normalize_party(item, _CD_DESIGNER_FIELD_MAP)
            for item in dossier.get("designers", []) or []
            if isinstance(item, dict)
        ) if d
    ]
    designs = [
        _normalize_cd_design(d)
        for d in dossier.get("designs", []) or []
        if isinstance(d, dict)
    ]

    return CanonicalDesignRecord(
        application_no=_clean_str(dossier.get("application_no")),
        registration_no=_clean_str(dossier.get("register_no")),
        application_date=_dmy_to_iso(dossier.get("application_date")),
        registration_date=_dmy_to_iso(dossier.get("register_date")),
        design_count=_parse_design_count(dossier.get("design_count")),
        type=_clean_str(dossier.get("type")),
        locarno_codes=list(dossier.get("locarno_codes") or []),
        attorney=_normalize_attorney(dossier.get("attorney"), _CD_ATTORNEY_FIELD_MAP),
        holders=holders,
        designers=designers,
        priorities=[],
        designs=designs,
        source_format="CD",
    )


# ---------------------------------------------------------------------------
# Step 3.3 — normalize_pdf_record
# ---------------------------------------------------------------------------

# PDF applicant.id is the same TPECLIENT id CD ships as client_no — so
# canonical holders carry it under client_no whichever side the record
# came from.
_PDF_APPLICANT_FIELD_MAP: Dict[str, str] = {
    "name":    "name",
    "id":      "client_no",   # rename: PDF.id -> canonical.client_no
    "address": "address",
    "country": "country",
}

_PDF_DESIGNER_FIELD_MAP: Dict[str, str] = {
    "name": "name",
}

_PDF_ATTORNEY_FIELD_MAP: Dict[str, str] = {
    "name": "name",
    "firm": "firm",
}


def _normalize_pdf_view(view: Dict[str, Any]) -> CanonicalDesignView:
    """Convert a PDF view dict to a CanonicalDesignView.

    Drops PyMuPDF extraction artefacts (``image_xref``, ``bbox``,
    ``page``) and any inline embeddings vector — none of those belong
    in the merged JSON. Preserves ``view_index`` (cast to str for
    canonical shape) plus ``image_path`` and the new ``image_source``
    provenance tag (D.1) so the consumer can resolve the canonical
    key under the correct sibling folder.
    """
    image_path = _clean_str(view.get("image_path"))
    raw_source = _clean_str(view.get("image_source"))
    return CanonicalDesignView(
        view_no=str(view.get("view_index") or ""),
        image_path=image_path,
        image_source=raw_source if image_path else None,
    )


def _normalize_pdf_design(design: Dict[str, Any]) -> CanonicalDesign:
    """Convert a PDF design dict (design_index / product_name_tr / views)
    to a CanonicalDesign.

    Field renames:
      ``design_index``     -> ``no`` (str)
      ``product_name_tr``  -> ``product_name``
    """
    views = [
        _normalize_pdf_view(v)
        for v in design.get("views", []) or []
        if isinstance(v, dict)
    ]
    return CanonicalDesign(
        no=str(design.get("design_index") or ""),
        product_name=(_clean_str(design.get("product_name_tr")) or ""),
        views=views,
    )


def _normalize_pdf_priority(priority: Dict[str, Any]) -> Dict[str, Any]:
    """PDF priority shape ``{date, number, country}`` — clean fields."""
    out: Dict[str, Any] = {}
    for key in ("date", "number", "country"):
        value = _clean_str(priority.get(key))
        if value:
            out[key] = value
    return out


def _normalize_pdf_hague_reference(
    ref: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """PDF Hague-section ``hague_reference`` block:
    ``{wipo_bulletin, designated_states[], product_name_en}``.
    Returns ``None`` when the dict has no useful content."""
    if not isinstance(ref, dict):
        return None
    out: Dict[str, Any] = {}
    wipo = _clean_str(ref.get("wipo_bulletin"))
    if wipo:
        out["wipo_bulletin"] = wipo
    states = ref.get("designated_states")
    if isinstance(states, list):
        cleaned = [s for s in states if isinstance(s, str) and s.strip()]
        if cleaned:
            out["designated_states"] = [s.strip() for s in cleaned]
    product = _clean_str(ref.get("product_name_en"))
    if product:
        out["product_name_en"] = product
    return out or None


def _normalize_pdf_deferred_publication(
    dp: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """PDF deferred-publication block: ``{period_months: int}``."""
    if not isinstance(dp, dict):
        return None
    period = dp.get("period_months")
    if isinstance(period, int):
        return {"period_months": period}
    return None


def normalize_pdf_record(record: Dict[str, Any]) -> CanonicalDesignRecord:
    """Convert one PDF ``records[]`` entry into a CanonicalDesignRecord.

    Field mapping:
      ``filing_date``       -> ``application_date`` (already ISO, just rename)
      ``registration_date`` -> ``registration_date`` (already ISO)
      ``locarno_classes``   -> ``locarno_codes``
      ``applicants``        -> ``holders`` (with PDF.id -> canonical.client_no)
      ``design_index``      -> ``designs[].no`` (str)
      ``product_name_tr``   -> ``designs[].product_name``
      ``view_index``        -> ``designs[].views[].view_no`` (str)

    PDF-only fields preserved on the canonical record:
      - ``section`` ("tr_native" / "deferred" / "hague" / ...)
      - ``hague_reference`` (Hague-section records)
      - ``page_range``
      - ``deferred_publication``
      - ``priorities``

    Embedding vectors and PyMuPDF artefacts (image_xref, bbox, page)
    are intentionally dropped — they belong in the source metadata.json
    (Q2 locked decision).

    No date normalisation needed: pdf_extract_tasarim already produces
    ISO dates via ``normalize_tr_date``.
    """
    holders = [
        h for h in (
            _normalize_party(item, _PDF_APPLICANT_FIELD_MAP)
            for item in record.get("applicants", []) or []
            if isinstance(item, dict)
        ) if h
    ]
    designers = [
        d for d in (
            _normalize_party(item, _PDF_DESIGNER_FIELD_MAP)
            for item in record.get("designers", []) or []
            if isinstance(item, dict)
        ) if d
    ]
    priorities = [
        p for p in (
            _normalize_pdf_priority(item)
            for item in record.get("priorities", []) or []
            if isinstance(item, dict)
        ) if p
    ]
    designs = [
        _normalize_pdf_design(d)
        for d in record.get("designs", []) or []
        if isinstance(d, dict)
    ]

    raw_page_range = record.get("page_range")
    page_range: Optional[List[int]] = None
    if (isinstance(raw_page_range, list)
            and len(raw_page_range) == 2
            and all(isinstance(x, int) for x in raw_page_range)):
        page_range = list(raw_page_range)

    return CanonicalDesignRecord(
        application_no=_clean_str(record.get("application_no")),
        registration_no=_clean_str(record.get("registration_no")),
        application_date=_clean_str(record.get("filing_date")),
        registration_date=_clean_str(record.get("registration_date")),
        design_count=_parse_design_count(record.get("design_count")),
        type=None,  # PDF has no IDDOSSIER.TYPE equivalent
        section=_clean_str(record.get("section")),
        locarno_codes=list(record.get("locarno_classes") or []),
        attorney=_normalize_attorney(record.get("attorney"), _PDF_ATTORNEY_FIELD_MAP),
        holders=holders,
        designers=designers,
        priorities=priorities,
        designs=designs,
        hague_reference=_normalize_pdf_hague_reference(record.get("hague_reference")),
        page_range=page_range,
        deferred_publication=_normalize_pdf_deferred_publication(
            record.get("deferred_publication"),
        ),
        source_format="PDF",
    )

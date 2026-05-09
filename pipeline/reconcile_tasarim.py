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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# Step 3.4 — merge_records + Hague pairing helper
# ---------------------------------------------------------------------------

_REG_NO_WHITESPACE_RE = re.compile(r"\s+")


def _normalise_registration_no(value: Optional[str]) -> Optional[str]:
    """Collapse whitespace + uppercase a Hague registration_no for pairing.

    PDF ships ``"DM 244882"`` (with space, parsed from the bulletin).
    CD's IDDOSSIER.REGISTERNO ships either ``"DM 244882"`` or
    ``"DM244882"`` depending on how the row was written into the
    HSQLDB. Both collapse to ``"DM244882"`` so the orchestrator can
    match them as the same Hague registration.

    Returns ``None`` for empty / non-string input.
    """
    if not value or not isinstance(value, str):
        return None
    cleaned = _REG_NO_WHITESPACE_RE.sub("", value).strip().upper()
    return cleaned or None


def _design_sort_key(design: CanonicalDesign) -> int:
    """Numeric sort key for designs/views (so design 10 comes after 9
    not 1). Falls back to a sentinel for non-numeric values."""
    try:
        return int(design.no)
    except (ValueError, TypeError):
        return 1 << 30


def _view_sort_key(view: CanonicalDesignView) -> int:
    try:
        return int(view.view_no)
    except (ValueError, TypeError):
        return 1 << 30


def _merge_design_views(
    cd_views: List[CanonicalDesignView],
    pdf_views: List[CanonicalDesignView],
) -> List[CanonicalDesignView]:
    """Merge two view lists for the same design.

    CD views win on duplicate ``view_no``. PDF-only views (the rare
    case where PDF has a view CD didn't ship) are appended. Output is
    sorted by ``int(view_no)``.
    """
    by_no: Dict[str, CanonicalDesignView] = {}
    for v in cd_views:
        if v.view_no:
            by_no[v.view_no] = v
    for v in pdf_views:
        if v.view_no and v.view_no not in by_no:
            by_no[v.view_no] = v
    return sorted(by_no.values(), key=_view_sort_key)


def _merge_designs(
    cd_designs: List[CanonicalDesign],
    pdf_designs: List[CanonicalDesign],
) -> List[CanonicalDesign]:
    """Merge two design lists by ``no``.

    For each design that appears on both sides:
      - product_name: CD wins (HSQLDB.PRODUCTNAME is authoritative;
        PDF's ``product_name_tr`` is OCR-noisy)
      - views: ``_merge_design_views`` (CD wins on duplicate view_no)

    PDF-only designs (no matching CD design) are appended verbatim.
    Output sorted by ``int(no)``.
    """
    pdf_by_no: Dict[str, CanonicalDesign] = {
        d.no: d for d in pdf_designs if d.no
    }
    out: List[CanonicalDesign] = []
    seen: set = set()

    for cd_design in cd_designs:
        if not cd_design.no or cd_design.no in seen:
            continue
        seen.add(cd_design.no)
        pdf_match = pdf_by_no.get(cd_design.no)
        if pdf_match is None:
            out.append(cd_design)
            continue
        out.append(CanonicalDesign(
            no=cd_design.no,
            product_name=cd_design.product_name or pdf_match.product_name,
            views=_merge_design_views(cd_design.views, pdf_match.views),
        ))

    for pdf_design in pdf_designs:
        if pdf_design.no and pdf_design.no not in seen:
            seen.add(pdf_design.no)
            out.append(pdf_design)

    return sorted(out, key=_design_sort_key)


def _merge_attorneys(
    cd_attorney: Optional[Dict[str, Any]],
    pdf_attorney: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Combine CD's ``{no, name, title, address}`` and PDF's
    ``{name, firm}`` attorneys into a single dict.

    CD wins on every shared key (currently just ``name``); PDF
    contributes its unique ``firm`` field. Returns ``None`` only when
    both sides are empty/missing.
    """
    if not cd_attorney and not pdf_attorney:
        return None
    out: Dict[str, Any] = {}
    if pdf_attorney:
        out.update(pdf_attorney)  # PDF first (so CD overrides on conflict)
    if cd_attorney:
        out.update(cd_attorney)
    return out or None


def merge_records(
    cd: CanonicalDesignRecord,
    pdf: CanonicalDesignRecord,
) -> CanonicalDesignRecord:
    """Merge two paired records (one from CD, one from PDF) into one.

    Precedence: **CD wins on every overlapping field**; PDF fills gaps
    where CD is ``None`` / empty. Caller must guarantee pairing —
    this function is pure precedence application and doesn't validate
    that the two records describe the same bulletin entry.

    Field-level precedence:

      Scalar fields           CD or PDF (CD-first via ``or``)
      type                    CD-only in practice; defensive ``cd or pdf``
      section                 PDF-only
      locarno_codes           CD wins; PDF fills if CD list is empty
      attorney                Merged (see ``_merge_attorneys``)
      holders / designers /
       priorities             CD wins; PDF fills if CD list is empty
      designs                 Merged by design ``no`` (see ``_merge_designs``)
      hague_reference /
       page_range /
       deferred_publication   PDF-only fields
      source_format           "BOTH"
    """
    return CanonicalDesignRecord(
        application_no=cd.application_no or pdf.application_no,
        registration_no=cd.registration_no or pdf.registration_no,
        application_date=cd.application_date or pdf.application_date,
        registration_date=cd.registration_date or pdf.registration_date,
        design_count=(
            cd.design_count if cd.design_count is not None else pdf.design_count
        ),
        type=cd.type or pdf.type,
        section=pdf.section,
        locarno_codes=cd.locarno_codes if cd.locarno_codes else list(pdf.locarno_codes),
        attorney=_merge_attorneys(cd.attorney, pdf.attorney),
        holders=cd.holders if cd.holders else list(pdf.holders),
        designers=cd.designers if cd.designers else list(pdf.designers),
        priorities=cd.priorities if cd.priorities else list(pdf.priorities),
        designs=_merge_designs(cd.designs, pdf.designs),
        hague_reference=pdf.hague_reference,
        page_range=list(pdf.page_range) if pdf.page_range else None,
        deferred_publication=pdf.deferred_publication,
        source_format="BOTH",
    )


# ---------------------------------------------------------------------------
# Step 3.5 — reconcile_metadata orchestrator
# ---------------------------------------------------------------------------


def _normalise_bulletin_no(raw: Any) -> Optional[str]:
    """Coerce a bulletin_no value to a comparable string. CD ships str
    (``"240"``), PDF ships int (``240``); both need to compare equal
    when checking for cross-doc consistency."""
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _utcnow_iso() -> str:
    """Current UTC time as ISO 8601 with seconds precision."""
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _is_hague_record(record: CanonicalDesignRecord) -> bool:
    """A record is Hague-shaped when either:
      - PDF tagged it with ``section == "hague"``, or
      - its application_no carries the ``DM/`` prefix (CD-side dossier
        for an international design — IDDOSSIER ships application_no
        like ``"DM/086402"``)."""
    if record.section == "hague":
        return True
    appno = record.application_no
    if appno and appno.upper().startswith("DM/"):
        return True
    return False


def _pair_key(record: CanonicalDesignRecord) -> Optional[str]:
    """Compute the cross-source pairing key for one record.

    TR records pair by ``application_no``. Hague records pair by the
    normalised registration_no (PDF's ``"DM 244882"`` and CD's
    ``"DM244882"``/``"DM 244882"`` both collapse to ``"DM244882"``).

    Returns ``None`` when no usable key is available — the record
    will pass through as a single-side entry without any pairing
    attempt.
    """
    if _is_hague_record(record):
        return _normalise_registration_no(record.registration_no)
    return record.application_no


def _record_to_dict(rec: CanonicalDesignRecord) -> Dict[str, Any]:
    """Serialise a record into JSON-ready dict, dropping None scalars
    so the merged JSON stays tidy. Empty lists are preserved (downstream
    distinguishes ``"no holders"`` from ``"field absent"``).

    asdict recursively converts nested CanonicalDesign / CanonicalDesignView
    dataclasses to dicts, which is exactly the shape we want on disk.
    """
    out = asdict(rec)
    return {k: v for k, v in out.items() if v is not None}


def _build_stats(records: List[CanonicalDesignRecord]) -> Dict[str, Any]:
    """Aggregate stats for the reconciler's stats block.

    The headline number is ``by_source_format`` — the CD/PDF/BOTH
    distribution tells the operator how much of the issue actually
    came from a paired merge vs a single-side passthrough.
    ``views_by_source`` is a sanity check: every cd-tagged view should
    correspond to a real file under cd_images/, every pdf-tagged
    view to a file under images/.
    """
    by_source_format = {"CD": 0, "PDF": 0, "BOTH": 0}
    by_section: Dict[str, int] = {}
    designs_total = 0
    views_total = 0
    views_by_source = {"cd": 0, "pdf": 0, "none": 0}

    for rec in records:
        by_source_format[rec.source_format] = (
            by_source_format.get(rec.source_format, 0) + 1
        )
        section = rec.section or "unknown"
        by_section[section] = by_section.get(section, 0) + 1
        designs_total += len(rec.designs)
        for design in rec.designs:
            for view in design.views:
                views_total += 1
                source = view.image_source or "none"
                views_by_source[source] = views_by_source.get(source, 0) + 1

    return {
        "records": len(records),
        "by_source_format": by_source_format,
        "by_section": by_section,
        "designs_total": designs_total,
        "views_total": views_total,
        "views_by_source": views_by_source,
    }


def reconcile_metadata(
    cd_doc: Optional[Dict[str, Any]] = None,
    pdf_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge two per-issue metadata docs into one canonical document.

    Inputs are dicts as produced by ``load_cd_metadata`` / ``load_pdf_metadata``.
    Either may be ``None`` for single-side reconcile:
      - CD-only:  legacy bulletins where no modern PDF was collected
      - PDF-only: modern bulletins where no CD was ever produced

    Output document shape::

        {
          "bulletin_no":   "240",
          "bulletin_date": "2016-03-09",
          "source_archive": "240_CD.rar",  # from cd_doc when present
          "source_pdf":    "bulletin.pdf", # from pdf_doc when present
          "reconciled_at": "2026-...",
          "stats":   { ... see _build_stats ... },
          "records": [ canonical record dict, ... ],   # sorted
          "cd_annotations": [ ... passthrough from cd_doc ... ],
        }

    Pairing key:
      - TR records: ``application_no``
      - Hague:      normalised ``registration_no`` (whitespace/case
                    insensitive)

    Raises:
      - ``ValueError`` when both ``cd_doc`` and ``pdf_doc`` are ``None``.
      - ``ValueError`` when both docs are present but their
        ``bulletin_no`` values disagree (silently merging two
        different bulletins would be catastrophic).

    PDF events (``events.json``) are intentionally NOT included in the
    output — they live as a sibling file in the TS folder. CD's
    ``annotations`` array passes through unchanged as
    ``cd_annotations`` per the locked Q3 decision.
    """
    if cd_doc is None and pdf_doc is None:
        raise ValueError(
            "reconcile_metadata requires at least one of cd_doc / pdf_doc"
        )

    cd_bulletin = _normalise_bulletin_no(cd_doc.get("bulletin_no")) if cd_doc else None
    pdf_bulletin = _normalise_bulletin_no(pdf_doc.get("bulletin_no")) if pdf_doc else None

    if cd_doc is not None and pdf_doc is not None:
        if cd_bulletin != pdf_bulletin:
            raise ValueError(
                f"bulletin_no mismatch: CD={cd_bulletin!r} PDF={pdf_bulletin!r} "
                f"(reconcile would silently produce wrong overlap; aborting)"
            )

    cd_records = (
        [normalize_cd_dossier(d) for d in cd_doc.get("dossiers", []) or []]
        if cd_doc is not None else []
    )
    pdf_records = (
        [normalize_pdf_record(r) for r in pdf_doc.get("records", []) or []]
        if pdf_doc is not None else []
    )

    cd_by_key: Dict[str, CanonicalDesignRecord] = {}
    for rec in cd_records:
        key = _pair_key(rec)
        if key:
            cd_by_key[key] = rec

    merged: List[CanonicalDesignRecord] = []
    matched_keys: set = set()

    for pdf_rec in pdf_records:
        key = _pair_key(pdf_rec)
        cd_match = cd_by_key.get(key) if key else None
        if cd_match is not None:
            merged.append(merge_records(cd_match, pdf_rec))
            matched_keys.add(key)
        else:
            merged.append(pdf_rec)  # source_format already "PDF"

    for cd_rec in cd_records:
        key = _pair_key(cd_rec)
        if key and key in matched_keys:
            continue
        merged.append(cd_rec)  # source_format already "CD"

    # Sort for deterministic output. Application_no first (most records
    # have one); registration_no as the tiebreak for Hague-only records.
    merged.sort(key=lambda r: (r.application_no or "", r.registration_no or ""))

    bulletin_no = cd_bulletin or pdf_bulletin
    bulletin_date = (
        (cd_doc.get("bulletin_date") if cd_doc else None)
        or (pdf_doc.get("bulletin_date") if pdf_doc else None)
    )
    source_archive = cd_doc.get("source_archive") if cd_doc else None
    source_pdf = pdf_doc.get("source") if pdf_doc else None
    cd_annotations = list(cd_doc.get("annotations", []) or []) if cd_doc else []

    return {
        "bulletin_no":   bulletin_no,
        "bulletin_date": bulletin_date,
        "source_archive": source_archive,
        "source_pdf":    source_pdf,
        "reconciled_at": _utcnow_iso(),
        "stats":         _build_stats(merged),
        "records":       [_record_to_dict(r) for r in merged],
        "cd_annotations": cd_annotations,
    }

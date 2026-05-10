"""Tasarım — merge cd_metadata.json into metadata.json (PDF-shape).

Stage-3 follow-up: where the original reconcile_tasarim writes a separate
``merged_metadata.json`` (canonical-shape), this module writes the merge
result back into ``metadata.json`` itself, in the **PDF-extracted shape**
that ``ingest_designs`` already knows how to parse.

Why this exists:
  - ``ingest_designs`` and ``embeddings_tasarim`` only read ``metadata.json``.
  - CD-only folders (no PDF) had no ``metadata.json`` -> 14 folders × ~3K
    designs each were not in DB and had no embeddings.
  - PDF+CD folders had ``metadata.json`` from the PDF parse, so DB ended
    up with PDF-derived field values even where CD had cleaner ones.

Overwriting ``metadata.json`` with a merged document closes both gaps in
one shot: ingest and embeddings keep their current code, but read a
file that already carries CD-prefered field values plus the CD-only
folders' synthesized records.

Top-level + per-record shape mirrors what ``pdf_extract_tasarim`` produces.
Embeddings are preserved across the merge by indexing the existing
``metadata.json`` by canonical ``image_path`` (the canonical
``{appno_norm}/{d}_{v}.jpg`` key — unique per issue).

Built incrementally. Each helper has its own unit-test block.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from pipeline.reconcile_tasarim import (  # noqa: E402
    _clean_str,
    _dmy_to_iso,
    _normalise_registration_no,
    _parse_design_count,
    _utcnow_iso,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [TASARIM-MERGE-META] - %(levelname)s - %(message)s",
)
logger = logging.getLogger("turkpatent.tasarim_merge_meta")


# ---------------------------------------------------------------------------
# Per-row field translators: CD shape -> PDF shape
# ---------------------------------------------------------------------------

def _cd_holder_to_pdf_applicant(holder: Dict[str, Any]) -> Dict[str, Any]:
    """Translate one CD ``holders[]`` entry into a PDF ``applicants[]`` entry.

    PDF applicants ship ``{name, id, address, country}``; CD holders ship
    ``{client_no, title, address, city, country}``. Mapping:

      ``title``     -> ``name``           (CD's entity-name field)
      ``client_no`` -> ``id``             (TPECLIENT id, same field on both)
      ``address``   -> ``address``        (concat with city when both present)
      ``country``   -> ``country``

    CD's ``city`` field has no PDF counterpart at the design level.
    Append it to ``address`` (comma-separated) so the data isn't lost
    when the row gets ingested into the trademarks-side ``holders``
    table where it might still be useful for entity dedup.
    """
    if not isinstance(holder, dict):
        return {}
    parts = [_clean_str(holder.get("address")), _clean_str(holder.get("city"))]
    address = ", ".join(p for p in parts if p) or None
    return {
        "name":    _clean_str(holder.get("title")),
        "id":      _clean_str(holder.get("client_no")),
        "address": address,
        "country": _clean_str(holder.get("country")),
    }


def _cd_designer_to_pdf_designer(designer: Dict[str, Any]) -> Dict[str, Any]:
    """Translate one CD ``designers[]`` entry to PDF ``designers[]``.

    PDF designers are ``{name}``-only (the only field
    ``pdf_extract_tasarim`` parses). CD ships address + country as well
    but those don't have a corresponding PDF field, so they're dropped
    here — the canonical ``designers`` column in the DB is ``TEXT[]``
    of names, no per-designer detail is stored downstream.
    """
    if not isinstance(designer, dict):
        return {}
    return {"name": _clean_str(designer.get("name"))}


def _cd_attorney_to_pdf_attorney(
    attorney: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Translate a CD attorney ``{no, name, title, address}`` block to
    PDF's ``{name, firm}`` shape.

    CD's IDDOSSIER ATTORNEYNAME is denormalized — usually the natural
    person's name with the firm in trailing parens, e.g.
    ``"IŞIK ÖZDOĞAN (MOROĞLU ARSEVEN DANIŞMANLIK A.Ş.)"``. We don't
    parse the firm out here; instead the merge-with-PDF case lets PDF's
    pre-split (name, firm) win, so this synthesis path only runs for
    CD-only records where there's no cleaner PDF parse to defer to.

    Returns ``None`` when the attorney block is missing or has no
    usable name field (avoids decorating the record with an empty
    ``"attorney": {"name": null}`` block).
    """
    if not isinstance(attorney, dict):
        return None
    name = _clean_str(attorney.get("name"))
    if not name:
        return None
    return {"name": name, "firm": None}


def _cd_view_to_pdf_view(view: Dict[str, Any]) -> Dict[str, Any]:
    """Translate one CD view ``{view_no, image_path}`` to PDF view shape.

    PDF views carry PyMuPDF artefacts (``page``, ``image_xref``,
    ``bbox``) that don't exist on the CD side. Set them to ``None``.
    The canonical ``image_path`` and the ``image_source="cd"`` tag are
    preserved exactly so D.1's resolve_view_image_path keeps working
    and embeddings get carried over by ``_index_existing_embeddings``.
    """
    if not isinstance(view, dict):
        return {}
    image_path = _clean_str(view.get("image_path"))
    return {
        "view_index":   int(view.get("view_no") or 1),
        "page":         None,
        "image_xref":   None,
        "bbox":         None,
        "image_path":   image_path,
        "image_source": "cd" if image_path else None,
    }


def _cd_design_to_pdf_design(design: Dict[str, Any]) -> Dict[str, Any]:
    """Translate one CD design dict to PDF design shape.

    Field renames:
      ``no``           -> ``design_index`` (str -> int)
      ``product_name`` -> ``product_name_tr`` (trailing whitespace stripped)
      ``views``        -> ``views`` (each translated by ``_cd_view_to_pdf_view``)
    """
    if not isinstance(design, dict):
        return {}
    return {
        "design_index":    int(design.get("no") or 1),
        "product_name_tr": (_clean_str(design.get("product_name")) or ""),
        "views": [
            _cd_view_to_pdf_view(v)
            for v in (design.get("views") or [])
            if isinstance(v, dict)
        ],
    }


# ---------------------------------------------------------------------------
# CD-only synthesis: build a PDF-shape record from a CD dossier
# ---------------------------------------------------------------------------

def synthesize_cd_record_in_pdf_shape(
    cd_dossier: Dict[str, Any],
    *,
    record_index: int,
) -> Dict[str, Any]:
    """Convert one CD ``dossiers[]`` entry into a PDF-shape ``records[]`` entry.

    Used when CD ships a dossier the PDF doesn't (the CD-only-folder
    case, plus CD dossiers with no PDF counterpart in a paired bulletin).

    ``section`` is derived from ``application_no``: ``"hague"`` for
    ``DM/...`` shapes, ``"tr_native"`` otherwise. PDF Hague records also
    populate a ``hague_reference`` block; the CD side has no equivalent
    structured data, so that field is omitted (downstream is fine — the
    DB column is JSONB nullable).

    The synthesized record carries:
      - all scalar dossier fields (renamed)
      - locarno codes (renamed locarno_codes -> locarno_classes)
      - applicants / designers / attorney (translated)
      - designs (with views; each view tagged image_source="cd")
      - empty priorities (CD ships none)
      - empty page_range (CD has no PDF page knowledge)

    Returns a dict ready to drop into ``payload["records"]``.
    """
    appno = (cd_dossier.get("application_no") or "").strip()
    is_hague = appno.upper().startswith("DM/")
    section = "hague" if is_hague else "tr_native"

    out: Dict[str, Any] = {
        "section":       section,
        "record_index":  record_index,
        "application_no":   appno or None,
        "registration_no":  _clean_str(cd_dossier.get("register_no")),
        "filing_date":      _dmy_to_iso(cd_dossier.get("application_date")),
        "registration_date": _dmy_to_iso(cd_dossier.get("register_date")),
        "design_count":     _parse_design_count(cd_dossier.get("design_count")) or 1,
        "locarno_classes":  list(cd_dossier.get("locarno_codes") or []),
        "applicants": [
            a for a in (
                _cd_holder_to_pdf_applicant(h)
                for h in (cd_dossier.get("holders") or [])
                if isinstance(h, dict)
            ) if a.get("name") or a.get("id")
        ],
        "designers": [
            d for d in (
                _cd_designer_to_pdf_designer(item)
                for item in (cd_dossier.get("designers") or [])
                if isinstance(item, dict)
            ) if d.get("name")
        ],
        "attorney":   _cd_attorney_to_pdf_attorney(cd_dossier.get("attorney")),
        "priorities": [],
        "designs": [
            _cd_design_to_pdf_design(d)
            for d in (cd_dossier.get("designs") or [])
            if isinstance(d, dict)
        ],
        "page_range": [],
    }
    return out


# ---------------------------------------------------------------------------
# Top-level merge: paired (PDF, CD) records and the BOTH-folder orchestrator
# ---------------------------------------------------------------------------

def _merge_design_views(
    pdf_views: List[Dict[str, Any]],
    cd_views: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge two view lists for the same design. CD wins on duplicate
    ``view_index``; PDF-only views are kept; output sorted numerically.

    CD views are translated to PDF shape on insertion so the consumer
    sees a uniform view dict regardless of source.
    """
    by_idx: Dict[int, Dict[str, Any]] = {}
    for v in pdf_views or []:
        if not isinstance(v, dict):
            continue
        idx = v.get("view_index")
        if isinstance(idx, int):
            by_idx[idx] = v
    for v in cd_views or []:
        if not isinstance(v, dict):
            continue
        try:
            cd_idx = int(v.get("view_no") or 0)
        except (ValueError, TypeError):
            continue
        if cd_idx > 0:
            by_idx[cd_idx] = _cd_view_to_pdf_view(v)  # CD wins on overlap
    return [by_idx[k] for k in sorted(by_idx)]


def _merge_designs(
    pdf_designs: List[Dict[str, Any]],
    cd_designs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge per-design lists by ``design_index``.

    Per-design rules:
      - Both sides present: keep PDF's design dict, override product_name_tr
        with CD's product_name when CD's is non-empty (CD's IDDESIGN.PRODUCTNAME
        is the authoritative HSQLDB value), and union views via _merge_design_views.
      - PDF only: kept verbatim.
      - CD only: translated via _cd_design_to_pdf_design.

    Output sorted by design_index.
    """
    pdf_by_idx: Dict[int, Dict[str, Any]] = {}
    for d in pdf_designs or []:
        if not isinstance(d, dict):
            continue
        idx = d.get("design_index")
        if isinstance(idx, int):
            pdf_by_idx[idx] = d

    cd_by_idx: Dict[int, Dict[str, Any]] = {}
    for d in cd_designs or []:
        if not isinstance(d, dict):
            continue
        try:
            cd_idx = int(d.get("no") or 0)
        except (ValueError, TypeError):
            continue
        if cd_idx > 0:
            cd_by_idx[cd_idx] = d

    out: List[Dict[str, Any]] = []
    for idx in sorted(set(pdf_by_idx) | set(cd_by_idx)):
        pdf_d = pdf_by_idx.get(idx)
        cd_d = cd_by_idx.get(idx)
        if pdf_d and cd_d:
            cd_name = _clean_str(cd_d.get("product_name"))
            merged = dict(pdf_d)
            if cd_name:
                merged["product_name_tr"] = cd_name
            merged["views"] = _merge_design_views(
                pdf_d.get("views") or [], cd_d.get("views") or [],
            )
            out.append(merged)
        elif pdf_d:
            out.append(pdf_d)
        else:
            out.append(_cd_design_to_pdf_design(cd_d))  # type: ignore[arg-type]
    return out


def merge_pdf_record_with_cd_dossier(
    pdf_record: Dict[str, Any],
    cd_dossier: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply CD-wins precedence to a paired (PDF, CD) record.

    Output is PDF-shape (so ingest_designs keeps reading without a
    code change). Caller must guarantee the inputs describe the same
    application — pair detection happens in ``merge_to_pdf_shape``.

    Precedence rules (CD wins where present and non-empty; PDF retained
    otherwise):
      - registration_no, filing_date, registration_date, design_count
      - locarno_classes (CD's clean list overrides PDF's regex parse)
      - applicants (CD's HSQLDB rows are cleaner than PDF's regex parse)
      - designers (same reason)
      - attorney: CD's name + PDF's firm. CD's name is the cleaner
        IDDOSSIER.ATTORNEYNAME; PDF's firm comes from the bulletin's
        pre-split parens form, which CD doesn't preserve, so we keep it.
      - designs (per-design merge via _merge_designs; CD wins on shared
        product_name and on shared view_index)

    PDF-only fields preserved from pdf_record verbatim:
      - section, record_index, page_range, hague_reference,
        deferred_publication, priorities
    """
    out = dict(pdf_record)

    cd_reg = _clean_str(cd_dossier.get("register_no"))
    if cd_reg:
        out["registration_no"] = cd_reg

    cd_filing = _dmy_to_iso(cd_dossier.get("application_date"))
    if cd_filing:
        out["filing_date"] = cd_filing

    cd_reg_date = _dmy_to_iso(cd_dossier.get("register_date"))
    if cd_reg_date:
        out["registration_date"] = cd_reg_date

    cd_count = _parse_design_count(cd_dossier.get("design_count"))
    if cd_count is not None:
        out["design_count"] = cd_count

    cd_locarno = list(cd_dossier.get("locarno_codes") or [])
    if cd_locarno:
        out["locarno_classes"] = cd_locarno

    cd_applicants = [
        a for a in (
            _cd_holder_to_pdf_applicant(h)
            for h in (cd_dossier.get("holders") or [])
            if isinstance(h, dict)
        ) if a.get("name") or a.get("id")
    ]
    if cd_applicants:
        out["applicants"] = cd_applicants

    cd_designers = [
        d for d in (
            _cd_designer_to_pdf_designer(item)
            for item in (cd_dossier.get("designers") or [])
            if isinstance(item, dict)
        ) if d.get("name")
    ]
    if cd_designers:
        out["designers"] = cd_designers

    cd_attorney = cd_dossier.get("attorney") or {}
    cd_atty_name = _clean_str(cd_attorney.get("name")) if isinstance(cd_attorney, dict) else None
    if cd_atty_name:
        pdf_attorney = pdf_record.get("attorney") or {}
        pdf_firm = pdf_attorney.get("firm") if isinstance(pdf_attorney, dict) else None
        out["attorney"] = {"name": cd_atty_name, "firm": pdf_firm}

    out["designs"] = _merge_designs(
        pdf_record.get("designs") or [],
        cd_dossier.get("designs") or [],
    )
    return out


def _coerce_bulletin_no(value: Any) -> Any:
    """CD ships bulletin_no as ``"240"`` (str); PDF as ``240`` (int).
    For consistency in the merged top-level field, coerce digit-strings
    to int but pass anything else through untouched."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def merge_to_pdf_shape(
    pdf_doc: Optional[Dict[str, Any]] = None,
    cd_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge a per-issue (PDF, CD) doc pair into one PDF-shape document.

    Either input may be ``None`` for single-side merge:
      - ``pdf_doc=None``: synthesize records from CD dossiers (CD-only folder)
      - ``cd_doc=None``:  pass PDF through unchanged but tag merge_source

    Pairing key inside an issue:
      - TR records: ``application_no``
      - Hague:      normalised ``registration_no`` (whitespace/case insensitive)

    The output adds three top-level fields beyond the PDF shape:
      - ``merge_source``: ``"pdf_only"`` | ``"cd_only"`` | ``"both"``
      - ``merged_at``:    ISO timestamp the merge was performed

    Raises ``ValueError`` when both inputs are ``None``.
    """
    if pdf_doc is None and cd_doc is None:
        raise ValueError(
            "merge_to_pdf_shape requires at least one of pdf_doc / cd_doc"
        )

    merged_at = _utcnow_iso()

    if pdf_doc is None:
        cd_doc = cd_doc or {}
        records = [
            synthesize_cd_record_in_pdf_shape(d, record_index=i + 1)
            for i, d in enumerate(cd_doc.get("dossiers") or [])
            if isinstance(d, dict)
        ]
        return {
            "bulletin_no":   _coerce_bulletin_no(cd_doc.get("bulletin_no")),
            "bulletin_date": cd_doc.get("bulletin_date"),
            "source":        cd_doc.get("source_archive"),
            "page_count":    0,
            "record_count":  len(records),
            "records":       records,
            "merge_source":  "cd_only",
            "merged_at":     merged_at,
        }

    if cd_doc is None:
        out = dict(pdf_doc)
        out["merge_source"] = "pdf_only"
        out["merged_at"]    = merged_at
        return out

    # BOTH case — pair by app_no (TR) or normalised registration_no (Hague)
    cd_by_key: Dict[str, Dict[str, Any]] = {}
    for d in (cd_doc.get("dossiers") or []):
        if not isinstance(d, dict):
            continue
        appno = (d.get("application_no") or "").strip()
        is_hague = appno.upper().startswith("DM/")
        key = (
            _normalise_registration_no(d.get("register_no"))
            if is_hague
            else appno
        )
        if key:
            cd_by_key[key] = d

    matched_keys: set = set()
    out_records: List[Dict[str, Any]] = []

    for pdf_rec in (pdf_doc.get("records") or []):
        if not isinstance(pdf_rec, dict):
            continue
        if pdf_rec.get("section") == "hague":
            key = _normalise_registration_no(pdf_rec.get("registration_no"))
        else:
            key = pdf_rec.get("application_no")
        cd_match = cd_by_key.get(key) if key else None
        if cd_match is not None:
            out_records.append(merge_pdf_record_with_cd_dossier(pdf_rec, cd_match))
            matched_keys.add(key)
        else:
            out_records.append(pdf_rec)

    next_idx = max(
        (r.get("record_index", 0) for r in out_records if isinstance(r, dict)),
        default=0,
    ) + 1
    for cd_d in (cd_doc.get("dossiers") or []):
        if not isinstance(cd_d, dict):
            continue
        appno = (cd_d.get("application_no") or "").strip()
        is_hague = appno.upper().startswith("DM/")
        key = (
            _normalise_registration_no(cd_d.get("register_no"))
            if is_hague
            else appno
        )
        if key in matched_keys:
            continue
        out_records.append(synthesize_cd_record_in_pdf_shape(cd_d, record_index=next_idx))
        next_idx += 1

    out = dict(pdf_doc)
    out["records"]       = out_records
    out["record_count"]  = len(out_records)
    out["merge_source"]  = "both"
    out["merged_at"]     = merged_at
    return out


# ---------------------------------------------------------------------------
# Embedding preservation across merge
# ---------------------------------------------------------------------------

def _index_existing_embeddings(pdf_doc: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build an ``image_path -> embeddings`` index from an existing
    metadata.json so the merge can preserve previously-computed
    embeddings without forcing a full re-embed.

    The canonical ``image_path`` shape ``{appno_norm}/{d}_{v}.{ext}`` is
    unique per issue and survives the merge unchanged (CD and PDF agree
    on this key by design), so it's a reliable bridge between the
    pre-merge view and its post-merge counterpart even when
    ``application_no`` / ``design_index`` / ``view_index`` change shape
    (e.g. PDF Hague records have null ``application_no``).
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(pdf_doc, dict):
        return out
    for r in pdf_doc.get("records") or []:
        if not isinstance(r, dict):
            continue
        for d in r.get("designs") or []:
            if not isinstance(d, dict):
                continue
            for v in d.get("views") or []:
                if not isinstance(v, dict):
                    continue
                ip = v.get("image_path")
                emb = v.get("embeddings")
                if ip and isinstance(emb, dict) and emb:
                    out[ip] = emb
    return out


def _attach_existing_embeddings(
    merged_doc: Dict[str, Any],
    embeddings_by_image_path: Dict[str, Dict[str, Any]],
) -> int:
    """Re-attach embeddings to merged-doc views by ``image_path`` lookup.

    Mutates ``merged_doc`` in place. Returns the number of views that
    received an embedding (useful for the CLI report so the operator
    can see how many embeddings carried through unchanged vs how many
    will need fresh computation in stage 5).
    """
    if not embeddings_by_image_path:
        return 0
    attached = 0
    for r in merged_doc.get("records") or []:
        if not isinstance(r, dict):
            continue
        for d in r.get("designs") or []:
            if not isinstance(d, dict):
                continue
            for v in d.get("views") or []:
                if not isinstance(v, dict):
                    continue
                ip = v.get("image_path")
                if ip and ip in embeddings_by_image_path:
                    v["embeddings"] = embeddings_by_image_path[ip]
                    attached += 1
    return attached

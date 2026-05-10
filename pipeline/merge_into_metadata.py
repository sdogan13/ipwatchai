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

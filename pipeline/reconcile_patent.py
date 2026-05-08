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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


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

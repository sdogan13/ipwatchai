"""Tasarım (industrial design) bulletin PDF metadata extractor.

Sister to ``pdf_extract.py`` (Marka). Reads ``bulletins/Tasarim/TS_*/bulletin.pdf``
and produces ``metadata.json`` + an ``images/`` folder per issue.

The parser handles five sub-sections in the same PDF:

  * ``tr_native``         — main TR design body (with images per design view)
  * ``deferred``          — yayım erteleme talepli (no images, no designer)
  * ``deferred_lifted``   — yayım erteleme talebi kaldırılan (full images)
  * ``republished``       — yeniden yayınlanan
  * ``hague``             — Lahey international designs (different INID layout)

Records are INID-coded. TR boundary is ``(21) YYYY/NNNNNN``. Hague boundary is
``WIPO Bülten No:`` followed by ``(11) DM ###``. View labels follow the
INID block as ``N.M product_name`` lines and are paired with image rects on
the same page by bbox proximity.

CLI::

    python pdf_extract_tasarim.py                      # all issues with bulletin.pdf, missing metadata.json
    python pdf_extract_tasarim.py --issue TS_483_2026-04-24
    python pdf_extract_tasarim.py --bulletins-root C:/path/Tasarim
    python pdf_extract_tasarim.py --force             # re-parse even if metadata.json exists
"""

import argparse
import json
import logging
import re
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_DIR = _LOCAL_PROJECT_ROOT / "bulletins" / "Tasarim"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [TASARIM-EXTRACT] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.tasarim_extract")


def _get_fitz():
    """Lazy import of PyMuPDF so unit tests don't need the C library at collection time."""
    import fitz
    return fitz


# ---------------------------------------------------------------------------
# Regex constants (used by both pure helpers and the streaming parser)
# ---------------------------------------------------------------------------

# Record boundaries
TR_APPNO_RE = re.compile(r"\(21\)\s*(\d{4}/\d{3,6})")
HAGUE_HEADER_RE = re.compile(r"WIPO\s+B[üu]lten\s+No\s*:\s*(\d+/\d{4})")
HAGUE_REGNO_RE = re.compile(r"\(11\)\s*(DM\s*\d+)")

# INID block tokenizer — codes are 2 or 3 digits, plus the special (ES) deferral marker.
# Excludes 7-digit TPECLIENT IDs like (7610221) and firm names like (MOROĞLU ARSEVEN ...).
INID_TOKEN_RE = re.compile(r"\((\d{2,3}|ES)\)")

# Date formats
TR_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")

# Locarno class entry: 26-05 or 06.01
LOCARNO_RE = re.compile(r"\b(\d{2}[-.]\d{2})\b")

# Priority entry: "27.06.2025  30/010,422  US"
PRIORITY_RE = re.compile(
    r"(\d{2}\.\d{2}\.\d{4})\s+([^\s][^\n]*?)\s+([A-Z]{2})\b"
)

# Deferred-publication marker: "(ES) 30 Ay"
ES_RE = re.compile(r"\(ES\)\s*(\d+)\s*Ay")

# View label: "1.1 Lamba"  (design_idx . view_idx product_name)
VIEW_LABEL_RE = re.compile(r"^(\d+)\.(\d+)\s+(.+?)\s*$")

# Bulletin metadata from body footer
FOOTER_BULLETIN_RE = re.compile(r"(\d{4})\s*/\s*(\d{3,4})\s+Tasar[ıi]mlar\s+B[üu]lteni")
FOOTER_DATE_RE = re.compile(r"Yay[ıi]n\s+Tarihi\s*:?\s*(\d{2})\.(\d{2})\.(\d{4})")

# Section header detection (matched against per-page text, case-insensitive contains)
# Order matters: longer, more specific phrases come first so they win against
# substring overlaps like "YAYIN ERTELEME TALEBİ KALDIRILAN" vs "YAYIN ERTELEME TALEPLİ".
SECTION_MARKERS: List[Tuple[str, str]] = [
    ("YAYIN ERTELEME TALEBİ KALDIRILAN", "deferred_lifted"),
    ("YAYIN ERTELEME TALEPLİ", "deferred"),
    ("YENIDEN YAYINLANAN", "republished"),
    ("YENİDEN YAYINLANAN", "republished"),
    ("İLAN OLUNMUŞTUR", "announcement"),
    ("ILAN OLUNMUSTUR", "announcement"),
    ("LAHEY", "hague"),
    ("WIPO BÜLTEN NO", "hague"),
    ("WIPO BULTEN NO", "hague"),
    ("DÜZELTMELER", "correction"),
    ("DUZELTMELER", "correction"),
]

KNOWN_SECTIONS = {
    "tr_native", "deferred", "deferred_lifted", "republished",
    "announcement", "hague", "correction",
}
TR_SECTIONS = {"tr_native", "deferred", "deferred_lifted", "republished"}

# Hague designated-states INID
HAGUE_STATES_RE = re.compile(r"\(81\)\s*(?:[IVX]+\.\s*)?([A-Z]{2}(?:\s*,\s*[A-Z]{2})*)")


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Applicant:
    name: str
    id: Optional[str] = None
    address: Optional[str] = None
    country: Optional[str] = None


@dataclass
class Designer:
    name: str


@dataclass
class Attorney:
    name: str
    firm: Optional[str] = None


@dataclass
class Priority:
    date: str
    number: str
    country: str


@dataclass
class View:
    view_index: int
    page: int
    image_xref: Optional[int] = None
    bbox: Optional[List[float]] = None
    image_path: Optional[str] = None


@dataclass
class Design:
    design_index: int
    product_name_tr: str
    views: List[View] = field(default_factory=list)


@dataclass
class HagueRef:
    wipo_bulletin: Optional[str] = None
    designated_states: List[str] = field(default_factory=list)
    product_name_en: Optional[str] = None


@dataclass
class DeferredPub:
    period_months: int


@dataclass
class TasarimRecord:
    section: str
    record_index: int
    application_no: Optional[str] = None
    registration_no: Optional[str] = None
    filing_date: Optional[str] = None
    registration_date: Optional[str] = None
    design_count: int = 1
    locarno_classes: List[str] = field(default_factory=list)
    applicants: List[Applicant] = field(default_factory=list)
    designers: List[Designer] = field(default_factory=list)
    attorney: Optional[Attorney] = None
    priorities: List[Priority] = field(default_factory=list)
    designs: List[Design] = field(default_factory=list)
    deferred_publication: Optional[DeferredPub] = None
    hague_reference: Optional[HagueRef] = None
    page_range: List[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Collapse whitespace, drop nul bytes."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\x00", "")).strip()


def normalize_tr_date(raw: str) -> Optional[str]:
    """``06.09.2024`` -> ``2024-09-06``. Returns None on no/bad match."""
    if not raw:
        return None
    m = TR_DATE_RE.search(raw)
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return f"{yyyy}-{mm}-{dd}"


def parse_inid_fields(text: str) -> Dict[str, List[str]]:
    """Tokenize an INID-coded text block.

    Returns ``{code: [value, value, ...]}``. Values are the raw text between
    the closing ``)`` of one INID code and the opening ``(`` of the next.
    Codes that recur (e.g. multiple priorities on (30), multiple designers on
    (72)) appear in the same key as a list.

    The last INID code's value is clipped at the first view-label pattern
    (``N.M ...``) so trailing ``1.1 Lamba 2.1 Lamba ...`` view labels don't
    leak into the final field's value.
    """
    out: Dict[str, List[str]] = {}
    matches = list(INID_TOKEN_RE.finditer(text))
    for idx, m in enumerate(matches):
        code = m.group(1)
        value_start = m.end()
        if idx + 1 < len(matches):
            value_end = matches[idx + 1].start()
        else:
            value_end = len(text)
            tail = text[value_start:value_end]
            view_clip = re.search(r"(?:^|\s)\d+\.\d+\s+\S", tail)
            if view_clip:
                value_end = value_start + view_clip.start()
        value = text[value_start:value_end].strip()
        out.setdefault(code, []).append(value)
    return out


def parse_locarno_list(raw: str) -> List[str]:
    """``(51)`` value -> list of normalized ``NN-NN`` strings (Locarno class-subclass)."""
    if not raw:
        return []
    found = LOCARNO_RE.findall(raw)
    out: List[str] = []
    for item in found:
        normalized = item.replace(".", "-")
        if normalized not in out:
            out.append(normalized)
    return out


def parse_priorities(raw: str) -> List[Priority]:
    """``(30)`` value -> list of ``Priority``."""
    if not raw:
        return []
    out: List[Priority] = []
    for date_raw, number, country in PRIORITY_RE.findall(raw):
        iso = normalize_tr_date(date_raw)
        if iso is None:
            continue
        out.append(Priority(date=iso, number=number.strip(), country=country))
    return out


def parse_attorney(raw: str) -> Optional[Attorney]:
    """``(74)`` value: ``NAME (FIRM)`` -> Attorney.

    Strips trailing view-label noise (``... 1.1 Pano 1.2 Pano``) defensively
    in case it leaked past the INID-block clip.
    """
    raw = clean_text(raw)
    if not raw:
        return None
    raw = re.sub(r"\s\d+\.\d+\s+.*$", "", raw).strip()
    if not raw:
        return None
    m = re.search(r"^(.*?)\s*\(([^()]+)\)", raw)
    if m:
        return Attorney(name=m.group(1).strip(), firm=m.group(2).strip())
    return Attorney(name=raw)


def parse_applicant(raw: str) -> Optional[Applicant]:
    """``(73)`` value: ``NAME (CLIENT_ID) ADDRESS COUNTRY`` -> Applicant.

    Only the first ``(NNNNN)`` group is treated as the client id; later parens
    inside the address (e.g. ``(K:5)``) are ignored.
    """
    raw = clean_text(raw)
    if not raw:
        return None

    client_match = re.search(r"\((\d{4,9})\)", raw)
    if client_match:
        name = raw[: client_match.start()].strip().rstrip(",")
        client_id = client_match.group(1)
        tail = raw[client_match.end():].strip()
        country = None
        country_match = re.search(r"\b([A-ZÇĞİÖŞÜ]{4,})\s*$", tail)
        if country_match:
            country = country_match.group(1)
            tail = tail[: country_match.start()].strip().rstrip(",")
        return Applicant(name=name, id=client_id, address=tail or None, country=country)

    return Applicant(name=raw)


def parse_designers(raw_list: Sequence[str]) -> List[Designer]:
    """``(72)`` value(s) -> list of designers. View labels mixed in are stripped."""
    out: List[Designer] = []
    for raw in raw_list:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # Strip trailing view-label noise like "1.1 Lamba" appearing on the same line
            line = VIEW_LABEL_RE.sub("", line).strip()
            if not line:
                continue
            out.append(Designer(name=line))
    return out


def parse_view_labels(text: str) -> List[Tuple[int, int, str]]:
    """Return ``(design_index, view_index, product_name)`` from view-label lines.

    Constraints:
      * design and view indices are 1–3 digits (so date fragments like
        ``04.2026`` and Locarno-list noise can't slip through)
      * product name must start with a non-digit, non-whitespace char and
        contain at least one alphabetic character (so ``33`` from a page
        footer doesn't get accepted as a name)
    """
    out: List[Tuple[int, int, str]] = []
    for line in text.splitlines():
        line = line.strip()
        for m in re.finditer(
            r"(\d{1,3})\.(\d{1,3})\s+([^\d\s][^\.]*?)(?=\s+\d{1,3}\.\d{1,3}\b|$)",
            line,
        ):
            d_idx = int(m.group(1))
            v_idx = int(m.group(2))
            name = clean_text(m.group(3))
            if name and any(c.isalpha() for c in name):
                out.append((d_idx, v_idx, name))
    return out


def detect_deferred_period(text: str) -> Optional[int]:
    m = ES_RE.search(text)
    return int(m.group(1)) if m else None


def detect_section_for_page(page_text: str, current: str) -> str:
    """Return the section a page belongs to. ``current`` is sticky unless overridden."""
    upper = page_text.upper()
    for marker, name in SECTION_MARKERS:
        if marker.upper() in upper:
            return name
    return current


def extract_bulletin_metadata(full_text: str) -> Tuple[Optional[int], Optional[str]]:
    """Return ``(bulletin_no, bulletin_date_iso)`` from any body-page footer match."""
    bulletin_no: Optional[int] = None
    bulletin_date: Optional[str] = None

    m = FOOTER_BULLETIN_RE.search(full_text)
    if m:
        try:
            bulletin_no = int(m.group(2))
        except ValueError:
            bulletin_no = None

    d = FOOTER_DATE_RE.search(full_text)
    if d:
        dd, mm, yyyy = d.groups()
        bulletin_date = f"{yyyy}-{mm}-{dd}"

    return bulletin_no, bulletin_date


def normalize_appno_for_filename(application_no: Optional[str]) -> str:
    """``2024/007254`` -> ``2024_007254``. Used for image filenames."""
    if not application_no:
        return "unknown"
    return application_no.replace("/", "_").replace(" ", "_")


def view_image_key(application_no_normalized: str, design_idx: int, view_idx: int) -> str:
    """Canonical image key shape used in metadata.json::

        ``{appno_norm}/{design_idx}_{view_idx}.jpg``

    No leading ``images/`` prefix — the prefix is ``cd_images/`` for CD
    output and ``images/`` for PDF output, and the JSON key is
    deliberately the same string in both so a future stage-3 reconciler
    can match PDF and CD images by a single key.

    Consumer resolves: ``Path(ts_folder) / "images" / image_path``
    (or ``"cd_images"`` for the CD JSON).
    """
    return f"{application_no_normalized}/{design_idx}_{view_idx}.jpg"


# ---------------------------------------------------------------------------
# Record parsing
# ---------------------------------------------------------------------------

def parse_tr_record(
    block_text: str,
    *,
    section: str,
    record_index: int,
    page_range: Tuple[int, int],
) -> TasarimRecord:
    """Parse one TR-style record (sections tr_native / deferred / deferred_lifted / republished)."""
    fields = parse_inid_fields(block_text)
    record = TasarimRecord(section=section, record_index=record_index, page_range=list(page_range))

    if "21" in fields and fields["21"]:
        m = re.search(r"\d{4}/\d{3,6}", fields["21"][0])
        if m:
            record.application_no = m.group(0)

    if "11" in fields and fields["11"]:
        record.registration_no = clean_text(fields["11"][0])

    if "15" in fields and fields["15"]:
        record.registration_date = normalize_tr_date(fields["15"][0])

    if "22" in fields and fields["22"]:
        record.filing_date = normalize_tr_date(fields["22"][0])

    if "28" in fields and fields["28"]:
        m = re.search(r"\d+", fields["28"][0])
        if m:
            try:
                record.design_count = int(m.group(0))
            except ValueError:
                pass

    if "51" in fields:
        for raw in fields["51"]:
            for cls in parse_locarno_list(raw):
                if cls not in record.locarno_classes:
                    record.locarno_classes.append(cls)

    if "73" in fields:
        for raw in fields["73"]:
            applicant = parse_applicant(raw)
            if applicant:
                record.applicants.append(applicant)

    if "72" in fields:
        record.designers = parse_designers(fields["72"])

    if "74" in fields and fields["74"]:
        record.attorney = parse_attorney(fields["74"][0])

    if "30" in fields:
        for raw in fields["30"]:
            record.priorities.extend(parse_priorities(raw))

    period = detect_deferred_period(block_text)
    if period is not None:
        record.deferred_publication = DeferredPub(period_months=period)

    # Designs are populated later when we associate view labels + images
    return record


def parse_hague_record(
    block_text: str,
    *,
    record_index: int,
    page_range: Tuple[int, int],
) -> TasarimRecord:
    """Parse one Hague-section record. Different shape: no (21), (11) is ``DM ####``,
    designated states in (81), product name in (54), no images."""
    fields = parse_inid_fields(block_text)
    record = TasarimRecord(section="hague", record_index=record_index, page_range=list(page_range))

    if "11" in fields and fields["11"]:
        record.registration_no = clean_text(fields["11"][0])

    if "15" in fields and fields["15"]:
        record.registration_date = normalize_tr_date(fields["15"][0])

    if "22" in fields and fields["22"]:
        record.filing_date = normalize_tr_date(fields["22"][0])

    if "28" in fields and fields["28"]:
        m = re.search(r"\d+", fields["28"][0])
        if m:
            try:
                record.design_count = int(m.group(0))
            except ValueError:
                pass

    if "51" in fields:
        for raw in fields["51"]:
            for cls in parse_locarno_list(raw):
                if cls not in record.locarno_classes:
                    record.locarno_classes.append(cls)

    if "73" in fields:
        for raw in fields["73"]:
            applicant = parse_applicant(raw)
            if applicant:
                record.applicants.append(applicant)

    if "72" in fields:
        record.designers = parse_designers(fields["72"])

    if "74" in fields and fields["74"]:
        record.attorney = parse_attorney(fields["74"][0])

    if "30" in fields:
        for raw in fields["30"]:
            record.priorities.extend(parse_priorities(raw))

    hague = HagueRef()
    wipo_match = HAGUE_HEADER_RE.search(block_text)
    if wipo_match:
        hague.wipo_bulletin = wipo_match.group(1)

    states_match = HAGUE_STATES_RE.search(block_text)
    if states_match:
        hague.designated_states = [s.strip() for s in states_match.group(1).split(",") if s.strip()]

    if "54" in fields and fields["54"]:
        hague.product_name_en = clean_text(fields["54"][0])

    record.hague_reference = hague
    return record


# ---------------------------------------------------------------------------
# Image extraction (TR sections only)
# ---------------------------------------------------------------------------

def _bbox_distance(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    """Minimum corner-to-corner distance between two bboxes (lower = closer)."""
    ax = (a[0] + a[2]) / 2
    ay = (a[1] + a[3]) / 2
    bx = (b[0] + b[2]) / 2
    by = (b[1] + b[3]) / 2
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _save_pixmap_jpeg(doc, xref: int, dest: Path) -> bool:
    fitz = _get_fitz()
    try:
        pix = fitz.Pixmap(doc, xref)
        if pix.n - pix.alpha >= 4:  # CMYK
            pix = fitz.Pixmap(fitz.csRGB, pix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(dest))
        return dest.exists() and dest.stat().st_size > 0
    except Exception as e:
        logger.warning("image extract failed for xref=%d: %r", xref, e)
        return False


def populate_designs_for_tr_record(
    doc,
    record: TasarimRecord,
    block_text: str,
    images_dir: Path,
    *,
    extract_images: bool = True,
) -> None:
    """Fill ``record.designs`` from the record's TEXT BLOCK (canonical view labels),
    then locate each view's bbox + image on the page. Saves JPEGs into ``images_dir``.

    Using the block as the canonical source for which (design, view) pairs belong
    to this record prevents cross-contamination when multiple records share a page.
    No-op for hague (no images).
    """
    if record.section == "hague":
        return
    if not record.page_range:
        return

    canonical_labels = parse_view_labels(block_text)
    if not canonical_labels:
        return

    # Build canonical design list from block; product name comes from the FIRST
    # view we see for each design.
    designs: Dict[int, Design] = {}
    name_for_key: Dict[Tuple[int, int], str] = {}
    for d_idx, v_idx, name in canonical_labels:
        design = designs.setdefault(d_idx, Design(design_index=d_idx, product_name_tr=name))
        if not design.product_name_tr:
            design.product_name_tr = name
        name_for_key[(d_idx, v_idx)] = name

    canonical_keys = set(name_for_key.keys())
    located_views: Dict[Tuple[int, int], View] = {}

    image_bearing = record.section in {"tr_native", "deferred_lifted", "republished"}
    appno_norm = normalize_appno_for_filename(record.application_no)

    if image_bearing:
        start_1, end_1 = record.page_range
        for page_idx in range(start_1 - 1, end_1):
            if page_idx < 0 or page_idx >= doc.page_count:
                continue
            page = doc[page_idx]

            page_text = page.get_text("text")
            page_labels = [
                (d, v, n) for (d, v, n) in parse_view_labels(page_text)
                if (d, v) in canonical_keys and (d, v) not in located_views
            ]
            if not page_labels:
                continue

            try:
                blocks = page.get_text("blocks")
            except Exception:
                blocks = []

            label_bboxes: List[Tuple[Tuple[int, int], Tuple[float, float, float, float]]] = []
            for d_idx, v_idx, _ in page_labels:
                target = f"{d_idx}.{v_idx}"
                best_block = None
                for block in blocks:
                    if len(block) < 5:
                        continue
                    btext = block[4] or ""
                    if target in btext:
                        best_block = block
                        break
                if best_block is not None:
                    bbox = (best_block[0], best_block[1], best_block[2], best_block[3])
                    label_bboxes.append(((d_idx, v_idx), bbox))

            image_rects: List[Tuple[int, Tuple[float, float, float, float]]] = []
            try:
                for info in page.get_image_info(xrefs=True):
                    xref = info.get("xref")
                    bbox = info.get("bbox")
                    if xref and bbox:
                        image_rects.append((int(xref), tuple(bbox)))
            except Exception:
                image_rects = []

            used_imgs: set = set()
            for (d_idx, v_idx), label_bbox in label_bboxes:
                best_img: Optional[Tuple[int, Tuple[float, float, float, float], float]] = None
                for img_idx, (xref, ibox) in enumerate(image_rects):
                    if img_idx in used_imgs:
                        continue
                    dist = _bbox_distance(label_bbox, ibox)
                    if best_img is None or dist < best_img[2]:
                        best_img = (img_idx, (xref, ibox), dist)
                xref_val: Optional[int] = None
                ibox: Optional[Tuple[float, float, float, float]] = None
                if best_img is not None:
                    used_imgs.add(best_img[0])
                    xref_val = best_img[1][0]
                    ibox = best_img[1][1]

                view = View(
                    view_index=v_idx,
                    page=page_idx + 1,
                    image_xref=xref_val,
                    bbox=list(ibox) if ibox else None,
                )
                if extract_images and xref_val is not None:
                    key = view_image_key(appno_norm, d_idx, v_idx)
                    dest = images_dir / key
                    if _save_pixmap_jpeg(doc, xref_val, dest):
                        view.image_path = key
                located_views[(d_idx, v_idx)] = view

    # Attach views (located + any unlocated canonical views) to their designs
    for (d_idx, v_idx), name in name_for_key.items():
        design = designs[d_idx]
        if (d_idx, v_idx) in located_views:
            design.views.append(located_views[(d_idx, v_idx)])
        elif not image_bearing:
            # Section without images (deferred): record the canonical view metadata
            design.views.append(View(view_index=v_idx, page=record.page_range[0]))
        # else: image_bearing record where the view was canonical but couldn't be
        # located on a page — drop it; the design is still represented.

    record.designs = [designs[k] for k in sorted(designs.keys())]


# ---------------------------------------------------------------------------
# Streaming PDF parser
# ---------------------------------------------------------------------------

@dataclass
class _Boundary:
    page_index: int
    char_offset: int
    kind: str  # "tr" | "hague"
    appno: Optional[str] = None


def _scan_page_texts(doc) -> List[str]:
    return [doc[i].get_text("text") for i in range(doc.page_count)]


def _section_for_page(page_texts: List[str]) -> List[str]:
    """For each page, return the section name. Sticky propagation forward.

    Section detection is suppressed until the first page that contains an
    actual record boundary (TR app-no marker). The cover, opposition notice,
    INID legend, and sequential-index pages all reference section titles in
    passing, and would otherwise flip the sticky section before the main body
    starts.
    """
    sections: List[str] = []
    main_body_started = False
    current = "tr_native"
    for text in page_texts:
        if not main_body_started:
            if TR_APPNO_RE.search(text):
                main_body_started = True
            sections.append("tr_native")
            continue
        current = detect_section_for_page(text, current)
        sections.append(current)
    return sections


def _build_global_text(page_texts: List[str]) -> Tuple[str, List[int]]:
    """Concatenate per-page text with newline separators.

    Returns ``(full_text, page_starts)`` where ``page_starts[i]`` is the
    character offset in ``full_text`` at which page ``i`` begins.
    """
    parts: List[str] = []
    page_starts: List[int] = []
    cursor = 0
    for i, text in enumerate(page_texts):
        page_starts.append(cursor)
        parts.append(text)
        cursor += len(text)
        if i + 1 < len(page_texts):
            parts.append("\n")
            cursor += 1
    return "".join(parts), page_starts


def _char_pos_to_page(pos: int, page_starts: List[int]) -> int:
    """Return the 0-indexed page that contains ``pos``."""
    lo, hi = 0, len(page_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if page_starts[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _record_start_pos(full_text: str, boundary_pos: int) -> int:
    """Extend a record's start backward to include a leading ``(11) ...`` field
    if it appears within ~100 chars before the ``(21)`` boundary on the same
    visual line. Otherwise just return ``boundary_pos``.
    """
    lookback_start = max(0, boundary_pos - 120)
    window = full_text[lookback_start:boundary_pos]
    m = re.search(r"\(11\)\s*[A-Z0-9 /-]+\s*$", window)
    if m:
        return lookback_start + m.start()
    return boundary_pos


def _find_tr_records(
    full_text: str,
    page_starts: List[int],
    sections: List[str],
) -> List[Tuple[int, int, str, str]]:
    """Return list of ``(start_pos, end_pos, appno, section)`` for each TR record.

    Slicing is done in the GLOBAL char-position space so multiple records on
    the same page are properly bounded against each other (record N starts at
    its own ``(21)`` and ends just before record N+1's ``(21)``).
    """
    matches = list(TR_APPNO_RE.finditer(full_text))
    if not matches:
        return []

    out: List[Tuple[int, int, str, str]] = []
    for i, m in enumerate(matches):
        start = _record_start_pos(full_text, m.start())
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        end = _record_start_pos(full_text, end) if end < len(full_text) else end

        page_idx = _char_pos_to_page(m.start(), page_starts)
        section = sections[page_idx] if page_idx < len(sections) else "tr_native"
        if section not in TR_SECTIONS:
            continue
        # If the record's natural end is in a different section, clip back
        end_page = _char_pos_to_page(end - 1 if end > 0 else 0, page_starts)
        if end_page < len(sections) and sections[end_page] != section:
            for p in range(page_idx, min(end_page + 1, len(sections))):
                if sections[p] != section:
                    end = page_starts[p]
                    break
        out.append((start, end, m.group(1), section))
    return out


def _find_hague_records(
    full_text: str,
    page_starts: List[int],
    sections: List[str],
) -> List[Tuple[int, int, str]]:
    matches = list(HAGUE_REGNO_RE.finditer(full_text))
    if not matches:
        return []
    out: List[Tuple[int, int, str]] = []
    for i, m in enumerate(matches):
        page_idx = _char_pos_to_page(m.start(), page_starts)
        if page_idx >= len(sections) or sections[page_idx] != "hague":
            continue
        # Hague records often have "WIPO Bülten No:" header just before (11) —
        # extend start backward to capture it.
        lookback = max(0, m.start() - 200)
        wipo_match = HAGUE_HEADER_RE.search(full_text[lookback:m.start()])
        start = lookback + wipo_match.start() if wipo_match else m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        end_page = _char_pos_to_page(end - 1 if end > 0 else 0, page_starts)
        if end_page < len(sections) and sections[end_page] != "hague":
            for p in range(page_idx, min(end_page + 1, len(sections))):
                if sections[p] != "hague":
                    end = page_starts[p]
                    break
        out.append((start, end, clean_text(m.group(1))))
    return out


def parse_pdf(
    pdf_path: Path,
    *,
    extract_images: bool = True,
    images_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Parse a Tasarım bulletin PDF into the metadata payload dict."""
    fitz = _get_fitz()
    doc = fitz.open(str(pdf_path))

    page_texts = _scan_page_texts(doc)
    sections = _section_for_page(page_texts)
    full_text, page_starts = _build_global_text(page_texts)

    bulletin_no, bulletin_date = extract_bulletin_metadata(full_text)

    tr_records_raw = _find_tr_records(full_text, page_starts, sections)
    hague_records_raw = _find_hague_records(full_text, page_starts, sections)

    records: List[TasarimRecord] = []

    for i, (start, end, appno, section) in enumerate(tr_records_raw):
        block = full_text[start:end]
        start_page = _char_pos_to_page(start, page_starts) + 1
        end_page = _char_pos_to_page(max(start, end - 1), page_starts) + 1
        record = parse_tr_record(
            block,
            section=section,
            record_index=i + 1,
            page_range=(start_page, end_page),
        )
        if not record.application_no:
            record.application_no = appno
        records.append(record)

    for i, (start, end, regno) in enumerate(hague_records_raw):
        block = full_text[start:end]
        start_page = _char_pos_to_page(start, page_starts) + 1
        end_page = _char_pos_to_page(max(start, end - 1), page_starts) + 1
        record = parse_hague_record(
            block,
            record_index=len(records) + 1,
            page_range=(start_page, end_page),
        )
        if not record.registration_no:
            record.registration_no = regno
        records.append(record)

    # Image extraction + view association for TR sections (using each record's
    # canonical block text so multi-record pages don't cross-pollinate).
    if images_dir is not None:
        # Re-derive block text per record from the boundary list we already computed.
        for i, (start, end, appno, section) in enumerate(tr_records_raw):
            if i >= len(records):
                break
            block = full_text[start:end]
            populate_designs_for_tr_record(
                doc, records[i], block, images_dir,
                extract_images=extract_images and section in {"tr_native", "deferred_lifted", "republished"},
            )

    doc.close()

    payload: Dict[str, Any] = {
        "bulletin_no": bulletin_no,
        "bulletin_date": bulletin_date,
        "source": pdf_path.name,
        "page_count": len(page_texts),
        "record_count": len(records),
        "records": [_record_to_dict(r) for r in records],
    }
    return payload


def _record_to_dict(record: TasarimRecord) -> Dict[str, Any]:
    """Serialize record stripping None/empty optionals for cleaner JSON."""
    d = asdict(record)
    if d.get("attorney") is None:
        d.pop("attorney", None)
    if d.get("hague_reference") is None:
        d.pop("hague_reference", None)
    if d.get("deferred_publication") is None:
        d.pop("deferred_publication", None)
    return d


# ---------------------------------------------------------------------------
# Issue-folder orchestration
# ---------------------------------------------------------------------------

def find_issue_folders(bulletins_root: Path) -> List[Path]:
    if not bulletins_root.is_dir():
        return []
    return sorted(p for p in bulletins_root.iterdir() if p.is_dir() and p.name.startswith("TS_"))


def metadata_is_fresh(issue_folder: Path) -> bool:
    """True if metadata.json exists, is non-empty, and is newer than bulletin.pdf."""
    pdf = issue_folder / "bulletin.pdf"
    meta = issue_folder / "metadata.json"
    if not (pdf.is_file() and meta.is_file()):
        return False
    try:
        return meta.stat().st_size > 0 and meta.stat().st_mtime >= pdf.stat().st_mtime
    except OSError:
        return False


def extract_issue(issue_folder: Path, *, force: bool = False, extract_images: bool = True) -> Dict[str, Any]:
    pdf = issue_folder / "bulletin.pdf"
    meta_path = issue_folder / "metadata.json"
    images_dir = issue_folder / "images"

    if not pdf.is_file():
        raise FileNotFoundError(f"missing bulletin.pdf in {issue_folder}")
    if not force and metadata_is_fresh(issue_folder):
        logger.info("[=] %s already up to date", issue_folder.name)
        return {"status": "skipped", "issue": issue_folder.name}

    # Clean slate on --force: wipe any prior images/ tree so old flat-named
    # files (legacy "{appno}_{d}_{v}.jpg" layout) don't coexist with new
    # per-application subfolder layout.
    if force and images_dir.exists():
        shutil.rmtree(images_dir)

    logger.info("[*] parsing %s", issue_folder.name)
    started = time.time()
    payload = parse_pdf(pdf, extract_images=extract_images, images_dir=images_dir)
    payload["extracted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload["extract_duration_seconds"] = round(time.time() - started, 1)

    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "[+] %s: %d records (TR=%d, Hague=%d) in %.1fs",
        issue_folder.name,
        payload["record_count"],
        sum(1 for r in payload["records"] if r["section"] != "hague"),
        sum(1 for r in payload["records"] if r["section"] == "hague"),
        payload["extract_duration_seconds"],
    )
    return {"status": "ok", "issue": issue_folder.name, **payload}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_argv(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pdf_extract_tasarim", add_help=True)
    parser.add_argument("--issue", type=str, default=None, help="single issue folder name (e.g. TS_483_2026-04-24)")
    parser.add_argument("--bulletins-root", type=Path, default=_LOCAL_DEFAULT_BULLETINS_DIR)
    parser.add_argument("--force", action="store_true", help="re-parse even if metadata.json is fresh")
    parser.add_argument("--no-images", action="store_true", help="skip image extraction (metadata only)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_argv(argv)
    extract_images = not args.no_images

    if args.issue:
        target = args.bulletins_root / args.issue
        result = extract_issue(target, force=args.force, extract_images=extract_images)
        return 0 if result.get("status") in {"ok", "skipped"} else 1

    folders = find_issue_folders(args.bulletins_root)
    if not folders:
        logger.warning("no TS_* folders under %s", args.bulletins_root)
        return 0
    logger.info("scanning %d issue folder(s) under %s", len(folders), args.bulletins_root)
    failed = 0
    for folder in folders:
        try:
            extract_issue(folder, force=args.force, extract_images=extract_images)
        except Exception as e:
            logger.exception("issue %s failed: %r", folder.name, e)
            failed += 1
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

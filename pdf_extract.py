"""
PDF Bulletin Extractor for Turkish Patent Office (Türk Patent)
================================================================
Parses trademark bulletin PDFs from turkpatent.gov.tr into the same
folder structure that ai.py (embeddings) and ingest.py (DB load) expect:

    bulletins/Marka/BLT_{num}_{date}/
        metadata.json   — array of trademark records
        images/         — extracted logo JPEGs named {year}_{seqno}.jpg

WIPO standard codes used in the bulletin:
    (210) Application number        (220) Filing date
    (731) Holder / applicant        (740) Attorney
    (540) Trademark name            (531) Vienna classification
    (511) Nice class numbers        (510) Goods / services
    (591) Color claim               (300) Priority
    (151) Registration date/number
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

def _get_fitz():
    """Lazy import of PyMuPDF to allow module to load even if fitz is missing."""
    try:
        import fitz
        return fitz
    except ImportError:
        return None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Regex that matches WIPO field codes like (210), (220), etc.
WIPO_CODE_RE = re.compile(r"\((\d{3})\)")

# Application number pattern: YYYY/NNNNNN
APP_NO_RE = re.compile(r"(\d{4}/\d{3,6})")

# PDF filename pattern: {bulletin_no}_{date}.pdf
PDF_NAME_RE = re.compile(r"^(\d+)_(\d{4}-\d{2}-\d{2})\.pdf$")

# Sections we want to parse (domestic + Madrid + re-examination)
# We detect these via the TOC page or by looking for (210) markers
SKIP_SECTION_HEADERS = {
    "BOLUNMELER", "HACİZ KALDIRMA", "HACİZ KONULANLAR",
    "TEMLİK", "LİSANS", "UNVAN DEĞİŞİKLİĞİ", "ADRES DEĞİŞİKLİĞİ",
    "NEVI DEĞİŞİKLİĞİ", "VEKİL DEĞİŞİKLİĞİ", "SINIFLANDIRMA",
    "DÜZELTMELER", "İPTAL",
}

# Minimum expected records from a bulletin PDF (canary check)
MIN_EXPECTED_RECORDS = 100


# ---------------------------------------------------------------------------
# TOC parser — extract section page ranges from table of contents
# ---------------------------------------------------------------------------
def _parse_toc(doc) -> Dict[str, int]:
    """Parse the İçindekiler (TOC) page to get section start pages."""
    sections = {}
    # TOC is typically on page 3 (index 2)
    for page_idx in range(min(5, doc.page_count)):
        text = doc[page_idx].get_text()
        if "İçindekiler" not in text:
            continue
        # Extract section names and page numbers
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Pattern: "Section Name ....... 123" or "Section Name  123"
            match = re.search(r"(\d+)\s*$", line)
            if match:
                page_no = int(match.group(1))
                name = line[:match.start()].strip().rstrip(".")
                if name:
                    sections[name] = page_no
        break
    return sections


def _get_application_page_ranges(doc) -> List[Tuple[int, int]]:
    """Return (start_page, end_page) ranges that contain trademark applications.

    Uses page numbers from TOC, sorted by page order, to identify sections
    that contain actual trademark records (not annotations/şerhler).
    """
    toc = _parse_toc(doc)
    if not toc:
        logger.warning("Could not parse TOC — scanning entire PDF")
        return [(0, doc.page_count)]

    # Sort entries by page number for ordered processing
    sorted_entries = sorted(toc.items(), key=lambda x: x[1])
    ranges = []

    # Strategy: find sections whose names contain "Başvurularının İlan" or
    # "Mahkeme Karar" (court decision published applications — these also
    # contain full WIPO-coded trademark records), and use the NEXT
    # TOC entry as the end boundary.
    for i, (name, page) in enumerate(sorted_entries):
        if "Başvurularının İlan" in name or "Mahkeme Karar" in name:
            start = page - 1  # 0-indexed
            # End = next TOC entry's page (or document end)
            end = sorted_entries[i + 1][1] - 1 if i + 1 < len(sorted_entries) else doc.page_count
            ranges.append((start, end))
            logger.info(f"  Section: '{name}' -> pages {start+1}-{end}")

    if not ranges:
        logger.warning("No application sections found in TOC — scanning entire PDF")
        return [(0, doc.page_count)]

    return ranges


# ---------------------------------------------------------------------------
# WIPO field extraction from text
# ---------------------------------------------------------------------------
# Regex to match page header/footer lines from the bulletin PDF.
# Two variants exist depending on header vs footer position:
#   Footer: "_____ 2026/488 Resmi Marka Bülteni ... Yayın Tarihi : 12.03.2026  3209"
#   Header: "_____ 3212    Yayın Tarihi : 12.03.2026  Türk Patent ... 2026/488 Resmi Marka Bülteni"
_PAGE_ARTIFACT_RE = re.compile(
    r"_+\s*(?:\d{4}/\d+\s+Resmi Marka Bülteni.*?Yayın Tarihi\s*:\s*[\d.]+\s*\d*"
    r"|\d{1,4}\s+Yayın Tarihi\s*:\s*[\d.]+.*?Resmi Marka Bülteni)",
    re.DOTALL,
)


def _clean_page_artifacts(text: str) -> str:
    """Remove page header/footer noise from extracted text."""
    return _PAGE_ARTIFACT_RE.sub("", text).strip()


def _segment_wipo_fields(text: str) -> List[Dict[str, str]]:
    """Split a block of text into trademark records using WIPO (210) boundaries.

    Returns a list of dicts mapping WIPO code -> field text.
    """
    # Find all WIPO code positions
    markers = list(WIPO_CODE_RE.finditer(text))
    if not markers:
        return []

    # Group consecutive fields; split on (210) for new records
    records: List[Dict[str, str]] = []
    current: Dict[str, str] = {}

    for i, m in enumerate(markers):
        code = m.group(1)
        # Text runs from end of this marker to start of next marker (or end of text)
        start = m.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        value = text[start:end].strip()
        # Remove page headers/footers that bleed into field text
        value = _clean_page_artifacts(value)
        # Collapse PDF line-wrap newlines into spaces for text-only fields.
        # (731) holder blocks rely on newlines to separate name/address lines,
        # so we leave those intact and collapse in consumers instead.
        if code not in ("731", "740"):
            value = re.sub(r"\s*\n\s*", " ", value).strip()

        if code == "210" and current:
            # New record boundary
            records.append(current)
            current = {}

        current[code] = value

    if current:
        records.append(current)

    return records


# ---------------------------------------------------------------------------
# Field parsers
# ---------------------------------------------------------------------------
def _parse_app_no(raw: str) -> Optional[str]:
    """Extract application number like '2025/012958'."""
    # Could have leading international reg number on a separate line
    m = APP_NO_RE.search(raw)
    return m.group(1) if m else raw.strip()[:20] if raw.strip() else None


def _parse_date(raw: str) -> str:
    """Normalize date from DD.MM.YYYY or DD/MM/YYYY to DD/MM/YYYY."""
    raw = raw.strip().split("\n")[0].strip()
    raw = raw.replace(".", "/")
    return raw


def _parse_holder(raw: str) -> Tuple[List[Dict], List[Dict]]:
    """Parse (731) holder block into HOLDERS and ATTORNEYS lists.

    Format examples:
        5586902-HEKİM İLAÇ ... (TR)
        MADENLER MAH. İLKE SK. ... Ümraniye İstanbul
        Vekil:
        RÜŞTÜ GÜMÜŞ(ACAR FİKRİ MÜLKİYET ...)

    Multiple holders separated by consecutive ID-NAME lines.
    """
    holders = []
    attorneys = []

    # Split on "Vekil:" to separate holders from attorney
    parts = re.split(r"\bVekil\s*:", raw, maxsplit=1)
    holder_text = parts[0].strip()
    attorney_text = parts[1].strip() if len(parts) > 1 else ""

    # Parse holders - each starts with a client ID pattern like "1234567-NAME"
    # or just a name with (COUNTRY) suffix
    # Client IDs are typically 7 digits: split on lines starting with digits followed by dash
    holder_blocks = re.split(r"(?m)(?=^\d{5,}-)", holder_text)
    # Filter out empty/whitespace-only blocks and stray digit fragments
    holder_blocks = [b.strip() for b in holder_blocks if b.strip() and len(b.strip()) > 3]

    for block in holder_blocks:
        block = block.strip()
        if not block:
            continue
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue

        # First line: "ID-TITLE (COUNTRY)" or just "TITLE (COUNTRY)"
        first = lines[0]
        client_id = ""
        title = first
        id_match = re.match(r"(\d+)-(.+)", first)
        if id_match:
            client_id = id_match.group(1)
            title = id_match.group(2).strip()

        # Extract country from last parentheses
        country = "TÜRKİYE"
        country_match = re.search(r"\(([A-Z]{2})\)\s*$", title)
        if country_match:
            country_code = country_match.group(1)
            title = title[:country_match.start()].strip()
            country = _country_code_to_name(country_code)

        # Remaining lines are address
        address_lines = lines[1:]
        address = " ".join(address_lines).strip()

        # Try to extract city from address (last word before postal code or end)
        city = ""
        if address:
            # Pattern: "... CityName PostalCode" or "... CityName"
            city_match = re.search(r"(\S+)\s*(\d{5})?\s*$", address)
            if city_match:
                city = city_match.group(1)

        holders.append({
            "TPECLIENTID": client_id,
            "TITLE": title,
            "ADDRESS": address,
            "TOWN_DISTRICT": "",
            "POSTALCODE": "",
            "CITY_PROVINCE": city,
            "COUNTRY": country,
        })

    if not holders:
        # Fallback: treat entire text as one holder
        holders.append({
            "TPECLIENTID": "",
            "TITLE": holder_text.split("\n")[0].strip() if holder_text else "",
            "ADDRESS": "",
            "TOWN_DISTRICT": "",
            "POSTALCODE": "",
            "CITY_PROVINCE": "",
            "COUNTRY": "TÜRKİYE",
        })

    # Parse attorney
    if attorney_text:
        att_lines = [l.strip() for l in attorney_text.split("\n") if l.strip()]
        att_name = att_lines[0] if att_lines else ""
        attorneys.append({"NO": "", "NAME": att_name, "TITLE": ""})

    return holders, attorneys


def _country_code_to_name(code: str) -> str:
    """Map 2-letter country code to Turkish name (common ones)."""
    MAP = {
        "TR": "TÜRKİYE", "US": "AMERİKA BİRLEŞİK DEVLETLERİ",
        "DE": "ALMANYA", "GB": "BİRLEŞİK KRALLIK", "FR": "FRANSA",
        "IT": "İTALYA", "ES": "İSPANYA", "CH": "İSVİÇRE",
        "CN": "ÇİN", "JP": "JAPONYA", "KR": "GÜNEY KORE",
        "NL": "HOLLANDA", "BE": "BELÇİKA", "AT": "AVUSTURYA",
        "SE": "İSVEÇ", "DK": "DANİMARKA", "NO": "NORVEÇ",
        "FI": "FİNLANDİYA", "PL": "POLONYA", "RU": "RUSYA",
        "IN": "HİNDİSTAN", "BR": "BREZİLYA", "AU": "AVUSTRALYA",
        "CA": "KANADA", "AE": "BİRLEŞİK ARAP EMİRLİKLERİ",
    }
    return MAP.get(code, code)


def _parse_nice_classes(raw_511: str, raw_510: str) -> Tuple[str, List[str], List[Dict]]:
    """Parse Nice class numbers from (511) and goods text from (510).

    Returns (nice_raw, nice_list, goods_list).
    """
    # (511) contains class numbers like "05" or "24 , 25" or "35 , 06 , 07"
    raw_511 = raw_511.strip().split("\n")[0].strip()
    # Normalize separators
    class_nums = [c.strip().zfill(2) for c in re.split(r"[,/\s]+", raw_511) if c.strip().isdigit()]
    nice_raw = " / ".join(class_nums)
    nice_list = class_nums if class_nums else []

    # (510) goods — split by class if multiple classes
    goods = []
    goods_text = raw_510.strip()
    if goods_text:
        # If multiple classes, goods descriptions are concatenated
        # We assign entire text to first class for simplicity (matching existing behavior)
        for i, cls in enumerate(class_nums):
            goods.append({
                "CLASSID": cls,
                "SUBCLASSID": cls,
                "TEXT": goods_text if i == 0 else "",
                "SEQ": i,
            })
    if not goods and goods_text:
        goods.append({"CLASSID": "98", "SUBCLASSID": "98", "TEXT": goods_text, "SEQ": 0})

    return nice_raw, nice_list, goods


def _parse_vienna_classes(raw: str) -> Tuple[str, List[str]]:
    """Parse Vienna classification from (531).

    Examples: "1.3.2; 1.3.15; 1.3.13" or "17.02;27.03;" or "null"
    """
    raw = raw.strip()
    if not raw or raw.lower() == "null":
        return "", []
    # Split on semicolons or commas
    parts = [p.strip().rstrip(";") for p in re.split(r"[;,]", raw) if p.strip() and p.strip() != "null"]
    # Extract top-level class (first number before .)
    top_classes = list(dict.fromkeys(p.split(".")[0] for p in parts if p))
    return raw, top_classes


def _make_image_key(app_no: str) -> str:
    """Convert application number to image filename key: 2025/012958 -> 2025_012958."""
    return app_no.replace("/", "_")


# ---------------------------------------------------------------------------
# Core PDF parsing
# ---------------------------------------------------------------------------
def _build_metadata_record(
    fields: Dict[str, str],
    bulletin_no: str,
    bulletin_date: str,
    has_image: bool = False,
) -> Optional[Dict[str, Any]]:
    """Convert WIPO field dict to a metadata.json record."""
    app_no = _parse_app_no(fields.get("210", ""))
    if not app_no:
        return None

    # Must have at least (220) date and (511) class to be a real entry
    if "220" not in fields or "511" not in fields:
        return None

    # Application date
    app_date = _parse_date(fields.get("220", ""))

    # Trademark name from (540)
    name = fields.get("540", "").strip()
    # Remove leading/trailing newlines, keep first meaningful line(s)
    name = " ".join(name.split())

    # Holders and attorneys from (731)
    holders, attorneys = _parse_holder(fields.get("731", ""))

    # Nice classes and goods
    nice_raw, nice_list, goods = _parse_nice_classes(
        fields.get("511", ""), fields.get("510", "")
    )

    # Vienna classes
    vienna_raw, vienna_list = _parse_vienna_classes(fields.get("531", ""))

    # Registration info
    reg_no = ""
    reg_date = ""
    if "151" in fields:
        reg_text = fields["151"].strip()
        date_match = re.search(r"\d{2}[./]\d{2}[./]\d{4}", reg_text)
        if date_match:
            reg_date = _parse_date(date_match.group(0))

    # International registration number (Madrid)
    int_reg_no = ""
    # For Madrid entries, there's often a number before (210)
    # We check the raw text around the record

    image_key = _make_image_key(app_no) if has_image else ""

    return {
        "APPLICATIONNO": app_no,
        "STATUS": "Application/Published",
        "IMAGE": image_key,
        "TRADEMARK": {
            "APPLICATIONDATE": app_date,
            "REGISTERNO": reg_no,
            "REGISTERDATE": reg_date,
            "INTREGNO": int_reg_no,
            "NAME": name,
            "NICECLASSES_RAW": nice_raw,
            "NICECLASSES_LIST": nice_list,
            "TM_TYPE_CODE": "",
            "VIENNACLASSES_RAW": vienna_raw,
            "VIENNACLASSES_LIST": vienna_list,
            "BULLETIN_NO": bulletin_no,
            "BULLETIN_DATE": bulletin_date,
            "EXTRA_COL_11": "",
            "EXTRA_COL_12": "",
        },
        "HOLDERS": holders,
        "ATTORNEYS": attorneys,
        "GOODS": goods,
        "EXTRACTEDGOODS": [],
    }


def parse_bulletin_pdf(pdf_path: Path, output_dir: Path, bulletin_no: str, bulletin_date: str) -> Dict[str, Any]:
    """Parse a bulletin PDF and produce metadata.json + images/.

    Delegates to parse_bulletin_pdf_v2 which uses sequential image assignment.
    """
    return parse_bulletin_pdf_v2(pdf_path, output_dir, bulletin_no, bulletin_date)


def _parse_bulletin_pdf_v1(pdf_path: Path, output_dir: Path, bulletin_no: str, bulletin_date: str) -> Dict[str, Any]:
    """DEPRECATED: V1 parser with Vienna-only image assignment. Kept for reference."""
    fitz = _get_fitz()
    if fitz is None:
        raise ImportError("PyMuPDF required. pip install PyMuPDF")

    t0 = time.time()
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    logger.info(f"Opened PDF: {pdf_path.name} ({doc.page_count} pages)")

    # Get page ranges for application sections
    page_ranges = _get_application_page_ranges(doc)
    logger.info(f"Application sections: {page_ranges}")

    # --- Phase 1: Extract all text and images page by page ---
    all_records: List[Dict[str, Any]] = []
    image_count = 0
    # Track images per page for association with records
    page_images: Dict[int, List[Tuple[float, bytes, str]]] = {}  # page_idx -> [(y_pos, img_bytes, ext)]

    for range_start, range_end in page_ranges:
        range_end = min(range_end, doc.page_count)
        accumulated_text = ""
        page_image_map: Dict[int, List] = {}  # page_idx -> [(y_pos, xref)]

        for page_idx in range(range_start, range_end):
            page = doc[page_idx]
            page_text = page.get_text()

            # Skip annotation/şerhler pages (they have different structure)
            first_line = page_text.strip().split("\n")[0].strip().upper() if page_text.strip() else ""
            if any(header in first_line for header in SKIP_SECTION_HEADERS):
                # We've entered annotations — stop this range
                break

            accumulated_text += page_text + "\n"

            # Extract images from this page
            img_list = page.get_images(full=True)
            if img_list:
                page_image_map[page_idx] = []
                for img_info in img_list:
                    xref = img_info[0]
                    try:
                        # Get image position on page
                        img_rects = page.get_image_rects(xref)
                        y_pos = img_rects[0].y0 if img_rects else 0
                    except Exception:
                        y_pos = 0
                    page_image_map[page_idx].append((y_pos, xref))

        # --- Phase 2: Parse accumulated text into records ---
        raw_records = _segment_wipo_fields(accumulated_text)

        # --- Phase 3: Extract images and build metadata records ---
        # First, figure out which records have images by checking (531) or page proximity
        # Simple approach: extract ALL images and match by app number position in text

        # Build a map: app_no -> whether it has an image nearby
        # We do this by finding the page+position of each (210) marker in text
        # and matching against image positions on the same page

        # For simplicity and reliability: extract images in page order,
        # match them to records that appear on the same page(s)
        all_image_xrefs = []
        for pidx in sorted(page_image_map.keys()):
            for y_pos, xref in page_image_map[pidx]:
                all_image_xrefs.append((pidx, y_pos, xref))

        # Now process records and associate images
        # Strategy: records with (531) Vienna class likely have images
        # But also track image positions vs text positions

        # Simpler reliable approach: just extract all images and assign them
        # to records that have (531) field, in order of appearance
        records_with_vienna = [i for i, r in enumerate(raw_records) if r.get("531")]
        img_idx = 0

        for i, fields in enumerate(raw_records):
            app_no = _parse_app_no(fields.get("210", ""))
            if not app_no:
                continue

            # Check if this record should have an image
            has_image = False
            if i in records_with_vienna and img_idx < len(all_image_xrefs):
                # Try to extract and save the image
                _, _, xref = all_image_xrefs[img_idx]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    # Convert CMYK to RGB if needed
                    if pix.colorspace and pix.colorspace.n >= 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    # Skip very small images (likely decorative)
                    if pix.width >= 50 and pix.height >= 50:
                        img_key = _make_image_key(app_no)
                        img_path = images_dir / f"{img_key}.jpg"
                        pix.save(str(img_path))
                        has_image = True
                        image_count += 1
                    pix = None  # free memory
                except Exception as e:
                    logger.warning(f"Failed to extract image for {app_no}: {e}")
                img_idx += 1

            record = _build_metadata_record(fields, bulletin_no, bulletin_date, has_image)
            if record:
                all_records.append(record)

        # Handle remaining images (records without Vienna class but with images)
        # This catches cases where images exist but (531) is missing

    doc.close()

    # --- Canary check ---
    if len(all_records) < MIN_EXPECTED_RECORDS:
        logger.error(
            f"Canary check FAILED: only {len(all_records)} records extracted "
            f"from {pdf_path.name} (expected >= {MIN_EXPECTED_RECORDS}). "
            f"Possible parsing failure."
        )
        return {"status": "failed", "records": len(all_records), "images": image_count,
                "error": f"Too few records: {len(all_records)}"}

    # --- Write metadata.json ---
    meta_path = output_dir / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    duration = time.time() - t0
    logger.info(
        f"PDF extraction complete: {len(all_records)} records, "
        f"{image_count} images in {duration:.1f}s"
    )

    return {
        "status": "success",
        "records": len(all_records),
        "images": image_count,
        "duration_seconds": round(duration, 1),
    }


# ---------------------------------------------------------------------------
# Better image-record association (v2)
# ---------------------------------------------------------------------------
def parse_bulletin_pdf_v2(pdf_path: Path, output_dir: Path, bulletin_no: str, bulletin_date: str) -> Dict[str, Any]:
    """Improved parser that associates images with records using page position.

    This version processes page-by-page, tracking both text positions and
    image positions to correctly match logos to their trademark records.
    """
    fitz = _get_fitz()
    if fitz is None:
        raise ImportError("PyMuPDF required. pip install PyMuPDF")

    t0 = time.time()
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    logger.info(f"Opened PDF: {pdf_path.name} ({doc.page_count} pages)")

    page_ranges = _get_application_page_ranges(doc)
    logger.info(f"Scanning page ranges: {page_ranges}")

    all_records: List[Dict[str, Any]] = []
    image_count = 0
    current_app_no = None  # Track which record we're inside

    for range_start, range_end in page_ranges:
        range_end = min(range_end, doc.page_count)

        # Accumulate full text across pages for this range, then parse
        full_text = ""
        # But also track page-level images
        range_images: List[Tuple[int, int]] = []  # (page_idx, xref)

        for page_idx in range(range_start, range_end):
            page = doc[page_idx]
            page_text = page.get_text()

            # Detect section breaks
            stripped = page_text.strip()
            if not stripped:
                continue
            first_line = stripped.split("\n")[0].strip().upper()
            if any(header in first_line for header in SKIP_SECTION_HEADERS):
                break

            # Skip pure index pages (only application numbers, no WIPO codes)
            if not WIPO_CODE_RE.search(page_text) and APP_NO_RE.search(page_text):
                # Could be an index page — skip if no descriptive text
                if len(page_text.strip()) < 200 or page_text.count("\n") > 50:
                    continue

            full_text += page_text + "\n"

            # Collect images
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                range_images.append((page_idx, xref))

            # Progress logging every 500 pages
            if (page_idx - range_start) % 500 == 0 and page_idx > range_start:
                logger.info(f"  Scanning page {page_idx}/{range_end}...")

        # Parse all records from accumulated text
        raw_records = _segment_wipo_fields(full_text)
        logger.info(f"  Range {range_start}-{range_end}: {len(raw_records)} raw records, {len(range_images)} images")

        # Match images to records sequentially — the PDF embeds one image
        # per trademark entry (even text-only marks get a rendered image).
        # The number of images should roughly equal the number of records.
        img_iter = iter(range_images)

        for fields in raw_records:
            app_no = _parse_app_no(fields.get("210", ""))
            if not app_no:
                continue

            # Skip records that aren't real entries (no date + no class)
            if "220" not in fields or "511" not in fields:
                continue

            # Each record gets the next image in sequence
            has_image = False
            img_info = next(img_iter, None)
            if img_info:
                _, xref = img_info
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.colorspace and pix.colorspace.n >= 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    if pix.width >= 50 and pix.height >= 50:
                        img_key = _make_image_key(app_no)
                        img_path = images_dir / f"{img_key}.jpg"
                        pix.save(str(img_path))
                        has_image = True
                        image_count += 1
                    pix = None
                except Exception as e:
                    logger.warning(f"Image extract failed for {app_no}: {e}")

            record = _build_metadata_record(fields, bulletin_no, bulletin_date, has_image)
            if record:
                all_records.append(record)

    doc.close()

    # Canary check
    if len(all_records) < MIN_EXPECTED_RECORDS:
        logger.error(
            f"Canary FAILED: {len(all_records)} records from {pdf_path.name} "
            f"(expected >= {MIN_EXPECTED_RECORDS})"
        )
        return {"status": "failed", "records": len(all_records), "images": image_count,
                "error": f"Too few records: {len(all_records)}"}

    # Write metadata.json
    meta_path = output_dir / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    duration = time.time() - t0
    logger.info(
        f"PDF extraction: {len(all_records)} records, {image_count} images, {duration:.1f}s"
    )
    return {
        "status": "success",
        "records": len(all_records),
        "images": image_count,
        "duration_seconds": round(duration, 1),
    }


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------
def find_unprocessed_pdfs(root_dir: Path) -> List[Tuple[Path, str, str]]:
    """Find PDF files in root_dir that haven't been extracted yet.

    Returns list of (pdf_path, bulletin_no, bulletin_date).
    """
    results = []
    if not root_dir.exists():
        return results

    for f in sorted(root_dir.iterdir()):
        if not f.is_file():
            continue
        m = PDF_NAME_RE.match(f.name)
        if not m:
            continue
        bulletin_no = m.group(1)
        bulletin_date = m.group(2)

        # Check if already extracted
        output_dir = root_dir / f"BLT_{bulletin_no}_{bulletin_date}"
        meta_file = output_dir / "metadata.json"
        if meta_file.exists():
            logger.info(f"Skipping {f.name} — already extracted to {output_dir.name}")
            continue

        results.append((f, bulletin_no, bulletin_date))

    return results


def run_pdf_extraction(root_dir: Path = None, settings=None) -> Dict[str, Any]:
    """Pipeline entry point: find and extract all unprocessed PDF bulletins.

    Args:
        root_dir: Bulletins root (e.g., /app/bulletins/Marka)
        settings: Optional pipeline settings object

    Returns:
        {"processed": N, "skipped": N, "failed": N, "total_records": N}
    """
    if root_dir is None:
        try:
            from config.settings import settings as _app_settings
            root_dir = Path(_app_settings.pipeline.bulletins_root)
        except Exception:
            root_dir = Path("bulletins/Marka")

    pdfs = find_unprocessed_pdfs(root_dir)
    if not pdfs:
        logger.info("No unprocessed PDF bulletins found")
        return {"processed": 0, "skipped": 0, "failed": 0, "total_records": 0}

    logger.info(f"Found {len(pdfs)} PDF bulletin(s) to extract")

    processed = 0
    failed = 0
    total_records = 0

    for pdf_path, bulletin_no, bulletin_date in pdfs:
        output_dir = root_dir / f"BLT_{bulletin_no}_{bulletin_date}"
        logger.info(f"Extracting {pdf_path.name} -> {output_dir.name}")

        try:
            result = parse_bulletin_pdf_v2(pdf_path, output_dir, bulletin_no, bulletin_date)
            if result["status"] == "success":
                processed += 1
                total_records += result["records"]
                logger.info(
                    f"  OK: {result['records']} records, {result['images']} images "
                    f"in {result.get('duration_seconds', 0)}s"
                )
            else:
                failed += 1
                logger.error(f"  FAILED: {result.get('error', 'unknown')}")
        except Exception as e:
            failed += 1
            logger.exception(f"  Exception extracting {pdf_path.name}: {e}")

    return {
        "processed": processed,
        "skipped": 0,
        "failed": failed,
        "total_records": total_records,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Extract trademark data from bulletin PDFs")
    parser.add_argument("--root", type=str, default="bulletins/Marka",
                        help="Bulletins root directory")
    parser.add_argument("--pdf", type=str, default=None,
                        help="Process a single PDF file")
    args = parser.parse_args()

    if args.pdf:
        pdf = Path(args.pdf)
        m = PDF_NAME_RE.match(pdf.name)
        if not m:
            print(f"PDF filename must match pattern: {{num}}_{{YYYY-MM-DD}}.pdf")
            exit(1)
        out = pdf.parent / f"BLT_{m.group(1)}_{m.group(2)}"
        result = parse_bulletin_pdf_v2(pdf, out, m.group(1), m.group(2))
        print(json.dumps(result, indent=2))
    else:
        result = run_pdf_extraction(Path(args.root))
        print(json.dumps(result, indent=2))

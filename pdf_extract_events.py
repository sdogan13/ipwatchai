"""
PDF Event Extractor for Turkish Patent Bulletins (GZ + BLT)
============================================================
Parses supplementary sections from trademark bulletin PDFs:
  - GZ (Gazette): transfers, mergers, seizures, licenses, cancellations, renewals
  - BLT (Bulletin): splits, seizure lifts, injunction lifts, Madrid annotations

Usage:
    python pdf_extract_events.py --pdf bulletins/Marka/GZ_499_2026-01-30/bulletin.pdf --source GZ
    python pdf_extract_events.py --pdf bulletins/Marka/BLT_488_2026-03-12/bulletin.pdf --source BLT
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import for PyMuPDF
# ---------------------------------------------------------------------------
def _get_fitz():
    try:
        import fitz
        return fitz
    except ImportError:
        logger.error("PyMuPDF required: pip install PyMuPDF")
        return None


# ---------------------------------------------------------------------------
# Constants — sub-section headers
# ---------------------------------------------------------------------------
# Order matters: longer/more specific headers must come before shorter ones
# (e.g. "KISMİ DEVİR" before "DEVİR", "İHTİYATİ HACİZ" before "HACİZ")

GZ_SECTION_HEADERS = [
    ("BİRLEŞME", "merger", "transfer"),
    ("KISMİ DEVİR", "partial_transfer", "transfer"),
    ("DEVİR", "transfer", "transfer"),
    ("EŞYA SINIRLANDIRMA (MAHKEME KARARI", "goods_limitation_court", "simple"),
    ("İHTİYATİ HACİZ KONULANLAR", "precautionary_seizure", "court"),
    ("HACİZ KONULANLAR", "seizure", "court"),
    ("İHTİYATİ TEDBİR KONULANLAR", "precautionary_injunction", "court"),
    ("TEDBİR KONULANLAR", "injunction", "court"),
    ("İFLAS İLANI", "bankruptcy", "simple"),
    ("LİSANS KAYDI", "license", "court"),
    ("MAL HİZMET SINIRLANDIRMA", "goods_limitation", "simple"),
    ("MADRİD DÖNÜŞTÜRME", "madrid_conversion", "simple"),
    ("MADRİD YERDEĞİŞTİRME", "madrid_replacement", "simple"),
    ("İPTAL EDİLENLER", "cancellation", "court"),
    ("İŞLEMDEN ÇEKİLEN BAŞVURULAR", "withdrawal", "simple"),
]

BLT_SECTION_HEADERS = [
    ("BÖLÜNMELER", "split", "simple"),
    ("BOLUNMELER", "split", "simple"),
    ("HACİZ KALDIRMA", "seizure_lift", "court"),
    ("İHTİYATİ HACİZ KONULANLAR", "precautionary_seizure", "court"),
    ("HACİZ KONULANLAR", "seizure", "court"),
    ("KISITLAMA KALDIRMA", "restriction_lift", "court"),
    ("İHTİYATİ TEDBİR KONULANLAR", "precautionary_injunction", "court"),
    ("TEDBİR KALDIRMA", "injunction_lift", "court"),
    ("TEDBİR KONULANLAR", "injunction", "court"),
    ("İFLAS İLANI", "bankruptcy", "simple"),
    ("MAL HİZMET SINIRLANDIRMA", "goods_limitation", "simple"),
    ("EŞYA SINIRLANDIRMA (MAHKEME KARARI", "goods_limitation_court", "court"),
    ("MARKA ÖRNEĞİ DÜZELTİLDİ", "logo_correction", "correction"),
    ("SAHİP DÜZELTİLDİ", "holder_correction", "correction"),
    ("İŞLEMDEN ÇEKİLEN BAŞVURULAR", "withdrawal", "simple"),
]

# Regex patterns
APP_NO_RE = re.compile(r"(\d{4}/\d{3,6})")
REG_NO_RE = re.compile(r"\(111\)\s*(\d{4}\s+\d{3,6}|\d+)")
FILING_DATE_RE = re.compile(r"\(220\)\s*([\d/.]+)")
TM_NAME_RE = re.compile(r"\(566\)\s*(.+?)(?=\s*(?:Esas No:|Devreden|$))", re.DOTALL)
CASE_NO_RE = re.compile(r"Esas No:\s*([^\(]+)\(([^)]+)\)")
CASE_DATE_RE = re.compile(r"Esas Tarihi\s*:\s*([\d/.\-]+)")

# Page header/footer noise
PAGE_NOISE_RE = re.compile(
    r"_+\s*(?:\d{4}/\d+\s+Resmi Marka (?:Bülteni|Gazetesi).*?Yayın Tarihi\s*:\s*[\d.]+\s*\d*"
    r"|\d{1,5}\s+Yayın Tarihi\s*:\s*[\d.]+.*?Resmi Marka (?:Bülteni|Gazetesi))",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# TOC parsing
# ---------------------------------------------------------------------------
def _parse_toc(doc) -> Dict[str, int]:
    """Parse İçindekiler (TOC) page to get section start pages."""
    sections = {}
    for page_idx in range(min(8, doc.page_count)):
        text = doc[page_idx].get_text()
        if "İçindekiler" not in text and "İÇİNDEKİLER" not in text:
            continue
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            match = re.search(r"(\d+)\s*$", line)
            if match:
                page_no = int(match.group(1))
                name = line[:match.start()].strip().rstrip(".")
                if name and len(name) > 2:
                    sections[name] = page_no
        break
    return sections


def _scan_backwards_for_events(doc, source_type: str) -> Optional[Tuple[int, int]]:
    """Scan backwards from end of PDF for known event sub-section headers.

    Used when TOC references pages beyond the PDF (multi-volume bulletins where
    events are in a different volume, or single-volume PDFs with no TOC).
    Returns (start_page_0indexed, end_page_0indexed) or None.
    """
    headers = GZ_SECTION_HEADERS if source_type == "GZ" else BLT_SECTION_HEADERS
    header_texts = [h[0] for h in headers]
    max_page = doc.page_count

    # Scan backwards from end, looking for the first event header
    first_header_page = None
    last_header_page = None
    for p in range(max_page - 1, max(0, max_page - 500), -1):
        try:
            text = doc[p].get_text()
        except Exception:
            continue
        for ht in header_texts:
            if ht in text:
                if last_header_page is None:
                    last_header_page = p
                first_header_page = p
                break

    if first_header_page is not None:
        # Go a few pages before the first header to catch the section start
        start = max(0, first_header_page - 2)
        end = min(last_header_page + 50, max_page)  # events extend after last header
        logger.info(f"Backward scan found events at pages {start + 1}–{end}")
        return (start, end)

    return None


def _find_events_page_range(doc, source_type: str) -> Tuple[int, int]:
    """Find the page range of the events section using the TOC.

    Returns (start_page_0indexed, end_page_0indexed).
    """
    toc = _parse_toc(doc)
    if not toc:
        logger.warning("No TOC found — will scan entire PDF for event headers")
        return (0, doc.page_count)

    sorted_sections = sorted(toc.items(), key=lambda x: x[1])
    logger.info(f"TOC sections: {[(n, p) for n, p in sorted_sections]}")

    # Find the events section
    if source_type == "GZ":
        # GZ TOC: may be truncated, e.g. "İLİŞKİN İLANLAR" or full
        # "Tescilli Markalar Üzerindeki İşlemlere İlişkin İlanlar"
        target_keywords = ["İşlemlere İlişkin", "İLİŞKİN İLANLAR", "Tescilli Markalar Üzerindeki"]
    else:
        # BLT: "Marka Bülteni Şerhleri"
        target_keywords = ["Şerhleri", "Bülteni Şerhleri"]

    events_start = None
    events_idx = None
    for i, (name, page) in enumerate(sorted_sections):
        if any(kw in name for kw in target_keywords):
            events_start = page - 1  # 0-indexed
            events_idx = i
            logger.info(f"Events section: '{name}' starts at page {page}")
            break

    if events_start is None:
        logger.warning(f"Could not find events section in TOC for {source_type}")
        return (0, doc.page_count)

    # Clamp TOC page numbers to actual document page count
    # (some multi-volume gazettes have cumulative page numbers exceeding the PDF)
    max_page = doc.page_count
    if events_start >= max_page:
        logger.warning(f"TOC events_start ({events_start}) exceeds page count ({max_page}), "
                        "scanning backwards for event headers")
        # Multi-volume: events section is in a different volume.
        # Scan backwards from the end for known sub-section headers.
        scan_result = _scan_backwards_for_events(doc, source_type)
        if scan_result:
            return scan_result
        return (0, max_page)

    # End: find the last section before we hit non-event content
    events_end = max_page

    # Narrow end using next TOC section that's clearly past events
    if events_idx is not None:
        for j in range(events_idx + 1, len(sorted_sections)):
            next_name, next_page = sorted_sections[j]
            if any(kw in next_name for kw in ["Düzeltmeler", "Yenilenen", "Mahkeme Karar",
                                                "Renkli Marka", "Ses ve Hareket"]):
                events_end = min(next_page - 1, max_page)
                logger.info(f"Events section ends before '{next_name}' at page {next_page}")
                break

    return (events_start, min(events_end, max_page))


def _find_renewals_page_range(doc) -> Optional[Tuple[int, int]]:
    """Find GZ section 5: Yenilenen Markalar."""
    toc = _parse_toc(doc)
    max_page = doc.page_count
    if toc:
        sorted_sections = sorted(toc.items(), key=lambda x: x[1])
        for i, (name, page) in enumerate(sorted_sections):
            if "Yenilenen" in name or "YENİLENEN" in name:
                start = min(page - 1, max_page - 1)
                end = min(sorted_sections[i + 1][1] - 1, max_page) if i + 1 < len(sorted_sections) else max_page
                if start >= max_page:
                    # TOC page exceeds PDF — fall through to scan
                    break
                return (start, end)

    # Fallback: scan last portion of PDF for renewal markers
    # Renewals are typically near the end of the gazette
    scan_start = max(0, max_page - 200)
    for p in range(scan_start, max_page):
        try:
            text = doc[p].get_text()
            if "YENİLENEN MARKALAR" in text or "Yenilenen Markalar" in text:
                logger.info(f"Renewals found by scan at page {p + 1}")
                return (p, max_page)
        except Exception:
            continue
    return None


def _find_corrections_page_range(doc, source_type: str) -> Optional[Tuple[int, int]]:
    """Find Düzeltmeler section from TOC."""
    toc = _parse_toc(doc)
    max_page = doc.page_count
    if toc:
        sorted_sections = sorted(toc.items(), key=lambda x: x[1])
        for i, (name, page) in enumerate(sorted_sections):
            if "Düzeltmeler" in name or "DÜZELTMELER" in name:
                start = min(page - 1, max_page - 1)
                end = min(sorted_sections[i + 1][1] - 1, max_page) if i + 1 < len(sorted_sections) else max_page
                if start >= max_page:
                    break  # Fall through to scan
                return (start, end)

    # Fallback: scan last portion of PDF for corrections markers
    scan_start = max(0, max_page - 200)
    for p in range(scan_start, max_page):
        try:
            text = doc[p].get_text()
            if "DÜZELTMELER" in text or "Düzeltmeler" in text:
                # Find end: next section or max 20 pages
                end = min(p + 20, max_page)
                logger.info(f"Corrections found by scan at page {p + 1}")
                return (p, end)
        except Exception:
            continue
    return None


def _find_madrid_page_range(doc) -> Optional[Tuple[int, int]]:
    """Find Madrid Bölümü Şerhleri section from TOC (BLT only)."""
    toc = _parse_toc(doc)
    if not toc:
        return None
    sorted_sections = sorted(toc.items(), key=lambda x: x[1])
    for i, (name, page) in enumerate(sorted_sections):
        if "Madrid" in name and "Şerhleri" in name:
            start = page - 1
            end = sorted_sections[i + 1][1] - 1 if i + 1 < len(sorted_sections) else doc.page_count
            return (start, end)
    return None


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------
def _extract_pages_text(doc, start: int, end: int) -> List[Tuple[int, str]]:
    """Extract text from pages, returning [(page_number_1indexed, text), ...]."""
    pages = []
    for p in range(start, min(end, doc.page_count)):
        text = doc[p].get_text()
        # Clean page header/footer noise
        text = PAGE_NOISE_RE.sub("", text)
        pages.append((p + 1, text))
    return pages


def _pages_to_text(pages: List[Tuple[int, str]]) -> str:
    """Join page texts with page markers."""
    parts = []
    for page_no, text in pages:
        parts.append(f"\n<<PAGE:{page_no}>>\n{text}")
    return "\n".join(parts)


def _get_page_for_position(text: str, pos: int) -> int:
    """Given a position in the joined text, find the page number."""
    # Find the last <<PAGE:N>> marker before (or at) this position
    last_page = 0
    for m in re.finditer(r"<<PAGE:(\d+)>>", text):
        if m.start() <= pos:
            last_page = int(m.group(1))
        else:
            break
    # If no marker found before pos, search entire text for the first marker
    if last_page == 0:
        m = re.search(r"<<PAGE:(\d+)>>", text)
        if m:
            last_page = int(m.group(1))
    return last_page


# ---------------------------------------------------------------------------
# Sub-section splitting
# ---------------------------------------------------------------------------
def _split_into_subsections(
    text: str,
    headers: List[Tuple[str, str, str]],
) -> List[Tuple[str, str, str, str, int]]:
    """Split events text into sub-sections by detecting headers.

    Returns: [(event_type, parser_type, header_found, section_text, page_no), ...]
    """
    # Build a regex that matches any of the headers at the start of a line
    # Sort by length descending so longer matches win
    sorted_headers = sorted(headers, key=lambda h: len(h[0]), reverse=True)

    # Find all header positions
    found = []
    for header_text, event_type, parser_type in sorted_headers:
        # Match header at start of line (possibly with leading whitespace)
        pattern = re.compile(
            r"(?:^|\n)\s*" + re.escape(header_text) + r"(?:\s|$|\n)",
            re.MULTILINE,
        )
        for m in pattern.finditer(text):
            page_no = _get_page_for_position(text, m.start())
            found.append((m.start(), m.end(), event_type, parser_type, header_text, page_no))

    if not found:
        return []

    # Sort by position and deduplicate overlapping matches
    found.sort(key=lambda x: x[0])
    deduped = []
    for item in found:
        if deduped and item[0] < deduped[-1][1] + 5:
            # Overlapping — keep the longer header match
            if len(item[4]) > len(deduped[-1][4]):
                deduped[-1] = item
        else:
            deduped.append(item)

    # Extract text between headers
    subsections = []
    for i, (start, end, event_type, parser_type, header, page_no) in enumerate(deduped):
        # Text runs from end of this header to start of next header (or end of text)
        text_start = end
        text_end = deduped[i + 1][0] if i + 1 < len(deduped) else len(text)
        section_text = text[text_start:text_end].strip()
        if section_text:
            subsections.append((event_type, parser_type, header, section_text, page_no))
            logger.info(
                f"  Found sub-section: {header} ({event_type}) at page {page_no}, "
                f"{len(section_text)} chars"
            )

    return subsections


def _clean_raw_text(text: str, limit: int = 2000) -> str:
    """Collapse newlines to spaces, strip page markers and WIPO codes for raw_text storage."""
    text = re.sub(r"<<PAGE:\d+>>", "", text)
    # Remove WIPO field codes like (210), (220), (111), (566), (791), etc.
    text = re.sub(r"\(\d{3}\)", "", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"  +", " ", text).strip()
    return text[:limit]


# ---------------------------------------------------------------------------
# Record-level splitting — split section text into per-trademark blocks
# ---------------------------------------------------------------------------
def _split_210_blocks(text: str) -> List[Tuple[str, int]]:
    """Split text into blocks starting with (210), return [(block_text, page_no), ...]."""
    # Find all (210) positions
    markers = list(re.finditer(r"\(210\)", text))
    if not markers:
        return []

    blocks = []
    for i, m in enumerate(markers):
        start = m.start()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        block = text[start:end].strip()
        page_no = _get_page_for_position(text, start)
        blocks.append((block, page_no))

    return blocks


# ---------------------------------------------------------------------------
# Field extraction from a single (210) block
# ---------------------------------------------------------------------------
def _extract_app_no(block: str) -> Optional[str]:
    """Extract application number like '2025/012958'."""
    m = APP_NO_RE.search(block)
    return m.group(1) if m else None


def _extract_reg_no(block: str) -> Optional[str]:
    """Extract registration number from (111) field.

    GZ format: (111)2018 05555 or (111)2024\n170165
    """
    m = re.search(r"\(111\)\s*(\d{4})\s*(\d{3,6})", block)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return None


def _extract_filing_date(block: str) -> Optional[str]:
    """Extract filing date from (220) field."""
    m = FILING_DATE_RE.search(block)
    if m:
        return m.group(1).strip()
    return None


def _extract_tm_name(block: str) -> Optional[str]:
    """Extract trademark name from (566) field."""
    # Stop at: Esas No:, Devreden, (791), (210), or end of line after (566)
    m = re.search(
        r"\(566\)\s*(.+?)(?=\s*(?:Esas No:|Devreden\s*:|\(791\)|\(210\)|$))",
        block, re.DOTALL,
    )
    if m:
        name = m.group(1).strip()
        # Clean up multiline names — but only take first meaningful line(s)
        lines = [l.strip() for l in name.split("\n") if l.strip()]
        # Filter out page markers and very long goods descriptions
        cleaned = []
        for line in lines:
            line = re.sub(r"<<PAGE:\d+>>", "", line).strip()
            if not line:
                continue
            # Stop if we hit what looks like goods description (starts with class-like text)
            if re.match(r"^(?:Ahşap|Yapıldıkları|Değerli|Dokunmuş|Müşterilerin|\d+\.\s*SINIF)", line):
                break
            cleaned.append(line)
            # Most names are 1-2 lines
            if len(cleaned) >= 3:
                break
        name = " ".join(cleaned).strip()
        return name if name else None
    return None


def _extract_court_info(block: str) -> Dict[str, Optional[str]]:
    """Extract court case info: case_no, court_name, case_date."""
    result = {"case_no": None, "court_name": None, "case_date": None}
    m = CASE_NO_RE.search(block)
    if m:
        result["case_no"] = m.group(1).strip()
        result["court_name"] = m.group(2).strip()
    m2 = CASE_DATE_RE.search(block)
    if m2:
        result["case_date"] = m2.group(1).strip()
    return result


def _extract_transfer_parties(block: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract Devreden (from) and Devralan (to) from transfer blocks."""
    old_holder = None
    new_holder = None

    # Devreden : NAME — stop before Devralan (with optional "(lar)" suffix)
    m = re.search(r"Devreden\s*:\s*(.+?)(?=\s*Devralan|\Z)", block, re.DOTALL)
    if m:
        old_holder = re.sub(r"\s*\n\s*", " ", m.group(1)).strip()
        old_holder = re.sub(r"<<PAGE:\d+>>", "", old_holder).strip()

    # Devralan(lar) : NAME(ADDRESS)
    m2 = re.search(r"Devralan\(?(?:lar)?\)?\s*:\s*(.+?)(?=\n\(210\)|\n<<PAGE|\Z)", block, re.DOTALL)
    if m2:
        new_holder = re.sub(r"\s*\n\s*", " ", m2.group(1)).strip()
        new_holder = re.sub(r"<<PAGE:\d+>>", "", new_holder).strip()

    return old_holder, new_holder


# ---------------------------------------------------------------------------
# Parser functions — one per format type
# ---------------------------------------------------------------------------
def parse_transfer_records(
    text: str, event_type: str, source_type: str, bulletin_no: str, bulletin_date: str,
) -> List[Dict[str, Any]]:
    """Parse Format A — transfers/mergers with Devreden/Devralan fields.

    Used for: BİRLEŞME, DEVİR, KISMİ DEVİR (GZ only)
    """
    events = []
    blocks = _split_210_blocks(text)

    for block, page_no in blocks:
        app_no = _extract_app_no(block)
        if not app_no:
            continue

        reg_no = _extract_reg_no(block)
        tm_name = _extract_tm_name(block)
        old_holder, new_holder = _extract_transfer_parties(block)

        # For partial transfers, capture goods text after Devralan block
        goods_text = None
        if event_type == "partial_transfer":
            # Text after the Devralan line that isn't another record
            m = re.search(r"Devralan.*?\)\s*\n(.+?)(?=\(210\)|\Z)", block, re.DOTALL)
            if m:
                goods = m.group(1).strip()
                goods = re.sub(r"<<PAGE:\d+>>", "", goods).strip()
                if goods and len(goods) > 5:
                    goods_text = goods

        events.append({
            "application_no": app_no,
            "registration_no": reg_no,
            "event_type": event_type,
            "event_subtype": None,
            "source_type": source_type,
            "bulletin_no": bulletin_no,
            "bulletin_date": bulletin_date,
            "page_number": page_no,
            "old_value": old_holder,
            "new_value": new_holder,
            "details": {
                "trademark_name": tm_name,
                "goods_text": goods_text,
            },
            "raw_text": _clean_raw_text(block),
        })

    return events


def parse_court_records(
    text: str, event_type: str, source_type: str, bulletin_no: str, bulletin_date: str,
) -> List[Dict[str, Any]]:
    """Parse Format B — records with court/case info (Esas No).

    Used for: HACİZ, TEDBİR, İHTİYATİ HACİZ, İHTİYATİ TEDBİR, LİSANS, İPTAL, etc.
    """
    events = []
    blocks = _split_210_blocks(text)

    for block, page_no in blocks:
        app_no = _extract_app_no(block)
        if not app_no:
            continue

        reg_no = _extract_reg_no(block)
        tm_name = _extract_tm_name(block)
        court_info = _extract_court_info(block)

        events.append({
            "application_no": app_no,
            "registration_no": reg_no,
            "event_type": event_type,
            "event_subtype": None,
            "source_type": source_type,
            "bulletin_no": bulletin_no,
            "bulletin_date": bulletin_date,
            "page_number": page_no,
            "old_value": None,
            "new_value": None,
            "details": {
                "trademark_name": tm_name,
                **court_info,
            },
            "raw_text": _clean_raw_text(block),
        })

    return events


def parse_simple_records(
    text: str, event_type: str, source_type: str, bulletin_no: str, bulletin_date: str,
) -> List[Dict[str, Any]]:
    """Parse Format C — minimal (210)+(566) records.

    Used for: BOLUNMELER, MAL HİZMET SINIRLANDIRMA, İŞLEMDEN ÇEKİLEN, İFLAS, etc.
    """
    events = []
    blocks = _split_210_blocks(text)

    for block, page_no in blocks:
        app_no = _extract_app_no(block)
        if not app_no:
            continue

        reg_no = _extract_reg_no(block)
        tm_name = _extract_tm_name(block)

        # Some "simple" records actually have court info — try extracting it
        court_info = _extract_court_info(block)
        has_court = court_info["case_no"] is not None

        events.append({
            "application_no": app_no,
            "registration_no": reg_no,
            "event_type": event_type,
            "event_subtype": None,
            "source_type": source_type,
            "bulletin_no": bulletin_no,
            "bulletin_date": bulletin_date,
            "page_number": page_no,
            "old_value": None,
            "new_value": None,
            "details": {
                "trademark_name": tm_name,
                **(court_info if has_court else {}),
            },
            "raw_text": _clean_raw_text(block),
        })

    return events


def parse_correction_prose(
    text: str, source_type: str, bulletin_no: str, bulletin_date: str,
) -> List[Dict[str, Any]]:
    """Parse Format D — free-text correction paragraphs.

    Used for: DÜZELTMELER, MARKA ÖRNEĞİ DÜZELTİLDİ, SAHİP DÜZELTİLDİ
    """
    events = []

    # Split on "Şerh ve ilan olunur." which ends each correction block
    paragraphs = re.split(r"Şerh ve ilan olunur\.", text)

    for para in paragraphs:
        para = para.strip()
        if not para or len(para) < 20:
            continue

        # Remove page markers for text processing
        clean = re.sub(r"<<PAGE:\d+>>", "", para).strip()

        # Try to extract application number(s)
        app_nos = APP_NO_RE.findall(clean)
        page_no = _get_page_for_position(text, text.find(para[:50]))

        if app_nos:
            # One event per mentioned app_no
            for app_no in set(app_nos):
                events.append({
                    "application_no": app_no,
                    "registration_no": None,
                    "event_type": "correction",
                    "event_subtype": None,
                    "source_type": source_type,
                    "bulletin_no": bulletin_no,
                    "bulletin_date": bulletin_date,
                    "page_number": page_no,
                    "old_value": None,
                    "new_value": None,
                    "details": {},
                    "raw_text": _clean_raw_text(clean),
                })
        else:
            # No app_no found — store as generic correction
            events.append({
                "application_no": "UNKNOWN",
                "registration_no": None,
                "event_type": "correction",
                "event_subtype": None,
                "source_type": source_type,
                "bulletin_no": bulletin_no,
                "bulletin_date": bulletin_date,
                "page_number": page_no,
                "old_value": None,
                "new_value": None,
                "details": {},
                "raw_text": _clean_raw_text(clean),
            })

    return events


def parse_madrid_records(
    text: str, source_type: str, bulletin_no: str, bulletin_date: str,
) -> List[Dict[str, Any]]:
    """Parse Format E — English WIPO Madrid notifications (BLT only).

    Split on NOTIFICATION markers. Extract registration number, holder, action type.
    """
    events = []

    # Split on NOTIFICATION markers
    blocks = re.split(r"(?=NOTIFICATION\s)", text)

    for block in blocks:
        block = block.strip()
        if not block or "NOTIFICATION" not in block:
            continue

        clean = re.sub(r"<<PAGE:\d+>>", "", block).strip()
        page_no = _get_page_for_position(text, text.find(block[:50]))

        # Extract notification code (e.g. LIN/2025/45)
        notif_match = re.search(r"NOTIFICATION\s+(\w+/\d{4}/\d+)", clean)
        notif_code = notif_match.group(1) if notif_match else None

        # Extract registration number
        reg_match = re.search(r"Registration number\s+([\d\s]+)\s*\(([^)]+)\)", clean)
        reg_no = reg_match.group(1).strip() if reg_match else None
        tm_name = reg_match.group(2).strip() if reg_match else None

        # Extract holder
        holder_match = re.search(r"Name and address of holder\s+(.+?)(?=\n(?:Legal|Designations|State))", clean, re.DOTALL)
        holder = re.sub(r"\s+", " ", holder_match.group(1)).strip() if holder_match else None

        # Determine sub-type from notification code prefix
        subtype = None
        if notif_code:
            prefix = notif_code.split("/")[0]
            subtype_map = {
                "LIN": "limitation",
                "RIN": "correction",
                "HRN": "holder_right",
                "CEN": "cessation",
                "REN": "renewal",
                "TRN": "transfer",
            }
            subtype = subtype_map.get(prefix, prefix)

        events.append({
            "application_no": f"MADRID_{reg_no}" if reg_no else "MADRID_UNKNOWN",
            "registration_no": reg_no,
            "event_type": "madrid",
            "event_subtype": subtype,
            "source_type": source_type,
            "bulletin_no": bulletin_no,
            "bulletin_date": bulletin_date,
            "page_number": page_no,
            "old_value": holder,
            "new_value": None,
            "details": {
                "trademark_name": tm_name,
                "notification_code": notif_code,
            },
            "raw_text": _clean_raw_text(clean),
        })

    return events


def parse_renewal_list(
    text: str, source_type: str, bulletin_no: str, bulletin_date: str,
) -> List[Dict[str, Any]]:
    """Parse Format F — GZ section 5 renewal list.

    Three-line groups: reg_no, date, name (but format is irregular).
    """
    events = []

    # Remove page markers
    clean = re.sub(r"<<PAGE:\d+>>", "\n", text)
    # Remove the header line
    clean = re.sub(r"Yenilenen Markalar Listesi", "", clean)

    # Pattern: registration number (like "2005 42226" or "84963"),
    # then date (like "30/09/2015"), then name
    lines = [l.strip() for l in clean.split("\n") if l.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]
        # Try to match as a registration number (digits and spaces)
        if re.match(r"^[\d\s]+$", line) and len(line) >= 4:
            reg_no = line.strip()
            renewal_date = None
            tm_name = None

            # Next line should be a date
            if i + 1 < len(lines):
                date_match = re.match(r"(\d{2}/\d{2}/\d{4})", lines[i + 1])
                if date_match:
                    renewal_date = date_match.group(1)
                    # Next line should be the name
                    if i + 2 < len(lines):
                        tm_name = lines[i + 2]
                        i += 3
                    else:
                        i += 2
                else:
                    i += 1
                    continue
            else:
                i += 1
                continue

            events.append({
                "application_no": reg_no.replace(" ", "/") if "/" not in reg_no else reg_no,
                "registration_no": reg_no,
                "event_type": "renewal",
                "event_subtype": None,
                "source_type": source_type,
                "bulletin_no": bulletin_no,
                "bulletin_date": bulletin_date,
                "page_number": 0,
                "old_value": None,
                "new_value": renewal_date,
                "details": {
                    "trademark_name": tm_name,
                    "renewal_date": renewal_date,
                },
                "raw_text": f"{reg_no} | {renewal_date} | {tm_name}",
            })
        else:
            i += 1

    return events


def parse_bare_app_number_list(
    text: str, source_type: str, bulletin_no: str, bulletin_date: str,
) -> List[Dict[str, Any]]:
    """Parse pages of bare application numbers (no sub-section headers, no (210) prefix).

    Some BLT bulletins (e.g. BLT_351) have a Şerhleri section that is just a flat
    list of YYYY/NNNNN numbers — these are withdrawal entries without explicit headers.
    """
    events = []
    # Remove page markers and noise
    clean = re.sub(r"<<PAGE:\d+>>", "\n", text)
    clean = PAGE_NOISE_RE.sub("", clean)

    for line in clean.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Match bare app numbers: YYYY/NNNNN (5-6 digits)
        m = re.match(r"^(\d{4}/\d{3,6})\s*$", line)
        if m:
            events.append({
                "application_no": m.group(1),
                "registration_no": None,
                "event_type": "withdrawal",
                "event_subtype": None,
                "source_type": source_type,
                "bulletin_no": bulletin_no,
                "bulletin_date": bulletin_date,
                "page_number": 0,
                "old_value": None,
                "new_value": None,
                "details": {},
                "raw_text": m.group(1),
            })

    return events


# ---------------------------------------------------------------------------
# Main extraction orchestrator
# ---------------------------------------------------------------------------
PARSER_DISPATCH = {
    "transfer": parse_transfer_records,
    "court": parse_court_records,
    "simple": parse_simple_records,
    "correction": parse_correction_prose,
}


def extract_events_from_pdf(
    pdf_path: Path,
    source_type: str,
    bulletin_no: str,
    bulletin_date: str,
) -> Dict[str, Any]:
    """Extract all events from a bulletin PDF.

    Args:
        pdf_path: Path to the PDF file
        source_type: "GZ" or "BLT"
        bulletin_no: Bulletin number (e.g. "499")
        bulletin_date: Publication date (e.g. "2026-01-30")

    Returns:
        {
            "status": "success" | "failed",
            "source_type": "GZ" | "BLT",
            "bulletin_no": "499",
            "events": [...],
            "stats": {"transfer": 50, "seizure": 30, ...},
            "errors": [...]
        }
    """
    fitz = _get_fitz()
    if fitz is None:
        return {"status": "failed", "error": "PyMuPDF not installed"}

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        return {"status": "failed", "error": f"Cannot open PDF: {e}"}

    logger.info(f"Opened {pdf_path.name} ({doc.page_count} pages, source={source_type})")

    all_events = []
    errors = []
    headers = GZ_SECTION_HEADERS if source_type == "GZ" else BLT_SECTION_HEADERS

    # --- Phase 1: Main events section (structured (210) records) ---
    events_start, events_end = _find_events_page_range(doc, source_type)
    logger.info(f"Events section: pages {events_start + 1}–{events_end}")

    pages = _extract_pages_text(doc, events_start, events_end)
    full_text = _pages_to_text(pages)

    subsections = _split_into_subsections(full_text, headers)
    logger.info(f"Found {len(subsections)} sub-sections in events section")

    for event_type, parser_type, header, section_text, page_no in subsections:
        try:
            parser_fn = PARSER_DISPATCH.get(parser_type)
            if parser_fn is None:
                errors.append(f"Unknown parser type: {parser_type} for {header}")
                continue

            if parser_type == "correction":
                records = parser_fn(section_text, source_type, bulletin_no, bulletin_date)
            else:
                records = parser_fn(
                    section_text, event_type, source_type, bulletin_no, bulletin_date,
                )
            all_events.extend(records)
            logger.info(f"    {header}: {len(records)} events extracted")
        except Exception as e:
            errors.append(f"Error parsing {header}: {e}")
            logger.exception(f"Error parsing {header}")

    # --- Phase 1b: Bare app number fallback ---
    # Some BLT bulletins have Şerhleri sections with only bare YYYY/NNNNN numbers
    # (no sub-section headers, no (210) prefix). These are withdrawal lists.
    # Only use this fallback when:
    #  - No sub-sections were found in a defined events range
    #  - The range is NOT the full document (which would mean we couldn't locate the section)
    events_range_is_targeted = (events_start > 0) or (events_end < doc.page_count)
    if not subsections and events_range_is_targeted and events_start < doc.page_count:
        try:
            bare_events = parse_bare_app_number_list(
                full_text, source_type, bulletin_no, bulletin_date,
            )
            if bare_events:
                all_events.extend(bare_events)
                logger.info(f"  Bare app number fallback: {len(bare_events)} withdrawals")
        except Exception as e:
            errors.append(f"Error parsing bare app numbers: {e}")

    # --- Phase 2: Düzeltmeler (corrections) — separate TOC section ---
    corrections_range = _find_corrections_page_range(doc, source_type)
    if corrections_range:
        logger.info(f"Corrections section: pages {corrections_range[0] + 1}–{corrections_range[1]}")
        corr_pages = _extract_pages_text(doc, corrections_range[0], corrections_range[1])
        corr_text = _pages_to_text(corr_pages)
        try:
            corr_events = parse_correction_prose(corr_text, source_type, bulletin_no, bulletin_date)
            all_events.extend(corr_events)
            logger.info(f"  Corrections: {len(corr_events)} events")
        except Exception as e:
            errors.append(f"Error parsing corrections: {e}")

    # --- Phase 3: Madrid annotations (BLT only) ---
    if source_type == "BLT":
        madrid_range = _find_madrid_page_range(doc)
        if madrid_range:
            logger.info(f"Madrid section: pages {madrid_range[0] + 1}–{madrid_range[1]}")
            madrid_pages = _extract_pages_text(doc, madrid_range[0], madrid_range[1])
            madrid_text = _pages_to_text(madrid_pages)
            try:
                madrid_events = parse_madrid_records(
                    madrid_text, source_type, bulletin_no, bulletin_date,
                )
                all_events.extend(madrid_events)
                logger.info(f"  Madrid: {len(madrid_events)} events")
            except Exception as e:
                errors.append(f"Error parsing Madrid: {e}")

    # --- Phase 4: Renewals (GZ only) ---
    if source_type == "GZ":
        renewals_range = _find_renewals_page_range(doc)
        if renewals_range:
            logger.info(f"Renewals section: pages {renewals_range[0] + 1}–{renewals_range[1]}")
            ren_pages = _extract_pages_text(doc, renewals_range[0], renewals_range[1])
            ren_text = _pages_to_text(ren_pages)
            try:
                ren_events = parse_renewal_list(
                    ren_text, source_type, bulletin_no, bulletin_date,
                )
                all_events.extend(ren_events)
                logger.info(f"  Renewals: {len(ren_events)} events")
            except Exception as e:
                errors.append(f"Error parsing renewals: {e}")

    doc.close()

    # --- Rename fields for GZ sources ---
    if source_type == "GZ":
        for ev in all_events:
            ev["gazette_no"] = ev.pop("bulletin_no")
            ev["gazette_date"] = ev.pop("bulletin_date")

    # --- Stats ---
    stats = {}
    for ev in all_events:
        t = ev["event_type"]
        stats[t] = stats.get(t, 0) + 1

    logger.info(f"Total: {len(all_events)} events. Stats: {stats}")
    if errors:
        logger.warning(f"Errors: {errors}")

    # Use source-appropriate field names in the result envelope
    no_key = "gazette_no" if source_type == "GZ" else "bulletin_no"
    date_key = "gazette_date" if source_type == "GZ" else "bulletin_date"

    return {
        "status": "success",
        "source_type": source_type,
        no_key: bulletin_no,
        date_key: bulletin_date,
        "events": all_events,
        "stats": stats,
        "total": len(all_events),
        "errors": errors,
    }


def extract_events_from_folder(
    folder: Path,
    source_type: str,
    bulletin_no: str,
    bulletin_date: str,
) -> Dict[str, Any]:
    """Extract events from all PDFs in a bulletin folder.

    For multi-PDF folders (Era 1 BLTs split by page range), tries each PDF
    and merges results. For single-PDF folders, delegates to extract_events_from_pdf.
    """
    pdfs = sorted(folder.glob("*.pdf"), key=lambda p: p.stat().st_size, reverse=True)
    if not pdfs:
        return {"status": "failed", "error": "No PDFs found in folder"}

    # Single PDF: use the standard path
    if len(pdfs) == 1:
        return extract_events_from_pdf(pdfs[0], source_type, bulletin_no, bulletin_date)

    # Check for bulletin.pdf or ulusal.pdf first
    for name in ("bulletin.pdf", "ulusal.pdf"):
        candidate = folder / name
        if candidate.exists() and candidate.stat().st_size > 0:
            return extract_events_from_pdf(candidate, source_type, bulletin_no, bulletin_date)

    # Multi-PDF: extract from each part and merge
    logger.info(f"Multi-PDF folder: {len(pdfs)} PDFs found, extracting from each")
    all_events = []
    all_errors = []
    all_stats: Dict[str, int] = {}

    for pdf in pdfs:
        try:
            result = extract_events_from_pdf(pdf, source_type, bulletin_no, bulletin_date)
            if result.get("status") == "success" and result.get("total", 0) > 0:
                all_events.extend(result.get("events", []))
                for k, v in result.get("stats", {}).items():
                    all_stats[k] = all_stats.get(k, 0) + v
                logger.info(f"  {pdf.name}: {result['total']} events")
            all_errors.extend(result.get("errors", []))
        except Exception as e:
            all_errors.append(f"Error processing {pdf.name}: {e}")

    no_key = "gazette_no" if source_type == "GZ" else "bulletin_no"
    date_key = "gazette_date" if source_type == "GZ" else "bulletin_date"

    return {
        "status": "success",
        "source_type": source_type,
        no_key: bulletin_no,
        date_key: bulletin_date,
        "events": all_events,
        "stats": all_stats,
        "total": len(all_events),
        "errors": all_errors,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _detect_source_type(folder_name: str) -> str:
    """Detect GZ or BLT from folder name."""
    if folder_name.startswith("GZ_"):
        return "GZ"
    return "BLT"


def _parse_folder_info(folder_name: str) -> Tuple[str, str, str]:
    """Extract source_type, bulletin_no, bulletin_date from folder name."""
    source_type = _detect_source_type(folder_name)
    # Pattern: GZ_499_2026-01-30 or BLT_488_2026-03-12
    m = re.match(r"(?:GZ|BLT)_(\d+)(?:_(\d{4}-\d{2}-\d{2}))?", folder_name)
    if m:
        return source_type, m.group(1), m.group(2) or ""
    return source_type, "", ""


if __name__ == "__main__":
    import argparse

    # Force UTF-8 output on Windows
    if sys.platform == "win32":
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    parser = argparse.ArgumentParser(description="Extract events from trademark bulletin PDFs")
    parser.add_argument("--pdf", type=str, required=True, help="Path to bulletin PDF")
    parser.add_argument("--source", type=str, choices=["GZ", "BLT"], default=None,
                        help="Source type (auto-detected from folder name if omitted)")
    parser.add_argument("--bulletin-no", type=str, default=None, help="Bulletin number")
    parser.add_argument("--bulletin-date", type=str, default=None, help="Bulletin date YYYY-MM-DD")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file path")
    parser.add_argument("--sample", type=int, default=0,
                        help="Print N sample events per type instead of full output")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}")
        sys.exit(1)

    # Auto-detect from folder name
    folder_name = pdf_path.parent.name
    source_type = args.source or _detect_source_type(folder_name)
    _, auto_no, auto_date = _parse_folder_info(folder_name)
    bulletin_no = args.bulletin_no or auto_no
    bulletin_date = args.bulletin_date or auto_date

    if not bulletin_no:
        print("ERROR: Could not detect bulletin number. Use --bulletin-no.")
        sys.exit(1)

    print(f"Extracting events from: {pdf_path.name}")
    print(f"Source: {source_type}, Bulletin: {bulletin_no}, Date: {bulletin_date}")
    print()

    result = extract_events_from_pdf(pdf_path, source_type, bulletin_no, bulletin_date)

    print(f"\n{'='*60}")
    print(f"RESULTS: {result['total']} events extracted")
    print(f"{'='*60}")
    print(f"Stats by event type:")
    for event_type, count in sorted(result.get("stats", {}).items(), key=lambda x: -x[1]):
        print(f"  {event_type:30s} {count:>5d}")

    if result.get("errors"):
        print(f"\nErrors ({len(result['errors'])}):")
        for err in result["errors"]:
            print(f"  - {err}")

    # Print samples
    if args.sample > 0:
        print(f"\n{'='*60}")
        print(f"SAMPLE EVENTS ({args.sample} per type)")
        print(f"{'='*60}")
        seen_types = {}
        for ev in result.get("events", []):
            t = ev["event_type"]
            if t not in seen_types:
                seen_types[t] = 0
            if seen_types[t] < args.sample:
                seen_types[t] += 1
                print(f"\n--- {t} (#{seen_types[t]}) ---")
                # Print key fields, skip raw_text
                for k, v in ev.items():
                    if k == "raw_text":
                        print(f"  raw_text: {v[:150]}...")
                    else:
                        print(f"  {k}: {v}")

    # Save to file
    if args.output:
        out_path = Path(args.output)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"\nSaved to {out_path}")
    elif not args.sample:
        # Default: save next to PDF
        out_path = pdf_path.parent / "events.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"\nSaved to {out_path}")

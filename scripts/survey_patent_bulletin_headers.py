"""Scan patent bulletin PDFs for section header phrases.

Renders the text of each page, looks for capitalised-banner lines
(likely section headers), and dumps a sorted frequency table. Helps
expand `pdf_extract_patent_events._SECTION_HEADERS_TO_EVENT_TYPE`
when we discover the parser is missing sub-headers.

Usage:
  python scripts/survey_patent_bulletin_headers.py \
    bulletins/Patent__Faydali_Model/PT_2025_3_2025-03-21 \
    bulletins/Patent__Faydali_Model/PT_2024_6_2024-06-21 \
    bulletins/Patent__Faydali_Model/PT_2020_12_2020-12-21
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("PyMuPDF (fitz) not installed", file=sys.stderr)
    sys.exit(1)


# A line is a candidate section header if it's ALL CAPS, long enough
# to be a real banner (>= 30 chars), and contains at least one space
# (filters single-word page numbers / titles / headers like "PATENT").
_HEADER_RE = re.compile(r"^[A-ZÇĞİÖŞÜ0-9 /().\-,'’]{30,}$")

# Mixed-case sub-section headers (like "Kesinleşen Patent Verilme...")
# are harder to filter. They typically end with a parenthesised cite
# like "(6769 SMK)" or "(551 KHK)". Use that as the strong signal.
_MIXED_CASE_HEADER_RE = re.compile(
    r"^[A-ZÇĞİÖŞÜa-zçğıöşü0-9 /().\-,'’]{30,}\((6769 SMK|551 KHK|551 Sayılı KHK)\)\s*$"
)


def scan_pdf(pdf_path: Path) -> Counter:
    headers: Counter = Counter()
    with fitz.open(str(pdf_path)) as doc:
        for i in range(doc.page_count):
            text = doc[i].get_text("text")
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if _HEADER_RE.match(line) or _MIXED_CASE_HEADER_RE.match(line):
                    headers[line] += 1
    return headers


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    total: Counter = Counter()
    for folder in argv:
        pdf = Path(folder) / "bulletin.pdf"
        if not pdf.is_file():
            print(f"skip {folder} — no bulletin.pdf", file=sys.stderr)
            continue
        print(f"-- {pdf}", file=sys.stderr)
        total += scan_pdf(pdf)
    # Show headers that look like real section banners — present in
    # multiple bulletins is a strong signal, but single-bulletin ones
    # matter too if they're rare. Print everything sorted by frequency.
    print(f"\n=== Header candidates ({len(total)} unique) ===\n")
    for header, count in total.most_common():
        print(f"{count:5d}  {header}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

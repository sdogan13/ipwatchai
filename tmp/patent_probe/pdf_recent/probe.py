"""
Patent / Faydalı Model bulletin PDF probe.
Reads bulletins/Patent__Faydali_Model/2025_08.pdf and emits an analysis dump.

All output goes to ./report.txt (next to this script). Read-only on bulletins.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz  # PyMuPDF

REPO_ROOT = Path(r"C:\Users\701693\turk_patent")
PDF_PATH = REPO_ROOT / "bulletins" / "Patent__Faydali_Model" / "2025_08.pdf"
OUT_DIR = Path(__file__).resolve().parent
REPORT = OUT_DIR / "report.txt"
SAMPLES_DIR = OUT_DIR / "samples"
SAMPLES_DIR.mkdir(exist_ok=True)
FIG_DIR = OUT_DIR / "figures"
FIG_DIR.mkdir(exist_ok=True)

INID_RE = re.compile(r"\((\d{2,3})\)")  # patents commonly use 2-digit codes (51), (54)


def banner(fh, title: str) -> None:
    fh.write("\n" + "=" * 78 + "\n")
    fh.write(title + "\n")
    fh.write("=" * 78 + "\n")


def main() -> None:
    if not PDF_PATH.exists():
        sys.exit(f"PDF not found: {PDF_PATH}")

    size_bytes = PDF_PATH.stat().st_size
    doc = fitz.open(str(PDF_PATH))

    with REPORT.open("w", encoding="utf-8") as fh:
        # ------------------------------------------------------------------
        # 1. Summary
        # ------------------------------------------------------------------
        banner(fh, "1. PDF SUMMARY")
        fh.write(f"Path: {PDF_PATH}\n")
        fh.write(f"File size: {size_bytes:,} bytes ({size_bytes / 1024 / 1024:.2f} MiB)\n")
        fh.write(f"Page count: {doc.page_count}\n")
        meta = doc.metadata or {}
        for k in ("title", "author", "subject", "keywords", "creator", "producer",
                  "creationDate", "modDate", "format", "encryption"):
            fh.write(f"  meta.{k}: {meta.get(k)!r}\n")

        toc = doc.get_toc(simple=False)
        fh.write(f"TOC entries (outline): {len(toc)}\n")
        if toc:
            for lvl, title, page, *rest in toc[:200]:
                fh.write(f"  L{lvl}  p{page:>4}  {title}\n")

        # ------------------------------------------------------------------
        # 2. Section structure — scan first ~30 pages for cover/TOC
        # ------------------------------------------------------------------
        banner(fh, "2. FIRST 8 PAGES (cover + TOC text)")
        for p in range(min(8, doc.page_count)):
            text = doc[p].get_text("text")
            fh.write(f"\n--- page {p + 1} ---\n")
            fh.write(text[:2500])
            fh.write("\n")

        # Heuristic section detection: scan all pages for big-header lines
        banner(fh, "2b. DETECTED SECTION HEADERS (heuristic)")
        section_keywords = [
            "PATENT BAŞVURU", "FAYDALI MODEL BAŞVURU",
            "VERİLEN PATENT", "VERİLEN FAYDALI MODEL",
            "TESCİL EDİLEN PATENT", "TESCİL EDİLEN FAYDALI MODEL",
            "YAYIMLANAN", "YAYINLANAN", "YAYIMLANMIŞ",
            "İNCELEMESİZ", "İNCELEMELİ",
            "İTİRAZ", "DEVİR", "DEVIR", "LİSANS", "LISANS",
            "HÜKÜMSÜZ", "YENİLEME", "ÜCRET", "RÜÇHAN",
            "KAPAK", "İÇİNDEKİLER",
            "AVRUPA PATENT", "EPC", "PCT",
            "ULUSAL AŞAMA",
        ]
        section_hits: list[tuple[int, str]] = []
        for p in range(doc.page_count):
            ptext = doc[p].get_text("text")
            head = ptext.strip().split("\n", 8)[:8]
            for line in head:
                up = line.strip().upper()
                if any(kw in up for kw in section_keywords) and len(up) < 120:
                    section_hits.append((p + 1, line.strip()))
                    break
        fh.write(f"Total candidate header lines: {len(section_hits)}\n")
        for pno, line in section_hits[:120]:
            fh.write(f"  p{pno:>5}: {line}\n")

        # ------------------------------------------------------------------
        # 3. Sample raw text records — pull text from a handful of pages
        # ------------------------------------------------------------------
        banner(fh, "3. SAMPLE RAW TEXT (selected pages)")
        # Pick pages spread across the document
        sample_pages = sorted({
            5, 25, 50, 100,
            doc.page_count // 4,
            doc.page_count // 2,
            (3 * doc.page_count) // 4,
            doc.page_count - 50,
            doc.page_count - 10,
        })
        sample_pages = [p for p in sample_pages if 0 <= p < doc.page_count]
        for p in sample_pages:
            text = doc[p].get_text("text")
            (SAMPLES_DIR / f"page_{p + 1:05d}.txt").write_text(text, encoding="utf-8")
            fh.write(f"\n--- page {p + 1} (chars={len(text)}) ---\n")
            fh.write(text[:3000])
            fh.write("\n")

        # ------------------------------------------------------------------
        # 4. INID code frequency across the whole document
        # ------------------------------------------------------------------
        banner(fh, "4. INID CODE FREQUENCY (whole PDF)")
        inid_counter: Counter[str] = Counter()
        page_codes_first_appearance: dict[str, int] = {}
        # Walk pages — get one big concatenation cheaply
        all_text_parts: list[str] = []
        for p in range(doc.page_count):
            t = doc[p].get_text("text")
            all_text_parts.append(t)
            for m in INID_RE.finditer(t):
                code = m.group(1)
                inid_counter[code] += 1
                page_codes_first_appearance.setdefault(code, p + 1)
        full_text = "\n".join(all_text_parts)
        fh.write(f"Distinct codes found: {len(inid_counter)}\n")
        for code, n in inid_counter.most_common():
            fh.write(f"  ({code}) -> {n:>6} hits  first p{page_codes_first_appearance[code]}\n")

        # ------------------------------------------------------------------
        # 5. IPC class samples — capture what follows '(51)' in text
        # ------------------------------------------------------------------
        banner(fh, "5. IPC SAMPLES (after '(51)' marker)")
        ipc_samples: list[str] = []
        # Window 200 chars after each (51) occurrence
        for m in re.finditer(r"\(51\)", full_text):
            window = full_text[m.end(): m.end() + 250]
            # cut at next INID code
            nxt = INID_RE.search(window)
            if nxt:
                window = window[: nxt.start()]
            window = window.strip()
            if window:
                ipc_samples.append(window)
            if len(ipc_samples) >= 30:
                break
        for i, s in enumerate(ipc_samples[:30], 1):
            cls_lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
            fh.write(f"\nSample {i:02d} -> {len(cls_lines)} line(s):\n")
            for ln in cls_lines:
                fh.write(f"   {ln}\n")

        # Cardinality histogram (lines per (51) block)
        cardinality = Counter()
        ipc_token_re = re.compile(r"[A-H]\d{2}[A-Z]\s*\d+/\d+")
        for s in ipc_samples:
            n = len(ipc_token_re.findall(s))
            cardinality[n] += 1
        fh.write(f"\nIPC tokens-per-record distribution (first 30 samples): {dict(cardinality)}\n")

        # ------------------------------------------------------------------
        # 6. Image / drawing inventory
        # ------------------------------------------------------------------
        banner(fh, "6. IMAGE / DRAWING INVENTORY")
        per_page_counts: list[int] = []
        ext_counter: Counter[str] = Counter()
        size_buckets: Counter[str] = Counter()
        total_images = 0
        sample_extracted = 0
        figure_pages_with_images: list[tuple[int, int]] = []
        for p in range(doc.page_count):
            page = doc[p]
            imgs = page.get_images(full=True)
            per_page_counts.append(len(imgs))
            total_images += len(imgs)
            if imgs:
                figure_pages_with_images.append((p + 1, len(imgs)))
            for img in imgs:
                xref = img[0]
                try:
                    info = doc.extract_image(xref)
                except Exception:
                    continue
                ext = info.get("ext", "?")
                w, h = info.get("width", 0), info.get("height", 0)
                ext_counter[ext] += 1
                if w * h <= 0:
                    bucket = "0"
                elif max(w, h) < 100:
                    bucket = "<100"
                elif max(w, h) < 400:
                    bucket = "100-400"
                elif max(w, h) < 1000:
                    bucket = "400-1000"
                else:
                    bucket = ">=1000"
                size_buckets[bucket] += 1
                # Save up to 5 sample figures
                if sample_extracted < 5 and bucket in ("100-400", "400-1000", ">=1000"):
                    out = FIG_DIR / f"p{p + 1:05d}_x{xref}.{ext}"
                    try:
                        out.write_bytes(info["image"])
                        sample_extracted += 1
                    except Exception:
                        pass
        fh.write(f"Total embedded image objects: {total_images}\n")
        fh.write(f"Pages with >=1 image: {sum(1 for c in per_page_counts if c)}\n")
        if per_page_counts:
            non_zero = [c for c in per_page_counts if c]
            fh.write(f"Avg images / image-bearing page: "
                     f"{(sum(non_zero) / len(non_zero)) if non_zero else 0:.2f}\n")
            fh.write(f"Max images on a single page: {max(per_page_counts)}\n")
        fh.write(f"Image format counts: {dict(ext_counter)}\n")
        fh.write(f"Image size buckets:  {dict(size_buckets)}\n")
        fh.write(f"Saved {sample_extracted} sample figures to ./figures/\n")
        fh.write("\nFirst 25 image-bearing pages (page, count):\n")
        for pno, cnt in figure_pages_with_images[:25]:
            fh.write(f"  p{pno:>5}: {cnt}\n")
        if len(figure_pages_with_images) > 25:
            fh.write(f"  ... ({len(figure_pages_with_images) - 25} more)\n")

        # ------------------------------------------------------------------
        # 7. Sample full record text blocks — find 5 records that include (54)+(57)
        # ------------------------------------------------------------------
        banner(fh, "7. SAMPLE RECORD BLOCKS (5)")
        # Search for occurrences of '(11)' or '(21)' as record start.
        # Patent bulletins typically begin records with (11) for granted or (21) for applications.
        record_blocks: list[tuple[int, str]] = []
        # Use page text per page to keep records readable
        for p in range(doc.page_count):
            ptext = doc[p].get_text("text")
            if "(54)" not in ptext or ("(21)" not in ptext and "(11)" not in ptext):
                continue
            record_blocks.append((p + 1, ptext))
            if len(record_blocks) >= 12:
                break
        for i, (pno, ptext) in enumerate(record_blocks[:5], 1):
            fh.write(f"\n--- record-bearing page #{i}  p{pno} ---\n")
            fh.write(ptext[:3500])
            fh.write("\n")

        # ------------------------------------------------------------------
        # 8. Faydalı Model vs Patent split — count by section keywords
        # ------------------------------------------------------------------
        banner(fh, "8. FAYDALI MODEL vs PATENT")
        patent_pages = []
        utility_pages = []
        for pno, line in section_hits:
            up = line.upper()
            if "FAYDALI MODEL" in up:
                utility_pages.append((pno, line))
            elif "PATENT" in up:
                patent_pages.append((pno, line))
        fh.write(f"Patent-section header hits: {len(patent_pages)} (first 10)\n")
        for pno, line in patent_pages[:10]:
            fh.write(f"  p{pno}: {line}\n")
        fh.write(f"Faydalı Model header hits: {len(utility_pages)} (first 10)\n")
        for pno, line in utility_pages[:10]:
            fh.write(f"  p{pno}: {line}\n")

        # ------------------------------------------------------------------
        # 9. Free-text Turkish field labels (peeking at Marka-style labels)
        # ------------------------------------------------------------------
        banner(fh, "9. TURKISH FIELD LABELS NEAR INID CODES")
        # Heuristic: find lines that contain '(NN)' followed by Turkish word
        label_examples: dict[str, set[str]] = defaultdict(set)
        for code in inid_counter:
            # Pattern: (code) <space?> <up-to-60 chars on same/next line>
            for m in re.finditer(rf"\({code}\)\s*([^\n\r]{{1,80}})", full_text):
                snip = m.group(1).strip()
                if snip and not snip.startswith("("):
                    label_examples[code].add(snip[:80])
                if len(label_examples[code]) >= 4:
                    break
        for code in sorted(label_examples, key=lambda c: -inid_counter[c])[:30]:
            fh.write(f"\n({code}) examples ({inid_counter[code]} hits):\n")
            for s in list(label_examples[code])[:4]:
                fh.write(f"   - {s}\n")

    doc.close()
    print(f"Wrote {REPORT}")


if __name__ == "__main__":
    main()

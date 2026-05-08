"""TS_483 Tasarim bulletin probe.

Read-only inspection. Writes only into tmp/tasarim_probe/.
Run with: python tmp/tasarim_probe/probe.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

REPO = Path(__file__).resolve().parents[2]
PDF = REPO / "bulletins" / "Tasarim" / "TS_483_2026-04-24" / "bulletin.pdf"
OUT = Path(__file__).resolve().parent
SAMPLES = OUT / "samples"
FIGS = OUT / "figures"
SAMPLES.mkdir(exist_ok=True)
FIGS.mkdir(exist_ok=True)


def section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def main() -> None:
    section("1. PDF SUMMARY")
    size_mb = PDF.stat().st_size / (1024 * 1024)
    doc = fitz.open(str(PDF))
    md = doc.metadata or {}
    print(f"path: {PDF}")
    print(f"size_mb: {size_mb:.1f}")
    print(f"page_count: {doc.page_count}")
    pdf_ver = "?"
    try:
        x = doc.xref_get_key(-1, "Version") if hasattr(doc, "xref_get_key") else None
        pdf_ver = x or md.get("format") or "?"
    except Exception:
        pdf_ver = md.get("format") or "?"
    print(f"pdf_version: {pdf_ver}")
    print(f"producer: {md.get('producer')}")
    print(f"creator: {md.get('creator')}")
    print(f"title: {md.get('title')}")
    print(f"creationDate: {md.get('creationDate')}")
    print(f"modDate: {md.get('modDate')}")

    toc = doc.get_toc()
    print(f"toc_entries: {len(toc)}")
    for entry in toc[:40]:
        print(f"  toc {entry}")

    section("2. COVER + INDEX (pages 1..6)")
    cover_dump = []
    for pno in range(min(6, doc.page_count)):
        text = doc[pno].get_text("text")
        cover_dump.append(f"---- PAGE {pno+1} ----\n{text}")
    (SAMPLES / "cover_pages_1_6.txt").write_text("\n".join(cover_dump), encoding="utf-8")
    # Print first 4 pages for cover analysis
    for pno in range(min(4, doc.page_count)):
        text = doc[pno].get_text("text")
        print(f"\n-- page {pno+1} (first 800 chars) --")
        print(text[:800])

    section("3. INID / FIELD-MARKER FREQUENCY (sample 200 pages, evenly spaced)")
    # sample evenly across the doc
    n = doc.page_count
    sample_pages = sorted({int(i * (n - 1) / 199) for i in range(200)})
    inid_counter: Counter[str] = Counter()
    label_counter: Counter[str] = Counter()
    label_re = re.compile(
        r"\b(Başvuru Numarası|Başvuru Tarihi|Tescil Numarası|Tescil Tarihi|"
        r"Tasarımcı|Tasarımcılar|Başvuru Sahibi|Başvuru Sahipleri|Vekil|"
        r"Locarno Sınıfı|Locarno|Rüçhan|Öncelik|Ürün Adı|Ürün|"
        r"Tasarım Sayısı|Yayım Tarihi|Yayım No)"
    )
    inid_re = re.compile(r"\((\d{2,3})\)")
    boundary_hits_21 = 0
    boundary_hits_app = 0
    for pno in sample_pages:
        text = doc[pno].get_text("text")
        for m in inid_re.findall(text):
            inid_counter[m] += 1
        for m in label_re.findall(text):
            label_counter[m] += 1
        boundary_hits_21 += len(re.findall(r"\(21\)\s*\d{4}/\d{3,6}", text))
        boundary_hits_app += len(re.findall(r"Başvuru Numarası\s*[:\-]?\s*\d{4}/\d{3,6}", text))
    print("INID 2-3 digit code frequencies (top 25):")
    for code, c in inid_counter.most_common(25):
        print(f"  ({code}) -> {c}")
    print("\nTurkish field-label frequencies:")
    for lbl, c in label_counter.most_common():
        print(f"  {lbl}: {c}")
    print(f"\n(21)+app_no boundary hits across sample: {boundary_hits_21}")
    print(f"'Başvuru Numarası …' boundary hits across sample: {boundary_hits_app}")

    section("4. ESTIMATED RECORD COUNT (full scan)")
    # full-scan boundary detection, two heuristics
    total_inid21 = 0
    total_app_no_label = 0
    pages_with_records = 0
    page_record_starts: list[tuple[int, str]] = []  # (page, app_no)
    boundary_re_inid = re.compile(r"\(21\)\s*(\d{4}/\d{3,6})")
    boundary_re_label = re.compile(r"Başvuru Numarası\s*[:\-]?\s*(\d{4}/\d{3,6})")
    # generic app-no with no marker
    bare_app_re = re.compile(r"^\s*(\d{4}/\d{4,6})\s*$", re.MULTILINE)

    bare_hits_total = 0
    for pno in range(doc.page_count):
        text = doc[pno].get_text("text")
        m1 = boundary_re_inid.findall(text)
        m2 = boundary_re_label.findall(text)
        m3 = bare_app_re.findall(text)
        if m1 or m2:
            pages_with_records += 1
        total_inid21 += len(m1)
        total_app_no_label += len(m2)
        bare_hits_total += len(m3)
        for app in m1 or m2:
            page_record_starts.append((pno + 1, app))
    print(f"total (21)+app_no across all pages: {total_inid21}")
    print(f"total 'Başvuru Numarası …' across all pages: {total_app_no_label}")
    print(f"total bare YYYY/NNNNNN-on-own-line hits: {bare_hits_total}")
    print(f"pages_with_records: {pages_with_records}")

    section("5. SECTION STRUCTURE (heuristic header scan, first 30 + last 10 pages)")
    header_keywords = [
        "İÇİNDEKİLER", "BAŞVURULAR", "TESCİLLER", "TASARIM",
        "ULUSLARARASI", "LAHEY", "HAGUE", "DM/", "ERTELEME",
        "İTİRAZ", "YAYIM", "DÜZELTME", "İPTAL", "DEVAM",
        "ENDÜSTRİYEL TASARIM", "YENİLEME", "BÜLTEN",
    ]
    pages_to_scan = list(range(min(30, doc.page_count))) + list(range(max(0, doc.page_count - 10), doc.page_count))
    for pno in pages_to_scan:
        text = doc[pno].get_text("text")
        head = text[:300].replace("\n", " ⏎ ")
        # mark candidate header
        if any(k in text[:400].upper() for k in [k.upper() for k in header_keywords]):
            print(f"  page {pno+1}: {head[:200]}")

    section("6. PER-PAGE IMAGE STATS + UNIQUE XREFS")
    unique_xrefs: set[int] = set()
    total_refs = 0
    page_img_counts: list[int] = []
    for pno in range(doc.page_count):
        info = doc[pno].get_images(full=True)
        page_img_counts.append(len(info))
        total_refs += len(info)
        for tup in info:
            unique_xrefs.add(tup[0])
    print(f"total image references: {total_refs}")
    print(f"unique image xrefs: {len(unique_xrefs)}")
    if page_img_counts:
        avg = sum(page_img_counts) / len(page_img_counts)
        print(f"images-per-page: min={min(page_img_counts)}, avg={avg:.2f}, max={max(page_img_counts)}")
    # distribution buckets
    buckets = Counter()
    for c in page_img_counts:
        if c == 0:
            buckets["0"] += 1
        elif c <= 4:
            buckets["1-4"] += 1
        elif c <= 8:
            buckets["5-8"] += 1
        elif c <= 16:
            buckets["9-16"] += 1
        else:
            buckets["17+"] += 1
    print("page-image distribution:", dict(buckets))

    section("7. PICK 5 REPRESENTATIVE RECORDS (first 200 records)")
    # Walk the document and slice text between consecutive (21) markers.
    # Build a flat list of (page_no, char_offset_in_page_text, app_no) using span-level info.
    record_starts: list[dict] = []
    for pno in range(doc.page_count):
        text = doc[pno].get_text("text")
        for m in boundary_re_inid.finditer(text):
            record_starts.append({"page": pno + 1, "offset": m.start(), "app_no": m.group(1), "text": text})
    print(f"total record starts found: {len(record_starts)}")

    def slice_record(idx: int) -> str:
        cur = record_starts[idx]
        nxt = record_starts[idx + 1] if idx + 1 < len(record_starts) else None
        if nxt and nxt["page"] == cur["page"]:
            return cur["text"][cur["offset"]:nxt["offset"]]
        # spans into next page(s)
        chunk = [cur["text"][cur["offset"]:]]
        end_page = nxt["page"] if nxt else cur["page"] + 1
        for p in range(cur["page"] + 1, min(end_page, doc.page_count + 1)):
            page_text = doc[p - 1].get_text("text")
            if nxt and p == nxt["page"]:
                chunk.append(page_text[: nxt["offset"]])
            else:
                chunk.append(page_text)
        return "\n".join(chunk)

    # 1: simple (1st record)
    # 2: a record where the slice contains "Tasarım Sayısı" >1 or multiple Locarno entries
    # 3: priority — slice contains 'Rüçhan' or '(30)'
    # 4: Hague — slice contains 'DM/' or 'Lahey' or 'Hague'
    # 5: deferred — slice contains 'erteleme' or no images on its page
    picks: dict[str, int] = {}
    multi_re = re.compile(r"Tasarım Sayısı\s*[:\-]?\s*([2-9]|\d{2,})", re.IGNORECASE)
    prio_re = re.compile(r"(Rüçhan|\(30\)|Öncelik)", re.IGNORECASE)
    hague_re = re.compile(r"(DM/\d|Lahey|Hague)", re.IGNORECASE)
    deferred_re = re.compile(r"(erteleme|deferred)", re.IGNORECASE)
    for idx in range(len(record_starts)):
        body = slice_record(idx)
        if "simple" not in picks and idx == 0:
            picks["simple"] = idx
        if "multi" not in picks and multi_re.search(body):
            picks["multi"] = idx
        if "priority" not in picks and prio_re.search(body):
            picks["priority"] = idx
        if "hague" not in picks and hague_re.search(body):
            picks["hague"] = idx
        if "deferred" not in picks and deferred_re.search(body):
            picks["deferred"] = idx
        if len(picks) == 5:
            break
    print("picks:", {k: (v, record_starts[v]["app_no"], record_starts[v]["page"]) for k, v in picks.items()})

    for label, idx in picks.items():
        body = slice_record(idx)
        meta = record_starts[idx]
        out_path = SAMPLES / f"record_{label}_{meta['app_no'].replace('/', '-')}.txt"
        out_path.write_text(body[:6000], encoding="utf-8")
        print(f"\n--- {label} (app_no={meta['app_no']} page={meta['page']}) ---")
        print(body[:1800])

    section("8. MULTI-DESIGN RECORD ANALYSIS")
    # Look at first ~100 records to gauge how 'Tasarım Sayısı' values are distributed
    counts = Counter()
    sample_size = min(800, len(record_starts))
    for idx in range(sample_size):
        body = slice_record(idx)
        m = multi_re.search(body)
        if m:
            try:
                counts[int(m.group(1))] += 1
            except ValueError:
                pass
        else:
            # also check for explicit single-design phrasing
            counts[1] += 1
    print(f"design-count distribution over first {sample_size} records:")
    for k, v in sorted(counts.items()):
        print(f"  {k} designs: {v}")

    section("9. SAMPLE 10 RECORDS — VIEWS / LOCARNO / DESIGNER")
    locarno_re = re.compile(r"\b(\d{2})[\.\-](\d{2})\b")
    view_re = re.compile(r"\b(\d+\.\d+)\b")  # e.g. 1.1, 1.2 view-numbering
    sampled = list(range(0, min(len(record_starts), 5000), max(1, len(record_starts) // 10)))[:10]
    for idx in sampled:
        body = slice_record(idx)
        meta = record_starts[idx]
        loc = locarno_re.findall(body[:1000])
        ts = multi_re.search(body)
        des = re.search(r"Tasarımcı(?:\s*\(?lar\)?)?\s*[:\-]?\s*([^\n]{2,120})", body)
        prod = re.search(r"Ürün(?:\s*Adı)?\s*[:\-]?\s*([^\n]{2,120})", body)
        print(
            f"  [{idx:5d}] app={meta['app_no']:>11} page={meta['page']:4d} "
            f"locarno={loc[:3]} design_count={ts.group(1) if ts else '?'} "
            f"designer={(des.group(1).strip() if des else '?')[:60]!r} "
            f"product={(prod.group(1).strip() if prod else '?')[:60]!r}"
        )

    section("10. DEFERRED + HAGUE SCAN")
    deferred_pages = []
    hague_pages = []
    for pno in range(doc.page_count):
        t = doc[pno].get_text("text")
        if deferred_re.search(t):
            deferred_pages.append(pno + 1)
        if hague_re.search(t):
            hague_pages.append(pno + 1)
    print(f"deferred-publication pages: {len(deferred_pages)}; first 10 -> {deferred_pages[:10]}")
    print(f"hague-reference pages:      {len(hague_pages)}; first 10 -> {hague_pages[:10]}")

    section("11. DUMP A FEW IMAGES (first 5 unique xrefs)")
    saved = 0
    seen: set[int] = set()
    for pno in range(doc.page_count):
        for tup in doc[pno].get_images(full=True):
            xref = tup[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                fp = FIGS / f"xref_{xref}_p{pno+1}.png"
                pix.save(str(fp))
                saved += 1
            except Exception as exc:  # pragma: no cover
                print(f"  skip xref={xref}: {exc}")
            if saved >= 5:
                break
        if saved >= 5:
            break
    print(f"saved {saved} sample images to {FIGS}")

    section("12. RECORD-BOUNDARY VERIFICATION")
    # Confirm (21) doesn't false-positive in body text
    # Check whether (21) ever appears NOT followed by an app number
    bare_inid_21 = 0
    for pno in range(min(50, doc.page_count)):
        text = doc[pno].get_text("text")
        for m in re.finditer(r"\(21\)", text):
            tail = text[m.end():m.end()+40]
            if not re.match(r"\s*\d{4}/\d{3,6}", tail):
                bare_inid_21 += 1
    print(f"(21) NOT followed by app_no in first 50 pages: {bare_inid_21}")

    doc.close()
    print("\nDONE.")


if __name__ == "__main__":
    main()

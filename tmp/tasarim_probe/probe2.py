"""TS_483 follow-up probe.

Re-runnable. Refines:
- multi-design detection via (28) field
- Hague section structure (pages 477+)
- bulletin sequential list (pages 9-13)
- record-spanning pages
- text mode comparison (raw vs blocks)
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import fitz

REPO = Path(__file__).resolve().parents[2]
PDF = REPO / "bulletins" / "Tasarim" / "TS_483_2026-04-24" / "bulletin.pdf"
OUT = Path(__file__).resolve().parent
SAMPLES = OUT / "samples"


def section(t: str) -> None:
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


def main() -> None:
    doc = fitz.open(str(PDF))
    n = doc.page_count

    section("A. SEQUENTIAL LIST PAGES 9-13")
    for pno in range(8, 14):
        text = doc[pno].get_text("text")
        print(f"\n-- page {pno+1} (first 1200 chars) --")
        print(text[:1200])

    section("B. (28) DESIGN-COUNT DISTRIBUTION (full doc)")
    pat28 = re.compile(r"\(28\)\s*(\d+)")
    pat21 = re.compile(r"\(21\)\s*(\d{4}/\d{3,6})")
    pat11 = re.compile(r"\(11\)\s*(\d{4}\s?\d{6})")
    counts = Counter()
    n21 = n28 = n11 = 0
    by_count: list[tuple[int, str, int]] = []  # (page, app_no, design_count)
    for pno in range(n):
        t = doc[pno].get_text("text")
        n21 += len(pat21.findall(t))
        n28_local = pat28.findall(t)
        n11 += len(pat11.findall(t))
        for c in n28_local:
            counts[int(c)] += 1
        for m in pat21.finditer(t):
            # find nearest (28) within 300 chars
            tail = t[m.start():m.start()+800]
            m28 = pat28.search(tail)
            by_count.append((pno + 1, m.group(1), int(m28.group(1)) if m28 else -1))
        n28 += len(n28_local)
    print(f"(11) hits: {n11}")
    print(f"(21) hits: {n21}")
    print(f"(28) hits: {n28}")
    print("design-count distribution from (28):", dict(counts))
    # Show 5 largest design records
    by_count.sort(key=lambda r: r[2], reverse=True)
    print("\nTop 10 records by design count:")
    for r in by_count[:10]:
        print(f"  page={r[0]:4d} app={r[1]:>11} designs={r[2]}")

    section("C. ONE TRUE MULTI-DESIGN RECORD")
    # Find first (21) record where (28) >= 5
    target = next((r for r in by_count if r[2] >= 5), None)
    if target:
        page = target[0]
        # dump 4 pages around it
        for pno in range(page - 1, min(n, page + 4)):
            t = doc[pno].get_text("text")
            print(f"\n-- page {pno+1} (first 1500 chars) --")
            print(t[:1500])
        # Save full slice
        full = "\n----PAGE BREAK----\n".join(
            doc[p].get_text("text") for p in range(page - 1, min(n, page + 4))
        )
        (SAMPLES / f"record_multi_{target[1].replace('/', '-')}.txt").write_text(full, encoding="utf-8")

    section("D. HAGUE SECTION (pages 477-484)")
    for pno in range(476, min(n, 484)):
        t = doc[pno].get_text("text")
        print(f"\n-- page {pno+1} (first 1500 chars) --")
        print(t[:1500])
    # Save it
    if n >= 477:
        full = "\n----PAGE BREAK----\n".join(
            doc[p].get_text("text") for p in range(476, min(n, 484))
        )
        (SAMPLES / "section_hague_pages_477_484.txt").write_text(full, encoding="utf-8")

    section("E. DEFERRED SECTION (pages 431-438)")
    for pno in range(430, min(n, 438)):
        t = doc[pno].get_text("text")
        print(f"\n-- page {pno+1} (first 1500 chars) --")
        print(t[:1500])
    if n >= 431:
        full = "\n----PAGE BREAK----\n".join(
            doc[p].get_text("text") for p in range(430, min(n, 438))
        )
        (SAMPLES / "section_deferred_pages_431_438.txt").write_text(full, encoding="utf-8")

    section("F. RAW vs BLOCKS TEXT FOR PAGE 17 (single record)")
    p = doc[16]
    print("=== get_text('text') ===")
    print(p.get_text("text")[:2500])

    section("G. STRUCTURED RECORD WALK — first 12 (21)-records, summarize fields")
    rec_re = re.compile(r"\(21\)\s*(\d{4}/\d{3,6})")
    field_re = re.compile(r"\((\d{2,3})\)|\b(ES|RP)\)")
    # collect record offsets
    rec_locs: list[tuple[int, int, str]] = []  # (page, offset, app_no)
    for pno in range(n):
        t = doc[pno].get_text("text")
        for m in rec_re.finditer(t):
            rec_locs.append((pno, m.start(), m.group(1)))
    print(f"total records: {len(rec_locs)}")

    def slice_full(idx: int) -> str:
        cur = rec_locs[idx]
        nxt = rec_locs[idx + 1] if idx + 1 < len(rec_locs) else None
        text_cur = doc[cur[0]].get_text("text")
        if nxt and nxt[0] == cur[0]:
            return text_cur[cur[1]:nxt[1]]
        chunks = [text_cur[cur[1]:]]
        end_p = nxt[0] if nxt else cur[0] + 1
        for p in range(cur[0] + 1, min(end_p + 1, n)):
            tt = doc[p].get_text("text")
            if nxt and p == nxt[0]:
                chunks.append(tt[: nxt[1]])
                break
            chunks.append(tt)
        return "\n".join(chunks)

    for idx in range(min(12, len(rec_locs))):
        body = slice_full(idx)
        # Field map
        fields: dict[str, list[str]] = {}
        # Match codes like (28) value, (51) value etc — but (21) and (15) appear together.
        # Tokenize on /\((\d{2,3})\)/
        tokens = re.split(r"\((\d{2,3})\)", body)
        # tokens[0] is preamble; tokens[1::2] codes, tokens[2::2] values
        i = 1
        while i < len(tokens):
            code = tokens[i]
            val = tokens[i + 1] if i + 1 < len(tokens) else ""
            # cut value at the first newline that begins with non-space then a (NN)
            val = val.strip()
            fields.setdefault(code, []).append(val[:200])
            i += 2
        # also detect (ES) deferred indicator
        es = re.search(r"\(ES\)\s*([^\n]+)", body)
        if es:
            fields["ES"] = [es.group(1).strip()]
        meta = rec_locs[idx]
        designs_in_views = re.findall(r"^\s*(\d+)\.(\d+)\s+([\wÇĞİıÖŞÜçğöşü\-\s]{2,40}?)\s*$", body, re.MULTILINE)
        n_unique = len({d[0] for d in designs_in_views})
        print(f"\n[{idx:3d}] page={meta[0]+1} app={meta[2]}")
        for code in sorted(fields.keys()):
            vals = fields[code]
            joined = " | ".join(v[:100].replace("\n", " ") for v in vals)
            print(f"   ({code}) {joined}")
        print(f"   views_in_block: {len(designs_in_views)}; unique design indexes: {n_unique}")

    doc.close()


if __name__ == "__main__":
    main()

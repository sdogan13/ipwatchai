"""One-shot data quality audit script."""
import json
import sys
import io
import re
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

ROOT = Path("bulletins/Tasarim/TS_483_2026-04-24")
m = json.load(open(ROOT / "metadata.json", encoding="utf-8"))
records = m["records"]


# Find 2025/002220 and inspect the off-by-one
target = next((r for r in records if r.get("application_no") == "2025/002220"), None)
if target:
    print(f"=== inspect {target['application_no']} ===")
    print(f"section: {target['section']}")
    print(f"page_range: {target['page_range']}")
    print(f"design_count (28): {target['design_count']}")
    print(f"parsed designs: {len(target['designs'])}")
    for d in target["designs"]:
        views = [(v["view_index"], v["page"], v.get("image_path")) for v in d["views"]]
        print(f"  design {d['design_index']} {d['product_name_tr']!r}: {views}")

# Block-text inspection
import fitz
doc = fitz.open(ROOT / "bulletin.pdf")
full_text = "".join(doc[i].get_text("text") for i in range(doc.page_count))
match = re.search(r"\(21\)\s*2025/002220", full_text)
if match:
    nxt = re.search(r"\(21\)\s*\d{4}/\d", full_text[match.end():])
    end = match.end() + nxt.start() if nxt else len(full_text)
    start_back = max(0, match.start() - 120)
    print("\n=== raw text from 120 chars before (21) to next (21) ===")
    print(full_text[start_back:end][:1500])
    print("=== end raw ===")

# Mismatch breakdown
mismatches = []
for r in records:
    if r["section"] not in ("tr_native", "deferred_lifted", "republished"):
        continue
    expected = r.get("design_count", 0)
    actual = len(r.get("designs", []))
    if expected != actual:
        mismatches.append((r.get("application_no"), expected, actual, r["page_range"]))

print(f"\n=== mismatch breakdown ({len(mismatches)} total) ===")
delta_counter = Counter(act - exp for _, exp, act, _ in mismatches)
for delta, count in sorted(delta_counter.items()):
    print(f"  delta {delta:+d}: {count} records")

doc.close()

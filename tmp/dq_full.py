"""Full data-quality audit of the extracted TS_483 metadata."""
import json
import sys
import io
import os
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

ROOT = Path("bulletins/Tasarim/TS_483_2026-04-24")
m = json.load(open(ROOT / "metadata.json", encoding="utf-8"))
records = m["records"]
N = len(records)

print(f"=== TS_483 data quality audit ({N} records) ===")
sec = Counter(r["section"] for r in records)
print(f"sections: {dict(sec)}")


def pct(n, base=N):
    return f"{100*n/base:.1f}%"


print()
print("== field coverage (all records) ==")
print(f"  application_no:     {pct(sum(1 for r in records if r.get('application_no')))}")
print(f"  registration_no:    {pct(sum(1 for r in records if r.get('registration_no')))}")
print(f"  filing_date:        {pct(sum(1 for r in records if r.get('filing_date')))}")
print(f"  registration_date:  {pct(sum(1 for r in records if r.get('registration_date')))}")
print(f"  design_count >=1:   {pct(sum(1 for r in records if r.get('design_count', 0) >= 1))}")
print(f"  locarno_classes:    {pct(sum(1 for r in records if r.get('locarno_classes')))}")
print(f"  >=1 applicant:      {pct(sum(1 for r in records if r.get('applicants')))}")
print(f"  >=1 designer:       {pct(sum(1 for r in records if r.get('designers')))}")
print(f"  attorney present:   {pct(sum(1 for r in records if r.get('attorney')))}")
print(f"  >=1 design parsed:  {pct(sum(1 for r in records if r.get('designs')))}")

# Applicant ID coverage by section
print()
print("== applicant ID coverage by section ==")
for s in sorted(sec):
    rs = [r for r in records if r["section"] == s]
    apps = [a for r in rs for a in r["applicants"]]
    n_id = sum(1 for a in apps if a.get("id"))
    avg = (len(apps) / len(rs)) if rs else 0
    print(f"  {s:14s}: {len(rs):3d} records, {len(apps):4d} applicants ({avg:.1f}/rec), {n_id} with id ({pct(n_id, max(1,len(apps)))})")

# Duplicate appno check
appnos = Counter(r.get("application_no") for r in records if r.get("application_no"))
dupes = {a: c for a, c in appnos.items() if c > 1}
print()
print(f"== duplicate appnos: {len(dupes)} ==")

# design_count match for image-bearing TR sections
mismatches = []
for r in records:
    if r["section"] not in ("tr_native", "deferred_lifted", "republished"):
        continue
    if r.get("design_count", 0) != len(r.get("designs", [])):
        mismatches.append((r["application_no"], r["design_count"], len(r["designs"])))
print(f"== design_count vs parsed_designs mismatches (image-bearing TR): {len(mismatches)} ==")
for ano, exp, act in mismatches[:5]:
    print(f"  {ano}: (28)={exp} parsed={act}")

# Image extraction quality
print()
total_views = sum(len(d["views"]) for r in records for d in r.get("designs", []))
views_with_image = sum(1 for r in records for d in r.get("designs", []) for v in d["views"] if v.get("image_path"))
img_count = len(os.listdir(ROOT / "images")) if (ROOT / "images").is_dir() else 0
print("== image extraction ==")
print(f"  total views in metadata:          {total_views}")
print(f"  views with image_path:            {views_with_image} ({pct(views_with_image, max(1,total_views))})")
print(f"  image files on disk:              {img_count}")

# Page-range distribution
prs = [r["page_range"][1] - r["page_range"][0] + 1 for r in records if r.get("page_range")]
print()
print("== page_range sizes ==")
print(f"  min={min(prs)} max={max(prs)} avg={sum(prs)/len(prs):.2f}")

# Attorney trailing-noise leftovers
import re
bad = [r for r in records if r.get("attorney") and re.search(r"\d+\.\d+\s", r["attorney"].get("name", "") or "")]
print()
print(f"== attorneys with trailing view-label noise: {len(bad)} ==")

# 58-design giant
big = next((r for r in records if r.get("application_no") == "2026/002401"), None)
if big:
    print()
    print(f"== canary 2026/002401 (58-design giant) ==")
    print(f"  (28) design_count: {big['design_count']}")
    print(f"  parsed designs:    {len(big['designs'])}")
    print(f"  total views:       {sum(len(d['views']) for d in big['designs'])}")
    print(f"  page_range:        {big['page_range']}")

# Hague canary
hague = next((r for r in records if r.get("registration_no") == "DM 244882"), None)
if hague:
    print()
    print(f"== canary DM 244882 (Hague) ==")
    print(f"  filing_date:    {hague['filing_date']}")
    print(f"  locarno:        {hague['locarno_classes']}")
    print(f"  states:         {hague['hague_reference']['designated_states']}")
    print(f"  product_en:     {hague['hague_reference']['product_name_en']}")

"""
Test ai.py's batch translation pipeline on real metadata from dataset folders.
Loads trademark names from metadata.json files and runs them through
batch_translate_to_turkish() to verify FastText LangID works on real data.
"""
import sys
sys.path.insert(0, '/app')

import json
import os
import time
from pathlib import Path
from collections import Counter

from utils.translation import (
    batch_translate_to_turkish, detect_language_fasttext, turkish_lower
)

DATA_ROOT = Path("/app/bulletins/Marka")

# Pick a mix of folder types: BLT (Turkish bulletins), GZ (gazettes), APP (applications)
test_folders = []
for prefix in ['BLT_', 'GZ_', 'APP_']:
    found = sorted([f for f in DATA_ROOT.iterdir() if f.is_dir() and f.name.startswith(prefix)])
    test_folders.extend(found[:3])  # Take first 3 of each type

print(f"Testing {len(test_folders)} folders")
print("=" * 80)

all_names = []
folder_names = []

for folder in test_folders:
    meta_path = folder / "metadata.json"
    if not meta_path.exists():
        print(f"  SKIP {folder.name} (no metadata.json)")
        continue

    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"  SKIP {folder.name} (JSON error: {e})")
        continue

    records = data if isinstance(data, list) else [data]
    names = []
    for rec in records:
        tm = rec.get("TRADEMARK", {})
        name = tm.get("NAME", "")
        if name and name.strip():
            names.append(name.strip())

    if names:
        # Sample up to 50 names per folder
        sample = names[:50]
        all_names.extend(sample)
        folder_names.extend([(folder.name, n) for n in sample])
        print(f"  {folder.name:30s}: {len(names):>5} total names, sampling {len(sample)}")

print(f"\nTotal names to process: {len(all_names)}")
print()

# Step 1: Run FastText detection on all names
print("=" * 80)
print("STEP 1: FastText Language Detection")
print("=" * 80)
start = time.time()
lang_counts = Counter()
detection_results = []
for name in all_names:
    iso, nllb, conf = detect_language_fasttext(name)
    lang_counts[iso] += 1
    detection_results.append((iso, conf))
detect_time = time.time() - start

print(f"  Detection time: {detect_time:.3f}s ({detect_time/len(all_names)*1000:.2f}ms/name)")
print(f"  Language distribution:")
for lang, count in lang_counts.most_common(15):
    pct = count / len(all_names) * 100
    print(f"    {lang:10s}: {count:>5} ({pct:5.1f}%)")

# Step 2: Run batch_translate_to_turkish
print()
print("=" * 80)
print("STEP 2: batch_translate_to_turkish (full pipeline)")
print("=" * 80)
start = time.time()
results = batch_translate_to_turkish(all_names)
translate_time = time.time() - start

print(f"  Translation time: {translate_time:.1f}s ({translate_time/len(all_names)*1000:.1f}ms/name)")

# Analyze results
translated_count = 0
kept_original = 0
lang_after = Counter()
for (name_tr, lang), orig in zip(results, all_names):
    lang_after[lang] += 1
    if turkish_lower(name_tr) != turkish_lower(orig):
        translated_count += 1
    else:
        kept_original += 1

print(f"  Translated (changed): {translated_count}")
print(f"  Kept original:        {kept_original}")
print(f"  Language distribution after:")
for lang, count in lang_after.most_common(15):
    pct = count / len(all_names) * 100
    print(f"    {lang:10s}: {count:>5} ({pct:5.1f}%)")

# Step 3: Show interesting samples
print()
print("=" * 80)
print("STEP 3: Sample Results (showing translations that CHANGED)")
print("=" * 80)
changed = []
for (folder_name, orig), (name_tr, lang) in zip(folder_names, results):
    if turkish_lower(name_tr) != turkish_lower(orig):
        changed.append((folder_name, orig, name_tr, lang))

# Show up to 30 changed names
for folder, orig, name_tr, lang in changed[:30]:
    print(f"  [{lang:2s}] {orig:40s} -> {name_tr:40s} ({folder})")

if len(changed) > 30:
    print(f"  ... and {len(changed) - 30} more")

# Step 4: Show Turkish-detected samples (should NOT be translated)
print()
print("=" * 80)
print("STEP 4: Turkish-detected samples (should keep original)")
print("=" * 80)
tr_samples = [(fn, orig, name_tr, lang) for (fn, orig), (name_tr, lang) in zip(folder_names, results) if lang == 'tr']
for folder, orig, name_tr, lang in tr_samples[:20]:
    match = "OK" if turkish_lower(name_tr) == turkish_lower(orig) else "CHANGED!"
    print(f"  {orig:40s} -> {name_tr:40s} [{match}]")

# Step 5: Spot-check — names with Turkish chars should ALL be detected as Turkish
print()
print("=" * 80)
print("STEP 5: Turkish char validation")
print("=" * 80)
UNIQUE_TR = set('ğĞışŞİ')
misdetected = []
for orig, (iso, conf) in zip(all_names, detection_results):
    if any(c in UNIQUE_TR for c in orig) and iso != 'tr':
        misdetected.append((orig, iso, conf))

if misdetected:
    print(f"  WARNING: {len(misdetected)} names with Turkish chars NOT detected as Turkish:")
    for name, lang, conf in misdetected[:10]:
        print(f"    {name:40s} -> {lang} (conf={conf:.3f})")
else:
    print(f"  ALL {sum(1 for o in all_names if any(c in UNIQUE_TR for c in o))} names with Turkish chars correctly detected as Turkish")

print()
print("=" * 80)
print("DONE")
print("=" * 80)

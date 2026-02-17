"""
Test ai.py's REAL pipeline on actual data folders.
Temporarily clears name_tr/detected_lang from a sample of records in a
metadata.json, runs process_folder(), and checks the results.
"""
import sys
sys.path.insert(0, '/app')

import json
import time
import shutil
from pathlib import Path
from collections import Counter

# Import the actual ai.py pipeline function
from ai import process_folder

DATA_ROOT = Path("/app/bulletins/Marka")

# Pick folders to test — one of each type
test_folders = []
for prefix in ['BLT_', 'GZ_', 'APP_']:
    found = sorted([f for f in DATA_ROOT.iterdir() if f.is_dir() and f.name.startswith(prefix)])
    if found:
        test_folders.append(found[0])

print(f"Testing {len(test_folders)} folders through ai.py process_folder()")
print("=" * 80)

for folder in test_folders:
    meta_path = folder / "metadata.json"
    backup_path = folder / "metadata.json.bak"

    if not meta_path.exists():
        print(f"SKIP {folder.name} (no metadata.json)")
        continue

    # Load metadata
    with open(meta_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    total = len(data)
    print(f"\n{'=' * 80}")
    print(f"FOLDER: {folder.name} ({total} records)")
    print(f"{'=' * 80}")

    # Show current state
    has_tr = sum(1 for r in data if r.get("name_tr") is not None)
    has_lang = sum(1 for r in data if r.get("detected_lang") is not None)
    print(f"  Before: {has_tr}/{total} have name_tr, {has_lang}/{total} have detected_lang")

    # Sample some records that have name_tr to show BEFORE state
    named_records = [(i, r) for i, r in enumerate(data)
                     if r.get("TRADEMARK", {}).get("NAME") and r.get("name_tr")]
    sample_indices = [i for i, _ in named_records[:20]]

    print(f"\n  BEFORE (sample of {len(sample_indices)} records):")
    for idx in sample_indices[:10]:
        r = data[idx]
        name = r.get("TRADEMARK", {}).get("NAME", "?")
        name_tr = r.get("name_tr", "?")
        lang = r.get("detected_lang", "?")
        print(f"    {name:40s} -> name_tr={name_tr:35s} lang={lang}")

    # Backup metadata
    shutil.copy2(meta_path, backup_path)

    # Clear name_tr and detected_lang for our sample records
    # to force ai.py to re-process them
    for idx in sample_indices:
        data[idx]["name_tr"] = None
        data[idx]["detected_lang"] = None

    # Write modified metadata
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n  Cleared {len(sample_indices)} records, running process_folder()...")

    # Run the actual ai.py pipeline
    start = time.time()
    process_folder(folder)
    elapsed = time.time() - start

    print(f"  process_folder() completed in {elapsed:.1f}s")

    # Reload and check results
    with open(meta_path, 'r', encoding='utf-8') as f:
        data_after = json.load(f)

    print(f"\n  AFTER (same {len(sample_indices)} records):")
    lang_counts = Counter()
    changed = 0
    for idx in sample_indices[:10]:
        r = data_after[idx]
        name = r.get("TRADEMARK", {}).get("NAME", "?")
        name_tr = r.get("name_tr", "?")
        lang = r.get("detected_lang", "?")
        lang_counts[lang] += 1
        print(f"    {name:40s} -> name_tr={name_tr:35s} lang={lang}")

    # Count how many got re-filled
    refilled = sum(1 for idx in sample_indices if data_after[idx].get("name_tr") is not None)
    print(f"\n  Refilled: {refilled}/{len(sample_indices)} records got name_tr back")
    print(f"  Language distribution: {dict(lang_counts)}")

    # Restore backup
    shutil.move(str(backup_path), str(meta_path))
    print(f"  Restored original metadata.json")

print(f"\n{'=' * 80}")
print("ALL FOLDERS TESTED")
print(f"{'=' * 80}")

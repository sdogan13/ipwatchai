"""
Test ai.py pipeline on real data — show translation results clearly.
Processes a folder through process_folder(), then reads back the results.
"""
import sys
sys.path.insert(0, '/app')

import json
import time
import shutil
from pathlib import Path
from collections import Counter

from ai import process_folder

DATA_ROOT = Path("/app/bulletins/Marka")

# Pick one BLT folder with existing name_tr (to test re-processing)
# and one GZ folder without (to test fresh processing)
folders_to_test = []
for prefix, count in [('BLT_', 1), ('GZ_', 1), ('APP_', 1)]:
    found = sorted([f for f in DATA_ROOT.iterdir()
                    if f.is_dir() and f.name.startswith(prefix)
                    and (f / "metadata.json").exists()])
    folders_to_test.extend(found[:count])

for folder in folders_to_test:
    meta_path = folder / "metadata.json"
    backup_path = folder / "metadata.json.testbak"

    with open(meta_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    total = len(data)
    named = [(i, r) for i, r in enumerate(data)
             if r.get("TRADEMARK", {}).get("NAME", "").strip()]

    print(f"\n{'=' * 80}")
    print(f"FOLDER: {folder.name} — {total} records, {len(named)} with names")
    print(f"{'=' * 80}")

    # Backup
    shutil.copy2(meta_path, backup_path)

    # Clear name_tr and detected_lang for first 30 named records
    sample_indices = [i for i, _ in named[:30]]
    for idx in sample_indices:
        data[idx]["name_tr"] = None
        data[idx]["detected_lang"] = None

    # Write modified metadata
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Cleared {len(sample_indices)} records, running process_folder()...")
    start = time.time()
    process_folder(folder)
    elapsed = time.time() - start
    print(f"process_folder() completed in {elapsed:.1f}s\n")

    # Read results
    with open(meta_path, 'r', encoding='utf-8') as f:
        data_after = json.load(f)

    # Show results for the cleared records
    lang_counts = Counter()
    changed_count = 0
    print(f"{'NAME':<35s} | {'DETECTED_LANG':>13s} | {'NAME_TR':<35s} | CHANGED?")
    print("-" * 100)

    for idx in sample_indices:
        r = data_after[idx]
        name = r.get("TRADEMARK", {}).get("NAME", "?")
        name_tr = r.get("name_tr", "")
        lang = r.get("detected_lang", "?")
        lang_counts[lang] += 1

        # Truncate long names for display
        name_disp = name[:33] + ".." if len(name) > 35 else name
        tr_disp = name_tr[:33] + ".." if len(name_tr) > 35 else name_tr

        from utils.translation import turkish_lower
        changed = turkish_lower(name_tr) != turkish_lower(name) if name_tr else False
        if changed:
            changed_count += 1
        mark = "YES" if changed else ""

        print(f"{name_disp:<35s} | {lang:>13s} | {tr_disp:<35s} | {mark}")

    # Restore backup
    shutil.move(str(backup_path), str(meta_path))

    print(f"\nSummary: {len(sample_indices)} records processed")
    print(f"  Languages: {dict(lang_counts.most_common())}")
    print(f"  Translations changed: {changed_count}/{len(sample_indices)}")
    print(f"  Restored original metadata.json")

print(f"\n{'=' * 80}")
print("DONE — all folders tested through ai.py process_folder()")
print(f"{'=' * 80}")

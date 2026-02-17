"""Find APPLICATIONNO duplicates across metadata.json folders - encoding-safe version."""
import json
import os
import glob
import sys
import io
from collections import defaultdict, Counter

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def safe(s):
    """Make string safe for any console encoding."""
    if isinstance(s, str):
        return s.encode('ascii', errors='replace').decode('ascii')
    return str(s)

def main():
    base = r'C:\Users\701693\turk_patent\bulletins\Marka'
    pattern = os.path.join(base, '*', 'metadata.json')
    files = sorted(glob.glob(pattern))
    print(f'Found {len(files)} metadata.json files', flush=True)

    app_map = defaultdict(list)
    errors = []

    for i, filepath in enumerate(files):
        folder = os.path.basename(os.path.dirname(filepath))
        if (i + 1) % 100 == 0:
            print(f'  Processing {i+1}/{len(files)}: {folder}...', flush=True)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                records = json.load(f)
            if not isinstance(records, list):
                errors.append((folder, 'not a list'))
                continue
            for rec in records:
                app_no = rec.get('APPLICATIONNO') or rec.get('applicationno') or rec.get('application_no')
                if app_no:
                    status = rec.get('STATUS') or rec.get('status') or rec.get('Status') or 'N/A'
                    app_map[str(app_no)].append((folder, safe(status)))
        except Exception as e:
            errors.append((folder, str(e)[:80]))

    print(f'\nTotal unique APPLICATIONNO values: {len(app_map):,}', flush=True)
    print(f'Total folders processed: {len(files)}')
    if errors:
        print(f'Errors: {len(errors)}')
        for folder, err in errors[:5]:
            print(f'  {folder}: {err}')

    dupes = {}
    for k, v in app_map.items():
        distinct_folders = set(f for f, s in v)
        if len(distinct_folders) >= 2:
            dupes[k] = v

    print(f'\nRecords appearing in 2+ distinct folders: {len(dupes):,}')

    def folder_family(name):
        if name.startswith('BLT_'): return 'BLT'
        if name.startswith('GZ_'):  return 'GZ'
        if name.startswith('APP_'): return 'APP'
        return 'OTHER'

    same_blt = {}
    same_gz = {}
    cross_blt_gz = {}
    cross_with_app = {}

    for app_no, entries in dupes.items():
        families = set(folder_family(f) for f, s in entries)
        if 'APP' in families:
            cross_with_app[app_no] = entries
        elif families == {'BLT'}:
            same_blt[app_no] = entries
        elif families == {'GZ'}:
            same_gz[app_no] = entries
        elif 'BLT' in families and 'GZ' in families:
            cross_blt_gz[app_no] = entries

    print(f'\n=== CATEGORY COUNTS ===')
    print(f'Same family BLT (in multiple BLT folders):  {len(same_blt):,}')
    print(f'Same family GZ  (in multiple GZ folders):   {len(same_gz):,}')
    print(f'Cross-family BLT + GZ:                      {len(cross_blt_gz):,}')
    print(f'Cross-family with APP:                       {len(cross_with_app):,}')

    def print_examples(title, data, n=10):
        sep = '=' * 70
        print(f'\n{sep}')
        print(title)
        print(f'(showing {min(n, len(data))} of {len(data):,} total)')
        print(sep)
        items = list(data.items())[:n]
        for app_no, entries in items:
            seen = set()
            folders_unique = []
            for f, s in entries:
                key = (f, s)
                if key not in seen:
                    seen.add(key)
                    folders_unique.append((f, s))
            print(f'\n  APPLICATIONNO: {app_no}  ({len(set(f for f,s in entries))} distinct folders)')
            for f, s in sorted(folders_unique, key=lambda x: x[0]):
                print(f'    {f:25s}  STATUS={safe(s)}')

    print_examples('SAME FAMILY: BLT in multiple BLT folders', same_blt, 10)
    print_examples('SAME FAMILY: GZ in multiple GZ folders', same_gz, 10)
    print_examples('CROSS-FAMILY: BLT + GZ (no APP)', cross_blt_gz, 10)
    print_examples('CROSS-FAMILY: Includes APP folder', cross_with_app, 10)

    # Distribution
    folder_count_dist = Counter()
    for app_no, entries in dupes.items():
        distinct_folders = len(set(f for f, s in entries))
        folder_count_dist[distinct_folders] += 1

    sep = '=' * 70
    print(f'\n{sep}')
    print('DISTRIBUTION: Number of distinct folders per duplicated record')
    print(sep)
    for count in sorted(folder_count_dist.keys()):
        print(f'  {count} folders: {folder_count_dist[count]:,} records')

    # Top 10 most duplicated
    print(f'\n{sep}')
    print('TOP 10 MOST DUPLICATED RECORDS (by distinct folder count)')
    print(sep)
    top = sorted(dupes.items(), key=lambda x: len(set(f for f, s in x[1])), reverse=True)[:10]
    for app_no, entries in top:
        distinct = len(set(f for f, s in entries))
        families = set(folder_family(f) for f, s in entries)
        seen = set()
        folders_unique = []
        for f, s in entries:
            key = (f, s)
            if key not in seen:
                seen.add(key)
                folders_unique.append((f, s))
        print(f'\n  APPLICATIONNO: {app_no}  ({distinct} folders, families: {families})')
        for f, s in sorted(folders_unique, key=lambda x: x[0]):
            print(f'    {f:25s}  STATUS={safe(s)}')

    # STATUS transition analysis for cross-family
    print(f'\n{sep}')
    print('STATUS TRANSITIONS: BLT->GZ cross-family (sample 20)')
    print(sep)
    count = 0
    for app_no, entries in cross_blt_gz.items():
        if count >= 20:
            break
        blt_statuses = set()
        gz_statuses = set()
        blt_folders = set()
        gz_folders = set()
        for f, s in entries:
            if f.startswith('BLT_'):
                blt_statuses.add(s)
                blt_folders.add(f)
            elif f.startswith('GZ_'):
                gz_statuses.add(s)
                gz_folders.add(f)
        # Only show if statuses differ
        if blt_statuses != gz_statuses:
            print(f'\n  {app_no}: BLT({",".join(sorted(blt_statuses))}) -> GZ({",".join(sorted(gz_statuses))})')
            print(f'    BLT folders: {", ".join(sorted(blt_folders)[:3])}')
            print(f'    GZ  folders: {", ".join(sorted(gz_folders)[:3])}')
            count += 1

if __name__ == '__main__':
    main()

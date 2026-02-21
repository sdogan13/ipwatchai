import json
import sys
import os
import gc
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DATA_ROOT = Path(os.path.join("C:", os.sep, "Users", "701693", "turk_patent", "bulletins", "Marka"))


def get_folder_type(folder_name):
    if folder_name.startswith("BLT_"):
        return "BLT"
    elif folder_name.startswith("GZ_"):
        return "GZ"
    elif folder_name.startswith("APP_"):
        return "APP"
    return "OTHER"


def get_folders():
    return sorted([
        d for d in DATA_ROOT.iterdir()
        if d.is_dir() and get_folder_type(d.name) in ("BLT", "GZ", "APP")
    ])


def pass1_scan():
    app_no_folders = defaultdict(set)
    folders = get_folders()
    print("Found %d data folders to scan" % len(folders))
    blt_c = sum(1 for f in folders if get_folder_type(f.name) == "BLT")
    gz_c = sum(1 for f in folders if get_folder_type(f.name) == "GZ")
    app_c = sum(1 for f in folders if get_folder_type(f.name) == "APP")
    print("  BLT: %d" % blt_c)
    print("  GZ:  %d" % gz_c)
    print("  APP: %d" % app_c)
    print()
    fc = 0
    rc = 0
    error_folders = []
    for folder in folders:
        metadata_path = folder / "metadata.json"
        if not metadata_path.exists():
            continue
        folder_name = folder.name
        fc += 1
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                records = json.load(f)
        except Exception as e:
            error_folders.append((folder_name, str(e)))
            continue
        if not isinstance(records, list):
            error_folders.append((folder_name, "not a list"))
            continue
        for rec in records:
            app_no = rec.get("APPLICATIONNO", "")
            if not app_no:
                continue
            rc += 1
            app_no_folders[app_no].add(folder_name)
        del records
        if fc % 50 == 0:
            gc.collect()
            print("  Scanned %d/%d folders, %d records, %d unique app_nos" % (fc, len(folders), rc, len(app_no_folders)), flush=True)
    print()
    print("Scan complete: %d folders, %d records, %d unique app_nos" % (fc, rc, len(app_no_folders)), flush=True)
    if error_folders:
        print("Folders with errors (%d):" % len(error_folders))
        for fn, err in error_folders:
            print("  %s: %s" % (fn, err[:80]))
    return app_no_folders


def categorize_overlaps(app_no_folders):
    multi_blt = []
    multi_gz = []
    blt_gz = []
    blt_app = []
    gz_app = []
    all_three = []
    for app_no, folder_set in app_no_folders.items():
        types = set(get_folder_type(fn) for fn in folder_set)
        type_fc = defaultdict(int)
        for fn in folder_set:
            type_fc[get_folder_type(fn)] += 1
        has_blt = "BLT" in types
        has_gz = "GZ" in types
        has_app = "APP" in types
        if has_blt and has_gz and has_app:
            all_three.append((app_no, sorted(folder_set)))
        if has_blt and has_gz:
            blt_gz.append((app_no, sorted(folder_set)))
        if has_gz and has_app:
            gz_app.append((app_no, sorted(folder_set)))
        if has_blt and has_app:
            blt_app.append((app_no, sorted(folder_set)))
        if type_fc["BLT"] > 1:
            multi_blt.append((app_no, sorted(folder_set)))
        if type_fc["GZ"] > 1:
            multi_gz.append((app_no, sorted(folder_set)))
    return {
        "multi_blt": multi_blt, "multi_gz": multi_gz,
        "blt_gz": blt_gz, "blt_app": blt_app,
        "gz_app": gz_app, "all_three": all_three,
    }


def pass2_get_details(app_no, folder_list):
    details = []
    for folder_name in folder_list:
        metadata_path = DATA_ROOT / folder_name / "metadata.json"
        if not metadata_path.exists():
            continue
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                records = json.load(f)
            for rec in records:
                if rec.get("APPLICATIONNO", "") == app_no:
                    tm = rec.get("TRADEMARK", {}) or {}
                    details.append({
                        "folder": folder_name,
                        "type": get_folder_type(folder_name),
                        "status": rec.get("STATUS", ""),
                        "register_no": tm.get("REGISTERNO", ""),
                        "name": tm.get("NAME", ""),
                    })
                    break
            del records
        except Exception:
            pass
    return details


def print_examples(category_name, items, max_examples=5):
    sep = "=" * 80
    print()
    print(sep)
    print("  %s" % category_name)
    print("  Total: %d application numbers" % len(items))
    print(sep)
    if not items:
        print("  (none found)")
        return
    items_sorted = sorted(items, key=lambda x: len(x[1]), reverse=True)
    for i, (app_no, folder_list) in enumerate(items_sorted[:max_examples]):
        print()
        print("  Example %d: APPLICATIONNO = %s" % (i + 1, app_no))
        print("  Found in %d folder(s):" % len(folder_list))
        details = pass2_get_details(app_no, folder_list)
        for d in details:
            nd = d["name"][:40] if d["name"] else "(empty)"
            rd = d["register_no"] if d["register_no"] else "(none)"
            sd = d["status"] if d["status"] else "(empty)"
            print("    [%s] %s" % (d["type"], d["folder"]))
            print("         NAME: %s  |  REGISTERNO: %s  |  STATUS: %s" % (nd, rd, sd))


def main():
    print("=" * 80)
    print("PASS 1: Scanning all metadata.json files for application numbers...")
    print("=" * 80, flush=True)
    app_no_folders = pass1_scan()

    print()
    print("Categorizing overlaps...", flush=True)
    cats = categorize_overlaps(app_no_folders)

    hdr = "#" * 80
    print()
    print(hdr)
    print("#  CROSS-FOLDER OVERLAP ANALYSIS")
    print(hdr)

    dash45 = "-" * 45
    dash8 = "-" * 8
    print()
    print("%-45s %8s" % ("Category", "Count"))
    print("%s %s" % (dash45, dash8))
    print("%-45s %8d" % ("Same app_no in multiple BLT_ folders", len(cats["multi_blt"])))
    print("%-45s %8d" % ("Same app_no in multiple GZ_ folders", len(cats["multi_gz"])))
    print("%-45s %8d" % ("Same app_no in both BLT_ and GZ_", len(cats["blt_gz"])))
    print("%-45s %8d" % ("Same app_no in both BLT_ and APP_", len(cats["blt_app"])))
    print("%-45s %8d" % ("Same app_no in both GZ_ and APP_", len(cats["gz_app"])))
    print("%-45s %8d" % ("Same app_no in BLT_ + GZ_ + APP_", len(cats["all_three"])))

    multi_folder = sum(1 for an, fset in app_no_folders.items() if len(fset) > 1)
    print()
    print("%-45s %8d" % ("Total app_nos in >1 folder (any type)", multi_folder))
    print("%-45s %8d" % ("Total unique application numbers", len(app_no_folders)))

    print()
    print("=" * 80)
    print("PASS 2: Loading details for examples...", flush=True)
    print("=" * 80)

    print_examples("Same app_no in MULTIPLE BLT_ folders", cats["multi_blt"], 5)
    print_examples("Same app_no in MULTIPLE GZ_ folders", cats["multi_gz"], 5)
    print_examples("Same app_no in both BLT_ and GZ_ folders", cats["blt_gz"], 5)
    print_examples("Same app_no in both BLT_ and APP_ folders", cats["blt_app"], 5)
    print_examples("Same app_no in both GZ_ and APP_ folders", cats["gz_app"], 5)
    print_examples("Same app_no in BLT_ + GZ_ + APP_ (all three)", cats["all_three"], 5)

    sep = "=" * 80
    print()
    print(sep)
    print("  DISTRIBUTION: Number of folders per application_no")
    print(sep)
    fcd = defaultdict(int)
    for an, fset in app_no_folders.items():
        fcd[len(fset)] += 1
    for n in sorted(fcd.keys()):
        bar = "*" * min(fcd[n] // 100, 60)
        print("  %3d folder(s): %8d  %s" % (n, fcd[n], bar))

    print()
    print(sep)
    print("  TOP 10 app_nos by number of distinct folders")
    print(sep)
    by_fc = sorted(
        [(an, len(fset), sorted(fset)) for an, fset in app_no_folders.items()],
        key=lambda x: x[1], reverse=True
    )
    for an, nf, fnames in by_fc[:10]:
        types = sorted(set(get_folder_type(fn) for fn in fnames))
        details = pass2_get_details(an, fnames[:1])
        nm = details[0]["name"][:30] if details and details[0]["name"] else "(empty)"
        print("  %-20s in %d folders  types=%s  name=%s" % (an, nf, types, nm))
        for fn in fnames[:8]:
            print("    - %s" % fn)
        if len(fnames) > 8:
            print("    ... and %d more" % (len(fnames) - 8))


if __name__ == "__main__":
    main()

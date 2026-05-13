"""Data quality check for collected EUTM metadata pages.

Walks every bulletins/Marka_EU/BACKFILL_*/page_*.json file and reports:
  - Total records, unique applicationNumber counts
  - Required-field coverage (applicationNumber, markFeature, niceClasses, status)
  - applicationNumber regex compliance
  - Status enum compliance (vs OpenAPI spec)
  - markFeature enum compliance
  - Per-window date sanity (applicationDate within the window's month)
  - Page-size sanity (most non-last pages should have 100 records)
  - Sample of 3 records (early/mid/late corpus)
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("bulletins/Marka_EU")

# Per the OpenAPI spec v1.1.0
VALID_STATUSES = {
    "RECEIVED", "UNDER_EXAMINATION", "APPLICATION_PUBLISHED", "REGISTRATION_PENDING",
    "REGISTERED", "WITHDRAWN", "REFUSED", "OPPOSITION_PENDING", "APPEALED",
    "CANCELLATION_PENDING", "CANCELLED", "SURRENDERED", "EXPIRED", "APPEALABLE",
    "START_OF_OPPOSITION_PERIOD", "ACCEPTANCE_PENDING", "ACCEPTED", "REMOVED_FROM_REGISTER",
}
VALID_FEATURES = {
    "WORD", "FIGURATIVE", "SHAPE_3D", "COLOUR", "SOUND", "HOLOGRAM",
    "OLFACTORY", "POSITION", "PATTERN", "MOTION", "MULTIMEDIA", "OTHER",
}
APP_NO_RE = re.compile(r"^(\d{9}|W\d{8}[A-Z]?)$")

stats = {
    "windows": 0,
    "partial_windows": 0,
    "page_files": 0,
    "records_total": 0,
    "records_unique_app_no": set(),
    "missing_app_no": 0,
    "missing_status": 0,
    "missing_feature": 0,
    "missing_nice_classes": 0,
    "app_no_bad_format": 0,
    "status_unknown": Counter(),
    "feature_unknown": Counter(),
    "status_dist": Counter(),
    "feature_dist": Counter(),
    "non_full_non_last_pages": [],
    "date_out_of_window": [],
    "examples_per_window": {},  # window -> first record (for sampling)
}


def window_bounds(window_name: str):
    """BACKFILL_YYYY-MM -> (YYYY, MM)"""
    s = window_name.removeprefix("BACKFILL_")
    y, m = s.split("-")
    return int(y), int(m)


for window_dir in sorted(ROOT.iterdir()):
    if not window_dir.is_dir() or not window_dir.name.startswith("BACKFILL_"):
        continue
    stats["windows"] += 1
    manifest_path = window_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            if m.get("partial"):
                stats["partial_windows"] += 1
        except Exception:
            pass

    win_year, win_month = window_bounds(window_dir.name)
    pages = sorted(window_dir.glob("page_*.json"))
    n_pages = len(pages)

    for idx, page_file in enumerate(pages):
        stats["page_files"] += 1
        try:
            body = json.loads(page_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        tms = body.get("trademarks") or []
        is_last = idx == n_pages - 1

        if not is_last and len(tms) != 100 and len(tms) > 0:
            stats["non_full_non_last_pages"].append((str(page_file), len(tms)))

        for r in tms:
            stats["records_total"] += 1
            app = r.get("applicationNumber")
            if not app:
                stats["missing_app_no"] += 1
            else:
                stats["records_unique_app_no"].add(app)
                if not APP_NO_RE.match(app):
                    stats["app_no_bad_format"] += 1

            status = r.get("status")
            if not status:
                stats["missing_status"] += 1
            else:
                stats["status_dist"][status] += 1
                if status not in VALID_STATUSES:
                    stats["status_unknown"][status] += 1

            feat = r.get("markFeature")
            if not feat:
                stats["missing_feature"] += 1
            else:
                stats["feature_dist"][feat] += 1
                if feat not in VALID_FEATURES:
                    stats["feature_unknown"][feat] += 1

            nc = r.get("niceClasses")
            if not nc:
                stats["missing_nice_classes"] += 1

            app_date = r.get("applicationDate")
            if app_date:
                try:
                    y, mo, _ = app_date.split("-")
                    if int(y) != win_year or int(mo) != win_month:
                        stats["date_out_of_window"].append((window_dir.name, app, app_date))
                except Exception:
                    pass

            if window_dir.name not in stats["examples_per_window"]:
                stats["examples_per_window"][window_dir.name] = r


# ----- Report -----

def pct(n: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{100*n/total:.2f}%"


total = stats["records_total"]
unique = len(stats["records_unique_app_no"])
print("=" * 70)
print("EUTM DATA QUALITY REPORT")
print("=" * 70)
print(f"Windows scanned        : {stats['windows']}")
print(f"  of which partial     : {stats['partial_windows']}")
print(f"Page JSON files        : {stats['page_files']:,}")
print(f"Records total          : {total:,}")
print(f"Records unique (app_no): {unique:,}")
print(f"Duplicates             : {total - unique:,}  ({pct(total-unique, total)})")
print()
print("FIELD COVERAGE")
print(f"  missing applicationNumber : {stats['missing_app_no']:>10,}  ({pct(stats['missing_app_no'], total)})")
print(f"  missing status            : {stats['missing_status']:>10,}  ({pct(stats['missing_status'], total)})")
print(f"  missing markFeature       : {stats['missing_feature']:>10,}  ({pct(stats['missing_feature'], total)})")
print(f"  missing niceClasses       : {stats['missing_nice_classes']:>10,}  ({pct(stats['missing_nice_classes'], total)})")
print()
print("FORMAT COMPLIANCE")
print(f"  applicationNumber bad fmt : {stats['app_no_bad_format']:>10,}  ({pct(stats['app_no_bad_format'], total)})")
print()
print("ENUM COMPLIANCE")
print(f"  unknown status values     : {len(stats['status_unknown'])} distinct  total occurrences: {sum(stats['status_unknown'].values()):,}")
for s, n in stats["status_unknown"].most_common(5):
    print(f"    {s}: {n:,}")
print(f"  unknown markFeature values: {len(stats['feature_unknown'])} distinct  total occurrences: {sum(stats['feature_unknown'].values()):,}")
for s, n in stats["feature_unknown"].most_common(5):
    print(f"    {s}: {n:,}")
print()
print("STATUS DISTRIBUTION (top 10)")
for s, n in stats["status_dist"].most_common(10):
    print(f"  {s:<30s} {n:>10,}  ({pct(n, total)})")
print()
print("MARK FEATURE DISTRIBUTION")
for s, n in stats["feature_dist"].most_common(12):
    print(f"  {s:<30s} {n:>10,}  ({pct(n, total)})")
print()
print("DATE SANITY")
print(f"  Records with applicationDate OUTSIDE their window-month: {len(stats['date_out_of_window']):,}")
if stats["date_out_of_window"]:
    print("  (first 5 examples, format: window | app_no | date)")
    for w, a, d in stats["date_out_of_window"][:5]:
        print(f"    {w} | {a} | {d}")
print()
print("PAGE-SIZE ANOMALIES")
print(f"  non-last pages with != 100 records: {len(stats['non_full_non_last_pages']):,}")
if stats["non_full_non_last_pages"]:
    for p, n in stats["non_full_non_last_pages"][:5]:
        print(f"    {p}  records={n}")
print()
print("SAMPLE RECORDS (early / mid / late corpus)")
keys = sorted(stats["examples_per_window"].keys())
samples = []
if keys:
    samples.append(stats["examples_per_window"][keys[0]])
    samples.append(stats["examples_per_window"][keys[len(keys) // 2]])
    samples.append(stats["examples_per_window"][keys[-1]])
for r in samples:
    print(f"  {r.get('applicationNumber')}  {r.get('markFeature','?'):<10s}  "
          f"{r.get('status','?'):<22s}  appDate={r.get('applicationDate','?')}  "
          f"name={(r.get('wordMarkSpecification') or {}).get('verbalElement','-')[:40]}")

import os
import json
from pathlib import Path

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"


def _resolve_local_test_root(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


# Set this to the folder containing your B_ and BLT_ directories
ROOT_DIR = _resolve_local_test_root(
    os.environ.get("PIPELINE_BULLETINS_ROOT") or os.environ.get("DATA_ROOT"),
    _LOCAL_DEFAULT_BULLETINS_ROOT,
)

def is_not_empty(value):
    """
    Determines if a value from the B_ dataset should overwrite the BLT_ dataset.
    Returns False if the value is None, an empty string, "null", or an empty list/dict.
    """
    if value is None:
        return False
    if isinstance(value, str) and value.strip() in ("", "null", "None"):
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True

def deep_merge(target, source):
    """
    Recursively updates the target dictionary with the source dictionary,
    but ONLY if the source values are considered 'not empty'.
    Includes special protection for NICE Classes to prevent truncation.
    """
    for key, value in source.items():
        if not is_not_empty(value):
            continue 

        # Smart protection for NICE Classes
        if key in ["NICECLASSES_LIST", "NICECLASSES_RAW"]:
            target_list = target.get("NICECLASSES_LIST", [])
            source_list = source.get("NICECLASSES_LIST", [])
            if isinstance(target_list, list) and len(target_list) >= len(source_list) and len(target_list) > 0:
                continue

        if isinstance(value, dict) and key in target and isinstance(target[key], dict):
            deep_merge(target[key], value)
        else:
            target[key] = value
            
    return target

def main():
    print(f"Starting bulk merge in: {ROOT_DIR}\n")
    
    # Find all B_ folders
    b_folders = [d for d in ROOT_DIR.iterdir() if d.is_dir() and d.name.startswith("B_")]
    
    if not b_folders:
        print("[INFO] No B_ folders found to process.")
        return

    # Sort them so they process in a nice, predictable order
    b_folders.sort(key=lambda x: x.name)
    
    # --- Tracking Lists for Final Report ---
    skipped_mismatch_folders = []
    skipped_missing_blt_folders = []

    for b_dir in b_folders:
        # Extract the folder ID (e.g., "489" from "B_489")
        parts = b_dir.name.split('_')
        if len(parts) < 2:
            continue
        folder_id = parts[1]
        
        # Look for any folder that starts with "BLT_{folder_id}"
        blt_candidates = [d for d in ROOT_DIR.iterdir() if d.is_dir() and d.name.startswith(f"BLT_{folder_id}")]
        
        # --- NEW: Skip if no BLT folder exists at all ---
        if not blt_candidates:
            print(f"[WARNING] No matching BLT folder found for {b_dir.name}!")
            print("  -> [ACTION] Skipping and adding to missing list.")
            skipped_missing_blt_folders.append(b_dir.name)
            print("-" * 50)
            continue
            
        # Sort alphabetically to pick the newest date if multiple exist
        blt_candidates.sort()
        blt_dir = blt_candidates[-1]
            
        b_json_path = b_dir / "metadata.json"
        blt_json_path = blt_dir / "metadata.json"
        
        # Check if B_ data exists
        if not b_json_path.exists():
            print(f"[SKIP] {b_dir.name} metadata.json is missing.")
            print("-" * 50)
            continue
            
        # --- NEW: Skip if BLT folder exists but has no JSON file ---
        if not blt_json_path.exists():
            print(f"[WARNING] {blt_dir.name} exists but has no metadata.json!")
            print("  -> [ACTION] Skipping and adding to missing list.")
            skipped_missing_blt_folders.append(f"{b_dir.name} (Missing JSON in BLT)")
            print("-" * 50)
            continue

        print(f"[INFO] Merging: {b_dir.name} -> {blt_dir.name}")
        
        # Load JSON Data
        try:
            with open(b_json_path, 'r', encoding='utf-8') as f:
                b_data = json.load(f)
            with open(blt_json_path, 'r', encoding='utf-8') as f:
                blt_data = json.load(f)
        except Exception as e:
            print(f"  -> [ERROR] Failed to read JSON: {e}")
            print("-" * 50)
            continue
            
        blt_dict = {item.get("APPLICATIONNO"): item for item in blt_data if item.get("APPLICATIONNO")}
        
        # The 50% Match Safety Check
        b_app_nos = {item.get("APPLICATIONNO") for item in b_data if item.get("APPLICATIONNO")}
        blt_app_nos = set(blt_dict.keys())

        if not b_app_nos:
            print(f"  -> [SKIP] {b_dir.name} has no valid applications inside.")
            print("-" * 50)
            continue

        overlap_count = len(b_app_nos.intersection(blt_app_nos))
        match_percentage = (overlap_count / len(b_app_nos)) * 100

        if match_percentage < 50.0:
            print(f"  -> [WARNING] Only {match_percentage:.1f}% match ({overlap_count}/{len(b_app_nos)}). Mismatch detected!")
            print("  -> [ACTION] Skipping this folder to prevent data corruption.")
            skipped_mismatch_folders.append(f"{b_dir.name} (Matched only {match_percentage:.1f}%)")
            print("-" * 50)
            continue
        else:
            print(f"  -> [VALIDATION] Pass: {match_percentage:.1f}% match ({overlap_count}/{len(b_app_nos)}).")
        
        updated_count = 0
        added_count = 0
        
        for b_item in b_data:
            app_no = b_item.get("APPLICATIONNO")
            if not app_no:
                continue
                
            if app_no in blt_dict:
                blt_dict[app_no] = deep_merge(blt_dict[app_no], b_item)
                updated_count += 1
            else:
                blt_dict[app_no] = b_item
                added_count += 1
                
        merged_blt_list = list(blt_dict.values())
        
        try:
            with open(blt_json_path, 'w', encoding='utf-8') as f:
                json.dump(merged_blt_list, f, ensure_ascii=False, indent=2)
            print(f"  -> [SUCCESS] Updated {updated_count} records | Appended {added_count} new records.")
        except Exception as e:
            print(f"  -> [ERROR] Failed to save merged data: {e}")
            
        print("-" * 50)

    # --- Final Report ---
    if skipped_mismatch_folders or skipped_missing_blt_folders:
        print("\n" + "=" * 50)
        print("🚨 BULK MERGE COMPLETE - WITH SKIPPED FOLDERS 🚨")
        
        if skipped_missing_blt_folders:
            print("\n❌ MISSING TARGET FOLDERS (No BLT folder found):")
            for folder in skipped_missing_blt_folders:
                print(f"  - {folder}")
                
        if skipped_mismatch_folders:
            print("\n⚠️ DATA MISMATCH (Failed 50% overlap rule):")
            for folder in skipped_mismatch_folders:
                print(f"  - {folder}")
                
        print("=" * 50)
    else:
        print("\n✅ BULK MERGE COMPLETE - All folders passed safety checks and merged successfully!")

if __name__ == "__main__":
    main()

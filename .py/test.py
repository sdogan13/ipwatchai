import os
import json
import re

def normalize_id(text):
    match = re.search(r"(\d{4})/(\d+)", str(text))
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    return str(text).strip()

def load_json(json_path):
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return [data] if isinstance(data, dict) else data
    except Exception as e:
        return []

def save_json(json_path, data):
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def get_app_id(record):
    if "APPLICATIONNO" in record and record["APPLICATIONNO"]:
        return normalize_id(record["APPLICATIONNO"])
    return None

def find_target_folder(root_dir, target_num):
    pattern = re.compile(rf"^GZ_{target_num}(?!\d)")
    candidates = [f for f in os.listdir(root_dir) if pattern.match(f) and os.path.isdir(os.path.join(root_dir, f))]
    return os.path.join(root_dir, max(candidates, key=len)) if candidates else None

def undo_mistakes(root_dir):
    print("--- Step 1: Memorizing the accidentally copied Application Numbers ---")
    polluting_ids = set()
    
    # The mistake happened from GZ_38 to GZ_63
    for i in range(38, 64):
        src_folder = os.path.join(root_dir, f"GZ_{i}")
        json_path = os.path.join(src_folder, "metadata.json")
        
        if os.path.exists(json_path):
            records = load_json(json_path)
            for r in records:
                app_id = get_app_id(r)
                if app_id:
                    polluting_ids.add(app_id)
                    
    print(f"  -> Memorized {len(polluting_ids)} Application Numbers that need to be removed.\n")

    print("--- Step 2: Surgically cleaning the higher target folders ---")
    # The mistake polluted GZ_472 to GZ_499
    total_cleaned = 0
    for i in range(472, 500):
        target_folder = find_target_folder(root_dir, i)
        if not target_folder:
            continue
            
        json_path = os.path.join(target_folder, "metadata.json")
        if os.path.exists(json_path):
            records = load_json(json_path)
            original_len = len(records)
            
            # Keep only the records that are NOT in our polluted list
            cleaned_records = [r for r in records if get_app_id(r) not in polluting_ids]
            
            removed_count = original_len - len(cleaned_records)
            if removed_count > 0:
                save_json(json_path, cleaned_records)
                print(f"  [Cleaned] Removed {removed_count} mistaken records from {os.path.basename(target_folder)}")
                total_cleaned += removed_count

    print("-" * 60)
    print(f"UNDO COMPLETE: Successfully erased {total_cleaned} mistaken records.")
    print("Your target folders are now clean and ready for the real Global Map merge.")

if __name__ == "__main__":
    target_directory = r"C:\Users\701693\turk_patent\bulletins\Marka"
    if os.path.exists(target_directory):
        undo_mistakes(target_directory)
    else:
        print(f"Directory not found: {target_directory}")
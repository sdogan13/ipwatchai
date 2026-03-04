import os
import json
import shutil
import re
from collections import Counter
from send2trash import send2trash  # <-- NEW: Imports the Recycle Bin tool

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
        print(f"Error reading {json_path}: {e}")
        return []

def save_json(json_path, data):
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error saving {json_path}: {e}")

def get_app_id(record):
    if "APPLICATIONNO" in record and record["APPLICATIONNO"]:
        return normalize_id(record["APPLICATIONNO"])
    return None

def is_valid_data(value):
    if value is None: return False
    if value == "" or value == []: return False
    if isinstance(value, str) and value.strip().lower() == "null": return False
    if isinstance(value, dict) and not value: return False
    return True

def deep_merge(target, source):
    merged = False
    for key, source_val in source.items():
        if isinstance(source_val, dict) and isinstance(target.get(key), dict):
            if deep_merge(target[key], source_val):
                merged = True
        elif is_valid_data(source_val):
            if target.get(key) != source_val:
                target[key] = source_val
                merged = True
    return merged

def is_source_folder(folder_name):
    """Checks if the folder is strictly one of our lower folders (GZ_1 to GZ_63)."""
    match = re.match(r"^GZ_(\d+)$", folder_name)
    if match:
        return 1 <= int(match.group(1)) <= 63
    return False

def build_global_index(root_dir):
    print("--- Phase 1: Building Global 'Phonebook' of Higher GZ Folders ---")
    global_map = {}
    indexed_folders = 0
    
    for folder_name in os.listdir(root_dir):
        if not folder_name.startswith("GZ_") or is_source_folder(folder_name):
            continue
            
        folder_path = os.path.join(root_dir, folder_name)
        json_path = os.path.join(folder_path, "metadata.json")
        
        if os.path.exists(json_path):
            records = load_json(json_path)
            for rec in records:
                app_id = get_app_id(rec)
                if app_id:
                    global_map[app_id] = folder_path
            indexed_folders += 1
            
    print(f"  -> Successfully indexed {len(global_map)} applications across {indexed_folders} folders.\n")
    return global_map

def process_global_mapping_move(root_dir):
    global_map = build_global_index(root_dir)
    
    global_stats = {
        "metadata_updated": 0, "records_appended": 0,
        "images_moved": 0, "images_skipped_and_deleted": 0,
        "folders_deleted": 0
    }

    print("--- Phase 2: Smart Move via Global Map ---")
    print("-" * 60)

    for i in range(1, 64):
        src_folder_name = f"GZ_{i}"               
        src_folder = os.path.join(root_dir, src_folder_name)
        
        if not os.path.exists(src_folder):
            continue
            
        src_json_path = os.path.join(src_folder, "metadata.json")
        if not os.path.exists(src_json_path):
            print(f"  [!] Skipping {src_folder_name}: No metadata.json found.")
            continue
            
        src_records = load_json(src_json_path)
        if not src_records:
            print(f"  [!] Skipping {src_folder_name}: JSON is empty.")
            continue

        # --- SMART ANCHOR SYSTEM ---
        target_counts = Counter()
        for rec in src_records:
            app_id = get_app_id(rec)
            if app_id in global_map:
                target_counts[global_map[app_id]] += 1
                
        if not target_counts:
            print(f"  [?] Skipping {src_folder_name}: 0 matches found in the entire global map.")
            continue
            
        dst_folder = target_counts.most_common(1)[0][0]
        dst_folder_name = os.path.basename(dst_folder)
        
        print(f"Moving {src_folder_name} -> {dst_folder_name} ...")
        
        local_updated = 0
        local_appended = 0
        local_img_moved = 0
        local_img_skipped = 0
        
        # --- 1. MERGE METADATA ---
        dst_json_path = os.path.join(dst_folder, "metadata.json")
        dst_records = load_json(dst_json_path) if os.path.exists(dst_json_path) else []
        
        dst_map = {get_app_id(rec): rec for rec in dst_records if get_app_id(rec)}
        metadata_changed = False
        
        for src_rec in src_records:
            app_id = get_app_id(src_rec)
            if not app_id: continue
                
            if app_id in dst_map:
                if deep_merge(dst_map[app_id], src_rec):
                    local_updated += 1
                    global_stats["metadata_updated"] += 1
                    metadata_changed = True
            else:
                dst_records.append(src_rec)
                dst_map[app_id] = src_rec
                local_appended += 1
                global_stats["records_appended"] += 1
                metadata_changed = True
        
        if metadata_changed:
            save_json(dst_json_path, dst_records)

        # --- 2. MOVE IMAGES ---
        src_images_dir = os.path.join(src_folder, "images")
        dst_images_dir = os.path.join(dst_folder, "images")
        
        if os.path.exists(src_images_dir):
            os.makedirs(dst_images_dir, exist_ok=True)
            for img_filename in os.listdir(src_images_dir):
                if not img_filename.lower().endswith(('.png', '.jpg', '.jpeg')): continue
                    
                src_img_path = os.path.join(src_images_dir, img_filename)
                dst_img_path = os.path.join(dst_images_dir, img_filename)
                
                if not os.path.exists(dst_img_path):
                    try:
                        shutil.move(src_img_path, dst_img_path)
                        local_img_moved += 1
                        global_stats["images_moved"] += 1
                    except Exception:
                        pass
                else:
                    # Target has the image. Send duplicate source to Recycle Bin.
                    try:
                        send2trash(src_img_path)
                        local_img_skipped += 1
                        global_stats["images_skipped_and_deleted"] += 1
                    except Exception:
                        pass

        # --- 3. RECYCLE SOURCE FOLDER ---
        try:
            send2trash(src_folder) # <-- NEW: Sends the whole folder to the Recycle Bin safely
            global_stats["folders_deleted"] += 1
            print(f"  [Recycled] Successfully moved {src_folder_name} to the Recycle Bin")
        except Exception as e:
            print(f"  [!] Could not recycle {src_folder_name}: {e}")

        print(f"  -> Metadata: {local_updated} existing overwritten | {local_appended} new records appended")
        print(f"  -> Images:   {local_img_moved} physically moved | {local_img_skipped} skipped/recycled (duplicates)")
        print("-" * 60)

    print("\n" + "="*50)
    print("FINAL GRAND TOTALS")
    print(f"  Total existing records overwritten:    {global_stats['metadata_updated']}")
    print(f"  Total new records appended:            {global_stats['records_appended']}")
    print(f"  Total successfully moved images:       {global_stats['images_moved']}")
    print(f"  Total skipped/recycled duplicate images: {global_stats['images_skipped_and_deleted']}")
    print(f"  Total source folders safely recycled:  {global_stats['folders_deleted']}")
    print("="*50)

if __name__ == "__main__":
    target_directory = r"C:\Users\701693\turk_patent\bulletins\Marka"
    if os.path.exists(target_directory):
        process_global_mapping_move(target_directory)
    else:
        print(f"Directory not found: {target_directory}")
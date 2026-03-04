import os
import json
import shutil
import re
import sys
from collections import defaultdict

def normalize_id(text):
    """
    Converts '2006/15591' to '2006_15591' for matching filenames.
    """
    match = re.search(r"(\d{4})/(\d+)", str(text))
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    return None

def extract_ids_from_json(json_path):
    """
    Reads a JSON file and extracts IDs from 'IMAGE' key.
    Fallbacks to 'APPLICATIONNO'.
    """
    ids = set()
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        def scan_item(item):
            if "IMAGE" in item and isinstance(item["IMAGE"], str) and item["IMAGE"].strip():
                ids.add(item["IMAGE"].strip())
            elif "APPLICATIONNO" in item and item["APPLICATIONNO"]:
                norm_id = normalize_id(item["APPLICATIONNO"])
                if norm_id:
                    ids.add(norm_id)
        
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict): scan_item(item)
        elif isinstance(data, dict):
            scan_item(data)
            
    except Exception as e:
        print(f"Error reading {json_path}: {e}")
    
    return ids

def build_global_map(root_dir):
    """
    Now indexes folders that have metadata.json, even if the 'images' subfolder
    is currently missing.
    """
    print("Step 1: Building Global Index from all folders with metadata.json...")
    global_map = {}
    
    for root, dirs, files in os.walk(root_dir):
        if "metadata.json" in files:
            json_path = os.path.join(root, "metadata.json")
            found_ids = extract_ids_from_json(json_path)
            for img_id in found_ids:
                global_map[img_id] = root
                
    print(f"  -> Indexed {len(global_map)} records across valid bulletins.\n")
    return global_map

def get_folder_type(folder_name):
    """
    Identifies if a folder is 'BLT', 'GZ', or 'OTHER'.
    """
    folder_upper = folder_name.upper()
    if "GZ_" in folder_upper:
        return "GZ"
    if "BLT_" in folder_upper:
        return "BLT"
    return "OTHER"

def safe_handle_file(src_path, dst_folder):
    """
    Handles file transfer to dst_folder. 
    Creates the directory if it doesn't exist.
    ALWAYS copies to protect the source file.
    """
    if not os.path.exists(dst_folder):
        os.makedirs(dst_folder, exist_ok=True)
        
    filename = os.path.basename(src_path)
    dst_path = os.path.join(dst_folder, filename)

    # CASE 1: Target Exists (Skip to protect existing and source)
    if os.path.exists(dst_path):
        return "skipped"

    # CASE 2: Target Empty (Copy to protect source)
    try:
        shutil.copy2(src_path, dst_path)
        return "copied"
    except Exception:
        return "error"

def fix_misplaced_images(root_dir):
    global_index = build_global_map(root_dir)
    
    print("Step 2: Scanning for misplaced images (Collecting Tasks)...")
    
    # Store tasks to execute them in order later
    same_family_tasks = []
    diff_family_tasks = []
    
    for root, dirs, files in os.walk(root_dir):
        if os.path.basename(root) != "images":
            continue
            
        current_bulletin_folder = os.path.dirname(root)
        if not os.path.exists(os.path.join(current_bulletin_folder, "metadata.json")):
            continue
        
        for filename in files:
            if not filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                continue
                
            img_id = os.path.splitext(filename)[0]
            clean_id = img_id.split('_dup')[0].split('_moved')[0]

            correct_folder = global_index.get(clean_id)
            
            if correct_folder and os.path.abspath(correct_folder) != os.path.abspath(current_bulletin_folder):
                src_folder_name = os.path.basename(current_bulletin_folder)
                dst_folder_name = os.path.basename(correct_folder)
                
                src_type = get_folder_type(src_folder_name)
                dst_type = get_folder_type(dst_folder_name)
                
                is_same_type = (src_type == dst_type) and (src_type != "OTHER")
                
                task = {
                    "filename": filename,
                    "src_path": os.path.join(root, filename),
                    "target_images_dir": os.path.join(correct_folder, "images"),
                    "src_folder": src_folder_name,
                    "dst_folder": dst_folder_name,
                    "same_type": is_same_type
                }
                
                if is_same_type:
                    same_family_tasks.append(task)
                else:
                    diff_family_tasks.append(task)

    # --- EXECUTION PHASE 1: Same Family ---
    print(f"\nStep 3: Executing Phase 1 - SAME FAMILY Transfers (Copy/Protect) - {len(same_family_tasks)} files to process")
    stats_p1 = {"copied": 0, "skipped": 0, "error": 0}
    
    # Group by source folder for cleaner logging
    grouped_p1 = defaultdict(list)
    for t in same_family_tasks:
        grouped_p1[t['src_folder']].append(t)

    for src_folder, tasks in grouped_p1.items():
        count = len(tasks)
        print(f"  -> Processing {count} files from: {src_folder}")
        for task in tasks:
            res = safe_handle_file(task['src_path'], task['target_images_dir'])
            stats_p1[res] += 1

    # --- EXECUTION PHASE 2: Different Family ---
    print(f"\nStep 4: Executing Phase 2 - DIFFERENT FAMILY Transfers (Copy/Protect) - {len(diff_family_tasks)} files to process")
    stats_p2 = {"copied": 0, "skipped": 0, "error": 0}
    
    grouped_p2 = defaultdict(list)
    for t in diff_family_tasks:
        grouped_p2[t['src_folder']].append(t)

    for src_folder, tasks in grouped_p2.items():
        count = len(tasks)
        print(f"  -> Processing {count} files from: {src_folder}")
        for task in tasks:
            res = safe_handle_file(task['src_path'], task['target_images_dir'])
            stats_p2[res] += 1

    print("\n" + "="*40)
    print("FINAL SUMMARY (All source files protected)")
    print("PHASE 1 (Same Family):")
    print(f"  Copied: {stats_p1['copied']}, Skipped: {stats_p1['skipped']}, Errors: {stats_p1['error']}")
    print("-" * 20)
    print("PHASE 2 (Diff Family):")
    print(f"  Copied: {stats_p2['copied']}, Skipped: {stats_p2['skipped']}, Errors: {stats_p2['error']}")
    print("="*40)

if __name__ == "__main__":
    target_directory = r"C:\Users\701693\turk_patent\bulletins\Marka"
    
    if os.path.exists(target_directory):
        fix_misplaced_images(target_directory)
    else:
        print(f"Directory not found: {target_directory}")
import json
import re
import os
from pathlib import Path

# --- CORRECT PATH FOR YOUR USER ---
ROOT_DIR = Path(r"C:\Users\701693\turk_patent\bulletins\Marka")

def clean_name_field(text):
    if not text or not isinstance(text, str):
        return ""
    # Remove 'sekil', 'şekil', '+sekil' etc.
    text = re.sub(r'\+?\s*[sş]ekil', '', text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip()

def extract_tpe_id(item, source_key):
    val = item.get(source_key, "")
    if val:
        id_match = re.search(r'\s*\((\d+)\)$', val)
        if id_match:
            item["TPECLIENTID"] = id_match.group(1)
            item[source_key] = val[:id_match.start()].strip()
            return True
    return False

def process_metadata_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        return None

    # Ensure data is a list (sometimes it handles single dicts)
    if isinstance(data, dict):
        data = [data]

    fields_fixed = 0
    file_changed = False
    
    for rec in data:
        # 1. Clean Trademark Name
        tm = rec.get("TRADEMARK", {})
        old_name = tm.get("NAME", "")
        new_name = clean_name_field(old_name)
        if new_name != old_name:
            tm["NAME"] = new_name
            fields_fixed += 1
            file_changed = True

        # 2. Extract ID from Holders
        holders = rec.get("HOLDERS", [])
        if isinstance(holders, list):
            for holder in holders:
                if extract_tpe_id(holder, "TITLE"):
                    fields_fixed += 1
                    file_changed = True

        # 3. Extract ID from Attorneys
        attorneys = rec.get("ATTORNEYS", [])
        if isinstance(attorneys, list):
            for att in attorneys:
                for key in ["NAME", "TITLE"]:
                    if extract_tpe_id(att, key):
                        fields_fixed += 1
                        file_changed = True
                        break 

    if file_changed:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return f"Fixed {fields_fixed} items in {file_path.name}"
            
    return None

if __name__ == "__main__":
    print(f"Scanning directory: {ROOT_DIR}")
    
    # Find all JSON files recursively
    files = list(ROOT_DIR.rglob("*.json"))
    print(f"Found {len(files)} JSON files.")
    
    count = 0
    for json_file in files:
        res = process_metadata_file(json_file)
        if res:
            print(res)
            count += 1

    print("-" * 30)
    print(f"✅ CLEANING COMPLETE: Modified {count} files.")
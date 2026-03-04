import json
import re
import logging
from pathlib import Path

# ===================== CONFIGURATION =====================
ROOT_DIR = Path(r"C:\Users\sdogan\turk_patent\bulletins\Marka")

# Regex to find YYYY-MM-DD in folder names
DATE_PATTERN = re.compile(r'(\d{4})-(\d{2})-(\d{2})')

def fix_dates_from_folders():
    if not ROOT_DIR.exists():
        print(f"Error: Root path not found: {ROOT_DIR}")
        return

    print(f"🔍 Scanning {ROOT_DIR} for folder-based date fixes...\n")
    
    metadata_files = list(ROOT_DIR.rglob("metadata.json"))
    fixed_count = 0
    
    for json_file in metadata_files:
        folder_name = json_file.parent.name
        
        # 1. Check if folder name contains a date
        match = DATE_PATTERN.search(folder_name)
        if not match:
            continue
            
        # Extract and format date: YYYY-MM-DD -> DD.MM.YYYY
        year, month, day = match.groups()
        formatted_date = f"{day}.{month}.{year}" # e.g., 12.07.2006
        
        # 2. Determine target field based on folder type
        target_field = ""
        if folder_name.upper().startswith("BLT_"):
            target_field = "BULLETIN_DATE"
        elif folder_name.upper().startswith("GZ_"):
            target_field = "GAZETTE_DATE"
        else:
            # Skip if we can't determine type (safety first)
            continue

        try:
            # 3. Load and Update JSON
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if not data: continue

            updates_made = False
            for rec in data:
                tm = rec.get("TRADEMARK", {})
                current_val = tm.get(target_field, "")
                
                # Only update if missing, "Unknown", or empty
                if not current_val or current_val == "Unknown":
                    tm[target_field] = formatted_date
                    updates_made = True
            
            # 4. Save if changes occurred
            if updates_made:
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                print(f"✅ Fixed {folder_name}: Set {target_field} = {formatted_date}")
                fixed_count += 1
                
        except Exception as e:
            print(f"❌ Error processing {folder_name}: {e}")

    print("\n" + "="*50)
    print(f"Processing Complete.")
    print(f"Total Folders Updated: {fixed_count}")
    print("="*50)

if __name__ == "__main__":
    fix_dates_from_folders()
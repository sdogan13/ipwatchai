import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONFIGURATION ---
# We use os.path for raw speed over pathlib
ROOT_DIR = os.getenv("DATA_ROOT", r"C:\Users\701693\turk_patent\bulletins\Marka")
TARGET_LOGOS_DIR = os.path.join(ROOT_DIR, "LOGOS")

# Extensions (set for fast lookup)
VALID_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp'}

def process_folder_fast(folder_path):
    """
    Uses os.scandir and shutil.copyfile for maximum IOPS.
    Returns: (folder_name, count_copied, count_skipped)
    """
    local_copy = 0
    local_skip = 0
    
    # Construct path to images manually to avoid overhead
    images_path = os.path.join(folder_path, "images")
    
    # Fast fail if directory doesn't exist
    if not os.path.exists(images_path):
        return (os.path.basename(folder_path), 0, 0)

    try:
        # os.scandir is significantly faster than os.listdir or pathlib.iterdir
        with os.scandir(images_path) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                
                # Check extension (manual slicing is faster than splitext for known lengths, 
                # but splitext is safer. We stick to splitext for correctness).
                _, ext = os.path.splitext(entry.name)
                if ext.lower() not in VALID_EXTENSIONS:
                    continue

                dest_path = os.path.join(TARGET_LOGOS_DIR, entry.name)

                # 1. Existence Check
                if os.path.exists(dest_path):
                    local_skip += 1
                    continue

                try:
                    # 2. Fast Copy (copyfile is faster than copy2 because it skips metadata/timestamps)
                    shutil.copyfile(entry.path, dest_path)
                    local_copy += 1
                except OSError:
                    pass # Permission errors etc
                    
    except OSError:
        pass # Folder access errors

    return (os.path.basename(folder_path), local_copy, local_skip)

def consolidate():
    print(f"[*] Starting MAX SPEED Consolidation")
    print(f"[*] Root: {ROOT_DIR}")
    
    if not os.path.exists(TARGET_LOGOS_DIR):
        os.makedirs(TARGET_LOGOS_DIR)

    # 1. Fast Scan for directories
    # We only want top level directories starting with GZ_ or BLT_
    target_folders = []
    with os.scandir(ROOT_DIR) as it:
        for entry in it:
            if entry.is_dir() and (entry.name.startswith("GZ_") or entry.name.startswith("BLT_")):
                target_folders.append(entry.path)

    total_folders = len(target_folders)
    print(f"[*] Found {total_folders} folders. Launching 32 threads...")
    
    start_time = time.time()
    total_files = 0
    total_skips = 0

    # 2. Execution
    # 32 Threads matches your 32 Logical Processors
    with ThreadPoolExecutor(max_workers=32) as executor:
        futures = {executor.submit(process_folder_fast, f): f for f in target_folders}
        
        for i, future in enumerate(as_completed(futures)):
            name, copied, skipped = future.result()
            total_files += copied
            total_skips += skipped
            
            # Progress bar effect
            if i % 10 == 0:
                print(f"\r[{i}/{total_folders}] Processing... (Copied: {total_files} | Skipped: {total_skips})", end="")

    duration = time.time() - start_time
    print(f"\n\n[SUCCESS] Done in {duration:.2f}s")
    print(f"Total Copied: {total_files}")
    print(f"Total Skipped: {total_skips}")

if __name__ == "__main__":
    consolidate()
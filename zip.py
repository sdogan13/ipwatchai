from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import time
import locale
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ----------------------------
# Config (from settings with fallback defaults)
# ----------------------------
try:
    from config.settings import settings as _app_settings
    _pipe = _app_settings.pipeline
    _DEFAULT_ROOT = Path(_pipe.bulletins_root)
    _DEFAULT_7Z = _pipe.seven_zip_path
    _DEFAULT_SKIP = _pipe.skip_existing
    _DEFAULT_CLEAN = _pipe.clean_after_extract
    _DEFAULT_MAX_CD = _pipe.max_cd_archives or None  # 0 means no limit
except Exception:
    _DEFAULT_ROOT = Path(r"C:\Users\701693\turk_patent\bulletins\Marka")
    _DEFAULT_7Z = r"C:\Program Files\7-Zip\7z.exe"
    _DEFAULT_SKIP = True
    _DEFAULT_CLEAN = True
    _DEFAULT_MAX_CD = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [EXTRACTOR] - %(levelname)s - %(message)s",
)
logger = logging.getLogger("turkpatent.extractor")

ARCHIVE_EXTS = {".zip", ".rar", ".7z"}
SKIP_EXTS = {".part", ".crdownload", ".tmp"}

# --- Patterns ---
# DIRECT_CD_RE: Capture the content inside the _CD wrapper
# e.g. "484_Gazete_CD" -> stem "484_Gazete"
# Updated to optionally match date suffix like _2026-01-12
DIRECT_CD_RE = re.compile(r"^(?P<stem>.*)_CD(?:_\d{4}-\d{2}-\d{2})?$", re.IGNORECASE)

# RANGE_RE: Detects "316-323"
RANGE_RE = re.compile(r"\b(\d+)\s*-\s*(\d+)\b")

# GROUP_HINT_RE: Keywords for group/range archives
GROUP_HINT_RE = re.compile(r"(bülteni|bulteni|gazetesi|mülkiyet|mulkiyet)", re.IGNORECASE)

# NUM_DIR_RE: Used for deep scanning
NUM_DIR_RE = re.compile(r"^(?P<num>\d+)(?:_CD)?$", re.IGNORECASE)

# DATE_RE: Detects YYYY-MM-DD
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*]')

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"}


# ---------------------- prefix logic ----------------------
def doc_prefix_from_text(s: str) -> str:
    """
    Decide prefix based on archive name / folder names:
      - Marka Bülteni        -> MB_
      - Marka Gazetesi       -> MG_
      - Sınai Mülkiyet ...   -> SMG_
    """
    t = s.lower()
    t = t.replace("_", " ")

    if "sınai" in t or "sinai" in t or "mülkiyet" in t or "mulkiyet" in t:
        if "gazete" in t or "gazetesi" in t:
            return "SMG_"
        return "SM_"

    if "marka" in t and ("bülten" in t or "bulten" in t or "bülteni" in t or "bulteni" in t):
        return "MB_"

    if "marka" in t and ("gazete" in t or "gazetesi" in t):
        return "GZ_" 

    if "gazete" in t or "gazetesi" in t:
        return "GZ_"

    if "bülten" in t or "bulten" or "bülteni" in t or "bulteni" in t:
        return "BLT_"

    return "UNK_"


def normalize_prefix(prefix: str) -> str:
    if prefix == "UNK_":
        return ""
    return prefix


# ---------------------- 7z ----------------------
def find_7z(explicit: Optional[str] = None) -> Path:
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        raise FileNotFoundError(f"7z not found at: {explicit}")

    # Try config setting first
    cfg_path = Path(_DEFAULT_7Z)
    if cfg_path.exists():
        return cfg_path

    # Prefer modern 7zz (7-Zip >=21.x) which has better RAR5 support
    p = shutil.which("7zz")
    if p:
        return Path(p)

    p = shutil.which("7z")
    if p:
        return Path(p)

    fallback = Path(r"C:\Program Files\7-Zip\7z.exe")
    if fallback.exists():
        return fallback

    raise FileNotFoundError("7z.exe not found. Install 7-Zip or add it to PATH.")


# ---------------------- helpers ----------------------
def sanitize_folder_name(name: str) -> str:
    name = INVALID_FS_CHARS.sub("_", name).strip().strip(".")
    return name or "tmp"

def rm_tree(p: Path) -> None:
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)

def dedupe_paths(paths: List[Path]) -> List[Path]:
    seen = set()
    out = []
    for x in paths:
        k = str(x).lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out

def choose_best_tmb(files: List[Path]) -> Path:
    return max(files, key=lambda p: p.stat().st_size if p.exists() else -1)

def _safe_decode(b: bytes) -> str:
    if not b:
        return ""
    for enc in ("utf-8", "cp1254", locale.getpreferredencoding(False), "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")

def extract_date_from_text(text: str) -> Optional[str]:
    m = DATE_RE.search(text)
    if m:
        return m.group(1)
    return None


# ---------------------- cleaning modes ----------------------
def clean_final_dir_cd_mode(final_dir: Path) -> None:
    """CD mode: keep images/, tmbulletin*, AND PDFs."""
    if not final_dir.exists():
        return
    for item in final_dir.iterdir():
        if item.is_dir() and item.name.lower() == "images":
            continue
        if item.is_file():
            low = item.name.lower()
            # Keep PDFs, DB files, and common Java App files
            if low.endswith((".pdf", ".script", ".log", ".properties", ".jar", ".bat", ".inf", ".txt")):
                continue
            if low.startswith("tmbulletin") or "gazete" in low:
                continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            try:
                item.unlink()
            except OSError:
                pass

def clean_final_dir_single_issue_mode(final_dir: Path) -> None:
    """Single-issue: keep images/ + tmbulletin* + PDFs."""
    if not final_dir.exists():
        return
    for item in final_dir.iterdir():
        if item.is_dir() and item.name.lower() == "images":
            continue
        if item.is_file():
            low = item.name.lower()
            if low.endswith((".pdf", ".script", ".txt")) or low.startswith("tmbulletin") or "gazete" in low:
                continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            try:
                item.unlink()
            except OSError:
                pass

def clean_final_dir_flat_mode(final_dir: Path) -> None:
    """Group flatten: remove all subfolders; keep files."""
    if not final_dir.exists():
        return
    for item in final_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)


# ---------------------- already processed checks ----------------------
def already_processed_cd_mode(final_dir: Path) -> bool:
    if not final_dir.exists():
        return False
    
    # If we have PDFs or Java App files, consider it processed
    if any(p.suffix.lower() == ".pdf" for p in final_dir.glob("*.pdf")):
        return True
    if any(p.suffix.lower() == ".jar" for p in final_dir.glob("*.jar")):
        return True

    images_dir = final_dir / "images"
    images_ok = images_dir.exists() and any(p.is_file() for p in images_dir.rglob("*"))
    return images_ok

def already_processed_single_issue_mode(final_dir: Path) -> bool:
    if not final_dir.exists():
        return False

    has_pdf = False
    for p in final_dir.iterdir():
        if p.is_file() and p.suffix.lower() == ".pdf" and p.stat().st_size > 0:
            has_pdf = True
            break
      
    if has_pdf:
        return True

    images_dir = final_dir / "images"
    if images_dir.exists() and any(p.is_file() for p in images_dir.rglob("*")):
        return True

    return False

def already_processed_flat_mode(final_dir: Path) -> bool:
    if not final_dir.exists():
        return False

    for p in final_dir.iterdir():
        if not p.is_file():
            continue
        if p.stat().st_size <= 0:
            continue
        
        ext = p.suffix.lower()
        if ext == ".pdf":
            return True
        if ext in IMAGE_EXTS:
            return True

    return False


# ---------------------- 7z extract ----------------------
def _try_fallback_extract(archive_path: Path, temp_dir: Path, primary_error: str) -> bool:
    """Try alternative extractors when primary 7z fails (e.g. RAR5 Unsupported Method)."""
    is_rar = archive_path.suffix.lower() in (".rar",)

    # Try 7zz (modern 7-Zip) if primary was old p7zip
    sevenzz = shutil.which("7zz")
    if sevenzz:
        logging.info("Retrying with 7zz (modern 7-Zip) for %s", archive_path.name)
        rm_tree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(
            [sevenzz, "x", "-y", f"-o{temp_dir}", str(archive_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if p.returncode in (0, 1):
            logging.info("7zz succeeded for %s", archive_path.name)
            return True
        logging.warning("7zz also failed for %s (rc=%d)", archive_path.name, p.returncode)

    # Try unrar for RAR files
    if is_rar:
        unrar_bin = shutil.which("unrar")
        if unrar_bin:
            logging.info("Retrying with unrar for %s", archive_path.name)
            rm_tree(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)
            p = subprocess.run(
                [unrar_bin, "x", "-o+", str(archive_path), str(temp_dir) + "/"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            if p.returncode == 0:
                logging.info("unrar succeeded for %s", archive_path.name)
                return True
            logging.warning("unrar also failed for %s (rc=%d)", archive_path.name, p.returncode)

    return False


def extract_to_temp(seven_z: Path, archive_path: Path, temp_dir: Path, retries: int = 2) -> None:
    rm_tree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(seven_z), "x", "-y", f"-o{temp_dir}", str(archive_path)]

    last_msg = ""
    for attempt in range(retries + 1):
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if p.returncode in (0, 1):
            if p.returncode == 1:
                msg = _safe_decode((p.stderr or p.stdout)[-800:])
                logging.warning("7z warnings for %s: %s", archive_path.name, msg.strip())
            return

        last_msg = _safe_decode((p.stderr or p.stdout)[-1600:])
        if attempt < retries:
            time.sleep(1.0)

    # Primary 7z failed — try fallback extractors (7zz, unrar)
    if "Unsupported Method" in last_msg or archive_path.suffix.lower() == ".rar":
        if _try_fallback_extract(archive_path, temp_dir, last_msg):
            return

    raise RuntimeError(f"7z extract failed for {archive_path}:\n{last_msg}")


# ---------------------- CD merge helper ----------------------
def merge_tree_move(src: Path, dst: Path) -> Tuple[int, int, int]:
    dst.mkdir(parents=True, exist_ok=True)
    moved = replaced = skipped = 0

    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            m, r, s = merge_tree_move(item, target)
            moved += m
            replaced += r
            skipped += s
            try:
                item.rmdir()
            except OSError:
                pass
            continue

        if target.exists() and target.is_file():
            try:
                src_size = item.stat().st_size
                dst_size = target.stat().st_size
            except OSError:
                src_size, dst_size = -1, -1

            if src_size > dst_size:
                try:
                    target.unlink()
                except OSError:
                    pass
                shutil.move(str(item), str(target))
                replaced += 1
            else:
                try:
                    item.unlink()
                except OSError:
                    pass
                skipped += 1
        else:
            shutil.move(str(item), str(target))
            moved += 1

    return moved, replaced, skipped


# ---------------------- FLATTEN move ----------------------
def unique_target_path(dst_dir: Path, filename: str) -> Path:
    target = dst_dir / filename
    if not target.exists():
        return target
    stem = target.stem
    ext = target.suffix
    n = 1
    while True:
        cand = dst_dir / f"{stem}__dup{n}{ext}"
        if not cand.exists():
            return cand
        n += 1

def move_file_with_collision_policy(src: Path, dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    target = dst_dir / src.name

    if not target.exists():
        shutil.move(str(src), str(target))
        return

    try:
        ssz = src.stat().st_size
        dsz = target.stat().st_size
    except OSError:
        ssz = dsz = -1

    if ssz > dsz:
        try:
            target.unlink()
        except OSError:
            pass
        shutil.move(str(src), str(target))
    elif ssz == dsz:
        try:
            src.unlink()
        except OSError:
            pass
    else:
        alt = unique_target_path(dst_dir, src.name)
        shutil.move(str(src), str(alt))


# ---------------------- archive classification ----------------------
def extract_number_from_text(text: str) -> Optional[int]:
    """
    Tries to find a standalone number in the text.
    Prioritizes numbers that are distinct tokens.
    """
    # Remove date suffixes first to avoid false positives at the end of string
    text_clean = DATE_RE.sub("", text)

    # 1. Try finding a number at the start: "484_Gazete" -> 484
    m = re.match(r"^(\d+)", text_clean)
    if m:
        return int(m.group(1))
    
    # 2. Try finding a number at the end: "Marka_Gazetesi_433" -> 433
    m = re.search(r"(\d+)$", text_clean)
    if m:
        return int(m.group(1))
        
    return None

def find_archives(
    root: Path,
    max_cd: Optional[int] = None
) -> Tuple[List[Tuple[int, Path, str, Optional[str]]], List[Tuple[int, Path, str, Optional[str]]], List[Tuple[Path, str]]]:
    
    best_cd_by_num: Dict[int, Tuple[Path, str, Optional[str]]] = {}
    best_single_by_num: Dict[int, Tuple[Path, str, Optional[str]]] = {}
    group_ranges: List[Tuple[Path, str]] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in ("images", "_tmp_extract", "_tmp_group_extract")]

        for fn in filenames:
            p = Path(dirpath) / fn
            ext = p.suffix.lower()
            if ext in SKIP_EXTS or ext not in ARCHIVE_EXTS:
                continue

            stem = p.stem

            # 1) Direct CD archives (ending in _CD)
            m = DIRECT_CD_RE.match(stem)
            if m:
                # stem includes text like '433_Gazete' or just '433'
                full_stem = m.group("stem")
                
                # UPDATED: Use more robust number extraction
                num = extract_number_from_text(full_stem)
                
                if num is not None:
                    prefix = normalize_prefix(doc_prefix_from_text(full_stem))
                    # Use original stem to capture date if present at end
                    date_str = extract_date_from_text(stem)

                    prev = best_cd_by_num.get(num)
                    if prev is None:
                        best_cd_by_num[num] = (p, prefix, date_str)
                    else:
                        try:
                            if p.stat().st_size > prev[0].stat().st_size:
                                best_cd_by_num[num] = (p, prefix, date_str)
                        except OSError:
                            pass
                continue

            # 2) Group/range archives
            if RANGE_RE.search(stem) and GROUP_HINT_RE.search(stem):
                prefix = normalize_prefix(doc_prefix_from_text(stem))
                group_ranges.append((p, prefix))
                continue

            # 3) Single-issue archives
            num = extract_number_from_text(stem)
            if num is not None:
                prefix = normalize_prefix(doc_prefix_from_text(stem))
                date_str = extract_date_from_text(stem)
                prev = best_single_by_num.get(num)
                if prev is None:
                    best_single_by_num[num] = (p, prefix, date_str)
                else:
                    try:
                        if p.stat().st_size > prev[0].stat().st_size:
                            best_single_by_num[num] = (p, prefix, date_str)
                    except OSError:
                        pass

    # CD wins over single-issue SAME number + SAME prefix
    for n in list(best_single_by_num.keys()):
        _, single_prefix, _ = best_single_by_num[n]
        if n in best_cd_by_num:
            _, cd_prefix, _ = best_cd_by_num[n]
            if single_prefix == cd_prefix:
                del best_single_by_num[n]

    direct_cd = sorted([(n, pp[0], pp[1], pp[2]) for n, pp in best_cd_by_num.items()], key=lambda x: x[0])
    if max_cd is not None:
        direct_cd = direct_cd[:max_cd]

    single_issue = sorted([(n, pp[0], pp[1], pp[2]) for n, pp in best_single_by_num.items()], key=lambda x: x[0])
    group_ranges = sorted(group_ranges, key=lambda x: x[0].name.lower())
    return direct_cd, single_issue, group_ranges


# ---------------------- infer bulletin number for deep group scanning ----------------------
def infer_num_from_relative_path(rel_parts: Tuple[str, ...]) -> Optional[int]:
    for part in rel_parts:
        m = NUM_DIR_RE.match(part)
        if m:
            try:
                return int(m.group("num"))
            except ValueError:
                pass
    return None

def infer_num_from_filename(name: str) -> Optional[int]:
    m = re.match(r"^(?P<num>\d{1,6})(?:[_\s-].*)?$", name)
    if m:
        try:
            return int(m.group("num"))
        except ValueError:
            return None
    return None

def is_target_file(p: Path) -> bool:
    if not p.is_file():
        return False
    ext = p.suffix.lower()
    low = p.name.lower()
    if ext == ".pdf":
        return True
    if low.startswith("tmbulletin") or "gazete" in low:
        return True
    if ext in IMAGE_EXTS:
        return True
    return False


# ---------------------- processors ----------------------
def process_cd_archive_cd_mode(
    seven_z: Path,
    root: Path,
    num: int,
    arc: Path,
    prefix: str,
    date_str: Optional[str],
    skip_already_processed: bool,
    clean_final: bool,
    delete_archive: bool,
) -> str:
    """
    ###_CD archives.
    """
    num_str = str(num)
    folder_name = f"{prefix}{num_str}"
    if date_str:
        folder_name += f"_{date_str}"
        
    final_dir = root / folder_name
    tmp_base = root / "_tmp_extract"
    temp_dir = tmp_base / f"{prefix}{num_str}_CD"

    if skip_already_processed and already_processed_cd_mode(final_dir):
        return "SKIP(already processed)"

    final_dir.mkdir(parents=True, exist_ok=True)
    
    # Clean logic: If Gazette (GZ_), do NOT aggressively clean to preserve apps/structure
    is_gazette = (prefix == "GZ_")
    if clean_final and not is_gazette:
        clean_final_dir_cd_mode(final_dir)

    extract_to_temp(seven_z, arc, temp_dir)
    
    # Verify extraction result
    has_extracted_files = any(temp_dir.iterdir()) if temp_dir.exists() else False

    if is_gazette:
        # GAZETTE MODE: Move EVERYTHING to preserve Java app structure
        items = list(temp_dir.iterdir())
        if len(items) == 1 and items[0].is_dir():
            src_root = items[0]
        else:
            src_root = temp_dir
        
        merge_tree_move(src_root, final_dir)
        moved_any = True

        # --- POST-PROCESSING FOR GAZETTE: HOIST ASSETS (COPY) ---
        for f in final_dir.rglob("*"):
            if f.is_file() and f.parent != final_dir:
                low = f.name.lower()
                if "tmbulletin" in low or "gazete" in low or low.endswith(".script"):
                    dest = final_dir / f.name
                    if not dest.exists() or (dest.exists() and f.stat().st_size > dest.stat().st_size):
                        try:
                            shutil.copy2(str(f), str(dest))
                        except OSError:
                            pass

        sub_img_dirs = [d for d in final_dir.rglob("images") if d.is_dir() and d.name.lower() == "images" and d != final_dir / "images"]
        if sub_img_dirs:
            root_images = final_dir / "images"
            root_images.mkdir(exist_ok=True)
            for s_dir in sub_img_dirs:
                for img in s_dir.iterdir():
                    if img.is_file():
                        dest_img = root_images / img.name
                        if not dest_img.exists():
                            try:
                                shutil.copy2(str(img), str(dest_img))
                            except OSError:
                                pass

    else:
        # BULLETIN MODE: Original cherry-picking logic
        img_dirs = [d for d in temp_dir.rglob("images") if d.is_dir() and d.name.lower() == "images"]
        img_dirs = dedupe_paths(img_dirs)
        moved_any = False

        if img_dirs:
            dest_images = final_dir / "images"
            if not dest_images.exists():
                shutil.move(str(img_dirs[0]), str(dest_images))
                moved_any = True
                remaining = img_dirs[1:]
            else:
                remaining = img_dirs

            for src_img in remaining:
                m, r, _ = merge_tree_move(src_img, dest_images)
                if (m + r) > 0:
                    moved_any = True

        tmb_files = [f for f in temp_dir.rglob("*") if f.is_file()]
        tmb_files = [f for f in tmb_files if "tmbulletin" in f.name.lower() or "gazete" in f.name.lower() or "script" in f.suffix.lower()]
        tmb_files = dedupe_paths(tmb_files)

        for tmb in tmb_files:
            dest = final_dir / tmb.name
            if dest.exists():
                try:
                    if tmb.stat().st_size > dest.stat().st_size:
                        try:
                            dest.unlink()
                        except OSError:
                            pass
                        shutil.move(str(tmb), str(dest))
                        moved_any = True
                except OSError:
                    pass
            else:
                shutil.move(str(tmb), str(dest))
                moved_any = True

        pdfs = [f for f in temp_dir.rglob("*.pdf") if f.is_file()]
        for pdf in pdfs:
            dest = final_dir / pdf.name
            move_file_with_collision_policy(pdf, final_dir)
            moved_any = True

    rm_tree(temp_dir)
    try:
        if tmp_base.exists() and not any(tmp_base.iterdir()):
            tmp_base.rmdir()
    except OSError:
        pass

    if moved_any:
        if delete_archive:
            try:
                arc.unlink()
            except OSError:
                pass
        if clean_final and not is_gazette:
            clean_final_dir_cd_mode(final_dir)
        return "OK"

    # STRUCTURAL CHECK
    if has_extracted_files:
        raise RuntimeError(f"CRITICAL: Content extracted from {arc.name} but no valid structure found. Folder layout may have changed.")

    return "SKIP(nothing moved)"


def process_single_issue_archive_cd_style(
    seven_z: Path,
    root: Path,
    num: int,
    arc: Path,
    prefix: str,
    date_str: Optional[str],
    skip_already_processed: bool,
    clean_final: bool,
    delete_archive: bool,
) -> str:
    """
    Single issue: output folder is ROOT/<prefix><num>
    """
    num_str = str(num)
    folder_name = f"{prefix}{num_str}"
    if date_str:
        folder_name += f"_{date_str}"
        
    final_dir = root / folder_name
    tmp_base = root / "_tmp_extract"
    temp_dir = tmp_base / f"single_{prefix}{num_str}"

    if skip_already_processed and already_processed_single_issue_mode(final_dir):
        return "SKIP(already processed)"

    final_dir.mkdir(parents=True, exist_ok=True)
    if clean_final:
        clean_final_dir_single_issue_mode(final_dir)

    extract_to_temp(seven_z, arc, temp_dir)
    
    has_extracted_files = any(temp_dir.iterdir()) if temp_dir.exists() else False

    # 1. Images
    img_dirs = [d for d in temp_dir.rglob("images") if d.is_dir() and d.name.lower() == "images"]
    img_dirs = dedupe_paths(img_dirs)

    moved_any = False

    if img_dirs:
        dest_images = final_dir / "images"
        if not dest_images.exists():
            shutil.move(str(img_dirs[0]), str(dest_images))
            moved_any = True
            remaining = img_dirs[1:]
        else:
            remaining = img_dirs

        for src_img in remaining:
            m, r, _ = merge_tree_move(src_img, dest_images)
            if (m + r) > 0:
                moved_any = True

    # 2. Database/Text files
    tmb_files = [f for f in temp_dir.rglob("*") if f.is_file()]
    tmb_files = [f for f in tmb_files if "tmbulletin" in f.name.lower() or "gazete" in f.name.lower()]
    tmb_files = dedupe_paths(tmb_files)

    for tmb in tmb_files:
        dest = final_dir / tmb.name
        move_file_with_collision_policy(tmb, final_dir)
        moved_any = True

    # 3. PDFs
    pdfs = [f for f in temp_dir.rglob("*.pdf") if f.is_file()]
    for pdf in pdfs:
        dest = final_dir / pdf.name
        move_file_with_collision_policy(pdf, final_dir)
        moved_any = True

    rm_tree(temp_dir)
    try:
        if tmp_base.exists() and not any(tmp_base.iterdir()):
            tmp_base.rmdir()
    except OSError:
        pass

    if moved_any:
        if delete_archive:
            try:
                arc.unlink()
            except OSError:
                pass
        if clean_final:
            clean_final_dir_single_issue_mode(final_dir)
        return "OK"

    # STRUCTURAL CHECK
    if has_extracted_files:
        raise RuntimeError(f"CRITICAL: Content extracted from {arc.name} but no valid structure found. Folder layout may have changed.")

    return "SKIP(nothing moved)"


def process_group_range_archive_flatten_deep(
    seven_z: Path,
    root: Path,
    group_arc: Path,
    prefix: str,
    skip_already_processed: bool,
    clean_final: bool,
    delete_archive: bool,
) -> Tuple[int, int, int]:
    """
    Group/range: DEEP scan for pdf/images/tmbulletin, assign to numbers, output to ROOT/<prefix><num>
    """
    ok = skip = fail = 0

    tmp_base = root / "_tmp_group_extract"
    tmp_base.mkdir(parents=True, exist_ok=True)
    temp_dir = tmp_base / sanitize_folder_name(group_arc.stem)

    extract_to_temp(seven_z, group_arc, temp_dir)
    
    has_extracted_files = any(temp_dir.iterdir()) if temp_dir.exists() else False

    buckets: Dict[int, List[Path]] = {}
    unassigned = 0

    for f in temp_dir.rglob("*"):
        if not is_target_file(f):
            continue

        rel = f.relative_to(temp_dir)
        rel_parts = tuple(rel.parts[:-1])
        num = infer_num_from_relative_path(rel_parts)

        if num is None:
            num = infer_num_from_filename(f.name)

        if num is None:
            unassigned += 1
            continue

        buckets.setdefault(num, []).append(f)

    if not buckets:
        # STRUCTURAL CHECK
        if has_extracted_files:
             msg = f"CRITICAL: Content extracted from group {group_arc.name} but no target files could be mapped. Folder layout may have changed."
             logging.error(msg)
             rm_tree(temp_dir)
             raise RuntimeError(msg)

        logging.warning("No PDF/images/tmbulletin found in group %s.", group_arc.name)
        rm_tree(temp_dir)
        return (0, 1, 0)

    if unassigned:
        logging.warning("Group %s: %d target files could not be assigned to a number.", group_arc.name, unassigned)

    for num, files in sorted(buckets.items(), key=lambda x: x[0]):
        try:
            final_dir = root / f"{prefix}{num}"

            if skip_already_processed and already_processed_flat_mode(final_dir):
                skip += 1
                continue

            final_dir.mkdir(parents=True, exist_ok=True)
            if clean_final:
                clean_final_dir_flat_mode(final_dir)

            moved = 0
            for src in files:
                if src.exists():
                    move_file_with_collision_policy(src, final_dir)
                    moved += 1

            if moved > 0:
                ok += 1
            else:
                skip += 1

        except Exception as e:
            fail += 1
            logging.error("FAIL flatten %s%d from %s: %s", prefix, num, group_arc.name, e)

    rm_tree(temp_dir)
    try:
        if tmp_base.exists() and not any(tmp_base.iterdir()):
            tmp_base.rmdir()
    except OSError:
        pass

    if delete_archive:
        try:
            group_arc.unlink()
        except OSError:
            pass

    return ok, skip, fail


# ---------------------- callable entry point ----------------------
def run_extraction(root_dir: Path = None, settings=None) -> dict:
    """
    Run archive extraction. Returns summary dict.

    Args:
        root_dir: Root directory containing archives. Defaults to config.
        settings: Optional PipelineSettings override.

    Returns:
        { "extracted": int, "skipped": int, "failed": int, "duration_seconds": float }
    """
    root = Path(root_dir) if root_dir else _DEFAULT_ROOT
    skip_already = settings.skip_existing if settings else _DEFAULT_SKIP
    clean_final = settings.clean_after_extract if settings else _DEFAULT_CLEAN
    max_cd = (settings.max_cd_archives or None) if settings else _DEFAULT_MAX_CD

    seven_z_hint = settings.seven_zip_path if settings else None
    seven_z = find_7z(seven_z_hint if seven_z_hint and Path(seven_z_hint).exists() else None)

    t0 = time.time()
    direct_cd, single_issue, group_ranges = find_archives(root, max_cd)

    logger.info("ROOT: %s", root)
    logger.info("7z  : %s", seven_z)
    logger.info("CD archives (###_CD): %d", len(direct_cd))
    logger.info("Single-issue archives: %d", len(single_issue))
    logger.info("Group/range archives: %d", len(group_ranges))

    ok = skip = fail = 0

    # 1) CD archives
    for i, (num, arc, prefix, date_str) in enumerate(direct_cd, 1):
        try:
            st = process_cd_archive_cd_mode(
                seven_z=seven_z, root=root, num=num, arc=arc,
                prefix=prefix, date_str=date_str,
                skip_already_processed=skip_already,
                clean_final=clean_final, delete_archive=False,
            )
            ok += 1 if st.startswith("OK") else 0
            skip += 0 if st.startswith("OK") else 1
        except RuntimeError as e:
            if "CRITICAL" in str(e):
                logger.error("FATAL ERROR: %s", e)
                raise
            fail += 1
            logger.error("[CD %d/%d] FAIL %s: %s", i, len(direct_cd), arc.name, e)
        except Exception as e:
            fail += 1
            logger.error("[CD %d/%d] FAIL %s: %s", i, len(direct_cd), arc.name, e)

    # 2) Single issue archives
    for j, (num, arc, prefix, date_str) in enumerate(single_issue, 1):
        try:
            st = process_single_issue_archive_cd_style(
                seven_z=seven_z, root=root, num=num, arc=arc,
                prefix=prefix, date_str=date_str,
                skip_already_processed=skip_already,
                clean_final=clean_final, delete_archive=False,
            )
            ok += 1 if st.startswith("OK") else 0
            skip += 0 if st.startswith("OK") else 1
        except RuntimeError as e:
            if "CRITICAL" in str(e):
                logger.error("FATAL ERROR: %s", e)
                raise
            fail += 1
            logger.error("[SINGLE %d/%d] FAIL %s: %s", j, len(single_issue), arc.name, e)
        except Exception as e:
            fail += 1
            logger.error("[SINGLE %d/%d] FAIL %s: %s", j, len(single_issue), arc.name, e)

    # 3) Group/range archives
    for k, (g, prefix) in enumerate(group_ranges, 1):
        try:
            gok, gskip, gfail = process_group_range_archive_flatten_deep(
                seven_z=seven_z, root=root, group_arc=g, prefix=prefix,
                skip_already_processed=skip_already,
                clean_final=clean_final, delete_archive=False,
            )
            ok += gok; skip += gskip; fail += gfail
            logger.info("[GROUP %d/%d] %s (%s) -> OK=%d SKIP=%d FAIL=%d",
                        k, len(group_ranges), g.name, prefix, gok, gskip, gfail)
        except RuntimeError as e:
            if "CRITICAL" in str(e):
                logger.error("FATAL ERROR: %s", e)
                raise
            fail += 1
            logger.error("[GROUP %d/%d] FAIL %s: %s", k, len(group_ranges), g.name, e)
        except Exception as e:
            fail += 1
            logger.error("[GROUP %d/%d] FAIL %s: %s", k, len(group_ranges), g.name, e)

    duration = time.time() - t0
    logger.info("Done. Time: %.1fs | OK=%d SKIP=%d FAIL=%d", duration, ok, skip, fail)
    return {
        "extracted": ok,
        "skipped": skip,
        "failed": fail,
        "duration_seconds": round(duration, 1),
    }


# ---------------------- main (CLI) ----------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Process Marka archives: ###_CD unchanged, single-issue prefixed, group/range prefixed deep-flatten (pdf/images/tmb)."
    )
    ap.add_argument("--root", type=Path, default=_DEFAULT_ROOT)
    ap.add_argument("--7z", dest="seven_z", type=str, default=None)
    ap.add_argument("--max-cd", type=int, default=None, help="Limit number of ###_CD archives (testing)")
    ap.add_argument("--no-skip", action="store_true", help="Do NOT skip already processed")
    ap.add_argument("--no-clean", action="store_true", help="Do NOT clean final dirs before moving")
    ap.add_argument("--delete-cd-archives", action="store_true")
    ap.add_argument("--delete-single-archives", action="store_true")
    ap.add_argument("--delete-group-archives", action="store_true")
    ap.add_argument("--no-groups", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args, _unknown = ap.parse_known_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    root: Path = args.root
    seven_z = find_7z(args.seven_z)

    direct_cd, single_issue, group_ranges = find_archives(root, args.max_cd)

    logger.info("ROOT: %s", root)
    logger.info("7z  : %s", seven_z)
    logger.info("CD archives (###_CD): %d", len(direct_cd))
    logger.info("Single-issue archives: %d", len(single_issue))
    logger.info("Group/range archives: %d", len(group_ranges))

    skip_already = not args.no_skip
    clean_final = not args.no_clean

    ok = skip = fail = 0
    t0 = time.time()

    # 1) CD archives
    for i, (num, arc, prefix, date_str) in enumerate(direct_cd, 1):
        try:
            st = process_cd_archive_cd_mode(
                seven_z=seven_z, root=root, num=num, arc=arc,
                prefix=prefix, date_str=date_str,
                skip_already_processed=skip_already,
                clean_final=clean_final,
                delete_archive=args.delete_cd_archives,
            )
            ok += 1 if st.startswith("OK") else 0
            skip += 0 if st.startswith("OK") else 1
            if args.verbose:
                logger.info("[CD %d/%d] %s -> %s", i, len(direct_cd), arc.name, st)
        except RuntimeError as e:
            if "CRITICAL" in str(e):
                logger.error("FATAL ERROR: %s", e)
                return 1
            fail += 1
            logger.error("[CD %d/%d] FAIL %s: %s", i, len(direct_cd), arc.name, e)
        except Exception as e:
            fail += 1
            logger.error("[CD %d/%d] FAIL %s: %s", i, len(direct_cd), arc.name, e)

    # 2) Single issue archives
    for j, (num, arc, prefix, date_str) in enumerate(single_issue, 1):
        try:
            st = process_single_issue_archive_cd_style(
                seven_z=seven_z, root=root, num=num, arc=arc,
                prefix=prefix, date_str=date_str,
                skip_already_processed=skip_already,
                clean_final=clean_final,
                delete_archive=args.delete_single_archives,
            )
            ok += 1 if st.startswith("OK") else 0
            skip += 0 if st.startswith("OK") else 1
            if args.verbose:
                logger.info("[SINGLE %d/%d] %s (%s) -> %s", j, len(single_issue), arc.name, prefix, st)
        except RuntimeError as e:
            if "CRITICAL" in str(e):
                logger.error("FATAL ERROR: %s", e)
                return 1
            fail += 1
            logger.error("[SINGLE %d/%d] FAIL %s: %s", j, len(single_issue), arc.name, e)
        except Exception as e:
            fail += 1
            logger.error("[SINGLE %d/%d] FAIL %s: %s", j, len(single_issue), arc.name, e)

    # 3) Group/range archives
    if not args.no_groups:
        for k, (g, prefix) in enumerate(group_ranges, 1):
            try:
                gok, gskip, gfail = process_group_range_archive_flatten_deep(
                    seven_z=seven_z, root=root, group_arc=g, prefix=prefix,
                    skip_already_processed=skip_already,
                    clean_final=clean_final,
                    delete_archive=args.delete_group_archives,
                )
                ok += gok; skip += gskip; fail += gfail
                logger.info("[GROUP %d/%d] %s (%s) -> OK=%d SKIP=%d FAIL=%d",
                            k, len(group_ranges), g.name, prefix, gok, gskip, gfail)
            except RuntimeError as e:
                if "CRITICAL" in str(e):
                    logger.error("FATAL ERROR: %s", e)
                    return 1
                fail += 1
                logger.error("[GROUP %d/%d] FAIL %s: %s", k, len(group_ranges), g.name, e)
            except Exception as e:
                fail += 1
                logger.error("[GROUP %d/%d] FAIL %s: %s", k, len(group_ranges), g.name, e)

    logger.info("Done. Time: %.1fs", time.time() - t0)
    logger.info("TOTAL: OK=%d SKIP=%d FAIL=%d", ok, skip, fail)
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
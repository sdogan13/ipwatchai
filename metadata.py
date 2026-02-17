import re
import json
import codecs
import logging
import sys
import time
from pathlib import Path
from datetime import datetime

# ----------------------------
# Config (from settings with fallback defaults)
# ----------------------------
try:
    from config.settings import settings as _app_settings
    _pipe = _app_settings.pipeline
    ROOT = Path(_pipe.bulletins_root)
    SKIP_IF_METADATA_EXISTS = _pipe.skip_if_metadata_exists
    CANARY_FAILURE_THRESHOLD = _pipe.canary_failure_threshold
except Exception:
    ROOT = Path(r"C:\Users\701693\turk_patent\bulletins\Marka")
    SKIP_IF_METADATA_EXISTS = True
    CANARY_FAILURE_THRESHOLD = 0.05

OUTPUT_NAME = "metadata.json"
DEBUG_LIMIT = 0                  # Set to > 0 (e.g. 1000) to test on small data chunks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [METADATA] - %(levelname)s - %(message)s",
)
logger = logging.getLogger("turkpatent.metadata")

# Regex to capture INSERT statements.
INSERT_START_RE = re.compile(r"(?:/\*.*?\*/)?\s*INSERT\s+INTO\s+([\"`\[]?[\w\.]+[\"`\]]?)\s+VALUES\s*\(", re.IGNORECASE)

# Regex to capture DELETE statements (Handling the "Zombie Record" problem)
# Matches: DELETE FROM TRADEMARK WHERE APPLICATIONNO='2024/12345'
# Updated to handle schema prefixes like PUBLIC.TRADEMARK
DELETE_RE = re.compile(r"DELETE\s+FROM\s+[\"']?(?:\w+\.)?TRADEMARK[\"']?\s+WHERE\s+APPLICATIONNO\s*=\s*'([^']+)'", re.IGNORECASE)

# Regex to extract number from folder name
FOLDER_NUM_RE = re.compile(r"(\d+)")

# Regex to extract date from folder name (YYYY-MM-DD)
FOLDER_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

def clean_table_name(raw_name: str) -> str:
    """Cleans table name from quotes or schema prefixes."""
    if "." in raw_name:
        raw_name = raw_name.split(".")[-1]
    return raw_name.strip('"`[]').upper()

def decode_escapes(s: str) -> str:
    if not s: return ""
    if "\\u" in s or "\\t" in s or "\\n" in s or "\\x" in s:
        try:
            return codecs.decode(s, "unicode_escape")
        except Exception:
            return s
    return s

def clean_text(s: str) -> str:
    s = decode_escapes(s)
    for ch in ["\u0009", "\u000a", "\xa0", "\r", "\n", "\t"]:
        s = s.replace(ch, " ")
    return " ".join(s.split()).strip()

# Regex to strip "şekil" noise from trademark names (compiled once).
# Logo-only trademarks have NAME = "şekil" or "ABC + şekil" etc.
_SEKIL_RE = re.compile(r'\+?\s*[şŞsS][eEİi]\u0307?[kK][iİıIlL]\u0307?[lL]', re.IGNORECASE)

def clean_sekil_from_name(raw_name: str):
    """Remove 'şekil' (and variants) from a trademark name.

    - If name is ONLY şekil → return None (logo-only trademark, no text).
    - If şekil appears with other text → strip it, keep the rest.
    Handles: şekil, sekil, Şekil, ŞEKIL, SEKİL, +şekil, + şekil, +sekil, şeki̇l, ŞEKİ̇L
    """
    if not raw_name:
        return None
    name = _SEKIL_RE.sub('', raw_name, count=1)
    name = ' '.join(name.split())  # collapse whitespace
    return name if name else None

def clean_appno(s: str) -> str:
    """Standardizes Application Number for use as a dictionary key."""
    s = clean_text(s)
    return re.sub(r"\s+", "", s)

def parse_sql_values(values_block: str) -> list[str]:
    """Robustly parses SQL value list, handling commas inside quoted strings."""
    out = []
    i, n = 0, len(values_block)
    while i < n:
        while i < n and values_block[i] in " \r\n\t,":
            i += 1
        if i >= n: break

        if values_block[i] == "'":
            i += 1
            buf = []
            while i < n:
                ch = values_block[i]
                if ch == "'":
                    if i + 1 < n and values_block[i + 1] == "'":
                        buf.append("'"); i += 2; continue
                    i += 1
                    break
                buf.append(ch); i += 1
            out.append("".join(buf))
        else:
            j = i
            while j < n and values_block[j] != ",":
                j += 1
            token = values_block[i:j].strip()
            val = "" if token.upper() == "NULL" else token
            out.append(val)
            i = j
        while i < n and values_block[i] in " \r\n\t":
            i += 1
        if i < n and values_block[i] == ",":
            i += 1
    return out

def extract_values_inside_parens(insert_block: str) -> str:
    up = insert_block.upper()
    idx = up.find("VALUES")
    if idx == -1: return ""
    lp = insert_block.find("(", idx)
    if lp == -1: return ""
    rp = insert_block.rfind(")")
    if rp == -1 or rp <= lp: return ""
    return insert_block[lp+1:rp]

def parse_date_sortable(date_str: str) -> datetime:
    if not date_str: return datetime.min
    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y")
    except ValueError:
        return datetime.min

def parse_tmbulletin_files(input_paths: list[Path], source_status: str, folder_number: str, folder_date: str) -> list[dict]:
    """
    Parses SQL files into a dictionary.
    New Argument: folder_number -> Injected into metadata as BULLETIN_NO or GAZETTE_NO.
    New Argument: folder_date   -> Injected into metadata as BULLETIN_DATE or GAZETTE_DATE.
    Includes Canary Logic to detect extraction failures.
    """
    trademarks = {}
    seq = 0

    # Canary Counters
    total_trademark_attempts = 0
    failed_trademark_attempts = 0

    # Process files in order (Script first -> Log second)
    for input_path in input_paths:
        logger.info("   Reading: %s", input_path.name)

        current_table = None
        current_lines = []

        # Try encodings
        encodings = ["utf-8", "cp1254", "latin-1"]
        file_handle = None
        for enc in encodings:
            try:
                f = open(input_path, "r", encoding=enc)
                for _ in range(10): f.readline()
                f.seek(0)
                file_handle = f
                break
            except UnicodeDecodeError:
                if f: f.close()
                continue

        if not file_handle:
            # Canary Logic: Encoding failure is critical
            raise RuntimeError(f"CRITICAL: Could not detect encoding for {input_path.name}. Halting to prevent data pollution.")

        # Nested processor for INSERT blocks
        def process_insert_block(lines_list, table_name):
            nonlocal seq, total_trademark_attempts, failed_trademark_attempts

            is_trademark_table = (table_name == "TRADEMARK")
            if is_trademark_table:
                total_trademark_attempts += 1

            block = "".join(lines_list)
            values_inside = extract_values_inside_parens(block)
            if not values_inside:
                if is_trademark_table: failed_trademark_attempts += 1
                return

            values_raw = parse_sql_values(values_inside)
            if not values_raw:
                if is_trademark_table: failed_trademark_attempts += 1
                return

            appno = clean_appno(values_raw[0])
            if not appno:
                if is_trademark_table: failed_trademark_attempts += 1
                return

            # Initialize record if new
            rec = trademarks.setdefault(appno, {
                "APPLICATIONNO": appno,
                "STATUS": source_status,
                "IMAGE": appno.replace("/", "_"),
                "TRADEMARK": {},
                "HOLDERS": [],
                "ATTORNEYS": [],
                "GOODS": [],
                "EXTRACTEDGOODS": [],
            })

            def get_val(idx):
                return clean_text(values_raw[idx]) if idx < len(values_raw) else ""

            try:
                if table_name == "TRADEMARK" and len(values_raw) >= 6:
                    rec["TRADEMARK"] = {
                        "APPLICATIONDATE": get_val(1),
                        "REGISTERNO": get_val(2),
                        "REGISTERDATE": get_val(3),
                        "INTREGNO": get_val(4),
                        "NAME": clean_sekil_from_name(get_val(5)),
                        "NICECLASSES_RAW": get_val(6),
                        "NICECLASSES_LIST": to_class_list(values_raw[6]) if len(values_raw) > 6 else [],
                        "TM_TYPE_CODE": get_val(7),
                        "VIENNACLASSES_RAW": get_val(8),
                        "VIENNACLASSES_LIST": to_class_list(values_raw[8]) if len(values_raw) > 8 else [],
                        "BULLETIN_NO": get_val(9), # Original from DB
                        "BULLETIN_DATE": get_val(10),
                        "EXTRA_COL_11": get_val(11),
                        "EXTRA_COL_12": get_val(12)
                    }

                    # --- INJECT FOLDER METADATA LOGIC ---
                    if source_status == "Registered":
                        rec["TRADEMARK"]["GAZETTE_NO"] = folder_number
                        if folder_date:
                            rec["TRADEMARK"]["GAZETTE_DATE"] = folder_date
                    else:
                        rec["TRADEMARK"]["BULLETIN_NO"] = folder_number
                        if folder_date:
                            rec["TRADEMARK"]["BULLETIN_DATE"] = folder_date
                    # ----------------------------------

                elif table_name == "HOLDER" and len(values_raw) >= 2:
                    holder = {
                        "TPECLIENTID": get_val(1),
                        "TITLE": get_val(2),
                        "ADDRESS": get_val(3),
                        "TOWN_DISTRICT": get_val(4),
                        "POSTALCODE": get_val(5),
                        "CITY_PROVINCE": get_val(6),
                        "COUNTRY": get_val(7)
                    }
                    if holder not in rec["HOLDERS"]:
                        rec["HOLDERS"].append(holder)
                elif table_name == "ATTORNEY" and len(values_raw) >= 2:
                    atty = { "NO": get_val(1), "NAME": get_val(2), "TITLE": get_val(3) }
                    if atty not in rec["ATTORNEYS"]:
                        rec["ATTORNEYS"].append(atty)
                elif table_name in ("GOODS", "EXTRACTEDGOODS") and len(values_raw) >= 4:
                    c_id = get_val(1)
                    s_id = get_val(2)
                    txt = get_val(3)

                    is_duplicate = False
                    for g in rec[table_name]:
                        if g["CLASSID"] == c_id and g.get("SUBCLASSID") == s_id and g["TEXT"] == txt:
                            is_duplicate = True
                            break

                    if not is_duplicate:
                        item = { "CLASSID": c_id, "SUBCLASSID": s_id, "TEXT": txt, "SEQ": seq }
                        seq += 1
                        rec[table_name].append(item)
            except Exception:
                if is_trademark_table: failed_trademark_attempts += 1
                pass

        # Parse File Loop
        line_count = 0
        deleted_count = 0
        try:
            for line in file_handle:
                line_count += 1
                if DEBUG_LIMIT > 0 and line_count > DEBUG_LIMIT: break

                # 1. CHECK FOR DELETE
                m_del = DELETE_RE.search(line)
                if m_del:
                    del_id = clean_appno(m_del.group(1))
                    if del_id in trademarks:
                        del trademarks[del_id]
                        deleted_count += 1
                    continue

                # 2. CHECK FOR INSERT
                m = INSERT_START_RE.search(line)
                if m:
                    if current_table and current_lines:
                        process_insert_block(current_lines, current_table)
                    current_table = clean_table_name(m.group(1))
                    current_lines = [line]
                else:
                    if current_table:
                        current_lines.append(line)

            # Flush last block
            if current_table and current_lines:
                process_insert_block(current_lines, current_table)

        finally:
            file_handle.close()
            logger.info("      Scanned %d lines. Deletions applied: %d", line_count, deleted_count)

    # === CANARY CHECK ===
    if total_trademark_attempts > 0:
        failure_rate = failed_trademark_attempts / total_trademark_attempts
        if failure_rate > CANARY_FAILURE_THRESHOLD:
            msg = f"CRITICAL CANARY FAILURE: Parsing error rate is {failure_rate:.2%} (Limit: {CANARY_FAILURE_THRESHOLD:.0%}). {failed_trademark_attempts}/{total_trademark_attempts} records failed. Halting."
            raise RuntimeError(msg)

    # Final Compilation
    logger.info("   Final DB Size: %d records.", len(trademarks))
    out_list = list(trademarks.values())

    # Sort Goods within records
    for rec in out_list:
        rec.pop("GOODS_TEXT", None)
        rec.pop("EXTRACTEDGOODS_TEXT", None)
        if "GOODS" in rec:
            rec["GOODS"].sort(key=lambda x: x["SEQ"])

    # Sort Records by Date
    out_list.sort(key=lambda x: parse_date_sortable(x.get("TRADEMARK", {}).get("APPLICATIONDATE", "")))
    return out_list

def to_class_list(s: str) -> list[str]:
    s = clean_text(s)
    return re.findall(r"\d{1,3}", s)

def find_db_files(folder: Path) -> list[Path]:
    """Finds the relevant database files."""
    all_files = list(folder.rglob("*"))

    script_file = None
    log_file = None
    fallback_txt = None

    for f in all_files:
        if not f.is_file(): continue
        low_name = f.name.lower()

        if low_name.endswith(".script") and "tmbulletin" in low_name:
            if not script_file or f.stat().st_size > script_file.stat().st_size:
                script_file = f
        elif low_name.endswith(".log") and "tmbulletin" in low_name:
             if not log_file or f.stat().st_size > log_file.stat().st_size:
                log_file = f
        elif "tmbulletin" in low_name and f.suffix == ".txt":
             if not fallback_txt or f.stat().st_size > fallback_txt.stat().st_size:
                fallback_txt = f

    files_to_process = []

    if script_file and script_file.stat().st_size > 0:
        files_to_process.append(script_file)
        if log_file and log_file.stat().st_size > 0:
            files_to_process.append(log_file)
    elif fallback_txt:
        files_to_process.append(fallback_txt)
    else:
        gazette_txts = [f for f in all_files if "gazete" in f.name.lower() and f.suffix == ".txt"]
        if gazette_txts:
            files_to_process.append(max(gazette_txts, key=lambda p: p.stat().st_size))

    return files_to_process

def get_folder_number(p: Path) -> int:
    m = FOLDER_NUM_RE.search(p.name)
    if m: return int(m.group(1))
    return 999999

def extract_folder_number_str(folder_name: str) -> str:
    m = FOLDER_NUM_RE.search(folder_name)
    if m: return m.group(1)
    return "000"

def extract_folder_date_str(folder_name: str) -> str:
    """Extracts date in YYYY-MM-DD format from folder name if present."""
    m = FOLDER_DATE_RE.search(folder_name)
    if m: return m.group(1)
    return None

# =============================================================================
# Callable Functions for Pipeline Integration
# =============================================================================

def process_single_folder(folder_path: Path, skip_existing: bool = True) -> dict:
    """
    Process a single folder to extract metadata from HSQLDB SQL files.

    Args:
        folder_path: Path to the folder containing HSQLDB files
        skip_existing: If True, skip folders that already have metadata.json

    Returns:
        dict with keys: 'status', 'records', 'error'
        - status: 'success', 'skipped', 'no_db_files', 'error'
        - records: number of records extracted (0 if skipped/error)
        - error: error message if status is 'error'
    """
    result = {"status": "unknown", "records": 0, "error": None}

    if not folder_path.is_dir():
        result["status"] = "error"
        result["error"] = f"Not a directory: {folder_path}"
        return result

    db_files = find_db_files(folder_path)

    if not db_files:
        result["status"] = "no_db_files"
        return result

    out_path = folder_path / OUTPUT_NAME

    if skip_existing and out_path.exists():
        result["status"] = "skipped"
        return result

    # Detect Status based on folder name
    fname = folder_path.name.lower()
    if "gazete" in fname or "gz_" in fname:
        status = "Registered"
    else:
        status = "Application/Published"

    # Extract Folder Number String (e.g. "484")
    folder_num_str = extract_folder_number_str(folder_path.name)

    # Extract Folder Date String (e.g. "2026-01-12")
    folder_date_str = extract_folder_date_str(folder_path.name)

    try:
        data = parse_tmbulletin_files(db_files, status, folder_num_str, folder_date_str)

        if not data:
            result["status"] = "no_data"
            return result

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        result["status"] = "success"
        result["records"] = len(data)
        return result

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        return result


def run_metadata_extraction(
    root_path: Path = None,
    skip_existing: bool = True,
    stop_on_error: bool = True,
    verbose: bool = True
) -> dict:
    """
    Run metadata extraction on all folders in root_path.

    Args:
        root_path: Root directory containing bulletin folders (default: ROOT from config)
        skip_existing: If True, skip folders that already have metadata.json
        stop_on_error: If True, stop on critical parsing errors
        verbose: If True, log progress messages

    Returns:
        dict with keys: 'processed', 'skipped', 'failed', 'no_db_files', 'errors'
    """
    if root_path is None:
        root_path = ROOT

    if not root_path.exists():
        raise FileNotFoundError(f"Root path not found: {root_path}")

    if verbose:
        logger.info("Starting Metadata Extraction on: %s", root_path)

    all_dirs = sorted([p for p in root_path.iterdir() if p.is_dir()], key=get_folder_number)

    stats = {
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "no_db_files": 0,
        "errors": []
    }

    for folder in all_dirs:
        result = process_single_folder(folder, skip_existing=skip_existing)

        if result["status"] == "success":
            stats["processed"] += 1
            if verbose:
                logger.info("[OK] %s: %d records", folder.name, result["records"])
        elif result["status"] == "skipped":
            stats["skipped"] += 1
        elif result["status"] == "no_db_files":
            stats["no_db_files"] += 1
        elif result["status"] == "error":
            stats["failed"] += 1
            stats["errors"].append({"folder": folder.name, "error": result["error"]})
            if verbose:
                logger.error("[ERROR] %s: %s", folder.name, result["error"])
            if stop_on_error and "CRITICAL" in str(result["error"]).upper():
                raise RuntimeError(f"Critical error in {folder.name}: {result['error']}")

    if verbose:
        logger.info("Finished. Processed: %d, Skipped: %d, Failed: %d, No DB Files: %d",
                     stats["processed"], stats["skipped"], stats["failed"], stats["no_db_files"])

    return stats


def run_metadata(root_dir: Path = None, settings=None) -> dict:
    """
    Run metadata extraction. Returns summary dict.

    Args:
        root_dir: Root directory override. Defaults to config.
        settings: Optional PipelineSettings override.

    Returns:
        { "processed": int, "skipped": int, "failed": int, "duration_seconds": float }
    """
    global SKIP_IF_METADATA_EXISTS, CANARY_FAILURE_THRESHOLD

    if settings is not None:
        root_dir = root_dir or Path(settings.bulletins_root)
        SKIP_IF_METADATA_EXISTS = settings.skip_if_metadata_exists
        CANARY_FAILURE_THRESHOLD = settings.canary_failure_threshold

    t0 = time.time()
    stats = run_metadata_extraction(
        root_path=root_dir,
        skip_existing=SKIP_IF_METADATA_EXISTS,
        stop_on_error=True,
        verbose=True,
    )
    duration = time.time() - t0

    return {
        "processed": stats["processed"],
        "skipped": stats["skipped"],
        "failed": stats["failed"],
        "no_db_files": stats.get("no_db_files", 0),
        "duration_seconds": round(duration, 1),
    }


if __name__ == "__main__":
    if not ROOT.exists():
        logger.error("Root path not found: %s", ROOT)
        sys.exit(1)

    logger.info("Starting Metadata Extraction on: %s", ROOT)
    result = run_metadata()
    logger.info("Result: %s", result)
    if result["failed"] > 0:
        sys.exit(1)

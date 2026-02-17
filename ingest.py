import json
import os
import sys
import time
import shutil
import psycopg2
import getpass
import argparse
from psycopg2.extras import Json, execute_values
from pathlib import Path
from datetime import datetime, date, timedelta
from utils.deadline import calculate_appeal_deadline
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import re as _re

# ===================== DATABASE CONNECTION POOL =====================
from db.pool import (
    get_connection,
    release_connection,
    connection_context,
    close_pool
)

# ===================== CONFIG =====================
ROOT_DIR = Path(os.getenv("DATA_ROOT", r"C:\Users\701693\turk_patent\bulletins\Marka"))
# UPDATED: Point to the generated file containing vectors (525KB file)
CLASSES_FILE = ROOT_DIR / "nice_classes_with_embeddings.json"

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_db_connection():
    """
    Get a database connection from the pool.

    IMPORTANT: Caller must release the connection when done using:
        release_connection(conn)

    Or use connection_context() for automatic cleanup:
        with connection_context() as conn:
            # use connection
    """
    return get_connection()

# ============================================
# UNIVERSAL SCAN QUEUE INTEGRATION
# ============================================

def extract_bulletin_info(folder_name: str):
    """
    Extract bulletin number and date from folder name.

    Examples:
        BLT_2025_03 -> ('2025/03', date(2025, 3, 1))
        BLT2025-03  -> ('2025/03', date(2025, 3, 1))

    Returns:
        (bulletin_no, bulletin_date) or (None, None)
    """
    patterns = [
        r'BLT[_-]?(\d{4})[_-](\d{2})',
        r'(\d{4})[_-](\d{2})',
    ]
    for pattern in patterns:
        match = _re.search(pattern, folder_name)
        if match:
            year, month = match.groups()
            bulletin_no = f"{year}/{month}"
            try:
                bulletin_date = datetime(int(year), int(month), 1).date()
            except ValueError:
                bulletin_date = None
            return bulletin_no, bulletin_date
    return None, None


def _check_scan_queue_table(conn) -> bool:
    """Check if universal_scan_queue table exists (cached)."""
    if not hasattr(_check_scan_queue_table, '_exists'):
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public'
                        AND table_name = 'universal_scan_queue'
                    )
                """)
                _check_scan_queue_table._exists = cur.fetchone()[0]
        except Exception:
            _check_scan_queue_table._exists = False
    return _check_scan_queue_table._exists


def add_to_scan_queue(conn, trademark_ids: list, bulletin_no: str = None,
                      bulletin_date=None, priority: int = 0):
    """
    Add newly ingested trademarks to the universal scan queue
    for Opposition Radar processing.

    Args:
        conn: Database connection
        trademark_ids: List of trademark UUIDs to queue
        bulletin_no: Bulletin number for tracking
        bulletin_date: Bulletin publication date
        priority: Processing priority (higher = first)

    Returns:
        Number of items queued
    """
    if not trademark_ids:
        return 0

    if not _check_scan_queue_table(conn):
        return 0

    try:
        with conn.cursor() as cur:
            values = [(str(tid), bulletin_no, bulletin_date, priority) for tid in trademark_ids]

            execute_values(cur, """
                INSERT INTO universal_scan_queue (trademark_id, bulletin_no, bulletin_date, priority)
                VALUES %s
                ON CONFLICT (trademark_id) DO NOTHING
            """, values)

            queued_count = cur.rowcount
            conn.commit()

            if queued_count > 0:
                logging.info(f"   Queued {queued_count} trademarks for Opposition Radar scan")

            return queued_count

    except Exception as e:
        logging.warning(f"   Failed to queue for Opposition Radar scan: {e}")
        return 0


def parse_date(date_str):
    """Robust date parser handling multiple formats."""
    if not date_str: return None
    date_str = str(date_str).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None

def embedding_to_halfvec(embedding, expected_dim=None):
    """
    Convert embedding list to PostgreSQL halfvec string format.

    Args:
        embedding: List of floats or None
        expected_dim: Expected number of dimensions (e.g. 512, 768, 384).
                      If provided and actual dims don't match, returns None.

    Returns:
        String in format '[0.1,0.2,0.3,...]' or None
    """
    if embedding is None:
        return None
    if not isinstance(embedding, (list, tuple)):
        return None
    if len(embedding) == 0:
        return None
    if expected_dim is not None and len(embedding) != expected_dim:
        return None
    try:
        return '[' + ','.join(str(float(v)) for v in embedding) + ']'
    except (TypeError, ValueError):
        return None

def determine_status(folder_name, status_raw, reg_no_val=None):
    """
    Maps raw text and context to Database Enum.

    Logic priority (text wins):
    1. If status_raw has text, apply keyword matching for ALL folder types
    2. If no match from keywords, check reg_no_val → 'Registered'
    3. If no text AND no reg_no, fall back to folder-based defaults

    Turkish status text mappings:
    - Refused: geçersiz, reddedildi, etc.
    - Withdrawn: feragat edildi, geri çekildi, etc.
    - Registered: tescil edildi, tescilli, kabul edildi
    - Opposed: itiraz
    - Expired: sona erdi, süresi doldu, hükümsüz, etc.
    - Published: yayınlandı, yayinlandi, ilan edildi
    """
    folder_upper = folder_name.upper()
    status_lower = str(status_raw).lower().strip() if status_raw else ""
    has_reg_no = reg_no_val and str(reg_no_val).strip() and str(reg_no_val).strip().lower() not in ('null', 'none', '')

    # ============================================
    # 1. STATUS TEXT KEYWORDS (applies to ALL sources)
    # ============================================
    if status_lower:
        # Refused
        refused_keywords = [
            'geçersiz', 'gecersiz',
            'marka başvurusu/tescili geçersiz',
            'başvuru geçersiz', 'basvuru gecersiz',
            'tescil geçersiz', 'tescil gecersiz',
            'reddedildi', 'red edildi',
            'ret kararı', 'red kararı',
        ]
        if any(kw in status_lower for kw in refused_keywords):
            return 'Refused'

        # Withdrawn
        withdrawn_keywords = [
            'feragat edildi', 'feragat',
            'geri çekildi', 'geri cekildi',
            'geri alındı', 'geri alindi',
            'vazgeçildi', 'vazgecildi',
            'iptal edildi',
        ]
        if any(kw in status_lower for kw in withdrawn_keywords):
            return 'Withdrawn'

        # Registered
        registered_keywords = [
            'tescil edildi', 'tescilli',
            'kabul edildi',
        ]
        if any(kw in status_lower for kw in registered_keywords):
            return 'Registered'

        # Opposed
        if 'itiraz' in status_lower:
            return 'Opposed'

        # Expired
        expired_keywords = ['sona erdi', 'süresi doldu', 'suresi doldu', 'hükümsüz', 'hukumsuz']
        if any(kw in status_lower for kw in expired_keywords):
            return 'Expired'

        # Published
        published_keywords = ['yayınlandı', 'yayinlandi', 'ilan edildi']
        if any(kw in status_lower for kw in published_keywords):
            return 'Published'

        # status_lower has text but no keywords matched → fall through

    # ============================================
    # 2. REGISTRATION NUMBER → Registered
    # ============================================
    if has_reg_no:
        return 'Registered'

    # ============================================
    # 3. FOLDER-BASED DEFAULTS (no text, no reg_no)
    # ============================================
    if folder_upper.startswith("GZ_") or "GAZETE" in folder_upper:
        return 'Registered'
    if folder_upper.startswith("BLT_") or "BULTEN" in folder_upper:
        return 'Published'
    if folder_upper.startswith("APP_") or "SCRAPED" in folder_upper:
        return 'Applied'

    return 'Applied'

def get_status_rank(status):
    ranks = {
        'Renewed': 4, 'Registered': 3, 'Transferred': 3,
        'Expired': 2, 'Opposed': 2, 'Refused': 2, 'Withdrawn': 2,
        'Published': 1, 'Partial Refusal': 1, 'Applied': 0
    }
    return ranks.get(status, -1)

def get_source_rank(folder_name):
    """Source authority hierarchy: APP_ (3) > GZ_ (2) > BLT_ (1)."""
    fu = folder_name.upper()
    if fu.startswith("APP_") or "SCRAPED" in fu:
        return 3, 'APP'
    if fu.startswith("GZ_") or "GAZETE" in fu:
        return 2, 'GZ'
    return 1, 'BLT'


# ===================== SOURCE PRIORITY + FIELD OWNERSHIP =====================
#
# Three categories of fields in UPDATE statements:
#   SHARED     — subject to source priority (APP > GZ > BLT)
#   BLT-owned  — only BLT_ sources may write
#   GZ-owned   — only GZ_ sources may write
#
# Each tuple: (db_column_name, VALUES_alias_expression)

_SHARED_FIELDS = [
    ('name',                  'v.name'),
    ('name_tr',               'v.name_tr'),
    ('detected_lang',         'v.detected_lang'),
    ('holder_name',           'v.holder_name'),
    ('holder_tpe_client_id',  'v.holder_tpe_client_id'),
    ('attorney_name',         'v.attorney_name'),
    ('attorney_no',           'v.attorney_no'),
    ('nice_class_numbers',    'v.nice_classes::integer[]'),
    ('vienna_class_numbers',  'v.vienna_classes::integer[]'),
    ('extracted_goods',       'v.goods::jsonb'),
    ('application_date',      'v.app_date::date'),
    ('last_event_date',       'v.last_date::date'),
    ('expiry_date',           'v.expiry::date'),
    ('image_path',            'v.img_path'),
    ('image_embedding',       'v.img_emb::halfvec(512)'),
    ('dinov2_embedding',      'v.dino_emb::halfvec(768)'),
    ('text_embedding',        'v.txt_emb::halfvec(384)'),
    ('color_histogram',       'v.color_emb::halfvec(512)'),
    ('logo_ocr_text',         'v.ocr_text'),
]

_BLT_OWNED_FIELDS = [
    ('bulletin_no',      'v.b_no'),
    ('bulletin_date',    'v.b_date::date'),
    ('appeal_deadline',  'v.appeal::date'),
]

_GZ_OWNED_FIELDS = [
    ('registration_no',    'v.reg_no'),
    ('wipo_no',            'v.wipo_no'),
    ('registration_date',  'v.reg_date::date'),
    ('gazette_no',         'v.g_no'),
    ('gazette_date',       'v.g_date::date'),
]


def _priority_coalesce(col, val, source):
    """SQL SET clause for a shared field respecting source priority.

    APP_ (rank 3): always writes — COALESCE(new, existing).
    GZ_  (rank 2): overwrites BLT_ but not APP_.
    BLT_ (rank 1): only fills NULLs when APP_/GZ_ present; overwrites other BLT_.
    """
    tc = f"tm.{col}"
    if source == 'APP':
        return f"{col} = COALESCE({val}, {tc})"
    if source == 'GZ':
        return (
            f"{col} = CASE\n"
            f"                    WHEN COALESCE(tm.status_source, '') = 'APP'"
            f" THEN COALESCE({tc}, {val})\n"
            f"                    ELSE COALESCE({val}, {tc})\n"
            f"                END"
        )
    # BLT
    return (
        f"{col} = CASE\n"
        f"                    WHEN COALESCE(tm.status_source, '') IN ('APP', 'GZ')"
        f" THEN COALESCE({tc}, {val})\n"
        f"                    ELSE COALESCE({val}, {tc})\n"
        f"                END"
    )


def _owned_field(col, val, owner, source):
    """SQL SET clause for an owned field.  Owner always writes; others never touch."""
    tc = f"tm.{col}"
    if source == owner:
        return f"{col} = COALESCE({val}, {tc})"
    return f"{col} = {tc}"


def _build_update_set(source):
    """Build the full SET clause for a source-aware UPDATE.

    Covers every field: shared (priority), BLT-owned, GZ-owned,
    current_status, status_source, updated_at.
    """
    parts = []

    # Shared fields — source priority
    for col, val in _SHARED_FIELDS:
        parts.append(_priority_coalesce(col, val, source))

    # BLT-owned fields — only BLT_ writes
    for col, val in _BLT_OWNED_FIELDS:
        parts.append(_owned_field(col, val, 'BLT', source))

    # GZ-owned fields — only GZ_ writes
    for col, val in _GZ_OWNED_FIELDS:
        parts.append(_owned_field(col, val, 'GZ', source))

    # current_status — priority-aware
    if source == 'APP':
        parts.append("current_status = v.status::tm_status")
    elif source == 'GZ':
        parts.append(
            "current_status = CASE\n"
            "                    WHEN COALESCE(tm.status_source, '') = 'APP'"
            " THEN tm.current_status\n"
            "                    ELSE v.status::tm_status\n"
            "                END"
        )
    else:
        parts.append(
            "current_status = CASE\n"
            "                    WHEN COALESCE(tm.status_source, '') IN ('APP', 'GZ')"
            " THEN tm.current_status\n"
            "                    ELSE v.status::tm_status\n"
            "                END"
        )

    # status_source — priority-aware
    if source == 'APP':
        parts.append("status_source = 'APP'")
    elif source == 'GZ':
        parts.append(
            "status_source = CASE\n"
            "                    WHEN COALESCE(tm.status_source, '') = 'APP'"
            " THEN tm.status_source\n"
            "                    ELSE 'GZ'\n"
            "                END"
        )
    else:
        parts.append(
            "status_source = CASE\n"
            "                    WHEN COALESCE(tm.status_source, '') IN ('APP', 'GZ')"
            " THEN tm.status_source\n"
            "                    ELSE 'BLT'\n"
            "                END"
        )

    parts.append("updated_at = NOW()")
    return ',\n                    '.join(parts)


def _build_update_sql(source):
    """Build the complete UPDATE ... FROM (VALUES) SQL for a source type.

    Args:
        source: 'APP', 'GZ', or 'BLT'
    """
    return f"""
                UPDATE trademarks AS tm
                SET
                    {_build_update_set(source)}
                FROM (VALUES %s) AS v(
                    name, status, nice_classes, goods, last_date, appeal, expiry,
                    b_no, b_date, g_no, g_date, img_path,
                    app_date, reg_date, img_emb, dino_emb, txt_emb, color_emb,
                    ocr_text,
                    name_tr, detected_lang,
                    holder_name, holder_tpe_client_id,
                    attorney_name, attorney_no,
                    src_tag,
                    reg_no, wipo_no, vienna_classes,
                    app_no
                )
                WHERE tm.application_no = v.app_no
            """


def clean_name(raw_name):
    """Basic name cleanup (whitespace normalization).

    Note: şekil removal is now handled earlier in metadata.py during extraction.
    Returns cleaned name or None if the name is empty.
    """
    if not raw_name:
        return None
    name = ' '.join(raw_name.split())  # collapse whitespace
    return name if name else None

def sanitize(val):
    """Convert dirty/sentinel values to real Python None.

    Catches: literal "null"/"None"/"N/A"/"-", empty strings,
    whitespace-only strings, empty lists, empty dicts.
    Strips surrounding whitespace from strings.
    """
    if val is None:
        return None
    if isinstance(val, str):
        stripped = val.strip()
        if stripped == "" or stripped.lower() in ("null", "none", "n/a", "-"):
            return None
        return stripped
    if isinstance(val, list) and len(val) == 0:
        return None
    if isinstance(val, dict) and len(val) == 0:
        return None
    return val

def _trunc(val, max_len):
    """Sanitize then truncate string to max_len."""
    s = sanitize(val)
    if s is None:
        return None
    s = str(s)
    return s[:max_len] if len(s) > max_len else s

def extract_tpe_id(name_str):
    """Extract TPE Client ID from name string like 'ACME CORP (12345)' -> ('ACME CORP', '12345')"""
    if not name_str or not isinstance(name_str, str):
        return name_str, None
    trimmed = name_str.strip()
    id_match = _re.search(r'\s*\((\d+)\)', trimmed)
    if id_match:
        clean_name = trimmed[:id_match.start()].strip()
        tpe_id = id_match.group(1)
        return clean_name, tpe_id
    return trimmed, None

# Pre-scanned file index: basename (no ext) -> full filename, built once per directory
_file_index: dict[str, dict[str, str]] = {}  # dir_key -> {basename: filename}


def _build_file_index(dir_path: Path, dir_key: str) -> dict[str, str]:
    """Scan a directory once and index files by basename (without extension)."""
    index = {}
    if dir_path.is_dir():
        for f in dir_path.iterdir():
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                index[f.stem] = f.name
    _file_index[dir_key] = index
    return index


def _resolve_image_path(folder_name: str, image_field: str, root_dir: Path) -> str | None:
    """Build a full relative image path using pre-scanned file index (O(1) lookup).

    Search order:
      1. Per-folder images/ directory (preferred, small)
      2. Shared LOGOS/ directory (fallback)

    Returns forward-slash relative path from project root
    (e.g. "bulletins/Marka/BLT_253/images/2011_41714.jpg"), or None.
    """
    if not image_field:
        return None
    image_field = sanitize(image_field)
    if image_field is None:
        return None

    # Determine the relative prefix (e.g. "bulletins/Marka")
    try:
        project_root = root_dir.parent.parent
        rel_root = root_dir.relative_to(project_root)
    except ValueError:
        rel_root = Path("bulletins/Marka")

    rel_prefix = str(rel_root).replace("\\", "/")

    # Check per-folder images/ directory (indexed once per folder)
    img_dir_key = f"{root_dir}/{folder_name}/images"
    if img_dir_key not in _file_index:
        _build_file_index(root_dir / folder_name / "images", img_dir_key)

    img_index = _file_index[img_dir_key]
    if image_field in img_index:
        filename = img_index[image_field]
        return f"{rel_prefix}/{folder_name}/images/{filename}"

    # Fallback: check shared LOGOS/ directory
    logos_dir_key = f"{root_dir}/LOGOS"
    if logos_dir_key not in _file_index:
        _build_file_index(root_dir / "LOGOS", logos_dir_key)

    logos_index = _file_index[logos_dir_key]
    if image_field in logos_index:
        filename = logos_index[image_field]
        return f"{rel_prefix}/LOGOS/{filename}"

    return None


# ===================== SELF-HEALING FOR CORRUPT metadata.json =====================

def _has_tmbulletin_source(folder_path: Path) -> bool:
    """Check if tmbulletin/HSQLDB source files exist in a folder for metadata regeneration."""
    for root, dirs, files in os.walk(folder_path):
        for fname in files:
            flow = fname.lower()
            if 'tmbulletin' in flow and (flow.endswith('.log') or flow.endswith('.script') or flow.endswith('.txt')):
                return True
            if 'gazete' in flow and flow.endswith('.txt'):
                return True
    return False


def _repair_corrupt_metadata(metadata_path: Path) -> dict:
    """Attempt to repair a corrupt metadata.json by regenerating from tmbulletin source.

    After regenerating metadata, runs ai.py process_folder() to generate embeddings
    so the repaired records are ingested with full AI features.

    Returns: {'status': 'repaired'|'unrecoverable'|'regen_failed', 'records': int, 'error': str|None}
    """
    folder_path = metadata_path.parent
    folder_name = folder_path.name

    # Step 1: Check if source data exists
    if not _has_tmbulletin_source(folder_path):
        logging.warning(f"   UNRECOVERABLE: {folder_name} — no tmbulletin source files")
        return {"status": "unrecoverable", "records": 0, "error": "No tmbulletin source files"}

    # Step 2: Backup corrupt file (don't overwrite existing backups)
    backup_path = metadata_path.parent / "metadata.json.corrupt_backup"
    if backup_path.exists():
        idx = 1
        while (metadata_path.parent / f"metadata.json.corrupt_backup.{idx}").exists():
            idx += 1
        backup_path = metadata_path.parent / f"metadata.json.corrupt_backup.{idx}"

    try:
        shutil.copy2(str(metadata_path), str(backup_path))
        logging.info(f"   Backed up corrupt file -> {backup_path.name}")
    except Exception as e:
        logging.error(f"   Failed to backup corrupt file: {e}")
        return {"status": "regen_failed", "records": 0, "error": f"Backup failed: {e}"}

    # Step 3: Remove corrupt file so regeneration doesn't skip it
    try:
        metadata_path.unlink()
    except Exception as e:
        logging.error(f"   Failed to remove corrupt file: {e}")
        return {"status": "regen_failed", "records": 0, "error": f"Remove failed: {e}"}

    # Step 4: Run metadata.py's process_single_folder to regenerate
    try:
        from metadata import process_single_folder as _regen_folder
        result = _regen_folder(folder_path, skip_existing=False)

        if result["status"] == "success" and result["records"] > 0:
            # Step 5: Verify the regenerated file is valid JSON
            with open(metadata_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, list) or len(data) == 0:
                raise ValueError("Regenerated file is empty or not a JSON list")

            # Step 6: Run ai.py to generate embeddings for the repaired metadata
            try:
                from ai import process_folder as _ai_process_folder
                logging.info(f"   Running AI feature generation for {folder_name} ({len(data)} records)...")
                _ai_process_folder(folder_path)
                logging.info(f"   REPAIRED: {folder_name} — regenerated {len(data)} records (with AI features)")
            except Exception as ai_err:
                logging.warning(f"   REPAIRED (no AI): {folder_name} — {len(data)} records, AI failed: {ai_err}")

            return {"status": "repaired", "records": len(data), "error": None}
        else:
            error_msg = result.get("error") or f"metadata.py returned status={result['status']}"
            logging.error(f"   REGEN FAILED: {folder_name} — {error_msg}")
            # Restore backup if regeneration produced nothing usable
            if not metadata_path.exists() and backup_path.exists():
                shutil.copy2(str(backup_path), str(metadata_path))
            return {"status": "regen_failed", "records": 0, "error": error_msg}

    except Exception as e:
        logging.error(f"   REGEN FAILED: {folder_name} — {e}")
        # Restore backup if regeneration crashed
        if not metadata_path.exists() and backup_path.exists():
            shutil.copy2(str(backup_path), str(metadata_path))
        return {"status": "regen_failed", "records": 0, "error": str(e)}


def pre_scan_and_repair(base_dir: Path) -> dict:
    """Scan all metadata.json files and repair corrupt ones before ingestion starts.

    This runs ONCE at the start of ingestion. The per-folder JSONDecodeError handler
    in process_file_batch is just a safety net for anything missed here.
    """
    repair_stats = {
        'repaired': [],       # [(folder_name, record_count), ...]
        'unrecoverable': [],  # [folder_name, ...]
        'regen_failed': [],   # [folder_name, ...]
    }

    logging.info("=" * 60)
    logging.info("Pre-scan: checking all metadata.json files for corruption...")

    metadata_files = sorted(base_dir.rglob("metadata.json"))
    corrupt_count = 0

    for meta_path in metadata_files:
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise json.JSONDecodeError("Root element is not a JSON array", "", 0)
        except json.JSONDecodeError as e:
            corrupt_count += 1
            folder_name = meta_path.parent.name
            file_size = meta_path.stat().st_size
            logging.warning(
                f"   CORRUPT: {folder_name}/metadata.json "
                f"(size={file_size:,} bytes, error at pos {e.pos}: {e.msg})"
            )

            result = _repair_corrupt_metadata(meta_path)
            if result["status"] == "repaired":
                repair_stats["repaired"].append((folder_name, result["records"]))
            elif result["status"] == "unrecoverable":
                repair_stats["unrecoverable"].append(folder_name)
            else:
                repair_stats["regen_failed"].append(folder_name)
        except Exception as e:
            logging.warning(f"   Cannot read {meta_path.parent.name}/metadata.json: {e}")

    # Summary
    if corrupt_count > 0:
        repaired_count = len(repair_stats["repaired"])
        repaired_records = sum(r[1] for r in repair_stats["repaired"])
        logging.info(f"Pre-scan complete: {corrupt_count} corrupt file(s) found")
        logging.info(f"   Repaired and ready: {repaired_count} folders ({repaired_records} records)")
        logging.info(f"   Unrecoverable (no source): {len(repair_stats['unrecoverable'])} folders")
        logging.info(f"   Regeneration failed: {len(repair_stats['regen_failed'])} folders")
    else:
        logging.info("Pre-scan complete: all metadata.json files are valid")
    logging.info("=" * 60)

    return repair_stats


def _print_repair_summary(repair_stats: dict):
    """Print self-healing summary at the end of ingestion."""
    repaired = repair_stats.get('repaired', [])
    unrecoverable = repair_stats.get('unrecoverable', [])
    regen_failed = repair_stats.get('regen_failed', [])

    if not repaired and not unrecoverable and not regen_failed:
        return  # Nothing to report

    logging.info("")
    logging.info("=" * 60)
    logging.info("Self-healing summary:")

    if repaired:
        total_records = sum(r[1] for r in repaired)
        logging.info(f"   Repaired and ingested: {len(repaired)} folders ({total_records} records)")
        for folder, count in repaired:
            logging.info(f"     - {folder}: {count} records")
    else:
        logging.info(f"   Repaired and ingested: 0 folders")

    if unrecoverable:
        logging.info(f"   Unrecoverable (no source): {len(unrecoverable)} folders")
        for folder in unrecoverable:
            logging.info(f"     - {folder}")
    else:
        logging.info(f"   Unrecoverable (no source): 0 folders")

    if regen_failed:
        logging.info(f"   Regeneration failed: {len(regen_failed)} folders")
        for folder in regen_failed:
            logging.info(f"     - {folder}")
    else:
        logging.info(f"   Regeneration failed: 0 folders")

    logging.info("=" * 60)


def calculate_expiration_status(current_status, ref_date, status_raw=None):
    """
    Only mark as Expired if the source data EXPLICITLY says expired.
    Don't auto-expire based on date calculation - renewals happen.
    """
    # Check for explicit expiration keywords in raw status
    if status_raw:
        status_lower = str(status_raw).lower()
        expired_keywords = ['sona erdi', 'süresi doldu', 'suresi doldu', 'süre sonu',
                           'sure sonu', 'expired', 'yürürlükten', 'yururlukten', 'hükümsüz']
        if any(kw in status_lower for kw in expired_keywords):
            return 'Expired'

    # Don't auto-expire - trust the source status
    return current_status

def check_and_migrate_schema(conn):
    cur = conn.cursor()
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        cur.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
        
        logging.info("⚙️  Verifying database schema...")
        
        # Ensure core tables
        cur.execute("""
            CREATE TABLE IF NOT EXISTS processed_files (
                filename VARCHAR(512) PRIMARY KEY,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR(20),
                record_count INT DEFAULT 0,
                error_log TEXT
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS nice_classes_lookup (
                class_number INTEGER PRIMARY KEY,
                name_tr VARCHAR(100),
                name_en VARCHAR(100),
                description TEXT,
                description_embedding halfvec(384),
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """)

        # Handle Enum
        try:
            cur.execute("""
                DO $$ BEGIN
                    CREATE TYPE tm_status AS ENUM (
                        'Applied', 'Published', 'Opposed', 'Registered', 
                        'Refused', 'Withdrawn', 'Transferred', 'Renewed', 
                        'Partial Refusal', 'Expired', 'Unknown'
                    );
                EXCEPTION
                    WHEN duplicate_object THEN null;
                END $$;
            """)
        except Exception:
            conn.rollback()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS trademarks (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                application_no VARCHAR(255) UNIQUE NOT NULL,
                name TEXT,
                current_status tm_status DEFAULT 'Published',
                last_event_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # [CRITICAL FIX] Handle index row size error for long trademark names
        logging.info("⚙️  Optimizing indices for long strings...")
        cur.execute("DROP INDEX IF EXISTS idx_tm_name;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tm_name_trgm ON trademarks USING GIST (name gist_trgm_ops);")

        # Add all columns required by the script
        cols_to_check = [
            ("availability_status", "VARCHAR(50)"),
            ("nice_class_numbers", "INTEGER[]"),
            ("vienna_class_numbers", "INTEGER[]"),
            ("extracted_goods", "JSONB"),
            ("registration_no", "VARCHAR(255)"),
            ("wipo_no", "VARCHAR(255)"),
            ("application_date", "DATE"),
            ("registration_date", "DATE"),
            ("bulletin_no", "VARCHAR(255)"),
            ("bulletin_date", "DATE"),
            ("gazette_no", "VARCHAR(255)"),
            ("gazette_date", "DATE"),
            ("appeal_deadline", "DATE"),
            ("expiry_date", "DATE"),
            ("image_path", "TEXT"),
            ("image_embedding", "halfvec(512)"),
            ("dinov2_embedding", "halfvec(768)"),
            ("text_embedding", "halfvec(384)"),
            ("color_histogram", "halfvec(512)"),
            ("logo_ocr_text", "TEXT"),
            ("name_tr", "VARCHAR(500)"),
            ("detected_lang", "VARCHAR(10)"),
            ("holder_name", "VARCHAR(500)"),
            ("holder_tpe_client_id", "VARCHAR(50)"),
            ("attorney_name", "VARCHAR(500)"),
            ("attorney_no", "VARCHAR(50)"),
            ("status_source", "VARCHAR(10)"),
        ]

        from psycopg2 import sql as psql
        # Whitelist of allowed column types for DDL safety
        ALLOWED_COL_TYPES = {"VARCHAR(500)", "VARCHAR(50)", "VARCHAR(10)", "TEXT", "INTEGER", "BOOLEAN", "TIMESTAMP"}
        for col_name, col_type in cols_to_check:
            if col_type not in ALLOWED_COL_TYPES:
                logging.warning(f"   -> Skipping unknown column type: {col_type}")
                continue
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='trademarks' AND column_name=%s;", (col_name,))
            if not cur.fetchone():
                logging.info(f"   -> Adding missing column: {col_name}...")
                cur.execute(psql.SQL("ALTER TABLE trademarks ADD COLUMN {} " + col_type + ";").format(
                    psql.Identifier(col_name)
                ))

        # Add indexes for holder and attorney lookups (after columns exist)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tm_holder_tpe_id ON trademarks(holder_tpe_client_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tm_holder_name ON trademarks(holder_name);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tm_attorney_name ON trademarks(attorney_name);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tm_attorney_no ON trademarks(attorney_no);")

        conn.commit()
        logging.info("✅ Schema verified.")
    except Exception as e:
        conn.rollback()
        logging.error(f"❌ Schema Check Failed: {e}")

def load_nice_classes(conn):
    """Load Nice Class definitions into database.

    Supports two JSON formats:
    1. nice_classes.json: {"1": "description", "2": "description", ...}
    2. nice_classes_with_embeddings.json: [{"CLASSNO": 1, "DESCRIPTION": "...", "CLASS_EMBEDDING": [...]}, ...]

    If pgvector is installed and embeddings are available, they will be loaded.
    """
    # Try both files - prefer with_embeddings for richer data
    classes_file = ROOT_DIR / "nice_classes_with_embeddings.json"
    if not classes_file.exists():
        classes_file = ROOT_DIR / "nice_classes.json"

    if not classes_file.exists():
        logging.warning(f"⚠️ No Nice Classes file found. Skipping class reference load.")
        return

    logging.info(f"📚 Loading Nice Class reference data from {classes_file.name}...")

    # Short human-readable names for each Nice class (used in UI dropdowns/badges)
    _CLASS_NAMES_TR = {
        1: "Kimyasallar", 2: "Boyalar", 3: "Kozmetikler", 4: "Yaglar ve Yakitlar",
        5: "Ilaclar", 6: "Metal Urunler", 7: "Makineler", 8: "El Aletleri",
        9: "Elektronik", 10: "Tibbi Cihazlar", 11: "Aydinlatma", 12: "Tasitlar",
        13: "Atesli Silahlar", 14: "Mucevherat", 15: "Muzik Aletleri",
        16: "Kagit Urunleri", 17: "Kaucuk", 18: "Deri Urunler",
        19: "Yapi Malzemeleri", 20: "Mobilya", 21: "Ev Esyalari", 22: "Halatlar",
        23: "Iplikler", 24: "Kumaslar", 25: "Giyim", 26: "Dantela",
        27: "Halilar", 28: "Oyuncaklar", 29: "Et Urunleri", 30: "Gida",
        31: "Tarim Urunleri", 32: "Bira/Alkolsuz Ic.", 33: "Alkolu Icecekler",
        34: "Tutun", 35: "Reklamcilik", 36: "Sigortacilik", 37: "Insaat",
        38: "Telekomun.", 39: "Tasimacilik", 40: "Malzeme Isleme",
        41: "Egitim", 42: "Yazilim/BT", 43: "Yiyecek/Icecek", 44: "Saglik",
        45: "Hukuk Hizmetleri", 99: "Global Marka (Tum Siniflar)",
    }
    _CLASS_NAMES_EN = {
        1: "Chemicals", 2: "Paints", 3: "Cosmetics", 4: "Oils & Fuels",
        5: "Pharmaceuticals", 6: "Metal Products", 7: "Machines", 8: "Hand Tools",
        9: "Electronics", 10: "Medical Devices", 11: "Lighting", 12: "Vehicles",
        13: "Firearms", 14: "Jewelry", 15: "Musical Instruments",
        16: "Paper Products", 17: "Rubber", 18: "Leather Goods",
        19: "Building Materials", 20: "Furniture", 21: "Household Items",
        22: "Ropes", 23: "Yarns", 24: "Fabrics", 25: "Clothing", 26: "Lace",
        27: "Carpets", 28: "Toys", 29: "Meat Products", 30: "Food",
        31: "Agricultural Products", 32: "Beer/Non-Alc. Bev.",
        33: "Alcoholic Beverages", 34: "Tobacco", 35: "Advertising",
        36: "Insurance", 37: "Construction", 38: "Telecom.",
        39: "Transportation", 40: "Material Processing", 41: "Education",
        42: "Software/IT", 43: "Food/Beverage", 44: "Healthcare",
        45: "Legal Services", 99: "Global Brand (All Classes)",
    }

    try:
        with open(classes_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Check if pgvector is available
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        pgvector_available = cur.fetchone() is not None

        insert_rows_with_emb = []
        insert_rows_no_emb = []

        # Handle different JSON structures
        if isinstance(data, list):
            # Format: [{"CLASSNO": 1, "DESCRIPTION": "...", "CLASS_EMBEDDING": [...]}, ...]
            for item in data:
                if not isinstance(item, dict):
                    continue
                c_num = item.get("CLASSNO")
                desc = item.get("DESCRIPTION")
                embedding = item.get("CLASS_EMBEDDING")
                if c_num and desc:
                    c_num = int(c_num)
                    name_tr = _CLASS_NAMES_TR.get(c_num)
                    name_en = _CLASS_NAMES_EN.get(c_num)
                    if pgvector_available and embedding:
                        emb_str = '[' + ','.join(map(str, embedding)) + ']'
                        insert_rows_with_emb.append((c_num, name_tr, name_en, desc, emb_str))
                    else:
                        insert_rows_no_emb.append((c_num, name_tr, name_en, desc))
        elif isinstance(data, dict):
            # Format: {"1": "description", ...} or {"1": {"CLASSNO": 1, ...}, ...}
            for key, value in data.items():
                if isinstance(value, str):
                    try:
                        c_num = int(key)
                        name_tr = _CLASS_NAMES_TR.get(c_num)
                        name_en = _CLASS_NAMES_EN.get(c_num)
                        insert_rows_no_emb.append((c_num, name_tr, name_en, value))
                    except ValueError:
                        continue
                elif isinstance(value, dict):
                    c_num = value.get("CLASSNO")
                    desc = value.get("DESCRIPTION")
                    embedding = value.get("CLASS_EMBEDDING")
                    if c_num and desc:
                        c_num = int(c_num)
                        name_tr = _CLASS_NAMES_TR.get(c_num)
                        name_en = _CLASS_NAMES_EN.get(c_num)
                        if pgvector_available and embedding:
                            emb_str = '[' + ','.join(map(str, embedding)) + ']'
                            insert_rows_with_emb.append((c_num, name_tr, name_en, desc, emb_str))
                        else:
                            insert_rows_no_emb.append((c_num, name_tr, name_en, desc))
        else:
            logging.error("   ❌ Invalid JSON structure. Expected List or Dict.")
            return

        total_rows = len(insert_rows_with_emb) + len(insert_rows_no_emb)
        if total_rows == 0:
            logging.warning("   ⚠️ No valid class definitions found.")
            return

        # Insert rows WITH embeddings
        if insert_rows_with_emb:
            insert_sql = """
                INSERT INTO nice_classes_lookup (class_number, name_tr, name_en, description, description_embedding)
                VALUES %s
                ON CONFLICT (class_number) DO UPDATE SET
                    name_tr = EXCLUDED.name_tr,
                    name_en = EXCLUDED.name_en,
                    description = EXCLUDED.description,
                    description_embedding = EXCLUDED.description_embedding,
                    updated_at = NOW();
            """
            execute_values(cur, insert_sql, insert_rows_with_emb)
            logging.info(f"   ✅ Loaded {len(insert_rows_with_emb)} Nice Classes WITH embeddings.")

        # Insert rows WITHOUT embeddings
        if insert_rows_no_emb:
            insert_sql = """
                INSERT INTO nice_classes_lookup (class_number, name_tr, name_en, description)
                VALUES %s
                ON CONFLICT (class_number) DO UPDATE SET
                    name_tr = EXCLUDED.name_tr,
                    name_en = EXCLUDED.name_en,
                    description = EXCLUDED.description,
                    updated_at = NOW();
            """
            execute_values(cur, insert_sql, insert_rows_no_emb)
            logging.info(f"   ✅ Loaded {len(insert_rows_no_emb)} Nice Classes without embeddings.")

        conn.commit()
        logging.info(f"   ✅ Total: {total_rows} Nice Class definitions loaded.")
    except Exception as e:
        conn.rollback()
        logging.error(f"   ❌ Failed to load class references: {e}")

# === BATCH PROCESSING LOGIC ===

def process_file_batch(conn, file_path, force=False):
    cur = conn.cursor()
    filename = file_path.name
    folder_name = file_path.parent.name
    logging.info(f"Processing Batch: {folder_name}/{filename}")

    is_app_source = folder_name.upper().startswith("APP_") or "scraped" in folder_name.lower()
    is_bulletin_source = folder_name.upper().startswith("BLT_") or "BULTEN" in folder_name.upper()
    is_gazette_source = folder_name.upper().startswith("GZ_") or "GAZETE" in folder_name.upper()
    new_source_rank, source_tag = get_source_rank(folder_name)

    # Extract gazette number and date from GZ_ folder names
    # GZ metadata doesn't contain GAZETTE_NO/GAZETTE_DATE keys — derive from folder name
    # Formats: GZ_300, GZ_449_2017-09-30
    folder_gazette_no = None
    folder_gazette_date = None
    if is_gazette_source:
        parts = folder_name.split('_')
        if len(parts) >= 2:
            folder_gazette_no = parts[1]
        if len(parts) >= 3:
            folder_gazette_date = parse_date(parts[2])

    if not force and not is_app_source:
        cur.execute("SELECT status FROM processed_files WHERE filename = %s", (f"{folder_name}/{filename}",))
        row = cur.fetchone()
        if row and row[0] in ('success', 'repaired'):
            logging.info("   -> Skipped (Already processed).")
            return

    cur.execute("""
        INSERT INTO processed_files (filename, status, processed_at) 
        VALUES (%s, 'processing', NOW()) 
        ON CONFLICT (filename) DO UPDATE SET status = 'processing', processed_at = NOW();
    """, (f"{folder_name}/{filename}",))

    was_repaired = False
    try:
        # Load metadata.json — with self-healing safety net for corrupt files.
        # The pre_scan_and_repair() pass should have caught corrupt files already;
        # this is a safety net for anything it missed (e.g. concurrent corruption).
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as jde:
            file_size = file_path.stat().st_size
            logging.warning(
                f"   SAFETY NET: Corrupt {folder_name}/metadata.json "
                f"(size={file_size:,}B, error pos {jde.pos}: {jde.msg})"
            )
            repair = _repair_corrupt_metadata(file_path)
            if repair["status"] == "repaired":
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                was_repaired = True
                logging.info(f"   Retrying ingestion with {len(data)} repaired records")
            else:
                cur.execute(
                    "UPDATE processed_files SET status = %s, error_log = %s WHERE filename = %s",
                    (repair["status"], repair.get("error", ""), f"{folder_name}/{filename}")
                )
                conn.commit()
                return

        if not data: return

        # PREFETCH
        app_map = {rec.get("APPLICATIONNO"): rec for rec in data if rec.get("APPLICATIONNO")}
        all_app_nos = list(app_map.keys())
        
        existing_db_records = {}
        if all_app_nos:
            cur.execute("""
                SELECT application_no, id, last_event_date, current_status, expiry_date, status_source
                FROM trademarks WHERE application_no = ANY(%s)
            """, (all_app_nos,))
            for row in cur.fetchall():
                existing_db_records[row[0]] = {"id": row[1], "last_date": row[2], "status": row[3], "expiry": row[4], "status_source": row[5]}

        new_inserts = []
        updates = []
        history_inserts = []
        skipped_count = 0

        for rec in data:
            app_no = rec.get("APPLICATIONNO")
            if not app_no: continue

            tm = rec.get("TRADEMARK", {})
            tm_name = clean_name(tm.get("NAME", ""))

            # [SANITY CHECK] Skip corrupted/extremely long names
            if tm_name and len(tm_name) > 2000:
                logging.warning(f"   ⚠️ Skipping record {app_no}: Name is too long ({len(tm_name)} chars). Likely corrupted.")
                skipped_count += 1
                continue

            status_raw = rec.get("STATUS", "")
            reg_no_val = tm.get("REGISTERNO") # Extract Registration Number
            
            # Updated Status Logic
            db_status = determine_status(folder_name, status_raw, reg_no_val)
            
            # Dates
            app_date = parse_date(tm.get("APPLICATIONDATE"))
            reg_date = parse_date(tm.get("REGISTERDATE"))
            bulletin_date_val = parse_date(tm.get("BULLETIN_DATE"))
            gazette_date_val = folder_gazette_date if is_gazette_source else parse_date(tm.get("GAZETTE_DATE"))
            
            comparison_date = None
            if is_bulletin_source and bulletin_date_val: comparison_date = bulletin_date_val
            elif is_gazette_source and gazette_date_val: comparison_date = gazette_date_val
            elif db_status == 'Registered' and reg_date: comparison_date = reg_date
            elif db_status == 'Published' and bulletin_date_val: comparison_date = bulletin_date_val
            elif app_date: comparison_date = app_date
            
            db_write_date = comparison_date or datetime.now().date()
            db_status = calculate_expiration_status(db_status, comparison_date or app_date, status_raw)
            
            new_expiry_date = None
            if app_date:
                try: new_expiry_date = app_date.replace(year=app_date.year + 10)
                except ValueError: new_expiry_date = app_date + timedelta(days=3652)
            
            # Appeal Deadline (2 calendar months from bulletin publication)
            appeal_dl = None
            if bulletin_date_val and (db_status == 'Published' or is_bulletin_source):
                appeal_dl = calculate_appeal_deadline(bulletin_date_val)

            # AI Vectors - convert to halfvec string format for PostgreSQL
            img_emb = embedding_to_halfvec(rec.get("image_embedding"), expected_dim=512)
            dino_emb = embedding_to_halfvec(rec.get("dinov2_embedding"), expected_dim=768)
            txt_emb = embedding_to_halfvec(rec.get("text_embedding"), expected_dim=384)
            color_emb = embedding_to_halfvec(rec.get("color_histogram"), expected_dim=512)
            img_path = _resolve_image_path(folder_name, rec.get("IMAGE"), ROOT_DIR)
            ocr_text = rec.get("logo_ocr_text")

            # Translation (VARCHAR(500) — truncate corrupted parser bleed)
            name_tr = _trunc(rec.get("name_tr"), 500)
            detected_lang = _trunc(rec.get("detected_lang"), 10)

            # Extract holder info from HOLDERS array (VARCHAR-bounded — truncate parser bleed)
            holders_list = rec.get("HOLDERS", [])
            holder_name = None
            holder_tpe_client_id = None
            if holders_list and len(holders_list) > 0:
                first_holder = holders_list[0]
                raw_title = first_holder.get("TITLE", "")
                existing_tpe = first_holder.get("TPECLIENTID", "")
                holder_clean, extracted_id = extract_tpe_id(raw_title)
                holder_name = _trunc(holder_clean, 500)
                holder_tpe_client_id = _trunc(existing_tpe or extracted_id, 50)

            # Extract attorney info from ATTORNEYS array
            # ATTORNEYS uses NAME/NO fields (not TITLE/TPECLIENTID like HOLDERS)
            attorneys_list = rec.get("ATTORNEYS", [])
            attorney_name = None
            attorney_no = None
            if attorneys_list and len(attorneys_list) > 0:
                first_attorney = attorneys_list[0]
                raw_name = first_attorney.get("NAME", "")
                existing_no = first_attorney.get("NO", "")
                atty_clean, extracted_id = extract_tpe_id(raw_name)
                attorney_name = _trunc(atty_clean, 500)
                attorney_no = _trunc(existing_no or extracted_id, 50)

            # Registration / WIPO numbers (VARCHAR(255) columns)
            reg_no = _trunc(reg_no_val, 255)
            wipo_no = _trunc(tm.get("INTREGNO"), 255)

            # Classes
            raw_classes = tm.get("NICECLASSES_LIST", [])
            clean_classes_list = [int(c) for c in raw_classes if str(c).strip().isdigit()]
            raw_vienna = tm.get("VIENNACLASSES_LIST", [])
            vienna_classes = [int(c) for c in raw_vienna if str(c).strip().isdigit()]

            # EXTRACTEDGOODS = goods REMOVED due to conflicts (not the same as GOODS).
            # Store NULL when absent/empty — never fall back to GOODS.
            raw_extracted = rec.get("EXTRACTEDGOODS")
            extracted_goods_data = raw_extracted if raw_extracted else None

            existing = existing_db_records.get(app_no)
            
            if not existing:
                # INSERT - handle bulletin data based on source
                if is_gazette_source:
                    # GZ_ source: Don't trust BULLETIN_NO from metadata (it's copied from gazette)
                    insert_bulletin_no = None
                    insert_bulletin_date = None
                elif is_app_source:
                    # APP_ source: Doesn't own bulletin fields
                    insert_bulletin_no = None
                    insert_bulletin_date = None
                else:
                    # BLT_ source: Owns bulletin fields
                    insert_bulletin_no = tm.get("BULLETIN_NO")
                    insert_bulletin_date = bulletin_date_val

                new_inserts.append((
                    app_no, sanitize(tm_name), db_status,
                    clean_classes_list or None,
                    Json(extracted_goods_data) if extracted_goods_data else None,
                    reg_no, wipo_no, vienna_classes or None,
                    app_date, reg_date, db_write_date,
                    sanitize(insert_bulletin_no), insert_bulletin_date,
                    sanitize(folder_gazette_no if is_gazette_source else tm.get("GAZETTE_NO")), gazette_date_val,
                    appeal_dl, new_expiry_date, img_path,
                    img_emb, dino_emb, txt_emb, color_emb,
                    sanitize(ocr_text),
                    name_tr, detected_lang,
                    holder_name, holder_tpe_client_id,
                    attorney_name, attorney_no,
                    source_tag
                ))
            else:
                curr_status = existing['status']
                existing_source = existing.get('status_source') or 'BLT'
                old_source_rank = {'APP': 3, 'GZ': 2}.get(existing_source, 1)

                # === DECISION LOGIC FOR UPDATES (source authority hierarchy) ===
                should_update = False
                final_status = db_status  # Default to new status

                if new_source_rank >= old_source_rank:
                    # Higher or equal authority → accept update
                    should_update = True
                    # APP_ still keeps existing strong statuses when new is just 'Applied'
                    if is_app_source and db_status == 'Applied':
                        strong_statuses = ['Registered', 'Refused', 'Opposed', 'Withdrawn', 'Expired', 'Partial Refusal', 'Renewed']
                        if curr_status in strong_statuses:
                            final_status = curr_status
                else:
                    # Lower authority → skip entirely
                    skipped_count += 1
                    continue

                # Force override
                if force:
                    should_update = True

                if should_update:
                    is_renewal = False
                    if curr_status in ['Registered', 'Renewed', 'Expired'] and final_status == 'Registered':
                         if existing['expiry'] and new_expiry_date and new_expiry_date > existing['expiry']:
                             final_status = 'Renewed'
                             is_renewal = True

                    # Prepare for bulk UPDATE join — sanitize all fields
                    updates.append((
                        sanitize(tm_name),
                        final_status,
                        clean_classes_list or None,
                        Json(extracted_goods_data) if extracted_goods_data else None,
                        db_write_date, appeal_dl, new_expiry_date,
                        sanitize(tm.get("BULLETIN_NO")), bulletin_date_val,
                        sanitize(folder_gazette_no if is_gazette_source else tm.get("GAZETTE_NO")), gazette_date_val,
                        img_path,
                        app_date, reg_date,
                        img_emb, dino_emb, txt_emb, color_emb,
                        sanitize(ocr_text),
                        name_tr, detected_lang,
                        holder_name, holder_tpe_client_id,
                        attorney_name, attorney_no,
                        source_tag,
                        reg_no, wipo_no, vienna_classes or None,
                        app_no  # Key - MUST be last
                    ))

                    if curr_status != final_status or is_renewal:
                        history_inserts.append((existing['id'], db_write_date, "STATUS_CHANGE" if not is_renewal else "RENEWAL", filename, f"{curr_status} -> {final_status}"))
                else:
                    skipped_count += 1

        # EXECUTE BATCH WRITES
        # Deduplicate: some metadata files have duplicate application_no values.
        # Keep the LAST occurrence (later records may have updated data).
        if new_inserts:
            seen_app_nos = {}
            for i, row in enumerate(new_inserts):
                seen_app_nos[row[0]] = i  # row[0] = application_no
            if len(seen_app_nos) < len(new_inserts):
                dups = len(new_inserts) - len(seen_app_nos)
                logging.warning(f"   ⚠️ Removed {dups} duplicate application_no(s) within batch")
                new_inserts = [new_inserts[i] for i in sorted(seen_app_nos.values())]
            logging.info(f"   -> Batch Inserting {len(new_inserts)} new records...")
            insert_sql = """
                INSERT INTO trademarks (
                    application_no, name, current_status, nice_class_numbers, extracted_goods,
                    registration_no, wipo_no, vienna_class_numbers,
                    application_date, registration_date, last_event_date,
                    bulletin_no, bulletin_date, gazette_no, gazette_date,
                    appeal_deadline, expiry_date, image_path,
                    image_embedding, dinov2_embedding, text_embedding, color_histogram,
                    logo_ocr_text,
                    name_tr, detected_lang,
                    holder_name, holder_tpe_client_id,
                    attorney_name, attorney_no,
                    status_source
                ) VALUES %s
                ON CONFLICT (application_no) DO UPDATE SET
                    registration_no = COALESCE(EXCLUDED.registration_no, trademarks.registration_no),
                    wipo_no = COALESCE(EXCLUDED.wipo_no, trademarks.wipo_no),
                    vienna_class_numbers = COALESCE(EXCLUDED.vienna_class_numbers, trademarks.vienna_class_numbers),
                    extracted_goods = COALESCE(EXCLUDED.extracted_goods, trademarks.extracted_goods),
                    appeal_deadline = COALESCE(EXCLUDED.appeal_deadline, trademarks.appeal_deadline),
                    image_embedding = COALESCE(EXCLUDED.image_embedding, trademarks.image_embedding),
                    dinov2_embedding = COALESCE(EXCLUDED.dinov2_embedding, trademarks.dinov2_embedding),
                    text_embedding = COALESCE(EXCLUDED.text_embedding, trademarks.text_embedding),
                    color_histogram = COALESCE(EXCLUDED.color_histogram, trademarks.color_histogram),
                    logo_ocr_text = COALESCE(EXCLUDED.logo_ocr_text, trademarks.logo_ocr_text),
                    name_tr = COALESCE(EXCLUDED.name_tr, trademarks.name_tr),
                    detected_lang = COALESCE(EXCLUDED.detected_lang, trademarks.detected_lang),
                    holder_name = COALESCE(EXCLUDED.holder_name, trademarks.holder_name),
                    holder_tpe_client_id = COALESCE(EXCLUDED.holder_tpe_client_id, trademarks.holder_tpe_client_id),
                    attorney_name = COALESCE(EXCLUDED.attorney_name, trademarks.attorney_name),
                    attorney_no = COALESCE(EXCLUDED.attorney_no, trademarks.attorney_no),
                    updated_at = NOW()
            """
            execute_values(cur, insert_sql, new_inserts)

        if updates:
            # Deduplicate updates by app_no (last element in tuple)
            seen_upd = {}
            for i, row in enumerate(updates):
                seen_upd[row[-1]] = i  # row[-1] = app_no (last element)
            if len(seen_upd) < len(updates):
                dups = len(updates) - len(seen_upd)
                logging.warning(f"   ⚠️ Removed {dups} duplicate update(s) within batch")
                updates = [updates[i] for i in sorted(seen_upd.values())]
            logging.info(f"   -> Batch Updating {len(updates)} existing records...")

            # Source-aware UPDATE: priority rules + field ownership
            if is_app_source:
                update_sql = _build_update_sql('APP')
            elif is_gazette_source:
                update_sql = _build_update_sql('GZ')
            else:
                update_sql = _build_update_sql('BLT')

            execute_values(cur, update_sql, updates)

        if history_inserts:
            try:
                cur.execute("SAVEPOINT before_history")
                hist_sql = """
                    INSERT INTO trademark_history (trademark_id, event_date, event_type, source_file, description)
                    VALUES %s ON CONFLICT DO NOTHING
                """
                execute_values(cur, hist_sql, history_inserts)
                cur.execute("RELEASE SAVEPOINT before_history")
            except Exception as hist_err:
                # History write failure must not roll back the main trademarks batch
                cur.execute("ROLLBACK TO SAVEPOINT before_history")
                logging.warning(f"   ⚠️ History insert skipped (partition missing?): {hist_err}")

        final_status = 'repaired' if was_repaired else 'success'
        cur.execute("UPDATE processed_files SET status = %s, record_count = %s WHERE filename = %s",
                   (final_status, len(new_inserts) + len(updates), f"{folder_name}/{filename}"))
        conn.commit()
        logging.info(f"   ✅ Batch Complete. {len(new_inserts)} Ins, {len(updates)} Upd, {skipped_count} Skip.")

        # Trigger watchlist scan for new trademarks
        if new_inserts:
            # Get the IDs of newly inserted trademarks (shared by both hooks below)
            cur.execute("""
                SELECT id FROM trademarks WHERE application_no = ANY(%s)
            """, ([ins[0] for ins in new_inserts],))
            new_trademark_ids = [row[0] for row in cur.fetchall()]

            # Hook 1: Watchlist scan
            if new_trademark_ids:
                try:
                    from watchlist.scanner import trigger_watchlist_scan
                    source_type = 'bulletin' if is_bulletin_source else ('gazette' if is_gazette_source else 'application')
                    trigger_watchlist_scan(new_trademark_ids, source_type, folder_name)
                    logging.info(f"   Watchlist scan triggered for {len(new_trademark_ids)} new trademarks")
                except Exception as e:
                    logging.warning(f"   Watchlist scan skipped: {e}")

            # Hook 2: Queue for Opposition Radar (universal conflict scanning)
            if new_trademark_ids and is_bulletin_source:
                queue_bulletin_no, queue_bulletin_date = extract_bulletin_info(folder_name)
                add_to_scan_queue(
                    conn=conn,
                    trademark_ids=new_trademark_ids,
                    bulletin_no=queue_bulletin_no,
                    bulletin_date=queue_bulletin_date,
                    priority=1
                )

    except Exception as e:
        conn.rollback()
        logging.error(f"   ❌ Batch Failed: {e}")
        cur.execute("UPDATE processed_files SET status = 'failed', error_log = %s WHERE filename = %s", (str(e), f"{folder_name}/{filename}"))
        conn.commit()

def run_ingest(force=False, settings=None) -> dict:
    """
    Callable entry point for pipeline integration.

    Args:
        force: Force re-processing of all files.
        settings: Optional PipelineSettings override for root_dir.

    Returns:
        { "inserted": N, "updated": N, "skipped": N, "duration_seconds": N, "repair_stats": dict }
    """
    global ROOT_DIR

    if settings is not None:
        ROOT_DIR = Path(settings.bulletins_root)

    t0 = time.time()
    conn = None
    inserted = 0
    updated = 0
    skipped = 0
    repair_stats = {'repaired': [], 'unrecoverable': [], 'regen_failed': []}

    try:
        conn = get_connection()
        check_and_migrate_schema(conn)
        load_nice_classes(conn)

        # Pre-scan: detect and repair corrupt metadata.json files before ingestion
        repair_stats = pre_scan_and_repair(ROOT_DIR)

        metadata_files = list(ROOT_DIR.rglob("metadata.json"))
        logging.info(f"Found {len(metadata_files)} files.")

        def sort_key(p):
            name = p.parent.name.upper()
            m = _re.search(r'_(\d+)', p.parent.name)
            num = int(m.group(1)) if m else 0
            if name.startswith("BLT"): return (0, -num)
            if name.startswith("GZ"):  return (1, -num)
            return (2, -num)  # APP last (highest authority overwrites)
        metadata_files.sort(key=sort_key)

        for json_file in metadata_files:
            process_file_batch(conn, json_file, force)

    except Exception as e:
        logging.error(f"Ingestion failed: {e}")
        raise
    finally:
        if conn:
            release_connection(conn)

    duration = time.time() - t0
    _print_repair_summary(repair_stats)
    logging.info(f"Ingestion complete in {duration:.1f}s")

    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "duration_seconds": round(duration, 1),
        "repair_stats": repair_stats,
    }


def main():
    parser = argparse.ArgumentParser(description="Ingest trademark data (10M Scale).")
    parser.add_argument("--force", action="store_true", help="Force re-processing.")
    parser.add_argument("--folder", type=str, help="Process only this folder name (e.g. GZ_300).")
    args = parser.parse_args()

    conn = None
    repair_stats = {'repaired': [], 'unrecoverable': [], 'regen_failed': []}
    try:
        conn = get_connection()
        check_and_migrate_schema(conn)
        load_nice_classes(conn)

        if args.folder:
            # Single-folder mode
            folder_path = ROOT_DIR / args.folder / "metadata.json"
            if not folder_path.exists():
                logging.error(f"metadata.json not found: {folder_path}")
                sys.exit(1)
            metadata_files = [folder_path]
            logging.info(f"Single-folder mode: {args.folder}")
        else:
            # Pre-scan: detect and repair corrupt metadata.json files before ingestion
            repair_stats = pre_scan_and_repair(ROOT_DIR)

            metadata_files = list(ROOT_DIR.rglob("metadata.json"))
            logging.info(f"Found {len(metadata_files)} files.")

        def sort_key(p):
            name = p.parent.name.upper()
            m = _re.search(r'_(\d+)', p.parent.name)
            num = int(m.group(1)) if m else 0
            if name.startswith("BLT"): return (0, -num)
            if name.startswith("GZ"):  return (1, -num)
            return (2, -num)  # APP last (highest authority overwrites)
        metadata_files.sort(key=sort_key)

        for json_file in metadata_files:
            process_file_batch(conn, json_file, args.force)

    except Exception as e:
        logging.error(f"Ingestion failed: {e}")
        raise
    finally:
        # Always return connection to pool
        if conn:
            release_connection(conn)
        _print_repair_summary(repair_stats)
        logging.info("Database connection returned to pool.")


if __name__ == "__main__":
    try:
        main()
    finally:
        # Ensure pool is closed on exit
        close_pool()
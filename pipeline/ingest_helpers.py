import json
import os
import sys
import time
import shutil
import argparse
from psycopg2.extras import Json, execute_values
from pathlib import Path
from datetime import datetime, timedelta
import logging
from dotenv import load_dotenv

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"
_PRE_DOTENV_PIPELINE_BULLETINS_ROOT = os.environ.get("PIPELINE_BULLETINS_ROOT")
_PRE_DOTENV_DATA_ROOT = os.environ.get("DATA_ROOT")

if str(_LOCAL_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_LOCAL_PROJECT_ROOT))

from utils.deadline import calculate_appeal_deadline


def _resolve_local_ingest_root(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


# Load environment variables from .env file
load_dotenv()

import re as _re

# ===================== DATABASE CONNECTION POOL =====================
from db.pool import (
    get_connection,
    release_connection,
    close_pool
)
from pipeline import ingest_bootstrap as _bootstrap
from pipeline import ingest_rules as _rules

# ===================== CONFIG =====================
ROOT_DIR = _bootstrap.default_ingest_root()
CLASSES_FILE = ROOT_DIR / "nice_classes_with_embeddings.json"

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_db_connection():
    """
    Get a database connection from the pool.
    """
    return get_connection()

# ============================================
# UNIVERSAL SCAN QUEUE INTEGRATION
# ============================================

def extract_bulletin_info(folder_name: str):
    """
    Extract bulletin number and date from folder name.
    Safely handles both BLT_327_2019-06-27 and BLT_2025_03 formats.
    """
    # 1. Extract the primary number (e.g., 327 from BLT_327)
    no_match = _re.search(r'(?:BLT|BULTEN|GZ|GAZETE)[_-]?(\d+)', folder_name, _re.IGNORECASE)
    bulletin_no = no_match.group(1) if no_match else None

    # 2. Extract the date if present (e.g., 2019-06-27 or 2025_03)
    date_match = _re.search(r'(\d{4}[_-]\d{2}[_-]\d{2}|\d{4}[_-]\d{2})', folder_name)
    bulletin_date = None
    if date_match:
        d_str = date_match.group(1).replace('_', '-')
        try:
            if len(d_str) == 7:  # YYYY-MM
                bulletin_date = datetime.strptime(d_str, "%Y-%m").date()
            else:  # YYYY-MM-DD
                bulletin_date = datetime.strptime(d_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    return bulletin_no, bulletin_date


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
    if not trademark_ids or not _check_scan_queue_table(conn):
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
                logging.info(f"   Queued {queued_count} trademarks for Radar scan")
            return queued_count
    except Exception as e:
        logging.warning(f"   Failed to queue for Radar scan: {e}")
        return 0


def parse_date(date_str):
    if not date_str: return None
    date_str = str(date_str).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def calculate_expiration_status(application_date):
    """
    Calculate the trademark expiry date from the application date.

    The ingest pipeline treats expiry as 10 years plus the standard 6-month
    renewal window already embedded in process_file_batch.
    """
    if isinstance(application_date, datetime):
        application_date = application_date.date()
    elif isinstance(application_date, str):
        application_date = parse_date(application_date)

    if not application_date:
        return None

    try:
        ten_year_date = application_date.replace(year=application_date.year + 10)
    except ValueError:
        ten_year_date = application_date + timedelta(days=3652)

    return ten_year_date + timedelta(days=183)

def embedding_to_halfvec(embedding, expected_dim=None):
    if embedding is None or not isinstance(embedding, (list, tuple)) or len(embedding) == 0:
        return None
    if expected_dim is not None and len(embedding) != expected_dim:
        return None
    try:
        return '[' + ','.join(str(float(v)) for v in embedding) + ']'
    except (TypeError, ValueError):
        return None

def _determine_db_status_raw(folder_name, status_raw, reg_no_val=None):
    folder_upper = folder_name.upper()
    status_text = _repair_mojibake(str(status_raw)) if status_raw else ""
    status_lower = status_text.lower().replace('\u0307', '').strip()
    has_reg_no = reg_no_val and str(reg_no_val).strip() and str(reg_no_val).strip().lower() not in ('null', 'none', '')

    if status_lower:
        refused_keywords = ['geçersiz', 'gecersiz', 'marka başvurusu/tescili geçersiz', 'başvuru geçersiz', 'basvuru gecersiz', 'tescil geçersiz', 'tescil gecersiz', 'reddedildi', 'red edildi', 'ret kararı', 'red kararı', 'refused', 'rejected']
        if 'başvuru geçersiz' in status_lower or 'marka başvurusu/tescili geçersiz' in status_lower or 'tescil geçersiz' in status_lower or 'ret kararı' in status_lower:
            return 'Reddedildi'
        if any(kw in status_lower for kw in refused_keywords): return 'Reddedildi'

        withdrawn_keywords = ['feragat edildi', 'feragat', 'geri çekildi', 'geri cekildi', 'geri alındı', 'geri alindi', 'vazgeçildi', 'vazgecildi', 'withdrawn']
        if any(kw in status_lower for kw in withdrawn_keywords): return 'Geri Çekildi'

        cancelled_keywords = ['iptal edildi', 'mahkeme kararı', 'mahkeme karari', 'cancelled', 'canceled']
        if any(kw in status_lower for kw in cancelled_keywords): return 'İptal Edildi'

        registered_keywords = ['tescil edildi', 'tescilli', 'kabul edildi', 'registered']
        if any(kw in status_lower for kw in registered_keywords): return 'Tescil Edildi'

        if 'itiraz' in status_lower or 'opposed' in status_lower: return 'İtiraz Edildi'

        expired_keywords = ['sona erdi', 'süresi doldu', 'suresi doldu', 'hükümsüz', 'hukumsuz', 'expired', 'yürürlükten', 'yururlukten']
        if any(kw in status_lower for kw in expired_keywords): return 'Süresi Doldu'

        published_keywords = ['yayınlandı', 'yayinlandi', 'ilan edildi', 'published']
        if any(kw in status_lower for kw in published_keywords): return 'Yayında'

        if 'renewed' in status_lower or 'yenilendi' in status_lower: return 'Yenilendi'

    if has_reg_no: return 'Tescil Edildi'
    if folder_upper.startswith("GZ_") or "GAZETE" in folder_upper: return 'Tescil Edildi'
    if folder_upper.startswith("BLT_") or "BULTEN" in folder_upper: return 'Yayında'

    return 'Başvuruldu'

def get_status_rank(status):
    ranks = {
        'Yenilendi': 4, 'Tescil Edildi': 3, 'Devredildi': 3,
        'Süresi Doldu': 2, 'İtiraz Edildi': 2, 'Reddedildi': 2, 'Geri Çekildi': 2, 'İptal Edildi': 2,
        'Yayında': 1, 'Kısmi Red': 1, 'Başvuruldu': 0
    }
    return ranks.get(status, -1)

def get_source_rank(folder_name):
    fu = folder_name.upper()
    if _rules.is_app_source_folder(folder_name): return 3, 'APP'
    if fu.startswith("GZ_") or "GAZETE" in fu: return 2, 'GZ'
    return 1, 'BLT'

_SHARED_FIELDS = [
    ('name',                  'v.name'),
    ('name_tr',               'v.name_tr'),
    ('detected_lang',         'v.detected_lang'),
    ('holder_name',           'v.holder_name'),
    ('holder_tpe_client_id',  'v.holder_tpe_client_id'),
    ('attorney_name',         'v.attorney_name'),
    ('attorney_no',           'v.attorney_no'),
    ('extracted_goods',       'v.goods::jsonb'),
    ('application_date',      'v.app_date::date'),
    ('last_event_date',       'v.last_date::date'),
    ('expiry_date',           'v.expiry::date'),
    ('image_path',            'v.img_path'),
    ('image_embedding',       'v.img_emb::halfvec(512)'),
    ('dinov2_embedding',      'v.dino_emb::halfvec(768)'),
    ('color_histogram',       'v.color_emb::halfvec(512)'),
    ('logo_ocr_text',         'v.ocr_text'),
]

_SUSPICIOUS_SIX_FIELDS = [
    ('nice_class_numbers',    'v.nice_classes::integer[]'),
    ('vienna_class_numbers',  'v.vienna_classes::integer[]'),
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
    tc = f"tm.{col}"
    if source == 'APP': return f"{col} = COALESCE({val}, {tc})"
    if source == 'GZ': return f"{col} = CASE WHEN COALESCE(tm.status_source, '') = 'APP' THEN COALESCE({tc}, {val}) ELSE COALESCE({val}, {tc}) END"
    return f"{col} = CASE WHEN COALESCE(tm.status_source, '') IN ('APP', 'GZ') THEN COALESCE({tc}, {val}) ELSE COALESCE({val}, {tc}) END"

def _suspicious_six_coalesce(col, val, source):
    tc = f"tm.{col}"
    if source == 'APP': priority_logic = f"COALESCE({val}, {tc})"
    elif source == 'GZ': priority_logic = f"CASE WHEN COALESCE(tm.status_source, '') = 'APP' THEN COALESCE({tc}, {val}) ELSE COALESCE({val}, {tc}) END"
    else: priority_logic = f"CASE WHEN COALESCE(tm.status_source, '') IN ('APP', 'GZ') THEN COALESCE({tc}, {val}) ELSE COALESCE({val}, {tc}) END"
    return f"{col} = CASE WHEN COALESCE(cardinality({val}), 0) = 6 AND COALESCE(cardinality({tc}), 0) > 6 THEN {tc} ELSE {priority_logic} END"

def _owned_field(col, val, owner, source):
    tc = f"tm.{col}"
    if source == owner: return f"{col} = COALESCE({val}, {tc})"
    return f"{col} = {tc}"

def _build_update_set(source):
    parts = []
    for col, val in _SHARED_FIELDS: parts.append(_priority_coalesce(col, val, source))
    for col, val in _SUSPICIOUS_SIX_FIELDS: parts.append(_suspicious_six_coalesce(col, val, source))
    for col, val in _BLT_OWNED_FIELDS: parts.append(_owned_field(col, val, 'BLT', source))
    for col, val in _GZ_OWNED_FIELDS: parts.append(_owned_field(col, val, 'GZ', source))

    # Ingest owns only current_status + status_source.
    # final_status* fields are reconciler-owned and updated separately.
    parts.append("current_status = v.status::tm_status")
    parts.append("status_source = v.src_tag")
    parts.append("updated_at = NOW()")
    return ',\n                    '.join(parts)

def _build_update_sql(source):
    return f"""
                UPDATE trademarks AS tm
                SET
                    {_build_update_set(source)}
                FROM (VALUES %s) AS v(
                    name, status, nice_classes, goods, last_date, appeal, expiry,
                    b_no, b_date, g_no, g_date, img_path,
                    app_date, reg_date, img_emb, dino_emb, color_emb,
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
    return _rules.clean_name(raw_name)

def sanitize(val):
    if val is None: return None
    if isinstance(val, str):
        stripped = val.replace("\x00", "").strip()
        if stripped == "" or stripped.lower() in ("null", "none", "n/a", "-"): return None
        return stripped
    if isinstance(val, list) and len(val) == 0: return None
    if isinstance(val, dict) and len(val) == 0: return None
    return val

_TRANSLATION_SOURCE_NAME_KEY = "name_tr_source_name"


def _raw_name_was_materially_cleaned(raw_name, cleaned_name) -> bool:
    raw_text = sanitize(_rules._repair_mojibake(str(raw_name))) if raw_name is not None else None
    if not raw_text:
        return False
    return (cleaned_name or None) != " ".join(str(raw_text).split())


def _metadata_text_features_are_from_clean_name(rec: dict, cleaned_name: str | None) -> bool:
    if not cleaned_name:
        return False
    return rec.get(_TRANSLATION_SOURCE_NAME_KEY) == cleaned_name


def _trunc(val, max_len):
    s = sanitize(val)
    if s is None: return None
    s = str(s)
    return s[:max_len] if len(s) > max_len else s

def extract_tpe_id(name_str):
    if not name_str or not isinstance(name_str, str): return name_str, None
    trimmed = name_str.strip()
    id_match = _re.search(r'\s*\((\d+)\)', trimmed)
    if id_match:
        clean_name = trimmed[:id_match.start()].strip()
        tpe_id = id_match.group(1)
        return clean_name, tpe_id
    return trimmed, None

_file_index: dict[str, dict[str, str]] = {}

def _build_file_index(dir_path: Path, dir_key: str) -> dict[str, str]:
    index = {}
    if dir_path.is_dir():
        for f in dir_path.iterdir():
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                index[f.stem] = f.name
    _file_index[dir_key] = index
    return index

def determine_status(folder_name, status_raw, reg_no_val=None):
    status_aliases = {
        DB_STATUS_RENEWED: 'Renewed',
        DB_STATUS_REGISTERED: 'Registered',
        DB_STATUS_TRANSFERRED: 'Transferred',
        DB_STATUS_EXPIRED: 'Expired',
        DB_STATUS_OPPOSED: 'Opposed',
        DB_STATUS_REFUSED: 'Refused',
        DB_STATUS_WITHDRAWN: 'Withdrawn',
        DB_STATUS_CANCELLED: 'Cancelled',
        DB_STATUS_PUBLISHED: 'Published',
        DB_STATUS_PARTIAL_REFUSAL: 'Partial Refusal',
        DB_STATUS_APPLIED: 'Applied',
    }
    status = _canonicalize_db_status(determine_db_status(folder_name, status_raw, reg_no_val))
    return status_aliases.get(status, status)


def get_status_rank(status):
    status_aliases = {
        'Renewed': DB_STATUS_RENEWED,
        'Registered': DB_STATUS_REGISTERED,
        'Expired': DB_STATUS_EXPIRED,
        'Opposed': DB_STATUS_OPPOSED,
        'Refused': DB_STATUS_REFUSED,
        'Withdrawn': DB_STATUS_WITHDRAWN,
        'Cancelled': DB_STATUS_CANCELLED,
        'Published': DB_STATUS_PUBLISHED,
        'Applied': DB_STATUS_APPLIED,
        'Partial Refusal': DB_STATUS_PARTIAL_REFUSAL,
    }
    ranks = {
        DB_STATUS_RENEWED: 4,
        DB_STATUS_REGISTERED: 3,
        DB_STATUS_TRANSFERRED: 3,
        DB_STATUS_EXPIRED: 2,
        DB_STATUS_OPPOSED: 2,
        DB_STATUS_REFUSED: 2,
        DB_STATUS_WITHDRAWN: 2,
        DB_STATUS_CANCELLED: 2,
        DB_STATUS_PUBLISHED: 1,
        DB_STATUS_PARTIAL_REFUSAL: 1,
        DB_STATUS_APPLIED: 0,
    }
    normalized = _canonicalize_db_status(status_aliases.get(status, status))
    return ranks.get(normalized, -1)


def _resolve_image_path(folder_name: str, image_field: str, root_dir: Path) -> str | None:
    if not image_field: return None
    image_field = sanitize(image_field)
    if image_field is None: return None

    try:
        project_root = root_dir.parent.parent
        rel_root = root_dir.relative_to(project_root)
    except ValueError:
        rel_root = Path("bulletins/Marka")

    rel_prefix = str(rel_root).replace("\\", "/")
    img_dir_key = f"{root_dir}/{folder_name}/images"

    if img_dir_key not in _file_index:
        _build_file_index(root_dir / folder_name / "images", img_dir_key)

    img_index = _file_index[img_dir_key]
    if image_field in img_index:
        filename = img_index[image_field]
        return f"{rel_prefix}/{folder_name}/images/{filename}"
    return None

# ===================== SELF-HEALING FOR CORRUPT metadata.json =====================

def _has_tmbulletin_source(folder_path: Path) -> bool:
    for root, dirs, files in os.walk(folder_path):
        for fname in files:
            flow = fname.lower()
            if 'tmbulletin' in flow and (flow.endswith('.log') or flow.endswith('.script') or flow.endswith('.txt')): return True
            if 'gazete' in flow and flow.endswith('.txt'): return True
    return False

def _repair_corrupt_metadata(metadata_path: Path) -> dict:
    folder_path = metadata_path.parent
    folder_name = folder_path.name
    if not _has_tmbulletin_source(folder_path):
        logging.warning(f"   UNRECOVERABLE: {folder_name} - no tmbulletin source files")
        return {"status": "unrecoverable", "records": 0, "error": "No tmbulletin source files"}

    backup_path = metadata_path.parent / "metadata.json.corrupt_backup"
    if backup_path.exists():
        idx = 1
        while (metadata_path.parent / f"metadata.json.corrupt_backup.{idx}").exists(): idx += 1
        backup_path = metadata_path.parent / f"metadata.json.corrupt_backup.{idx}"

    try:
        shutil.copy2(str(metadata_path), str(backup_path))
        logging.info(f"   Backed up corrupt file -> {backup_path.name}")
    except Exception as e: return {"status": "regen_failed", "records": 0, "error": f"Backup failed: {e}"}

    try: metadata_path.unlink()
    except Exception as e: return {"status": "regen_failed", "records": 0, "error": f"Remove failed: {e}"}

    try:
        from metadata import process_single_folder as _regen_folder
        result = _regen_folder(folder_path, skip_existing=False)
        if result["status"] == "success" and result["records"] > 0:
            with open(metadata_path, 'r', encoding='utf-8') as f: data = json.load(f)
            if not isinstance(data, list) or len(data) == 0: raise ValueError("Regenerated file is empty or not a JSON list")
            try:
                from pipeline.ai import process_folder as _ai_process_folder
                logging.info(f"   Running AI feature generation for {folder_name} ({len(data)} records)...")
                _ai_process_folder(folder_path)
                logging.info(f"   REPAIRED: {folder_name} - regenerated {len(data)} records (with AI features)")
            except Exception as ai_err:
                logging.warning(f"   REPAIRED (no AI): {folder_name} - {len(data)} records, AI failed: {ai_err}")
            return {"status": "repaired", "records": len(data), "error": None}
        else:
            error_msg = result.get("error") or f"metadata.py returned status={result['status']}"
            logging.error(f"   REGEN FAILED: {folder_name} - {error_msg}")
            if not metadata_path.exists() and backup_path.exists(): shutil.copy2(str(backup_path), str(metadata_path))
            return {"status": "regen_failed", "records": 0, "error": error_msg}
    except Exception as e:
        logging.error(f"   REGEN FAILED: {folder_name} - {e}")
        if not metadata_path.exists() and backup_path.exists(): shutil.copy2(str(backup_path), str(metadata_path))
        return {"status": "regen_failed", "records": 0, "error": str(e)}

def pre_scan_and_repair(base_dir: Path) -> dict:
    repair_stats = {'repaired': [], 'unrecoverable': [], 'regen_failed': []}
    logging.info("=" * 60)
    logging.info("Pre-scan: checking all metadata.json files for corruption...")
    metadata_files = sorted(base_dir.rglob("metadata.json"))
    corrupt_count = 0

    for meta_path in metadata_files:
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, list): raise json.JSONDecodeError("Root element is not a JSON array", "", 0)
        except json.JSONDecodeError as e:
            corrupt_count += 1
            folder_name = meta_path.parent.name
            logging.warning(f"   CORRUPT: {folder_name}/metadata.json (error at pos {e.pos}: {e.msg})")
            result = _repair_corrupt_metadata(meta_path)
            if result["status"] == "repaired": repair_stats["repaired"].append((folder_name, result["records"]))
            elif result["status"] == "unrecoverable": repair_stats["unrecoverable"].append(folder_name)
            else: repair_stats["regen_failed"].append(folder_name)
        except Exception as e: logging.warning(f"   Cannot read {meta_path.parent.name}/metadata.json: {e}")

    if corrupt_count > 0:
        logging.info(f"Pre-scan complete: {corrupt_count} corrupt file(s) found")
        logging.info(f"   Repaired and ready: {len(repair_stats['repaired'])} folders")
    else:
        logging.info("Pre-scan complete: all metadata.json files are valid")
    logging.info("=" * 60)
    return repair_stats

def _print_repair_summary(repair_stats: dict):
    if not repair_stats.get('repaired') and not repair_stats.get('unrecoverable') and not repair_stats.get('regen_failed'): return
    logging.info("\n" + "=" * 60 + "\nSelf-healing summary:")
    for key, label in [('repaired', 'Repaired'), ('unrecoverable', 'Unrecoverable'), ('regen_failed', 'Regen Failed')]:
        items = repair_stats.get(key, [])
        logging.info(f"   {label}: {len(items)} folders")
    logging.info("=" * 60)


def check_and_migrate_schema(conn):
    cur = conn.cursor()
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        cur.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

        logging.info("âš™ï¸  Verifying database schema...")
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
        try:
            cur.execute("""
                DO $$ BEGIN
                    CREATE TYPE tm_status AS ENUM (
                        'Başvuruldu', 'Yayında', 'İtiraz Edildi', 'Tescil Edildi',
                        'Reddedildi', 'Geri Çekildi', 'Devredildi', 'Yenilendi',
                        'Kısmi Red', 'Süresi Doldu', 'Bilinmiyor', 'İptal Edildi'
                    );
                EXCEPTION WHEN duplicate_object THEN null; END $$;
            """)
        except Exception: conn.rollback()

        try:
            cur.execute("ALTER TYPE tm_status ADD VALUE IF NOT EXISTS 'İptal Edildi';")
            conn.commit()
        except Exception: conn.rollback()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS trademarks (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                application_no VARCHAR(255) UNIQUE NOT NULL,
                name TEXT,
                current_status tm_status DEFAULT 'Yayında',
                last_event_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        logging.info("âš™ï¸  Optimizing indices for long strings...")
        cur.execute("DROP INDEX IF EXISTS idx_tm_name;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tm_name_trgm ON trademarks USING GIST (name gist_trgm_ops);")

        cols_to_check = [
            ("availability_status", "VARCHAR(50)"), ("nice_class_numbers", "INTEGER[]"),
            ("vienna_class_numbers", "INTEGER[]"), ("extracted_goods", "JSONB"),
            ("registration_no", "VARCHAR(255)"), ("wipo_no", "VARCHAR(255)"),
            ("application_date", "DATE"), ("registration_date", "DATE"),
            ("bulletin_no", "VARCHAR(255)"), ("bulletin_date", "DATE"),
            ("gazette_no", "VARCHAR(255)"), ("gazette_date", "DATE"),
            ("appeal_deadline", "DATE"), ("expiry_date", "DATE"),
            ("image_path", "TEXT"), ("image_embedding", "halfvec(512)"),
            ("dinov2_embedding", "halfvec(768)"),
            ("color_histogram", "halfvec(512)"), ("logo_ocr_text", "TEXT"),
            ("name_tr", "VARCHAR(500)"), ("detected_lang", "VARCHAR(10)"),
            ("holder_name", "VARCHAR(500)"), ("holder_tpe_client_id", "VARCHAR(50)"),
            ("attorney_name", "VARCHAR(500)"), ("attorney_no", "VARCHAR(50)"),
            ("status_source", "VARCHAR(10)"),
        ]
        from psycopg2 import sql as psql
        ALLOWED_COL_TYPES = {"VARCHAR(500)", "VARCHAR(50)", "VARCHAR(10)", "TEXT", "INTEGER", "BOOLEAN", "TIMESTAMP"}
        for col_name, col_type in cols_to_check:
            if col_type not in ALLOWED_COL_TYPES: continue
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='trademarks' AND column_name=%s;", (col_name,))
            if not cur.fetchone():
                logging.info(f"   -> Adding missing column: {col_name}...")
                cur.execute(psql.SQL("ALTER TABLE trademarks ADD COLUMN {} " + col_type + ";").format(psql.Identifier(col_name)))

        cur.execute("CREATE INDEX IF NOT EXISTS idx_tm_holder_tpe_id ON trademarks(holder_tpe_client_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tm_holder_name ON trademarks(holder_name);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tm_attorney_name ON trademarks(attorney_name);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tm_attorney_no ON trademarks(attorney_no);")

        conn.commit()
        logging.info("âœ… Schema verified.")
    except Exception as e:
        conn.rollback()
        logging.error(f"âŒ Schema Check Failed: {e}")

def load_nice_classes(conn):
    classes_file = ROOT_DIR / "nice_classes_with_embeddings.json"
    if not classes_file.exists(): classes_file = ROOT_DIR / "nice_classes.json"
    if not classes_file.exists(): return

    logging.info(f"ðŸ“š Loading Nice Class reference data from {classes_file.name}...")

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

    try:
        with open(classes_file, 'r', encoding='utf-8') as f: data = json.load(f)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        pgvector_available = cur.fetchone() is not None

        insert_rows_with_emb = []
        insert_rows_no_emb = []

        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict): continue
                c_num = item.get("CLASSNO")
                desc = item.get("DESCRIPTION")
                embedding = item.get("CLASS_EMBEDDING")
                if c_num and desc:
                    c_num = int(c_num)
                    if pgvector_available and embedding:
                        emb_str = '[' + ','.join(map(str, embedding)) + ']'
                        insert_rows_with_emb.append((c_num, _CLASS_NAMES_TR.get(c_num), "", desc, emb_str))
                    else: insert_rows_no_emb.append((c_num, _CLASS_NAMES_TR.get(c_num), "", desc))

        if insert_rows_with_emb:
            execute_values(cur, """
                INSERT INTO nice_classes_lookup (class_number, name_tr, name_en, description, description_embedding)
                VALUES %s ON CONFLICT (class_number) DO UPDATE SET description = EXCLUDED.description, description_embedding = EXCLUDED.description_embedding;
            """, insert_rows_with_emb)
        if insert_rows_no_emb:
            execute_values(cur, """
                INSERT INTO nice_classes_lookup (class_number, name_tr, name_en, description)
                VALUES %s ON CONFLICT (class_number) DO UPDATE SET description = EXCLUDED.description;
            """, insert_rows_no_emb)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"   âŒ Failed to load class references: {e}")

DB_STATUS_APPLIED = _rules.DB_STATUS_APPLIED
DB_STATUS_PUBLISHED = _rules.DB_STATUS_PUBLISHED
DB_STATUS_OPPOSED = _rules.DB_STATUS_OPPOSED
DB_STATUS_REGISTERED = _rules.DB_STATUS_REGISTERED
DB_STATUS_REFUSED = _rules.DB_STATUS_REFUSED
DB_STATUS_WITHDRAWN = _rules.DB_STATUS_WITHDRAWN
DB_STATUS_TRANSFERRED = _rules.DB_STATUS_TRANSFERRED
DB_STATUS_RENEWED = _rules.DB_STATUS_RENEWED
DB_STATUS_PARTIAL_REFUSAL = _rules.DB_STATUS_PARTIAL_REFUSAL
DB_STATUS_EXPIRED = _rules.DB_STATUS_EXPIRED
DB_STATUS_UNKNOWN = _rules.DB_STATUS_UNKNOWN
DB_STATUS_CANCELLED = _rules.DB_STATUS_CANCELLED
_canonicalize_db_status = _rules._canonicalize_db_status
parse_date = _rules.parse_date
calculate_expiration_status = _rules.calculate_expiration_status
extract_bulletin_info = _rules.extract_bulletin_info
_explicit_db_status_from_text = _rules._explicit_db_status_from_text
_determine_db_status_raw = _rules._determine_db_status_raw
determine_db_status = _rules.determine_db_status
determine_status = _rules.determine_status
get_status_rank = _rules.get_status_rank
get_source_rank = _rules.get_source_rank
is_app_source_folder = _rules.is_app_source_folder
has_valid_registration_no = _rules.has_valid_registration_no
_name_cleans_to_empty = _rules._name_cleans_to_empty
_SHARED_FIELDS = _rules._SHARED_FIELDS
_SUSPICIOUS_SIX_FIELDS = _rules._SUSPICIOUS_SIX_FIELDS
_BLT_OWNED_FIELDS = _rules._BLT_OWNED_FIELDS
_GZ_OWNED_FIELDS = _rules._GZ_OWNED_FIELDS
_priority_coalesce = _rules._priority_coalesce
_suspicious_six_coalesce = _rules._suspicious_six_coalesce
_owned_field = _rules._owned_field
_build_update_set = _rules._build_update_set
_build_update_sql = _rules._build_update_sql
check_and_migrate_schema = _bootstrap.check_and_migrate_schema
load_nice_classes = _bootstrap.load_nice_classes


def _incoming_status_can_replace_existing(explicit_status, db_status, reg_no_val):
    if explicit_status not in (None, DB_STATUS_APPLIED, DB_STATUS_PUBLISHED):
        return True
    return (
        explicit_status is None
        and db_status == DB_STATUS_REGISTERED
        and has_valid_registration_no(reg_no_val)
    )


def _app_safe_nice_classes_for_update(incoming_classes, existing_classes):
    if not incoming_classes:
        return None
    existing_classes = existing_classes or []
    if not existing_classes:
        return incoming_classes
    if len(incoming_classes) > 6 and len(incoming_classes) > len(existing_classes):
        return incoming_classes
    return None


# === BATCH PROCESSING LOGIC ===

_INSERT_COLUMNS = [
    "application_no",
    "name",
    "current_status",
    "nice_class_numbers",
    "extracted_goods",
    "registration_no",
    "wipo_no",
    "vienna_class_numbers",
    "application_date",
    "registration_date",
    "last_event_date",
    "bulletin_no",
    "bulletin_date",
    "gazette_no",
    "gazette_date",
    "appeal_deadline",
    "expiry_date",
    "image_path",
    "image_embedding",
    "dinov2_embedding",
    "color_histogram",
    "logo_ocr_text",
    "name_tr",
    "detected_lang",
    "holder_name",
    "holder_tpe_client_id",
    "attorney_name",
    "attorney_no",
    "status_source",
]


def _build_insert_sql():
    columns = ", ".join(_INSERT_COLUMNS)
    return f"""
                INSERT INTO trademarks (
                    {columns}
                ) VALUES %s
                ON CONFLICT (application_no) DO UPDATE SET
                    registration_no = COALESCE(EXCLUDED.registration_no, trademarks.registration_no),
                    wipo_no = COALESCE(EXCLUDED.wipo_no, trademarks.wipo_no),
                    vienna_class_numbers = COALESCE(EXCLUDED.vienna_class_numbers, trademarks.vienna_class_numbers),
                    extracted_goods = COALESCE(EXCLUDED.extracted_goods, trademarks.extracted_goods),
                    appeal_deadline = COALESCE(EXCLUDED.appeal_deadline, trademarks.appeal_deadline),
                    image_embedding = COALESCE(EXCLUDED.image_embedding, trademarks.image_embedding),
                    dinov2_embedding = COALESCE(EXCLUDED.dinov2_embedding, trademarks.dinov2_embedding),
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


def process_file_batch(conn, file_path, force=False):
    cur = conn.cursor()
    filename = file_path.name
    folder_name = file_path.parent.name
    logging.info(f"Processing Batch: {folder_name}/{filename}")

    is_app_source = is_app_source_folder(folder_name)
    is_bulletin_source = folder_name.upper().startswith("BLT_") or "BULTEN" in folder_name.upper()
    is_gazette_source = folder_name.upper().startswith("GZ_") or "GAZETE" in folder_name.upper()
    new_source_rank, source_tag = get_source_rank(folder_name)

    folder_gazette_no = None
    folder_gazette_date = None
    if is_gazette_source:
        parts = folder_name.split('_')
        if len(parts) >= 2: folder_gazette_no = parts[1]
        if len(parts) >= 3: folder_gazette_date = parse_date(parts[2])

    if not force and not is_app_source:
        cur.execute(
            "SELECT status, COALESCE(record_count, 0) FROM processed_files WHERE filename = %s",
            (f"{folder_name}/{filename}",),
        )
        row = cur.fetchone()
        if row and row[0] in ('success', 'repaired'):
            logging.info("   -> Skipped (Already processed).")
            return {
                "status": "skipped",
                "filename": f"{folder_name}/{filename}",
                "inserted": 0,
                "updated": 0,
                "skipped": 1,
                "record_count": row[1],
                "error": None,
            }

    cur.execute("INSERT INTO processed_files (filename, status, processed_at) VALUES (%s, 'processing', NOW()) ON CONFLICT (filename) DO UPDATE SET status = 'processing', processed_at = NOW();", (f"{folder_name}/{filename}",))

    was_repaired = False
    try:
        try:
            with open(file_path, 'r', encoding='utf-8') as f: data = json.load(f)
        except json.JSONDecodeError:
            repair = _repair_corrupt_metadata(file_path)
            if repair["status"] == "repaired":
                with open(file_path, 'r', encoding='utf-8') as f: data = json.load(f)
                was_repaired = True
            else:
                cur.execute("UPDATE processed_files SET status = %s, error_log = %s WHERE filename = %s", (repair["status"], repair.get("error", ""), f"{folder_name}/{filename}"))
                conn.commit()
                return {
                    "status": repair["status"],
                    "filename": f"{folder_name}/{filename}",
                    "inserted": 0,
                    "updated": 0,
                    "skipped": 0,
                    "record_count": 0,
                    "error": repair.get("error"),
                }

        if not data:
            file_result_status = 'repaired' if was_repaired else 'success'
            cur.execute(
                "UPDATE processed_files SET status = %s, record_count = 0, error_log = NULL WHERE filename = %s",
                (file_result_status, f"{folder_name}/{filename}"),
            )
            conn.commit()
            _file_index.clear()
            return {
                "status": file_result_status,
                "filename": f"{folder_name}/{filename}",
                "inserted": 0,
                "updated": 0,
                "skipped": 0,
                "record_count": 0,
                "error": None,
            }

        app_map = {rec.get("APPLICATIONNO"): rec for rec in data if rec.get("APPLICATIONNO")}
        all_app_nos = list(app_map.keys())

        existing_db_records = {}
        if all_app_nos:
            cur.execute("SELECT application_no, id, last_event_date, current_status, expiry_date, status_source, name, nice_class_numbers FROM trademarks WHERE application_no = ANY(%s)", (all_app_nos,))
            for row in cur.fetchall():
                existing_db_records[row[0]] = {"id": row[1], "last_date": row[2], "status": _canonicalize_db_status(row[3]), "expiry": row[4], "status_source": row[5], "name": row[6], "nice_class_numbers": row[7]}

        new_inserts = []
        updates = []
        history_inserts = []
        skipped_count = 0

        for rec in data:
            app_no = rec.get("APPLICATIONNO")
            if not app_no: continue

            tm = rec.get("TRADEMARK", {})
            raw_tm_name = tm.get("NAME", "")
            tm_name = clean_name(raw_tm_name)

            if tm_name and len(tm_name) > 2000:
                skipped_count += 1
                continue

            status_raw = rec.get("STATUS", "")
            reg_no_val = tm.get("REGISTERNO")

            db_status = determine_db_status(folder_name, status_raw, reg_no_val)
            explicit_status = _explicit_db_status_from_text(status_raw)

            app_date = parse_date(tm.get("APPLICATIONDATE"))
            reg_date = parse_date(tm.get("REGISTERDATE"))
            bulletin_date_val = parse_date(tm.get("BULLETIN_DATE"))
            gazette_date_val = folder_gazette_date if is_gazette_source else parse_date(tm.get("GAZETTE_DATE"))

            comparison_date = None
            if is_bulletin_source and bulletin_date_val: comparison_date = bulletin_date_val
            elif is_gazette_source and gazette_date_val: comparison_date = gazette_date_val
            elif db_status == DB_STATUS_REGISTERED and reg_date: comparison_date = reg_date
            elif db_status == DB_STATUS_PUBLISHED and bulletin_date_val: comparison_date = bulletin_date_val
            elif app_date: comparison_date = app_date

            if comparison_date is None and db_status == DB_STATUS_PUBLISHED and bulletin_date_val:
                comparison_date = bulletin_date_val

            db_write_date = comparison_date or datetime.now().date()

            new_expiry_date = None
            if app_date:
                try: ten_yr = app_date.replace(year=app_date.year + 10)
                except ValueError: ten_yr = app_date + timedelta(days=3652)
                new_expiry_date = ten_yr + timedelta(days=183)

            appeal_dl = calculate_appeal_deadline(bulletin_date_val) if bulletin_date_val and (db_status == DB_STATUS_PUBLISHED or is_bulletin_source) else None
            if bulletin_date_val and db_status == DB_STATUS_PUBLISHED:
                appeal_dl = calculate_appeal_deadline(bulletin_date_val)

            img_emb = embedding_to_halfvec(rec.get("image_embedding"), 512)
            dino_emb = embedding_to_halfvec(rec.get("dinov2_embedding"), 768)
            name_features_are_stale = (
                not tm_name
                or (
                    _raw_name_was_materially_cleaned(raw_tm_name, tm_name)
                    and not _metadata_text_features_are_from_clean_name(rec, tm_name)
                )
            )
            color_emb = embedding_to_halfvec(rec.get("color_histogram"), 512)
            img_path = _resolve_image_path(folder_name, rec.get("IMAGE"), ROOT_DIR)
            ocr_text = rec.get("logo_ocr_text")

            name_tr = None if name_features_are_stale else _trunc(rec.get("name_tr"), 500)
            detected_lang = None if name_features_are_stale else _trunc(rec.get("detected_lang"), 10)

            holders_list = rec.get("HOLDERS", [])
            holder_name, holder_tpe_client_id = None, None
            if holders_list and len(holders_list) > 0:
                raw_title = holders_list[0].get("TITLE", "")
                existing_tpe = holders_list[0].get("TPECLIENTID", "")
                holder_clean, extracted_id = extract_tpe_id(raw_title)
                holder_name = _trunc(holder_clean, 500)
                holder_tpe_client_id = _trunc(existing_tpe or extracted_id, 50)

            attorneys_list = rec.get("ATTORNEYS", [])
            attorney_name, attorney_no = None, None
            if attorneys_list and len(attorneys_list) > 0:
                raw_name = attorneys_list[0].get("NAME", "")
                existing_no = attorneys_list[0].get("NO", "")
                atty_clean, extracted_id = extract_tpe_id(raw_name)
                attorney_name = _trunc(atty_clean, 500)
                attorney_no = _trunc(existing_no or extracted_id, 50)

            reg_no = _trunc(reg_no_val, 255)
            wipo_no = _trunc(tm.get("INTREGNO"), 255)

            raw_classes = tm.get("NICECLASSES_LIST", [])
            clean_classes_list = [int(c) for c in raw_classes if str(c).strip().isdigit()]
            raw_vienna = tm.get("VIENNACLASSES_LIST", [])
            vienna_classes = [int(c) for c in raw_vienna if str(c).strip().isdigit()]

            raw_extracted = rec.get("EXTRACTEDGOODS")
            extracted_goods_data = raw_extracted if raw_extracted else None

            existing = existing_db_records.get(app_no)

            if not existing:
                insert_bulletin_no = tm.get("BULLETIN_NO") if is_bulletin_source else None
                insert_bulletin_date = bulletin_date_val if is_bulletin_source else None

                new_inserts.append((
                    app_no, sanitize(tm_name), db_status,
                    clean_classes_list or None, Json(extracted_goods_data) if extracted_goods_data else None,
                    reg_no, wipo_no, vienna_classes or None,
                    app_date, reg_date, db_write_date,
                    sanitize(insert_bulletin_no), insert_bulletin_date,
                    sanitize(folder_gazette_no if is_gazette_source else tm.get("GAZETTE_NO")), gazette_date_val,
                    appeal_dl, new_expiry_date, img_path,
                    img_emb, dino_emb, color_emb, sanitize(ocr_text),
                    name_tr, detected_lang, holder_name, holder_tpe_client_id,
                    attorney_name, attorney_no, source_tag
                ))
            else:
                curr_status = existing['status']
                existing_source = existing.get('status_source') or 'BLT'
                old_source_rank = {'APP': 3, 'GZ': 2}.get(existing_source, 1)
                clear_name = (
                    _name_cleans_to_empty(raw_tm_name)
                    and _name_cleans_to_empty(existing.get("name"))
                )
                clear_text_features = name_features_are_stale and (bool(tm_name) or clear_name)

                should_update = force
                next_status = db_status
                status_can_replace = _incoming_status_can_replace_existing(
                    explicit_status,
                    db_status,
                    reg_no_val,
                )

                if existing_source == "LIVE" and not status_can_replace:
                    should_update = True
                    next_status = curr_status
                elif (
                    existing_source == "APP"
                    and curr_status == DB_STATUS_APPLIED
                    and not is_app_source
                    and db_status != DB_STATUS_APPLIED
                ):
                    should_update = True
                    next_status = db_status
                elif new_source_rank >= old_source_rank:
                    should_update = True
                    if is_app_source and not status_can_replace:
                        next_status = curr_status
                else:
                    should_update = True
                    next_status = curr_status

                if should_update:
                    is_renewal = False
                    if curr_status in [DB_STATUS_REGISTERED, DB_STATUS_RENEWED, DB_STATUS_EXPIRED] and next_status == DB_STATUS_REGISTERED:
                         if existing['expiry'] and new_expiry_date and new_expiry_date > existing['expiry']:
                             next_status = DB_STATUS_RENEWED
                             is_renewal = True

                    # If ingest keeps the prior status, keep the prior ingest source tag too.
                    next_source_tag = existing_source if (next_status == curr_status and not is_renewal) else source_tag
                    update_classes_list = clean_classes_list or None
                    if is_app_source:
                        update_classes_list = _app_safe_nice_classes_for_update(
                            clean_classes_list,
                            existing.get("nice_class_numbers"),
                        )

                    updates.append((
                        sanitize(tm_name), clear_name, clear_text_features, next_status, update_classes_list,
                        Json(extracted_goods_data) if extracted_goods_data else None,
                        db_write_date, appeal_dl, new_expiry_date,
                        sanitize(tm.get("BULLETIN_NO")), bulletin_date_val,
                        sanitize(folder_gazette_no if is_gazette_source else tm.get("GAZETTE_NO")), gazette_date_val,
                        img_path, app_date, reg_date,
                        img_emb, dino_emb, color_emb, sanitize(ocr_text),
                        name_tr, detected_lang, None, None, None,
                        holder_name, holder_tpe_client_id,
                        attorney_name, attorney_no,
                        next_source_tag,  # Stored as the ingest-owned status_source.
                        reg_no, wipo_no, vienna_classes or None, app_no
                    ))

                    if curr_status != next_status or is_renewal:
                        history_inserts.append((existing['id'], db_write_date, "STATUS_CHANGE" if not is_renewal else "RENEWAL", filename, f"{curr_status} -> {next_status}"))
                else:
                    skipped_count += 1

        if new_inserts:
            seen_app_nos = {}
            for i, row in enumerate(new_inserts): seen_app_nos[row[0]] = i
            if len(seen_app_nos) < len(new_inserts):
                new_inserts = [new_inserts[i] for i in sorted(seen_app_nos.values())]

            execute_values(cur, _build_insert_sql(), new_inserts)

        if updates:
            seen_upd = {}
            for i, row in enumerate(updates): seen_upd[row[-1]] = i
            if len(seen_upd) < len(updates):
                updates = [updates[i] for i in sorted(seen_upd.values())]

            update_sql = _build_update_sql('APP' if is_app_source else ('GZ' if is_gazette_source else 'BLT'))
            execute_values(cur, update_sql, updates)

        if history_inserts:
            try:
                cur.execute("SAVEPOINT before_history")
                execute_values(cur, "INSERT INTO trademark_history (trademark_id, event_date, event_type, source_file, description) VALUES %s ON CONFLICT DO NOTHING", history_inserts)
                cur.execute("RELEASE SAVEPOINT before_history")
            except Exception as hist_err:
                cur.execute("ROLLBACK TO SAVEPOINT before_history")
                logging.warning(f"   âš ï¸ History insert skipped: {hist_err}")

        file_result_status = 'repaired' if was_repaired else 'success'
        cur.execute(
            "UPDATE processed_files SET status = %s, record_count = %s, error_log = NULL WHERE filename = %s",
            (file_result_status, len(new_inserts) + len(updates), f"{folder_name}/{filename}"),
        )
        conn.commit()

        # [CRITICAL FIX] Clean up memory leak - free dictionary memory after folder finishes
        _file_index.clear()

        batch_app_nos = [ins[0] for ins in new_inserts] + [upd[0] for upd in updates]
        if batch_app_nos:
            try:
                from utils.status_reconciler import update_final_status_batch
                update_final_status_batch(conn, app_nos=batch_app_nos)
            except Exception: pass

        logging.info(f"   âœ… Batch Complete. {len(new_inserts)} Ins, {len(updates)} Upd, {skipped_count} Skip.")

        if new_inserts:
            cur.execute("SELECT id FROM trademarks WHERE application_no = ANY(%s)", ([ins[0] for ins in new_inserts],))
            new_trademark_ids = [row[0] for row in cur.fetchall()]

            if new_trademark_ids:
                try:
                    from watchlist.scanner import trigger_watchlist_scan
                    trigger_watchlist_scan(new_trademark_ids, 'bulletin' if is_bulletin_source else ('gazette' if is_gazette_source else 'application'), folder_name)
                except Exception as exc:
                    # Mirror the patent/design/cografi post-ingest hooks:
                    # log scan failures so an observability gap doesn't
                    # swallow missed alerts. Never re-raise — a failed
                    # scan must not poison a successful ingest commit.
                    logging.warning(
                        f"   Watchlist scan failed for {folder_name} "
                        f"({len(new_trademark_ids)} new trademarks): {exc!r}"
                    )

            if new_trademark_ids and is_bulletin_source:
                queue_bulletin_no, queue_bulletin_date = extract_bulletin_info(folder_name)
                add_to_scan_queue(conn=conn, trademark_ids=new_trademark_ids, bulletin_no=queue_bulletin_no, bulletin_date=queue_bulletin_date, priority=1)

        return {
            "status": file_result_status,
            "filename": f"{folder_name}/{filename}",
            "inserted": len(new_inserts),
            "updated": len(updates),
            "skipped": skipped_count,
            "record_count": len(data),
            "error": None,
        }

    except Exception as e:
        conn.rollback()
        logging.error(f"   âŒ Batch Failed: {e}")
        cur.execute("UPDATE processed_files SET status = 'failed', error_log = %s WHERE filename = %s", (str(e), f"{folder_name}/{filename}"))
        conn.commit()
        # Ensure memory is freed even on failure
        _file_index.clear()
        return {
            "status": "failed",
            "filename": f"{folder_name}/{filename}",
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "record_count": 0,
            "error": str(e),
        }

def run_ingest(force=False, settings=None) -> dict:
    global ROOT_DIR
    if settings is not None: ROOT_DIR = Path(settings.bulletins_root)
    t0 = time.time()
    conn, inserted, updated, skipped = None, 0, 0, 0
    repair_stats = {'repaired': [], 'unrecoverable': [], 'regen_failed': []}

    try:
        conn = get_connection()
        check_and_migrate_schema(conn)
        load_nice_classes(conn)
        repair_stats = pre_scan_and_repair(ROOT_DIR)

        metadata_files = list(ROOT_DIR.rglob("metadata.json"))
        logging.info(f"Found {len(metadata_files)} files.")

        def sort_key(p):
            name = p.parent.name.upper()
            m = _re.search(r'_(\d+)', p.parent.name)
            num = int(m.group(1)) if m else 0
            if name.startswith("BLT"): return (0, -num)
            if name.startswith("GZ"):  return (1, -num)
            return (2, -num)
        metadata_files.sort(key=sort_key)

        for json_file in metadata_files: process_file_batch(conn, json_file, force)
    except Exception as e:
        logging.error(f"Ingestion failed: {e}")
        raise
    finally:
        if conn: release_connection(conn)

    duration = time.time() - t0
    _print_repair_summary(repair_stats)
    logging.info(f"Ingestion complete in {duration:.1f}s")
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "duration_seconds": round(duration, 1), "repair_stats": repair_stats}


# Compatibility redefinitions keep public helper behavior stable for tests and
# ancillary scripts without changing the DB-facing ingestion pipeline.
DB_STATUS_APPLIED = "Başvuruldu"
DB_STATUS_PUBLISHED = "Yayında"
DB_STATUS_OPPOSED = "İtiraz Edildi"
DB_STATUS_REGISTERED = "Tescil Edildi"
DB_STATUS_REFUSED = "Reddedildi"
DB_STATUS_WITHDRAWN = "Geri Çekildi"
DB_STATUS_TRANSFERRED = "Devredildi"
DB_STATUS_RENEWED = "Yenilendi"
DB_STATUS_PARTIAL_REFUSAL = "Kısmi Red"
DB_STATUS_EXPIRED = "Süresi Doldu"
DB_STATUS_UNKNOWN = "Bilinmiyor"
DB_STATUS_CANCELLED = "İptal Edildi"


def _repair_mojibake(text):
    if not isinstance(text, str):
        return text

    repaired = text
    for _ in range(3):
        if not any(ch in repaired for ch in ("Ã", "Ä", "Å", "Â")):
            break
        candidate = repaired
        for source_encoding in ("latin1", "cp1252"):
            try:
                candidate = repaired.encode(source_encoding).decode("utf-8")
                break
            except UnicodeError:
                continue
        if candidate == repaired:
            break
        repaired = candidate
    return repaired


def _canonicalize_db_status(status):
    if not status:
        return status

    normalized = _repair_mojibake(status)
    aliases = {
        "Applied": DB_STATUS_APPLIED,
        "Published": DB_STATUS_PUBLISHED,
        "Opposed": DB_STATUS_OPPOSED,
        "Registered": DB_STATUS_REGISTERED,
        "Refused": DB_STATUS_REFUSED,
        "Withdrawn": DB_STATUS_WITHDRAWN,
        "Transferred": DB_STATUS_TRANSFERRED,
        "Renewed": DB_STATUS_RENEWED,
        "Partial Refusal": DB_STATUS_PARTIAL_REFUSAL,
        "Expired": DB_STATUS_EXPIRED,
        "Unknown": DB_STATUS_UNKNOWN,
        "Cancelled": DB_STATUS_CANCELLED,
    }
    return aliases.get(normalized, normalized)


_determine_db_status_raw = determine_status


def determine_db_status(folder_name, status_raw, reg_no_val=None):
    return _canonicalize_db_status(_determine_db_status_raw(folder_name, status_raw, reg_no_val))


def extract_bulletin_info(folder_name: str):
    """
    Extract bulletin number and date from folder name.
    Safely handles both BLT_327_2019-06-27 and BLT_2025_03 formats.
    """
    period_match = _re.search(r'^(?:BLT|BULTEN|GZ|GAZETE)[_-]?(\d{4})[_-](\d{2})$', folder_name, _re.IGNORECASE)
    if period_match:
        bulletin_no = f"{period_match.group(1)}/{period_match.group(2)}"
    else:
        no_match = _re.search(r'(?:BLT|BULTEN|GZ|GAZETE)[_-]?(\d+)', folder_name, _re.IGNORECASE)
        bulletin_no = no_match.group(1) if no_match else None

    date_match = _re.search(r'(\d{4}[_-]\d{2}[_-]\d{2}|\d{4}[_-]\d{2})', folder_name)
    bulletin_date = None
    if date_match:
        d_str = date_match.group(1).replace('_', '-')
        try:
            if len(d_str) == 7:
                bulletin_date = datetime.strptime(d_str, "%Y-%m").date()
            else:
                bulletin_date = datetime.strptime(d_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    return bulletin_no, bulletin_date


def determine_status(folder_name, status_raw, reg_no_val=None):
    status_aliases = {
        'Yenilendi': 'Renewed',
        'Tescil Edildi': 'Registered',
        'Devredildi': 'Transferred',
        'Süresi Doldu': 'Expired',
        'İtiraz Edildi': 'Opposed',
        'Reddedildi': 'Refused',
        'Geri Çekildi': 'Withdrawn',
        'İptal Edildi': 'Cancelled',
        'Yayında': 'Published',
        'Kısmi Red': 'Partial Refusal',
        'Başvuruldu': 'Applied',
    }
    status = determine_db_status(folder_name, status_raw, reg_no_val)
    return status_aliases.get(status, status)


def get_status_rank(status):
    status_aliases = {
        'Renewed': 'Yenilendi',
        'Registered': 'Tescil Edildi',
        'Expired': 'Süresi Doldu',
        'Opposed': 'İtiraz Edildi',
        'Refused': 'Reddedildi',
        'Withdrawn': 'Geri Çekildi',
        'Cancelled': 'İptal Edildi',
        'Published': 'Yayında',
        'Applied': 'Başvuruldu',
        'Partial Refusal': 'Kısmi Red',
    }
    ranks = {
        'Yenilendi': 4,
        'Tescil Edildi': 3,
        'Devredildi': 3,
        'Süresi Doldu': 2,
        'İtiraz Edildi': 2,
        'Reddedildi': 2,
        'Geri Çekildi': 2,
        'İptal Edildi': 2,
        'Yayında': 1,
        'Kısmi Red': 1,
        'Başvuruldu': 0,
    }
    status = status_aliases.get(status, status)
    return ranks.get(status, -1)


def _determine_db_status_raw(folder_name, status_raw, reg_no_val=None):
    folder_upper = folder_name.upper()
    status_text = _repair_mojibake(str(status_raw)) if status_raw else ""
    status_lower = status_text.lower().replace('\u0307', '').strip()
    has_reg_no = reg_no_val and str(reg_no_val).strip() and str(reg_no_val).strip().lower() not in ('null', 'none', '')

    if status_lower:
        refused_keywords = ['geçersiz', 'gecersiz', 'marka başvurusu/tescili geçersiz', 'başvuru geçersiz', 'basvuru gecersiz', 'tescil geçersiz', 'tescil gecersiz', 'reddedildi', 'red edildi', 'ret kararı', 'red kararı', 'refused', 'rejected']
        if 'başvuru geçersiz' in status_lower or 'marka başvurusu/tescili geçersiz' in status_lower or 'tescil geçersiz' in status_lower or 'ret kararı' in status_lower:
            return DB_STATUS_REFUSED
        if any(kw in status_lower for kw in refused_keywords):
            return DB_STATUS_REFUSED

        withdrawn_keywords = ['feragat edildi', 'feragat', 'geri çekildi', 'geri cekildi', 'geri alındı', 'geri alindi', 'vazgeçildi', 'vazgecildi', 'withdrawn']
        if any(kw in status_lower for kw in withdrawn_keywords):
            return DB_STATUS_WITHDRAWN

        cancelled_keywords = ['iptal edildi', 'mahkeme kararı', 'mahkeme karari', 'cancelled', 'canceled']
        if any(kw in status_lower for kw in cancelled_keywords):
            return DB_STATUS_CANCELLED

        registered_keywords = ['tescil edildi', 'tescilli', 'kabul edildi', 'registered']
        if any(kw in status_lower for kw in registered_keywords):
            return DB_STATUS_REGISTERED

        if 'itiraz' in status_lower or 'opposed' in status_lower:
            return DB_STATUS_OPPOSED

        expired_keywords = ['sona erdi', 'süresi doldu', 'suresi doldu', 'hükümsüz', 'hukumsuz', 'expired', 'yürürlükten', 'yururlukten']
        if any(kw in status_lower for kw in expired_keywords):
            return DB_STATUS_EXPIRED

        published_keywords = ['yayınlandı', 'yayinlandi', 'ilan edildi', 'published']
        if any(kw in status_lower for kw in published_keywords):
            return DB_STATUS_PUBLISHED

        if 'renewed' in status_lower or 'yenilendi' in status_lower:
            return DB_STATUS_RENEWED

    if has_reg_no:
        return DB_STATUS_REGISTERED
    if folder_upper.startswith("GZ_") or "GAZETE" in folder_upper:
        return DB_STATUS_REGISTERED
    if folder_upper.startswith("BLT_") or "BULTEN" in folder_upper:
        return DB_STATUS_PUBLISHED

    return DB_STATUS_APPLIED


def determine_db_status(folder_name, status_raw, reg_no_val=None):
    return _canonicalize_db_status(_determine_db_status_raw(folder_name, status_raw, reg_no_val))


def determine_status(folder_name, status_raw, reg_no_val=None):
    status_aliases = {
        DB_STATUS_RENEWED: 'Renewed',
        DB_STATUS_REGISTERED: 'Registered',
        DB_STATUS_TRANSFERRED: 'Transferred',
        DB_STATUS_EXPIRED: 'Expired',
        DB_STATUS_OPPOSED: 'Opposed',
        DB_STATUS_REFUSED: 'Refused',
        DB_STATUS_WITHDRAWN: 'Withdrawn',
        DB_STATUS_CANCELLED: 'Cancelled',
        DB_STATUS_PUBLISHED: 'Published',
        DB_STATUS_PARTIAL_REFUSAL: 'Partial Refusal',
        DB_STATUS_APPLIED: 'Applied',
    }
    status = determine_db_status(folder_name, status_raw, reg_no_val)
    return status_aliases.get(status, status)


def get_status_rank(status):
    status_aliases = {
        'Renewed': DB_STATUS_RENEWED,
        'Registered': DB_STATUS_REGISTERED,
        'Transferred': DB_STATUS_TRANSFERRED,
        'Expired': DB_STATUS_EXPIRED,
        'Opposed': DB_STATUS_OPPOSED,
        'Refused': DB_STATUS_REFUSED,
        'Withdrawn': DB_STATUS_WITHDRAWN,
        'Cancelled': DB_STATUS_CANCELLED,
        'Published': DB_STATUS_PUBLISHED,
        'Applied': DB_STATUS_APPLIED,
        'Partial Refusal': DB_STATUS_PARTIAL_REFUSAL,
    }
    ranks = {
        DB_STATUS_RENEWED: 4,
        DB_STATUS_REGISTERED: 3,
        DB_STATUS_TRANSFERRED: 3,
        DB_STATUS_EXPIRED: 2,
        DB_STATUS_OPPOSED: 2,
        DB_STATUS_REFUSED: 2,
        DB_STATUS_WITHDRAWN: 2,
        DB_STATUS_CANCELLED: 2,
        DB_STATUS_PUBLISHED: 1,
        DB_STATUS_PARTIAL_REFUSAL: 1,
        DB_STATUS_APPLIED: 0,
    }
    normalized = _canonicalize_db_status(status_aliases.get(status, status))
    return ranks.get(normalized, -1)


def _resolve_image_path(folder_name: str, image_field: str, root_dir: Path) -> str | None:
    if not image_field:
        return None

    image_field = sanitize(image_field)
    if image_field is None:
        return None

    try:
        project_root = root_dir.parent.parent
        rel_root = root_dir.relative_to(project_root)
    except ValueError:
        rel_root = Path("bulletins/Marka")

    rel_prefix = str(rel_root).replace("\\", "/")
    img_dir_key = f"{root_dir}/{folder_name}/images"

    if img_dir_key not in _file_index:
        _build_file_index(root_dir / folder_name / "images", img_dir_key)

    img_index = _file_index[img_dir_key]
    if image_field in img_index:
        filename = img_index[image_field]
        return f"{rel_prefix}/{folder_name}/images/{filename}"

    logos_dir_key = f"{root_dir}/LOGOS"
    if logos_dir_key not in _file_index:
        _build_file_index(root_dir / "LOGOS", logos_dir_key)

    logos_index = _file_index[logos_dir_key]
    if image_field in logos_index:
        filename = logos_index[image_field]
        return f"{rel_prefix}/LOGOS/{filename}"

    return None


def cleanup_sekil_names(conn, batch_size: int = 5000) -> int:
    """Remove placeholder-only 'sekil' values and invalidate stale name-derived text features."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, name_tr
        FROM trademarks
        WHERE (
            name IS NOT NULL
          AND (
              lower(name) LIKE '%sekil%'
              OR name ~* '(s|ş)ek(i|ı|İ)l'
          )
        )
        OR (
            name_tr IS NOT NULL
            AND (
                lower(name_tr) LIKE '%sekil%'
                OR name_tr ~* '(s|ÅŸ)ek(i|Ä±|Ä°)l'
            )
        )
        OR (
            COALESCE(NULLIF(BTRIM(name), ''), NULLIF(BTRIM(name_tr), '')) IS NULL
            AND (
                detected_lang IS NOT NULL
                OR name_tr_backend IS NOT NULL
                OR name_tr_model IS NOT NULL
                OR name_tr_updated_at IS NOT NULL
            )
        )
        """
    )
    rows = cur.fetchall()
    cleaned_total = 0

    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        updates = []
        for tm_id, current_name, current_name_tr in batch:
            cleaned_name = _rules.clean_name(current_name)
            cleaned_name_tr = _rules.clean_name(current_name_tr)
            name_changed = cleaned_name != current_name
            name_tr_changed = cleaned_name_tr != current_name_tr
            logo_only_after_cleanup = not cleaned_name and not cleaned_name_tr
            if name_changed or name_tr_changed or logo_only_after_cleanup:
                clear_translation = name_changed or name_tr_changed or logo_only_after_cleanup
                updates.append(
                    (
                        str(tm_id),
                        sanitize(cleaned_name),
                        None if clear_translation else sanitize(cleaned_name_tr),
                        clear_translation,
                    )
                )

        if updates:
            execute_values(
                cur,
                """
                UPDATE trademarks AS tm
                SET name = v.name::text,
                    name_tr = v.name_tr::text,
                    detected_lang = CASE WHEN v.clear_translation THEN NULL ELSE tm.detected_lang END,
                    name_tr_backend = CASE WHEN v.clear_translation THEN NULL ELSE tm.name_tr_backend END,
                    name_tr_model = CASE WHEN v.clear_translation THEN NULL ELSE tm.name_tr_model END,
                    name_tr_updated_at = CASE WHEN v.clear_translation THEN NULL ELSE tm.name_tr_updated_at END,
                    updated_at = NOW()
                FROM (VALUES %s) AS v(id, name, name_tr, clear_translation)
                WHERE tm.id = v.id::uuid
                """,
                updates,
            )
            cleaned_total += len(updates)
            conn.commit()

    return cleaned_total


def cleanup_applied_publication_statuses(conn) -> int:
    """Compatibility wrapper for the standalone post-ingest status repair."""
    from pipeline.status_repair import run_status_repair

    return run_status_repair(conn=conn).get("repaired", 0)


def main():
    parser = argparse.ArgumentParser(description="Ingest trademark data (10M Scale).")
    parser.add_argument("--force", action="store_true", help="Force re-processing.")
    parser.add_argument("--folder", type=str, help="Process only this folder name (e.g. GZ_300).")
    args = parser.parse_args()

    conn, repair_stats = None, {'repaired': [], 'unrecoverable': [], 'regen_failed': []}
    try:
        conn = get_connection()
        check_and_migrate_schema(conn)
        load_nice_classes(conn)

        if args.folder:
            folder_path = ROOT_DIR / args.folder / "metadata.json"
            if not folder_path.exists():
                logging.error(f"metadata.json not found: {folder_path}")
                sys.exit(1)
            metadata_files = [folder_path]
        else:
            repair_stats = pre_scan_and_repair(ROOT_DIR)
            metadata_files = list(ROOT_DIR.rglob("metadata.json"))

        def sort_key(p):
            name = p.parent.name.upper()
            m = _re.search(r'_(\d+)', p.parent.name)
            num = int(m.group(1)) if m else 0
            if name.startswith("BLT"): return (0, num)
            if name.startswith("GZ"):  return (1, num)
            return (2, num)
        metadata_files.sort(key=sort_key)

        for json_file in metadata_files: process_file_batch(conn, json_file, args.force)
        cleaned_names = cleanup_sekil_names(conn)
        if cleaned_names:
            logging.info("Cleaned %s trademark name placeholder(s)", cleaned_names)
    except Exception as e:
        logging.error(f"Ingestion failed: {e}")
        raise
    finally:
        if conn: release_connection(conn)
        _print_repair_summary(repair_stats)

if __name__ == "__main__":
    try: main()
    finally: close_pool()

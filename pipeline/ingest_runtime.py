"""Canonical ingest runtime orchestration."""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from psycopg2.extras import Json, execute_values

from db.pool import close_pool, get_connection, release_connection
from pipeline import ingest_bootstrap as _bootstrap
from pipeline import ingest_legacy as _legacy
from pipeline import ingest_rules as _rules
from utils.deadline import calculate_appeal_deadline

ROOT_DIR = _bootstrap.default_ingest_root()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

sanitize = _legacy.sanitize
_trunc = _legacy._trunc
embedding_to_halfvec = _legacy.embedding_to_halfvec
extract_tpe_id = _legacy.extract_tpe_id
_build_file_index = _legacy._build_file_index
_resolve_image_path = _legacy._resolve_image_path
_has_tmbulletin_source = _legacy._has_tmbulletin_source
_repair_corrupt_metadata = _legacy._repair_corrupt_metadata
pre_scan_and_repair = _legacy.pre_scan_and_repair
_print_repair_summary = _legacy._print_repair_summary
_check_scan_queue_table = _legacy._check_scan_queue_table
add_to_scan_queue = _legacy.add_to_scan_queue

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
clean_name = _rules.clean_name
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
    "text_embedding",
    "color_histogram",
    "logo_ocr_text",
    "name_tr",
    "detected_lang",
    "name_tr_backend",
    "name_tr_model",
    "name_tr_updated_at",
    "holder_name",
    "holder_tpe_client_id",
    "attorney_name",
    "attorney_no",
    "status_source",
]


def get_db_connection():
    return get_connection()


def set_root_dir(root_dir):
    global ROOT_DIR
    ROOT_DIR = _bootstrap.resolve_ingest_root(str(root_dir))
    return ROOT_DIR


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
                    text_embedding = COALESCE(EXCLUDED.text_embedding, trademarks.text_embedding),
                    color_histogram = COALESCE(EXCLUDED.color_histogram, trademarks.color_histogram),
                    logo_ocr_text = COALESCE(EXCLUDED.logo_ocr_text, trademarks.logo_ocr_text),
                    name_tr = COALESCE(EXCLUDED.name_tr, trademarks.name_tr),
                    detected_lang = COALESCE(EXCLUDED.detected_lang, trademarks.detected_lang),
                    name_tr_backend = COALESCE(EXCLUDED.name_tr_backend, trademarks.name_tr_backend),
                    name_tr_model = COALESCE(EXCLUDED.name_tr_model, trademarks.name_tr_model),
                    name_tr_updated_at = COALESCE(EXCLUDED.name_tr_updated_at, trademarks.name_tr_updated_at),
                    holder_name = COALESCE(EXCLUDED.holder_name, trademarks.holder_name),
                    holder_tpe_client_id = COALESCE(EXCLUDED.holder_tpe_client_id, trademarks.holder_tpe_client_id),
                    attorney_name = COALESCE(EXCLUDED.attorney_name, trademarks.attorney_name),
                    attorney_no = COALESCE(EXCLUDED.attorney_no, trademarks.attorney_no),
                    updated_at = NOW()
            """


def metadata_file_sort_key(path: Path, descending: bool = True):
    name = path.parent.name.upper()
    match = re.search(r"_(\d+)", path.parent.name)
    number = int(match.group(1)) if match else 0
    sort_number = -number if descending else number
    if name.startswith("BLT"):
        return (0, sort_number, path.parent.name)
    if name.startswith("GZ"):
        return (1, sort_number, path.parent.name)
    return (2, sort_number, path.parent.name)


def _collect_metadata_files(root_dir: Path, folder_name: str | None = None, descending: bool = True):
    if folder_name:
        metadata_path = root_dir / folder_name / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"metadata.json not found: {metadata_path}")
        return [metadata_path]
    metadata_files = list(root_dir.rglob("metadata.json"))
    metadata_files.sort(key=lambda path: metadata_file_sort_key(path, descending=descending))
    return metadata_files


def process_file_batch(conn, file_path, force=False):
    cur = conn.cursor()
    filename = file_path.name
    folder_name = file_path.parent.name
    file_key = f"{folder_name}/{filename}"
    logging.info(f"Processing Batch: {file_key}")

    is_app_source = folder_name.upper().startswith("APP_") or "scraped" in folder_name.lower()
    is_bulletin_source = folder_name.upper().startswith("BLT_") or "BULTEN" in folder_name.upper()
    is_gazette_source = folder_name.upper().startswith("GZ_") or "GAZETE" in folder_name.upper()
    new_source_rank, source_tag = get_source_rank(folder_name)

    folder_gazette_no = None
    folder_gazette_date = None
    if is_gazette_source:
        parts = folder_name.split("_")
        if len(parts) >= 2:
            folder_gazette_no = parts[1]
        if len(parts) >= 3:
            folder_gazette_date = parse_date(parts[2])

    if not force and not is_app_source:
        cur.execute(
            "SELECT status, COALESCE(record_count, 0) FROM processed_files WHERE filename = %s",
            (file_key,),
        )
        row = cur.fetchone()
        if row and row[0] in ("success", "repaired"):
            logging.info("   -> Skipped (Already processed).")
            return {
                "status": "skipped",
                "filename": file_key,
                "inserted": 0,
                "updated": 0,
                "skipped": 1,
                "record_count": row[1],
                "error": None,
            }

    cur.execute(
        """
        INSERT INTO processed_files (filename, status, processed_at)
        VALUES (%s, 'processing', NOW())
        ON CONFLICT (filename) DO UPDATE
        SET status = 'processing', processed_at = NOW()
        """,
        (file_key,),
    )

    was_repaired = False
    try:
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError:
            repair = _repair_corrupt_metadata(file_path)
            if repair["status"] == "repaired":
                with open(file_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                was_repaired = True
            else:
                cur.execute(
                    "UPDATE processed_files SET status = %s, error_log = %s WHERE filename = %s",
                    (repair["status"], repair.get("error", ""), file_key),
                )
                conn.commit()
                return {
                    "status": repair["status"],
                    "filename": file_key,
                    "inserted": 0,
                    "updated": 0,
                    "skipped": 0,
                    "record_count": 0,
                    "error": repair.get("error"),
                }

        if not data:
            file_result_status = "repaired" if was_repaired else "success"
            cur.execute(
                "UPDATE processed_files SET status = %s, record_count = 0, error_log = NULL WHERE filename = %s",
                (file_result_status, file_key),
            )
            conn.commit()
            _legacy._file_index.clear()
            return {
                "status": file_result_status,
                "filename": file_key,
                "inserted": 0,
                "updated": 0,
                "skipped": 0,
                "record_count": 0,
                "error": None,
            }

        all_app_nos = [rec.get("APPLICATIONNO") for rec in data if rec.get("APPLICATIONNO")]
        existing_db_records = {}
        if all_app_nos:
            cur.execute(
                """
                SELECT application_no, id, last_event_date, current_status, expiry_date, status_source, name
                FROM trademarks
                WHERE application_no = ANY(%s)
                """,
                (all_app_nos,),
            )
            for row in cur.fetchall():
                existing_db_records[row[0]] = {
                    "id": row[1],
                    "last_date": row[2],
                    "status": _canonicalize_db_status(row[3]),
                    "expiry": row[4],
                    "status_source": row[5],
                    "name": row[6],
                }

        new_inserts = []
        updates = []
        history_inserts = []
        skipped_count = 0

        for rec in data:
            app_no = rec.get("APPLICATIONNO")
            if not app_no:
                continue

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
            if is_bulletin_source and bulletin_date_val:
                comparison_date = bulletin_date_val
            elif is_gazette_source and gazette_date_val:
                comparison_date = gazette_date_val
            elif db_status == DB_STATUS_REGISTERED and reg_date:
                comparison_date = reg_date
            elif db_status == DB_STATUS_PUBLISHED and bulletin_date_val:
                comparison_date = bulletin_date_val
            elif app_date:
                comparison_date = app_date
            if comparison_date is None and db_status == DB_STATUS_PUBLISHED and bulletin_date_val:
                comparison_date = bulletin_date_val
            db_write_date = comparison_date or datetime.now().date()

            new_expiry_date = None
            if app_date:
                try:
                    ten_year_date = app_date.replace(year=app_date.year + 10)
                except ValueError:
                    ten_year_date = app_date + timedelta(days=3652)
                new_expiry_date = ten_year_date + timedelta(days=183)

            appeal_dl = None
            if bulletin_date_val and (db_status == DB_STATUS_PUBLISHED or is_bulletin_source):
                appeal_dl = calculate_appeal_deadline(bulletin_date_val)

            img_emb = embedding_to_halfvec(rec.get("image_embedding"), 512)
            dino_emb = embedding_to_halfvec(rec.get("dinov2_embedding"), 768)
            txt_emb = embedding_to_halfvec(rec.get("text_embedding"), 384)
            color_emb = embedding_to_halfvec(rec.get("color_histogram"), 512)
            img_path = _resolve_image_path(folder_name, rec.get("IMAGE"), ROOT_DIR)
            ocr_text = rec.get("logo_ocr_text")

            name_tr = _trunc(rec.get("name_tr"), 500)
            detected_lang = _trunc(rec.get("detected_lang"), 10)
            name_tr_backend = _trunc(rec.get("name_tr_backend"), 32)
            name_tr_model = _trunc(rec.get("name_tr_model"), 255)
            name_tr_updated_at_raw = rec.get("name_tr_updated_at")
            name_tr_updated_at = None
            if name_tr_updated_at_raw:
                try:
                    normalized_updated_at = str(name_tr_updated_at_raw).replace("Z", "+00:00")
                    name_tr_updated_at = datetime.fromisoformat(normalized_updated_at)
                except ValueError:
                    name_tr_updated_at = None

            holders_list = rec.get("HOLDERS", [])
            holder_name, holder_tpe_client_id = None, None
            if holders_list:
                raw_title = holders_list[0].get("TITLE", "")
                existing_tpe = holders_list[0].get("TPECLIENTID", "")
                holder_clean, extracted_id = extract_tpe_id(raw_title)
                holder_name = _trunc(holder_clean, 500)
                holder_tpe_client_id = _trunc(existing_tpe or extracted_id, 50)

            attorneys_list = rec.get("ATTORNEYS", [])
            attorney_name, attorney_no = None, None
            if attorneys_list:
                raw_name = attorneys_list[0].get("NAME", "")
                existing_no = attorneys_list[0].get("NO", "")
                atty_clean, extracted_id = extract_tpe_id(raw_name)
                attorney_name = _trunc(atty_clean, 500)
                attorney_no = _trunc(existing_no or extracted_id, 50)

            reg_no = _trunc(reg_no_val, 255)
            wipo_no = _trunc(tm.get("INTREGNO"), 255)
            clean_classes_list = [int(c) for c in tm.get("NICECLASSES_LIST", []) if str(c).strip().isdigit()]
            vienna_classes = [int(c) for c in tm.get("VIENNACLASSES_LIST", []) if str(c).strip().isdigit()]
            raw_extracted = rec.get("EXTRACTEDGOODS")
            extracted_goods_data = raw_extracted if raw_extracted else None

            existing = existing_db_records.get(app_no)
            if not existing:
                insert_bulletin_no = tm.get("BULLETIN_NO") if is_bulletin_source else None
                insert_bulletin_date = bulletin_date_val if is_bulletin_source else None
                new_inserts.append(
                    (
                        app_no,
                        sanitize(tm_name),
                        db_status,
                        clean_classes_list or None,
                        Json(extracted_goods_data) if extracted_goods_data else None,
                        reg_no,
                        wipo_no,
                        vienna_classes or None,
                        app_date,
                        reg_date,
                        db_write_date,
                        sanitize(insert_bulletin_no),
                        insert_bulletin_date,
                        sanitize(folder_gazette_no if is_gazette_source else tm.get("GAZETTE_NO")),
                        gazette_date_val,
                        appeal_dl,
                        new_expiry_date,
                        img_path,
                        img_emb,
                        dino_emb,
                        txt_emb,
                        color_emb,
                        sanitize(ocr_text),
                        name_tr,
                        detected_lang,
                        name_tr_backend,
                        name_tr_model,
                        name_tr_updated_at,
                        holder_name,
                        holder_tpe_client_id,
                        attorney_name,
                        attorney_no,
                        source_tag,
                    )
                )
                continue

            curr_status = existing["status"]
            existing_source = existing.get("status_source") or "BLT"
            old_source_rank = {"APP": 3, "GZ": 2}.get(existing_source, 1)
            clear_name = (
                _name_cleans_to_empty(raw_tm_name)
                and _name_cleans_to_empty(existing.get("name"))
            )
            should_update = force
            next_status = db_status

            if (
                existing_source == "APP"
                and curr_status == DB_STATUS_APPLIED
                and not is_app_source
                and db_status != DB_STATUS_APPLIED
            ):
                should_update = True
                next_status = db_status
            elif new_source_rank >= old_source_rank:
                should_update = True
                if is_app_source and (
                    db_status == DB_STATUS_APPLIED or explicit_status is None
                ):
                    next_status = curr_status
            else:
                should_update = True
                next_status = curr_status

            if not should_update:
                skipped_count += 1
                continue

            is_renewal = False
            if curr_status in [DB_STATUS_REGISTERED, DB_STATUS_RENEWED, DB_STATUS_EXPIRED] and next_status == DB_STATUS_REGISTERED:
                if existing["expiry"] and new_expiry_date and new_expiry_date > existing["expiry"]:
                    next_status = DB_STATUS_RENEWED
                    is_renewal = True

            next_source_tag = existing_source if (next_status == curr_status and not is_renewal) else source_tag
            updates.append(
                (
                    sanitize(tm_name),
                    clear_name,
                    next_status,
                    clean_classes_list or None,
                    Json(extracted_goods_data) if extracted_goods_data else None,
                    db_write_date,
                    appeal_dl,
                    new_expiry_date,
                    sanitize(tm.get("BULLETIN_NO")),
                    bulletin_date_val,
                    sanitize(folder_gazette_no if is_gazette_source else tm.get("GAZETTE_NO")),
                    gazette_date_val,
                    img_path,
                    app_date,
                    reg_date,
                    img_emb,
                    dino_emb,
                    txt_emb,
                    color_emb,
                    sanitize(ocr_text),
                    name_tr,
                    detected_lang,
                    name_tr_backend,
                    name_tr_model,
                    name_tr_updated_at,
                    holder_name,
                    holder_tpe_client_id,
                    attorney_name,
                    attorney_no,
                    next_source_tag,
                    reg_no,
                    wipo_no,
                    vienna_classes or None,
                    app_no,
                )
            )
            if curr_status != next_status or is_renewal:
                history_inserts.append(
                    (
                        existing["id"],
                        db_write_date,
                        "STATUS_CHANGE" if not is_renewal else "RENEWAL",
                        filename,
                        f"{curr_status} -> {next_status}",
                    )
                )

        if new_inserts:
            deduped = {}
            for index, row in enumerate(new_inserts):
                deduped[row[0]] = index
            if len(deduped) < len(new_inserts):
                new_inserts = [new_inserts[i] for i in sorted(deduped.values())]
            execute_values(cur, _build_insert_sql(), new_inserts)

        if updates:
            deduped = {}
            for index, row in enumerate(updates):
                deduped[row[-1]] = index
            if len(deduped) < len(updates):
                updates = [updates[i] for i in sorted(deduped.values())]
            update_sql = _build_update_sql("APP" if is_app_source else ("GZ" if is_gazette_source else "BLT"))
            execute_values(cur, update_sql, updates)

        if history_inserts:
            try:
                cur.execute("SAVEPOINT before_history")
                execute_values(
                    cur,
                    """
                    INSERT INTO trademark_history (trademark_id, event_date, event_type, source_file, description)
                    VALUES %s
                    ON CONFLICT DO NOTHING
                    """,
                    history_inserts,
                )
                cur.execute("RELEASE SAVEPOINT before_history")
            except Exception as hist_err:
                cur.execute("ROLLBACK TO SAVEPOINT before_history")
                logging.warning(f"   History insert skipped: {hist_err}")

        file_result_status = "repaired" if was_repaired else "success"
        cur.execute(
            "UPDATE processed_files SET status = %s, record_count = %s, error_log = NULL WHERE filename = %s",
            (file_result_status, len(new_inserts) + len(updates), file_key),
        )
        conn.commit()
        _legacy._file_index.clear()

        batch_app_nos = [row[0] for row in new_inserts] + [row[-1] for row in updates]
        if batch_app_nos:
            try:
                from utils.status_reconciler import update_final_status_batch

                update_final_status_batch(conn, app_nos=batch_app_nos)
            except Exception:
                pass

        if new_inserts:
            cur.execute("SELECT id FROM trademarks WHERE application_no = ANY(%s)", ([row[0] for row in new_inserts],))
            new_trademark_ids = [row[0] for row in cur.fetchall()]
            if new_trademark_ids:
                try:
                    from watchlist.scanner import trigger_watchlist_scan

                    trigger_watchlist_scan(
                        new_trademark_ids,
                        "bulletin" if is_bulletin_source else ("gazette" if is_gazette_source else "application"),
                        folder_name,
                    )
                except Exception:
                    pass
            if new_trademark_ids and is_bulletin_source:
                queue_bulletin_no, queue_bulletin_date = extract_bulletin_info(folder_name)
                add_to_scan_queue(
                    conn=conn,
                    trademark_ids=new_trademark_ids,
                    bulletin_no=queue_bulletin_no,
                    bulletin_date=queue_bulletin_date,
                    priority=1,
                )

        logging.info(f"   Batch Complete. {len(new_inserts)} Ins, {len(updates)} Upd, {skipped_count} Skip.")
        return {
            "status": file_result_status,
            "filename": file_key,
            "inserted": len(new_inserts),
            "updated": len(updates),
            "skipped": skipped_count,
            "record_count": len(data),
            "error": None,
        }
    except Exception as exc:
        conn.rollback()
        logging.error(f"   Batch Failed: {exc}")
        cur.execute("UPDATE processed_files SET status = 'failed', error_log = %s WHERE filename = %s", (str(exc), file_key))
        conn.commit()
        _legacy._file_index.clear()
        return {
            "status": "failed",
            "filename": file_key,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "record_count": 0,
            "error": str(exc),
        }


def _process_metadata_files(conn, metadata_files, force=False):
    summary = {
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "processed_files": 0,
        "failed_files": 0,
        "file_results": [],
    }
    for json_file in metadata_files:
        result = process_file_batch(conn, json_file, force)
        if result is None:
            continue
        summary["file_results"].append(result)
        summary["processed_files"] += 1
        summary["inserted"] += result.get("inserted", 0)
        summary["updated"] += result.get("updated", 0)
        summary["skipped"] += result.get("skipped", 0)
        if result.get("status") == "failed":
            summary["failed_files"] += 1
    return summary


def cleanup_sekil_names(conn, batch_size: int = 5000) -> int:
    """Remove placeholder-only 'sekil' values and clean mixed name variants."""
    cur = conn.cursor()
    cleaned_total = 0
    cur.execute(
        """
        SELECT id, name
        FROM trademarks
        WHERE name IS NOT NULL
          AND (
              lower(name) LIKE '%sekil%'
              OR name ~* '(s|ş)ek(i|ı|İ)l'
          )
        """
    )

    rows = cur.fetchall()
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        updates = []
        for tm_id, current_name in batch:
            cleaned = clean_name(current_name)
            if cleaned != current_name:
                updates.append((str(tm_id), sanitize(cleaned)))

        if updates:
            execute_values(
                cur,
                """
                UPDATE trademarks AS tm
                SET name = v.name::text,
                    updated_at = NOW()
                FROM (VALUES %s) AS v(id, name)
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


def _run_ingest(force=False, root_dir: Path | None = None, folder_name: str | None = None, descending: bool = True):
    if root_dir is not None:
        set_root_dir(root_dir)

    started = time.time()
    repair_stats = {"repaired": [], "unrecoverable": [], "regen_failed": []}
    conn = None
    try:
        conn = get_connection()
        _bootstrap.assert_ingest_runtime_ready(conn)
        if folder_name is None:
            repair_stats = pre_scan_and_repair(ROOT_DIR)
        metadata_files = _collect_metadata_files(ROOT_DIR, folder_name=folder_name, descending=descending)
        logging.info(f"Found {len(metadata_files)} files.")
        result = _process_metadata_files(conn, metadata_files, force=force)
        result["name_cleaned"] = cleanup_sekil_names(conn)
        duration = time.time() - started
        _print_repair_summary(repair_stats)
        if result["name_cleaned"]:
            logging.info("Cleaned %s trademark name placeholder(s)", result["name_cleaned"])
        logging.info(f"Ingestion complete in {duration:.1f}s")
        result.update(
            {
                "duration_seconds": round(duration, 1),
                "repair_stats": repair_stats,
            }
        )
        return result
    finally:
        if conn:
            release_connection(conn)


def run_ingest(force=False, settings=None) -> dict:
    root_dir = ROOT_DIR
    if settings is not None:
        root_dir = Path(settings.bulletins_root)
    return _run_ingest(force=force, root_dir=root_dir, descending=True)


def run_ingest_cli(force=False, folder_name=None, settings=None):
    root_dir = ROOT_DIR
    if settings is not None:
        root_dir = Path(settings.bulletins_root)
    return _run_ingest(force=force, root_dir=root_dir, folder_name=folder_name, descending=False)


def main():
    parser = argparse.ArgumentParser(description="Ingest trademark data (10M Scale).")
    parser.add_argument("--force", action="store_true", help="Force re-processing.")
    parser.add_argument("--folder", type=str, help="Process only this folder name (e.g. GZ_300).")
    args = parser.parse_args()
    run_ingest_cli(force=args.force, folder_name=args.folder)


__all__ = [
    "ROOT_DIR",
    "get_db_connection",
    "set_root_dir",
    "sanitize",
    "_trunc",
    "embedding_to_halfvec",
    "clean_name",
    "_name_cleans_to_empty",
    "extract_tpe_id",
    "_build_file_index",
    "_resolve_image_path",
    "_has_tmbulletin_source",
    "_repair_corrupt_metadata",
    "pre_scan_and_repair",
    "_print_repair_summary",
    "_check_scan_queue_table",
    "add_to_scan_queue",
    "DB_STATUS_APPLIED",
    "DB_STATUS_PUBLISHED",
    "DB_STATUS_OPPOSED",
    "DB_STATUS_REGISTERED",
    "DB_STATUS_REFUSED",
    "DB_STATUS_WITHDRAWN",
    "DB_STATUS_TRANSFERRED",
    "DB_STATUS_RENEWED",
    "DB_STATUS_PARTIAL_REFUSAL",
    "DB_STATUS_EXPIRED",
    "DB_STATUS_UNKNOWN",
    "DB_STATUS_CANCELLED",
    "_canonicalize_db_status",
    "parse_date",
    "calculate_expiration_status",
    "extract_bulletin_info",
    "_explicit_db_status_from_text",
    "_determine_db_status_raw",
    "determine_db_status",
    "determine_status",
    "get_status_rank",
    "get_source_rank",
    "_SHARED_FIELDS",
    "_SUSPICIOUS_SIX_FIELDS",
    "_BLT_OWNED_FIELDS",
    "_GZ_OWNED_FIELDS",
    "_priority_coalesce",
    "_suspicious_six_coalesce",
    "_owned_field",
    "_build_update_set",
    "_build_update_sql",
    "check_and_migrate_schema",
    "load_nice_classes",
    "_INSERT_COLUMNS",
    "_build_insert_sql",
    "metadata_file_sort_key",
    "process_file_batch",
    "_collect_metadata_files",
    "_process_metadata_files",
    "cleanup_sekil_names",
    "cleanup_applied_publication_statuses",
    "_run_ingest",
    "run_ingest",
    "run_ingest_cli",
    "main",
]


if __name__ == "__main__":
    try:
        main()
    finally:
        close_pool()

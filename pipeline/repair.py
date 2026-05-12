"""General post-ingest database repair routines."""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from psycopg2.extras import RealDictCursor, execute_values

from db.pool import close_pool, get_connection, release_connection
from pipeline import ingest_bootstrap as _bootstrap
from pipeline.ingest_rules import (
    DB_STATUS_APPLIED,
    DB_STATUS_PUBLISHED,
    DB_STATUS_REGISTERED,
    DB_STATUS_REFUSED,
    _explicit_db_status_from_text,
    _repair_mojibake,
    clean_name,
)
from pipeline.status_repair import run_status_repair

logger = logging.getLogger(__name__)

_SEKIL_WORD_RE = re.compile(
    r"(?:\+\s*)?\b(?:s|\u015f)ek(?:i|\u0131|\u0130)l\b",
    re.IGNORECASE,
)
_TRAILING_ATTACHED_SEKIL_RE = re.compile(
    r"(?P<prefix>.*[0-9A-Za-z\u00c0-\u024f])(?:s|\u015f)ek(?:i|\u0131|\u0130)l\s*$",
    re.IGNORECASE,
)
_EMPTY_PARENS_RE = re.compile(r"\(\s*\)")
_TRAILING_SEPARATOR_RE = re.compile(r"[\s+&/\\,;:._-]+$")
_SEKIL_LIKE_PATTERNS = ["%sekil%", "%\u015fekil%", "%sek\u0131l%", "%\u015fek\u0131l%"]
_NAME_REPAIR_FIELDS = {"name", "name_tr"}
_TRANSLATION_TEXT_FIELDS = ("name_tr", "detected_lang", "name_tr_backend", "name_tr_model", "name_tr_updated_at")
_SOURCE_FOLDER_RE = re.compile(r"^(BLT|GZ)_(\d+)(?:\D|$)", re.IGNORECASE)
_APP_NO_LINE_RE = re.compile(r'^\s+"APPLICATIONNO":\s+"(?P<value>.*?)",?\s*$')
_NICE_RAW_LINE_RE = re.compile(r'^\s+"NICECLASSES_RAW":\s+"(?P<value>.*?)",?\s*$')
_NICE_LIST_START_RE = re.compile(r'^\s+"NICECLASSES_LIST":\s+\[\s*$')
_NICE_LIST_INLINE_RE = re.compile(r'^\s+"NICECLASSES_LIST":\s+\[(?P<value>.*?)\],?\s*$')
_NICE_LIST_VALUE_RE = re.compile(r'^\s+"?(?P<value>\d{1,3})"?,?\s*$')
_NICE_CLASS_RE = re.compile(r"\d{1,3}")
_LIVE_CHECK_SUCCESS_CODES = {
    "updated",
    "confirmed",
    "no_decision",
    "no_exact_match",
    "classes_not_richer",
}
_LIVE_PRIORITY_WINDOW_YEARS = 11
_LIVE_STATUS_RECENT_THRESHOLD = "4 months"
_LIVE_PROVISIONAL_REFUSAL_THRESHOLD = "1 year"
LIVE_PROVISIONAL_SOURCE = "LIVE_PROV"
_LIVE_CHECK_TABLE_SQL = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE TABLE IF NOT EXISTS repair_live_trademark_checks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trademark_id UUID REFERENCES trademarks(id) ON DELETE CASCADE,
    application_no VARCHAR(255) NOT NULL,
    check_kind VARCHAR(20) NOT NULL,
    query_text TEXT,
    result_code VARCHAR(50) NOT NULL,
    live_status_text TEXT,
    live_registration_no TEXT,
    resolved_status TEXT,
    live_nice_classes INTEGER[],
    artifact_dir TEXT,
    error TEXT,
    checked_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (application_no, check_kind)
);
ALTER TABLE repair_live_trademark_checks
    ADD COLUMN IF NOT EXISTS live_registration_no TEXT;
CREATE INDEX IF NOT EXISTS idx_repair_live_checks_kind_result
    ON repair_live_trademark_checks(check_kind, result_code);
CREATE INDEX IF NOT EXISTS idx_repair_live_checks_checked_at
    ON repair_live_trademark_checks(checked_at DESC);
"""
_LIVE_PROVISIONAL_TABLE_SQL = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE TABLE IF NOT EXISTS repair_live_provisional_status_marks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trademark_id UUID REFERENCES trademarks(id) ON DELETE CASCADE,
    application_no VARCHAR(255) NOT NULL UNIQUE,
    previous_status tm_status,
    previous_status_source VARCHAR(255),
    previous_final_status tm_status,
    previous_final_status_source VARCHAR(255),
    previous_final_status_at DATE,
    marked_status tm_status NOT NULL DEFAULT 'Reddedildi',
    marked_source VARCHAR(255) NOT NULL DEFAULT 'LIVE_PROV',
    marked_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_repair_live_provisional_marked_at
    ON repair_live_provisional_status_marks(marked_at DESC);
"""


def _repair_db_name(raw_name):
    raw_text = _repair_mojibake(str(raw_name)) if raw_name else ""
    word_removed = bool(_SEKIL_WORD_RE.search(raw_text))
    repaired_name = clean_name(raw_name)
    if not repaired_name:
        return repaired_name

    before_attached = repaired_name
    repaired_name = _TRAILING_ATTACHED_SEKIL_RE.sub(r"\g<prefix>", repaired_name)
    attached_removed = repaired_name != before_attached
    if word_removed or attached_removed:
        repaired_name = _EMPTY_PARENS_RE.sub("", repaired_name)
        repaired_name = _TRAILING_SEPARATOR_RE.sub("", repaired_name)
    repaired_name = " ".join(repaired_name.split())
    return repaired_name if repaired_name else None


def _has_repairable_text_value(value: Any) -> bool:
    return bool(clean_name(value))


def _name_field_repair_candidates(
    conn,
    *,
    field_name: str,
    app_no: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    if field_name not in _NAME_REPAIR_FIELDS:
        raise ValueError(f"Unsupported name repair field: {field_name}")

    cur = conn.cursor(cursor_factory=RealDictCursor)
    params: list[Any] = [_SEKIL_LIKE_PATTERNS]
    filters = [
        f"{field_name} IS NOT NULL",
        f"lower({field_name}) LIKE ANY(%s::text[])",
    ]
    if app_no:
        filters.append("application_no = %s")
        params.append(app_no)

    limit_clause = ""
    if limit:
        limit_clause = "LIMIT %s"
        params.append(limit)

    cur.execute(
        f"""
        SELECT id, application_no, name, name_tr
        FROM trademarks
        WHERE {" AND ".join(filters)}
        ORDER BY application_no NULLS LAST
        {limit_clause}
        """,
        params,
    )
    return [dict(row) for row in cur.fetchall()]


def _logo_only_text_feature_candidates(
    conn,
    *,
    app_no: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    params: list[Any] = []
    filters = [
        "COALESCE(NULLIF(BTRIM(name), ''), NULLIF(BTRIM(name_tr), '')) IS NULL",
        """(
            detected_lang IS NOT NULL
            OR name_tr_backend IS NOT NULL
            OR name_tr_model IS NOT NULL
            OR name_tr_updated_at IS NOT NULL
        )""",
    ]
    if app_no:
        filters.append("application_no = %s")
        params.append(app_no)

    limit_clause = ""
    if limit:
        limit_clause = "LIMIT %s"
        params.append(limit)

    cur.execute(
        f"""
        SELECT id, application_no
        FROM trademarks
        WHERE {" AND ".join(filters)}
        ORDER BY application_no NULLS LAST
        {limit_clause}
        """,
        params,
    )
    return [dict(row) for row in cur.fetchall()]


def _name_repair_candidates(conn, *, app_no: str | None = None, limit: int | None = None) -> list[dict]:
    return _name_field_repair_candidates(conn, field_name="name", app_no=app_no, limit=limit)


def _name_tr_repair_candidates(conn, *, app_no: str | None = None, limit: int | None = None) -> list[dict]:
    return _name_field_repair_candidates(conn, field_name="name_tr", app_no=app_no, limit=limit)


def _run_name_field_repair(
    *,
    conn,
    field_name: str,
    candidate_loader,
    dry_run: bool = False,
    app_no: str | None = None,
    limit: int | None = None,
) -> dict:
    if field_name not in _NAME_REPAIR_FIELDS:
        raise ValueError(f"Unsupported name repair field: {field_name}")

    candidates = candidate_loader(conn, app_no=app_no, limit=limit)
    decisions = []
    for row in candidates:
        current_value = row[field_name]
        repaired_value = _repair_db_name(current_value)
        if repaired_value != current_value:
            repaired_name = repaired_value if field_name == "name" else row.get("name")
            repaired_name_tr = repaired_value if field_name == "name_tr" else row.get("name_tr")
            logo_only_after_repair = (
                not _has_repairable_text_value(repaired_name)
                and not _has_repairable_text_value(repaired_name_tr)
            )
            decisions.append(
                {
                    "id": str(row["id"]),
                    "application_no": row["application_no"],
                    "from": current_value,
                    "to": repaired_value,
                    "clear_translation_features": True,
                }
            )

    if not dry_run and decisions:
        cur = conn.cursor()
        if field_name == "name":
            update_sql = """
            UPDATE trademarks AS tm
            SET name = v.value::text,
                name_tr = CASE WHEN v.clear_translation_features THEN NULL ELSE tm.name_tr END,
                detected_lang = CASE WHEN v.clear_translation_features THEN NULL ELSE tm.detected_lang END,
                name_tr_backend = CASE WHEN v.clear_translation_features THEN NULL ELSE tm.name_tr_backend END,
                name_tr_model = CASE WHEN v.clear_translation_features THEN NULL ELSE tm.name_tr_model END,
                name_tr_updated_at = CASE WHEN v.clear_translation_features THEN NULL ELSE tm.name_tr_updated_at END,
                updated_at = NOW()
            FROM (VALUES %s) AS v(id, value, clear_translation_features)
            WHERE tm.id = v.id::uuid
            """
        else:
            update_sql = """
            UPDATE trademarks AS tm
            SET name_tr = v.value::text,
                detected_lang = CASE WHEN v.clear_translation_features THEN NULL ELSE tm.detected_lang END,
                name_tr_backend = CASE WHEN v.clear_translation_features THEN NULL ELSE tm.name_tr_backend END,
                name_tr_model = CASE WHEN v.clear_translation_features THEN NULL ELSE tm.name_tr_model END,
                name_tr_updated_at = CASE WHEN v.clear_translation_features THEN NULL ELSE tm.name_tr_updated_at END,
                updated_at = NOW()
            FROM (VALUES %s) AS v(id, value, clear_translation_features)
            WHERE tm.id = v.id::uuid
            """
        execute_values(
            cur,
            update_sql,
            [
                (
                    decision["id"],
                    decision["to"],
                    decision["clear_translation_features"],
                )
                for decision in decisions
            ],
        )
        conn.commit()

    samples = [
        {
            "id": decision["id"],
            "application_no": decision["application_no"],
            "from": decision["from"],
            "to": decision["to"],
        }
        for decision in decisions[:20]
    ]

    return {
        "status": "success",
        "dry_run": dry_run,
        "candidates": len(candidates),
        "decisions": len(decisions),
        "repaired": 0 if dry_run else len(decisions),
        "would_repair": len(decisions) if dry_run else 0,
        "text_embeddings_cleared": 0,
        "would_clear_text_embeddings": 0,
        "samples": samples,
    }


def run_name_repair(
    *,
    conn,
    dry_run: bool = False,
    app_no: str | None = None,
    limit: int | None = None,
) -> dict:
    return _run_name_field_repair(
        conn=conn,
        field_name="name",
        candidate_loader=_name_repair_candidates,
        dry_run=dry_run,
        app_no=app_no,
        limit=limit,
    )


def run_name_tr_repair(
    *,
    conn,
    dry_run: bool = False,
    app_no: str | None = None,
    limit: int | None = None,
) -> dict:
    return _run_name_field_repair(
        conn=conn,
        field_name="name_tr",
        candidate_loader=_name_tr_repair_candidates,
        dry_run=dry_run,
        app_no=app_no,
        limit=limit,
    )


def run_logo_only_text_feature_repair(
    *,
    conn,
    dry_run: bool = False,
    app_no: str | None = None,
    limit: int | None = None,
) -> dict:
    candidates = _logo_only_text_feature_candidates(conn, app_no=app_no, limit=limit)
    decisions = [
        {
            "id": str(row["id"]),
            "application_no": row["application_no"],
            "clear_translation_features": True,
        }
        for row in candidates
    ]

    if not dry_run and decisions:
        cur = conn.cursor()
        execute_values(
            cur,
            """
            UPDATE trademarks AS tm
            SET name_tr = NULL,
                detected_lang = NULL,
                name_tr_backend = NULL,
                name_tr_model = NULL,
                name_tr_updated_at = NULL,
                updated_at = NOW()
            FROM (VALUES %s) AS v(id)
            WHERE tm.id = v.id::uuid
            """,
            [(decision["id"],) for decision in decisions],
        )
        conn.commit()

    return {
        "status": "success",
        "dry_run": dry_run,
        "candidates": len(candidates),
        "decisions": len(decisions),
        "repaired": 0 if dry_run else len(decisions),
        "would_repair": len(decisions) if dry_run else 0,
        "text_embeddings_cleared": 0,
        "would_clear_text_embeddings": 0,
        "samples": decisions[:20],
    }


def _normalize_nice_classes(values: Iterable[Any] | Any) -> list[int]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        raw_values = _NICE_CLASS_RE.findall(str(values))
    else:
        raw_values = []
        for value in values:
            raw_values.extend(_NICE_CLASS_RE.findall(str(value)))
    classes = {int(value) for value in raw_values if value.isdigit() and 1 <= int(value) <= 45}
    return sorted(classes)


def _parse_live_detail_nice_classes(detail_text: str) -> list[int]:
    """Extract Nice Sınıfları values from live DETAY text."""
    if not detail_text:
        return []

    text = html.unescape(str(detail_text))
    text = re.sub(r"\r\n?", "\n", text)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    def normalize_label(value: str) -> str:
        return (
            value.lower()
            .replace("Ä±", "i")
            .replace("Ä°", "i")
            .replace("ÅŸ", "s")
            .replace("Åž", "s")
            .replace("\u0131", "i")
            .replace("\u0130", "i")
            .replace("\u015f", "s")
            .replace("\u015e", "s")
            .replace("\u00fc", "u")
            .replace("\u00dc", "u")
        )

    normalized_lines = [normalize_label(line) for line in lines]
    start_index = 0
    end_index = len(lines)
    for index, normalized in enumerate(normalized_lines):
        if "marka bilgileri" in normalized:
            start_index = index
            break
    for index in range(start_index, len(lines)):
        if "mal ve hizmet bilgileri" in normalized_lines[index]:
            end_index = index
            break

    label_re = re.compile(r"nice\s+s(?:ı|i|Ä±)n(?:ı|i|Ä±)flar(?:ı|i|Ä±)", flags=re.IGNORECASE)
    for index in range(start_index, end_index):
        normalized = normalized_lines[index]
        if "nice siniflari" not in normalized:
            continue
        if "islem" in normalized and "sekil" in normalized:
            continue
        same_line = lines[index]
        inline = label_re.split(same_line, maxsplit=1)
        candidates = []
        if len(inline) > 1:
            candidates.append(re.split(r"\bT(?:ü|u|Ã¼)r(?:ü|u|Ã¼)\b", inline[-1], maxsplit=1, flags=re.IGNORECASE)[0])
        candidates.extend(lines[index + 1:min(index + 4, end_index)])
        for candidate in candidates:
            if "/" not in candidate and len(re.findall(r"\d{1,3}", candidate)) == 1:
                continue
            classes = _normalize_nice_classes(candidate)
            if classes:
                return classes

    match = re.search(
        r"nice\s+s(?:ı|i|Ä±)n(?:ı|i|Ä±)flar(?:ı|i|Ä±)\s*[:\-]?\s*([0-9\s/.,;]+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return []
    return _normalize_nice_classes(match.group(1))


def _class_repair_candidates(conn, *, app_no: str | None = None, limit: int | None = None) -> list[dict]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    params: list[Any] = []
    filters = ["cardinality(nice_class_numbers) = 6"]
    if app_no:
        filters.append("application_no = %s")
        params.append(app_no)

    limit_clause = ""
    if limit:
        limit_clause = "LIMIT %s"
        params.append(limit)

    cur.execute(
        f"""
        SELECT id, application_no, nice_class_numbers, bulletin_no, gazette_no
        FROM trademarks
        WHERE {" AND ".join(filters)}
        ORDER BY application_no NULLS LAST
        {limit_clause}
        """,
        params,
    )
    return [dict(row) for row in cur.fetchall()]


def _metadata_folder_sort_key(path: Path) -> tuple[int, str, int, str]:
    name = path.parent.name
    source_number = 0
    date_text = ""
    suffix_number = 0

    source_match = _SOURCE_FOLDER_RE.match(name)
    if source_match:
        source_number = int(source_match.group(2))

    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    if date_match:
        date_text = date_match.group(1)

    suffix_match = re.search(r"_(\d+)$", name)
    if suffix_match:
        suffix_number = int(suffix_match.group(1))

    return source_number, date_text, suffix_number, name


def _collect_source_metadata_files(root_dir: Path, source_numbers: dict[str, set[str]]) -> dict[tuple[str, str], list[Path]]:
    files: dict[tuple[str, str], list[Path]] = {}
    for metadata_path in root_dir.rglob("metadata.json"):
        folder_name = metadata_path.parent.name
        match = _SOURCE_FOLDER_RE.match(folder_name)
        if not match:
            continue
        source = match.group(1).upper()
        number = match.group(2)
        if number not in source_numbers.get(source, set()):
            continue
        files.setdefault((source, number), []).append(metadata_path)

    for paths in files.values():
        paths.sort(key=_metadata_folder_sort_key)
    return files


def _iter_metadata_class_records(metadata_path: Path, candidate_app_nos: set[str]):
    current_app = None
    is_candidate = False
    nice_raw = ""
    in_nice_list = False
    nice_list: list[str] = []

    with metadata_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            app_match = _APP_NO_LINE_RE.match(line)
            if app_match:
                current_app = app_match.group("value")
                is_candidate = current_app in candidate_app_nos
                nice_raw = ""
                in_nice_list = False
                nice_list = []
                continue

            if not is_candidate:
                continue

            raw_match = _NICE_RAW_LINE_RE.match(line)
            if raw_match:
                nice_raw = raw_match.group("value")
                continue

            inline_match = _NICE_LIST_INLINE_RE.match(line)
            if inline_match:
                normalized = _normalize_nice_classes(inline_match.group("value"))
                if not normalized:
                    normalized = _normalize_nice_classes(nice_raw)
                yield current_app, normalized
                is_candidate = False
                continue

            if _NICE_LIST_START_RE.match(line):
                in_nice_list = True
                nice_list = []
                continue

            if in_nice_list:
                if "]" in line:
                    normalized = _normalize_nice_classes(nice_list)
                    if not normalized:
                        normalized = _normalize_nice_classes(nice_raw)
                    yield current_app, normalized
                    in_nice_list = False
                    is_candidate = False
                    continue

                value_match = _NICE_LIST_VALUE_RE.match(line)
                if value_match:
                    nice_list.append(value_match.group("value"))

            elif nice_raw and (line.lstrip().startswith('"') or line.lstrip().startswith("}")):
                normalized = _normalize_nice_classes(nice_raw)
                yield current_app, normalized
                is_candidate = False


def _class_evidence_wins(current: dict | None, candidate: dict) -> bool:
    if current is None:
        return True
    current_source_rank = 2 if current["source"] == "GZ" else 1
    candidate_source_rank = 2 if candidate["source"] == "GZ" else 1
    if candidate_source_rank != current_source_rank:
        return candidate_source_rank > current_source_rank
    if len(candidate["classes"]) != len(current["classes"]):
        return len(candidate["classes"]) > len(current["classes"])
    return candidate["folder_key"] > current["folder_key"]


def run_class_repair(
    *,
    conn,
    root_dir: Path | str | None = None,
    dry_run: bool = False,
    app_no: str | None = None,
    limit: int | None = None,
) -> dict:
    root_dir = Path(root_dir) if root_dir is not None else _bootstrap.default_ingest_root()
    candidates = _class_repair_candidates(conn, app_no=app_no, limit=limit)
    candidates_by_app = {row["application_no"]: row for row in candidates}
    app_nos_by_source_number: dict[tuple[str, str], set[str]] = {}
    source_numbers: dict[str, set[str]] = {"BLT": set(), "GZ": set()}

    for row in candidates:
        if row.get("bulletin_no"):
            number = str(row["bulletin_no"]).strip()
            source_numbers["BLT"].add(number)
            app_nos_by_source_number.setdefault(("BLT", number), set()).add(row["application_no"])
        if row.get("gazette_no"):
            number = str(row["gazette_no"]).strip()
            source_numbers["GZ"].add(number)
            app_nos_by_source_number.setdefault(("GZ", number), set()).add(row["application_no"])

    metadata_files = _collect_source_metadata_files(root_dir, source_numbers) if candidates else {}
    evidence_by_app: dict[str, dict] = {}
    metadata_matches = 0
    source_files_scanned = 0

    for source_key, paths in metadata_files.items():
        candidate_app_nos = app_nos_by_source_number.get(source_key, set())
        if not candidate_app_nos:
            continue
        for metadata_path in paths:
            source_files_scanned += 1
            folder_key = _metadata_folder_sort_key(metadata_path)
            for matched_app_no, classes in _iter_metadata_class_records(metadata_path, candidate_app_nos):
                metadata_matches += 1
                if len(classes) <= 6:
                    continue
                evidence = {
                    "source": source_key[0],
                    "classes": classes,
                    "source_file": str(metadata_path),
                    "folder_key": folder_key,
                }
                if _class_evidence_wins(evidence_by_app.get(matched_app_no), evidence):
                    evidence_by_app[matched_app_no] = evidence

    decisions = []
    for app_no_key, evidence in sorted(evidence_by_app.items()):
        row = candidates_by_app.get(app_no_key)
        if not row:
            continue
        existing_classes = _normalize_nice_classes(row.get("nice_class_numbers"))
        if evidence["classes"] == existing_classes:
            continue
        decisions.append(
            {
                "id": str(row["id"]),
                "application_no": app_no_key,
                "from": row["nice_class_numbers"],
                "to": evidence["classes"],
                "source": evidence["source"],
                "source_file": evidence["source_file"],
            }
        )

    missing_source_rows = 0
    for row in candidates:
        has_source_file = False
        if row.get("bulletin_no") and ("BLT", str(row["bulletin_no"]).strip()) in metadata_files:
            has_source_file = True
        if row.get("gazette_no") and ("GZ", str(row["gazette_no"]).strip()) in metadata_files:
            has_source_file = True
        if not has_source_file:
            missing_source_rows += 1

    if not dry_run and decisions:
        cur = conn.cursor()
        execute_values(
            cur,
            """
            UPDATE trademarks AS tm
            SET nice_class_numbers = v.nice_classes::integer[],
                updated_at = NOW()
            FROM (VALUES %s) AS v(id, nice_classes)
            WHERE tm.id = v.id::uuid
            """,
            [(decision["id"], decision["to"]) for decision in decisions],
        )
        conn.commit()

    return {
        "status": "success",
        "dry_run": dry_run,
        "candidates": len(candidates),
        "source_files_scanned": source_files_scanned,
        "metadata_matches": metadata_matches,
        "missing_source_rows": missing_source_rows,
        "decisions": len(decisions),
        "repaired": 0 if dry_run else len(decisions),
        "would_repair": len(decisions) if dry_run else 0,
        "samples": decisions[:20],
    }


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _live_artifact_root(artifact_dir: Path | str | None = None) -> Path:
    if artifact_dir is None:
        artifact_dir = os.environ.get("REPAIR_LIVE_ARTIFACT_DIR") or "artifacts/repair/live_trademark_checks"
    path = Path(artifact_dir)
    if not path.is_absolute():
        path = _bootstrap.PROJECT_ROOT / path
    return path


def _ensure_live_check_table(conn) -> None:
    cur = conn.cursor()
    cur.execute(_LIVE_CHECK_TABLE_SQL)
    conn.commit()


def _ensure_live_provisional_table(conn) -> None:
    cur = conn.cursor()
    cur.execute(_LIVE_PROVISIONAL_TABLE_SQL)
    conn.commit()


def _has_registration_no(value: Any) -> bool:
    if value is None:
        return False
    text = _repair_mojibake(str(value)).strip()
    return bool(text and text.lower() not in {"-", "null", "none", "yok", "n/a", "na"})


def _resolve_live_status(status_text: str | None, registration_no: Any = None) -> str | None:
    resolved = _explicit_db_status_from_text(status_text or "")
    if resolved:
        if resolved in (DB_STATUS_APPLIED, DB_STATUS_PUBLISHED):
            return None
        return resolved
    if _has_registration_no(registration_no):
        return DB_STATUS_REGISTERED
    return None


def _live_status_skip_counts(
    conn,
    *,
    app_no: str | None = None,
    include_older_than_11_years: bool = False,
) -> dict:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    params: list[Any] = [DB_STATUS_PUBLISHED, DB_STATUS_REFUSED, LIVE_PROVISIONAL_SOURCE]
    filters = [
        """(
            tm.current_status = %s::tm_status
            OR (
                tm.current_status = %s::tm_status
                AND tm.status_source = %s
            )
        )"""
    ]
    if app_no:
        filters.append("tm.application_no = %s")
        params.append(app_no)

    cur.execute(
        f"""
        SELECT
            COUNT(*) FILTER (
                WHERE tm.name IS NULL OR btrim(tm.name) = ''
            ) AS no_name,
            COUNT(*) FILTER (
                WHERE tm.name IS NOT NULL
                  AND btrim(tm.name) <> ''
                  AND tm.bulletin_date >= CURRENT_DATE - INTERVAL '{_LIVE_STATUS_RECENT_THRESHOLD}'
            ) AS recent,
            COUNT(*) FILTER (
                WHERE tm.name IS NOT NULL
                  AND btrim(tm.name) <> ''
                  AND (tm.bulletin_date IS NULL OR tm.bulletin_date < CURRENT_DATE - INTERVAL '{_LIVE_STATUS_RECENT_THRESHOLD}')
                  AND (tm.application_date IS NULL OR tm.application_date < CURRENT_DATE - INTERVAL '11 years')
            ) AS older_than_priority_window
        FROM trademarks tm
        WHERE {" AND ".join(filters)}
        """,
        params,
    )
    row = cur.fetchone() or {}
    older_count = 0 if include_older_than_11_years else row.get("older_than_priority_window", 0)
    return {
        "skipped_no_name": row.get("no_name", 0),
        "skipped_recent": row.get("recent", 0),
        "skipped_older_than_11_years": older_count,
    }


def _live_status_progress_filter_sql() -> str:
    return """
        NOT EXISTS (
            SELECT 1
            FROM repair_live_trademark_checks chk
            WHERE chk.application_no = tm.application_no
              AND chk.check_kind = 'status'
              AND (
                  chk.result_code = ANY(%s::text[])
                  OR (
                      chk.result_code = 'no_decision'
                      AND chk.live_registration_no IS NOT NULL
                  )
              )
        )
    """


def run_live_status_provisional_refusal_mark(
    *,
    conn,
    dry_run: bool = False,
    app_no: str | None = None,
    limit: int | None = None,
    include_older_than_11_years: bool | None = None,
) -> dict:
    """Temporarily mark unchecked published live-status candidates as refused.

    The marker is deliberately not `LIVE`: live repair continues to select these rows
    and replaces the provisional source once real live evidence is found.
    """
    _ensure_live_check_table(conn)
    _ensure_live_provisional_table(conn)
    include_older = (
        _env_bool("REPAIR_LIVE_INCLUDE_OLDER_THAN_11_YEARS", False)
        if include_older_than_11_years is None
        else include_older_than_11_years
    )
    status_success_codes = list(_LIVE_CHECK_SUCCESS_CODES - {"no_decision"})
    filters = [
        "tm.current_status = %s::tm_status",
        "tm.name IS NOT NULL",
        "btrim(tm.name) <> ''",
        f"tm.bulletin_date < CURRENT_DATE - INTERVAL '{_LIVE_PROVISIONAL_REFUSAL_THRESHOLD}'",
        _live_status_progress_filter_sql(),
    ]
    params: list[Any] = [DB_STATUS_PUBLISHED, status_success_codes]
    if app_no:
        filters.append("tm.application_no = %s")
        params.append(app_no)
    elif not include_older:
        filters.append("tm.application_date >= CURRENT_DATE - INTERVAL '11 years'")

    limit_clause = ""
    if limit:
        limit_clause = "LIMIT %s"
        params.append(limit)

    candidate_sql = f"""
        SELECT
            tm.id,
            tm.application_no,
            tm.current_status,
            tm.status_source,
            tm.final_status,
            tm.final_status_source,
            tm.final_status_at
        FROM trademarks tm
        WHERE {" AND ".join(filters)}
        ORDER BY
            CASE
                WHEN tm.application_date >= CURRENT_DATE - INTERVAL '11 years' THEN 0
                ELSE 1
            END,
            tm.bulletin_date NULLS FIRST,
            tm.application_date NULLS FIRST,
            tm.id
        {limit_clause}
    """
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if dry_run:
        cur.execute(
            f"""
            WITH candidates AS MATERIALIZED ({candidate_sql})
            SELECT
                count(*) AS candidates,
                array(
                    SELECT application_no
                    FROM candidates
                    ORDER BY application_no
                    LIMIT 20
                ) AS samples
            FROM candidates
            """,
            params,
        )
        row = cur.fetchone() or {}
        return {
            "status": "success",
            "dry_run": True,
            "candidates": row.get("candidates", 0),
            "marked": 0,
            "would_mark": row.get("candidates", 0),
            "source": LIVE_PROVISIONAL_SOURCE,
            "samples": row.get("samples") or [],
        }

    cur.execute(
        f"""
        WITH candidates AS MATERIALIZED ({candidate_sql}),
        marked AS (
            INSERT INTO repair_live_provisional_status_marks (
                trademark_id,
                application_no,
                previous_status,
                previous_status_source,
                previous_final_status,
                previous_final_status_source,
                previous_final_status_at,
                marked_status,
                marked_source
            )
            SELECT
                id,
                application_no,
                current_status,
                status_source,
                final_status,
                final_status_source,
                final_status_at,
                %s::tm_status,
                %s
            FROM candidates
            ON CONFLICT (application_no) DO NOTHING
            RETURNING application_no
        ),
        updated AS (
            UPDATE trademarks AS tm
            SET current_status = %s::tm_status,
                status_source = %s,
                final_status = %s::tm_status,
                final_status_source = 'ingest',
                final_status_at = CURRENT_DATE,
                updated_at = NOW()
            FROM candidates c
            WHERE tm.id = c.id
            RETURNING tm.application_no
        )
        SELECT
            (SELECT count(*) FROM candidates) AS candidates,
            (SELECT count(*) FROM marked) AS audit_rows,
            (SELECT count(*) FROM updated) AS marked,
            array(
                SELECT application_no
                FROM updated
                ORDER BY application_no
                LIMIT 20
            ) AS samples
        """,
        params
        + [
            DB_STATUS_REFUSED,
            LIVE_PROVISIONAL_SOURCE,
            DB_STATUS_REFUSED,
            LIVE_PROVISIONAL_SOURCE,
            DB_STATUS_REFUSED,
        ],
    )
    row = cur.fetchone() or {}
    conn.commit()
    return {
        "status": "success",
        "dry_run": False,
        "candidates": row.get("candidates", 0),
        "marked": row.get("marked", 0),
        "audit_rows": row.get("audit_rows", 0),
        "would_mark": 0,
        "source": LIVE_PROVISIONAL_SOURCE,
        "samples": row.get("samples") or [],
    }


def _live_status_candidates(
    conn,
    *,
    app_no: str | None = None,
    limit: int | None = None,
    include_older_than_11_years: bool = False,
) -> list[dict]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    params: list[Any] = [DB_STATUS_PUBLISHED, DB_STATUS_REFUSED, LIVE_PROVISIONAL_SOURCE]
    filters = [
        """(
            tm.current_status = %s::tm_status
            OR (
                tm.current_status = %s::tm_status
                AND tm.status_source = %s
            )
        )""",
        "tm.name IS NOT NULL",
        "btrim(tm.name) <> ''",
        f"(tm.bulletin_date IS NULL OR tm.bulletin_date < CURRENT_DATE - INTERVAL '{_LIVE_STATUS_RECENT_THRESHOLD}')",
    ]
    progress_filter = _live_status_progress_filter_sql()
    if app_no:
        filters.append("tm.application_no = %s")
        params.append(app_no)
    else:
        filters.append(progress_filter)
        params.append(list(_LIVE_CHECK_SUCCESS_CODES - {"no_decision"}))
        if not include_older_than_11_years:
            filters.append("tm.application_date >= CURRENT_DATE - INTERVAL '11 years'")

    limit_clause = ""
    if limit:
        limit_clause = "LIMIT %s"
        params.append(limit)

    cur.execute(
        f"""
        SELECT
            tm.id,
            tm.application_no,
            tm.name,
            tm.current_status::text AS current_status,
            tm.status_source,
            tm.bulletin_date,
            tm.application_date
        FROM trademarks tm
        WHERE {" AND ".join(filters)}
        ORDER BY
            CASE
                WHEN tm.application_date >= CURRENT_DATE - INTERVAL '11 years' THEN 0
                ELSE 1
            END,
            tm.bulletin_date DESC NULLS LAST,
            tm.application_date DESC NULLS LAST,
            tm.id DESC
        {limit_clause}
        """,
        params,
    )
    return [dict(row) for row in cur.fetchall()]


def _live_class_candidates(
    conn,
    *,
    app_no: str | None = None,
    limit: int | None = None,
    include_older_than_11_years: bool = False,
) -> list[dict]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    params: list[Any] = []
    filters = [
        "cardinality(tm.nice_class_numbers) = 6",
        "tm.name IS NOT NULL",
        "btrim(tm.name) <> ''",
    ]
    progress_filter = """
        NOT EXISTS (
            SELECT 1
            FROM repair_live_trademark_checks chk
            WHERE chk.application_no = tm.application_no
              AND chk.check_kind = 'classes'
              AND chk.result_code = ANY(%s::text[])
        )
    """
    if app_no:
        filters.append("tm.application_no = %s")
        params.append(app_no)
    else:
        filters.append(progress_filter)
        params.append(list(_LIVE_CHECK_SUCCESS_CODES))
        if not include_older_than_11_years:
            filters.append("tm.application_date >= CURRENT_DATE - INTERVAL '11 years'")

    limit_clause = ""
    if limit:
        limit_clause = "LIMIT %s"
        params.append(limit)

    cur.execute(
        f"""
        SELECT
            tm.id,
            tm.application_no,
            tm.name,
            tm.nice_class_numbers,
            tm.application_date
        FROM trademarks tm
        WHERE {" AND ".join(filters)}
        ORDER BY
            CASE
                WHEN tm.application_date >= CURRENT_DATE - INTERVAL '11 years' THEN 0
                ELSE 1
            END,
            tm.application_date DESC NULLS LAST,
            tm.id DESC
        {limit_clause}
        """,
        params,
    )
    return [dict(row) for row in cur.fetchall()]


def _fetch_live_evidence(candidate: dict, *, artifact_root: Path, scraper=None) -> dict:
    if scraper is None:
        from scrapper import TurkPatentScraper

        scraper = TurkPatentScraper(headless=_env_bool("REPAIR_LIVE_HEADLESS", True))

    return scraper.fetch_live_detail_evidence(
        candidate["application_no"],
        candidate["name"],
        artifact_root=artifact_root,
        limit=_env_int("REPAIR_LIVE_SEARCH_LIMIT", 200),
        max_scroll_seconds=_env_int("REPAIR_LIVE_SEARCH_SECONDS", 90),
    )


def _fetch_live_status_evidence(candidate: dict, *, scraper=None) -> dict:
    if scraper is None:
        from scrapper import TurkPatentScraper

        scraper = TurkPatentScraper(headless=_env_bool("REPAIR_LIVE_HEADLESS", True))

    return scraper.fetch_live_grid_evidence(
        candidate["application_no"],
        candidate["name"],
        limit=_env_int("REPAIR_LIVE_SEARCH_LIMIT", 200),
        max_scroll_seconds=_env_int("REPAIR_LIVE_SEARCH_SECONDS", 90),
    )


def _progress_row(
    *,
    candidate: dict,
    check_kind: str,
    result_code: str,
    evidence: dict | None = None,
    resolved_status: str | None = None,
    error: str | None = None,
) -> tuple:
    evidence = evidence or {}
    return (
        str(candidate["id"]),
        candidate["application_no"],
        check_kind,
        candidate.get("name"),
        result_code,
        evidence.get("status_text"),
        evidence.get("registration_no", ""),
        resolved_status,
        evidence.get("nice_classes") or None,
        evidence.get("artifact_dir"),
        error or evidence.get("artifact_error"),
    )


def _upsert_live_check_rows(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    cur = conn.cursor()
    execute_values(
        cur,
        """
        INSERT INTO repair_live_trademark_checks (
            trademark_id,
            application_no,
            check_kind,
            query_text,
            result_code,
            live_status_text,
            live_registration_no,
            resolved_status,
            live_nice_classes,
            artifact_dir,
            error
        )
        VALUES %s
        ON CONFLICT (application_no, check_kind) DO UPDATE
        SET trademark_id = EXCLUDED.trademark_id,
            query_text = EXCLUDED.query_text,
            result_code = EXCLUDED.result_code,
            live_status_text = EXCLUDED.live_status_text,
            live_registration_no = EXCLUDED.live_registration_no,
            resolved_status = EXCLUDED.resolved_status,
            live_nice_classes = EXCLUDED.live_nice_classes,
            artifact_dir = EXCLUDED.artifact_dir,
            error = EXCLUDED.error,
            checked_at = NOW(),
            updated_at = NOW()
        """,
        rows,
    )


def _apply_live_status_decisions(conn, decisions: list[dict]) -> None:
    if not decisions:
        return

    cur = conn.cursor()
    execute_values(
        cur,
        """
        UPDATE trademarks AS tm
        SET current_status = v.status::tm_status,
            status_source = 'LIVE',
            updated_at = NOW()
        FROM (VALUES %s) AS v(id, status)
        WHERE tm.id = v.id::uuid
        """,
        [(decision["id"], decision["to"]) for decision in decisions],
    )

    history_rows = [
        (
            decision["id"],
            date.today(),
            "LIVE_STATUS_REPAIR",
            decision.get("artifact_dir") or "live_status_repair",
            (
                f"{decision['from']} -> {decision['to']}; "
                f"live_status_text={decision.get('live_status_text') or ''}; "
                f"registration_no={decision.get('registration_no') or ''}"
            ),
        )
        for decision in decisions
    ]
    try:
        cur.execute("SAVEPOINT before_live_status_repair_history")
        execute_values(
            cur,
            """
            INSERT INTO trademark_history (trademark_id, event_date, event_type, source_file, description)
            VALUES %s
            ON CONFLICT DO NOTHING
            """,
            history_rows,
        )
        cur.execute("RELEASE SAVEPOINT before_live_status_repair_history")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT before_live_status_repair_history")
        logger.warning("Live status repair history insert skipped", exc_info=True)


def _apply_live_class_decisions(conn, decisions: list[dict]) -> None:
    if not decisions:
        return
    cur = conn.cursor()
    execute_values(
        cur,
        """
        UPDATE trademarks AS tm
        SET nice_class_numbers = v.nice_classes::integer[],
            updated_at = NOW()
        FROM (VALUES %s) AS v(id, nice_classes)
        WHERE tm.id = v.id::uuid
        """,
        [(decision["id"], decision["to"]) for decision in decisions],
    )


def run_live_status_repair(
    *,
    conn,
    dry_run: bool = False,
    app_no: str | None = None,
    limit: int | None = None,
    artifact_dir: Path | str | None = None,
    include_older_than_11_years: bool | None = None,
    live_fetcher=None,
) -> dict:
    _ensure_live_check_table(conn)
    effective_limit = limit if limit is not None else _env_int("REPAIR_LIVE_STATUS_BATCH_SIZE", 5)
    include_older = (
        _env_bool("REPAIR_LIVE_INCLUDE_OLDER_THAN_11_YEARS", False)
        if include_older_than_11_years is None
        else include_older_than_11_years
    )
    candidates = _live_status_candidates(
        conn,
        app_no=app_no,
        limit=effective_limit,
        include_older_than_11_years=include_older,
    )
    skip_counts = _live_status_skip_counts(
        conn,
        app_no=app_no,
        include_older_than_11_years=include_older,
    )
    _live_artifact_root(artifact_dir)

    decisions: list[dict] = []
    progress_rows: list[tuple] = []
    checked = matched = confirmed = no_decision = no_exact_match = failed = artifacts_saved = 0
    safety_stopped = False
    safety_reason = None
    next_allowed_at = None

    scraper = None
    try:
        if live_fetcher is None and candidates:
            from scrapper import TurkPatentScraper

            scraper = TurkPatentScraper(headless=_env_bool("REPAIR_LIVE_HEADLESS", True))

        for candidate in candidates:
            try:
                evidence = (
                    live_fetcher(candidate)
                    if live_fetcher is not None
                    else _fetch_live_status_evidence(candidate, scraper=scraper)
                )
            except Exception as exc:
                checked += 1
                failed += 1
                progress_rows.append(
                    _progress_row(
                        candidate=candidate,
                        check_kind="status",
                        result_code="failed",
                        error=str(exc),
                    )
                )
                continue

            if evidence.get("safety_stop"):
                safety_stopped = True
                safety_reason = evidence.get("safety_reason") or evidence.get("artifact_error")
                next_allowed_at = evidence.get("next_allowed_at")
                progress_rows.append(
                    _progress_row(
                        candidate=candidate,
                        check_kind="status",
                        result_code="safety_stop",
                        evidence=evidence,
                        error=safety_reason,
                    )
                )
                break

            checked += 1
            if evidence.get("artifact_dir"):
                artifacts_saved += 1
            if not evidence.get("matched"):
                no_exact_match += 1
                progress_rows.append(
                    _progress_row(
                        candidate=candidate,
                        check_kind="status",
                        result_code="no_exact_match",
                        evidence=evidence,
                    )
                )
                continue

            matched += 1
            resolved_status = _resolve_live_status(
                evidence.get("status_text"),
                evidence.get("registration_no"),
            )
            if not resolved_status:
                status_text = _repair_mojibake(str(evidence.get("status_text") or ""))
                result_code = "confirmed" if "yay" in status_text.lower() else "no_decision"
                if result_code == "confirmed":
                    confirmed += 1
                else:
                    no_decision += 1
                progress_rows.append(
                    _progress_row(
                        candidate=candidate,
                        check_kind="status",
                        result_code=result_code,
                        evidence=evidence,
                    )
                )
                continue

            decision = {
                "id": str(candidate["id"]),
                "application_no": candidate["application_no"],
                "from": candidate.get("current_status"),
                "to": resolved_status,
                "source": "LIVE",
                "live_status_text": evidence.get("status_text"),
                "registration_no": evidence.get("registration_no"),
                "artifact_dir": evidence.get("artifact_dir"),
            }
            decisions.append(decision)
            progress_rows.append(
                _progress_row(
                    candidate=candidate,
                    check_kind="status",
                    result_code="updated",
                    evidence=evidence,
                    resolved_status=resolved_status,
                )
            )
    finally:
        if scraper is not None:
            scraper.close()

    if not dry_run:
        _apply_live_status_decisions(conn, decisions)
        _upsert_live_check_rows(conn, progress_rows)
        conn.commit()
        if decisions:
            try:
                from utils.status_reconciler import update_final_status_batch

                update_final_status_batch(
                    conn,
                    app_nos=[decision["application_no"] for decision in decisions],
                )
            except Exception:
                logger.warning("Final status recompute skipped after live status repair", exc_info=True)

    return {
        "status": "success",
        "dry_run": dry_run,
        "candidates": len(candidates),
        "checked": checked,
        "matched": matched,
        "artifacts_saved": artifacts_saved,
        "decisions": len(decisions),
        "repaired": 0 if dry_run else len(decisions),
        "would_repair": len(decisions) if dry_run else 0,
        "confirmed": confirmed,
        "no_decision": no_decision,
        "no_exact_match": no_exact_match,
        "failed": failed,
        "skipped_no_name": skip_counts["skipped_no_name"],
        "skipped_recent": skip_counts["skipped_recent"],
        "skipped_older_than_11_years": skip_counts["skipped_older_than_11_years"],
        "include_older_than_11_years": include_older,
        "priority_window_years": _LIVE_PRIORITY_WINDOW_YEARS,
        "status_recent_threshold": _LIVE_STATUS_RECENT_THRESHOLD,
        "safety_stopped": safety_stopped,
        "safety_reason": safety_reason,
        "next_allowed_at": next_allowed_at,
        "samples": decisions[:20],
    }


def run_live_class_repair(
    *,
    conn,
    dry_run: bool = False,
    app_no: str | None = None,
    limit: int | None = None,
    artifact_dir: Path | str | None = None,
    include_older_than_11_years: bool | None = None,
    live_fetcher=None,
) -> dict:
    _ensure_live_check_table(conn)
    effective_limit = limit if limit is not None else _env_int("REPAIR_LIVE_CLASSES_BATCH_SIZE", 5)
    include_older = (
        _env_bool("REPAIR_LIVE_INCLUDE_OLDER_THAN_11_YEARS", False)
        if include_older_than_11_years is None
        else include_older_than_11_years
    )
    candidates = _live_class_candidates(
        conn,
        app_no=app_no,
        limit=effective_limit,
        include_older_than_11_years=include_older,
    )
    artifact_root = _live_artifact_root(artifact_dir)

    decisions: list[dict] = []
    progress_rows: list[tuple] = []
    checked = matched = no_decision = no_exact_match = failed = artifacts_saved = not_richer = 0
    safety_stopped = False
    safety_reason = None
    next_allowed_at = None

    scraper = None
    try:
        if live_fetcher is None and candidates:
            from scrapper import TurkPatentScraper

            scraper = TurkPatentScraper(headless=_env_bool("REPAIR_LIVE_HEADLESS", True))

        for candidate in candidates:
            try:
                evidence = (
                    live_fetcher(candidate)
                    if live_fetcher is not None
                    else _fetch_live_evidence(candidate, artifact_root=artifact_root, scraper=scraper)
                )
            except Exception as exc:
                checked += 1
                failed += 1
                progress_rows.append(
                    _progress_row(
                        candidate=candidate,
                        check_kind="classes",
                        result_code="failed",
                        error=str(exc),
                    )
                )
                continue

            if evidence.get("safety_stop"):
                safety_stopped = True
                safety_reason = evidence.get("safety_reason") or evidence.get("artifact_error")
                next_allowed_at = evidence.get("next_allowed_at")
                progress_rows.append(
                    _progress_row(
                        candidate=candidate,
                        check_kind="classes",
                        result_code="safety_stop",
                        evidence=evidence,
                        error=safety_reason,
                    )
                )
                break

            checked += 1
            if evidence.get("artifact_dir"):
                artifacts_saved += 1
            if not evidence.get("matched"):
                no_exact_match += 1
                progress_rows.append(
                    _progress_row(
                        candidate=candidate,
                        check_kind="classes",
                        result_code="no_exact_match",
                        evidence=evidence,
                    )
                )
                continue

            matched += 1
            live_classes = _normalize_nice_classes(evidence.get("nice_classes"))
            existing_classes = _normalize_nice_classes(candidate.get("nice_class_numbers"))
            if not live_classes:
                no_decision += 1
                progress_rows.append(
                    _progress_row(
                        candidate=candidate,
                        check_kind="classes",
                        result_code="no_decision",
                        evidence=evidence,
                    )
                )
                continue

            if len(live_classes) <= 6 or live_classes == existing_classes:
                not_richer += 1
                progress_rows.append(
                    _progress_row(
                        candidate=candidate,
                        check_kind="classes",
                        result_code="classes_not_richer",
                        evidence={**evidence, "nice_classes": live_classes},
                    )
                )
                continue

            decision = {
                "id": str(candidate["id"]),
                "application_no": candidate["application_no"],
                "from": candidate.get("nice_class_numbers"),
                "to": live_classes,
                "source": "LIVE",
                "artifact_dir": evidence.get("artifact_dir"),
            }
            decisions.append(decision)
            progress_rows.append(
                _progress_row(
                    candidate=candidate,
                    check_kind="classes",
                    result_code="updated",
                    evidence={**evidence, "nice_classes": live_classes},
                )
            )
    finally:
        if scraper is not None:
            scraper.close()

    if not dry_run:
        _apply_live_class_decisions(conn, decisions)
        _upsert_live_check_rows(conn, progress_rows)
        conn.commit()

    return {
        "status": "success",
        "dry_run": dry_run,
        "candidates": len(candidates),
        "checked": checked,
        "matched": matched,
        "artifacts_saved": artifacts_saved,
        "decisions": len(decisions),
        "repaired": 0 if dry_run else len(decisions),
        "would_repair": len(decisions) if dry_run else 0,
        "not_richer": not_richer,
        "no_decision": no_decision,
        "no_exact_match": no_exact_match,
        "failed": failed,
        "include_older_than_11_years": include_older,
        "priority_window_years": _LIVE_PRIORITY_WINDOW_YEARS,
        "safety_stopped": safety_stopped,
        "safety_reason": safety_reason,
        "next_allowed_at": next_allowed_at,
        "samples": decisions[:20],
    }


def run_repair(
    *,
    conn=None,
    root_dir: Path | str | None = None,
    dry_run: bool = False,
    app_no: str | None = None,
    limit: int | None = None,
) -> dict:
    owns_conn = conn is None
    if root_dir is None:
        root_dir = _bootstrap.default_ingest_root()
    root_dir = Path(root_dir)

    if owns_conn:
        conn = get_connection()

    try:
        status_summary = run_status_repair(
            conn=conn,
            root_dir=root_dir,
            dry_run=dry_run,
            app_no=app_no,
            limit=limit,
        )
        name_summary = run_name_repair(
            conn=conn,
            dry_run=dry_run,
            app_no=app_no,
            limit=limit,
        )
        name_tr_summary = run_name_tr_repair(
            conn=conn,
            dry_run=dry_run,
            app_no=app_no,
            limit=limit,
        )
        logo_only_text_summary = run_logo_only_text_feature_repair(
            conn=conn,
            dry_run=dry_run,
            app_no=app_no,
            limit=limit,
        )
        classes_summary = run_class_repair(
            conn=conn,
            root_dir=root_dir,
            dry_run=dry_run,
            app_no=app_no,
            limit=limit,
        )
        live_status_summary = run_live_status_repair(
            conn=conn,
            dry_run=dry_run,
            app_no=app_no,
            limit=limit,
        )
        live_classes_summary = run_live_class_repair(
            conn=conn,
            dry_run=dry_run,
            app_no=app_no,
            limit=limit,
        )
        summaries = [
            status_summary,
            name_summary,
            name_tr_summary,
            logo_only_text_summary,
            classes_summary,
            live_status_summary,
            live_classes_summary,
        ]
        repaired = sum(summary.get("repaired", 0) for summary in summaries)
        would_repair = sum(summary.get("would_repair", 0) for summary in summaries)
        return {
            "status": "success",
            "dry_run": dry_run,
            "repaired": repaired,
            "would_repair": would_repair,
            "candidates": sum(summary.get("candidates", 0) for summary in summaries),
            "decisions": sum(summary.get("decisions", 0) for summary in summaries),
            "routines": {
                "status": status_summary,
                "name": name_summary,
                "name_tr": name_tr_summary,
                "logo_only_text_features": logo_only_text_summary,
                "classes": classes_summary,
                "live_status": live_status_summary,
                "live_classes": live_classes_summary,
            },
        }
    finally:
        if owns_conn and conn is not None:
            release_connection(conn)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Run post-ingest database repair routines.")
    parser.add_argument("--dry-run", action="store_true", help="Report decisions without changing the database.")
    parser.add_argument("--app-no", type=str, default=None, help="Repair only one application number.")
    parser.add_argument("--limit", type=int, default=None, help="Limit rows considered per routine.")
    parser.add_argument("--root-dir", type=str, default=None, help="Root directory containing metadata.")
    parser.add_argument(
        "--provision-live-refusals",
        action="store_true",
        help="Temporarily mark unchecked live-status candidates as Reddedildi with LIVE_PROV source.",
    )
    args = parser.parse_args()

    try:
        if args.provision_live_refusals:
            conn = get_connection()
            try:
                summary = run_live_status_provisional_refusal_mark(
                    conn=conn,
                    dry_run=args.dry_run,
                    app_no=args.app_no,
                    limit=args.limit,
                )
            finally:
                release_connection(conn)
        else:
            summary = run_repair(
                root_dir=args.root_dir,
                dry_run=args.dry_run,
                app_no=args.app_no,
                limit=args.limit,
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    finally:
        close_pool()


if __name__ == "__main__":
    main()

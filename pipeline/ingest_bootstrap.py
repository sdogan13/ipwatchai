"""Explicit ingest runtime setup and readiness checks."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BULLETINS_ROOT = PROJECT_ROOT / "bulletins" / "Marka"
DEFAULT_INGEST_RUNTIME_SETUP_COMMAND = "python migrations/run_ingest_runtime_migration.py"

_CLASS_NAMES_TR = {
    1: "Kimyasallar",
    2: "Boyalar",
    3: "Kozmetikler",
    4: "Yağlar ve Yakıtlar",
    5: "İlaçlar",
    6: "Metal Ürünler",
    7: "Makineler",
    8: "El Aletleri",
    9: "Elektronik",
    10: "Tıbbi Cihazlar",
    11: "Aydınlatma",
    12: "Taşıtlar",
    13: "Ateşli Silahlar",
    14: "Mücevherat",
    15: "Müzik Aletleri",
    16: "Kağıt Ürünleri",
    17: "Kauçuk",
    18: "Deri Ürünler",
    19: "Yapı Malzemeleri",
    20: "Mobilya",
    21: "Ev Eşyaları",
    22: "Halatlar",
    23: "İplikler",
    24: "Kumaşlar",
    25: "Giyim",
    26: "Dantela",
    27: "Halılar",
    28: "Oyuncaklar",
    29: "Et Ürünleri",
    30: "Gıda",
    31: "Tarım Ürünleri",
    32: "Bira/Alkolsüz İç.",
    33: "Alkollü İçecekler",
    34: "Tütün",
    35: "Reklamcılık",
    36: "Sigortacılık",
    37: "İnşaat",
    38: "Telekomün.",
    39: "Taşımacılık",
    40: "Malzeme İşleme",
    41: "Eğitim",
    42: "Yazılım/BT",
    43: "Yiyecek/İçecek",
    44: "Sağlık",
    45: "Hukuk Hizmetleri",
    99: "Global Marka (Tüm Sınıflar)",
}

REQUIRED_TRADEMARK_COLUMNS = {
    "availability_status",
    "nice_class_numbers",
    "vienna_class_numbers",
    "extracted_goods",
    "registration_no",
    "wipo_no",
    "application_date",
    "registration_date",
    "bulletin_no",
    "bulletin_date",
    "gazette_no",
    "gazette_date",
    "appeal_deadline",
    "expiry_date",
    "image_path",
    "image_embedding",
    "dinov2_embedding",
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
}


def default_ingest_root() -> Path:
    env_value = (
        os.environ.get("PIPELINE_BULLETINS_ROOT")
        or os.environ.get("DATA_ROOT")
    )
    return resolve_ingest_root(env_value)


def resolve_ingest_root(value: str | None, default: Path | None = None) -> Path:
    default = default or DEFAULT_BULLETINS_ROOT
    if not value:
        return default.resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _classes_file(root_dir: Path | None = None) -> Path | None:
    root_dir = root_dir or default_ingest_root()
    embedded = root_dir / "nice_classes_with_embeddings.json"
    if embedded.exists():
        return embedded
    basic = root_dir / "nice_classes.json"
    if basic.exists():
        return basic
    return None


def seed_nice_classes(conn, root_dir: Path | None = None) -> int:
    classes_file = _classes_file(root_dir)
    if classes_file is None:
        logging.warning("Nice class source JSON not found; skipping seed.")
        return 0

    with open(classes_file, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    pgvector_available = cur.fetchone() is not None

    rows_with_emb = []
    rows_no_emb = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            class_no = item.get("CLASSNO")
            description = item.get("DESCRIPTION")
            embedding = item.get("CLASS_EMBEDDING")
            if not class_no or not description:
                continue
            class_no = int(class_no)
            row = (class_no, _CLASS_NAMES_TR.get(class_no), "", description)
            if pgvector_available and embedding:
                emb_str = "[" + ",".join(map(str, embedding)) + "]"
                rows_with_emb.append(row + (emb_str,))
            else:
                rows_no_emb.append(row)

    if rows_with_emb:
        execute_values(
            cur,
            """
            INSERT INTO nice_classes_lookup
                (class_number, name_tr, name_en, description, description_embedding)
            VALUES %s
            ON CONFLICT (class_number) DO UPDATE
            SET
                name_tr = EXCLUDED.name_tr,
                name_en = EXCLUDED.name_en,
                description = EXCLUDED.description,
                description_embedding = EXCLUDED.description_embedding,
                updated_at = NOW()
            """,
            rows_with_emb,
        )
    if rows_no_emb:
        execute_values(
            cur,
            """
            INSERT INTO nice_classes_lookup
                (class_number, name_tr, name_en, description)
            VALUES %s
            ON CONFLICT (class_number) DO UPDATE
            SET
                name_tr = EXCLUDED.name_tr,
                name_en = EXCLUDED.name_en,
                description = EXCLUDED.description,
                updated_at = NOW()
            """,
            rows_no_emb,
        )
    conn.commit()
    return len(rows_with_emb) + len(rows_no_emb)


def apply_ingest_runtime_setup(conn, root_dir: Path | None = None) -> None:
    sql_path = PROJECT_ROOT / "migrations" / "ingest_runtime.sql"
    sql = sql_path.read_text(encoding="utf-8")
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()
    seed_nice_classes(conn, root_dir=root_dir)


def assert_ingest_runtime_ready(conn) -> None:
    cur = conn.cursor()

    missing = []
    cur.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = ANY(%s)
        """,
        (["processed_files", "nice_classes_lookup", "trademarks"],),
    )
    tables = {row[0] for row in cur.fetchall()}
    for required in ("processed_files", "nice_classes_lookup", "trademarks"):
        if required not in tables:
            missing.append(f"table:{required}")

    cur.execute("SELECT typname FROM pg_type WHERE typname = 'tm_status'")
    if cur.fetchone() is None:
        missing.append("type:tm_status")

    cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
    if cur.fetchone() is None:
        missing.append("extension:vector")

    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'trademarks'
        """
    )
    trademark_columns = {row[0] for row in cur.fetchall()}
    missing_columns = sorted(REQUIRED_TRADEMARK_COLUMNS - trademark_columns)
    missing.extend(f"column:trademarks.{name}" for name in missing_columns)

    cur.execute("SELECT COUNT(*) FROM nice_classes_lookup")
    nice_class_count = cur.fetchone()[0] if "nice_classes_lookup" in tables else 0
    if nice_class_count <= 0:
        missing.append("data:nice_classes_lookup")

    if missing:
        raise RuntimeError(
            "Ingest runtime is not ready: "
            + ", ".join(missing)
            + f". Run `{DEFAULT_INGEST_RUNTIME_SETUP_COMMAND}` first."
        )


def check_and_migrate_schema(conn):
    assert_ingest_runtime_ready(conn)


def load_nice_classes(conn):
    assert_ingest_runtime_ready(conn)


__all__ = [
    "PROJECT_ROOT",
    "DEFAULT_BULLETINS_ROOT",
    "DEFAULT_INGEST_RUNTIME_SETUP_COMMAND",
    "default_ingest_root",
    "resolve_ingest_root",
    "seed_nice_classes",
    "apply_ingest_runtime_setup",
    "assert_ingest_runtime_ready",
    "check_and_migrate_schema",
    "load_nice_classes",
]

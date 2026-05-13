"""Canonical ingest rules, status normalization, and SQL builders."""

from __future__ import annotations

from datetime import datetime, timedelta
import re

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


def parse_date(date_str):
    if not date_str:
        return None
    date_str = str(date_str).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def calculate_expiration_status(application_date):
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


def extract_bulletin_info(folder_name: str):
    period_match = re.search(
        r"^(?:BLT|BULTEN|GZ|GAZETE)[_-]?(\d{4})[_-](\d{2})$",
        folder_name,
        re.IGNORECASE,
    )
    if period_match:
        bulletin_no = f"{period_match.group(1)}/{period_match.group(2)}"
    else:
        no_match = re.search(
            r"(?:BLT|BULTEN|GZ|GAZETE)[_-]?(\d+)",
            folder_name,
            re.IGNORECASE,
        )
        bulletin_no = no_match.group(1) if no_match else None

    date_match = re.search(r"(\d{4}[_-]\d{2}[_-]\d{2}|\d{4}[_-]\d{2})", folder_name)
    bulletin_date = None
    if date_match:
        d_str = date_match.group(1).replace("_", "-")
        try:
            bulletin_date = (
                datetime.strptime(d_str, "%Y-%m").date()
                if len(d_str) == 7
                else datetime.strptime(d_str, "%Y-%m-%d").date()
            )
        except ValueError:
            bulletin_date = None

    return bulletin_no, bulletin_date


def _explicit_db_status_from_text(status_raw):
    status_text = _repair_mojibake(str(status_raw)) if status_raw else ""
    status_lower = status_text.lower().replace("\u0307", "").strip()

    if status_lower:
        refused_keywords = [
            "geçersiz",
            "gecersiz",
            "marka başvurusu/tescili geçersiz",
            "başvuru geçersiz",
            "basvuru gecersiz",
            "tescil geçersiz",
            "tescil gecersiz",
            "reddedildi",
            "red edildi",
            "ret kararı",
            "red kararı",
            "refused",
            "rejected",
        ]
        if any(kw in status_lower for kw in refused_keywords):
            return DB_STATUS_REFUSED

        withdrawn_keywords = [
            "feragat edildi",
            "feragat",
            "geri çekildi",
            "geri cekildi",
            "geri alındı",
            "geri alindi",
            "vazgeçildi",
            "vazgecildi",
            "withdrawn",
        ]
        if any(kw in status_lower for kw in withdrawn_keywords):
            return DB_STATUS_WITHDRAWN

        cancelled_keywords = [
            "iptal edildi",
            "mahkeme kararı",
            "mahkeme karari",
            "cancelled",
            "canceled",
        ]
        if any(kw in status_lower for kw in cancelled_keywords):
            return DB_STATUS_CANCELLED

        registered_keywords = [
            "tescil edildi",
            "tescilli",
            "kabul edildi",
            "registered",
        ]
        if any(kw in status_lower for kw in registered_keywords):
            return DB_STATUS_REGISTERED

        if "itiraz" in status_lower or "opposed" in status_lower:
            return DB_STATUS_OPPOSED

        expired_keywords = [
            "sona erdi",
            "süresi doldu",
            "suresi doldu",
            "hükümsüz",
            "hukumsuz",
            "expired",
            "yürürlükten",
            "yururlukten",
        ]
        if any(kw in status_lower for kw in expired_keywords):
            return DB_STATUS_EXPIRED

        published_keywords = ["yayınlandı", "yayinlandi", "ilan edildi", "published"]
        if any(kw in status_lower for kw in published_keywords):
            return DB_STATUS_PUBLISHED

        if "renewed" in status_lower or "yenilendi" in status_lower:
            return DB_STATUS_RENEWED

    return None


def _determine_db_status_raw(folder_name, status_raw, reg_no_val=None):
    folder_upper = folder_name.upper()
    has_reg_no = has_valid_registration_no(reg_no_val)

    explicit_status = _explicit_db_status_from_text(status_raw)
    if explicit_status:
        return explicit_status

    if has_reg_no:
        return DB_STATUS_REGISTERED
    if folder_upper.startswith("GZ_") or "GAZETE" in folder_upper:
        return DB_STATUS_REGISTERED
    if folder_upper.startswith("BLT_") or "BULTEN" in folder_upper:
        return DB_STATUS_PUBLISHED
    return DB_STATUS_APPLIED


def determine_db_status(folder_name, status_raw, reg_no_val=None):
    return _canonicalize_db_status(
        _determine_db_status_raw(folder_name, status_raw, reg_no_val)
    )


def determine_status(folder_name, status_raw, reg_no_val=None):
    status_aliases = {
        DB_STATUS_RENEWED: "Renewed",
        DB_STATUS_REGISTERED: "Registered",
        DB_STATUS_TRANSFERRED: "Transferred",
        DB_STATUS_EXPIRED: "Expired",
        DB_STATUS_OPPOSED: "Opposed",
        DB_STATUS_REFUSED: "Refused",
        DB_STATUS_WITHDRAWN: "Withdrawn",
        DB_STATUS_CANCELLED: "Cancelled",
        DB_STATUS_PUBLISHED: "Published",
        DB_STATUS_PARTIAL_REFUSAL: "Partial Refusal",
        DB_STATUS_APPLIED: "Applied",
    }
    status = determine_db_status(folder_name, status_raw, reg_no_val)
    return status_aliases.get(status, status)


def get_status_rank(status):
    status_aliases = {
        "Renewed": DB_STATUS_RENEWED,
        "Registered": DB_STATUS_REGISTERED,
        "Transferred": DB_STATUS_TRANSFERRED,
        "Expired": DB_STATUS_EXPIRED,
        "Opposed": DB_STATUS_OPPOSED,
        "Refused": DB_STATUS_REFUSED,
        "Withdrawn": DB_STATUS_WITHDRAWN,
        "Cancelled": DB_STATUS_CANCELLED,
        "Published": DB_STATUS_PUBLISHED,
        "Applied": DB_STATUS_APPLIED,
        "Partial Refusal": DB_STATUS_PARTIAL_REFUSAL,
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


def get_source_rank(folder_name):
    if is_app_source_folder(folder_name):
        return 3, "APP"
    folder_upper = folder_name.upper()
    if folder_upper.startswith("GZ_") or "GAZETE" in folder_upper:
        return 2, "GZ"
    return 1, "BLT"


def is_app_source_folder(folder_name):
    folder_upper = str(folder_name or "").upper()
    return (
        folder_upper.startswith("APP_")
        or folder_upper.startswith("LIVE_")
        or "SCRAPED" in folder_upper
    )


def has_valid_registration_no(value):
    if value is None:
        return False
    text = _repair_mojibake(str(value)).strip()
    return bool(text and text.lower() not in {"null", "none", "-", "yok", "n/a", "na"})


_SEKIL_WORD_RE = re.compile(
    r"(?:\+\s*)?\b(?:s|\u015f)ek(?:i|\u0131|\u0130)l\b",
    re.IGNORECASE,
)


def clean_name(raw_name):
    if not raw_name:
        return None

    name = _repair_mojibake(str(raw_name))
    name = _SEKIL_WORD_RE.sub("", name)
    name = " ".join(name.split())
    return name if name else None


def _name_cleans_to_empty(raw_name):
    if raw_name is None:
        return False

    raw_text = _repair_mojibake(str(raw_name)).strip()
    if not raw_text:
        return False

    return clean_name(raw_text) is None


_SHARED_FIELDS = [
    ("name", "v.name"),
    ("name_tr", "v.name_tr"),
    ("detected_lang", "v.detected_lang"),
    ("name_tr_backend", "v.name_tr_backend"),
    ("name_tr_model", "v.name_tr_model"),
    ("name_tr_updated_at", "v.name_tr_updated_at::timestamp"),
    ("holder_name", "v.holder_name"),
    ("holder_tpe_client_id", "v.holder_tpe_client_id"),
    ("attorney_name", "v.attorney_name"),
    ("attorney_no", "v.attorney_no"),
    ("extracted_goods", "v.goods::jsonb"),
    ("application_date", "v.app_date::date"),
    ("last_event_date", "v.last_date::date"),
    ("expiry_date", "v.expiry::date"),
    ("image_path", "v.img_path"),
    ("image_embedding", "v.img_emb::halfvec(512)"),
    ("dinov2_embedding", "v.dino_emb::halfvec(768)"),
    ("logo_ocr_text", "v.ocr_text"),
]

_SUSPICIOUS_SIX_FIELDS = [
    ("nice_class_numbers", "v.nice_classes::integer[]"),
    ("vienna_class_numbers", "v.vienna_classes::integer[]"),
]

_BLT_OWNED_FIELDS = [
    ("bulletin_no", "v.b_no"),
    ("bulletin_date", "v.b_date::date"),
    ("appeal_deadline", "v.appeal::date"),
]

_GZ_OWNED_FIELDS = [
    ("registration_no", "v.reg_no"),
    ("wipo_no", "v.wipo_no"),
    ("registration_date", "v.reg_date::date"),
    ("gazette_no", "v.g_no"),
    ("gazette_date", "v.g_date::date"),
]

_NAME_DERIVED_TEXT_FIELDS = {
    "name_tr",
    "detected_lang",
    "name_tr_backend",
    "name_tr_model",
    "name_tr_updated_at",
}


def _priority_coalesce(col, val, source):
    if col == "name":
        return _priority_name_coalesce(val, source)

    target_col = f"tm.{col}"
    if source == "APP":
        priority_logic = f"COALESCE({val}, {target_col})"
    elif source == "GZ":
        priority_logic = (
            f"CASE WHEN COALESCE(tm.status_source, '') = 'APP' "
            f"THEN COALESCE({target_col}, {val}) ELSE COALESCE({val}, {target_col}) END"
        )
    else:
        priority_logic = (
            f"CASE WHEN COALESCE(tm.status_source, '') IN ('APP', 'GZ') "
            f"THEN COALESCE({target_col}, {val}) ELSE COALESCE({val}, {target_col}) END"
        )
    if col in _NAME_DERIVED_TEXT_FIELDS:
        return f"{col} = CASE WHEN v.clear_text_features THEN NULL ELSE {priority_logic} END"
    return f"{col} = {priority_logic}"


def _priority_name_coalesce(val, source):
    target_col = "tm.name"
    if source == "APP":
        priority_logic = f"COALESCE({val}, {target_col})"
    elif source == "GZ":
        priority_logic = (
            f"CASE WHEN COALESCE(tm.status_source, '') = 'APP' "
            f"THEN COALESCE({target_col}, {val}) ELSE COALESCE({val}, {target_col}) END"
        )
    else:
        priority_logic = (
            f"CASE WHEN COALESCE(tm.status_source, '') IN ('APP', 'GZ') "
            f"THEN COALESCE({target_col}, {val}) ELSE COALESCE({val}, {target_col}) END"
        )

    return f"name = CASE WHEN v.clear_name THEN NULL ELSE {priority_logic} END"


def _suspicious_six_coalesce(col, val, source):
    target_col = f"tm.{col}"
    if source == "APP" and col == "nice_class_numbers":
        incoming_count = f"COALESCE(cardinality({val}), 0)"
        target_count = f"COALESCE(cardinality({target_col}), 0)"
        return (
            f"{col} = CASE "
            f"WHEN {target_count} = 0 THEN COALESCE({val}, {target_col}) "
            f"WHEN {incoming_count} > 6 AND {incoming_count} > {target_count} THEN {val} "
            f"ELSE {target_col} END"
        )
    if source == "APP":
        priority_logic = f"COALESCE({val}, {target_col})"
    elif source == "GZ":
        priority_logic = (
            f"CASE WHEN COALESCE(tm.status_source, '') = 'APP' "
            f"THEN COALESCE({target_col}, {val}) ELSE COALESCE({val}, {target_col}) END"
        )
    else:
        priority_logic = (
            f"CASE WHEN COALESCE(tm.status_source, '') IN ('APP', 'GZ') "
            f"THEN COALESCE({target_col}, {val}) ELSE COALESCE({val}, {target_col}) END"
        )
    return (
        f"{col} = CASE WHEN COALESCE(cardinality({val}), 0) = 6 "
        f"AND COALESCE(cardinality({target_col}), 0) > 6 THEN {target_col} "
        f"ELSE {priority_logic} END"
    )


def _owned_field(col, val, owner, source):
    target_col = f"tm.{col}"
    if source == owner:
        return f"{col} = COALESCE({val}, {target_col})"
    return f"{col} = {target_col}"


def _build_update_set(source):
    parts = []
    for col, val in _SHARED_FIELDS:
        parts.append(_priority_coalesce(col, val, source))
    for col, val in _SUSPICIOUS_SIX_FIELDS:
        parts.append(_suspicious_six_coalesce(col, val, source))
    for col, val in _BLT_OWNED_FIELDS:
        parts.append(_owned_field(col, val, "BLT", source))
    for col, val in _GZ_OWNED_FIELDS:
        parts.append(_owned_field(col, val, "GZ", source))
    parts.append("current_status = v.status::tm_status")
    parts.append("status_source = v.src_tag")
    parts.append("updated_at = NOW()")
    return ",\n                    ".join(parts)


def _build_update_sql(source):
    return f"""
                UPDATE trademarks AS tm
                SET
                    {_build_update_set(source)}
                FROM (VALUES %s) AS v(
                    name, clear_name, clear_text_features, status, nice_classes, goods, last_date, appeal, expiry,
                    b_no, b_date, g_no, g_date, img_path,
                    app_date, reg_date, img_emb, dino_emb,
                    ocr_text,
                    name_tr, detected_lang, name_tr_backend, name_tr_model, name_tr_updated_at,
                    holder_name, holder_tpe_client_id,
                    attorney_name, attorney_no,
                    src_tag,
                    reg_no, wipo_no, vienna_classes,
                    app_no
                )
                WHERE tm.application_no = v.app_no
            """


__all__ = [
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
    "_repair_mojibake",
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
    "is_app_source_folder",
    "has_valid_registration_no",
    "clean_name",
    "_name_cleans_to_empty",
    "_SHARED_FIELDS",
    "_SUSPICIOUS_SIX_FIELDS",
    "_BLT_OWNED_FIELDS",
    "_GZ_OWNED_FIELDS",
    "_priority_coalesce",
    "_priority_name_coalesce",
    "_suspicious_six_coalesce",
    "_owned_field",
    "_build_update_set",
    "_build_update_sql",
]

"""Education content and progress helpers for the landing page."""

from __future__ import annotations

import csv
import errno
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import quote

from fastapi import HTTPException

from config.settings import PROJECT_ROOT
from database.crud import Database
from models.schemas import (
    EducationCatalogResponse,
    EducationFlashcardCard,
    EducationFlashcardDeckDetail,
    EducationFlashcardDeckSummary,
    EducationModerationItem,
    EducationModerationUpdate,
    EducationPdfItem,
    EducationProgressItem,
    EducationProgressResponse,
    EducationProgressSyncRequest,
    EducationProgressUpdate,
    EducationQuizOption,
    EducationQuizQuestion,
    EducationQuizSectionDetail,
    EducationQuizSectionSummary,
    EducationStats,
)


EDUCATION_ROOT = PROJECT_ROOT / "education"
ALLOWED_EDUCATION_ASSET_EXTENSIONS = {".pdf", ".png", ".mp4"}
FLASHCARD_JSON_PATH = EDUCATION_ROOT / "flashcards.json"
MODERATION_OVERRIDES_PATH = EDUCATION_ROOT / "moderation_overrides.json"

CATEGORY_ORDER = [
    "Patent",
    "Marka",
    "Coğrafi İşaret",
    "Tasarım",
    "Genel",
]

FLASHCARD_TITLE_OVERRIDES = {
    "6769": "6769",
    "cografi": "Coğrafi İşaretler",
    "cografi_1": "Coğrafi İşaretler II",
    "marka": "Marka",
    "marka_2": "Marka II",
    "meslek_kurallari": "Meslek Kuralları",
    "ortak_hukumler": "Ortak Hükümler",
    "ortak_hukumler_2": "Ortak Hükümler II",
    "smk": "SMK",
    "sureler": "Süreler",
    "vekill": "Vekillik",
}

FLASHCARD_ORDER = [
    "flashcards_vekill.csv",
    "flashcards_meslek_kurallari.csv",
    "flashcards_smk.csv",
    "flashcards_6769.csv",
    "flashcards_marka.csv",
    "flashcards_marka_2.csv",
    "flashcards_ortak_hukumler.csv",
    "flashcards_ortak_hukumler_2.csv",
    "flashcards_cografi.csv",
    "flashcards_cografi_1.csv",
    "flashcards_sureler.csv",
]

PDF_TITLE_OVERRIDES = {
    "6769_Industrial_Property_Law_English": "6769 Industrial Property Law (English)",
    "6769_Sayili_Sinai_Mulkiyet_Kanunu_Kapsamli_Ozet_ve_Stratejik_Analiz_Raporu": "6769 Sayılı Sınai Mülkiyet Kanunu Özeti ve Stratejik Analiz",
    "6769_Sinai_Mulkiyet_Kanunu": "6769 Sınai Mülkiyet Kanunu",
    "Innovation_Protection_Blueprint": "Innovation Protection Blueprint",
    "Innovation_Protection_Blueprint_1": "Innovation Protection Blueprint I",
    "Innovation_Protection_Blueprint_1_IPWATCHAI_Watermarked": "Innovation Protection Blueprint I (Watermarked)",
    "Innovation_Protection_Blueprint (1)": "Innovation Protection Blueprint (Alt Version)",
}

PDF_ORDER = [
    "6769_Sinai_Mulkiyet_Kanunu.pdf",
    "6769_Sayili_Sinai_Mulkiyet_Kanunu_Kapsamli_Ozet_ve_Stratejik_Analiz_Raporu.pdf",
    "6769_Industrial_Property_Law_English.pdf",
    "Innovation_Protection_Blueprint.pdf",
    "Innovation_Protection_Blueprint_1.pdf",
    "Innovation_Protection_Blueprint_1_IPWATCHAI_Watermarked.pdf",
    "Innovation_Protection_Blueprint (1).pdf",
]

QUIZ_SECTION_ORDER = CATEGORY_ORDER


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return slug or "education-item"


def _coerce_known_category_title(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    category_by_slug = {_slugify(title): title for title in CATEGORY_ORDER}
    return category_by_slug.get(_slugify(raw))


def _normalize_moderation_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip()


def _humanize_identifier(value: str) -> str:
    text = re.sub(r"[_\-]+", " ", value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:1].upper() + text[1:] if text else value


def _education_signature() -> tuple:
    if not EDUCATION_ROOT.exists():
        return tuple()

    signature = []
    for path in sorted(EDUCATION_ROOT.iterdir()):
        if not path.is_file():
            continue
        stat = path.stat()
        signature.append((path.name, stat.st_size, stat.st_mtime_ns))
    return tuple(signature)


def _pdf_sort_key(item: Dict[str, Any]) -> tuple:
    try:
        return (PDF_ORDER.index(item["file_name"]), item["title"])
    except ValueError:
        return (len(PDF_ORDER), item["title"])


def _flashcard_sort_key(item: Dict[str, Any]) -> tuple:
    if item["title"] in CATEGORY_ORDER:
        return (CATEGORY_ORDER.index(item["title"]), item["title"])
    try:
        return (FLASHCARD_ORDER.index(item["id"]), item["title"])
    except ValueError:
        return (len(FLASHCARD_ORDER), item["title"])


def _quiz_sort_key(item: Dict[str, Any]) -> tuple:
    if item["title"] in CATEGORY_ORDER:
        return (CATEGORY_ORDER.index(item["title"]), item["title"])
    try:
        return (QUIZ_SECTION_ORDER.index(item["title"]), item["title"])
    except ValueError:
        return (len(QUIZ_SECTION_ORDER), item["title"])


def _detect_pdf_language(path: Path) -> str | None:
    name = path.stem.lower()
    if "english" in name:
        return "en"
    return "tr"


def _normalize_quiz_section_title(raw_title: str | None) -> str:
    title = (raw_title or "").strip()
    if not title or re.fullmatch(r"\d+\s*/\s*\d+", title):
        return "Genel"
    return title


def _merge_progress_data(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(left or {})
    for key, value in (right or {}).items():
        existing = merged.get(key)
        if isinstance(existing, list) and isinstance(value, list):
            items = []
            for candidate in existing + value:
                if candidate not in items:
                    items.append(candidate)
            merged[key] = items
            continue
        if isinstance(existing, dict) and isinstance(value, dict):
            nested = dict(existing)
            nested.update(value)
            merged[key] = nested
            continue
        merged[key] = value
    return merged


def _parse_pdf_items() -> List[Dict[str, Any]]:
    pdfs = []
    for path in EDUCATION_ROOT.glob("*.pdf"):
        title = PDF_TITLE_OVERRIDES.get(path.stem, _humanize_identifier(path.stem))
        pdfs.append(
            {
                "id": path.name,
                "title": title,
                "file_name": path.name,
                "file_size_bytes": path.stat().st_size,
                "language": _detect_pdf_language(path),
                "download_url": f"/api/v1/education/assets/{quote(path.name)}",
            }
        )
    return sorted(pdfs, key=_pdf_sort_key)


def _default_moderation_overrides() -> Dict[str, Dict[str, Dict[str, Any]]]:
    return {
        "flashcards": {},
        "quiz_questions": {},
    }


def _read_moderation_overrides() -> Dict[str, Dict[str, Dict[str, Any]]]:
    payload = _default_moderation_overrides()
    if not MODERATION_OVERRIDES_PATH.exists():
        return payload

    try:
        with MODERATION_OVERRIDES_PATH.open("r", encoding="utf-8") as handle:
            raw_payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return payload

    if not isinstance(raw_payload, dict):
        return payload

    for bucket_name in ("flashcards", "quiz_questions"):
        raw_bucket = raw_payload.get(bucket_name) or {}
        if not isinstance(raw_bucket, dict):
            continue
        cleaned_bucket: Dict[str, Dict[str, Any]] = {}
        for item_id, item_data in raw_bucket.items():
            if not isinstance(item_data, dict):
                continue
            cleaned_item: Dict[str, Any] = {}
            category_title = _coerce_known_category_title(item_data.get("category_title"))
            if category_title:
                cleaned_item["category_title"] = category_title
            if bool(item_data.get("deleted")):
                cleaned_item["deleted"] = True
            if bucket_name == "quiz_questions":
                if "explanation" in item_data:
                    explanation = _normalize_moderation_text(item_data.get("explanation"))
                    if explanation is not None:
                        cleaned_item["explanation"] = explanation
                if "summary" in item_data:
                    summary = _normalize_moderation_text(item_data.get("summary"))
                    if summary is not None:
                        cleaned_item["summary"] = summary
            if cleaned_item:
                cleaned_bucket[str(item_id)] = cleaned_item
        payload[bucket_name] = cleaned_bucket

    return payload


def _write_moderation_overrides(payload: Dict[str, Dict[str, Dict[str, Any]]]) -> None:
    MODERATION_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = MODERATION_OVERRIDES_PATH.with_suffix(".tmp")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        temp_path.write_text(serialized, encoding="utf-8")
        temp_path.replace(MODERATION_OVERRIDES_PATH)
    except OSError as exc:
        if exc.errno not in {errno.EROFS, errno.EACCES}:
            raise
        MODERATION_OVERRIDES_PATH.write_text(serialized, encoding="utf-8")
    _build_education_cache.cache_clear()


def _load_raw_flashcard_cards() -> List[Dict[str, Any]]:
    cards = []
    if FLASHCARD_JSON_PATH.exists():
        with FLASHCARD_JSON_PATH.open("r", encoding="utf-8-sig") as handle:
            raw_cards = json.load(handle)

        for index, raw_card in enumerate(raw_cards, start=1):
            title = (
                _coerce_known_category_title(raw_card.get("flashcardTitle"))
                or str(raw_card.get("flashcardTitle") or "").strip()
                or "Genel"
            )
            card = {
                "id": str(raw_card.get("id") or f"{_slugify(title)}-{index}"),
                "front": str(raw_card.get("front") or "").strip(),
                "back": str(raw_card.get("back") or "").strip(),
                "category_title": title,
            }
            if card["front"] and card["back"]:
                cards.append(card)
        return cards

    for path in EDUCATION_ROOT.glob("flashcards_*.csv"):
        deck_key = path.stem.removeprefix("flashcards_")
        title = FLASHCARD_TITLE_OVERRIDES.get(deck_key, _humanize_identifier(deck_key))
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            for index, row in enumerate(reader, start=1):
                if len(row) < 2:
                    continue
                front = str(row[0]).strip()
                back = str(row[1]).strip()
                if not front or not back:
                    continue
                cards.append(
                    {
                        "id": f"{deck_key}-{index}",
                        "front": front,
                        "back": back,
                        "category_title": title,
                    }
                )

    return cards


def _parse_flashcard_decks(
    raw_cards: List[Dict[str, Any]],
    moderation_overrides: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    grouped_cards: Dict[str, List[Dict[str, Any]]] = {}
    flashcard_overrides = moderation_overrides.get("flashcards") or {}

    for raw_card in raw_cards:
        override = flashcard_overrides.get(raw_card["id"]) or {}
        if override.get("deleted"):
            continue

        title = (
            _coerce_known_category_title(override.get("category_title"))
            or raw_card["category_title"]
            or "Genel"
        )
        grouped_cards.setdefault(title, [])
        grouped_cards[title].append(
            {
                "id": raw_card["id"],
                "front": raw_card["front"],
                "back": raw_card["back"],
                "category_title": title,
            }
        )

    decks = []
    for title in CATEGORY_ORDER:
        cards = grouped_cards.get(title, [])
        if not cards:
            continue
        decks.append(
            {
                "id": _slugify(title),
                "title": title,
                "card_count": len(cards),
                "cards": cards,
            }
        )

    for title in sorted(grouped_cards.keys()):
        if title in CATEGORY_ORDER:
            continue
        cards = grouped_cards[title]
        if not cards:
            continue
        decks.append(
            {
                "id": _slugify(title),
                "title": title,
                "card_count": len(cards),
                "cards": cards,
            }
        )

    return decks


def _extract_question_explanation(raw_question: Dict[str, Any]) -> Dict[str, Any]:
    detail = raw_question.get("detailedExplanation") or {}
    assistant = detail.get("assistantResponse") or {}
    explanation = (assistant.get("coreExplanation") or "").strip() or None
    summary = (assistant.get("summary") or "").strip() or None
    return {
        "explanation": explanation,
        "summary": summary,
    }


def _load_raw_quiz_questions() -> List[Dict[str, Any]]:
    question_path = EDUCATION_ROOT / "sorular.json"
    if not question_path.exists():
        return []

    with question_path.open("r", encoding="utf-8-sig") as handle:
        raw_questions = json.load(handle)

    questions = []
    section_counts: Dict[str, int] = {}

    for source_index, raw_question in enumerate(raw_questions, start=1):
        title = _normalize_quiz_section_title(raw_question.get("quizTitle"))

        options = []
        correct_option_id = None
        for raw_option in raw_question.get("options") or []:
            option_id = str(raw_option.get("id") or "").strip()
            option_text = str(raw_option.get("text") or "").strip()
            if not option_id or not option_text:
                continue
            if str(raw_option.get("status") or "").strip().lower() == "right answer":
                correct_option_id = option_id
            options.append(
                {
                    "id": option_id,
                    "text": option_text,
                    "short_feedback": (str(raw_option.get("shortFeedback") or "").strip() or None),
                }
            )

        prompt = str(raw_question.get("question") or "").strip()
        if not prompt or not options:
            continue

        section_counts[title] = section_counts.get(title, 0) + 1
        explanation = _extract_question_explanation(raw_question)
        questions.append(
            {
                "id": f"quiz-question-{source_index}",
                "legacy_id": f"{_slugify(title)}-{section_counts[title]}",
                "prompt": prompt,
                "options": options,
                "correct_option_id": correct_option_id,
                "explanation": explanation["explanation"],
                "summary": explanation["summary"],
                "category_title": title,
            }
        )

    return questions


def _parse_quiz_sections(
    raw_questions: List[Dict[str, Any]],
    moderation_overrides: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    sections: Dict[str, Dict[str, Any]] = {}
    question_overrides = moderation_overrides.get("quiz_questions") or {}

    for raw_question in raw_questions:
        override = question_overrides.get(raw_question["id"]) or {}
        if override.get("deleted"):
            continue

        title = (
            _coerce_known_category_title(override.get("category_title"))
            or raw_question["category_title"]
            or "Genel"
        )
        section_id = _slugify(title)
        section = sections.setdefault(
            section_id,
            {
                "id": section_id,
                "title": title,
                "questions": [],
            },
        )
        section["questions"].append(
            {
                "id": raw_question["id"],
                "legacy_id": raw_question["legacy_id"],
                "prompt": raw_question["prompt"],
                "options": raw_question["options"],
                "correct_option_id": raw_question["correct_option_id"],
                "explanation": (
                    override["explanation"]
                    if "explanation" in override
                    else raw_question["explanation"]
                ),
                "summary": (
                    override["summary"]
                    if "summary" in override
                    else raw_question["summary"]
                ),
                "category_title": title,
            }
        )

    quiz_sections = []
    for section in sections.values():
        quiz_sections.append(
            {
                "id": section["id"],
                "title": section["title"],
                "question_count": len(section["questions"]),
                "questions": section["questions"],
            }
        )
    return sorted(quiz_sections, key=_quiz_sort_key)


def _build_category_summaries(
    flashcard_decks: List[Dict[str, Any]],
    quiz_sections: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    deck_by_title = {str(deck.get("title") or "").strip(): deck for deck in flashcard_decks}
    section_by_title = {str(section.get("title") or "").strip(): section for section in quiz_sections}

    ordered_titles = [
        title
        for title in CATEGORY_ORDER
        if title in deck_by_title or title in section_by_title
    ]
    for title in sorted(set(deck_by_title) | set(section_by_title)):
        if title and title not in ordered_titles:
            ordered_titles.append(title)

    categories = []
    for title in ordered_titles:
        deck = deck_by_title.get(title)
        section = section_by_title.get(title)
        categories.append(
            {
                "id": _slugify(title),
                "title": title,
                "flashcard_deck_id": deck.get("id") if deck else None,
                "flashcard_card_count": int(deck.get("card_count") or 0) if deck else 0,
                "quiz_section_id": section.get("id") if section else None,
                "question_count": int(section.get("question_count") or 0) if section else 0,
            }
        )
    return categories


@lru_cache(maxsize=4)
def _build_education_cache(_signature: tuple) -> Dict[str, Any]:
    moderation_overrides = _read_moderation_overrides()
    raw_flashcards = _load_raw_flashcard_cards()
    raw_quiz_questions = _load_raw_quiz_questions()
    pdfs = _parse_pdf_items()
    flashcard_decks = _parse_flashcard_decks(raw_flashcards, moderation_overrides)
    quiz_sections = _parse_quiz_sections(raw_quiz_questions, moderation_overrides)
    categories = _build_category_summaries(
        flashcard_decks=flashcard_decks,
        quiz_sections=quiz_sections,
    )

    stats = {
        "pdf_count": len(pdfs),
        "flashcard_deck_count": len(flashcard_decks),
        "flashcard_card_count": sum(deck["card_count"] for deck in flashcard_decks),
        "quiz_section_count": len(quiz_sections),
        "question_count": sum(section["question_count"] for section in quiz_sections),
    }

    return {
        "stats": stats,
        "catalog": {
            "stats": stats,
            "categories": categories,
            "pdfs": pdfs,
            "flashcard_decks": [
                {
                    "id": deck["id"],
                    "title": deck["title"],
                    "card_count": deck["card_count"],
                }
                for deck in flashcard_decks
            ],
            "quiz_sections": [
                {
                    "id": section["id"],
                    "title": section["title"],
                    "question_count": section["question_count"],
                }
                for section in quiz_sections
            ],
        },
        "pdf_map": {item["id"]: item for item in pdfs},
        "flashcard_map": {deck["id"]: deck for deck in flashcard_decks},
        "quiz_map": {section["id"]: section for section in quiz_sections},
        "flashcard_item_map": {
            card["id"]: card
            for deck in flashcard_decks
            for card in deck.get("cards") or []
        },
        "quiz_question_item_map": {
            question["id"]: question
            for section in quiz_sections
            for question in section.get("questions") or []
        },
        "source_flashcard_map": {
            card["id"]: {
                "category_title": card["category_title"],
            }
            for card in raw_flashcards
        },
        "source_quiz_question_map": {
            question["id"]: {
                "category_title": question["category_title"],
                "legacy_id": question["legacy_id"],
                "explanation": question["explanation"],
                "summary": question["summary"],
            }
            for question in raw_quiz_questions
        },
    }


def _get_education_cache() -> Dict[str, Any]:
    return _build_education_cache(_education_signature())


def _validate_progress_target(item_type: str, item_key: str) -> None:
    cache = _get_education_cache()
    if item_type == "pdf" and item_key in cache["pdf_map"]:
        return
    if item_type == "flashcard" and item_key in cache["flashcard_map"]:
        return
    if item_type == "quiz" and item_key in cache["quiz_map"]:
        return
    raise HTTPException(status_code=404, detail="Education item not found")


def _ensure_education_moderator(current_user) -> None:
    role = str(getattr(current_user, "role", "") or "").strip().lower()
    if getattr(current_user, "is_superadmin", False) or role == "admin":
        return
    raise HTTPException(status_code=403, detail="Education moderation requires admin access")


def _get_source_moderation_item(item_type: str, item_id: str) -> Dict[str, Any]:
    cache = _get_education_cache()
    if item_type == "flashcard":
        item = cache["source_flashcard_map"].get(item_id)
        if item:
            return item
    if item_type == "quiz_question":
        item = cache["source_quiz_question_map"].get(item_id)
        if item:
            return item
    raise HTTPException(status_code=404, detail="Education moderation item not found")


def _moderation_bucket_name(item_type: str) -> str:
    if item_type == "flashcard":
        return "flashcards"
    if item_type == "quiz_question":
        return "quiz_questions"
    raise HTTPException(status_code=400, detail="Unsupported education moderation type")


def _compact_moderation_entry(
    item_type: str,
    source_item: Dict[str, Any],
    item_data: Dict[str, Any],
) -> Dict[str, Any]:
    compact: Dict[str, Any] = {}
    category_title = _coerce_known_category_title(item_data.get("category_title"))
    if category_title and category_title != source_item.get("category_title"):
        compact["category_title"] = category_title
    if item_type == "quiz_question":
        if "explanation" in item_data:
            explanation = _normalize_moderation_text(item_data.get("explanation"))
            source_explanation = _normalize_moderation_text(source_item.get("explanation")) or ""
            if explanation is not None and explanation != source_explanation:
                compact["explanation"] = explanation
        if "summary" in item_data:
            summary = _normalize_moderation_text(item_data.get("summary"))
            source_summary = _normalize_moderation_text(source_item.get("summary")) or ""
            if summary is not None and summary != source_summary:
                compact["summary"] = summary
    if bool(item_data.get("deleted")):
        compact["deleted"] = True
    return compact


def resolve_education_asset_path(file_name: str) -> Path:
    requested = Path(file_name or "").name
    if not requested:
        raise HTTPException(status_code=404, detail="Education asset not found")

    asset_path = EDUCATION_ROOT / requested
    if (
        not asset_path.exists()
        or not asset_path.is_file()
        or asset_path.suffix.lower() not in ALLOWED_EDUCATION_ASSET_EXTENSIONS
    ):
        raise HTTPException(status_code=404, detail="Education asset not found")

    return asset_path


def _serialize_progress_rows(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    items = []
    for row in rows:
        progress_data = row.get("progress_data") or {}
        if isinstance(progress_data, str):
            try:
                progress_data = json.loads(progress_data)
            except json.JSONDecodeError:
                progress_data = {}
        items.append(
            {
                "item_type": row["item_type"],
                "item_key": row["item_key"],
                "status": row["status"],
                "percent_complete": int(row.get("percent_complete") or 0),
                "progress_data": progress_data,
                "completed_at": row.get("completed_at"),
                "last_interacted_at": row.get("last_interacted_at"),
                "updated_at": row.get("updated_at"),
            }
        )
    return {"items": items}


def _status_from_percent(percent_complete: int, current_status: str | None = None) -> str:
    status = (current_status or "").strip() or "not_started"
    if percent_complete >= 100:
        return "completed"
    if percent_complete > 0 and status == "not_started":
        return "in_progress"
    return status


def _upsert_progress(cur, user_id: str, payload: EducationProgressUpdate) -> Dict[str, Any]:
    percent_complete = max(0, min(100, int(payload.percent_complete)))
    status = _status_from_percent(percent_complete, payload.status)
    progress_json = json.dumps(payload.progress_data or {}, ensure_ascii=False)

    cur.execute(
        """
        INSERT INTO education_progress (
            user_id,
            item_type,
            item_key,
            status,
            percent_complete,
            progress_data,
            last_interacted_at,
            completed_at
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s::jsonb,
            CURRENT_TIMESTAMP,
            CASE WHEN %s = 'completed' THEN CURRENT_TIMESTAMP ELSE NULL END
        )
        ON CONFLICT (user_id, item_type, item_key)
        DO UPDATE SET
            status = EXCLUDED.status,
            percent_complete = EXCLUDED.percent_complete,
            progress_data = EXCLUDED.progress_data,
            last_interacted_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP,
            completed_at = CASE
                WHEN EXCLUDED.status = 'completed' THEN COALESCE(education_progress.completed_at, CURRENT_TIMESTAMP)
                ELSE NULL
            END
        RETURNING
            item_type,
            item_key,
            status,
            percent_complete,
            progress_data,
            completed_at,
            last_interacted_at,
            updated_at
        """,
        (
            user_id,
            payload.item_type,
            payload.item_key,
            status,
            percent_complete,
            progress_json,
            status,
        ),
    )
    return cur.fetchone()


async def get_education_catalog_data() -> EducationCatalogResponse:
    cache = _get_education_cache()
    return EducationCatalogResponse.model_validate(cache["catalog"])


async def get_flashcard_deck_data(deck_id: str) -> EducationFlashcardDeckDetail:
    cache = _get_education_cache()
    deck = cache["flashcard_map"].get(deck_id)
    if not deck:
        raise HTTPException(status_code=404, detail="Flashcard deck not found")
    return EducationFlashcardDeckDetail.model_validate(deck)


async def get_quiz_section_data(section_id: str) -> EducationQuizSectionDetail:
    cache = _get_education_cache()
    section = cache["quiz_map"].get(section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Quiz section not found")
    return EducationQuizSectionDetail.model_validate(section)


async def upsert_education_moderation_data(
    data: EducationModerationUpdate,
    current_user,
) -> EducationModerationItem:
    _ensure_education_moderator(current_user)

    source_item = _get_source_moderation_item(data.item_type, data.item_id)
    payload = _read_moderation_overrides()
    bucket_name = _moderation_bucket_name(data.item_type)
    bucket = dict(payload.get(bucket_name) or {})
    next_item = dict(bucket.get(data.item_id) or {})

    if data.category_title is not None:
        normalized_category = _coerce_known_category_title(data.category_title)
        if not normalized_category:
            raise HTTPException(status_code=400, detail="Invalid education category")
        next_item["category_title"] = normalized_category
    if data.item_type != "quiz_question" and (data.explanation is not None or data.summary is not None):
        raise HTTPException(status_code=400, detail="Only quiz questions support explanation editing")
    if data.explanation is not None:
        next_item["explanation"] = _normalize_moderation_text(data.explanation)
    if data.summary is not None:
        next_item["summary"] = _normalize_moderation_text(data.summary)
    if data.deleted is not None:
        next_item["deleted"] = bool(data.deleted)

    compact_item = _compact_moderation_entry(data.item_type, source_item, next_item)
    if compact_item:
        bucket[data.item_id] = compact_item
    else:
        bucket.pop(data.item_id, None)

    payload[bucket_name] = bucket
    _write_moderation_overrides(payload)

    effective_category = (
        compact_item.get("category_title")
        or source_item.get("category_title")
    )
    effective_explanation = (
        compact_item["explanation"]
        if "explanation" in compact_item
        else source_item.get("explanation")
    )
    effective_summary = (
        compact_item["summary"]
        if "summary" in compact_item
        else source_item.get("summary")
    )

    return EducationModerationItem.model_validate(
        {
            "item_type": data.item_type,
            "item_id": data.item_id,
            "category_title": effective_category,
            "explanation": effective_explanation,
            "summary": effective_summary,
            "deleted": bool(compact_item.get("deleted")),
        }
    )


async def get_education_progress_data(current_user, database_factory=Database) -> EducationProgressResponse:
    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT
                item_type,
                item_key,
                status,
                percent_complete,
                progress_data,
                completed_at,
                last_interacted_at,
                updated_at
            FROM education_progress
            WHERE user_id = %s
            ORDER BY updated_at DESC, item_type ASC, item_key ASC
            """,
            (str(current_user.id),),
        )
        rows = cur.fetchall()
    return EducationProgressResponse.model_validate(_serialize_progress_rows(rows))


async def upsert_education_progress_data(
    data: EducationProgressUpdate,
    current_user,
    database_factory=Database,
) -> EducationProgressItem:
    _validate_progress_target(data.item_type, data.item_key)

    with database_factory() as db:
        cur = db.cursor()
        row = _upsert_progress(cur, str(current_user.id), data)
        db.commit()

    return EducationProgressItem.model_validate(_serialize_progress_rows([row])["items"][0])


async def sync_education_progress_data(
    data: EducationProgressSyncRequest,
    current_user,
    database_factory=Database,
) -> EducationProgressResponse:
    if not data.items:
        return await get_education_progress_data(current_user=current_user, database_factory=database_factory)

    for item in data.items:
        _validate_progress_target(item.item_type, item.item_key)

    user_id = str(current_user.id)
    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT
                item_type,
                item_key,
                status,
                percent_complete,
                progress_data,
                completed_at,
                last_interacted_at,
                updated_at
            FROM education_progress
            WHERE user_id = %s
            """,
            (user_id,),
        )
        existing_rows = cur.fetchall()
        existing_map = {
            (row["item_type"], row["item_key"]): row
            for row in existing_rows
        }

        for item in data.items:
            existing = existing_map.get((item.item_type, item.item_key))
            merged_data = dict(item.progress_data or {})
            merged_percent = int(item.percent_complete or 0)
            merged_status = item.status

            if existing:
                existing_progress = existing.get("progress_data") or {}
                if isinstance(existing_progress, str):
                    try:
                        existing_progress = json.loads(existing_progress)
                    except json.JSONDecodeError:
                        existing_progress = {}
                merged_data = _merge_progress_data(existing_progress, merged_data)
                merged_percent = max(int(existing.get("percent_complete") or 0), merged_percent)
                if existing.get("status") == "completed" or merged_percent >= 100:
                    merged_status = "completed"
                elif merged_percent > 0:
                    merged_status = "in_progress"
                else:
                    merged_status = "not_started"

            merged_payload = EducationProgressUpdate(
                item_type=item.item_type,
                item_key=item.item_key,
                status=merged_status,
                percent_complete=merged_percent,
                progress_data=merged_data,
            )
            row = _upsert_progress(cur, user_id, merged_payload)
            existing_map[(item.item_type, item.item_key)] = row

        db.commit()

    return await get_education_progress_data(current_user=current_user, database_factory=database_factory)

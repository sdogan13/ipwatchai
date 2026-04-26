"""Assign study-material categories for education quizzes and flashcards.

This script:
1. rewrites `education/sorular.json` so each question's `quizTitle` is one of
   the canonical categories
2. preserves the previous quiz title in `sourceQuizTitle`
3. generates `education/flashcards.json` with `flashcardTitle` plus source
   metadata for each flashcard row loaded from the raw CSV files

The classifier is deterministic and uses weighted keyword / phrase rules with
light source-title hints for ambiguous items.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDUCATION_ROOT = PROJECT_ROOT / "education"
QUIZ_PATH = EDUCATION_ROOT / "sorular.json"
FLASHCARD_OUTPUT_PATH = EDUCATION_ROOT / "flashcards.json"

CATEGORY_ORDER = [
    "Patent",
    "Marka",
    "Coğrafi İşaret",
    "Tasarım",
    "Genel",
]

CATEGORY_PRIORITY = [
    "Coğrafi İşaret",
    "Tasarım",
    "Patent",
    "Marka",
    "Genel",
]

ASCII_TRANSLATION = str.maketrans(
    {
        231: "c",  # ç
        287: "g",  # ğ
        305: "i",  # ı
        246: "o",  # ö
        351: "s",  # ş
        252: "u",  # ü
        199: "c",  # Ç
        286: "g",  # Ğ
        304: "i",  # İ
        214: "o",  # Ö
        350: "s",  # Ş
        220: "u",  # Ü
        73: "i",   # I
    }
)

STOPWORDS = {
    "ve", "veya", "ile", "da", "de", "bu", "bir", "iki", "uc", "dort", "bes",
    "alti", "yedi", "sekiz", "dokuz", "on", "icin", "olan", "olarak", "olur",
    "olmaz", "gore", "hangi", "hangisi", "hangisidir", "nedir", "kac", "kactir",
    "asagidaki", "asagidakiler", "asagidakilerden", "degil", "degildir", "vardir",
    "yoktur", "en", "sonra", "once", "her", "hic", "tum", "bazi", "ise", "ki",
    "na", "ne", "ya", "ye", "ait", "ilgili", "iliskin", "hakkinda", "durumda",
    "durumunda", "oldugu", "oldugunda", "kapsaminda", "kapsamina", "kapsami",
    "kadar", "arasi", "sorulur", "uzere", "gibi", "sekilde", "ifade",
    "ifadelerden", "gosterir", "gosterilen", "madde", "maddesine", "maddesi",
    "maddede", "maddelerden", "sayilir", "sayilan", "edilir", "edilemez", "eder",
    "etmez", "olup", "biri", "birisi", "nedeniyle", "sebebiyle", "tarafindan",
    "arasinda", "hangileridir", "bunlardanhangisi", "asagidakilerdenhangisi",
    "uyarinca", "gorevi", "gorevleri", "olanlar", "midir", "mudur",
}

CATEGORY_RULES = {
    "Patent": {
        "phrases": {
            "faydali model": 10,
            "incelemeli patent": 12,
            "incelemesiz patent": 12,
            "arastirma raporu": 9,
            "ruchan hakki": 9,
            "patent basvurusu": 8,
            "patent sahibi": 7,
            "bulus basamagi": 8,
            "tarifname": 7,
        },
        "tokens": {
            "patent": 7,
            "bulus": 5,
            "faydali": 5,
            "model": 4,
            "ruchan": 6,
            "incelem": 5,
            "tarifname": 5,
            "istem": 4,
            "istemler": 4,
            "yenilik": 3,
            "arastirma": 3,
            "koruma": 2,
        },
    },
    "Marka": {
        "phrases": {
            "marka basvurusu": 8,
            "marka sahibi": 8,
            "marka tescil": 8,
            "ortak marka": 8,
            "garanti markasi": 8,
            "taninmis marka": 10,
            "ayirt edici": 10,
            "markanin kullanimi": 7,
        },
        "tokens": {
            "marka": 7,
            "tescil": 5,
            "itiraz": 4,
            "ayirt": 5,
            "logo": 4,
            "sinif": 3,
            "kullanim": 3,
            "yenile": 2,
            "amblem": 2,
            "hukumsuz": 4,
        },
    },
    "Coğrafi İşaret": {
        "phrases": {
            "cografi isaret": 12,
            "geleneksel urun adi": 12,
            "mahrec isareti": 12,
            "mense adi": 12,
            "cografi alan": 8,
            "denetim mercii": 7,
        },
        "tokens": {
            "cografi": 7,
            "isaret": 5,
            "mahrec": 6,
            "mense": 6,
            "geleneksel": 5,
            "urun": 3,
            "denetim": 4,
            "amblem": 2,
        },
    },
    "Tasarım": {
        "phrases": {
            "endustriyel tasarim": 12,
            "bilgilenmis kullanici": 12,
            "tasarim hakki": 8,
            "tasarim sahibi": 8,
            "tasarimin yeni": 7,
        },
        "tokens": {
            "tasarim": 8,
            "gorunum": 6,
            "kullanici": 3,
            "bilgilenmis": 6,
        },
    },
    "Genel": {
        "phrases": {
            "sinai mulkiyet": 12,
            "sinai mulkiyet kanunu": 14,
            "sicil ve disiplin": 12,
            "meslek kurali": 12,
            "marka vekili": 8,
            "patent vekili": 8,
            "vekillik sinavi": 10,
        },
        "tokens": {
            "vekil": 6,
            "vekillik": 6,
            "kurum": 4,
            "kanun": 4,
            "yonetmelik": 4,
            "disiplin": 5,
            "mulkiyet": 4,
            "sinai": 4,
            "basvuru": 2,
            "hak": 2,
            "sicil": 4,
        },
    },
}

QUIZ_TITLE_HINTS = {
    "marka testi": "Marka",
    "patent testi": "Patent",
    "tasarim testi": "Tasarım",
    "vekillik testi": "Genel",
    "sinai mulkiyet testi": "Genel",
    "smk testi": "Genel",
    "yonetmelik testi": "Genel",
}

FLASHCARD_SOURCE_HINTS = {
    "marka": "Marka",
    "marka_2": "Marka",
    "cografi": "Coğrafi İşaret",
    "cografi_1": "Coğrafi İşaret",
    "meslek_kurallari": "Genel",
    "vekill": "Genel",
    "6769": "Genel",
    "smk": "Genel",
    "ortak_hukumler": "Genel",
}

UMBRELLA_TERMS = (
    "sinai mulkiyet kanunu",
    "sinai mulkiyet",
    "6769",
    "smk",
)

DOMAIN_MARKERS = {
    "Patent": ("patent", "faydali model", "bulus", "ruchan", "arastirma raporu"),
    "Marka": ("marka", "ortak marka", "garanti markasi", "taninmis marka"),
    "Coğrafi İşaret": ("cografi isaret", "geleneksel urun adi", "mahrec", "mense"),
    "Tasarım": ("tasarim", "endustriyel tasarim", "bilgilenmis kullanici"),
}


def _normalize_for_match(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text or "")
    normalized = normalized.translate(ASCII_TRANSLATION).lower()
    normalized = normalized.replace("'", " ")
    normalized = re.sub(r"[^0-9a-z\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _tokenize(text: str) -> list[str]:
    return [
        token
        for token in _normalize_for_match(text).split()
        if len(token) > 2 and not token.isdigit() and token not in STOPWORDS
    ]


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def _source_hint_category(hint: str) -> str | None:
    for hint_key, hint_category in QUIZ_TITLE_HINTS.items():
        if _contains_phrase(hint, hint_key):
            return hint_category
    for hint_key, hint_category in FLASHCARD_SOURCE_HINTS.items():
        if _contains_phrase(hint, hint_key):
            return hint_category
    return None


def _domain_match_count(text: str) -> int:
    count = 0
    for markers in DOMAIN_MARKERS.values():
        if any(_contains_phrase(text, marker) for marker in markers):
            count += 1
    return count


def classify_text(text: str, *, source_hint: str = "") -> str:
    normalized = _normalize_for_match(text)
    hint = _normalize_for_match(source_hint)
    tokens = Counter(_tokenize(text))
    scores = {category: 0 for category in CATEGORY_ORDER}

    for category, config in CATEGORY_RULES.items():
        for phrase, weight in config["phrases"].items():
            if _contains_phrase(normalized, phrase):
                scores[category] += weight
        for token, count in tokens.items():
            for stem, weight in config["tokens"].items():
                if token.startswith(stem):
                    scores[category] += count * weight

    # Source-title hints help where the question text is procedural and generic.
    source_category = _source_hint_category(hint)
    if source_category:
        if source_category == "Genel":
            scores[source_category] += 3
        else:
            scores[source_category] += 7

    # Generic umbrella content should stay under Genel unless a specific domain
    # clearly dominates the prompt.
    if any(_contains_phrase(normalized, term) for term in UMBRELLA_TERMS):
        scores["Genel"] += 8

    if _domain_match_count(normalized) >= 3:
        scores["Genel"] += 10

    specific_categories = [category for category in CATEGORY_ORDER if category != "Genel"]
    best_specific = max(specific_categories, key=lambda category: scores[category])
    best_specific_score = scores[best_specific]
    general_score = scores["Genel"]

    if _domain_match_count(normalized) >= 3 and best_specific_score < 12:
        return "Genel"

    if best_specific_score >= 6 and best_specific_score >= general_score:
        return best_specific

    if best_specific_score >= 10:
        return best_specific

    if source_category and source_category != "Genel" and best_specific_score > 0:
        if best_specific == source_category or best_specific_score >= 4:
            return best_specific

    if source_category and source_category != "Genel" and general_score <= 8:
        return source_category

    if general_score > 0:
        return "Genel"

    if best_specific_score > 0:
        return best_specific

    if source_category:
        return source_category

    # Final fallback.
    if not normalized:
        return "Genel"
    return "Genel"


def _classify_quizzes() -> dict[str, int]:
    raw_questions = json.loads(QUIZ_PATH.read_text(encoding="utf-8-sig"))
    counts = Counter()

    for item in raw_questions:
        question_text = str(item.get("question") or "").strip()
        source_title = str(item.get("sourceQuizTitle") or item.get("quizTitle") or "").strip()
        if not question_text:
            category = "Genel"
        else:
            category = classify_text(question_text, source_hint=source_title)
            if "sourceQuizTitle" not in item:
                item["sourceQuizTitle"] = source_title
        item["quizTitle"] = category
        counts[category] += 1

    QUIZ_PATH.write_text(
        json.dumps(raw_questions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dict(counts)


def _classify_flashcards() -> dict[str, int]:
    categorized_cards = []
    counts = Counter()

    for path in sorted(EDUCATION_ROOT.glob("flashcards_*.csv")):
        source_key = path.stem.removeprefix("flashcards_")
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            for index, row in enumerate(reader, start=1):
                if len(row) < 2:
                    continue
                front = str(row[0]).strip()
                back = str(row[1]).strip()
                if not front or not back:
                    continue
                category = classify_text(front, source_hint=source_key)
                categorized_cards.append(
                    {
                        "id": f"{source_key}-{index}",
                        "flashcardTitle": category,
                        "front": front,
                        "back": back,
                        "sourceFile": path.name,
                        "sourceDeck": source_key,
                        "sourceIndex": index,
                    }
                )
                counts[category] += 1

    # Stable order: category first, then original source position.
    category_index = {category: idx for idx, category in enumerate(CATEGORY_ORDER)}
    categorized_cards.sort(
        key=lambda item: (
            category_index.get(item["flashcardTitle"], len(CATEGORY_ORDER)),
            item["sourceDeck"],
            item["sourceIndex"],
        )
    )

    FLASHCARD_OUTPUT_PATH.write_text(
        json.dumps(categorized_cards, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dict(counts)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    quiz_counts = _classify_quizzes()
    flashcard_counts = _classify_flashcards()

    print("Quiz category counts:")
    for category in CATEGORY_ORDER:
        print(f"  {category}: {quiz_counts.get(category, 0)}")

    print("Flashcard category counts:")
    for category in CATEGORY_ORDER:
        print(f"  {category}: {flashcard_counts.get(category, 0)}")

    print(f"Wrote {QUIZ_PATH}")
    print(f"Wrote {FLASHCARD_OUTPUT_PATH}")


if __name__ == "__main__":
    main()

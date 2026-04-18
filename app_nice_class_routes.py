"""Nice Classification models, helpers, and routes for the legacy FastAPI app."""

import logging
import re
from typing import List

from fastapi import Form
from pydantic import BaseModel, Field

from config.settings import settings
from utils.class_utils import GLOBAL_CLASS


MODULE_LOGGER = logging.getLogger(__name__)


class ClassSuggestionRequest(BaseModel):
    description: str = Field(
        ...,
        description="Description of goods/services in Turkish or English",
        min_length=3,
        max_length=2000,
    )
    top_k: int = Field(5, ge=1, le=45, description="Number of classes to return")
    lang: str = Field("tr", description="Language for class names: tr, en, ar")


class SuggestedClass(BaseModel):
    class_number: int
    class_name: str
    similarity: float
    description: str


class ClassSuggestionResponse(BaseModel):
    query: str
    suggestions: List[SuggestedClass]
    processing_time_ms: float


NICE_CLASS_NAMES = {
    1: "Chemicals",
    2: "Paints & Varnishes",
    3: "Cosmetics & Cleaning",
    4: "Industrial Oils & Fuels",
    5: "Pharmaceuticals",
    6: "Common Metals",
    7: "Machines & Machine Tools",
    8: "Hand Tools",
    9: "Electronics & Software",
    10: "Medical Apparatus",
    11: "Lighting & Heating",
    12: "Vehicles",
    13: "Firearms & Explosives",
    14: "Jewelry & Watches",
    15: "Musical Instruments",
    16: "Paper & Office Supplies",
    17: "Rubber & Plastic",
    18: "Leather Goods",
    19: "Building Materials",
    20: "Furniture",
    21: "Household Utensils",
    22: "Ropes & Textile Fibers",
    23: "Yarns & Threads",
    24: "Textiles & Bedding",
    25: "Clothing & Footwear",
    26: "Haberdashery",
    27: "Floor Coverings",
    28: "Games & Sporting Goods",
    29: "Meat & Processed Foods",
    30: "Staple Foods",
    31: "Agricultural Products",
    32: "Beers & Beverages",
    33: "Alcoholic Beverages",
    34: "Tobacco",
    35: "Advertising & Business",
    36: "Insurance & Finance",
    37: "Construction & Repair",
    38: "Telecommunications",
    39: "Transport & Storage",
    40: "Material Treatment",
    41: "Education & Entertainment",
    42: "Scientific & Tech Services",
    43: "Food & Accommodation",
    44: "Medical & Beauty Services",
    45: "Legal & Security Services",
    99: "Global Brand (All Classes)",
}


NICE_CLASS_NAMES_TR = {
    1: "Kimyasallar",
    2: "Boyalar",
    3: "Kozmetikler",
    4: "YaÄŸlar ve YakÄ±tlar",
    5: "EczacÄ±lÄ±k ÃœrÃ¼nleri",
    6: "Metaller",
    7: "Makineler",
    8: "El Aletleri",
    9: "Bilgisayar ve Elektronik",
    10: "TÄ±bbi Cihazlar",
    11: "AydÄ±nlatma ve IsÄ±tma",
    12: "TaÅŸÄ±tlar",
    13: "AteÅŸli Silahlar",
    14: "MÃ¼cevherat",
    15: "MÃ¼zik Aletleri",
    16: "KaÄŸÄ±t ve Ofis",
    17: "KauÃ§uk ve Plastik",
    18: "Deri ÃœrÃ¼nleri",
    19: "YapÄ± Malzemeleri",
    20: "Mobilya",
    21: "Ev EÅŸyalarÄ±",
    22: "Halatlar ve Ã‡adÄ±rlar",
    23: "Ä°plikler",
    24: "Tekstil",
    25: "Giyim",
    26: "Aksesuarlar",
    27: "HalÄ±lar",
    28: "Oyunlar ve Oyuncaklar",
    29: "Et ve SÃ¼t ÃœrÃ¼nleri",
    30: "GÄ±da ÃœrÃ¼nleri",
    31: "TarÄ±m ÃœrÃ¼nleri",
    32: "Ä°Ã§ecekler",
    33: "AlkollÃ¼ Ä°Ã§ecekler",
    34: "TÃ¼tÃ¼n",
    35: "ReklamcÄ±lÄ±k",
    36: "SigortacÄ±lÄ±k ve Finans",
    37: "Ä°nÅŸaat",
    38: "TelekomÃ¼nikasyon",
    39: "TaÅŸÄ±macÄ±lÄ±k",
    40: "Ãœretim",
    41: "EÄŸitim ve EÄŸlence",
    42: "Bilimsel ve Teknolojik Hizmetler",
    43: "Yiyecek ve Konaklama",
    44: "TÄ±bbi Hizmetler",
    45: "Hukuki Hizmetler",
    99: "Global Marka (TÃ¼m SÄ±nÄ±flar)",
}


def parse_classes_text(text: str) -> list:
    """
    Parse classes from text input.
    Accepts formats: "9,35,42" or "9, 35, 42" or "9 35 42"
    Supports Class 99 (Global Brand) which covers all 45 classes.
    """
    if not text:
        return []

    parts = re.split(r"[,\s]+", text.strip())
    classes = []

    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            num = int(part)
            if (1 <= num <= 45) or num == GLOBAL_CLASS:
                classes.append(num)
        except ValueError:
            pass

    return sorted(list(set(classes)))


def get_class_name(class_num: int, lang: str = "tr") -> str:
    """Get name for a Nice class in the requested language."""
    if lang == "tr":
        return NICE_CLASS_NAMES_TR.get(class_num, f"Sinif {class_num}")
    return NICE_CLASS_NAMES.get(class_num, f"Class {class_num}")


async def validate_classes(classes_text: str = Form(..., description="Nice siniflari (ornek: 9, 35, 42)")):
    """
    Validate and parse Nice class input.
    Returns parsed classes and any validation errors.
    """
    parts = re.split(r"[,\s]+", classes_text.strip())
    valid_classes = []
    invalid_entries = []

    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            num = int(part)
            if 1 <= num <= 45:
                valid_classes.append(num)
            else:
                invalid_entries.append({"value": num, "reason": "1-45 arasi olmali"})
        except ValueError:
            invalid_entries.append({"value": part, "reason": "Gecerli sayi degil"})

    valid_classes = sorted(list(set(valid_classes)))

    if not valid_classes:
        message = "Gecerli sinif bulunamadi"
    elif invalid_entries:
        invalid_str = ", ".join(str(entry["value"]) for entry in invalid_entries)
        message = f"{len(valid_classes)} gecerli sinif, {len(invalid_entries)} gecersiz ({invalid_str})"
    else:
        class_names = [f"{class_num} ({get_class_name(class_num)})" for class_num in valid_classes]
        message = f"{len(valid_classes)} sinif secildi: {', '.join(class_names)}"

    return {
        "valid": len(valid_classes) > 0,
        "classes": valid_classes,
        "classes_with_names": [
            {"number": class_num, "name_tr": get_class_name(class_num, "tr"), "name_en": get_class_name(class_num, "en")}
            for class_num in valid_classes
        ],
        "invalid": invalid_entries,
        "count": len(valid_classes),
        "message": message,
    }


async def get_nice_classes(lang: str = "tr"):
    """
    Return all Nice classes with names for reference.
    Supports Turkish (tr) and English (en).
    Includes Class 99 (Global Brand) which covers all 45 classes.
    """
    names = NICE_CLASS_NAMES_TR if lang == "tr" else NICE_CLASS_NAMES
    standard_classes = [(num, name) for num, name in sorted(names.items()) if num <= 45]
    special_classes = [(num, name) for num, name in sorted(names.items()) if num > 45]

    return {
        "language": lang,
        "total": 45,
        "total_with_special": len(names),
        "classes": [{"number": num, "name": name} for num, name in standard_classes],
        "special_classes": [
            {"number": num, "name": name, "description": "Covers all 45 classes"}
            for num, name in special_classes
        ],
    }


async def suggest_nice_classes(request: ClassSuggestionRequest):
    """
    Suggest relevant Nice classes based on goods/services description.

    Uses semantic embedding similarity against Nice class descriptions.
    Supports both Turkish and English input (multilingual model).
    """
    from services.nice_class_service import run_nice_class_suggestion

    payload = await run_nice_class_suggestion(
        description=request.description,
        top_k=request.top_k,
        lang=request.lang,
        settings=settings,
        logger=MODULE_LOGGER,
        class_name_getter=get_class_name,
    )
    return ClassSuggestionResponse(**payload)


def register_nice_class_routes(app):
    """Register Nice Classification endpoints on the legacy FastAPI app."""
    app.add_api_route("/api/validate-classes", validate_classes, methods=["POST"], tags=["Nice Classification"])
    app.add_api_route("/api/nice-classes", get_nice_classes, methods=["GET"], tags=["Nice Classification"])
    app.add_api_route(
        "/api/suggest-classes",
        suggest_nice_classes,
        methods=["POST"],
        response_model=ClassSuggestionResponse,
        tags=["Nice Classification"],
    )

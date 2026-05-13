"""Nice Classification models, helpers, and routes for the legacy FastAPI app."""

import logging
import re
from typing import List, Optional

from fastapi import Depends, Form, HTTPException, Request
from pydantic import BaseModel, Field

from auth.authentication import CurrentUser, get_current_user_optional
from config.settings import settings
from utils.anon_quota import (
    ANON_CLASS_SUGGEST_DAILY_LIMIT,
    _client_ip_from_request,
    check_and_consume_anon_class_suggest,
)
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
    reason: Optional[str] = None


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
    4: "Yağlar ve Yakıtlar",
    5: "Eczacılık Ürünleri",
    6: "Metaller",
    7: "Makineler",
    8: "El Aletleri",
    9: "Bilgisayar ve Elektronik",
    10: "Tıbbi Cihazlar",
    11: "Aydınlatma ve Isıtma",
    12: "Taşıtlar",
    13: "Ateşli Silahlar",
    14: "Mücevherat",
    15: "Müzik Aletleri",
    16: "Kağıt ve Ofis",
    17: "Kauçuk ve Plastik",
    18: "Deri Ürünleri",
    19: "Yapı Malzemeleri",
    20: "Mobilya",
    21: "Ev Eşyaları",
    22: "Halatlar ve Çadırlar",
    23: "İplikler",
    24: "Tekstil",
    25: "Giyim",
    26: "Aksesuarlar",
    27: "Halılar",
    28: "Oyunlar ve Oyuncaklar",
    29: "Et ve Süt Ürünleri",
    30: "Gıda Ürünleri",
    31: "Tarım Ürünleri",
    32: "İçecekler",
    33: "Alkollü İçecekler",
    34: "Tütün",
    35: "Reklamcılık",
    36: "Sigortacılık ve Finans",
    37: "İnşaat",
    38: "Telekomünikasyon",
    39: "Taşımacılık",
    40: "Üretim",
    41: "Eğitim ve Eğlence",
    42: "Bilimsel ve Teknolojik Hizmetler",
    43: "Yiyecek ve Konaklama",
    44: "Tıbbi Hizmetler",
    45: "Hukuki Hizmetler",
    99: "Global Marka (Tüm Sınıflar)",
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
        return NICE_CLASS_NAMES_TR.get(class_num, f"Sınıf {class_num}")
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


async def suggest_nice_classes(
    request: Request,
    payload_in: ClassSuggestionRequest,
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    """Suggest Nice classes from a goods/services description.

    Access policy:
      * Anonymous: ``ANON_CLASS_SUGGEST_DAILY_LIMIT`` calls per IP per day,
        then 401. Lets prospects taste the feature once a day.
      * Authenticated: requires AI credits. Free plan has zero credits ⇒ 402
        ``credits_exhausted`` ⇒ frontend prompts upgrade. Paid plans deduct
        1 credit from the shared ``monthly_ai_credits`` pool per call.
    """
    from services.nice_class_service import run_nice_class_suggestion

    org_id: Optional[str] = None
    if current_user is None:
        ip = _client_ip_from_request(request)
        allowed, remaining = check_and_consume_anon_class_suggest(ip)
        if not allowed:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "anon_limit_reached",
                    "upgrade_context": "class_suggestions",
                    "anon_daily_limit": ANON_CLASS_SUGGEST_DAILY_LIMIT,
                    "message": (
                        "Ücretsiz deneme hakkınız bugün için doldu. "
                        "Devam etmek için giriş yapın veya bir plana abone olun."
                    ),
                    "message_en": (
                        f"Anonymous daily limit ({ANON_CLASS_SUGGEST_DAILY_LIMIT}) "
                        "reached. Sign in or subscribe to a plan to continue."
                    ),
                },
            )
    else:
        # Authenticated path: gate on the AI-credits pool (shared with Name Lab).
        # Superadmins bypass the gate, mirroring Tasarım/Locarno.
        from database.crud import Database
        from services.creative_service import _is_superadmin_user
        from utils.subscription import check_ai_credit_eligibility

        is_superadmin = _is_superadmin_user(current_user)
        org_id = str(getattr(current_user, "organization_id", "") or "")
        if not is_superadmin:
            if not org_id:
                raise HTTPException(status_code=403, detail={
                    "error": "no_organization",
                    "message": "Hesabınız bir organizasyona bağlı değil.",
                    "message_en": "Account is not linked to an organization.",
                })
            with Database() as db:
                can_use, reason, details = check_ai_credit_eligibility(db, org_id, cost=1)
            if not can_use:
                status_code = 403 if reason == "upgrade_required" else 402
                # Tailor the upgrade modal to class suggestion specifically
                # rather than the generic AI-credits framing.
                if isinstance(details, dict):
                    details["upgrade_context"] = "class_suggestions"
                raise HTTPException(status_code=status_code, detail=details)
        else:
            # Skip deduction for superadmin
            org_id = ""

    payload = await run_nice_class_suggestion(
        description=payload_in.description,
        top_k=payload_in.top_k,
        lang=payload_in.lang,
        settings=settings,
        logger=MODULE_LOGGER,
        class_name_getter=get_class_name,
    )

    # Deduct AFTER a successful retrieval so failed LLM calls don't burn
    # credits. Anonymous path doesn't deduct from any pool.
    if current_user is not None and org_id:
        from database.crud import Database
        from utils.subscription import deduct_name_credit
        with Database() as db:
            deduct_name_credit(db, org_id)

    return ClassSuggestionResponse(**payload)


def register_nice_class_routes(app, limiter=None):
    """Register Nice Classification endpoints on the legacy FastAPI app.

    ``limiter`` is the slowapi.Limiter instance used by the rest of the app.
    The class-suggester is rate-limited to mirror the Tasarım/Locarno
    counterpart and to add a safety ceiling on top of the per-IP anon quota.
    """
    app.add_api_route("/api/validate-classes", validate_classes, methods=["POST"], tags=["Nice Classification"])
    app.add_api_route("/api/nice-classes", get_nice_classes, methods=["GET"], tags=["Nice Classification"])

    if limiter is not None:
        @app.post(
            "/api/suggest-classes",
            response_model=ClassSuggestionResponse,
            tags=["Nice Classification"],
        )
        @limiter.limit("20/minute")
        async def _suggest_nice_classes_limited(
            request: Request,
            payload_in: ClassSuggestionRequest,
            current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
        ):
            return await suggest_nice_classes(request, payload_in, current_user)
    else:
        # Test / lightweight environments without a configured limiter.
        app.add_api_route(
            "/api/suggest-classes",
            suggest_nice_classes,
            methods=["POST"],
            response_model=ClassSuggestionResponse,
            tags=["Nice Classification"],
        )

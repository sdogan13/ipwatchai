"""Locarno class suggestion service — Gemini-backed.

Given a free-text description of an industrial design, asks Gemini to pick
the top relevant Locarno top-level classes (01..32). Mirrors the public
shape of ``services.creative_service.suggest_names_data`` but is much
lighter — no DB validation, no caching, no batch sizing.

Costs: 1 AI credit per successful suggestion call. Quota is enforced through
the existing ``deduct_name_credit`` /
``check_name_generation_eligibility`` helpers (same pool as Name Lab — keeps
the credits model coherent for v1).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from database.crud import Database


logger = logging.getLogger("turkpatent.locarno_suggest")


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class LocarnoSuggestionRequest(BaseModel):
    description: str = Field(..., min_length=2, max_length=500)
    language: str = Field(default="tr")  # 'tr' or 'en'
    count: int = Field(default=5, ge=1, le=10)


class LocarnoSuggestion(BaseModel):
    class_number: str
    name_tr: Optional[str] = None
    name_en: Optional[str] = None
    reason: Optional[str] = None


class LocarnoSuggestionResponse(BaseModel):
    suggestions: List[LocarnoSuggestion]
    description: str
    language: str
    cached: bool = False
    credits_remaining: Optional[int] = None


# ---------------------------------------------------------------------------
# Locarno taxonomy loader (DB)
# ---------------------------------------------------------------------------

def _load_locarno_taxonomy(db) -> List[Dict[str, str]]:
    cur = db.cursor()
    cur.execute(
        """
        SELECT class_number, name_tr, name_en
        FROM locarno_classes_lookup
        ORDER BY class_number ASC
        """
    )
    rows = cur.fetchall()
    return [
        {
            "class_number": r["class_number"] if isinstance(r, dict) else r[0],
            "name_tr": (r["name_tr"] if isinstance(r, dict) else r[1]) or "",
            "name_en": (r["name_en"] if isinstance(r, dict) else r[2]) or "",
        }
        for r in rows
    ]


def _build_prompt(description: str, taxonomy: List[Dict[str, str]], *, language: str, count: int) -> str:
    """Build a prompt that asks the model to return JSON suggestions."""
    name_field = "name_tr" if language == "tr" else "name_en"
    taxonomy_lines = "\n".join(
        f"- {c['class_number']}: {c[name_field] or c['name_en']}"
        for c in taxonomy
    )
    return (
        "You are an expert in the Locarno International Classification for industrial designs. "
        "Given a free-text description of a product or design, choose the top {count} most relevant "
        "Locarno top-level classes from the list below.\n\n"
        "Return a JSON object with a single key \"suggestions\" containing an array of "
        "objects with keys \"class_number\" (two-digit string from the list) and "
        "\"reason\" (short justification, in {lang_label}). "
        "Order by relevance descending. Only include classes that are actually relevant — "
        "if fewer than {count} are relevant, return fewer.\n\n"
        "Locarno classes:\n{taxonomy}\n\n"
        "Description: {description}\n"
    ).format(
        count=count,
        lang_label="Turkish" if language == "tr" else "English",
        taxonomy=taxonomy_lines,
        description=description.strip(),
    )


def _parse_suggestions(
    raw: Dict[str, Any],
    taxonomy: List[Dict[str, str]],
    *,
    max_count: int,
) -> List[Dict[str, Any]]:
    """Validate Gemini's response and enrich with localized names."""
    raw_items = raw.get("suggestions") if isinstance(raw, dict) else None
    if not isinstance(raw_items, list):
        return []
    by_class = {c["class_number"]: c for c in taxonomy}
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        cn = str(item.get("class_number") or "").strip()
        if len(cn) == 1:
            cn = "0" + cn
        if cn not in by_class or cn in seen:
            continue
        seen.add(cn)
        meta = by_class[cn]
        out.append({
            "class_number": cn,
            "name_tr": meta["name_tr"],
            "name_en": meta["name_en"],
            "reason": str(item.get("reason") or "").strip()[:300] or None,
        })
        if len(out) >= max_count:
            break
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def suggest_locarno_classes_data(
    *,
    request: LocarnoSuggestionRequest,
    current_user,
    database_factory=Database,
    gemini_client_getter=None,
) -> LocarnoSuggestionResponse:
    """Run Gemini against the Locarno taxonomy and return ranked suggestions."""
    if gemini_client_getter is None:
        from generative_ai.gemini_client import get_gemini_client
        gemini_client_getter = get_gemini_client

    # Quota / credit check — reuse Name Lab pool for v1
    from services.creative_service import (
        check_name_generation_eligibility,
        deduct_name_credit,
        increment_name_generation_usage,
        _is_superadmin_user,
    )

    org_id = str(current_user.organization_id)
    user_id = str(current_user.id)
    is_superadmin = _is_superadmin_user(current_user)

    if not is_superadmin:
        with database_factory() as db:
            can_generate, reason, details = check_name_generation_eligibility(db, org_id, 0)
        if not can_generate:
            status_code = 403 if reason == "upgrade_required" else 402
            raise HTTPException(status_code=status_code, detail=details)

    # Load Locarno taxonomy from DB
    with database_factory() as db:
        taxonomy = _load_locarno_taxonomy(db)
    if not taxonomy:
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "Locarno taxonomy not loaded"},
        )

    client = gemini_client_getter()
    if not client.is_available():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_unavailable",
                "message": "Sınıf önerme servisi şu anda kullanılamıyor.",
                "message_en": "Class suggestion service is currently unavailable.",
            },
        )

    prompt = _build_prompt(
        description=request.description,
        taxonomy=taxonomy,
        language=request.language,
        count=request.count,
    )

    try:
        raw = await client.generate_json(prompt=prompt, max_output_tokens=2048, temperature=0.2)
    except Exception as exc:  # noqa: BLE001
        logger.error("locarno_suggest_failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generation_failed",
                "message": "Sınıf önerisi alınamadı. Lütfen tekrar deneyin.",
                "message_en": f"Locarno suggestion failed: {exc}",
            },
        ) from exc

    suggestions = _parse_suggestions(raw, taxonomy, max_count=request.count)
    if not suggestions:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "no_suggestions",
                "message": "Açıklamaya uygun sınıf bulunamadı.",
                "message_en": "No relevant classes found for the description.",
            },
        )

    # Deduct credit on successful response
    credits_remaining = None
    if not is_superadmin:
        with database_factory() as db:
            try:
                deduct_name_credit(db, org_id, user_id, cost=1)
                increment_name_generation_usage(db, org_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("locarno_suggest_credit_deduct_failed: %s", exc)

    return LocarnoSuggestionResponse(
        suggestions=[LocarnoSuggestion(**s) for s in suggestions],
        description=request.description,
        language=request.language,
        cached=False,
        credits_remaining=credits_remaining,
    )

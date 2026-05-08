"""Locarno class suggestion service — Gemini-backed.

Given a free-text description of an industrial design, asks Gemini to pick
the top relevant Locarno top-level classes (01..32). Mirrors the public
shape of ``services.creative_service.suggest_names_data`` but is much
lighter — no DB validation, no caching, no batch sizing.

No AI-credit gating: a single text-only Gemini completion is cheap and the
feature is core to the Tasarım search UX. Abuse is bounded by the route-
level rate limit (20/min) configured on the public endpoint.
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
    similarity: float = 0.0


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
        "objects with keys:\n"
        "  - class_number (two-digit string from the list)\n"
        "  - confidence (number 0..1 — 1.0 = perfectly fits, 0.0 = irrelevant)\n"
        "  - reason (short justification, in {lang_label})\n\n"
        "Order by confidence descending. Only include classes that are actually relevant — "
        "if fewer than {count} are relevant, return fewer.\n\n"
        "Expected JSON shape:\n"
        '{{"suggestions":[{{"class_number":"26","confidence":0.92,"reason":"…"}}]}}\n\n'
        "Locarno classes:\n{taxonomy}\n\n"
        "Description: {description}\n"
    ).format(
        count=count,
        lang_label="Turkish" if language == "tr" else "English",
        taxonomy=taxonomy_lines,
        description=description.strip(),
    )


def _coerce_confidence(value) -> float:
    """Confidence -> 0..1 float. Mirrors nice_class_service._coerce_confidence."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN
        return 0.0
    return max(0.0, min(1.0, v))


def _parse_suggestions(
    raw: Dict[str, Any],
    taxonomy: List[Dict[str, str]],
    *,
    max_count: int,
) -> List[Dict[str, Any]]:
    """Validate the model's response, enrich with localized names + clamp confidence."""
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
        confidence = _coerce_confidence(
            item.get("confidence", item.get("similarity", item.get("score")))
        )
        out.append({
            "class_number": cn,
            "name_tr": meta["name_tr"],
            "name_en": meta["name_en"],
            "reason": str(item.get("reason") or "").strip()[:300] or None,
            "similarity": round(confidence, 4),
        })
        if len(out) >= max_count:
            break
    out.sort(key=lambda c: c["similarity"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def suggest_locarno_classes_data(
    *,
    request: LocarnoSuggestionRequest,
    current_user,
    database_factory=Database,
    qwen_client_getter=None,
    gemini_client_getter=None,
) -> LocarnoSuggestionResponse:
    """Run Qwen-flash (primary) → Gemini-2.5-flash-lite (fallback) against the
    Locarno taxonomy and return ranked suggestions.

    No AI-credit gating: a single text-only call is cheap and the feature is
    core to the Tasarım search UX. Abuse is bounded by the route-level rate
    limit (20/min in app_design_search_routes.py).

    Provider order mirrors ``services.nice_class_service.suggest_classes_data``:
    Qwen is tried first; if it fails (auth, network, JSON parse), the Gemini
    client is tried. Both failures → 503 with the combined error detail.
    """
    if qwen_client_getter is None:
        from generative_ai.qwen_client import get_qwen_client
        qwen_client_getter = get_qwen_client
    if gemini_client_getter is None:
        from generative_ai.gemini_client import get_gemini_client
        gemini_client_getter = get_gemini_client

    # Load Locarno taxonomy from DB
    with database_factory() as db:
        taxonomy = _load_locarno_taxonomy(db)
    if not taxonomy:
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "Locarno taxonomy not loaded"},
        )

    prompt = _build_prompt(
        description=request.description,
        taxonomy=taxonomy,
        language=request.language,
        count=request.count,
    )

    raw = None
    provider_errors = []

    # 1) Qwen flash (primary)
    try:
        qwen_client = qwen_client_getter()
        if qwen_client and qwen_client.is_available():
            raw = await qwen_client.generate_json(
                prompt=prompt, max_output_tokens=2048, temperature=0.2,
                model="qwen-flash",
            )
        else:
            provider_errors.append("qwen: not configured")
    except Exception as exc:  # noqa: BLE001
        logger.warning("locarno_suggest_qwen_failed: %s", exc)
        provider_errors.append(f"qwen: {exc}")
        raw = None

    # 2) Gemini fallback
    if raw is None:
        try:
            gemini_client = gemini_client_getter()
            if not gemini_client or not gemini_client.is_available():
                provider_errors.append("gemini: not configured")
                raise RuntimeError("no available providers")
            raw = await gemini_client.generate_json(
                prompt=prompt, max_output_tokens=2048, temperature=0.2,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("locarno_suggest_failed: providers=%s", provider_errors + [f"gemini: {exc}"])
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "generation_failed",
                    "message": "Sınıf önerisi alınamadı. Lütfen tekrar deneyin.",
                    "message_en": "Locarno suggestion failed: " + "; ".join(provider_errors + [str(exc)]),
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

    return LocarnoSuggestionResponse(
        suggestions=[LocarnoSuggestion(**s) for s in suggestions],
        description=request.description,
        language=request.language,
        cached=False,
        credits_remaining=None,
    )

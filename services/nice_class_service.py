"""Nice-class service helpers used by HTTP route modules."""

from __future__ import annotations

import json
import time
from typing import Any

import psycopg2
import psycopg2.extras
from fastapi import HTTPException


DEFAULT_QWEN_CLASS_MODEL = "qwen-flash"
DEFAULT_GEMINI_CLASS_FALLBACK_MODEL = "gemini-2.5-flash-lite"
CLASS_SUGGESTION_MAX_OUTPUT_TOKENS = 1200

CLASS_SUGGESTION_SYSTEM_PROMPT = (
    "You are a Nice Classification assistant. Match trademark goods/services to "
    "Nice classes using only the supplied class catalogue. Return only valid JSON."
)


def _get_creative_setting(settings, name: str, default: str) -> str:
    creative = getattr(settings, "creative", None)
    if creative is not None:
        value = getattr(creative, name, None)
        if value:
            return str(value)

    value = getattr(settings, name, None)
    if value:
        return str(value)

    return default


def _is_forbidden_qwen_class_model(model: str | None) -> bool:
    return bool(model) and str(model).strip().lower().startswith("qwen-max")


def _db_connect(settings, connect_fn):
    return connect_fn(
        host=settings.database.host,
        port=settings.database.port,
        database=settings.database.name,
        user=settings.database.user,
        password=settings.database.password,
    )


def _row_value(row, key: str, default=None):
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _catalogue_description(row, lang: str) -> str:
    lang = (lang or "tr").lower()
    if lang.startswith("tr"):
        candidates = ("description_tr", "description", "description_en")
    elif lang.startswith("en"):
        candidates = ("description_en", "description", "description_tr")
    else:
        candidates = ("description", "description_tr", "description_en")

    for key in candidates:
        value = _row_value(row, key)
        if value:
            return str(value)
    return ""


def _catalogue_name(row, lang: str) -> str:
    lang = (lang or "tr").lower()
    if lang.startswith("tr"):
        candidates = ("name_tr", "name_en")
    elif lang.startswith("en"):
        candidates = ("name_en", "name_tr")
    else:
        candidates = ("name_en", "name_tr")

    for key in candidates:
        value = _row_value(row, key)
        if value:
            return str(value)

    return f"Class {_row_value(row, 'class_number', '')}".strip()


def _truncate_description(description: str, max_length: int = 200) -> str:
    if len(description) <= max_length:
        return description
    return description[:max_length] + "..."


def _load_class_catalogue(settings, connect_fn) -> list[dict[str, Any]]:
    conn = None
    cur = None
    try:
        conn = _db_connect(settings, connect_fn)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT
                class_number,
                name_tr,
                name_en,
                description
            FROM nice_classes_lookup
            WHERE class_number BETWEEN 1 AND 45
            ORDER BY class_number
            """
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _build_class_suggestion_prompt(
    *,
    description: str,
    trademark_name: str | None,
    top_k: int,
    lang: str,
    catalogue: list[dict[str, Any]],
) -> str:
    catalogue_payload = [
        {
            "class_number": int(row["class_number"]),
            "name": _catalogue_name(row, lang),
            "description": _catalogue_description(row, lang),
        }
        for row in catalogue
        if _row_value(row, "class_number") is not None
    ]
    input_payload = {
        "trademark_name": trademark_name or "",
        "goods_services_description": description,
        "language": lang,
        "top_k": top_k,
        "nice_class_catalogue": catalogue_payload,
    }

    return (
        "Choose the most relevant Nice classes for the input goods/services.\n"
        "Rules:\n"
        "- Use only class_number values 1 through 45 from nice_class_catalogue.\n"
        "- Return at most top_k suggestions.\n"
        "- confidence must be a number from 0 to 1.\n"
        "- Do not include class 99 or any class not supplied in the catalogue.\n"
        "- Return a single JSON object and no prose.\n\n"
        "Expected JSON shape:\n"
        '{"suggestions":[{"class_number":42,"confidence":0.91}]}\n\n'
        "Input JSON:\n"
        f"{json.dumps(input_payload, ensure_ascii=False)}"
    )


def _coerce_class_number(value) -> int | None:
    try:
        class_number = int(value)
    except (TypeError, ValueError):
        return None

    if 1 <= class_number <= 45:
        return class_number
    return None


def _coerce_confidence(value) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0

    return max(0.0, min(1.0, confidence))


def _normalise_provider_suggestions(
    raw_payload: dict[str, Any],
    *,
    catalogue_by_number: dict[int, dict[str, Any]],
    top_k: int,
    lang: str,
    class_name_getter,
) -> list[dict[str, Any]]:
    raw_suggestions = raw_payload.get("suggestions") if isinstance(raw_payload, dict) else None
    if not isinstance(raw_suggestions, list):
        return []

    seen: set[int] = set()
    suggestions: list[dict[str, Any]] = []
    for item in raw_suggestions:
        if not isinstance(item, dict):
            continue

        class_number = _coerce_class_number(item.get("class_number"))
        if class_number is None or class_number in seen or class_number not in catalogue_by_number:
            continue

        confidence = _coerce_confidence(
            item.get("confidence", item.get("similarity", item.get("score")))
        )
        catalogue_row = catalogue_by_number[class_number]
        description = _catalogue_description(catalogue_row, lang)
        suggestions.append(
            {
                "class_number": class_number,
                "class_name": class_name_getter(class_number, lang),
                "similarity": round(confidence, 4),
                "description": _truncate_description(description),
            }
        )
        seen.add(class_number)

    suggestions.sort(key=lambda item: item["similarity"], reverse=True)
    return suggestions[:top_k]


async def _generate_with_provider(
    *,
    provider_name: str,
    client_getter,
    model: str,
    prompt: str,
    catalogue_by_number: dict[int, dict[str, Any]],
    top_k: int,
    lang: str,
    class_name_getter,
) -> list[dict[str, Any]]:
    client = client_getter()
    if client is None or not client.is_available():
        raise RuntimeError(f"{provider_name} client is not available")

    raw_payload = await client.generate_json(
        prompt=prompt,
        system_prompt=CLASS_SUGGESTION_SYSTEM_PROMPT,
        max_output_tokens=CLASS_SUGGESTION_MAX_OUTPUT_TOKENS,
        temperature=0.1,
        model=model,
    )
    suggestions = _normalise_provider_suggestions(
        raw_payload,
        catalogue_by_number=catalogue_by_number,
        top_k=top_k,
        lang=lang,
        class_name_getter=class_name_getter,
    )
    if not suggestions:
        raise RuntimeError(f"{provider_name} returned no valid class suggestions")
    return suggestions


def _log_provider_skip(logger, message: str) -> None:
    if not logger:
        return
    try:
        logger.warning(message)
    except Exception:
        pass


async def run_nice_class_suggestion(
    description,
    top_k,
    lang,
    settings,
    logger=None,
    class_name_getter=None,
    text_embedding_getter=None,
    connect_fn=None,
    timer=None,
    qwen_client_getter=None,
    gemini_client_getter=None,
    trademark_name=None,
):
    """Suggest Nice classes from a goods/services description."""
    if class_name_getter is None:
        class_name_getter = lambda class_num, current_lang="tr": f"Class {class_num}"
    if connect_fn is None:
        connect_fn = psycopg2.connect
    if timer is None:
        timer = time.time
    if qwen_client_getter is None:
        from generative_ai.qwen_client import get_qwen_client

        qwen_client_getter = lambda: get_qwen_client(getattr(settings, "creative", None))
    if gemini_client_getter is None:
        from generative_ai.gemini_client import get_gemini_client

        gemini_client_getter = lambda: get_gemini_client(getattr(settings, "creative", None))

    start_time = timer()

    try:
        catalogue = _load_class_catalogue(settings, connect_fn)
    except Exception as exc:
        if logger:
            logger.error("Class catalogue load error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    if not catalogue:
        raise HTTPException(status_code=500, detail="Nice class catalogue is empty")

    catalogue_by_number = {
        int(row["class_number"]): row
        for row in catalogue
        if _coerce_class_number(_row_value(row, "class_number")) is not None
    }
    prompt = _build_class_suggestion_prompt(
        description=description,
        trademark_name=trademark_name,
        top_k=top_k,
        lang=lang,
        catalogue=catalogue,
    )

    provider_errors: list[str] = []
    suggestions: list[dict[str, Any]] | None = None

    qwen_model = _get_creative_setting(
        settings, "qwen_class_model", DEFAULT_QWEN_CLASS_MODEL
    )
    if _is_forbidden_qwen_class_model(qwen_model):
        message = (
            f"Skipping Qwen class suggestion model {qwen_model!r}; "
            "qwen-max is reserved for risk reports."
        )
        provider_errors.append(message)
        _log_provider_skip(logger, message)
    else:
        try:
            suggestions = await _generate_with_provider(
                provider_name="qwen",
                client_getter=qwen_client_getter,
                model=qwen_model,
                prompt=prompt,
                catalogue_by_number=catalogue_by_number,
                top_k=top_k,
                lang=lang,
                class_name_getter=class_name_getter,
            )
        except Exception as exc:
            provider_errors.append(f"qwen: {exc}")
            _log_provider_skip(logger, f"Qwen class suggestion failed: {exc}")

    if suggestions is None:
        gemini_model = _get_creative_setting(
            settings,
            "gemini_class_fallback_model",
            DEFAULT_GEMINI_CLASS_FALLBACK_MODEL,
        )
        try:
            suggestions = await _generate_with_provider(
                provider_name="gemini",
                client_getter=gemini_client_getter,
                model=gemini_model,
                prompt=prompt,
                catalogue_by_number=catalogue_by_number,
                top_k=top_k,
                lang=lang,
                class_name_getter=class_name_getter,
            )
        except Exception as exc:
            provider_errors.append(f"gemini: {exc}")
            if logger:
                logger.error("Gemini class suggestion failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=(
                    "Class suggestion provider unavailable: "
                    + "; ".join(provider_errors)
                ),
            )

    processing_time = (timer() - start_time) * 1000
    return {
        "query": description[:100] + "..." if len(description) > 100 else description,
        "suggestions": suggestions,
        "processing_time_ms": round(processing_time, 2),
    }

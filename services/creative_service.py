"""Service helpers for Creative Suite routes."""

import hashlib
import io
import json
import logging
import math
import os
import sys
import uuid
from pathlib import Path
from typing import Callable, List, Optional
from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import FileResponse
from psycopg2.extras import RealDictCursor

from config.settings import settings
from database.crud import Database
from models.schemas import (
    GenerationHistoryItem,
    GenerationHistoryResponse,
    LogoGenerationRequest,
    LogoGenerationResponse,
    LogoProjectResponse,
    LogoResult,
    NameSuggestionRequest,
    NameSuggestionResponse,
    SafeNameResult,
)
from risk_engine import RISK_THRESHOLDS, calculate_visual_similarity, score_pair
from utils.subscription import (
    check_ai_credit_eligibility,
    check_logo_generation_eligibility,
    check_name_generation_eligibility,
    deduct_logo_credit,
    deduct_name_credit,
    get_org_plan,
    increment_name_generation_usage,
    refund_logo_credit,
)

logger = logging.getLogger(__name__)

_redis_client = None
_risk_engine_instance = None

LOGO_AUDIT_PENDING = "pending"
LOGO_AUDIT_RUNNING = "running"
LOGO_AUDIT_COMPLETED = "completed"
LOGO_AUDIT_FAILED = "failed"


def _get_redis():
    """Get or create Redis client for Creative Suite cache (db=4)."""
    global _redis_client
    if _redis_client is None:
        try:
            import redis

            _redis_client = redis.Redis(
                host=settings.redis.host,
                port=settings.redis.port,
                password=settings.redis.password,
                db=settings.creative.generation_cache_db,
            )
            _redis_client.ping()
        except Exception:
            _redis_client = None
    return _redis_client


def _session_key(org_id: str, query: str) -> str:
    """Build Redis key for a name generation session."""
    query_hash = hashlib.md5(query.lower().strip().encode("utf-8")).hexdigest()[:12]
    return f"namesession:{org_id}:{query_hash}"


def _normalize_name_request_payload(request: NameSuggestionRequest) -> dict:
    """Return the stable request payload used for name cache/session keys."""
    return {
        "query": request.query.strip().lower(),
        "nice_classes": sorted({int(value) for value in request.nice_classes}),
        "industry": request.industry.strip().lower(),
        "style": request.style,
        "language": request.language,
        "avoid_names": sorted({name.strip().lower() for name in request.avoid_names if name.strip()}),
    }


def _name_request_cache_key(request: NameSuggestionRequest) -> str:
    """Build a stable cache/session key so different prompts do not share results."""
    payload = json.dumps(
        _normalize_name_request_payload(request),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"name-request-v2:{payload}"


def _get_loaded_pipeline_ai_module():
    """Return the already-loaded pipeline AI module without triggering model loading."""
    return sys.modules.get("pipeline.ai")


def _logo_visual_audit_available(ai_module=None) -> tuple[bool, str]:
    """Check whether Logo Studio can audit generated logos before exposing the tool."""
    module = ai_module if ai_module is not None else _get_loaded_pipeline_ai_module()
    if module is None:
        return False, "CLIP modeli yuklenmemis"
    if not hasattr(module, "clip_model") or module.clip_model is None:
        return False, "CLIP modeli yuklenmemis"
    if not hasattr(module, "get_clip_embedding_cached"):
        return False, "CLIP gorsel analiz fonksiyonu yuklenmemis"
    return True, ""


def _get_session_count(org_id: str, query: str) -> int:
    """Get how many names have been generated in this session."""
    redis_client = _get_redis()
    if redis_client is None:
        return 0
    try:
        value = redis_client.get(_session_key(org_id, query) + ":count")
        return int(value) if value else 0
    except Exception:
        return 0


def _increment_session_count(org_id: str, query: str, count: int) -> int:
    """Increment the session counter and return the new total."""
    redis_client = _get_redis()
    if redis_client is None:
        return count
    try:
        key = _session_key(org_id, query) + ":count"
        new_value = redis_client.incrby(key, count)
        redis_client.expire(key, settings.creative.generation_cache_ttl)
        return int(new_value)
    except Exception:
        return count


def _simple_similarity(a: str, b: str) -> float:
    """Quick SequenceMatcher ratio for pre-filtering."""
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


def _get_risk_engine():
    """Get or create the RiskEngine singleton."""
    global _risk_engine_instance
    if _risk_engine_instance is None:
        try:
            from risk_engine import RiskEngine

            _risk_engine_instance = RiskEngine()
        except Exception as exc:
            logger.error("Failed to initialize RiskEngine: %s", exc)
            return None
    return _risk_engine_instance


def _batch_validate_names(
    candidate_names: List[str],
    nice_classes: List[int],
    avoid_names: List[str],
    similarity_threshold: float,
) -> List[SafeNameResult]:
    """
    Validate generated names against the trademark database.

    Stage 2 does a fast DB pre-screen, then Stage 3 runs the full RiskEngine
    for translation and cross-language conflicts.
    """
    if not candidate_names:
        return []

    from pipeline import ai
    from db.pool import get_connection, release_connection
    from risk_engine import get_risk_level

    try:
        embeddings = ai.get_text_embeddings_batch_cached(candidate_names)
    except Exception as exc:
        logger.warning("Batch text embedding failed, falling back to individual: %s", exc)
        embeddings = []
        for name in candidate_names:
            try:
                embeddings.append(ai.get_text_embedding_cached(name))
            except Exception:
                embeddings.append(None)

    results: List[SafeNameResult] = []

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        class_filter = ""
        class_params = []
        if nice_classes:
            class_filter = "AND t.nice_class_numbers && %s::int[]"
            class_params = [nice_classes]

        for index, name in enumerate(candidate_names):
            emb = embeddings[index] if index < len(embeddings) else None

            skip = False
            name_lower = name.lower().strip()
            for avoid in avoid_names:
                if _simple_similarity(name_lower, avoid.lower().strip()) > 0.7:
                    skip = True
                    break
            if skip:
                results.append(
                    SafeNameResult(
                        name=name,
                        risk_score=100.0,
                        risk_level="critical",
                        text_similarity=1.0,
                        semantic_similarity=1.0,
                        phonetic_match=True,
                        translation_similarity=0.0,
                        closest_match=avoid,
                        is_safe=False,
                    )
                )
                continue

            try:
                if emb is not None:
                    params_stage2 = [
                        str(emb),
                        name,
                        name,
                        str(emb),
                        name,
                    ] + class_params + [
                        str(emb),
                        name,
                    ]
                    sql_stage2 = f"""
                        SELECT
                            t.name,
                            t.application_no,
                            (1 - (t.text_embedding <=> %s::halfvec)) AS semantic_sim,
                            similarity(t.name, %s) AS trgm_sim,
                            (dmetaphone(t.name) = dmetaphone(%s)) AS phonetic_match
                        FROM trademarks t
                        WHERE t.name IS NOT NULL
                            AND (
                                (1 - (t.text_embedding <=> %s::halfvec)) > 0.3
                                OR similarity(t.name, %s) > 0.3
                            )
                            {class_filter}
                        ORDER BY GREATEST(
                            (1 - (t.text_embedding <=> %s::halfvec)),
                            similarity(t.name, %s)
                        ) DESC
                        LIMIT 10
                    """
                else:
                    params_stage2 = [name, name, name] + class_params + [name]
                    sql_stage2 = f"""
                        SELECT
                            t.name,
                            t.application_no,
                            0.0 AS semantic_sim,
                            similarity(t.name, %s) AS trgm_sim,
                            (dmetaphone(t.name) = dmetaphone(%s)) AS phonetic_match
                        FROM trademarks t
                        WHERE t.name IS NOT NULL
                            AND similarity(t.name, %s) > 0.3
                            {class_filter}
                        ORDER BY similarity(t.name, %s) DESC
                        LIMIT 10
                    """

                cur.execute(sql_stage2, params_stage2)
                matches = cur.fetchall()
            except Exception as exc:
                logger.warning("DB query failed for candidate %s: %s", name, exc)
                matches = []

            closest_name = None
            max_semantic = 0.0
            max_trgm = 0.0
            has_phonetic = False

            for match in matches:
                semantic = float(match.get("semantic_sim", 0) or 0)
                trigram = float(match.get("trgm_sim", 0) or 0)
                phonetic = bool(match.get("phonetic_match", False))

                if semantic > max_semantic:
                    max_semantic = semantic
                if trigram > max_trgm:
                    max_trgm = trigram
                    closest_name = match["name"]
                if semantic > max_semantic - 0.01 and semantic > max_trgm:
                    closest_name = match["name"]
                if phonetic:
                    has_phonetic = True
                    if closest_name is None:
                        closest_name = match["name"]

            stage2_risk_score = max(max_semantic, max_trgm) * 100.0

            stage2_safe = True
            if max_semantic > similarity_threshold:
                stage2_safe = False
            if max_trgm > similarity_threshold:
                stage2_safe = False
            if has_phonetic:
                stage2_safe = False

            engine_score = 0.0
            translation_similarity = 0.0

            try:
                engine = _get_risk_engine()
                if engine:
                    result_dict, _ = engine.assess_brand_risk(
                        name=name,
                        target_classes=nice_classes if nice_classes else None,
                    )
                    engine_score = result_dict.get("final_risk_score", 0)

                    top_candidates = result_dict.get("top_candidates", [])
                    if top_candidates:
                        top = top_candidates[0]
                        top_scores = top.get("scores", {})
                        translation_similarity = top_scores.get("translation_similarity", 0.0)

                        if engine_score * 100.0 > stage2_risk_score:
                            closest_name = top.get("name", closest_name)
                            max_semantic = max(
                                max_semantic,
                                top_scores.get("semantic_similarity", 0.0),
                            )
            except Exception as exc:
                logger.warning("Risk engine check failed for %s: %s", name, exc)

            risk_score = max(stage2_risk_score, engine_score * 100.0)
            if not stage2_safe:
                is_safe = False
            else:
                is_safe = (risk_score / 100.0) < RISK_THRESHOLDS["high"]

            risk_level = get_risk_level(risk_score / 100.0)

            results.append(
                SafeNameResult(
                    name=name,
                    risk_score=round(risk_score, 1),
                    risk_level=risk_level,
                    text_similarity=round(max_trgm, 3),
                    semantic_similarity=round(max_semantic, 3),
                    phonetic_match=has_phonetic,
                    translation_similarity=round(translation_similarity, 3),
                    closest_match=closest_name,
                    is_safe=is_safe,
                )
            )
    finally:
        release_connection(conn)

    return results


def _get_cached_results(org_id: str, query: str) -> Optional[dict]:
    """Get cached name results from Redis."""
    redis_client = _get_redis()
    if redis_client is None:
        return None
    try:
        key = _session_key(org_id, query) + ":results"
        data = redis_client.get(key)
        if data:
            parsed = json.loads(data)
            safe = [SafeNameResult(**item) for item in parsed["safe"]]
            return {
                "safe": safe,
                "filtered_count": parsed["filtered_count"],
                "total_generated": parsed["total_generated"],
            }
    except Exception:
        pass
    return None


def _cache_results(
    org_id: str,
    query: str,
    safe_names: List[SafeNameResult],
    filtered_count: int,
    total_generated: int,
) -> None:
    """Cache name results in Redis."""
    redis_client = _get_redis()
    if redis_client is None:
        return
    try:
        key = _session_key(org_id, query) + ":results"
        data = json.dumps(
            {
                "safe": [item.model_dump() for item in safe_names],
                "filtered_count": filtered_count,
                "total_generated": total_generated,
            },
            ensure_ascii=False,
        )
        redis_client.setex(key, settings.creative.generation_cache_ttl, data)
    except Exception:
        pass


def _get_ai_credits_remaining(org_id: str, cost: int) -> dict:
    """Get current unified AI credit status for the response."""
    try:
        with Database() as db:
            plan = get_org_plan(db, org_id)
            _, _, details = check_ai_credit_eligibility(db, org_id, cost=cost)
        return {
            "current_plan": details.get("current_plan", plan.get("plan_name", "free")),
            "display_name": details.get("display_name", plan.get("display_name", "")),
            "monthly_remaining": details.get("monthly_remaining", 0),
            "purchased_remaining": details.get("purchased_remaining", 0),
            "total_remaining": details.get("total_remaining", 0),
            "monthly_limit": details.get("monthly_limit", 0),
            "cost": cost,
            # Compatibility fields for older UI/tests during the migration.
            "monthly": details.get("monthly_remaining", 0),
            "purchased": details.get("purchased_remaining", 0),
            "plan": details.get("current_plan", plan.get("plan_name", "free")),
        }
    except Exception:
        return {
            "current_plan": "free",
            "display_name": "",
            "monthly_remaining": 0,
            "purchased_remaining": 0,
            "total_remaining": 0,
            "monthly_limit": 0,
            "cost": cost,
            "monthly": 0,
            "purchased": 0,
            "plan": "free",
        }


def _get_plan_credits(org_id: str, session_count: int) -> dict:
    """Get current name-generation credit status for the response."""
    credits = _get_ai_credits_remaining(org_id, cost=1)
    try:
        with Database() as db:
            plan = get_org_plan(db, org_id)
        credits.update(
            {
                "session_limit": plan["name_suggestions_per_session"],
                "used": session_count,
                "plan": plan["plan_name"],
            }
        )
        return credits
    except Exception:
        credits.update(
            {
                "session_limit": 5,
                "used": session_count,
                "plan": credits.get("plan", "free"),
            }
        )
        return credits


def _save_logo_image(image_bytes: bytes, org_id: str, generation_id: str, index: int) -> Optional[str]:
    """Validate and save a generated logo image to disk."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.verify()
    except Exception as exc:
        logger.warning("Invalid image data from Gemini for variation %s: %s", index, exc)
        return None

    image = Image.open(io.BytesIO(image_bytes))
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")

    base_dir = Path(settings.creative.logo_output_dir) / org_id / generation_id
    os.makedirs(base_dir, exist_ok=True)

    file_path = base_dir / f"variation_{index + 1}.png"
    image.save(str(file_path), format="PNG")
    logger.info("Saved logo image to %s", file_path)
    return str(file_path)


def _generate_all_visual_features(image_path: str) -> dict:
    """Generate CLIP, DINOv2, and OCR features for a generated logo."""
    from pipeline import ai

    features = {
        "clip_embedding": None,
        "dino_embedding": None,
        "ocr_text": "",
    }

    try:
        features["clip_embedding"] = ai.get_clip_embedding_cached(image_path)
    except Exception as exc:
        logger.warning("CLIP embedding failed for %s: %s", image_path, exc)

    try:
        features["dino_embedding"] = ai.get_dino_embedding_cached(image_path)
    except Exception as exc:
        logger.warning("DINOv2 embedding failed for %s: %s", image_path, exc)

    try:
        if hasattr(ai, "ocr_reader") and ai.ocr_reader is not None:
            texts = ai.ocr_reader.readtext(image_path, detail=0, paragraph=True)
            features["ocr_text"] = " ".join(texts).lower().strip() if texts else ""
    except Exception as exc:
        logger.warning("OCR extraction failed for %s: %s", image_path, exc)

    return features


def _cosine_sim(vec1, vec2) -> float:
    """Return cosine similarity between two vectors."""
    import numpy as np

    vector1 = np.array(vec1, dtype=np.float32)
    vector2 = np.array(vec2, dtype=np.float32)

    norm1 = np.linalg.norm(vector1)
    norm2 = np.linalg.norm(vector2)
    if norm1 == 0 or norm2 == 0:
        return 0.0

    return float(np.dot(vector1, vector2) / (norm1 * norm2))


def _build_visual_breakdown(
    *,
    clip_sim: float,
    dinov2_sim: float,
    color_sim: float = 0.0,
    ocr_text_a: str = "",
    ocr_text_b: str = "",
) -> dict:
    """Build a stable visual-breakdown payload for logo similarity results."""
    from difflib import SequenceMatcher

    from utils.idf_scoring import normalize_turkish

    if ocr_text_a and ocr_text_b:
        ocr_score = SequenceMatcher(
            None,
            normalize_turkish(ocr_text_a),
            normalize_turkish(ocr_text_b),
        ).ratio()
    else:
        ocr_score = 0.0

    components_used = []
    if clip_sim:
        components_used.append("clip")
    if dinov2_sim:
        components_used.append("dino")
    if color_sim:
        components_used.append("color")
    if ocr_score:
        components_used.append("ocr")

    raw_combined = calculate_visual_similarity(
        clip_sim=clip_sim,
        dinov2_sim=dinov2_sim,
        color_sim=color_sim,
        ocr_text_a=ocr_text_a,
        ocr_text_b=ocr_text_b,
    )
    return {
        "clip_score": round(float(clip_sim), 4),
        "dino_score": round(float(dinov2_sim), 4),
        "ocr_score": round(float(ocr_score), 4),
        "raw_combined": round(float(raw_combined), 4),
        "components_used": components_used,
    }


def _full_visual_similarity_search(
    features: dict,
    nice_classes: List[int],
    brand_name: str = "",
    top_k: int = 5,
) -> list:
    """Search the trademark database using the generated logo's visual features."""
    from db.pool import get_connection, release_connection

    clip_embedding = features.get("clip_embedding")
    if not clip_embedding:
        return []

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        embedding_str = str(clip_embedding)
        class_filter = ""
        params = [embedding_str, embedding_str]
        if nice_classes:
            class_filter = "AND t.nice_class_numbers && %s::int[]"
            params.append(nice_classes)
        params.append(embedding_str)

        cur.execute(
            f"""
            SELECT
                t.name,
                t.application_no,
                t.bulletin_no,
                t.image_path,
                t.logo_ocr_text,
                t.dinov2_embedding,
                (1 - (t.image_embedding <=> %s::halfvec)) AS raw_clip_sim
            FROM trademarks t
            WHERE t.image_embedding IS NOT NULL
                AND (1 - (t.image_embedding <=> %s::halfvec)) > 0.25
                {class_filter}
            ORDER BY t.image_embedding <=> %s::halfvec
            LIMIT {top_k * 2}
        """,
            params,
        )
        rows = cur.fetchall()

        dino_embedding = features.get("dino_embedding")
        ocr_text = features.get("ocr_text", "")
        results = []

        for row in rows:
            clip_sim = float(row.get("raw_clip_sim", 0) or 0)
            dino_sim = 0.0
            candidate_dino = row.get("dinov2_embedding")
            if dino_embedding and candidate_dino:
                try:
                    if isinstance(candidate_dino, str):
                        candidate_dino = json.loads(candidate_dino)
                    dino_sim = _cosine_sim(dino_embedding, candidate_dino)
                except Exception:
                    dino_sim = 0.0

            candidate_ocr = (row.get("logo_ocr_text") or "").lower().strip()
            breakdown = _build_visual_breakdown(
                clip_sim=clip_sim,
                dinov2_sim=dino_sim,
                color_sim=0.0,
                ocr_text_a=ocr_text,
                ocr_text_b=candidate_ocr,
            )
            combined_sim = breakdown["raw_combined"]

            if ocr_text and row.get("name"):
                try:
                    score_pair_result = score_pair(
                        query_name=ocr_text,
                        candidate_name=row["name"],
                        visual_sim=combined_sim,
                    )
                    combined_sim = max(combined_sim, score_pair_result.get("total", 0))
                except Exception:
                    pass

            results.append(
                {
                    "name": row.get("name"),
                    "application_no": row.get("application_no"),
                    "bulletin_no": row.get("bulletin_no"),
                    "image_path": row.get("image_path"),
                    "combined_sim": combined_sim,
                    "visual_breakdown": {
                        "clip": breakdown["clip_score"],
                        "dino": breakdown["dino_score"],
                        "ocr": breakdown["ocr_score"],
                        "raw_combined": breakdown["raw_combined"],
                        "components_used": breakdown["components_used"],
                    },
                }
            )

        results.sort(key=lambda item: item["combined_sim"], reverse=True)
        return results[:top_k]
    except Exception as exc:
        logger.error("Full visual similarity search failed: %s", exc)
        return []
    finally:
        release_connection(conn)


def _store_generated_image(
    generation_log_id: str,
    org_id: str,
    image_path: str,
    clip_embedding: Optional[list],
    similarity_score: float,
    is_safe: bool,
    dino_embedding: Optional[list] = None,
    ocr_text: Optional[str] = None,
    visual_breakdown: Optional[dict] = None,
    project_id: Optional[str] = None,
    parent_image_id: Optional[str] = None,
    variant_index: Optional[int] = None,
    generation_kind: str = "INITIAL",
    revision_prompt: Optional[str] = None,
    audit_status: str = LOGO_AUDIT_COMPLETED,
    audit_error: Optional[str] = None,
) -> Optional[str]:
    """Persist a generated logo image and return its UUID."""
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO generated_images
                    (generation_log_id, org_id, image_path, clip_embedding,
                     dino_embedding, ocr_text, visual_breakdown,
                     similarity_score, is_safe, project_id, parent_image_id,
                     variant_index, generation_kind, revision_prompt,
                     audit_status, audit_error, audited_at)
                VALUES (%s, %s, %s, %s::halfvec,
                        %s::halfvec, %s, %s::jsonb,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        CASE WHEN %s = 'completed' THEN NOW() ELSE NULL END)
                RETURNING id
            """,
                (
                    generation_log_id,
                    org_id,
                    image_path,
                    str(clip_embedding) if clip_embedding else None,
                    str(dino_embedding) if dino_embedding else None,
                    ocr_text or None,
                    json.dumps(visual_breakdown, ensure_ascii=False) if visual_breakdown else None,
                    similarity_score,
                    is_safe,
                    project_id,
                    parent_image_id,
                    variant_index,
                    generation_kind,
                    revision_prompt,
                    audit_status,
                    audit_error,
                    audit_status,
                ),
            )
            db.commit()
            row = cur.fetchone()
            return str(row["id"]) if row else None
    except Exception as exc:
        logger.error("Failed to store generated image: %s", exc)
        return None


def _get_logo_credits_remaining(org_id: str) -> dict:
    """Get the remaining unified AI credits for a logo-generation run."""
    return _get_ai_credits_remaining(org_id, cost=5)


def _create_logo_project(
    *,
    org_id: str,
    user_id: str,
    request: LogoGenerationRequest,
    database_factory=Database,
) -> str:
    """Create a Logo Studio project thread and return its UUID."""
    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO logo_projects
                (org_id, user_id, brand_name, description, style, nice_classes, color_preferences)
            VALUES (%s, %s, %s, %s, %s, %s::int[], %s)
            RETURNING id
            """,
            (
                org_id,
                user_id,
                request.brand_name.strip(),
                request.description.strip(),
                request.style,
                request.nice_classes,
                request.color_preferences.strip(),
            ),
        )
        db.commit()
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "project_create_failed",
                    "message": "Logo projesi olusturulamadi.",
                    "message_en": "Logo project could not be created.",
                },
            )
        return str(row["id"])


def _get_logo_project_row(
    *,
    project_id: str,
    org_id: str,
    database_factory=Database,
):
    """Return an org-scoped Logo Studio project row."""
    try:
        UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid project ID format") from exc

    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT
                id, org_id, user_id, brand_name, description, style,
                nice_classes, color_preferences, selected_image_id,
                created_at, updated_at
            FROM logo_projects
            WHERE id = %s AND org_id = %s
            """,
            (project_id, org_id),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Logo project not found")
    return row


def _get_logo_image_row(
    *,
    image_id: str,
    org_id: str,
    database_factory=Database,
):
    """Return an org-scoped generated logo image row."""
    try:
        UUID(image_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid image ID format") from exc

    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT
                id, generation_log_id, org_id, image_path, project_id, parent_image_id,
                variant_index, generation_kind, revision_prompt,
                similarity_score, is_safe, visual_breakdown,
                audit_status, audit_error, audited_at, created_at
            FROM generated_images
            WHERE id = %s AND org_id = %s
            """,
            (image_id, org_id),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Logo image not found")
    return row


def _logo_result_from_row(row: dict) -> LogoResult:
    """Map a generated_images row to the public LogoResult shape."""
    image_id = str(row["id"])
    breakdown = row.get("visual_breakdown")
    if isinstance(breakdown, str):
        try:
            breakdown = json.loads(breakdown)
        except Exception:
            breakdown = None
    breakdown = breakdown or {}
    return LogoResult(
        image_id=image_id,
        image_url=f"/api/v1/tools/generated-image/{image_id}",
        similarity_score=float(row.get("similarity_score") or 0),
        closest_match_name=breakdown.get("closest_match_name"),
        closest_match_image_url=breakdown.get("closest_match_image_url"),
        is_safe=bool(row.get("is_safe", False)),
        visual_breakdown=breakdown or None,
        project_id=str(row["project_id"]) if row.get("project_id") else None,
        parent_image_id=str(row["parent_image_id"]) if row.get("parent_image_id") else None,
        variant_index=row.get("variant_index"),
        generation_kind=row.get("generation_kind") or "INITIAL",
        revision_prompt=row.get("revision_prompt"),
        audit_status=row.get("audit_status") or LOGO_AUDIT_COMPLETED,
        audit_error=row.get("audit_error"),
        audited_at=row.get("audited_at"),
    )


def _get_project_logo_results(
    *,
    project_id: str,
    org_id: str,
    database_factory=Database,
) -> List[LogoResult]:
    """Return all candidates for a Logo Studio project."""
    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT
                id, generation_log_id, org_id, image_path, project_id, parent_image_id,
                variant_index, generation_kind, revision_prompt,
                similarity_score, is_safe, visual_breakdown,
                audit_status, audit_error, audited_at, created_at
            FROM generated_images
            WHERE project_id = %s AND org_id = %s
            ORDER BY created_at ASC, variant_index ASC NULLS LAST
            """,
            (project_id, org_id),
        )
        rows = cur.fetchall()
    return [_logo_result_from_row(row) for row in rows]


def _build_closest_match_image_url(match: dict) -> Optional[str]:
    """Build the public image URL for the closest matching trademark."""
    image_path = match.get("image_path")
    if image_path:
        return f"/api/trademark-image/{image_path}"
    return None


def _image_media_type(image_path: str) -> str:
    """Return the best-effort media type for a generated image path."""
    ext = os.path.splitext(image_path)[1].lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    return media_types.get(ext, "image/png")


def audit_generated_logo_image(
    image_id: str,
    *,
    database_factory=Database,
    visual_audit_available_checker=None,
    generate_visual_features_handler=None,
    visual_similarity_search_handler=None,
    closest_match_image_url_builder=None,
    settings_obj=settings,
) -> None:
    """Run the visual trademark audit for one generated logo image."""
    if visual_audit_available_checker is None:
        visual_audit_available_checker = _logo_visual_audit_available
    if generate_visual_features_handler is None:
        generate_visual_features_handler = _generate_all_visual_features
    if visual_similarity_search_handler is None:
        visual_similarity_search_handler = _full_visual_similarity_search
    if closest_match_image_url_builder is None:
        closest_match_image_url_builder = _build_closest_match_image_url

    try:
        UUID(str(image_id))
    except ValueError:
        logger.warning("Skipping logo audit with invalid image id: %s", image_id)
        return

    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE generated_images
            SET audit_status = %s, audit_error = NULL
            WHERE id = %s
            RETURNING id, org_id, image_path, project_id
            """,
            (LOGO_AUDIT_RUNNING, image_id),
        )
        row = cur.fetchone()
        db.commit()

    if not row:
        logger.warning("Logo audit skipped; generated image not found: %s", image_id)
        return

    org_id = str(row["org_id"])
    image_path = str(row["image_path"])
    project_id = str(row["project_id"]) if row.get("project_id") else None
    brand_name = ""
    nice_classes = []
    if project_id:
        with database_factory() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT brand_name, nice_classes
                FROM logo_projects
                WHERE id = %s AND org_id = %s
                """,
                (project_id, org_id),
            )
            project = cur.fetchone()
            if project:
                brand_name = project.get("brand_name") or ""
                nice_classes = list(project.get("nice_classes") or [])

    try:
        visual_ready, visual_reason = visual_audit_available_checker()
        if not visual_ready:
            raise RuntimeError(visual_reason or "visual audit unavailable")

        features = generate_visual_features_handler(image_path)
        max_similarity = 0.0
        is_safe = False
        top_breakdown = None

        if not features.get("clip_embedding"):
            raise RuntimeError("CLIP logo embedding could not be generated")

        matches = visual_similarity_search_handler(
            features=features,
            nice_classes=nice_classes,
            brand_name=brand_name,
            top_k=5,
        )
        if matches:
            top_match = matches[0]
            max_similarity = float(top_match.get("combined_sim") or 0)
            top_breakdown = dict(top_match.get("visual_breakdown") or {})
            top_breakdown["closest_match_name"] = top_match.get("name")
            top_breakdown["closest_match_image_url"] = closest_match_image_url_builder(top_match)

        logo_threshold = getattr(settings_obj.creative, "logo_similarity_threshold", RISK_THRESHOLDS["high"])
        is_safe = max_similarity < logo_threshold

        with database_factory() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE generated_images
                SET
                    clip_embedding = %s::halfvec,
                    dino_embedding = %s::halfvec,
                    ocr_text = %s,
                    visual_breakdown = %s::jsonb,
                    similarity_score = %s,
                    is_safe = %s,
                    audit_status = %s,
                    audit_error = NULL,
                    audited_at = NOW()
                WHERE id = %s
                """,
                (
                    str(features.get("clip_embedding")) if features.get("clip_embedding") else None,
                    str(features.get("dino_embedding")) if features.get("dino_embedding") else None,
                    features.get("ocr_text") or None,
                    json.dumps(top_breakdown, ensure_ascii=False) if top_breakdown else None,
                    round(max_similarity * 100, 1),
                    is_safe,
                    LOGO_AUDIT_COMPLETED,
                    image_id,
                ),
            )
            db.commit()
    except Exception as exc:
        logger.error("Logo visual audit failed for %s: %s", image_id, exc)
        with database_factory() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE generated_images
                SET audit_status = %s, audit_error = %s, audited_at = NOW()
                WHERE id = %s
                """,
                (LOGO_AUDIT_FAILED, str(exc)[:500], image_id),
            )
            db.commit()


async def get_generated_image_response(
    *,
    image_id: str,
    current_user=None,
    database_factory=Database,
    file_exists=os.path.isfile,
    logo_output_dir: Optional[str] = None,
):
    """Resolve and return an auth-scoped generated image file."""
    org_id = str(current_user.organization_id)

    try:
        UUID(image_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid image ID format") from exc

    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT image_path, org_id
            FROM generated_images
            WHERE id = %s
        """,
            (image_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Image not found")

    if str(row["org_id"]) != org_id:
        raise HTTPException(status_code=403, detail="Access denied")

    image_path = str(row["image_path"])
    try:
        resolved_image_path = Path(image_path).expanduser().resolve()
        resolved_output_dir = Path(logo_output_dir or settings.creative.logo_output_dir).expanduser().resolve()
        if resolved_output_dir not in [resolved_image_path, *resolved_image_path.parents]:
            raise ValueError("Generated image path is outside the configured logo directory")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image path")

    if not file_exists(str(resolved_image_path)):
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    return FileResponse(
        path=str(resolved_image_path),
        media_type=_image_media_type(str(resolved_image_path)),
        headers={
            "Cache-Control": "public, max-age=604800",
        },
    )


async def get_generation_history_data(
    *,
    page: int,
    per_page: int,
    feature_type: Optional[str],
    current_user=None,
    database_factory=Database,
):
    """Return paginated Creative Suite generation history for the org."""
    org_id = str(current_user.organization_id)

    with database_factory() as db:
        cur = db.cursor()

        where_clause = "WHERE gl.org_id = %s"
        params = [org_id]

        if feature_type:
            where_clause += " AND gl.feature_type = %s"
            params.append(feature_type)

        cur.execute(
            f"""
            SELECT COUNT(*) as total
            FROM generation_logs gl
            {where_clause}
        """,
            params,
        )
        total = cur.fetchone()["total"]

        total_pages = max(1, math.ceil(total / per_page))
        offset = (page - 1) * per_page

        cur.execute(
            f"""
            SELECT
                gl.id,
                gl.feature_type,
                gl.input_params,
                gl.output_data,
                gl.credits_used,
                gl.created_at
            FROM generation_logs gl
            {where_clause}
            ORDER BY gl.created_at DESC
            LIMIT %s OFFSET %s
        """,
            params + [per_page, offset],
        )
        rows = cur.fetchall()

        items = []
        for row in rows:
            item = GenerationHistoryItem(
                id=str(row["id"]),
                feature_type=row["feature_type"],
                input_params=row.get("input_params"),
                output_data=row.get("output_data"),
                credits_used=row.get("credits_used", 1),
                created_at=row["created_at"],
                images=None,
            )

            if row["feature_type"] == "LOGO":
                cur.execute(
                    """
                    SELECT
                        id, image_path, project_id, parent_image_id, variant_index,
                        generation_kind, revision_prompt, similarity_score, is_safe,
                        visual_breakdown, audit_status, audit_error, audited_at, created_at
                    FROM generated_images
                    WHERE generation_log_id = %s AND org_id = %s
                    ORDER BY created_at, variant_index ASC NULLS LAST
                """,
                    (str(row["id"]), org_id),
                )
                img_rows = cur.fetchall()
                item.images = [
                    {
                        "image_id": str(ir["id"]),
                        "image_url": f"/api/v1/tools/generated-image/{ir['id']}",
                        "project_id": str(ir["project_id"]) if ir.get("project_id") else None,
                        "parent_image_id": str(ir["parent_image_id"]) if ir.get("parent_image_id") else None,
                        "variant_index": ir.get("variant_index"),
                        "generation_kind": ir.get("generation_kind") or "INITIAL",
                        "revision_prompt": ir.get("revision_prompt"),
                        "similarity_score": float(ir.get("similarity_score") or 0),
                        "is_safe": bool(ir.get("is_safe", False)),
                        "visual_breakdown": ir.get("visual_breakdown"),
                        "audit_status": ir.get("audit_status") or LOGO_AUDIT_COMPLETED,
                        "audit_error": ir.get("audit_error"),
                        "audited_at": ir.get("audited_at").isoformat() if ir.get("audited_at") else None,
                    }
                    for ir in img_rows
                ]

            items.append(item)

    return GenerationHistoryResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


async def get_logo_project_data(
    *,
    project_id: str,
    current_user=None,
    database_factory=Database,
) -> LogoProjectResponse:
    """Return a Logo Studio project and all of its candidates."""
    org_id = str(current_user.organization_id)
    row = _get_logo_project_row(
        project_id=project_id,
        org_id=org_id,
        database_factory=database_factory,
    )
    logos = _get_project_logo_results(
        project_id=project_id,
        org_id=org_id,
        database_factory=database_factory,
    )
    return LogoProjectResponse(
        id=str(row["id"]),
        org_id=str(row["org_id"]),
        user_id=str(row["user_id"]),
        brand_name=row.get("brand_name") or "",
        description=row.get("description") or "",
        style=row.get("style") or "modern",
        nice_classes=list(row.get("nice_classes") or []),
        color_preferences=row.get("color_preferences") or "",
        selected_image_id=str(row["selected_image_id"]) if row.get("selected_image_id") else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        logos=logos,
    )


async def select_logo_project_candidate_data(
    *,
    project_id: str,
    image_id: str,
    current_user=None,
    database_factory=Database,
) -> LogoProjectResponse:
    """Select an audited safe logo candidate for the project."""
    org_id = str(current_user.organization_id)
    _get_logo_project_row(
        project_id=project_id,
        org_id=org_id,
        database_factory=database_factory,
    )
    image = _get_logo_image_row(
        image_id=image_id,
        org_id=org_id,
        database_factory=database_factory,
    )
    if str(image.get("project_id")) != project_id:
        raise HTTPException(status_code=400, detail="Logo image does not belong to this project")
    if (image.get("audit_status") or LOGO_AUDIT_COMPLETED) != LOGO_AUDIT_COMPLETED:
        raise HTTPException(status_code=409, detail="Logo audit must complete before selection")
    if not image.get("is_safe"):
        raise HTTPException(status_code=409, detail="Risky logos cannot be selected for final use")

    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE logo_projects
            SET selected_image_id = %s, updated_at = NOW()
            WHERE id = %s AND org_id = %s
            """,
            (image_id, project_id, org_id),
        )
        db.commit()

    return await get_logo_project_data(
        project_id=project_id,
        current_user=current_user,
        database_factory=database_factory,
    )


async def retry_logo_audit_data(
    *,
    image_id: str,
    current_user=None,
    database_factory=Database,
    audit_scheduler: Optional[Callable[[str], None]] = None,
) -> LogoResult:
    """Reset a generated logo to pending and queue another visual audit."""
    org_id = str(current_user.organization_id)
    image = _get_logo_image_row(
        image_id=image_id,
        org_id=org_id,
        database_factory=database_factory,
    )
    if (image.get("audit_status") or LOGO_AUDIT_COMPLETED) in (LOGO_AUDIT_PENDING, LOGO_AUDIT_RUNNING):
        raise HTTPException(status_code=409, detail="Logo audit is already running")

    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE generated_images
            SET audit_status = %s, audit_error = NULL
            WHERE id = %s AND org_id = %s
            RETURNING
                id, generation_log_id, org_id, image_path, project_id, parent_image_id,
                variant_index, generation_kind, revision_prompt,
                similarity_score, is_safe, visual_breakdown,
                audit_status, audit_error, audited_at, created_at
            """,
            (LOGO_AUDIT_PENDING, image_id, org_id),
        )
        row = cur.fetchone()
        db.commit()

    if audit_scheduler is not None:
        audit_scheduler(image_id)
    return _logo_result_from_row(row)


async def creative_suite_status_data(
    *,
    feature_enabled_getter=None,
    gemini_client_getter=None,
    ai_module=None,
):
    """Return public Creative Suite availability status."""
    if feature_enabled_getter is None:
        from utils.feature_flags import is_feature_enabled

        feature_enabled_getter = is_feature_enabled

    status = {
        "name_generator": {"available": False, "reason": "", "cost": 1},
        "logo_studio": {
            "available": False,
            "reason": "",
            "cost": 5,
            "audit_available": False,
            "audit_reason": "",
        },
    }

    if not feature_enabled_getter("ai_studio_enabled"):
        reason = "AI Studio gecici olarak devre disi birakildi"
        status["name_generator"]["reason"] = reason
        status["logo_studio"]["reason"] = reason
        return status

    try:
        if gemini_client_getter is None:
            from generative_ai.gemini_client import get_gemini_client

            gemini_client_getter = get_gemini_client

        client = gemini_client_getter()
        if client.is_available():
            status["name_generator"]["available"] = True
            status["logo_studio"]["available"] = True
        else:
            reason = "Gemini API anahtari yapilandirilmamis"
            status["name_generator"]["reason"] = reason
            status["logo_studio"]["reason"] = reason
    except Exception as exc:
        reason = f"Servis baslatilamadi: {str(exc)}"
        status["name_generator"]["reason"] = reason
        status["logo_studio"]["reason"] = reason

    visual_ready, visual_reason = _logo_visual_audit_available(ai_module)
    status["logo_studio"]["audit_available"] = visual_ready
    status["logo_studio"]["audit_reason"] = visual_reason

    return status


async def suggest_names_data(
    *,
    request: NameSuggestionRequest,
    current_user=None,
    settings_obj=settings,
    database_factory=Database,
    name_eligibility_checker=check_name_generation_eligibility,
    deduct_name_credit_handler=deduct_name_credit,
    increment_name_generation_usage_handler=increment_name_generation_usage,
    session_count_getter=None,
    cached_results_getter=None,
    plan_credits_getter=None,
    gemini_client_getter=None,
    batch_validate_names_handler=None,
    session_count_incrementer=None,
    cache_results_handler=None,
    generation_log_handler=None,
    audit_log_handler=None,
):
    """Generate AI name suggestions and validate them against the trademark database."""
    if session_count_getter is None:
        session_count_getter = _get_session_count
    if cached_results_getter is None:
        cached_results_getter = _get_cached_results
    if plan_credits_getter is None:
        plan_credits_getter = _get_plan_credits
    if batch_validate_names_handler is None:
        batch_validate_names_handler = _batch_validate_names
    if session_count_incrementer is None:
        session_count_incrementer = _increment_session_count
    if cache_results_handler is None:
        cache_results_handler = _cache_results
    if gemini_client_getter is None:
        from generative_ai.gemini_client import get_gemini_client

        gemini_client_getter = get_gemini_client

    org_id = str(current_user.organization_id)
    user_id = str(current_user.id)
    query = request.query.strip()
    request_key = _name_request_cache_key(request)

    session_count = session_count_getter(org_id, request_key)

    with database_factory() as db:
        can_generate, reason, details = name_eligibility_checker(
            db,
            org_id,
            session_count,
        )

    if not can_generate:
        status_code = 403 if reason == "upgrade_required" else 402
        raise HTTPException(status_code=status_code, detail=details)

    using_purchased = details.get("using_purchased_credits", False)

    cached_results = cached_results_getter(org_id, request_key)
    if cached_results is not None:
        plan = plan_credits_getter(org_id, session_count)
        return NameSuggestionResponse(
            safe_names=cached_results["safe"],
            filtered_count=cached_results["filtered_count"],
            total_generated=cached_results["total_generated"],
            session_count=session_count,
            credits_remaining=plan,
            cached=True,
        )

    client = gemini_client_getter()
    if not client.is_available():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_unavailable",
                "message": "Isim olusturma servisi su anda kullanilamiyor. Lutfen daha sonra tekrar deneyin.",
                "message_en": "Name generation service is currently unavailable. Please try again later.",
            },
        )

    avoid_list = list(set(request.avoid_names + [query]))
    nice_classes_str = ", ".join(str(c) for c in request.nice_classes) if request.nice_classes else "Not specified"
    prompt = client.build_name_prompt(
        concept=query,
        industry=request.industry,
        nice_classes=nice_classes_str,
        style=request.style,
        language="Turkish and English" if request.language == "tr" else "English and Turkish",
        avoid_names=", ".join(avoid_list) if avoid_list else "None",
        count=settings_obj.creative.name_batch_size,
    )

    try:
        generated_names = await client.generate_names(
            prompt=prompt,
            count=settings_obj.creative.name_batch_size,
        )
    except Exception as exc:
        retries_attempted = getattr(exc, "retries_attempted", None)
        logger.error(
            "gemini_name_generation_failed: %s (retries=%s)",
            exc,
            retries_attempted,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generation_failed",
                "message": "Isim olusturma basarisiz oldu. Lutfen tekrar deneyin.",
                "message_en": f"Name generation failed: {exc}",
            },
        ) from exc

    if not generated_names:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "no_names_generated",
                "message": "Isim olusturulamadi. Lutfen farkli parametrelerle tekrar deneyin.",
                "message_en": "No names could be generated. Try different parameters.",
            },
        )

    total_generated = len(generated_names)
    all_results = batch_validate_names_handler(
        candidate_names=generated_names,
        nice_classes=request.nice_classes,
        avoid_names=avoid_list,
        similarity_threshold=settings_obj.creative.name_similarity_threshold,
    )

    safe_names = [result for result in all_results if result.is_safe]
    filtered_count = total_generated - len(safe_names)

    credits_used = 0
    if safe_names:
        with database_factory() as db:
            if not deduct_name_credit_handler(db, org_id):
                raise HTTPException(
                    status_code=402,
                    detail={
                        "error": "credits_exhausted",
                        "message": "AI kredisi dusulemedi.",
                        "message_en": "Could not deduct AI credit.",
                    },
                )
            increment_name_generation_usage_handler(db, user_id, org_id)
        credits_used = 1

    new_session_count = session_count_incrementer(org_id, request_key, len(safe_names))
    cache_results_handler(org_id, request_key, safe_names, filtered_count, total_generated)

    if generation_log_handler is not None:
        generation_log_handler(
            org_id=org_id,
            user_id=user_id,
            feature_type="NAME",
            input_prompt=prompt,
            input_params={
                "query": query,
                "nice_classes": request.nice_classes,
                "industry": request.industry,
                "style": request.style,
                "language": request.language,
                "avoid_names": request.avoid_names,
            },
            output_data={
                "total_generated": total_generated,
                "safe_count": len(safe_names),
                "filtered_count": filtered_count,
                "safe_names": [name.name for name in safe_names],
            },
            credits_used=credits_used,
        )

    if audit_log_handler is not None:
        audit_log_handler(
            user_id=user_id,
            org_id=org_id,
            action="generate_names",
            resource_type="creative_suite",
            metadata={
                "query": query,
                "total_generated": total_generated,
                "safe_count": len(safe_names),
                "using_purchased_credits": using_purchased,
            },
        )

    plan = plan_credits_getter(org_id, new_session_count)
    return NameSuggestionResponse(
        safe_names=safe_names,
        filtered_count=filtered_count,
        total_generated=total_generated,
        session_count=new_session_count,
        credits_remaining=plan,
        cached=False,
    )


async def generate_logo_data(
    *,
    request: LogoGenerationRequest,
    current_user=None,
    settings_obj=settings,
    database_factory=Database,
    logo_eligibility_checker=check_logo_generation_eligibility,
    deduct_logo_credit_handler=deduct_logo_credit,
    refund_logo_credit_handler=refund_logo_credit,
    gemini_client_getter=None,
    generation_uuid_factory=None,
    save_logo_image_handler=None,
    generate_visual_features_handler=None,
    visual_similarity_search_handler=None,
    store_generated_image_handler=None,
    logo_credits_remaining_getter=None,
    closest_match_image_url_builder=None,
    visual_audit_available_checker=None,
    audit_scheduler: Optional[Callable[[str], None]] = None,
    create_logo_project_handler=None,
    generation_log_handler=None,
    audit_log_handler=None,
):
    """Generate AI logo candidates and queue their trademark visual audits."""
    if gemini_client_getter is None:
        from generative_ai.gemini_client import get_gemini_client

        gemini_client_getter = get_gemini_client
    if generation_uuid_factory is None:
        generation_uuid_factory = uuid.uuid4
    if save_logo_image_handler is None:
        save_logo_image_handler = _save_logo_image
    if generate_visual_features_handler is None:
        generate_visual_features_handler = _generate_all_visual_features
    if visual_similarity_search_handler is None:
        visual_similarity_search_handler = _full_visual_similarity_search
    if store_generated_image_handler is None:
        store_generated_image_handler = _store_generated_image
    if logo_credits_remaining_getter is None:
        logo_credits_remaining_getter = _get_logo_credits_remaining
    if closest_match_image_url_builder is None:
        closest_match_image_url_builder = _build_closest_match_image_url
    if visual_audit_available_checker is None:
        visual_audit_available_checker = _logo_visual_audit_available
    if create_logo_project_handler is None:
        create_logo_project_handler = _create_logo_project

    org_id = str(current_user.organization_id)
    user_id = str(current_user.id)

    with database_factory() as db:
        can_generate, reason, details = logo_eligibility_checker(db, org_id)

    if not can_generate:
        status_code = 403 if reason == "upgrade_required" else 402
        raise HTTPException(status_code=status_code, detail=details)

    client = gemini_client_getter()
    if not client.is_available():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_unavailable",
                "message": "Logo olusturma servisi su anda kullanilamiyor.",
                "message_en": "Logo generation service is currently unavailable.",
            },
        )

    revision_prompt = request.revision_prompt.strip()
    is_revision = bool(revision_prompt or request.parent_image_id)
    project_id = request.project_id
    parent_row = None

    if project_id:
        _get_logo_project_row(
            project_id=project_id,
            org_id=org_id,
            database_factory=database_factory,
        )

    if request.parent_image_id:
        parent_row = _get_logo_image_row(
            image_id=request.parent_image_id,
            org_id=org_id,
            database_factory=database_factory,
        )
        parent_project_id = str(parent_row["project_id"]) if parent_row.get("project_id") else None
        if project_id and parent_project_id and parent_project_id != project_id:
            raise HTTPException(status_code=400, detail="Parent logo does not belong to this project")
        if not project_id:
            project_id = parent_project_id
        if not project_id:
            raise HTTPException(status_code=400, detail="Parent logo is not attached to a project")
        if (parent_row.get("audit_status") or LOGO_AUDIT_COMPLETED) in (LOGO_AUDIT_PENDING, LOGO_AUDIT_RUNNING):
            raise HTTPException(status_code=409, detail="Parent logo audit is still running")

    if is_revision and not project_id:
        raise HTTPException(status_code=400, detail="A project and selected logo are required for revision")

    with database_factory() as db:
        deducted = deduct_logo_credit_handler(db, org_id)

    if not deducted:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "credits_exhausted",
                "message": "Logo olusturma kredisi dusulemedi.",
                "message_en": "Could not deduct logo generation credit.",
            },
        )

    description = request.description
    if request.color_preferences:
        description = f"{description}. Color scheme: {request.color_preferences}".strip(". ")

    try:
        if is_revision and parent_row is not None and hasattr(client, "generate_logo_revisions"):
            reference_bytes = None
            reference_path = parent_row.get("image_path")
            if reference_path and os.path.isfile(str(reference_path)):
                with open(str(reference_path), "rb") as image_file:
                    reference_bytes = image_file.read()
            image_bytes_list = await client.generate_logo_revisions(
                brand_name=request.brand_name,
                description=description,
                style=request.style,
                revision_prompt=revision_prompt,
                reference_image_bytes=reference_bytes,
                count=settings_obj.creative.logo_images_per_run,
            )
        else:
            generation_description = description
            if revision_prompt:
                generation_description = f"{description}. Revision request: {revision_prompt}".strip(". ")
            image_bytes_list = await client.generate_logos(
                brand_name=request.brand_name,
                description=generation_description,
                style=request.style,
                count=settings_obj.creative.logo_images_per_run,
            )
    except Exception as exc:
        retries_attempted = getattr(exc, "retries_attempted", None)
        logger.error(
            "gemini_logo_generation_failed: %s (retries=%s)",
            exc,
            retries_attempted,
        )
        with database_factory() as db:
            refund_logo_credit_handler(db, org_id)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generation_failed",
                "message": "Logo olusturma basarisiz oldu. Krediniz iade edildi.",
                "message_en": f"Logo generation failed: {exc}. Your credit has been refunded.",
            },
        ) from exc

    if not image_bytes_list:
        with database_factory() as db:
            refund_logo_credit_handler(db, org_id)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "no_logos_generated",
                "message": "Logo olusturulamadi. Krediniz iade edildi.",
                "message_en": "No logos could be generated. Your credit has been refunded.",
            },
        )

    if not project_id:
        try:
            project_id = create_logo_project_handler(
                org_id=org_id,
                user_id=user_id,
                request=request,
                database_factory=database_factory,
            )
        except Exception:
            with database_factory() as db:
                refund_logo_credit_handler(db, org_id)
            raise

    generation_id = str(generation_uuid_factory())
    generation_kind = "REVISION" if is_revision else "INITIAL"
    log_id = None
    if generation_log_handler is not None:
        log_id = generation_log_handler(
            org_id=org_id,
            user_id=user_id,
            feature_type="LOGO",
            input_prompt=f"Logo for '{request.brand_name}': {description}",
            input_params={
                "project_id": project_id,
                "parent_image_id": request.parent_image_id,
                "revision_prompt": revision_prompt,
                "generation_kind": generation_kind,
                "brand_name": request.brand_name,
                "description": request.description,
                "style": request.style,
                "nice_classes": request.nice_classes,
                "color_preferences": request.color_preferences,
                "count": len(image_bytes_list),
            },
            output_data={
                "generation_id": generation_id,
                "project_id": project_id,
                "variations": len(image_bytes_list),
                "audit_status": LOGO_AUDIT_PENDING,
            },
            credits_used=5,
        )
    if not log_id:
        log_id = generation_id

    logo_results: List[LogoResult] = []
    for index, image_bytes in enumerate(image_bytes_list):
        saved_path = save_logo_image_handler(image_bytes, org_id, generation_id, index)
        if not saved_path:
            continue

        image_id = store_generated_image_handler(
            generation_log_id=log_id,
            org_id=org_id,
            image_path=saved_path,
            clip_embedding=None,
            similarity_score=0.0,
            is_safe=False,
            dino_embedding=None,
            ocr_text=None,
            visual_breakdown=None,
            project_id=project_id,
            parent_image_id=request.parent_image_id,
            variant_index=index + 1,
            generation_kind=generation_kind,
            revision_prompt=revision_prompt or None,
            audit_status=LOGO_AUDIT_PENDING,
            audit_error=None,
        )
        if not image_id:
            logger.error("Skipping generated logo because image metadata could not be stored: %s", saved_path)
            try:
                os.remove(saved_path)
            except Exception:
                pass
            continue

        logo_results.append(
            LogoResult(
                image_id=image_id,
                image_url=f"/api/v1/tools/generated-image/{image_id}",
                similarity_score=0.0,
                closest_match_name=None,
                closest_match_image_url=None,
                is_safe=False,
                visual_breakdown=None,
                project_id=project_id,
                parent_image_id=request.parent_image_id,
                variant_index=index + 1,
                generation_kind=generation_kind,
                revision_prompt=revision_prompt or None,
                audit_status=LOGO_AUDIT_PENDING,
                audit_error=None,
            )
        )
        if audit_scheduler is not None:
            audit_scheduler(image_id)

    if not logo_results:
        with database_factory() as db:
            refund_logo_credit_handler(db, org_id)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "processing_failed",
                "message": "Logo isleme basarisiz oldu. Krediniz iade edildi.",
                "message_en": "Logo processing failed. Your credit has been refunded.",
            },
        )

    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE logo_projects SET updated_at = NOW() WHERE id = %s AND org_id = %s",
            (project_id, org_id),
        )
        db.commit()

    if audit_log_handler is not None:
        audit_log_handler(
            user_id=user_id,
            org_id=org_id,
            action="generate_logos",
            resource_type="creative_suite",
            resource_id=log_id,
            metadata={
                "project_id": project_id,
                "generation_kind": generation_kind,
                "parent_image_id": request.parent_image_id,
                "brand_name": request.brand_name,
                "style": request.style,
                "variations_generated": len(logo_results),
                "audit_status": LOGO_AUDIT_PENDING,
            },
        )

    credits = logo_credits_remaining_getter(org_id)
    return LogoGenerationResponse(
        logos=logo_results,
        credits_remaining=credits,
        generation_id=log_id,
        project_id=project_id,
    )

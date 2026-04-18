"""Service helpers for Creative Suite routes."""

import hashlib
import io
import json
import logging
import math
import os
import uuid
from pathlib import Path
from typing import List, Optional
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
    LogoResult,
    NameSuggestionRequest,
    NameSuggestionResponse,
    SafeNameResult,
)
from risk_engine import RISK_THRESHOLDS, calculate_visual_similarity, score_pair
from utils.subscription import (
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

    import ai
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


def _get_plan_credits(org_id: str, session_count: int) -> dict:
    """Get current credit status for the response."""
    try:
        with Database() as db:
            plan = get_org_plan(db, org_id)
            cur = db.cursor()
            cur.execute(
                """
                SELECT COALESCE(name_credits_purchased, 0) as purchased
                FROM organizations WHERE id = %s
            """,
                (org_id,),
            )
            row = cur.fetchone()
            purchased = row["purchased"] if row else 0

        return {
            "session_limit": plan["name_suggestions_per_session"],
            "used": session_count,
            "purchased": purchased,
            "plan": plan["plan_name"],
        }
    except Exception:
        return {"session_limit": 5, "used": session_count, "purchased": 0, "plan": "free"}


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
    import ai

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
                     similarity_score, is_safe)
                VALUES (%s, %s, %s, %s::halfvec,
                        %s::halfvec, %s, %s::jsonb,
                        %s, %s)
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
                ),
            )
            db.commit()
            row = cur.fetchone()
            return str(row["id"]) if row else None
    except Exception as exc:
        logger.error("Failed to store generated image: %s", exc)
        return None


def _get_logo_credits_remaining(org_id: str) -> dict:
    """Get the remaining logo-generation credits for the org."""
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT
                    COALESCE(logo_credits_monthly, 0) as monthly,
                    COALESCE(logo_credits_purchased, 0) as purchased
                FROM organizations WHERE id = %s
            """,
                (org_id,),
            )
            row = cur.fetchone()
            if row:
                return {"monthly": row["monthly"], "purchased": row["purchased"]}
    except Exception:
        pass
    return {"monthly": 0, "purchased": 0}


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


async def get_generated_image_response(
    *,
    image_id: str,
    current_user=None,
    database_factory=Database,
    file_exists=os.path.isfile,
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

    image_path = row["image_path"]
    if ".." in image_path:
        raise HTTPException(status_code=400, detail="Invalid image path")

    if not file_exists(image_path):
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    return FileResponse(
        path=image_path,
        media_type=_image_media_type(image_path),
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
                        id, image_path, similarity_score, is_safe, created_at
                    FROM generated_images
                    WHERE generation_log_id = %s AND org_id = %s
                    ORDER BY created_at
                """,
                    (str(row["id"]), org_id),
                )
                img_rows = cur.fetchall()
                item.images = [
                    {
                        "image_id": str(ir["id"]),
                        "image_url": f"/api/v1/tools/generated-image/{ir['id']}",
                        "similarity_score": float(ir.get("similarity_score") or 0),
                        "is_safe": bool(ir.get("is_safe", True)),
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
        "name_generator": {"available": False, "reason": ""},
        "logo_studio": {"available": False, "reason": ""},
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

    if status["logo_studio"]["available"]:
        try:
            if ai_module is None:
                import ai as ai_module

            if not hasattr(ai_module, "clip_model") or ai_module.clip_model is None:
                status["logo_studio"]["reason"] = "CLIP modeli yuklenmemis"
        except Exception:
            pass

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

    session_count = session_count_getter(org_id, query)

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

    cached_results = cached_results_getter(org_id, query)
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

    with database_factory() as db:
        if using_purchased:
            deduct_name_credit_handler(db, org_id)
        increment_name_generation_usage_handler(db, user_id, org_id)

    new_session_count = session_count_incrementer(org_id, query, len(safe_names))
    cache_results_handler(org_id, query, safe_names, filtered_count, total_generated)

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
    generation_log_handler=None,
    audit_log_handler=None,
):
    """Generate AI logos and audit them against the trademark image corpus."""
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

    org_id = str(current_user.organization_id)
    user_id = str(current_user.id)

    with database_factory() as db:
        can_generate, reason, details = logo_eligibility_checker(db, org_id)

    if not can_generate:
        status_code = 403 if reason == "upgrade_required" else 402
        raise HTTPException(status_code=status_code, detail=details)

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

    client = gemini_client_getter()
    if not client.is_available():
        with database_factory() as db:
            refund_logo_credit_handler(db, org_id)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_unavailable",
                "message": "Logo olusturma servisi su anda kullanilamiyor. Krediniz iade edildi.",
                "message_en": "Logo generation service is currently unavailable. Your credit has been refunded.",
            },
        )

    description = request.description
    if request.color_preferences:
        description = f"{description}. Color scheme: {request.color_preferences}".strip(". ")

    try:
        image_bytes_list = await client.generate_logos(
            brand_name=request.brand_name,
            description=description,
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

    generation_id = str(generation_uuid_factory())
    log_id = None
    if generation_log_handler is not None:
        log_id = generation_log_handler(
            org_id=org_id,
            user_id=user_id,
            feature_type="LOGO",
            input_prompt=f"Logo for '{request.brand_name}': {description}",
            input_params={
                "brand_name": request.brand_name,
                "description": request.description,
                "style": request.style,
                "nice_classes": request.nice_classes,
                "color_preferences": request.color_preferences,
                "count": len(image_bytes_list),
            },
            output_data={"generation_id": generation_id, "variations": len(image_bytes_list)},
        )
    if not log_id:
        log_id = generation_id

    logo_results: List[LogoResult] = []
    for index, image_bytes in enumerate(image_bytes_list):
        saved_path = save_logo_image_handler(image_bytes, org_id, generation_id, index)
        if not saved_path:
            continue

        features = generate_visual_features_handler(saved_path)
        max_similarity = 0.0
        closest_match_name = None
        closest_match_image_url = None
        top_breakdown = None

        if features.get("clip_embedding"):
            matches = visual_similarity_search_handler(
                features=features,
                nice_classes=request.nice_classes,
                brand_name=request.brand_name,
                top_k=5,
            )
            if matches:
                top_match = matches[0]
                max_similarity = top_match["combined_sim"]
                closest_match_name = top_match.get("name")
                closest_match_image_url = closest_match_image_url_builder(top_match)
                top_breakdown = top_match.get("visual_breakdown")

        is_safe = max_similarity < RISK_THRESHOLDS["high"]
        image_id = store_generated_image_handler(
            generation_log_id=log_id,
            org_id=org_id,
            image_path=saved_path,
            clip_embedding=features.get("clip_embedding"),
            similarity_score=round(max_similarity * 100, 1),
            is_safe=is_safe,
            dino_embedding=features.get("dino_embedding"),
            ocr_text=features.get("ocr_text") or None,
            visual_breakdown=top_breakdown,
        )
        if not image_id:
            image_id = str(generation_uuid_factory())

        logo_results.append(
            LogoResult(
                image_id=image_id,
                image_url=f"/api/v1/tools/generated-image/{image_id}",
                similarity_score=round(max_similarity * 100, 1),
                closest_match_name=closest_match_name,
                closest_match_image_url=closest_match_image_url,
                is_safe=is_safe,
                visual_breakdown=top_breakdown,
            )
        )

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

    if audit_log_handler is not None:
        audit_log_handler(
            user_id=user_id,
            org_id=org_id,
            action="generate_logos",
            resource_type="creative_suite",
            resource_id=log_id,
            metadata={
                "brand_name": request.brand_name,
                "style": request.style,
                "variations_generated": len(logo_results),
                "safe_count": sum(1 for logo in logo_results if logo.is_safe),
            },
        )

    credits = logo_credits_remaining_getter(org_id)
    return LogoGenerationResponse(
        logos=logo_results,
        credits_remaining=credits,
        generation_id=log_id,
    )

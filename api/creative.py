"""
Creative Suite API — Name Generator & Logo Studio
===================================================
POST /api/v1/tools/suggest-names   — Generate & certify safe brand names
POST /api/v1/tools/generate-logo   — Generate logos with visual similarity audit
GET  /api/v1/tools/generated-image — Serve generated images (auth-protected)
GET  /api/v1/tools/generation-history — List past generations

Usage:
    from api.creative import router as creative_router
    app.include_router(creative_router)
"""
import hashlib
import io
import json
import logging
import math
import os
import time
import uuid
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from psycopg2.extras import RealDictCursor

from auth.authentication import CurrentUser, get_current_user
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
from risk_engine import RISK_THRESHOLDS, score_pair
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

router = APIRouter(prefix="/api/v1/tools", tags=["Creative Suite"])

# Redis client for session caching (lazy-init)
_redis_client = None


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
    r = _get_redis()
    if r is None:
        return 0
    try:
        val = r.get(_session_key(org_id, query) + ":count")
        return int(val) if val else 0
    except Exception:
        return 0


def _increment_session_count(org_id: str, query: str, count: int) -> int:
    """Increment session counter and return new total."""
    r = _get_redis()
    if r is None:
        return count
    try:
        key = _session_key(org_id, query) + ":count"
        new_val = r.incrby(key, count)
        r.expire(key, settings.creative.generation_cache_ttl)
        return int(new_val)
    except Exception:
        return count


# ============================================================
# Audit logging for Creative Suite actions
# ============================================================

def _audit_log(
    user_id: str,
    org_id: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Write an audit log entry for Creative Suite actions."""
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute("""
                INSERT INTO audit_log
                    (user_id, organization_id, action, resource_type, resource_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                user_id, org_id, action, resource_type,
                resource_id,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
            ))
            db.commit()
    except Exception as e:
        logger.warning("Audit log write failed (non-fatal)", error=str(e))


# ============================================================
# Batch validation against 2.3M trademark database
# ============================================================

def _batch_validate_names(
    candidate_names: List[str],
    nice_classes: List[int],
    avoid_names: List[str],
    similarity_threshold: float,
) -> List[SafeNameResult]:
    """
    Validate generated names against the trademark database.

    Two-stage scoring:
      Stage 2 — Fast pre-screen: pgvector semantic + pg_trgm + dmetaphone (LIMIT 10)
      Stage 3 — Full RiskEngine: cross-language exact match, translation similarity,
                IDF-weighted scoring, phonetic via score_pair()

    Stage 3 runs for EVERY name (not conditionally) because it is the only path
    that checks translation columns (name_tr/en/ku/fa) and cross-language conflicts.

    Returns list of SafeNameResult for ALL candidates (safe or not).
    """
    if not candidate_names:
        return []

    import ai  # ai.py module at project root — embedding functions
    from risk_engine import get_risk_level

    # ----------------------------------------------------------
    # Step 1: Generate text embeddings for all candidates (batched)
    # ----------------------------------------------------------
    try:
        embeddings = ai.get_text_embeddings_batch_cached(candidate_names)
    except Exception as e:
        logger.warning("Batch text embedding failed, falling back to individual", error=str(e))
        embeddings = []
        for name in candidate_names:
            try:
                embeddings.append(ai.get_text_embedding_cached(name))
            except Exception:
                embeddings.append(None)

    # ----------------------------------------------------------
    # Step 2: Fast pre-screen — semantic + trigram + phonetic
    # ----------------------------------------------------------
    results: List[SafeNameResult] = []

    from db.pool import get_connection, release_connection
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Build class filter SQL
        class_filter = ""
        class_params = []
        if nice_classes:
            class_filter = "AND t.nice_class_numbers && %s::int[]"
            class_params = [nice_classes]

        for i, name in enumerate(candidate_names):
            emb = embeddings[i] if i < len(embeddings) else None

            # --- Pre-filter: skip if too similar to avoid_names ---
            skip = False
            name_lower = name.lower().strip()
            for avoid in avoid_names:
                if _simple_similarity(name_lower, avoid.lower().strip()) > 0.7:
                    skip = True
                    break
            if skip:
                results.append(SafeNameResult(
                    name=name,
                    risk_score=100.0,
                    risk_level="critical",
                    text_similarity=1.0,
                    semantic_similarity=1.0,
                    phonetic_match=True,
                    translation_similarity=0.0,
                    closest_match=avoid,
                    is_safe=False,
                ))
                continue

            # --- Stage 2: Fast DB query — semantic + trigram + phonetic ---
            try:
                if emb is not None:
                    params_s2 = [
                        str(emb), name, name,   # SELECT
                        str(emb), name,          # WHERE
                    ] + class_params + [
                        str(emb), name,          # ORDER BY
                    ]
                    sql_s2 = f"""
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
                    params_s2 = [name, name, name] + class_params + [name]
                    sql_s2 = f"""
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

                cur.execute(sql_s2, params_s2)
                matches = cur.fetchall()

            except Exception as e:
                logger.warning("DB query failed for candidate", name=name, error=str(e))
                matches = []

            # --- Evaluate Stage 2 matches ---
            closest_name = None
            max_semantic = 0.0
            max_trgm = 0.0
            has_phonetic = False

            for m in matches:
                sem = float(m.get("semantic_sim", 0) or 0)
                trg = float(m.get("trgm_sim", 0) or 0)
                phon = bool(m.get("phonetic_match", False))

                if sem > max_semantic:
                    max_semantic = sem
                if trg > max_trgm:
                    max_trgm = trg
                    closest_name = m["name"]
                if sem > max_semantic - 0.01 and sem > max_trgm:
                    closest_name = m["name"]
                if phon:
                    has_phonetic = True
                    if closest_name is None:
                        closest_name = m["name"]

            # Stage 2 risk score (0-100 scale)
            s2_risk_score = max(max_semantic, max_trgm) * 100.0

            # Stage 2 safety verdict (quick)
            s2_safe = True
            if max_semantic > similarity_threshold:
                s2_safe = False
            if max_trgm > similarity_threshold:
                s2_safe = False
            if has_phonetic:
                s2_safe = False

            # ----------------------------------------------------------
            # Stage 3: ALWAYS run full RiskEngine
            # This is the ONLY path that checks translations (name_tr/en/ku/fa),
            # cross-language exact matches, and IDF-weighted scoring.
            # ----------------------------------------------------------
            engine_score = 0.0
            translation_sim = 0.0

            try:
                engine = _get_risk_engine()
                if engine:
                    result_dict, _ = engine.assess_brand_risk(
                        name=name,
                        target_classes=nice_classes if nice_classes else None,
                    )
                    engine_score = result_dict.get("final_risk_score", 0)

                    # Extract translation_similarity from top candidate
                    top_candidates = result_dict.get("top_candidates", [])
                    if top_candidates:
                        top = top_candidates[0]
                        top_scores = top.get("scores", {})
                        translation_sim = top_scores.get("translation_similarity", 0.0)

                        # If engine found a higher-scoring match, adopt it
                        if engine_score * 100.0 > s2_risk_score:
                            closest_name = top.get("name", closest_name)
                            max_semantic = max(
                                max_semantic,
                                top_scores.get("semantic_similarity", 0.0),
                            )
            except Exception as e:
                logger.warning("Risk engine check failed", name=name, error=str(e))

            # --- Merge: take the HIGHER score from Stage 2 or Stage 3 ---
            risk_score = max(s2_risk_score, engine_score * 100.0)

            # Safety: must pass BOTH stages
            # Stage 2 catches phonetic + high trigram/semantic quickly;
            # Stage 3 catches translations + IDF-weighted cross-language
            if not s2_safe:
                is_safe = False
            else:
                is_safe = (risk_score / 100.0) < RISK_THRESHOLDS["high"]

            risk_level = get_risk_level(risk_score / 100.0)

            results.append(SafeNameResult(
                name=name,
                risk_score=round(risk_score, 1),
                risk_level=risk_level,
                text_similarity=round(max_trgm, 3),
                semantic_similarity=round(max_semantic, 3),
                phonetic_match=has_phonetic,
                translation_similarity=round(translation_sim, 3),
                closest_match=closest_name,
                is_safe=is_safe,
            ))

    finally:
        release_connection(conn)

    return results


# Lazy RiskEngine singleton (heavy — loads AI models)
_risk_engine_instance = None


def _get_risk_engine():
    """Get or create RiskEngine singleton."""
    global _risk_engine_instance
    if _risk_engine_instance is None:
        try:
            from risk_engine import RiskEngine
            _risk_engine_instance = RiskEngine()
        except Exception as e:
            logger.error("Failed to initialize RiskEngine", error=str(e))
            return None
    return _risk_engine_instance


def _simple_similarity(a: str, b: str) -> float:
    """Quick SequenceMatcher ratio for pre-filtering."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


# ============================================================
# Generation logging
# ============================================================

def _log_generation(
    org_id: str,
    user_id: str,
    feature_type: str,
    input_prompt: str,
    input_params: dict,
    output_data: dict,
    credits_used: int = 1,
) -> Optional[str]:
    """Insert a record into generation_logs and return the log id."""
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute("""
                INSERT INTO generation_logs
                    (org_id, user_id, feature_type, input_prompt, input_params, output_data, credits_used)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                org_id, user_id, feature_type,
                input_prompt,
                json.dumps(input_params, ensure_ascii=False),
                json.dumps(output_data, ensure_ascii=False),
                credits_used,
            ))
            db.commit()
            row = cur.fetchone()
            return str(row["id"]) if row else None
    except Exception as e:
        logger.error("Failed to log generation", error=str(e))
        return None


# ============================================================
# Endpoint: POST /api/v1/tools/suggest-names
# ============================================================

@router.post("/suggest-names", response_model=NameSuggestionResponse)
async def suggest_names(
    request: NameSuggestionRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Generate AI-powered brand name suggestions and validate each against
    the 2.3M trademark database. Only "safe" names are returned.

    Flow: Gemini generates → each name checked (semantic, trigram, phonetic) → safe names returned.
    """
    org_id = str(current_user.organization_id)
    user_id = str(current_user.id)
    query = request.query.strip()

    # ----------------------------------------------------------
    # 1. Session tracking
    # ----------------------------------------------------------
    session_count = _get_session_count(org_id, query)

    # ----------------------------------------------------------
    # 2. Credit check (monthly hard cap + per-session soft cap)
    # ----------------------------------------------------------
    with Database() as db:
        can_generate, reason, details = check_name_generation_eligibility(
            db, org_id, session_count,
        )

    if not can_generate:
        if reason == "monthly_limit_exceeded":
            status_code = 402
        elif reason == "upgrade_required":
            status_code = 403
        else:
            status_code = 402
        raise HTTPException(status_code=status_code, detail=details)

    using_purchased = details.get("using_purchased_credits", False)

    # ----------------------------------------------------------
    # 3. Check Redis cache first
    # ----------------------------------------------------------
    cached_results = _get_cached_results(org_id, query)
    if cached_results is not None:
        plan = _get_plan_credits(org_id, session_count)
        return NameSuggestionResponse(
            safe_names=cached_results["safe"],
            filtered_count=cached_results["filtered_count"],
            total_generated=cached_results["total_generated"],
            session_count=session_count,
            credits_remaining=plan,
            cached=True,
        )

    # ----------------------------------------------------------
    # 4. Generate names via Gemini
    # ----------------------------------------------------------
    from generative_ai.gemini_client import get_gemini_client, GeminiError

    client = get_gemini_client()
    if not client.is_available():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_unavailable",
                "message": "Isim olusturma servisi su anda kullanilamiyor. Lutfen daha sonra tekrar deneyin.",
                "message_en": "Name generation service is currently unavailable. Please try again later.",
            },
        )

    # Build the prompt
    avoid_list = list(set(request.avoid_names + [query]))
    nice_classes_str = ", ".join(str(c) for c in request.nice_classes) if request.nice_classes else "Not specified"

    prompt = client.build_name_prompt(
        industry=request.industry,
        nice_classes=nice_classes_str,
        style=request.style,
        language="Turkish and English" if request.language == "tr" else "English and Turkish",
        avoid_names=", ".join(avoid_list) if avoid_list else "None",
        count=settings.creative.name_batch_size,
    )

    try:
        generated_names = await client.generate_names(
            prompt=prompt,
            count=settings.creative.name_batch_size,
        )
    except GeminiError as e:
        logger.error("gemini_name_generation_failed", error=str(e), retries=e.retries_attempted)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generation_failed",
                "message": "Isim olusturma basarisiz oldu. Lutfen tekrar deneyin.",
                "message_en": f"Name generation failed: {e}",
            },
        )

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

    # ----------------------------------------------------------
    # 5. Validate each name against the DB
    # ----------------------------------------------------------
    all_results = _batch_validate_names(
        candidate_names=generated_names,
        nice_classes=request.nice_classes,
        avoid_names=avoid_list,
        similarity_threshold=settings.creative.name_similarity_threshold,
    )

    safe_names = [r for r in all_results if r.is_safe]
    filtered_count = total_generated - len(safe_names)

    # ----------------------------------------------------------
    # 6. Deduct purchased credit if using purchased + track monthly usage
    # ----------------------------------------------------------
    with Database() as db:
        if using_purchased:
            deduct_name_credit(db, org_id)
        increment_name_generation_usage(db, user_id, org_id)

    # ----------------------------------------------------------
    # 7. Update session count
    # ----------------------------------------------------------
    new_session_count = _increment_session_count(org_id, query, len(safe_names))

    # ----------------------------------------------------------
    # 8. Cache results in Redis
    # ----------------------------------------------------------
    _cache_results(org_id, query, safe_names, filtered_count, total_generated)

    # ----------------------------------------------------------
    # 9. Log generation + audit
    # ----------------------------------------------------------
    _log_generation(
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
            "safe_names": [n.name for n in safe_names],
        },
    )

    _audit_log(
        user_id=user_id, org_id=org_id,
        action="generate_names",
        resource_type="creative_suite",
        metadata={
            "query": query,
            "total_generated": total_generated,
            "safe_count": len(safe_names),
            "using_purchased_credits": using_purchased,
        },
    )

    # ----------------------------------------------------------
    # 10. Return response
    # ----------------------------------------------------------
    plan = _get_plan_credits(org_id, new_session_count)

    return NameSuggestionResponse(
        safe_names=safe_names,
        filtered_count=filtered_count,
        total_generated=total_generated,
        session_count=new_session_count,
        credits_remaining=plan,
        cached=False,
    )


# ============================================================
# Redis cache helpers
# ============================================================

def _get_cached_results(org_id: str, query: str) -> Optional[dict]:
    """Get cached name results from Redis."""
    r = _get_redis()
    if r is None:
        return None
    try:
        key = _session_key(org_id, query) + ":results"
        data = r.get(key)
        if data:
            parsed = json.loads(data)
            # Reconstruct SafeNameResult objects
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
    org_id: str, query: str,
    safe_names: List[SafeNameResult],
    filtered_count: int,
    total_generated: int,
) -> None:
    """Cache name results in Redis."""
    r = _get_redis()
    if r is None:
        return
    try:
        key = _session_key(org_id, query) + ":results"
        data = json.dumps({
            "safe": [n.dict() for n in safe_names],
            "filtered_count": filtered_count,
            "total_generated": total_generated,
        }, ensure_ascii=False)
        r.setex(key, settings.creative.generation_cache_ttl, data)
    except Exception:
        pass


def _get_plan_credits(org_id: str, session_count: int) -> dict:
    """Get current credit status for the response."""
    try:
        with Database() as db:
            plan = get_org_plan(db, org_id)
            cur = db.cursor()
            cur.execute("""
                SELECT COALESCE(name_credits_purchased, 0) as purchased
                FROM organizations WHERE id = %s
            """, (org_id,))
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


# ============================================================
# Logo Studio helpers
# ============================================================

def _save_logo_image(image_bytes: bytes, org_id: str, generation_id: str, index: int) -> Optional[str]:
    """
    Validate and save a generated logo image to disk.

    Returns:
        The saved file path (relative to project root), or None if invalid.
    """
    from PIL import Image

    # Validate the image is actually valid
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()
    except Exception as e:
        logger.warning("Invalid image data from Gemini", error=str(e), variation=index)
        return None

    # Re-open after verify (verify() consumes the stream)
    img = Image.open(io.BytesIO(image_bytes))

    # Convert to RGB if RGBA/P/etc — save as PNG
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")

    # Build directory: uploads/generated/logos/{org_id}/{generation_id}/
    base_dir = Path(settings.creative.logo_output_dir) / org_id / generation_id
    os.makedirs(base_dir, exist_ok=True)

    filename = f"variation_{index + 1}.png"
    file_path = base_dir / filename

    img.save(str(file_path), format="PNG")
    logger.info("logo_saved", path=str(file_path), size=len(image_bytes))

    # Return relative path (from project root)
    return str(file_path)


def _generate_all_visual_features(image_path: str) -> dict:
    """
    Generate ALL visual features for a saved logo image.

    Returns dict with keys:
        clip_embedding: list[float] | None   (512d)
        dino_embedding: list[float] | None   (768d)
        ocr_text: str                        (extracted text or "")
    Each wrapped in try/except — failures in one don't block others.
    """
    import ai

    features = {
        "clip_embedding": None,
        "dino_embedding": None,
        "ocr_text": "",
    }

    # CLIP (512d)
    try:
        features["clip_embedding"] = ai.get_clip_embedding_cached(image_path)
    except Exception as e:
        logger.warning("CLIP embedding failed", path=image_path, error=str(e))

    # DINOv2 (768d)
    try:
        features["dino_embedding"] = ai.get_dino_embedding_cached(image_path)
    except Exception as e:
        logger.warning("DINOv2 embedding failed", path=image_path, error=str(e))

    # OCR text extraction
    try:
        if hasattr(ai, "ocr_reader") and ai.ocr_reader is not None:
            texts = ai.ocr_reader.readtext(image_path, detail=0, paragraph=True)
            features["ocr_text"] = " ".join(texts).lower().strip() if texts else ""
    except Exception as e:
        logger.warning("OCR extraction failed", path=image_path, error=str(e))

    return features


def _cosine_sim(vec1, vec2) -> float:
    """Cosine similarity between two vectors (numpy)."""
    import numpy as np

    v1 = np.array(vec1, dtype=np.float32)
    v2 = np.array(vec2, dtype=np.float32)

    dot = np.dot(v1, v2)
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)

    if norm1 == 0 or norm2 == 0:
        return 0.0
    return float(dot / (norm1 * norm2))


def _full_visual_similarity_search(
    features: dict,
    nice_classes: List[int],
    brand_name: str = "",
    top_k: int = 5,
) -> list:
    """
    Search the trademark database using full visual features (CLIP + DINOv2 + OCR).

    Flow:
    1. Candidate retrieval via pgvector CLIP search (fast path, unchanged)
    2. Enhanced SELECT: also fetch dinov2_embedding, logo_ocr_text, name
    3. Per-candidate: calculate_visual_similarity(clip, dino, color=0, ocr_a, ocr_b)
    4. If OCR text exists on generated logo: also try score_pair() for text conflicts

    Args:
        features: dict from _generate_all_visual_features()
        nice_classes: Nice class filter
        brand_name: The brand name on the logo (for score_pair text conflicts)
        top_k: Number of candidates to return

    Returns:
        List of dicts with: name, application_no, bulletin_no, image_path,
                            combined_sim, visual_breakdown
    """
    from db.pool import get_connection, release_connection
    from risk_engine import calculate_visual_similarity

    clip_emb = features.get("clip_embedding")
    if not clip_emb:
        return []

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        emb_str = str(clip_emb)

        class_filter = ""
        params = [emb_str, emb_str]

        if nice_classes:
            class_filter = "AND t.nice_class_numbers && %s::int[]"
            params = [emb_str, emb_str] + [nice_classes]

        params.append(emb_str)

        sql = f"""
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
        """
        # Fetch more candidates than top_k since re-ranking may change order

        cur.execute(sql, params)
        rows = cur.fetchall()

        dino_emb = features.get("dino_embedding")
        ocr_text = features.get("ocr_text", "")

        results = []
        for row in rows:
            clip_sim = float(row.get("raw_clip_sim", 0) or 0)

            # DINOv2 similarity (in-memory cosine if both exist)
            dino_sim = 0.0
            candidate_dino = row.get("dinov2_embedding")
            if dino_emb and candidate_dino:
                try:
                    # DB returns string representation; parse if needed
                    if isinstance(candidate_dino, str):
                        import json
                        candidate_dino = json.loads(candidate_dino)
                    dino_sim = _cosine_sim(dino_emb, candidate_dino)
                except Exception:
                    dino_sim = 0.0

            # OCR text from candidate
            candidate_ocr = (row.get("logo_ocr_text") or "").lower().strip()

            # Combine visual scores — OCR compares logo text vs logo text ONLY
            combined_sim = calculate_visual_similarity(
                clip_sim=clip_sim,
                dinov2_sim=dino_sim,
                color_sim=0.0,
                ocr_text_a=ocr_text,
                ocr_text_b=candidate_ocr,
            )

            # If OCR text exists on generated logo, also try score_pair()
            # to detect text-based conflicts beyond pure visual similarity
            if ocr_text and row.get("name"):
                try:
                    sp_result = score_pair(
                        query_name=ocr_text,
                        candidate_name=row["name"],
                        visual_sim=combined_sim,
                    )
                    sp_total = sp_result.get("total", 0)
                    if sp_total > combined_sim:
                        combined_sim = sp_total
                except Exception:
                    pass

            results.append({
                "name": row.get("name"),
                "application_no": row.get("application_no"),
                "bulletin_no": row.get("bulletin_no"),
                "image_path": row.get("image_path"),
                "combined_sim": combined_sim,
                "visual_breakdown": {
                    "clip": vis["clip_score"],
                    "dino": vis["dino_score"],
                    "ocr": vis["ocr_score"],
                    "raw_combined": vis["raw_combined"],
                    "components_used": vis["components_used"],
                },
            })

        # Sort by combined_sim descending and take top_k
        results.sort(key=lambda r: r["combined_sim"], reverse=True)
        return results[:top_k]

    except Exception as e:
        logger.error("Full visual similarity search failed", error=str(e))
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
    """
    Insert a record into generated_images and return its UUID.
    """
    try:
        with Database() as db:
            cur = db.cursor()
            clip_str = str(clip_embedding) if clip_embedding else None
            dino_str = str(dino_embedding) if dino_embedding else None
            breakdown_json = json.dumps(visual_breakdown, ensure_ascii=False) if visual_breakdown else None
            cur.execute("""
                INSERT INTO generated_images
                    (generation_log_id, org_id, image_path, clip_embedding,
                     dino_embedding, ocr_text, visual_breakdown,
                     similarity_score, is_safe)
                VALUES (%s, %s, %s, %s::halfvec,
                        %s::halfvec, %s, %s::jsonb,
                        %s, %s)
                RETURNING id
            """, (
                generation_log_id, org_id, image_path, clip_str,
                dino_str, ocr_text or None, breakdown_json,
                similarity_score, is_safe,
            ))
            db.commit()
            row = cur.fetchone()
            return str(row["id"]) if row else None
    except Exception as e:
        logger.error("Failed to store generated image", error=str(e))
        return None


def _get_logo_credits_remaining(org_id: str) -> dict:
    """Get current logo credit status for the response."""
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute("""
                SELECT
                    COALESCE(logo_credits_monthly, 0) as monthly,
                    COALESCE(logo_credits_purchased, 0) as purchased
                FROM organizations WHERE id = %s
            """, (org_id,))
            row = cur.fetchone()
            if row:
                return {"monthly": row["monthly"], "purchased": row["purchased"]}
    except Exception:
        pass
    return {"monthly": 0, "purchased": 0}


def _build_closest_match_image_url(match: dict) -> Optional[str]:
    """Build image URL for a closest-match trademark."""
    image_path = match.get("image_path")
    if image_path:
        return f"/api/trademark-image/{image_path}"
    return None


# ============================================================
# Endpoint: POST /api/v1/tools/generate-logo
# ============================================================

@router.post("/generate-logo", response_model=LogoGenerationResponse)
async def generate_logo(
    request: LogoGenerationRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Generate AI-powered logo variations and audit each against the 2.3M
    trademark image database using CLIP visual similarity.

    Flow: Credit check → Gemini generates 4 logos → CLIP embedding → pgvector
    visual similarity search → safety audit → results returned.
    """
    org_id = str(current_user.organization_id)
    user_id = str(current_user.id)

    # ----------------------------------------------------------
    # 1. Credit check
    # ----------------------------------------------------------
    with Database() as db:
        can_generate, reason, details = check_logo_generation_eligibility(db, org_id)

    if not can_generate:
        status_code = 403 if reason == "upgrade_required" else 402
        raise HTTPException(status_code=status_code, detail=details)

    # ----------------------------------------------------------
    # 2. Deduct credit BEFORE generation (prevents race conditions)
    # ----------------------------------------------------------
    with Database() as db:
        deducted = deduct_logo_credit(db, org_id)

    if not deducted:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "credits_exhausted",
                "message": "Logo olusturma kredisi dusulemedi.",
                "message_en": "Could not deduct logo generation credit.",
            },
        )

    # ----------------------------------------------------------
    # 3. Generate logos via Gemini
    # ----------------------------------------------------------
    from generative_ai.gemini_client import get_gemini_client, GeminiError

    client = get_gemini_client()
    if not client.is_available():
        # Refund credit since Gemini is not available
        with Database() as db:
            refund_logo_credit(db, org_id)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_unavailable",
                "message": "Logo olusturma servisi su anda kullanilamiyor. Krediniz iade edildi.",
                "message_en": "Logo generation service is currently unavailable. Your credit has been refunded.",
            },
        )

    # Build description with color preferences
    description = request.description
    if request.color_preferences:
        description = f"{description}. Color scheme: {request.color_preferences}".strip(". ")

    try:
        image_bytes_list = await client.generate_logos(
            brand_name=request.brand_name,
            description=description,
            style=request.style,
            count=settings.creative.logo_images_per_run,
        )
    except GeminiError as e:
        logger.error("gemini_logo_generation_failed", error=str(e), retries=e.retries_attempted)
        # Refund credit on Gemini failure
        with Database() as db:
            refund_logo_credit(db, org_id)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generation_failed",
                "message": "Logo olusturma basarisiz oldu. Krediniz iade edildi.",
                "message_en": f"Logo generation failed: {e}. Your credit has been refunded.",
            },
        )

    if not image_bytes_list:
        with Database() as db:
            refund_logo_credit(db, org_id)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "no_logos_generated",
                "message": "Logo olusturulamadi. Krediniz iade edildi.",
                "message_en": "No logos could be generated. Your credit has been refunded.",
            },
        )

    # ----------------------------------------------------------
    # 4. Create generation log entry
    # ----------------------------------------------------------
    generation_id = str(uuid.uuid4())
    log_id = _log_generation(
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
        log_id = generation_id  # Fallback to UUID if logging failed

    # ----------------------------------------------------------
    # 5. Save images, generate visual features, run full similarity audit
    # ----------------------------------------------------------
    logo_results: List[LogoResult] = []

    for idx, img_bytes in enumerate(image_bytes_list):
        # 5a. Save image to disk
        saved_path = _save_logo_image(img_bytes, org_id, generation_id, idx)
        if not saved_path:
            continue

        # 5b. Generate ALL visual features (CLIP + DINOv2 + OCR)
        features = _generate_all_visual_features(saved_path)

        # 5c. Full visual similarity search against trademark database
        max_similarity = 0.0
        closest_match_name = None
        closest_match_image_url = None
        top_breakdown = None

        if features.get("clip_embedding"):
            matches = _full_visual_similarity_search(
                features=features,
                nice_classes=request.nice_classes,
                brand_name=request.brand_name,
                top_k=5,
            )
            if matches:
                top_match = matches[0]
                max_similarity = top_match["combined_sim"]
                closest_match_name = top_match["name"]
                closest_match_image_url = _build_closest_match_image_url(top_match)
                top_breakdown = top_match.get("visual_breakdown")

        # 5d. Safety check using centralized threshold
        is_safe = max_similarity < RISK_THRESHOLDS["high"]

        # 5e. Store in generated_images table (with all visual features)
        image_id = _store_generated_image(
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
            image_id = str(uuid.uuid4())  # Fallback

        logo_results.append(LogoResult(
            image_id=image_id,
            image_url=f"/api/v1/tools/generated-image/{image_id}",
            similarity_score=round(max_similarity * 100, 1),
            closest_match_name=closest_match_name,
            closest_match_image_url=closest_match_image_url,
            is_safe=is_safe,
            visual_breakdown=top_breakdown,
        ))

    if not logo_results:
        # All image saves/embeds failed — refund
        with Database() as db:
            refund_logo_credit(db, org_id)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "processing_failed",
                "message": "Logo isleme basarisiz oldu. Krediniz iade edildi.",
                "message_en": "Logo processing failed. Your credit has been refunded.",
            },
        )

    # ----------------------------------------------------------
    # 6. Audit log + return response
    # ----------------------------------------------------------
    _audit_log(
        user_id=user_id, org_id=org_id,
        action="generate_logos",
        resource_type="creative_suite",
        resource_id=log_id,
        metadata={
            "brand_name": request.brand_name,
            "style": request.style,
            "variations_generated": len(logo_results),
            "safe_count": sum(1 for lr in logo_results if lr.is_safe),
        },
    )

    credits = _get_logo_credits_remaining(org_id)

    return LogoGenerationResponse(
        logos=logo_results,
        credits_remaining=credits,
        generation_id=log_id,
    )


# ============================================================
# Endpoint: GET /api/v1/tools/generated-image/{image_id}
# ============================================================

@router.get("/generated-image/{image_id}")
async def get_generated_image(
    image_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Serve a generated logo image by its UUID.
    Only the owning organization can access their images.
    """
    org_id = str(current_user.organization_id)

    # Validate UUID format
    try:
        UUID(image_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid image ID format")

    # Look up the image in the database
    with Database() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT image_path, org_id
            FROM generated_images
            WHERE id = %s
        """, (image_id,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Image not found")

    # Security: verify org_id matches
    if str(row["org_id"]) != org_id:
        raise HTTPException(status_code=403, detail="Access denied")

    image_path = row["image_path"]

    # Path traversal protection
    if ".." in image_path:
        raise HTTPException(status_code=400, detail="Invalid image path")

    # Check file exists
    if not os.path.isfile(image_path):
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    # Determine media type
    ext = os.path.splitext(image_path)[1].lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    media_type = media_types.get(ext, "image/png")

    return FileResponse(
        path=image_path,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=604800",  # 7-day cache (images don't change)
        },
    )


# ============================================================
# Endpoint: GET /api/v1/tools/generation-history
# ============================================================

@router.get("/generation-history", response_model=GenerationHistoryResponse)
async def get_generation_history(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    feature_type: Optional[str] = Query(None, regex="^(NAME|LOGO)$"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    List past generations for the authenticated user's organization.
    Supports pagination and filtering by feature type (NAME or LOGO).
    """
    org_id = str(current_user.organization_id)

    with Database() as db:
        cur = db.cursor()

        # Build WHERE clause
        where_clause = "WHERE gl.org_id = %s"
        params = [org_id]

        if feature_type:
            where_clause += " AND gl.feature_type = %s"
            params.append(feature_type)

        # Count total
        cur.execute(f"""
            SELECT COUNT(*) as total
            FROM generation_logs gl
            {where_clause}
        """, params)
        total = cur.fetchone()["total"]

        total_pages = max(1, math.ceil(total / per_page))
        offset = (page - 1) * per_page

        # Fetch page of generation logs
        cur.execute(f"""
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
        """, params + [per_page, offset])
        rows = cur.fetchall()

        # For LOGO entries, fetch associated generated_images
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
                cur.execute("""
                    SELECT
                        id, image_path, similarity_score, is_safe, created_at
                    FROM generated_images
                    WHERE generation_log_id = %s AND org_id = %s
                    ORDER BY created_at
                """, (str(row["id"]), org_id))
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


# ============================================================
# Endpoint: GET /api/v1/tools/status
# ============================================================

@router.get("/status")
async def creative_suite_status():
    """
    Returns the availability status of Creative Suite services.
    Used by the frontend to enable/disable AI Studio features gracefully.
    No auth required so the UI can check before user interacts.
    """
    from utils.feature_flags import is_feature_enabled

    status = {
        "name_generator": {"available": False, "reason": ""},
        "logo_studio": {"available": False, "reason": ""},
    }

    # Feature flag kill switch
    if not is_feature_enabled("ai_studio_enabled"):
        reason = "AI Studio gecici olarak devre disi birakildi"
        status["name_generator"]["reason"] = reason
        status["logo_studio"]["reason"] = reason
        return status

    # Check Gemini client availability
    try:
        from generative_ai.gemini_client import get_gemini_client
        client = get_gemini_client()
        if client.is_available():
            status["name_generator"]["available"] = True
            status["logo_studio"]["available"] = True
        else:
            reason = "Gemini API anahtari yapilandirilmamis"
            status["name_generator"]["reason"] = reason
            status["logo_studio"]["reason"] = reason
    except Exception as e:
        reason = f"Servis baslatilamadi: {str(e)}"
        status["name_generator"]["reason"] = reason
        status["logo_studio"]["reason"] = reason

    # Check CLIP model for Logo Studio (visual similarity requires it)
    if status["logo_studio"]["available"]:
        try:
            import ai
            if not hasattr(ai, "clip_model") or ai.clip_model is None:
                status["logo_studio"]["reason"] = "CLIP modeli yuklenmemis"
                # Still available — CLIP loads lazily on first logo generation
        except Exception:
            pass

    return status

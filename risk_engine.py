import os
import time
import math

# ===================== CRITICAL STABILITY FIX =====================
os.environ["XFORMERS_DISABLED"] = "1"

import psycopg2
import torch
import numpy as np
import cv2
from pathlib import Path
from PIL import Image
from torchvision import transforms
from sentence_transformers import CrossEncoder
from dotenv import load_dotenv

# Import Pipeline Components
import scrapper
import ingest
import ai  # Optimization: Reuse models loaded here
# CENTRALIZED IDF SCORING - consistent across the entire system
from utils.idf_scoring import (
    calculate_text_similarity as idf_text_similarity,
    calculate_adjusted_score,
    calculate_risk_score,
    analyze_query,
    get_word_weight,
    get_word_class,
    is_generic_word
)
# Translation similarity for cross-language conflict detection
from utils.translation import calculate_translation_similarity
# Class 99 (Global Brand) utilities - covers all 45 Nice classes
from utils.class_utils import (
    GLOBAL_CLASS,
    classes_overlap,
    get_overlapping_classes,
    expand_classes
)
# Legacy imports (for backward compatibility with existing code)
from idf_scoring import compute_idf_weighted_score  # 3-tier IDF scoring

# ===================== DATABASE CONNECTION POOL =====================
from db.pool import (
    get_connection,
    release_connection,
    connection_context,
    close_pool
)

# ===================== STRUCTURED LOGGING =====================
from logging_config import get_logger, log_timing, setup_logging

# Load environment vars
load_dotenv()

# Setup Logging
setup_logging()
logger = get_logger(__name__)

# ===================== CONFIG =====================
DATA_ROOT = Path(os.getenv("DATA_ROOT", r"C:\Users\701693\turk_patent\bulletins\Marka"))

# ===================== RISK THRESHOLDS — Single source of truth =====================
# Used by: risk_engine, watchlist/scanner, workers/universal_scanner, database/crud, agentic_search, frontend
RISK_THRESHOLDS = {
    "critical": 0.90,    # >= 90%
    "very_high": 0.80,   # >= 80%
    "high": 0.70,        # >= 70%
    "medium": 0.50,      # >= 50%
    "low": 0.0,          # < 50%
}


def get_risk_level(score: float) -> str:
    """Single source of truth for risk level classification."""
    if score >= RISK_THRESHOLDS["critical"]:
        return "critical"
    elif score >= RISK_THRESHOLDS["very_high"]:
        return "very_high"
    elif score >= RISK_THRESHOLDS["high"]:
        return "high"
    elif score >= RISK_THRESHOLDS["medium"]:
        return "medium"
    return "low"

def calculate_visual_similarity(
    clip_sim: float = 0.0,
    dinov2_sim: float = 0.0,
    color_sim: float = 0.0,
    ocr_text_a: str = "",
    ocr_text_b: str = "",
) -> float:
    """
    Combine all visual signals into one score.
    OCR compares logo text vs logo text ONLY — never brand name vs OCR.

    Weights: CLIP 0.35, DINOv2 0.30, color 0.15, OCR 0.20
    """
    from difflib import SequenceMatcher

    if ocr_text_a and ocr_text_b:
        ocr_sim = SequenceMatcher(
            None,
            ocr_text_a.lower().strip(),
            ocr_text_b.lower().strip(),
        ).ratio()
    else:
        ocr_sim = 0.0

    return (
        clip_sim * 0.35
        + dinov2_sim * 0.30
        + color_sim * 0.15
        + ocr_sim * 0.20
    )


def get_status_category(status):
    """
    Categorizes trademark status for user guidance.

    CANCELLED = WARNING (not opportunity!) because:
    - Usually means court cancelled after legal battle
    - Indicates the name is being actively defended
    - Previous applicant lost in court/appeal
    """
    categories = {
        # HIGH RISK - Blocks your application
        'Registered': {'level': 'RISK', 'multiplier': 1.0,
                       'message': '⛔ Active trademark - blocks registration'},
        'Published': {'level': 'RISK', 'multiplier': 1.0,
                      'message': '⛔ Pending registration - likely to block'},
        'Renewed': {'level': 'RISK', 'multiplier': 1.0,
                    'message': '⛔ Recently renewed - actively protected'},
        'Opposed': {'level': 'RISK', 'multiplier': 0.9,
                    'message': '⛔ Under opposition but still active'},
        'Applied': {'level': 'RISK', 'multiplier': 0.85,
                    'message': '⛔ Pending application - may be registered soon'},

        # WARNING - Indicates protection exists / legal risk
        'Refused': {'level': 'WARNING', 'multiplier': 0.8,
                    'message': '⚠️ Previous application REJECTED - office protects this name'},
        'Cancelled': {'level': 'WARNING', 'multiplier': 0.75,
                      'message': '⚠️ CANCELLED by court/appeal - name is legally defended!'},
        'Partial Refusal': {'level': 'WARNING', 'multiplier': 0.6,
                            'message': '⚠️ Partially rejected - some classes blocked'},

        # OPPORTUNITY - Name may be available
        'Expired': {'level': 'OPPORTUNITY', 'multiplier': 0.3,
                    'message': '💡 EXPIRED - Name available! (Owner has 6-month grace period to renew)'},
        'Withdrawn': {'level': 'OPPORTUNITY', 'multiplier': 0.3,
                      'message': '💡 WITHDRAWN - Owner abandoned, name available'},

        'Unknown': {'level': 'UNKNOWN', 'multiplier': 0.5,
                    'message': 'Status unknown - verify manually'}
    }
    return categories.get(status, categories['Unknown'])


# ===================== TURKISH TEXT NORMALIZATION =====================

def normalize_turkish(text: str) -> str:
    """
    Normalize Turkish characters to ASCII equivalents for comparison.
    This ensures 'doğan' matches 'dogan', 'şeker' matches 'seker', etc.
    """
    if not text:
        return ""
    replacements = {
        'ğ': 'g', 'Ğ': 'G',
        'ı': 'i', 'İ': 'I',
        'ö': 'o', 'Ö': 'O',
        'ü': 'u', 'Ü': 'U',
        'ş': 's', 'Ş': 'S',
        'ç': 'c', 'Ç': 'C',
    }
    for tr_char, en_char in replacements.items():
        text = text.replace(tr_char, en_char)
    return text


def check_substring_containment(query: str, target: str) -> float:
    """
    Check if query is contained in target or vice versa.
    Returns 1.0 if containment found, 0.0 otherwise.

    Examples:
        "nike" in "nike sports" → 1.0
        "dogan" in "d.p doğan patent" → 1.0 (after normalization)
    """
    q = normalize_turkish(query.lower().strip())
    t = normalize_turkish(target.lower().strip())

    if not q or not t:
        return 0.0

    if q in t or t in q:
        return 1.0
    return 0.0


def calculate_token_overlap(query: str, target: str) -> float:
    """
    Calculate the ratio of query tokens found in target.

    Examples:
        "dogan patent" vs "d.p doğan patent":
        - q_tokens = {"dogan", "patent"}
        - t_tokens = {"d.p", "dogan", "patent"} (after normalization)
        - matches = {"dogan", "patent"} = 2/2 = 1.0 (100% overlap!)
    """
    q_norm = normalize_turkish(query.lower().strip())
    t_norm = normalize_turkish(target.lower().strip())

    q_tokens = set(q_norm.split())
    t_tokens = set(t_norm.split())

    if not q_tokens:
        return 0.0

    # How many query tokens appear in target?
    matches = q_tokens.intersection(t_tokens)
    return len(matches) / len(q_tokens)


def calculate_turkish_similarity(query: str, target: str) -> float:
    """
    Calculate text similarity with Turkish character normalization.

    This ensures 'doğan' properly matches 'dogan' by normalizing
    Turkish special characters before comparison.

    Uses multiple approaches and returns the maximum:
    1. SequenceMatcher on normalized strings
    2. Containment check (query in target or vice versa)
    3. Token overlap ratio

    Args:
        query: Search query (e.g., "dogan patent")
        target: Candidate trademark name (e.g., "d.p doğan patent")

    Returns:
        float: Similarity score between 0.0 and 1.0

    Examples:
        "dogan" vs "doğan" → 1.0 (exact after normalization)
        "dogan patent" vs "d.p doğan patent" → 1.0 (containment)
        "dogan" vs "dogancay" → ~0.67 (SequenceMatcher)
    """
    from difflib import SequenceMatcher

    if not query or not target:
        return 0.0

    # Normalize Turkish characters
    q_norm = normalize_turkish(query.lower().strip())
    t_norm = normalize_turkish(target.lower().strip())

    if not q_norm or not t_norm:
        return 0.0

    # 1. Exact match after normalization
    if q_norm == t_norm:
        return 1.0

    # 2. Containment check
    if q_norm in t_norm or t_norm in q_norm:
        return 1.0

    # 3. SequenceMatcher similarity
    seq_ratio = SequenceMatcher(None, q_norm, t_norm).ratio()

    # 4. Token overlap
    token_overlap = calculate_token_overlap(query, target)

    # Return maximum of all methods
    return max(seq_ratio, token_overlap)


def _dynamic_combine(
    text_idf_score: float,
    visual_sim: float,
    translation_sim: float,
    phonetic_sim: float,
) -> dict:
    """
    Combine all scoring signals with dynamic confidence-based weighting.

    Each signal has a base weight reflecting its importance. Signals with higher
    scores get exponentially boosted weight (confident = more influence). Signals
    with score=0 get weight=0 (dead signals don't dilute active ones). Weights
    are normalized to sum to 1.0.

    Args:
        text_idf_score: IDF-weighted text score from Cases A-F
        visual_sim: Combined CLIP+DINOv2+color+OCR visual similarity
        translation_sim: Cross-language translation similarity
        phonetic_sim: Phonetic match (0.0 or 1.0 from dmetaphone)

    Returns:
        dict with 'total' and 'dynamic_weights'
    """
    BASE_WEIGHTS = {
        "text":        0.55,
        "visual":      0.25,
        "translation": 0.15,
        "phonetic":    0.05,
    }

    STEEPNESS = 4.0

    signals = {
        "text":        text_idf_score,
        "visual":      visual_sim,
        "translation": translation_sim,
        "phonetic":    phonetic_sim,
    }

    boosted_weights = {}
    for key, score in signals.items():
        if score > 0:
            boosted_weights[key] = BASE_WEIGHTS[key] * math.exp(score * STEEPNESS)

    if not boosted_weights:
        return {
            "total": 0.0,
            "dynamic_weights": {k: 0.0 for k in BASE_WEIGHTS},
        }

    total_weight = sum(boosted_weights.values())
    final_weights = {k: v / total_weight for k, v in boosted_weights.items()}

    total = sum(signals[k] * final_weights[k] for k in final_weights)
    total = max(0.0, min(1.0, total))

    # Floor: near-perfect translation match guarantees high total risk.
    # e.g. APPLE ↔ ELMA with translation_sim >= 0.95 → total can't be below 0.90.
    if translation_sim >= 0.95:
        total = max(total, 0.90)

    all_weights = {k: 0.0 for k in BASE_WEIGHTS}
    all_weights.update(final_weights)

    return {
        "total": round(total, 4),
        "dynamic_weights": {k: round(v, 4) for k, v in all_weights.items()},
    }


def score_pair(query_name, candidate_name,
               text_sim=0.0, semantic_sim=0.0, visual_sim=0.0, phonetic_sim=0.0,
               candidate_translations=None):
    """
    Score a query name against a candidate name. Single source of scoring truth.

    Callers compute raw similarity values from their data sources (SQL, in-memory cosine, etc.)
    and pass them here for consistent IDF-weighted scoring + dynamic confidence combination.

    Args:
        query_name: The query/watched trademark name
        candidate_name: The candidate/existing trademark name
        text_sim: Pre-computed text similarity (pg_trgm, SequenceMatcher, etc.)
        semantic_sim: Pre-computed semantic similarity (pgvector cosine or in-memory cosine)
        visual_sim: Pre-computed visual similarity (pgvector cosine or combined CLIP/DINOv2/color)
        phonetic_sim: Pre-computed phonetic similarity (0.0 or 1.0 from metaphone, etc.)
        candidate_translations: Optional dict {name_tr, name_en, name_ku, name_fa}

    Returns:
        dict: Score breakdown with dynamic combined total.
              Key fields: total, text_idf_score, text_similarity, semantic_similarity,
              visual_similarity, phonetic_similarity, translation_similarity,
              dynamic_weights, exact_match, matched_words, etc.
    """
    # 1. Recalculate text similarity with Turkish normalization
    lex_turkish = calculate_turkish_similarity(query_name, candidate_name)

    # Cross-language: also check against pre-computed translations
    if candidate_translations:
        for key in ('name_tr', 'name_en', 'name_ku', 'name_fa'):
            trans_val = candidate_translations.get(key) or ''
            if trans_val:
                lex_turkish = max(lex_turkish, calculate_turkish_similarity(query_name, trans_val))

    # Use the maximum of caller-provided text_sim and our Turkish calculation
    text_sim = max(text_sim, lex_turkish)

    # 2. Translation similarity (cross-language conflict detection)
    #    Uses pre-stored name_tr from DB (0 NLLB calls per candidate).
    candidate_name_tr = (candidate_translations or {}).get('name_tr') or ''
    trans_sim = calculate_translation_similarity(
        query_name, candidate_name, candidate_name_tr=candidate_name_tr
    )

    # 3. IDF-weighted scoring (3-tier) — produces the text component score
    _idf_total, breakdown = compute_idf_weighted_score(
        query=query_name,
        target=candidate_name,
        text_sim=text_sim,
        semantic_sim=semantic_sim,
        phonetic_sim=phonetic_sim,
        visual_sim=visual_sim
    )

    # 4. Store translation in breakdown
    breakdown['translation_similarity'] = trans_sim

    # 5. Dynamic confidence-weighted combination of ALL signals
    combined = _dynamic_combine(
        text_idf_score=_idf_total,
        visual_sim=visual_sim,
        translation_sim=trans_sim,
        phonetic_sim=phonetic_sim,
    )

    breakdown['total'] = combined['total']
    breakdown['text_idf_score'] = _idf_total
    breakdown['dynamic_weights'] = combined['dynamic_weights']

    return breakdown


class RiskEngine:
    def __init__(self, existing_conn=None):
        init_start = time.perf_counter()
        logger.info("Initializing Risk Engine", reusing_models=True)

        # --- OPTIMIZATION: Reuse models from ai.py to save VRAM ---
        self.device = ai.device
        self.text_model = ai.text_model
        self.clip_model = ai.clip_model
        self.clip_preprocess = ai.clip_preprocess
        self.dino_model = ai.dinov2_model
        self.dino_preprocess = ai.dinov2_preprocess

        # Load CrossEncoder (Not present in ai.py, so we load it here)
        try:
            self.cross_encoder = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-12-v2', device=self.device)
            logger.info("CrossEncoder loaded", model="ms-marco-MiniLM-L-12-v2")
        except Exception as e:
            logger.warning("CrossEncoder failed, using Bi-Encoder only", error=str(e))
            self.cross_encoder = None

        # Track if we own the connection (for cleanup)
        self._owns_connection = False

        if existing_conn:
            self.conn = existing_conn
        else:
            try:
                # Get connection from pool
                self.conn = get_connection()
                self._owns_connection = True
                logger.info("Database connected", source="pool")
            except Exception as e:
                logger.error("Database connection failed", error=str(e))
                raise e

        self._ensure_phonetic_capabilities()

        init_duration = (time.perf_counter() - init_start) * 1000
        logger.info("Risk Engine initialized", duration_ms=round(init_duration, 2), device=str(self.device))

    def close(self):
        """Release database connection back to the pool."""
        if self._owns_connection and self.conn:
            try:
                release_connection(self.conn)
                logger.info("Database connection released")
            except Exception as e:
                logger.error("Error releasing connection", error=str(e))
            finally:
                self.conn = None
                self._owns_connection = False

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - release connection."""
        self.close()
        return False

    def __del__(self):
        """Destructor - ensure connection is released."""
        if hasattr(self, '_owns_connection') and self._owns_connection:
            self.close()

    def _ensure_phonetic_capabilities(self):
        try:
            cur = self.conn.cursor()
            cur.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;")
            # Safe split string
            check_query = (
                "SELECT indexname FROM pg_indexes "
                "WHERE indexname = 'idx_tm_phonetic'"
            )
            cur.execute(check_query)
            if not cur.fetchone():
                cur.execute("CREATE INDEX idx_tm_phonetic ON trademarks (dmetaphone(name));")
                self.conn.commit()
        except Exception:
            self.conn.rollback()

    def _encode_single_image(self, pil_img):
        # Helper to extract all 3 visual vectors
        try:
            cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            hsv_img = cv2.cvtColor(cv_img, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv_img], [0, 1, 2], None, [8, 2, 2], [0, 180, 0, 256, 0, 256])
            cv2.normalize(hist, hist)
            color_vec = hist.flatten().tolist()
        except:
            color_vec = None

        clip_input = self.clip_preprocess(pil_img).unsqueeze(0).to(self.device)
        with torch.no_grad():
            clip_feat = self.clip_model.encode_image(clip_input)
            clip_feat /= clip_feat.norm(dim=-1, keepdim=True)
            clip_vec = clip_feat.squeeze().tolist()

        dino_input = self.dino_preprocess(pil_img).unsqueeze(0).to(self.device)
        with torch.no_grad():
            dino_vec = self.dino_model(dino_input).flatten().tolist()
            
        return clip_vec, dino_vec, color_vec

    def get_query_vectors(self, name, image_path=None):
        text_vec = self.text_model.encode(name).tolist()
        img_vec, dino_vec, color_vec = None, None, None

        if image_path and os.path.exists(image_path):
            try:
                pil_img = Image.open(image_path).convert('RGB')
                img_vec, dino_vec, color_vec = self._encode_single_image(pil_img)
            except Exception as e:
                logging.error(f"Image process failed: {e}")

        return text_vec, img_vec, dino_vec, color_vec

    def suggest_classes(self, description, limit=3):
        if not description or not str(description).strip():
            return []
        
        desc_vec = self.text_model.encode(description).tolist()
        cur = self.conn.cursor()
        sql = """
            SELECT class_number, description, 
                   (1 - (description_embedding <=> %s::halfvec)) as similarity
            FROM nice_classes_lookup
            ORDER BY similarity DESC
            LIMIT %s
        """
        try:
            cur.execute(sql, (str(desc_vec), limit))
            results = cur.fetchall()
            return [{"class_number": r[0], "description": r[1], "confidence": float(r[2])} for r in results]
        except Exception:
            self.conn.rollback()
            return []

    def pre_screen_candidates(self, name_input, target_classes=None, limit=30):
        cur = self.conn.cursor()

        # Normalize query for Turkish character matching
        name_normalized = normalize_turkish(name_input)
        seen_ids = set()
        all_candidates = []

        # First, check for EXACT matches (highest priority)
        # Cross-language: also match against pre-computed translations
        exact_sql = """
            SELECT id, application_no, name, nice_class_numbers, image_path,
                   1.0 as lexical_score
            FROM trademarks
            WHERE (LOWER(name) = LOWER(%s)
                   OR LOWER(name_tr) = LOWER(%s)
                   OR LOWER(name_en) = LOWER(%s)
                   OR LOWER(name_ku) = LOWER(%s)
                   OR LOWER(name_fa) = LOWER(%s))
        """
        exact_params = [name_input, name_input, name_input, name_input, name_input]

        if target_classes and len(target_classes) > 0:
            # Class 99 (Global Brand) covers ALL 45 classes - include in any class filter
            exact_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
            exact_params.append(target_classes)

        exact_sql += " LIMIT 10;"
        cur.execute(exact_sql, exact_params)
        exact_matches = cur.fetchall()

        for match in exact_matches:
            if match[0] not in seen_ids:
                seen_ids.add(match[0])
                all_candidates.append(match)

        # Also check for normalized exact matches (doğan == dogan)
        if name_normalized != name_input.lower():
            # Search for Turkish variants using normalized form
            normalized_sql = """
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       1.0 as lexical_score
                FROM trademarks
                WHERE LOWER(
                    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')
                ) = LOWER(%s)
            """
            norm_params = [name_normalized]

            if target_classes and len(target_classes) > 0:
                # Class 99 (Global Brand) covers ALL 45 classes - include in any class filter
                normalized_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                norm_params.append(target_classes)

            normalized_sql += " LIMIT 10;"
            cur.execute(normalized_sql, norm_params)
            norm_matches = cur.fetchall()

            for match in norm_matches:
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match)

        # Then get similar matches using PostgreSQL similarity
        remaining_limit = limit - len(all_candidates)
        if remaining_limit > 0:
            sql = """
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       GREATEST(
                           similarity(name, %s),
                           similarity(
                               LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                               REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                               'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                               'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')),
                               %s
                           ),
                           COALESCE(similarity(name_tr, %s), 0),
                           COALESCE(similarity(name_en, %s), 0),
                           COALESCE(similarity(name_ku, %s), 0),
                           COALESCE(similarity(name_fa, %s), 0)
                       ) as lexical_score
                FROM trademarks
                WHERE current_status NOT IN ('Refused', 'Withdrawn')
                  AND LOWER(name) != LOWER(%s)
            """
            params = [name_input, name_normalized, name_input, name_input, name_input, name_input, name_input]

            if target_classes and len(target_classes) > 0:
                # Class 99 (Global Brand) covers ALL 45 classes - include in any class filter
                sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                params.append(target_classes)

            sql += """
                ORDER BY GREATEST(
                    similarity(name, %s),
                    similarity(
                        LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                        'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                        'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')),
                        %s
                    ),
                    COALESCE(similarity(name_tr, %s), 0),
                    COALESCE(similarity(name_en, %s), 0),
                    COALESCE(similarity(name_ku, %s), 0),
                    COALESCE(similarity(name_fa, %s), 0)
                ) DESC LIMIT %s;
            """
            params.extend([name_input, name_normalized, name_input, name_input, name_input, name_input, remaining_limit])

            cur.execute(sql, params)
            similar_matches = cur.fetchall()

            for match in similar_matches:
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match)

        return all_candidates

    def calculate_hybrid_risk(self, candidates, name_input, query_text_vec,
                                 query_img_vec, query_dino_vec=None, query_color_vec=None):
        if not candidates: return []

        candidate_ids = [str(c[0]) for c in candidates]

        # Build visual columns: CLIP, DINOv2, color cosine + OCR text
        clip_col = f"(1 - (t.image_embedding <=> %s::halfvec))" if query_img_vec else "0.0"
        dino_col = f"(1 - (t.dinov2_embedding <=> %s::halfvec))" if query_dino_vec else "0.0"
        color_col = f"(1 - (t.color_histogram <=> %s::halfvec))" if query_color_vec else "0.0"

        sql = f"""
            SELECT
                t.application_no, t.name, t.current_status, t.nice_class_numbers, t.image_path,
                t.name_tr, t.name_en, t.name_ku, t.name_fa,
                t.holder_name, t.holder_tpe_client_id,
                (1 - (t.text_embedding <=> %s::halfvec)) as score_semantic,
                similarity(t.name, %s) as score_lexical,
                {clip_col} as score_clip,
                {dino_col} as score_dinov2,
                {color_col} as score_color,
                t.logo_ocr_text,
                t.bulletin_no,
                (dmetaphone(t.name) = dmetaphone(%s)) as phonetic_match,
                (t.extracted_goods IS NOT NULL
                    AND t.extracted_goods != '[]'::jsonb
                    AND t.extracted_goods != 'null'::jsonb) AS has_extracted_goods
            FROM trademarks t
            WHERE t.id = ANY(%s::uuid[])
        """

        params = [str(query_text_vec), name_input]
        if query_img_vec:
            params.append(str(query_img_vec))
        if query_dino_vec:
            params.append(str(query_dino_vec))
        if query_color_vec:
            params.append(str(query_color_vec))
        params.extend([name_input, candidate_ids])

        cur = self.conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()

        results = []

        for r in rows:
            candidate_name = r[1] or ""
            candidate_name_tr = r[5] or ""
            candidate_name_en = r[6] or ""
            candidate_name_ku = r[7] or ""
            candidate_name_fa = r[8] or ""
            candidate_holder_name = r[9]
            candidate_holder_tpe_id = r[10]
            sem = float(r[11]) if r[11] is not None else 0.0
            lex_postgres = float(r[12]) if r[12] is not None else 0.0
            clip_sim = float(r[13]) if r[13] is not None else 0.0
            dino_sim = float(r[14]) if r[14] is not None else 0.0
            color_sim = float(r[15]) if r[15] is not None else 0.0
            candidate_ocr = (r[16] or "").strip()
            phon_match = bool(r[18]) if len(r) > 18 and r[18] is not None else False
            has_eg = bool(r[19]) if len(r) > 19 and r[19] is not None else False

            # Full composite visual score (CLIP 0.35 + DINOv2 0.30 + color 0.15 + OCR 0.20)
            # Query OCR not available (RiskEngine doesn't load EasyOCR), so ocr_text_a=""
            vis = calculate_visual_similarity(
                clip_sim=clip_sim,
                dinov2_sim=dino_sim,
                color_sim=color_sim,
                ocr_text_a="",
                ocr_text_b=candidate_ocr,
            )

            # Centralized scoring via score_pair()
            score_breakdown = score_pair(
                query_name=name_input,
                candidate_name=candidate_name,
                text_sim=lex_postgres,
                semantic_sim=sem,
                visual_sim=vis,
                phonetic_sim=1.0 if phon_match else 0.0,
                candidate_translations={
                    'name_tr': candidate_name_tr, 'name_en': candidate_name_en,
                    'name_ku': candidate_name_ku, 'name_fa': candidate_name_fa,
                }
            )

            results.append({
                "application_no": r[0],
                "name": candidate_name,
                "status": r[2],
                "classes": r[3],
                "image_path": r[4],
                "holder_name": candidate_holder_name,
                "holder_tpe_client_id": candidate_holder_tpe_id,
                "bulletin_no": r[17] if len(r) > 17 else None,
                "exact_match": score_breakdown.get("exact_match", False),
                "scores": score_breakdown,
                "has_extracted_goods": has_eg
            })

        # Sort: exact matches first, then by total score
        results.sort(key=lambda x: (x.get('exact_match', False), x['scores']['total']), reverse=True)
        return results

    @log_timing("assess_brand_risk")
    def assess_brand_risk(self, name, image_path=None, target_classes=None, description=None):
        """
        Fast path only - returns result from local DB without live investigation.
        Returns tuple: (result_dict, needs_live_investigation: bool)
        """
        query_start = time.perf_counter()
        logger.info("Assessing brand risk", trademark_name=name, has_image=image_path is not None, mode="fast_path")

        suggested_classes = []
        if (not target_classes or len(target_classes) == 0) and description:
            suggestions = self.suggest_classes(description)
            target_classes = [s['class_number'] for s in suggestions]
            suggested_classes = suggestions
            logger.info("Auto-mapped classes", classes=target_classes, suggestion_count=len(suggestions))

        # Vector encoding
        vec_start = time.perf_counter()
        q_text_vec, q_img_vec, q_dino_vec, q_color_vec = self.get_query_vectors(name, image_path)
        vec_duration = (time.perf_counter() - vec_start) * 1000
        logger.debug("Query vectors generated", duration_ms=round(vec_duration, 2), has_image_vec=q_img_vec is not None)

        # Pre-screening
        screen_start = time.perf_counter()
        raw_candidates = self.pre_screen_candidates(name, target_classes, limit=30)
        screen_duration = (time.perf_counter() - screen_start) * 1000
        logger.debug("Pre-screening complete", candidates=len(raw_candidates), duration_ms=round(screen_duration, 2))

        # Hybrid risk calculation
        risk_start = time.perf_counter()
        final_results = self.calculate_hybrid_risk(
            raw_candidates, name, q_text_vec, q_img_vec, q_dino_vec, q_color_vec
        )
        risk_duration = (time.perf_counter() - risk_start) * 1000
        logger.debug("Hybrid risk calculated", results=len(final_results), duration_ms=round(risk_duration, 2))

        top_score = final_results[0]['scores']['total'] if final_results else 0.0
        needs_live = top_score < 0.75

        total_duration = (time.perf_counter() - query_start) * 1000
        logger.info(
            "Brand risk assessment complete",
            trademark_name=name,
            risk_score=round(top_score, 4),
            candidates=len(final_results),
            needs_live_investigation=needs_live,
            duration_ms=round(total_duration, 2)
        )

        result = {
            "query": {"name": name, "classes": target_classes, "has_logo": image_path is not None},
            "auto_suggested_classes": suggested_classes,
            "final_risk_score": top_score,
            "top_candidates": final_results[:100],
            "source": "local_db"
        }

        return result, needs_live

    def run_live_investigation(self, name, target_classes=None, progress_callback=None):
        """
        Run live investigation: Scrape → AI Enrich → Ingest → Recalculate.
        progress_callback(percent, message) is called to report progress.
        Returns updated result dict.
        """
        investigation_start = time.perf_counter()

        def report(pct, msg):
            logger.info("Live investigation progress", percent=pct, message=msg, trademark_name=name)
            if progress_callback:
                progress_callback(pct, msg)

        report(10, "Starting live investigation")

        try:
            report(15, "Launching scraper")
            bot = scrapper.TurkPatentScraper(headless=True)
            scraped_data = bot.search_and_ingest(name)

            if scraped_data and bot.active_data_dir:
                active_dir = bot.active_data_dir
                meta_file = bot.active_metadata_file
                bot.close()

                report(40, "Generating AI embeddings")
                ai.process_folder(active_dir)

                report(70, "Ingesting to database")
                ingest.process_file_batch(self.conn, meta_file, force=True)

                report(90, "Recalculating risk scores")
                logger.info("Live scraping complete", trademark_name=name, data_dir=str(active_dir))
            else:
                logger.info("No new data found from scraper", trademark_name=name)
                bot.close()
                report(90, "No new data found, finalizing")

        except Exception as e:
            logger.error("Live investigation failed", trademark_name=name, error=str(e))
            self.conn.rollback()
            raise

        # Recalculate with new data (text-only, no image)
        q_text_vec, _, _, _ = self.get_query_vectors(name, None)
        raw_candidates = self.pre_screen_candidates(name, target_classes, limit=30)
        final_results = self.calculate_hybrid_risk(raw_candidates, name, q_text_vec, None)

        report(100, "Complete")

        investigation_duration = (time.perf_counter() - investigation_start) * 1000
        top_score = final_results[0]['scores']['total'] if final_results else 0.0

        logger.info(
            "Live investigation complete",
            name=name,
            risk_score=round(top_score, 4),
            candidates=len(final_results),
            duration_ms=round(investigation_duration, 2)
        )

        return {
            "query": {"name": name, "classes": target_classes, "has_logo": False},
            "auto_suggested_classes": [],
            "final_risk_score": top_score,
            "top_candidates": final_results[:100],
            "source": "live_investigation"
        }

    def assess_brand_risk_full(self, name, image_path=None, target_classes=None, description=None):
        """
        Full synchronous assessment (legacy behavior) - includes live investigation if needed.
        """
        result, needs_live = self.assess_brand_risk(name, image_path, target_classes, description)

        if needs_live:
            logging.info("   ⚠️ Risk < 75%. Triggering Live Investigation...")
            result = self.run_live_investigation(name, target_classes)

        return result

if __name__ == "__main__":
    engine = RiskEngine()
    report = engine.assess_brand_risk_full("Nike", target_classes=[25])
    print(f"\n🏆 Risk: {report['final_risk_score'] * 100:.2f}%")
    for m in report['top_candidates']:
        print(f" - {m['name']} ({m['status']}): {m['scores']['total']*100:.1f}%")
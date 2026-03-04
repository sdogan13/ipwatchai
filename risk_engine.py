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
# CrossEncoder removed — was unused, wasted ~120MB VRAM
from dotenv import load_dotenv

# Import Pipeline Components
import scrapper
import ingest
import ai  # Optimization: Reuse models loaded here
from utils.idf_scoring import (
    normalize_turkish,
    turkish_lower,
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
# Graduated phonetic scoring (replaces binary dMetaphone match)
from utils.phonetic import calculate_phonetic_similarity
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
            normalize_turkish(ocr_text_a),
            normalize_turkish(ocr_text_b),
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
    Categorizes trademark status into risk levels and returns user guidance messages.
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
# normalize_turkish and turkish_lower imported from utils.idf_scoring (canonical)


def check_substring_containment(query: str, target: str) -> float:
    """
    Check if query is contained in target or vice versa.
    Returns 1.0 if containment found, 0.0 otherwise.

    Examples:
        "nike" in "nike sports" → 1.0
        "dogan" in "d.p doğan patent" → 1.0 (after normalization)
    """
    q = normalize_turkish(query)
    t = normalize_turkish(target)

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
    q_norm = normalize_turkish(query)
    t_norm = normalize_turkish(target)

    q_tokens = set(q_norm.split())
    t_tokens = set(t_norm.split())

    if not q_tokens:
        return 0.0

    matches = q_tokens.intersection(t_tokens)
    return len(matches) / len(q_tokens)


def calculate_name_similarity(query: str, target: str) -> float:
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

    # Normalize Turkish characters (turkish_lower + ASCII fold)
    q_norm = normalize_turkish(query)
    t_norm = normalize_turkish(target)

    if not q_norm or not t_norm:
        return 0.0

    if q_norm == t_norm:
        return 1.0

    seq_ratio = SequenceMatcher(None, q_norm, t_norm).ratio()

    token_overlap = calculate_token_overlap(query, target)

    return min(max(seq_ratio, token_overlap), 0.99)


# Backward-compat alias
calculate_turkish_similarity = calculate_name_similarity


def _dynamic_combine(
    text_idf_score: float,
    visual_sim: float,
    translation_sim: float,
) -> dict:
    """
    Combine all scoring signals with dynamic confidence-based weighting.
    Signals with higher scores receive exponentially boosted weights. 
    Phonetic similarity is naturally incorporated within `text_idf_score`.

    Args:
        text_idf_score: IDF-weighted text score
        visual_sim: Combined visual similarity
        translation_sim: Cross-language similarity

    Returns:
        dict: combined `total` score and applied `dynamic_weights`
    """
    BASE_WEIGHTS = {
        "text":        0.60,
        "visual":      0.25,
        "translation": 0.15,
    }

    STEEPNESS = 4.0

    signals = {
        "text":        text_idf_score,
        "visual":      visual_sim,
        "translation": translation_sim,
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

    if visual_sim >= 0.85:
        total = max(total, visual_sim)

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
        candidate_translations: Optional dict {name_tr}

    Returns:
        dict: Score breakdown with dynamic combined total.
              Key fields: total, text_idf_score, text_similarity, semantic_similarity,
              visual_similarity, phonetic_similarity, translation_similarity,
              dynamic_weights, exact_match, matched_words, etc.
    """
    # Image-only search: skip text/IDF scoring, use visual directly
    if not query_name or not query_name.strip():
        breakdown = {
            "exact_match": False,
            "containment": 0.0,
            "token_overlap": 0.0,
            "weighted_overlap": 0.0,
            "distinctive_match": 0.0,
            "text_similarity": 0.0,
            "semantic_similarity": 0.0,
            "phonetic_similarity": 0.0,
            "visual_similarity": round(visual_sim, 4),
            "translation_similarity": 0.0,
            "matched_words": [],
            "scoring_path": "IMAGE_ONLY",
            "text_idf_score": 0.0,
            "total": round(visual_sim, 4),
            "dynamic_weights": {"text": 0.0, "visual": 1.0, "translation": 0.0},
        }
        return breakdown

    lex_turkish = calculate_name_similarity(query_name, candidate_name)
    text_sim = max(text_sim, lex_turkish)

    from utils.translation import detect_language_fasttext
    query_lang, _, _ = detect_language_fasttext(query_name)
    if query_lang == 'tr':
        trans_sim = 0.0
    else:
        candidate_name_tr = (candidate_translations or {}).get('name_tr') or ''
        trans_res = calculate_translation_similarity(
            query_name, candidate_name, candidate_name_tr=candidate_name_tr
        )
        trans_sim = trans_res if isinstance(trans_res, float) else trans_res.get("translation_similarity", 0.0)

    _idf_total, breakdown = compute_idf_weighted_score(
        query=query_name,
        target=candidate_name,
        text_sim=text_sim,
        semantic_sim=semantic_sim,
        phonetic_sim=phonetic_sim,
        visual_sim=visual_sim
    )

    # No post-IDF adjustments needed: HierarchicalTextScorer natively handles missing tokens.

    breakdown['translation_similarity'] = trans_sim

    #    Phonetic is already inside _idf_total via compute_idf_weighted_score()
    combined = _dynamic_combine(
        text_idf_score=_idf_total,
        visual_sim=visual_sim,
        translation_sim=trans_sim,
    )

    if visual_sim > 0:
        text_only = _dynamic_combine(
            text_idf_score=_idf_total,
            visual_sim=0.0,
            translation_sim=trans_sim,
        )
        if text_only['total'] > combined['total']:
            combined['total'] = text_only['total']

    breakdown['total'] = combined['total']
    breakdown['text_idf_score'] = _idf_total
    breakdown['dynamic_weights'] = combined['dynamic_weights']

    logger.info(f"LIVE_API_SCORE: {query_name} vs {candidate_name} | text={text_sim}, semantic={semantic_sim} | idf_total={_idf_total} | score={breakdown['total']}")
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
        """Extract all visual vectors + OCR text from a PIL image.

        Returns:
            tuple: (clip_vec, dino_vec, color_vec, ocr_text)
        """
        try:
            cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            hsv_img = cv2.cvtColor(cv_img, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv_img], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256])
            cv2.normalize(hist, hist)
            color_vec = hist.flatten().tolist()
        except Exception:
            color_vec = None

        clip_input = self.clip_preprocess(pil_img).unsqueeze(0).to(self.device)
        if next(self.clip_model.parameters()).dtype == torch.float16:
            clip_input = clip_input.half()
        with torch.no_grad():
            clip_feat = self.clip_model.encode_image(clip_input)
            clip_feat /= clip_feat.norm(dim=-1, keepdim=True)
            clip_vec = clip_feat.squeeze().tolist()
        del clip_input, clip_feat

        dino_input = self.dino_preprocess(pil_img).unsqueeze(0).to(self.device)
        if next(self.dino_model.parameters()).dtype == torch.float16:
            dino_input = dino_input.half()
        with torch.no_grad():
            dino_vec = self.dino_model(dino_input).flatten().tolist()
        del dino_input

        # Extract OCR text from the image
        ocr_text = ""
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                pil_img.save(f, format='PNG')
                tmp_path = f.name
            from utils.idf_scoring import extract_ocr_text
            ocr_text = extract_ocr_text(tmp_path) or ""
            os.unlink(tmp_path)
        except Exception:
            ocr_text = ""

        return clip_vec, dino_vec, color_vec, ocr_text

    def get_query_vectors(self, name, image_path=None):
        """Encode query name and optional image into vectors.

        Returns:
            tuple: (text_vec, img_vec, dino_vec, color_vec, ocr_text)
        """
        text_vec = self.text_model.encode(name).tolist()
        img_vec, dino_vec, color_vec, ocr_text = None, None, None, ""

        if image_path and os.path.exists(image_path):
            try:
                pil_img = Image.open(image_path).convert('RGB')
                img_vec, dino_vec, color_vec, ocr_text = self._encode_single_image(pil_img)
            except Exception as e:
                logger.error("Image process failed", error=str(e))

        return text_vec, img_vec, dino_vec, color_vec, ocr_text

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

    def pre_screen_candidates(self, name_input, target_classes=None, limit=500, status_filter=None, attorney_no=None, q_img_vec=None, q_dino_vec=None, q_ocr_text=None):
        cur = self.conn.cursor()

        # Normalize query for Turkish character matching
        name_normalized = normalize_turkish(name_input)
        seen_ids = set()
        all_candidates = []

        # ── Model-based translation for cross-language candidate discovery ──
        translated_name = None
        translated_normalized = None
        detected_lang = 'unknown'
        try:
            from utils.translation import auto_translate_to_turkish
            tr_result, detected_lang = auto_translate_to_turkish(name_input)
            if tr_result and normalize_turkish(tr_result) != name_normalized:
                translated_name = tr_result
                translated_normalized = normalize_turkish(tr_result)
                logger.debug("Pre-screen translation",
                             query=name_input, detected_lang=detected_lang,
                             translated=translated_name)
        except Exception as e:
            logger.warning("Pre-screen translation failed, continuing without", error=str(e))

        # First, check for EXACT matches (highest priority)
        exact_sql = """
            SELECT id, application_no, name, nice_class_numbers, image_path,
                   1.0 as lexical_score
            FROM trademarks
            WHERE (LOWER(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')
            ) = %s
            OR LOWER(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name_tr,
                'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')
            ) = %s)
        """
        exact_params = [name_normalized, name_normalized]

        exact_sql += " AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)"

        if status_filter:
            exact_sql += " AND current_status = %s"
            exact_params.append(status_filter)

        if attorney_no:
            exact_sql += " AND attorney_no = %s"
            exact_params.append(attorney_no)

        if target_classes and len(target_classes) > 0:
            exact_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
            exact_params.append(target_classes)

        exact_sql += " LIMIT 20;"
        cur.execute(exact_sql, exact_params)
        exact_matches = cur.fetchall()

        for match in exact_matches:
            if match[0] not in seen_ids:
                seen_ids.add(match[0])
                all_candidates.append(match)

        if name_normalized != turkish_lower(name_input):
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
                ) = %s
            """
            norm_params = [name_normalized]

            # 11-year renewal window
            normalized_sql += " AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)"

            if status_filter:
                normalized_sql += " AND current_status = %s"
                norm_params.append(status_filter)

            if attorney_no:
                normalized_sql += " AND attorney_no = %s"
                norm_params.append(attorney_no)

            if target_classes and len(target_classes) > 0:
                normalized_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                norm_params.append(target_classes)

            normalized_sql += " LIMIT 20;"
            cur.execute(normalized_sql, norm_params)
            norm_matches = cur.fetchall()

            for match in norm_matches:
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match)

        if translated_normalized:
            tr_exact_sql = """
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       0.9 as lexical_score
                FROM trademarks
                WHERE (LOWER(
                    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')
                ) = %s
                OR LOWER(
                    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name_tr,
                    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')
                ) = %s)
            """
            tr_exact_params = [translated_normalized, translated_normalized]

            # 11-year renewal window
            tr_exact_sql += " AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)"

            if status_filter:
                tr_exact_sql += " AND current_status = %s"
                tr_exact_params.append(status_filter)

            if attorney_no:
                tr_exact_sql += " AND attorney_no = %s"
                tr_exact_params.append(attorney_no)

            if target_classes and len(target_classes) > 0:
                tr_exact_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                tr_exact_params.append(target_classes)

            tr_exact_sql += " LIMIT 20;"
            cur.execute(tr_exact_sql, tr_exact_params)
            tr_exact_matches = cur.fetchall()

            for match in tr_exact_matches:
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match)

        from idf_scoring import tokenize as _tok
        from idf_lookup import IDFLookup as _IDF
        _q_tokens = _tok(name_input)
        if translated_name:
            _q_tokens = _q_tokens | _tok(translated_name)
        _distinctive_tokens = [w for w in _q_tokens if _IDF.get_word_class(w) == 'distinctive']

        # Fallback: if the query consists entirely of semi-generic or generic words,
        # use the semi-generic words for the keyword containment search so we don't
        # rely 100% on the vector search (which might miss exact substring matches).
        if not _distinctive_tokens:
            _distinctive_tokens = [w for w in _q_tokens if _IDF.get_word_class(w) == 'semi_generic']
            
        # Add back high value semi-generics to the distinctive list for the AND search
        # so queries like 'dogan patent' don't get flooded by 'dogan' alone
        elif len(_distinctive_tokens) == 1 and len([w for w in _q_tokens if _IDF.get_word_class(w) == 'semi_generic']) > 0:
            _distinctive_tokens.extend([w for w in _q_tokens if _IDF.get_word_class(w) == 'semi_generic'])

        if _distinctive_tokens and len(_distinctive_tokens) == 1:
            # Single distinctive word — containment search
            dtok = _distinctive_tokens[0]
            escaped = dtok.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            contain_sql = """
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       0.85 as lexical_score
                FROM trademarks
                WHERE LOWER(
                    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')
                ) LIKE %s ESCAPE '\\'
                  AND current_status NOT IN ('Refused', 'Withdrawn')
                  AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)
            """
            contain_params = [f'%{escaped}%']

            if status_filter:
                contain_sql += " AND current_status = %s"
                contain_params.append(status_filter)

            if attorney_no:
                contain_sql += " AND attorney_no = %s"
                contain_params.append(attorney_no)

            if target_classes and len(target_classes) > 0:
                contain_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                contain_params.append(target_classes)

            if translated_normalized and _distinctive_tokens:
                tr_dtok = normalize_turkish(translated_name) if translated_name else None
                if tr_dtok and tr_dtok != escaped:
                    tr_escaped = tr_dtok.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                    contain_sql += """
                        OR LOWER(
                            REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                            REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                            'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                            'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')
                        ) LIKE %s ESCAPE '\\'
                    """
                    contain_params.append(f'%{tr_escaped}%')

            contain_sql += " ORDER BY length(name) ASC LIMIT 30;"
            cur.execute(contain_sql, contain_params)
            contain_matches = cur.fetchall()

            for match in contain_matches:
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match)

        # Try to gather tokens for the AND search based STRICTLY on the original input. 
        # Including translation tokens (like "patenti" for "patent") breaks AND searches 
        # because candidates won't contain both the English and Turkish suffix at the same time.
        _base_tokens = list(_tok(name_input))
        
        # We want to AND both distinctive and high-value semi-generic words
        # so queries like "dogan patent" mandate both words, preventing floods of "dogan" alone.
        _and_search_tokens = [w for w in _base_tokens if _IDF.get_word_class(w) in ['distinctive', 'semi_generic']]
        
        if _and_search_tokens and len(_base_tokens) > 1:
            # 1) Search for candidates that contain ALL important tokens (AND logic)
            # This is critical for finding 'd.p. doğan patent' when searching 'dogan patent'.
            # An OR logic query gets flooded by short single-word matches.
            and_clauses = []
            and_params = []
            for dtok in _and_search_tokens:
                escaped = dtok.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                and_clauses.append("""
                    LOWER(
                        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                        'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                        'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')
                    ) LIKE %s ESCAPE '\\'
                """)
                and_params.append(f'%{escaped}%')

            and_sql = """
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       0.85 as lexical_score
                FROM trademarks
                WHERE (""" + " AND ".join(and_clauses) + """)
                  AND current_status NOT IN ('Refused', 'Withdrawn')
                  AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)
            """
            tok_and_params = list(and_params)

            if status_filter:
                and_sql += " AND current_status = %s"
                tok_and_params.append(status_filter)

            if attorney_no:
                and_sql += " AND attorney_no = %s"
                tok_and_params.append(attorney_no)

            if target_classes and len(target_classes) > 0:
                and_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                tok_and_params.append(target_classes)

            and_sql += " ORDER BY length(name) ASC LIMIT 50;"
            cur.execute(and_sql, tok_and_params)
            and_matches = cur.fetchall()

            for match in and_matches:
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match)

            # 2) Fallback to OR logic for partial matches
            like_clauses = []
            token_params = []
            for dtok in _distinctive_tokens:
                escaped = dtok.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                like_clauses.append("""
                    LOWER(
                        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                        'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                        'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')
                    ) LIKE %s ESCAPE '\\'
                """)
                token_params.append(f'%{escaped}%')

            token_sql = """
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       0.8 as lexical_score
                FROM trademarks
                WHERE (""" + " OR ".join(like_clauses) + """)
                  AND current_status NOT IN ('Refused', 'Withdrawn')
                  AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)
            """
            tok_params = list(token_params)

            if status_filter:
                token_sql += " AND current_status = %s"
                tok_params.append(status_filter)

            if attorney_no:
                token_sql += " AND attorney_no = %s"
                tok_params.append(attorney_no)

            if target_classes and len(target_classes) > 0:
                token_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                tok_params.append(target_classes)

            token_sql += " ORDER BY length(name) ASC LIMIT 20;"
            cur.execute(token_sql, tok_params)
            token_matches = cur.fetchall()

            for match in token_matches:
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match)

        try:
            q_text_vec = self.text_model.encode(name_input).tolist()
            vec_sql = """
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       (1 - (text_embedding <=> %s::halfvec)) as lexical_score
                FROM trademarks
                WHERE text_embedding IS NOT NULL
                  AND current_status NOT IN ('Refused', 'Withdrawn')
                  AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)
            """
            vec_params = [str(q_text_vec)]

            if status_filter:
                vec_sql += " AND current_status = %s"
                vec_params.append(status_filter)

            if attorney_no:
                vec_sql += " AND attorney_no = %s"
                vec_params.append(attorney_no)

            if target_classes and len(target_classes) > 0:
                vec_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                vec_params.append(target_classes)

            vec_sql += " ORDER BY text_embedding <=> %s::halfvec LIMIT 50;"
            vec_params.append(str(q_text_vec))

            cur.execute(vec_sql, vec_params)
            vec_matches = cur.fetchall()

            for match in vec_matches:
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match)
        except Exception as e:
            logger.warning("Text vector search stage failed, continuing without", error=str(e))

        if q_img_vec:
            try:
                img_sql = """
                    SELECT id, application_no, name, nice_class_numbers, image_path,
                           (1 - (image_embedding <=> %s::halfvec)) as lexical_score
                    FROM trademarks
                    WHERE image_embedding IS NOT NULL
                      AND current_status NOT IN ('Refused', 'Withdrawn')
                      AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)
                """
                img_p = [str(q_img_vec)]

                if status_filter:
                    img_sql += " AND current_status = %s"
                    img_p.append(status_filter)

                if attorney_no:
                    img_sql += " AND attorney_no = %s"
                    img_p.append(attorney_no)

                if target_classes and len(target_classes) > 0:
                    img_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                    img_p.append(target_classes)

                img_sql += " ORDER BY image_embedding <=> %s::halfvec LIMIT 50;"
                img_p.append(str(q_img_vec))

                cur.execute(img_sql, img_p)
                img_matches = cur.fetchall()

                for match in img_matches:
                    if match[0] not in seen_ids:
                        seen_ids.add(match[0])
                        all_candidates.append(match)
            except Exception as e:
                logger.warning("Image vector search stage failed, continuing without", error=str(e))

        ocr_queries = set()
        if name_input and name_input.strip():
            ocr_queries.add(normalize_turkish(name_input.strip()))
        if translated_name:
            ocr_queries.add(normalize_turkish(translated_name.strip()))
        if q_ocr_text and q_ocr_text.strip():
            ocr_queries.add(normalize_turkish(q_ocr_text.strip()))

        for ocr_q in ocr_queries:
            if len(ocr_q) < 2:
                continue
            try:
                escaped_ocr = ocr_q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                ocr_sql = """
                    SELECT id, application_no, name, nice_class_numbers, image_path,
                           0.75 as lexical_score
                    FROM trademarks
                    WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
                      AND LOWER(
                          REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                          REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(logo_ocr_text,
                          'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                          'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')
                      ) LIKE %s ESCAPE '\\'
                      AND current_status NOT IN ('Refused', 'Withdrawn')
                      AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)
                """
                ocr_params = [f'%{escaped_ocr}%']

                if status_filter:
                    ocr_sql += " AND current_status = %s"
                    ocr_params.append(status_filter)

                if attorney_no:
                    ocr_sql += " AND attorney_no = %s"
                    ocr_params.append(attorney_no)

                if target_classes and len(target_classes) > 0:
                    ocr_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                    ocr_params.append(target_classes)

                ocr_sql += " ORDER BY length(name) ASC LIMIT 20;"
                cur.execute(ocr_sql, ocr_params)
                ocr_matches = cur.fetchall()

                for match in ocr_matches:
                    if match[0] not in seen_ids:
                        seen_ids.add(match[0])
                        all_candidates.append(match)
            except Exception as e:
                logger.warning("OCR text search failed for query", ocr_query=ocr_q, error=str(e))

        # ── Stage 4.6: Phonetic pre-screening (dmetaphone) ──
        if name_input and name_input.strip() and len(name_normalized) >= 2:
            try:
                qlen = len(name_input.strip())
                phon_sql = """
                    SELECT id, application_no, name, nice_class_numbers, image_path,
                           0.70 as lexical_score
                    FROM trademarks
                    WHERE name IS NOT NULL
                      AND length(name) BETWEEN GREATEST(2, %s - 2) AND %s + 4
                      AND dmetaphone(
                          LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                          REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                          'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                          'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))
                      ) = dmetaphone(%s)
                      AND current_status NOT IN ('Refused', 'Withdrawn')
                      AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)
                """
                phon_params = [qlen, qlen, name_normalized]

                if status_filter:
                    phon_sql += " AND current_status = %s"
                    phon_params.append(status_filter)

                if attorney_no:
                    phon_sql += " AND attorney_no = %s"
                    phon_params.append(attorney_no)

                if target_classes and len(target_classes) > 0:
                    phon_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                    phon_params.append(target_classes)

                phon_sql += " ORDER BY levenshtein(LOWER(name), LOWER(%s)) ASC, length(name) ASC LIMIT 100;"
                phon_params.append(name_input)
                cur.execute(phon_sql, phon_params)
                phon_matches = cur.fetchall()

                # DEBUG: log phonetic matches
                logger.info(f"PHON_DEBUG: query={name_input!r} normalized={name_normalized!r} "
                           f"total_matches={len(phon_matches)} already_seen={len(seen_ids)}")
                for pm in phon_matches[:10]:
                    pm_name = pm[2] if len(pm) > 2 else '?'
                    pm_in_seen = pm[0] in seen_ids
                    logger.info(f"  PHON_MATCH: name={pm_name!r} already_seen={pm_in_seen}")

                phon_added = 0
                for match in phon_matches:
                    if match[0] not in seen_ids:
                        seen_ids.add(match[0])
                        all_candidates.append(match)
                        phon_added += 1

                if phon_added > 0:
                    logger.info(f"PHON_ADDED: {phon_added} new candidates from phonetic pre-screen")
                else:
                    logger.info("PHON_ADDED: 0 new candidates (all already seen)")
            except Exception as e:
                logger.warning("Phonetic pre-screen failed, continuing without", error=str(e))

        remaining_limit = limit - len(all_candidates)
        if remaining_limit > 0:
            tr_sim_cols = ""
            tr_order_cols = ""
            tr_select_params = []
            tr_order_params = []
            if translated_name:
                tr_sim_cols = """,
                    COALESCE(similarity(name, %s), 0),
                    COALESCE(similarity(name_tr, %s), 0)"""
                tr_order_cols = """,
                    COALESCE(similarity(name, %s), 0),
                    COALESCE(similarity(name_tr, %s), 0)"""
                tr_select_params = [translated_name, translated_name]
                tr_order_params = [translated_name, translated_name]

            sql = f"""
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
                           COALESCE(similarity(name_tr, %s), 0)
                           {tr_sim_cols}
                       ) as lexical_score
                FROM trademarks
                WHERE current_status NOT IN ('Refused', 'Withdrawn')
                  AND LOWER(name) != LOWER(%s)
                  AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)
                  AND GREATEST(
                      similarity(name, %s),
                      similarity(
                          LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                          REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                          'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                          'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')),
                          %s
                      ),
                      COALESCE(similarity(name_tr, %s), 0)
                  ) >= 0.3
            """
            params = [name_input, name_normalized, name_input] + tr_select_params + [name_input, name_input, name_normalized, name_input]

            if status_filter:
                sql += " AND current_status = %s"
                params.append(status_filter)

            if attorney_no:
                sql += " AND attorney_no = %s"
                params.append(attorney_no)

            if target_classes and len(target_classes) > 0:
                sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                params.append(target_classes)

            sql += f"""
                ORDER BY GREATEST(
                    similarity(name, %s),
                    similarity(
                        LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                        'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                        'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')),
                        %s
                    ),
                    COALESCE(similarity(name_tr, %s), 0)
                    {tr_order_cols}
                ) DESC LIMIT %s;
            """
            params.extend([name_input, name_normalized, name_input] + tr_order_params + [remaining_limit])

            cur.execute(sql, params)
            similar_matches = cur.fetchall()

            for match in similar_matches:
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match)

        return all_candidates

    def pre_screen_by_image(self, q_img_vec, q_dino_vec=None, target_classes=None, limit=20, status_filter=None):
        """Pre-screen candidates by visual similarity when text query is empty."""
        cur = self.conn.cursor()
        all_candidates = []

        # Use CLIP embedding as primary, DINOv2 as secondary
        visual_cols = []
        params = []
        if q_img_vec:
            visual_cols.append("(1 - (image_embedding <=> %s::halfvec))")
            params.append(str(q_img_vec))
        if q_dino_vec:
            visual_cols.append("(1 - (dinov2_embedding <=> %s::halfvec))")
            params.append(str(q_dino_vec))

        if not visual_cols:
            return []

        # Combine visual scores
        if len(visual_cols) == 2:
            score_expr = f"GREATEST({visual_cols[0]}, {visual_cols[1]})"
        else:
            score_expr = visual_cols[0]

        sql = f"""
            SELECT id, application_no, name, nice_class_numbers, image_path,
                   {score_expr} as visual_score
            FROM trademarks
            WHERE image_embedding IS NOT NULL
              AND current_status NOT IN ('Refused', 'Withdrawn')
              AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)
        """

        if status_filter:
            sql += " AND current_status = %s"
            params.append(status_filter)

        if target_classes and len(target_classes) > 0:
            sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
            params.append(target_classes)

        order_params = []
        if q_img_vec:
            order_params.append(str(q_img_vec))
        if q_dino_vec:
            order_params.append(str(q_dino_vec))
        params.extend(order_params)

        sql += f" ORDER BY {score_expr} DESC LIMIT %s;"
        params.append(limit)

        cur.execute(sql, params)
        all_candidates = cur.fetchall()
        return all_candidates

    def calculate_hybrid_risk(self, candidates, name_input, query_text_vec,
                                 query_img_vec, query_dino_vec=None, query_color_vec=None,
                                 query_ocr_text=""):
        if not candidates: return []

        candidate_ids = [str(c[0]) for c in candidates]

        clip_col = f"(1 - (t.image_embedding <=> %s::halfvec))" if query_img_vec else "0.0"
        dino_col = f"(1 - (t.dinov2_embedding <=> %s::halfvec))" if query_dino_vec else "0.0"
        color_col = f"(1 - (t.color_histogram <=> %s::halfvec))" if query_color_vec else "0.0"

        sql = f"""
            SELECT
                t.application_no, t.name, t.current_status, t.nice_class_numbers, t.image_path,
                t.name_tr, t.application_date, t.expiry_date,
                t.holder_name, t.holder_tpe_client_id,
                t.attorney_name, t.attorney_no, t.registration_no,
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
                    AND t.extracted_goods != 'null'::jsonb) AS has_extracted_goods,
                t.extracted_goods
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
            candidate_app_date = r[6]
            candidate_expiry_date = r[7]
            candidate_holder_name = r[8]
            candidate_holder_tpe_id = r[9]
            candidate_attorney_name = r[10]
            candidate_attorney_no = r[11]
            candidate_registration_no = r[12]
            sem = float(r[13]) if r[13] is not None else 0.0
            lex_postgres = float(r[14]) if r[14] is not None else 0.0
            clip_sim = float(r[15]) if r[15] is not None else 0.0
            dino_sim = float(r[16]) if r[16] is not None else 0.0
            color_sim = float(r[17]) if r[17] is not None else 0.0
            candidate_ocr = (r[18] or "").strip()
            phon_match = bool(r[20]) if len(r) > 20 and r[20] is not None else False
            has_eg = bool(r[21]) if len(r) > 21 and r[21] is not None else False
            raw_extracted_goods = r[22] if len(r) > 22 else None

            vis = calculate_visual_similarity(
                clip_sim=clip_sim,
                dinov2_sim=dino_sim,
                color_sim=color_sim,
                ocr_text_a=query_ocr_text,
                ocr_text_b=candidate_ocr,
            )

            # Centralized scoring via score_pair()
            score_breakdown = score_pair(
                query_name=name_input,
                candidate_name=candidate_name,
                text_sim=lex_postgres,
                semantic_sim=sem,
                visual_sim=vis,
                phonetic_sim=calculate_phonetic_similarity(name_input, candidate_name),
                candidate_translations={
                    'name_tr': candidate_name_tr,
                }
            )

            results.append({
                "application_no": r[0],
                "name": candidate_name,
                "name_tr": candidate_name_tr or None,
                "status": r[2],
                "classes": r[3],
                "image_path": r[4],
                "application_date": str(candidate_app_date) if candidate_app_date else None,
                "expiry_date": str(candidate_expiry_date) if candidate_expiry_date else None,
                "holder_name": candidate_holder_name,
                "holder_tpe_client_id": candidate_holder_tpe_id,
                "attorney_name": candidate_attorney_name,
                "attorney_no": candidate_attorney_no,
                "registration_no": candidate_registration_no,
                "bulletin_no": r[19] if len(r) > 19 else None,
                "exact_match": score_breakdown.get("exact_match", False),
                "scores": score_breakdown,
                "has_extracted_goods": has_eg,
                "extracted_goods": raw_extracted_goods if has_eg else None
            })

        # Sort: exact matches first, then by total score
        results.sort(key=lambda x: (x.get('exact_match', False), x['scores']['total']), reverse=True)
        return results

    @log_timing("assess_brand_risk")
    def assess_brand_risk(self, name, image_path=None, target_classes=None, description=None, status_filter=None, attorney_no=None):
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
        q_text_vec, q_img_vec, q_dino_vec, q_color_vec, q_ocr_text = self.get_query_vectors(name, image_path)
        vec_duration = (time.perf_counter() - vec_start) * 1000
        logger.debug("Query vectors generated", duration_ms=round(vec_duration, 2), has_image_vec=q_img_vec is not None, has_ocr=bool(q_ocr_text))

        screen_start = time.perf_counter()
        logger.debug("Pre-screen decision", name_empty=not name.strip(), has_img_vec=q_img_vec is not None, has_dino_vec=q_dino_vec is not None, has_ocr=bool(q_ocr_text))
        if not name.strip() and (q_img_vec or q_dino_vec):
            ocr_name = q_ocr_text.strip() if q_ocr_text else ""
            raw_candidates = self.pre_screen_candidates(ocr_name, target_classes, limit=500, status_filter=status_filter, attorney_no=attorney_no, q_img_vec=q_img_vec, q_dino_vec=q_dino_vec, q_ocr_text=q_ocr_text)
            logger.info("Pre-screening by IMAGE+OCR", candidates=len(raw_candidates), ocr_text=ocr_name[:50])
        else:
            raw_candidates = self.pre_screen_candidates(name, target_classes, limit=500, status_filter=status_filter, attorney_no=attorney_no, q_img_vec=q_img_vec, q_dino_vec=q_dino_vec, q_ocr_text=q_ocr_text)
        screen_duration = (time.perf_counter() - screen_start) * 1000
        logger.debug("Pre-screening complete", candidates=len(raw_candidates), duration_ms=round(screen_duration, 2))

        # Hybrid risk calculation
        risk_start = time.perf_counter()
        final_results = self.calculate_hybrid_risk(
            raw_candidates, name, q_text_vec, q_img_vec, q_dino_vec, q_color_vec,
            query_ocr_text=q_ocr_text
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

    def run_live_investigation(self, name, target_classes=None, progress_callback=None, status_filter=None, attorney_no=None):
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
            from agentic_search import _scrape_lock

            report(15, "Waiting for scrape queue...")
            with _scrape_lock:
                report(18, "Launching scraper")
                bot = scrapper.TurkPatentScraper(headless=True)
                scraped_data = bot.search_and_ingest(name)

                # Brief cooldown between scrapes
                time.sleep(2)

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
        q_text_vec, _, _, _, _ = self.get_query_vectors(name, None)
        raw_candidates = self.pre_screen_candidates(name, target_classes, limit=500, status_filter=status_filter, attorney_no=attorney_no)
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
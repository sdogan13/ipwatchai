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

def turkish_lower(text: str) -> str:
    """
    Turkish-aware lowercase. Python's str.lower() follows Unicode rules where
    'I'.lower() == 'i', but in Turkish 'I' should become 'ı' and 'İ' should
    become 'i'. This function handles the Turkish I/İ distinction correctly.

    Turkish rules:
        İ → i    (dotted capital → dotted small)
        I → ı    (undotted capital → undotted small)
        All other characters use standard Unicode lowercasing.
    """
    if not text:
        return ""
    # Must replace İ before I, otherwise İ's dot gets mangled
    text = text.replace('İ', 'i').replace('I', 'ı')
    return text.lower()


def normalize_turkish(text: str) -> str:
    """
    Normalize Turkish characters to ASCII equivalents for comparison.
    This ensures 'doğan' matches 'dogan', 'şeker' matches 'seker', etc.
    Uses turkish_lower() for proper I/İ handling before ASCII folding.
    """
    if not text:
        return ""
    # First apply Turkish-aware lowercasing
    text = turkish_lower(text.strip())
    # Then fold Turkish chars to ASCII
    replacements = {
        'ğ': 'g',
        'ı': 'i',
        'ö': 'o',
        'ü': 'u',
        'ş': 's',
        'ç': 'c',
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

    # How many query tokens appear in target?
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

    # 1. Exact match after normalization → only case that returns 1.0
    if q_norm == t_norm:
        return 1.0

    # 2. SequenceMatcher similarity
    seq_ratio = SequenceMatcher(None, q_norm, t_norm).ratio()

    # 3. Token overlap
    token_overlap = calculate_token_overlap(query, target)

    # Return maximum, but cap at 0.99 for non-exact matches.
    # 1.0 is reserved for exact normalized match only.
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

    Each signal has a base weight reflecting its importance. Signals with higher
    scores get exponentially boosted weight (confident = more influence). Signals
    with score=0 get weight=0 (dead signals don't dilute active ones). Weights
    are normalized to sum to 1.0.

    Phonetic similarity is NOT a separate signal here — it is already incorporated
    inside compute_idf_weighted_score() as part of `base = max(text_sim, semantic_sim,
    phonetic_sim)` in Cases B-F. Adding it here would double-count it.

    Args:
        text_idf_score: IDF-weighted text score from Cases A-F (includes phonetic)
        visual_sim: Combined CLIP+DINOv2+color+OCR visual similarity
        translation_sim: Cross-language translation similarity

    Returns:
        dict with 'total' and 'dynamic_weights'
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

    # 1. Recalculate text similarity with Turkish normalization (original name only)
    lex_turkish = calculate_name_similarity(query_name, candidate_name)
    text_sim = max(text_sim, lex_turkish)

    # 2. Translation similarity (cross-language conflict detection)
    #    Skip entirely when query is Turkish — translation scoring is meaningless
    #    for same-language comparisons (Turkish query vs Turkish DB records).
    #    The text similarity already handles Turkish↔Turkish matching.
    from utils.translation import detect_language_fasttext
    query_lang, _, _ = detect_language_fasttext(query_name)
    if query_lang == 'tr':
        trans_sim = 0.0
    else:
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

    # --- Post-IDF adjustments (shared imports) ---
    from idf_lookup import IDFLookup as _IDFLookup
    from idf_scoring import tokenize as _tokenize
    from difflib import SequenceMatcher as _SM
    _q_tokens = _tokenize(query_name) if query_name else set()
    _t_tokens = _tokenize(candidate_name) if candidate_name else set()

    # 3a. Coverage adjustment: when score is high (>=0.90) but not all
    #     query tokens are matched in the target, apply a penalty.
    #     100% is reserved for exact normalized matches only (CHECK 1).
    #     Penalty: ~11% per missing distinctive word, ~4% per missing other word.
    if _idf_total >= 0.90 and not breakdown.get('exact_match', False) and _q_tokens:
        _penalty = 0.0
        for _w in _q_tokens:
            _matched = _w in _t_tokens or any(
                _SM(None, _w, _tw).ratio() >= (0.85 if min(len(_w), len(_tw)) <= 4 else 0.80)
                for _tw in _t_tokens
            )
            if not _matched:
                _wc = _IDFLookup.get_word_class(_w)
                if _wc == 'distinctive':
                    _penalty += 0.11
                else:
                    _penalty += 0.04

        if _penalty > 0:
            _idf_total = max(0.0, _idf_total - _penalty)
            breakdown['total'] = round(_idf_total, 4)
            breakdown['scoring_path'] = breakdown.get('scoring_path', '') + ' > COVERAGE(-{:.0%})'.format(_penalty)

    # 3b. Hard ceiling: if zero distinctive query words matched, cap at 0.25
    #     This prevents semi-generic/generic-only matches from inflating scores.
    #     Skip for containment/exact paths where distinctive_weight_matched is set.
    _dist_matched = breakdown.get('distinctive_match', 0.0)
    _dist_weight_matched = breakdown.get('distinctive_weight_matched', 0.0)
    if _dist_matched == 0.0 and _dist_weight_matched == 0.0 and _idf_total > 0.25:
        _has_distinctive = any(
            _IDFLookup.get_word_class(w) == 'distinctive' for w in _q_tokens
        )
        if _has_distinctive:
            _idf_total = 0.25
            breakdown['total'] = 0.25
            breakdown['scoring_path'] = breakdown.get('scoring_path', '') + ' > CEILING (no distinctive match)'

    # 4. Store translation in breakdown
    breakdown['translation_similarity'] = trans_sim

    # 5. Dynamic confidence-weighted combination of ALL signals
    #    Phonetic is already inside _idf_total via compute_idf_weighted_score()
    combined = _dynamic_combine(
        text_idf_score=_idf_total,
        visual_sim=visual_sim,
        translation_sim=trans_sim,
    )

    # 5b. Floor: visual should never LOWER the total score.
    #     Compute text-only total (no visual) and ensure full total >= text-only total.
    #     This guarantees watchlist scores (with logo) are always >= search scores (text-only).
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
        # Match input precision to model (FP16 when models are .half())
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
        # Uses langdetect (trained model) for language detection + NLLB for
        # translation. No hardcoded character sets or word lists.
        #   - Turkish query → detected as 'tr' → skip translation → single pool
        #   - Non-Turkish query → translate to Turkish → double pool
        # Cost: ~100ms per query for non-Turkish, ~0ms for Turkish.
        translated_name = None
        translated_normalized = None
        detected_lang = 'unknown'
        try:
            from utils.translation import auto_translate_to_turkish
            tr_result, detected_lang = auto_translate_to_turkish(name_input)
            # Only use translation if it's meaningfully different from original
            if tr_result and normalize_turkish(tr_result) != name_normalized:
                translated_name = tr_result
                translated_normalized = normalize_turkish(tr_result)
                logger.debug("Pre-screen translation",
                             query=name_input, detected_lang=detected_lang,
                             translated=translated_name)
        except Exception as e:
            logger.warning("Pre-screen translation failed, continuing without", error=str(e))

        # First, check for EXACT matches (highest priority)
        # Use Python-side Turkish normalization for matching instead of SQL LOWER()
        # because PostgreSQL LOWER() is not Turkish-aware (I→i instead of I→ı)
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

        # 11-year renewal window: exclude expired marks (renewal period is 10 years)
        exact_sql += " AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)"

        if status_filter:
            exact_sql += " AND current_status = %s"
            exact_params.append(status_filter)

        if attorney_no:
            exact_sql += " AND attorney_no = %s"
            exact_params.append(attorney_no)

        if target_classes and len(target_classes) > 0:
            # Class 99 (Global Brand) covers ALL 45 classes - include in any class filter
            exact_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
            exact_params.append(target_classes)

        exact_sql += " LIMIT 20;"
        cur.execute(exact_sql, exact_params)
        exact_matches = cur.fetchall()

        for match in exact_matches:
            if match[0] not in seen_ids:
                seen_ids.add(match[0])
                all_candidates.append(match)

        # Also check for normalized exact matches (doğan == dogan)
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
                # Class 99 (Global Brand) covers ALL 45 classes - include in any class filter
                normalized_sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                norm_params.append(target_classes)

            normalized_sql += " LIMIT 20;"
            cur.execute(normalized_sql, norm_params)
            norm_matches = cur.fetchall()

            for match in norm_matches:
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match)

        # ── Stage 1c: Exact match for translated query (non-Turkish only) ──
        # "golden eagle" translated to "altın kartal" → exact match on name
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

        # ── Stage 2: Containment match for short queries ──
        # Single-word search "STAR" should find "STARLIGHT", "GOLDSTAR", "MEGASTAR".
        # Uses LIKE '%query%' on Turkish-normalized name. Only for 1-word queries
        # (multi-word queries use the token LIKE stage below instead).
        from idf_scoring import tokenize as _tok
        from idf_lookup import IDFLookup as _IDF
        _q_tokens = _tok(name_input)
        # Also include tokens from translated query (e.g., "golden","eagle" + "altin","kartal")
        if translated_name:
            _q_tokens = _q_tokens | _tok(translated_name)
        _distinctive_tokens = [w for w in _q_tokens if _IDF.get_word_class(w) == 'distinctive']

        if _distinctive_tokens and len(_tok(name_input)) == 1:
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

            # Also search translated form (e.g. "APPLE" → "elma" → find "elma bahçesi")
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

        # ── Stage 2.5: Token-level search for multi-word queries ──
        # For queries like "dogan patent", the full-string pg_trgm similarity
        # to "doğan" is too low (length mismatch). This step tokenizes the
        # query, picks distinctive words, and searches for trademarks whose
        # Turkish-normalized name CONTAINS any of those tokens.
        if _distinctive_tokens and len(_tok(name_input)) > 1:
            # Build LIKE clauses for each distinctive token (Turkish-normalized)
            # Escape LIKE special chars to prevent injection
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

            # Order by name length so shorter (more relevant) names come first.
            # "doğan" is more relevant than "hakan aydoğan h.a.-ay-med medikal..."
            token_sql += " ORDER BY length(name) ASC LIMIT 20;"
            cur.execute(token_sql, tok_params)
            token_matches = cur.fetchall()

            for match in token_matches:
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match)

        # ── Stage 3: Semantic vector search (MiniLM text_embedding via HNSW) ──
        # Catches conceptual/semantic matches that text stages miss entirely.
        # "APPLE" finds "ELMA" via embedding proximity without translation.
        # Uses pgvector HNSW index — ~50ms for millions of rows.
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

        # ── Stage 4: Image vector search (CLIP via HNSW index) ──
        # When user uploads a logo, find visually similar trademarks.
        # Uses CLIP only for ordering (has HNSW index, ~200ms).
        # DINOv2 contributes to scoring later in calculate_hybrid_risk.
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

        # ── Stage 4.5: OCR text search (logo text matching via GiST trigram) ──
        # Searches logo_ocr_text column for: (a) the user's text query, and
        # (b) OCR text extracted from an uploaded logo image.
        # Catches cases like logo with "STAR" text → finds other logos containing "STAR".
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

        # ── Stage 5: Trigram similarity (pg_trgm with 0.3 floor) ──
        remaining_limit = limit - len(all_candidates)
        if remaining_limit > 0:
            # Build translated similarity columns if we have a translation
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
                # Class 99 (Global Brand) covers ALL 45 classes - include in any class filter
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

        # Duplicate vector params for ORDER BY (score_expr appears twice in query)
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

        # Build visual columns: CLIP, DINOv2, color cosine + OCR text
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

            # Full composite visual score (CLIP 0.35 + DINOv2 0.30 + color 0.15 + OCR 0.20)
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
                phonetic_sim=1.0 if phon_match else 0.0,
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

        # Pre-screening: unified pipeline for both text and image searches
        screen_start = time.perf_counter()
        logger.debug("Pre-screen decision", name_empty=not name.strip(), has_img_vec=q_img_vec is not None, has_dino_vec=q_dino_vec is not None, has_ocr=bool(q_ocr_text))
        if not name.strip() and (q_img_vec or q_dino_vec):
            # Image-only search: use OCR text as the text query if available
            # This enables text stages (exact, containment, trigram) using logo text
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
            # Use the global scrape lock to serialize TurkPatent requests (prevent IP blocking)
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
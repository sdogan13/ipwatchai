"""
Centralized IDF-Based Scoring Module
====================================

Used across the entire system for consistent trademark similarity scoring.
Implements 3-tier word classification: GENERIC, SEMI_GENERIC, DISTINCTIVE.

This module consolidates scoring logic from:
- idf_lookup.py (database loading)
- idf_scoring.py (scoring algorithms)
- utils/scoring.py (text similarity)

Usage:
    from utils.idf_scoring import (
        initialize_idf_scoring,
        calculate_adjusted_score,
        calculate_risk_score,
        get_word_weight,
        is_cache_loaded
    )

    # At app startup (async)
    await initialize_idf_scoring(db_pool)

    # Anywhere in the codebase
    result = calculate_adjusted_score(0.85, "dogan patent", "doruk patent")
    # result['adjusted_score'] will be ~0.15 (only generic "patent" matches)
"""

import re
import logging
from typing import Dict, List, Tuple, Optional, Set
from functools import lru_cache
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


# ===============================================================
# GLOBAL CONSTANTS - Used across the entire system
# ===============================================================

MAX_RESULTS = 10          # Maximum search results to return
MAX_ALERTS_PER_ITEM = 10  # Maximum alerts per watchlist item


# ===============================================================
# GLOBAL CACHE - Loaded once at startup
# ===============================================================

_word_data: Dict[str, dict] = {}  # word -> {idf, doc_freq, weight, word_class}
_total_docs: int = 0
_cache_loaded: bool = False


# ===============================================================
# IDF THRESHOLDS AND WEIGHTS (3-tier classification)
# ===============================================================

# IDF thresholds for word classification
# Based on analysis of Turkish trademark corpus
IDF_THRESHOLD_GENERIC = 6.0       # IDF < 6.0 = GENERIC
IDF_THRESHOLD_SEMI_GENERIC = 8.5  # 6.0 <= IDF < 8.5 = SEMI_GENERIC
                                  # IDF >= 8.5 = DISTINCTIVE

# Weight multipliers by class
WEIGHT_GENERIC = 0.1       # Generic words contribute very little
WEIGHT_SEMI_GENERIC = 0.5  # Industry terms contribute moderately
WEIGHT_DISTINCTIVE = 1.0   # Unique brand names contribute fully


# ===============================================================
# FOREIGN GENERICS OVERRIDE
# ===============================================================
# Imported from standalone module to avoid circular imports with idf_lookup.py
from foreign_generics import FOREIGN_GENERICS_OVERRIDE  # noqa: F401

FOREIGN_GENERICS_OVERRIDE = FOREIGN_GENERICS_OVERRIDE  # re-export for back-compat


# ===============================================================
# INITIALIZATION
# ===============================================================


async def initialize_idf_scoring(db_pool) -> bool:
    """
    Load IDF data from database into memory.
    Call this ONCE at application startup.

    Args:
        db_pool: asyncpg connection pool

    Returns:
        True if initialization successful
    """
    global _word_data, _total_docs, _cache_loaded

    if _cache_loaded:
        logger.info("IDF scoring already initialized")
        return True

    try:
        async with db_pool.acquire() as conn:
            # Get total document count
            _total_docs = await conn.fetchval(
                "SELECT COUNT(*) FROM trademarks WHERE name IS NOT NULL"
            )

            # Check if word_idf table exists
            table_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'word_idf'
                )
            """)

            if not table_exists:
                logger.warning("word_idf table not found - run compute_idf.py first")
                _cache_loaded = True  # Mark as loaded to prevent retries
                return False

            # Load all word IDF data
            rows = await conn.fetch("""
                SELECT word, idf_score, document_frequency, is_generic
                FROM word_idf
            """)

            _word_data = {}
            for row in rows:
                word = row['word']
                idf = float(row['idf_score'])
                doc_freq = int(row['document_frequency'])

                if word in FOREIGN_GENERICS_OVERRIDE:
                    idf = 2.0
                    word_class = 'generic'
                    weight = WEIGHT_GENERIC
                elif idf < IDF_THRESHOLD_GENERIC:
                    word_class = 'generic'
                    weight = WEIGHT_GENERIC
                elif idf < IDF_THRESHOLD_SEMI_GENERIC:
                    word_class = 'semi_generic'
                    weight = WEIGHT_SEMI_GENERIC
                else:
                    word_class = 'distinctive'
                    weight = WEIGHT_DISTINCTIVE

                _word_data[word] = {
                    'idf': idf,
                    'doc_freq': doc_freq,
                    'weight': weight,
                    'word_class': word_class
                }

            _cache_loaded = True
            logger.info(f"IDF Scoring initialized: {len(_word_data):,} words, {_total_docs:,} docs")
            return True

    except Exception as e:
        logger.error(f"Failed to initialize IDF scoring: {e}")
        _cache_loaded = True  # Prevent retry loops
        return False


def initialize_idf_scoring_sync():
    """
    Synchronous initialization using psycopg2.
    Use when asyncpg pool is not available.
    """
    global _word_data, _total_docs, _cache_loaded

    if _cache_loaded:
        return True

    try:
        import psycopg2
        import os

        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "trademark_db"),
            user=os.getenv("DB_USER", "turk_patent"),
            password=os.getenv("DB_PASSWORD")
        )

        cur = conn.cursor()

        # Get total docs
        cur.execute("SELECT COUNT(*) FROM trademarks WHERE name IS NOT NULL")
        _total_docs = cur.fetchone()[0]

        # Load word IDF data
        cur.execute("SELECT word, idf_score, document_frequency FROM word_idf")

        _word_data = {}
        for word, idf, doc_freq in cur.fetchall():
            idf = float(idf)

            if word in FOREIGN_GENERICS_OVERRIDE:
                idf = 2.0
                word_class = 'generic'
                weight = WEIGHT_GENERIC
            elif idf < IDF_THRESHOLD_GENERIC:
                word_class = 'generic'
                weight = WEIGHT_GENERIC
            elif idf < IDF_THRESHOLD_SEMI_GENERIC:
                word_class = 'semi_generic'
                weight = WEIGHT_SEMI_GENERIC
            else:
                word_class = 'distinctive'
                weight = WEIGHT_DISTINCTIVE

            _word_data[word] = {
                'idf': idf,
                'doc_freq': doc_freq,
                'weight': weight,
                'word_class': word_class
            }

        cur.close()
        conn.close()

        _cache_loaded = True
        logger.info(f"IDF Scoring initialized (sync): {len(_word_data):,} words")
        return True

    except Exception as e:
        logger.warning(f"Sync IDF init failed: {e}")
        _cache_loaded = True
        return False


# ===============================================================
# TURKISH TEXT NORMALIZATION
# ===============================================================

def turkish_lower(text: str) -> str:
    """Turkish-aware lowercase: İ→i, I→ı, then standard .lower()."""
    if not text:
        return ""
    text = text.replace('İ', 'i').replace('I', 'ı')
    return text.lower()


def normalize_turkish(text: str) -> str:
    """
    Normalize Turkish characters to ASCII equivalents and strip punctuation.
    Uses turkish_lower() for proper I/İ handling.
    """
    if not text:
        return ""

    # First apply Turkish-aware lowercasing
    text = turkish_lower(text.strip())
    
    # Fold Turkish chars to ASCII
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

    # Replace all non-alphanumeric characters with spaces
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def tokenize(text: str) -> Set[str]:
    """Extract unique words from text (min length 2)."""
    normalized = normalize_turkish(text)
    words = set(re.findall(r'\b[a-z0-9]+\b', normalized))
    return {w for w in words if len(w) > 1}


# ===============================================================
# WORD-LEVEL FUNCTIONS
# ===============================================================

def get_word_weight(word: str) -> float:
    """
    Get IDF-based weight for a single word.

    Returns:
        0.1 for GENERIC, 0.5 for SEMI_GENERIC, 1.0 for DISTINCTIVE
    """
    _ensure_loaded()

    word_norm = normalize_turkish(word)

    if word_norm in _word_data:
        return _word_data[word_norm]['weight']

    # Unknown word = distinctive (rare/unique)
    return WEIGHT_DISTINCTIVE


def get_word_class(word: str) -> str:
    """
    Get 3-tier classification for a word.

    Returns: 'generic', 'semi_generic', or 'distinctive'
    """
    _ensure_loaded()

    word_norm = normalize_turkish(word)

    if word_norm in _word_data:
        return _word_data[word_norm]['word_class']

    return 'distinctive'


def get_word_idf(word: str) -> float:
    """Get raw IDF score for a word."""
    _ensure_loaded()

    word_norm = normalize_turkish(word)

    if word_norm in _word_data:
        return _word_data[word_norm]['idf']

    # Unknown word gets high IDF (distinctive)
    return 9.0


def get_doc_frequency(word: str) -> int:
    """Get document frequency for a word."""
    _ensure_loaded()

    word_norm = normalize_turkish(word)

    if word_norm in _word_data:
        return _word_data[word_norm]['doc_freq']

    return 0


def is_generic_word(word: str) -> bool:
    """Check if word is GENERIC or SEMI_GENERIC."""
    word_class = get_word_class(word)
    return word_class in ('generic', 'semi_generic')


# ===============================================================
# CORE SCORING FUNCTIONS
# ===============================================================

def calculate_text_weight(text: str) -> Tuple[float, List[dict]]:
    """
    Calculate overall IDF weight for a text.

    Returns:
        (min_weight, word_details)
        min_weight: Minimum weight among all words (0.1 to 1.0)
    """
    words = tokenize(text)

    if not words:
        return 1.0, []

    word_details = []
    for word in words:
        weight = get_word_weight(word)
        word_class = get_word_class(word)
        doc_freq = get_doc_frequency(word)

        word_details.append({
            'word': word,
            'weight': weight,
            'word_class': word_class,
            'doc_freq': doc_freq
        })

    # Use MINIMUM weight (most generic word determines penalty)
    min_weight = min(wd['weight'] for wd in word_details)

    return min_weight, word_details


def calculate_adjusted_score(
    raw_similarity: float,
    query_text: str,
    candidate_text: str,
    include_details: bool = False
) -> dict:
    """DEPRECATED. Wraps new Hierarchical scorer."""
    from idf_scoring import compute_idf_weighted_score
    score, breakdown = compute_idf_weighted_score(query_text, candidate_text, raw_similarity)
    result = {'raw_score': round(raw_similarity, 4), 'adjusted_score': round(score, 4), 'idf_weight': 1.0, 'blended_weight': score, 'blend_factor': 0.0, 'query_weight': 1.0, 'candidate_weight': 1.0}
    if include_details:
        result['details'] = {'query_words': breakdown.get("matched_words", []), 'candidate_words': breakdown.get("scoring_path", ""), 'breakdown': breakdown}
    return result

def calculate_text_similarity(query: str, target: str) -> float:
    """DEPRECATED. Wraps new Hierarchical scorer."""
    from idf_scoring import compute_idf_weighted_score
    score, _ = compute_idf_weighted_score(query, target)
    return score

def calculate_risk_score(
    text_similarity: float,
    image_similarity: Optional[float],
    class_overlap_ratio: float,
    query_text: str,
    candidate_text: str
) -> dict:
    """
    Calculate final risk score combining all factors.
    Use this for watchlist scanner and alert generation.

    Args:
        text_similarity: Raw text/name similarity (0-1)
        image_similarity: Image/logo similarity (0-1) or None
        class_overlap_ratio: Ratio of overlapping Nice classes (0-1)
        query_text: Watchlist brand name
        candidate_text: Conflicting trademark name

    Returns:
        {
            'overall_score': 0.65,
            'risk_level': 'high',
            'components': {...}
        }
    """
    _ensure_loaded()

    # Apply IDF adjustment to text similarity
    idf_result = calculate_adjusted_score(text_similarity, query_text, candidate_text)
    adjusted_text_sim = idf_result['adjusted_score']

    # Weights for combining scores
    TEXT_WEIGHT = 0.5
    IMAGE_WEIGHT = 0.3
    CLASS_WEIGHT = 0.2

    # Calculate components
    if image_similarity is not None and image_similarity > 0:
        text_component = adjusted_text_sim * TEXT_WEIGHT
        image_component = image_similarity * IMAGE_WEIGHT
    else:
        # Redistribute image weight to text if no image
        text_component = adjusted_text_sim * (TEXT_WEIGHT + IMAGE_WEIGHT)
        image_component = 0

    class_component = class_overlap_ratio * CLASS_WEIGHT

    # Final score
    final_score = text_component + image_component + class_component

    # Determine risk level using centralized thresholds
    from risk_engine import get_risk_level as _central_get_risk_level
    risk_level = _central_get_risk_level(final_score)

    return {
        'overall_score': round(final_score, 4),
        'risk_level': risk_level,
        'components': {
            'text': {
                'raw': round(text_similarity, 4),
                'adjusted': round(adjusted_text_sim, 4),
                'idf_weight': idf_result['applied_weight'],
                'contribution': round(text_component, 4)
            },
            'image': {
                'score': round(image_similarity, 4) if image_similarity else None,
                'contribution': round(image_component, 4)
            },
            'class_overlap': {
                'ratio': round(class_overlap_ratio, 4),
                'contribution': round(class_component, 4)
            }
        }
    }


def calculate_combined_score(
    text_similarity: float = None,
    image_similarity: float = None,
    search_type: str = 'combined'
) -> dict:
    """
    DEPRECATED: Use risk_engine.score_pair() instead.
    Calculate combined score for text+image search.

    Key rules:
    1. Image-only search: image score = overall score
    2. Text-only search: text score = overall score
    3. Combined search: weighted, but high scores dominate

    Args:
        text_similarity: Text/name similarity (0-1) or None
        image_similarity: Image/logo similarity (0-1) or None
        search_type: 'text', 'image', or 'combined'

    Returns:
        dict with overall_score, text_score, image_score, search_type, risk_level
    """
    # Normalize inputs
    text_sim = float(text_similarity) if text_similarity is not None else 0.0
    image_sim = float(image_similarity) if image_similarity is not None else 0.0

    # ═══════════════════════════════════════════════════════════════
    # RULE 1: Image-only search - image is everything
    # ═══════════════════════════════════════════════════════════════
    if search_type == 'image' or text_similarity is None or text_sim < 0.1:
        overall = image_sim
        return {
            'overall_score': round(overall, 3),
            'text_score': round(text_sim, 3),
            'image_score': round(image_sim, 3),
            'search_type': 'image',
            'risk_level': get_risk_level_simple(overall)
        }

    # ═══════════════════════════════════════════════════════════════
    # RULE 2: Text-only search - text is everything
    # ═══════════════════════════════════════════════════════════════
    if search_type == 'text' or image_similarity is None or image_sim < 0.1:
        overall = text_sim
        return {
            'overall_score': round(overall, 3),
            'text_score': round(text_sim, 3),
            'image_score': round(image_sim, 3),
            'search_type': 'text',
            'risk_level': get_risk_level_simple(overall)
        }

    # ═══════════════════════════════════════════════════════════════
    # RULE 3: Combined search - smart weighting
    # ═══════════════════════════════════════════════════════════════

    if image_sim >= 0.80:
        overall = (image_sim * 0.80) + (text_sim * 0.20)

    elif text_sim >= 0.80:
        overall = (text_sim * 0.80) + (image_sim * 0.20)

    # Both moderate - use balanced weighting
    else:
        # 60% text, 40% image (text usually more reliable)
        overall = (text_sim * 0.60) + (image_sim * 0.40)

    # ═══════════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════════
    if image_sim >= 0.95 or text_sim >= 0.95:
        overall = max(overall, 0.85)

    if image_sim >= 0.99:
        overall = max(overall, 0.92)  # ~100% image = at least 92% overall

    if text_sim >= 0.99:
        overall = max(overall, 0.92)  # ~100% text = at least 92% overall

    return {
        'overall_score': round(overall, 3),
        'text_score': round(text_sim, 3),
        'image_score': round(image_sim, 3),
        'search_type': 'combined',
        'risk_level': get_risk_level_simple(overall)
    }


def adjust_image_similarity(raw_score: float) -> float:
    """
    DEPRECATED: Use risk_engine.calculate_visual_similarity() instead.
    Apply a curve to image similarity scores to be more discriminating.

    Problem: Raw CLIP/DINOv2 scores are too generous for non-exact matches.
    - 100% stays 100% (exact match)
    - 95%+ stays high (near-exact)
    - Mid-range scores get reduced significantly
    - Low scores stay low

    Args:
        raw_score: Raw image similarity score (0-1)

    Returns:
        Adjusted score (0-1) that better reflects true similarity
    """
    if raw_score >= 0.98:
        # Near-perfect match - keep as is
        return raw_score

    if raw_score >= 0.95:
        # Very high match - slight reduction
        # 95% -> 90%, 98% -> 96%
        return 0.90 + (raw_score - 0.95) * 2

    if raw_score >= 0.80:
        # High match - moderate reduction
        # Map 80-95% to 60-90%
        normalized = (raw_score - 0.80) / 0.15  # 0 to 1
        return 0.60 + (normalized * 0.30)  # 60% to 90%

    if raw_score >= 0.60:
        # Medium match - significant reduction
        # Map 60-80% to 35-60%
        normalized = (raw_score - 0.60) / 0.20  # 0 to 1
        return 0.35 + (normalized * 0.25)  # 35% to 60%

    if raw_score >= 0.40:
        # Low-medium match - heavy reduction
        # Map 40-60% to 20-35%
        normalized = (raw_score - 0.40) / 0.20  # 0 to 1
        return 0.20 + (normalized * 0.15)  # 20% to 35%

    # Low match - keep proportionally low
    # 40% -> 20%, 20% -> 10%
    return raw_score * 0.5


def get_risk_level_simple(score: float) -> str:
    """Get simple risk level string. Delegates to risk_engine."""
    from risk_engine import get_risk_level as _central_get_risk_level
    return _central_get_risk_level(score)


def get_risk_level(score: float) -> dict:
    """
    Get risk level classification based on similarity score.
    Uses centralized RISK_THRESHOLDS from risk_engine.

    Returns dict with: level, text, color
    """
    from risk_engine import get_risk_level as _central_get_risk_level
    level = _central_get_risk_level(score)
    level_map = {
        'critical': {'level': 'critical', 'text': 'Kritik Risk', 'color': '#ef4444'},
        'very_high': {'level': 'very_high', 'text': 'Cok Yuksek Risk', 'color': '#f97316'},
        'high': {'level': 'high', 'text': 'Yuksek Risk', 'color': '#f59e0b'},
        'medium': {'level': 'medium', 'text': 'Orta Risk', 'color': '#eab308'},
        'low': {'level': 'low', 'text': 'Dusuk Risk', 'color': '#22c55e'},
    }
    return level_map.get(level, level_map['low'])


# ===============================================================
# QUERY ANALYSIS
# ===============================================================

def analyze_query(query: str) -> dict:
    """
    Analyze a search query and return word importance breakdown.
    Useful for debugging and displaying to users.

    Args:
        query: Search query (e.g., "dogan patent")

    Returns:
        {
            'query': 'dogan patent',
            'words': [
                {'word': 'dogan', 'weight': 1.0, 'word_class': 'distinctive'},
                {'word': 'patent', 'weight': 0.1, 'word_class': 'generic'}
            ],
            'distinctive_weight': 0.91,
            'generic_weight': 0.09
        }
    """
    _ensure_loaded()

    words = tokenize(query)

    if not words:
        return {"query": query, "words": [], "total_weight": 0}

    word_analysis = []
    total_weighted = 0.0

    for word in sorted(words):
        weight = get_word_weight(word)
        word_class = get_word_class(word)
        doc_freq = get_doc_frequency(word)
        idf = get_word_idf(word)

        word_analysis.append({
            "word": word,
            "idf": round(idf, 2),
            "word_class": word_class,
            "weight": weight,
            "doc_freq": doc_freq
        })
        total_weighted += weight

    # Calculate final weights (normalized)
    for wa in word_analysis:
        if total_weighted > 0:
            wa["normalized_weight"] = round(wa["weight"] / total_weighted, 3)
        else:
            wa["normalized_weight"] = 0.0

    # Sort by weight descending
    word_analysis.sort(key=lambda x: x["weight"], reverse=True)

    # Summary by class
    distinctive_weight = sum(w["normalized_weight"] for w in word_analysis if w["word_class"] == "distinctive")
    semi_generic_weight = sum(w["normalized_weight"] for w in word_analysis if w["word_class"] == "semi_generic")
    generic_weight = sum(w["normalized_weight"] for w in word_analysis if w["word_class"] == "generic")

    return {
        "query": query,
        "normalized": normalize_turkish(query),
        "words": word_analysis,
        "distinctive_weight": round(distinctive_weight, 3),
        "semi_generic_weight": round(semi_generic_weight, 3),
        "generic_weight": round(generic_weight, 3),
        "distinctive_count": sum(1 for w in word_analysis if w["word_class"] == "distinctive"),
        "semi_generic_count": sum(1 for w in word_analysis if w["word_class"] == "semi_generic"),
        "generic_count": sum(1 for w in word_analysis if w["word_class"] == "generic")
    }


# ===============================================================
# UTILITY FUNCTIONS
# ===============================================================

def is_cache_loaded() -> bool:
    """Check if IDF cache is loaded."""
    return _cache_loaded


def get_cache_stats() -> dict:
    """Get statistics about the IDF cache."""
    return {
        'loaded': _cache_loaded,
        'word_count': len(_word_data),
        'total_docs': _total_docs
    }


def get_most_common_words(limit: int = 50) -> List[dict]:
    """Get most common (generic) words from cache."""
    _ensure_loaded()

    sorted_words = sorted(
        _word_data.items(),
        key=lambda x: x[1]['doc_freq'],
        reverse=True
    )[:limit]

    return [
        {
            'word': word,
            'doc_freq': data['doc_freq'],
            'idf': round(data['idf'], 2),
            'weight': data['weight'],
            'word_class': data['word_class']
        }
        for word, data in sorted_words
    ]


def clear_cache():
    """Clear the cache (for testing or reload)."""
    global _word_data, _total_docs, _cache_loaded
    _word_data = {}
    _total_docs = 0
    _cache_loaded = False
    logger.info("IDF cache cleared")


def _ensure_loaded():
    """Ensure cache is loaded, using sync method as fallback."""
    global _cache_loaded

    if not _cache_loaded:
        initialize_idf_scoring_sync()


# ===============================================================
# MULTI-FACTOR SCORING SYSTEM
# Addresses: substring matches, length differences, coverage
# ===============================================================

def word_similarity(word1: str, word2: str) -> float:
    """
    Calculate similarity between two words - BALANCED VERSION.

    Not too strict, not too loose.

    Key rules:
    - Exact match = 1.0
    - Prefix match (dogan → doganlar) = 0.75+ (decent match)
    - Suffix match (dogan in erdogan) = 0.55 (partial credit)
    - Middle substring = 0.35 (less common)
    - Fuzzy match gets partial credit

    Returns: 0.1-1.0 (minimum floor of 0.1)
    """
    w1 = normalize_turkish(word1)
    w2 = normalize_turkish(word2)

    # Exact match
    if w1 == w2:
        return 1.0

    # Very short words - need high similarity
    if len(w1) <= 2 or len(w2) <= 2:
        if w1 == w2:
            return 1.0
        # Allow fuzzy for short words
        ratio = SequenceMatcher(None, w1, w2).ratio()
        return ratio if ratio >= 0.7 else 0.1

    # Check if one is substring of other
    if w1 in w2 or w2 in w1:
        shorter = w1 if len(w1) < len(w2) else w2
        longer = w2 if len(w1) < len(w2) else w1

        if longer.startswith(shorter):
            ratio = len(shorter) / len(longer)
            return 0.65 + (0.30 * ratio)  # 0.65-0.95

        # Suffix match: "dogan" in "erdogan" - DIFFERENT WORDS
        if longer.endswith(shorter):
            ratio = len(shorter) / len(longer)
            return 0.25 + (0.15 * ratio)  # 0.25-0.40 (low score)

        # Middle substring: less common, lower but not zero
        ratio = len(shorter) / len(longer)
        return 0.30 + (0.20 * ratio)  # 0.30-0.50

    # Fuzzy matching using SequenceMatcher
    ratio = SequenceMatcher(None, w1, w2).ratio()

    # Give graduated credit based on similarity
    if ratio >= 0.85:
        return ratio  # High similarity passes through
    elif ratio >= 0.70:
        return ratio * 0.90  # Slight penalty
    elif ratio >= 0.50:
        return ratio * 0.70  # Moderate penalty, but not zero
    else:
        return 0.1  # Minimum floor instead of 0


def calculate_word_match_factor(query_words: List[str], result_words: List[str]) -> Tuple[float, Dict]:
    """
    Calculate how well query words match result words using strict word boundaries.

    Returns: (factor 0.0-1.0, details dict)
    """
    if not query_words or not result_words:
        return 0.0, {'matches': []}

    matches = []
    result_words_norm = [normalize_turkish(w) for w in result_words]

    for qw in query_words:
        qw_norm = normalize_turkish(qw)
        best_match = None
        best_score = 0.0
        match_type = 'none'

        for i, rw_norm in enumerate(result_words_norm):
            sim = word_similarity(qw, result_words[i])
            if sim > best_score:
                best_score = sim
                best_match = result_words[i]
                if sim == 1.0:
                    match_type = 'exact'
                elif sim >= 0.7:
                    match_type = 'prefix'
                elif sim >= 0.3:
                    match_type = 'partial'
                else:
                    match_type = 'weak'

        matches.append({
            'query_word': qw,
            'matched_word': best_match,
            'similarity': round(best_score, 3),
            'match_type': match_type,
            'word_weight': get_word_weight(qw)
        })

    total_weight = 0
    weighted_sum = 0

    for m in matches:
        word_weight = m['word_weight']
        weighted_sum += m['similarity'] * word_weight
        total_weight += word_weight

    factor = weighted_sum / total_weight if total_weight > 0 else 0.0

    return round(factor, 3), {'matches': matches}


def calculate_length_ratio_factor(query_words: List[str], result_words: List[str]) -> float:
    """
    Softer penalty for longer results.

    Examples:
    - Query: 2 words, Result: 2 words → factor = 1.0
    - Query: 2 words, Result: 3 words → factor = 0.88
    - Query: 2 words, Result: 4 words → factor = 0.83

    Returns: 0.65-1.0 (minimum 0.65)
    """
    query_len = len(query_words)
    result_len = len(result_words)

    if query_len == 0 or result_len == 0:
        return 0.70  # Default for edge cases

    # If result is shorter or equal, no penalty
    if result_len <= query_len:
        return 1.0

    # Calculate ratio
    ratio = query_len / result_len

    # Softer formula: minimum 0.65 instead of 0.5
    factor = 0.65 + (0.35 * ratio)

    return round(factor, 3)


def calculate_coverage_factor(query_words: List[str], result_words: List[str],
                              match_threshold: float = 0.75) -> Tuple[float, Dict]:
    """Calculate coverage factor evaluating partial character matches.
    Returns: (factor 0.25-1.0, details dict)
    """
    if not query_words:
        return 0.70, {}

    result_words_norm = [normalize_turkish(w) for w in result_words]

    found_distinctive = []
    found_semi_generic = []
    found_generic = []
    partial_matches = []  # NEW: Track partial matches
    not_found_distinctive = []
    not_found_other = []

    for qw in query_words:
        qw_norm = normalize_turkish(qw)
        word_weight = get_word_weight(qw)
        is_distinctive = word_weight >= WEIGHT_DISTINCTIVE

        # Check for match with best score tracking
        best_match_score = 0.0
        best_match_word = None

        for rw_norm in result_words_norm:
            sim = word_similarity(qw, rw_norm)
            if sim > best_match_score:
                best_match_score = sim
                best_match_word = rw_norm

        # Categorize based on match quality
        if best_match_score >= match_threshold:
            # Full match
            if is_distinctive:
                found_distinctive.append(qw)
            elif word_weight >= WEIGHT_SEMI_GENERIC:
                found_semi_generic.append(qw)
            else:
                found_generic.append(qw)
        elif best_match_score >= 0.45:
            # Partial match (e.g., "dogan" in "erdogan")
            partial_matches.append({
                'word': qw,
                'score': best_match_score,
                'matched_to': best_match_word,
                'is_distinctive': is_distinctive
            })
        else:
            # No match
            if is_distinctive:
                not_found_distinctive.append(qw)
            else:
                not_found_other.append(qw)

    # Calculate coverage scores
    total_distinctive = len([qw for qw in query_words if get_word_weight(qw) >= WEIGHT_DISTINCTIVE])
    total_semi_generic = len([qw for qw in query_words if WEIGHT_GENERIC < get_word_weight(qw) < WEIGHT_DISTINCTIVE])
    total_generic = len([qw for qw in query_words if get_word_weight(qw) <= WEIGHT_GENERIC])

    # Full match scores
    distinctive_score = len(found_distinctive) / max(total_distinctive, 1) if total_distinctive > 0 else 1.0
    semi_generic_score = len(found_semi_generic) / max(total_semi_generic, 1) if total_semi_generic > 0 else 1.0
    generic_score = len(found_generic) / max(total_generic, 1) if total_generic > 0 else 1.0

    # Calculate partial credit
    partial_credit = 0.0
    for pm in partial_matches:
        # Give proportional credit based on match score
        if pm['is_distinctive']:
            partial_credit += pm['score'] * 0.6  # 60% of match score for distinctive
        else:
            partial_credit += pm['score'] * 0.3  # 30% for non-distinctive

    # Normalize partial credit by total words
    partial_credit = partial_credit / max(len(query_words), 1)

    factor = (
        (0.55 * distinctive_score) +
        (0.20 * semi_generic_score) +
        (0.10 * generic_score) +
        (0.15 * min(partial_credit * 2, 1.0))  # Boost partial credit contribution
    )

    # Softer penalty when only generic words match
    if len(found_distinctive) == 0 and total_distinctive > 0:
        if len(found_generic) > 0 or len(found_semi_generic) > 0:
            # Only generic/semi-generic words matched
            if len(partial_matches) > 0:
                # But we have partial matches - less penalty
                factor = max(factor * 0.65, 0.35)
            else:
                factor = max(factor * 0.50, 0.25)

    factor = max(factor, 0.25)

    details = {
        'found_distinctive': found_distinctive,
        'found_semi_generic': found_semi_generic,
        'found_generic': found_generic,
        'partial_matches': partial_matches,
        'not_found_distinctive': not_found_distinctive,
        'not_found_other': not_found_other,
        'distinctive_score': round(distinctive_score, 3),
        'semi_generic_score': round(semi_generic_score, 3),
        'generic_score': round(generic_score, 3),
        'partial_credit': round(partial_credit, 3)
    }

    return round(factor, 3), details


def calculate_idf_blended_factor(query_text: str, result_text: str) -> Tuple[float, Dict]:
    """
    Get IDF-based weight with blending for gradual penalty.

    Returns: (blended_factor 0.46-1.0, details dict)
    """
    query_weight, query_details = calculate_text_weight(query_text)
    result_weight, result_details = calculate_text_weight(result_text)

    # Use minimum (most generic word determines penalty)
    idf_weight = min(query_weight, result_weight)

    # Apply blending for gradual penalty
    BLEND = 0.4
    blended_weight = BLEND + (1 - BLEND) * idf_weight

    return round(blended_weight, 3), {
        'query_weight': round(query_weight, 3),
        'result_weight': round(result_weight, 3),
        'raw_idf': round(idf_weight, 3),
        'blended': round(blended_weight, 3)
    }


def calculate_comprehensive_score(
    query_text: str,
    result_text: str,
    raw_similarity: float = None,
    include_details: bool = False,
) -> Dict:
    """DEPRECATED. Wraps new Hierarchical scorer."""
    from idf_scoring import compute_idf_weighted_score
    raw = raw_similarity if raw_similarity is not None else 0.0
    score, breakdown = compute_idf_weighted_score(query_text, result_text, raw)
    
    risk_level = "low"
    if score >= 0.70: risk_level = "critical"
    elif score >= 0.50: risk_level = "high"
    elif score >= 0.30: risk_level = "medium"
    
    result = {
        'raw_score': round(raw, 3),
        'final_score': round(score, 3),
        'factors': {'word_match': 0.0, 'length_ratio': 0.0, 'coverage': 0.0, 'idf': 0.0},
        'weighted_factor': round(score, 3),
        'risk_level': risk_level
    }
    
    if include_details:
        result['details'] = breakdown
    return result

def calculate_alert_risk_score(
    query_text: str,
    result_text: str,
    raw_text_similarity: float,
    image_similarity: Optional[float],
    class_overlap_ratio: float,
    include_details: bool = False
) -> Dict:
    """
    Calculate risk score for watchlist alerts.
    Combines comprehensive text scoring with image and class overlap.

    Use this for:
    - Watchlist scanner
    - Alert generation
    - Risk assessment

    Args:
        query_text: Watchlist brand name
        result_text: Conflicting trademark name
        raw_text_similarity: Pre-calculated text similarity (0-1)
        image_similarity: Image/logo similarity (0-1) or None
        class_overlap_ratio: Ratio of overlapping Nice classes (0-1)
        include_details: Include full breakdown

    Returns:
        {
            'overall_score': 0.45,
            'risk_level': 'medium',
            'components': {...}
        }
    """
    # Get comprehensive text score
    text_result = calculate_comprehensive_score(
        query_text=query_text,
        result_text=result_text,
        raw_similarity=raw_text_similarity,
        include_details=include_details
    )

    adjusted_text_score = text_result['final_score']

    # Weight distribution
    TEXT_WEIGHT = 0.50
    IMAGE_WEIGHT = 0.25
    CLASS_WEIGHT = 0.25

    text_component = adjusted_text_score * TEXT_WEIGHT

    if image_similarity is not None and image_similarity > 0:
        image_component = image_similarity * IMAGE_WEIGHT
    else:
        # Redistribute weight to text if no image
        text_component = adjusted_text_score * (TEXT_WEIGHT + IMAGE_WEIGHT)
        image_component = 0

    class_component = class_overlap_ratio * CLASS_WEIGHT

    overall_score = text_component + image_component + class_component

    # Determine risk level
    if overall_score >= 0.65:
        risk_level = 'critical'
    elif overall_score >= 0.45:
        risk_level = 'high'
    elif overall_score >= 0.30:
        risk_level = 'medium'
    else:
        risk_level = 'low'

    return {
        'overall_score': round(overall_score, 3),
        'risk_level': risk_level,
        'components': {
            'text': {
                'raw': round(raw_text_similarity, 3),
                'adjusted': round(adjusted_text_score, 3),
                'factors': text_result['factors'],
                'contribution': round(text_component, 3)
            },
            'image': {
                'score': round(image_similarity, 3) if image_similarity else None,
                'contribution': round(image_component, 3)
            },
            'class_overlap': {
                'ratio': round(class_overlap_ratio, 3),
                'contribution': round(class_component, 3)
            }
        },
        'text_details': text_result.get('details') if include_details else None
    }


# ===============================================================
# OCR-ENHANCED IMAGE SCORING
# ===============================================================

# Global OCR reader (loaded lazily)
_ocr_reader = None
_ocr_available = False

def _load_ocr_reader():
    """Lazily load EasyOCR reader."""
    global _ocr_reader, _ocr_available
    if _ocr_reader is not None:
        return _ocr_reader

    try:
        import easyocr
        import torch
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        _ocr_reader = easyocr.Reader(['en', 'tr'], gpu=(device == 'cuda'), verbose=False)
        _ocr_available = True
        logger.info(f"EasyOCR loaded on {device}")
        return _ocr_reader
    except ImportError:
        logger.warning("EasyOCR not available - OCR features disabled")
        _ocr_available = False
        return None
    except Exception as e:
        logger.error(f"Failed to load EasyOCR: {e}")
        _ocr_available = False
        return None


def extract_ocr_text(image_path: str) -> str:
    """
    Extract text from an image using EasyOCR.

    Args:
        image_path: Path to image file

    Returns:
        Extracted text (lowercase, stripped) or empty string
    """
    reader = _load_ocr_reader()
    if reader is None:
        return ""

    try:
        results = reader.readtext(image_path, detail=0, paragraph=True)
        text = turkish_lower(" ".join(results).strip())
        return text
    except Exception as e:
        logger.warning(f"OCR extraction failed: {e}")
        return ""


def calculate_ocr_similarity(ocr_text: str, trademark_name: str) -> float:
    """DEPRECATED: Use risk_engine.calculate_visual_similarity() instead.
    Kept for backward compatibility only."""
    import warnings
    warnings.warn("calculate_ocr_similarity is deprecated, use risk_engine.calculate_visual_similarity", DeprecationWarning, stacklevel=2)
    if not ocr_text or not trademark_name:
        return 0.0
    from difflib import SequenceMatcher
    return SequenceMatcher(None, turkish_lower(ocr_text.strip()), turkish_lower(trademark_name.strip())).ratio()


def combine_visual_scores(
    clip_sim: float = 0.0,
    dino_sim: float = 0.0,
    color_sim: float = 0.0,
    ocr_text_query: str = "",
    ocr_text_target: str = "",
) -> dict:
    """DEPRECATED: Use risk_engine.calculate_visual_similarity() instead.
    Kept for backward compatibility only."""
    import warnings
    warnings.warn("combine_visual_scores is deprecated, use risk_engine.calculate_visual_similarity", DeprecationWarning, stacklevel=2)
    from risk_engine import calculate_visual_similarity
    score = calculate_visual_similarity(
        clip_sim=clip_sim, dinov2_sim=dino_sim, color_sim=color_sim,
        ocr_text_a=ocr_text_query, ocr_text_b=ocr_text_target,
    )
    return {"combined_score": score, "clip_score": clip_sim, "dino_score": dino_sim, "color_score": color_sim, "ocr_score": 0.0, "components_used": []}


def calculate_image_score_with_ocr(
    raw_image_similarity: float,
    query_ocr_text: str,
    trademark_ocr_text: str = None
) -> dict:
    """DEPRECATED: Use risk_engine.calculate_visual_similarity() instead.
    Kept for backward compatibility only."""
    import warnings
    warnings.warn("calculate_image_score_with_ocr is deprecated, use risk_engine.calculate_visual_similarity", DeprecationWarning, stacklevel=2)
    from risk_engine import calculate_visual_similarity, get_risk_level
    score = calculate_visual_similarity(
        clip_sim=raw_image_similarity,
        ocr_text_a=query_ocr_text or "",
        ocr_text_b=trademark_ocr_text or "",
    )
    return {
        'final_score': score, 'visual_score': raw_image_similarity,
        'ocr_boost': 0.0, 'ocr_similarity': 0.0,
        'ocr_query_text': query_ocr_text or "", 'ocr_target_text': trademark_ocr_text or "",
        'risk_level': get_risk_level(score),
    }


# ===============================================================
# CLI TEST
# ===============================================================

if __name__ == "__main__":
    print("=" * 70)
    print("COMPREHENSIVE MULTI-FACTOR SCORING TEST")
    print("=" * 70)

    # Initialize
    initialize_idf_scoring_sync()

    print(f"\nCache stats: {get_cache_stats()}")

    # Comprehensive scoring test cases
    print("\n" + "=" * 70)
    print("COMPREHENSIVE SCORING TEST (Multi-Factor)")
    print("=" * 70)

    comprehensive_tests = [
        ("dogan patent", "d.p dogan patent", "Should be 82-88% - exact 'dogan' match"),
        ("dogan patent", "dogan", "Should be 75-82% - exact distinctive match (containment)"),
        ("dogan patent", "erdogan patent ofisi", "Should be 28-38% - 'dogan'≠'erdogan'"),
        ("dogan patent", "dogru patent", "Should be 20-30% - 'dogan'≠'dogru'"),
        ("dogan patent", "xyz patent abc", "Should be LOW - no distinctive match"),
        ("nike", "nike sports", "Should be 70-78% - exact match"),
        ("coca cola", "coca cola company", "Should be HIGH - both distinctive match"),
        ("abc xyz", "abc", "Should be HIGH - distinctive match (containment)"),
    ]

    print(f"\n{'Query':<25} {'Result':<30} {'Raw':>6} {'Final':>6} {'Risk':<10} Note")
    print("-" * 100)

    for query, result, note in comprehensive_tests:
        # Let calculate_comprehensive_score compute its own raw_similarity
        scoring = calculate_comprehensive_score(query, result, include_details=False)
        print(f"{query:<25} {result:<30} {scoring['raw_score']:>5.0%} {scoring['final_score']:>5.0%} {scoring['risk_level']:<10} {note}")

    # Detailed breakdown for problem case
    print("\n" + "=" * 70)
    print("DETAILED BREAKDOWN: 'dogan patent' vs 'erdogan patent ofisi'")
    print("=" * 70)

    detail = calculate_comprehensive_score(
        "dogan patent",
        "erdogan patent ofisi",
        include_details=True
    )

    print(f"\nRaw Score: {detail['raw_score']:.1%}")
    print(f"Final Score: {detail['final_score']:.1%}")
    print(f"Risk Level: {detail['risk_level']}")
    print(f"\nFactors:")
    for name, value in detail['factors'].items():
        print(f"  {name}: {value:.3f}")
    print(f"Weighted Factor: {detail['weighted_factor']:.3f}")

    if 'details' in detail:
        print(f"\nWord Match Details:")
        for m in detail['details']['word_match']['matches']:
            print(f"  '{m['query_word']}' -> '{m['matched_word']}' ({m['match_type']}, {m['similarity']:.1%})")

        print(f"\nCoverage Details:")
        cov = detail['details']['coverage']
        print(f"  Found Distinctive: {cov['found_distinctive']}")
        print(f"  Found Generic: {cov['found_generic']}")
        print(f"  NOT Found Distinctive: {cov['not_found_distinctive']}")

    # Legacy tests
    print("\n" + "=" * 70)
    print("LEGACY TEXT SIMILARITY (for comparison)")
    print("-" * 70)

    for query, target, _, description in comprehensive_tests[:5]:
        score = calculate_text_similarity(query, target)
        print(f"'{query}' vs '{target}': {score:.1%}")

    print("\n" + "=" * 70)
    print("Query Analysis:")
    print("-" * 70)

    for query in ["dogan patent", "erdogan patent ofisi", "coca cola"]:
        analysis = analyze_query(query)
        print(f"\n'{query}':")
        print(f"  Distinctive: {analysis['distinctive_weight']:.1%}")
        print(f"  Generic: {analysis['generic_weight']:.1%}")
        for w in analysis['words']:
            print(f"    {w['word']}: {w['word_class']} (weight={w['weight']})")

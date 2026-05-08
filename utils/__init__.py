# Utils package - Trademark scoring utilities
#
# CENTRALIZED IDF SCORING MODULE (utils/idf_scoring.py)
# Uses data-driven IDF from word_idf table (run compute_idf.py monthly)

# ===============================================================
# CENTRALIZED IDF SCORING - USE THESE FUNCTIONS!
# ===============================================================
from .idf_scoring import (
    # Initialization (call at app startup)
    initialize_idf_scoring,       # Async init with db pool
    initialize_idf_scoring_sync,  # Sync init fallback

    # Core scoring functions
    calculate_text_similarity,    # Main text similarity with IDF
    calculate_adjusted_score,     # Adjust raw scores with IDF
    calculate_risk_score,         # Full risk assessment
    calculate_combined_score,     # Combine text/image/semantic
    get_risk_level,               # Risk level classification

    # Word-level functions
    get_word_weight,              # Get IDF weight for word
    get_word_class,               # Get 3-tier classification
    is_generic_word,              # Check if generic/semi-generic
    get_word_idf,                 # Get raw IDF score
    get_doc_frequency,            # Get document frequency

    # Text normalization
    normalize_turkish,            # Turkish char normalization
    tokenize,                     # Split text into tokens

    # Analysis & debugging
    analyze_query,                # Full query analysis

    # Cache management
    is_cache_loaded,              # Check if initialized
    get_cache_stats,              # Get cache statistics
    get_most_common_words,        # Get generic words list
    clear_cache,                  # Clear for testing
)

# Public surface for `from utils import X` consumers; declaring __all__
# also silences F401 unused-import warnings on the barrel re-exports above.
__all__ = [
    "initialize_idf_scoring",
    "initialize_idf_scoring_sync",
    "calculate_text_similarity",
    "calculate_adjusted_score",
    "calculate_risk_score",
    "calculate_combined_score",
    "get_risk_level",
    "get_word_weight",
    "get_word_class",
    "is_generic_word",
    "get_word_idf",
    "get_doc_frequency",
    "normalize_turkish",
    "tokenize",
    "analyze_query",
    "is_cache_loaded",
    "get_cache_stats",
    "get_most_common_words",
    "clear_cache",
]

# ===============================================================
# BACKWARD COMPATIBILITY
# ===============================================================
# utils.scoring has been deleted. GENERIC_WORDS_FALLBACK is no longer exported.
# Use is_generic_word() from utils.idf_scoring instead.

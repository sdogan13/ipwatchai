"""
Admin Routes
Extracted from api/routes.py for maintainability.
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException, status
from auth.authentication import CurrentUser, get_current_user, require_role
from models.schemas import (
    PaginatedResponse, SuccessResponse
)
from database.crud import Database

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["Admin"])
# ==========================================
# Admin Routes - IDF Management
# ==========================================

@admin_router.get("/idf-stats")
async def get_idf_stats(user: CurrentUser = Depends(require_role(["owner", "admin"]))):
    """
    Get IDF scoring system statistics.
    Shows cache status, word counts, and top generic words.
    """
    from utils.idf_scoring import (
        is_cache_loaded, get_cache_stats, get_most_common_words
    )

    stats = get_cache_stats()
    most_common = get_most_common_words(30)

    return {
        "success": True,
        "stats": stats,
        "most_common_words": most_common
    }


@admin_router.get("/idf-analyze")
async def analyze_word(
    word: str = Query(..., description="Word to analyze"),
    user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """
    Analyze a specific word's IDF classification.
    Returns IDF score, word class, weight, and document frequency.
    """
    from utils.idf_scoring import (
        get_word_idf, get_word_class, get_word_weight, get_doc_frequency
    )

    return {
        "word": word,
        "idf_score": get_word_idf(word),
        "word_class": get_word_class(word),
        "weight": get_word_weight(word),
        "doc_frequency": get_doc_frequency(word)
    }


@admin_router.get("/idf-query-analysis")
async def analyze_query(
    q: str = Query(..., description="Query to analyze"),
    user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """
    Analyze a search query and show word importance breakdown.
    Useful for debugging why certain results rank high/low.
    """
    from utils.idf_scoring import analyze_query as _analyze

    return _analyze(q)


@admin_router.post("/idf-test-similarity")
async def test_similarity(
    query: str = Query(..., description="Search query"),
    target: str = Query(..., description="Target trademark name"),
    user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """
    Test IDF-weighted similarity between two texts.
    Returns both raw and adjusted scores with breakdown.
    """
    from utils.idf_scoring import (
        calculate_text_similarity, calculate_adjusted_score
    )

    # Get text similarity
    text_sim = calculate_text_similarity(query, target)

    # Get detailed adjusted score
    adjusted = calculate_adjusted_score(text_sim, query, target, include_details=True)

    return {
        "query": query,
        "target": target,
        "text_similarity": round(text_sim, 4),
        "adjusted_score": adjusted['adjusted_score'],
        "applied_weight": adjusted['applied_weight'],
        "details": adjusted.get('details', {})
    }


@admin_router.post("/idf-refresh")
async def refresh_idf_cache(
    user: CurrentUser = Depends(require_role(["admin"]))
):
    """
    Refresh IDF cache from database.
    Requires ADMIN role. Use after running compute_idf.py.
    """
    from utils.idf_scoring import clear_cache, initialize_idf_scoring_sync, get_cache_stats

    clear_cache()
    success = initialize_idf_scoring_sync()
    stats = get_cache_stats()

    return {
        "success": success,
        "message": "IDF cache refreshed" if success else "IDF refresh failed",
        "stats": stats
    }



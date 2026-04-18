"""
Attorney Portfolio API - PRO Feature
=====================================
View all trademark applications handled by a specific attorney.
Mirrors the holder portfolio pattern (api/holders.py).
"""

import logging

from fastapi import APIRouter, Depends, Query

from auth.authentication import CurrentUser, get_current_user
from services.attorney_service import (
    build_attorney_trademarks_csv_stream,
    get_attorney_trademarks_data,
    search_attorney_portfolio_data,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attorneys", tags=["attorneys"])


@router.get("/{attorney_no}/trademarks")
async def get_attorney_trademarks(
    attorney_no: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Get all trademark applications handled by an attorney.

    PRO feature - requires Professional or Enterprise plan.
    """
    return await get_attorney_trademarks_data(
        attorney_no=attorney_no,
        page=page,
        page_size=page_size,
        current_user=current_user,
    )


@router.get("/search")
async def search_attorneys(
    query: str = Query(..., min_length=2),
    limit: int = Query(10, ge=1, le=50),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Search for attorneys by name or ID (autocomplete). PRO feature.
    """
    return await search_attorney_portfolio_data(
        query=query,
        limit=limit,
        current_user=current_user,
    )


@router.get("/{attorney_no}/trademarks/csv")
async def export_attorney_trademarks_csv(
    attorney_no: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Export ALL trademarks by an attorney as CSV. PRO feature."""
    return await build_attorney_trademarks_csv_stream(
        attorney_no=attorney_no,
        current_user=current_user,
    )

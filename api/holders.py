"""
Holder Portfolio API - PRO Feature
==================================
View all trademark applications by a specific holder.
"""

import logging

from fastapi import APIRouter, Depends, Query

from auth.authentication import CurrentUser, get_current_user
from services.holder_service import (
    build_holder_trademarks_csv_stream,
    get_holder_trademarks_data,
    search_holder_portfolio_data,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/holders", tags=["holders"])


@router.get("/{tpe_client_id}/trademarks")
async def get_holder_trademarks(
    tpe_client_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Get all trademark applications by a holder.

    PRO feature - requires Professional or Enterprise plan.
    """
    return await get_holder_trademarks_data(
        tpe_client_id=tpe_client_id,
        page=page,
        page_size=page_size,
        current_user=current_user,
    )


@router.get("/search")
async def search_holders(
    query: str = Query(..., min_length=2),
    limit: int = Query(10, ge=1, le=50),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Search for holders by name (autocomplete). PRO feature.
    """
    return await search_holder_portfolio_data(
        query=query,
        limit=limit,
        current_user=current_user,
    )


@router.get("/{tpe_client_id}/trademarks/csv")
async def export_holder_trademarks_csv(
    tpe_client_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Export ALL trademarks by a holder as CSV. PRO feature."""
    return await build_holder_trademarks_csv_stream(
        tpe_client_id=tpe_client_id,
        current_user=current_user,
    )

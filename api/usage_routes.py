"""
Usage Routes
Extracted from api/routes.py for maintainability.
"""
import logging

from fastapi import APIRouter, Depends
from auth.authentication import CurrentUser, get_current_user

logger = logging.getLogger(__name__)

usage_router = APIRouter(prefix="/usage", tags=["Usage"])
# ==========================================
# Usage Summary
# ==========================================

@usage_router.get("/summary")
async def get_usage_summary(current_user: CurrentUser = Depends(get_current_user)):
    """
    Unified credits/usage endpoint.
    Returns all usage counters and plan limits for the current user.
    """
    from services.usage_service import get_usage_summary_data

    return await get_usage_summary_data(current_user=current_user)



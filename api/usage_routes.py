"""
Usage Routes
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



"""
Dashboard Routes
Extracted from api/routes.py for maintainability.
"""
import logging

from fastapi import APIRouter, Depends

from auth.authentication import CurrentUser, get_current_user
from models.schemas import DashboardStats

logger = logging.getLogger(__name__)

dashboard_router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
# ==========================================
# Dashboard Routes
# ==========================================


@dashboard_router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(current_user: CurrentUser = Depends(get_current_user)):
    """Get main dashboard statistics."""
    from services.dashboard_service import get_dashboard_stats_data

    return await get_dashboard_stats_data(current_user=current_user)

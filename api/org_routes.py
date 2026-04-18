"""
Organization Routes
Extracted from api/routes.py for maintainability.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends
from auth.authentication import CurrentUser, get_current_user, require_role
from models.schemas import (
    OrganizationResponse,
    OrganizationStats,
    OrganizationUpdate,
    SuccessResponse,
)
from pydantic import BaseModel as PydanticBaseModel
from api.watchlist_background import run_watchlist_scan_task
from services.organization_service import (
    get_organization_data,
    get_organization_settings_data,
    get_organization_stats_data,
    prepare_organization_threshold_rescan,
    update_organization_record,
)

logger = logging.getLogger(__name__)

class ThresholdUpdateRequest(PydanticBaseModel):
    """Request model for threshold update"""
    threshold: float

org_router = APIRouter(prefix="/organization", tags=["Organization"])
# ==========================================
# Organization Routes
# ==========================================

@org_router.get("", response_model=OrganizationResponse)
async def get_organization(current_user: CurrentUser = Depends(get_current_user)):
    """Get current organization details"""
    return await get_organization_data(current_user=current_user)


@org_router.put("", response_model=OrganizationResponse)
async def update_organization(
    data: OrganizationUpdate,
    current_user: CurrentUser = Depends(require_role(["admin"]))
):
    """Update organization (admin only)"""
    return await update_organization_record(
        data=data,
        current_user=current_user,
    )


@org_router.get("/stats", response_model=OrganizationStats)
async def get_organization_stats(current_user: CurrentUser = Depends(get_current_user)):
    """Get organization statistics"""
    return await get_organization_stats_data(current_user=current_user)


@org_router.get("/settings")
async def get_organization_settings(current_user: CurrentUser = Depends(get_current_user)):
    """Get organization settings including default threshold"""
    return await get_organization_settings_data(current_user=current_user)


@org_router.put("/threshold", response_model=SuccessResponse)
async def update_threshold_and_rescan(
    request: ThresholdUpdateRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update threshold and automatically rescan all watchlist items"""
    payload = await prepare_organization_threshold_rescan(
        threshold=request.threshold,
        current_user=current_user,
    )

    for item_id in payload["item_ids"]:
        background_tasks.add_task(run_watchlist_scan_task, UUID(str(item_id)))

    return SuccessResponse(message=payload["message"])



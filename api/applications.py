"""
Trademark Applications API
===========================
Endpoints for managing trademark registration applications.

Endpoints:
- POST /applications/            - Create new application (draft)
- GET  /applications/            - List applications (paginated, filterable)
- GET  /applications/{id}        - Get single application
- PUT  /applications/{id}        - Update draft application
- DELETE /applications/{id}      - Delete draft application
- POST /applications/{id}/submit - Submit draft for review
- POST /applications/{id}/logo   - Upload logo file
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query, HTTPException, Depends, UploadFile, File
from fastapi.responses import FileResponse

from auth.authentication import CurrentUser, get_current_user
from models.schemas import (
    TrademarkApplicationCreate,
    TrademarkApplicationUpdate,
    TrademarkApplicationResponse,
)
from services.application_service import (
    create_application_data,
    delete_application_data,
    get_application_data,
    get_application_logo_file,
    list_applications_data,
    submit_application_data,
    upload_application_logo_data,
    update_application_data,
)
from utils.subscription import check_application_eligibility

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])


@router.post("/", response_model=TrademarkApplicationResponse)
async def create_application(
    data: TrademarkApplicationCreate,
    user: CurrentUser = Depends(get_current_user),
):
    """Create a new trademark application (starts as draft)."""
    return await create_application_data(
        data=data,
        user=user,
        eligibility_checker=check_application_eligibility,
    )


@router.get("/")
async def list_applications(
    status: Optional[str] = Query(None, description="Filter by status"),
    application_type: Optional[str] = Query(None, description="Filter by type: registration, appeal, renewal"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
):
    """List applications for the user's organization."""
    return await list_applications_data(
        organization_id=user.organization_id,
        status=status,
        application_type=application_type,
        page=page,
        page_size=page_size,
    )


@router.get("/{app_id}", response_model=TrademarkApplicationResponse)
async def get_application(
    app_id: UUID,
    user: CurrentUser = Depends(get_current_user),
):
    """Get a single application by ID."""
    return await get_application_data(
        app_id=app_id,
        organization_id=user.organization_id,
    )


@router.put("/{app_id}", response_model=TrademarkApplicationResponse)
async def update_application(
    app_id: UUID,
    data: TrademarkApplicationUpdate,
    user: CurrentUser = Depends(get_current_user),
):
    """Update a draft application. Only drafts can be edited."""
    return await update_application_data(
        app_id=app_id,
        data=data,
        user=user,
    )


@router.delete("/{app_id}")
async def delete_application(
    app_id: UUID,
    user: CurrentUser = Depends(get_current_user),
):
    """Delete a draft application."""
    return await delete_application_data(
        app_id=app_id,
        user=user,
    )


@router.post("/{app_id}/submit", response_model=TrademarkApplicationResponse)
async def submit_application(
    app_id: UUID,
    user: CurrentUser = Depends(get_current_user),
):
    """Submit a draft application for specialist review."""
    return await submit_application_data(
        app_id=app_id,
        user=user,
    )


@router.post("/{app_id}/logo")
async def upload_application_logo(
    app_id: UUID,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    """Upload a logo for a trademark application."""
    return await upload_application_logo_data(
        app_id=app_id,
        file=file,
        user=user,
    )


@router.get("/{app_id}/logo")
async def get_application_logo(
    app_id: UUID,
    user: CurrentUser = Depends(get_current_user),
):
    """Serve the application logo."""
    logo_path = await get_application_logo_file(
        app_id=app_id,
        user=user,
    )
    return FileResponse(logo_path)

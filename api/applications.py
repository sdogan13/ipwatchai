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
import os
import shutil
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query, HTTPException, Depends, UploadFile, File
from fastapi.responses import FileResponse

from auth.authentication import CurrentUser, get_current_user
from database.crud import Database, ApplicationCRUD
from models.schemas import (
    TrademarkApplicationCreate,
    TrademarkApplicationUpdate,
    TrademarkApplicationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])

UPLOAD_DIR = Path("static/uploads/applications")


@router.post("/", response_model=TrademarkApplicationResponse)
async def create_application(
    data: TrademarkApplicationCreate,
    user: CurrentUser = Depends(get_current_user),
):
    """Create a new trademark application (starts as draft)."""
    from utils.subscription import check_application_eligibility

    with Database() as db:
        can_create, reason, details = check_application_eligibility(
            db, str(user.id), str(user.organization_id)
        )
        if not can_create:
            raise HTTPException(status_code=403, detail=details)

        row = ApplicationCRUD.create(db, user.organization_id, user.id, data)
        return TrademarkApplicationResponse(**row)


@router.get("/")
async def list_applications(
    status: Optional[str] = Query(None, description="Filter by status"),
    application_type: Optional[str] = Query(None, description="Filter by type: registration, appeal, renewal"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
):
    """List applications for the user's organization."""
    with Database() as db:
        rows, total = ApplicationCRUD.get_by_organization(
            db, user.organization_id,
            status=status, application_type=application_type,
            page=page, page_size=page_size
        )
        import math
        items = [TrademarkApplicationResponse(**r) for r in rows]
        return {
            "items": [item.dict() for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": math.ceil(total / page_size) if total > 0 else 0,
        }


@router.get("/{app_id}", response_model=TrademarkApplicationResponse)
async def get_application(
    app_id: UUID,
    user: CurrentUser = Depends(get_current_user),
):
    """Get a single application by ID."""
    with Database() as db:
        row = ApplicationCRUD.get_by_id(db, app_id, user.organization_id)
        if not row:
            raise HTTPException(status_code=404, detail="Application not found")
        return TrademarkApplicationResponse(**row)


@router.put("/{app_id}", response_model=TrademarkApplicationResponse)
async def update_application(
    app_id: UUID,
    data: TrademarkApplicationUpdate,
    user: CurrentUser = Depends(get_current_user),
):
    """Update a draft application. Only drafts can be edited."""
    with Database() as db:
        try:
            row = ApplicationCRUD.update(db, app_id, user.organization_id, data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not row:
            raise HTTPException(status_code=404, detail="Application not found")
        return TrademarkApplicationResponse(**row)


@router.delete("/{app_id}")
async def delete_application(
    app_id: UUID,
    user: CurrentUser = Depends(get_current_user),
):
    """Delete a draft application."""
    with Database() as db:
        deleted = ApplicationCRUD.delete(db, app_id, user.organization_id)
        if not deleted:
            raise HTTPException(
                status_code=400,
                detail="Application not found or is not a draft"
            )
        # Clean up uploaded logo if any
        logo_dir = UPLOAD_DIR / str(app_id)
        if logo_dir.exists():
            shutil.rmtree(logo_dir, ignore_errors=True)
        return {"success": True, "message": "Application deleted"}


@router.post("/{app_id}/submit", response_model=TrademarkApplicationResponse)
async def submit_application(
    app_id: UUID,
    user: CurrentUser = Depends(get_current_user),
):
    """Submit a draft application for specialist review."""
    with Database() as db:
        row = ApplicationCRUD.get_by_id(db, app_id, user.organization_id)
        if not row:
            raise HTTPException(status_code=404, detail="Application not found")
        if row['status'] != 'draft':
            raise HTTPException(status_code=400, detail="Only draft applications can be submitted")

        # Validate required fields for submission
        errors = []
        if not row.get('brand_name'):
            errors.append("brand_name")
        if not row.get('nice_class_numbers'):
            errors.append("nice_class_numbers")
        if not row.get('applicant_full_name'):
            errors.append("applicant_full_name")
        if not row.get('applicant_id_no'):
            errors.append("applicant_id_no")
        if not row.get('applicant_address'):
            errors.append("applicant_address")
        if not row.get('applicant_phone'):
            errors.append("applicant_phone")
        if not row.get('applicant_email'):
            errors.append("applicant_email")
        if errors:
            raise HTTPException(
                status_code=422,
                detail={"message": "Missing required fields for submission", "fields": errors}
            )

        updated = ApplicationCRUD.update_status(db, app_id, user.organization_id, 'submitted')
        return TrademarkApplicationResponse(**updated)


@router.post("/{app_id}/logo")
async def upload_application_logo(
    app_id: UUID,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    """Upload a logo for a trademark application."""
    # Validate file type
    allowed = {'image/png', 'image/jpeg', 'image/webp'}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Only PNG, JPG, and WEBP images are allowed")

    # Validate file size (5MB max)
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size must be under 5MB")

    with Database() as db:
        row = ApplicationCRUD.get_by_id(db, app_id, user.organization_id)
        if not row:
            raise HTTPException(status_code=404, detail="Application not found")

        # Save file
        app_dir = UPLOAD_DIR / str(app_id)
        app_dir.mkdir(parents=True, exist_ok=True)

        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'png'
        logo_filename = f"logo.{ext}"
        logo_path = app_dir / logo_filename

        with open(logo_path, 'wb') as f:
            f.write(contents)

        # Update DB
        relative_path = str(logo_path).replace('\\', '/')
        updated = ApplicationCRUD.update_logo(db, app_id, user.organization_id, relative_path)
        return {
            "success": True,
            "logo_url": f"/api/v1/applications/{app_id}/logo",
            "logo_path": relative_path,
        }


@router.get("/{app_id}/logo")
async def get_application_logo(
    app_id: UUID,
    user: CurrentUser = Depends(get_current_user),
):
    """Serve the application logo."""
    with Database() as db:
        row = ApplicationCRUD.get_by_id(db, app_id, user.organization_id)
        if not row or not row.get('logo_path'):
            raise HTTPException(status_code=404, detail="Logo not found")

        logo_path = Path(row['logo_path'])
        if not logo_path.exists():
            raise HTTPException(status_code=404, detail="Logo file not found")

        return FileResponse(logo_path)

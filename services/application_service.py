"""Service helpers for trademark application flows."""

import math
import shutil
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, UploadFile

from config.settings import settings
from database.crud import ApplicationCRUD, Database
from models.schemas import (
    TrademarkApplicationCreate,
    TrademarkApplicationResponse,
    TrademarkApplicationUpdate,
)


APPLICATION_UPLOAD_DIR = Path(settings.paths.upload_dir) / "applications"
ALLOWED_APPLICATION_LOGO_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_APPLICATION_LOGO_BYTES = 5 * 1024 * 1024
SUBMISSION_REQUIRED_FIELDS = (
    "brand_name",
    "nice_class_numbers",
    "applicant_full_name",
    "applicant_id_no",
    "applicant_address",
    "applicant_phone",
    "applicant_email",
)


async def create_application_data(
    *,
    data: TrademarkApplicationCreate,
    user,
    db_factory=Database,
    eligibility_checker=None,
    application_crud=ApplicationCRUD,
):
    """Create a new application when subscription limits allow it."""
    checker = eligibility_checker
    if checker is None:
        from utils.subscription import check_application_eligibility

        checker = check_application_eligibility

    with db_factory() as db:
        can_create, _reason, details = checker(
            db,
            str(user.id),
            str(user.organization_id),
        )
        if not can_create:
            raise HTTPException(status_code=403, detail=details)

        row = application_crud.create(db, user.organization_id, user.id, data)

    return TrademarkApplicationResponse(**row)


async def list_applications_data(
    *,
    organization_id: UUID,
    status=None,
    application_type=None,
    registry_kind=None,
    page: int = 1,
    page_size: int = 20,
    db_factory=Database,
):
    """Return a paginated application list for an organization.

    registry_kind filters by registry ('trademark' default if None).
    """
    with db_factory() as db:
        rows, total = ApplicationCRUD.get_by_organization(
            db,
            organization_id,
            status=status,
            application_type=application_type,
            registry_kind=registry_kind,
            page=page,
            page_size=page_size,
        )

    items = [TrademarkApplicationResponse(**row) for row in rows]
    return {
        "items": [item.dict() for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size) if total > 0 else 0,
    }


async def get_application_data(
    *,
    app_id: UUID,
    organization_id: UUID,
    db_factory=Database,
):
    """Return a single application scoped to an organization."""
    with db_factory() as db:
        row = ApplicationCRUD.get_by_id(db, app_id, organization_id)

    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return TrademarkApplicationResponse(**row)


async def update_application_data(
    *,
    app_id: UUID,
    data: TrademarkApplicationUpdate,
    user,
    db_factory=Database,
    application_crud=ApplicationCRUD,
):
    """Update a draft application and convert CRUD errors into HTTP errors."""
    with db_factory() as db:
        try:
            row = application_crud.update(db, app_id, user.organization_id, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return TrademarkApplicationResponse(**row)


async def delete_application_data(
    *,
    app_id: UUID,
    user,
    db_factory=Database,
    application_crud=ApplicationCRUD,
    upload_dir: Path | None = None,
    tree_remover=None,
):
    """Delete a draft application and clean up any uploaded logo directory."""
    root = upload_dir or APPLICATION_UPLOAD_DIR
    remover = tree_remover or shutil.rmtree

    with db_factory() as db:
        deleted = application_crud.delete(db, app_id, user.organization_id)

    if not deleted:
        raise HTTPException(
            status_code=400,
            detail="Application not found or is not a draft",
        )

    logo_dir = root / str(app_id)
    if logo_dir.exists():
        remover(logo_dir, ignore_errors=True)

    return {"success": True, "message": "Application deleted"}


async def submit_application_data(
    *,
    app_id: UUID,
    user,
    db_factory=Database,
    application_crud=ApplicationCRUD,
):
    """Validate and submit a draft application for specialist review."""
    with db_factory() as db:
        row = application_crud.get_by_id(db, app_id, user.organization_id)
        if not row:
            raise HTTPException(status_code=404, detail="Application not found")
        if row["status"] != "draft":
            raise HTTPException(
                status_code=400,
                detail="Only draft applications can be submitted",
            )

        missing_fields = [
            field for field in SUBMISSION_REQUIRED_FIELDS if not row.get(field)
        ]
        if missing_fields:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Missing required fields for submission",
                    "fields": missing_fields,
                },
            )

        updated = application_crud.update_status(
            db,
            app_id,
            user.organization_id,
            "submitted",
        )

    if not updated:
        raise HTTPException(status_code=404, detail="Application not found")
    return TrademarkApplicationResponse(**updated)


async def upload_application_logo_data(
    *,
    app_id: UUID,
    file: UploadFile,
    user,
    db_factory=Database,
    application_crud=ApplicationCRUD,
    upload_dir: Path | None = None,
):
    """Validate, store, and register an application logo upload."""
    if file.content_type not in ALLOWED_APPLICATION_LOGO_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Only PNG, JPG, and WEBP images are allowed",
        )

    contents = await file.read()
    if len(contents) > MAX_APPLICATION_LOGO_BYTES:
        raise HTTPException(
            status_code=400,
            detail="File size must be under 5MB",
        )

    root = upload_dir or APPLICATION_UPLOAD_DIR

    with db_factory() as db:
        row = application_crud.get_by_id(db, app_id, user.organization_id)
        if not row:
            raise HTTPException(status_code=404, detail="Application not found")

        app_dir = root / str(app_id)
        app_dir.mkdir(parents=True, exist_ok=True)

        filename = file.filename or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
        logo_path = app_dir / f"logo.{ext}"
        logo_path.write_bytes(contents)

        relative_path = str(logo_path).replace("\\", "/")
        application_crud.update_logo(
            db,
            app_id,
            user.organization_id,
            relative_path,
        )

    return {
        "success": True,
        "logo_url": f"/api/v1/applications/{app_id}/logo",
        "logo_path": relative_path,
    }


async def get_application_logo_file(
    *,
    app_id: UUID,
    user,
    db_factory=Database,
    application_crud=ApplicationCRUD,
):
    """Resolve an existing application logo file for download."""
    with db_factory() as db:
        row = application_crud.get_by_id(db, app_id, user.organization_id)

    if not row or not row.get("logo_path"):
        raise HTTPException(status_code=404, detail="Logo not found")

    logo_path = Path(row["logo_path"])
    if not logo_path.exists():
        raise HTTPException(status_code=404, detail="Logo file not found")

    return logo_path

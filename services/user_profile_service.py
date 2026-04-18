"""Service helpers for user profile and user management flows."""

from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from auth.authentication import hash_password, verify_password
from config.settings import settings
from database.crud import Database, OrganizationCRUD, UserCRUD
from models.schemas import SuccessResponse, UserResponse


AVATAR_UPLOAD_DIR = Path(settings.paths.upload_dir) / "avatars"
ALLOWED_AVATAR_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}
MAX_AVATAR_BYTES = 5 * 1024 * 1024


async def get_user_profile_data(
    *,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
):
    """Return the authenticated user's profile payload."""
    with db_factory() as db:
        user = user_crud.get_by_id(db, current_user.id)

    return {
        "id": str(user["id"]),
        "email": user.get("email", ""),
        "first_name": user.get("first_name", ""),
        "last_name": user.get("last_name", ""),
        "phone": user.get("phone", ""),
        "title": user.get("title", ""),
        "department": user.get("department", ""),
        "linkedin": user.get("linkedin", ""),
        "avatar_url": user.get("avatar_url", ""),
        "created_at": user.get("created_at").isoformat()
        if user.get("created_at")
        else None,
        "is_email_verified": bool(user.get("is_email_verified", False)),
    }


async def update_user_profile_data(
    *,
    data,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
    password_hasher=hash_password,
    password_verifier=verify_password,
):
    """Update the authenticated user's editable profile fields."""
    try:
        with db_factory() as db:
            current_user_data = user_crud.get_by_id(db, current_user.id)

            update_data = {}
            if data.first_name is not None:
                update_data["first_name"] = data.first_name
            if data.last_name is not None:
                update_data["last_name"] = data.last_name
            if data.email is not None and data.email != current_user_data.get("email"):
                existing = user_crud.get_by_email(db, data.email)
                if existing and str(existing["id"]) != str(current_user.id):
                    raise HTTPException(
                        status_code=400,
                        detail="Bu e-posta adresi zaten kullaniliyor",
                    )
                update_data["email"] = data.email
            if data.phone is not None:
                update_data["phone"] = data.phone
            if data.title is not None:
                update_data["title"] = data.title
            if data.department is not None:
                update_data["department"] = data.department
            if data.linkedin is not None:
                update_data["linkedin"] = data.linkedin
            if data.avatar_url is not None:
                update_data["avatar_url"] = data.avatar_url

            if data.new_password:
                if not data.current_password:
                    raise HTTPException(status_code=400, detail="Mevcut sifre gerekli")

                if not password_verifier(
                    data.current_password,
                    current_user_data["password_hash"],
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="Mevcut sifre yanlis",
                    )

                update_data["password_hash"] = password_hasher(data.new_password)

            if update_data:
                user_crud.update(db, current_user.id, update_data)

        return {"success": True, "message": "Profil guncellendi"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sunucu hatasi: {str(exc)}")


async def upload_avatar_data(
    *,
    file: UploadFile,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
    avatar_dir: Path | None = None,
    uuid_factory=uuid4,
):
    """Validate, store, and register a user avatar upload."""
    if file.content_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Sadece resim dosyalari yuklenebilir (JPEG, PNG, GIF, WebP)",
        )

    contents = await file.read()
    if len(contents) > MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=400,
            detail="Dosya boyutu 5MB'dan buyuk olamaz",
        )

    upload_dir = avatar_dir or AVATAR_UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename or "").suffix.lstrip(".") or "jpg"
    filename = f"{current_user.id}_{uuid_factory().hex[:8]}.{suffix}"
    filepath = upload_dir / filename
    filepath.write_bytes(contents)

    avatar_url = f"/static/avatars/{filename}"

    with db_factory() as db:
        user_crud.update(db, current_user.id, {"avatar_url": avatar_url})

    return {"success": True, "avatar_url": avatar_url}


async def get_user_organization_data(
    *,
    current_user,
    db_factory=Database,
    organization_crud=OrganizationCRUD,
):
    """Return the authenticated user's organization payload."""
    with db_factory() as db:
        org = organization_crud.get_by_id(db, current_user.organization_id)

    return {
        "id": str(org["id"]),
        "name": org.get("name", ""),
        "tax_id": org.get("tax_id", ""),
        "industry": org.get("industry", ""),
        "address": org.get("address", ""),
        "phone": org.get("phone", ""),
        "website": org.get("website", ""),
        "risk_threshold": org.get("default_alert_threshold", 0.7),
        "email_notifications": org.get("email_notifications", True),
        "weekly_report": org.get("weekly_report", True),
    }


async def update_user_organization_data(
    *,
    data,
    current_user,
    db_factory=Database,
    organization_crud=OrganizationCRUD,
):
    """Update the current organization profile/settings."""
    with db_factory() as db:
        update_data = {}
        if data.name is not None:
            update_data["name"] = data.name
        if data.tax_id is not None:
            update_data["tax_id"] = data.tax_id
        if data.industry is not None:
            update_data["industry"] = data.industry
        if data.address is not None:
            update_data["address"] = data.address
        if data.phone is not None:
            update_data["phone"] = data.phone
        if data.website is not None:
            update_data["website"] = data.website
        if data.risk_threshold is not None:
            update_data["default_alert_threshold"] = data.risk_threshold
        if data.email_notifications is not None:
            update_data["email_notifications"] = data.email_notifications
        if data.weekly_report is not None:
            update_data["weekly_report"] = data.weekly_report

        if update_data:
            organization_crud.update(db, current_user.organization_id, update_data)
            db.commit()

    return {"success": True, "message": "Sirket bilgileri guncellendi"}


async def list_users_data(
    *,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
):
    """List active users for the current organization."""
    with db_factory() as db:
        users = user_crud.get_by_organization(db, current_user.organization_id)

    return [UserResponse(**user) for user in users]


async def create_user_data(
    *,
    data,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
):
    """Create a new user within the current organization."""
    with db_factory() as db:
        try:
            user = user_crud.create(db, current_user.organization_id, data)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )

    return UserResponse(**user)


async def get_user_data(
    *,
    user_id,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
):
    """Return an organization-scoped user record."""
    with db_factory() as db:
        user = user_crud.get_by_id(db, user_id)

    if not user or user["organization_id"] != str(current_user.organization_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserResponse(**user)


async def update_user_record(
    *,
    user_id,
    data,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
):
    """Update a user when the actor is the owner or an org admin."""
    if user_id != current_user.id and current_user.role not in ["admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized",
        )

    with db_factory() as db:
        user = user_crud.update(db, user_id, data)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserResponse(**user)


async def deactivate_user_data(
    *,
    user_id,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
):
    """Deactivate a user in the current organization."""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate yourself",
        )

    with db_factory() as db:
        user_crud.deactivate(db, user_id)

    return SuccessResponse(message="User deactivated")

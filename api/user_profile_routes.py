"""
User Profile & User Management Routes
Self-service profile, avatar upload, organization profile, and admin user management
"""
import os
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from pydantic import BaseModel as PydanticBaseModel

from config.settings import settings
from auth.authentication import (
    CurrentUser, get_current_user, require_role,
    hash_password, verify_password,
)
from models.schemas import (
    OrganizationCreate, OrganizationUpdate, OrganizationResponse, OrganizationStats,
    UserCreate, UserUpdate, UserResponse, UserRole,
    SuccessResponse,
)
from database.crud import (
    Database, OrganizationCRUD, UserCRUD,
)

logger = logging.getLogger(__name__)


# ==========================================
# Request Models
# ==========================================

class ProfileUpdateRequest(PydanticBaseModel):
    """Request model for profile update"""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    department: Optional[str] = None
    linkedin: Optional[str] = None
    avatar_url: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None


class OrganizationProfileUpdate(PydanticBaseModel):
    """Request model for organization profile update"""
    name: Optional[str] = None
    tax_id: Optional[str] = None
    industry: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    risk_threshold: Optional[float] = None
    email_notifications: Optional[bool] = None
    weekly_report: Optional[bool] = None


# ==========================================
# Router Instances
# ==========================================

user_profile_router = APIRouter(prefix="/user", tags=["User Profile"])
users_router = APIRouter(prefix="/users", tags=["Users"])


# ==========================================
# User Profile Routes (Self-service)
# ==========================================

@user_profile_router.get("/profile")
async def get_user_profile(current_user: CurrentUser = Depends(get_current_user)):
    """Get current user's profile information"""
    with Database() as db:
        user = UserCRUD.get_by_id(db, current_user.id)
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
            "created_at": user.get("created_at").isoformat() if user.get("created_at") else None,
            "is_email_verified": bool(user.get("is_email_verified", False))
        }


@user_profile_router.put("/profile")
async def update_user_profile(
    data: ProfileUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update current user's profile"""
    try:
        with Database() as db:
            # Get current user data to compare email
            current_user_data = UserCRUD.get_by_id(db, current_user.id)

            # Build update fields
            update_data = {}
            if data.first_name is not None:
                update_data["first_name"] = data.first_name
            if data.last_name is not None:
                update_data["last_name"] = data.last_name
            # Only update email if it changed (avoid unique constraint violation)
            if data.email is not None and data.email != current_user_data.get("email"):
                # Check if new email is already taken
                existing = UserCRUD.get_by_email(db, data.email)
                if existing and str(existing["id"]) != str(current_user.id):
                    raise HTTPException(status_code=400, detail="Bu e-posta adresi zaten kullaniliyor")
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

            # Handle password change
            if data.new_password:
                if not data.current_password:
                    raise HTTPException(status_code=400, detail="Mevcut sifre gerekli")

                if not verify_password(data.current_password, current_user_data["password_hash"]):
                    raise HTTPException(status_code=400, detail="Mevcut sifre yanlis")

                update_data["password_hash"] = hash_password(data.new_password)

            # Update user
            if update_data:
                UserCRUD.update(db, current_user.id, update_data)

            return {"success": True, "message": "Profil guncellendi"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sunucu hatasi: {str(e)}")


@user_profile_router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Upload user avatar image"""
    import uuid as uuid_module

    # Validate file type
    allowed_types = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Sadece resim dosyalari yuklenebilir (JPEG, PNG, GIF, WebP)")

    # Validate file size (max 5MB)
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Dosya boyutu 5MB'dan buyuk olamaz")

    # Create uploads directory if not exists
    upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'avatars')
    os.makedirs(upload_dir, exist_ok=True)

    # Generate unique filename
    ext = file.filename.split('.')[-1] if '.' in file.filename else 'jpg'
    filename = f"{current_user.id}_{uuid_module.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join(upload_dir, filename)

    # Save file
    with open(filepath, 'wb') as f:
        f.write(contents)

    # Generate URL
    avatar_url = f"/static/avatars/{filename}"

    # Update user's avatar_url in database
    with Database() as db:
        UserCRUD.update(db, current_user.id, {"avatar_url": avatar_url})

    return {"success": True, "avatar_url": avatar_url}


@user_profile_router.get("/organization")
async def get_user_organization(current_user: CurrentUser = Depends(get_current_user)):
    """Get current user's organization information"""
    with Database() as db:
        org = OrganizationCRUD.get_by_id(db, current_user.organization_id)
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
            "weekly_report": org.get("weekly_report", True)
        }


@user_profile_router.put("/organization")
async def update_user_organization(
    data: OrganizationProfileUpdate,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update organization settings (for org admins/owners)"""
    with Database() as db:
        # Build update fields
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

        # Update organization
        if update_data:
            OrganizationCRUD.update(db, current_user.organization_id, update_data)
            db.commit()

        return {"success": True, "message": "Sirket bilgileri guncellendi"}


# ==========================================
# User Management Routes
# ==========================================

@users_router.get("", response_model=List[UserResponse])
async def list_users(
    current_user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """List all users in organization (admin only)"""
    with Database() as db:
        users = UserCRUD.get_by_organization(db, current_user.organization_id)
        return [UserResponse(**u) for u in users]


@users_router.post("", response_model=UserResponse)
async def create_user(
    data: UserCreate,
    current_user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """Create new user in organization (admin only)"""
    with Database() as db:
        try:
            user = UserCRUD.create(db, current_user.organization_id, data)
            return UserResponse(**user)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@users_router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    current_user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """Get user details (admin only)"""
    with Database() as db:
        user = UserCRUD.get_by_id(db, user_id)
        if not user or user['organization_id'] != str(current_user.organization_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return UserResponse(**user)


@users_router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    data: UserUpdate,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update user (self or admin)"""
    # Users can update themselves, admins can update anyone in org
    if user_id != current_user.id and current_user.role not in ["owner", "admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    
    with Database() as db:
        user = UserCRUD.update(db, user_id, data)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return UserResponse(**user)


@users_router.delete("/{user_id}", response_model=SuccessResponse)
async def deactivate_user(
    user_id: UUID,
    current_user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """Deactivate user (admin only)"""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate yourself"
        )
    
    with Database() as db:
        UserCRUD.deactivate(db, user_id)
        return SuccessResponse(message="User deactivated")

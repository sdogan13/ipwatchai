"""
User Profile & User Management Routes
Self-service profile, avatar upload, organization profile, and admin user management
"""

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel as PydanticBaseModel

from auth.authentication import CurrentUser, get_current_user, require_role
from models.schemas import SuccessResponse, UserCreate, UserResponse, UserUpdate
from services.user_profile_service import (
    create_user_data,
    deactivate_user_data,
    get_user_data,
    get_user_organization_data,
    get_user_profile_data,
    list_users_data,
    update_user_organization_data,
    update_user_profile_data,
    update_user_record,
    upload_avatar_data,
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
    return await get_user_profile_data(current_user=current_user)


@user_profile_router.put("/profile")
async def update_user_profile(
    data: ProfileUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update current user's profile"""
    return await update_user_profile_data(data=data, current_user=current_user)


@user_profile_router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upload user avatar image"""
    return await upload_avatar_data(file=file, current_user=current_user)


@user_profile_router.get("/organization")
async def get_user_organization(current_user: CurrentUser = Depends(get_current_user)):
    """Get current user's organization information"""
    return await get_user_organization_data(current_user=current_user)


@user_profile_router.put("/organization")
async def update_user_organization(
    data: OrganizationProfileUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update organization settings (for org admins/owners)"""
    return await update_user_organization_data(data=data, current_user=current_user)


# ==========================================
# User Management Routes
# ==========================================

@users_router.get("", response_model=List[UserResponse])
async def list_users(current_user: CurrentUser = Depends(require_role(["admin"]))):
    """List all users in organization (admin only)"""
    return await list_users_data(current_user=current_user)


@users_router.post("", response_model=UserResponse)
async def create_user(
    data: UserCreate,
    current_user: CurrentUser = Depends(require_role(["admin"])),
):
    """Create new user in organization (admin only)"""
    return await create_user_data(data=data, current_user=current_user)


@users_router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    current_user: CurrentUser = Depends(require_role(["admin"])),
):
    """Get user details (admin only)"""
    return await get_user_data(user_id=user_id, current_user=current_user)


@users_router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    data: UserUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update user (self or admin)"""
    return await update_user_record(
        user_id=user_id,
        data=data,
        current_user=current_user,
    )


@users_router.delete("/{user_id}", response_model=SuccessResponse)
async def deactivate_user(
    user_id: UUID,
    current_user: CurrentUser = Depends(require_role(["admin"])),
):
    """Deactivate user (admin only)"""
    return await deactivate_user_data(user_id=user_id, current_user=current_user)

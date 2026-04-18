"""
Authentication Routes
Login, register, password management, email verification
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel as PydanticBaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from auth.authentication import (
    CurrentUser,
    PasswordChange,
    PasswordReset,
    PasswordResetConfirm,
    TokenPair,
    UserRegister,
    VerifyEmailRequest,
    get_current_user,
)
from models.schemas import SuccessResponse, UserProfile
from services.auth_service import (
    change_password_data,
    forgot_password_data,
    get_current_user_profile_data,
    login_user,
    refresh_token_data,
    register_user,
    resend_verification_data,
    reset_password_data,
    verify_email_data,
)
from utils.settings_manager import get_rate_limit_value

logger = logging.getLogger(__name__)

# Rate limiter (IP-based for auth)
limiter = Limiter(key_func=get_remote_address)


# ==========================================
# Router Instance
# ==========================================

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])


# ==========================================
# Authentication Routes
# ==========================================

@auth_router.post("/register", response_model=TokenPair)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.register", "5/minute"))
async def register(request: Request, data: UserRegister):
    """
    Register new user and organization.
    Creates organization if organization_name provided, otherwise joins existing.
    """
    ip = request.client.host if request.client else "unknown"
    return await register_user(data=data, ip=ip)


@auth_router.post("/login", response_model=TokenPair)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "5/minute"))
async def login(request: Request):
    """Login with email and password. Accepts JSON or form-urlencoded."""
    ip = request.client.host if request.client else "unknown"

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        body = await request.json()
        email = body.get("email", "")
        password = body.get("password", "")
    else:
        form = await request.form()
        email = form.get("username", "") or form.get("email", "")
        password = form.get("password", "")

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email and password are required",
        )

    return await login_user(email=email, password=password, ip=ip)


class RefreshTokenRequest(PydanticBaseModel):
    refresh_token: str


@auth_router.post("/refresh", response_model=TokenPair)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "5/minute"))
async def refresh_token(request: Request, data: RefreshTokenRequest):
    """
    Refresh access token using a valid refresh token.
    Does NOT require the Authorization header — the refresh token is sent in the body.
    """
    return await refresh_token_data(refresh_token=data.refresh_token)


@auth_router.post("/change-password", response_model=SuccessResponse)
async def change_password(
    data: PasswordChange,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Change password for current user"""
    return await change_password_data(data=data, current_user=current_user)


@auth_router.post("/forgot-password", response_model=SuccessResponse)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "3/minute"))
async def forgot_password(request: Request, data: PasswordReset):
    """Request a password reset. Generates a 6-digit code stored in DB."""
    return await forgot_password_data(data=data)


@auth_router.post("/reset-password", response_model=SuccessResponse)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "5/minute"))
async def reset_password(request: Request, data: PasswordResetConfirm):
    """Verify the 6-digit reset code and set a new password."""
    return await reset_password_data(data=data)


@auth_router.post("/verify-email", response_model=SuccessResponse)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "5/minute"))
async def verify_email(
    request: Request,
    data: VerifyEmailRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Verify email with 6-digit code sent during registration."""
    return await verify_email_data(data=data, current_user=current_user)


@auth_router.post("/resend-verification", response_model=SuccessResponse)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "2/minute"))
async def resend_verification(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Resend email verification code. Invalidates previous codes."""
    return await resend_verification_data(current_user=current_user)


@auth_router.get("/me", response_model=UserProfile)
async def get_current_user_profile(current_user: CurrentUser = Depends(get_current_user)):
    """Get current user profile with organization info"""
    return await get_current_user_profile_data(current_user=current_user)

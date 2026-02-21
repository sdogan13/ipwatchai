"""
API Routes
All REST endpoints for the Trademark Risk Assessment System
"""
import io
import os
import re
import logging
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID, uuid4

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, BackgroundTasks, Body, Request
from pydantic import BaseModel as PydanticBaseModel
from fastapi.responses import FileResponse, StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from utils.settings_manager import get_rate_limit_value
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from config.settings import settings
from auth.authentication import (
    CurrentUser, TokenPair, UserLogin, UserRegister, PasswordChange,
    PasswordReset, PasswordResetConfirm, VerifyEmailRequest,
    get_current_user, require_role, create_token_pair, decode_token,
    hash_password, verify_password, generate_verification_token
)
from models.schemas import (
    # Organization
    OrganizationCreate, OrganizationUpdate, OrganizationResponse, OrganizationStats,
    # User
    UserCreate, UserUpdate, UserResponse, UserProfile, UserRole,
    # Watchlist
    WatchlistItemCreate, WatchlistItemUpdate, WatchlistItemResponse,
    WatchlistBulkImport, WatchlistBulkImportResult,
    FileUploadResult, FileUploadSummary, FileUploadWarning,
    FileUploadSkippedItem, FileUploadErrorItem,
    ColumnDetectionResponse, ColumnAutoMappings, ColumnMapping,
    # Alerts
    AlertResponse, AlertUpdate, AlertAcknowledge, AlertResolve, AlertDismiss,
    AlertStatus, AlertSeverity, AlertDigest,
    # Reports
    ReportRequest, ReportResponse,
    # Common
    PaginatedResponse, SuccessResponse, DashboardStats
)
from database.crud import (
    Database, get_db_connection,
    OrganizationCRUD, UserCRUD, WatchlistCRUD, AlertCRUD
)

logger = logging.getLogger(__name__)

# Rate limiter (IP-based for auth, user-based elsewhere)
limiter = Limiter(key_func=get_remote_address)


# ==========================================
# Router Instances
# ==========================================

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])
users_router = APIRouter(prefix="/users", tags=["Users"])
user_profile_router = APIRouter(prefix="/user", tags=["User Profile"])
org_router = APIRouter(prefix="/organization", tags=["Organization"])
watchlist_router = APIRouter(prefix="/watchlist", tags=["Watchlist"])
alerts_router = APIRouter(prefix="/alerts", tags=["Alerts"])
reports_router = APIRouter(prefix="/reports", tags=["Reports"])
dashboard_router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
admin_router = APIRouter(prefix="/admin", tags=["Admin"])
trademark_router = APIRouter(prefix="/trademark", tags=["Trademark"])
usage_router = APIRouter(prefix="/usage", tags=["Usage"])


# ==========================================
# Request Models
# ==========================================

class ThresholdUpdateRequest(PydanticBaseModel):
    """Request model for threshold update"""
    threshold: float


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
# Authentication Routes
# ==========================================

@auth_router.post("/register", response_model=TokenPair)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.register", "5/minute"))
async def register(request: Request, data: UserRegister):
    """
    Register new user and organization.
    Creates organization if organization_name provided, otherwise joins existing.
    """
    with Database() as db:
        try:
            # Check if email exists
            existing = UserCRUD.get_by_email(db, data.email)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered"
                )
            
            # Create or get organization
            if data.organization_name:
                # Create new organization
                slug = data.organization_name.lower().replace(" ", "-")
                org = OrganizationCRUD.create(db, OrganizationCreate(
                    name=data.organization_name,
                    slug=slug,
                    email=data.email
                ))
                role = UserRole.OWNER
            elif data.organization_slug:
                # Join existing organization
                org = OrganizationCRUD.get_by_slug(db, data.organization_slug)
                if not org:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Organization not found"
                    )
                role = UserRole.MEMBER
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Must provide organization_name or organization_slug"
                )
            
            # Create user
            user = UserCRUD.create(db, UUID(org['id']), UserCreate(
                email=data.email,
                password=data.password,
                first_name=data.first_name,
                last_name=data.last_name,
                role=role
            ))
            
            # Generate tokens
            ip = request.client.host if request.client else "unknown"
            logger.info(f"New registration: user={user['id']} email={data.email} org={user['organization_id']} IP={ip}")

            # Generate email verification code (6-digit, same pattern as password reset)
            verification_code = f"{secrets.randbelow(1000000):06d}"
            code_hash = hashlib.sha256(verification_code.encode()).hexdigest()
            verification_expires = datetime.utcnow() + timedelta(hours=24)

            cur = db.cursor()
            cur.execute(
                """INSERT INTO email_verification_tokens (id, user_id, token_hash, expires_at, created_at)
                   VALUES (gen_random_uuid(), %s, %s, %s, NOW())""",
                (str(user['id']), code_hash, verification_expires)
            )
            db.commit()

            # Send welcome + verification email (non-blocking, don't fail registration if email fails)
            try:
                from notifications.service import EmailService
                email_svc = EmailService()
                if email_svc.is_configured():
                    email_svc.send_welcome(
                        to_email=data.email,
                        first_name=data.first_name,
                        plan_name="Free",
                        lang=getattr(data, 'lang', 'tr'),
                        verification_code=verification_code
                    )
            except Exception as e:
                logger.error(f"Failed to send welcome email to {data.email}: {e}")

            return create_token_pair(
                str(user['id']),
                str(user['organization_id']),
                user['role']
            )

        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@auth_router.post("/login", response_model=TokenPair)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "5/minute"))
async def login(request: Request):
    """Login with email and password. Accepts JSON or form-urlencoded."""
    ip = request.client.host if request.client else "unknown"

    # Parse email/password from JSON or form-urlencoded
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        body = await request.json()
        email = body.get("email", "")
        password = body.get("password", "")
    else:
        # form-urlencoded: frontend sends 'username' field (OAuth2 convention)
        form = await request.form()
        email = form.get("username", "") or form.get("email", "")
        password = form.get("password", "")

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email and password are required"
        )

    with Database() as db:
        user = UserCRUD.get_by_email(db, email)

        if not user:
            logger.warning(f"Failed login: email={email} IP={ip} reason=user_not_found")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )

        if not user['is_active']:
            logger.warning(f"Failed login: email={email} IP={ip} reason=account_deactivated")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account is deactivated"
            )

        if not verify_password(password, user['password_hash']):
            logger.warning(f"Failed login: email={email} IP={ip} reason=wrong_password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )

        # Update last login
        UserCRUD.update_login(db, UUID(user['id']))

        logger.info(f"Successful login: user={user['id']} email={email} IP={ip}")
        return create_token_pair(
            str(user['id']),
            str(user['organization_id']),
            user['role']
        )


class RefreshTokenRequest(PydanticBaseModel):
    refresh_token: str


@auth_router.post("/refresh", response_model=TokenPair)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "5/minute"))
async def refresh_token(request: Request, data: RefreshTokenRequest):
    """
    Refresh access token using a valid refresh token.
    Does NOT require the Authorization header — the refresh token is sent in the body.
    """
    from psycopg2.extras import RealDictCursor

    payload = decode_token(data.refresh_token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )

    if payload.type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type — expected refresh token"
        )

    # Verify user still exists and is active
    with Database() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT id, role, is_active FROM users WHERE id = %s",
            (payload.sub,)
        )
        user = cur.fetchone()
        if user is None or not user['is_active']:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or deactivated"
            )

        # Verify org is active
        cur.execute(
            "SELECT id, is_active FROM organizations WHERE id = %s",
            (payload.org,)
        )
        org = cur.fetchone()
        if org is None or not org['is_active']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organization is deactivated"
            )

    logger.info(f"Token refresh: user={payload.sub}")
    return create_token_pair(
        payload.sub,
        payload.org,
        user['role']
    )


@auth_router.post("/change-password", response_model=SuccessResponse)
async def change_password(
    data: PasswordChange,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Change password for current user"""
    with Database() as db:
        user = UserCRUD.get_by_email(db, current_user.email)
        
        if not verify_password(data.current_password, user['password_hash']):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect"
            )
        
        # Update password
        cur = db.cursor()
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (hash_password(data.new_password), str(current_user.id))
        )
        db.commit()
        
        return SuccessResponse(message="Password changed successfully")


@auth_router.post("/forgot-password", response_model=SuccessResponse)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "3/minute"))
async def forgot_password(request: Request, data: PasswordReset):
    """Request a password reset. Generates a 6-digit code stored in DB."""
    import secrets, hashlib
    from datetime import datetime, timedelta

    with Database() as db:
        user = UserCRUD.get_by_email(db, data.email)
        if not user:
            # Don't reveal whether the email exists
            return SuccessResponse(message="If this email is registered, a reset code has been generated.")

        # Generate a 6-digit code
        code = f"{secrets.randbelow(1000000):06d}"
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        expires = datetime.utcnow() + timedelta(minutes=15)

        cur = db.cursor()
        # Delete any existing tokens for this user
        cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (str(user['id']),))
        # Insert new token
        cur.execute(
            """INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at, created_at)
               VALUES (gen_random_uuid(), %s, %s, %s, NOW())""",
            (str(user['id']), code_hash, expires)
        )
        db.commit()

        logger.info(f"Password reset requested: email={data.email} user_id={user['id']}")

        # Send code via email
        try:
            from notifications.service import EmailService
            email_svc = EmailService()
            if email_svc.is_configured():
                email_svc.send_password_reset(to_email=data.email, code=code, lang=getattr(data, 'lang', 'tr'))
            else:
                logger.warning("SMTP not configured — password reset code not emailed")
        except Exception as e:
            logger.error(f"Failed to send password reset email: {e}")

        return SuccessResponse(message="If this email is registered, a reset code has been sent.")


@auth_router.post("/reset-password", response_model=SuccessResponse)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "5/minute"))
async def reset_password(request: Request, data: PasswordResetConfirm):
    """Verify the 6-digit reset code and set a new password."""
    import hashlib
    from datetime import datetime

    code_hash = hashlib.sha256(data.token.encode()).hexdigest()

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """SELECT prt.id, prt.user_id, prt.expires_at, prt.used_at
               FROM password_reset_tokens prt
               WHERE prt.token_hash = %s""",
            (code_hash,)
        )
        token_row = cur.fetchone()

        if not token_row:
            raise HTTPException(status_code=400, detail="Invalid or expired reset code")

        if token_row['used_at'] is not None:
            raise HTTPException(status_code=400, detail="This reset code has already been used")

        if token_row['expires_at'] < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Reset code has expired")

        # Update password
        new_hash = hash_password(data.new_password)
        cur.execute(
            "UPDATE users SET password_hash = %s, password_changed_at = NOW() WHERE id = %s",
            (new_hash, str(token_row['user_id']))
        )
        # Mark token as used
        cur.execute(
            "UPDATE password_reset_tokens SET used_at = NOW() WHERE id = %s",
            (str(token_row['id']),)
        )
        db.commit()

        logger.info(f"Password reset completed: user_id={token_row['user_id']}")
        return SuccessResponse(message="Password has been reset successfully")


@auth_router.post("/verify-email", response_model=SuccessResponse)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "5/minute"))
async def verify_email(request: Request, data: VerifyEmailRequest, current_user: CurrentUser = Depends(get_current_user)):
    """Verify email with 6-digit code sent during registration."""
    code_hash = hashlib.sha256(data.code.encode()).hexdigest()

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """SELECT evt.id, evt.user_id, evt.expires_at, evt.used_at
               FROM email_verification_tokens evt
               WHERE evt.token_hash = %s AND evt.user_id = %s""",
            (code_hash, str(current_user.id))
        )
        token_row = cur.fetchone()

        if not token_row:
            raise HTTPException(status_code=400, detail="Invalid verification code")

        if token_row['used_at'] is not None:
            raise HTTPException(status_code=400, detail="This code has already been used")

        if token_row['expires_at'] < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Verification code has expired")

        # Mark email as verified
        UserCRUD.verify_email(db, current_user.id)
        # Mark token as used
        cur.execute(
            "UPDATE email_verification_tokens SET used_at = NOW() WHERE id = %s",
            (str(token_row['id']),)
        )
        db.commit()

        logger.info(f"Email verified: user_id={current_user.id}")
        return SuccessResponse(message="Email verified successfully")


@auth_router.post("/resend-verification", response_model=SuccessResponse)
@limiter.limit(lambda: get_rate_limit_value("rate_limit.login", "2/minute"))
async def resend_verification(request: Request, current_user: CurrentUser = Depends(get_current_user)):
    """Resend email verification code. Invalidates previous codes."""
    with Database() as db:
        # Check if already verified
        user = UserCRUD.get_by_id(db, current_user.id)
        if user.get('is_email_verified'):
            return SuccessResponse(message="Email is already verified")

        cur = db.cursor()
        # Delete old verification tokens for this user
        cur.execute("DELETE FROM email_verification_tokens WHERE user_id = %s", (str(current_user.id),))

        # Generate new 6-digit code
        verification_code = f"{secrets.randbelow(1000000):06d}"
        code_hash = hashlib.sha256(verification_code.encode()).hexdigest()
        expires = datetime.utcnow() + timedelta(hours=24)

        cur.execute(
            """INSERT INTO email_verification_tokens (id, user_id, token_hash, expires_at, created_at)
               VALUES (gen_random_uuid(), %s, %s, %s, NOW())""",
            (str(current_user.id), code_hash, expires)
        )
        db.commit()

        # Send combined welcome + verification email
        try:
            from notifications.service import EmailService
            email_svc = EmailService()
            if email_svc.is_configured():
                email_svc.send_welcome(
                    to_email=current_user.email,
                    first_name=current_user.first_name or "User",
                    plan_name="Free",
                    lang="tr",
                    verification_code=verification_code
                )
        except Exception as e:
            logger.error(f"Failed to send verification email to {current_user.email}: {e}")

        logger.info(f"Verification code resent: user_id={current_user.id}")
        return SuccessResponse(message="Verification code sent")


@auth_router.get("/me", response_model=UserProfile)
async def get_current_user_profile(current_user: CurrentUser = Depends(get_current_user)):
    """Get current user profile with organization info"""
    with Database() as db:
        user = UserCRUD.get_by_id(db, current_user.id)
        org = OrganizationCRUD.get_by_id(db, current_user.organization_id)

        # Map is_email_verified to is_verified for schema compatibility
        user_data = dict(user)
        user_data['is_verified'] = user_data.pop('is_email_verified', False)

        # Enrich org with plan details from subscription_plans
        org_data = dict(org)
        if org_data.get('subscription_plan_id'):
            cur = db.cursor()
            cur.execute(
                "SELECT name, max_watchlist_items, max_api_calls_per_day FROM subscription_plans WHERE id = %s",
                (str(org_data['subscription_plan_id']),)
            )
            plan_row = cur.fetchone()
            if plan_row:
                org_data['plan'] = plan_row['name']
                org_data['max_watchlist_items'] = plan_row['max_watchlist_items']
                org_data['max_monthly_searches'] = plan_row['max_api_calls_per_day'] * 30

        # Super admins get unlimited access
        if current_user.is_superadmin:
            from utils.subscription import PLAN_FEATURES
            sa = PLAN_FEATURES['superadmin']
            org_data['plan'] = 'enterprise'
            org_data['max_watchlist_items'] = sa['max_watchlist_items']
            org_data['max_monthly_searches'] = sa['monthly_live_searches']
            org_data['max_users'] = sa['max_users']

        return UserProfile(
            **user_data,
            organization=OrganizationResponse(**org_data),
            permissions=[]
        )


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
    import os
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


# ==========================================
# Organization Routes
# ==========================================

@org_router.get("", response_model=OrganizationResponse)
async def get_organization(current_user: CurrentUser = Depends(get_current_user)):
    """Get current organization details"""
    with Database() as db:
        org = OrganizationCRUD.get_by_id(db, current_user.organization_id)
        return OrganizationResponse(**org)


@org_router.put("", response_model=OrganizationResponse)
async def update_organization(
    data: OrganizationUpdate,
    current_user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """Update organization (admin only)"""
    with Database() as db:
        org = OrganizationCRUD.update(db, current_user.organization_id, data)
        return OrganizationResponse(**org)


@org_router.get("/stats", response_model=OrganizationStats)
async def get_organization_stats(current_user: CurrentUser = Depends(get_current_user)):
    """Get organization statistics"""
    with Database() as db:
        stats = OrganizationCRUD.get_stats(db, current_user.organization_id)
        org_id = str(current_user.organization_id)
        cur = db.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(au.quick_searches), 0) + COALESCE(SUM(au.live_searches), 0) as cnt
            FROM api_usage au
            JOIN users u ON au.user_id = u.id
            WHERE u.organization_id = %s
              AND au.usage_date >= date_trunc('month', CURRENT_DATE)
        """, (org_id,))
        srch = cur.fetchone()
        return OrganizationStats(
            user_count=stats.get('user_count', 0),
            active_watchlist_items=stats.get('active_watchlist_items', 0),
            new_alerts=stats.get('new_alerts', 0),
            critical_alerts=stats.get('critical_alerts', 0),
            searches_this_month=srch['cnt'] if srch else 0,
            storage_used_mb=0.0  # TODO: Implement
        )


@org_router.get("/settings")
async def get_organization_settings(current_user: CurrentUser = Depends(get_current_user)):
    """Get organization settings including default threshold"""
    with Database() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT id, name, default_alert_threshold
            FROM organizations WHERE id = %s
        """, (str(current_user.organization_id),))
        org = cur.fetchone()

        return {
            "organization_id": str(org['id']),
            "name": org['name'],
            "default_alert_threshold": org['default_alert_threshold'] or 0.7
        }


@org_router.put("/threshold", response_model=SuccessResponse)
async def update_threshold_and_rescan(
    request: ThresholdUpdateRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update threshold and automatically rescan all watchlist items"""
    threshold = request.threshold

    # Validate threshold
    if threshold < 0.3 or threshold > 0.99:
        raise HTTPException(status_code=400, detail="Threshold must be between 0.3 and 0.99")

    with Database() as db:
        cur = db.cursor()
        org_id = str(current_user.organization_id)

        # Update organization threshold
        cur.execute("""
            UPDATE organizations SET default_alert_threshold = %s WHERE id = %s
        """, (threshold, org_id))

        # Clear ALL old alerts
        cur.execute("DELETE FROM alerts_mt WHERE organization_id = %s", (org_id,))
        deleted_alerts = cur.rowcount

        # Update all watchlist items with new threshold
        cur.execute("""
            UPDATE watchlist_mt SET alert_threshold = %s, last_scan_at = NULL
            WHERE organization_id = %s
        """, (threshold, org_id))

        # Get ALL active watchlist items (no page limit)
        # First get total count, then fetch all in one query
        _, total = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only=True, page_size=1
        )
        items, _ = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only=True, page_size=max(total, 1)
        )

        db.commit()

    if not items:
        return SuccessResponse(
            message=f"%{int(threshold * 100)} esik ayarlandi. Eski {deleted_alerts} uyari silindi. Taranacak marka yok."
        )

    # Queue fresh scans for all items
    for item in items:
        background_tasks.add_task(_scan_watchlist_item, UUID(item['id']))

    return SuccessResponse(
        message=f"%{int(threshold * 100)} esik ile {len(items)} marka taramaya alindi. Eski {deleted_alerts} uyari silindi."
    )


# ==========================================
# Watchlist Routes
# ==========================================

@watchlist_router.get("/stats")
async def watchlist_stats(
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get aggregate stats for the organization's watchlist"""
    with Database() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT
                COUNT(DISTINCT w.id) AS total_items,
                COUNT(DISTINCT w.id) FILTER (WHERE w.is_active = TRUE) AS active_items,
                COUNT(DISTINCT w.id) FILTER (WHERE a.id IS NOT NULL
                    AND a.status NOT IN ('dismissed', 'resolved')
                    AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)) AS items_with_threats,
                COUNT(a.id) FILTER (WHERE a.severity = 'critical' AND a.status NOT IN ('dismissed', 'resolved')
                    AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)) AS critical_threats,
                COUNT(a.id) FILTER (WHERE a.severity = 'high' AND a.status NOT IN ('dismissed', 'resolved')
                    AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)) AS high_threats,
                COUNT(a.id) FILTER (WHERE a.severity = 'medium' AND a.status NOT IN ('dismissed', 'resolved')
                    AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)) AS medium_threats,
                COUNT(a.id) FILTER (WHERE a.severity = 'low' AND a.status NOT IN ('dismissed', 'resolved')
                    AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)) AS low_threats,
                COUNT(a.id) FILTER (WHERE a.status = 'new'
                    AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)) AS new_alerts,
                MIN(t.appeal_deadline) FILTER (WHERE t.appeal_deadline > CURRENT_DATE
                    AND a.status NOT IN ('dismissed', 'resolved')) AS nearest_deadline
            FROM watchlist_mt w
            LEFT JOIN alerts_mt a ON w.id = a.watchlist_item_id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE w.organization_id = %s AND w.is_active = TRUE
        """, (str(current_user.organization_id),))
        row = cur.fetchone()

        nearest = row['nearest_deadline']
        nearest_days = None
        if nearest:
            from datetime import date as date_type
            nearest_days = (nearest - date_type.today()).days

        return {
            "total_items": row['total_items'],
            "active_items": row['active_items'],
            "items_with_threats": row['items_with_threats'],
            "critical_threats": row['critical_threats'],
            "high_threats": row['high_threats'],
            "medium_threats": row['medium_threats'],
            "low_threats": row['low_threats'],
            "new_alerts": row['new_alerts'],
            "nearest_deadline": nearest.isoformat() if nearest else None,
            "nearest_deadline_days": nearest_days
        }


@watchlist_router.get("", response_model=PaginatedResponse)
async def list_watchlist(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    active_only: bool = True,
    search: Optional[str] = Query(None, min_length=1, max_length=200),
    sort: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user)
):
    """List watchlist items for organization"""
    with Database() as db:
        items, total = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only, page, page_size,
            search=search, sort_by=sort
        )

        # Fetch conflict summaries for all watchlist items in one query
        conflict_summaries = {}
        item_ids = [item['id'] for item in items]
        if item_ids:
            cur = db.cursor()
            cur.execute("""
                SELECT
                    a.watchlist_item_id,
                    COUNT(*) as total_conflicts,
                    COUNT(*) FILTER (WHERE t.current_status = 'Applied' AND t.bulletin_date IS NULL) as pre_publication_count,
                    COUNT(*) FILTER (WHERE t.appeal_deadline > CURRENT_DATE AND t.appeal_deadline <= CURRENT_DATE + INTERVAL '7 days') as critical_count,
                    COUNT(*) FILTER (WHERE t.appeal_deadline > CURRENT_DATE + INTERVAL '7 days' AND t.appeal_deadline <= CURRENT_DATE + INTERVAL '30 days') as urgent_count,
                    COUNT(*) FILTER (WHERE t.appeal_deadline > CURRENT_DATE + INTERVAL '30 days') as active_count,
                    MIN(t.appeal_deadline) FILTER (WHERE t.appeal_deadline > CURRENT_DATE) as nearest_deadline,
                    MAX(CASE a.severity
                        WHEN 'critical' THEN 4
                        WHEN 'high' THEN 3
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 1
                        ELSE 0
                    END) FILTER (WHERE a.status NOT IN ('dismissed', 'resolved')) AS highest_severity_rank
                FROM alerts_mt a
                LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
                WHERE a.watchlist_item_id = ANY(%s::uuid[])
                    AND a.status NOT IN ('dismissed', 'resolved')
                    AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
                GROUP BY a.watchlist_item_id
            """, (item_ids,))
            severity_map = {4: 'critical', 3: 'high', 2: 'medium', 1: 'low'}
            for row in cur.fetchall():
                wid = str(row['watchlist_item_id'])
                nearest = row['nearest_deadline']
                days_to_nearest = None
                if nearest:
                    from datetime import date as date_type
                    today = date_type.today()
                    days_to_nearest = (nearest - today).days
                conflict_summaries[wid] = {
                    "total": row['total_conflicts'],
                    "pre_publication": row['pre_publication_count'],
                    "active_critical": row['critical_count'],
                    "active_urgent": row['urgent_count'],
                    "active": row['active_count'],
                    "nearest_deadline": nearest.isoformat() if nearest else None,
                    "nearest_deadline_days": days_to_nearest,
                    "highest_severity": severity_map.get(row['highest_severity_rank'])
                }

        response_items = []
        for item in items:
            resp = WatchlistItemResponse(**item)
            resp.conflict_summary = conflict_summaries.get(str(item['id']))
            response_items.append(resp)

        return PaginatedResponse(
            items=response_items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size
        )


@watchlist_router.post("", response_model=WatchlistItemResponse)
async def create_watchlist_item(
    data: WatchlistItemCreate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Add trademark to watchlist — auto-copies AI embeddings & logo from trademarks DB when application_no is provided."""
    # Check logo tracking eligibility
    if getattr(data, 'monitor_visual', False):
        from utils.subscription import get_user_plan, get_plan_limit
        with Database() as db_check:
            plan = get_user_plan(db_check, str(current_user.id))
            can_track = get_plan_limit(plan['plan_name'], 'can_track_logos')
            if not can_track:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": "upgrade_required", "message": "Logo tracking requires a paid plan."}
                )

    with Database() as db:
        try:
            # If application_no is provided, look up trademark AI features
            tm_ai = None
            app_no = getattr(data, 'application_no', None)
            if app_no:
                cur = db.cursor()
                cur.execute("""
                    SELECT image_path,
                           image_embedding::text, dinov2_embedding::text,
                           color_histogram::text, logo_ocr_text, text_embedding::text
                    FROM trademarks
                    WHERE application_no = %s
                    LIMIT 1
                """, (app_no,))
                tm_ai = cur.fetchone()

            if tm_ai:
                def _parse_vec(val):
                    if not val:
                        return None
                    if isinstance(val, list):
                        return val
                    s = val.strip()
                    if s.startswith('[') and s.endswith(']'):
                        return [float(x) for x in s[1:-1].split(',') if x.strip()]
                    return None

                logo_abs = None
                img_path = tm_ai.get('image_path')
                if img_path:
                    from main import find_trademark_image
                    logo_abs = find_trademark_image(img_path)

                item = WatchlistCRUD.create_with_embeddings(
                    db, current_user.organization_id, current_user.id, data,
                    logo_path=logo_abs,
                    logo_embedding=_parse_vec(tm_ai.get('image_embedding')),
                    logo_dinov2_embedding=_parse_vec(tm_ai.get('dinov2_embedding')),
                    logo_color_histogram=_parse_vec(tm_ai.get('color_histogram')),
                    logo_ocr_text=tm_ai.get('logo_ocr_text'),
                    text_embedding=_parse_vec(tm_ai.get('text_embedding')),
                )
            else:
                item = WatchlistCRUD.create(
                    db, current_user.organization_id, current_user.id, data
                )

            # Trigger initial scan in background
            background_tasks.add_task(
                _scan_watchlist_item,
                UUID(item['id'])
            )

            return WatchlistItemResponse(**item)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@watchlist_router.post("/bulk", response_model=WatchlistBulkImportResult)
async def bulk_import_watchlist(
    data: WatchlistBulkImport,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Bulk import watchlist items"""
    with Database() as db:
        # Pre-check watchlist limit
        from utils.subscription import get_plan_limit, get_user_plan
        plan_info = get_user_plan(db, str(current_user.id))
        plan_name = plan_info.get("plan_name", "free")
        max_items = get_plan_limit(plan_name, "max_watchlist_items")
        cur = db.cursor()
        cur.execute("SELECT COUNT(*) FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE",
                    (str(current_user.organization_id),))
        current_count = cur.fetchone()['count']
        remaining_slots = max(0, max_items - current_count)

        if remaining_slots == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "limit_exceeded",
                    "message": f"Izleme listesi limitinize ulastiniz ({max_items}). Daha fazla eklemek icin planinizi yukseltin.",
                    "current_count": current_count,
                    "max_items": max_items,
                }
            )

        created = 0
        failed = 0
        errors = []
        created_ids = []

        for i, item in enumerate(data.items):
            if created >= remaining_slots:
                errors.append({"index": i, "brand_name": item.brand_name,
                               "error": f"Izleme listesi limiti asildi ({max_items})"})
                failed += 1
                continue
            try:
                result = WatchlistCRUD.create(
                    db, current_user.organization_id, current_user.id, item
                )
                created += 1
                created_ids.append(UUID(result['id']))
            except Exception as e:
                failed += 1
                errors.append({"index": i, "brand_name": item.brand_name, "error": str(e)})

        # Trigger scans for all created items
        for item_id in created_ids:
            background_tasks.add_task(_scan_watchlist_item, item_id)

        return WatchlistBulkImportResult(
            total=len(data.items),
            created=created,
            failed=failed,
            errors=errors
        )


class BulkFromPortfolioRequest(PydanticBaseModel):
    holder_id: Optional[str] = None
    attorney_no: Optional[str] = None
    similarity_threshold: float = 0.70


@watchlist_router.post("/bulk-from-portfolio", response_model=WatchlistBulkImportResult)
async def bulk_import_from_portfolio(
    data: BulkFromPortfolioRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Bulk import watchlist items from a holder/attorney portfolio,
    copying AI embeddings and logo paths from the trademarks table."""
    if not data.holder_id and not data.attorney_no:
        raise HTTPException(status_code=400, detail="holder_id or attorney_no required")

    # Check portfolio access permission
    from utils.subscription import get_user_plan as _gup_port, get_plan_limit as _gpl_port
    with Database() as db_perm:
        _pplan = _gup_port(db_perm, str(current_user.id))
        if not _gpl_port(_pplan['plan_name'], 'can_view_holder_portfolio'):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "upgrade_required",
                    "message": "Portfoy erisimi icin Business veya ustu plan gereklidir.",
                    "current_plan": _pplan['plan_name'],
                }
            )

    with Database() as db:
        cur = db.cursor()

        if data.holder_id:
            where_col = "holder_tpe_client_id"
            param = data.holder_id
        else:
            where_col = "attorney_no"
            param = data.attorney_no

        # Fetch trademarks with all AI columns
        from psycopg2 import sql as psql
        cur.execute(psql.SQL("""
            SELECT application_no, name, nice_class_numbers, image_path,
                   image_embedding::text, dinov2_embedding::text,
                   color_histogram::text, logo_ocr_text, text_embedding::text
            FROM trademarks
            WHERE {} = %s
            ORDER BY application_date DESC NULLS LAST
        """).format(psql.Identifier(where_col)), (param,))
        rows = cur.fetchall()

        if not rows:
            return WatchlistBulkImportResult(total=0, created=0, failed=0, errors=[])

        def _parse_vec(val):
            if not val:
                return None
            if isinstance(val, list):
                return val
            s = val.strip()
            if s.startswith('[') and s.endswith(']'):
                return [float(x) for x in s[1:-1].split(',') if x.strip()]
            return None

        # Pre-check watchlist limit
        from utils.subscription import get_plan_limit, get_user_plan
        plan_info = get_user_plan(db, str(current_user.id))
        plan_name = plan_info.get("plan_name", "free")
        max_items = get_plan_limit(plan_name, "max_watchlist_items")
        cur.execute("SELECT COUNT(*) FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE",
                    (str(current_user.organization_id),))
        current_count = cur.fetchone()['count']
        remaining_slots = max(0, max_items - current_count)

        created = 0
        failed = 0
        errors = []
        created_ids = []
        limit_reached = False

        for i, tm in enumerate(rows):
            # Stop adding once limit is reached
            if created >= remaining_slots:
                limit_reached = True
                break

            try:
                brand = tm.get('name') or tm.get('application_no') or 'Unknown'
                classes = tm.get('nice_class_numbers') or []
                # Filter to valid Nice classes (1-45)
                classes = [c for c in classes if 1 <= c <= 45]
                if not classes:
                    classes = [1]

                item_data = WatchlistItemCreate(
                    brand_name=brand,
                    nice_class_numbers=classes,
                    application_no=tm.get('application_no'),
                    similarity_threshold=data.similarity_threshold,
                )

                # Resolve image_path to absolute filesystem path for logo_path
                logo_abs = None
                img_path = tm.get('image_path')
                if img_path:
                    from main import find_trademark_image
                    logo_abs = find_trademark_image(img_path)

                # Use SAVEPOINT so one failure doesn't abort the whole transaction
                cur.execute("SAVEPOINT sp_bulk")
                result = WatchlistCRUD.create_with_embeddings(
                    db, current_user.organization_id, current_user.id, item_data,
                    logo_path=logo_abs,
                    logo_embedding=_parse_vec(tm.get('image_embedding')),
                    logo_dinov2_embedding=_parse_vec(tm.get('dinov2_embedding')),
                    logo_color_histogram=_parse_vec(tm.get('color_histogram')),
                    logo_ocr_text=tm.get('logo_ocr_text'),
                    text_embedding=_parse_vec(tm.get('text_embedding')),
                    auto_commit=False,
                )
                cur.execute("RELEASE SAVEPOINT sp_bulk")
                created += 1
                created_ids.append(UUID(result['id']))
            except Exception as e:
                try:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_bulk")
                except Exception:
                    pass
                failed += 1
                errors.append({"index": i, "brand_name": tm.get('name', ''), "error": str(e)})

        # Commit all successful inserts
        db.commit()

        # Trigger scans for all created items
        for item_id in created_ids:
            background_tasks.add_task(_scan_watchlist_item, item_id)

        return WatchlistBulkImportResult(
            total=len(rows),
            created=created,
            failed=failed,
            errors=errors,
            limit_reached=limit_reached,
            max_allowed=max_items,
            current_count=current_count + created,
        )


# ==========================================
# Column name variants for file upload
# ==========================================

BRAND_NAME_VARIANTS = [
    'marka adı', 'marka adi', 'marka', 'trademark_name', 'trademark name',
    'brand name', 'brand_name', 'name', 'isim'
]

APP_NO_VARIANTS = [
    'başvuru no', 'başvuru numarası', 'başvuru no.',
    'basvuru no', 'basvuru numarasi', 'basvuru no.',
    'application no', 'application number', 'application_no',
    'app no', 'app_no', 'application'
]

CLASS_VARIANTS = [
    'sınıf', 'sınıflar', 'sınıf no', 'sınıf numarası',
    'sinif', 'siniflar', 'sinif no', 'sinif numarasi',
    'nice class', 'nice classes', 'nice_class', 'nice_classes',
    'class', 'classes', 'class no'
]

BULLETIN_VARIANTS = [
    'bülten no', 'bülten numarası', 'bülten',
    'bulten no', 'bulten numarasi', 'bulten',
    'bulletin no', 'bulletin number', 'bulletin'
]


def _find_column(columns: List[str], variants: List[str]) -> Optional[str]:
    """Find a column by checking against variant names."""
    for variant in variants:
        if variant in columns:
            return variant
    return None


def _parse_nice_classes(value) -> List[int]:
    """Parse Nice class values into list of integers."""
    if pd.isna(value) or not value:
        return []

    value_str = str(value)
    numbers = re.findall(r'\d+', value_str)

    classes = []
    for num in numbers:
        n = int(num)
        if 1 <= n <= 45:  # Valid Nice class range
            classes.append(n)

    return sorted(list(set(classes)))


@watchlist_router.get("/upload/template")
async def download_template():
    """Generate Excel template with mandatory columns."""

    wb = Workbook()
    ws = wb.active
    ws.title = "Marka Listesi"

    # Headers - mark required with *
    headers = [
        ("Marka Adı *", True),      # Required
        ("Başvuru No *", True),     # Required
        ("Sınıflar *", True),       # Required
        ("Bülten No", False)        # Optional
    ]

    # Style headers
    required_fill = PatternFill(start_color="DC2626", end_color="DC2626", fill_type="solid")
    optional_fill = PatternFill(start_color="0EA5E9", end_color="0EA5E9", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    for col, (header, is_required) in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = required_fill if is_required else optional_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    # Sample data
    sample_data = [
        ["ÖRNEK MARKA 1", "2023/12345", "9, 35", "305"],
        ["ÖRNEK MARKA 2", "2023/67890", "25, 35, 42", "306"],
        ["ÖRNEK MARKA 3", "2022/11111", "30, 43", ""],
    ]

    for row_idx, row_data in enumerate(sample_data, 2):
        for col_idx, value in enumerate(row_data, 1):
            ws.cell(row=row_idx, column=col_idx, value=value)

    # Instructions
    ws.cell(row=6, column=1, value="* Zorunlu sütunlar. Bülten No opsiyoneldir.")
    ws.cell(row=6, column=1).font = Font(italic=True, color="666666")

    ws.cell(row=7, column=1, value="Sınıflar: Virgülle ayırarak yazın (örn: 9, 35, 42)")
    ws.cell(row=7, column=1).font = Font(italic=True, color="666666")

    # Column widths
    ws.column_dimensions['A'].width = 25
    ws.column_dimensions['B'].width = 18
    ws.column_dimensions['C'].width = 15
    ws.column_dimensions['D'].width = 12

    # Save to BytesIO
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=marka_listesi_sablon.xlsx"}
    )


@watchlist_router.post("/upload/detect-columns", response_model=ColumnDetectionResponse)
async def detect_columns(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Read file and return column names for mapping UI."""
    contents = await file.read()
    filename = file.filename.lower() if file.filename else ""

    try:
        if filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(contents), nrows=5)
            # Count total rows
            df_count = pd.read_excel(io.BytesIO(contents), usecols=[0])
            total_rows = len(df_count)
        elif filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents), nrows=5)
            df_count = pd.read_csv(io.BytesIO(contents), usecols=[0])
            total_rows = len(df_count)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Desteklenmeyen dosya formati. Excel veya CSV yukleyin."
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dosya okunamadi: {str(e)}"
        )

    # Store original column names
    original_columns = list(df.columns)

    # Normalize for matching
    normalized_columns = [str(col).lower().strip() for col in df.columns]

    # Try auto-detect mappings
    auto_mappings = ColumnAutoMappings(
        brand_name=_find_column(normalized_columns, BRAND_NAME_VARIANTS),
        application_no=_find_column(normalized_columns, APP_NO_VARIANTS),
        nice_classes=_find_column(normalized_columns, CLASS_VARIANTS),
        bulletin_no=_find_column(normalized_columns, BULLETIN_VARIANTS)
    )

    # Map normalized back to original for the response
    norm_to_orig = {str(col).lower().strip(): str(col) for col in original_columns}

    # Use original column names in auto_mappings
    auto_mappings_orig = ColumnAutoMappings(
        brand_name=norm_to_orig.get(auto_mappings.brand_name) if auto_mappings.brand_name else None,
        application_no=norm_to_orig.get(auto_mappings.application_no) if auto_mappings.application_no else None,
        nice_classes=norm_to_orig.get(auto_mappings.nice_classes) if auto_mappings.nice_classes else None,
        bulletin_no=norm_to_orig.get(auto_mappings.bulletin_no) if auto_mappings.bulletin_no else None
    )

    # Get sample data (first 3 rows) with original column names
    df.columns = original_columns  # Restore original column names
    sample_data = df.head(3).fillna('').to_dict('records')

    # Convert all values to strings for JSON serialization
    sample_data = [
        {k: str(v) if v != '' else '' for k, v in row.items()}
        for row in sample_data
    ]

    return ColumnDetectionResponse(
        columns=original_columns,
        sample_data=sample_data,
        auto_mappings=auto_mappings_orig,
        total_rows=total_rows
    )


@watchlist_router.post("/upload/with-mapping", response_model=FileUploadResult)
async def upload_with_mapping(
    file: UploadFile = File(...),
    column_mapping: str = Form(...),
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Upload file with custom column mappings."""
    import json

    # Parse the column mapping JSON
    try:
        mappings = json.loads(column_mapping)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Gecersiz sutun eslestirme formati"
        )

    # Normalize alternative field names
    if 'nice_class_numbers' in mappings and 'nice_classes' not in mappings:
        mappings['nice_classes'] = mappings.pop('nice_class_numbers')

    # Validate required mappings (only brand_name is strictly required)
    if not mappings.get('brand_name'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Eksik zorunlu eslestirme: brand_name"
        )

    # Read file
    contents = await file.read()
    filename = file.filename.lower() if file.filename else ""

    try:
        if filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(contents))
        elif filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents))
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Desteklenmeyen dosya formati"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dosya okunamadi: {str(e)}"
        )

    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dosya bos"
        )

    # Rename columns based on user mapping
    # mappings = {'brand_name': 'User Column Name', ...}
    # We need to rename user's column name to our standard name
    rename_map = {v: k for k, v in mappings.items() if v}
    df = df.rename(columns=rename_map)

    # Now normalize all column names for consistency
    df.columns = [str(col).lower().strip() for col in df.columns]

    # Warnings for optional columns
    warnings = []
    bulletin_col = mappings.get('bulletin_no')
    if not bulletin_col:
        warnings.append(FileUploadWarning(
            column="Bulten No",
            message="Bulten numarasi sutunu eslestirme yapilmadi. Bu opsiyonel bir alandir."
        ))

    # Get organization ID
    org_id = str(current_user.organization_id)
    user_id = str(current_user.id)

    # Get existing application numbers
    with Database() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT customer_application_no
            FROM watchlist_mt
            WHERE organization_id = %s
              AND customer_application_no IS NOT NULL
              AND is_active = TRUE
        """, (org_id,))
        existing = cur.fetchall()
        existing_app_nos = {
            r['customer_application_no'].strip().lower()
            for r in existing if r['customer_application_no']
        }

    # Pre-check watchlist limit
    from utils.subscription import get_plan_limit, get_user_plan as _get_plan
    with Database() as db_lim:
        _plan = _get_plan(db_lim, user_id)
        _plan_name = _plan.get("plan_name", "free")
        _max_items = get_plan_limit(_plan_name, "max_watchlist_items")
        _cur = db_lim.cursor()
        _cur.execute("SELECT COUNT(*) FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE", (org_id,))
        _current_count = _cur.fetchone()['count']
    remaining_slots = max(0, _max_items - _current_count)

    # Process rows
    added_count = 0
    skipped_count = 0
    error_count = 0
    skipped_items = []
    error_items = []
    created_ids = []

    with Database() as db:
        cur = db.cursor()

        for idx, row in df.iterrows():
            row_num = idx + 2  # Excel row number (1-indexed + header)

            try:
                # Check watchlist limit before each insert
                if added_count >= remaining_slots:
                    error_count += 1
                    error_items.append(FileUploadErrorItem(
                        row=row_num,
                        error=f"Izleme listesi limiti asildi ({_max_items})"
                    ))
                    continue

                # Validate brand name (required)
                brand_name = str(row.get('brand_name', '')).strip()
                if not brand_name or brand_name.lower() in ['nan', 'none', '']:
                    error_count += 1
                    error_items.append(FileUploadErrorItem(
                        row=row_num,
                        error="Marka adi bos"
                    ))
                    continue

                # Application number (optional — auto-generate if missing)
                app_no = str(row.get('application_no', '')).strip()
                if not app_no or app_no.lower() in ['nan', 'none', '']:
                    app_no = f"WL-{uuid4().hex[:8].upper()}"

                # Nice classes (optional — default to empty list)
                classes_raw = row.get('nice_classes', '') if 'nice_classes' in df.columns else ''
                classes_str = str(classes_raw).strip() if classes_raw is not None else ''
                if classes_str and classes_str.lower() not in ['nan', 'none', '']:
                    nice_classes = _parse_nice_classes(classes_raw)
                else:
                    nice_classes = []

                # Bulletin number (optional)
                bulletin_no = None
                if 'bulletin_no' in df.columns:
                    bulletin_no = str(row.get('bulletin_no', '')).strip()
                    if bulletin_no.lower() in ['nan', 'none', '']:
                        bulletin_no = None

                # Check duplicate
                if app_no.lower() in existing_app_nos:
                    skipped_count += 1
                    skipped_items.append(FileUploadSkippedItem(
                        row=row_num,
                        brand_name=brand_name,
                        application_no=app_no,
                        reason="Zaten mevcut"
                    ))
                    continue

                # Insert
                item_id = uuid4()
                cur.execute("""
                    INSERT INTO watchlist_mt (
                        id, organization_id, user_id, brand_name,
                        nice_class_numbers, customer_application_no, customer_bulletin_no,
                        alert_threshold, is_active, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        0.7, TRUE, NOW(), NOW()
                    )
                """, (str(item_id), org_id, user_id, brand_name, nice_classes, app_no, bulletin_no))

                added_count += 1
                existing_app_nos.add(app_no.lower())
                created_ids.append(item_id)

            except Exception as e:
                error_count += 1
                error_items.append(FileUploadErrorItem(
                    row=row_num,
                    brand_name=brand_name if 'brand_name' in dir() else None,
                    error=str(e)[:100]
                ))

        db.commit()

    # Trigger background scans for created items
    if background_tasks and created_ids:
        for item_id in created_ids:
            background_tasks.add_task(_scan_watchlist_item, item_id)

    # Build message
    message_parts = [f"{added_count} marka eklendi"]
    if skipped_count > 0:
        message_parts.append(f"{skipped_count} zaten mevcut (atlandi)")
    if error_count > 0:
        message_parts.append(f"{error_count} hatali satir")

    return FileUploadResult(
        success=True,
        message=", ".join(message_parts),
        summary=FileUploadSummary(
            total_rows=len(df),
            added=added_count,
            skipped=skipped_count,
            errors=error_count
        ),
        warnings=warnings,
        skipped_items=skipped_items[:10],
        error_items=error_items[:10]
    )


@watchlist_router.post("/upload", response_model=FileUploadResult)
async def upload_file(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Upload Excel/CSV file with mandatory column validation."""

    # Parse file
    contents = await file.read()
    filename = file.filename.lower() if file.filename else ""

    try:
        if filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(contents))
        elif filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents))
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "unsupported_format",
                    "message": "Desteklenmeyen dosya formatı",
                    "detail": "Lütfen Excel (.xlsx, .xls) veya CSV (.csv) dosyası yükleyin."
                }
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "parse_error",
                "message": "Dosya okunamadı",
                "detail": str(e)
            }
        )

    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "empty_file",
                "message": "Dosya boş"
            }
        )

    # Store original column names for error messages
    original_columns = list(df.columns)

    # Normalize column names for matching
    df.columns = [str(col).lower().strip() for col in df.columns]

    # Find columns
    brand_col = _find_column(df.columns.tolist(), BRAND_NAME_VARIANTS)
    app_no_col = _find_column(df.columns.tolist(), APP_NO_VARIANTS)
    class_col = _find_column(df.columns.tolist(), CLASS_VARIANTS)
    bulletin_col = _find_column(df.columns.tolist(), BULLETIN_VARIANTS)

    # Validate mandatory columns
    missing_columns = []

    if not brand_col:
        missing_columns.append({
            "column": "Marka Adı",
            "variants": "marka adı, brand name, name, isim",
            "reason": "Hangi markaların izleneceğini belirler"
        })

    if not app_no_col:
        missing_columns.append({
            "column": "Başvuru No",
            "variants": "başvuru no, application no, app no",
            "reason": "Mükerrer kontrol ve çakışma filtreleme için gerekli"
        })

    if not class_col:
        missing_columns.append({
            "column": "Sınıflar",
            "variants": "sınıf, sınıflar, nice class, classes",
            "reason": "Hangi sınıflarda arama yapılacağını belirler"
        })

    if missing_columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_mandatory_columns",
                "message": f"{len(missing_columns)} zorunlu sütun eksik",
                "missing_columns": missing_columns,
                "found_columns": original_columns,
                "required_columns": [
                    {"name": "Marka Adı", "variants": "marka adı, brand name, name"},
                    {"name": "Başvuru No", "variants": "başvuru no, application no"},
                    {"name": "Sınıflar", "variants": "sınıf, sınıflar, nice class, classes"}
                ],
                "optional_columns": [
                    {"name": "Bülten No", "variants": "bülten no, bulletin no"}
                ],
                "example": {
                    "headers": ["Marka Adı", "Başvuru No", "Sınıflar", "Bülten No"],
                    "rows": [
                        ["ÖRNEK MARKA", "2023/12345", "9, 35, 42", "305"],
                        ["DİĞER MARKA", "2023/67890", "25, 35", "306"]
                    ]
                }
            }
        )

    # Warnings for optional columns
    warnings = []
    if not bulletin_col:
        warnings.append(FileUploadWarning(
            column="Bülten No",
            message="Bülten numarası sütunu bulunamadı. Bu opsiyonel bir alandır."
        ))

    # Get organization ID
    org_id = str(current_user.organization_id)
    user_id = str(current_user.id)

    # Get existing application numbers + watchlist limit
    with Database() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT customer_application_no
            FROM watchlist_mt
            WHERE organization_id = %s
              AND customer_application_no IS NOT NULL
              AND is_active = TRUE
        """, (org_id,))
        existing = cur.fetchall()
        existing_app_nos = {
            r['customer_application_no'].strip().lower()
            for r in existing if r['customer_application_no']
        }

        # Pre-check watchlist limit
        from utils.subscription import get_plan_limit, get_user_plan as _get_plan2
        _plan2 = _get_plan2(db, user_id)
        _plan_name2 = _plan2.get("plan_name", "free")
        _max_items2 = get_plan_limit(_plan_name2, "max_watchlist_items")
        cur.execute("SELECT COUNT(*) FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE", (org_id,))
        _current_count2 = cur.fetchone()['count']
    remaining_slots2 = max(0, _max_items2 - _current_count2)

    # Process rows
    added_count = 0
    skipped_count = 0
    error_count = 0
    skipped_items = []
    error_items = []
    created_ids = []

    with Database() as db:
        cur = db.cursor()

        for idx, row in df.iterrows():
            row_num = idx + 2  # Excel row number (1-indexed + header)

            try:
                # Check watchlist limit before each insert
                if added_count >= remaining_slots2:
                    error_count += 1
                    error_items.append(FileUploadErrorItem(
                        row=row_num,
                        error=f"Izleme listesi limiti asildi ({_max_items2})"
                    ))
                    continue

                # Validate brand name (required)
                brand_name = str(row.get(brand_col, '')).strip()
                if not brand_name or brand_name.lower() in ['nan', 'none', '']:
                    error_count += 1
                    error_items.append(FileUploadErrorItem(
                        row=row_num,
                        error="Marka adı boş"
                    ))
                    continue

                # Validate application number (required)
                app_no = str(row.get(app_no_col, '')).strip()
                if not app_no or app_no.lower() in ['nan', 'none', '']:
                    error_count += 1
                    error_items.append(FileUploadErrorItem(
                        row=row_num,
                        brand_name=brand_name,
                        error="Başvuru numarası boş"
                    ))
                    continue

                # Validate nice classes (required)
                classes_raw = row.get(class_col, '')
                nice_classes = _parse_nice_classes(classes_raw)
                if not nice_classes:
                    error_count += 1
                    error_items.append(FileUploadErrorItem(
                        row=row_num,
                        brand_name=brand_name,
                        error="Sınıf bilgisi boş veya geçersiz"
                    ))
                    continue

                # Bulletin number (optional)
                bulletin_no = None
                if bulletin_col and bulletin_col in row:
                    bulletin_no = str(row.get(bulletin_col, '')).strip()
                    if bulletin_no.lower() in ['nan', 'none', '']:
                        bulletin_no = None

                # Check duplicate
                if app_no.lower() in existing_app_nos:
                    skipped_count += 1
                    skipped_items.append(FileUploadSkippedItem(
                        row=row_num,
                        brand_name=brand_name,
                        application_no=app_no,
                        reason="Zaten mevcut"
                    ))
                    continue

                # Insert
                item_id = uuid4()
                cur.execute("""
                    INSERT INTO watchlist_mt (
                        id, organization_id, user_id, brand_name,
                        nice_class_numbers, customer_application_no, customer_bulletin_no,
                        alert_threshold, is_active, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        0.7, TRUE, NOW(), NOW()
                    )
                """, (str(item_id), org_id, user_id, brand_name, nice_classes, app_no, bulletin_no))

                added_count += 1
                existing_app_nos.add(app_no.lower())
                created_ids.append(item_id)

            except Exception as e:
                error_count += 1
                error_items.append(FileUploadErrorItem(
                    row=row_num,
                    brand_name=brand_name if 'brand_name' in dir() else None,
                    error=str(e)[:100]
                ))

        db.commit()

    # Trigger background scans for created items
    if background_tasks and created_ids:
        for item_id in created_ids:
            background_tasks.add_task(_scan_watchlist_item, item_id)

    # Build message
    message_parts = [f"{added_count} marka eklendi"]
    if skipped_count > 0:
        message_parts.append(f"{skipped_count} zaten mevcut (atlandı)")
    if error_count > 0:
        message_parts.append(f"{error_count} hatalı satır")

    return FileUploadResult(
        success=True,
        message=", ".join(message_parts),
        summary=FileUploadSummary(
            total_rows=len(df),
            added=added_count,
            skipped=skipped_count,
            errors=error_count
        ),
        warnings=warnings,
        skipped_items=skipped_items[:10],
        error_items=error_items[:10]
    )


@watchlist_router.post("/scan-all", response_model=SuccessResponse)
async def trigger_scan_all(
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Scan all active watchlist items for the organization"""
    with Database() as db:
        # Check auto-scan eligibility
        from utils.subscription import get_user_plan as _gup_scan, get_plan_limit as _gpl_scan
        _scan_plan = _gup_scan(db, str(current_user.id))
        _scan_max = _gpl_scan(_scan_plan['plan_name'], 'auto_scan_max_items')
        if _scan_max == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "upgrade_required",
                    "message": "Otomatik tarama icin planinizi yukseltin.",
                    "current_plan": _scan_plan['plan_name'],
                }
            )

        # Get ALL active watchlist items for this org (no page limit)
        # First get total count, then fetch all in one query
        _, total = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only=True, page_size=1
        )
        items, _ = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only=True, page_size=max(total, 1)
        )

    if not items:
        return SuccessResponse(message="Izleme listesinde taranacak marka yok")

    # Respect auto_scan_max_items limit
    items_to_scan = items[:_scan_max] if _scan_max < 999999 else items

    # Queue scans for items within limit
    for item in items_to_scan:
        background_tasks.add_task(_scan_watchlist_item, UUID(item['id']))

    msg = f"{len(items_to_scan)} marka taramaya alindi (toplam: {total})"
    if len(items_to_scan) < len(items):
        msg += f" — plan limitiniz nedeniyle {_scan_max} marka tarandi"
    return SuccessResponse(message=msg)


@watchlist_router.get("/scan-status")
async def get_scan_status(
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get auto-scan schedule status and next scan time"""
    from workers.scheduler import get_next_scan_time
    return {
        "auto_scan_enabled": True,
        "schedule": "Daily at 03:00",
        "next_scan_at": get_next_scan_time(),
    }


@watchlist_router.delete("/all", response_model=SuccessResponse)
async def delete_all_watchlist(
    current_user: CurrentUser = Depends(get_current_user)
):
    """Delete ALL watchlist items and alerts for the organization"""
    with Database() as db:
        cur = db.cursor()
        org_id = str(current_user.organization_id)

        # Delete associated alerts first
        cur.execute("""
            DELETE FROM alerts_mt WHERE organization_id = %s
        """, (org_id,))
        deleted_alerts = cur.rowcount

        # Delete all watchlist items
        cur.execute("""
            DELETE FROM watchlist_mt WHERE organization_id = %s
        """, (org_id,))
        deleted_items = cur.rowcount

        db.commit()

    return SuccessResponse(
        message=f"{deleted_items} marka ve {deleted_alerts} uyari silindi"
    )


@watchlist_router.post("/rescan", response_model=SuccessResponse)
async def rescan_all_watchlist(
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Clear old alerts and rescan all watchlist items fresh"""
    with Database() as db:
        # Check auto-scan eligibility
        from utils.subscription import get_user_plan as _gup_rescan, get_plan_limit as _gpl_rescan
        _rescan_plan = _gup_rescan(db, str(current_user.id))
        _rescan_max = _gpl_rescan(_rescan_plan['plan_name'], 'auto_scan_max_items')
        if _rescan_max == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "upgrade_required",
                    "message": "Otomatik tarama icin planinizi yukseltin.",
                    "current_plan": _rescan_plan['plan_name'],
                }
            )

        cur = db.cursor()
        org_id = str(current_user.organization_id)

        # Clear old alerts
        cur.execute("""
            DELETE FROM alerts_mt WHERE organization_id = %s
        """, (org_id,))
        cleared_alerts = cur.rowcount

        # Reset last_scan_at for all items
        cur.execute("""
            UPDATE watchlist_mt SET last_scan_at = NULL WHERE organization_id = %s
        """, (org_id,))

        # Get ALL active watchlist items (no page limit)
        # First get total count, then fetch all in one query
        _, total = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only=True, page_size=1
        )
        items, _ = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only=True, page_size=max(total, 1)
        )

        db.commit()

    if not items:
        return SuccessResponse(message=f"Eski {cleared_alerts} uyari silindi. Taranacak marka yok.")

    # Respect auto_scan_max_items limit
    items_to_scan = items[:_rescan_max] if _rescan_max < 999999 else items

    # Queue fresh scans for items within limit
    for item in items_to_scan:
        background_tasks.add_task(_scan_watchlist_item, UUID(item['id']))

    return SuccessResponse(
        message=f"Eski {cleared_alerts} uyari silindi. {len(items_to_scan)} marka yeniden taramaya alindi."
    )


@watchlist_router.get("/{item_id}", response_model=WatchlistItemResponse)
async def get_watchlist_item(
    item_id: UUID,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get watchlist item details"""
    with Database() as db:
        item = WatchlistCRUD.get_by_id(db, item_id, current_user.organization_id)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
        return WatchlistItemResponse(**item)


@watchlist_router.put("/{item_id}", response_model=WatchlistItemResponse)
async def update_watchlist_item(
    item_id: UUID,
    data: WatchlistItemUpdate,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update watchlist item"""
    # Check logo tracking eligibility if enabling visual monitoring
    if getattr(data, 'monitor_visual', None) is True:
        from utils.subscription import get_user_plan, get_plan_limit
        with Database() as db_check:
            plan = get_user_plan(db_check, str(current_user.id))
            can_track = get_plan_limit(plan['plan_name'], 'can_track_logos')
            if not can_track:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": "upgrade_required", "message": "Logo tracking requires a paid plan."}
                )

    with Database() as db:
        item = WatchlistCRUD.update(db, item_id, current_user.organization_id, data)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
        return WatchlistItemResponse(**item)


@watchlist_router.delete("/{item_id}", response_model=SuccessResponse)
async def delete_watchlist_item(
    item_id: UUID,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Remove item from watchlist AND its associated alerts"""
    with Database() as db:
        cur = db.cursor()

        # First, delete all alerts for this watchlist item
        cur.execute("""
            DELETE FROM alerts_mt
            WHERE watchlist_item_id = %s
        """, (str(item_id),))
        deleted_alerts = cur.rowcount

        # Then delete the watchlist item
        success = WatchlistCRUD.delete(db, item_id, current_user.organization_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

        db.commit()
        return SuccessResponse(message=f"Marka ve {deleted_alerts} uyari silindi")


@watchlist_router.post("/{item_id}/scan", response_model=SuccessResponse)
async def trigger_scan(
    item_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Manually trigger scan for watchlist item"""
    with Database() as db:
        item = WatchlistCRUD.get_by_id(db, item_id, current_user.organization_id)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    
    background_tasks.add_task(_scan_watchlist_item, item_id)
    return SuccessResponse(message="Scan triggered")


# ==========================================
# Watchlist Logo Upload
# ==========================================

WATCHLIST_LOGOS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads", "watchlist_logos")


@watchlist_router.post("/{item_id}/logo", response_model=SuccessResponse)
async def upload_watchlist_logo(
    item_id: UUID,
    background_tasks: BackgroundTasks,
    logo: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Upload a logo image for a watchlist item. Generates visual embeddings in background."""
    with Database() as db:
        item = WatchlistCRUD.get_by_id(db, item_id, current_user.organization_id)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    # Validate file type
    if not logo.content_type or not logo.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="Dosya bir gorsel olmali (PNG, JPG, WEBP)")

    # Read and validate image
    contents = await logo.read()
    if len(contents) > 5 * 1024 * 1024:  # 5MB max
        raise HTTPException(status_code=400, detail="Dosya boyutu 5MB'yi asamaz")

    # Save to disk
    org_dir = os.path.join(WATCHLIST_LOGOS_DIR, str(current_user.organization_id))
    os.makedirs(org_dir, exist_ok=True)

    ext = os.path.splitext(logo.filename or 'logo.png')[1] or '.png'
    filename = f"{item_id}{ext}"
    filepath = os.path.join(org_dir, filename)

    with open(filepath, 'wb') as f:
        f.write(contents)

    # Update logo_path immediately
    with Database() as db:
        WatchlistCRUD.update_logo(db, item_id, logo_path=filepath)

    # Generate embeddings in background
    background_tasks.add_task(_process_watchlist_logo, item_id, filepath)

    return SuccessResponse(message="Logo yuklendi, embeddingler olusturuluyor...")


@watchlist_router.get("/{item_id}/logo")
async def get_watchlist_logo(
    item_id: UUID,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get the logo image for a watchlist item"""
    from fastapi.responses import FileResponse as FR

    with Database() as db:
        item = WatchlistCRUD.get_by_id(db, item_id, current_user.organization_id)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    logo_path = item.get('logo_path')
    if not logo_path:
        raise HTTPException(status_code=404, detail="Logo bulunamadi")

    # Security: block directory traversal
    if ".." in logo_path:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Try absolute path first (user-uploaded logos)
    if os.path.isfile(logo_path):
        ext = os.path.splitext(logo_path)[1].lower()
        media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                 ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}.get(ext, "image/png")
        return FR(logo_path, media_type=media)

    # Try resolving relative path from project root (copied from trademarks)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(project_root, logo_path.replace("/", os.sep))
    if os.path.isfile(full_path):
        ext = os.path.splitext(full_path)[1].lower()
        media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                 ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}.get(ext, "image/png")
        return FR(full_path, media_type=media)

    raise HTTPException(status_code=404, detail="Logo bulunamadi")


@watchlist_router.delete("/{item_id}/logo", response_model=SuccessResponse)
async def delete_watchlist_logo(
    item_id: UUID,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Remove the logo from a watchlist item and clear visual embeddings"""
    with Database() as db:
        item = WatchlistCRUD.get_by_id(db, item_id, current_user.organization_id)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    # Delete file
    logo_path = item.get('logo_path')
    if logo_path and os.path.isfile(logo_path):
        try:
            os.remove(logo_path)
        except OSError:
            pass

    # Clear DB columns
    with Database() as db:
        WatchlistCRUD.clear_logo(db, item_id)

    return SuccessResponse(message="Logo silindi")


def _process_watchlist_logo(item_id: UUID, filepath: str):
    """Background task: generate CLIP, DINOv2, color, OCR embeddings for uploaded logo"""
    import traceback
    logger.info(f"[LOGO] Generating embeddings for watchlist {item_id}")
    try:
        from watchlist.scanner import generate_logo_embeddings
        result = generate_logo_embeddings(filepath)
        if not result:
            logger.warning(f"[LOGO] No embeddings generated for {item_id}")
            return

        with Database() as db:
            WatchlistCRUD.update_logo(
                db, item_id,
                logo_path=filepath,
                logo_embedding=result.get('clip_embedding'),
                dino_embedding=result.get('dino_embedding'),
                color_histogram=result.get('color_histogram'),
                logo_ocr_text=result.get('ocr_text'),
            )
        logger.info(f"[LOGO] Embeddings stored for watchlist {item_id}")
    except Exception as e:
        logger.error(f"[LOGO] Failed for {item_id}: {e}")
        logger.error(traceback.format_exc())


def _scan_watchlist_item(item_id: UUID):
    """Background task to scan watchlist item - uses singleton scanner for performance"""
    import traceback
    logger.info(f"🔍 [SCAN START] Scanning watchlist item {item_id}")
    try:
        from watchlist.scanner import get_scanner
        scanner = get_scanner()  # Reuse cached scanner with loaded models
        # Ensure connection is clean (rollback any aborted transaction)
        try:
            scanner.conn.rollback()
        except Exception:
            pass
        alerts_count = scanner.scan_single_watchlist(item_id)
        logger.info(f"✅ [SCAN COMPLETE] Item {item_id}: {alerts_count} alerts created")
    except Exception as e:
        logger.error(f"❌ [SCAN FAILED] Item {item_id}: {e}")
        logger.error(f"   Traceback: {traceback.format_exc()}")
        # Reset singleton scanner to get fresh connection on next scan
        from watchlist.scanner import reset_scanner
        reset_scanner()


# ==========================================
# Alert Routes
# ==========================================

@alerts_router.get("", response_model=PaginatedResponse)
async def list_alerts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[List[AlertStatus]] = Query(None),
    severity: Optional[List[AlertSeverity]] = Query(None),
    watchlist_id: Optional[UUID] = None,
    current_user: CurrentUser = Depends(get_current_user)
):
    """List alerts for organization with filtering"""
    with Database() as db:
        status_values = [s.value for s in status] if status else None
        severity_values = [s.value for s in severity] if severity else None
        
        alerts, total = AlertCRUD.get_by_organization(
            db, current_user.organization_id,
            status=status_values,
            severity=severity_values,
            watchlist_id=watchlist_id,
            page=page,
            page_size=page_size
        )
        
        return PaginatedResponse(
            items=[_format_alert(a) for a in alerts],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size
        )


@alerts_router.get("/summary", response_model=dict)
async def get_alerts_summary(
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get alerts summary by status and severity"""
    with Database() as db:
        cur = db.cursor()
        
        # By status (only appealable: deadline not yet passed or pre-publication)
        cur.execute("""
            SELECT a.status, COUNT(*) as count
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
              AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
            GROUP BY a.status
        """, (str(current_user.organization_id),))
        by_status = {row['status']: row['count'] for row in cur.fetchall()}

        # By severity (new only, appealable)
        cur.execute("""
            SELECT a.severity, COUNT(*) as count
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s AND a.status = 'new'
              AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
            GROUP BY a.severity
        """, (str(current_user.organization_id),))
        by_severity = {row['severity']: row['count'] for row in cur.fetchall()}
        
        return {
            "by_status": by_status,
            "by_severity": by_severity,
            "total_new": by_status.get('new', 0)
        }


@alerts_router.get("/aggregate")
async def aggregate_alerts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    severity: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get all alerts across all watchlist items, sorted by deadline urgency"""
    with Database() as db:
        cur = db.cursor()
        org_id = str(current_user.organization_id)

        where_extra = ""
        params = [org_id]
        if severity:
            where_extra = " AND a.severity = %s"
            params.append(severity)

        # Count (only appealable: deadline not yet passed or pre-publication)
        cur.execute("""
            SELECT COUNT(*) FROM alerts_mt a
            JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
                AND a.status NOT IN ('dismissed', 'resolved')
                AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
        """ + where_extra, params)
        total = cur.fetchone()['count']

        # Fetch page
        offset = (page - 1) * page_size
        cur.execute("""
            SELECT a.*, w.brand_name AS watched_brand_name,
                   a.opposition_deadline, a.conflicting_name,
                   t.name AS tm_name, t.current_status, t.bulletin_date
            FROM alerts_mt a
            JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
                AND a.status NOT IN ('dismissed', 'resolved')
                AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
        """ + where_extra + """
            ORDER BY
                CASE WHEN a.opposition_deadline IS NOT NULL AND a.opposition_deadline > CURRENT_DATE
                     THEN 0 ELSE 1 END,
                a.opposition_deadline ASC NULLS LAST,
                a.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])

        rows = cur.fetchall()
        items = []
        from datetime import date as date_type
        today = date_type.today()
        for r in rows:
            deadline = r.get('opposition_deadline')
            deadline_days = None
            if deadline and deadline > today:
                deadline_days = (deadline - today).days
            items.append({
                "id": str(r['id']),
                "watchlist_item_id": str(r['watchlist_item_id']),
                "watched_brand_name": r.get('watched_brand_name'),
                "conflicting_brand_name": r.get('conflicting_name') or r.get('tm_name'),
                "conflicting_trademark_id": str(r['conflicting_trademark_id']) if r.get('conflicting_trademark_id') else None,
                "severity": r.get('severity'),
                "risk_score": r.get('overall_risk_score'),
                "status": r.get('status'),
                "opposition_deadline": deadline.isoformat() if deadline else None,
                "deadline_days": deadline_days,
                "overlapping_classes": r.get('overlapping_classes'),
                "created_at": r['created_at'].isoformat() if r.get('created_at') else None
            })

        return PaginatedResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size
        )


@alerts_router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: UUID,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get alert details"""
    with Database() as db:
        alert = AlertCRUD.get_by_id(db, alert_id, current_user.organization_id)
        if not alert:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
        
        # Mark as seen if new
        if alert['status'] == 'new':
            AlertCRUD.update_status(db, alert_id, current_user.organization_id, AlertStatus.SEEN)
            alert['status'] = 'seen'
        
        return _format_alert(alert)


@alerts_router.post("/{alert_id}/acknowledge", response_model=AlertResponse)
async def acknowledge_alert(
    alert_id: UUID,
    data: AlertAcknowledge,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Acknowledge alert"""
    with Database() as db:
        alert = AlertCRUD.update_status(
            db, alert_id, current_user.organization_id,
            AlertStatus.ACKNOWLEDGED,
            user_id=current_user.id,
            notes=data.notes
        )
        if not alert:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
        return _format_alert(alert)


@alerts_router.post("/{alert_id}/resolve", response_model=AlertResponse)
async def resolve_alert(
    alert_id: UUID,
    data: AlertResolve,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Resolve alert"""
    with Database() as db:
        alert = AlertCRUD.update_status(
            db, alert_id, current_user.organization_id,
            AlertStatus.RESOLVED,
            user_id=current_user.id,
            notes=data.resolution_notes
        )
        if not alert:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
        return _format_alert(alert)


@alerts_router.post("/{alert_id}/dismiss", response_model=AlertResponse)
async def dismiss_alert(
    alert_id: UUID,
    data: AlertDismiss,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Dismiss alert (false positive)"""
    with Database() as db:
        alert = AlertCRUD.update_status(
            db, alert_id, current_user.organization_id,
            AlertStatus.DISMISSED,
            user_id=current_user.id,
            notes=data.reason
        )
        if not alert:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
        return _format_alert(alert)


def _format_alert(alert: dict) -> AlertResponse:
    """Format alert dict to response model"""
    from models.schemas import ConflictingTrademark, AlertScores
    from utils.deadline import classify_deadline_status

    # Use live status from trademarks table if alert's stored status is NULL
    conflict_status = (
        alert.get('conflicting_status') or
        alert.get('conflict_live_status') or
        'Unknown'
    )

    # Use live classes from trademarks table if available
    conflict_classes = (
        alert.get('conflicting_classes') or
        alert.get('conflict_live_classes') or
        []
    )

    # Classify deadline status
    deadline_info = classify_deadline_status(
        current_status=conflict_status,
        bulletin_date=alert.get('conflict_bulletin_date'),
        appeal_deadline=alert.get('conflict_appeal_deadline') or alert.get('opposition_deadline')
    )

    return AlertResponse(
        id=UUID(alert['id']),
        organization_id=UUID(alert['organization_id']),
        watchlist_id=UUID(alert['watchlist_item_id']),
        watched_brand_name=alert.get('watched_brand_name'),
        watchlist_bulletin_no=alert.get('watchlist_bulletin_no'),  # User's portfolio bulletin
        watchlist_application_no=alert.get('watchlist_application_no'),  # User's app number
        watchlist_classes=alert.get('watchlist_classes', []),  # User's Nice classes
        conflicting=ConflictingTrademark(
            id=UUID(alert['conflicting_trademark_id']) if alert.get('conflicting_trademark_id') else None,
            name=alert.get('conflicting_name', ''),
            application_no=alert.get('conflicting_application_no', ''),
            status=conflict_status,
            classes=conflict_classes,
            holder=alert.get('conflicting_holder_name'),
            image_path=alert.get('conflicting_image_path'),
            application_date=alert.get('conflict_application_date'),
            has_extracted_goods=bool(alert.get('conflict_has_extracted_goods', False))
        ),
        conflict_bulletin_no=alert.get('conflict_bulletin_no'),  # Conflicting trademark's bulletin
        overlapping_classes=alert.get('overlapping_classes', []),  # Classes that overlap
        scores=AlertScores(
            total=alert.get('overall_risk_score', 0),
            text_similarity=alert.get('text_similarity_score'),
            semantic_similarity=alert.get('semantic_similarity_score'),
            visual_similarity=alert.get('visual_similarity_score'),
            translation_similarity=alert.get('translation_similarity_score'),
            phonetic_match=alert.get('phonetic_match', False)
        ),
        severity=alert['severity'],
        status=alert['status'],
        source_type=alert.get('source_type'),
        source_reference=alert.get('source_bulletin'),
        source_date=None,
        appeal_deadline=alert.get('conflict_appeal_deadline'),
        conflict_bulletin_date=alert.get('conflict_bulletin_date'),
        deadline_status=deadline_info["status"],
        deadline_days_remaining=deadline_info["days_remaining"],
        deadline_label=deadline_info["label_tr"],
        deadline_urgency=deadline_info["urgency"],
        detected_at=alert['created_at'],
        seen_at=None,
        acknowledged_at=alert.get('acknowledged_at'),
        resolved_at=alert.get('resolved_at'),
        resolution_notes=alert.get('resolution_notes')
    )


# ==========================================
# Dashboard Routes
# ==========================================

@dashboard_router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(current_user: CurrentUser = Depends(get_current_user)):
    """Get main dashboard statistics"""
    with Database() as db:
        cur = db.cursor()
        org_id = str(current_user.organization_id)
        
        # Watchlist counts
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE is_active) as active
            FROM watchlist_mt WHERE organization_id = %s
        """, (org_id,))
        wl = cur.fetchone()
        
        # Alert counts (only appealable: deadline not yet passed or pre-publication)
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE a.status = 'new') as new,
                COUNT(*) FILTER (WHERE a.severity = 'critical' AND a.status != 'dismissed') as critical,
                COUNT(*) FILTER (WHERE a.created_at > NOW() - INTERVAL '7 days') as this_week
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
              AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
        """, (org_id,))
        al = cur.fetchone()

        # Active deadlines & pre-publication counts (from alerts joined with trademarks)
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE t.appeal_deadline IS NOT NULL
                    AND t.appeal_deadline >= CURRENT_DATE
                    AND a.status != 'dismissed') as active_deadlines,
                COUNT(*) FILTER (WHERE t.appeal_deadline IS NULL
                    AND (t.current_status IS NULL OR t.current_status = 'Applied')
                    AND a.status != 'dismissed') as pre_publication
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
        """, (org_id,))
        dl = cur.fetchone()
        
        # Searches this month (from api_usage table)
        cur.execute("""
            SELECT COALESCE(SUM(au.quick_searches), 0) + COALESCE(SUM(au.live_searches), 0) as cnt
            FROM api_usage au
            JOIN users u ON au.user_id = u.id
            WHERE u.organization_id = %s
              AND au.usage_date >= date_trunc('month', CURRENT_DATE)
        """, (org_id,))
        searches_row = cur.fetchone()
        searches_this_month = searches_row['cnt'] if searches_row else 0

        # Organization limits from PLAN_FEATURES (single source of truth)
        from utils.subscription import get_user_plan as _gup_dash, get_plan_limit as _gpl_dash
        _dash_plan = _gup_dash(db, str(current_user.id))
        _dash_plan_name = _dash_plan['plan_name']

        wl_limit = _gpl_dash(_dash_plan_name, 'max_watchlist_items')
        user_limit = _gpl_dash(_dash_plan_name, 'max_users')
        qs_limit = _gpl_dash(_dash_plan_name, 'max_daily_quick_searches')
        ls_limit = _gpl_dash(_dash_plan_name, 'monthly_live_searches')
        report_limit = _gpl_dash(_dash_plan_name, 'monthly_reports')

        # Count users in org
        cur.execute("SELECT COUNT(*) as cnt FROM users WHERE organization_id = %s AND is_active = TRUE", (org_id,))
        user_count = cur.fetchone()['cnt']

        return DashboardStats(
            watchlist_count=wl['total'],
            active_watchlist=wl['active'],
            total_alerts=al['total'],
            new_alerts=al['new'],
            critical_alerts=al['critical'],
            alerts_this_week=al['this_week'],
            searches_this_month=searches_this_month,
            active_deadline_count=dl['active_deadlines'],
            pre_publication_count=dl['pre_publication'],
            plan_usage={
                "watchlist": {"used": wl['active'], "limit": wl_limit},
                "users": {"used": user_count, "limit": user_limit},
                "searches": {"used": searches_this_month, "limit": qs_limit + ls_limit},
                "reports": {"used": 0, "limit": report_limit},
            }
        )


# ==========================================
# Admin Routes - IDF Management
# ==========================================

@admin_router.get("/idf-stats")
async def get_idf_stats(user: CurrentUser = Depends(require_role(["owner", "admin"]))):
    """
    Get IDF scoring system statistics.
    Shows cache status, word counts, and top generic words.
    """
    from utils.idf_scoring import (
        is_cache_loaded, get_cache_stats, get_most_common_words
    )

    stats = get_cache_stats()
    most_common = get_most_common_words(30)

    return {
        "success": True,
        "stats": stats,
        "most_common_words": most_common
    }


@admin_router.get("/idf-analyze")
async def analyze_word(
    word: str = Query(..., description="Word to analyze"),
    user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """
    Analyze a specific word's IDF classification.
    Returns IDF score, word class, weight, and document frequency.
    """
    from utils.idf_scoring import (
        get_word_idf, get_word_class, get_word_weight, get_doc_frequency
    )

    return {
        "word": word,
        "idf_score": get_word_idf(word),
        "word_class": get_word_class(word),
        "weight": get_word_weight(word),
        "doc_frequency": get_doc_frequency(word)
    }


@admin_router.get("/idf-query-analysis")
async def analyze_query(
    q: str = Query(..., description="Query to analyze"),
    user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """
    Analyze a search query and show word importance breakdown.
    Useful for debugging why certain results rank high/low.
    """
    from utils.idf_scoring import analyze_query as _analyze

    return _analyze(q)


@admin_router.post("/idf-test-similarity")
async def test_similarity(
    query: str = Query(..., description="Search query"),
    target: str = Query(..., description="Target trademark name"),
    user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """
    Test IDF-weighted similarity between two texts.
    Returns both raw and adjusted scores with breakdown.
    """
    from utils.idf_scoring import (
        calculate_text_similarity, calculate_adjusted_score
    )

    # Get text similarity
    text_sim = calculate_text_similarity(query, target)

    # Get detailed adjusted score
    adjusted = calculate_adjusted_score(text_sim, query, target, include_details=True)

    return {
        "query": query,
        "target": target,
        "text_similarity": round(text_sim, 4),
        "adjusted_score": adjusted['adjusted_score'],
        "applied_weight": adjusted['applied_weight'],
        "details": adjusted.get('details', {})
    }


@admin_router.post("/idf-refresh")
async def refresh_idf_cache(
    user: CurrentUser = Depends(require_role(["admin"]))
):
    """
    Refresh IDF cache from database.
    Requires ADMIN role. Use after running compute_idf.py.
    """
    from utils.idf_scoring import clear_cache, initialize_idf_scoring_sync, get_cache_stats

    clear_cache()
    success = initialize_idf_scoring_sync()
    stats = get_cache_stats()

    return {
        "success": success,
        "message": "IDF cache refreshed" if success else "IDF refresh failed",
        "stats": stats
    }


# ==========================================
# Trademark Detail (extracted goods lazy-load)
# ==========================================

@trademark_router.get("/{application_no:path}/extracted-goods")
async def get_extracted_goods(
    application_no: str,
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Lazy-load endpoint: fetch extracted goods for a specific trademark.
    Called when user clicks the extracted goods indicator on a card.
    """
    with Database() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT application_no, name, extracted_goods, nice_class_numbers
            FROM trademarks
            WHERE application_no = %s
        """, (application_no,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marka bulunamadi")

    extracted = row.get("extracted_goods")
    if not extracted or extracted == [] or extracted is None:
        return {
            "application_no": application_no,
            "has_extracted_goods": False,
            "extracted_goods": [],
            "total_items": 0
        }

    return {
        "application_no": application_no,
        "name": row.get("name"),
        "has_extracted_goods": True,
        "extracted_goods": extracted,
        "nice_classes": row.get("nice_class_numbers"),
        "total_items": len(extracted) if isinstance(extracted, list) else 0
    }


# ==========================================
# Usage Summary
# ==========================================

@usage_router.get("/summary")
async def get_usage_summary(current_user: CurrentUser = Depends(get_current_user)):
    """
    Unified credits/usage endpoint.
    Returns all usage counters and plan limits for the current user.
    """
    from utils.subscription import (
        get_user_plan, get_plan_limit,
        get_daily_quick_searches, get_live_search_usage,
        get_monthly_name_generations, get_org_plan,
        check_ai_credit_eligibility, get_monthly_applications,
    )

    with Database() as db:
        user_id = str(current_user.id)
        org_id = str(current_user.organization_id)
        plan = get_user_plan(db, user_id)
        plan_name = plan['plan_name']

        # Daily quick searches
        qs_used = get_daily_quick_searches(db, user_id)
        qs_limit = get_plan_limit(plan_name, 'max_daily_quick_searches')

        # Monthly live searches
        ls_used = get_live_search_usage(db, user_id)
        ls_limit = get_plan_limit(plan_name, 'monthly_live_searches')

        # AI credits (org-level, unified pool)
        ai_ok, _, ai_details = check_ai_credit_eligibility(db, org_id, cost=1)
        ai_remaining = ai_details.get('total_remaining', 0)
        ai_limit = get_plan_limit(plan_name, 'monthly_ai_credits')

        # Monthly name generations (org-level, for display)
        ng_used = get_monthly_name_generations(db, org_id)

        # Monthly applications (org-level)
        app_used = get_monthly_applications(db, org_id)
        app_limit = get_plan_limit(plan_name, 'monthly_applications')

        # Logo tracking
        can_track_logos = get_plan_limit(plan_name, 'can_track_logos')

        # Watchlist items count
        cur = db.cursor()
        cur.execute(
            "SELECT COUNT(*) as cnt FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE",
            (org_id,)
        )
        wl_row = cur.fetchone()
        wl_count = wl_row['cnt'] if wl_row else 0
        wl_limit = get_plan_limit(plan_name, 'max_watchlist_items')

        # Name generation limit (uses the unified AI credit pool)
        ng_limit = ai_limit  # name generations share the AI credit pool

        # Logo generation limit (also from unified AI credit pool)
        logo_limit = ai_limit

    return {
        "plan": plan_name,
        "display_name": plan['display_name'],
        "usage": {
            "daily_quick_searches": {"used": qs_used, "limit": qs_limit},
            "monthly_live_searches": {"used": ls_used, "limit": ls_limit},
            "monthly_ai_credits": {"remaining": ai_remaining, "limit": ai_limit},
            "monthly_name_generations": {"used": ng_used, "limit": ng_limit},
            "monthly_name_generations_used": ng_used,
            "monthly_applications": {"used": app_used, "limit": app_limit},
            "watchlist_items": {"used": wl_count, "limit": wl_limit},
            "logo_credits": {"remaining": ai_remaining, "limit": logo_limit},
            "can_track_logos": can_track_logos,
        },
    }


# ==========================================
# Main API App
# ==========================================

def create_app():
    """Create FastAPI application with all routes"""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="AI-powered trademark risk assessment with watchlist monitoring"
    )
    
    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include routers
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(org_router, prefix="/api/v1")
    app.include_router(watchlist_router, prefix="/api/v1")
    app.include_router(alerts_router, prefix="/api/v1")
    app.include_router(reports_router, prefix="/api/v1")
    app.include_router(dashboard_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(trademark_router, prefix="/api/v1")

    # Health check
    @app.get("/health")
    async def health():
        return {
            "status": "healthy",
            "version": settings.app_version,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    return app


# For running directly
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
# Route ordering fix applied

"""
Authentication Routes
Login, register, password management, email verification
"""
import logging
import secrets
import hashlib
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel as PydanticBaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from config.settings import settings
from auth.authentication import (
    CurrentUser, TokenPair, UserLogin, UserRegister, PasswordChange,
    PasswordReset, PasswordResetConfirm, VerifyEmailRequest,
    get_current_user, require_role, create_token_pair, decode_token,
    hash_password, verify_password, generate_verification_token
)
from models.schemas import (
    OrganizationCreate, OrganizationResponse,
    UserCreate, UserProfile, UserRole,
    SuccessResponse,
)
from database.crud import (
    Database,
    OrganizationCRUD, UserCRUD,
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

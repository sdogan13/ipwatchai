"""Service helpers for authentication and account lifecycle flows."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status

from auth.authentication import (
    PasswordChange,
    PasswordReset,
    PasswordResetConfirm,
    TokenPair,
    UserRegister,
    VerifyEmailRequest,
    create_token_pair,
    decode_token,
    hash_password,
    verify_password,
)
from database.crud import Database, OrganizationCRUD, UserCRUD
from models.schemas import (
    OrganizationCreate,
    OrganizationResponse,
    SuccessResponse,
    UserCreate,
    UserProfile,
    UserRole,
)


logger = logging.getLogger(__name__)


def _build_email_service():
    from notifications.service import EmailService

    return EmailService()


def _generate_six_digit_code():
    return f"{secrets.randbelow(1000000):06d}"


async def register_user(
    *,
    data: UserRegister,
    ip: str,
    db_factory=Database,
    organization_crud=OrganizationCRUD,
    user_crud=UserCRUD,
    token_pair_factory=create_token_pair,
    now_getter=datetime.utcnow,
    code_generator=_generate_six_digit_code,
    email_service_factory=_build_email_service,
):
    """Register a user and either create or join an organization."""
    with db_factory() as db:
        try:
            existing = user_crud.get_by_email(db, data.email)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered",
                )

            if data.organization_name:
                slug = data.organization_name.lower().replace(" ", "-")
                org = organization_crud.create(
                    db,
                    OrganizationCreate(
                        name=data.organization_name,
                        slug=slug,
                        email=data.email,
                    ),
                )
                role = UserRole.ADMIN
            elif data.organization_slug:
                org = organization_crud.get_by_slug(db, data.organization_slug)
                if not org:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Organization not found",
                    )
                role = UserRole.USER
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Must provide organization_name or organization_slug",
                )

            user = user_crud.create(
                db,
                UUID(str(org["id"])),
                UserCreate(
                    email=data.email,
                    password=data.password,
                    first_name=data.first_name,
                    last_name=data.last_name,
                    role=role,
                ),
            )

            logger.info(
                "New registration: user=%s email=%s org=%s IP=%s",
                user["id"],
                data.email,
                user["organization_id"],
                ip,
            )

            verification_code = code_generator()
            code_hash = hashlib.sha256(verification_code.encode()).hexdigest()
            verification_expires = now_getter() + timedelta(hours=24)

            cur = db.cursor()
            cur.execute(
                """INSERT INTO email_verification_tokens (id, user_id, token_hash, expires_at, created_at)
                   VALUES (gen_random_uuid(), %s, %s, %s, NOW())""",
                (str(user["id"]), code_hash, verification_expires),
            )
            db.commit()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )

    try:
        email_svc = email_service_factory()
        if email_svc.is_configured():
            email_svc.send_welcome(
                to_email=data.email,
                first_name=data.first_name,
                plan_name="Free",
                lang=getattr(data, "lang", "tr"),
                verification_code=verification_code,
            )
    except Exception as exc:
        logger.error("Failed to send welcome email to %s: %s", data.email, exc)

    return token_pair_factory(
        str(user["id"]),
        str(user["organization_id"]),
        user["role"],
    )


async def login_user(
    *,
    email: str,
    password: str,
    ip: str,
    db_factory=Database,
    user_crud=UserCRUD,
    password_verifier=verify_password,
    token_pair_factory=create_token_pair,
):
    """Authenticate a user and issue a fresh token pair."""
    with db_factory() as db:
        user = user_crud.get_by_email(db, email)

        if not user:
            logger.warning(
                "Failed login: email=%s IP=%s reason=user_not_found",
                email,
                ip,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )

        if not user["is_active"]:
            logger.warning(
                "Failed login: email=%s IP=%s reason=account_deactivated",
                email,
                ip,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account is deactivated",
            )

        if not password_verifier(password, user["password_hash"]):
            logger.warning(
                "Failed login: email=%s IP=%s reason=wrong_password",
                email,
                ip,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )

        user_crud.update_login(db, UUID(str(user["id"])))

    logger.info("Successful login: user=%s email=%s IP=%s", user["id"], email, ip)
    return token_pair_factory(
        str(user["id"]),
        str(user["organization_id"]),
        user["role"],
    )


async def refresh_token_data(
    *,
    refresh_token: str,
    db_factory=Database,
    token_decoder=decode_token,
    token_pair_factory=create_token_pair,
):
    """Issue a new token pair for a valid refresh token."""
    payload = token_decoder(refresh_token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    if payload.type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type - expected refresh token",
        )

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT id, role, is_active FROM users WHERE id = %s",
            (payload.sub,),
        )
        user = cur.fetchone()
        if user is None or not user["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or deactivated",
            )

        cur.execute(
            "SELECT id, is_active FROM organizations WHERE id = %s",
            (payload.org,),
        )
        org = cur.fetchone()
        if org is None or not org["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organization is deactivated",
            )

    logger.info("Token refresh: user=%s", payload.sub)
    return token_pair_factory(payload.sub, payload.org, user["role"])


async def change_password_data(
    *,
    data: PasswordChange,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
    password_verifier=verify_password,
    password_hasher=hash_password,
):
    """Change the authenticated user's password."""
    with db_factory() as db:
        user = user_crud.get_by_email(db, current_user.email)

        if not password_verifier(data.current_password, user["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect",
            )

        cur = db.cursor()
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (password_hasher(data.new_password), str(current_user.id)),
        )
        db.commit()

    return SuccessResponse(message="Password changed successfully")


async def forgot_password_data(
    *,
    data: PasswordReset,
    db_factory=Database,
    user_crud=UserCRUD,
    now_getter=datetime.utcnow,
    code_generator=_generate_six_digit_code,
    email_service_factory=_build_email_service,
):
    """Store and email a password reset code when the address exists."""
    with db_factory() as db:
        user = user_crud.get_by_email(db, data.email)
        if not user:
            return SuccessResponse(
                message="If this email is registered, a reset code has been generated."
            )

        code = code_generator()
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        expires = now_getter() + timedelta(minutes=15)

        cur = db.cursor()
        cur.execute(
            "DELETE FROM password_reset_tokens WHERE user_id = %s",
            (str(user["id"]),),
        )
        cur.execute(
            """INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at, created_at)
               VALUES (gen_random_uuid(), %s, %s, %s, NOW())""",
            (str(user["id"]), code_hash, expires),
        )
        db.commit()

    logger.info("Password reset requested: email=%s user_id=%s", data.email, user["id"])

    try:
        email_svc = email_service_factory()
        if email_svc.is_configured():
            email_svc.send_password_reset(
                to_email=data.email,
                code=code,
                lang=getattr(data, "lang", "tr"),
            )
        else:
            logger.warning("SMTP not configured - password reset code not emailed")
    except Exception as exc:
        logger.error("Failed to send password reset email: %s", exc)

    return SuccessResponse(
        message="If this email is registered, a reset code has been sent."
    )


async def reset_password_data(
    *,
    data: PasswordResetConfirm,
    db_factory=Database,
    password_hasher=hash_password,
    now_getter=datetime.utcnow,
):
    """Validate a password reset code and set the new password."""
    code_hash = hashlib.sha256(data.token.encode()).hexdigest()

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """SELECT prt.id, prt.user_id, prt.expires_at, prt.used_at
               FROM password_reset_tokens prt
               WHERE prt.token_hash = %s""",
            (code_hash,),
        )
        token_row = cur.fetchone()

        if not token_row:
            raise HTTPException(status_code=400, detail="Invalid or expired reset code")
        if token_row["used_at"] is not None:
            raise HTTPException(
                status_code=400,
                detail="This reset code has already been used",
            )
        if token_row["expires_at"] < now_getter():
            raise HTTPException(status_code=400, detail="Reset code has expired")

        cur.execute(
            "UPDATE users SET password_hash = %s, password_changed_at = NOW() WHERE id = %s",
            (password_hasher(data.new_password), str(token_row["user_id"])),
        )
        cur.execute(
            "UPDATE password_reset_tokens SET used_at = NOW() WHERE id = %s",
            (str(token_row["id"]),),
        )
        db.commit()

    logger.info("Password reset completed: user_id=%s", token_row["user_id"])
    return SuccessResponse(message="Password has been reset successfully")


async def verify_email_data(
    *,
    data: VerifyEmailRequest,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
    now_getter=datetime.utcnow,
):
    """Validate the submitted verification code for the current user."""
    code_hash = hashlib.sha256(data.code.encode()).hexdigest()

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """SELECT evt.id, evt.user_id, evt.expires_at, evt.used_at
               FROM email_verification_tokens evt
               WHERE evt.token_hash = %s AND evt.user_id = %s""",
            (code_hash, str(current_user.id)),
        )
        token_row = cur.fetchone()

        if not token_row:
            raise HTTPException(status_code=400, detail="Invalid verification code")
        if token_row["used_at"] is not None:
            raise HTTPException(
                status_code=400,
                detail="This code has already been used",
            )
        if token_row["expires_at"] < now_getter():
            raise HTTPException(
                status_code=400,
                detail="Verification code has expired",
            )

        user_crud.verify_email(db, current_user.id)
        cur.execute(
            "UPDATE email_verification_tokens SET used_at = NOW() WHERE id = %s",
            (str(token_row["id"]),),
        )
        db.commit()

    logger.info("Email verified: user_id=%s", current_user.id)
    return SuccessResponse(message="Email verified successfully")


async def resend_verification_data(
    *,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
    now_getter=datetime.utcnow,
    code_generator=_generate_six_digit_code,
    email_service_factory=_build_email_service,
):
    """Invalidate any old verification codes and send a fresh one."""
    with db_factory() as db:
        user = user_crud.get_by_id(db, current_user.id)
        if user.get("is_email_verified"):
            return SuccessResponse(message="Email is already verified")

        cur = db.cursor()
        cur.execute(
            "DELETE FROM email_verification_tokens WHERE user_id = %s",
            (str(current_user.id),),
        )

        verification_code = code_generator()
        code_hash = hashlib.sha256(verification_code.encode()).hexdigest()
        expires = now_getter() + timedelta(hours=24)

        cur.execute(
            """INSERT INTO email_verification_tokens (id, user_id, token_hash, expires_at, created_at)
               VALUES (gen_random_uuid(), %s, %s, %s, NOW())""",
            (str(current_user.id), code_hash, expires),
        )
        db.commit()

    try:
        email_svc = email_service_factory()
        if email_svc.is_configured():
            email_svc.send_welcome(
                to_email=current_user.email,
                first_name=current_user.first_name or "User",
                plan_name="Free",
                lang="tr",
                verification_code=verification_code,
            )
    except Exception as exc:
        logger.error(
            "Failed to send verification email to %s: %s",
            current_user.email,
            exc,
        )

    logger.info("Verification code resent: user_id=%s", current_user.id)
    return SuccessResponse(message="Verification code sent")


async def get_current_user_profile_data(
    *,
    current_user,
    db_factory=Database,
    user_crud=UserCRUD,
    organization_crud=OrganizationCRUD,
    plan_features_getter=None,
):
    """Return the authenticated user profile enriched with organization limits."""
    with db_factory() as db:
        user = user_crud.get_by_id(db, current_user.id)
        org = organization_crud.get_by_id(db, current_user.organization_id)

        user_data = dict(user)
        user_data["is_verified"] = user_data.pop("is_email_verified", False)

        org_data = dict(org)
        if org_data.get("subscription_plan_id"):
            cur = db.cursor()
            cur.execute(
                "SELECT name, max_watchlist_items, max_api_calls_per_day FROM subscription_plans WHERE id = %s",
                (str(org_data["subscription_plan_id"]),),
            )
            plan_row = cur.fetchone()
            if plan_row:
                org_data["plan"] = plan_row["name"]
                org_data["max_watchlist_items"] = plan_row["max_watchlist_items"]
                org_data["max_monthly_searches"] = (
                    plan_row["max_api_calls_per_day"] * 30
                )

    if current_user.is_superadmin:
        features_getter = plan_features_getter
        if features_getter is None:
            from utils.subscription import PLAN_FEATURES

            features_getter = lambda: PLAN_FEATURES
        plan_features = features_getter()
        superadmin_features = plan_features["superadmin"]
        org_data["plan"] = "enterprise"
        org_data["max_watchlist_items"] = superadmin_features["max_watchlist_items"]
        org_data["max_monthly_searches"] = superadmin_features[
            "monthly_live_searches"
        ]
        org_data["max_users"] = superadmin_features["max_users"]

    return UserProfile(
        **user_data,
        organization=OrganizationResponse(**org_data),
        permissions=[],
    )

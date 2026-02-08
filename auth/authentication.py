"""
Authentication Module
JWT-based authentication with password hashing
"""
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple
from uuid import UUID

import bcrypt

logger = logging.getLogger(__name__)
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field, validator
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config.settings import settings

# Security scheme
security = HTTPBearer()


# ==========================================
# Pydantic Models
# ==========================================

class TokenPayload(BaseModel):
    """JWT Token Payload"""
    sub: str  # user_id
    org: str  # organization_id
    role: str
    exp: datetime
    type: str = "access"  # access or refresh


class TokenPair(BaseModel):
    """Access + Refresh Token Pair"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class UserRegister(BaseModel):
    """User Registration Request"""
    email: EmailStr
    password: str = Field(..., min_length=8)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    organization_name: Optional[str] = None  # If creating new org
    organization_slug: Optional[str] = None  # If joining existing org
    
    @validator("password")
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain a digit")
        return v


class UserLogin(BaseModel):
    """User Login Request"""
    email: EmailStr
    password: str


class PasswordReset(BaseModel):
    """Password Reset Request"""
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Password Reset Confirmation"""
    token: str
    new_password: str = Field(..., min_length=8)


class PasswordChange(BaseModel):
    """Password Change (logged in user)"""
    current_password: str
    new_password: str = Field(..., min_length=8)


class CurrentUser(BaseModel):
    """Current authenticated user context"""
    id: UUID
    organization_id: UUID
    email: str
    first_name: Optional[str]
    last_name: Optional[str]
    role: str
    permissions: list


# ==========================================
# Password Hashing
# ==========================================

def hash_password(password: str) -> str:
    """Hash password using bcrypt"""
    salt = bcrypt.gensalt(rounds=10)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8")
    )


# ==========================================
# Token Generation
# ==========================================

def create_access_token(user_id: str, organization_id: str, role: str) -> str:
    """Create JWT access token"""
    expires = datetime.utcnow() + timedelta(minutes=settings.auth.access_token_expire_minutes)
    
    payload = {
        "sub": user_id,
        "org": organization_id,
        "role": role,
        "exp": expires,
        "type": "access",
        "iat": datetime.utcnow()
    }
    
    return jwt.encode(payload, settings.auth.secret_key, algorithm=settings.auth.algorithm)


def create_refresh_token(user_id: str, organization_id: str, role: str) -> str:
    """Create JWT refresh token"""
    expires = datetime.utcnow() + timedelta(days=settings.auth.refresh_token_expire_days)
    
    payload = {
        "sub": user_id,
        "org": organization_id,
        "role": role,
        "exp": expires,
        "type": "refresh",
        "iat": datetime.utcnow(),
        "jti": secrets.token_urlsafe(32)  # Unique token ID
    }
    
    return jwt.encode(payload, settings.auth.secret_key, algorithm=settings.auth.algorithm)


def create_token_pair(user_id: str, organization_id: str, role: str) -> TokenPair:
    """Create both access and refresh tokens"""
    return TokenPair(
        access_token=create_access_token(user_id, organization_id, role),
        refresh_token=create_refresh_token(user_id, organization_id, role),
        expires_in=settings.auth.access_token_expire_minutes * 60
    )


def decode_token(token: str) -> Optional[TokenPayload]:
    """Decode and validate JWT token"""
    try:
        payload = jwt.decode(
            token,
            settings.auth.secret_key,
            algorithms=[settings.auth.algorithm]
        )
        return TokenPayload(**payload)
    except JWTError:
        return None


# ==========================================
# Token Utilities
# ==========================================

def generate_verification_token() -> str:
    """Generate email verification token"""
    return secrets.token_urlsafe(32)


def generate_reset_token() -> str:
    """Generate password reset token"""
    return secrets.token_urlsafe(32)


def generate_api_key() -> Tuple[str, str]:
    """
    Generate API key
    Returns: (full_key, key_prefix)
    """
    key = secrets.token_urlsafe(32)
    prefix = key[:8]
    return key, prefix


def hash_api_key(key: str) -> str:
    """Hash API key for storage"""
    return hash_password(key)


def verify_api_key(key: str, key_hash: str) -> bool:
    """Verify API key against hash"""
    return verify_password(key, key_hash)


# ==========================================
# FastAPI Dependencies
# ==========================================

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> CurrentUser:
    """
    FastAPI dependency to get current authenticated user.
    Verifies JWT token AND checks user/org are active in the database.
    Usage: current_user: CurrentUser = Depends(get_current_user)
    """
    from database.crud import get_db_connection
    from psycopg2.extras import RealDictCursor

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = credentials.credentials
    payload = decode_token(token)

    if payload is None:
        raise credentials_exception

    if payload.type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type"
        )

    # DB lookup — verify user exists and is active
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            "SELECT id, email, first_name, last_name, role, is_active "
            "FROM users WHERE id = %s",
            (payload.sub,)
        )
        user = cur.fetchone()
        if user is None or not user['is_active']:
            logger.warning(f"Blocked auth: user={payload.sub} reason=inactive_or_missing")
            raise credentials_exception

        # Verify organization is active
        cur.execute(
            "SELECT id, is_active FROM organizations WHERE id = %s",
            (payload.org,)
        )
        org = cur.fetchone()
        if org is None or not org['is_active']:
            logger.warning(f"Blocked auth: user={payload.sub} org={payload.org} reason=org_deactivated")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organization is deactivated"
            )

        cur.close()

        return CurrentUser(
            id=UUID(str(user['id'])),
            organization_id=UUID(payload.org),
            email=user['email'],
            first_name=user.get('first_name'),
            last_name=user.get('last_name'),
            role=user['role'],
            permissions=[]
        )
    except HTTPException:
        raise
    except Exception:
        raise credentials_exception
    finally:
        if conn:
            conn.close()


async def get_current_active_user(
    current_user: CurrentUser = Depends(get_current_user)
) -> CurrentUser:
    """Ensure user is active (already verified in get_current_user)"""
    return current_user


def require_role(allowed_roles: list):
    """
    Dependency factory to require specific roles.
    Usage: Depends(require_role(["owner", "admin"]))
    """
    async def role_checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' not authorized. Required: {allowed_roles}"
            )
        return current_user
    return role_checker


def require_permission(permission: str):
    """
    Dependency factory to require specific permission.
    Usage: Depends(require_permission("watchlist.write"))
    """
    async def permission_checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if permission not in current_user.permissions and current_user.role not in ["owner", "admin"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required"
            )
        return current_user
    return permission_checker


# TODO: Implement API key authentication when needed.
# Should support X-API-Key header with keys stored in api_keys table.

"""
API Routes
All REST endpoints for the Trademark Risk Assessment System
"""
import io
import os
import re
import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, BackgroundTasks, Body, Request
from pydantic import BaseModel as PydanticBaseModel
from fastapi.responses import FileResponse, StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from config.settings import settings
from auth.authentication import (
    CurrentUser, TokenPair, UserLogin, UserRegister, PasswordChange,
    get_current_user, require_role, create_token_pair,
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
@limiter.limit(f"{settings.auth.login_rate_limit}/minute")
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
            return create_token_pair(
                str(user['id']),
                str(user['organization_id']),
                user['role']
            )
            
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@auth_router.post("/login", response_model=TokenPair)
@limiter.limit(f"{settings.auth.login_rate_limit}/minute")
async def login(request: Request, data: UserLogin):
    """Login with email and password"""
    with Database() as db:
        user = UserCRUD.get_by_email(db, data.email)
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )
        
        if not user['is_active']:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account is deactivated"
            )
        
        if not verify_password(data.password, user['password_hash']):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )
        
        # Update last login
        UserCRUD.update_login(db, UUID(user['id']))
        
        return create_token_pair(
            str(user['id']),
            str(user['organization_id']),
            user['role']
        )


@auth_router.post("/refresh", response_model=TokenPair)
async def refresh_token(current_user: CurrentUser = Depends(get_current_user)):
    """Refresh access token"""
    return create_token_pair(
        str(current_user.id),
        str(current_user.organization_id),
        current_user.role
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


@auth_router.get("/me", response_model=UserProfile)
async def get_current_user_profile(current_user: CurrentUser = Depends(get_current_user)):
    """Get current user profile with organization info"""
    with Database() as db:
        user = UserCRUD.get_by_id(db, current_user.id)
        org = OrganizationCRUD.get_by_id(db, current_user.organization_id)

        # Map is_email_verified to is_verified for schema compatibility
        user_data = dict(user)
        user_data['is_verified'] = user_data.pop('is_email_verified', False)

        return UserProfile(
            **user_data,
            organization=OrganizationResponse(**org),
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
            "created_at": user.get("created_at").isoformat() if user.get("created_at") else None
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
        return OrganizationStats(
            user_count=stats.get('user_count', 0),
            active_watchlist_items=stats.get('active_watchlist_items', 0),
            new_alerts=stats.get('new_alerts', 0),
            critical_alerts=stats.get('critical_alerts', 0),
            searches_this_month=0,  # TODO: Implement
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

@watchlist_router.get("", response_model=PaginatedResponse)
async def list_watchlist(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=2000),  # Increased default to 100, max to 2000
    active_only: bool = True,
    current_user: CurrentUser = Depends(get_current_user)
):
    """List watchlist items for organization"""
    with Database() as db:
        items, total = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only, page, page_size
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
                    COUNT(*) FILTER (WHERE t.appeal_deadline IS NOT NULL AND t.appeal_deadline < CURRENT_DATE) as expired_count,
                    MIN(t.appeal_deadline) FILTER (WHERE t.appeal_deadline > CURRENT_DATE) as nearest_deadline
                FROM alerts_mt a
                LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
                WHERE a.watchlist_item_id = ANY(%s::uuid[])
                    AND a.status NOT IN ('dismissed', 'resolved')
                GROUP BY a.watchlist_item_id
            """, (item_ids,))
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
                    "expired": row['expired_count'],
                    "nearest_deadline": nearest.isoformat() if nearest else None,
                    "nearest_deadline_days": days_to_nearest
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
    """Add trademark to watchlist"""
    with Database() as db:
        try:
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
        created = 0
        failed = 0
        errors = []
        created_ids = []
        
        for i, item in enumerate(data.items):
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
async def detect_columns(file: UploadFile = File(...)):
    """Read file and return column names for mapping UI."""
    contents = await file.read()
    filename = file.filename.lower() if file.filename else ""

    try:
        if filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(contents), nrows=5)
        elif filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents), nrows=5)
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
        auto_mappings=auto_mappings_orig
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

    # Validate required mappings
    required_fields = ['brand_name', 'application_no', 'nice_classes']
    missing_mappings = [f for f in required_fields if not mappings.get(f)]
    if missing_mappings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Eksik zorunlu eslestirmeler: {', '.join(missing_mappings)}"
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
                # Validate brand name (required)
                brand_name = str(row.get('brand_name', '')).strip()
                if not brand_name or brand_name.lower() in ['nan', 'none', '']:
                    error_count += 1
                    error_items.append(FileUploadErrorItem(
                        row=row_num,
                        error="Marka adi bos"
                    ))
                    continue

                # Validate application number (required)
                app_no = str(row.get('application_no', '')).strip()
                if not app_no or app_no.lower() in ['nan', 'none', '']:
                    error_count += 1
                    error_items.append(FileUploadErrorItem(
                        row=row_num,
                        brand_name=brand_name,
                        error="Basvuru numarasi bos"
                    ))
                    continue

                # Validate nice classes (required)
                classes_raw = row.get('nice_classes', '')
                nice_classes = _parse_nice_classes(classes_raw)
                if not nice_classes:
                    error_count += 1
                    error_items.append(FileUploadErrorItem(
                        row=row_num,
                        brand_name=brand_name,
                        error="Sinif bilgisi bos veya gecersiz"
                    ))
                    continue

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

    # Queue scans for all items
    for item in items:
        background_tasks.add_task(_scan_watchlist_item, UUID(item['id']))

    return SuccessResponse(message=f"{len(items)} marka taramaya alindi (toplam: {total})")


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

    # Queue fresh scans for all items
    for item in items:
        background_tasks.add_task(_scan_watchlist_item, UUID(item['id']))

    return SuccessResponse(
        message=f"Eski {cleared_alerts} uyari silindi. {len(items)} marka yeniden taramaya alindi."
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
    if not logo_path or not os.path.isfile(logo_path):
        raise HTTPException(status_code=404, detail="Logo bulunamadi")

    return FR(logo_path, media_type="image/png")


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
        alerts_count = scanner.scan_single_watchlist(item_id)
        logger.info(f"✅ [SCAN COMPLETE] Item {item_id}: {alerts_count} alerts created")
    except Exception as e:
        logger.error(f"❌ [SCAN FAILED] Item {item_id}: {e}")
        logger.error(f"   Traceback: {traceback.format_exc()}")


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
        
        # By status
        cur.execute("""
            SELECT status, COUNT(*) as count
            FROM alerts_mt WHERE organization_id = %s
            GROUP BY status
        """, (str(current_user.organization_id),))
        by_status = {row['status']: row['count'] for row in cur.fetchall()}
        
        # By severity (new only)
        cur.execute("""
            SELECT severity, COUNT(*) as count
            FROM alerts_mt WHERE organization_id = %s AND status = 'new'
            GROUP BY severity
        """, (str(current_user.organization_id),))
        by_severity = {row['severity']: row['count'] for row in cur.fetchall()}
        
        return {
            "by_status": by_status,
            "by_severity": by_severity,
            "total_new": by_status.get('new', 0)
        }


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
            filing_date=None,
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
        
        # Alert counts
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'new') as new,
                COUNT(*) FILTER (WHERE severity = 'critical' AND status = 'new') as critical,
                COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days') as this_week
            FROM alerts_mt WHERE organization_id = %s
        """, (org_id,))
        al = cur.fetchone()
        
        # Organization limits from subscription plan
        cur.execute("""
            SELECT o.*, sp.max_watchlist_items, sp.max_alerts_per_month, sp.max_reports_per_month
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.id = %s
        """, (org_id,))
        org = cur.fetchone()

        return DashboardStats(
            watchlist_count=wl['total'],
            active_watchlist=wl['active'],
            total_alerts=al['total'],
            new_alerts=al['new'],
            critical_alerts=al['critical'],
            alerts_this_week=al['this_week'],
            searches_this_month=0,  # TODO
            plan_usage={
                "watchlist": {"used": wl['active'], "limit": org.get('max_watchlist_items') or 100},
                "users": {"used": 0, "limit": 10},  # TODO
                "searches": {"used": 0, "limit": org.get('max_reports_per_month') or 100}
            }
        )


# ==========================================
# Admin Routes - IDF Management
# ==========================================

@admin_router.get("/idf-stats")
async def get_idf_stats(user: CurrentUser = Depends(get_current_user)):
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
    user: CurrentUser = Depends(get_current_user)
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
    user: CurrentUser = Depends(get_current_user)
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
    user: CurrentUser = Depends(get_current_user)
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
    user: CurrentUser = Depends(require_role(UserRole.ADMIN))
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

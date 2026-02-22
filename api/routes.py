"""
API Routes â€” Router registry
Imports domain routers from focused modules and re-exports them
for backward compatibility with main.py and tests.
"""
import io
import os
import re
import logging
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
    CurrentUser, get_current_user, require_role,
    hash_password, verify_password,
)
from models.schemas import (
    # Organization
    OrganizationCreate, OrganizationUpdate, OrganizationResponse, OrganizationStats,
    # Watchlist
    WatchlistItemCreate, WatchlistItemUpdate, WatchlistItemResponse,
    WatchlistBulkImport, WatchlistBulkImportResult,
    FileUploadResult, FileUploadSummary, FileUploadWarning,
    FileUploadSkippedItem, FileUploadErrorItem,
    ColumnDetectionResponse, ColumnAutoMappings, ColumnMapping,
    # Alerts
    AlertResponse, AlertUpdate, AlertAcknowledge, AlertResolve, AlertDismiss,
    AlertStatus, AlertSeverity, AlertDigest,
    # Common
    PaginatedResponse, SuccessResponse, DashboardStats
)
from database.crud import (
    Database, get_db_connection,
    OrganizationCRUD, UserCRUD, WatchlistCRUD, AlertCRUD
)

logger = logging.getLogger(__name__)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)


# ==========================================
# Import domain routers from focused modules
# ==========================================

# Auth: login, register, password, email verification
from api.auth_routes import auth_router

# User profile + user management: profile CRUD, avatar, org profile, admin user ops
from api.user_profile_routes import user_profile_router, users_router

# Re-export request models for backward compatibility
from api.user_profile_routes import ProfileUpdateRequest, OrganizationProfileUpdate

# Alert routes: list, get, acknowledge, resolve, dismiss, digest
from api.alert_routes import alerts_router

# Newly extracted domain routes
from api.org_routes import org_router
from api.dashboard_routes import dashboard_router
from api.admin_routes import admin_router
from api.trademark_routes import trademark_router
from api.usage_routes import usage_router

# ==========================================
# Remaining routers defined inline (to be extracted in future)
# ==========================================

watchlist_router = APIRouter(prefix="/watchlist", tags=["Watchlist"])





# NOTE: Auth routes (register, login, password, email verification) â†’ api/auth_routes.py
# NOTE: User profile routes (profile CRUD, avatar, org profile) â†’ api/user_profile_routes.py
# NOTE: User management routes (list/create/update/deactivate users) â†’ api/user_profile_routes.py



# NOTE: Organization routes -> api/org_routes.py
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
    """Add trademark to watchlist â€” auto-copies AI embeddings & logo from trademarks DB when application_no is provided."""
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
    'marka adÄ±', 'marka adi', 'marka', 'trademark_name', 'trademark name',
    'brand name', 'brand_name', 'name', 'isim'
]

APP_NO_VARIANTS = [
    'baÅŸvuru no', 'baÅŸvuru numarasÄ±', 'baÅŸvuru no.',
    'basvuru no', 'basvuru numarasi', 'basvuru no.',
    'application no', 'application number', 'application_no',
    'app no', 'app_no', 'application'
]

CLASS_VARIANTS = [
    'sÄ±nÄ±f', 'sÄ±nÄ±flar', 'sÄ±nÄ±f no', 'sÄ±nÄ±f numarasÄ±',
    'sinif', 'siniflar', 'sinif no', 'sinif numarasi',
    'nice class', 'nice classes', 'nice_class', 'nice_classes',
    'class', 'classes', 'class no'
]

BULLETIN_VARIANTS = [
    'bÃ¼lten no', 'bÃ¼lten numarasÄ±', 'bÃ¼lten',
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
        ("Marka AdÄ± *", True),      # Required
        ("BaÅŸvuru No *", True),     # Required
        ("SÄ±nÄ±flar *", True),       # Required
        ("BÃ¼lten No", False)        # Optional
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
        ["Ã–RNEK MARKA 1", "2023/12345", "9, 35", "305"],
        ["Ã–RNEK MARKA 2", "2023/67890", "25, 35, 42", "306"],
        ["Ã–RNEK MARKA 3", "2022/11111", "30, 43", ""],
    ]

    for row_idx, row_data in enumerate(sample_data, 2):
        for col_idx, value in enumerate(row_data, 1):
            ws.cell(row=row_idx, column=col_idx, value=value)

    # Instructions
    ws.cell(row=6, column=1, value="* Zorunlu sÃ¼tunlar. BÃ¼lten No opsiyoneldir.")
    ws.cell(row=6, column=1).font = Font(italic=True, color="666666")

    ws.cell(row=7, column=1, value="SÄ±nÄ±flar: VirgÃ¼lle ayÄ±rarak yazÄ±n (Ã¶rn: 9, 35, 42)")
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

                # Application number (optional â€” auto-generate if missing)
                app_no = str(row.get('application_no', '')).strip()
                if not app_no or app_no.lower() in ['nan', 'none', '']:
                    app_no = f"WL-{uuid4().hex[:8].upper()}"

                # Nice classes (optional â€” default to empty list)
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
                    "message": "Desteklenmeyen dosya formatÄ±",
                    "detail": "LÃ¼tfen Excel (.xlsx, .xls) veya CSV (.csv) dosyasÄ± yÃ¼kleyin."
                }
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "parse_error",
                "message": "Dosya okunamadÄ±",
                "detail": str(e)
            }
        )

    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "empty_file",
                "message": "Dosya boÅŸ"
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
            "column": "Marka AdÄ±",
            "variants": "marka adÄ±, brand name, name, isim",
            "reason": "Hangi markalarÄ±n izleneceÄŸini belirler"
        })

    if not app_no_col:
        missing_columns.append({
            "column": "BaÅŸvuru No",
            "variants": "baÅŸvuru no, application no, app no",
            "reason": "MÃ¼kerrer kontrol ve Ã§akÄ±ÅŸma filtreleme iÃ§in gerekli"
        })

    if not class_col:
        missing_columns.append({
            "column": "SÄ±nÄ±flar",
            "variants": "sÄ±nÄ±f, sÄ±nÄ±flar, nice class, classes",
            "reason": "Hangi sÄ±nÄ±flarda arama yapÄ±lacaÄŸÄ±nÄ± belirler"
        })

    if missing_columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_mandatory_columns",
                "message": f"{len(missing_columns)} zorunlu sÃ¼tun eksik",
                "missing_columns": missing_columns,
                "found_columns": original_columns,
                "required_columns": [
                    {"name": "Marka AdÄ±", "variants": "marka adÄ±, brand name, name"},
                    {"name": "BaÅŸvuru No", "variants": "baÅŸvuru no, application no"},
                    {"name": "SÄ±nÄ±flar", "variants": "sÄ±nÄ±f, sÄ±nÄ±flar, nice class, classes"}
                ],
                "optional_columns": [
                    {"name": "BÃ¼lten No", "variants": "bÃ¼lten no, bulletin no"}
                ],
                "example": {
                    "headers": ["Marka AdÄ±", "BaÅŸvuru No", "SÄ±nÄ±flar", "BÃ¼lten No"],
                    "rows": [
                        ["Ã–RNEK MARKA", "2023/12345", "9, 35, 42", "305"],
                        ["DÄ°ÄžER MARKA", "2023/67890", "25, 35", "306"]
                    ]
                }
            }
        )

    # Warnings for optional columns
    warnings = []
    if not bulletin_col:
        warnings.append(FileUploadWarning(
            column="BÃ¼lten No",
            message="BÃ¼lten numarasÄ± sÃ¼tunu bulunamadÄ±. Bu opsiyonel bir alandÄ±r."
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
                        error="Marka adÄ± boÅŸ"
                    ))
                    continue

                # Validate application number (required)
                app_no = str(row.get(app_no_col, '')).strip()
                if not app_no or app_no.lower() in ['nan', 'none', '']:
                    error_count += 1
                    error_items.append(FileUploadErrorItem(
                        row=row_num,
                        brand_name=brand_name,
                        error="BaÅŸvuru numarasÄ± boÅŸ"
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
                        error="SÄ±nÄ±f bilgisi boÅŸ veya geÃ§ersiz"
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
        message_parts.append(f"{skipped_count} zaten mevcut (atlandÄ±)")
    if error_count > 0:
        message_parts.append(f"{error_count} hatalÄ± satÄ±r")

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
        msg += f" â€” plan limitiniz nedeniyle {_scan_max} marka tarandi"
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
    logger.info(f"ðŸ” [SCAN START] Scanning watchlist item {item_id}")
    try:
        from watchlist.scanner import get_scanner
        scanner = get_scanner()  # Reuse cached scanner with loaded models
        # Ensure connection is clean (rollback any aborted transaction)
        try:
            scanner.conn.rollback()
        except Exception:
            pass
        alerts_count = scanner.scan_single_watchlist(item_id)
        logger.info(f"âœ… [SCAN COMPLETE] Item {item_id}: {alerts_count} alerts created")
    except Exception as e:
        logger.error(f"âŒ [SCAN FAILED] Item {item_id}: {e}")
        logger.error(f"   Traceback: {traceback.format_exc()}")
        # Reset singleton scanner to get fresh connection on next scan
        from watchlist.scanner import reset_scanner
        reset_scanner()



# NOTE: Alert routes (list, get, acknowledge, resolve, dismiss, digest) -> api/alert_routes.py


# NOTE: Dashboard routes -> api/dashboard_routes.py
# NOTE: Admin routes -> api/admin_routes.py
# NOTE: Trademark routes -> api/trademark_routes.py
# NOTE: Usage routes -> api/usage_routes.py

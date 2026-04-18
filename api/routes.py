"""
API Routes â€” Router registry
Imports domain routers from focused modules and re-exports them
for backward compatibility with main.py and tests.
"""
import io
import os
import re
import psycopg2
import logging
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID, uuid4

import pandas as pd
from fastapi import Depends, HTTPException, status, UploadFile, File, Form, Query, BackgroundTasks, Body, Request
from pydantic import BaseModel as PydanticBaseModel, Field
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
    PortfolioPreviewRequest, PortfolioPreviewResponse,
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
from api.watchlist_routes import (
    BulkFromPortfolioRequest,
    bulk_import_from_portfolio,
    bulk_import_watchlist,
    create_watchlist_item,
    delete_all_watchlist,
    delete_watchlist_logo,
    delete_watchlist_item,
    detect_columns,
    download_template,
    get_scan_status,
    get_watchlist_item,
    get_watchlist_logo,
    list_watchlist,
    preview_portfolio_import,
    rescan_all_watchlist,
    trigger_scan_all,
    trigger_scan,
    upload_file,
    upload_watchlist_logo,
    upload_with_mapping,
    update_all_threshold,
    update_watchlist_item,
    watchlist_router,
    watchlist_stats,
)

# NOTE: Auth routes (register, login, password, email verification) â†’ api/auth_routes.py
# NOTE: User profile routes (profile CRUD, avatar, org profile) â†’ api/user_profile_routes.py
# NOTE: User management routes (list/create/update/deactivate users) â†’ api/user_profile_routes.py


# NOTE: Watchlist upload/template routes -> api/watchlist_routes.py
# NOTE: Watchlist logo routes -> api/watchlist_routes.py


# NOTE: Alert routes (list, get, acknowledge, resolve, dismiss, digest) -> api/alert_routes.py


# NOTE: Dashboard routes -> api/dashboard_routes.py
# NOTE: Admin routes -> api/admin_routes.py
# NOTE: Trademark routes -> api/trademark_routes.py
# NOTE: Usage routes -> api/usage_routes.py

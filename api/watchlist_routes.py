"""Watchlist routes extracted from the legacy api.routes module."""

import io
import logging
import re
import threading
from typing import List, Optional
from uuid import UUID, uuid4

import pandas as pd
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from pydantic import BaseModel as PydanticBaseModel, Field

from auth.authentication import CurrentUser, get_current_user
from database.crud import Database, WatchlistCRUD
from models.schemas import (
    ColumnAutoMappings,
    ColumnDetectionResponse,
    FileUploadErrorItem,
    FileUploadResult,
    FileUploadSkippedItem,
    FileUploadSummary,
    FileUploadWarning,
    PaginatedResponse,
    PortfolioPreviewRequest,
    PortfolioPreviewResponse,
    SuccessResponse,
    WatchlistBulkImport,
    WatchlistBulkImportResult,
    WatchlistItemCreate,
    WatchlistItemResponse,
    WatchlistItemUpdate,
)
from api.watchlist_background import run_watchlist_scan_task

logger = logging.getLogger(__name__)

watchlist_router = APIRouter(prefix="/watchlist", tags=["Watchlist"])


# ==========================================
# Column name variants for file upload
# ==========================================

BRAND_NAME_VARIANTS = [
    "marka adı",
    "marka adi",
    "marka",
    "trademark_name",
    "trademark name",
    "brand name",
    "brand_name",
    "name",
    "isim",
]

APP_NO_VARIANTS = [
    "başvuru no",
    "başvuru numarası",
    "başvuru no.",
    "basvuru no",
    "basvuru numarasi",
    "basvuru no.",
    "application no",
    "application number",
    "application_no",
    "app no",
    "app_no",
    "application",
]

CLASS_VARIANTS = [
    "sınıf",
    "sınıflar",
    "sınıf no",
    "sınıf numarası",
    "sinif",
    "siniflar",
    "sinif no",
    "sinif numarasi",
    "nice class",
    "nice classes",
    "nice_class",
    "nice_classes",
    "class",
    "classes",
    "class no",
]

BULLETIN_VARIANTS = [
    "bülten no",
    "bülten numarası",
    "bülten",
    "bulten no",
    "bulten numarasi",
    "bulten",
    "bulletin no",
    "bulletin number",
    "bulletin",
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
    numbers = re.findall(r"\d+", value_str)

    classes = []
    for num in numbers:
        n = int(num)
        if 1 <= n <= 45:
            classes.append(n)

    return sorted(list(set(classes)))


@watchlist_router.get("/stats")
async def watchlist_stats(
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get aggregate stats for the organization's watchlist."""
    from services.watchlist_service import get_watchlist_stats_summary

    return await get_watchlist_stats_summary(
        current_user=current_user,
        min_score=min_score,
    )


@watchlist_router.get("", response_model=PaginatedResponse)
async def list_watchlist(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    active_only: bool = True,
    search: Optional[str] = Query(None, min_length=1, max_length=200),
    sort: Optional[str] = Query(None),
    renewal_only: bool = Query(False),
    appeals_only: bool = Query(False),
    status_filter: Optional[str] = Query(None),
    threshold: float = Query(0.70, ge=0.0, le=100.0),
    tm_status: Optional[str] = Query(None, max_length=50),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List watchlist items for organization."""
    from services.watchlist_service import get_watchlist_page

    payload = await get_watchlist_page(
        current_user=current_user,
        page=page,
        page_size=page_size,
        active_only=active_only,
        search=search,
        sort=sort,
        renewal_only=renewal_only,
        appeals_only=appeals_only,
        status_filter=status_filter,
        threshold=threshold,
        tm_status=tm_status,
        logger=logger,
    )
    return PaginatedResponse(**payload)


@watchlist_router.post("", response_model=WatchlistItemResponse)
async def create_watchlist_item(
    data: WatchlistItemCreate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Add trademark to watchlist and trigger an initial scan."""
    from services.watchlist_service import create_watchlist_item_record

    payload = await create_watchlist_item_record(
        data=data,
        current_user=current_user,
    )
    background_tasks.add_task(run_watchlist_scan_task, payload["scan_item_id"])
    return WatchlistItemResponse(**payload["item"])


@watchlist_router.post("/bulk", response_model=WatchlistBulkImportResult)
async def bulk_import_watchlist(
    data: WatchlistBulkImport,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Bulk import watchlist items."""
    from services.watchlist_service import import_watchlist_items_bulk

    payload = await import_watchlist_items_bulk(
        data=data,
        current_user=current_user,
    )
    for item_id in payload["scan_item_ids"]:
        background_tasks.add_task(run_watchlist_scan_task, item_id)
    result = dict(payload["result"])
    result["queued_scans"] = len(payload["scan_item_ids"])
    return WatchlistBulkImportResult(**result)



@watchlist_router.post("/portfolio-preview", response_model=PortfolioPreviewResponse)
async def preview_portfolio_import(
    data: PortfolioPreviewRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Preview bulk import from portfolio to calculate duplicates before confirming."""
    from services.watchlist_service import preview_watchlist_portfolio_import

    payload = await preview_watchlist_portfolio_import(
        data=data,
        current_user=current_user,
    )
    return PortfolioPreviewResponse(**payload)



class BulkFromPortfolioRequest(PydanticBaseModel):
    holder_id: Optional[str] = None
    attorney_no: Optional[str] = None


@watchlist_router.post("/bulk-from-portfolio", response_model=WatchlistBulkImportResult)
async def bulk_import_from_portfolio(
    data: BulkFromPortfolioRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Bulk import watchlist items from a holder or attorney portfolio."""
    from services.watchlist_service import import_watchlist_items_from_portfolio

    payload = await import_watchlist_items_from_portfolio(
        data=data,
        current_user=current_user,
    )
    for item_id in payload["scan_item_ids"]:
        background_tasks.add_task(run_watchlist_scan_task, item_id)
    result = dict(payload["result"])
    result["queued_scans"] = len(payload["scan_item_ids"])
    return WatchlistBulkImportResult(**result)



@watchlist_router.get("/upload/template")
async def download_template():
    """Generate Excel template with mandatory columns."""
    from services.watchlist_service import build_watchlist_upload_template

    output = build_watchlist_upload_template()
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=marka_listesi_sablon.xlsx"},
    )



@watchlist_router.post("/upload/detect-columns", response_model=ColumnDetectionResponse)
async def detect_columns(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Read file and return column names for mapping UI."""
    from services.watchlist_service import detect_watchlist_upload_columns

    contents = await file.read()
    payload = detect_watchlist_upload_columns(
        contents=contents,
        filename=file.filename or "",
        brand_name_variants=BRAND_NAME_VARIANTS,
        application_no_variants=APP_NO_VARIANTS,
        class_variants=CLASS_VARIANTS,
        bulletin_variants=BULLETIN_VARIANTS,
        find_column=_find_column,
    )
    return ColumnDetectionResponse(**payload)



@watchlist_router.post("/upload/with-mapping", response_model=FileUploadResult)
async def upload_with_mapping(
    file: UploadFile = File(...),
    column_mapping: str = Form(...),
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upload file with custom column mappings."""
    contents = await file.read()
    from services.watchlist_service import import_watchlist_upload_with_mapping

    payload = await import_watchlist_upload_with_mapping(
        contents=contents,
        filename=file.filename,
        column_mapping=column_mapping,
        current_user=current_user,
    )
    if background_tasks:
        for item_id in payload["scan_item_ids"]:
            background_tasks.add_task(run_watchlist_scan_task, item_id)
    result = payload["result"]
    if isinstance(result, dict):
        result = dict(result)
        result["queued_scans"] = len(payload["scan_item_ids"])
    else:
        result.queued_scans = len(payload["scan_item_ids"])
    return result



@watchlist_router.post("/upload", response_model=FileUploadResult)
async def upload_file(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upload Excel/CSV file with mandatory column validation."""
    contents = await file.read()
    from services.watchlist_service import import_watchlist_upload_file

    payload = await import_watchlist_upload_file(
        contents=contents,
        filename=file.filename,
        current_user=current_user,
        brand_name_variants=BRAND_NAME_VARIANTS,
        application_no_variants=APP_NO_VARIANTS,
        class_variants=CLASS_VARIANTS,
        bulletin_variants=BULLETIN_VARIANTS,
        find_column=_find_column,
        parse_nice_classes=_parse_nice_classes,
    )
    if background_tasks:
        for item_id in payload["scan_item_ids"]:
            background_tasks.add_task(run_watchlist_scan_task, item_id)
    result = payload["result"]
    if isinstance(result, dict):
        result = dict(result)
        result["queued_scans"] = len(payload["scan_item_ids"])
    else:
        result.queued_scans = len(payload["scan_item_ids"])
    return result



@watchlist_router.post("/{item_id}/logo", response_model=SuccessResponse)
async def upload_watchlist_logo(
    item_id: UUID,
    background_tasks: BackgroundTasks,
    logo: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upload a logo image for a watchlist item. Generates visual embeddings in background."""
    contents = await logo.read()
    from services.watchlist_service import store_watchlist_logo_upload

    payload = await store_watchlist_logo_upload(
        item_id=item_id,
        current_user=current_user,
        logo_filename=logo.filename or "logo.png",
        content_type=logo.content_type,
        contents=contents,
    )
    background_tasks.add_task(
        _start_watchlist_logo_thread,
        payload["item_id"],
        payload["filepath"],
    )
    return SuccessResponse(success=payload["success"], message=payload["message"])


@watchlist_router.get("/{item_id}/logo")
async def get_watchlist_logo(
    item_id: UUID,
):
    """Get the logo image for a watchlist item (no auth - served as <img src>)"""
    from fastapi.responses import FileResponse as FR
    from services.watchlist_service import resolve_watchlist_logo_file

    payload = await resolve_watchlist_logo_file(item_id=item_id)
    return FR(payload["path"], media_type=payload["media_type"])


@watchlist_router.delete("/{item_id}/logo", response_model=SuccessResponse)
async def delete_watchlist_logo(
    item_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Remove the logo from a watchlist item and clear visual embeddings"""
    from services.watchlist_service import delete_watchlist_logo_asset

    payload = await delete_watchlist_logo_asset(
        item_id=item_id,
        current_user=current_user,
    )
    return SuccessResponse(**payload)


def _process_watchlist_logo(item_id: UUID, filepath: str):
    """Background task: generate CLIP, DINOv2, color, OCR embeddings for uploaded logo"""
    from services.watchlist_service import process_watchlist_logo_embeddings

    process_watchlist_logo_embeddings(
        item_id=item_id,
        filepath=filepath,
        logger=logger,
    )


def _start_watchlist_logo_thread(item_id: UUID, filepath: str):
    """Detach logo embedding work from the request lifecycle."""
    thread = threading.Thread(
        target=_process_watchlist_logo,
        args=(item_id, filepath),
        daemon=True,
        name=f"watchlist-logo-{item_id}",
    )
    thread.start()


@watchlist_router.post("/scan-all", response_model=SuccessResponse)
async def trigger_scan_all(
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Scan all active watchlist items for the organization."""
    from services.watchlist_service import prepare_watchlist_scan_all

    payload = await prepare_watchlist_scan_all(
        current_user=current_user,
    )
    for item_id in payload["item_ids"]:
        background_tasks.add_task(run_watchlist_scan_task, item_id)
    return SuccessResponse(
        success=payload["success"],
        message=payload["message"],
    )



@watchlist_router.get("/scan-status")
async def get_scan_status(
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get auto-scan schedule status and next scan time."""
    from services.watchlist_service import get_watchlist_scan_status

    return await get_watchlist_scan_status(current_user=current_user)



@watchlist_router.delete("/all", response_model=SuccessResponse)
async def delete_all_watchlist(
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete all watchlist items and alerts for the organization."""
    from services.watchlist_service import delete_all_watchlist_records

    payload = await delete_all_watchlist_records(
        current_user=current_user,
    )
    return SuccessResponse(**payload)



@watchlist_router.post("/rescan", response_model=SuccessResponse)
async def rescan_all_watchlist(
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Clear old alerts and rescan all watchlist items fresh."""
    from services.watchlist_service import prepare_watchlist_rescan

    payload = await prepare_watchlist_rescan(
        current_user=current_user,
    )
    for item_id in payload["item_ids"]:
        background_tasks.add_task(run_watchlist_scan_task, item_id)
    return SuccessResponse(
        success=payload["success"],
        message=payload["message"],
    )



class _BulkThresholdUpdate(PydanticBaseModel):
    threshold: float = Field(..., ge=0.5, le=0.95)


@watchlist_router.put("/bulk-threshold", response_model=SuccessResponse)
async def update_all_threshold(
    data: _BulkThresholdUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update alert_threshold for all active watchlist items in this org."""
    from services.watchlist_service import update_watchlist_bulk_thresholds

    payload = await update_watchlist_bulk_thresholds(
        threshold=data.threshold,
        current_user=current_user,
    )
    return SuccessResponse(**payload)



@watchlist_router.get("/{item_id}", response_model=WatchlistItemResponse)
async def get_watchlist_item(
    item_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get watchlist item details."""
    from services.watchlist_service import get_watchlist_item_detail

    item = await get_watchlist_item_detail(
        item_id=item_id,
        current_user=current_user,
    )
    return WatchlistItemResponse(**item)


@watchlist_router.put("/{item_id}", response_model=WatchlistItemResponse)
async def update_watchlist_item(
    item_id: UUID,
    data: WatchlistItemUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update watchlist item settings."""
    from services.watchlist_service import update_watchlist_item_record

    item = await update_watchlist_item_record(
        item_id=item_id,
        data=data,
        current_user=current_user,
    )
    return WatchlistItemResponse(**item)


@watchlist_router.delete("/{item_id}", response_model=SuccessResponse)
async def delete_watchlist_item(
    item_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Remove a watchlist item and its alerts."""
    from services.watchlist_service import delete_watchlist_item_record

    payload = await delete_watchlist_item_record(
        item_id=item_id,
        current_user=current_user,
    )
    return SuccessResponse(**payload)


@watchlist_router.post("/{item_id}/scan", response_model=SuccessResponse)
async def trigger_scan(
    item_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Manually trigger a scan for a watchlist item."""
    from services.watchlist_service import prepare_watchlist_item_scan

    payload = await prepare_watchlist_item_scan(
        item_id=item_id,
        current_user=current_user,
    )
    background_tasks.add_task(run_watchlist_scan_task, payload["item_id"])
    return SuccessResponse(success=payload["success"], message=payload["message"])

"""
File Upload API for Trademark Data
Allows customers to upload Excel/CSV files with their trademarks
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile

from auth.authentication import CurrentUser, get_current_user
from services.upload_service import (
    COLUMN_ALIASES,
    build_upload_template_response,
    find_column,
    parse_nice_classes,
    process_trademark_upload,
)

router = APIRouter(prefix="/api/v1/upload", tags=["upload"])


@router.post("/trademarks")
async def upload_trademarks(
    file: UploadFile = File(...),
    add_to_watchlist: bool = Form(True),
    run_analysis: bool = Form(False),
    alert_threshold: Optional[float] = Form(None, include_in_schema=False),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Upload Excel/CSV file with trademarks.

    Parameters:
    - file: Excel (.xlsx, .xls) or CSV file
    - add_to_watchlist: Add trademarks to watchlist (default: True)
    - run_analysis: Run conflict analysis (default: False)

    Returns:
    - List of parsed trademarks
    - Validation errors if any
    - Watchlist results if add_to_watchlist=True
    """
    return await process_trademark_upload(
        file=file,
        add_to_watchlist=add_to_watchlist,
        run_analysis=run_analysis,
        alert_threshold=None,
        current_user=current_user,
    )


@router.get("/template")
async def download_template():
    """
    Download sample Excel template for trademark upload.
    """
    return build_upload_template_response()

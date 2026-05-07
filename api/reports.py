"""
Reports API
===========
Generate, list, and download reports.
"""

import logging

from fastapi import APIRouter, Depends, Query

from auth.authentication import CurrentUser, get_current_user
from models.schemas import ReportRequest
from services.report_service import (
    build_report_download_response,
    delete_all_reports_data,
    delete_report_data,
    generate_report_data,
    get_report_data,
    list_reports_data,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.post("/generate")
async def generate_report_endpoint(
    request: ReportRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Generate a new report.

    Downloadable reports are not monthly-limited here; the monthly risk-report
    quota is enforced by the search risk-report endpoint.
    """
    return await generate_report_data(
        request=request,
        current_user=current_user,
    )


@router.get("")
async def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List reports for current user's organization."""
    return await list_reports_data(
        page=page,
        page_size=page_size,
        current_user=current_user,
    )


@router.delete("")
async def delete_all_reports(
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete all reports for current user's organization."""
    return await delete_all_reports_data(
        current_user=current_user,
    )


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get a single report by ID."""
    return await get_report_data(
        report_id=report_id,
        current_user=current_user,
    )


@router.delete("/{report_id}")
async def delete_report(
    report_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete a single report for current user's organization."""
    return await delete_report_data(
        report_id=report_id,
        current_user=current_user,
    )


@router.get("/{report_id}/download")
async def download_report(
    report_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download a completed report file."""
    return await build_report_download_response(
        report_id=report_id,
        current_user=current_user,
    )

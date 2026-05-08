"""Lead Feed API - Opposition Radar."""

from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from auth.authentication import CurrentUser, get_current_user
from services.lead_service import (
    LEADS_PER_PAGE,
    dismiss_lead_data,
    export_cancellations_csv_data,
    export_leads_csv_data,
    export_renewals_csv_data,
    get_cancellation_feed_data,
    get_lead_credits_data,
    get_lead_detail_data,
    get_lead_feed_data,
    get_lead_stats_data,
    get_renewal_feed_data,
    get_renewal_stats_data,
    mark_lead_contacted_data,
    mark_lead_converted_data,
)


class LeadResponse(BaseModel):
    id: str
    new_mark_name: Optional[str] = None
    new_mark_app_no: Optional[str] = None
    new_mark_holder_name: Optional[str] = None
    new_mark_nice_classes: Optional[List[int]] = None
    new_mark_image: Optional[str] = None
    existing_mark_name: Optional[str] = None
    existing_mark_app_no: Optional[str] = None
    existing_mark_holder_name: Optional[str] = None
    existing_mark_nice_classes: Optional[List[int]] = None
    existing_mark_image: Optional[str] = None
    similarity_score: float
    text_similarity: Optional[float] = None
    semantic_similarity: Optional[float] = None
    visual_similarity: Optional[float] = None
    translation_similarity: Optional[float] = None
    risk_level: str
    conflict_type: str
    overlapping_classes: Optional[List[int]] = None
    conflict_reasons: Optional[List[str]] = None
    bulletin_no: Optional[str] = None
    bulletin_date: Optional[date] = None
    opposition_deadline: date
    days_until_deadline: int
    urgency_level: str
    new_mark_application_date: Optional[date] = None
    existing_mark_application_date: Optional[date] = None
    new_mark_has_extracted_goods: bool = False
    existing_mark_has_extracted_goods: bool = False
    lead_status: str
    created_at: datetime


class LeadStatsResponse(BaseModel):
    total_leads: int
    critical_leads: int
    urgent_leads: int
    upcoming_leads: int
    new_leads: int
    viewed_leads: int
    contacted_leads: int
    converted_leads: int
    avg_similarity: Optional[float] = None
    last_scan_at: Optional[datetime] = None


class LeadActionResponse(BaseModel):
    success: bool
    message: str
    lead_id: str
    new_status: str


router = APIRouter(prefix="/leads", tags=["Opposition Radar"])


@router.get("/feed")
async def get_lead_feed(
    urgency: Optional[str] = Query(None, description="Filter: 'critical', 'urgent', 'soon', 'all'"),
    nice_class: Optional[int] = Query(None, description="Filter by Nice class"),
    min_score: Optional[float] = Query(0.6, ge=0.0, le=1.0, description="Minimum similarity score"),
    status: Optional[str] = Query("new", description="Lead status: 'new', 'viewed', 'all'"),
    search: Optional[str] = Query(None, description="Search brand name or holder name"),
    page: int = Query(1, ge=1),
    limit: int = Query(LEADS_PER_PAGE, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get the paginated opposition lead feed."""
    return await get_lead_feed_data(
        urgency=urgency,
        nice_class=nice_class,
        min_score=min_score,
        status=status,
        search=search,
        page=page,
        limit=limit,
        current_user=current_user,
    )


@router.get("/stats", response_model=LeadStatsResponse)
async def get_lead_stats(current_user: CurrentUser = Depends(get_current_user)):
    """Get lead statistics for the Opposition Radar dashboard."""
    return await get_lead_stats_data(current_user=current_user)


@router.get("/credits")
async def get_lead_credits(current_user: CurrentUser = Depends(get_current_user)):
    """Get the authenticated user's lead credit status."""
    return await get_lead_credits_data(current_user=current_user)


@router.get("/export/csv")
async def export_leads_csv(
    urgency: Optional[str] = Query(None),
    nice_class: Optional[int] = Query(None),
    min_score: Optional[float] = Query(0.6, ge=0.0, le=1.0),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Export opposition leads as CSV."""
    return await export_leads_csv_data(
        urgency=urgency,
        nice_class=nice_class,
        min_score=min_score,
        current_user=current_user,
    )


@router.get("/{lead_id}")
async def get_lead_detail(
    lead_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get detailed information for a specific lead."""
    return await get_lead_detail_data(
        lead_id=lead_id,
        current_user=current_user,
    )


@router.post("/{lead_id}/contact", response_model=LeadActionResponse)
async def mark_lead_contacted(
    lead_id: str,
    notes: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Mark a lead as contacted."""
    # Access validation still runs inside lead_service._require_lead_access.
    return await mark_lead_contacted_data(
        lead_id=lead_id,
        notes=notes,
        current_user=current_user,
    )


@router.post("/{lead_id}/convert", response_model=LeadActionResponse)
async def mark_lead_converted(
    lead_id: str,
    notes: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Mark a lead as converted."""
    # Access validation still runs inside lead_service._require_lead_access.
    return await mark_lead_converted_data(
        lead_id=lead_id,
        notes=notes,
        current_user=current_user,
    )


@router.post("/{lead_id}/dismiss", response_model=LeadActionResponse)
async def dismiss_lead(
    lead_id: str,
    reason: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Dismiss a lead."""
    # Access validation still runs inside lead_service._require_lead_access.
    return await dismiss_lead_data(
        lead_id=lead_id,
        reason=reason,
        current_user=current_user,
    )


@router.get("/renewals/stats")
async def get_renewal_stats(current_user: CurrentUser = Depends(get_current_user)):
    """Get renewal lead statistics."""
    return await get_renewal_stats_data(current_user=current_user)


@router.get("/renewals/feed")
async def get_renewal_feed(
    urgency: Optional[str] = Query(None, description="Filter: 'grace_period', 'critical', 'urgent', 'upcoming', 'all'"),
    nice_class: Optional[int] = Query(None, description="Filter by Nice class"),
    search: Optional[str] = Query(None, description="Search brand name or holder name"),
    page: int = Query(1, ge=1),
    limit: int = Query(LEADS_PER_PAGE, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get the paginated renewal lead feed."""
    return await get_renewal_feed_data(
        urgency=urgency,
        nice_class=nice_class,
        search=search,
        page=page,
        limit=limit,
        current_user=current_user,
    )


@router.get("/renewals/export/csv")
async def export_renewals_csv(
    urgency: Optional[str] = Query(None),
    nice_class: Optional[int] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Export renewal leads as CSV."""
    return await export_renewals_csv_data(
        urgency=urgency,
        nice_class=nice_class,
        current_user=current_user,
    )


@router.get("/cancellations/feed")
async def get_cancellation_feed(
    nice_class: Optional[int] = Query(None, description="Filter by Nice class"),
    search: Optional[str] = Query(None, description="Search brand name or holder name"),
    page: int = Query(1, ge=1),
    limit: int = Query(LEADS_PER_PAGE, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get the paginated cancellation lead feed (recently-cancelled marks)."""
    return await get_cancellation_feed_data(
        nice_class=nice_class,
        search=search,
        page=page,
        limit=limit,
        current_user=current_user,
    )


@router.get("/cancellations/export/csv")
async def export_cancellations_csv(
    nice_class: Optional[int] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Export cancellation leads as CSV."""
    return await export_cancellations_csv_data(
        nice_class=nice_class,
        current_user=current_user,
    )

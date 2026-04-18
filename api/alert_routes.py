"""
Alert Routes — view, acknowledge, resolve, dismiss alerts
Extracted from api/routes.py for maintainability.
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from auth.authentication import CurrentUser, get_current_user
from models.schemas import (
    AlertAcknowledge,
    AlertDismiss,
    AlertResolve,
    AlertResponse,
    AlertSeverity,
    AlertStatus,
    PaginatedResponse,
)
from services.alert_service import (
    acknowledge_alert_data,
    aggregate_alerts_data,
    dismiss_alert_data,
    get_alert_data,
    get_alerts_summary_data,
    list_alerts_data,
    resolve_alert_data,
)

logger = logging.getLogger(__name__)

alerts_router = APIRouter(prefix="/alerts", tags=["Alerts"])


@alerts_router.get("", response_model=PaginatedResponse)
async def list_alerts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[List[AlertStatus]] = Query(None),
    severity: Optional[List[AlertSeverity]] = Query(None),
    watchlist_id: Optional[UUID] = None,
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List alerts for organization with filtering."""
    return await list_alerts_data(
        page=page,
        page_size=page_size,
        status_filters=status,
        severity_filters=severity,
        watchlist_id=watchlist_id,
        min_score=min_score,
        current_user=current_user,
    )


@alerts_router.get("/summary", response_model=dict)
async def get_alerts_summary(
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get alerts summary by status and severity."""
    return await get_alerts_summary_data(current_user=current_user)


@alerts_router.get("/aggregate")
async def aggregate_alerts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    severity: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get all alerts across all watchlist items, sorted by deadline urgency."""
    return await aggregate_alerts_data(
        page=page,
        page_size=page_size,
        severity=severity,
        current_user=current_user,
    )


@alerts_router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get alert details."""
    return await get_alert_data(
        alert_id=alert_id,
        current_user=current_user,
    )


@alerts_router.post("/{alert_id}/acknowledge", response_model=AlertResponse)
async def acknowledge_alert(
    alert_id: UUID,
    data: AlertAcknowledge,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Acknowledge alert."""
    return await acknowledge_alert_data(
        alert_id=alert_id,
        data=data,
        current_user=current_user,
    )


@alerts_router.post("/{alert_id}/resolve", response_model=AlertResponse)
async def resolve_alert(
    alert_id: UUID,
    data: AlertResolve,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Resolve alert."""
    return await resolve_alert_data(
        alert_id=alert_id,
        data=data,
        current_user=current_user,
    )


@alerts_router.post("/{alert_id}/dismiss", response_model=AlertResponse)
async def dismiss_alert(
    alert_id: UUID,
    data: AlertDismiss,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Dismiss alert (false positive)."""
    return await dismiss_alert_data(
        alert_id=alert_id,
        data=data,
        current_user=current_user,
    )

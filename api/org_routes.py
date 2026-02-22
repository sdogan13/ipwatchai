"""
Organization Routes
Extracted from api/routes.py for maintainability.
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException, status, BackgroundTasks, Request, Body
from auth.authentication import CurrentUser, get_current_user, require_role
from models.schemas import (
    PaginatedResponse, SuccessResponse, OrganizationResponse, OrganizationUpdate, OrganizationCreate, OrganizationStats
)
from database.crud import Database, OrganizationCRUD, UserCRUD
from pydantic import BaseModel as PydanticBaseModel

logger = logging.getLogger(__name__)

class ThresholdUpdateRequest(PydanticBaseModel):
    """Request model for threshold update"""
    threshold: float

org_router = APIRouter(prefix="/organization", tags=["Organization"])
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
        org_id = str(current_user.organization_id)
        cur = db.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(au.quick_searches), 0) + COALESCE(SUM(au.live_searches), 0) as cnt
            FROM api_usage au
            JOIN users u ON au.user_id = u.id
            WHERE u.organization_id = %s
              AND au.usage_date >= date_trunc('month', CURRENT_DATE)
        """, (org_id,))
        srch = cur.fetchone()
        return OrganizationStats(
            user_count=stats.get('user_count', 0),
            active_watchlist_items=stats.get('active_watchlist_items', 0),
            new_alerts=stats.get('new_alerts', 0),
            critical_alerts=stats.get('critical_alerts', 0),
            searches_this_month=srch['cnt'] if srch else 0,
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



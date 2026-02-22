"""
Alert Routes — view, acknowledge, resolve, dismiss alerts
Extracted from api/routes.py for maintainability.
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query
from auth.authentication import CurrentUser, get_current_user
from models.schemas import (
    AlertResponse, AlertUpdate, AlertAcknowledge, AlertResolve, AlertDismiss,
    AlertStatus, AlertSeverity, AlertDigest,
    PaginatedResponse, SuccessResponse
)
from database.crud import Database, AlertCRUD

logger = logging.getLogger(__name__)

alerts_router = APIRouter(prefix="/alerts", tags=["Alerts"])

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
        
        # By status (only appealable: deadline not yet passed or pre-publication)
        cur.execute("""
            SELECT a.status, COUNT(*) as count
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
              AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
            GROUP BY a.status
        """, (str(current_user.organization_id),))
        by_status = {row['status']: row['count'] for row in cur.fetchall()}

        # By severity (new only, appealable)
        cur.execute("""
            SELECT a.severity, COUNT(*) as count
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s AND a.status = 'new'
              AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
            GROUP BY a.severity
        """, (str(current_user.organization_id),))
        by_severity = {row['severity']: row['count'] for row in cur.fetchall()}
        
        return {
            "by_status": by_status,
            "by_severity": by_severity,
            "total_new": by_status.get('new', 0)
        }


@alerts_router.get("/aggregate")
async def aggregate_alerts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    severity: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get all alerts across all watchlist items, sorted by deadline urgency"""
    with Database() as db:
        cur = db.cursor()
        org_id = str(current_user.organization_id)

        where_extra = ""
        params = [org_id]
        if severity:
            where_extra = " AND a.severity = %s"
            params.append(severity)

        # Count (only appealable: deadline not yet passed or pre-publication)
        cur.execute("""
            SELECT COUNT(*) FROM alerts_mt a
            JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
                AND a.status NOT IN ('dismissed', 'resolved')
                AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
        """ + where_extra, params)
        total = cur.fetchone()['count']

        # Fetch page
        offset = (page - 1) * page_size
        cur.execute("""
            SELECT a.*, w.brand_name AS watched_brand_name,
                   a.opposition_deadline, a.conflicting_name,
                   t.name AS tm_name, t.current_status, t.bulletin_date
            FROM alerts_mt a
            JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
                AND a.status NOT IN ('dismissed', 'resolved')
                AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
        """ + where_extra + """
            ORDER BY
                CASE WHEN a.opposition_deadline IS NOT NULL AND a.opposition_deadline > CURRENT_DATE
                     THEN 0 ELSE 1 END,
                a.opposition_deadline ASC NULLS LAST,
                a.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])

        rows = cur.fetchall()
        items = []
        from datetime import date as date_type
        today = date_type.today()
        for r in rows:
            deadline = r.get('opposition_deadline')
            deadline_days = None
            if deadline and deadline > today:
                deadline_days = (deadline - today).days
            items.append({
                "id": str(r['id']),
                "watchlist_item_id": str(r['watchlist_item_id']),
                "watched_brand_name": r.get('watched_brand_name'),
                "conflicting_brand_name": r.get('conflicting_name') or r.get('tm_name'),
                "conflicting_trademark_id": str(r['conflicting_trademark_id']) if r.get('conflicting_trademark_id') else None,
                "severity": r.get('severity'),
                "risk_score": r.get('overall_risk_score'),
                "status": r.get('status'),
                "opposition_deadline": deadline.isoformat() if deadline else None,
                "deadline_days": deadline_days,
                "overlapping_classes": r.get('overlapping_classes'),
                "created_at": r['created_at'].isoformat() if r.get('created_at') else None
            })

        return PaginatedResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size
        )


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
            application_date=alert.get('conflict_application_date'),
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



"""
Lead Feed API - Opposition Radar
================================
Powers the lead generation dashboard for IP attorneys.

Endpoints:
- GET  /leads/feed          - List potential clients (conflicts)
- GET  /leads/stats         - Dashboard statistics
- GET  /leads/credits       - Check daily credits
- GET  /leads/{id}          - Get lead details (marks as viewed)
- POST /leads/{id}/contact  - Mark lead as contacted
- POST /leads/{id}/convert  - Mark lead as converted
- POST /leads/{id}/dismiss  - Dismiss a lead
- GET  /leads/export/csv    - Export leads as CSV (Enterprise only)
"""

import csv
import io
import logging
from datetime import datetime, date
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Query, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor

from auth.authentication import CurrentUser, get_current_user
from database.crud import Database
from utils.subscription import get_user_plan, get_plan_limit

logger = logging.getLogger(__name__)

# ============================================
# CONFIGURATION
# ============================================

LEADS_PER_PAGE = 20
MAX_EXPORT_LEADS = 500


# ============================================
# PYDANTIC MODELS
# ============================================

class LeadResponse(BaseModel):
    id: str
    # Aggressor (new application)
    new_mark_name: Optional[str] = None
    new_mark_app_no: Optional[str] = None
    new_mark_holder_name: Optional[str] = None
    new_mark_nice_classes: Optional[List[int]] = None
    new_mark_image: Optional[str] = None
    # Victim (potential client)
    existing_mark_name: Optional[str] = None
    existing_mark_app_no: Optional[str] = None
    existing_mark_holder_name: Optional[str] = None
    existing_mark_nice_classes: Optional[List[int]] = None
    existing_mark_image: Optional[str] = None
    # Conflict details
    similarity_score: float
    text_similarity: Optional[float] = None
    semantic_similarity: Optional[float] = None
    visual_similarity: Optional[float] = None
    translation_similarity: Optional[float] = None
    risk_level: str
    conflict_type: str
    overlapping_classes: Optional[List[int]] = None
    conflict_reasons: Optional[List[str]] = None
    # Timing
    bulletin_no: Optional[str] = None
    bulletin_date: Optional[date] = None
    opposition_deadline: date
    days_until_deadline: int
    urgency_level: str
    # Application dates
    new_mark_application_date: Optional[date] = None
    existing_mark_application_date: Optional[date] = None
    # Extracted goods flags
    new_mark_has_extracted_goods: bool = False
    existing_mark_has_extracted_goods: bool = False
    # Status
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


# ============================================
# ROUTER
# ============================================

router = APIRouter(prefix="/leads", tags=["Opposition Radar"])


# ============================================
# HELPERS
# ============================================

def _get_lead_access(db, user_id: str) -> dict:
    """
    Check if user can access leads and how many remain today.

    Returns:
        dict with: can_access, plan, daily_limit, used_today, remaining
    """
    plan = get_user_plan(db, user_id)
    plan_name = plan['plan_name']
    daily_limit = get_plan_limit(plan_name, 'daily_lead_views')

    if daily_limit == 0:
        return {
            'can_access': False,
            'plan': plan_name,
            'daily_limit': 0,
            'used_today': 0,
            'remaining': 0,
        }

    # Count today's views
    cur = db.cursor()
    cur.execute("""
        SELECT COUNT(*) as cnt
        FROM lead_access_log
        WHERE user_id = %s
          AND action = 'viewed'
          AND created_at::date = CURRENT_DATE
    """, (user_id,))
    used_today = cur.fetchone()['cnt']

    if daily_limit == -1:
        remaining = -1  # unlimited
    else:
        remaining = max(0, daily_limit - used_today)

    return {
        'can_access': True,
        'plan': plan_name,
        'daily_limit': daily_limit,
        'used_today': used_today,
        'remaining': remaining,
    }


def _require_lead_access(db, user_id: str) -> dict:
    """Check lead access; raise HTTPException if denied."""
    access = _get_lead_access(db, user_id)

    if not access['can_access']:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "upgrade_required",
                "message": "Lead erisimi icin Professional veya Enterprise plan gereklidir.",
                "current_plan": access['plan'],
            }
        )

    if access['remaining'] == 0 and access['daily_limit'] != -1:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "daily_limit_exceeded",
                "message": f"Gunluk {access['daily_limit']} lead limitinize ulastiniz.",
                "daily_limit": access['daily_limit'],
                "used_today": access['used_today'],
            }
        )

    return access


def _log_lead_access(db, user_id: str, org_id: str, conflict_id: str, action: str):
    """Log lead access for auditing and usage tracking."""
    cur = db.cursor()
    cur.execute("""
        INSERT INTO lead_access_log (user_id, organization_id, conflict_id, action)
        VALUES (%s, %s, %s::uuid, %s)
    """, (str(user_id), str(org_id) if org_id else None, conflict_id, action))
    db.commit()


def _urgency_case_sql():
    """SQL CASE expression for urgency level."""
    return """
        CASE
            WHEN uc.days_until_deadline <= 7 THEN 'critical'
            WHEN uc.days_until_deadline <= 14 THEN 'urgent'
            WHEN uc.days_until_deadline <= 30 THEN 'soon'
            ELSE 'normal'
        END
    """


# ============================================
# ENDPOINTS
# ============================================

@router.get("/feed")
async def get_lead_feed(
    urgency: Optional[str] = Query(None, description="Filter: 'critical', 'urgent', 'soon', 'all'"),
    nice_class: Optional[int] = Query(None, description="Filter by Nice class"),
    min_score: Optional[float] = Query(0.6, ge=0.0, le=1.0, description="Minimum similarity score"),
    risk_level: Optional[str] = Query(None, description="Filter: 'CRITICAL', 'HIGH', 'MEDIUM'"),
    status: Optional[str] = Query('new', description="Lead status: 'new', 'viewed', 'all'"),
    search: Optional[str] = Query(None, description="Search brand name or holder name"),
    page: int = Query(1, ge=1),
    limit: int = Query(LEADS_PER_PAGE, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get lead feed - potential clients for opposition services.

    Returns conflicts where existing trademark holders may need
    legal representation against new applicants.

    Excludes conflicts where the victim is already an IP Watch AI subscriber.
    """
    with Database() as db:
        _require_lead_access(db, str(current_user.id))

        cur = db.cursor()
        urgency_sql = _urgency_case_sql()

        query = f"""
            SELECT
                uc.id,
                uc.new_mark_name, uc.new_mark_app_no,
                uc.new_mark_holder_name, uc.new_mark_nice_classes,
                uc.existing_mark_name, uc.existing_mark_app_no,
                uc.existing_mark_holder_name, uc.existing_mark_nice_classes,
                uc.similarity_score,
                uc.text_similarity, uc.semantic_similarity, uc.visual_similarity, uc.translation_similarity,
                uc.risk_level, uc.conflict_type,
                uc.overlapping_classes, uc.conflict_reasons,
                uc.bulletin_no, uc.bulletin_date,
                uc.opposition_deadline, uc.days_until_deadline,
                uc.lead_status, uc.created_at,
                new_tm.image_path as new_mark_image,
                exist_tm.image_path as existing_mark_image,
                new_tm.application_date as new_mark_application_date,
                exist_tm.application_date as existing_mark_application_date,
                {urgency_sql} as urgency_level,
                (new_tm.extracted_goods IS NOT NULL
                    AND new_tm.extracted_goods != '[]'::jsonb
                    AND new_tm.extracted_goods != 'null'::jsonb) AS new_mark_has_extracted_goods,
                (exist_tm.extracted_goods IS NOT NULL
                    AND exist_tm.extracted_goods != '[]'::jsonb
                    AND exist_tm.extracted_goods != 'null'::jsonb) AS existing_mark_has_extracted_goods
            FROM universal_conflicts uc
            LEFT JOIN trademarks new_tm ON uc.new_mark_id = new_tm.id
            LEFT JOIN trademarks exist_tm ON uc.existing_mark_id = exist_tm.id
            WHERE uc.opposition_deadline >= CURRENT_DATE
              AND uc.similarity_score >= %s
              AND uc.overlapping_classes IS NOT NULL
              AND array_length(uc.overlapping_classes, 1) > 0
        """
        params: list = [min_score]

        # Exclude existing customers (ethical guardrail)
        query += """
              AND NOT EXISTS (
                  SELECT 1 FROM organizations org
                  WHERE org.id = uc.existing_mark_holder_id
              )
        """

        # Urgency filter
        if urgency and urgency != 'all':
            if urgency == 'critical':
                query += " AND uc.days_until_deadline <= 7"
            elif urgency == 'urgent':
                query += " AND uc.days_until_deadline <= 14"
            elif urgency == 'soon':
                query += " AND uc.days_until_deadline <= 30"

        # Nice class filter
        if nice_class is not None:
            query += " AND %s = ANY(uc.overlapping_classes)"
            params.append(nice_class)

        # Risk level filter
        if risk_level:
            query += " AND uc.risk_level = %s"
            params.append(risk_level.upper())

        # Status filter
        if status and status != 'all':
            query += " AND uc.lead_status = %s"
            params.append(status)

        # Text search filter (brand name or holder name)
        if search and search.strip():
            safe_search = search.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            query += """ AND (
                uc.new_mark_name ILIKE %s ESCAPE '\\' OR
                uc.existing_mark_name ILIKE %s ESCAPE '\\' OR
                uc.new_mark_holder_name ILIKE %s ESCAPE '\\' OR
                uc.existing_mark_holder_name ILIKE %s ESCAPE '\\'
            )"""
            like_pattern = f"%{safe_search}%"
            params.extend([like_pattern, like_pattern, like_pattern, like_pattern])

        # Get total count before pagination
        count_query = "SELECT COUNT(*) as cnt FROM (" + query + ") sub"
        cur.execute(count_query, params)
        total_count = cur.fetchone()['cnt']

        query += " ORDER BY uc.opposition_deadline ASC, uc.similarity_score DESC"

        offset = (page - 1) * limit
        query += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        leads = cur.fetchall()

        return {
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "items": [
            LeadResponse(
                id=str(lead['id']),
                new_mark_name=lead['new_mark_name'],
                new_mark_app_no=lead['new_mark_app_no'],
                new_mark_holder_name=lead['new_mark_holder_name'],
                new_mark_nice_classes=lead['new_mark_nice_classes'],
                new_mark_image=lead.get('new_mark_image'),
                existing_mark_name=lead['existing_mark_name'],
                existing_mark_app_no=lead['existing_mark_app_no'],
                existing_mark_holder_name=lead['existing_mark_holder_name'],
                existing_mark_nice_classes=lead['existing_mark_nice_classes'],
                existing_mark_image=lead.get('existing_mark_image'),
                similarity_score=lead['similarity_score'],
                text_similarity=lead.get('text_similarity'),
                semantic_similarity=lead.get('semantic_similarity'),
                visual_similarity=lead.get('visual_similarity'),
                translation_similarity=lead.get('translation_similarity'),
                risk_level=lead['risk_level'],
                conflict_type=lead['conflict_type'],
                overlapping_classes=lead['overlapping_classes'],
                conflict_reasons=lead['conflict_reasons'],
                bulletin_no=lead['bulletin_no'],
                bulletin_date=lead['bulletin_date'],
                opposition_deadline=lead['opposition_deadline'],
                days_until_deadline=lead['days_until_deadline'],
                urgency_level=lead['urgency_level'],
                new_mark_application_date=lead.get('new_mark_application_date'),
                existing_mark_application_date=lead.get('existing_mark_application_date'),
                new_mark_has_extracted_goods=bool(lead.get('new_mark_has_extracted_goods', False)),
                existing_mark_has_extracted_goods=bool(lead.get('existing_mark_has_extracted_goods', False)),
                lead_status=lead['lead_status'],
                created_at=lead['created_at'],
            )
            for lead in leads
        ]}


@router.get("/stats", response_model=LeadStatsResponse)
async def get_lead_stats(
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get lead statistics for the Opposition Radar dashboard."""
    with Database() as db:
        _require_lead_access(db, str(current_user.id))

        cur = db.cursor()
        cur.execute("""
            SELECT
                COUNT(*) as total_leads,
                COUNT(*) FILTER (WHERE days_until_deadline <= 7) as critical_leads,
                COUNT(*) FILTER (WHERE days_until_deadline > 7 AND days_until_deadline <= 14) as urgent_leads,
                COUNT(*) FILTER (WHERE days_until_deadline > 14 AND days_until_deadline <= 30) as upcoming_leads,
                COUNT(*) FILTER (WHERE lead_status = 'new') as new_leads,
                COUNT(*) FILTER (WHERE lead_status = 'viewed') as viewed_leads,
                COUNT(*) FILTER (WHERE lead_status = 'contacted') as contacted_leads,
                COUNT(*) FILTER (WHERE lead_status = 'converted') as converted_leads,
                ROUND(AVG(similarity_score)::numeric, 3) as avg_similarity,
                MAX(created_at) as last_scan_at
            FROM universal_conflicts
            WHERE opposition_deadline >= CURRENT_DATE
              AND overlapping_classes IS NOT NULL
              AND array_length(overlapping_classes, 1) > 0
        """)

        stats = cur.fetchone()

        return LeadStatsResponse(
            total_leads=stats['total_leads'] or 0,
            critical_leads=stats['critical_leads'] or 0,
            urgent_leads=stats['urgent_leads'] or 0,
            upcoming_leads=stats['upcoming_leads'] or 0,
            new_leads=stats['new_leads'] or 0,
            viewed_leads=stats['viewed_leads'] or 0,
            contacted_leads=stats['contacted_leads'] or 0,
            converted_leads=stats['converted_leads'] or 0,
            avg_similarity=float(stats['avg_similarity']) if stats['avg_similarity'] else None,
            last_scan_at=stats['last_scan_at'],
        )


@router.get("/credits")
async def get_lead_credits(
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get user's lead access credits status."""
    with Database() as db:
        access = _get_lead_access(db, str(current_user.id))

        return {
            "can_access": access['can_access'],
            "plan": access['plan'],
            "daily_limit": access['daily_limit'],
            "used_today": access['used_today'],
            "remaining": access['remaining'] if access['daily_limit'] != -1 else "unlimited",
        }


@router.get("/export/csv")
async def export_leads_csv(
    urgency: Optional[str] = Query(None),
    nice_class: Optional[int] = Query(None),
    min_score: Optional[float] = Query(0.6, ge=0.0, le=1.0),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Export leads as CSV file. Enterprise plan only.
    """
    with Database() as db:
        access = _require_lead_access(db, str(current_user.id))

        if not get_plan_limit(access['plan'], 'can_export_csv_leads'):
            raise HTTPException(
                status_code=403,
                detail="CSV export sadece Enterprise plan icin kullanilabilir."
            )

        cur = db.cursor()

        query = """
            SELECT
                uc.new_mark_name, uc.new_mark_app_no, uc.new_mark_holder_name,
                uc.existing_mark_name, uc.existing_mark_app_no, uc.existing_mark_holder_name,
                uc.similarity_score, uc.risk_level, uc.conflict_type,
                uc.opposition_deadline, uc.days_until_deadline
            FROM universal_conflicts uc
            WHERE uc.opposition_deadline >= CURRENT_DATE
              AND uc.similarity_score >= %s
              AND uc.lead_status NOT IN ('dismissed', 'converted')
              AND uc.overlapping_classes IS NOT NULL
              AND array_length(uc.overlapping_classes, 1) > 0
        """
        params: list = [min_score]

        if urgency == 'critical':
            query += " AND uc.days_until_deadline <= 7"
        elif urgency == 'urgent':
            query += " AND uc.days_until_deadline <= 14"

        if nice_class is not None:
            query += " AND %s = ANY(uc.overlapping_classes)"
            params.append(nice_class)

        query += " ORDER BY uc.opposition_deadline ASC LIMIT %s"
        params.append(MAX_EXPORT_LEADS)

        cur.execute(query, params)
        leads = cur.fetchall()

        # Generate CSV
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            'Yeni Marka', 'Yeni Basvuru No', 'Yeni Basvuru Sahibi',
            'Mevcut Marka', 'Mevcut Basvuru No', 'Potansiyel Musteri',
            'Benzerlik', 'Risk', 'Tip', 'Itiraz Suresi', 'Kalan Gun'
        ])

        for lead in leads:
            writer.writerow([
                lead['new_mark_name'],
                lead['new_mark_app_no'],
                lead['new_mark_holder_name'],
                lead['existing_mark_name'],
                lead['existing_mark_app_no'],
                lead['existing_mark_holder_name'],
                f"{lead['similarity_score']:.1%}",
                lead['risk_level'],
                lead['conflict_type'],
                lead['opposition_deadline'],
                lead['days_until_deadline'],
            ])

        # Log export
        _log_lead_access(
            db, str(current_user.id),
            str(current_user.organization_id),
            '00000000-0000-0000-0000-000000000000',  # placeholder for bulk export
            'exported'
        )

        output.seek(0)

        filename = f"leads_{datetime.now().strftime('%Y%m%d')}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )


@router.get("/{lead_id}")
async def get_lead_detail(
    lead_id: str,
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get detailed information about a specific lead.
    Marks the lead as 'viewed' and logs access.
    """
    with Database() as db:
        _require_lead_access(db, str(current_user.id))

        cur = db.cursor()
        urgency_sql = _urgency_case_sql()

        cur.execute(f"""
            SELECT
                uc.*,
                new_tm.image_path as new_mark_image,
                exist_tm.image_path as existing_mark_image,
                new_tm.application_date as new_mark_application_date,
                exist_tm.application_date as existing_mark_application_date,
                {urgency_sql} as urgency_level,
                (new_tm.extracted_goods IS NOT NULL
                    AND new_tm.extracted_goods != '[]'::jsonb
                    AND new_tm.extracted_goods != 'null'::jsonb) AS new_mark_has_extracted_goods,
                (exist_tm.extracted_goods IS NOT NULL
                    AND exist_tm.extracted_goods != '[]'::jsonb
                    AND exist_tm.extracted_goods != 'null'::jsonb) AS existing_mark_has_extracted_goods
            FROM universal_conflicts uc
            LEFT JOIN trademarks new_tm ON uc.new_mark_id = new_tm.id
            LEFT JOIN trademarks exist_tm ON uc.existing_mark_id = exist_tm.id
            WHERE uc.id = %s::uuid
        """, (lead_id,))

        lead = cur.fetchone()

        if not lead:
            raise HTTPException(status_code=404, detail="Lead bulunamadi.")

        # Mark as viewed if currently new
        if lead['lead_status'] == 'new':
            cur.execute("""
                UPDATE universal_conflicts
                SET lead_status = 'viewed',
                    viewed_by = array_append(COALESCE(viewed_by, '{}'), %s::uuid)
                WHERE id = %s::uuid
            """, (str(current_user.id), lead_id))
            db.commit()

        # Log access
        _log_lead_access(
            db, str(current_user.id),
            str(current_user.organization_id),
            lead_id, 'viewed'
        )

        return dict(lead)


@router.post("/{lead_id}/contact", response_model=LeadActionResponse)
async def mark_lead_contacted(
    lead_id: str,
    notes: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Mark a lead as contacted."""
    with Database() as db:
        _require_lead_access(db, str(current_user.id))
        cur = db.cursor()
        cur.execute("""
            UPDATE universal_conflicts
            SET lead_status = 'contacted',
                contacted_at = NOW(),
                notes = COALESCE(notes || E'\\n', '') || %s
            WHERE id = %s::uuid
            RETURNING id
        """, (notes or '', lead_id))

        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Lead bulunamadi.")

        db.commit()

        _log_lead_access(
            db, str(current_user.id),
            str(current_user.organization_id),
            lead_id, 'contacted'
        )

        return LeadActionResponse(
            success=True,
            message="Lead 'iletisime gecildi' olarak isaretlendi.",
            lead_id=lead_id,
            new_status="contacted",
        )


@router.post("/{lead_id}/convert", response_model=LeadActionResponse)
async def mark_lead_converted(
    lead_id: str,
    notes: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Mark a lead as converted (became a client)."""
    with Database() as db:
        _require_lead_access(db, str(current_user.id))
        cur = db.cursor()
        cur.execute("""
            UPDATE universal_conflicts
            SET lead_status = 'converted',
                notes = COALESCE(notes || E'\\n', '') || %s
            WHERE id = %s::uuid
            RETURNING id
        """, (notes or '', lead_id))

        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Lead bulunamadi.")

        db.commit()

        _log_lead_access(
            db, str(current_user.id),
            str(current_user.organization_id),
            lead_id, 'converted'
        )

        return LeadActionResponse(
            success=True,
            message="Lead 'musteri oldu' olarak isaretlendi.",
            lead_id=lead_id,
            new_status="converted",
        )


@router.post("/{lead_id}/dismiss", response_model=LeadActionResponse)
async def dismiss_lead(
    lead_id: str,
    reason: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Dismiss a lead (not interested / not relevant)."""
    with Database() as db:
        _require_lead_access(db, str(current_user.id))
        cur = db.cursor()
        cur.execute("""
            UPDATE universal_conflicts
            SET lead_status = 'dismissed',
                notes = COALESCE(notes || E'\\n', '') || %s
            WHERE id = %s::uuid
            RETURNING id
        """, (f"Dismissed: {reason}" if reason else "Dismissed", lead_id))

        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Lead bulunamadi.")

        db.commit()

        _log_lead_access(
            db, str(current_user.id),
            str(current_user.organization_id),
            lead_id, 'dismissed'
        )

        return LeadActionResponse(
            success=True,
            message="Lead reddedildi.",
            lead_id=lead_id,
            new_status="dismissed",
        )

"""
Reports API
===========
Generate, list, and download reports.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from psycopg2.extras import RealDictCursor

from auth.authentication import CurrentUser, get_current_user
from database.crud import Database
from utils.subscription import get_user_plan, check_report_eligibility, get_plan_limit
from models.schemas import ReportRequest, ReportResponse, ReportType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["Reports"])


# ============================================
# POST /reports/generate
# ============================================
@router.post("/generate")
async def generate_report_endpoint(
    request: ReportRequest,
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Generate a new report.

    Plan limits: Free=1/month, Starter=5, Professional=20, Enterprise=100.
    """
    with Database() as db:
        plan = get_user_plan(db, str(current_user.id))
        plan_name = plan['plan_name']

        eligibility = check_report_eligibility(
            db, plan_name, str(current_user.organization_id)
        )

        if not eligibility['eligible']:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "limit_exceeded",
                    "message": eligibility['reason'],
                    "reports_used": eligibility['reports_used'],
                    "reports_limit": eligibility['reports_limit'],
                }
            )

        # Map schema ReportType enum to generator report_type strings
        report_type_map = {
            ReportType.WATCHLIST_SUMMARY: "watchlist_status",
            ReportType.ALERT_DIGEST: "weekly_digest",
            ReportType.RISK_ASSESSMENT: "monthly_summary",
            ReportType.COMPETITOR_ANALYSIS: "full_portfolio",
            ReportType.PORTFOLIO_STATUS: "watchlist_status",
            ReportType.CUSTOM: "monthly_summary",
        }
        generator_type = report_type_map.get(request.report_type, "weekly_digest")

        # Build parameters for generator
        parameters = {}
        if request.period_start:
            parameters['date_start'] = request.period_start.isoformat()
        if request.period_end:
            parameters['date_end'] = request.period_end.isoformat()
        if request.watchlist_ids and len(request.watchlist_ids) > 0:
            parameters['watchlist_id'] = str(request.watchlist_ids[0])

        try:
            from reports.generator import ReportGenerator

            generator = ReportGenerator(db=db)

            if request.file_format == 'xlsx':
                result_path = generator.generate_excel_report(
                    user_id=current_user.id,
                    report_type=generator_type,
                    parameters=parameters
                )
                # For excel, result is file path string
                result = {
                    'report_id': None,
                    'status': 'completed',
                    'file_path': result_path,
                }
            else:
                result = generator.generate_report(
                    user_id=current_user.id,
                    report_type=generator_type,
                    parameters=parameters
                )

            # Update title if provided
            if request.title and result.get('report_id'):
                cur = db.cursor()
                cur.execute(
                    "UPDATE reports SET report_name = %s WHERE id = %s",
                    (request.title, result['report_id'])
                )
                db.commit()

            # Fetch the created report record for response
            report_id = result.get('report_id')
            if report_id:
                cur = db.cursor(cursor_factory=RealDictCursor)
                cur.execute("SELECT * FROM reports WHERE id = %s", (str(report_id),))
                report_row = cur.fetchone()
                if report_row:
                    return {
                        "id": str(report_row['id']),
                        "organization_id": str(report_row['organization_id']) if report_row['organization_id'] else None,
                        "report_type": report_row['report_type'],
                        "title": report_row['report_name'],
                        "status": report_row['status'],
                        "file_path": report_row['file_path'],
                        "file_format": report_row['file_format'] or 'pdf',
                        "file_size_bytes": report_row['file_size_bytes'],
                        "generated_at": report_row['generated_at'].isoformat() if report_row['generated_at'] else None,
                        "created_at": report_row['created_at'].isoformat() if report_row['created_at'] else None,
                    }

            return {
                "report_id": result.get('report_id'),
                "status": result.get('status', 'completed'),
                "file_path": result.get('file_path'),
                "message": "Rapor olusturuldu"
            }

        except Exception as e:
            logger.error(f"Report generation failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": "generation_failed", "message": str(e)}
            )


# ============================================
# GET /reports
# ============================================
@router.get("")
async def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user)
):
    """List reports for current user's organization."""
    with Database() as db:
        cur = db.cursor(cursor_factory=RealDictCursor)
        org_id = str(current_user.organization_id)

        # Total count
        cur.execute(
            "SELECT COUNT(*) as cnt FROM reports WHERE organization_id = %s",
            (org_id,)
        )
        total = cur.fetchone()['cnt']

        # Paginated results
        offset = (page - 1) * page_size
        cur.execute("""
            SELECT * FROM reports
            WHERE organization_id = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (org_id, page_size, offset))

        rows = cur.fetchall()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1

        reports = []
        for row in rows:
            reports.append({
                "id": str(row['id']),
                "organization_id": str(row['organization_id']) if row['organization_id'] else None,
                "report_type": row['report_type'],
                "title": row['report_name'],
                "status": row['status'],
                "file_path": row['file_path'],
                "file_format": row['file_format'] or 'pdf',
                "file_size_bytes": row['file_size_bytes'],
                "generated_at": row['generated_at'].isoformat() if row['generated_at'] else None,
                "created_at": row['created_at'].isoformat() if row['created_at'] else None,
            })

        # Get usage info
        plan = get_user_plan(db, str(current_user.id))
        eligibility = check_report_eligibility(
            db, plan['plan_name'], org_id
        )

        return {
            "reports": reports,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "usage": {
                "reports_used": eligibility['reports_used'],
                "reports_limit": eligibility['reports_limit'],
                "can_export": eligibility['can_export'],
            }
        }


# ============================================
# GET /reports/{report_id}
# ============================================
@router.get("/{report_id}")
async def get_report(
    report_id: str,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get a single report by ID."""
    with Database() as db:
        cur = db.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM reports WHERE id = %s", (report_id,))
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Rapor bulunamadi")

        if str(row['organization_id']) != str(current_user.organization_id):
            raise HTTPException(status_code=403, detail="Bu rapora erisiminiz yok")

        return {
            "id": str(row['id']),
            "organization_id": str(row['organization_id']) if row['organization_id'] else None,
            "report_type": row['report_type'],
            "title": row['report_name'],
            "status": row['status'],
            "file_path": row['file_path'],
            "file_format": row['file_format'] or 'pdf',
            "file_size_bytes": row['file_size_bytes'],
            "generated_at": row['generated_at'].isoformat() if row['generated_at'] else None,
            "created_at": row['created_at'].isoformat() if row['created_at'] else None,
            "download_count": row['download_count'] or 0,
            "error_message": row['error_message'],
        }


# ============================================
# GET /reports/{report_id}/download
# ============================================
@router.get("/{report_id}/download")
async def download_report(
    report_id: str,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Download a completed report file."""
    with Database() as db:
        cur = db.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM reports WHERE id = %s", (report_id,))
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Rapor bulunamadi")

        if str(row['organization_id']) != str(current_user.organization_id):
            raise HTTPException(status_code=403, detail="Bu rapora erisiminiz yok")

        # Check plan allows export
        plan = get_user_plan(db, str(current_user.id))
        can_export = get_plan_limit(plan['plan_name'], 'can_export_reports')
        if not can_export:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "export_not_allowed",
                    "message": "Rapor indirme icin planinizi yukseltin"
                }
            )

        if row['status'] != 'completed':
            raise HTTPException(
                status_code=400,
                detail="Rapor henuz tamamlanmadi (durum: " + (row['status'] or 'unknown') + ")"
            )

        file_path = row['file_path']
        if not file_path or not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="Rapor dosyasi bulunamadi")

        # Increment download count
        cur.execute("""
            UPDATE reports
            SET download_count = COALESCE(download_count, 0) + 1,
                last_downloaded_at = NOW()
            WHERE id = %s
        """, (report_id,))
        db.commit()

        # Determine media type
        file_format = row['file_format'] or 'pdf'
        media_types = {
            'pdf': 'application/pdf',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'csv': 'text/csv',
        }
        media_type = media_types.get(file_format, 'application/octet-stream')

        report_name = row['report_name'] or 'rapor'
        filename = f"{report_name}.{file_format}"

        return FileResponse(
            path=file_path,
            media_type=media_type,
            filename=filename
        )

"""
Attorney Portfolio API - PRO Feature
=====================================
View all trademark applications handled by a specific attorney.
Mirrors the holder portfolio pattern (api/holders.py).
"""

import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from psycopg2.extras import RealDictCursor

from auth.authentication import CurrentUser, get_current_user
from database.crud import Database
from utils.subscription import get_user_plan, get_plan_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attorneys", tags=["attorneys"])


@router.get("/{attorney_no}/trademarks")
async def get_attorney_trademarks(
    attorney_no: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get all trademark applications handled by an attorney.

    PRO feature - requires Professional or Enterprise plan.

    Args:
        attorney_no: Attorney number (unique identifier)
        page: Page number (1-indexed)
        page_size: Results per page (max 100)
    """
    with Database() as db:
        plan = get_user_plan(db, str(current_user.id))

        if not get_plan_limit(plan['plan_name'], 'can_view_holder_portfolio'):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "pro_feature",
                    "message": "Vekil portföyü görüntüleme PRO özelliğidir",
                    "upgrade_url": "/pricing"
                }
            )

        offset = (page - 1) * page_size
        cur = db.cursor()

        # Get attorney info
        cur.execute("""
            SELECT DISTINCT attorney_name, attorney_no
            FROM trademarks
            WHERE attorney_no = %s
            LIMIT 1
        """, (attorney_no,))

        attorney_row = cur.fetchone()

        if not attorney_row:
            raise HTTPException(status_code=404, detail="Attorney not found")

        attorney_name = attorney_row['attorney_name']

        # Get total count
        cur.execute("""
            SELECT COUNT(*) as cnt FROM trademarks
            WHERE attorney_no = %s
        """, (attorney_no,))
        total_count = cur.fetchone()['cnt']

        # Get paginated trademarks
        cur.execute("""
            SELECT
                id, application_no, name, current_status,
                nice_class_numbers, application_date, registration_date,
                image_path, bulletin_no,
                holder_name, holder_tpe_client_id,
                (extracted_goods IS NOT NULL
                    AND extracted_goods != '[]'::jsonb
                    AND extracted_goods != 'null'::jsonb) AS has_extracted_goods
            FROM trademarks
            WHERE attorney_no = %s
            ORDER BY application_date DESC NULLS LAST, application_no DESC
            LIMIT %s OFFSET %s
        """, (attorney_no, page_size, offset))

        rows = cur.fetchall()

        trademarks = []
        for tm in rows:
            trademarks.append({
                "id": str(tm['id']),
                "application_no": tm['application_no'],
                "name": tm['name'],
                "status": tm['current_status'],
                "classes": tm['nice_class_numbers'] or [],
                "application_date": tm['application_date'].isoformat() if tm['application_date'] else None,
                "registration_date": tm['registration_date'].isoformat() if tm['registration_date'] else None,
                "image_path": tm['image_path'],
                "has_extracted_goods": bool(tm.get('has_extracted_goods', False)),
                "holder_name": tm.get('holder_name'),
                "holder_tpe_client_id": tm.get('holder_tpe_client_id'),
            })

        total_pages = (total_count + page_size - 1) // page_size

        return {
            "attorney_name": attorney_name,
            "attorney_no": attorney_no,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "trademarks": trademarks
        }


@router.get("/search")
async def search_attorneys(
    query: str = Query(..., min_length=2),
    limit: int = Query(10, ge=1, le=50),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Search for attorneys by name or ID (autocomplete). PRO feature.
    """
    with Database() as db:
        plan = get_user_plan(db, str(current_user.id))

        if not get_plan_limit(plan['plan_name'], 'can_view_holder_portfolio'):
            raise HTTPException(status_code=403, detail="PRO feature")

        cur = db.cursor()
        cur.execute("""
            SELECT
                attorney_name,
                attorney_no,
                COUNT(*) as trademark_count
            FROM trademarks
            WHERE (attorney_name ILIKE %s OR attorney_no ILIKE %s)
              AND attorney_name IS NOT NULL
              AND attorney_no IS NOT NULL
            GROUP BY attorney_name, attorney_no
            ORDER BY trademark_count DESC
            LIMIT %s
        """, (f"%{query}%", f"%{query}%", limit))

        rows = cur.fetchall()

        return {
            "query": query,
            "results": [
                {
                    "attorney_name": row['attorney_name'],
                    "attorney_no": row['attorney_no'],
                    "trademark_count": row['trademark_count']
                }
                for row in rows
            ]
        }


@router.get("/{attorney_no}/trademarks/csv")
async def export_attorney_trademarks_csv(
    attorney_no: str,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Export ALL trademarks by an attorney as CSV. PRO feature."""
    with Database() as db:
        plan = get_user_plan(db, str(current_user.id))
        if not get_plan_limit(plan['plan_name'], 'can_view_holder_portfolio'):
            raise HTTPException(status_code=403, detail="PRO feature")

        cur = db.cursor()
        cur.execute("""
            SELECT DISTINCT attorney_name FROM trademarks
            WHERE attorney_no = %s LIMIT 1
        """, (attorney_no,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Attorney not found")
        attorney_name = row['attorney_name'] or attorney_no

        cur.execute("""
            SELECT application_no, name, current_status,
                   nice_class_numbers, application_date, registration_date,
                   registration_no, holder_name, holder_tpe_client_id,
                   bulletin_no
            FROM trademarks
            WHERE attorney_no = %s
            ORDER BY application_date DESC NULLS LAST, application_no DESC
        """, (attorney_no,))
        rows = cur.fetchall()

    buf = io.StringIO()
    buf.write('\ufeff')  # BOM for Excel
    writer = csv.writer(buf)
    writer.writerow(['Marka Adi', 'Basvuru No', 'Durum', 'Siniflar',
                     'Basvuru Tarihi', 'Tescil Tarihi', 'Tescil No',
                     'Sahip', 'Sahip TPE No', 'Bulten No'])
    for tm in rows:
        writer.writerow([
            tm.get('name') or '',
            tm.get('application_no') or '',
            tm.get('current_status') or '',
            '; '.join(str(c) for c in (tm.get('nice_class_numbers') or [])),
            tm['application_date'].isoformat() if tm.get('application_date') else '',
            tm['registration_date'].isoformat() if tm.get('registration_date') else '',
            tm.get('registration_no') or '',
            tm.get('holder_name') or '',
            tm.get('holder_tpe_client_id') or '',
            tm.get('bulletin_no') or '',
        ])

    safe_name = ''.join(c if c.isascii() and (c.isalnum() or c in ' _-') else '_' for c in attorney_name)[:50]
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_portfolio.csv"'}
    )

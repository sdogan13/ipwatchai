"""
Holder Portfolio API - PRO Feature
==================================
View all trademark applications by a specific holder.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor

from auth.authentication import CurrentUser, get_current_user
from database.crud import Database
from utils.subscription import get_user_plan, get_plan_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/holders", tags=["holders"])


@router.get("/{tpe_client_id}/trademarks")
async def get_holder_trademarks(
    tpe_client_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get all trademark applications by a holder.

    PRO feature - requires Professional or Enterprise plan.

    Args:
        tpe_client_id: TPE Client ID (unique identifier from Turkish Patent Office)
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
                    "message": "Sahip portföyü görüntüleme PRO özelliğidir",
                    "upgrade_url": "/pricing"
                }
            )

        offset = (page - 1) * page_size
        cur = db.cursor()

        # Get holder info
        cur.execute("""
            SELECT DISTINCT holder_name, holder_tpe_client_id
            FROM trademarks
            WHERE holder_tpe_client_id = %s
            LIMIT 1
        """, (tpe_client_id,))

        holder_row = cur.fetchone()

        if not holder_row:
            raise HTTPException(status_code=404, detail="Holder not found")

        holder_name = holder_row['holder_name']

        # Get total count
        cur.execute("""
            SELECT COUNT(*) as cnt FROM trademarks
            WHERE holder_tpe_client_id = %s
        """, (tpe_client_id,))
        total_count = cur.fetchone()['cnt']

        # Get paginated trademarks
        cur.execute("""
            SELECT
                id, application_no, name, current_status,
                nice_class_numbers, application_date, registration_date,
                image_path, bulletin_no, gazette_no,
                (extracted_goods IS NOT NULL
                    AND extracted_goods != '[]'::jsonb
                    AND extracted_goods != 'null'::jsonb) AS has_extracted_goods
            FROM trademarks
            WHERE holder_tpe_client_id = %s
            ORDER BY application_date DESC NULLS LAST, application_no DESC
            LIMIT %s OFFSET %s
        """, (tpe_client_id, page_size, offset))

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
            })

        total_pages = (total_count + page_size - 1) // page_size

        return {
            "holder_name": holder_name,
            "holder_tpe_client_id": tpe_client_id,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "trademarks": trademarks
        }


@router.get("/search")
async def search_holders(
    query: str = Query(..., min_length=2),
    limit: int = Query(10, ge=1, le=50),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Search for holders by name (autocomplete). PRO feature.
    """
    with Database() as db:
        plan = get_user_plan(db, str(current_user.id))

        if not get_plan_limit(plan['plan_name'], 'can_view_holder_portfolio'):
            raise HTTPException(status_code=403, detail="PRO feature")

        cur = db.cursor()
        cur.execute("""
            SELECT
                holder_name,
                holder_tpe_client_id,
                COUNT(*) as trademark_count
            FROM trademarks
            WHERE holder_name ILIKE %s
              AND holder_name IS NOT NULL
              AND holder_tpe_client_id IS NOT NULL
            GROUP BY holder_name, holder_tpe_client_id
            ORDER BY trademark_count DESC
            LIMIT %s
        """, (f"%{query}%", limit))

        rows = cur.fetchall()

        return {
            "query": query,
            "results": [
                {
                    "holder_name": row['holder_name'],
                    "holder_tpe_client_id": row['holder_tpe_client_id'],
                    "trademark_count": row['trademark_count']
                }
                for row in rows
            ]
        }

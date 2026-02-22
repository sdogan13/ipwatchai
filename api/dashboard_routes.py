"""
Dashboard Routes
Extracted from api/routes.py for maintainability.
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException, status
from auth.authentication import CurrentUser, get_current_user, require_role
from models.schemas import (
    PaginatedResponse, SuccessResponse, DashboardStats
)
from database.crud import Database, WatchlistCRUD

logger = logging.getLogger(__name__)

dashboard_router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
# ==========================================
# Dashboard Routes
# ==========================================

@dashboard_router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(current_user: CurrentUser = Depends(get_current_user)):
    """Get main dashboard statistics"""
    with Database() as db:
        cur = db.cursor()
        org_id = str(current_user.organization_id)
        
        # Watchlist counts
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE is_active) as active
            FROM watchlist_mt WHERE organization_id = %s
        """, (org_id,))
        wl = cur.fetchone()
        
        # Alert counts (only appealable: deadline not yet passed or pre-publication)
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE a.status = 'new') as new,
                COUNT(*) FILTER (WHERE a.severity = 'critical' AND a.status != 'dismissed') as critical,
                COUNT(*) FILTER (WHERE a.created_at > NOW() - INTERVAL '7 days') as this_week
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
              AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
        """, (org_id,))
        al = cur.fetchone()

        # Active deadlines & pre-publication counts (from alerts joined with trademarks)
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE t.appeal_deadline IS NOT NULL
                    AND t.appeal_deadline >= CURRENT_DATE
                    AND a.status != 'dismissed') as active_deadlines,
                COUNT(*) FILTER (WHERE t.appeal_deadline IS NULL
                    AND (t.current_status IS NULL OR t.current_status = 'Applied')
                    AND a.status != 'dismissed') as pre_publication
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
        """, (org_id,))
        dl = cur.fetchone()
        
        # Searches this month (from api_usage table)
        cur.execute("""
            SELECT COALESCE(SUM(au.quick_searches), 0) + COALESCE(SUM(au.live_searches), 0) as cnt
            FROM api_usage au
            JOIN users u ON au.user_id = u.id
            WHERE u.organization_id = %s
              AND au.usage_date >= date_trunc('month', CURRENT_DATE)
        """, (org_id,))
        searches_row = cur.fetchone()
        searches_this_month = searches_row['cnt'] if searches_row else 0

        # Organization limits from PLAN_FEATURES (single source of truth)
        from utils.subscription import get_user_plan as _gup_dash, get_plan_limit as _gpl_dash
        _dash_plan = _gup_dash(db, str(current_user.id))
        _dash_plan_name = _dash_plan['plan_name']

        wl_limit = _gpl_dash(_dash_plan_name, 'max_watchlist_items')
        user_limit = _gpl_dash(_dash_plan_name, 'max_users')
        qs_limit = _gpl_dash(_dash_plan_name, 'max_daily_quick_searches')
        ls_limit = _gpl_dash(_dash_plan_name, 'monthly_live_searches')
        report_limit = _gpl_dash(_dash_plan_name, 'monthly_reports')

        # Count users in org
        cur.execute("SELECT COUNT(*) as cnt FROM users WHERE organization_id = %s AND is_active = TRUE", (org_id,))
        user_count = cur.fetchone()['cnt']

        return DashboardStats(
            watchlist_count=wl['total'],
            active_watchlist=wl['active'],
            total_alerts=al['total'],
            new_alerts=al['new'],
            critical_alerts=al['critical'],
            alerts_this_week=al['this_week'],
            searches_this_month=searches_this_month,
            active_deadline_count=dl['active_deadlines'],
            pre_publication_count=dl['pre_publication'],
            plan_usage={
                "watchlist": {"used": wl['active'], "limit": wl_limit},
                "users": {"used": user_count, "limit": user_limit},
                "searches": {"used": searches_this_month, "limit": qs_limit + ls_limit},
                "reports": {"used": 0, "limit": report_limit},
            }
        )



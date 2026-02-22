"""
Usage Routes
Extracted from api/routes.py for maintainability.
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException, status
from auth.authentication import CurrentUser, get_current_user, require_role
from models.schemas import (
    PaginatedResponse, SuccessResponse
)
from database.crud import Database

logger = logging.getLogger(__name__)

usage_router = APIRouter(prefix="/usage", tags=["Usage"])
# ==========================================
# Usage Summary
# ==========================================

@usage_router.get("/summary")
async def get_usage_summary(current_user: CurrentUser = Depends(get_current_user)):
    """
    Unified credits/usage endpoint.
    Returns all usage counters and plan limits for the current user.
    """
    from utils.subscription import (
        get_user_plan, get_plan_limit,
        get_daily_quick_searches, get_live_search_usage,
        get_monthly_name_generations, get_org_plan,
        check_ai_credit_eligibility, get_monthly_applications,
    )

    with Database() as db:
        user_id = str(current_user.id)
        org_id = str(current_user.organization_id)
        plan = get_user_plan(db, user_id)
        plan_name = plan['plan_name']

        # Daily quick searches
        qs_used = get_daily_quick_searches(db, user_id)
        qs_limit = get_plan_limit(plan_name, 'max_daily_quick_searches')

        # Monthly live searches
        ls_used = get_live_search_usage(db, user_id)
        ls_limit = get_plan_limit(plan_name, 'monthly_live_searches')

        # AI credits (org-level, unified pool)
        ai_ok, _, ai_details = check_ai_credit_eligibility(db, org_id, cost=1)
        ai_remaining = ai_details.get('total_remaining', 0)
        ai_limit = get_plan_limit(plan_name, 'monthly_ai_credits')

        # Monthly name generations (org-level, for display)
        ng_used = get_monthly_name_generations(db, org_id)

        # Monthly applications (org-level)
        app_used = get_monthly_applications(db, org_id)
        app_limit = get_plan_limit(plan_name, 'monthly_applications')

        # Logo tracking
        can_track_logos = get_plan_limit(plan_name, 'can_track_logos')

        # Watchlist items count
        cur = db.cursor()
        cur.execute(
            "SELECT COUNT(*) as cnt FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE",
            (org_id,)
        )
        wl_row = cur.fetchone()
        wl_count = wl_row['cnt'] if wl_row else 0
        wl_limit = get_plan_limit(plan_name, 'max_watchlist_items')

        # Name generation limit (uses the unified AI credit pool)
        ng_limit = ai_limit  # name generations share the AI credit pool

        # Logo generation limit (also from unified AI credit pool)
        logo_limit = ai_limit

    return {
        "plan": plan_name,
        "display_name": plan['display_name'],
        "usage": {
            "daily_quick_searches": {"used": qs_used, "limit": qs_limit},
            "monthly_live_searches": {"used": ls_used, "limit": ls_limit},
            "monthly_ai_credits": {"remaining": ai_remaining, "limit": ai_limit},
            "monthly_name_generations": {"used": ng_used, "limit": ng_limit},
            "monthly_name_generations_used": ng_used,
            "monthly_applications": {"used": app_used, "limit": app_limit},
            "watchlist_items": {"used": wl_count, "limit": wl_limit},
            "logo_credits": {"remaining": ai_remaining, "limit": logo_limit},
            "can_track_logos": can_track_logos,
        },
    }



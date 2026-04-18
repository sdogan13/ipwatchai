"""Usage summary service helpers used by HTTP route modules."""

from database.crud import Database


async def get_usage_summary_data(
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    plan_limit_getter=None,
    daily_quick_searches_getter=None,
    live_search_usage_getter=None,
    ai_credit_eligibility_checker=None,
    monthly_name_generations_getter=None,
    monthly_applications_getter=None,
):
    """Return unified usage counters and plan limits for the current user."""
    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan

    if plan_limit_getter is None:
        from utils.subscription import get_plan_limit

        plan_limit_getter = get_plan_limit

    if daily_quick_searches_getter is None:
        from utils.subscription import get_daily_quick_searches

        daily_quick_searches_getter = get_daily_quick_searches

    if live_search_usage_getter is None:
        from utils.subscription import get_live_search_usage

        live_search_usage_getter = get_live_search_usage

    if ai_credit_eligibility_checker is None:
        from utils.subscription import check_ai_credit_eligibility

        ai_credit_eligibility_checker = check_ai_credit_eligibility

    if monthly_name_generations_getter is None:
        from utils.subscription import get_monthly_name_generations

        monthly_name_generations_getter = get_monthly_name_generations

    if monthly_applications_getter is None:
        from utils.subscription import get_monthly_applications

        monthly_applications_getter = get_monthly_applications

    with database_factory() as db:
        user_id = str(current_user.id)
        org_id = str(current_user.organization_id)
        plan = user_plan_getter(db, user_id)
        plan_name = plan["plan_name"]

        qs_used = daily_quick_searches_getter(db, user_id)
        qs_limit = plan_limit_getter(plan_name, "max_daily_quick_searches")

        ls_used = live_search_usage_getter(db, user_id)
        ls_limit = plan_limit_getter(plan_name, "monthly_live_searches")

        _, _, ai_details = ai_credit_eligibility_checker(db, org_id, cost=1)
        ai_remaining = ai_details.get("total_remaining", 0)
        ai_limit = plan_limit_getter(plan_name, "monthly_ai_credits")

        ng_used = monthly_name_generations_getter(db, org_id)
        app_used = monthly_applications_getter(db, org_id)
        app_limit = plan_limit_getter(plan_name, "monthly_applications")
        can_track_logos = plan_limit_getter(plan_name, "can_track_logos")

        cur = db.cursor()
        cur.execute(
            "SELECT COUNT(*) as cnt FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE",
            (org_id,),
        )
        wl_row = cur.fetchone()
        wl_count = wl_row["cnt"] if wl_row else 0
        wl_limit = plan_limit_getter(plan_name, "max_watchlist_items")

    return {
        "plan": plan_name,
        "display_name": plan["display_name"],
        "usage": {
            "daily_quick_searches": {"used": qs_used, "limit": qs_limit},
            "monthly_live_searches": {"used": ls_used, "limit": ls_limit},
            "monthly_ai_credits": {"remaining": ai_remaining, "limit": ai_limit},
            "monthly_name_generations": {"used": ng_used, "limit": ai_limit},
            "monthly_name_generations_used": ng_used,
            "monthly_applications": {"used": app_used, "limit": app_limit},
            "watchlist_items": {"used": wl_count, "limit": wl_limit},
            "logo_credits": {"remaining": ai_remaining, "limit": ai_limit},
            "can_track_logos": can_track_logos,
        },
    }

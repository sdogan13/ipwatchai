"""Dashboard stats service helpers used by HTTP route modules."""

from database.crud import Database
from models.schemas import DashboardStats
from utils.deadline import active_similarity_conflict_sql


async def get_dashboard_stats_data(
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    plan_limit_getter=None,
    report_eligibility_checker=None,
):
    """Return the main dashboard statistics payload for the current user."""
    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan

    if plan_limit_getter is None:
        from utils.subscription import get_plan_limit

        plan_limit_getter = get_plan_limit

    if report_eligibility_checker is None:
        from utils.subscription import check_report_eligibility

        report_eligibility_checker = check_report_eligibility

    with database_factory() as db:
        cur = db.cursor()
        org_id = str(current_user.organization_id)

        cur.execute(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE is_active) as active
            FROM watchlist_mt WHERE organization_id = %s
        """,
            (org_id,),
        )
        wl = cur.fetchone()

        cur.execute(
            f"""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE a.status = 'new') as new,
                COUNT(*) FILTER (WHERE a.severity = 'critical' AND a.status != 'dismissed') as critical,
                COUNT(*) FILTER (WHERE a.created_at > NOW() - INTERVAL '7 days') as this_week
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
              AND {active_similarity_conflict_sql('a', 't')}
        """,
            (org_id,),
        )
        al = cur.fetchone()

        cur.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE {active_similarity_conflict_sql('a', 't')}
                    AND a.status != 'dismissed') as active_deadlines,
                COUNT(*) FILTER (WHERE t.appeal_deadline IS NULL
                    AND (t.final_status IS NULL OR t.final_status = 'Başvuruldu')
                    AND a.status != 'dismissed') as pre_publication
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
        """,
            (org_id,),
        )
        dl = cur.fetchone()

        cur.execute(
            """
            SELECT COALESCE(SUM(au.live_searches), 0) as cnt
            FROM api_usage au
            JOIN users u ON au.user_id = u.id
            WHERE u.organization_id = %s
              AND au.usage_date >= date_trunc('month', CURRENT_DATE)
        """,
            (org_id,),
        )
        searches_row = cur.fetchone()
        searches_this_month = searches_row["cnt"] if searches_row else 0

        plan = user_plan_getter(db, str(current_user.id))
        plan_name = plan["plan_name"]

        wl_limit = plan_limit_getter(plan_name, "max_watchlist_items")
        user_limit = plan_limit_getter(plan_name, "max_users")
        ls_limit = plan_limit_getter(plan_name, "max_daily_live_searches")
        report_limit = plan_limit_getter(plan_name, "monthly_reports")
        report_eligibility = report_eligibility_checker(db, plan_name, org_id)

        cur.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE organization_id = %s AND is_active = TRUE",
            (org_id,),
        )
        user_count = cur.fetchone()["cnt"]

    return DashboardStats(
        watchlist_count=wl["total"],
        active_watchlist=wl["active"],
        total_alerts=al["total"],
        new_alerts=al["new"],
        critical_alerts=al["critical"],
        alerts_this_week=al["this_week"],
        searches_this_month=searches_this_month,
        active_deadline_count=dl["active_deadlines"],
        pre_publication_count=dl["pre_publication"],
        plan_usage={
            "watchlist": {"used": wl["active"], "limit": wl_limit},
            "users": {"used": user_count, "limit": user_limit},
            "searches": {"used": searches_this_month, "limit": ls_limit},
            "reports": {
                "used": report_eligibility["reports_used"],
                "limit": report_limit,
            },
        },
    )

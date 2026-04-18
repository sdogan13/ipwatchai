"""Dashboard stats service helpers used by HTTP route modules."""

from database.crud import Database
from models.schemas import DashboardStats


async def get_dashboard_stats_data(
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    plan_limit_getter=None,
):
    """Return the main dashboard statistics payload for the current user."""
    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan

    if plan_limit_getter is None:
        from utils.subscription import get_plan_limit

        plan_limit_getter = get_plan_limit

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
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE a.status = 'new') as new,
                COUNT(*) FILTER (WHERE a.severity = 'critical' AND a.status != 'dismissed') as critical,
                COUNT(*) FILTER (WHERE a.created_at > NOW() - INTERVAL '7 days') as this_week
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
              AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
        """,
            (org_id,),
        )
        al = cur.fetchone()

        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE t.appeal_deadline IS NOT NULL
                    AND t.appeal_deadline >= CURRENT_DATE
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
            SELECT COALESCE(SUM(au.quick_searches), 0) + COALESCE(SUM(au.live_searches), 0) as cnt
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
        qs_limit = plan_limit_getter(plan_name, "max_daily_quick_searches")
        ls_limit = plan_limit_getter(plan_name, "monthly_live_searches")
        report_limit = plan_limit_getter(plan_name, "monthly_reports")

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
            "searches": {"used": searches_this_month, "limit": qs_limit + ls_limit},
            "reports": {"used": 0, "limit": report_limit},
        },
    )

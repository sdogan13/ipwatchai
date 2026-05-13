"""Organization service helpers used by HTTP route modules."""

from uuid import UUID

from fastapi import HTTPException

from database.crud import Database, OrganizationCRUD, WatchlistCRUD
from models.schemas import OrganizationResponse, OrganizationStats


async def get_organization_data(
    current_user,
    database_factory=Database,
    organization_crud=OrganizationCRUD,
):
    """Return the current organization payload."""
    with database_factory() as db:
        org = organization_crud.get_by_id(db, current_user.organization_id)
        return OrganizationResponse(**org)


async def update_organization_record(
    data,
    current_user,
    database_factory=Database,
    organization_crud=OrganizationCRUD,
):
    """Update and return the current organization payload."""
    with database_factory() as db:
        org = organization_crud.update(db, current_user.organization_id, data)
        return OrganizationResponse(**org)


async def get_organization_stats_data(
    current_user,
    database_factory=Database,
    organization_crud=OrganizationCRUD,
):
    """Return the organization dashboard stats payload."""
    with database_factory() as db:
        stats = organization_crud.get_stats(db, current_user.organization_id)
        org_id = str(current_user.organization_id)
        cur = db.cursor()
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
        searches = cur.fetchone()
        return OrganizationStats(
            user_count=stats.get("user_count", 0),
            active_watchlist_items=stats.get("active_watchlist_items", 0),
            new_alerts=stats.get("new_alerts", 0),
            critical_alerts=stats.get("critical_alerts", 0),
            searches_this_month=searches["cnt"] if searches else 0,
            storage_used_mb=0.0,
        )


async def get_organization_settings_data(
    current_user,
    database_factory=Database,
):
    """Return the current organization settings payload."""
    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, name, default_alert_threshold
            FROM organizations WHERE id = %s
        """,
            (str(current_user.organization_id),),
        )
        org = cur.fetchone()

        return {
            "organization_id": str(org["id"]),
            "name": org["name"],
            "default_alert_threshold": org["default_alert_threshold"] or 0.7,
        }


async def prepare_organization_threshold_rescan(
    threshold,
    current_user,
    database_factory=Database,
    watchlist_crud=WatchlistCRUD,
):
    """Update organization threshold and return the watchlist item ids to rescan."""
    if threshold < 0.3 or threshold > 0.99:
        raise HTTPException(
            status_code=400,
            detail="Threshold must be between 0.3 and 0.99",
        )

    with database_factory() as db:
        cur = db.cursor()
        org_id = str(current_user.organization_id)

        cur.execute(
            """
            UPDATE organizations SET default_alert_threshold = %s WHERE id = %s
        """,
            (threshold, org_id),
        )

        cur.execute("DELETE FROM alerts_mt WHERE organization_id = %s", (org_id,))
        deleted_alerts = cur.rowcount

        cur.execute(
            """
            UPDATE watchlist_mt SET alert_threshold = %s, last_scan_at = NULL
            WHERE organization_id = %s
        """,
            (threshold, org_id),
        )

        _, total = watchlist_crud.get_by_organization(
            db,
            current_user.organization_id,
            active_only=True,
            page_size=1,
        )
        items, _ = watchlist_crud.get_by_organization(
            db,
            current_user.organization_id,
            active_only=True,
            page_size=max(total, 1),
        )

        db.commit()

    if not items:
        return {
            "message": (
                f"%{int(threshold * 100)} esik ayarlandi. "
                f"Eski {deleted_alerts} uyari silindi. Taranacak marka yok."
            ),
            "item_ids": [],
        }

    return {
        "message": (
            f"%{int(threshold * 100)} esik ile {len(items)} marka taramaya alindi. "
            f"Eski {deleted_alerts} uyari silindi."
        ),
        "item_ids": [UUID(str(item["id"])) for item in items],
    }

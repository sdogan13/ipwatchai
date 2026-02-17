"""
Superadmin API endpoints.
All endpoints require is_superadmin=True.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Body, Query
from psycopg2.extras import RealDictCursor

from auth.authentication import CurrentUser, require_superadmin
from database.crud import Database
from utils.settings_manager import settings_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _audit_log(db: Database, user_id: str, action: str, details: dict):
    """Write an audit log entry for admin actions."""
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO audit_log (user_id, action, resource_type, metadata)
        VALUES (%s, %s, %s, %s)
        """,
        (user_id, action, "admin", json.dumps(details, ensure_ascii=False, default=str)),
    )


# ============ SETTINGS CRUD ============


@router.get("/settings")
async def list_settings(current_user: CurrentUser = Depends(require_superadmin())):
    """List all runtime settings."""
    return settings_manager.get_all()


@router.get("/settings/{category}")
async def get_settings_by_category(
    category: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Get all settings in a category."""
    return settings_manager.get_category(category)


@router.put("/settings/{key:path}")
async def update_setting(
    key: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """
    Update a single setting.
    Body: {"value": <any>, "category": "plan_limits", "description": "...", "value_type": "integer"}
    """
    value = payload.get("value")
    if value is None:
        raise HTTPException(status_code=400, detail="'value' is required")

    with Database() as db:
        conn = db.conn
        settings_manager.set(
            key=key,
            value=value,
            category=payload.get("category", "general"),
            description=payload.get("description"),
            value_type=payload.get("value_type", "string"),
            updated_by=str(current_user.id),
            conn=conn,
        )
        _audit_log(db, str(current_user.id), "setting_changed", {"key": key, "new_value": value})
        db.commit()

    return {"status": "ok", "key": key, "value": value}


@router.delete("/settings/{key:path}")
async def delete_setting(
    key: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Delete a setting (revert to code default)."""
    with Database() as db:
        conn = db.conn
        settings_manager.delete(key, conn=conn)
        _audit_log(db, str(current_user.id), "setting_deleted", {"key": key})
        db.commit()

    return {"status": "ok", "key": key, "reverted_to": "code_default"}


# ============ OVERVIEW / DASHBOARD DATA ============


@router.get("/overview")
async def admin_overview(current_user: CurrentUser = Depends(require_superadmin())):
    """Dashboard overview stats with revenue metrics."""
    stats = {}

    with Database() as db:
        cur = db.cursor()

        cur.execute("SELECT COUNT(*) as cnt FROM users WHERE is_active = TRUE")
        stats["total_active_users"] = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) as cnt FROM organizations WHERE is_active = TRUE")
        stats["total_active_orgs"] = cur.fetchone()["cnt"]

        cur.execute("""
            SELECT COALESCE(sp.name, 'free') as plan, COUNT(DISTINCT o.id) as org_count
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.is_active = TRUE
            GROUP BY sp.name
        """)
        stats["orgs_by_plan"] = {row["plan"]: row["org_count"] for row in cur.fetchall()}

        cur.execute("SELECT COUNT(*) as cnt FROM trademarks")
        stats["total_trademarks"] = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) as cnt FROM watchlist_mt WHERE is_active = TRUE")
        stats["total_watchlist_items"] = cur.fetchone()["cnt"]

        cur.execute("""
            SELECT COUNT(*) as cnt FROM users
            WHERE created_at >= NOW() - INTERVAL '7 days'
        """)
        stats["new_users_7d"] = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) as cnt FROM alerts_mt WHERE status NOT IN ('resolved', 'dismissed')")
        stats["total_alerts"] = cur.fetchone()["cnt"]

        cur.execute("""
            SELECT COALESCE(SUM(quick_searches), 0) + COALESCE(SUM(live_searches), 0) as cnt
            FROM api_usage WHERE usage_date = CURRENT_DATE
        """)
        stats["api_calls_today"] = cur.fetchone()["cnt"]

        # Revenue metrics: MRR (Monthly Recurring Revenue) from active paying orgs
        cur.execute("""
            SELECT COALESCE(sp.name, 'free') as plan_name,
                   COALESCE(sp.price_monthly, 0) as price,
                   COUNT(DISTINCT o.id) as org_count
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.is_active = TRUE
            GROUP BY sp.name, sp.price_monthly
        """)
        revenue_rows = cur.fetchall()
        mrr = sum(row["price"] * row["org_count"] for row in revenue_rows if row["price"])
        stats["mrr"] = float(mrr)
        stats["revenue_by_plan"] = {
            row["plan_name"]: {"price": float(row["price"] or 0), "orgs": row["org_count"],
                               "revenue": float((row["price"] or 0) * row["org_count"])}
            for row in revenue_rows
        }

        # Recent plan changes (last 7 days)
        cur.execute("""
            SELECT COUNT(*) as cnt FROM audit_log
            WHERE action = 'plan_changed'
            AND created_at >= NOW() - INTERVAL '7 days'
        """)
        stats["plan_changes_7d"] = cur.fetchone()["cnt"]

        # Total applications this month
        cur.execute("""
            SELECT COUNT(*) as cnt FROM trademark_applications_mt
            WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE)
        """)
        stats["applications_this_month"] = cur.fetchone()["cnt"]

        # Settings overrides count
        cur.execute("SELECT COUNT(*) as cnt FROM app_settings WHERE category = 'plan_limits'")
        stats["active_overrides"] = cur.fetchone()["cnt"]

    return stats


# ============ ORGANIZATION MANAGEMENT ============


@router.get("/organizations")
async def list_organizations(
    current_user: CurrentUser = Depends(require_superadmin()),
    search: str = Query(None),
    plan: str = Query(None),
    is_active: bool = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List all organizations with filters."""
    with Database() as db:
        cur = db.cursor()

        query = """
            SELECT o.id, o.name, o.slug, o.email, o.is_active, o.created_at,
                   COALESCE(sp.name, 'free') as plan_name,
                   sp.price_monthly,
                   o.logo_credits_monthly, o.logo_credits_purchased, o.name_credits_purchased,
                   (SELECT COUNT(*) FROM users u WHERE u.organization_id = o.id AND u.is_active = TRUE) as user_count,
                   (SELECT COUNT(*) FROM watchlist_mt w WHERE w.organization_id = o.id AND w.is_active = TRUE) as watchlist_count
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE 1=1
        """
        params = []

        if search:
            query += " AND (o.name ILIKE %s OR o.email ILIKE %s OR o.slug ILIKE %s)"
            s = f"%{search}%"
            params.extend([s, s, s])
        if plan:
            query += " AND COALESCE(sp.name, 'free') = %s"
            params.append(plan)
        if is_active is not None:
            query += " AND o.is_active = %s"
            params.append(is_active)

        # Count
        count_query = query.replace(
            query[query.index("SELECT"):query.index("FROM")],
            "SELECT COUNT(*) as cnt ",
        )
        # Simpler: build count separately
        count_sql = """
            SELECT COUNT(*) as cnt
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE 1=1
        """
        count_params = []
        if search:
            count_sql += " AND (o.name ILIKE %s OR o.email ILIKE %s OR o.slug ILIKE %s)"
            s = f"%{search}%"
            count_params.extend([s, s, s])
        if plan:
            count_sql += " AND COALESCE(sp.name, 'free') = %s"
            count_params.append(plan)
        if is_active is not None:
            count_sql += " AND o.is_active = %s"
            count_params.append(is_active)

        cur.execute(count_sql, count_params)
        total = cur.fetchone()["cnt"]

        query += " ORDER BY o.created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        orgs = [dict(row) for row in cur.fetchall()]

    return {"organizations": orgs, "total": total, "limit": limit, "offset": offset}


@router.get("/organizations/{org_id}")
async def get_organization_detail(
    org_id: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Get full organization detail including users and usage."""
    with Database() as db:
        cur = db.cursor()

        cur.execute("""
            SELECT o.*, COALESCE(sp.name, 'free') as plan_name, sp.price_monthly
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.id = %s
        """, (org_id,))
        org = cur.fetchone()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        org_dict = dict(org)

        cur.execute("""
            SELECT id, email, first_name, last_name, role, is_active,
                   COALESCE(is_superadmin, FALSE) as is_superadmin,
                   last_login_at, created_at
            FROM users WHERE organization_id = %s ORDER BY created_at
        """, (org_id,))
        org_dict["users"] = [dict(row) for row in cur.fetchall()]

    return org_dict


@router.put("/organizations/{org_id}/plan")
async def change_org_plan(
    org_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Change an organization's subscription plan. Body: {"plan_name": "professional"}"""
    plan_name = payload.get("plan_name")
    if not plan_name:
        raise HTTPException(status_code=400, detail="'plan_name' is required")

    with Database() as db:
        cur = db.cursor()

        cur.execute(
            "SELECT id, name FROM subscription_plans WHERE name = %s AND is_active = TRUE",
            (plan_name,),
        )
        plan = cur.fetchone()
        if not plan:
            raise HTTPException(status_code=404, detail=f"Plan '{plan_name}' not found")

        cur.execute("""
            SELECT COALESCE(sp.name, 'free') as old_plan
            FROM organizations o LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.id = %s
        """, (org_id,))
        old = cur.fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="Organization not found")
        old_plan = old["old_plan"]

        cur.execute(
            "UPDATE organizations SET subscription_plan_id = %s WHERE id = %s",
            (str(plan["id"]), org_id),
        )

        _audit_log(db, str(current_user.id), "plan_changed", {
            "organization_id": org_id,
            "old_plan": old_plan,
            "new_plan": plan_name,
        })
        db.commit()

    logger.info(f"Plan changed: org={org_id} {old_plan} -> {plan_name} by {current_user.id}")
    return {"status": "ok", "organization_id": org_id, "old_plan": old_plan, "new_plan": plan_name}


@router.put("/organizations/{org_id}/status")
async def toggle_org_status(
    org_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Activate or deactivate an organization. Body: {"is_active": true/false}"""
    is_active = payload.get("is_active")
    if is_active is None:
        raise HTTPException(status_code=400, detail="'is_active' is required")

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE organizations SET is_active = %s WHERE id = %s RETURNING id",
            (is_active, org_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Organization not found")

        _audit_log(db, str(current_user.id), "org_status_changed", {
            "organization_id": org_id,
            "is_active": is_active,
        })
        db.commit()

    return {"status": "ok", "organization_id": org_id, "is_active": is_active}


# ============ USER MANAGEMENT (CROSS-ORG) ============


@router.get("/users")
async def list_all_users(
    current_user: CurrentUser = Depends(require_superadmin()),
    search: str = Query(None),
    org_id: str = Query(None),
    role: str = Query(None),
    is_active: bool = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List all users across all organizations."""
    with Database() as db:
        cur = db.cursor()

        query = """
            SELECT u.id, u.email, u.first_name, u.last_name, u.role, u.is_active,
                   COALESCE(u.is_superadmin, FALSE) as is_superadmin,
                   u.last_login_at, u.created_at, u.organization_id,
                   o.name as org_name, COALESCE(sp.name, 'free') as plan_name
            FROM users u
            JOIN organizations o ON u.organization_id = o.id
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE 1=1
        """
        params = []

        if search:
            query += " AND (u.email ILIKE %s OR u.first_name ILIKE %s OR u.last_name ILIKE %s)"
            s = f"%{search}%"
            params.extend([s, s, s])
        if org_id:
            query += " AND u.organization_id = %s"
            params.append(org_id)
        if role:
            query += " AND u.role = %s"
            params.append(role)
        if is_active is not None:
            query += " AND u.is_active = %s"
            params.append(is_active)

        # Count
        count_sql = """
            SELECT COUNT(*) as cnt
            FROM users u
            JOIN organizations o ON u.organization_id = o.id
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE 1=1
        """
        count_params = []
        if search:
            count_sql += " AND (u.email ILIKE %s OR u.first_name ILIKE %s OR u.last_name ILIKE %s)"
            s = f"%{search}%"
            count_params.extend([s, s, s])
        if org_id:
            count_sql += " AND u.organization_id = %s"
            count_params.append(org_id)
        if role:
            count_sql += " AND u.role = %s"
            count_params.append(role)
        if is_active is not None:
            count_sql += " AND u.is_active = %s"
            count_params.append(is_active)

        cur.execute(count_sql, count_params)
        total = cur.fetchone()["cnt"]

        query += " ORDER BY u.created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        users = [dict(row) for row in cur.fetchall()]

    return {"users": users, "total": total, "limit": limit, "offset": offset}


@router.put("/users/{user_id}/role")
async def change_user_role(
    user_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Change a user's org role. Body: {"role": "admin"}"""
    new_role = payload.get("role")
    valid_roles = ["owner", "admin", "member", "viewer"]
    if new_role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {valid_roles}")

    with Database() as db:
        cur = db.cursor()

        cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
        old = cur.fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="User not found")

        cur.execute("UPDATE users SET role = %s WHERE id = %s", (new_role, user_id))

        _audit_log(db, str(current_user.id), "user_role_changed", {
            "target_user_id": user_id,
            "old_role": old["role"],
            "new_role": new_role,
        })
        db.commit()

    return {"status": "ok", "user_id": user_id, "old_role": old["role"], "new_role": new_role}


@router.put("/users/{user_id}/superadmin")
async def toggle_superadmin(
    user_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Grant or revoke superadmin. Body: {"is_superadmin": true/false}"""
    is_superadmin = payload.get("is_superadmin")
    if is_superadmin is None:
        raise HTTPException(status_code=400, detail="'is_superadmin' is required")

    if user_id == str(current_user.id) and not is_superadmin:
        raise HTTPException(status_code=400, detail="Cannot revoke your own superadmin status")

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE users SET is_superadmin = %s WHERE id = %s RETURNING id",
            (is_superadmin, user_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="User not found")

        _audit_log(db, str(current_user.id), "superadmin_toggled", {
            "target_user_id": user_id,
            "is_superadmin": is_superadmin,
        })
        db.commit()

    return {"status": "ok", "user_id": user_id, "is_superadmin": is_superadmin}


@router.put("/users/{user_id}/status")
async def toggle_user_status(
    user_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Activate or deactivate a user. Body: {"is_active": true/false}"""
    is_active = payload.get("is_active")
    if is_active is None:
        raise HTTPException(status_code=400, detail="'is_active' is required")

    if user_id == str(current_user.id) and not is_active:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE users SET is_active = %s WHERE id = %s RETURNING id",
            (is_active, user_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="User not found")

        _audit_log(db, str(current_user.id), "user_status_changed", {
            "target_user_id": user_id,
            "is_active": is_active,
        })
        db.commit()

    return {"status": "ok", "user_id": user_id, "is_active": is_active}


# ============ AUDIT LOG ============


@router.get("/audit-log")
async def get_audit_log(
    current_user: CurrentUser = Depends(require_superadmin()),
    action: str = Query(None),
    user_id: str = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """View audit log entries."""
    with Database() as db:
        cur = db.cursor()

        query = """
            SELECT al.*, u.email as user_email, u.first_name, u.last_name
            FROM audit_log al
            LEFT JOIN users u ON al.user_id = u.id
            WHERE 1=1
        """
        params = []

        if action:
            query += " AND al.action = %s"
            params.append(action)
        if user_id:
            query += " AND al.user_id = %s"
            params.append(user_id)

        query += " ORDER BY al.created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        entries = [dict(row) for row in cur.fetchall()]

    return {"entries": entries, "limit": limit, "offset": offset}


# ============ CREDIT MANAGEMENT ============


@router.get("/organizations/{org_id}/credits")
async def get_org_credits(
    org_id: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Get current credit balances for an organization."""
    from utils.subscription import get_plan_limit

    with Database() as db:
        cur = db.cursor()

        cur.execute("""
            SELECT o.logo_credits_monthly, o.logo_credits_purchased, o.name_credits_purchased,
                   o.logo_credits_reset_at,
                   COALESCE(o.ai_credits_monthly, 0) as ai_credits_monthly,
                   COALESCE(o.ai_credits_purchased, 0) as ai_credits_purchased,
                   o.ai_credits_reset_at,
                   COALESCE(sp.name, 'free') as plan_name
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.id = %s
        """, (org_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Organization not found")

        plan_name = row["plan_name"]

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE feature_type = 'LOGO') as logo_generations_this_month,
                COUNT(*) FILTER (WHERE feature_type = 'NAME') as name_generations_this_month
            FROM generation_logs
            WHERE org_id = %s
            AND created_at >= DATE_TRUNC('month', CURRENT_DATE)
        """, (org_id,))
        usage = cur.fetchone()

    ai_plan_limit = get_plan_limit(plan_name, "monthly_ai_credits")

    return {
        "organization_id": org_id,
        "plan": plan_name,
        "ai_credits": {
            "monthly_remaining": row["ai_credits_monthly"],
            "purchased": row["ai_credits_purchased"],
            "plan_limit": ai_plan_limit,
            "reset_at": str(row["ai_credits_reset_at"]) if row["ai_credits_reset_at"] else None,
        },
        "logo_credits": {
            "monthly_remaining": row["logo_credits_monthly"] or 0,
            "purchased": row["logo_credits_purchased"] or 0,
            "used_this_month": usage["logo_generations_this_month"] if usage else 0,
            "reset_at": str(row["logo_credits_reset_at"]) if row["logo_credits_reset_at"] else None,
        },
        "name_credits": {
            "purchased": row["name_credits_purchased"] or 0,
            "used_this_month": usage["name_generations_this_month"] if usage else 0,
        },
    }


@router.put("/organizations/{org_id}/credits")
async def adjust_org_credits(
    org_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """
    Adjust credits for an organization.
    Body: {
        "credit_type": "logo_purchased" | "logo_monthly" | "name_purchased",
        "operation": "set" | "add" | "subtract",
        "amount": 10,
        "reason": "Manual adjustment - customer complaint"
    }
    """
    credit_type = payload.get("credit_type")
    operation = payload.get("operation", "set")
    amount = payload.get("amount")
    reason = payload.get("reason", "")

    valid_types = {
        "logo_purchased": "logo_credits_purchased",
        "logo_monthly": "logo_credits_monthly",
        "name_purchased": "name_credits_purchased",
        "ai_monthly": "ai_credits_monthly",
        "ai_purchased": "ai_credits_purchased",
    }

    if credit_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"credit_type must be one of: {list(valid_types.keys())}")
    if amount is None or not isinstance(amount, (int, float)):
        raise HTTPException(status_code=400, detail="'amount' must be a number")
    if operation not in ("set", "add", "subtract"):
        raise HTTPException(status_code=400, detail="operation must be 'set', 'add', or 'subtract'")

    column = valid_types[credit_type]

    from psycopg2 import sql as psql

    with Database() as db:
        cur = db.cursor()

        cur.execute(
            psql.SQL("SELECT {} FROM organizations WHERE id = %s").format(psql.Identifier(column)),
            (org_id,)
        )
        old = cur.fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="Organization not found")
        old_value = old[column] or 0

        col_id = psql.Identifier(column)
        if operation == "set":
            new_value = int(amount)
            cur.execute(
                psql.SQL("UPDATE organizations SET {} = %s WHERE id = %s").format(col_id),
                (new_value, org_id)
            )
        elif operation == "add":
            new_value = old_value + int(amount)
            cur.execute(
                psql.SQL("UPDATE organizations SET {} = {} + %s WHERE id = %s").format(col_id, col_id),
                (int(amount), org_id)
            )
        else:  # subtract
            new_value = max(0, old_value - int(amount))
            cur.execute(
                psql.SQL("UPDATE organizations SET {} = GREATEST(0, {} - %s) WHERE id = %s").format(col_id, col_id),
                (int(amount), org_id)
            )

        _audit_log(db, str(current_user.id), "credit_adjustment", {
            "organization_id": org_id,
            "credit_type": credit_type,
            "operation": operation,
            "amount": amount,
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason,
        })
        db.commit()

    return {
        "status": "ok",
        "organization_id": org_id,
        "credit_type": credit_type,
        "old_value": old_value,
        "new_value": new_value,
    }


@router.post("/credits/bulk")
async def bulk_credit_adjustment(
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """
    Bulk credit operation across orgs by plan.
    Body: {
        "plan_filter": "professional" or "all",
        "credit_type": "logo_purchased",
        "operation": "add" or "set",
        "amount": 10,
        "reason": "Q1 bonus credits"
    }
    """
    plan_filter = payload.get("plan_filter", "all")
    credit_type = payload.get("credit_type")
    operation = payload.get("operation")
    amount = payload.get("amount")
    reason = payload.get("reason", "")

    valid_types = {
        "logo_purchased": "logo_credits_purchased",
        "logo_monthly": "logo_credits_monthly",
        "name_purchased": "name_credits_purchased",
        "ai_monthly": "ai_credits_monthly",
        "ai_purchased": "ai_credits_purchased",
    }

    if credit_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"credit_type must be one of: {list(valid_types.keys())}")
    if operation not in ("add", "set"):
        raise HTTPException(status_code=400, detail="Bulk operation only supports 'add' or 'set'")

    column = valid_types[credit_type]

    from psycopg2 import sql as psql
    col_id = psql.Identifier(column)

    with Database() as db:
        cur = db.cursor()

        where_parts = [psql.SQL("WHERE o.is_active = TRUE")]
        params = []
        if plan_filter != "all":
            where_parts.append(psql.SQL("AND COALESCE(sp.name, 'free') = %s"))
            params.append(plan_filter)
        where_clause = psql.SQL(" ").join(where_parts)

        subquery = psql.SQL("""
            SELECT o.id FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            {where}
        """).format(where=where_clause)

        if operation == "add":
            query = psql.SQL(
                "UPDATE organizations SET {col} = {col} + %s WHERE id IN ({sub})"
            ).format(col=col_id, sub=subquery)
            cur.execute(query, [int(amount)] + params)
        else:  # set
            query = psql.SQL(
                "UPDATE organizations SET {col} = %s WHERE id IN ({sub})"
            ).format(col=col_id, sub=subquery)
            cur.execute(query, [int(amount)] + params)

        affected = cur.rowcount

        _audit_log(db, str(current_user.id), "bulk_credit_adjustment", {
            "plan_filter": plan_filter,
            "credit_type": credit_type,
            "operation": operation,
            "amount": amount,
            "affected_orgs": affected,
            "reason": reason,
        })
        db.commit()

    return {"status": "ok", "affected_organizations": affected}


# ============ DISCOUNT CODES ============


@router.get("/discount-codes")
async def list_discount_codes(
    current_user: CurrentUser = Depends(require_superadmin()),
    is_active: bool = Query(None),
):
    """List all discount codes."""
    with Database() as db:
        cur = db.cursor()
        query = "SELECT * FROM discount_codes WHERE 1=1"
        params = []
        if is_active is not None:
            query += " AND is_active = %s"
            params.append(is_active)
        query += " ORDER BY created_at DESC"
        cur.execute(query, params)
        codes = [dict(row) for row in cur.fetchall()]
    return {"discount_codes": codes}


@router.post("/discount-codes")
async def create_discount_code(
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """
    Create a new discount code.
    Body: {
        "code": "LAUNCH20",
        "description": "Launch promotion 20% off",
        "discount_type": "percentage" or "fixed",
        "discount_value": 20.0,
        "applies_to_plan": "professional" (null for all),
        "max_uses": 100 (null for unlimited),
        "valid_from": "2026-01-01T00:00:00" (optional),
        "valid_until": "2026-12-31T23:59:59" (null for no expiry)
    }
    """
    code = payload.get("code", "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="'code' is required")

    discount_type = payload.get("discount_type", "percentage")
    if discount_type not in ("percentage", "fixed"):
        raise HTTPException(status_code=400, detail="discount_type must be 'percentage' or 'fixed'")

    discount_value = payload.get("discount_value")
    if discount_value is None or discount_value <= 0:
        raise HTTPException(status_code=400, detail="discount_value must be positive")

    if discount_type == "percentage" and discount_value > 100:
        raise HTTPException(status_code=400, detail="Percentage discount cannot exceed 100")

    with Database() as db:
        cur = db.cursor()

        cur.execute("SELECT id FROM discount_codes WHERE code = %s", (code,))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail=f"Code '{code}' already exists")

        cur.execute("""
            INSERT INTO discount_codes
                (code, description, discount_type, discount_value,
                 applies_to_plan, max_uses, valid_from, valid_until, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            code,
            payload.get("description"),
            discount_type,
            discount_value,
            payload.get("applies_to_plan"),
            payload.get("max_uses"),
            payload.get("valid_from"),
            payload.get("valid_until"),
            str(current_user.id),
        ))

        _audit_log(db, str(current_user.id), "discount_code_created", {
            "code": code,
            "discount_type": discount_type,
            "discount_value": float(discount_value),
        })
        db.commit()

    return {"status": "ok", "code": code}


@router.put("/discount-codes/{code_id}")
async def update_discount_code(
    code_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Update a discount code (description, max_uses, valid_until, is_active, discount_value)."""
    allowed_fields = {"description", "max_uses", "valid_until", "is_active", "discount_value"}
    updates = {k: v for k, v in payload.items() if k in allowed_fields}

    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    from psycopg2 import sql as psql

    with Database() as db:
        cur = db.cursor()

        set_parts = [psql.SQL("{} = %s").format(psql.Identifier(k)) for k in updates]
        set_parts.append(psql.SQL("updated_at = NOW()"))
        set_clause = psql.SQL(", ").join(set_parts)
        values = list(updates.values()) + [code_id]

        cur.execute(
            psql.SQL("UPDATE discount_codes SET {} WHERE id = %s").format(set_clause),
            values,
        )

        _audit_log(db, str(current_user.id), "discount_code_updated", {
            "code_id": code_id,
            "changes": {k: str(v) for k, v in updates.items()},
        })
        db.commit()

    return {"status": "ok", "code_id": code_id}


@router.delete("/discount-codes/{code_id}")
async def deactivate_discount_code(
    code_id: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Deactivate a discount code (soft delete)."""
    with Database() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE discount_codes SET is_active = FALSE, updated_at = NOW() WHERE id = %s",
            (code_id,),
        )
        _audit_log(db, str(current_user.id), "discount_code_deactivated", {"code_id": code_id})
        db.commit()

    return {"status": "ok", "code_id": code_id}


@router.get("/discount-codes/{code_id}/usage")
async def get_discount_code_usage(
    code_id: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """View usage history for a discount code."""
    with Database() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT dcu.*, o.name as org_name, o.email as org_email
            FROM discount_code_usage dcu
            JOIN organizations o ON dcu.organization_id = o.id
            WHERE dcu.discount_code_id = %s
            ORDER BY dcu.applied_at DESC
        """, (code_id,))
        usage = [dict(row) for row in cur.fetchall()]

    return {"usage": usage, "total_uses": len(usage)}


# ============ PRICING MANAGEMENT ============


@router.get("/plans")
async def list_plans(current_user: CurrentUser = Depends(require_superadmin())):
    """List all subscription plans with their DB values and code defaults."""
    from utils.subscription import PLAN_FEATURES

    with Database() as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM subscription_plans ORDER BY price_monthly ASC NULLS FIRST")
        db_plans = [dict(row) for row in cur.fetchall()]

        # Count orgs per plan for usage stats
        cur.execute("""
            SELECT COALESCE(sp.name, 'free') as plan_name, COUNT(*) as org_count
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.is_active = TRUE
            GROUP BY sp.name
        """)
        org_counts = {row["plan_name"]: row["org_count"] for row in cur.fetchall()}

    # Get all plan_limits overrides from settings
    overrides = settings_manager.get_category("plan_limits")

    # Categorize features for better UI grouping
    feature_categories = {
        "pricing": ["price_monthly", "price_annual_monthly"],
        "search_limits": ["max_daily_quick_searches", "monthly_live_searches"],
        "content_limits": ["max_watchlist_items", "max_users", "monthly_reports",
                           "monthly_applications", "monthly_ai_credits",
                           "name_suggestions_per_session", "daily_lead_views",
                           "auto_scan_max_items"],
        "boolean_flags": ["can_use_live_scraping", "can_track_logos",
                          "can_view_holder_portfolio", "can_export_reports",
                          "can_export_csv_leads", "priority_support",
                          "api_access", "dedicated_account_manager"],
        "other": ["auto_scan_frequency"],
    }

    result = []
    for db_plan in db_plans:
        plan_name = db_plan["name"]
        code_defaults = PLAN_FEATURES.get(plan_name, {})

        plan_overrides = {
            k.replace(f"plan.{plan_name}.", ""): v["value"]
            for k, v in overrides.items()
            if k.startswith(f"plan.{plan_name}.")
        }

        result.append({
            "db_record": db_plan,
            "code_defaults": code_defaults,
            "active_overrides": plan_overrides,
            "active_orgs": org_counts.get(plan_name, 0),
        })

    return {"plans": result, "feature_categories": feature_categories}


@router.put("/plans/{plan_name}/pricing")
async def update_plan_pricing(
    plan_name: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """
    Update plan pricing in the DB.
    Body: {"price_monthly": 999.00, "price_annual_monthly": 799.00,
           "display_name": "Pro", "description": "Professional plan", "is_active": true}
    """
    allowed = {"price_monthly", "price_annual_monthly", "display_name", "description", "is_active"}
    updates = {k: v for k, v in payload.items() if k in allowed}

    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    with Database() as db:
        cur = db.cursor()

        cur.execute("SELECT * FROM subscription_plans WHERE name = %s", (plan_name,))
        old = cur.fetchone()
        if not old:
            raise HTTPException(status_code=404, detail=f"Plan '{plan_name}' not found")

        from psycopg2 import sql as psql
        set_parts = [psql.SQL("{} = %s").format(psql.Identifier(k)) for k in updates]
        set_clause = psql.SQL(", ").join(set_parts)
        values = list(updates.values()) + [plan_name]

        cur.execute(
            psql.SQL("UPDATE subscription_plans SET {} WHERE name = %s").format(set_clause),
            values,
        )

        _audit_log(db, str(current_user.id), "plan_pricing_updated", {
            "plan": plan_name,
            "changes": {k: str(v) for k, v in updates.items()},
        })
        db.commit()

    return {"status": "ok", "plan": plan_name}


# ============ PAYMENT REFUNDS ============


@router.post("/payments/{payment_id}/refund")
async def refund_payment(
    payment_id: str,
    payload: dict = Body(default={}),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """
    Refund a completed payment via iyzico Refund API (full or partial).
    Body: {"amount": 499.00, "reason": "Customer requested"}
    Omit amount for full refund.
    """
    import iyzipay
    from api.payments import _get_iyzico_options, _activate_subscription

    refund_amount = payload.get("amount")  # None = full refund
    reason = payload.get("reason", "")

    with Database() as db:
        cur = db.cursor()

        # Fetch the payment
        cur.execute("SELECT * FROM payments WHERE id = %s", (payment_id,))
        payment = cur.fetchone()
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")

        if payment["status"] != "completed":
            raise HTTPException(status_code=400, detail="Only completed payments can be refunded")

        if payment.get("refund_status") in ("full",):
            raise HTTPException(status_code=400, detail="Payment already fully refunded")

        # Determine refund amount
        paid_amount = float(payment["amount"])
        if refund_amount is None:
            refund_amount = paid_amount
            refund_type = "full"
        else:
            refund_amount = float(refund_amount)
            if refund_amount <= 0 or refund_amount > paid_amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"Refund amount must be between 0 and {paid_amount}",
                )
            refund_type = "full" if refund_amount == paid_amount else "partial"

        # Extract paymentTransactionId from iyzico raw response
        raw_response = payment.get("iyzico_raw_response") or {}
        if isinstance(raw_response, str):
            raw_response = json.loads(raw_response)

        item_transactions = raw_response.get("itemTransactions", [])
        if not item_transactions:
            raise HTTPException(
                status_code=400,
                detail="No transaction ID found in iyzico response — cannot refund",
            )
        payment_transaction_id = item_transactions[0].get("paymentTransactionId", "")
        if not payment_transaction_id:
            raise HTTPException(
                status_code=400,
                detail="paymentTransactionId missing in iyzico response",
            )

        # Call iyzico Refund API
        refund_request = {
            'locale': 'tr',
            'conversationId': payment.get("iyzico_conversation_id", ""),
            'paymentTransactionId': payment_transaction_id,
            'price': f"{refund_amount:.2f}",
            'currency': payment.get("currency", "TRY"),
            'ip': '127.0.0.1',
        }

        try:
            refund_result = iyzipay.Refund().create(refund_request, _get_iyzico_options())
            result_str = refund_result.read().decode('utf-8')
            result_json = json.loads(result_str)
        except Exception as e:
            logger.error(f"iyzico refund API call failed: {e}")
            raise HTTPException(status_code=502, detail="Refund gateway error")

        if result_json.get("status") != "success":
            error_msg = result_json.get("errorMessage", "Unknown error")
            logger.error(f"iyzico refund failed: {error_msg}")
            raise HTTPException(status_code=502, detail=f"Refund failed: {error_msg}")

        # Update payment record with refund info
        cur.execute("""
            UPDATE payments
            SET refund_status = %s,
                refund_amount = %s,
                refunded_at = CURRENT_TIMESTAMP,
                refund_reason = %s,
                iyzico_refund_response = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (
            refund_type,
            refund_amount,
            reason,
            json.dumps(result_json),
            payment_id,
        ))

        # Full refund: downgrade org to free plan
        org_id = str(payment["organization_id"])
        if refund_type == "full":
            _activate_subscription(db, org_id, "free", "monthly")

        _audit_log(db, str(current_user.id), "payment_refunded", {
            "payment_id": payment_id,
            "organization_id": org_id,
            "refund_type": refund_type,
            "refund_amount": refund_amount,
            "original_amount": paid_amount,
            "reason": reason,
        })
        db.commit()

    logger.info(
        f"Payment {payment_id} refunded ({refund_type}): "
        f"{refund_amount} {payment.get('currency', 'TRY')} by {current_user.id}"
    )
    return {
        "status": "ok",
        "payment_id": payment_id,
        "refund_type": refund_type,
        "refund_amount": refund_amount,
    }


# ============ USAGE ANALYTICS ============


@router.get("/analytics/usage")
async def usage_analytics(
    current_user: CurrentUser = Depends(require_superadmin()),
    days: int = Query(30, ge=1, le=365),
):
    """API usage analytics over the last N days."""
    with Database() as db:
        cur = db.cursor()

        # Daily usage aggregates from api_usage table
        cur.execute("""
            SELECT usage_date as date,
                   COUNT(DISTINCT user_id) as unique_users,
                   COALESCE(SUM(quick_searches), 0) as quick_searches,
                   COALESCE(SUM(live_searches), 0) as live_searches,
                   COALESCE(SUM(name_generations), 0) as name_generations
            FROM api_usage
            WHERE usage_date >= CURRENT_DATE - %s * INTERVAL '1 day'
            GROUP BY usage_date
            ORDER BY usage_date DESC
        """, (days,))
        daily = [dict(row) for row in cur.fetchall()]

        # Usage by plan
        cur.execute("""
            SELECT COALESCE(sp.name, 'free') as plan,
                   COALESCE(SUM(au.quick_searches), 0) + COALESCE(SUM(au.live_searches), 0) as total_searches
            FROM api_usage au
            JOIN users u ON au.user_id = u.id
            JOIN organizations o ON u.organization_id = o.id
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE au.usage_date >= CURRENT_DATE - %s * INTERVAL '1 day'
            GROUP BY sp.name
        """, (days,))
        by_plan = {row["plan"]: row["total_searches"] for row in cur.fetchall()}

        # Top users by search volume
        cur.execute("""
            SELECT u.email, u.first_name, u.last_name, o.name as org_name,
                   COALESCE(SUM(au.quick_searches), 0) + COALESCE(SUM(au.live_searches), 0) as total_searches
            FROM api_usage au
            JOIN users u ON au.user_id = u.id
            JOIN organizations o ON u.organization_id = o.id
            WHERE au.usage_date >= CURRENT_DATE - %s * INTERVAL '1 day'
            GROUP BY u.id, u.email, u.first_name, u.last_name, o.name
            ORDER BY total_searches DESC
            LIMIT 20
        """, (days,))
        top_users = [dict(row) for row in cur.fetchall()]

        # Cost-bearing actions from generation_logs
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE feature_type = 'LOGO') as logo_generations,
                COUNT(*) FILTER (WHERE feature_type = 'NAME') as name_generations
            FROM generation_logs
            WHERE created_at >= CURRENT_DATE - %s * INTERVAL '1 day'
        """, (days,))
        costs = dict(cur.fetchone()) if cur.rowcount else {}

    return {
        "period_days": days,
        "daily_usage": daily,
        "usage_by_plan": by_plan,
        "top_users": top_users,
        "cost_bearing_actions": costs,
    }


@router.get("/analytics/export")
async def export_usage_csv(
    current_user: CurrentUser = Depends(require_superadmin()),
    days: int = Query(30, ge=1, le=365),
):
    """Export usage data as CSV."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    with Database() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT au.usage_date, u.email as user_email, o.name as org_name,
                   COALESCE(sp.name, 'free') as plan,
                   COALESCE(au.quick_searches, 0) as quick_searches,
                   COALESCE(au.live_searches, 0) as live_searches,
                   COALESCE(au.name_generations, 0) as name_generations
            FROM api_usage au
            LEFT JOIN users u ON au.user_id = u.id
            LEFT JOIN organizations o ON au.organization_id = o.id
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE au.usage_date >= CURRENT_DATE - %s * INTERVAL '1 day'
            ORDER BY au.usage_date DESC, u.email
        """, (days,))
        rows = cur.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "user_email", "org_name", "plan",
                     "quick_searches", "live_searches", "name_generations"])
    for row in rows:
        writer.writerow([
            row["usage_date"], row["user_email"], row["org_name"],
            row["plan"], row["quick_searches"], row["live_searches"],
            row["name_generations"],
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=usage_export_{days}d.csv"},
    )

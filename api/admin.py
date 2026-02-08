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
    """Dashboard overview stats."""
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
    with Database() as db:
        cur = db.cursor()

        cur.execute("""
            SELECT o.logo_credits_monthly, o.logo_credits_purchased, o.name_credits_purchased,
                   o.logo_credits_reset_at,
                   COALESCE(sp.name, 'free') as plan_name,
                   COALESCE(sp.logo_runs_per_month, 1) as plan_logo_monthly
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.id = %s
        """, (org_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Organization not found")

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE feature_type = 'LOGO') as logo_generations_this_month,
                COUNT(*) FILTER (WHERE feature_type = 'NAME') as name_generations_this_month
            FROM generation_logs
            WHERE org_id = %s
            AND created_at >= DATE_TRUNC('month', CURRENT_DATE)
        """, (org_id,))
        usage = cur.fetchone()

    return {
        "organization_id": org_id,
        "plan": row["plan_name"],
        "logo_credits": {
            "monthly_remaining": row["logo_credits_monthly"] or 0,
            "purchased": row["logo_credits_purchased"] or 0,
            "plan_default": row["plan_logo_monthly"] or 0,
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
    }

    if credit_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"credit_type must be one of: {list(valid_types.keys())}")
    if amount is None or not isinstance(amount, (int, float)):
        raise HTTPException(status_code=400, detail="'amount' must be a number")
    if operation not in ("set", "add", "subtract"):
        raise HTTPException(status_code=400, detail="operation must be 'set', 'add', or 'subtract'")

    column = valid_types[credit_type]

    with Database() as db:
        cur = db.cursor()

        cur.execute(f"SELECT {column} FROM organizations WHERE id = %s", (org_id,))
        old = cur.fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="Organization not found")
        old_value = old[column] or 0

        if operation == "set":
            new_value = int(amount)
            cur.execute(f"UPDATE organizations SET {column} = %s WHERE id = %s", (new_value, org_id))
        elif operation == "add":
            new_value = old_value + int(amount)
            cur.execute(f"UPDATE organizations SET {column} = {column} + %s WHERE id = %s", (int(amount), org_id))
        else:  # subtract
            new_value = max(0, old_value - int(amount))
            cur.execute(f"UPDATE organizations SET {column} = GREATEST(0, {column} - %s) WHERE id = %s", (int(amount), org_id))

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
    }

    if credit_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"credit_type must be one of: {list(valid_types.keys())}")
    if operation not in ("add", "set"):
        raise HTTPException(status_code=400, detail="Bulk operation only supports 'add' or 'set'")

    column = valid_types[credit_type]

    with Database() as db:
        cur = db.cursor()

        where = "WHERE o.is_active = TRUE"
        params = []
        if plan_filter != "all":
            where += " AND COALESCE(sp.name, 'free') = %s"
            params.append(plan_filter)

        if operation == "add":
            sql = f"""
                UPDATE organizations SET {column} = {column} + %s
                WHERE id IN (
                    SELECT o.id FROM organizations o
                    LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
                    {where}
                )
            """
            cur.execute(sql, [int(amount)] + params)
        else:  # set
            sql = f"""
                UPDATE organizations SET {column} = %s
                WHERE id IN (
                    SELECT o.id FROM organizations o
                    LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
                    {where}
                )
            """
            cur.execute(sql, [int(amount)] + params)

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

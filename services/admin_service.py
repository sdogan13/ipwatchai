"""Service helpers for superadmin dashboard and settings flows."""

import csv
import io
import json
import logging

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from database.crud import Database
from utils.settings_manager import settings_manager

logger = logging.getLogger(__name__)


def _write_admin_audit_log(db: Database, user_id: str, action: str, details: dict):
    """Persist an admin audit-log entry."""
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO audit_log (user_id, action, resource_type, metadata)
        VALUES (%s, %s, %s, %s)
        """,
        (user_id, action, "admin", json.dumps(details, ensure_ascii=False, default=str)),
    )


ADMIN_PLAN_FEATURE_CATEGORIES = {
    "pricing": ["price_monthly", "price_annual_monthly"],
    "search_limits": ["max_daily_quick_searches", "monthly_live_searches"],
    "content_limits": [
        "max_watchlist_items",
        "max_users",
        "monthly_reports",
        "monthly_applications",
        "monthly_ai_credits",
        "name_suggestions_per_session",
        "daily_lead_views",
        "auto_scan_max_items",
    ],
    "boolean_flags": [
        "can_use_live_scraping",
        "can_track_logos",
        "can_view_holder_portfolio",
        "can_export_reports",
        "can_export_csv_leads",
        "priority_support",
        "api_access",
        "dedicated_account_manager",
    ],
    "other": ["auto_scan_frequency"],
}


async def get_all_admin_settings_data(*, settings_getter=None):
    """Return all runtime settings for the superadmin UI."""
    getter = settings_getter or settings_manager.get_all
    return getter()


async def get_admin_settings_category_data(category: str, *, category_getter=None):
    """Return a specific settings category for the superadmin UI."""
    getter = category_getter or settings_manager.get_category
    return getter(category)


async def update_admin_setting_data(
    *,
    key: str,
    payload: dict,
    current_user,
    db_factory=Database,
    settings_setter=None,
    audit_logger=None,
):
    """Update a runtime setting and write the matching admin audit entry."""
    value = payload.get("value")
    if value is None:
        raise HTTPException(status_code=400, detail="'value' is required")

    setter = settings_setter or settings_manager.set
    logger = audit_logger or _write_admin_audit_log

    with db_factory() as db:
        conn = db.conn
        setter(
            key=key,
            value=value,
            category=payload.get("category", "general"),
            description=payload.get("description"),
            value_type=payload.get("value_type", "string"),
            updated_by=str(current_user.id),
            conn=conn,
        )
        logger(
            db,
            str(current_user.id),
            "setting_changed",
            {"key": key, "new_value": value},
        )
        db.commit()

    return {"status": "ok", "key": key, "value": value}


async def delete_admin_setting_data(
    *,
    key: str,
    current_user,
    db_factory=Database,
    settings_deleter=None,
    audit_logger=None,
):
    """Delete a runtime setting override and write the matching admin audit entry."""
    deleter = settings_deleter or settings_manager.delete
    logger = audit_logger or _write_admin_audit_log

    with db_factory() as db:
        conn = db.conn
        deleter(key, conn=conn)
        logger(
            db,
            str(current_user.id),
            "setting_deleted",
            {"key": key},
        )
        db.commit()

    return {"status": "ok", "key": key, "reverted_to": "code_default"}


async def get_admin_overview_data(*, db_factory=Database):
    """Aggregate overview dashboard metrics for the superadmin panel."""
    stats = {}

    with db_factory() as db:
        cur = db.cursor()

        cur.execute("SELECT COUNT(*) as cnt FROM users WHERE is_active = TRUE")
        stats["total_active_users"] = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) as cnt FROM organizations WHERE is_active = TRUE")
        stats["total_active_orgs"] = cur.fetchone()["cnt"]

        cur.execute(
            """
            SELECT COALESCE(sp.name, 'free') as plan, COUNT(DISTINCT o.id) as org_count
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.is_active = TRUE
            GROUP BY sp.name
            """
        )
        stats["orgs_by_plan"] = {row["plan"]: row["org_count"] for row in cur.fetchall()}

        cur.execute("SELECT COUNT(*) as cnt FROM trademarks")
        stats["total_trademarks"] = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) as cnt FROM watchlist_mt WHERE is_active = TRUE")
        stats["total_watchlist_items"] = cur.fetchone()["cnt"]

        cur.execute(
            """
            SELECT COUNT(*) as cnt FROM users
            WHERE created_at >= NOW() - INTERVAL '7 days'
            """
        )
        stats["new_users_7d"] = cur.fetchone()["cnt"]

        cur.execute(
            "SELECT COUNT(*) as cnt FROM alerts_mt WHERE status NOT IN ('resolved', 'dismissed')"
        )
        stats["total_alerts"] = cur.fetchone()["cnt"]

        cur.execute(
            """
            SELECT COALESCE(SUM(quick_searches), 0) + COALESCE(SUM(live_searches), 0) as cnt
            FROM api_usage WHERE usage_date = CURRENT_DATE
            """
        )
        stats["api_calls_today"] = cur.fetchone()["cnt"]

        cur.execute(
            """
            SELECT COALESCE(sp.name, 'free') as plan_name,
                   COALESCE(sp.price_monthly, 0) as price,
                   COUNT(DISTINCT o.id) as org_count
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.is_active = TRUE
            GROUP BY sp.name, sp.price_monthly
            """
        )
        revenue_rows = cur.fetchall()
        mrr = sum(row["price"] * row["org_count"] for row in revenue_rows if row["price"])
        stats["mrr"] = float(mrr)
        stats["revenue_by_plan"] = {
            row["plan_name"]: {
                "price": float(row["price"] or 0),
                "orgs": row["org_count"],
                "revenue": float((row["price"] or 0) * row["org_count"]),
            }
            for row in revenue_rows
        }

        cur.execute(
            """
            SELECT COUNT(*) as cnt FROM audit_log
            WHERE action = 'plan_changed'
            AND created_at >= NOW() - INTERVAL '7 days'
            """
        )
        stats["plan_changes_7d"] = cur.fetchone()["cnt"]

        cur.execute(
            """
            SELECT COUNT(*) as cnt FROM trademark_applications_mt
            WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE)
            """
        )
        stats["applications_this_month"] = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) as cnt FROM app_settings WHERE category = 'plan_limits'")
        stats["active_overrides"] = cur.fetchone()["cnt"]

    return stats


async def get_admin_plans_data(
    *,
    db_factory=Database,
    settings_category_getter=None,
    plan_feature_defaults=None,
):
    """Return subscription-plan rows with code defaults, overrides, and organization counts."""
    getter = settings_category_getter or settings_manager.get_category
    if plan_feature_defaults is None:
        from utils.subscription import PLAN_FEATURES

        plan_feature_defaults = PLAN_FEATURES

    with db_factory() as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM subscription_plans ORDER BY price_monthly ASC NULLS FIRST")
        db_plans = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT COALESCE(sp.name, 'free') as plan_name, COUNT(*) as org_count
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.is_active = TRUE
            GROUP BY sp.name
            """
        )
        org_counts = {row["plan_name"]: row["org_count"] for row in cur.fetchall()}

    overrides = getter("plan_limits") or {}
    result = []
    for db_plan in db_plans:
        plan_name = db_plan["name"]
        plan_overrides = {
            key.replace(f"plan.{plan_name}.", ""): value["value"]
            for key, value in overrides.items()
            if key.startswith(f"plan.{plan_name}.")
        }
        result.append(
            {
                "db_record": db_plan,
                "code_defaults": plan_feature_defaults.get(plan_name, {}),
                "active_overrides": plan_overrides,
                "active_orgs": org_counts.get(plan_name, 0),
            }
        )

    return {
        "plans": result,
        "feature_categories": ADMIN_PLAN_FEATURE_CATEGORIES,
    }


async def update_admin_plan_pricing_data(
    *,
    plan_name: str,
    payload: dict,
    current_user,
    db_factory=Database,
    audit_logger=None,
):
    """Update plan pricing metadata for the superadmin pricing panel."""
    allowed = {"price_monthly", "price_annual_monthly", "display_name", "description", "is_active"}
    updates = {key: value for key, value in payload.items() if key in allowed}

    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    from psycopg2 import sql as psql

    logger = audit_logger or _write_admin_audit_log

    with db_factory() as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM subscription_plans WHERE name = %s", (plan_name,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"Plan '{plan_name}' not found")

        set_parts = [psql.SQL("{} = %s").format(psql.Identifier(key)) for key in updates]
        set_clause = psql.SQL(", ").join(set_parts)
        values = list(updates.values()) + [plan_name]

        cur.execute(
            psql.SQL("UPDATE subscription_plans SET {} WHERE name = %s").format(set_clause),
            values,
        )

        logger(
            db,
            str(current_user.id),
            "plan_pricing_updated",
            {
                "plan": plan_name,
                "changes": {key: str(value) for key, value in updates.items()},
            },
        )
        db.commit()

    return {"status": "ok", "plan": plan_name}


async def refund_admin_payment_data(
    *,
    payment_id: str,
    payload: dict,
    current_user,
    db_factory=Database,
    audit_logger=None,
    iyzico_options_getter=None,
    subscription_activator=None,
    refund_client_factory=None,
    gateway_logger=None,
):
    """Refund a completed payment and persist the gateway response."""
    refund_amount = payload.get("amount")  # None = full refund
    reason = payload.get("reason", "")

    audit = audit_logger or _write_admin_audit_log
    service_logger = gateway_logger or logger

    if (
        iyzico_options_getter is None
        or subscription_activator is None
        or refund_client_factory is None
    ):
        import iyzipay
        from services.payment_service import _activate_subscription, _get_iyzico_options

        iyzico_options_getter = iyzico_options_getter or _get_iyzico_options
        subscription_activator = subscription_activator or _activate_subscription
        refund_client_factory = refund_client_factory or iyzipay.Refund

    with db_factory() as db:
        cur = db.cursor()

        cur.execute("SELECT * FROM payments WHERE id = %s", (payment_id,))
        payment = cur.fetchone()
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")

        if payment["status"] != "completed":
            raise HTTPException(status_code=400, detail="Only completed payments can be refunded")

        if payment.get("refund_status") in ("full",):
            raise HTTPException(status_code=400, detail="Payment already fully refunded")

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

        raw_response = payment.get("iyzico_raw_response") or {}
        if isinstance(raw_response, str):
            raw_response = json.loads(raw_response)

        item_transactions = raw_response.get("itemTransactions", [])
        if not item_transactions:
            raise HTTPException(
                status_code=400,
                detail="No transaction ID found in iyzico response - cannot refund",
            )
        payment_transaction_id = item_transactions[0].get("paymentTransactionId", "")
        if not payment_transaction_id:
            raise HTTPException(
                status_code=400,
                detail="paymentTransactionId missing in iyzico response",
            )

        refund_request = {
            "locale": "tr",
            "conversationId": payment.get("iyzico_conversation_id", ""),
            "paymentTransactionId": payment_transaction_id,
            "price": f"{refund_amount:.2f}",
            "currency": payment.get("currency", "TRY"),
            "ip": "127.0.0.1",
        }

        try:
            refund_result = refund_client_factory().create(
                refund_request,
                iyzico_options_getter(),
            )
            result_json = json.loads(refund_result.read().decode("utf-8"))
        except Exception as exc:
            service_logger.error(f"iyzico refund API call failed: {exc}")
            raise HTTPException(status_code=502, detail="Refund gateway error")

        if result_json.get("status") != "success":
            error_msg = result_json.get("errorMessage", "Unknown error")
            service_logger.error(f"iyzico refund failed: {error_msg}")
            raise HTTPException(status_code=502, detail=f"Refund failed: {error_msg}")

        cur.execute(
            """
            UPDATE payments
            SET refund_status = %s,
                refund_amount = %s,
                refunded_at = CURRENT_TIMESTAMP,
                refund_reason = %s,
                iyzico_refund_response = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                refund_type,
                refund_amount,
                reason,
                json.dumps(result_json),
                payment_id,
            ),
        )

        org_id = str(payment["organization_id"])
        if refund_type == "full":
            subscription_activator(db, org_id, "free", "monthly")

        audit(
            db,
            str(current_user.id),
            "payment_refunded",
            {
                "payment_id": payment_id,
                "organization_id": org_id,
                "refund_type": refund_type,
                "refund_amount": refund_amount,
                "original_amount": paid_amount,
                "reason": reason,
            },
        )
        db.commit()

    service_logger.info(
        f"Payment {payment_id} refunded ({refund_type}): "
        f"{refund_amount} {payment.get('currency', 'TRY')} by {current_user.id}"
    )
    return {
        "status": "ok",
        "payment_id": payment_id,
        "refund_type": refund_type,
        "refund_amount": refund_amount,
    }


async def get_admin_organizations_data(
    *,
    search=None,
    plan=None,
    is_active=None,
    limit=50,
    offset=0,
    db_factory=Database,
):
    """Return a filtered, paginated organization list for the superadmin panel."""
    with db_factory() as db:
        cur = db.cursor()

        query = """
            SELECT o.id, o.name, o.slug,
                   (
                       SELECT u.email
                       FROM users u
                       WHERE u.organization_id = o.id AND u.is_active = TRUE
                       ORDER BY COALESCE(u.is_superadmin, FALSE) DESC, u.created_at ASC
                       LIMIT 1
                   ) as email,
                   o.is_active, o.created_at,
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
            query += """
                AND (
                    o.name ILIKE %s
                    OR o.slug ILIKE %s
                    OR EXISTS (
                        SELECT 1
                        FROM users u
                        WHERE u.organization_id = o.id AND u.email ILIKE %s
                    )
                )
            """
            search_term = f"%{search}%"
            params.extend([search_term, search_term, search_term])
        if plan:
            query += " AND COALESCE(sp.name, 'free') = %s"
            params.append(plan)
        if is_active is not None:
            query += " AND o.is_active = %s"
            params.append(is_active)

        count_sql = """
            SELECT COUNT(*) as cnt
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE 1=1
        """
        count_params = []
        if search:
            count_sql += """
                AND (
                    o.name ILIKE %s
                    OR o.slug ILIKE %s
                    OR EXISTS (
                        SELECT 1
                        FROM users u
                        WHERE u.organization_id = o.id AND u.email ILIKE %s
                    )
                )
            """
            search_term = f"%{search}%"
            count_params.extend([search_term, search_term, search_term])
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
        organizations = [dict(row) for row in cur.fetchall()]

    return {
        "organizations": organizations,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def get_admin_organization_detail_data(*, org_id: str, db_factory=Database):
    """Return a single organization detail view, including users."""
    with db_factory() as db:
        cur = db.cursor()

        cur.execute(
            """
            SELECT o.*, COALESCE(sp.name, 'free') as plan_name, sp.price_monthly
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.id = %s
            """,
            (org_id,),
        )
        organization = cur.fetchone()
        if not organization:
            raise HTTPException(status_code=404, detail="Organization not found")

        organization_dict = dict(organization)

        cur.execute(
            """
            SELECT id, email, first_name, last_name, role, is_active,
                   COALESCE(is_superadmin, FALSE) as is_superadmin,
                   last_login_at, created_at
            FROM users WHERE organization_id = %s ORDER BY created_at
            """,
            (org_id,),
        )
        organization_dict["users"] = [dict(row) for row in cur.fetchall()]

    return organization_dict


async def toggle_admin_organization_status_data(
    *,
    org_id: str,
    payload: dict,
    current_user,
    db_factory=Database,
    audit_logger=None,
):
    """Activate or deactivate an organization as superadmin."""
    is_active = payload.get("is_active")
    if is_active is None:
        raise HTTPException(status_code=400, detail="'is_active' is required")

    logger = audit_logger or _write_admin_audit_log

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE organizations SET is_active = %s WHERE id = %s RETURNING id",
            (is_active, org_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Organization not found")

        logger(
            db,
            str(current_user.id),
            "org_status_changed",
            {"organization_id": org_id, "is_active": is_active},
        )
        db.commit()

    return {"status": "ok", "organization_id": org_id, "is_active": is_active}


async def change_admin_organization_plan_data(
    *,
    org_id: str,
    payload: dict,
    current_user,
    db_factory=Database,
    audit_logger=None,
):
    """Change an organization's subscription plan as superadmin."""
    plan_name = payload.get("plan_name")
    if not plan_name:
        raise HTTPException(status_code=400, detail="'plan_name' is required")

    logger = audit_logger or _write_admin_audit_log

    with db_factory() as db:
        cur = db.cursor()

        cur.execute(
            "SELECT id, name FROM subscription_plans WHERE name = %s AND is_active = TRUE",
            (plan_name,),
        )
        plan = cur.fetchone()
        if not plan:
            raise HTTPException(status_code=404, detail=f"Plan '{plan_name}' not found")

        cur.execute(
            """
            SELECT COALESCE(sp.name, 'free') as old_plan
            FROM organizations o LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.id = %s
            """,
            (org_id,),
        )
        old = cur.fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="Organization not found")
        old_plan = old["old_plan"]

        cur.execute(
            "UPDATE organizations SET subscription_plan_id = %s WHERE id = %s",
            (str(plan["id"]), org_id),
        )

        logger(
            db,
            str(current_user.id),
            "plan_changed",
            {
                "organization_id": org_id,
                "old_plan": old_plan,
                "new_plan": plan_name,
            },
        )
        db.commit()

    return {
        "status": "ok",
        "organization_id": org_id,
        "old_plan": old_plan,
        "new_plan": plan_name,
    }


async def get_admin_users_data(
    *,
    search=None,
    org_id=None,
    role=None,
    is_active=None,
    limit=50,
    offset=0,
    db_factory=Database,
):
    """Return a filtered, paginated cross-organization user list for the superadmin panel."""
    with db_factory() as db:
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
            search_term = f"%{search}%"
            params.extend([search_term, search_term, search_term])
        if org_id:
            query += " AND u.organization_id = %s"
            params.append(org_id)
        if role:
            query += " AND u.role = %s"
            params.append(role)
        if is_active is not None:
            query += " AND u.is_active = %s"
            params.append(is_active)

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
            search_term = f"%{search}%"
            count_params.extend([search_term, search_term, search_term])
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

    return {
        "users": users,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def change_admin_user_role_data(
    *,
    user_id: str,
    payload: dict,
    current_user,
    db_factory=Database,
    audit_logger=None,
):
    """Change an organization-scoped role for any user as superadmin."""
    new_role = payload.get("role")
    valid_roles = ["admin", "user", "viewer"]
    if new_role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {valid_roles}")

    logger = audit_logger or _write_admin_audit_log

    with db_factory() as db:
        cur = db.cursor()

        cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
        old = cur.fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="User not found")

        cur.execute("UPDATE users SET role = %s WHERE id = %s", (new_role, user_id))

        logger(
            db,
            str(current_user.id),
            "user_role_changed",
            {
                "target_user_id": user_id,
                "old_role": old["role"],
                "new_role": new_role,
            },
        )
        db.commit()

    return {"status": "ok", "user_id": user_id, "old_role": old["role"], "new_role": new_role}


async def toggle_admin_superadmin_data(
    *,
    user_id: str,
    payload: dict,
    current_user,
    db_factory=Database,
    audit_logger=None,
):
    """Grant or revoke superadmin for a target user."""
    is_superadmin = payload.get("is_superadmin")
    if is_superadmin is None:
        raise HTTPException(status_code=400, detail="'is_superadmin' is required")

    if user_id == str(current_user.id) and not is_superadmin:
        raise HTTPException(status_code=400, detail="Cannot revoke your own superadmin status")

    logger = audit_logger or _write_admin_audit_log

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE users SET is_superadmin = %s WHERE id = %s RETURNING id",
            (is_superadmin, user_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="User not found")

        logger(
            db,
            str(current_user.id),
            "superadmin_toggled",
            {
                "target_user_id": user_id,
                "is_superadmin": is_superadmin,
            },
        )
        db.commit()

    return {"status": "ok", "user_id": user_id, "is_superadmin": is_superadmin}


async def toggle_admin_user_status_data(
    *,
    user_id: str,
    payload: dict,
    current_user,
    db_factory=Database,
    audit_logger=None,
):
    """Activate or deactivate a target user as superadmin."""
    is_active = payload.get("is_active")
    if is_active is None:
        raise HTTPException(status_code=400, detail="'is_active' is required")

    if user_id == str(current_user.id) and not is_active:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    logger = audit_logger or _write_admin_audit_log

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE users SET is_active = %s WHERE id = %s RETURNING id",
            (is_active, user_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="User not found")

        logger(
            db,
            str(current_user.id),
            "user_status_changed",
            {
                "target_user_id": user_id,
                "is_active": is_active,
            },
        )
        db.commit()

    return {"status": "ok", "user_id": user_id, "is_active": is_active}


async def get_admin_audit_log_data(
    *,
    action=None,
    user_id=None,
    limit=100,
    offset=0,
    db_factory=Database,
):
    """Return filtered audit-log entries for the superadmin panel."""
    with db_factory() as db:
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


async def get_admin_org_credits_data(
    *,
    org_id: str,
    db_factory=Database,
    plan_limit_getter=None,
):
    """Return organization credit balances for the superadmin panel."""
    if plan_limit_getter is None:
        from utils.subscription import get_plan_limit as plan_limit_getter

    with db_factory() as db:
        cur = db.cursor()

        cur.execute(
            """
            SELECT o.logo_credits_monthly, o.logo_credits_purchased, o.name_credits_purchased,
                   o.logo_credits_reset_at,
                   COALESCE(o.ai_credits_monthly, 0) as ai_credits_monthly,
                   COALESCE(o.ai_credits_purchased, 0) as ai_credits_purchased,
                   o.ai_credits_reset_at,
                   COALESCE(sp.name, 'free') as plan_name
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.id = %s
            """,
            (org_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Organization not found")

        plan_name = row["plan_name"]

        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE feature_type = 'LOGO') as logo_generations_this_month,
                COUNT(*) FILTER (WHERE feature_type = 'NAME') as name_generations_this_month
            FROM generation_logs
            WHERE org_id = %s
            AND created_at >= DATE_TRUNC('month', CURRENT_DATE)
            """,
            (org_id,),
        )
        usage = cur.fetchone()

    ai_plan_limit = plan_limit_getter(plan_name, "monthly_ai_credits")

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


async def adjust_admin_org_credits_data(
    *,
    org_id: str,
    payload: dict,
    current_user,
    db_factory=Database,
    audit_logger=None,
):
    """Adjust credit balances for an organization as superadmin."""
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

    from psycopg2 import sql as psql

    logger = audit_logger or _write_admin_audit_log
    column = valid_types[credit_type]

    with db_factory() as db:
        cur = db.cursor()

        cur.execute(
            psql.SQL("SELECT {} FROM organizations WHERE id = %s").format(psql.Identifier(column)),
            (org_id,),
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
                (new_value, org_id),
            )
        elif operation == "add":
            new_value = old_value + int(amount)
            cur.execute(
                psql.SQL("UPDATE organizations SET {} = {} + %s WHERE id = %s").format(col_id, col_id),
                (int(amount), org_id),
            )
        else:
            new_value = max(0, old_value - int(amount))
            cur.execute(
                psql.SQL("UPDATE organizations SET {} = GREATEST(0, {} - %s) WHERE id = %s").format(col_id, col_id),
                (int(amount), org_id),
            )

        logger(
            db,
            str(current_user.id),
            "credit_adjustment",
            {
                "organization_id": org_id,
                "credit_type": credit_type,
                "operation": operation,
                "amount": amount,
                "old_value": old_value,
                "new_value": new_value,
                "reason": reason,
            },
        )
        db.commit()

    return {
        "status": "ok",
        "organization_id": org_id,
        "credit_type": credit_type,
        "old_value": old_value,
        "new_value": new_value,
    }


async def bulk_adjust_admin_credits_data(
    *,
    payload: dict,
    current_user,
    db_factory=Database,
    audit_logger=None,
):
    """Apply a bulk credit adjustment across active organizations for the superadmin panel."""
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

    from psycopg2 import sql as psql

    logger = audit_logger or _write_admin_audit_log
    column = valid_types[credit_type]
    col_id = psql.Identifier(column)

    with db_factory() as db:
        cur = db.cursor()

        where_parts = [psql.SQL("WHERE o.is_active = TRUE")]
        params = []
        if plan_filter != "all":
            where_parts.append(psql.SQL("AND COALESCE(sp.name, 'free') = %s"))
            params.append(plan_filter)
        where_clause = psql.SQL(" ").join(where_parts)

        subquery = psql.SQL(
            """
            SELECT o.id FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            {where}
            """
        ).format(where=where_clause)

        amount_int = int(amount)
        if operation == "add":
            query = psql.SQL(
                "UPDATE organizations SET {col} = {col} + %s WHERE id IN ({sub})"
            ).format(col=col_id, sub=subquery)
            cur.execute(query, [amount_int] + params)
        else:
            query = psql.SQL(
                "UPDATE organizations SET {col} = %s WHERE id IN ({sub})"
            ).format(col=col_id, sub=subquery)
            cur.execute(query, [amount_int] + params)

        affected = cur.rowcount

        logger(
            db,
            str(current_user.id),
            "bulk_credit_adjustment",
            {
                "plan_filter": plan_filter,
                "credit_type": credit_type,
                "operation": operation,
                "amount": amount,
                "affected_orgs": affected,
                "reason": reason,
            },
        )
        db.commit()

    return {"status": "ok", "affected_organizations": affected}


async def get_admin_discount_codes_data(
    *,
    is_active=None,
    db_factory=Database,
):
    """Return discount codes for the superadmin panel with optional active-state filtering."""
    with db_factory() as db:
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


async def create_admin_discount_code_data(
    *,
    payload: dict,
    current_user,
    db_factory=Database,
    audit_logger=None,
):
    """Create a discount code for the superadmin panel."""
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

    logger = audit_logger or _write_admin_audit_log

    with db_factory() as db:
        cur = db.cursor()

        cur.execute("SELECT id FROM discount_codes WHERE code = %s", (code,))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail=f"Code '{code}' already exists")

        cur.execute(
            """
            INSERT INTO discount_codes
                (code, description, discount_type, discount_value,
                 applies_to_plan, max_uses, valid_from, valid_until, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                code,
                payload.get("description"),
                discount_type,
                discount_value,
                payload.get("applies_to_plan"),
                payload.get("max_uses"),
                payload.get("valid_from"),
                payload.get("valid_until"),
                str(current_user.id),
            ),
        )

        logger(
            db,
            str(current_user.id),
            "discount_code_created",
            {
                "code": code,
                "discount_type": discount_type,
                "discount_value": float(discount_value),
            },
        )
        db.commit()

    return {"status": "ok", "code": code}


async def update_admin_discount_code_data(
    *,
    code_id: str,
    payload: dict,
    current_user,
    db_factory=Database,
    audit_logger=None,
):
    """Update a discount code for the superadmin panel."""
    allowed_fields = {"description", "max_uses", "valid_until", "is_active", "discount_value"}
    updates = {k: v for k, v in payload.items() if k in allowed_fields}

    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    from psycopg2 import sql as psql

    logger = audit_logger or _write_admin_audit_log

    with db_factory() as db:
        cur = db.cursor()

        set_parts = [psql.SQL("{} = %s").format(psql.Identifier(k)) for k in updates]
        set_parts.append(psql.SQL("updated_at = NOW()"))
        set_clause = psql.SQL(", ").join(set_parts)
        values = list(updates.values()) + [code_id]

        cur.execute(
            psql.SQL("UPDATE discount_codes SET {} WHERE id = %s").format(set_clause),
            values,
        )

        logger(
            db,
            str(current_user.id),
            "discount_code_updated",
            {
                "code_id": code_id,
                "changes": {k: str(v) for k, v in updates.items()},
            },
        )
        db.commit()

    return {"status": "ok", "code_id": code_id}


async def deactivate_admin_discount_code_data(
    *,
    code_id: str,
    current_user,
    db_factory=Database,
    audit_logger=None,
):
    """Deactivate a discount code for the superadmin panel."""
    logger = audit_logger or _write_admin_audit_log

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE discount_codes SET is_active = FALSE, updated_at = NOW() WHERE id = %s",
            (code_id,),
        )
        logger(
            db,
            str(current_user.id),
            "discount_code_deactivated",
            {"code_id": code_id},
        )
        db.commit()

    return {"status": "ok", "code_id": code_id}


async def get_admin_discount_code_usage_data(
    *,
    code_id: str,
    db_factory=Database,
):
    """Return usage history for a discount code in the superadmin panel."""
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT dcu.*, o.name as org_name,
                   (
                       SELECT u.email
                       FROM users u
                       WHERE u.organization_id = o.id AND u.is_active = TRUE
                       ORDER BY COALESCE(u.is_superadmin, FALSE) DESC, u.created_at ASC
                       LIMIT 1
                   ) as org_email
            FROM discount_code_usage dcu
            JOIN organizations o ON dcu.organization_id = o.id
            WHERE dcu.discount_code_id = %s
            ORDER BY dcu.applied_at DESC
            """,
            (code_id,),
        )
        usage = [dict(row) for row in cur.fetchall()]

    return {"usage": usage, "total_uses": len(usage)}


async def get_admin_usage_analytics_data(
    *,
    days: int = 30,
    db_factory=Database,
):
    """Return usage analytics aggregates for the superadmin panel."""
    with db_factory() as db:
        cur = db.cursor()

        cur.execute(
            """
            SELECT usage_date as date,
                   COUNT(DISTINCT user_id) as unique_users,
                   COALESCE(SUM(quick_searches), 0) as quick_searches,
                   COALESCE(SUM(live_searches), 0) as live_searches,
                   COALESCE(SUM(name_generations), 0) as name_generations
            FROM api_usage
            WHERE usage_date >= CURRENT_DATE - %s * INTERVAL '1 day'
            GROUP BY usage_date
            ORDER BY usage_date DESC
            """,
            (days,),
        )
        daily = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT COALESCE(sp.name, 'free') as plan,
                   COALESCE(SUM(au.quick_searches), 0) + COALESCE(SUM(au.live_searches), 0) as total_searches
            FROM api_usage au
            JOIN users u ON au.user_id = u.id
            JOIN organizations o ON u.organization_id = o.id
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE au.usage_date >= CURRENT_DATE - %s * INTERVAL '1 day'
            GROUP BY sp.name
            """,
            (days,),
        )
        by_plan = {row["plan"]: row["total_searches"] for row in cur.fetchall()}

        cur.execute(
            """
            SELECT u.email, u.first_name, u.last_name, o.name as org_name,
                   COALESCE(SUM(au.quick_searches), 0) + COALESCE(SUM(au.live_searches), 0) as total_searches
            FROM api_usage au
            JOIN users u ON au.user_id = u.id
            JOIN organizations o ON u.organization_id = o.id
            WHERE au.usage_date >= CURRENT_DATE - %s * INTERVAL '1 day'
            GROUP BY u.id, u.email, u.first_name, u.last_name, o.name
            ORDER BY total_searches DESC
            LIMIT 20
            """,
            (days,),
        )
        top_users = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE feature_type = 'LOGO') as logo_generations,
                COUNT(*) FILTER (WHERE feature_type = 'NAME') as name_generations
            FROM generation_logs
            WHERE created_at >= CURRENT_DATE - %s * INTERVAL '1 day'
            """,
            (days,),
        )
        costs = dict(cur.fetchone()) if cur.rowcount else {}

    return {
        "period_days": days,
        "daily_usage": daily,
        "usage_by_plan": by_plan,
        "top_users": top_users,
        "cost_bearing_actions": costs,
    }


async def build_admin_usage_export_response(
    *,
    days: int = 30,
    db_factory=Database,
):
    """Build the admin usage analytics CSV export response."""
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
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
            """,
            (days,),
        )
        rows = cur.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "date",
            "user_email",
            "org_name",
            "plan",
            "quick_searches",
            "live_searches",
            "name_generations",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["usage_date"],
                row["user_email"],
                row["org_name"],
                row["plan"],
                row["quick_searches"],
                row["live_searches"],
                row["name_generations"],
            ]
        )

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=usage_export_{days}d.csv"},
    )

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
from services.admin_service import (
    adjust_admin_org_credits_data,
    bulk_adjust_admin_credits_data,
    build_admin_usage_export_response,
    change_admin_organization_plan_data,
    change_admin_user_role_data,
    create_admin_discount_code_data,
    deactivate_admin_discount_code_data,
    delete_admin_setting_data,
    get_admin_discount_codes_data,
    get_admin_discount_code_usage_data,
    get_admin_audit_log_data,
    get_admin_org_credits_data,
    get_admin_organization_detail_data,
    get_admin_organizations_data,
    get_admin_plans_data,
    get_admin_usage_analytics_data,
    get_admin_users_data,
    get_admin_overview_data,
    get_admin_settings_category_data,
    get_all_admin_settings_data,
    refund_admin_payment_data,
    toggle_admin_organization_status_data,
    toggle_admin_superadmin_data,
    toggle_admin_user_status_data,
    update_admin_plan_pricing_data,
    update_admin_discount_code_data,
    update_admin_setting_data,
)

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
    return await get_all_admin_settings_data()


@router.get("/settings/{category}")
async def get_settings_by_category(
    category: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Get all settings in a category."""
    return await get_admin_settings_category_data(category)


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
    return await update_admin_setting_data(
        key=key,
        payload=payload,
        current_user=current_user,
    )


@router.delete("/settings/{key:path}")
async def delete_setting(
    key: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Delete a setting (revert to code default)."""
    return await delete_admin_setting_data(
        key=key,
        current_user=current_user,
    )


# ============ OVERVIEW / DASHBOARD DATA ============


@router.get("/overview")
async def admin_overview(current_user: CurrentUser = Depends(require_superadmin())):
    """Dashboard overview stats with revenue metrics."""
    return await get_admin_overview_data()


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
    return await get_admin_organizations_data(
        search=search,
        plan=plan,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )


@router.get("/organizations/{org_id}")
async def get_organization_detail(
    org_id: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Get full organization detail including users and usage."""
    return await get_admin_organization_detail_data(org_id=org_id)


@router.put("/organizations/{org_id}/plan")
async def change_org_plan(
    org_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Change an organization's subscription plan. Body: {"plan_name": "professional"}"""
    return await change_admin_organization_plan_data(
        org_id=org_id,
        payload=payload,
        current_user=current_user,
    )


@router.put("/organizations/{org_id}/status")
async def toggle_org_status(
    org_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Activate or deactivate an organization. Body: {"is_active": true/false}"""
    return await toggle_admin_organization_status_data(
        org_id=org_id,
        payload=payload,
        current_user=current_user,
    )


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
    return await get_admin_users_data(
        search=search,
        org_id=org_id,
        role=role,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )


@router.put("/users/{user_id}/role")
async def change_user_role(
    user_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Change a user's org role. Body: {"role": "admin"}"""
    return await change_admin_user_role_data(
        user_id=user_id,
        payload=payload,
        current_user=current_user,
    )


@router.put("/users/{user_id}/superadmin")
async def toggle_superadmin(
    user_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Grant or revoke superadmin. Body: {"is_superadmin": true/false}"""
    return await toggle_admin_superadmin_data(
        user_id=user_id,
        payload=payload,
        current_user=current_user,
    )


@router.put("/users/{user_id}/status")
async def toggle_user_status(
    user_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Activate or deactivate a user. Body: {"is_active": true/false}"""
    return await toggle_admin_user_status_data(
        user_id=user_id,
        payload=payload,
        current_user=current_user,
    )


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
    return await get_admin_audit_log_data(
        action=action,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )


# ============ CREDIT MANAGEMENT ============


@router.get("/organizations/{org_id}/credits")
async def get_org_credits(
    org_id: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Get current credit balances for an organization."""
    return await get_admin_org_credits_data(org_id=org_id)


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
    return await adjust_admin_org_credits_data(
        org_id=org_id,
        payload=payload,
        current_user=current_user,
    )


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
    return await bulk_adjust_admin_credits_data(
        payload=payload,
        current_user=current_user,
    )


# ============ DISCOUNT CODES ============


@router.get("/discount-codes")
async def list_discount_codes(
    current_user: CurrentUser = Depends(require_superadmin()),
    is_active: bool = Query(None),
):
    """List all discount codes."""
    return await get_admin_discount_codes_data(is_active=is_active)


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
    return await create_admin_discount_code_data(
        payload=payload,
        current_user=current_user,
    )


@router.put("/discount-codes/{code_id}")
async def update_discount_code(
    code_id: str,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Update a discount code (description, max_uses, valid_until, is_active, discount_value)."""
    return await update_admin_discount_code_data(
        code_id=code_id,
        payload=payload,
        current_user=current_user,
    )


@router.delete("/discount-codes/{code_id}")
async def deactivate_discount_code(
    code_id: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Deactivate a discount code (soft delete)."""
    return await deactivate_admin_discount_code_data(
        code_id=code_id,
        current_user=current_user,
    )


@router.get("/discount-codes/{code_id}/usage")
async def get_discount_code_usage(
    code_id: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """View usage history for a discount code."""
    return await get_admin_discount_code_usage_data(code_id=code_id)


# ============ PRICING MANAGEMENT ============


@router.get("/plans")
async def list_plans(current_user: CurrentUser = Depends(require_superadmin())):
    """List all subscription plans with their DB values and code defaults."""
    return await get_admin_plans_data()


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
    return await update_admin_plan_pricing_data(
        plan_name=plan_name,
        payload=payload,
        current_user=current_user,
    )


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
    return await refund_admin_payment_data(
        payment_id=payment_id,
        payload=payload,
        current_user=current_user,
    )

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
    return await get_admin_usage_analytics_data(days=days)


@router.get("/analytics/export")
async def export_usage_csv(
    current_user: CurrentUser = Depends(require_superadmin()),
    days: int = Query(30, ge=1, le=365),
):
    """Export usage data as CSV."""
    return await build_admin_usage_export_response(days=days)

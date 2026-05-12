"""Service helpers for payment flows."""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

import iyzipay
from dateutil.relativedelta import relativedelta
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from config.settings import settings
from database.crud import Database, get_db_connection
from utils.subscription import (
    PLAN_FEATURES,
    add_purchased_ai_credits,
    get_credit_pack,
    get_plan_limit,
)

logger = logging.getLogger(__name__)


def get_client_ip(request: Request) -> str:
    """Extract the real client IP from proxy headers with a safe fallback."""
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()

    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"


def get_iyzico_options(settings_obj=settings):
    """Build iyzico API options from runtime settings."""
    return {
        "api_key": settings_obj.iyzico.api_key,
        "secret_key": settings_obj.iyzico.secret_key,
        "base_url": settings_obj.iyzico.base_url,
    }


def calculate_amount(plan_name: str, billing_period: str, plan_features=None) -> float:
    """Calculate the payment amount from trusted server-side plan data."""
    plans = plan_features or PLAN_FEATURES
    plan = plans.get(plan_name)
    if not plan:
        raise ValueError(f"Unknown plan: {plan_name}")

    if billing_period == "annual":
        monthly = plan.get("price_annual_monthly", 0)
        return round(monthly * 12, 2)

    return float(plan.get("price_monthly", 0))


def get_subscription_plan_id(db, plan_name: str) -> str | None:
    """Look up the UUID of a subscription plan by name."""
    cur = db.cursor()
    cur.execute("SELECT id FROM subscription_plans WHERE name = %s", (plan_name,))
    row = cur.fetchone()
    if row:
        return str(row["id"])
    return None


def activate_subscription(
    db,
    org_id: str,
    plan_name: str,
    billing_period: str,
    *,
    plan_id_lookup=get_subscription_plan_id,
    plan_limit_getter=get_plan_limit,
    now_getter=None,
    gateway_logger=None,
):
    """Set an organization's subscription plan and billing dates."""
    service_logger = gateway_logger or logger
    plan_id = plan_id_lookup(db, plan_name)
    if not plan_id:
        service_logger.warning(
            "No subscription_plans row for '%s', skipping activation",
            plan_name,
        )
        return False

    now = now_getter() if now_getter is not None else datetime.utcnow()
    if plan_name == "free":
        end_date = None
    elif billing_period == "annual":
        end_date = now + relativedelta(years=1)
    else:
        end_date = now + relativedelta(months=1)

    ai_monthly_limit = int(plan_limit_getter(plan_name, "monthly_ai_credits") or 0)

    cur = db.cursor()
    cur.execute(
        """
        UPDATE organizations
        SET subscription_plan_id = %s,
            subscription_start_date = %s,
            subscription_end_date = %s,
            ai_credits_monthly = %s,
            ai_credits_reset_at = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """,
        (plan_id, now, end_date, ai_monthly_limit, now, org_id),
    )
    db.commit()
    return True


def retrieve_iyzico_payment(
    token: str,
    *,
    checkout_form_factory=None,
    options_getter=get_iyzico_options,
    gateway_logger=None,
):
    """Retrieve a payment result from iyzico by token."""
    service_logger = gateway_logger or logger
    checkout_factory = checkout_form_factory or iyzipay.CheckoutForm

    try:
        checkout_form = checkout_factory().retrieve(
            {"locale": "tr", "token": token},
            options_getter(),
        )
        result = checkout_form.read().decode("utf-8")
        return json.loads(result)
    except Exception as exc:
        service_logger.error("iyzico retrieve failed: %s", exc)
        return None


def apply_credit_pack_purchase(
    db,
    payment: dict,
    *,
    credit_adder=add_purchased_ai_credits,
    pack_getter=get_credit_pack,
):
    """Credit purchased AI credits to the organization after a successful pack payment.

    Idempotent: relies on the caller to gate by payment status so this function
    is only invoked once per completed payment row.
    """
    pack = pack_getter(payment.get("pack_id"))
    credits = int(payment.get("credits_amount") or 0)
    if pack is None and credits <= 0:
        logger.error(
            "Credit pack purchase missing pack_id and credits_amount for payment %s",
            payment.get("id"),
        )
        return False

    # Trust the pack definition over the stored amount when both exist.
    if pack is not None:
        credits = pack["credits"]

    return credit_adder(db, str(payment["organization_id"]), credits)


def _record_discount_usage(db, payment: dict) -> None:
    """Record discount code usage on the discount_codes + discount_code_usage tables."""
    code = (payment.get("discount_code") or "").strip().upper()
    if not code:
        return
    cur = db.cursor()
    cur.execute(
        "SELECT id, discount_type, discount_value FROM discount_codes WHERE code = %s",
        (code,),
    )
    row = cur.fetchone()
    if not row:
        return
    discount_id = row["id"]
    amount = payment.get("amount") or 0
    cur.execute(
        """
        INSERT INTO discount_code_usage
            (discount_code_id, organization_id, discount_amount, plan_name)
        VALUES (%s, %s, %s, %s)
        """,
        (discount_id, str(payment["organization_id"]), amount, payment.get("plan_name")),
    )
    cur.execute(
        "UPDATE discount_codes SET current_uses = COALESCE(current_uses, 0) + 1 WHERE id = %s",
        (discount_id,),
    )
    db.commit()


def process_payment_result(
    db,
    payment: dict,
    result_json: dict,
    *,
    subscription_activator=activate_subscription,
    credit_pack_applier=apply_credit_pack_purchase,
):
    """Persist a gateway result and activate the subscription / credits when paid."""
    payment_status = result_json.get("paymentStatus", "")
    iyzico_status = result_json.get("status", "")
    payment_id_iyzico = result_json.get("paymentId", "")
    cur = db.cursor()

    if iyzico_status == "success" and payment_status == "SUCCESS":
        cur.execute(
            """
            UPDATE payments
            SET status = 'completed',
                iyzico_payment_id = %s,
                iyzico_raw_response = %s,
                paid_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """,
            (payment_id_iyzico, json.dumps(result_json), str(payment["id"])),
        )
        db.commit()

        kind = (payment.get("kind") or "subscription").strip().lower()
        if kind == "credit_pack":
            credit_pack_applier(db, payment)
        else:
            subscription_activator(
                db,
                str(payment["organization_id"]),
                payment["plan_name"],
                payment["billing_period"],
            )

        _record_discount_usage(db, payment)
        return True

    cur.execute(
        """
        UPDATE payments
        SET status = 'failed',
            iyzico_raw_response = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """,
        (json.dumps(result_json), str(payment["id"])),
    )
    db.commit()
    return False


async def initialize_payment_data(
    *,
    request: Request,
    payload: dict,
    current_user,
    db_factory=Database,
    db_connection_factory=get_db_connection,
    settings_obj=settings,
    plan_features=None,
    amount_calculator=calculate_amount,
    client_ip_getter=get_client_ip,
    options_getter=get_iyzico_options,
    checkout_form_initialize_factory=None,
):
    """Create an iyzico checkout session for a paid plan."""
    plans = plan_features or PLAN_FEATURES
    initializer_factory = checkout_form_initialize_factory or iyzipay.CheckoutFormInitialize

    plan_name = payload.get("plan", "").strip().lower()
    billing_period = payload.get("billing", "monthly").strip().lower()

    if plan_name not in plans or plan_name in ("free", "superadmin"):
        raise HTTPException(status_code=400, detail="Invalid plan for payment")

    if billing_period not in ("monthly", "annual"):
        raise HTTPException(status_code=400, detail="billing must be 'monthly' or 'annual'")

    amount = amount_calculator(plan_name, billing_period, plan_features=plans)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount for this plan")

    conversation_id = str(uuid.uuid4()).replace("-", "")[:20]
    amount_str = f"{amount:.2f}"

    with db_factory(db_connection_factory()) as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT o.tax_id, o.address, o.city, o.country, o.phone,
                   u.phone as user_phone
            FROM organizations o
            LEFT JOIN users u ON u.id = %s
            WHERE o.id = %s
        """,
            (str(current_user.id), str(current_user.organization_id)),
        )
        org_data = cur.fetchone() or {}

        cur.execute(
            """
            INSERT INTO payments (organization_id, user_id, plan_name, billing_period, amount, currency, iyzico_conversation_id, status)
            VALUES (%s, %s, %s, %s, %s, 'TRY', %s, 'pending')
            RETURNING id
        """,
            (
                str(current_user.organization_id),
                str(current_user.id),
                plan_name,
                billing_period,
                amount,
                conversation_id,
            ),
        )
        payment_row = cur.fetchone()
        payment_id = str(payment_row["id"])
        db.commit()

    identity_number = (org_data.get("tax_id") or "11111111111").strip()
    buyer_address = org_data.get("address") or "Turkey"
    buyer_city = org_data.get("city") or "Istanbul"
    buyer_country = org_data.get("country") or "Turkey"
    buyer_phone = org_data.get("phone") or org_data.get("user_phone") or ""

    buyer = {
        "id": str(current_user.id),
        "name": current_user.first_name or "User",
        "surname": current_user.last_name or "User",
        "email": current_user.email,
        "identityNumber": identity_number,
        "registrationAddress": buyer_address,
        "city": buyer_city,
        "country": buyer_country,
        "ip": client_ip_getter(request),
    }
    if buyer_phone:
        buyer["gsmNumber"] = buyer_phone

    contact_name = f"{current_user.first_name or 'User'} {current_user.last_name or 'User'}"
    billing_address = {
        "contactName": contact_name,
        "city": buyer_city,
        "country": buyer_country,
        "address": buyer_address,
    }

    request_data = {
        "locale": "tr",
        "conversationId": conversation_id,
        "price": amount_str,
        "paidPrice": amount_str,
        "currency": "TRY",
        "basketId": payment_id,
        "paymentGroup": "SUBSCRIPTION",
        "callbackUrl": settings_obj.iyzico.callback_url,
        "enabledInstallments": [1],
        "buyer": buyer,
        "shippingAddress": billing_address,
        "billingAddress": billing_address,
        "basketItems": [
            {
                "id": payment_id,
                "name": f"IP Watch AI - {plan_name.title()} Plan ({billing_period})",
                "category1": "Subscription",
                "itemType": "VIRTUAL",
                "price": amount_str,
            }
        ],
    }

    try:
        checkout_form = initializer_factory().create(request_data, options_getter(settings_obj))
        result = checkout_form.read().decode("utf-8")
        result_json = json.loads(result)
    except Exception as exc:
        logger.error("iyzico initialize failed: %s", exc)
        raise HTTPException(status_code=502, detail="Payment gateway error")

    if result_json.get("status") != "success":
        error_msg = result_json.get("errorMessage", "Unknown error")
        logger.error("iyzico initialize error: %s", error_msg)
        raise HTTPException(status_code=502, detail=f"Payment init failed: {error_msg}")

    token = result_json.get("token", "")

    with db_factory(db_connection_factory()) as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE payments SET iyzico_token = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """,
            (token, payment_id),
        )
        db.commit()

    return {
        "checkout_form_content": result_json.get("checkoutFormContent", ""),
        "token": token,
        "conversation_id": conversation_id,
        "payment_id": payment_id,
    }


def _apply_discount(amount: float, discount: Optional[dict]) -> float:
    """Apply a validated discount to a TRY amount. Returns the discounted price (>=1)."""
    if not discount:
        return amount
    dtype = (discount.get("discount_type") or "").strip().lower()
    value = float(discount.get("discount_value") or 0)
    if dtype == "percentage":
        amount = amount * (1 - value / 100.0)
    elif dtype == "fixed":
        amount = amount - value
    return max(round(amount, 2), 1.0)


def _lookup_discount(db, code: str, plan_or_context: Optional[str]) -> Optional[dict]:
    """Look up an active discount code. Returns dict or None.

    Credit-pack discounts use applies_to_plan='credit_pack' or NULL (universal).
    """
    code = (code or "").strip().upper()
    if not code:
        return None
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, code, discount_type, discount_value, applies_to_plan
        FROM discount_codes
        WHERE code = %s
          AND is_active = TRUE
          AND (valid_from IS NULL OR valid_from <= NOW())
          AND (valid_until IS NULL OR valid_until >= NOW())
          AND (max_uses IS NULL OR current_uses < max_uses)
        """,
        (code,),
    )
    row = cur.fetchone()
    if not row:
        return None
    applies = (row.get("applies_to_plan") or "").strip().lower()
    if applies and plan_or_context and applies != plan_or_context:
        return None
    return dict(row)


async def initialize_credit_pack_purchase_data(
    *,
    request: Request,
    payload: dict,
    current_user,
    db_factory=Database,
    db_connection_factory=get_db_connection,
    settings_obj=settings,
    client_ip_getter=get_client_ip,
    options_getter=get_iyzico_options,
    checkout_form_initialize_factory=None,
    pack_getter=get_credit_pack,
):
    """Create an iyzico checkout session for a one-shot AI credit pack."""
    initializer_factory = checkout_form_initialize_factory or iyzipay.CheckoutFormInitialize

    pack = pack_getter(payload.get("pack_id"))
    if pack is None:
        raise HTTPException(status_code=400, detail="Invalid credit pack")

    discount_code = (payload.get("discount_code") or "").strip().upper() or None
    base_amount = float(pack["price_try"])

    with db_factory(db_connection_factory()) as db:
        discount = _lookup_discount(db, discount_code, "credit_pack") if discount_code else None
        if discount_code and discount is None:
            raise HTTPException(status_code=400, detail="Invalid or expired discount code")

        amount = _apply_discount(base_amount, discount)
        conversation_id = str(uuid.uuid4()).replace("-", "")[:20]
        amount_str = f"{amount:.2f}"

        cur = db.cursor()
        cur.execute(
            """
            SELECT o.tax_id, o.address, o.city, o.country, o.phone,
                   u.phone as user_phone
            FROM organizations o
            LEFT JOIN users u ON u.id = %s
            WHERE o.id = %s
            """,
            (str(current_user.id), str(current_user.organization_id)),
        )
        org_data = cur.fetchone() or {}

        cur.execute(
            """
            INSERT INTO payments (
                organization_id, user_id, plan_name, billing_period,
                amount, currency, iyzico_conversation_id, status,
                kind, pack_id, credits_amount, discount_code
            )
            VALUES (%s, %s, NULL, NULL, %s, 'TRY', %s, 'pending',
                    'credit_pack', %s, %s, %s)
            RETURNING id
            """,
            (
                str(current_user.organization_id),
                str(current_user.id),
                amount,
                conversation_id,
                pack["id"],
                pack["credits"],
                discount["code"] if discount else None,
            ),
        )
        payment_row = cur.fetchone()
        payment_id = str(payment_row["id"])
        db.commit()

    identity_number = (org_data.get("tax_id") or "11111111111").strip()
    buyer_address = org_data.get("address") or "Turkey"
    buyer_city = org_data.get("city") or "Istanbul"
    buyer_country = org_data.get("country") or "Turkey"
    buyer_phone = org_data.get("phone") or org_data.get("user_phone") or ""

    buyer = {
        "id": str(current_user.id),
        "name": current_user.first_name or "User",
        "surname": current_user.last_name or "User",
        "email": current_user.email,
        "identityNumber": identity_number,
        "registrationAddress": buyer_address,
        "city": buyer_city,
        "country": buyer_country,
        "ip": client_ip_getter(request),
    }
    if buyer_phone:
        buyer["gsmNumber"] = buyer_phone

    contact_name = f"{current_user.first_name or 'User'} {current_user.last_name or 'User'}"
    billing_address = {
        "contactName": contact_name,
        "city": buyer_city,
        "country": buyer_country,
        "address": buyer_address,
    }

    request_data = {
        "locale": "tr",
        "conversationId": conversation_id,
        "price": amount_str,
        "paidPrice": amount_str,
        "currency": "TRY",
        "basketId": payment_id,
        "paymentGroup": "PRODUCT",
        "callbackUrl": settings_obj.iyzico.callback_url,
        "enabledInstallments": [1],
        "buyer": buyer,
        "shippingAddress": billing_address,
        "billingAddress": billing_address,
        "basketItems": [
            {
                "id": payment_id,
                "name": f"IP Watch AI - {pack['credits']} AI Credits",
                "category1": "AI Credits",
                "itemType": "VIRTUAL",
                "price": amount_str,
            }
        ],
    }

    try:
        checkout_form = initializer_factory().create(request_data, options_getter(settings_obj))
        result = checkout_form.read().decode("utf-8")
        result_json = json.loads(result)
    except Exception as exc:
        logger.error("iyzico credit-pack initialize failed: %s", exc)
        raise HTTPException(status_code=502, detail="Payment gateway error")

    if result_json.get("status") != "success":
        error_msg = result_json.get("errorMessage", "Unknown error")
        logger.error("iyzico credit-pack init error: %s", error_msg)
        raise HTTPException(status_code=502, detail=f"Payment init failed: {error_msg}")

    token = result_json.get("token", "")

    with db_factory(db_connection_factory()) as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE payments SET iyzico_token = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (token, payment_id),
        )
        db.commit()

    return {
        "checkout_form_content": result_json.get("checkoutFormContent", ""),
        "token": token,
        "conversation_id": conversation_id,
        "payment_id": payment_id,
        "pack_id": pack["id"],
        "credits": pack["credits"],
        "amount_try": amount,
    }


async def payment_callback_data(
    *,
    request: Request,
    db_factory=Database,
    db_connection_factory=get_db_connection,
    payment_retriever=retrieve_iyzico_payment,
    payment_processor=process_payment_result,
):
    """Handle iyzico browser redirect callbacks."""
    form = await request.form()
    token = form.get("token", "")

    if not token:
        return RedirectResponse(url="/checkout?error=missing_token", status_code=303)

    with db_factory(db_connection_factory()) as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM payments WHERE iyzico_token = %s", (token,))
        payment = cur.fetchone()

        if not payment:
            logger.error("Payment not found for token: %s...", token[:20])
            return RedirectResponse(url="/checkout?error=payment_not_found", status_code=303)

        kind = (payment.get("kind") or "subscription").strip().lower()

        if payment["status"] == "completed":
            success_url = "/dashboard?credits=success" if kind == "credit_pack" else "/dashboard?payment=success"
            return RedirectResponse(url=success_url, status_code=303)

        plan_name = payment["plan_name"]
        billing_period = payment["billing_period"]

        result_json = payment_retriever(token)
        if result_json is None:
            return RedirectResponse(url="/checkout?error=gateway_error", status_code=303)

        success = payment_processor(db, payment, result_json)
        if success:
            success_url = "/dashboard?credits=success" if kind == "credit_pack" else "/dashboard?payment=success"
            return RedirectResponse(url=success_url, status_code=303)

        if kind == "credit_pack":
            return RedirectResponse(url="/dashboard?credits=failed", status_code=303)

        return RedirectResponse(
            url=f"/checkout?plan={plan_name}&billing={billing_period}&error=payment_failed",
            status_code=303,
        )


async def payment_webhook_data(
    *,
    request: Request,
    db_factory=Database,
    db_connection_factory=get_db_connection,
    payment_retriever=retrieve_iyzico_payment,
    payment_processor=process_payment_result,
):
    """Handle iyzico server-to-server webhook notifications."""
    token = None
    content_type = request.headers.get("content-type", "")

    if "form" in content_type:
        form = await request.form()
        token = form.get("token", "")
    else:
        try:
            body = await request.json()
            token = body.get("token", "")
        except Exception:
            token = None

    if not token:
        logger.warning("Webhook called without token")
        return JSONResponse({"status": "error", "message": "missing token"})

    with db_factory(db_connection_factory()) as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM payments WHERE iyzico_token = %s", (token,))
        payment = cur.fetchone()

        if not payment:
            logger.error("Webhook: payment not found for token: %s...", token[:20])
            return JSONResponse({"status": "error", "message": "payment not found"})

        if payment["status"] == "completed":
            logger.info("Webhook: payment %s already completed, skipping", payment["id"])
            return JSONResponse({"status": "ok", "message": "already processed"})

        result_json = payment_retriever(token)
        if result_json is None:
            logger.error("Webhook: failed to retrieve payment from iyzico for %s", payment["id"])
            return JSONResponse({"status": "error", "message": "iyzico retrieval failed"})

        success = payment_processor(db, payment, result_json)
        if success:
            logger.info("Webhook: payment %s completed successfully", payment["id"])
            return JSONResponse({"status": "ok", "message": "payment activated"})

        logger.warning("Webhook: payment %s failed", payment["id"])
        return JSONResponse({"status": "ok", "message": "payment failed"})


async def activate_free_plan_data(
    *,
    current_user,
    db_factory=Database,
    db_connection_factory=get_db_connection,
    subscription_activator=activate_subscription,
):
    """Activate the free plan for the current organization without payment."""
    with db_factory(db_connection_factory()) as db:
        success = subscription_activator(
            db,
            str(current_user.organization_id),
            "free",
            "monthly",
        )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to activate free plan")

    return {"success": True, "redirect": "/dashboard"}


_get_iyzico_options = get_iyzico_options
_calculate_amount = calculate_amount
_activate_subscription = activate_subscription
_retrieve_iyzico_payment = retrieve_iyzico_payment
_process_payment_result = process_payment_result

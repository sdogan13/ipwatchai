"""Service helpers for payment flows."""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Mapping, Optional

import iyzipay
from dateutil.relativedelta import relativedelta
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from config.settings import settings
from database.crud import Database, get_db_connection
from services.billing_catalog import (
    BillingCatalogError,
    get_billing_catalog,
    get_catalog_pack,
    get_catalog_plan,
    select_billing_region,
    stripe_price_for_pack,
    stripe_price_for_plan,
)
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


def calculate_catalog_amount(plan: Mapping[str, Any], billing_period: str) -> float:
    """Calculate the display amount for a regional catalog plan."""
    if billing_period == "annual":
        return round(float(plan.get("price_annual_monthly") or 0) * 12, 2)
    return float(plan.get("price_monthly") or 0)


def currency_minor_amount(amount: float, currency: str) -> int:
    """Convert a display amount to Stripe minor units."""
    zero_decimal = {"BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"}
    if currency.upper() in zero_decimal:
        return int(round(amount))
    return int(round(amount * 100))


def create_stripe_checkout_session(session_payload: dict, *, settings_obj=settings):
    """Create a Stripe Checkout Session using the configured secret key."""
    if not getattr(settings_obj, "stripe", None) or not settings_obj.stripe.secret_key:
        raise HTTPException(status_code=502, detail="Stripe is not configured")
    try:
        import stripe

        stripe.api_key = settings_obj.stripe.secret_key
        return stripe.checkout.Session.create(**session_payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Stripe checkout session creation failed: %s", exc)
        raise HTTPException(status_code=502, detail="Payment gateway error")


def construct_stripe_event(payload: bytes, signature: str, *, settings_obj=settings):
    """Verify and construct a Stripe webhook event."""
    if not getattr(settings_obj, "stripe", None) or not settings_obj.stripe.webhook_secret:
        raise HTTPException(status_code=500, detail="Stripe webhook secret is not configured")
    try:
        import stripe

        return stripe.Webhook.construct_event(
            payload,
            signature,
            settings_obj.stripe.webhook_secret,
        )
    except Exception as exc:
        logger.warning("Stripe webhook signature verification failed: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature")


def _stripe_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _stripe_metadata(obj: Any) -> Mapping[str, Any]:
    metadata = _stripe_value(obj, "metadata", {}) or {}
    if isinstance(metadata, Mapping):
        return metadata
    return {}


def _jsonable_gateway_object(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _jsonable_gateway_object(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable_gateway_object(item) for item in value]
    if hasattr(value, "to_dict_recursive"):
        return value.to_dict_recursive()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


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


def complete_payment(
    db,
    payment: dict,
    *,
    provider: str,
    provider_payment_id: Optional[str] = None,
    raw_response: Optional[dict] = None,
    subscription_activator=activate_subscription,
    credit_pack_applier=apply_credit_pack_purchase,
) -> bool:
    """Mark a payment completed once, then fulfill its subscription or credits."""
    if payment.get("status") == "completed":
        logger.info("Payment %s already completed, skipping fulfillment", payment.get("id"))
        return True

    raw_json = json.dumps(_jsonable_gateway_object(raw_response or {}))
    cur = db.cursor()
    if provider == "stripe":
        cur.execute(
            """
            UPDATE payments
            SET status = 'completed',
                provider = 'stripe',
                stripe_payment_intent_id = COALESCE(%s, stripe_payment_intent_id),
                stripe_raw_response = %s,
                paid_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status <> 'completed'
        """,
            (provider_payment_id, raw_json, str(payment["id"])),
        )
    else:
        cur.execute(
            """
            UPDATE payments
            SET status = 'completed',
                iyzico_payment_id = %s,
                iyzico_raw_response = %s,
                paid_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status <> 'completed'
        """,
            (provider_payment_id or "", raw_json, str(payment["id"])),
        )
    db.commit()
    if isinstance(getattr(cur, "rowcount", None), int) and cur.rowcount == 0:
        logger.info("Payment %s was already completed by another event", payment.get("id"))
        return True

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


def mark_payment_failed(
    db,
    payment: dict,
    *,
    provider: str,
    raw_response: Optional[dict] = None,
) -> bool:
    """Mark a pending payment failed without fulfilling entitlements."""
    if payment.get("status") == "completed":
        logger.info("Payment %s already completed; failure event ignored", payment.get("id"))
        return False

    raw_json = json.dumps(_jsonable_gateway_object(raw_response or {}))
    cur = db.cursor()
    if provider == "stripe":
        cur.execute(
            """
            UPDATE payments
            SET status = 'failed',
                provider = 'stripe',
                stripe_raw_response = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status <> 'completed'
        """,
            (raw_json, str(payment["id"])),
        )
    else:
        cur.execute(
            """
            UPDATE payments
            SET status = 'failed',
                iyzico_raw_response = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status <> 'completed'
        """,
            (raw_json, str(payment["id"])),
        )
    db.commit()
    return False


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

    if iyzico_status == "success" and payment_status == "SUCCESS":
        return complete_payment(
            db,
            payment,
            provider="iyzico",
            provider_payment_id=payment_id_iyzico,
            raw_response=result_json,
            subscription_activator=subscription_activator,
            credit_pack_applier=credit_pack_applier,
        )

    return mark_payment_failed(
        db,
        payment,
        provider="iyzico",
        raw_response=result_json,
    )


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
    stripe_session_creator=create_stripe_checkout_session,
):
    """Create a regional checkout session for a paid plan."""
    plans = plan_features or PLAN_FEATURES
    initializer_factory = checkout_form_initialize_factory or iyzipay.CheckoutFormInitialize

    plan_name = payload.get("plan", "").strip().lower()
    billing_period = payload.get("billing", "monthly").strip().lower()

    if plan_name not in plans or plan_name in ("free", "superadmin"):
        raise HTTPException(status_code=400, detail="Invalid plan for payment")

    if billing_period not in ("monthly", "annual"):
        raise HTTPException(status_code=400, detail="billing must be 'monthly' or 'annual'")

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

    selected_region = select_billing_region(
        explicit_region=payload.get("region"),
        organization_country=org_data.get("country"),
        headers=request.headers,
    )
    catalog = get_billing_catalog(region=selected_region, include_private=True, settings_obj=settings_obj)
    provider = catalog["provider"]
    currency = catalog["currency"]
    try:
        catalog_plan = get_catalog_plan(catalog, plan_name)
    except BillingCatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    amount = calculate_catalog_amount(catalog_plan, billing_period)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount for this plan")

    if provider == "stripe":
        try:
            stripe_price_id = stripe_price_for_plan(catalog, plan_name, billing_period)
        except BillingCatalogError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        with db_factory(db_connection_factory()) as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO payments (
                    organization_id, user_id, plan_name, billing_period,
                    amount, currency, status, provider, region, billing_country
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', 'stripe', %s, %s)
                RETURNING id
                """,
                (
                    str(current_user.organization_id),
                    str(current_user.id),
                    plan_name,
                    billing_period,
                    amount,
                    currency,
                    selected_region,
                    org_data.get("country"),
                ),
            )
            payment_row = cur.fetchone()
            payment_id = str(payment_row["id"])
            db.commit()

        metadata = {
            "payment_id": payment_id,
            "organization_id": str(current_user.organization_id),
            "user_id": str(current_user.id),
            "kind": "subscription",
            "plan": plan_name,
            "billing": billing_period,
            "region": selected_region,
        }
        stripe_settings = settings_obj.stripe
        session_payload = {
            "mode": "subscription",
            "line_items": [{"price": stripe_price_id, "quantity": 1}],
            "client_reference_id": payment_id,
            "customer_email": current_user.email,
            "success_url": stripe_settings.success_url,
            "cancel_url": stripe_settings.cancel_url,
            "automatic_tax": {"enabled": bool(stripe_settings.automatic_tax)},
            "metadata": metadata,
            "subscription_data": {"metadata": metadata},
        }
        session = stripe_session_creator(session_payload, settings_obj=settings_obj)
        session_id = _stripe_value(session, "id", "")
        checkout_url = _stripe_value(session, "url", "")
        customer_id = _stripe_value(session, "customer")
        subscription_id = _stripe_value(session, "subscription")

        with db_factory(db_connection_factory()) as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE payments
                SET stripe_checkout_session_id = %s,
                    stripe_customer_id = %s,
                    stripe_subscription_id = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (session_id, customer_id, subscription_id, payment_id),
            )
            db.commit()

        return {
            "provider": "stripe",
            "region": selected_region,
            "currency": currency,
            "checkout_url": checkout_url,
            "session_id": session_id,
            "payment_id": payment_id,
        }

    conversation_id = str(uuid.uuid4()).replace("-", "")[:20]
    amount_str = f"{amount:.2f}"

    with db_factory(db_connection_factory()) as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO payments (
                organization_id, user_id, plan_name, billing_period,
                amount, currency, iyzico_conversation_id, status,
                provider, region, billing_country
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending',
                    'iyzico', %s, %s)
            RETURNING id
        """,
            (
                str(current_user.organization_id),
                str(current_user.id),
                plan_name,
                billing_period,
                amount,
                currency,
                conversation_id,
                selected_region,
                org_data.get("country"),
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
        "provider": "iyzico",
        "region": selected_region,
        "currency": currency,
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
    stripe_session_creator=create_stripe_checkout_session,
):
    """Create a regional checkout session for a one-shot AI credit pack."""
    initializer_factory = checkout_form_initialize_factory or iyzipay.CheckoutFormInitialize

    pack = pack_getter(payload.get("pack_id"))
    if pack is None:
        raise HTTPException(status_code=400, detail="Invalid credit pack")

    discount_code = (payload.get("discount_code") or "").strip().upper() or None

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

    selected_region = select_billing_region(
        explicit_region=payload.get("region"),
        organization_country=org_data.get("country"),
        headers=request.headers,
    )
    catalog = get_billing_catalog(region=selected_region, include_private=True, settings_obj=settings_obj)
    provider = catalog["provider"]
    currency = catalog["currency"]
    try:
        catalog_pack = get_catalog_pack(catalog, pack["id"])
    except BillingCatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    base_amount = float(catalog_pack.get("price") or 0)
    if base_amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount for this credit pack")

    with db_factory(db_connection_factory()) as db:
        discount = _lookup_discount(db, discount_code, "credit_pack") if discount_code else None
        if discount_code and discount is None:
            raise HTTPException(status_code=400, detail="Invalid or expired discount code")

        amount = _apply_discount(base_amount, discount)
        conversation_id = str(uuid.uuid4()).replace("-", "")[:20]
        amount_str = f"{amount:.2f}"

        cur = db.cursor()
        if provider == "stripe":
            cur.execute(
                """
                INSERT INTO payments (
                    organization_id, user_id, plan_name, billing_period,
                    amount, currency, status,
                    kind, pack_id, credits_amount, discount_code,
                    provider, region, billing_country
                )
                VALUES (%s, %s, NULL, NULL, %s, %s, 'pending',
                        'credit_pack', %s, %s, %s,
                        'stripe', %s, %s)
                RETURNING id
                """,
                (
                    str(current_user.organization_id),
                    str(current_user.id),
                    amount,
                    currency,
                    pack["id"],
                    pack["credits"],
                    discount["code"] if discount else None,
                    selected_region,
                    org_data.get("country"),
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO payments (
                    organization_id, user_id, plan_name, billing_period,
                    amount, currency, iyzico_conversation_id, status,
                    kind, pack_id, credits_amount, discount_code,
                    provider, region, billing_country
                )
                VALUES (%s, %s, NULL, NULL, %s, %s, %s, 'pending',
                        'credit_pack', %s, %s, %s,
                        'iyzico', %s, %s)
                RETURNING id
                """,
                (
                    str(current_user.organization_id),
                    str(current_user.id),
                    amount,
                    currency,
                    conversation_id,
                    pack["id"],
                    pack["credits"],
                    discount["code"] if discount else None,
                    selected_region,
                    org_data.get("country"),
                ),
            )
        payment_row = cur.fetchone()
        payment_id = str(payment_row["id"])
        db.commit()

    if provider == "stripe":
        metadata = {
            "payment_id": payment_id,
            "organization_id": str(current_user.organization_id),
            "user_id": str(current_user.id),
            "kind": "credit_pack",
            "pack_id": pack["id"],
            "credits": str(pack["credits"]),
            "region": selected_region,
        }
        try:
            line_item = {"price": stripe_price_for_pack(catalog, pack["id"]), "quantity": 1}
        except BillingCatalogError as exc:
            if not discount:
                raise HTTPException(status_code=502, detail=str(exc))
            line_item = {
                "price_data": {
                    "currency": currency.lower(),
                    "unit_amount": currency_minor_amount(amount, currency),
                    "product_data": {"name": f"IP Watch AI - {pack['credits']} AI Credits"},
                },
                "quantity": 1,
            }
        if discount:
            line_item = {
                "price_data": {
                    "currency": currency.lower(),
                    "unit_amount": currency_minor_amount(amount, currency),
                    "product_data": {"name": f"IP Watch AI - {pack['credits']} AI Credits"},
                },
                "quantity": 1,
            }
        stripe_settings = settings_obj.stripe
        session_payload = {
            "mode": "payment",
            "line_items": [line_item],
            "client_reference_id": payment_id,
            "customer_email": current_user.email,
            "success_url": stripe_settings.success_url,
            "cancel_url": stripe_settings.cancel_url,
            "automatic_tax": {"enabled": bool(stripe_settings.automatic_tax)},
            "metadata": metadata,
            "payment_intent_data": {"metadata": metadata},
        }
        session = stripe_session_creator(session_payload, settings_obj=settings_obj)
        session_id = _stripe_value(session, "id", "")
        checkout_url = _stripe_value(session, "url", "")
        customer_id = _stripe_value(session, "customer")
        payment_intent_id = _stripe_value(session, "payment_intent")

        with db_factory(db_connection_factory()) as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE payments
                SET stripe_checkout_session_id = %s,
                    stripe_customer_id = %s,
                    stripe_payment_intent_id = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (session_id, customer_id, payment_intent_id, payment_id),
            )
            db.commit()

        return {
            "provider": "stripe",
            "region": selected_region,
            "currency": currency,
            "checkout_url": checkout_url,
            "session_id": session_id,
            "payment_id": payment_id,
            "pack_id": pack["id"],
            "credits": pack["credits"],
            "amount": amount,
        }

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
        "provider": "iyzico",
        "region": selected_region,
        "currency": currency,
        "token": token,
        "conversation_id": conversation_id,
        "payment_id": payment_id,
        "pack_id": pack["id"],
        "credits": pack["credits"],
        "amount": amount,
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


def _find_payment_for_stripe_object(db, stripe_object: Any) -> Optional[dict]:
    metadata = _stripe_metadata(stripe_object)
    object_id = _stripe_value(stripe_object, "id")
    object_type = _stripe_value(stripe_object, "object", "")
    payment_id = metadata.get("payment_id") or _stripe_value(stripe_object, "client_reference_id")
    session_id = object_id if object_type == "checkout.session" else None
    subscription_id = _stripe_value(stripe_object, "subscription")
    payment_intent_id = _stripe_value(stripe_object, "payment_intent")

    if object_type == "subscription":
        subscription_id = object_id
    elif object_type == "payment_intent":
        payment_intent_id = object_id

    candidates = []
    if payment_id:
        candidates.append(("id", str(payment_id)))
    if session_id:
        candidates.append(("stripe_checkout_session_id", str(session_id)))
    if subscription_id:
        candidates.append(("stripe_subscription_id", str(subscription_id)))
    if payment_intent_id:
        candidates.append(("stripe_payment_intent_id", str(payment_intent_id)))

    cur = db.cursor()
    for column, value in candidates:
        cur.execute(f"SELECT * FROM payments WHERE {column} = %s", (value,))
        row = cur.fetchone()
        if row:
            return row
    return None


def _update_stripe_payment_references(db, payment: dict, stripe_object: Any) -> None:
    object_type = _stripe_value(stripe_object, "object", "")
    session_id = _stripe_value(stripe_object, "id") if object_type == "checkout.session" else None
    customer_id = _stripe_value(stripe_object, "customer")
    subscription_id = _stripe_value(stripe_object, "subscription")
    payment_intent_id = _stripe_value(stripe_object, "payment_intent")

    if object_type == "subscription":
        subscription_id = _stripe_value(stripe_object, "id")
    elif object_type == "payment_intent":
        payment_intent_id = _stripe_value(stripe_object, "id")

    cur = db.cursor()
    cur.execute(
        """
        UPDATE payments
        SET stripe_checkout_session_id = COALESCE(%s, stripe_checkout_session_id),
            stripe_customer_id = COALESCE(%s, stripe_customer_id),
            stripe_subscription_id = COALESCE(%s, stripe_subscription_id),
            stripe_payment_intent_id = COALESCE(%s, stripe_payment_intent_id),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (session_id, customer_id, subscription_id, payment_intent_id, str(payment["id"])),
    )
    db.commit()


def process_stripe_event(
    db,
    event: Any,
    *,
    subscription_activator=activate_subscription,
    credit_pack_applier=apply_credit_pack_purchase,
) -> JSONResponse:
    event_type = _stripe_value(event, "type", "")
    data = _stripe_value(event, "data", {}) or {}
    stripe_object = _stripe_value(data, "object", {}) or {}
    payment = _find_payment_for_stripe_object(db, stripe_object)

    if not payment:
        logger.warning("Stripe webhook %s did not match a payment row", event_type)
        return JSONResponse({"status": "ok", "message": "payment not found"})

    _update_stripe_payment_references(db, payment, stripe_object)
    provider_payment_id = _stripe_value(stripe_object, "payment_intent")
    if _stripe_value(stripe_object, "object", "") == "payment_intent":
        provider_payment_id = _stripe_value(stripe_object, "id")

    success_events = {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "invoice.paid",
    }
    failure_events = {
        "checkout.session.expired",
        "checkout.session.async_payment_failed",
        "invoice.payment_failed",
        "payment_intent.payment_failed",
    }

    if event_type in success_events:
        complete_payment(
            db,
            payment,
            provider="stripe",
            provider_payment_id=provider_payment_id,
            raw_response=_jsonable_gateway_object(event),
            subscription_activator=subscription_activator,
            credit_pack_applier=credit_pack_applier,
        )
        return JSONResponse({"status": "ok", "message": "payment activated"})

    if event_type in failure_events:
        mark_payment_failed(
            db,
            payment,
            provider="stripe",
            raw_response=_jsonable_gateway_object(event),
        )
        return JSONResponse({"status": "ok", "message": "payment failed"})

    logger.info("Stripe webhook %s ignored for payment %s", event_type, payment["id"])
    return JSONResponse({"status": "ok", "message": "event ignored"})


async def stripe_webhook_data(
    *,
    request: Request,
    db_factory=Database,
    db_connection_factory=get_db_connection,
    settings_obj=settings,
    event_constructor=construct_stripe_event,
    event_processor=process_stripe_event,
):
    """Handle Stripe server-to-server webhook notifications."""
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    event = event_constructor(payload, signature, settings_obj=settings_obj)
    with db_factory(db_connection_factory()) as db:
        return event_processor(db, event)


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

"""
Payment API endpoints — iyzico Checkout Form integration.

Endpoints:
- POST /api/v1/payments/initialize  — Create iyzico checkout session (auth required)
- POST /api/v1/payments/callback    — iyzico redirect after payment (token-based, no auth)
- POST /api/v1/payments/webhook     — iyzico server-to-server notification (no auth)
- POST /api/v1/payments/activate-free — Activate free plan without payment (auth required)
"""
import json
import logging
import uuid
from datetime import datetime
from dateutil.relativedelta import relativedelta

import iyzipay
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from fastapi.responses import RedirectResponse, JSONResponse

from auth.authentication import CurrentUser, get_current_user
from config.settings import settings
from database.crud import Database, get_db_connection
from utils.subscription import PLAN_FEATURES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


def get_client_ip(request: Request) -> str:
    """Extract real client IP from proxy headers with safe fallback."""
    # Cloudflare
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()

    # Standard proxy header (take first entry)
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()

    # Nginx
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    # Direct connection
    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"


def _get_iyzico_options():
    """Build iyzico API options dict."""
    return {
        'api_key': settings.iyzico.api_key,
        'secret_key': settings.iyzico.secret_key,
        'base_url': settings.iyzico.base_url,
    }


def _calculate_amount(plan_name: str, billing_period: str) -> float:
    """Calculate price from server-side PLAN_FEATURES (never trust client)."""
    plan = PLAN_FEATURES.get(plan_name)
    if not plan:
        raise ValueError(f"Unknown plan: {plan_name}")

    if billing_period == "annual":
        monthly = plan.get("price_annual_monthly", 0)
        return round(monthly * 12, 2)
    else:
        return float(plan.get("price_monthly", 0))


def _get_subscription_plan_id(db, plan_name: str) -> str:
    """Look up the UUID of a subscription plan by name."""
    cur = db.cursor()
    cur.execute("SELECT id FROM subscription_plans WHERE name = %s", (plan_name,))
    row = cur.fetchone()
    if row:
        return str(row["id"])
    return None


def _activate_subscription(db, org_id: str, plan_name: str, billing_period: str):
    """Set organization's subscription plan and dates."""
    plan_id = _get_subscription_plan_id(db, plan_name)
    if not plan_id:
        logger.warning(f"No subscription_plans row for '{plan_name}', skipping activation")
        return False

    now = datetime.utcnow()
    if plan_name == "free":
        end_date = None
    elif billing_period == "annual":
        end_date = now + relativedelta(years=1)
    else:
        end_date = now + relativedelta(months=1)

    cur = db.cursor()
    cur.execute("""
        UPDATE organizations
        SET subscription_plan_id = %s,
            subscription_start_date = %s,
            subscription_end_date = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """, (plan_id, now, end_date, org_id))
    db.commit()
    return True


def _retrieve_iyzico_payment(token: str) -> dict | None:
    """Retrieve payment result from iyzico by token. Returns parsed JSON or None."""
    try:
        checkout_form = iyzipay.CheckoutForm().retrieve(
            {'locale': 'tr', 'token': token},
            _get_iyzico_options(),
        )
        result = checkout_form.read().decode('utf-8')
        return json.loads(result)
    except Exception as e:
        logger.error(f"iyzico retrieve failed: {e}")
        return None


def _process_payment_result(db, payment: dict, result_json: dict) -> bool:
    """
    Process iyzico payment result: update DB record and activate subscription.
    Returns True if payment was successful.
    """
    payment_status = result_json.get('paymentStatus', '')
    iyzico_status = result_json.get('status', '')
    payment_id_iyzico = result_json.get('paymentId', '')
    cur = db.cursor()

    if iyzico_status == 'success' and payment_status == 'SUCCESS':
        cur.execute("""
            UPDATE payments
            SET status = 'completed',
                iyzico_payment_id = %s,
                iyzico_raw_response = %s,
                paid_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (payment_id_iyzico, json.dumps(result_json), str(payment["id"])))
        db.commit()

        _activate_subscription(
            db,
            str(payment["organization_id"]),
            payment["plan_name"],
            payment["billing_period"],
        )
        return True
    else:
        cur.execute("""
            UPDATE payments
            SET status = 'failed',
                iyzico_raw_response = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (json.dumps(result_json), str(payment["id"])))
        db.commit()
        return False


@router.post("/initialize")
async def initialize_payment(
    request: Request,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Create an iyzico Checkout Form session.

    Body: {"plan": "starter", "billing": "monthly"}
    Returns: {"checkout_form_content": "<html>...", "token": "...", "conversation_id": "..."}
    """
    plan_name = payload.get("plan", "").strip().lower()
    billing_period = payload.get("billing", "monthly").strip().lower()

    if plan_name not in PLAN_FEATURES or plan_name in ("free", "superadmin"):
        raise HTTPException(status_code=400, detail="Invalid plan for payment")

    if billing_period not in ("monthly", "annual"):
        raise HTTPException(status_code=400, detail="billing must be 'monthly' or 'annual'")

    amount = _calculate_amount(plan_name, billing_period)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount for this plan")

    conversation_id = str(uuid.uuid4()).replace("-", "")[:20]
    amount_str = f"{amount:.2f}"

    # Create pending payment record and fetch org data for buyer info
    with Database(get_db_connection()) as db:
        cur = db.cursor()

        # Fetch organization data for buyer fields
        cur.execute("""
            SELECT o.tax_id, o.address, o.city, o.country, o.phone,
                   u.phone as user_phone
            FROM organizations o
            LEFT JOIN users u ON u.id = %s
            WHERE o.id = %s
        """, (str(current_user.id), str(current_user.organization_id)))
        org_data = cur.fetchone() or {}

        cur.execute("""
            INSERT INTO payments (organization_id, user_id, plan_name, billing_period, amount, currency, iyzico_conversation_id, status)
            VALUES (%s, %s, %s, %s, %s, 'TRY', %s, 'pending')
            RETURNING id
        """, (str(current_user.organization_id), str(current_user.id),
              plan_name, billing_period, amount, conversation_id))
        payment_row = cur.fetchone()
        payment_id = str(payment_row["id"])
        db.commit()

    # Build buyer from real org data with fallbacks
    identity_number = (org_data.get("tax_id") or "11111111111").strip()
    buyer_address = org_data.get("address") or "Turkey"
    buyer_city = org_data.get("city") or "Istanbul"
    buyer_country = org_data.get("country") or "Turkey"
    buyer_phone = org_data.get("phone") or org_data.get("user_phone") or ""

    buyer = {
        'id': str(current_user.id),
        'name': current_user.first_name or 'User',
        'surname': current_user.last_name or 'User',
        'email': current_user.email,
        'identityNumber': identity_number,
        'registrationAddress': buyer_address,
        'city': buyer_city,
        'country': buyer_country,
        'ip': get_client_ip(request),
    }
    if buyer_phone:
        buyer['gsmNumber'] = buyer_phone

    contact_name = f"{current_user.first_name or 'User'} {current_user.last_name or 'User'}"
    billing_address = {
        'contactName': contact_name,
        'city': buyer_city,
        'country': buyer_country,
        'address': buyer_address,
    }

    basket_items = [
        {
            'id': payment_id,
            'name': f'IP Watch AI - {plan_name.title()} Plan ({billing_period})',
            'category1': 'Subscription',
            'itemType': 'VIRTUAL',
            'price': amount_str,
        }
    ]

    request_data = {
        'locale': 'tr',
        'conversationId': conversation_id,
        'price': amount_str,
        'paidPrice': amount_str,
        'currency': 'TRY',
        'basketId': payment_id,
        'paymentGroup': 'SUBSCRIPTION',
        'callbackUrl': settings.iyzico.callback_url,
        'enabledInstallments': [1],
        'buyer': buyer,
        'shippingAddress': billing_address,
        'billingAddress': billing_address,
        'basketItems': basket_items,
    }

    try:
        checkout_form = iyzipay.CheckoutFormInitialize().create(request_data, _get_iyzico_options())
        result = checkout_form.read().decode('utf-8')
        result_json = json.loads(result)
    except Exception as e:
        logger.error(f"iyzico initialize failed: {e}")
        raise HTTPException(status_code=502, detail="Payment gateway error")

    if result_json.get('status') != 'success':
        error_msg = result_json.get('errorMessage', 'Unknown error')
        logger.error(f"iyzico initialize error: {error_msg}")
        raise HTTPException(status_code=502, detail=f"Payment init failed: {error_msg}")

    token = result_json.get('token', '')

    # Store token on payment record
    with Database(get_db_connection()) as db:
        cur = db.cursor()
        cur.execute("""
            UPDATE payments SET iyzico_token = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (token, payment_id))
        db.commit()

    return {
        "checkout_form_content": result_json.get('checkoutFormContent', ''),
        "token": token,
        "conversation_id": conversation_id,
        "payment_id": payment_id,
    }


@router.post("/callback")
async def payment_callback(request: Request):
    """
    iyzico redirects the browser here after payment.
    Retrieves payment result, updates DB, redirects to dashboard or checkout.
    No auth required — uses iyzico token to identify the payment.
    """
    form = await request.form()
    token = form.get("token", "")

    if not token:
        return RedirectResponse(url="/checkout?error=missing_token", status_code=303)

    # Find our payment record by token
    with Database(get_db_connection()) as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM payments WHERE iyzico_token = %s", (token,))
        payment = cur.fetchone()

        if not payment:
            logger.error(f"Payment not found for token: {token[:20]}...")
            return RedirectResponse(url="/checkout?error=payment_not_found", status_code=303)

        # Idempotency: if already completed, skip re-processing
        if payment["status"] == "completed":
            return RedirectResponse(url="/dashboard?payment=success", status_code=303)

        plan_name = payment["plan_name"]
        billing_period = payment["billing_period"]

        # Retrieve result from iyzico
        result_json = _retrieve_iyzico_payment(token)
        if result_json is None:
            return RedirectResponse(url="/checkout?error=gateway_error", status_code=303)

        success = _process_payment_result(db, payment, result_json)

        if success:
            return RedirectResponse(url="/dashboard?payment=success", status_code=303)
        else:
            return RedirectResponse(
                url=f"/checkout?plan={plan_name}&billing={billing_period}&error=payment_failed",
                status_code=303,
            )


@router.post("/webhook")
async def payment_webhook(request: Request):
    """
    iyzico server-to-server webhook notification.
    No auth required — iyzico POSTs token after payment completes.
    Always returns HTTP 200 so iyzico doesn't retry endlessly.
    """
    # iyzico sends token as form data or JSON
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
            pass

    if not token:
        logger.warning("Webhook called without token")
        return JSONResponse({"status": "error", "message": "missing token"})

    # Look up payment
    with Database(get_db_connection()) as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM payments WHERE iyzico_token = %s", (token,))
        payment = cur.fetchone()

        if not payment:
            logger.error(f"Webhook: payment not found for token: {token[:20]}...")
            return JSONResponse({"status": "error", "message": "payment not found"})

        # Idempotency: already processed
        if payment["status"] == "completed":
            logger.info(f"Webhook: payment {payment['id']} already completed, skipping")
            return JSONResponse({"status": "ok", "message": "already processed"})

        # Retrieve from iyzico
        result_json = _retrieve_iyzico_payment(token)
        if result_json is None:
            logger.error(f"Webhook: failed to retrieve payment from iyzico for {payment['id']}")
            return JSONResponse({"status": "error", "message": "iyzico retrieval failed"})

        success = _process_payment_result(db, payment, result_json)

        if success:
            logger.info(f"Webhook: payment {payment['id']} completed successfully")
            return JSONResponse({"status": "ok", "message": "payment activated"})
        else:
            logger.warning(f"Webhook: payment {payment['id']} failed")
            return JSONResponse({"status": "ok", "message": "payment failed"})


@router.post("/activate-free")
async def activate_free_plan(
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Activate the free plan without payment.
    Sets organization subscription to free plan with no end date.
    """
    with Database(get_db_connection()) as db:
        success = _activate_subscription(
            db,
            str(current_user.organization_id),
            "free",
            "monthly",
        )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to activate free plan")

    return {"success": True, "redirect": "/dashboard"}

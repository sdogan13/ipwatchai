import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from services.billing_catalog import get_billing_catalog


def _request(headers=None):
    header_pairs = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]

    async def receive():
        return {"type": "http.request", "body": b""}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": header_pairs,
            "client": ("203.0.113.10", 1234),
        },
        receive,
    )


def _settings(catalog):
    return SimpleNamespace(
        billing=SimpleNamespace(region_catalog_json=json.dumps(catalog)),
        stripe=SimpleNamespace(
            secret_key="sk_test",
            webhook_secret="whsec_test",
            success_url="https://app.example.test/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://app.example.test/cancel",
            automatic_tax=True,
        ),
        iyzico=SimpleNamespace(
            api_key="iyz",
            secret_key="secret",
            base_url="https://sandbox.iyzico.test",
            callback_url="https://app.example.test/iyzico/callback",
        ),
    )


def _catalog_config():
    return {
        "UK": {
            "plans": {
                "starter": {
                    "price_monthly": 31,
                    "price_annual_monthly": 25,
                    "stripe_price_monthly": "price_uk_starter_month",
                    "stripe_price_annual": "price_uk_starter_year",
                }
            },
            "credit_packs": {
                "small": {"price": 7, "stripe_price": "price_uk_small"}
            },
        },
        "EU": {
            "plans": {
                "starter": {
                    "price_monthly": 35,
                    "price_annual_monthly": 28,
                    "stripe_price_monthly": "price_eu_starter_month",
                    "stripe_price_annual": "price_eu_starter_year",
                }
            },
            "credit_packs": {
                "small": {"price": 8, "stripe_price": "price_eu_small"}
            },
        },
        "TR": {
            "plans": {
                "starter": {
                    "price_monthly": 499,
                    "price_annual_monthly": 399,
                }
            },
            "credit_packs": {
                "small": {"price": 200}
            },
        },
    }


def _db_context(cursor):
    db = MagicMock()
    db.cursor.return_value = cursor
    cm = MagicMock()
    cm.__enter__.return_value = db
    cm.__exit__.return_value = False
    return cm, db


def _current_user():
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="buyer@example.test",
        first_name="Buyer",
        last_name="User",
    )


def test_billing_catalog_resolves_regions_and_fallback():
    settings_obj = _settings(_catalog_config())

    uk = get_billing_catalog(region="UK", settings_obj=settings_obj)
    eu = get_billing_catalog(region="EU", settings_obj=settings_obj)
    tr = get_billing_catalog(region="TR", settings_obj=settings_obj)
    fallback = get_billing_catalog(region="NOPE", settings_obj=settings_obj)
    by_header = get_billing_catalog(headers={"CF-IPCountry": "DE"}, settings_obj=settings_obj)

    assert uk["provider"] == "stripe"
    assert uk["currency"] == "GBP"
    assert uk["plans"]["starter"]["price_monthly"] == 31
    assert eu["currency"] == "EUR"
    assert tr["provider"] == "iyzico"
    assert tr["currency"] == "TRY"
    assert fallback["region"] == "UK"
    assert by_header["region"] == "EU"
    assert uk["credit_packs"][0]["label_key"] == "studio.buy_credits.pack_small"


@pytest.mark.asyncio
async def test_stripe_subscription_checkout_creates_pending_payment_and_url():
    from services.payment_service import initialize_payment_data

    settings_obj = _settings(_catalog_config())
    user = _current_user()

    cursor_org = MagicMock()
    cursor_org.fetchone.return_value = {"country": None}
    cm_org, _ = _db_context(cursor_org)

    cursor_insert = MagicMock()
    cursor_insert.fetchone.return_value = {"id": "pay-sub-1"}
    cm_insert, db_insert = _db_context(cursor_insert)

    cursor_update = MagicMock()
    cm_update, db_update = _db_context(cursor_update)

    session_creator = MagicMock(
        return_value={
            "id": "cs_sub_1",
            "url": "https://checkout.stripe.test/sub",
            "customer": "cus_1",
            "subscription": "sub_1",
        }
    )

    response = await initialize_payment_data(
        request=_request(),
        payload={"plan": "starter", "billing": "monthly", "region": "UK"},
        current_user=user,
        db_factory=MagicMock(side_effect=[cm_org, cm_insert, cm_update]),
        db_connection_factory=MagicMock(side_effect=[object(), object(), object()]),
        settings_obj=settings_obj,
        stripe_session_creator=session_creator,
    )

    assert response["provider"] == "stripe"
    assert response["checkout_url"] == "https://checkout.stripe.test/sub"
    assert response["session_id"] == "cs_sub_1"
    session_payload = session_creator.call_args.args[0]
    assert session_payload["mode"] == "subscription"
    assert session_payload["line_items"] == [{"price": "price_uk_starter_month", "quantity": 1}]
    assert session_payload["automatic_tax"] == {"enabled": True}
    assert session_payload["metadata"]["payment_id"] == "pay-sub-1"
    db_insert.commit.assert_called_once()
    db_update.commit.assert_called_once()


@pytest.mark.asyncio
async def test_stripe_credit_pack_checkout_creates_pending_payment_and_url():
    from services.payment_service import initialize_credit_pack_purchase_data

    settings_obj = _settings(_catalog_config())
    user = _current_user()

    cursor_org = MagicMock()
    cursor_org.fetchone.return_value = {"country": None}
    cm_org, _ = _db_context(cursor_org)

    cursor_insert = MagicMock()
    cursor_insert.fetchone.return_value = {"id": "pay-pack-1"}
    cm_insert, db_insert = _db_context(cursor_insert)

    cursor_update = MagicMock()
    cm_update, db_update = _db_context(cursor_update)

    session_creator = MagicMock(
        return_value={
            "id": "cs_pack_1",
            "url": "https://checkout.stripe.test/pack",
            "customer": "cus_2",
            "payment_intent": "pi_1",
        }
    )

    response = await initialize_credit_pack_purchase_data(
        request=_request(),
        payload={"pack_id": "small", "region": "UK"},
        current_user=user,
        db_factory=MagicMock(side_effect=[cm_org, cm_insert, cm_update]),
        db_connection_factory=MagicMock(side_effect=[object(), object(), object()]),
        settings_obj=settings_obj,
        stripe_session_creator=session_creator,
    )

    assert response["provider"] == "stripe"
    assert response["checkout_url"] == "https://checkout.stripe.test/pack"
    assert response["pack_id"] == "small"
    session_payload = session_creator.call_args.args[0]
    assert session_payload["mode"] == "payment"
    assert session_payload["line_items"] == [{"price": "price_uk_small", "quantity": 1}]
    assert session_payload["payment_intent_data"]["metadata"]["kind"] == "credit_pack"
    db_insert.commit.assert_called_once()
    db_update.commit.assert_called_once()


def test_stripe_webhook_completes_subscription_once():
    from services.payment_service import process_stripe_event

    payment = {
        "id": "pay-sub-1",
        "organization_id": "org-1",
        "plan_name": "starter",
        "billing_period": "monthly",
        "status": "pending",
        "kind": "subscription",
    }
    cursor = MagicMock()
    cursor.fetchone.return_value = payment
    db = MagicMock()
    db.cursor.return_value = cursor
    activator = MagicMock(return_value=True)
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_sub_1",
                "object": "checkout.session",
                "client_reference_id": "pay-sub-1",
                "subscription": "sub_1",
                "customer": "cus_1",
                "metadata": {"payment_id": "pay-sub-1", "kind": "subscription"},
            }
        },
    }

    response = process_stripe_event(db, event, subscription_activator=activator)

    assert response.status_code == 200
    activator.assert_called_once_with(db, "org-1", "starter", "monthly")

    cursor.fetchone.return_value = dict(payment, status="completed")
    activator.reset_mock()
    process_stripe_event(db, event, subscription_activator=activator)
    activator.assert_not_called()


def test_stripe_webhook_failure_marks_pending_without_fulfillment():
    from services.payment_service import process_stripe_event

    payment = {
        "id": "pay-pack-1",
        "organization_id": "org-1",
        "status": "pending",
        "kind": "credit_pack",
        "pack_id": "small",
        "credits_amount": 25,
    }
    cursor = MagicMock()
    cursor.fetchone.return_value = payment
    db = MagicMock()
    db.cursor.return_value = cursor
    credit_applier = MagicMock(return_value=True)
    event = {
        "type": "checkout.session.expired",
        "data": {
            "object": {
                "id": "cs_pack_1",
                "object": "checkout.session",
                "client_reference_id": "pay-pack-1",
                "metadata": {"payment_id": "pay-pack-1", "kind": "credit_pack"},
            }
        },
    }

    response = process_stripe_event(db, event, credit_pack_applier=credit_applier)

    assert response.status_code == 200
    credit_applier.assert_not_called()
    executed_sql = " ".join(call.args[0] for call in cursor.execute.call_args_list)
    assert "SET status = 'failed'" in executed_sql

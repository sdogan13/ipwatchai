"""
Payment API endpoints - iyzico Checkout Form integration.

Endpoints:
- POST /api/v1/payments/initialize  - Create iyzico checkout session (auth required)
- POST /api/v1/payments/callback    - iyzico redirect after payment (token-based, no auth)
- POST /api/v1/payments/webhook     - iyzico server-to-server notification (no auth)
- POST /api/v1/payments/activate-free - Activate free plan without payment (auth required)
"""

import logging

from fastapi import APIRouter, Body, Depends, Request

from auth.authentication import CurrentUser, get_current_user
from services.payment_service import (
    _activate_subscription,  # noqa: F401  re-exported
    _get_iyzico_options,  # noqa: F401  re-exported
    activate_free_plan_data,
    initialize_payment_data,
    payment_callback_data,
    payment_webhook_data,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.post("/initialize")
async def initialize_payment(
    request: Request,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create an iyzico Checkout Form session."""
    return await initialize_payment_data(
        request=request,
        payload=payload,
        current_user=current_user,
    )


@router.post("/callback")
async def payment_callback(request: Request):
    """Handle the browser redirect callback from iyzico."""
    return await payment_callback_data(request=request)


@router.post("/webhook")
async def payment_webhook(request: Request):
    """Handle iyzico server-to-server webhook notifications."""
    return await payment_webhook_data(request=request)


@router.post("/activate-free")
async def activate_free_plan(
    current_user: CurrentUser = Depends(get_current_user),
):
    """Activate the free plan without payment."""
    return await activate_free_plan_data(current_user=current_user)

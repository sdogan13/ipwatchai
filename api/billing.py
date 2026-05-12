"""
Billing API endpoints.
Public-facing discount code validation for pricing page / upgrade flow,
plus one-shot AI credit pack purchases.
"""
import logging

from fastapi import APIRouter, Body, Depends, Request

from auth.authentication import CurrentUser, get_current_user
from services.payment_service import initialize_credit_pack_purchase_data
from utils.subscription import list_credit_packs

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


@router.post("/validate-discount")
async def validate_discount_code(
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Validate a discount code and return the discount details.
    Body: {"code": "LAUNCH20", "plan": "professional"}
    """
    from services.billing_service import validate_discount_code_payload

    return await validate_discount_code_payload(payload=payload)


@router.get("/credit-packs")
async def get_credit_packs(
    current_user: CurrentUser = Depends(get_current_user),
):
    """List the AI credit packs available for purchase."""
    return {"packs": list_credit_packs()}


@router.post("/purchase-credits")
async def purchase_credits(
    request: Request,
    payload: dict = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Initialize an iyzico checkout session for an AI credit pack.

    Body: {"pack_id": "small" | "medium" | "large", "discount_code": "..." (optional)}
    """
    return await initialize_credit_pack_purchase_data(
        request=request,
        payload=payload,
        current_user=current_user,
    )

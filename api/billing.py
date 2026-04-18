"""
Billing API endpoints.
Public-facing discount code validation for pricing page / upgrade flow.
"""
import logging

from fastapi import APIRouter, Depends, Body

from auth.authentication import CurrentUser, get_current_user
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

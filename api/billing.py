"""
Billing API endpoints.
Public-facing discount code validation for pricing page / upgrade flow.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Body

from auth.authentication import CurrentUser, get_current_user
from database.crud import Database

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
    code = payload.get("code", "").strip().upper()
    plan = payload.get("plan")

    if not code:
        raise HTTPException(status_code=400, detail="Code is required")

    with Database() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT id, code, discount_type, discount_value, applies_to_plan,
                   max_uses, current_uses
            FROM discount_codes
            WHERE code = %s
            AND is_active = TRUE
            AND (valid_from IS NULL OR valid_from <= NOW())
            AND (valid_until IS NULL OR valid_until >= NOW())
            AND (max_uses IS NULL OR current_uses < max_uses)
        """, (code,))
        discount = cur.fetchone()

    if not discount:
        raise HTTPException(status_code=404, detail="Invalid or expired discount code")

    if discount["applies_to_plan"] and plan and discount["applies_to_plan"] != plan:
        raise HTTPException(
            status_code=400,
            detail=f"This code only applies to the {discount['applies_to_plan']} plan",
        )

    return {
        "valid": True,
        "code": code,
        "discount_type": discount["discount_type"],
        "discount_value": float(discount["discount_value"]),
        "applies_to_plan": discount["applies_to_plan"],
    }

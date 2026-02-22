"""
Trademark Routes
Extracted from api/routes.py for maintainability.
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException, status
from auth.authentication import CurrentUser, get_current_user, require_role
from models.schemas import (
    PaginatedResponse, SuccessResponse
)
from database.crud import Database

logger = logging.getLogger(__name__)

trademark_router = APIRouter(prefix="/trademark", tags=["Trademark"])
# ==========================================
# Trademark Detail (extracted goods lazy-load)
# ==========================================

@trademark_router.get("/{application_no:path}/extracted-goods")
async def get_extracted_goods(
    application_no: str,
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Lazy-load endpoint: fetch extracted goods for a specific trademark.
    Called when user clicks the extracted goods indicator on a card.
    """
    with Database() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT application_no, name, extracted_goods, nice_class_numbers
            FROM trademarks
            WHERE application_no = %s
        """, (application_no,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marka bulunamadi")

    extracted = row.get("extracted_goods")
    if not extracted or extracted == [] or extracted is None:
        return {
            "application_no": application_no,
            "has_extracted_goods": False,
            "extracted_goods": [],
            "total_items": 0
        }

    return {
        "application_no": application_no,
        "name": row.get("name"),
        "has_extracted_goods": True,
        "extracted_goods": extracted,
        "nice_classes": row.get("nice_class_numbers"),
        "total_items": len(extracted) if isinstance(extracted, list) else 0
    }



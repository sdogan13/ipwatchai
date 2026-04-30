"""
Trademark Routes
Extracted from api/routes.py for maintainability.
"""
import logging
from typing import List, Optional
from uuid import UUID
from datetime import date

from fastapi import APIRouter, Depends, Query, HTTPException, status
from auth.authentication import CurrentUser, get_current_user, require_role
from models.schemas import (
    PaginatedResponse, SuccessResponse
)
from database.crud import Database
from services.trademark_service import (
    get_extracted_goods_data,
    get_trademark_events_data,
)

logger = logging.getLogger(__name__)

trademark_router = APIRouter(prefix="/trademark", tags=["Trademark"])

# ---------------------------------------------------------------------------
# Turkish labels for event types and statuses
# ---------------------------------------------------------------------------
EVENT_TYPE_LABELS = {
    "transfer": "Devir",
    "merger": "Birleşme",
    "partial_transfer": "Kısmi Devir",
    "cancellation": "İptal",
    "withdrawal": "Geri Çekme",
    "renewal": "Yenileme",
    "seizure": "Haciz",
    "precautionary_seizure": "İhtiyati Haciz",
    "injunction": "İhtiyati Tedbir",
    "precautionary_injunction": "İhtiyati Tedbir",
    "seizure_lift": "Haciz Kaldırma",
    "injunction_lift": "Tedbir Kaldırma",
    "restriction_lift": "Kısıtlama Kaldırma",
    "license": "Lisans",
    "bankruptcy": "İflas",
    "correction": "Düzeltme",
    "madrid_registration": "Madrid Tescil",
    "madrid_renewal": "Madrid Yenileme",
    "address_change": "Adres Değişikliği",
    "name_change": "Unvan Değişikliği",
    "class_change": "Sınıf Değişikliği",
}

# Health card severity: critical > warning > info
EVENT_SEVERITY = {
    "cancellation": "critical",
    "seizure": "critical",
    "precautionary_seizure": "critical",
    "injunction": "warning",
    "precautionary_injunction": "warning",
    "bankruptcy": "critical",
    "transfer": "warning",
    "merger": "warning",
    "partial_transfer": "warning",
    "withdrawal": "warning",
    "renewal": "info",
    "license": "info",
    "seizure_lift": "info",
    "injunction_lift": "info",
    "restriction_lift": "info",
    "correction": "info",
    "address_change": "info",
    "name_change": "info",
    "class_change": "info",
    "madrid_registration": "info",
    "madrid_renewal": "info",
}


# ==========================================
# Event Timeline + Health Card
# ==========================================

@trademark_router.get("/{application_no:path}/events")
async def get_trademark_events(
    application_no: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    event_type: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Returns the event timeline and health card summary for a trademark.

    Response:
    - health_card: summary of the trademark's event-derived state
    - events: paginated list of events ordered by bulletin_date DESC
    - total: total event count
    """
    return await get_trademark_events_data(
        application_no=application_no,
        page=page,
        per_page=per_page,
        event_type=event_type,
        current_user=current_user,
    )


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
    return await get_extracted_goods_data(
        application_no=application_no,
        current_user=current_user,
    )

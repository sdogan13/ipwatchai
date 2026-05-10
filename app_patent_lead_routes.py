"""Patent / Faydalı Model lead API routes.

Endpoints:

  * ``GET /api/v1/patent-leads``         — paginated list filtered by category
  * ``GET /api/v1/patent-leads/summary`` — counts per category for badges

Patent leads are derived from ``patent_events`` rows on-the-fly by
``services/patent_lead_service.py``. Four categories: lapse, transfer,
license, rejected.

Optional ``watchlist_scoped=true`` restricts results to events on
patents matching the user's active holder watchlist — the "leads on
competitors I'm tracking" view.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request


logger = logging.getLogger("turkpatent.patent_lead_routes")


def register_patent_lead_routes(app, limiter):
    from auth.authentication import get_current_user
    from services import patent_lead_service as svc

    @app.get("/api/v1/patent-leads", tags=["Patent Leads"])
    @limiter.limit("60/minute")
    async def list_patent_leads(
        request: Request,
        category: str = Query(..., description="Lead category: lapse|transfer|license|rejected"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        holder: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        watchlist_scoped: bool = Query(False),
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.list_patent_leads(
            current_user=current_user,
            category=category,
            page=page,
            page_size=page_size,
            holder=holder,
            date_from=date_from,
            date_to=date_to,
            watchlist_scoped=watchlist_scoped,
        )

    @app.get("/api/v1/patent-leads/summary", tags=["Patent Leads"])
    @limiter.limit("60/minute")
    async def patent_lead_summary(
        request: Request,
        watchlist_scoped: bool = Query(False),
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.get_patent_lead_summary(
            current_user=current_user,
            watchlist_scoped=watchlist_scoped,
        )

"""Patent / Faydalı Model alert API routes.

Sister to ``app_design_alert_routes.py``. Endpoints:

  * ``GET    /api/v1/patent-alerts``                  — list (paginated, filterable)
  * ``GET    /api/v1/patent-alerts/summary``          — counts by status / severity
  * ``GET    /api/v1/patent-alerts/{id}``             — detail (transitions 'new' → 'seen')
  * ``POST   /api/v1/patent-alerts/{id}/acknowledge`` — acknowledge with optional notes
  * ``POST   /api/v1/patent-alerts/{id}/resolve``     — resolve with optional notes
  * ``POST   /api/v1/patent-alerts/{id}/dismiss``     — dismiss with optional notes
"""
from __future__ import annotations

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel


logger = logging.getLogger("turkpatent.patent_alert_routes")


class AlertActionBody(BaseModel):
    notes: Optional[str] = None


def register_patent_alert_routes(app, limiter):
    from auth.authentication import get_current_user
    from services import patent_alert_service as svc

    @app.get("/api/v1/patent-alerts", tags=["Patent Alerts"])
    @limiter.limit("60/minute")
    async def list_patent_alerts(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        status: Optional[List[str]] = Query(None),
        severity: Optional[List[str]] = Query(None),
        watchlist_item_id: Optional[UUID] = Query(None),
        min_score: float = Query(0.0, ge=0.0, le=100.0),
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.list_patent_alerts(
            current_user=current_user,
            page=page,
            page_size=page_size,
            status_filters=status,
            severity_filters=severity,
            watchlist_item_id=watchlist_item_id,
            min_score=min_score,
        )

    @app.get("/api/v1/patent-alerts/summary", tags=["Patent Alerts"])
    @limiter.limit("60/minute")
    async def patent_alerts_summary(
        request: Request,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.get_patent_alerts_summary(current_user=current_user)

    @app.get("/api/v1/patent-alerts/export.csv", tags=["Patent Alerts"])
    @limiter.limit("10/minute")
    async def export_patent_alerts_csv(
        request: Request,
        status: Optional[List[str]] = Query(None),
        severity: Optional[List[str]] = Query(None),
        watchlist_item_id: Optional[UUID] = Query(None),
        min_score: float = Query(0.0, ge=0.0, le=100.0),
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.export_patent_alerts_csv(
            current_user=current_user,
            status_filters=status,
            severity_filters=severity,
            watchlist_item_id=watchlist_item_id,
            min_score=min_score,
        )

    @app.get("/api/v1/patent-alerts/{alert_id}", tags=["Patent Alerts"])
    @limiter.limit("60/minute")
    async def get_patent_alert(
        request: Request,
        alert_id: UUID,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.get_patent_alert(alert_id=alert_id, current_user=current_user)

    @app.post("/api/v1/patent-alerts/{alert_id}/acknowledge", tags=["Patent Alerts"])
    @limiter.limit("30/minute")
    async def acknowledge_patent_alert(
        request: Request,
        alert_id: UUID,
        body: AlertActionBody,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.acknowledge_patent_alert(
            alert_id=alert_id, notes=body.notes, current_user=current_user,
        )

    @app.post("/api/v1/patent-alerts/{alert_id}/resolve", tags=["Patent Alerts"])
    @limiter.limit("30/minute")
    async def resolve_patent_alert(
        request: Request,
        alert_id: UUID,
        body: AlertActionBody,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.resolve_patent_alert(
            alert_id=alert_id, notes=body.notes, current_user=current_user,
        )

    @app.post("/api/v1/patent-alerts/{alert_id}/dismiss", tags=["Patent Alerts"])
    @limiter.limit("30/minute")
    async def dismiss_patent_alert(
        request: Request,
        alert_id: UUID,
        body: AlertActionBody,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.dismiss_patent_alert(
            alert_id=alert_id, notes=body.notes, current_user=current_user,
        )

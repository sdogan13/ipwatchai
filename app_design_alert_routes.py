"""Design alert API routes.

Endpoints:
  * ``GET    /api/v1/design-alerts``                      — list (paginated, filterable)
  * ``GET    /api/v1/design-alerts/summary``              — counts by status / severity
  * ``GET    /api/v1/design-alerts/{id}``                 — detail (marks 'new' as 'seen')
  * ``POST   /api/v1/design-alerts/{id}/acknowledge``     — acknowledge with optional notes
  * ``POST   /api/v1/design-alerts/{id}/resolve``         — resolve with optional notes
  * ``POST   /api/v1/design-alerts/{id}/dismiss``         — dismiss with optional notes
"""
from __future__ import annotations

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import Depends, Query, Request
from pydantic import BaseModel


logger = logging.getLogger("turkpatent.design_alert_routes")


class AlertActionBody(BaseModel):
    notes: Optional[str] = None


def register_design_alert_routes(app, limiter):
    from auth.authentication import get_current_user
    from services import design_alert_service as svc

    @app.get("/api/v1/design-alerts", tags=["Design Alerts"])
    @limiter.limit("60/minute")
    async def list_design_alerts(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        status: Optional[List[str]] = Query(None),
        severity: Optional[List[str]] = Query(None),
        watchlist_item_id: Optional[UUID] = Query(None),
        min_score: float = Query(0.0, ge=0.0, le=100.0),
        current_user=Depends(get_current_user),
    ):
        return svc.list_design_alerts(
            current_user=current_user,
            page=page,
            page_size=page_size,
            status_filters=status,
            severity_filters=severity,
            watchlist_item_id=watchlist_item_id,
            min_score=min_score,
        )

    @app.get("/api/v1/design-alerts/summary", tags=["Design Alerts"])
    @limiter.limit("60/minute")
    async def get_design_alerts_summary(
        request: Request,
        current_user=Depends(get_current_user),
    ):
        return svc.get_design_alerts_summary(current_user=current_user)

    @app.get("/api/v1/design-alerts/{alert_id}", tags=["Design Alerts"])
    @limiter.limit("60/minute")
    async def get_design_alert(
        request: Request,
        alert_id: UUID,
        current_user=Depends(get_current_user),
    ):
        return svc.get_design_alert(alert_id=alert_id, current_user=current_user)

    @app.post("/api/v1/design-alerts/{alert_id}/acknowledge", tags=["Design Alerts"])
    @limiter.limit("30/minute")
    async def acknowledge_design_alert(
        request: Request,
        alert_id: UUID,
        body: AlertActionBody,
        current_user=Depends(get_current_user),
    ):
        return svc.acknowledge_design_alert(
            alert_id=alert_id,
            notes=body.notes,
            current_user=current_user,
        )

    @app.post("/api/v1/design-alerts/{alert_id}/resolve", tags=["Design Alerts"])
    @limiter.limit("30/minute")
    async def resolve_design_alert(
        request: Request,
        alert_id: UUID,
        body: AlertActionBody,
        current_user=Depends(get_current_user),
    ):
        return svc.resolve_design_alert(
            alert_id=alert_id,
            notes=body.notes,
            current_user=current_user,
        )

    @app.post("/api/v1/design-alerts/{alert_id}/dismiss", tags=["Design Alerts"])
    @limiter.limit("30/minute")
    async def dismiss_design_alert(
        request: Request,
        alert_id: UUID,
        body: AlertActionBody,
        current_user=Depends(get_current_user),
    ):
        return svc.dismiss_design_alert(
            alert_id=alert_id,
            notes=body.notes,
            current_user=current_user,
        )

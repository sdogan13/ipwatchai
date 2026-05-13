"""Design detail API routes.

  * ``GET /api/v1/designs/{id}`` — full record by UUID

Design corpus is shared across tenants (same as design search). Auth
is required to keep the corpus behind the same gate the rest of the
API uses.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import Depends, HTTPException, Request


logger = logging.getLogger("turkpatent.design_detail_routes")


def register_design_detail_routes(app, limiter):
    from auth.authentication import get_current_user
    from services import design_detail_service as svc

    @app.get("/api/v1/designs/{design_id}", tags=["Design Detail"])
    @limiter.limit("60/minute")
    async def get_design_detail(
        request: Request,
        design_id: UUID,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.get_design_detail(design_id=design_id)

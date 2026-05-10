"""Patent detail API routes.

  * ``GET /api/v1/patents/{id}``                       — full record by UUID
  * ``GET /api/v1/patents/by-application/{app_no}``    — by application_no

The patent corpus is shared across tenants (same as search) so detail
isn't org-scoped. Auth is required to keep the corpus behind the same
gate the rest of the API uses.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import Depends, HTTPException, Path, Request


logger = logging.getLogger("turkpatent.patent_detail_routes")


def register_patent_detail_routes(app, limiter):
    from auth.authentication import get_current_user
    from services import patent_detail_service as svc

    @app.get("/api/v1/patents/{patent_id}", tags=["Patent Detail"])
    @limiter.limit("60/minute")
    async def get_patent_detail(
        request: Request,
        patent_id: UUID,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.get_patent_detail(patent_id=patent_id)

    @app.get("/api/v1/patents/by-application/{application_no:path}",
             tags=["Patent Detail"])
    @limiter.limit("60/minute")
    async def get_patent_detail_by_app(
        request: Request,
        application_no: str = Path(..., min_length=1, max_length=50),
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return svc.get_patent_detail_by_application_no(application_no=application_no)

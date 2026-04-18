"""
Pipeline Management API - Admin-only endpoints
===============================================
POST /api/v1/pipeline/trigger       - Trigger full pipeline run
POST /api/v1/pipeline/trigger-step  - Trigger a single step
GET  /api/v1/pipeline/status        - Current status + recent history
GET  /api/v1/pipeline/runs/{run_id} - Detailed run results

Usage:
    from api.pipeline import router as pipeline_router
    app.include_router(pipeline_router)
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from auth.authentication import CurrentUser, require_superadmin
from services.pipeline_service import (
    VALID_STEPS,
    get_pipeline_run_detail_data,
    get_pipeline_status_data,
    trigger_pipeline_run_data,
    trigger_pipeline_step_data,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/pipeline", tags=["Pipeline"])


@router.post("/trigger")
async def trigger_pipeline(
    skip_download: bool = Query(False, description="Skip download step"),
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Trigger a full pipeline run in the background."""
    return await trigger_pipeline_run_data(
        skip_download=skip_download,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post("/trigger-step")
async def trigger_pipeline_step(
    step: str = Query(..., description=f"Step to run: {', '.join(VALID_STEPS)}"),
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Trigger a single pipeline step in the background."""
    return await trigger_pipeline_step_data(
        step=step,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.get("/status")
async def pipeline_status(
    limit: int = Query(10, ge=1, le=50, description="Number of recent runs"),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Return recent pipeline runs and current status."""
    return await get_pipeline_status_data(limit=limit, current_user=current_user)


@router.get("/runs/{run_id}")
async def pipeline_run_detail(
    run_id: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Return full detail for a specific pipeline run."""
    return await get_pipeline_run_detail_data(run_id=run_id, current_user=current_user)

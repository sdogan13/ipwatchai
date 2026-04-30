"""
Creative Suite API.

POST /api/v1/tools/suggest-names
POST /api/v1/tools/generate-logo
GET  /api/v1/tools/logo-projects/{project_id}
POST /api/v1/tools/logo-projects/{project_id}/select
POST /api/v1/tools/generated-image/{image_id}/audit-retry
GET  /api/v1/tools/generated-image/{image_id}
GET  /api/v1/tools/generation-history
GET  /api/v1/tools/status
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from auth.authentication import CurrentUser, get_current_user
from database.crud import Database
from models.schemas import (
    GenerationHistoryResponse,
    LogoGenerationRequest,
    LogoGenerationResponse,
    LogoProjectResponse,
    LogoProjectSelectRequest,
    NameSuggestionRequest,
    NameSuggestionResponse,
)
from services.creative_service import (
    audit_generated_logo_image,
    creative_suite_status_data,
    generate_logo_data,
    get_generated_image_response,
    get_generation_history_data,
    get_logo_project_data,
    retry_logo_audit_data,
    select_logo_project_candidate_data,
    suggest_names_data,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tools", tags=["Creative Suite"])


def _audit_log(
    user_id: str,
    org_id: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Write an audit log entry for Creative Suite actions."""
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO audit_log
                    (user_id, organization_id, action, resource_type, resource_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
            """,
                (
                    user_id,
                    org_id,
                    action,
                    resource_type,
                    resource_id,
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                ),
            )
            db.commit()
    except Exception as exc:
        logger.warning("Audit log write failed (non-fatal): %s", exc)


def _log_generation(
    org_id: str,
    user_id: str,
    feature_type: str,
    input_prompt: str,
    input_params: dict,
    output_data: dict,
    credits_used: int = 1,
) -> Optional[str]:
    """Insert a generation log record and return its id."""
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO generation_logs
                    (org_id, user_id, feature_type, input_prompt, input_params, output_data, credits_used)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """,
                (
                    org_id,
                    user_id,
                    feature_type,
                    input_prompt,
                    json.dumps(input_params, ensure_ascii=False),
                    json.dumps(output_data, ensure_ascii=False),
                    credits_used,
                ),
            )
            db.commit()
            row = cur.fetchone()
            return str(row["id"]) if row else None
    except Exception as exc:
        logger.error("Failed to log generation: %s", exc)
        return None


@router.post("/suggest-names", response_model=NameSuggestionResponse)
async def suggest_names(
    request: NameSuggestionRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    return await suggest_names_data(
        request=request,
        current_user=current_user,
        generation_log_handler=_log_generation,
        audit_log_handler=_audit_log,
    )


@router.post("/generate-logo", response_model=LogoGenerationResponse)
async def generate_logo(
    request: LogoGenerationRequest,
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _schedule_audit(image_id: str) -> None:
        if background_tasks is not None:
            background_tasks.add_task(audit_generated_logo_image, image_id)

    return await generate_logo_data(
        request=request,
        current_user=current_user,
        audit_scheduler=_schedule_audit,
        generation_log_handler=_log_generation,
        audit_log_handler=_audit_log,
    )


@router.get("/logo-projects/{project_id}", response_model=LogoProjectResponse)
async def get_logo_project(
    project_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    return await get_logo_project_data(
        project_id=project_id,
        current_user=current_user,
    )


@router.post("/logo-projects/{project_id}/select", response_model=LogoProjectResponse)
async def select_logo_project_candidate(
    project_id: str,
    request: LogoProjectSelectRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    return await select_logo_project_candidate_data(
        project_id=project_id,
        image_id=request.image_id,
        current_user=current_user,
    )


@router.post("/generated-image/{image_id}/audit-retry")
async def retry_logo_audit(
    image_id: str,
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _schedule_audit(retry_image_id: str) -> None:
        if background_tasks is not None:
            background_tasks.add_task(audit_generated_logo_image, retry_image_id)

    return await retry_logo_audit_data(
        image_id=image_id,
        current_user=current_user,
        audit_scheduler=_schedule_audit,
    )


@router.get("/generated-image/{image_id}")
async def get_generated_image(
    image_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    return await get_generated_image_response(
        image_id=image_id,
        current_user=current_user,
    )


@router.get("/generation-history", response_model=GenerationHistoryResponse)
async def get_generation_history(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    feature_type: Optional[str] = Query(None, pattern="^(NAME|LOGO)$"),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await get_generation_history_data(
        page=page,
        per_page=per_page,
        feature_type=feature_type,
        current_user=current_user,
    )


@router.get("/status")
async def creative_suite_status():
    return await creative_suite_status_data()

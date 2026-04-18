"""Admin scoring route extraction from the legacy main app."""

import logging
from typing import Optional

from fastapi import Depends
from pydantic import BaseModel, Field

from auth.authentication import CurrentUser, require_role


logger = logging.getLogger(__name__)


class TestScoringRequest(BaseModel):
    query: str = Field(..., description="Query trademark name", min_length=1, max_length=200)
    target: str = Field(..., description="Target trademark name to compare", min_length=1, max_length=200)
    include_details: bool = Field(True, description="Include breakdown of scoring factors")


class TestScoringResponse(BaseModel):
    query: str
    target: str
    final_score: float
    final_score_pct: str
    risk_level: dict
    factors: Optional[dict] = None


async def test_scoring(
    request: TestScoringRequest,
    current_user: CurrentUser = Depends(require_role(["admin"])),
):
    """
    Test the multi-factor scoring system with any two trademark names.

    This endpoint helps verify scoring behavior for:
    - Word boundary matching (e.g., "dogan" vs "erdogan")
    - Length ratio penalty
    - Distinctive word coverage
    - IDF weighting

    Example test cases:
    - "dogan patent" vs "erdogan patent ofisi" = LOW (prefix mismatch)
    - "dogan patent" vs "d.p dogan patent" = HIGH (distinctive match)
    - "nike" vs "nike sports" = HIGH (containment)
    """
    from services.admin_scoring_service import run_admin_score_test

    payload = await run_admin_score_test(
        query=request.query,
        target=request.target,
        include_details=request.include_details,
        logger=logger,
    )
    return TestScoringResponse(**payload)


def register_admin_scoring_routes(app):
    """Register extracted admin scoring routes on the app."""
    app.add_api_route(
        "/api/admin/test-scoring",
        test_scoring,
        methods=["POST"],
        response_model=TestScoringResponse,
        tags=["Admin"],
    )

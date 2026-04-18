"""Legacy rollback search route extraction from the legacy main app."""

from fastapi import Request


async def legacy_text_search_impl(
    search_request,
    settings,
    normalize_turkish_fn,
    score_calculator,
    max_results,
):
    """Run the legacy text search path used for rollback/regression checks."""
    from services.search_service import run_legacy_rollback_search

    return await run_legacy_rollback_search(
        search_request=search_request,
        settings=settings,
        normalize_turkish_fn=normalize_turkish_fn,
        score_calculator=score_calculator,
        max_results=max_results,
    )


def register_legacy_rollback_routes(
    app,
    limiter,
    rate_limit,
    search_request_model,
    settings,
    normalize_turkish_fn,
    score_calculator,
    max_results,
):
    """Register the extracted legacy rollback route."""

    @limiter.limit(rate_limit)
    async def legacy_text_search(request: Request, search_request: search_request_model):
        """
        Temporary legacy rollback endpoint for text search.
        Always uses calculate_comprehensive_score() regardless of feature flag.
        Remove after 2026-03-10.
        """
        return await legacy_text_search_impl(
            search_request=search_request,
            settings=settings,
            normalize_turkish_fn=normalize_turkish_fn,
            score_calculator=score_calculator,
            max_results=max_results,
        )

    app.add_api_route(
        "/api/v1/search/legacy",
        legacy_text_search,
        methods=["POST"],
        tags=["Legacy"],
    )

    return legacy_text_search

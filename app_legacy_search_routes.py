"""Deprecated utility search route extraction from the legacy main app."""

from typing import Optional

from fastapi import Depends, File, Form, Query, Request, UploadFile

from auth.authentication import CurrentUser, get_current_user


async def simple_search_impl(
    request: Request,
    q: str,
    limit: int,
    search_request_factory,
    enhanced_search_handler,
    risk_level_getter,
    logger,
):
    """Legacy simple search adapter over the enhanced search flow."""
    from services.search_service import run_legacy_simple_search

    return await run_legacy_simple_search(
        request=request,
        q=q,
        limit=limit,
        search_request_factory=search_request_factory,
        enhanced_search_handler=enhanced_search_handler,
        risk_level_getter=risk_level_getter,
        logger=logger,
    )


async def unified_search_impl(
    request: Request,
    name: Optional[str],
    image: Optional[UploadFile],
    classes: Optional[str],
    goods_description: Optional[str],
    limit: int,
    search_request_factory,
    enhanced_search_handler,
    search_by_image_handler,
    risk_level_getter,
    logger,
):
    """Legacy unified search adapter for deprecated form-based consumers."""
    from services.search_service import run_legacy_unified_search

    return await run_legacy_unified_search(
        request=request,
        name=name,
        image=image,
        classes=classes,
        goods_description=goods_description,
        limit=limit,
        search_request_factory=search_request_factory,
        enhanced_search_handler=enhanced_search_handler,
        search_by_image_handler=search_by_image_handler,
        risk_level_getter=risk_level_getter,
        logger=logger,
    )


def register_legacy_search_utility_routes(
    app,
    limiter,
    rate_limit_getter,
    max_results,
    search_request_factory,
    enhanced_search_handler,
    search_by_image_handler,
    risk_level_getter,
    logger,
):
    """Register deprecated utility search routes on the app."""

    @limiter.limit(lambda: rate_limit_getter("rate_limit.public_search", "10/minute"))
    async def simple_search(
        request: Request,
        q: str = Query(..., description="Trademark name to search"),
        limit: int = Query(max_results, ge=1, le=max_results, description="Number of results (max 10)"),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        return await simple_search_impl(
            request=request,
            q=q,
            limit=limit,
            search_request_factory=search_request_factory,
            enhanced_search_handler=enhanced_search_handler,
            risk_level_getter=risk_level_getter,
            logger=logger,
        )

    @limiter.limit(lambda: rate_limit_getter("rate_limit.public_search", "10/minute"))
    async def unified_search(
        request: Request,
        name: Optional[str] = Form(None),
        image: Optional[UploadFile] = File(None),
        classes: Optional[str] = Form(None),
        goods_description: Optional[str] = Form(None),
        limit: int = Form(max_results),
    ):
        return await unified_search_impl(
            request=request,
            name=name,
            image=image,
            classes=classes,
            goods_description=goods_description,
            limit=limit,
            search_request_factory=search_request_factory,
            enhanced_search_handler=enhanced_search_handler,
            search_by_image_handler=search_by_image_handler,
            risk_level_getter=risk_level_getter,
            logger=logger,
        )

    app.add_api_route(
        "/api/search/simple",
        simple_search,
        methods=["GET"],
        tags=["Search"],
        deprecated=True,
    )
    app.add_api_route(
        "/api/search/unified",
        unified_search,
        methods=["POST"],
        tags=["Unified Search"],
        deprecated=True,
    )

    return simple_search, unified_search

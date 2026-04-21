"""Public search route extraction from the legacy main app."""

import io
import os
import tempfile
from typing import Optional

from fastapi import File, Form, HTTPException, Query, Request, Response, UploadFile
from PIL import Image


async def do_public_search_impl(
    query: str,
    image_path: str = None,
    nice_classes: list = None,
    status_code_getter=None,
    logger=None,
):
    """Shared implementation for GET and POST public search."""
    from services.search_service import run_public_search

    try:
        return await run_public_search(
            query=query,
            image_path=image_path,
            nice_classes=nice_classes,
            status_code_getter=status_code_getter,
            logger=logger,
        )
    except Exception as e:
        if logger:
            logger.error(f"Public search failed: {e}")
        raise HTTPException(status_code=500, detail="Search temporarily unavailable")


async def public_search_post_impl(
    query: Optional[str],
    image: Optional[UploadFile],
    classes: Optional[str],
    do_public_search_handler,
    allowed_image_types,
    max_image_size,
    validate_image_magic_bytes,
):
    """POST public search logic with optional image upload."""
    has_image = image is not None and image.filename
    has_query = query and len(query.strip()) >= 2
    if not has_query and not has_image:
        raise HTTPException(status_code=422, detail="Provide a brand name (min 2 chars) or upload a logo image")
    query = query.strip() if query else ""

    class_list = None
    if classes:
        try:
            class_list = [int(c.strip()) for c in classes.split(",") if c.strip()]
            class_list = [c for c in class_list if 1 <= c <= 45] or None
        except ValueError:
            class_list = None

    temp_path = None
    has_image = image is not None and image.filename
    if has_image:
        if image.content_type not in allowed_image_types:
            raise HTTPException(status_code=400, detail="Invalid image type")
        content = await image.read()
        if len(content) > max_image_size:
            raise HTTPException(status_code=413, detail="Image too large (max 10MB)")
        if not validate_image_magic_bytes(content):
            raise HTTPException(status_code=400, detail="Invalid image file content")
        try:
            Image.open(io.BytesIO(content)).verify()
        except Exception:
            raise HTTPException(status_code=400, detail="Corrupted image file")
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        temp_file.write(content)
        temp_file.close()
        temp_path = temp_file.name

    try:
        return await do_public_search_handler(
            query=query,
            image_path=temp_path,
            nice_classes=class_list,
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def register_public_search_routes(
    app,
    limiter,
    logger,
    status_code_getter,
    allowed_image_types,
    max_image_size,
    validate_image_magic_bytes,
    rate_limit_getter=None,
):
    """Register the extracted public search routes on the app."""
    from database.crud import Database
    from services.search_service import (
        PUBLIC_SEARCH_CLIENT_COOKIE,
        PUBLIC_SEARCH_COOKIE_MAX_AGE_SECONDS,
        check_public_search_eligibility,
        get_public_search_daily_limit,
        increment_public_search_usage,
        resolve_public_search_client_id,
    )

    def _public_rate_limit() -> str:
        configured = "10/minute"
        if rate_limit_getter is not None:
            configured = rate_limit_getter("rate_limit.public_search", "10/minute")

        try:
            configured_count = int(str(configured).split("/", 1)[0])
        except (TypeError, ValueError):
            configured_count = 10

        minimum_count = max(10, get_public_search_daily_limit() + 1)
        return f"{max(configured_count, minimum_count)}/minute"

    def _set_public_search_cookie(response: Response, client_id: str) -> None:
        response.set_cookie(
            key=PUBLIC_SEARCH_CLIENT_COOKIE,
            value=client_id,
            max_age=PUBLIC_SEARCH_COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def _enforce_public_search_limit(request: Request, response: Response):
        client_id, should_set_cookie = resolve_public_search_client_id(request)
        if should_set_cookie:
            _set_public_search_cookie(response, client_id)

        with Database() as db:
            allowed, _, detail = check_public_search_eligibility(db, client_id)

        if not allowed:
            raise HTTPException(status_code=429, detail=detail)

        return client_id

    def _record_public_search_usage(client_id: str) -> None:
        with Database() as db:
            increment_public_search_usage(db, client_id)

    async def _do_public_search(
        query: str,
        image_path: str = None,
        nice_classes: list = None,
    ):
        return await do_public_search_impl(
            query=query,
            image_path=image_path,
            nice_classes=nice_classes,
            status_code_getter=status_code_getter,
            logger=logger,
        )

    @limiter.limit(_public_rate_limit)
    async def public_search(
        request: Request,
        response: Response,
        query: str = Query(..., min_length=2, max_length=100, description="Trademark name to search"),
    ):
        """
        Public (unauthenticated) trademark search for landing page.
        Uses the free-tier daily quota plus a short-term public throttle.
        Returns max 10 results with limited fields.
        """
        client_id = _enforce_public_search_limit(request, response)
        payload = await _do_public_search(query=query)
        _record_public_search_usage(client_id)
        return payload

    @limiter.limit(_public_rate_limit)
    async def public_search_post(
        request: Request,
        response: Response,
        query: Optional[str] = Form(None, max_length=100, description="Trademark name to search"),
        image: Optional[UploadFile] = File(None, description="Optional logo image for visual search"),
        classes: Optional[str] = Form(None, description="Nice classes, comma-separated (e.g. 9,35,42)"),
    ):
        """
        Public (unauthenticated) trademark search with optional image upload.
        At least one of query or image must be provided.
        Uses the free-tier daily quota plus a short-term public throttle.
        Returns max 10 results with limited fields.
        """
        client_id = _enforce_public_search_limit(request, response)
        payload = await public_search_post_impl(
            query=query,
            image=image,
            classes=classes,
            do_public_search_handler=_do_public_search,
            allowed_image_types=allowed_image_types,
            max_image_size=max_image_size,
            validate_image_magic_bytes=validate_image_magic_bytes,
        )
        _record_public_search_usage(client_id)
        return payload

    app.add_api_route(
        "/api/v1/search/public",
        public_search,
        methods=["GET"],
        tags=["Search"],
    )
    app.add_api_route(
        "/api/v1/search/public",
        public_search_post,
        methods=["POST"],
        tags=["Search"],
    )

    return public_search, public_search_post, _do_public_search

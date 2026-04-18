"""Image-search route extraction from the legacy main app."""

from typing import Optional

from fastapi import File, Form, Request, UploadFile


async def search_by_image_impl(
    image,
    name,
    classes,
    limit,
    process_uploaded_image_handler,
    settings,
    logger,
    global_class,
    score_pair_fn,
    visual_similarity_fn,
    risk_level_getter,
    encode_query_image_handler,
    get_image_embedding_handler,
    extract_ocr_text_handler,
):
    """Run the extracted image-search flow used by the public upload endpoint."""
    from services.search_service import run_image_search

    return await run_image_search(
        image=image,
        name=name,
        classes=classes,
        limit=limit,
        process_uploaded_image_handler=process_uploaded_image_handler,
        settings=settings,
        logger=logger,
        global_class=global_class,
        score_pair_fn=score_pair_fn,
        visual_similarity_fn=visual_similarity_fn,
        risk_level_getter=risk_level_getter,
        encode_query_image_handler=encode_query_image_handler,
        get_image_embedding_handler=get_image_embedding_handler,
        extract_ocr_text_handler=extract_ocr_text_handler,
    )


def register_image_search_routes(
    app,
    limiter,
    rate_limit_getter,
    max_results,
    process_uploaded_image_handler,
    settings,
    logger,
    global_class,
    score_pair_fn,
    visual_similarity_fn,
    risk_level_getter,
    encode_query_image_handler,
    get_image_embedding_handler,
    extract_ocr_text_handler,
):
    """Register the extracted image-search route on the app."""

    @limiter.limit(lambda: rate_limit_getter("rate_limit.public_search", "10/minute"))
    async def search_by_image(
        request: Request,
        image: UploadFile = File(..., description="Aranacak logo/marka gorseli"),
        name: Optional[str] = Form(None, description="Optional trademark name for combined search"),
        classes: Optional[str] = Form(
            None,
            description="Nice siniflari (virgulle ayrilmis, orn: 9,35,42)",
        ),
        limit: int = Form(max_results, description=f"Maksimum sonuc sayisi (max {max_results})"),
    ):
        """
        Search for similar trademarks by uploading an image.
        Routes through score_pair() for unified scoring.

        Supports image-only and image+text (combined) modes.
        """
        return await search_by_image_impl(
            image=image,
            name=name,
            classes=classes,
            limit=limit,
            process_uploaded_image_handler=process_uploaded_image_handler,
            settings=settings,
            logger=logger,
            global_class=global_class,
            score_pair_fn=score_pair_fn,
            visual_similarity_fn=visual_similarity_fn,
            risk_level_getter=risk_level_getter,
            encode_query_image_handler=encode_query_image_handler,
            get_image_embedding_handler=get_image_embedding_handler,
            extract_ocr_text_handler=extract_ocr_text_handler,
        )

    app.add_api_route(
        "/api/search-by-image",
        search_by_image,
        methods=["POST"],
        tags=["Image Search"],
    )

    return search_by_image

"""Enhanced search route extraction from the legacy main app."""

from typing import Any, Dict, List, Optional

from fastapi import Request
from pydantic import BaseModel, Field
from pipeline.ingest_rules import _repair_mojibake


class SearchRequest(BaseModel):
    """Enhanced search request with auto class suggestion and optional image URL."""

    name: str = Field(..., min_length=1, description="Trademark name to search")
    classes: Optional[List[int]] = Field(
        None,
        description="Manually selected Nice classes (1-45)",
    )
    goods_description: Optional[str] = Field(
        None,
        min_length=10,
        description="Plain text description of goods/services for auto class suggestion",
    )
    auto_suggest_classes: bool = Field(
        default=True,
        description="If true and no classes provided, auto-suggest based on goods_description",
    )
    include_suggested_in_response: bool = Field(
        default=True,
        description="Include class suggestion details in response",
    )
    image_url: Optional[str] = Field(
        None,
        description="Optional image URL for combined text+image search",
    )
    attorney_no: Optional[str] = Field(None, description="Filter by attorney number")
    limit: int = Field(20, ge=1, le=100, description="Maximum number of results")


class AutoSuggestedClass(BaseModel):
    """Class that was auto-suggested for the search."""

    class_number: int
    class_name: str
    similarity_score: float


class TrademarkResult(BaseModel):
    """Enhanced search result with all detail fields for expandable view."""

    id: str = Field(..., description="Unique identifier for the trademark")
    name: str = Field(..., description="Trademark name/text")
    application_no: str = Field(..., description="Application number")
    application_date: Optional[str] = Field(None, description="Application date (YYYY-MM-DD)")
    registration_date: Optional[str] = Field(None, description="Registration date if registered")
    status: str = Field(..., description="Human-readable status (Tescilli, Başvuru, etc.)")
    status_code: str = Field(
        default="unknown",
        description="Status code (registered, pending, rejected, published)",
    )
    nice_classes: List[int] = Field(default=[], description="List of Nice class numbers")
    owner: Optional[str] = Field(None, description="Trademark owner/applicant name")
    holder_tpe_client_id: Optional[str] = Field(None, description="Holder TPE Client ID")
    attorney: Optional[str] = Field(None, description="Patent attorney/representative name")
    attorney_no: Optional[str] = Field(None, description="Patent attorney number (unique ID)")
    registration_no: Optional[str] = Field(None, description="Registration number")
    bulletin_no: Optional[str] = Field(None, description="Publication bulletin number")
    image_url: Optional[str] = Field(None, description="URL to trademark image")
    similarity: float = Field(..., ge=0, le=100, description="Overall similarity percentage")
    name_similarity: Optional[float] = Field(None, description="Text/name similarity (0-100)")
    text_similarity: Optional[float] = Field(None, description="Raw direct text similarity (0-1)")
    text_idf_score: Optional[float] = Field(None, description="Selected V2 textual path score (0-1)")
    path_a_score: Optional[float] = Field(None, description="Original-name textual path score (0-1)")
    path_b_score: Optional[float] = Field(None, description="Translated-name textual path score (0-1)")
    translation_similarity: Optional[float] = Field(None, description="Displayed translation path score (0-1)")
    scoring_path_source: Optional[str] = Field(None, description="Selected scoring source")
    scores: Optional[Dict[str, Any]] = Field(None, description="Canonical scoring diagnostics")
    class_overlap_count: int = Field(
        default=0,
        description="Number of overlapping classes with search",
    )


class SearchContext(BaseModel):
    """Context about the search that was performed."""

    searched_name: str
    searched_classes: List[int] = []
    goods_description: Optional[str] = None
    total_results: int
    search_time_ms: float


class EnhancedSearchResponse(BaseModel):
    """Enhanced search response with results and context."""

    results: List[TrademarkResult]
    search_context: SearchContext
    query: str
    total_results: int
    search_time_ms: float
    search_classes: List[int]
    classes_were_auto_suggested: bool
    auto_suggested_classes: Optional[List[AutoSuggestedClass]] = None
    suggestion_query: Optional[str] = None


def format_date(date_val) -> Optional[str]:
    """Format date to string (YYYY-MM-DD)."""
    if date_val is None:
        return None
    if isinstance(date_val, str):
        return date_val
    try:
        return date_val.strftime("%Y-%m-%d")
    except Exception:
        return str(date_val)


def get_status_code(status_text: Optional[str]) -> str:
    """Convert Turkish status to standardized code."""
    if not status_text:
        return "unknown"

    status_text = _repair_mojibake(status_text)
    status_map = {
        "Tescil Edildi": "registered",
        "Tescilli": "registered",
        "Tescil": "registered",
        "Yayında": "published",
        "Yayın": "published",
        "Başvuruldu": "pending",
        "Başvuru": "pending",
        "İnceleme": "pending",
        "İncelemede": "pending",
        "Reddedildi": "rejected",
        "Red": "rejected",
        "İptal Edildi": "cancelled",
        "İptal": "cancelled",
        "Süresi Doldu": "expired",
        "Geri Çekildi": "withdrawn",
        "İtiraz Edildi": "opposed",
        "Yenilendi": "renewed",
        "Kısmi Red": "partial_refusal",
        "Devredildi": "transferred",
        "Bilinmiyor": "unknown",
    }
    return status_map.get(status_text, "unknown")


def get_image_url(
    image_path: Optional[str],
    application_no: str,
    bulletin_no: Optional[str] = None,
) -> Optional[str]:
    """Get image URL for trademark using the image serving endpoint."""
    if image_path:
        return f"/api/trademark-image/{image_path}"
    if application_no:
        safe_app_no = application_no.replace("/", "_")
        return f"/api/trademark-image/{safe_app_no}"
    return None


async def get_class_suggestions_internal(
    goods_description: str,
    trademark_name: str = None,
    limit: int = 5,
    settings=None,
    logger=None,
    class_name_lookup=None,
) -> List[dict]:
    """
    Internal helper to get class suggestions without going through HTTP.
    Returns list of dicts with class_number, class_name, similarity.
    """
    try:
        from services.nice_class_service import run_nice_class_suggestion

        lookup = class_name_lookup or {}

        def class_name_getter(class_num, current_lang="tr"):
            return lookup.get(class_num) or lookup.get(str(class_num)) or f"Class {class_num}"

        payload = await run_nice_class_suggestion(
            description=goods_description,
            top_k=limit,
            lang="tr",
            settings=settings,
            logger=logger,
            class_name_getter=class_name_getter,
            trademark_name=trademark_name,
        )
        return payload["suggestions"]

    except Exception as exc:
        if logger:
            logger.error(f"Internal class suggestion error: {exc}")
        return []


async def enhanced_search_impl(
    search_request,
    settings,
    logger,
    normalize_turkish_fn,
    score_pair_fn,
    visual_similarity_fn,
    class_suggestions_handler,
    text_embedding_getter,
    encode_query_image_handler,
    date_formatter=format_date,
    status_code_getter=get_status_code,
    image_url_getter=get_image_url,
):
    """Run the extracted enhanced search flow."""
    from services.search_service import run_enhanced_search

    payload = await run_enhanced_search(
        search_request=search_request,
        settings=settings,
        logger=logger,
        normalize_turkish_fn=normalize_turkish_fn,
        score_pair_fn=score_pair_fn,
        visual_similarity_fn=visual_similarity_fn,
        class_suggestions_handler=class_suggestions_handler,
        text_embedding_getter=text_embedding_getter,
        encode_query_image_handler=encode_query_image_handler,
        date_formatter=date_formatter,
        status_code_getter=status_code_getter,
        image_url_getter=image_url_getter,
    )
    return EnhancedSearchResponse(**payload)


def register_enhanced_search_routes(
    app,
    limiter,
    rate_limit,
    settings,
    logger,
    normalize_turkish_fn,
    score_pair_fn,
    visual_similarity_fn,
    class_name_lookup,
    encode_query_image_handler,
):
    """Register the extracted enhanced search route on the app."""

    @limiter.limit(rate_limit)
    async def enhanced_search(request: Request, search_request: SearchRequest):
        from pipeline.ai import get_text_embedding_cached

        return await enhanced_search_impl(
            search_request=search_request,
            settings=settings,
            logger=logger,
            normalize_turkish_fn=normalize_turkish_fn,
            score_pair_fn=score_pair_fn,
            visual_similarity_fn=visual_similarity_fn,
            class_suggestions_handler=lambda goods_description, trademark_name=None, limit=5: get_class_suggestions_internal(
                goods_description=goods_description,
                trademark_name=trademark_name,
                limit=limit,
                settings=settings,
                logger=logger,
                class_name_lookup=class_name_lookup,
            ),
            text_embedding_getter=get_text_embedding_cached,
            encode_query_image_handler=encode_query_image_handler,
        )

    app.add_api_route(
        "/api/search",
        enhanced_search,
        methods=["POST"],
        response_model=EnhancedSearchResponse,
        tags=["Enhanced Search"],
    )

    return enhanced_search

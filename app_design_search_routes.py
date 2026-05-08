"""Tasarım (industrial design) search routes.

Three endpoints:
  * ``POST /api/v1/design-search/quick``  — authenticated, full results
  * ``GET/POST /api/v1/design-search/public`` — anonymous, max 10 results
  * ``GET /api/v1/design-image/{path:path}`` — serve view JPEGs

The image-bearing endpoints reuse the embedding pipeline's model loaders to
encode the uploaded image at request time, so the same DINOv2 / CLIP / HSV
representations the corpus was indexed with are used at query time.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parent
DESIGN_BULLETINS_ROOT = PROJECT_ROOT / "bulletins" / "Tasarim"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

logger = logging.getLogger("turkpatent.design_search_routes")


# ---------------------------------------------------------------------------
# Image embedding (lazy model load shared across requests)
# ---------------------------------------------------------------------------

_MODEL_BUNDLE = None  # cached LoadedModels from embeddings_tasarim


def _embed_query_image(temp_path: str) -> dict:
    """Encode an uploaded image with the same models the corpus was indexed
    with. Returns a dict with dinov2_vitl14 / clip_vitb32 / color_hsv lists.
    """
    global _MODEL_BUNDLE
    from embeddings_tasarim import detect_device, embed_image, load_models
    if _MODEL_BUNDLE is None:
        _MODEL_BUNDLE = load_models(detect_device())
    return embed_image(Path(temp_path), _MODEL_BUNDLE)


def _save_upload_to_temp(content: bytes) -> str:
    if len(content) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 10MB)")
    try:
        Image.open(io.BytesIO(content)).verify()
    except Exception:
        raise HTTPException(status_code=400, detail="Corrupted image file")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    tmp.write(content)
    tmp.close()
    return tmp.name


def _parse_locarno_param(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    return [v.strip() for v in raw.split(",") if v.strip()]


# ---------------------------------------------------------------------------
# Image-serving route
# ---------------------------------------------------------------------------

def _resolve_design_image(image_path: str) -> Optional[str]:
    if not image_path or ".." in image_path:
        return None
    candidate = (DESIGN_BULLETINS_ROOT / image_path.replace("/", os.sep)).resolve()
    try:
        candidate.relative_to(DESIGN_BULLETINS_ROOT.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return str(candidate)


def design_image_response(image_path: str) -> FileResponse:
    full = _resolve_design_image(image_path)
    if not full:
        raise HTTPException(status_code=404, detail="Design image not found")
    suffix = os.path.splitext(full)[1].lower()
    media_type = MEDIA_TYPES.get(suffix, "application/octet-stream")
    return FileResponse(full, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------------------
# Search route handlers
# ---------------------------------------------------------------------------

async def _do_design_search(
    *,
    query: Optional[str],
    image_temp_path: Optional[str],
    locarno_classes: Optional[List[str]],
    limit: int,
    public: bool,
) -> dict:
    from database.crud import Database
    from services.design_search_service import search_designs

    image_embeddings = None
    if image_temp_path:
        image_embeddings = _embed_query_image(image_temp_path)

    db = Database()
    with db.get_connection() as conn:
        result = search_designs(
            conn,
            query=query,
            image_embeddings=image_embeddings,
            locarno_classes=locarno_classes,
            limit=limit,
            public=public,
        )
    return result


async def design_search_quick(
    *,
    query: Optional[str],
    image: Optional[UploadFile],
    locarno: Optional[str],
    limit: int,
    user_id: Optional[str] = None,
) -> dict:
    """Authenticated full-detail search. Caller is responsible for auth + quota."""
    has_image = image is not None and image.filename
    has_query = bool(query and query.strip())
    if not (has_image or has_query):
        raise HTTPException(status_code=422, detail="Provide a product name (min 2 chars) or upload a design image")

    temp_path: Optional[str] = None
    if has_image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid image type")
        content = await image.read()
        temp_path = _save_upload_to_temp(content)
    try:
        return await _do_design_search(
            query=query.strip() if query else None,
            image_temp_path=temp_path,
            locarno_classes=_parse_locarno_param(locarno),
            limit=limit,
            public=False,
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


async def design_search_public(
    *,
    query: Optional[str],
    locarno: Optional[str],
) -> dict:
    """Public anonymous text-only search. Capped at 10 results."""
    if not query or len(query.strip()) < 2:
        raise HTTPException(status_code=422, detail="Provide a product name (min 2 chars)")
    return await _do_design_search(
        query=query.strip(),
        image_temp_path=None,
        locarno_classes=_parse_locarno_param(locarno),
        limit=10,
        public=True,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_design_search_routes(app, limiter, get_current_user_dep):
    """Register the three design routes on the app.

    ``limiter`` is the slowapi.Limiter instance used by the rest of the app.
    ``get_current_user_dep`` is the FastAPI dependency that decodes the
    Bearer token and returns ``{user_id, ...}`` (mirrors the trademark
    routes' auth wiring).
    """

    @app.get("/api/v1/design-image/{image_path:path}")
    async def get_design_image(image_path: str):
        return design_image_response(image_path)

    @app.get("/api/v1/design-search/public")
    @limiter.limit("10/minute")
    async def public_design_search_get(
        request: Request,
        query: str = Query(..., min_length=2, max_length=100),
        locarno: Optional[str] = Query(None),
    ):
        return await design_search_public(query=query, locarno=locarno)

    @app.post("/api/v1/design-search/public")
    @limiter.limit("10/minute")
    async def public_design_search_post(
        request: Request,
        query: Optional[str] = Form(None),
        locarno: Optional[str] = Form(None),
    ):
        return await design_search_public(query=query, locarno=locarno)

    @app.post("/api/v1/design-search/quick")
    @limiter.limit("60/minute")
    async def quick_design_search(
        request: Request,
        query: Optional[str] = Form(None),
        image: Optional[UploadFile] = File(None),
        locarno: Optional[str] = Form(None),
        limit: int = Form(20),
        current_user: dict = get_current_user_dep,
    ):
        return await design_search_quick(
            query=query, image=image, locarno=locarno, limit=limit,
            user_id=current_user.get("user_id") if current_user else None,
        )

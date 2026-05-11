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

from fastapi import Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
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
    root = DESIGN_BULLETINS_ROOT.resolve()

    def _check(rel: str) -> Optional[str]:
        candidate = (DESIGN_BULLETINS_ROOT / rel.replace("/", os.sep)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return str(candidate) if candidate.is_file() else None

    # 1. Literal lookup (works for rows whose image_path already includes
    # the correct subdir prefix).
    hit = _check(image_path)
    if hit:
        return hit

    # 2. Fallback for rows ingested before the D.1 CD-first refactor
    # (commit a8de7f3e), where image_path was stored without the
    # cd_images/ or images/ subdir prefix. DB-stored paths look like
    # "{source_folder}/{design_id}/{view}.jpg"; the real files live at
    # "{source_folder}/cd_images/{design_id}/{view}.jpg" (CD-sourced)
    # or "{source_folder}/images/{design_id}/{view}.jpg" (PDF-sourced).
    parts = image_path.split("/", 1)
    if len(parts) == 2:
        source_folder, rest = parts
        for subdir in ("cd_images", "images"):
            hit = _check(f"{source_folder}/{subdir}/{rest}")
            if hit:
                return hit
    return None


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

    with Database() as db:
        return search_designs(
            db.conn,
            query=query,
            image_embeddings=image_embeddings,
            locarno_classes=locarno_classes,
            limit=limit,
            public=public,
        )


async def design_search_quick(
    *,
    query: Optional[str],
    image: Optional[UploadFile],
    locarno: Optional[str],
    limit: int,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> dict:
    """Authenticated full-detail search.

    Counts against the same daily ``max_daily_quick_searches`` quota the
    trademark quick search uses. Over-limit returns 429 with the upgrade-hint
    payload. Increment happens AFTER a successful search so failed retrievals
    don't burn the user's quota.
    """
    has_image = image is not None and image.filename
    has_query = bool(query and query.strip())
    if not (has_image or has_query):
        raise HTTPException(status_code=422, detail="Provide a product name (min 2 chars) or upload a design image")

    # Daily quota check (mirrors agentic_search.py:954-961)
    if user_id:
        from database.crud import Database
        from utils.subscription import check_quick_search_eligibility
        with Database() as db:
            can_search, reason, details = check_quick_search_eligibility(db, user_id)
            if not can_search:
                raise HTTPException(status_code=429, detail=details)

    temp_path: Optional[str] = None
    if has_image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid image type")
        content = await image.read()
        temp_path = _save_upload_to_temp(content)
    try:
        result = await _do_design_search(
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

    # Increment AFTER successful retrieval — failed searches don't burn quota
    if user_id:
        from database.crud import Database
        from utils.subscription import increment_quick_search_usage
        with Database() as db:
            increment_quick_search_usage(db, user_id, organization_id)

    return result


async def design_search_public(
    *,
    query: Optional[str],
    image: Optional[UploadFile] = None,
    locarno: Optional[str] = None,
) -> dict:
    """Public anonymous design search. Capped at 10 results.

    Mirrors the authenticated quick endpoint's input surface (text +
    image + Locarno chips) so landing-page searches behave identically
    to the dashboard. Visual-dominant ranking via DINOv2 + CLIP + HSV
    runs the same way on the public path.
    """
    has_image = image is not None and image.filename
    has_query = bool(query and query.strip())
    if not has_query and not has_image:
        raise HTTPException(
            status_code=422,
            detail="Provide a product name (min 2 chars) or upload a design image",
        )

    temp_path: Optional[str] = None
    if has_image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid image type")
        content = await image.read()
        temp_path = _save_upload_to_temp(content)
    try:
        return await _do_design_search(
            query=query.strip() if has_query else None,
            image_temp_path=temp_path,
            locarno_classes=_parse_locarno_param(locarno),
            limit=10,
            public=True,
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_design_search_routes(app, limiter):
    """Register the three design routes on the app.

    ``limiter`` is the slowapi.Limiter instance used by the rest of the app.
    Auth is wired internally via ``Depends(get_current_user)`` from
    ``auth.authentication`` to match the existing trademark-route conventions.
    """
    from app_public_search_quota import (
        enforce_public_search_quota,
        record_public_search_usage,
    )
    from auth.authentication import get_current_user

    @app.get("/api/v1/design-image/{image_path:path}", tags=["Design Search"])
    async def get_design_image(image_path: str):
        return design_image_response(image_path)

    @app.get("/api/v1/design-search/public", tags=["Design Search"])
    @limiter.limit("10/minute")
    async def public_design_search_get(
        request: Request,
        response: Response,
        query: str = Query(..., min_length=2, max_length=100),
        locarno: Optional[str] = Query(None),
    ):
        client_id = enforce_public_search_quota(request, response)
        payload = await design_search_public(query=query, locarno=locarno)
        record_public_search_usage(client_id)
        return payload

    @app.post("/api/v1/design-search/public", tags=["Design Search"])
    @limiter.limit("10/minute")
    async def public_design_search_post(
        request: Request,
        response: Response,
        query: Optional[str] = Form(None),
        image: Optional[UploadFile] = File(None),
        locarno: Optional[str] = Form(None),
    ):
        client_id = enforce_public_search_quota(request, response)
        payload = await design_search_public(
            query=query, image=image, locarno=locarno,
        )
        record_public_search_usage(client_id)
        return payload

    @app.post("/api/v1/design-search/quick", tags=["Design Search"])
    @limiter.limit("60/minute")
    async def quick_design_search(
        request: Request,
        query: Optional[str] = Form(None),
        image: Optional[UploadFile] = File(None),
        locarno: Optional[str] = Form(None),
        limit: int = Form(20),
        current_user=Depends(get_current_user),
    ):
        user_id = None
        org_id = None
        if current_user is not None:
            uid = getattr(current_user, "id", None) or getattr(current_user, "user_id", None)
            user_id = str(uid) if uid is not None else None
            oid = getattr(current_user, "organization_id", None)
            org_id = str(oid) if oid is not None else None
        return await design_search_quick(
            query=query, image=image, locarno=locarno, limit=limit,
            user_id=user_id, organization_id=org_id,
        )

    # ---- Locarno class catalogue + AI suggest ----

    @app.get("/api/v1/locarno-classes", tags=["Design Search"])
    @limiter.limit("60/minute")
    async def list_locarno_classes(request: Request):
        """Return the 32 top-level Locarno classes with localized names.

        Public endpoint, no auth, no cost. Cache-friendly: the lookup table
        only changes on schema bootstrap.
        """
        from database.crud import Database
        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT class_number, name_tr, name_en
                FROM locarno_classes_lookup
                ORDER BY class_number ASC
                """
            )
            rows = cur.fetchall()
        items = []
        for r in rows:
            items.append({
                "class_number": r["class_number"] if isinstance(r, dict) else r[0],
                "name_tr": r["name_tr"] if isinstance(r, dict) else r[1],
                "name_en": r["name_en"] if isinstance(r, dict) else r[2],
            })
        return {"items": items, "total": len(items)}

    from auth.authentication import get_current_user_optional
    from utils.anon_quota import (
        ANON_CLASS_SUGGEST_DAILY_LIMIT,
        _client_ip_from_request,
        check_and_consume_anon_class_suggest,
    )

    @app.post("/api/v1/tools/suggest-locarno-classes", tags=["Design Search"])
    @limiter.limit("20/minute")
    async def suggest_locarno_classes_route(
        request: Request,
        current_user=Depends(get_current_user_optional),
    ):
        """Locarno class suggestion. Mirrors the Nice public path:
          * Anonymous: ANON_CLASS_SUGGEST_DAILY_LIMIT calls per IP per day,
            then 401 with an upgrade-context payload.
          * Authenticated: AI-credits gate inside the service (1 credit per
            call from the shared monthly_ai_credits pool).
        """
        from services.locarno_suggest_service import (
            LocarnoSuggestionRequest,
            suggest_locarno_classes_data,
        )

        if current_user is None:
            ip = _client_ip_from_request(request)
            allowed, _remaining = check_and_consume_anon_class_suggest(ip)
            if not allowed:
                raise HTTPException(
                    status_code=401,
                    detail={
                        "error": "anon_limit_reached",
                        "upgrade_context": "class_suggestions",
                        "anon_daily_limit": ANON_CLASS_SUGGEST_DAILY_LIMIT,
                        "message": (
                            "Ücretsiz Locarno öneri hakkınız bugün için doldu. "
                            "Devam etmek için giriş yapın veya bir plana abone olun."
                        ),
                        "message_en": (
                            f"Anonymous daily limit ({ANON_CLASS_SUGGEST_DAILY_LIMIT}) "
                            "reached. Sign in or subscribe to a plan to continue."
                        ),
                    },
                )

        body = await request.json()
        try:
            payload = LocarnoSuggestionRequest(**body)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await suggest_locarno_classes_data(
            request=payload,
            current_user=current_user,
        )

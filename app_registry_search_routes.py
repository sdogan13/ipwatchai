"""Cross-registry unified search routes.

Three endpoints, mirroring the design and trademark search shape:
  * POST   /api/v1/registry-search/quick   — Bearer auth, full results
  * GET    /api/v1/registry-search/public  — anonymous, max 10
  * POST   /api/v1/registry-search/public  — same, form body

Image queries are encoded with **both** DINOv2 backbones so each registry
queries its own vector space correctly:
  - DINOv2 ViT-L/14 (1024-dim) → designs.dinov2_vitl14_mean
  - DINOv2 ViT-B/14 (768-dim)  → trademarks.dinov2_embedding
CLIP ViT-B/32 and the HSV histogram are shared across registries.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, File, Form, HTTPException, Query, Request, UploadFile
from PIL import Image


logger = logging.getLogger("turkpatent.registry_search_routes")

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


# ---------------------------------------------------------------------------
# Image encoding — design + trademark backbones in one pass
# ---------------------------------------------------------------------------

_UNIFIED_MODELS: Optional[Dict[str, Any]] = None


def _load_unified_image_models() -> Dict[str, Any]:
    """Load (and cache) the model bundle needed for unified image queries.

    Reuses the design embedding pipeline's ViT-L/14 + CLIP + transforms,
    and additionally loads the trademark-side ViT-B/14 from the existing
    ``ai`` module.
    """
    global _UNIFIED_MODELS
    if _UNIFIED_MODELS is not None:
        return _UNIFIED_MODELS

    from embeddings_tasarim import detect_device, load_models as load_design_models
    design_bundle = load_design_models(detect_device())

    # Trademark-side ViT-B/14 + its preprocess transform live in ``ai.py``.
    # Imported lazily so this whole route module doesn't trigger heavy AI
    # loading on app startup.
    import ai as _ai

    _UNIFIED_MODELS = {
        "design_bundle": design_bundle,
        "tm_dinov2_model": getattr(_ai, "dinov2_model", None),
        "tm_dinov2_preprocess": getattr(_ai, "dinov2_preprocess", None),
        "device": design_bundle.device,
    }
    return _UNIFIED_MODELS


def _encode_for_unified(temp_path: str) -> Dict[str, Dict[str, List[float]]]:
    """Encode the uploaded image with all four signals.

    Returns ``{"design": {...}, "trademark": {...}}`` so callers can pass each
    payload to its respective registry's retrieval branch.
    """
    from embeddings_tasarim import embed_image
    bundle = _load_unified_image_models()
    design_models = bundle["design_bundle"]
    design_emb = embed_image(Path(temp_path), design_models)

    # Trademark side: re-encode with ViT-B/14 if available, plus reuse the
    # CLIP + HSV from the design pipeline (same models on both sides).
    trademark_emb: Dict[str, List[float]] = {
        "clip_vitb32": design_emb.get("clip_vitb32", []),
        "color_hsv": design_emb.get("color_hsv", []),
    }
    tm_dinov2_model = bundle.get("tm_dinov2_model")
    tm_preprocess = bundle.get("tm_dinov2_preprocess")
    if tm_dinov2_model is not None and tm_preprocess is not None:
        try:
            import torch
            from PIL import Image as _Image
            img = _Image.open(temp_path).convert("RGB")
            tensor = tm_preprocess(img).unsqueeze(0).to(bundle["device"])
            with torch.no_grad():
                feat = tm_dinov2_model(tensor)
            trademark_emb["dinov2_vitb14"] = feat.squeeze(0).cpu().float().tolist()
        except Exception as e:
            logger.warning("trademark ViT-B/14 encoding failed: %r", e)

    return {"design": design_emb, "trademark": trademark_emb}


# ---------------------------------------------------------------------------
# Upload handling
# ---------------------------------------------------------------------------

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


def _parse_csv_param(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    return [v.strip() for v in raw.split(",") if v.strip()]


def _parse_registries_param(raw: Optional[str]) -> Optional[List[str]]:
    return _parse_csv_param(raw)


# ---------------------------------------------------------------------------
# Search handlers
# ---------------------------------------------------------------------------

async def _do_unified_search(
    *,
    query: Optional[str],
    image_temp_path: Optional[str],
    nice_classes: Optional[List[int]],
    locarno_classes: Optional[List[str]],
    registries: Optional[List[str]],
    limit: int,
    public: bool,
) -> Dict[str, Any]:
    from database.crud import Database
    from services.registry_search_service import search_unified

    image_design = None
    image_trademark = None
    if image_temp_path:
        encoded = _encode_for_unified(image_temp_path)
        image_design = encoded.get("design")
        image_trademark = encoded.get("trademark")

    with Database() as db:
        return search_unified(
            db.conn,
            query=query,
            image_embeddings_design=image_design,
            image_embeddings_trademark=image_trademark,
            nice_classes=nice_classes,
            locarno_classes=locarno_classes,
            registries=registries,
            limit=limit,
            public=public,
        )


async def registry_search_authenticated(
    *,
    query: Optional[str],
    image: Optional[UploadFile],
    nice: Optional[str],
    locarno: Optional[str],
    registries: Optional[str],
    limit: int,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Authenticated full-detail unified search across both registries."""
    has_image = image is not None and image.filename
    has_query = bool(query and query.strip())
    if not (has_image or has_query):
        raise HTTPException(status_code=422, detail="Provide a search term (min 2 chars) or upload an image")

    if user_id:
        from database.crud import Database
        from utils.subscription import check_live_search_eligibility
        with Database() as db:
            can_search, reason, details = check_live_search_eligibility(db, user_id)
            if not can_search:
                raise HTTPException(status_code=429, detail=details)

    nice_list = None
    if nice:
        try:
            nice_list = [int(c.strip()) for c in nice.split(",") if c.strip()]
        except ValueError:
            nice_list = None
    locarno_list = _parse_csv_param(locarno)
    registries_list = _parse_registries_param(registries)

    temp_path: Optional[str] = None
    if has_image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid image type")
        content = await image.read()
        temp_path = _save_upload_to_temp(content)
    try:
        result = await _do_unified_search(
            query=query.strip() if query else None,
            image_temp_path=temp_path,
            nice_classes=nice_list,
            locarno_classes=locarno_list,
            registries=registries_list,
            limit=limit,
            public=False,
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    if user_id:
        from database.crud import Database
        from utils.subscription import increment_live_search_usage
        with Database() as db:
            increment_live_search_usage(db, user_id, organization_id)

    return result


async def registry_search_public(
    *,
    query: Optional[str],
    nice: Optional[str],
    locarno: Optional[str],
    registries: Optional[str],
) -> Dict[str, Any]:
    """Public anonymous text-only unified search. Capped at 10 results."""
    if not query or len(query.strip()) < 2:
        raise HTTPException(status_code=422, detail="Provide a search term (min 2 chars)")
    nice_list = None
    if nice:
        try:
            nice_list = [int(c.strip()) for c in nice.split(",") if c.strip()]
        except ValueError:
            nice_list = None
    return await _do_unified_search(
        query=query.strip(),
        image_temp_path=None,
        nice_classes=nice_list,
        locarno_classes=_parse_csv_param(locarno),
        registries=_parse_registries_param(registries),
        limit=10,
        public=True,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_registry_search_routes(app, limiter):
    """Register the three unified-search routes on the app."""
    from auth.authentication import get_current_user

    @app.get("/api/v1/registry-search/public", tags=["Registry Search"])
    @limiter.limit("10/minute")
    async def public_registry_search_get(
        request: Request,
        query: str = Query(..., min_length=2, max_length=100),
        nice: Optional[str] = Query(None),
        locarno: Optional[str] = Query(None),
        registries: Optional[str] = Query(None),
    ):
        return await registry_search_public(
            query=query, nice=nice, locarno=locarno, registries=registries,
        )

    @app.post("/api/v1/registry-search/public", tags=["Registry Search"])
    @limiter.limit("10/minute")
    async def public_registry_search_post(
        request: Request,
        query: Optional[str] = Form(None),
        nice: Optional[str] = Form(None),
        locarno: Optional[str] = Form(None),
        registries: Optional[str] = Form(None),
    ):
        return await registry_search_public(
            query=query, nice=nice, locarno=locarno, registries=registries,
        )

    @app.post("/api/v1/registry-search", tags=["Registry Search"])
    @limiter.limit("60/minute")
    async def registry_search(
        request: Request,
        query: Optional[str] = Form(None),
        image: Optional[UploadFile] = File(None),
        nice: Optional[str] = Form(None),
        locarno: Optional[str] = Form(None),
        registries: Optional[str] = Form(None),
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
        return await registry_search_authenticated(
            query=query, image=image, nice=nice, locarno=locarno,
            registries=registries, limit=limit,
            user_id=user_id, organization_id=org_id,
        )

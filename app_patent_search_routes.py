"""Patent / Faydalı Model search routes.

  * ``POST /api/v1/patent-search/quick``      — authenticated, full results
  * ``GET/POST /api/v1/patent-search/public`` — anonymous, max 10 results, text-only
  * ``GET /api/v1/patent-search/ipc-autocomplete?q=`` — typeahead over IPC
    classes that actually exist in the patent corpus
  * ``GET /api/v1/patent-image/{path:path}`` — serve figure thumbs;
    converts CD-era TIFFs to JPEG on the fly

The authenticated endpoint computes a query text embedding using the
same SentenceTransformer (multilingual-e5-large) the corpus was indexed
with so retrieval lives in one model space. Public path is text-only —
trigram + ILIKE over titles, no embedding cost.
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


logger = logging.getLogger("turkpatent.patent_search_routes")

PROJECT_ROOT = Path(__file__).resolve().parent
PATENT_BULLETINS_ROOT = PROJECT_ROOT / "bulletins" / "Patent__Faydali_Model"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/tiff"}
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_CACHE_HEADER = {"Cache-Control": "public, max-age=86400"}


# ---------------------------------------------------------------------------
# Query text embedding (lazy model load shared across requests)
# ---------------------------------------------------------------------------

_TEXT_MODEL = None  # cached SentenceTransformer


def _embed_query_text(query: str) -> Optional[List[float]]:
    """Encode a search query into the same 1024-dim space the corpus uses.

    Uses the e5 'query:' prefix (vs 'passage:' for indexed docs) per the
    multilingual-e5-large convention — retrieval quality drops when the
    prefixes are mismatched.
    """
    global _TEXT_MODEL
    q = (query or "").strip()
    if not q:
        return None
    try:
        from embeddings_patent import TEXT_MODEL_NAME, detect_device
        from sentence_transformers import SentenceTransformer
    except Exception:
        logger.exception("Failed to import patent text embedder")
        return None
    if _TEXT_MODEL is None:
        try:
            _TEXT_MODEL = SentenceTransformer(TEXT_MODEL_NAME, device=detect_device())
        except Exception:
            logger.exception("Failed to load patent text embedder")
            return None
    try:
        vec = _TEXT_MODEL.encode(
            f"query: {q}", normalize_embeddings=True, show_progress_bar=False,
        )
        return vec.tolist()
    except Exception:
        logger.exception("Failed to encode patent query text")
        return None


def _parse_csv_param(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    return [v.strip() for v in raw.split(",") if v.strip()]


# ---------------------------------------------------------------------------
# Query image embedding (lazy-shared models with the embedding pipeline)
# ---------------------------------------------------------------------------

_FIGURE_MODELS = None  # cached LoadedModels from embeddings_patent


def _embed_query_image(temp_path: str) -> Optional[List[float]]:
    """Encode an uploaded image with the same DINOv2 ViT-L/14 the corpus
    was indexed with, returning a 1024-dim vector for cosine retrieval
    against patents.primary_figure_embedding.

    Patents.primary_figure_embedding is the mean-pooled DINOv2 vector
    across all the patent's figures, so a single uploaded query image
    embedded with the same backbone is the right comparison vector.
    """
    global _FIGURE_MODELS
    try:
        from embeddings_patent import detect_device, embed_image, load_models
    except Exception:
        logger.exception("Failed to import patent figure embedder")
        return None
    if _FIGURE_MODELS is None:
        try:
            _FIGURE_MODELS = load_models(detect_device())
        except Exception:
            logger.exception("Failed to load patent figure embedder")
            return None
    try:
        result = embed_image(Path(temp_path), _FIGURE_MODELS)
        return result.get("dinov2_vitl14")
    except Exception:
        logger.exception("Failed to encode query image")
        return None


def _save_upload_to_temp(content: bytes) -> str:
    """Write the uploaded bytes to a temp file after a sanity check.
    PIL's verify() catches truncated/corrupt files before they reach the
    GPU model loader."""
    if len(content) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 10MB)")
    try:
        Image.open(io.BytesIO(content)).verify()
    except Exception:
        raise HTTPException(status_code=400, detail="Corrupted image file")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".img")
    tmp.write(content)
    tmp.close()
    return tmp.name


# ---------------------------------------------------------------------------
# Image-serving route — figures stored under bulletins/Patent__Faydali_Model
# ---------------------------------------------------------------------------

def _resolve_patent_image(image_path: str) -> Optional[str]:
    if not image_path or ".." in image_path:
        return None
    candidate = (PATENT_BULLETINS_ROOT / image_path.replace("/", os.sep)).resolve()
    try:
        candidate.relative_to(PATENT_BULLETINS_ROOT.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return str(candidate)


def _tiff_to_jpeg_bytes(path: str) -> bytes:
    """Convert a TIFF figure to JPEG bytes. CD-era figures ship as TIFF,
    which browsers can't render natively; PDF-era figures are PNG and
    served as-is."""
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=85)
        return buf.getvalue()


def patent_image_response(image_path: str):
    """Serve a patent figure. Direct ``FileResponse`` for displayable
    formats; on-the-fly TIFF→JPEG conversion otherwise."""
    full = _resolve_patent_image(image_path)
    if not full:
        raise HTTPException(status_code=404, detail="Patent image not found")
    suffix = os.path.splitext(full)[1].lower()
    if suffix in (".tif", ".tiff"):
        try:
            jpeg = _tiff_to_jpeg_bytes(full)
        except Exception:
            logger.exception("Failed to convert patent TIFF to JPEG: %s", full)
            raise HTTPException(status_code=500, detail="Image conversion failed")
        return Response(content=jpeg, media_type="image/jpeg", headers=_CACHE_HEADER)
    media_type = MEDIA_TYPES.get(suffix, "application/octet-stream")
    return FileResponse(full, media_type=media_type, headers=_CACHE_HEADER)


# ---------------------------------------------------------------------------
# Search route handlers
# ---------------------------------------------------------------------------

async def _do_patent_search(
    *,
    query: Optional[str],
    image_temp_path: Optional[str],
    ipc_classes: Optional[List[str]],
    holder: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    kind_code: Optional[str],
    limit: int,
    public: bool,
) -> dict:
    from database.crud import Database
    from services.patent_search_service import search_patents

    text_embedding = None
    if query and len(query.strip()) >= 2:
        # Skip embedding when the query parses as an exact ID — service
        # short-circuits to a direct row lookup so the embed is wasted.
        from services.patent_search_service import parse_id_query
        if not parse_id_query(query):
            text_embedding = _embed_query_text(query)

    figure_embedding = None
    if image_temp_path:
        figure_embedding = _embed_query_image(image_temp_path)

    with Database() as db:
        return search_patents(
            db.conn,
            query=query,
            text_embedding=text_embedding,
            figure_embedding=figure_embedding,
            ipc_classes=ipc_classes,
            holder=holder,
            date_from=date_from,
            date_to=date_to,
            kind_code=kind_code,
            limit=limit,
            public=public,
        )


async def patent_search_quick(
    *,
    query: Optional[str],
    image: Optional[UploadFile],
    ipc: Optional[str],
    holder: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    kind_code: Optional[str],
    limit: int,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> dict:
    """Authenticated full-detail patent search.

    Shares the daily ``max_daily_quick_searches`` quota with trademark and
    design quick searches (one bucket covers all three registries in v1).
    Increment happens AFTER a successful retrieval so failed searches
    don't burn quota.
    """
    has_image = image is not None and image.filename
    has_query = bool(query and query.strip())
    if not has_query and not has_image:
        # Filter-only mode is allowed (e.g. "all 2024 patents in IPC A61")
        # but we require at least one filter to be set so we don't
        # accidentally serve the whole corpus.
        any_filter = any([ipc, holder, date_from, date_to, kind_code])
        if not any_filter:
            raise HTTPException(
                status_code=422,
                detail="Provide a query (min 2 chars), an image, or at least one filter",
            )

    if user_id:
        from database.crud import Database
        from utils.subscription import check_quick_search_eligibility
        with Database() as db:
            can_search, _reason, details = check_quick_search_eligibility(db, user_id)
            if not can_search:
                raise HTTPException(status_code=429, detail=details)

    temp_path: Optional[str] = None
    if has_image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid image type")
        content = await image.read()
        temp_path = _save_upload_to_temp(content)
    try:
        result = await _do_patent_search(
            query=query.strip() if query else None,
            image_temp_path=temp_path,
            ipc_classes=_parse_csv_param(ipc),
            holder=holder.strip() if holder else None,
            date_from=date_from or None,
            date_to=date_to or None,
            kind_code=kind_code.strip().upper() if kind_code else None,
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
        from utils.subscription import increment_quick_search_usage
        with Database() as db:
            increment_quick_search_usage(db, user_id, organization_id)

    return result


async def patent_search_public(
    *,
    query: Optional[str],
    image: Optional[UploadFile] = None,
    ipc: Optional[str] = None,
    holder: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    kind_code: Optional[str] = None,
) -> dict:
    """Public anonymous patent search. Capped at 10 results.

    Accepts the same inputs as the authenticated quick endpoint (query,
    image, full filter surface) so the landing-page experience matches
    the dashboard. Quota enforcement is the caller's responsibility —
    the route layer wraps this with ``enforce_public_search_quota`` and
    ``record_public_search_usage`` so failed searches don't burn quota.
    """
    has_image = image is not None and image.filename
    has_query = bool(query and query.strip())
    if not has_query and not has_image:
        raise HTTPException(
            status_code=422,
            detail="Provide a query (min 2 chars) or upload an image",
        )

    temp_path: Optional[str] = None
    if has_image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid image type")
        content = await image.read()
        temp_path = _save_upload_to_temp(content)
    try:
        return await _do_patent_search(
            query=query.strip() if has_query else None,
            image_temp_path=temp_path,
            ipc_classes=_parse_csv_param(ipc),
            holder=holder.strip() if holder else None,
            date_from=date_from or None,
            date_to=date_to or None,
            kind_code=kind_code.strip().upper() if kind_code else None,
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
# IPC autocomplete (corpus-driven, distinct values from patents.ipc_classes)
# ---------------------------------------------------------------------------

def patent_ipc_autocomplete(prefix: str, limit: int = 20) -> dict:
    """Return IPC classes present in the corpus that start with ``prefix``.

    Reads distinct unnested values from ``patents.ipc_classes``. Cheap because
    the GIN index on ipc_classes already enumerates the array values; the
    distinct + ILIKE pass is small in practice (a few thousand unique codes).
    Joins to ``ipc_classes_lookup`` for descriptions when available.
    """
    from database.crud import Database

    p = (prefix or "").strip().upper()
    n = max(1, min(int(limit or 20), 50))
    if len(p) < 1:
        return {"items": [], "total": 0}

    with Database() as db:
        cur = db.cursor()
        # Ordering rules (in priority):
        #   1. Codes with descriptions in ipc_classes_lookup come first
        #      (canonical codes — what the user actually wants).
        #   2. Then by code length (shorter = higher in the IPC tree
        #      = more useful as a filter).
        #   3. Finally alphabetical.
        # This pushes the dirty corpus codes (e.g. "H04B  1/005" with
        # double spaces) below canonical entries like "H04N".
        cur.execute(
            """
            SELECT sub.code,
                   l.description_tr,
                   l.description_en,
                   l.section
            FROM (
                SELECT DISTINCT UPPER(unnest(ipc_classes)) AS code
                FROM patents
                WHERE ipc_classes IS NOT NULL AND array_length(ipc_classes, 1) > 0
            ) sub
            LEFT JOIN ipc_classes_lookup l ON UPPER(l.code) = sub.code
            WHERE sub.code LIKE %(p)s
            ORDER BY (l.description_en IS NULL),
                     length(sub.code),
                     sub.code
            LIMIT %(n)s
            """,
            {"p": p + "%", "n": n},
        )
        rows = cur.fetchall()

    items = []
    for r in rows:
        is_dict = isinstance(r, dict)
        items.append({
            "code": r["code"] if is_dict else r[0],
            "description_tr": (r["description_tr"] if is_dict else r[1]),
            "description_en": (r["description_en"] if is_dict else r[2]),
            "section": (r["section"] if is_dict else r[3]),
        })
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_patent_search_routes(app, limiter):
    """Register patent-search routes on the FastAPI app.

    ``limiter`` is the slowapi.Limiter instance used elsewhere. Auth is
    wired internally via ``Depends(get_current_user)`` to match the
    existing trademark/design-route conventions.
    """
    from app_public_search_quota import (
        enforce_public_search_quota,
        record_public_search_usage,
    )
    from auth.authentication import get_current_user

    @app.get("/api/v1/patent-search/public", tags=["Patent Search"])
    @limiter.limit("10/minute")
    async def public_patent_search_get(
        request: Request,
        response: Response,
        query: str = Query(..., min_length=2, max_length=200),
        ipc: Optional[str] = Query(None),
        holder: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        kind_code: Optional[str] = Query(None),
    ):
        client_id = enforce_public_search_quota(request, response)
        payload = await patent_search_public(
            query=query, ipc=ipc, holder=holder,
            date_from=date_from, date_to=date_to, kind_code=kind_code,
        )
        record_public_search_usage(client_id)
        return payload

    @app.post("/api/v1/patent-search/public", tags=["Patent Search"])
    @limiter.limit("10/minute")
    async def public_patent_search_post(
        request: Request,
        response: Response,
        query: Optional[str] = Form(None),
        image: Optional[UploadFile] = File(None),
        ipc: Optional[str] = Form(None),
        holder: Optional[str] = Form(None),
        date_from: Optional[str] = Form(None),
        date_to: Optional[str] = Form(None),
        kind_code: Optional[str] = Form(None),
    ):
        client_id = enforce_public_search_quota(request, response)
        payload = await patent_search_public(
            query=query, image=image, ipc=ipc, holder=holder,
            date_from=date_from, date_to=date_to, kind_code=kind_code,
        )
        record_public_search_usage(client_id)
        return payload

    @app.post("/api/v1/patent-search/quick", tags=["Patent Search"])
    @limiter.limit("60/minute")
    async def quick_patent_search(
        request: Request,
        query: Optional[str] = Form(None),
        image: Optional[UploadFile] = File(None),
        ipc: Optional[str] = Form(None),
        holder: Optional[str] = Form(None),
        date_from: Optional[str] = Form(None),
        date_to: Optional[str] = Form(None),
        kind_code: Optional[str] = Form(None),
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
        return await patent_search_quick(
            query=query, image=image, ipc=ipc, holder=holder,
            date_from=date_from, date_to=date_to, kind_code=kind_code,
            limit=limit,
            user_id=user_id, organization_id=org_id,
        )

    @app.get("/api/v1/patent-search/ipc-autocomplete", tags=["Patent Search"])
    @limiter.limit("60/minute")
    async def ipc_autocomplete(
        request: Request,
        q: str = Query(..., min_length=1, max_length=20),
        limit: int = Query(20, ge=1, le=50),
    ):
        return patent_ipc_autocomplete(q, limit=limit)

    @app.get("/api/v1/patent-image/{image_path:path}", tags=["Patent Search"])
    async def serve_patent_image(image_path: str):
        return patent_image_response(image_path)

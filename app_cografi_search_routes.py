"""Coğrafi İşaret ve Geleneksel Ürün Adı search routes.

  * ``POST /api/v1/cografi-search/quick``      — authenticated, full results,
    text + image hybrid retrieval, all filters
  * ``GET/POST /api/v1/cografi-search/public`` — anonymous, max 10 results,
    text-only retrieval, no image upload
  * ``GET /api/v1/cografi-search/autocomplete?q=`` — typeahead over names
    and regions present in the cografi corpus
  * ``GET /api/v1/cografi-image/{path:path}`` — serve figure thumbnails

The authenticated endpoint computes a query text embedding using the
same SentenceTransformer (multilingual-e5-large) the corpus was indexed
with so retrieval lives in one model space. Public path is text-only —
trigram + ILIKE over names, no embedding cost.
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


logger = logging.getLogger("turkpatent.cografi_search_routes")

PROJECT_ROOT = Path(__file__).resolve().parent
COGRAFI_BULLETINS_ROOT = (
    PROJECT_ROOT / "bulletins" / "Cografi_Isaret_ve_Geleneksel_Urun_Adi"
)
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
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

    Uses the e5 ``query:`` prefix (vs ``passage:`` for indexed docs) per
    the multilingual-e5-large convention — retrieval quality drops when
    the prefixes are mismatched.
    """
    global _TEXT_MODEL
    q = (query or "").strip()
    if not q:
        return None
    try:
        from embeddings_cografi import TEXT_MODEL_NAME, detect_device
        from sentence_transformers import SentenceTransformer
    except Exception:
        logger.exception("Failed to import cografi text embedder")
        return None
    if _TEXT_MODEL is None:
        try:
            _TEXT_MODEL = SentenceTransformer(TEXT_MODEL_NAME, device=detect_device())
        except Exception:
            logger.exception("Failed to load cografi text embedder")
            return None
    try:
        vec = _TEXT_MODEL.encode(
            f"query: {q}", normalize_embeddings=True, show_progress_bar=False,
        )
        return vec.tolist()
    except Exception:
        logger.exception("Failed to encode cografi query text")
        return None


def _parse_csv_param(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    return [v.strip() for v in raw.split(",") if v.strip()]


def _parse_int_param(raw: Optional[str]) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Query image embedding (lazy-shared models with the embedding pipeline)
# ---------------------------------------------------------------------------

_FIGURE_MODELS = None


def _embed_query_image(temp_path: str) -> Optional[List[float]]:
    """Encode an uploaded image with the same DINOv2 ViT-L/14 the corpus
    was indexed with. Returns a 1024-dim vector for cosine retrieval
    against ``cografi_records.primary_figure_embedding``.
    """
    global _FIGURE_MODELS
    try:
        from embeddings_cografi import detect_device, embed_image, load_models
    except Exception:
        logger.exception("Failed to import cografi figure embedder")
        return None
    if _FIGURE_MODELS is None:
        try:
            _FIGURE_MODELS = load_models(detect_device(), load_vision=True)
        except Exception:
            logger.exception("Failed to load cografi figure embedder")
            return None
    try:
        result = embed_image(Path(temp_path), _FIGURE_MODELS)
        return result.get("dinov2_vitl14")
    except Exception:
        logger.exception("Failed to encode query image")
        return None


def _save_upload_to_temp(content: bytes) -> str:
    """Write the uploaded bytes to a temp file after a sanity check."""
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
# Image-serving route — figures live under bulletins/Cografi_*/{folder}/figures/
# ---------------------------------------------------------------------------

def _resolve_cografi_image(image_path: str) -> Optional[str]:
    """Resolve a sanitised relative path to an absolute file path within
    the cografi bulletins root. Returns ``None`` when the path is missing,
    contains traversal sequences, or doesn't exist on disk.
    """
    if not image_path or ".." in image_path:
        return None
    # The route receives "{bulletin_folder}/{C2022_000469/1.jpeg}" or
    # "{bulletin_folder}/figures/{C2022_000469/1.jpeg}". Insert the
    # missing "figures/" segment when the caller followed the
    # service's compact URL form.
    parts = image_path.replace("\\", "/").split("/", 1)
    if len(parts) == 2 and not parts[1].startswith("figures/"):
        normalised = f"{parts[0]}/figures/{parts[1]}"
    else:
        normalised = image_path
    candidate = (COGRAFI_BULLETINS_ROOT / normalised.replace("/", os.sep)).resolve()
    try:
        candidate.relative_to(COGRAFI_BULLETINS_ROOT.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return str(candidate)


def cografi_image_response(image_path: str):
    """Serve a cografi figure with a 24h cache header."""
    full = _resolve_cografi_image(image_path)
    if not full:
        raise HTTPException(status_code=404, detail="Cografi image not found")
    suffix = os.path.splitext(full)[1].lower()
    media_type = MEDIA_TYPES.get(suffix, "application/octet-stream")
    return FileResponse(full, media_type=media_type, headers=_CACHE_HEADER)


# ---------------------------------------------------------------------------
# Search route handlers
# ---------------------------------------------------------------------------

async def _do_cografi_search(
    *,
    query: Optional[str],
    image_temp_path: Optional[str],
    section_keys: Optional[List[str]],
    record_types: Optional[List[str]],
    gi_type: Optional[str],
    region: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    application_no: Optional[str],
    registration_no: Optional[int],
    include_admin: bool,
    limit: int,
    public: bool,
) -> dict:
    from database.crud import Database
    from services.cografi_search_service import parse_id_query, search_cografi

    text_embedding = None
    if query and len(query.strip()) >= 2:
        # Skip embedding when the query parses as an exact ID — service
        # short-circuits to a direct row lookup so the embed is wasted.
        if not parse_id_query(query):
            text_embedding = _embed_query_text(query)

    figure_embedding = None
    if image_temp_path:
        figure_embedding = _embed_query_image(image_temp_path)

    with Database() as db:
        return search_cografi(
            db.conn,
            query=query,
            text_embedding=text_embedding,
            figure_embedding=figure_embedding,
            section_keys=section_keys,
            record_types=record_types,
            gi_type=gi_type,
            region=region,
            date_from=date_from,
            date_to=date_to,
            application_no=application_no,
            registration_no=registration_no,
            include_admin=include_admin,
            limit=limit,
            public=public,
        )


async def cografi_search_authenticated(
    *,
    query: Optional[str],
    image: Optional[UploadFile],
    section_keys: Optional[str],
    record_types: Optional[str],
    gi_type: Optional[str],
    region: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    application_no: Optional[str],
    registration_no: Optional[str],
    include_admin: bool,
    limit: int,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> dict:
    """Authenticated full-detail cografi search.

    Shares the daily ``max_daily_live_searches`` quota with patent /
    trademark / design quick searches (one bucket covers all four
    registries). Increment happens AFTER a successful retrieval so
    failed searches don't burn quota.
    """
    has_image = image is not None and image.filename
    has_query = bool(query and query.strip())
    if not has_query and not has_image:
        any_filter = any([
            section_keys, record_types, gi_type, region,
            date_from, date_to, application_no, registration_no,
        ])
        if not any_filter:
            raise HTTPException(
                status_code=422,
                detail="Provide a query (min 2 chars), an image, or at least one filter",
            )

    if user_id:
        from database.crud import Database
        from utils.subscription import check_live_search_eligibility
        with Database() as db:
            can_search, _reason, details = check_live_search_eligibility(db, user_id)
            if not can_search:
                raise HTTPException(status_code=429, detail=details)

    temp_path: Optional[str] = None
    if has_image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid image type")
        content = await image.read()
        temp_path = _save_upload_to_temp(content)
    try:
        result = await _do_cografi_search(
            query=query.strip() if query else None,
            image_temp_path=temp_path,
            section_keys=_parse_csv_param(section_keys),
            record_types=_parse_csv_param(record_types),
            gi_type=gi_type.strip() if gi_type else None,
            region=region.strip() if region else None,
            date_from=date_from or None,
            date_to=date_to or None,
            application_no=application_no.strip() if application_no else None,
            registration_no=_parse_int_param(registration_no),
            include_admin=bool(include_admin),
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


async def cografi_search_public(
    *,
    query: Optional[str],
    image: Optional[UploadFile] = None,
    section_keys: Optional[str] = None,
    record_types: Optional[str] = None,
    gi_type: Optional[str] = None,
    region: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    application_no: Optional[str] = None,
    registration_no: Optional[str] = None,
) -> dict:
    """Public anonymous cografi search. Capped at 10 results.

    Mirrors the authenticated quick endpoint's input surface (text +
    image + full filter set) so the landing-page experience matches
    the dashboard. The figure-embedding + e5 text-embedding hybrid
    ranking runs identically on the public path.
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
        return await _do_cografi_search(
            query=query.strip() if has_query else None,
            image_temp_path=temp_path,
            section_keys=_parse_csv_param(section_keys),
            record_types=_parse_csv_param(record_types),
            gi_type=gi_type.strip() if gi_type else None,
            region=region.strip() if region else None,
            date_from=date_from or None,
            date_to=date_to or None,
            application_no=application_no.strip() if application_no else None,
            registration_no=_parse_int_param(registration_no),
            include_admin=False,
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
# Autocomplete: typeahead over distinct names + regions
# ---------------------------------------------------------------------------

def cografi_autocomplete(prefix: str, limit: int = 20) -> dict:
    """Return cografi names AND regions starting with ``prefix``.

    Two concurrent rankings: the ``names`` list helps users find a GI
    by typing its first few letters; the ``regions`` list is for
    location-driven browsing ("type 'Kon' to find Konya GIs"). Both
    use the trigram indexes already on the columns.
    """
    from database.crud import Database

    p = (prefix or "").strip()
    n = max(1, min(int(limit or 20), 50))
    if len(p) < 2:
        return {"names": [], "regions": [], "total": 0}

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT DISTINCT name
            FROM cografi_records
            WHERE name IS NOT NULL
              AND LOWER(name) LIKE LOWER(%(p)s)
            ORDER BY name
            LIMIT %(n)s
            """,
            {"p": p + "%", "n": n},
        )
        names = [r[0] if not isinstance(r, dict) else r["name"]
                 for r in cur.fetchall()]
        cur.execute(
            """
            SELECT DISTINCT geographical_boundary AS region
            FROM cografi_records
            WHERE geographical_boundary IS NOT NULL
              AND LOWER(geographical_boundary) LIKE LOWER(%(p)s)
            ORDER BY geographical_boundary
            LIMIT %(n)s
            """,
            {"p": "%" + p + "%", "n": n},
        )
        regions = [
            (r[0] if not isinstance(r, dict) else r["region"])
            for r in cur.fetchall()
        ]
    return {
        "names": names,
        "regions": regions,
        "total": len(names) + len(regions),
    }


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

def cografi_detail_handler(record_id: str) -> dict:
    from services.cografi_detail_service import get_cografi_detail
    return get_cografi_detail(record_id=record_id)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_cografi_search_routes(app, limiter):
    """Register cografi search + detail + image routes on the FastAPI app.

    ``limiter`` is the slowapi.Limiter instance used elsewhere. Auth is
    wired internally via ``Depends(get_current_user)`` to match the
    existing patent / design / marka conventions.
    """
    from app_public_search_quota import (
        enforce_public_search_quota,
        record_public_search_usage,
    )
    from auth.authentication import get_current_user

    @app.get("/api/v1/cografi-search/public", tags=["Cografi Search"])
    @limiter.limit("10/minute")
    async def public_cografi_search_get(
        request: Request,
        response: Response,
        query: str = Query(..., min_length=2, max_length=200),
        section_keys: Optional[str] = Query(None),
        record_types: Optional[str] = Query(None),
        gi_type: Optional[str] = Query(None),
        region: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        application_no: Optional[str] = Query(None),
        registration_no: Optional[str] = Query(None),
    ):
        client_id = enforce_public_search_quota(request, response)
        payload = await cografi_search_public(
            query=query, section_keys=section_keys,
            record_types=record_types, gi_type=gi_type, region=region,
            date_from=date_from, date_to=date_to,
            application_no=application_no, registration_no=registration_no,
        )
        record_public_search_usage(client_id)
        return payload

    @app.post("/api/v1/cografi-search/public", tags=["Cografi Search"])
    @limiter.limit("10/minute")
    async def public_cografi_search_post(
        request: Request,
        response: Response,
        query: Optional[str] = Form(None),
        image: Optional[UploadFile] = File(None),
        section_keys: Optional[str] = Form(None),
        record_types: Optional[str] = Form(None),
        gi_type: Optional[str] = Form(None),
        region: Optional[str] = Form(None),
        date_from: Optional[str] = Form(None),
        date_to: Optional[str] = Form(None),
        application_no: Optional[str] = Form(None),
        registration_no: Optional[str] = Form(None),
    ):
        client_id = enforce_public_search_quota(request, response)
        payload = await cografi_search_public(
            query=query, image=image, section_keys=section_keys,
            record_types=record_types, gi_type=gi_type, region=region,
            date_from=date_from, date_to=date_to,
            application_no=application_no, registration_no=registration_no,
        )
        record_public_search_usage(client_id)
        return payload

    @app.post("/api/v1/cografi-search", tags=["Cografi Search"])
    @limiter.limit("60/minute")
    async def cografi_search(
        request: Request,
        query: Optional[str] = Form(None),
        image: Optional[UploadFile] = File(None),
        section_keys: Optional[str] = Form(None),
        record_types: Optional[str] = Form(None),
        gi_type: Optional[str] = Form(None),
        region: Optional[str] = Form(None),
        date_from: Optional[str] = Form(None),
        date_to: Optional[str] = Form(None),
        application_no: Optional[str] = Form(None),
        registration_no: Optional[str] = Form(None),
        include_admin: bool = Form(False),
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
        return await cografi_search_authenticated(
            query=query,
            image=image,
            section_keys=section_keys,
            record_types=record_types,
            gi_type=gi_type,
            region=region,
            date_from=date_from,
            date_to=date_to,
            application_no=application_no,
            registration_no=registration_no,
            include_admin=include_admin,
            limit=limit,
            user_id=user_id,
            organization_id=org_id,
        )

    @app.get("/api/v1/cografi-search/autocomplete", tags=["Cografi Search"])
    @limiter.limit("60/minute")
    async def cografi_autocomplete_endpoint(
        request: Request,
        q: str = Query(..., min_length=2, max_length=50),
        limit: int = Query(20, ge=1, le=50),
    ):
        return cografi_autocomplete(q, limit=limit)

    @app.get("/api/v1/cografi/{record_id}", tags=["Cografi Detail"])
    async def cografi_detail_endpoint(record_id: str):
        return cografi_detail_handler(record_id)

    @app.get("/api/v1/cografi-image/{image_path:path}", tags=["Cografi Search"])
    async def serve_cografi_image(image_path: str):
        return cografi_image_response(image_path)

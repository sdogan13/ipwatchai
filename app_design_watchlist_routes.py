"""Design watchlist API routes.

Endpoints:
  * ``POST   /api/v1/design-watchlist``           — create
  * ``GET    /api/v1/design-watchlist``           — list (paginated)
  * ``GET    /api/v1/design-watchlist/{id}``      — get one
  * ``PUT    /api/v1/design-watchlist/{id}``      — update
  * ``DELETE /api/v1/design-watchlist/{id}``      — delete
  * ``POST   /api/v1/design-watchlist/{id}/image``— upload reference image + embed
  * ``POST   /api/v1/design-watchlist/{id}/scan`` — trigger full-corpus single-item scan

Auth: every endpoint depends on ``get_current_user``. Image upload runs the
embeddings inline (the image-encode pass takes ~1s on CPU, which is fine for
a per-user create flow).
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import (
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from PIL import Image
from pydantic import BaseModel, Field


logger = logging.getLogger("turkpatent.design_watchlist_routes")


PROJECT_ROOT = Path(__file__).resolve().parent
WATCHLIST_IMAGE_ROOT = PROJECT_ROOT / "uploads" / "design_watchlist"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

_MODEL_BUNDLE = None  # cached embedding models


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class DesignWatchlistCreate(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=500)
    locarno_classes: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    customer_application_no: Optional[str] = None
    customer_registration_no: Optional[str] = None
    reference_design_id: Optional[UUID] = None
    similarity_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    monitor_text: bool = True
    monitor_visual: bool = True
    alert_email: bool = True
    alert_webhook: bool = False
    webhook_url: Optional[str] = None
    alert_frequency: str = Field(default="daily")
    tags: List[str] = Field(default_factory=list)
    priority: str = Field(default="medium")


class DesignWatchlistUpdate(BaseModel):
    product_name: Optional[str] = Field(default=None, max_length=500)
    locarno_classes: Optional[List[str]] = None
    description: Optional[str] = None
    customer_application_no: Optional[str] = None
    customer_registration_no: Optional[str] = None
    similarity_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    monitor_text: Optional[bool] = None
    monitor_visual: Optional[bool] = None
    alert_email: Optional[bool] = None
    alert_webhook: Optional[bool] = None
    webhook_url: Optional[str] = None
    alert_frequency: Optional[str] = None
    tags: Optional[List[str]] = None
    priority: Optional[str] = None
    is_active: Optional[bool] = None


class DesignWatchlistBulkThreshold(BaseModel):
    """Body for the PUT /design-watchlist/bulk-threshold endpoint.

    Defined at module level (not inside register_design_watchlist_routes)
    so FastAPI's body-vs-query introspection picks it up correctly.
    """
    threshold: float = Field(..., ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Embedding helpers (mirrors app_design_search_routes)
# ---------------------------------------------------------------------------

def _embed_image_at_path(temp_path: str) -> dict:
    global _MODEL_BUNDLE
    from embeddings_tasarim import detect_device, embed_image, load_models
    if _MODEL_BUNDLE is None:
        _MODEL_BUNDLE = load_models(detect_device())
    return embed_image(Path(temp_path), _MODEL_BUNDLE)


def _save_upload(content: bytes, item_id: UUID, content_type: str) -> Path:
    if len(content) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Image too large (max 10 MB)",
        )
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image type: {content_type}",
        )
    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image data",
        ) from exc

    WATCHLIST_IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
    suffix = ".jpg" if content_type in ("image/jpeg", "image/jpg") else \
             ".png" if content_type == "image/png" else ".webp"
    out_path = WATCHLIST_IMAGE_ROOT / f"{item_id}_{uuid4().hex[:8]}{suffix}"
    out_path.write_bytes(content)
    return out_path


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_design_watchlist_routes(app, limiter):
    from auth.authentication import get_current_user
    from services import design_watchlist_service as svc

    @app.post("/api/v1/design-watchlist", tags=["Design Watchlist"])
    @limiter.limit("30/minute")
    async def create_design_watchlist(
        request: Request,
        body: DesignWatchlistCreate,
        background_tasks: BackgroundTasks,
        current_user=Depends(get_current_user),
    ):
        item = svc.create_design_watchlist_item(
            data=body.model_dump(),
            current_user=current_user,
        )
        # Trigger an async full-corpus scan for the new item.
        background_tasks.add_task(_run_single_scan_safe, UUID(str(item["id"])))
        return _serialize_item(item)

    @app.get("/api/v1/design-watchlist", tags=["Design Watchlist"])
    @limiter.limit("60/minute")
    async def list_design_watchlist(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        is_active: Optional[bool] = Query(True),
        current_user=Depends(get_current_user),
    ):
        result = svc.list_design_watchlist_items(
            current_user=current_user,
            page=page,
            page_size=page_size,
            is_active=is_active,
        )
        result["items"] = [_serialize_item(it) for it in result["items"]]
        return result

    # ---------------------------------------------------------------
    # Bulk + stats endpoints (registered BEFORE the /{item_id} block so
    # FastAPI dispatches `/stats`, `/scan-all`, etc. to the static-path
    # handlers rather than treating them as UUIDs).
    # ---------------------------------------------------------------

    @app.get("/api/v1/design-watchlist/stats", tags=["Design Watchlist"])
    @limiter.limit("60/minute")
    async def design_watchlist_stats(
        request: Request,
        current_user=Depends(get_current_user),
    ):
        return svc.get_design_watchlist_stats(current_user=current_user)

    @app.post("/api/v1/design-watchlist/scan-all", tags=["Design Watchlist"])
    @limiter.limit("5/minute")
    async def design_watchlist_scan_all(
        request: Request,
        background_tasks: BackgroundTasks,
        current_user=Depends(get_current_user),
    ):
        ids = svc.list_active_item_ids_for_org(current_user=current_user)
        for item_id_str in ids:
            background_tasks.add_task(_run_single_scan_safe, UUID(item_id_str))
        return {"queued": len(ids)}

    @app.delete("/api/v1/design-watchlist/all", tags=["Design Watchlist"])
    @limiter.limit("5/minute")
    async def design_watchlist_delete_all(
        request: Request,
        current_user=Depends(get_current_user),
    ):
        return svc.delete_all_design_watchlist_items(current_user=current_user)

    @app.put("/api/v1/design-watchlist/bulk-threshold", tags=["Design Watchlist"])
    @limiter.limit("10/minute")
    async def design_watchlist_bulk_threshold(
        request: Request,
        body: DesignWatchlistBulkThreshold,
        current_user=Depends(get_current_user),
    ):
        return svc.update_all_design_watchlist_thresholds(
            current_user=current_user,
            threshold=float(body.threshold),
        )

    # ---------------------------------------------------------------
    # Bulk import from a holder's design portfolio. Mirrors the
    # trademark /api/v1/watchlist/bulk-from-portfolio endpoint so the
    # dashboard portfolio modal can "Add all to watchlist" with one
    # click. Queues background scans for each newly created item.
    # ---------------------------------------------------------------

    @app.post("/api/v1/design-watchlist/bulk-from-portfolio", tags=["Design Watchlist"])
    @limiter.limit("5/minute")
    async def design_watchlist_bulk_from_portfolio(
        request: Request,
        body: dict,
        background_tasks: BackgroundTasks,
        current_user=Depends(get_current_user),
    ):
        holder_id = (body or {}).get("holder_id") or (body or {}).get("id")
        if not holder_id:
            raise HTTPException(status_code=422, detail="holder_id is required")
        result = await svc.import_design_watchlist_from_portfolio(
            holder_id=str(holder_id),
            current_user=current_user,
        )
        for item_id_str in result.get("scan_item_ids", []):
            background_tasks.add_task(_run_single_scan_safe, UUID(item_id_str))
        result["queued_scans"] = len(result.get("scan_item_ids", []))
        return result

    # ---------------------------------------------------------------
    # Phase 3 — CSV bulk upload
    # ---------------------------------------------------------------

    @app.get("/api/v1/design-watchlist/upload/template", tags=["Design Watchlist"])
    @limiter.limit("30/minute")
    async def design_watchlist_upload_template(
        request: Request,
        current_user=Depends(get_current_user),
    ):
        content = svc.build_design_csv_template()
        return StreamingResponse(
            iter([content]),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="tasarim_takibi_sablon.csv"',
            },
        )

    @app.post("/api/v1/design-watchlist/upload/detect-columns", tags=["Design Watchlist"])
    @limiter.limit("10/minute")
    async def design_watchlist_upload_detect(
        request: Request,
        file: UploadFile = File(...),
        current_user=Depends(get_current_user),
    ):
        if not (file.filename or "").lower().endswith(".csv"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="CSV file required (Excel support coming soon)",
            )
        content = await file.read()
        return svc.detect_design_csv_columns(content)

    @app.post("/api/v1/design-watchlist/upload/with-mapping", tags=["Design Watchlist"])
    @limiter.limit("5/minute")
    async def design_watchlist_upload_with_mapping(
        request: Request,
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        column_mapping: str = Form(...),
        current_user=Depends(get_current_user),
    ):
        if not (file.filename or "").lower().endswith(".csv"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="CSV file required (Excel support coming soon)",
            )
        content = await file.read()
        result = svc.import_design_csv_with_mapping(
            content=content,
            column_mapping=column_mapping,
            current_user=current_user,
        )
        # Queue a single full-corpus scan per newly-created item (mirrors
        # what create_design_watchlist does for the single-add path).
        for new_id in result.get("scan_item_ids", []):
            background_tasks.add_task(_run_single_scan_safe, UUID(new_id))
        return result

    @app.get("/api/v1/design-watchlist/{item_id}", tags=["Design Watchlist"])
    @limiter.limit("60/minute")
    async def get_design_watchlist_one(
        request: Request,
        item_id: UUID,
        current_user=Depends(get_current_user),
    ):
        item = svc.get_design_watchlist_item(item_id=item_id, current_user=current_user)
        return _serialize_item(item)

    @app.put("/api/v1/design-watchlist/{item_id}", tags=["Design Watchlist"])
    @limiter.limit("30/minute")
    async def update_design_watchlist(
        request: Request,
        item_id: UUID,
        body: DesignWatchlistUpdate,
        current_user=Depends(get_current_user),
    ):
        # Pydantic v2: model_dump(exclude_unset=True) gives only client-provided fields.
        item = svc.update_design_watchlist_item(
            item_id=item_id,
            data=body.model_dump(exclude_unset=True),
            current_user=current_user,
        )
        return _serialize_item(item)

    @app.delete("/api/v1/design-watchlist/{item_id}", tags=["Design Watchlist"])
    @limiter.limit("30/minute")
    async def delete_design_watchlist(
        request: Request,
        item_id: UUID,
        current_user=Depends(get_current_user),
    ):
        return svc.delete_design_watchlist_item(item_id=item_id, current_user=current_user)

    @app.post("/api/v1/design-watchlist/{item_id}/image", tags=["Design Watchlist"])
    @limiter.limit("10/minute")
    async def upload_design_watchlist_image(
        request: Request,
        item_id: UUID,
        background_tasks: BackgroundTasks,
        image: UploadFile = File(...),
        current_user=Depends(get_current_user),
    ):
        content = await image.read()
        out_path = _save_upload(content, item_id, image.content_type or "")
        try:
            embeddings = _embed_image_at_path(str(out_path))
        except Exception as exc:
            logger.exception("design watchlist image embed failed for %s", item_id)
            try:
                out_path.unlink()
            except Exception:
                pass
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Image embedding failed",
            ) from exc

        item = svc.attach_design_watchlist_image(
            item_id=item_id,
            image_path=str(out_path.relative_to(PROJECT_ROOT)),
            embeddings=embeddings,
            current_user=current_user,
        )
        background_tasks.add_task(_run_single_scan_safe, item_id)
        return _serialize_item(item)

    @app.post("/api/v1/design-watchlist/{item_id}/scan", tags=["Design Watchlist"])
    @limiter.limit("10/minute")
    async def scan_design_watchlist_item(
        request: Request,
        item_id: UUID,
        background_tasks: BackgroundTasks,
        current_user=Depends(get_current_user),
    ):
        # Confirm the item belongs to this org before queueing the scan.
        svc.get_design_watchlist_item(item_id=item_id, current_user=current_user)
        background_tasks.add_task(_run_single_scan_safe, item_id)
        return {"queued": True, "item_id": str(item_id)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_single_scan_safe(item_id: UUID) -> None:
    from watchlist.design_scanner import scan_single_design_watchlist
    try:
        scan_single_design_watchlist(item_id=item_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("background design watchlist scan failed: %r", exc)


def _serialize_item(item: dict) -> dict:
    """Turn a DB row into a JSON-safe payload (drop raw embedding vectors)."""
    if not item:
        return {}
    out = {}
    drop_keys = {"dinov2_embedding", "clip_embedding", "color_histogram"}
    for k, v in item.items():
        if k in drop_keys:
            continue
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    out["has_image"] = bool(item.get("image_path"))
    out["has_embedding"] = item.get("dinov2_embedding") is not None
    return out

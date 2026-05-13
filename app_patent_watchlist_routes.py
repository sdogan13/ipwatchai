"""Patent / Faydalı Model watchlist routes.

CRUD over ``patent_watchlist_mt``:

  * ``POST   /api/v1/patent-watchlist``       — create (holder or reference)
  * ``GET    /api/v1/patent-watchlist``       — list (with filters)
  * ``GET    /api/v1/patent-watchlist/{id}``  — fetch one
  * ``PATCH  /api/v1/patent-watchlist/{id}``  — update mutable fields
  * ``DELETE /api/v1/patent-watchlist/{id}``  — hard delete (cascades alerts)

Reference-watch creation with ``reference_query`` (free text, no
``reference_patent_id``) embeds the query into the same e5 1024-d
space the corpus uses. Reuses ``_embed_query_text`` from
``app_patent_search_routes`` so the cached SentenceTransformer is shared.
"""
from __future__ import annotations

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request


logger = logging.getLogger("turkpatent.patent_watchlist_routes")


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_patent_watchlist_routes(app, limiter):
    """Register patent watchlist CRUD on the FastAPI app."""
    from auth.authentication import get_current_user
    from services.patent_watchlist_service import (
        create_patent_watchlist_item,
        delete_patent_watchlist_item,
        get_patent_watchlist_item,
        list_patent_watchlist_items,
        update_patent_watchlist_item,
    )

    @app.post("/api/v1/patent-watchlist", tags=["Patent Watchlist"])
    @limiter.limit("30/minute")
    async def create_patent_watchlist(
        request: Request,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        # If watch_type='reference' with reference_query (no patent_id), embed
        # the query so the scanner has something to cosine-against. We embed
        # in the route layer (not the service) because the e5 loader lives
        # next to the patent search routes.
        watch_type = (data.get("watch_type") or "").strip().lower()
        if (
            watch_type == "reference"
            and data.get("reference_query")
            and not data.get("reference_patent_id")
            and not data.get("reference_embedding")
        ):
            from app_patent_search_routes import _embed_query_text
            embedding = _embed_query_text(data["reference_query"])
            if embedding:
                data["reference_embedding"] = embedding

        return create_patent_watchlist_item(data=data, current_user=current_user)

    @app.get("/api/v1/patent-watchlist", tags=["Patent Watchlist"])
    @limiter.limit("60/minute")
    async def list_patent_watchlist(
        request: Request,
        watch_type: Optional[str] = Query(None),
        is_active: Optional[bool] = Query(True),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return list_patent_watchlist_items(
            current_user=current_user,
            watch_type=watch_type,
            is_active=is_active,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/patent-watchlist/{item_id}", tags=["Patent Watchlist"])
    @limiter.limit("60/minute")
    async def get_patent_watchlist(
        request: Request,
        item_id: UUID,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return get_patent_watchlist_item(item_id=item_id, current_user=current_user)

    @app.patch("/api/v1/patent-watchlist/{item_id}", tags=["Patent Watchlist"])
    @limiter.limit("30/minute")
    async def update_patent_watchlist(
        request: Request,
        item_id: UUID,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        return update_patent_watchlist_item(
            item_id=item_id, data=data, current_user=current_user,
        )

    @app.delete("/api/v1/patent-watchlist/{item_id}", tags=["Patent Watchlist"])
    @limiter.limit("30/minute")
    async def delete_patent_watchlist(
        request: Request,
        item_id: UUID,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return delete_patent_watchlist_item(
            item_id=item_id, current_user=current_user,
        )

    @app.post("/api/v1/patent-watchlist/{item_id}/scan", tags=["Patent Watchlist"])
    @limiter.limit("10/minute")
    async def scan_patent_watchlist(
        request: Request,
        item_id: UUID,
        current_user=Depends(get_current_user),
    ):
        """Manually trigger a scan for one watchlist item. Background-style:
        synchronous in-request for v1 since scans are fast (sub-second to a
        few seconds). Returns the scan summary with alerts_created count."""
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Hydrate the item (with org-scoping check) and pull the embedding
        # as text so cosine SQL can cast it.
        from database.crud import Database
        from services.patent_scanner_service import scan_and_store

        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, organization_id, user_id, watch_type, label,
                       holder_name, holder_id, holder_tpe_client_id,
                       reference_patent_id, reference_query,
                       reference_embedding::text AS reference_embedding,
                       ipc_classes, kind_codes, customer_application_no,
                       similarity_threshold
                FROM patent_watchlist_mt
                WHERE id = %s AND organization_id = %s
                """,
                (str(item_id), str(current_user.organization_id)),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Watchlist item not found")
            item = dict(row)
            return scan_and_store(db, item)

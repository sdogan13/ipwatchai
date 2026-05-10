"""Patent / Faydalı Model search routes.

Three endpoints + an IPC autocomplete:
  * ``POST /api/v1/patent-search/quick``      — authenticated, full results
  * ``GET/POST /api/v1/patent-search/public`` — anonymous, max 10 results, text-only
  * ``GET /api/v1/patent-search/ipc-autocomplete?q=`` — typeahead over IPC
    classes that actually exist in the patent corpus

The authenticated endpoint computes a query text embedding using the
same SentenceTransformer (multilingual-e5-large) the corpus was indexed
with so retrieval lives in one model space. Public path is text-only —
trigram + ILIKE over titles, no embedding cost.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import Depends, Form, HTTPException, Query, Request


logger = logging.getLogger("turkpatent.patent_search_routes")


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
# Search route handlers
# ---------------------------------------------------------------------------

async def _do_patent_search(
    *,
    query: Optional[str],
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
    if not public and query and len(query.strip()) >= 2:
        # Skip embedding when the query parses as an exact ID — service
        # short-circuits to a direct row lookup so the embed is wasted.
        from services.patent_search_service import parse_id_query
        if not parse_id_query(query):
            text_embedding = _embed_query_text(query)

    with Database() as db:
        return search_patents(
            db.conn,
            query=query,
            text_embedding=text_embedding,
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
    if not query or len(query.strip()) < 2:
        # Filter-only mode is allowed (e.g. "all 2024 patents in IPC A61")
        # but we require at least one filter to be set so we don't
        # accidentally serve the whole corpus.
        any_filter = any([ipc, holder, date_from, date_to, kind_code])
        if not any_filter:
            raise HTTPException(
                status_code=422,
                detail="Provide a query (min 2 chars) or at least one filter",
            )

    if user_id:
        from database.crud import Database
        from utils.subscription import check_quick_search_eligibility
        with Database() as db:
            can_search, _reason, details = check_quick_search_eligibility(db, user_id)
            if not can_search:
                raise HTTPException(status_code=429, detail=details)

    result = await _do_patent_search(
        query=query.strip() if query else None,
        ipc_classes=_parse_csv_param(ipc),
        holder=holder.strip() if holder else None,
        date_from=date_from or None,
        date_to=date_to or None,
        kind_code=kind_code.strip().upper() if kind_code else None,
        limit=limit,
        public=False,
    )

    if user_id:
        from database.crud import Database
        from utils.subscription import increment_quick_search_usage
        with Database() as db:
            increment_quick_search_usage(db, user_id, organization_id)

    return result


async def patent_search_public(
    *,
    query: Optional[str],
    ipc: Optional[str],
) -> dict:
    """Public anonymous text-only patent search. Capped at 10 results."""
    if not query or len(query.strip()) < 2:
        raise HTTPException(status_code=422, detail="Provide a query (min 2 chars)")
    return await _do_patent_search(
        query=query.strip(),
        ipc_classes=_parse_csv_param(ipc),
        holder=None,
        date_from=None,
        date_to=None,
        kind_code=None,
        limit=10,
        public=True,
    )


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
    from auth.authentication import get_current_user

    @app.get("/api/v1/patent-search/public", tags=["Patent Search"])
    @limiter.limit("10/minute")
    async def public_patent_search_get(
        request: Request,
        query: str = Query(..., min_length=2, max_length=200),
        ipc: Optional[str] = Query(None),
    ):
        return await patent_search_public(query=query, ipc=ipc)

    @app.post("/api/v1/patent-search/public", tags=["Patent Search"])
    @limiter.limit("10/minute")
    async def public_patent_search_post(
        request: Request,
        query: Optional[str] = Form(None),
        ipc: Optional[str] = Form(None),
    ):
        return await patent_search_public(query=query, ipc=ipc)

    @app.post("/api/v1/patent-search/quick", tags=["Patent Search"])
    @limiter.limit("60/minute")
    async def quick_patent_search(
        request: Request,
        query: Optional[str] = Form(None),
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
            query=query, ipc=ipc, holder=holder,
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

"""Cross-registry unified search.

Runs lightweight per-registry retrieval against trademarks AND designs in
parallel, merges results by raw cosine similarity (same scale on both sides,
since both use direct vector cosine + trigram), and returns a unified
response where each row carries a ``registry_type`` discriminator.

Companion to ``services/design_search_service.py`` (designs only) and
``services/search_service.py`` (trademarks with the heavy RiskEngine
scoring). This module is the "discovery" surface — fast cross-IP lookup —
not "deep risk analysis" on either registry.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence

from services.design_search_service import (
    DEFAULT_LIMIT,
    TRIGRAM_THRESHOLD,
    cap_limit,
    combine_scores,
    normalize_locarno_filter,
    search_designs,
    to_halfvec_literal,
)

logger = logging.getLogger("turkpatent.registry_search")

# Per-registry candidate cap before global merge.
DEFAULT_PER_REGISTRY_CANDIDATES = 100

# Trademarks have their own status enum; exclude clearly-inactive rows by
# default. Mirrors the design-side INACTIVE_STATUSES idea.
TRADEMARK_INACTIVE_STATUSES = ("Reddedildi", "Süresi Doldu", "Geri Çekildi")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def normalize_nice_filter(values: Optional[Sequence[Any]]) -> Optional[List[int]]:
    """Coerce ``[35, '41', '99']`` -> ``[35, 41, 99]``. None / empty -> None."""
    if not values:
        return None
    out: List[int] = []
    for v in values:
        if v is None or v == "":
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n in out:
            continue
        out.append(n)
    return out or None


def parse_registries(values: Optional[Sequence[str]]) -> List[str]:
    """Accept any of ``['trademark']``, ``['design']``, ``['trademark','design']``;
    default to both. Drop unknown values silently."""
    allowed = {"trademark", "design"}
    if not values:
        return ["trademark", "design"]
    seen: List[str] = []
    for v in values:
        if v in allowed and v not in seen:
            seen.append(v)
    return seen or ["trademark", "design"]


# ---------------------------------------------------------------------------
# Trademark-side retrieval (lightweight cosine + trigram)
# ---------------------------------------------------------------------------

def _retrieve_trademark_candidates(
    cur,
    *,
    query: Optional[str],
    image_embeddings: Optional[Dict[str, Any]],
    nice_classes: Optional[List[int]],
    limit: int,
) -> List[Dict[str, Any]]:
    """Direct cosine + trigram retrieval against the trademarks table.

    Returns a list of normalized result dicts (registry_type='trademark').
    Lightweight by design — does NOT use the RiskEngine. Use
    ``/api/v1/search/quick`` for full risk-engine scoring on a single
    trademark query.
    """
    by_id: Dict[str, Dict[str, float]] = {}
    nice_clause = ""
    if nice_classes:
        nice_clause = " AND (nice_class_numbers && %(nice)s::integer[] OR 99 = ANY(nice_class_numbers))"

    has_query = bool(query and query.strip())
    has_image = bool(image_embeddings)

    # Text trigram on trademarks.name
    if has_query:
        sql = f"""
            SELECT id::text AS rid,
                   similarity(LOWER(COALESCE(name,'')), LOWER(%(q)s)) AS sim
            FROM trademarks
            WHERE name IS NOT NULL
              AND current_status NOT IN %(inactive)s
              AND (
                  LOWER(name) LIKE LOWER(%(qlike)s)
                  OR similarity(LOWER(name), LOWER(%(q)s)) > %(thresh)s
              )
              {nice_clause}
            ORDER BY sim DESC
            LIMIT %(limit)s
        """
        cur.execute(sql, {
            "q": query.strip(), "qlike": f"%{query.strip()}%",
            "thresh": TRIGRAM_THRESHOLD,
            "inactive": TRADEMARK_INACTIVE_STATUSES,
            "nice": nice_classes, "limit": limit,
        })
        for row in cur.fetchall():
            rid = row[0]
            entry = by_id.setdefault(rid, {"text": 0.0, "dinov2": 0.0, "clip": 0.0, "color": 0.0})
            entry["text"] = max(entry["text"], float(row[1] or 0.0))

    # Image-side cosine retrieval (per-vector column)
    if has_image:
        for emb_key, column in (
            ("dinov2_vitb14", "dinov2_embedding"),
            ("clip_vitb32", "image_embedding"),
            ("color_hsv", "color_histogram"),
        ):
            vec = image_embeddings.get(emb_key)
            lit = to_halfvec_literal(vec) if vec is not None else None
            if not lit:
                continue
            sql = f"""
                SELECT id::text AS rid,
                       1 - ({column} <=> %(vec)s::halfvec) AS sim
                FROM trademarks
                WHERE {column} IS NOT NULL
                  AND current_status NOT IN %(inactive)s
                  {nice_clause}
                ORDER BY {column} <=> %(vec)s::halfvec
                LIMIT %(limit)s
            """
            cur.execute(sql, {
                "vec": lit,
                "inactive": TRADEMARK_INACTIVE_STATUSES,
                "nice": nice_classes, "limit": limit,
            })
            sim_field = {"dinov2_vitb14": "dinov2", "clip_vitb32": "clip",
                         "color_hsv": "color"}[emb_key]
            for row in cur.fetchall():
                rid = row[0]
                entry = by_id.setdefault(rid, {"text": 0.0, "dinov2": 0.0, "clip": 0.0, "color": 0.0})
                entry[sim_field] = max(entry[sim_field], float(row[1] or 0.0))

    if not by_id:
        return []

    # Hydrate candidate trademarks with the columns we display
    cur.execute(
        """
        SELECT t.id::text, t.application_no, t.registration_no, t.name,
               t.nice_class_numbers, t.current_status, t.application_date,
               t.image_path,
               h.name AS holder_name, h.tpe_client_id
        FROM trademarks t
        LEFT JOIN holders h ON h.id = t.holder_id
        WHERE t.id::text = ANY(%(ids)s)
        """,
        {"ids": list(by_id.keys())},
    )
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        (rid, app_no, reg_no, name, nice, status, app_date,
         image_path, holder_name, tpe_id) = row
        sims = by_id.get(rid, {})
        score = combine_scores(
            text=sims.get("text", 0.0),
            dinov2=sims.get("dinov2", 0.0),
            clip=sims.get("clip", 0.0),
            color=sims.get("color", 0.0),
            has_image=has_image,
        )
        out.append({
            "registry_type": "trademark",
            "id": rid,
            "application_no": app_no,
            "registration_no": reg_no,
            "title": name,
            "holder": {"name": holder_name, "tpe_client_id": tpe_id} if holder_name else None,
            "image_url": _trademark_image_url(image_path) if image_path else None,
            "similarity": round(score * 100.0, 2),
            "similarity_breakdown": {
                "text": round(float(sims.get("text", 0.0)), 4),
                "dinov2": round(float(sims.get("dinov2", 0.0)), 4),
                "clip": round(float(sims.get("clip", 0.0)), 4),
                "color": round(float(sims.get("color", 0.0)), 4),
            },
            "trademark": {
                "nice_classes": list(nice or []),
                "current_status": status,
                "application_date": app_date.isoformat() if app_date else None,
            },
            "design": None,
        })
    return out


def _trademark_image_url(image_path: str) -> str:
    """Mirror the URL shape used by the trademark image route."""
    return f"/api/trademark-image/{image_path.lstrip('/')}"


# ---------------------------------------------------------------------------
# Design-side normalization (wraps the existing design service result)
# ---------------------------------------------------------------------------

def _normalize_design_result(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a design search result into the unified schema."""
    return {
        "registry_type": "design",
        "id": row.get("id"),
        "application_no": row.get("application_no"),
        "registration_no": row.get("registration_no"),
        "title": row.get("product_name_tr") or row.get("product_name_en"),
        "holder": row.get("holder"),
        "image_url": row.get("image_url"),
        "similarity": row.get("similarity"),
        "similarity_breakdown": row.get("similarity_breakdown") or {},
        "trademark": None,
        "design": {
            "design_index": row.get("design_index"),
            "locarno_classes": list(row.get("locarno_classes") or []),
            "section": row.get("section"),
            "current_status": row.get("current_status"),
            "designers": list(row.get("designers") or []),
            "bulletin_no": row.get("bulletin_no"),
            "bulletin_date": row.get("bulletin_date"),
            "hague_reference": row.get("hague_reference"),
            "deferred_publication": row.get("deferred_publication"),
        },
    }


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def search_unified(
    conn,
    *,
    query: Optional[str] = None,
    image_embeddings_design: Optional[Dict[str, Any]] = None,
    image_embeddings_trademark: Optional[Dict[str, Any]] = None,
    nice_classes: Optional[Sequence[Any]] = None,
    locarno_classes: Optional[Sequence[str]] = None,
    registries: Optional[Sequence[str]] = None,
    limit: int = DEFAULT_LIMIT,
    public: bool = False,
) -> Dict[str, Any]:
    """Run searches against requested registries in parallel and merge."""
    started = time.time()
    limit = cap_limit(limit, public=public)
    registries_clean = parse_registries(registries)
    nice = normalize_nice_filter(nice_classes)
    locarno = normalize_locarno_filter(locarno_classes)
    has_query = bool(query and len(query.strip()) >= 2)
    has_image = bool(image_embeddings_design or image_embeddings_trademark)
    if not has_query and not has_image:
        return {
            "results": [], "total": 0, "duration_ms": 0,
            "by_registry": {"trademark": 0, "design": 0},
            "error": "registry_search.empty_query",
        }

    candidates: List[Dict[str, Any]] = []

    if "design" in registries_clean:
        design_payload = search_designs(
            conn,
            query=query,
            image_embeddings=image_embeddings_design,
            locarno_classes=locarno,
            limit=DEFAULT_PER_REGISTRY_CANDIDATES,
            public=False,
        )
        for row in design_payload.get("results", []):
            candidates.append(_normalize_design_result(row))

    if "trademark" in registries_clean:
        with conn.cursor() as cur:
            tm_rows = _retrieve_trademark_candidates(
                cur,
                query=query,
                image_embeddings=image_embeddings_trademark,
                nice_classes=nice,
                limit=DEFAULT_PER_REGISTRY_CANDIDATES,
            )
        candidates.extend(tm_rows)

    candidates.sort(key=lambda c: c.get("similarity", 0) or 0, reverse=True)
    final = candidates[:limit]
    by_registry = {"trademark": 0, "design": 0}
    for c in final:
        rt = c.get("registry_type")
        if rt in by_registry:
            by_registry[rt] += 1

    return {
        "results": final,
        "total": len(final),
        "by_registry": by_registry,
        "duration_ms": int((time.time() - started) * 1000),
        "filters": {
            "has_query": has_query,
            "has_image": has_image,
            "registries": registries_clean,
            "nice_classes": nice,
            "locarno_classes": locarno,
            "limit": limit,
            "public": public,
        },
    }

"""Tasarım (industrial design) search/retrieval service.

Sister to ``services/search_service.py`` (Marka). Lightweight design-tuned
implementation:

  * No phonetic matching (product names are generic, e.g. "Sandalye")
  * No OCR retrieval (designs have no logo text)
  * No translation Path A/B (single Turkish + occasional English on Hague)
  * Locarno class filter instead of Nice
  * Visual signal dominates: DINOv2 ViT-L/14 (1024-dim) primary, CLIP ViT-B/32
    (512-dim) secondary, HSV (512-dim) tertiary
  * Trigram similarity on ``product_name_tr`` for text queries

This module owns: candidate retrieval (SQL builder), score combination,
result mapping. Authentication, rate limiting, and quota checks live in
the route layer (``app_design_search_routes.py``).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


logger = logging.getLogger("turkpatent.design_search")

# Score weights — tuned for design retrieval where visual dominates.
WEIGHTS_IMAGE_QUERY = {"dinov2": 0.55, "clip": 0.30, "color": 0.10, "text": 0.05}
WEIGHTS_TEXT_QUERY = {"text": 0.70, "dinov2": 0.20, "clip": 0.10, "color": 0.0}

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
PUBLIC_RESULT_CAP = 10
TRIGRAM_THRESHOLD = 0.2

# Statuses excluded from default results — "active rights only" view.
INACTIVE_STATUSES = ("İptal Edildi", "Hükümsüz")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without DB)
# ---------------------------------------------------------------------------

def to_halfvec_literal(values: Optional[Sequence[float]]) -> Optional[str]:
    """``[v1,v2,...]`` literal for casting to halfvec(N) in SQL."""
    if values is None:
        return None
    materialized = list(values)
    if not materialized:
        return None
    return "[" + ",".join(f"{float(v):.6f}" for v in materialized) + "]"


def combine_scores(
    *,
    text: float = 0.0,
    dinov2: float = 0.0,
    clip: float = 0.0,
    color: float = 0.0,
    has_image: bool = False,
) -> float:
    """Combine per-signal similarities into an overall 0..1 score."""
    weights = WEIGHTS_IMAGE_QUERY if has_image else WEIGHTS_TEXT_QUERY
    score = (
        weights["text"] * max(0.0, text)
        + weights["dinov2"] * max(0.0, dinov2)
        + weights["clip"] * max(0.0, clip)
        + weights["color"] * max(0.0, color)
    )
    return min(1.0, score)


def normalize_locarno_filter(values: Optional[Sequence[str]]) -> Optional[List[str]]:
    """Accepts ``['06-01','06.02','06']`` -> normalized ``['06-01','06-02','06']``.
    Returns ``None`` when nothing usable is left so SQL can skip the filter."""
    if not values:
        return None
    out: List[str] = []
    for v in values:
        if not v:
            continue
        cleaned = v.strip().replace(".", "-")
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out or None


def _locarno_filter_clause(*, table_alias: str = "") -> str:
    """SQL clause that matches a Locarno filter against ``designs``.

    Top-level filter values (e.g. '07') match designs whose ``locarno_classes``
    contains '07' OR any '07-XX' subclass. Subclass filter values ('07-02')
    still require exact match. Called with ``table_alias='d'`` when the
    designs table is aliased in the calling SQL.
    """
    col = f"{table_alias}.locarno_classes" if table_alias else "locarno_classes"
    return (
        f" AND ({col} && %(locarno)s::text[]"
        f" OR EXISTS (SELECT 1 FROM unnest({col}) c"
        f" WHERE split_part(c, '-', 1) = ANY(%(locarno)s::text[])))"
    )


def cap_limit(value: Any, *, public: bool = False) -> int:
    """Coerce a limit param into the allowed range."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = DEFAULT_LIMIT
    if n < 1:
        n = 1
    cap = PUBLIC_RESULT_CAP if public else MAX_LIMIT
    return min(n, cap)


def design_image_url(image_path: Optional[str], source_folder: Optional[str]) -> Optional[str]:
    """Build a frontend-resolvable URL for a design view image.

    Image paths in the DB are stored relative to the issue folder, e.g.
    ``images/2024_007254_1_1.jpg``. The full filesystem path is
    ``bulletins/Tasarim/{source_folder}/images/{filename}``. The serving
    route reads under ``bulletins/Tasarim``, so we prepend the folder.
    """
    if not image_path or not source_folder:
        return None
    rel = image_path.lstrip("/")
    return f"/api/v1/design-image/{source_folder}/{rel}"


# ---------------------------------------------------------------------------
# Candidate retrieval
# ---------------------------------------------------------------------------

@dataclass
class DesignCandidate:
    design_id: str
    text_sim: float = 0.0
    dinov2_sim: float = 0.0
    clip_sim: float = 0.0
    color_sim: float = 0.0


def _retrieve_text_candidates(
    cur, query: str, locarno: Optional[List[str]], limit: int,
) -> List[DesignCandidate]:
    """Trigram + ILIKE retrieval over product_name_tr."""
    if not query:
        return []
    locarno_clause = _locarno_filter_clause() if locarno else ""
    sql = f"""
        SELECT id::text AS design_id,
               similarity(LOWER(COALESCE(product_name_tr,'')), LOWER(%(q)s)) AS sim
        FROM designs
        WHERE current_status NOT IN %(inactive)s
          AND product_name_tr IS NOT NULL
          AND (
              LOWER(product_name_tr) LIKE LOWER(%(qlike)s)
              OR similarity(LOWER(product_name_tr), LOWER(%(q)s)) > %(thresh)s
          )
          {locarno_clause}
        ORDER BY sim DESC
        LIMIT %(limit)s
    """
    cur.execute(sql, {
        "q": query, "qlike": f"%{query}%",
        "thresh": TRIGRAM_THRESHOLD,
        "inactive": INACTIVE_STATUSES,
        "locarno": locarno, "limit": limit,
    })
    return [DesignCandidate(design_id=row[0], text_sim=float(row[1] or 0.0))
            for row in cur.fetchall()]


def _retrieve_vector_candidates(
    cur, *, column: str, vec_literal: str,
    locarno: Optional[List[str]], limit: int, sim_field: str,
) -> List[DesignCandidate]:
    """Cosine retrieval against one of the design aggregate vector columns."""
    locarno_clause = _locarno_filter_clause() if locarno else ""
    sql = f"""
        SELECT id::text AS design_id,
               1 - ({column} <=> %(vec)s::halfvec) AS sim
        FROM designs
        WHERE current_status NOT IN %(inactive)s
          AND {column} IS NOT NULL
          {locarno_clause}
        ORDER BY {column} <=> %(vec)s::halfvec
        LIMIT %(limit)s
    """
    cur.execute(sql, {
        "vec": vec_literal, "inactive": INACTIVE_STATUSES,
        "locarno": locarno, "limit": limit,
    })
    out: List[DesignCandidate] = []
    for row in cur.fetchall():
        cand = DesignCandidate(design_id=row[0])
        setattr(cand, sim_field, float(row[1] or 0.0))
        out.append(cand)
    return out


def _retrieve_color_candidates_via_views(
    cur, vec_literal: str, locarno: Optional[List[str]], limit: int,
) -> List[DesignCandidate]:
    """Color HSV is per-view only; for design-level retrieval take the
    minimum distance across the design's views."""
    locarno_clause = _locarno_filter_clause(table_alias="d") if locarno else ""
    sql = f"""
        SELECT d.id::text AS design_id,
               1 - MIN(dv.color_hsv <=> %(vec)s::halfvec) AS sim
        FROM design_views dv
        JOIN designs d ON d.id = dv.design_id
        WHERE dv.color_hsv IS NOT NULL
          AND d.current_status NOT IN %(inactive)s
          {locarno_clause}
        GROUP BY d.id
        ORDER BY sim DESC
        LIMIT %(limit)s
    """
    cur.execute(sql, {
        "vec": vec_literal, "inactive": INACTIVE_STATUSES,
        "locarno": locarno, "limit": limit,
    })
    return [DesignCandidate(design_id=row[0], color_sim=float(row[1] or 0.0))
            for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Hydrate & rank
# ---------------------------------------------------------------------------

HYDRATE_COLS = (
    "d.id::text AS design_id, d.registry_type, d.application_no, d.design_index, d.registration_no, "
    "d.product_name_tr, d.product_name_en, d.locarno_classes, d.section, "
    "d.bulletin_no, d.bulletin_date, d.application_date, d.filing_date, d.registration_date, "
    "d.current_status, d.designers, d.attorney_name, d.attorney_firm, "
    "d.hague_reference, d.deferred_publication, d.source_issue_folder, "
    "d.holder_id, h.name AS holder_name, h.tpe_client_id, h.country AS holder_country, "
    "(SELECT image_path FROM design_views dv "
    " WHERE dv.design_id = d.id AND dv.image_path IS NOT NULL "
    " ORDER BY dv.view_index ASC LIMIT 1) AS first_image_path"
)


def _hydrate_designs(cur, design_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Look up full row data for a candidate set. Returns ``{design_id: row_dict}``."""
    if not design_ids:
        return {}
    sql = f"""
        SELECT {HYDRATE_COLS}
        FROM designs d
        LEFT JOIN holders h ON h.id = d.holder_id
        WHERE d.id::text = ANY(%(ids)s)
    """
    cur.execute(sql, {"ids": list(design_ids)})
    cols = [desc[0] for desc in cur.description]
    out: Dict[str, Dict[str, Any]] = {}
    for row in cur.fetchall():
        record = dict(zip(cols, row))
        out[record["design_id"]] = record
    return out


def _result_row(record: Dict[str, Any], *, similarity: float, breakdown: Dict[str, float]) -> Dict[str, Any]:
    holder = None
    if record.get("holder_name"):
        holder = {
            "name": record["holder_name"],
            "tpe_client_id": record.get("tpe_client_id"),
            "country": record.get("holder_country"),
        }
    image_url = design_image_url(record.get("first_image_path"), record.get("source_issue_folder"))
    bulletin_date = record.get("bulletin_date")
    application_date = record.get("application_date")
    filing_date = record.get("filing_date")
    registration_date = record.get("registration_date")
    return {
        "id": record["design_id"],
        "registry_type": record.get("registry_type") or "design",
        "application_no": record.get("application_no"),
        "design_index": record.get("design_index"),
        "registration_no": record.get("registration_no"),
        "product_name_tr": record.get("product_name_tr"),
        "product_name_en": record.get("product_name_en"),
        "locarno_classes": list(record.get("locarno_classes") or []),
        "section": record.get("section"),
        "current_status": record.get("current_status"),
        "bulletin_no": record.get("bulletin_no"),
        "bulletin_date": bulletin_date.isoformat() if bulletin_date else None,
        "application_date": application_date.isoformat() if application_date else None,
        "filing_date": filing_date.isoformat() if filing_date else None,
        "registration_date": registration_date.isoformat() if registration_date else None,
        "holder": holder,
        "designers": list(record.get("designers") or []),
        "attorney_name": record.get("attorney_name"),
        "attorney_firm": record.get("attorney_firm"),
        "image_url": image_url,
        "similarity": round(similarity * 100.0, 2),
        "similarity_breakdown": {k: round(float(v or 0.0), 4) for k, v in breakdown.items()},
        "hague_reference": record.get("hague_reference"),
        "deferred_publication": record.get("deferred_publication"),
    }


# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------

def search_designs(
    conn,
    *,
    query: Optional[str] = None,
    image_embeddings: Optional[Dict[str, Sequence[float]]] = None,
    locarno_classes: Optional[Sequence[str]] = None,
    limit: int = DEFAULT_LIMIT,
    public: bool = False,
) -> Dict[str, Any]:
    """Run a design search and return a serializable response dict.

    ``image_embeddings`` (when provided) should map at least
    ``dinov2_vitl14`` and ``clip_vitb32`` and optionally ``color_hsv``
    to plain-list embeddings; the route layer is responsible for
    extracting them via the same model loaders the embedding pipeline
    uses.
    """
    started = time.time()
    locarno = normalize_locarno_filter(locarno_classes)
    limit = cap_limit(limit, public=public)
    has_image = bool(image_embeddings)
    has_query = bool(query and len(query.strip()) >= 2)
    if not has_image and not has_query:
        return {"results": [], "total": 0, "duration_ms": 0,
                "error": "design_search.empty_query"}

    candidates: Dict[str, DesignCandidate] = {}

    def merge(c: DesignCandidate) -> None:
        existing = candidates.get(c.design_id)
        if existing is None:
            candidates[c.design_id] = c
            return
        existing.text_sim = max(existing.text_sim, c.text_sim)
        existing.dinov2_sim = max(existing.dinov2_sim, c.dinov2_sim)
        existing.clip_sim = max(existing.clip_sim, c.clip_sim)
        existing.color_sim = max(existing.color_sim, c.color_sim)

    with conn.cursor() as cur:
        if has_query:
            for c in _retrieve_text_candidates(cur, query.strip(), locarno, limit=200):
                merge(c)
        if has_image:
            dino_lit = to_halfvec_literal(image_embeddings.get("dinov2_vitl14"))
            if dino_lit:
                for c in _retrieve_vector_candidates(
                    cur, column="dinov2_vitl14_mean", vec_literal=dino_lit,
                    locarno=locarno, limit=200, sim_field="dinov2_sim",
                ):
                    merge(c)
            clip_lit = to_halfvec_literal(image_embeddings.get("clip_vitb32"))
            if clip_lit:
                for c in _retrieve_vector_candidates(
                    cur, column="clip_vitb32_mean", vec_literal=clip_lit,
                    locarno=locarno, limit=100, sim_field="clip_sim",
                ):
                    merge(c)
            color_lit = to_halfvec_literal(image_embeddings.get("color_hsv"))
            if color_lit:
                for c in _retrieve_color_candidates_via_views(
                    cur, vec_literal=color_lit, locarno=locarno, limit=100,
                ):
                    merge(c)

        # Score + rank
        ranked = sorted(
            candidates.values(),
            key=lambda c: combine_scores(
                text=c.text_sim, dinov2=c.dinov2_sim,
                clip=c.clip_sim, color=c.color_sim,
                has_image=has_image,
            ),
            reverse=True,
        )[:limit]

        hydrated = _hydrate_designs(cur, [c.design_id for c in ranked])

    results: List[Dict[str, Any]] = []
    for c in ranked:
        record = hydrated.get(c.design_id)
        if not record:
            continue
        score = combine_scores(
            text=c.text_sim, dinov2=c.dinov2_sim,
            clip=c.clip_sim, color=c.color_sim,
            has_image=has_image,
        )
        breakdown = {
            "text": c.text_sim, "dinov2": c.dinov2_sim,
            "clip": c.clip_sim, "color": c.color_sim,
        }
        results.append(_result_row(record, similarity=score, breakdown=breakdown))

    return {
        "results": results,
        "total": len(results),
        "duration_ms": int((time.time() - started) * 1000),
        "filters": {
            "locarno_classes": locarno,
            "has_query": has_query,
            "has_image": has_image,
            "public": public,
        },
    }


# ---------------------------------------------------------------------------
# Public design portfolio lookup (mirrors run_public_portfolio_lookup in
# services/search_service.py but for the `designs` table, joined to
# `holders` via holder_id). Used by the dashboard design result card's
# "Sahip" portfolio link.
# ---------------------------------------------------------------------------

# Mirror PUBLIC_PORTFOLIO_RESULT_CAP from the trademark portfolio so the
# anonymous design portfolio surface stays consistent with trademark.
_DESIGN_PORTFOLIO_PUBLIC_CAP = 10


async def run_public_design_portfolio_lookup(
    *,
    holder_id: Optional[str] = None,
    logger=None,
) -> Dict[str, Any]:
    """Return up to 10 designs for a given holder.

    Response shape matches the trademark public portfolio so the
    dashboard's existing portfolio modal can render either registry.
    """
    from fastapi import HTTPException

    from database.crud import Database

    if not holder_id:
        raise HTTPException(
            status_code=422,
            detail="holder_id is required",
        )

    holder_id = str(holder_id).strip()
    if not holder_id:
        raise HTTPException(status_code=422, detail="holder_id is required")

    with Database() as db:
        cur = db.cursor()

        cur.execute(
            "SELECT id, name, tpe_client_id FROM holders WHERE tpe_client_id = %s LIMIT 1",
            (holder_id,),
        )
        holder_row = cur.fetchone()
        if not holder_row:
            raise HTTPException(status_code=404, detail="Holder not found")
        holder_name = holder_row["name"] or holder_id

        cur.execute(
            "SELECT COUNT(*) AS cnt FROM designs WHERE holder_id = %s",
            (holder_row["id"],),
        )
        total_count = int(cur.fetchone()["cnt"] or 0)

        cur.execute(
            """
            SELECT d.id::text AS id,
                   d.application_no, d.design_index, d.registration_no,
                   d.product_name_tr, d.product_name_en,
                   d.locarno_classes, d.current_status,
                   d.application_date, d.registration_date,
                   d.bulletin_no, d.bulletin_date,
                   d.designers, d.source_issue_folder,
                   (SELECT image_path FROM design_views dv
                    WHERE dv.design_id = d.id AND dv.image_path IS NOT NULL
                    ORDER BY dv.view_index ASC LIMIT 1) AS first_image_path
            FROM designs d
            WHERE d.holder_id = %s
            ORDER BY d.application_date DESC NULLS LAST, d.application_no DESC
            LIMIT %s
            """,
            (holder_row["id"], _DESIGN_PORTFOLIO_PUBLIC_CAP),
        )
        rows = cur.fetchall()

    results: List[Dict[str, Any]] = []
    for d in rows:
        bulletin_date = d.get("bulletin_date")
        application_date = d.get("application_date")
        registration_date = d.get("registration_date")
        image_url = design_image_url(
            d.get("first_image_path"),
            d.get("source_issue_folder"),
        )
        title = d.get("product_name_tr") or d.get("product_name_en") or d.get("application_no")
        results.append({
            "id": d["id"],
            "registry_type": "design",
            "application_no": d.get("application_no"),
            "design_index": d.get("design_index"),
            "registration_no": d.get("registration_no"),
            "name": title,
            "product_name_tr": d.get("product_name_tr"),
            "product_name_en": d.get("product_name_en"),
            "locarno_classes": list(d.get("locarno_classes") or []),
            "current_status": d.get("current_status"),
            "application_date": application_date.isoformat() if application_date else None,
            "registration_date": registration_date.isoformat() if registration_date else None,
            "bulletin_no": d.get("bulletin_no"),
            "bulletin_date": bulletin_date.isoformat() if bulletin_date else None,
            "designers": list(d.get("designers") or []),
            "image_url": image_url,
        })

    return {
        "entity_type": "design-holder",
        "entity_id": holder_id,
        "entity_name": holder_name,
        "results": results,
        "total_count": total_count,
    }


async def build_public_design_portfolio_csv(
    *,
    holder_id: Optional[str] = None,
    logger=None,
    current_user=None,
):
    """Stream a CSV of every design for the given holder.

    Mirrors build_public_portfolio_csv (search_service.py:455) and is
    gated by ``can_download_portfolio`` on the current user's plan.
    """
    import csv
    import io

    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse

    from database.crud import Database

    if not holder_id:
        raise HTTPException(status_code=422, detail="holder_id is required")

    holder_id = str(holder_id).strip()
    if not holder_id:
        raise HTTPException(status_code=422, detail="holder_id is required")

    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    from utils.subscription import get_plan_limit, get_user_plan

    with Database() as db:
        plan = get_user_plan(db, str(current_user.id))
        if not get_plan_limit(plan["plan_name"], "can_download_portfolio"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "upgrade_required",
                    "message": "CSV export is available on paid plans.",
                    "current_plan": plan["plan_name"],
                    "upgrade_context": "portfolio_download",
                },
            )

        cur = db.cursor()
        cur.execute(
            "SELECT id, name FROM holders WHERE tpe_client_id = %s LIMIT 1",
            (holder_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Holder not found")
        holder_internal_id = row["id"]
        holder_name = row["name"] or holder_id

        cur.execute(
            """
            SELECT d.application_no, d.registration_no,
                   d.product_name_tr, d.product_name_en,
                   d.current_status, d.locarno_classes,
                   d.application_date, d.registration_date,
                   d.bulletin_no, d.designers,
                   d.attorney_name, d.attorney_firm
            FROM designs d
            WHERE d.holder_id = %s
            ORDER BY d.application_date DESC NULLS LAST, d.application_no DESC
            """,
            (holder_internal_id,),
        )
        rows = cur.fetchall()

    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf)
    writer.writerow([
        "Tasarim Adi (TR)",
        "Tasarim Adi (EN)",
        "Basvuru No",
        "Tescil No",
        "Durum",
        "Locarno Siniflari",
        "Basvuru Tarihi",
        "Tescil Tarihi",
        "Bulten No",
        "Tasarimcilar",
        "Vekil",
        "Vekil Firmasi",
    ])
    for d in rows:
        writer.writerow([
            d.get("product_name_tr") or "",
            d.get("product_name_en") or "",
            d.get("application_no") or "",
            d.get("registration_no") or "",
            d.get("current_status") or "",
            "; ".join(str(c) for c in (d.get("locarno_classes") or [])),
            d["application_date"].isoformat() if d.get("application_date") else "",
            d["registration_date"].isoformat() if d.get("registration_date") else "",
            d.get("bulletin_no") or "",
            "; ".join(str(p) for p in (d.get("designers") or [])),
            d.get("attorney_name") or "",
            d.get("attorney_firm") or "",
        ])

    safe_name = "".join(
        c if c.isascii() and (c.isalnum() or c in " _-") else "_" for c in holder_name
    )[:50]
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_design_portfolio.csv"',
        },
    )

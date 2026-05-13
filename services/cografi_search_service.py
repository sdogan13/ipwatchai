"""Coğrafi İşaret ve Geleneksel Ürün Adı search/retrieval service.

Sister to ``services/patent_search_service.py`` (Patent),
``services/design_search_service.py`` (Tasarım), and
``services/search_service.py`` (Marka). Cografi-tuned implementation:

  * Text-first hybrid: trigram on ``name`` + cosine on
    ``text_embedding`` (1024-dim e5-large passage). Coverage is
    100% for text since every record always has a name.
  * Figure (image) signal: cosine on
    ``cografi_records.primary_figure_embedding`` (mean-pooled DINOv2).
    Coverage is partial — ~41% of records have at least one figure —
    so figure similarity is ranked alongside text, not as the sole
    signal in hybrid mode.
  * Exact-ID shortcut: ``C{YYYY}/{NNNNNN}`` patterns short-circuit to
    a direct ``application_no`` lookup; bare integers short-circuit
    to ``registration_no`` lookup.
  * Filters: section_key, record_type, gi_type, region (trigram on
    ``geographical_boundary``), bulletin_date range, application_no,
    registration_no.
  * Excluded by default: corrections + gazette_only_announcements
    (administrative records that aren't useful in name searches; a
    ``include_admin`` flag can re-enable them).

This module owns: candidate retrieval (SQL builder), score combination,
result mapping. Authentication, rate limiting, quota checks live in
the route layer (``app_cografi_search_routes.py``).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


logger = logging.getLogger("turkpatent.cografi_search")

# Score weights — picked per query mode so the dominant signal wins:
#   * Text only         (default)      : text 0.4, embedding 0.6
#   * Text + image      (hybrid)       : text 0.25, embedding 0.35, figure 0.40
#   * Image only        (visual lookup): figure 1.0
WEIGHTS_TEXT_ONLY  = {"text": 0.4,  "embedding": 0.6,  "figure": 0.0}
WEIGHTS_HYBRID     = {"text": 0.25, "embedding": 0.35, "figure": 0.40}
WEIGHTS_IMAGE_ONLY = {"text": 0.0,  "embedding": 0.0,  "figure": 1.0}
# Backwards-compat alias used by some pure-helper tests.
WEIGHTS = WEIGHTS_TEXT_ONLY

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
PUBLIC_RESULT_CAP = 10
TRIGRAM_THRESHOLD = 0.2
CANDIDATE_POOL = 200

# Section keys excluded from default browse / search results. Corrections
# (Düzeltmeler) and gazette-only announcements are administrative
# bookkeeping rather than substantive GI records — they pollute name
# searches without adding value. Caller can opt in via include_admin.
DEFAULT_EXCLUDED_SECTIONS = ("corrections", "gazette_only_announcements")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

# Application-no shape: "C2022/000469" — single C, 4-digit year, 3-6 digit serial.
_APPNO_RE = re.compile(
    r"""^
    \s*
    C\s*
    (\d{4})                              # year
    \s*/\s*
    (\d{3,6})                            # serial
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
# Registration-no shape: bare positive integer (1838, 268, etc.).
_REGNO_RE = re.compile(r"^\s*(\d{1,5})\s*$")


def parse_id_query(query: str) -> Optional[Dict[str, Any]]:
    """Detect a query that's actually an application_no or registration_no.

    Returns one of:
      * ``{"application_no": "C2022/000469"}`` for application-no queries
      * ``{"registration_no": 1838}`` for registration-no queries
      * ``None`` when the query is regular search text
    """
    if not query:
        return None
    m = _APPNO_RE.match(query)
    if m:
        return {"application_no": f"C{m.group(1)}/{m.group(2)}"}
    m = _REGNO_RE.match(query)
    if m:
        return {"registration_no": int(m.group(1))}
    return None


def to_halfvec_literal(values: Optional[Sequence[float]]) -> Optional[str]:
    """``[v1,v2,...]`` literal for casting to ``halfvec(N)`` in SQL."""
    if values is None:
        return None
    materialized = list(values)
    if not materialized:
        return None
    return "[" + ",".join(f"{float(v):.6f}" for v in materialized) + "]"


def combine_scores(
    *,
    text: float = 0.0,
    embedding: float = 0.0,
    figure: float = 0.0,
    has_image: bool = False,
    has_text_query: bool = True,
) -> float:
    """Combine per-signal similarities into an overall 0..1 score."""
    if has_image and not has_text_query:
        weights = WEIGHTS_IMAGE_ONLY
    elif has_image:
        weights = WEIGHTS_HYBRID
    else:
        weights = WEIGHTS_TEXT_ONLY
    score = (
        weights["text"] * max(0.0, text)
        + weights["embedding"] * max(0.0, embedding)
        + weights["figure"] * max(0.0, figure)
    )
    return min(1.0, score)


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


def normalize_section_keys(values: Optional[Sequence[str]]) -> Optional[List[str]]:
    """Trim, lowercase, dedup section_key filter values."""
    if not values:
        return None
    out: List[str] = []
    for v in values:
        if not v:
            continue
        cleaned = v.strip().lower()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out or None


def normalize_record_types(values: Optional[Sequence[str]]) -> Optional[List[str]]:
    """Trim, uppercase, dedup record_type filter values."""
    if not values:
        return None
    out: List[str] = []
    for v in values:
        if not v:
            continue
        cleaned = v.strip().upper()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out or None


# ---------------------------------------------------------------------------
# Candidate retrieval
# ---------------------------------------------------------------------------

@dataclass
class CografiCandidate:
    record_id: str
    text_sim: float = 0.0
    embedding_sim: float = 0.0
    figure_sim: float = 0.0


def _filter_clauses(
    *,
    section_keys: Optional[List[str]],
    record_types: Optional[List[str]],
    gi_type: Optional[str],
    region: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    application_no: Optional[str],
    registration_no: Optional[int],
    include_admin: bool,
    table_alias: str = "r",
) -> Tuple[str, Dict[str, Any]]:
    """Build the SQL WHERE clause + params shared by every retrieval query.

    Always excludes administrative section_keys (corrections,
    gazette_only_announcements) unless ``include_admin=True`` so they
    don't pollute default browse / search results.
    """
    parts: List[str] = []
    params: Dict[str, Any] = {}
    if section_keys:
        parts.append(f" AND {table_alias}.section_key::text = ANY(%(section_keys)s)")
        params["section_keys"] = section_keys
    elif not include_admin:
        parts.append(f" AND {table_alias}.section_key::text NOT IN %(excluded_sections)s")
        params["excluded_sections"] = DEFAULT_EXCLUDED_SECTIONS
    if record_types:
        parts.append(f" AND {table_alias}.record_type::text = ANY(%(record_types)s)")
        params["record_types"] = record_types
    if gi_type:
        parts.append(
            f" AND LOWER({table_alias}.gi_type) = LOWER(%(gi_type)s)"
        )
        params["gi_type"] = gi_type
    if region:
        # Trigram OR ILIKE — same shape as name search but on the
        # geographical_boundary column (also trigram-indexed).
        parts.append(
            f" AND ("
            f"LOWER({table_alias}.geographical_boundary) LIKE LOWER(%(region_like)s)"
            f" OR similarity(LOWER({table_alias}.geographical_boundary), LOWER(%(region)s))"
            f"   > %(thresh)s)"
        )
        params["region"] = region
        params["region_like"] = f"%{region}%"
        params["thresh"] = TRIGRAM_THRESHOLD
    if date_from:
        parts.append(f" AND {table_alias}.bulletin_date >= %(date_from)s")
        params["date_from"] = date_from
    if date_to:
        parts.append(f" AND {table_alias}.bulletin_date <= %(date_to)s")
        params["date_to"] = date_to
    if application_no:
        parts.append(f" AND {table_alias}.application_no = %(application_no)s")
        params["application_no"] = application_no
    if registration_no is not None:
        parts.append(f" AND {table_alias}.registration_no = %(registration_no)s")
        params["registration_no"] = registration_no
    return "".join(parts), params


def _retrieve_text_candidates(
    cur, query: str, *, limit: int, **filters,
) -> List[CografiCandidate]:
    """Trigram + ILIKE retrieval over cografi_records.name."""
    if not query:
        return []
    filter_sql, filter_params = _filter_clauses(table_alias="r", **filters)
    sql = f"""
        SELECT r.id::text AS record_id,
               similarity(LOWER(COALESCE(r.name,'')), LOWER(%(q)s)) AS sim
        FROM cografi_records r
        WHERE r.name IS NOT NULL
          AND (
              LOWER(r.name) LIKE LOWER(%(qlike)s)
              OR similarity(LOWER(r.name), LOWER(%(q)s)) > %(thresh)s
          )
          {filter_sql}
        ORDER BY sim DESC
        LIMIT %(limit)s
    """
    params = {
        "q": query,
        "qlike": f"%{query}%",
        "thresh": TRIGRAM_THRESHOLD,
        "limit": limit,
        **filter_params,
    }
    cur.execute(sql, params)
    return [
        CografiCandidate(record_id=row[0], text_sim=float(row[1] or 0.0))
        for row in cur.fetchall()
    ]


def _retrieve_embedding_candidates(
    cur, vec_literal: str, *, limit: int, **filters,
) -> List[CografiCandidate]:
    """Cosine retrieval against cografi_records.text_embedding."""
    filter_sql, filter_params = _filter_clauses(table_alias="r", **filters)
    sql = f"""
        SELECT r.id::text AS record_id,
               1 - (r.text_embedding <=> %(vec)s::halfvec) AS sim
        FROM cografi_records r
        WHERE r.text_embedding IS NOT NULL
          {filter_sql}
        ORDER BY r.text_embedding <=> %(vec)s::halfvec
        LIMIT %(limit)s
    """
    params = {"vec": vec_literal, "limit": limit, **filter_params}
    cur.execute(sql, params)
    return [
        CografiCandidate(record_id=row[0], embedding_sim=float(row[1] or 0.0))
        for row in cur.fetchall()
    ]


def _retrieve_figure_candidates(
    cur, vec_literal: str, *, limit: int, **filters,
) -> List[CografiCandidate]:
    """Cosine retrieval against primary_figure_embedding (record-level mean)."""
    filter_sql, filter_params = _filter_clauses(table_alias="r", **filters)
    sql = f"""
        SELECT r.id::text AS record_id,
               1 - (r.primary_figure_embedding <=> %(vec)s::halfvec) AS sim
        FROM cografi_records r
        WHERE r.primary_figure_embedding IS NOT NULL
          {filter_sql}
        ORDER BY r.primary_figure_embedding <=> %(vec)s::halfvec
        LIMIT %(limit)s
    """
    params = {"vec": vec_literal, "limit": limit, **filter_params}
    cur.execute(sql, params)
    return [
        CografiCandidate(record_id=row[0], figure_sim=float(row[1] or 0.0))
        for row in cur.fetchall()
    ]


def _retrieve_filter_only_candidates(
    cur, *, limit: int, **filters,
) -> List[CografiCandidate]:
    """Filter-only browse: no query, just structured filters.

    Recency-ordered (newest bulletin first). Score stays 0 — the route
    layer surfaces the recency ordering as the implicit ranking.
    """
    filter_sql, filter_params = _filter_clauses(table_alias="r", **filters)
    sql = f"""
        SELECT r.id::text AS record_id
        FROM cografi_records r
        WHERE TRUE
          {filter_sql}
        ORDER BY r.bulletin_date DESC NULLS LAST, r.bulletin_no DESC
        LIMIT %(limit)s
    """
    params = {"limit": limit, **filter_params}
    cur.execute(sql, params)
    return [CografiCandidate(record_id=row[0]) for row in cur.fetchall()]


def _lookup_by_application_no(cur, application_no: str) -> List[CografiCandidate]:
    """Exact lookup on application_no.

    A given application can appear in multiple records (examined →
    art40 modified → ...). All matches return as perfect-score
    candidates ordered by bulletin_date DESC.
    """
    sql = """
        SELECT id::text FROM cografi_records
        WHERE application_no = %(an)s
        ORDER BY bulletin_date DESC NULLS LAST
        LIMIT 50
    """
    cur.execute(sql, {"an": application_no})
    return [
        CografiCandidate(record_id=row[0], text_sim=1.0, embedding_sim=1.0)
        for row in cur.fetchall()
    ]


def _lookup_by_registration_no(cur, registration_no: int) -> List[CografiCandidate]:
    """Exact lookup on registration_no.

    Matches the registration row plus any art42 records that reference
    this registration (via existing_registration_no).
    """
    sql = """
        SELECT id::text FROM cografi_records
        WHERE registration_no = %(rn)s
           OR existing_registration_no = %(rn)s
        ORDER BY bulletin_date DESC NULLS LAST
        LIMIT 50
    """
    cur.execute(sql, {"rn": registration_no})
    return [
        CografiCandidate(record_id=row[0], text_sim=1.0, embedding_sim=1.0)
        for row in cur.fetchall()
    ]


# ---------------------------------------------------------------------------
# Hydrate & result mapping
# ---------------------------------------------------------------------------


def cografi_image_url(image_path: Optional[str], bulletin_folder: Optional[str]) -> Optional[str]:
    """Build a frontend-resolvable URL for a cografi figure.

    ``cografi_figures.image_path`` is relative to the bulletin folder's
    ``figures/`` subdir (e.g. ``C2022_000469/1.jpeg``); the full path
    is ``bulletins/Cografi_Isaret_ve_Geleneksel_Urun_Adi/{bulletin_folder}/figures/{image_path}``.
    """
    if not image_path or not bulletin_folder:
        return None
    rel = image_path.lstrip("/")
    return f"/api/v1/cografi-image/{bulletin_folder}/{rel}"


HYDRATE_COLS = (
    "r.id::text AS record_id, r.registry_type, r.section_key::text, r.record_type::text, "
    "r.application_no, r.registration_no, r.existing_registration_no, "
    "r.application_date, r.registration_date, "
    "r.bulletin_no, r.bulletin_date, r.bulletin_folder, "
    "r.name, r.product_group, r.gi_type, r.geographical_boundary, "
    "r.usage_description, r.agent, "
    "(SELECT ch.name FROM cografi_holders ch "
    " WHERE ch.record_id = r.id AND ch.role = 'APPLICANT' "
    " ORDER BY ch.seq ASC LIMIT 1) AS applicant_name, "
    "(SELECT h.tpe_client_id FROM cografi_holders ch "
    " LEFT JOIN holders h ON h.id = ch.holder_id "
    " WHERE ch.record_id = r.id AND ch.role = 'APPLICANT' "
    " ORDER BY ch.seq ASC LIMIT 1) AS applicant_tpe_id, "
    "(SELECT ch.holder_id::text FROM cografi_holders ch "
    " WHERE ch.record_id = r.id AND ch.role = 'APPLICANT' "
    " ORDER BY ch.seq ASC LIMIT 1) AS applicant_internal_id, "
    "(SELECT ch.name FROM cografi_holders ch "
    " WHERE ch.record_id = r.id AND ch.role = 'REGISTRANT' "
    " ORDER BY ch.seq ASC LIMIT 1) AS registrant_name, "
    "(SELECT cf.image_path FROM cografi_figures cf "
    " WHERE cf.record_id = r.id "
    " ORDER BY cf.seq ASC LIMIT 1) AS first_image_path"
)


def _hydrate_records(cur, record_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    if not record_ids:
        return {}
    sql = f"""
        SELECT {HYDRATE_COLS}
        FROM cografi_records r
        WHERE r.id::text = ANY(%(ids)s)
    """
    cur.execute(sql, {"ids": list(record_ids)})
    cols = [desc[0] for desc in cur.description]
    out: Dict[str, Dict[str, Any]] = {}
    for row in cur.fetchall():
        record = dict(zip(cols, row))
        out[record["record_id"]] = record
    return out


def _isofmt(d: Any) -> Optional[str]:
    return d.isoformat() if d else None


def _result_row(
    record: Dict[str, Any], *, similarity: float, breakdown: Dict[str, float],
) -> Dict[str, Any]:
    image_url = cografi_image_url(
        record.get("first_image_path"), record.get("bulletin_folder"),
    )
    return {
        "id": record["record_id"],
        "registry_type": record.get("registry_type") or "cografi",
        "section_key": record.get("section_key"),
        "record_type": record.get("record_type"),
        "application_no": record.get("application_no"),
        "registration_no": record.get("registration_no"),
        "existing_registration_no": record.get("existing_registration_no"),
        "name": record.get("name"),
        "gi_type": record.get("gi_type"),
        "product_group": record.get("product_group"),
        "geographical_boundary": record.get("geographical_boundary"),
        "usage_description": record.get("usage_description"),
        "agent": record.get("agent"),
        "applicant_name": record.get("applicant_name") or record.get("registrant_name"),
        # Expose both the public TPE id (when available) and the
        # internal holders.id UUID so the result-card click-through
        # can match the design/patent fallback pattern: prefer the
        # TPE id, fall back to UUID.
        "applicant": {
            "name": record.get("applicant_name") or record.get("registrant_name"),
            "tpe_client_id": record.get("applicant_tpe_id"),
            "id": record.get("applicant_internal_id"),
        } if (record.get("applicant_name") or record.get("registrant_name")) else None,
        "bulletin_no": record.get("bulletin_no"),
        "bulletin_date": _isofmt(record.get("bulletin_date")),
        "application_date": _isofmt(record.get("application_date")),
        "registration_date": _isofmt(record.get("registration_date")),
        "image_url": image_url,
        "similarity": round(similarity * 100.0, 2),
        "similarity_breakdown": {
            k: round(float(v or 0.0), 4) for k, v in breakdown.items()
        },
    }


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def search_cografi(
    conn,
    *,
    query: Optional[str] = None,
    text_embedding: Optional[Sequence[float]] = None,
    figure_embedding: Optional[Sequence[float]] = None,
    section_keys: Optional[Sequence[str]] = None,
    record_types: Optional[Sequence[str]] = None,
    gi_type: Optional[str] = None,
    region: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    application_no: Optional[str] = None,
    registration_no: Optional[int] = None,
    include_admin: bool = False,
    limit: int = DEFAULT_LIMIT,
    public: bool = False,
) -> Dict[str, Any]:
    """Run a cografi search and return a serializable response dict.

    ``text_embedding`` (when provided) is a 1024-dim plain list in the
    same e5-large model space as ``cografi_records.text_embedding``.
    ``figure_embedding`` (when provided) is a 1024-dim DINOv2 ViT-L/14
    vector matching the corpus's ``primary_figure_embedding``. Public
    path is text-only — the route layer never passes embeddings on
    the public endpoint.
    """
    started = time.time()
    section_keys = normalize_section_keys(section_keys)
    record_types = normalize_record_types(record_types)
    limit = cap_limit(limit, public=public)

    has_query = bool(query and len(query.strip()) >= 2)
    has_embedding = bool(text_embedding) and not public
    has_image = bool(figure_embedding) and not public
    has_filters = bool(
        section_keys or record_types or gi_type or region
        or date_from or date_to or application_no or registration_no is not None
    )

    if not has_query and not has_embedding and not has_image and not has_filters:
        return {
            "results": [],
            "total": 0,
            "duration_ms": 0,
            "error": "cografi_search.empty_query",
        }

    filter_kwargs: Dict[str, Any] = {
        "section_keys": section_keys,
        "record_types": record_types,
        "gi_type": gi_type,
        "region": region,
        "date_from": date_from,
        "date_to": date_to,
        "application_no": application_no,
        "registration_no": registration_no,
        "include_admin": include_admin,
    }

    candidates: Dict[str, CografiCandidate] = {}

    def merge(c: CografiCandidate) -> None:
        existing = candidates.get(c.record_id)
        if existing is None:
            candidates[c.record_id] = c
            return
        existing.text_sim = max(existing.text_sim, c.text_sim)
        existing.embedding_sim = max(existing.embedding_sim, c.embedding_sim)
        existing.figure_sim = max(existing.figure_sim, c.figure_sim)

    with conn.cursor() as cur:
        # Exact-ID shortcut
        id_match = parse_id_query(query) if has_query else None
        if id_match and "application_no" in id_match:
            for c in _lookup_by_application_no(cur, id_match["application_no"]):
                merge(c)
        elif id_match and "registration_no" in id_match:
            for c in _lookup_by_registration_no(cur, id_match["registration_no"]):
                merge(c)

        if not id_match:
            if has_query:
                for c in _retrieve_text_candidates(
                    cur, query.strip(), limit=CANDIDATE_POOL, **filter_kwargs,
                ):
                    merge(c)
            if has_embedding:
                vec_lit = to_halfvec_literal(text_embedding)
                if vec_lit:
                    for c in _retrieve_embedding_candidates(
                        cur, vec_lit, limit=CANDIDATE_POOL, **filter_kwargs,
                    ):
                        merge(c)
            if has_image:
                fig_lit = to_halfvec_literal(figure_embedding)
                if fig_lit:
                    for c in _retrieve_figure_candidates(
                        cur, fig_lit, limit=CANDIDATE_POOL, **filter_kwargs,
                    ):
                        merge(c)
            if not has_query and not has_embedding and not has_image and has_filters:
                for c in _retrieve_filter_only_candidates(
                    cur, limit=CANDIDATE_POOL, **filter_kwargs,
                ):
                    merge(c)

        ranked = sorted(
            candidates.values(),
            key=lambda c: combine_scores(
                text=c.text_sim,
                embedding=c.embedding_sim,
                figure=c.figure_sim,
                has_image=has_image,
                has_text_query=has_query,
            ),
            reverse=True,
        )[:limit]

        hydrated = _hydrate_records(cur, [c.record_id for c in ranked])

    results: List[Dict[str, Any]] = []
    for c in ranked:
        record = hydrated.get(c.record_id)
        if not record:
            continue
        score = combine_scores(
            text=c.text_sim,
            embedding=c.embedding_sim,
            figure=c.figure_sim,
            has_image=has_image,
            has_text_query=has_query,
        )
        breakdown = {
            "text": c.text_sim,
            "embedding": c.embedding_sim,
            "figure": c.figure_sim,
        }
        results.append(_result_row(record, similarity=score, breakdown=breakdown))

    return {
        "results": results,
        "total": len(results),
        "duration_ms": int((time.time() - started) * 1000),
        "filters": {
            "section_keys": section_keys,
            "record_types": record_types,
            "gi_type": gi_type,
            "region": region,
            "date_from": date_from,
            "date_to": date_to,
            "application_no": application_no,
            "registration_no": registration_no,
            "has_query": has_query,
            "has_image": has_image,
            "id_lookup": bool(id_match),
            "include_admin": include_admin,
            "public": public,
        },
    }

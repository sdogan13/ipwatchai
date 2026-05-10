"""Patent / Faydalı Model search/retrieval service.

Sister to ``services/design_search_service.py`` (Tasarım) and
``services/search_service.py`` (Marka). Patent-tuned implementation:

  * Text-first hybrid: trigram on title + cosine on
    ``title_abstract_embedding``.
  * Figure (image) signal: cosine on ``patents.primary_figure_embedding``
    (mean-pooled DINOv2 vector built at ingest time). Coverage is
    partial — ~34% of the corpus has a figure embedding — so figure
    similarity is ranked alongside text, not as the sole signal.
  * Exact-ID shortcut: queries that look like a publication_no or
    application_no short-circuit to a direct row lookup.
  * Filters: IPC class array, holder name (trigram), date range
    (application_date), kind_code.
  * No phonetic / OCR / translation paths.

This module owns: candidate retrieval (SQL builder), score combination,
result mapping. Authentication, rate limiting, quota checks, and IPC
autocomplete live in the route layer (``app_patent_search_routes.py``).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


logger = logging.getLogger("turkpatent.patent_search")

# Score weights — picked per query mode so the dominant signal wins:
#   * Text only         (default)      : text 0.4, embedding 0.6
#   * Text + image      (hybrid)       : text 0.25, embedding 0.35, figure 0.40
#   * Image only        (visual lookup): figure 1.0
WEIGHTS_TEXT_ONLY = {"text": 0.4, "embedding": 0.6, "figure": 0.0}
WEIGHTS_HYBRID    = {"text": 0.25, "embedding": 0.35, "figure": 0.40}
WEIGHTS_IMAGE_ONLY = {"text": 0.0, "embedding": 0.0, "figure": 1.0}
# Backwards-compat alias for the existing tests on text-only mode.
WEIGHTS = WEIGHTS_TEXT_ONLY

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
PUBLIC_RESULT_CAP = 10
TRIGRAM_THRESHOLD = 0.2
CANDIDATE_POOL = 200

# Excluded record types from default results (UNKNOWN can't be classified
# reliably; LEGACY rows lack INID-coded fields).
EXCLUDED_RECORD_TYPES = ("UNKNOWN", "LEGACY")

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

# Application-no shape: "2017/15048" (4-digit year + slash/space + 4-6 digits).
# Publication-no shape: "TR 2017 15048 U3" or "2017/15048 U3" — the
# application-no plus an optional kind-code suffix.
_ID_RE = re.compile(
    r"""^
    \s*
    (?:TR\s+)?                          # optional country prefix
    (\d{4})[\s/]+(\d{4,6})              # year/serial
    (?:\s+([A-Z]\d?))?                  # optional kind code (B, A1, U3, T4...)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_id_query(query: str) -> Optional[Dict[str, str]]:
    """Detect a query that's actually an application_no / publication_no.

    Returns ``{"application_no": "2017/15048", "kind_code": "U3"}`` when
    matched (kind_code may be absent), else ``None``.
    """
    if not query:
        return None
    m = _ID_RE.match(query)
    if not m:
        return None
    year, serial, kind = m.group(1), m.group(2), m.group(3)
    out: Dict[str, str] = {"application_no": f"{year}/{serial}"}
    if kind:
        out["kind_code"] = kind.upper()
    return out


def to_halfvec_literal(values: Optional[Sequence[float]]) -> Optional[str]:
    """``[v1,v2,...]`` literal for casting to halfvec(N) in SQL."""
    if values is None:
        return None
    materialized = list(values)
    if not materialized:
        return None
    return "[" + ",".join(f"{float(v):.6f}" for v in materialized) + "]"


def combine_scores(
    *, text: float = 0.0, embedding: float = 0.0, figure: float = 0.0,
    has_image: bool = False, has_text_query: bool = True,
) -> float:
    """Combine per-signal similarities into an overall 0..1 score.

    Weights pick by query mode:
      - has_image=False               -> WEIGHTS_TEXT_ONLY (text + embedding)
      - has_image=True, has_text_query=True  -> WEIGHTS_HYBRID
      - has_image=True, has_text_query=False -> WEIGHTS_IMAGE_ONLY
    """
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


def normalize_ipc_filter(values: Optional[Sequence[str]]) -> Optional[List[str]]:
    """Normalize IPC class filter values. Accepts uppercase strings;
    deduplicates; trims whitespace. Returns ``None`` when nothing left."""
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


# ---------------------------------------------------------------------------
# Candidate retrieval
# ---------------------------------------------------------------------------

@dataclass
class PatentCandidate:
    patent_id: str
    text_sim: float = 0.0
    embedding_sim: float = 0.0
    figure_sim: float = 0.0


def _filter_clauses(
    *,
    ipc: Optional[List[str]],
    date_from: Optional[str],
    date_to: Optional[str],
    kind_code: Optional[str],
    table_alias: str = "p",
) -> tuple[str, Dict[str, Any]]:
    """Build filter SQL fragment + params dict shared across all retrieval queries."""
    parts: List[str] = []
    params: Dict[str, Any] = {}
    if ipc:
        parts.append(f" AND {table_alias}.ipc_classes && %(ipc)s::text[]")
        params["ipc"] = ipc
    if date_from:
        parts.append(f" AND {table_alias}.application_date >= %(date_from)s")
        params["date_from"] = date_from
    if date_to:
        parts.append(f" AND {table_alias}.application_date <= %(date_to)s")
        params["date_to"] = date_to
    if kind_code:
        parts.append(f" AND {table_alias}.kind_code = %(kind_code)s")
        params["kind_code"] = kind_code.upper()
    return "".join(parts), params


def _retrieve_text_candidates(
    cur,
    query: str,
    *,
    ipc: Optional[List[str]],
    date_from: Optional[str],
    date_to: Optional[str],
    kind_code: Optional[str],
    limit: int,
) -> List[PatentCandidate]:
    """Trigram + ILIKE retrieval over patents.title."""
    if not query:
        return []
    filter_sql, filter_params = _filter_clauses(
        ipc=ipc, date_from=date_from, date_to=date_to,
        kind_code=kind_code, table_alias="p",
    )
    sql = f"""
        SELECT p.id::text AS patent_id,
               similarity(LOWER(COALESCE(p.title,'')), LOWER(%(q)s)) AS sim
        FROM patents p
        WHERE p.record_type NOT IN %(excluded)s
          AND p.title IS NOT NULL
          AND (
              LOWER(p.title) LIKE LOWER(%(qlike)s)
              OR similarity(LOWER(p.title), LOWER(%(q)s)) > %(thresh)s
          )
          {filter_sql}
        ORDER BY sim DESC
        LIMIT %(limit)s
    """
    params = {
        "q": query, "qlike": f"%{query}%",
        "thresh": TRIGRAM_THRESHOLD,
        "excluded": EXCLUDED_RECORD_TYPES,
        "limit": limit,
        **filter_params,
    }
    cur.execute(sql, params)
    return [PatentCandidate(patent_id=row[0], text_sim=float(row[1] or 0.0))
            for row in cur.fetchall()]


def _retrieve_embedding_candidates(
    cur,
    vec_literal: str,
    *,
    ipc: Optional[List[str]],
    date_from: Optional[str],
    date_to: Optional[str],
    kind_code: Optional[str],
    limit: int,
) -> List[PatentCandidate]:
    """Cosine retrieval against title_abstract_embedding."""
    filter_sql, filter_params = _filter_clauses(
        ipc=ipc, date_from=date_from, date_to=date_to,
        kind_code=kind_code, table_alias="p",
    )
    sql = f"""
        SELECT p.id::text AS patent_id,
               1 - (p.title_abstract_embedding <=> %(vec)s::halfvec) AS sim
        FROM patents p
        WHERE p.record_type NOT IN %(excluded)s
          AND p.title_abstract_embedding IS NOT NULL
          {filter_sql}
        ORDER BY p.title_abstract_embedding <=> %(vec)s::halfvec
        LIMIT %(limit)s
    """
    params = {
        "vec": vec_literal,
        "excluded": EXCLUDED_RECORD_TYPES,
        "limit": limit,
        **filter_params,
    }
    cur.execute(sql, params)
    return [PatentCandidate(patent_id=row[0], embedding_sim=float(row[1] or 0.0))
            for row in cur.fetchall()]


def _retrieve_figure_candidates(
    cur,
    vec_literal: str,
    *,
    ipc: Optional[List[str]],
    date_from: Optional[str],
    date_to: Optional[str],
    kind_code: Optional[str],
    limit: int,
) -> List[PatentCandidate]:
    """Cosine retrieval against patents.primary_figure_embedding.

    Coverage caveat: only ~34% of the corpus has a figure embedding
    (Stage 6 ran on records that had at least one resolvable figure
    image). Patents without one are simply excluded from this signal.
    """
    filter_sql, filter_params = _filter_clauses(
        ipc=ipc, date_from=date_from, date_to=date_to,
        kind_code=kind_code, table_alias="p",
    )
    sql = f"""
        SELECT p.id::text AS patent_id,
               1 - (p.primary_figure_embedding <=> %(vec)s::halfvec) AS sim
        FROM patents p
        WHERE p.record_type NOT IN %(excluded)s
          AND p.primary_figure_embedding IS NOT NULL
          {filter_sql}
        ORDER BY p.primary_figure_embedding <=> %(vec)s::halfvec
        LIMIT %(limit)s
    """
    params = {
        "vec": vec_literal,
        "excluded": EXCLUDED_RECORD_TYPES,
        "limit": limit,
        **filter_params,
    }
    cur.execute(sql, params)
    return [PatentCandidate(patent_id=row[0], figure_sim=float(row[1] or 0.0))
            for row in cur.fetchall()]


def _retrieve_filter_only_candidates(
    cur,
    *,
    ipc: Optional[List[str]],
    date_from: Optional[str],
    date_to: Optional[str],
    kind_code: Optional[str],
    limit: int,
) -> List[PatentCandidate]:
    """Filter-only browse: no query, just structured filters.

    Returns rows matching the filters ordered by application_date DESC
    (newest first). Score is left at 0 since there's no relevance signal —
    the route layer surfaces the recency ordering as the implicit ranking.
    """
    filter_sql, filter_params = _filter_clauses(
        ipc=ipc, date_from=date_from, date_to=date_to,
        kind_code=kind_code, table_alias="p",
    )
    sql = f"""
        SELECT p.id::text AS patent_id
        FROM patents p
        WHERE p.record_type NOT IN %(excluded)s
          {filter_sql}
        ORDER BY p.application_date DESC NULLS LAST
        LIMIT %(limit)s
    """
    params = {"excluded": EXCLUDED_RECORD_TYPES, "limit": limit, **filter_params}
    cur.execute(sql, params)
    return [PatentCandidate(patent_id=row[0]) for row in cur.fetchall()]


def _retrieve_holder_filtered_ids(
    cur, holder_query: str,
) -> Optional[List[str]]:
    """Resolve holder-name filter to a set of patent_ids.

    Trigram-search ``patent_holders.name`` and return all patent_ids whose
    holder names match. Returns ``None`` when no holder filter or no matches
    (caller should treat None vs []  carefully — None means "no filter",
    [] means "filter active but matched nothing").
    """
    if not holder_query or len(holder_query.strip()) < 2:
        return None
    sql = """
        SELECT DISTINCT ph.patent_id::text
        FROM patent_holders ph
        WHERE LOWER(ph.name) LIKE LOWER(%(qlike)s)
           OR similarity(LOWER(ph.name), LOWER(%(q)s)) > %(thresh)s
        LIMIT 5000
    """
    cur.execute(sql, {
        "q": holder_query, "qlike": f"%{holder_query}%",
        "thresh": TRIGRAM_THRESHOLD,
    })
    return [row[0] for row in cur.fetchall()]


def _lookup_by_id(
    cur, *, application_no: str, kind_code: Optional[str] = None,
) -> List[PatentCandidate]:
    """Exact lookup on application_no (+ optional kind_code).

    Returns a perfect-score candidate per matching row. Same application
    can have multiple publications (B grant + A1 publication) so up to
    a handful of rows can come back.
    """
    if kind_code:
        sql = """
            SELECT id::text FROM patents
            WHERE application_no = %(an)s AND kind_code = %(kc)s
            LIMIT 20
        """
        cur.execute(sql, {"an": application_no, "kc": kind_code})
    else:
        sql = """
            SELECT id::text FROM patents
            WHERE application_no = %(an)s
            ORDER BY publication_date DESC NULLS LAST
            LIMIT 20
        """
        cur.execute(sql, {"an": application_no})
    return [PatentCandidate(patent_id=row[0], text_sim=1.0, embedding_sim=1.0)
            for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Hydrate & rank
# ---------------------------------------------------------------------------

HYDRATE_COLS = (
    "p.id::text AS patent_id, p.registry_type, p.application_no, p.publication_no, "
    "p.kind_code, p.record_type, "
    "p.application_date, p.publication_date, p.grant_date, "
    "p.bulletin_no, p.bulletin_date, "
    "p.title, p.abstract, p.ipc_classes, p.patent_type, "
    "(SELECT ph.name FROM patent_holders ph "
    " WHERE ph.patent_id = p.id ORDER BY ph.seq ASC LIMIT 1) AS first_holder_name, "
    "(SELECT ph.country FROM patent_holders ph "
    " WHERE ph.patent_id = p.id ORDER BY ph.seq ASC LIMIT 1) AS first_holder_country, "
    "(SELECT h.tpe_client_id FROM patent_holders ph "
    " LEFT JOIN holders h ON h.id = ph.holder_id "
    " WHERE ph.patent_id = p.id ORDER BY ph.seq ASC LIMIT 1) AS first_holder_tpe_id, "
    "(SELECT ARRAY_AGG(pi.name ORDER BY pi.seq) FROM patent_inventors pi "
    " WHERE pi.patent_id = p.id) AS inventors, "
    "(SELECT pa.name FROM patent_attorneys pa "
    " WHERE pa.patent_id = p.id ORDER BY pa.seq ASC LIMIT 1) AS first_attorney_name, "
    "(SELECT pa.firm FROM patent_attorneys pa "
    " WHERE pa.patent_id = p.id ORDER BY pa.seq ASC LIMIT 1) AS first_attorney_firm"
)


def _hydrate_patents(cur, patent_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Look up full row data for a candidate set. Returns ``{patent_id: row_dict}``."""
    if not patent_ids:
        return {}
    sql = f"""
        SELECT {HYDRATE_COLS}
        FROM patents p
        WHERE p.id::text = ANY(%(ids)s)
    """
    cur.execute(sql, {"ids": list(patent_ids)})
    cols = [desc[0] for desc in cur.description]
    out: Dict[str, Dict[str, Any]] = {}
    for row in cur.fetchall():
        record = dict(zip(cols, row))
        out[record["patent_id"]] = record
    return out


def _isofmt(d: Any) -> Optional[str]:
    return d.isoformat() if d else None


def _result_row(
    record: Dict[str, Any], *, similarity: float, breakdown: Dict[str, float],
) -> Dict[str, Any]:
    holder = None
    if record.get("first_holder_name"):
        holder = {
            "name": record["first_holder_name"],
            "country": record.get("first_holder_country"),
            "tpe_client_id": record.get("first_holder_tpe_id"),
        }
    attorney = None
    if record.get("first_attorney_name") or record.get("first_attorney_firm"):
        attorney = {
            "name": record.get("first_attorney_name"),
            "firm": record.get("first_attorney_firm"),
        }
    record_type = record.get("record_type")
    if hasattr(record_type, "value"):  # enum
        record_type = record_type.value
    return {
        "id": record["patent_id"],
        "registry_type": record.get("registry_type") or "patent",
        "application_no": record.get("application_no"),
        "publication_no": record.get("publication_no"),
        "kind_code": record.get("kind_code"),
        "record_type": record_type,
        "patent_type": record.get("patent_type"),
        "title": record.get("title"),
        "abstract": record.get("abstract"),
        "ipc_classes": list(record.get("ipc_classes") or []),
        "bulletin_no": record.get("bulletin_no"),
        "bulletin_date": _isofmt(record.get("bulletin_date")),
        "application_date": _isofmt(record.get("application_date")),
        "publication_date": _isofmt(record.get("publication_date")),
        "grant_date": _isofmt(record.get("grant_date")),
        "holder": holder,
        "inventors": list(record.get("inventors") or []),
        "attorney": attorney,
        "similarity": round(similarity * 100.0, 2),
        "similarity_breakdown": {k: round(float(v or 0.0), 4) for k, v in breakdown.items()},
    }


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def search_patents(
    conn,
    *,
    query: Optional[str] = None,
    text_embedding: Optional[Sequence[float]] = None,
    figure_embedding: Optional[Sequence[float]] = None,
    ipc_classes: Optional[Sequence[str]] = None,
    holder: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    kind_code: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    public: bool = False,
) -> Dict[str, Any]:
    """Run a patent search and return a serializable response dict.

    ``text_embedding`` (when provided) is a 1024-dim plain list in the
    same e5-large model space as ``patents.title_abstract_embedding``.
    ``figure_embedding`` (when provided) is a 1024-dim plain list in
    the same DINOv2 ViT-L/14 space as ``patents.primary_figure_embedding``.
    The route layer computes both at request time via the embedders
    Stage 6 used.
    """
    started = time.time()
    ipc = normalize_ipc_filter(ipc_classes)
    limit = cap_limit(limit, public=public)
    has_query = bool(query and len(query.strip()) >= 2)
    has_embedding = bool(text_embedding) and not public  # public path is text-only
    has_image = bool(figure_embedding) and not public    # public path is text-only too
    has_filters = bool(ipc or holder or date_from or date_to or kind_code)
    if not has_query and not has_embedding and not has_image and not has_filters:
        return {"results": [], "total": 0, "duration_ms": 0,
                "error": "patent_search.empty_query"}

    candidates: Dict[str, PatentCandidate] = {}

    def merge(c: PatentCandidate) -> None:
        existing = candidates.get(c.patent_id)
        if existing is None:
            candidates[c.patent_id] = c
            return
        existing.text_sim = max(existing.text_sim, c.text_sim)
        existing.embedding_sim = max(existing.embedding_sim, c.embedding_sim)
        existing.figure_sim = max(existing.figure_sim, c.figure_sim)

    with conn.cursor() as cur:
        # 1. Exact-ID shortcut: if the query parses as an application_no,
        #    return only those rows. Skip semantic search entirely.
        id_match = parse_id_query(query) if has_query else None
        if id_match:
            for c in _lookup_by_id(cur, **id_match):
                merge(c)

        # 2. Holder filter resolves to a set of allowed patent_ids
        #    (applied as post-filter on candidate set; we don't push it
        #    into every retrieval SQL because the join blows up the plan).
        holder_allowed: Optional[set] = None
        if holder and len(holder.strip()) >= 2:
            ids = _retrieve_holder_filtered_ids(cur, holder.strip())
            holder_allowed = set(ids or [])
            if not holder_allowed:
                # Filter active but matches nothing → empty result fast-path
                return {
                    "results": [], "total": 0,
                    "duration_ms": int((time.time() - started) * 1000),
                    "filters": {
                        "ipc_classes": ipc, "holder": holder,
                        "date_from": date_from, "date_to": date_to,
                        "kind_code": kind_code,
                        "has_query": has_query, "public": public,
                    },
                }

        # 3. Regular text + embedding retrieval (skipped when ID shortcut hit).
        #    Filter-only mode (no query, only filters) takes a separate path
        #    that browses recent rows matching the filter set.
        if not id_match:
            if has_query:
                for c in _retrieve_text_candidates(
                    cur, query.strip(),
                    ipc=ipc, date_from=date_from, date_to=date_to,
                    kind_code=kind_code, limit=CANDIDATE_POOL,
                ):
                    merge(c)
            if has_embedding:
                vec_lit = to_halfvec_literal(text_embedding)
                if vec_lit:
                    for c in _retrieve_embedding_candidates(
                        cur, vec_lit,
                        ipc=ipc, date_from=date_from, date_to=date_to,
                        kind_code=kind_code, limit=CANDIDATE_POOL,
                    ):
                        merge(c)
            if has_image:
                fig_lit = to_halfvec_literal(figure_embedding)
                if fig_lit:
                    for c in _retrieve_figure_candidates(
                        cur, fig_lit,
                        ipc=ipc, date_from=date_from, date_to=date_to,
                        kind_code=kind_code, limit=CANDIDATE_POOL,
                    ):
                        merge(c)
            if not has_query and not has_embedding and not has_image and has_filters:
                for c in _retrieve_filter_only_candidates(
                    cur,
                    ipc=ipc, date_from=date_from, date_to=date_to,
                    kind_code=kind_code, limit=CANDIDATE_POOL,
                ):
                    merge(c)

        # 4. Apply holder filter post-retrieval
        if holder_allowed is not None:
            candidates = {pid: c for pid, c in candidates.items() if pid in holder_allowed}

        # 5. Score + rank
        ranked = sorted(
            candidates.values(),
            key=lambda c: combine_scores(
                text=c.text_sim, embedding=c.embedding_sim, figure=c.figure_sim,
                has_image=has_image, has_text_query=has_query,
            ),
            reverse=True,
        )[:limit]

        hydrated = _hydrate_patents(cur, [c.patent_id for c in ranked])

    results: List[Dict[str, Any]] = []
    for c in ranked:
        record = hydrated.get(c.patent_id)
        if not record:
            continue
        score = combine_scores(
            text=c.text_sim, embedding=c.embedding_sim, figure=c.figure_sim,
            has_image=has_image, has_text_query=has_query,
        )
        breakdown = {
            "text": c.text_sim, "embedding": c.embedding_sim, "figure": c.figure_sim,
        }
        results.append(_result_row(record, similarity=score, breakdown=breakdown))

    return {
        "results": results,
        "total": len(results),
        "duration_ms": int((time.time() - started) * 1000),
        "filters": {
            "ipc_classes": ipc,
            "holder": holder,
            "date_from": date_from,
            "date_to": date_to,
            "kind_code": kind_code,
            "has_query": has_query,
            "has_image": has_image,
            "id_lookup": bool(id_match),
            "public": public,
        },
    }

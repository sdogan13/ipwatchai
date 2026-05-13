"""Coğrafi İşaret watchlist scanner.

Sister to ``services/patent_scanner_service.py``. Four scan modes, one
per watch_type:

  * ``holder``    — find every active cografi record whose
                    cografi_holders row matches the watched holder
                    (by holder_id then tpe_client_id then name trigram).
                    Stores ``overall_similarity_score`` of 1.0 and
                    ``match_type='holder'``.
  * ``reference`` — cosine similarity against
                    cografi_watchlist_mt.reference_embedding over
                    cografi_records.text_embedding. When the watchlist
                    also stores a reference_query, the text trigram
                    score is combined hybrid-style.
  * ``region``    — trigram match against
                    cografi_records.geographical_boundary using the
                    watch's region_query and/or region_terms[]. NEW
                    for cografi (no equivalent in patent watchlist).
  * ``lifecycle`` — match new records whose registration_no,
                    existing_registration_no, or
                    correction_referenced_record_id targets the
                    watched lifecycle_registration_no. Captures
                    art42 change requests / finalized changes /
                    corrections. NEW for cografi.

Conflict storage floor: 0.50 (matches patent + design). Per-item cap:
10 alerts per scan run.

This module owns: matching SQL, scoring, severity bucketing, alert
upsert. Authentication, route limiting, and watchlist hydration live
elsewhere.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID


logger = logging.getLogger("turkpatent.cografi_scanner")


# Bucket thresholds (overall_similarity_score → severity).
SEVERITY_THRESHOLDS = (
    (0.85, "critical"),
    (0.70, "high"),
    (0.55, "medium"),
    (0.0,  "low"),
)
CONFLICT_FLOOR = 0.50      # alerts below this aren't stored
ALERTS_PER_SCAN_CAP = 10   # per watchlist item, per scan run

WEIGHTS_REF_HYBRID = {"text": 0.4, "embedding": 0.6}

TRIGRAM_THRESHOLD = 0.2
DEFAULT_EXCLUDED_SECTIONS = ("corrections", "gazette_only_announcements")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def severity_for(score: float) -> str:
    """Bucket an overall 0..1 score into low/medium/high/critical."""
    s = max(0.0, min(1.0, float(score or 0.0)))
    for thresh, name in SEVERITY_THRESHOLDS:
        if s >= thresh:
            return name
    return "low"


@dataclass
class ScanMatch:
    record_id: str
    overall_score: float
    text_sim: float = 0.0
    embedding_sim: float = 0.0
    region_sim: float = 0.0
    match_type: str = "reference_embedding"


# ---------------------------------------------------------------------------
# Filter SQL builder (shared across all four scan modes)
# ---------------------------------------------------------------------------

def _common_filter_clauses(
    *,
    section_keys: Optional[Sequence[str]],
    record_types: Optional[Sequence[str]],
    gi_type: Optional[str],
    customer_application_no: Optional[str],
    customer_registration_no: Optional[int],
    candidate_record_ids: Optional[Sequence[str]] = None,
    table_alias: str = "r",
    include_admin_sections: bool = False,
) -> tuple[str, Dict[str, Any]]:
    """Filter SQL fragment + params dict shared by every scan query.

    Excludes administrative section_keys (corrections,
    gazette_only_announcements) by default — those are bookkeeping
    rows that aren't relevant to most watches. ``include_admin_sections``
    is set to True for lifecycle scans (they explicitly need to find
    correction records that target a watched registration).
    """
    parts: List[str] = []
    params: Dict[str, Any] = {}
    if section_keys:
        parts.append(f" AND {table_alias}.section_key::text = ANY(%(_sec)s::text[])")
        params["_sec"] = list(section_keys)
    elif not include_admin_sections:
        parts.append(f" AND {table_alias}.section_key::text NOT IN %(_excluded)s")
        params["_excluded"] = DEFAULT_EXCLUDED_SECTIONS
    if record_types:
        parts.append(f" AND {table_alias}.record_type::text = ANY(%(_rt)s::text[])")
        params["_rt"] = list(record_types)
    if gi_type:
        parts.append(f" AND LOWER({table_alias}.gi_type) = LOWER(%(_gi)s)")
        params["_gi"] = gi_type
    if customer_application_no:
        parts.append(f" AND ({table_alias}.application_no IS DISTINCT FROM %(_self_app)s)")
        params["_self_app"] = customer_application_no
    if customer_registration_no is not None:
        parts.append(f" AND ({table_alias}.registration_no IS DISTINCT FROM %(_self_reg)s)")
        params["_self_reg"] = int(customer_registration_no)
    if candidate_record_ids:
        parts.append(f" AND {table_alias}.id::text = ANY(%(_candidates)s::text[])")
        params["_candidates"] = list(candidate_record_ids)
    return "".join(parts), params


# ---------------------------------------------------------------------------
# Holder scan
# ---------------------------------------------------------------------------

def _scan_holder(cur, item: Dict[str, Any], *, candidate_record_ids=None) -> List[ScanMatch]:
    """Find cografi records whose cografi_holders row matches the watched holder."""
    holder_id = item.get("holder_id")
    holder_tpe = item.get("holder_tpe_client_id")
    holder_name = (item.get("holder_name") or "").strip()
    if not (holder_id or holder_tpe or holder_name):
        return []

    filter_sql, filter_params = _common_filter_clauses(
        section_keys=item.get("section_keys"),
        record_types=item.get("record_types"),
        gi_type=item.get("gi_type"),
        customer_application_no=item.get("customer_application_no"),
        customer_registration_no=item.get("customer_registration_no"),
        candidate_record_ids=candidate_record_ids,
        table_alias="r",
    )

    # Match on any of: holder.id (FK) | holder.tpe_client_id | name trigram.
    # The OR set is broad on purpose — partial-data watches still match.
    holder_match_parts: List[str] = []
    holder_params: Dict[str, Any] = {}
    if holder_id:
        holder_match_parts.append("ch.holder_id = %(_hid)s")
        holder_params["_hid"] = str(holder_id)
    if holder_tpe:
        holder_match_parts.append(
            "(SELECT h.tpe_client_id FROM holders h WHERE h.id = ch.holder_id) = %(_tpe)s"
        )
        holder_params["_tpe"] = holder_tpe
    if holder_name:
        holder_match_parts.append(
            "(LOWER(ch.name) LIKE LOWER(%(_hname_like)s) OR "
            " similarity(LOWER(ch.name), LOWER(%(_hname)s)) > %(_thresh)s)"
        )
        holder_params["_hname"] = holder_name
        holder_params["_hname_like"] = f"%{holder_name}%"
        holder_params["_thresh"] = TRIGRAM_THRESHOLD
    holder_match = " OR ".join(holder_match_parts)

    sql = f"""
        SELECT DISTINCT r.id::text AS record_id
        FROM cografi_records r
        JOIN cografi_holders ch ON ch.record_id = r.id
        WHERE ({holder_match})
          {filter_sql}
        LIMIT 500
    """
    params = {**holder_params, **filter_params}
    cur.execute(sql, params)
    return [
        ScanMatch(record_id=row["record_id"], overall_score=1.0, match_type="holder")
        for row in cur.fetchall()
    ]


# ---------------------------------------------------------------------------
# Reference scan
# ---------------------------------------------------------------------------

def _scan_reference(cur, item: Dict[str, Any], *, candidate_record_ids=None) -> List[ScanMatch]:
    """Cosine vs reference_embedding (+ optional text-trigram hybrid)."""
    ref_emb_text = item.get("reference_embedding_text")
    ref_query = (item.get("reference_query") or "").strip() or None
    has_emb = bool(ref_emb_text and ref_emb_text != "[]")
    has_query = bool(ref_query)
    if not (has_emb or has_query):
        return []

    matches: Dict[str, ScanMatch] = {}
    filter_sql, filter_params = _common_filter_clauses(
        section_keys=item.get("section_keys"),
        record_types=item.get("record_types"),
        gi_type=item.get("gi_type"),
        customer_application_no=item.get("customer_application_no"),
        customer_registration_no=item.get("customer_registration_no"),
        candidate_record_ids=candidate_record_ids,
        table_alias="r",
    )

    if has_emb:
        cur.execute(
            f"""
            SELECT r.id::text AS record_id,
                   1 - (r.text_embedding <=> %(vec)s::halfvec) AS sim
            FROM cografi_records r
            WHERE r.text_embedding IS NOT NULL
              {filter_sql}
            ORDER BY r.text_embedding <=> %(vec)s::halfvec
            LIMIT 200
            """,
            {"vec": ref_emb_text, **filter_params},
        )
        for row in cur.fetchall():
            rid = row["record_id"]
            sim = float(row["sim"] or 0.0)
            matches[rid] = ScanMatch(
                record_id=rid,
                overall_score=sim,
                embedding_sim=sim,
                match_type="reference_embedding",
            )

    if has_query:
        cur.execute(
            f"""
            SELECT r.id::text AS record_id,
                   similarity(LOWER(COALESCE(r.name, '')), LOWER(%(q)s)) AS sim
            FROM cografi_records r
            WHERE r.name IS NOT NULL
              AND (LOWER(r.name) LIKE LOWER(%(qlike)s)
                   OR similarity(LOWER(r.name), LOWER(%(q)s)) > %(thresh)s)
              {filter_sql}
            ORDER BY sim DESC
            LIMIT 200
            """,
            {
                "q": ref_query, "qlike": f"%{ref_query}%",
                "thresh": TRIGRAM_THRESHOLD, **filter_params,
            },
        )
        for row in cur.fetchall():
            rid = row["record_id"]
            text_sim = float(row["sim"] or 0.0)
            existing = matches.get(rid)
            if existing is None:
                matches[rid] = ScanMatch(
                    record_id=rid,
                    overall_score=text_sim,
                    text_sim=text_sim,
                    match_type="reference_text",
                )
            else:
                existing.text_sim = max(existing.text_sim, text_sim)
                existing.match_type = "reference_hybrid"

    # Hybrid scoring when both signals present on the same record.
    for m in matches.values():
        if m.match_type == "reference_hybrid":
            m.overall_score = (
                WEIGHTS_REF_HYBRID["text"] * m.text_sim
                + WEIGHTS_REF_HYBRID["embedding"] * m.embedding_sim
            )
    return list(matches.values())


# ---------------------------------------------------------------------------
# Region scan (NEW for cografi)
# ---------------------------------------------------------------------------

def _scan_region(cur, item: Dict[str, Any], *, candidate_record_ids=None) -> List[ScanMatch]:
    """Find records whose geographical_boundary matches the watched region.

    Combines the optional ``region_query`` (single trigram-match string)
    and optional ``region_terms[]`` (any-of, case-insensitive). Records
    matching by either signal pass; similarity score uses the trigram
    function so ranking is comparable to the other modes.
    """
    region_query = (item.get("region_query") or "").strip() or None
    region_terms = [t for t in (item.get("region_terms") or []) if t]
    if not (region_query or region_terms):
        return []

    filter_sql, filter_params = _common_filter_clauses(
        section_keys=item.get("section_keys"),
        record_types=item.get("record_types"),
        gi_type=item.get("gi_type"),
        customer_application_no=item.get("customer_application_no"),
        customer_registration_no=item.get("customer_registration_no"),
        candidate_record_ids=candidate_record_ids,
        table_alias="r",
    )

    region_match_parts: List[str] = []
    region_params: Dict[str, Any] = {}
    if region_query:
        region_match_parts.append(
            "(LOWER(r.geographical_boundary) LIKE LOWER(%(_rqlike)s)"
            " OR similarity(LOWER(r.geographical_boundary), LOWER(%(_rq)s)) > %(_thresh)s)"
        )
        region_params["_rq"] = region_query
        region_params["_rqlike"] = f"%{region_query}%"
        region_params["_thresh"] = TRIGRAM_THRESHOLD
    if region_terms:
        # Any-of-terms: substring match on each term, OR'd together.
        # Generated as %(_rt0)s, %(_rt1)s, ...
        any_parts = []
        for i, term in enumerate(region_terms):
            key = f"_rt{i}"
            any_parts.append(f"LOWER(r.geographical_boundary) LIKE LOWER(%({key})s)")
            region_params[key] = f"%{term}%"
        region_match_parts.append("(" + " OR ".join(any_parts) + ")")

    region_match = " OR ".join(region_match_parts)
    score_expr = (
        f"similarity(LOWER(COALESCE(r.geographical_boundary, '')), LOWER(%(_rq)s))"
        if region_query else "1.0"
    )
    sql = f"""
        SELECT r.id::text AS record_id, {score_expr} AS sim
        FROM cografi_records r
        WHERE r.geographical_boundary IS NOT NULL
          AND ({region_match})
          {filter_sql}
        ORDER BY sim DESC
        LIMIT 200
    """
    cur.execute(sql, {**region_params, **filter_params})
    return [
        ScanMatch(
            record_id=row["record_id"],
            overall_score=float(row["sim"] or 0.0) if region_query else 1.0,
            region_sim=float(row["sim"] or 0.0) if region_query else 1.0,
            match_type="region",
        )
        for row in cur.fetchall()
    ]


# ---------------------------------------------------------------------------
# Lifecycle scan (NEW for cografi)
# ---------------------------------------------------------------------------

def _scan_lifecycle(cur, item: Dict[str, Any], *, candidate_record_ids=None) -> List[ScanMatch]:
    """Find records that target the watched registration_no.

    Three matching channels per the schema:
      * ``existing_registration_no`` — art42 change requests + art42
        finalized records reference the existing registration via this
        column (mapped to match_type='lifecycle_change_request' or
        'lifecycle_finalized' based on section_key).
      * ``correction_referenced_record_id`` — corrections records
        reference the original record by id; we additionally match
        when the watched reg_no string appears verbatim in this column
        (mapped to match_type='lifecycle_correction').
      * ``registration_no`` — the registered row itself. Useful when
        the watcher wants to also see the original registration land.
    """
    reg_no = item.get("lifecycle_registration_no")
    if reg_no is None:
        return []
    try:
        reg_no = int(reg_no)
    except (TypeError, ValueError):
        return []

    # Lifecycle scans intentionally include admin sections (corrections).
    filter_sql, filter_params = _common_filter_clauses(
        section_keys=item.get("section_keys"),
        record_types=item.get("record_types"),
        gi_type=item.get("gi_type"),
        customer_application_no=item.get("customer_application_no"),
        customer_registration_no=item.get("customer_registration_no"),
        candidate_record_ids=candidate_record_ids,
        table_alias="r",
        include_admin_sections=True,
    )

    sql = f"""
        SELECT r.id::text AS record_id, r.section_key::text AS section_key
        FROM cografi_records r
        WHERE (
              r.existing_registration_no = %(_reg)s
           OR r.registration_no = %(_reg)s
           OR r.correction_referenced_record_id = %(_reg_text)s
        )
          {filter_sql}
        LIMIT 200
    """
    cur.execute(sql, {"_reg": reg_no, "_reg_text": str(reg_no), **filter_params})

    out: List[ScanMatch] = []
    for row in cur.fetchall():
        rec_id, section_key = row["record_id"], row["section_key"]
        if section_key == "article_42_change_requests":
            mt = "lifecycle_change_request"
        elif section_key == "article_42_finalized":
            mt = "lifecycle_finalized"
        elif section_key == "corrections":
            mt = "lifecycle_correction"
        else:
            mt = "lifecycle_change_request"  # registered originals + edge cases
        out.append(ScanMatch(record_id=rec_id, overall_score=1.0, match_type=mt))
    return out


# ---------------------------------------------------------------------------
# Top-level scan dispatcher
# ---------------------------------------------------------------------------

def scan_watchlist_item(
    db, item: Dict[str, Any], *, candidate_record_ids=None,
) -> List[ScanMatch]:
    """Run the appropriate scan for a single watchlist item."""
    cur = db.cursor()
    watch_type = (item.get("watch_type") or "").strip().lower()
    if watch_type == "holder":
        return _scan_holder(cur, item, candidate_record_ids=candidate_record_ids)
    if watch_type == "reference":
        return _scan_reference(cur, item, candidate_record_ids=candidate_record_ids)
    if watch_type == "region":
        return _scan_region(cur, item, candidate_record_ids=candidate_record_ids)
    if watch_type == "lifecycle":
        return _scan_lifecycle(cur, item, candidate_record_ids=candidate_record_ids)
    logger.warning("scan_watchlist_item: unknown watch_type %r for item %s",
                   watch_type, item.get("id"))
    return []


# ---------------------------------------------------------------------------
# Alert hydrate + upsert
# ---------------------------------------------------------------------------

ALERT_INSERT_SQL = """
    INSERT INTO cografi_alerts_mt (
        watchlist_item_id, user_id, organization_id,
        conflicting_record_id, conflicting_section_key, conflicting_record_type,
        conflicting_application_no, conflicting_registration_no,
        conflicting_existing_registration_no, conflicting_name,
        conflicting_gi_type, conflicting_geographical_boundary,
        conflicting_bulletin_no, conflicting_bulletin_date,
        conflicting_application_date, conflicting_registration_date,
        match_type, overall_similarity_score,
        text_similarity_score, embedding_similarity_score, region_similarity_score,
        score_details, overlapping_section_keys, severity
    )
    VALUES (
        %(watchlist_item_id)s, %(user_id)s, %(organization_id)s,
        %(conflicting_record_id)s, %(conflicting_section_key)s, %(conflicting_record_type)s,
        %(conflicting_application_no)s, %(conflicting_registration_no)s,
        %(conflicting_existing_registration_no)s, %(conflicting_name)s,
        %(conflicting_gi_type)s, %(conflicting_geographical_boundary)s,
        %(conflicting_bulletin_no)s, %(conflicting_bulletin_date)s,
        %(conflicting_application_date)s, %(conflicting_registration_date)s,
        %(match_type)s, %(overall_similarity_score)s,
        %(text_similarity_score)s, %(embedding_similarity_score)s, %(region_similarity_score)s,
        %(score_details)s::jsonb, %(overlapping_section_keys)s, %(severity)s
    )
    ON CONFLICT (watchlist_item_id, conflicting_record_id)
    WHERE conflicting_record_id IS NOT NULL
    DO NOTHING
    RETURNING id
"""


def _hydrate_for_alert(cur, record_id: str) -> Optional[Dict[str, Any]]:
    cur.execute(
        """
        SELECT r.section_key::text, r.record_type::text,
               r.application_no, r.registration_no, r.existing_registration_no,
               r.name, r.gi_type, r.geographical_boundary,
               r.bulletin_no, r.bulletin_date,
               r.application_date, r.registration_date
        FROM cografi_records r
        WHERE r.id = %s
        """,
        (record_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _overlapping_section_keys(
    item_keys: Optional[Sequence[str]], conflict_key: Optional[str],
) -> List[str]:
    if not conflict_key or not item_keys:
        return []
    keys = {k.lower().strip() for k in item_keys if k}
    return [conflict_key] if conflict_key in keys else []


def store_alerts_for_item(
    db, item: Dict[str, Any], matches: Sequence[ScanMatch],
) -> int:
    """Filter matches, dedup against existing alerts, insert new rows.

    Returns the number of NEW alerts written. Holder, region, and
    lifecycle matches always score 1.0 and are never filtered by the
    threshold (they're binary match types). Reference matches respect
    the per-watchlist similarity_threshold.
    """
    if not matches:
        return 0

    cur = db.cursor()
    threshold = max(CONFLICT_FLOOR, float(item.get("similarity_threshold") or 0.0))
    above = [
        m for m in matches
        if m.match_type in ("holder", "region",
                            "lifecycle_change_request",
                            "lifecycle_finalized",
                            "lifecycle_correction")
        or m.overall_score >= threshold
    ]
    above.sort(key=lambda m: m.overall_score, reverse=True)
    above = above[:ALERTS_PER_SCAN_CAP]
    if not above:
        return 0

    inserted = 0
    item_section_keys = item.get("section_keys") or []

    for m in above:
        hyd = _hydrate_for_alert(cur, m.record_id)
        if not hyd:
            continue
        score_details = {
            "text_sim": round(float(m.text_sim or 0.0), 4),
            "embedding_sim": round(float(m.embedding_sim or 0.0), 4),
            "region_sim": round(float(m.region_sim or 0.0), 4),
            "overall": round(float(m.overall_score or 0.0), 4),
        }
        params = {
            "watchlist_item_id": str(item["id"]),
            "user_id": str(item["user_id"]) if item.get("user_id") else None,
            "organization_id": str(item["organization_id"]),
            "conflicting_record_id": m.record_id,
            "conflicting_section_key": hyd.get("section_key"),
            "conflicting_record_type": hyd.get("record_type"),
            "conflicting_application_no": hyd.get("application_no"),
            "conflicting_registration_no": hyd.get("registration_no"),
            "conflicting_existing_registration_no": hyd.get("existing_registration_no"),
            "conflicting_name": hyd.get("name"),
            "conflicting_gi_type": hyd.get("gi_type"),
            "conflicting_geographical_boundary": hyd.get("geographical_boundary"),
            "conflicting_bulletin_no": hyd.get("bulletin_no"),
            "conflicting_bulletin_date": hyd.get("bulletin_date"),
            "conflicting_application_date": hyd.get("application_date"),
            "conflicting_registration_date": hyd.get("registration_date"),
            "match_type": m.match_type,
            "overall_similarity_score": float(m.overall_score),
            "text_similarity_score": float(m.text_sim) if m.text_sim else None,
            "embedding_similarity_score": float(m.embedding_sim) if m.embedding_sim else None,
            "region_similarity_score": float(m.region_sim) if m.region_sim else None,
            "score_details": json.dumps(score_details, ensure_ascii=False),
            "overlapping_section_keys": _overlapping_section_keys(
                item_section_keys, hyd.get("section_key"),
            ),
            "severity": severity_for(m.overall_score),
        }
        cur.execute(ALERT_INSERT_SQL, params)
        if cur.fetchone():
            inserted += 1

    db.commit()
    return inserted


def scan_and_store(
    db, item: Dict[str, Any], *, candidate_record_ids=None,
) -> Dict[str, Any]:
    """One-shot: scan + store. Updates last_scan_at on the watchlist item."""
    started = time.time()
    matches = scan_watchlist_item(db, item, candidate_record_ids=candidate_record_ids)
    new_alerts = store_alerts_for_item(db, item, matches)

    cur = db.cursor()
    cur.execute(
        "UPDATE cografi_watchlist_mt SET last_scan_at = NOW() WHERE id = %s",
        (str(item["id"]),),
    )
    db.commit()

    return {
        "watchlist_item_id": str(item["id"]),
        "watch_type": item.get("watch_type"),
        "matches_found": len(matches),
        "alerts_created": new_alerts,
        "duration_ms": int((time.time() - started) * 1000),
    }


def trigger_cografi_watchlist_scan(
    record_ids: Sequence[str],
    *,
    source_type: str = "bulletin",
    source_reference: Optional[str] = None,
) -> int:
    """Post-ingest hook: scan all active watchlists against newly landed records.

    Called by ``pipeline/ingest_cografi.py`` after the ingest run finishes.
    Scoped to new record IDs so cost is O(watchlist_count * new_ids).
    Failures on individual items are logged but never propagate — a
    busted watchlist row should not poison a successful ingest run.
    """
    from database.crud import Database
    from services.cografi_watchlist_service import get_active_cografi_watchlist_items

    if not record_ids:
        return 0

    total_new_alerts = 0
    with Database() as db:
        items = get_active_cografi_watchlist_items(db=db)
        for item in items:
            try:
                result = scan_and_store(
                    db, item, candidate_record_ids=list(record_ids),
                )
                total_new_alerts += int(result.get("alerts_created") or 0)
            except Exception:
                logger.exception(
                    "trigger_cografi_watchlist_scan: scan failed for item %s",
                    item.get("id"),
                )
    logger.info(
        "trigger_cografi_watchlist_scan: source=%s ref=%s records=%d alerts=%d",
        source_type, source_reference, len(record_ids), total_new_alerts,
    )
    return total_new_alerts


def scan_all_active_items(db) -> Dict[str, Any]:
    """Iterate every active watchlist item and run scan_and_store.

    Used by the scheduler. Returns aggregated stats.
    """
    from services.cografi_watchlist_service import get_active_cografi_watchlist_items

    items = get_active_cografi_watchlist_items(db=db)
    summary = {"items_scanned": 0, "alerts_created": 0, "errors": 0}
    for item in items:
        try:
            r = scan_and_store(db, item)
            summary["items_scanned"] += 1
            summary["alerts_created"] += int(r.get("alerts_created") or 0)
        except Exception:
            logger.exception("scan_all_active_items: failed for item %s", item.get("id"))
            summary["errors"] += 1
    return summary

"""Patent / Faydalı Model watchlist scanner.

Sister to the design scanner. Two scan modes corresponding to the two
watchlist watch types:

  * ``holder``    — find every active patent whose patent_holders row
                    matches the watched holder (by holder_id, then
                    tpe_client_id, then name trigram). Holder watches
                    don't have a similarity score in the usual sense;
                    every match counts. We store ``overall_similarity_score``
                    of 1.0 and ``match_type='holder'``.
  * ``reference`` — cosine similarity against
                    ``patent_watchlist_mt.reference_embedding`` over
                    ``patents.title_abstract_embedding``. When the
                    watchlist also stores a ``reference_query``, the
                    text trigram score is combined (hybrid). Severity
                    buckets follow the designs convention.

Conflict storage floor: 0.50 (mirrors design_scanner_service). Per-item
cap: 10 alerts per scan run.

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


logger = logging.getLogger("turkpatent.patent_scanner")


# Bucket thresholds (overall_similarity_score → severity). Mirrors
# the design alerts convention so existing UI patterns port unchanged.
SEVERITY_THRESHOLDS = (
    (0.85, "critical"),
    (0.70, "high"),
    (0.55, "medium"),
    (0.0,  "low"),
)
CONFLICT_FLOOR = 0.50      # alerts below this aren't stored
ALERTS_PER_SCAN_CAP = 10   # per watchlist item, per scan run

# Reference-watch hybrid weights (when both query + embedding are present).
WEIGHTS_REF_HYBRID = {"text": 0.4, "embedding": 0.6}

EXCLUDED_RECORD_TYPES = ("UNKNOWN", "LEGACY")
TRIGRAM_THRESHOLD = 0.2


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
    patent_id: str
    overall_score: float
    text_sim: float = 0.0
    embedding_sim: float = 0.0
    match_type: str = "reference_embedding"


# ---------------------------------------------------------------------------
# Filter SQL builder (shared between holder + reference scans)
# ---------------------------------------------------------------------------

def _common_filter_clauses(
    *,
    ipc_classes: Optional[Sequence[str]],
    kind_codes: Optional[Sequence[str]],
    customer_application_no: Optional[str],
    candidate_patent_ids: Optional[Sequence[str]] = None,
    table_alias: str = "p",
) -> tuple[str, Dict[str, Any]]:
    parts: List[str] = []
    params: Dict[str, Any] = {}
    parts.append(f" AND {table_alias}.record_type NOT IN %(_excluded)s")
    params["_excluded"] = EXCLUDED_RECORD_TYPES
    if ipc_classes:
        parts.append(f" AND {table_alias}.ipc_classes && %(_ipc)s::text[]")
        params["_ipc"] = list(ipc_classes)
    if kind_codes:
        parts.append(f" AND {table_alias}.kind_code = ANY(%(_kinds)s::text[])")
        params["_kinds"] = list(kind_codes)
    if customer_application_no:
        parts.append(f" AND {table_alias}.application_no <> %(_self)s")
        params["_self"] = customer_application_no
    if candidate_patent_ids:
        parts.append(f" AND {table_alias}.id::text = ANY(%(_candidates)s::text[])")
        params["_candidates"] = list(candidate_patent_ids)
    return "".join(parts), params


# ---------------------------------------------------------------------------
# Holder scan
# ---------------------------------------------------------------------------

def _scan_holder(cur, item: Dict[str, Any], *, candidate_patent_ids=None) -> List[ScanMatch]:
    """Find patents whose patent_holders row matches the watched holder.

    Match priority: holder_id (FK) > tpe_client_id > name trigram. We
    OR all three so a partial-data watchlist row still finds matches.
    """
    holder_id = item.get("holder_id")
    holder_tpe = item.get("holder_tpe_client_id")
    holder_name = item.get("holder_name")
    if not (holder_id or holder_tpe or holder_name):
        return []

    filter_sql, filter_params = _common_filter_clauses(
        ipc_classes=item.get("ipc_classes"),
        kind_codes=item.get("kind_codes"),
        customer_application_no=item.get("customer_application_no"),
        candidate_patent_ids=candidate_patent_ids,
        table_alias="p",
    )

    # patent_holders has both holder_id (FK to global holders) and a
    # denormalized name — match either side. tpe_client_id lives on
    # the holders table only.
    parts: List[str] = []
    params: Dict[str, Any] = {}
    if holder_id:
        parts.append("ph.holder_id = %(holder_id)s")
        params["holder_id"] = holder_id
    if holder_tpe:
        parts.append("EXISTS (SELECT 1 FROM holders h WHERE h.id = ph.holder_id "
                     "AND h.tpe_client_id = %(holder_tpe)s)")
        params["holder_tpe"] = holder_tpe
    if holder_name:
        # Exact case-insensitive match on the denormalized name. Trigram
        # is intentionally NOT used here — competitor tracking wants
        # "ACME Corp", not every company sharing 'Corp' as a substring.
        # (Smoke caught this with ERRESSE S.R.L. matching all Italian
        # S.R.L. companies via shared trigrams.)
        parts.append("LOWER(ph.name) = LOWER(%(holder_name)s)")
        params["holder_name"] = holder_name
    holder_clause = "(" + " OR ".join(parts) + ")"

    sql = f"""
        SELECT DISTINCT p.id::text AS patent_id
        FROM patents p
        JOIN patent_holders ph ON ph.patent_id = p.id
        WHERE {holder_clause}
          {filter_sql}
        LIMIT 5000
    """
    cur.execute(sql, {**params, **filter_params})
    return [
        ScanMatch(patent_id=row[0] if not isinstance(row, dict) else row["patent_id"],
                  overall_score=1.0, match_type="holder")
        for row in cur.fetchall()
    ]


# ---------------------------------------------------------------------------
# Reference scan (text + embedding hybrid)
# ---------------------------------------------------------------------------

def _scan_reference(cur, item: Dict[str, Any], *, candidate_patent_ids=None) -> List[ScanMatch]:
    """Find patents whose title_abstract_embedding is similar to the
    watched reference. When ``reference_query`` is also set, combine
    trigram on title with embedding cosine."""
    ref_emb = item.get("reference_embedding")  # halfvec text literal
    ref_query = item.get("reference_query")
    if not ref_emb and not ref_query:
        return []

    filter_sql, filter_params = _common_filter_clauses(
        ipc_classes=item.get("ipc_classes"),
        kind_codes=item.get("kind_codes"),
        customer_application_no=item.get("customer_application_no"),
        candidate_patent_ids=candidate_patent_ids,
        table_alias="p",
    )

    matches: Dict[str, ScanMatch] = {}

    if ref_emb:
        sql = f"""
            SELECT p.id::text AS patent_id,
                   1 - (p.title_abstract_embedding <=> %(vec)s::halfvec) AS sim
            FROM patents p
            WHERE p.title_abstract_embedding IS NOT NULL
              {filter_sql}
            ORDER BY p.title_abstract_embedding <=> %(vec)s::halfvec
            LIMIT 200
        """
        cur.execute(sql, {"vec": ref_emb, **filter_params})
        for row in cur.fetchall():
            pid = row[0] if not isinstance(row, dict) else row["patent_id"]
            sim = float(row[1] if not isinstance(row, dict) else row["sim"])
            matches[pid] = ScanMatch(patent_id=pid, embedding_sim=sim,
                                     overall_score=sim, match_type="reference_embedding")

    if ref_query:
        sql = f"""
            SELECT p.id::text AS patent_id,
                   similarity(LOWER(COALESCE(p.title,'')), LOWER(%(q)s)) AS sim
            FROM patents p
            WHERE p.title IS NOT NULL
              AND similarity(LOWER(p.title), LOWER(%(q)s)) > %(thresh)s
              {filter_sql}
            ORDER BY sim DESC
            LIMIT 200
        """
        cur.execute(sql, {"q": ref_query, "thresh": TRIGRAM_THRESHOLD, **filter_params})
        for row in cur.fetchall():
            pid = row[0] if not isinstance(row, dict) else row["patent_id"]
            sim = float(row[1] if not isinstance(row, dict) else row["sim"])
            existing = matches.get(pid)
            if existing:
                existing.text_sim = sim
                # Hybrid score
                existing.overall_score = (
                    WEIGHTS_REF_HYBRID["text"] * existing.text_sim
                    + WEIGHTS_REF_HYBRID["embedding"] * existing.embedding_sim
                )
                existing.match_type = "reference_hybrid"
            else:
                matches[pid] = ScanMatch(patent_id=pid, text_sim=sim,
                                         overall_score=sim, match_type="reference_text")

    return list(matches.values())


# ---------------------------------------------------------------------------
# Top-level scanner entry
# ---------------------------------------------------------------------------

def scan_watchlist_item(
    db, item: Dict[str, Any], *, candidate_patent_ids=None,
) -> List[ScanMatch]:
    """Run the appropriate scan for a single watchlist item.

    ``candidate_patent_ids`` (optional) restricts the corpus scan to a
    specific subset — used by the post-ingest hook to scan only newly
    landed patents instead of re-scanning the full ~280K-row corpus.
    """
    cur = db.cursor()
    watch_type = (item.get("watch_type") or "").strip().lower()
    if watch_type == "holder":
        return _scan_holder(cur, item, candidate_patent_ids=candidate_patent_ids)
    elif watch_type == "reference":
        return _scan_reference(cur, item, candidate_patent_ids=candidate_patent_ids)
    else:
        logger.warning("scan_watchlist_item: unknown watch_type %r for item %s",
                       watch_type, item.get("id"))
        return []


# ---------------------------------------------------------------------------
# Alert upsert
# ---------------------------------------------------------------------------

ALERT_CONFLICT_COLS = (
    "watchlist_item_id, user_id, organization_id, "
    "conflicting_patent_id, conflicting_application_no, conflicting_publication_no, "
    "conflicting_kind_code, conflicting_title, conflicting_abstract, "
    "conflicting_ipc_classes, conflicting_holder_name, conflicting_holder_country, "
    "conflicting_bulletin_no, conflicting_bulletin_date, conflicting_application_date, "
    "match_type, overall_similarity_score, text_similarity_score, embedding_similarity_score, "
    "score_details, overlapping_ipc_classes, severity"
)


def _hydrate_for_alert(cur, patent_id: str) -> Optional[Dict[str, Any]]:
    """Pull the conflict-reference fields we denormalize into patent_alerts_mt."""
    cur.execute(
        """
        SELECT p.application_no, p.publication_no, p.kind_code, p.title, p.abstract,
               p.ipc_classes, p.bulletin_no, p.bulletin_date, p.application_date,
               (SELECT ph.name FROM patent_holders ph
                 WHERE ph.patent_id = p.id ORDER BY ph.seq ASC LIMIT 1) AS holder_name,
               (SELECT ph.country FROM patent_holders ph
                 WHERE ph.patent_id = p.id ORDER BY ph.seq ASC LIMIT 1) AS holder_country
        FROM patents p
        WHERE p.id = %s
        """,
        (patent_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _overlapping_ipc(item_ipc: Optional[Sequence[str]],
                     conflict_ipc: Optional[Sequence[str]]) -> List[str]:
    if not item_ipc or not conflict_ipc:
        return []
    item_set = {c.upper().strip() for c in item_ipc if c}
    return [c for c in (conflict_ipc or [])
            if c and c.upper().strip() in item_set]


def store_alerts_for_item(
    db,
    item: Dict[str, Any],
    matches: Sequence[ScanMatch],
) -> int:
    """Filter matches above the floor, dedup against existing alerts, insert new.

    Returns the number of NEW alerts written (existing ones are skipped via
    the partial unique index uq_patent_alerts_pair).
    """
    if not matches:
        return 0

    cur = db.cursor()

    # Apply per-watchlist threshold (defaults to 0.50 in schema). Holder
    # watches always score 1.0 so they're never filtered by the threshold.
    threshold = max(CONFLICT_FLOOR, float(item.get("similarity_threshold") or 0.0))
    above = [m for m in matches if m.overall_score >= threshold]
    above.sort(key=lambda m: m.overall_score, reverse=True)
    above = above[:ALERTS_PER_SCAN_CAP]

    if not above:
        return 0

    inserted = 0
    item_ipc = item.get("ipc_classes") or []

    for m in above:
        hyd = _hydrate_for_alert(cur, m.patent_id)
        if not hyd:
            continue
        overlap = _overlapping_ipc(item_ipc, hyd.get("ipc_classes"))
        score_details = {
            "text_sim": round(float(m.text_sim or 0.0), 4),
            "embedding_sim": round(float(m.embedding_sim or 0.0), 4),
            "overall": round(float(m.overall_score or 0.0), 4),
        }
        params = {
            "watchlist_item_id": str(item["id"]),
            "user_id": str(item["user_id"]) if item.get("user_id") else None,
            "organization_id": str(item["organization_id"]),
            "conflicting_patent_id": m.patent_id,
            "conflicting_application_no": hyd.get("application_no"),
            "conflicting_publication_no": hyd.get("publication_no"),
            "conflicting_kind_code": hyd.get("kind_code"),
            "conflicting_title": hyd.get("title"),
            "conflicting_abstract": hyd.get("abstract"),
            "conflicting_ipc_classes": list(hyd.get("ipc_classes") or []),
            "conflicting_holder_name": hyd.get("holder_name"),
            "conflicting_holder_country": hyd.get("holder_country"),
            "conflicting_bulletin_no": hyd.get("bulletin_no"),
            "conflicting_bulletin_date": hyd.get("bulletin_date"),
            "conflicting_application_date": hyd.get("application_date"),
            "match_type": m.match_type,
            "overall_similarity_score": float(m.overall_score),
            "text_similarity_score": float(m.text_sim) if m.text_sim else None,
            "embedding_similarity_score": float(m.embedding_sim) if m.embedding_sim else None,
            "score_details": json.dumps(score_details, ensure_ascii=False),
            "overlapping_ipc_classes": overlap,
            "severity": severity_for(m.overall_score),
        }
        # ON CONFLICT DO NOTHING via the partial unique index
        cur.execute(
            f"""
            INSERT INTO patent_alerts_mt ({ALERT_CONFLICT_COLS})
            VALUES (
                %(watchlist_item_id)s, %(user_id)s, %(organization_id)s,
                %(conflicting_patent_id)s, %(conflicting_application_no)s, %(conflicting_publication_no)s,
                %(conflicting_kind_code)s, %(conflicting_title)s, %(conflicting_abstract)s,
                %(conflicting_ipc_classes)s, %(conflicting_holder_name)s, %(conflicting_holder_country)s,
                %(conflicting_bulletin_no)s, %(conflicting_bulletin_date)s, %(conflicting_application_date)s,
                %(match_type)s, %(overall_similarity_score)s, %(text_similarity_score)s, %(embedding_similarity_score)s,
                %(score_details)s::jsonb, %(overlapping_ipc_classes)s, %(severity)s
            )
            ON CONFLICT (watchlist_item_id, conflicting_patent_id)
            WHERE conflicting_patent_id IS NOT NULL
            DO NOTHING
            RETURNING id
            """,
            params,
        )
        if cur.fetchone():
            inserted += 1

    db.commit()
    return inserted


def scan_and_store(
    db, item: Dict[str, Any], *, candidate_patent_ids=None,
) -> Dict[str, Any]:
    """One-shot: scan + store. Updates last_scan_at on the watchlist item.

    Pass ``candidate_patent_ids`` to scope the scan to a subset of the
    corpus (post-ingest hook usage).
    """
    started = time.time()
    matches = scan_watchlist_item(db, item, candidate_patent_ids=candidate_patent_ids)
    new_alerts = store_alerts_for_item(db, item, matches)

    cur = db.cursor()
    cur.execute(
        "UPDATE patent_watchlist_mt SET last_scan_at = NOW() WHERE id = %s",
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


def trigger_patent_watchlist_scan(
    patent_ids: Sequence[str],
    *,
    source_type: str = "bulletin",
    source_reference: Optional[str] = None,
) -> int:
    """Post-ingest hook: scan all active watchlists against newly landed patents.

    Called by ``pipeline/ingest_patents.py`` after a bulletin completes.
    Scans every active watchlist row but scopes each scan to the new
    patent IDs (not the full corpus) so cost is O(watchlist_count * new_ids)
    rather than O(watchlist_count * corpus_size).

    Returns the total number of new alerts generated across all watchlists.
    Failures on individual items are logged but never propagate — a busted
    watchlist row should not poison a successful ingest run.
    """
    from database.crud import Database
    from services.patent_watchlist_service import get_active_patent_watchlist_items

    if not patent_ids:
        return 0

    total_new_alerts = 0
    with Database() as db:
        items = get_active_patent_watchlist_items(db=db)
        for item in items:
            try:
                result = scan_and_store(
                    db, item, candidate_patent_ids=list(patent_ids),
                )
                total_new_alerts += int(result.get("alerts_created") or 0)
            except Exception:
                logger.exception(
                    "trigger_patent_watchlist_scan: scan failed for item %s",
                    item.get("id"),
                )
    logger.info(
        "trigger_patent_watchlist_scan: source=%s ref=%s patents=%d alerts=%d",
        source_type, source_reference, len(patent_ids), total_new_alerts,
    )
    return total_new_alerts


def scan_all_active_items(db) -> Dict[str, Any]:
    """Iterate over every active watchlist item and run scan_and_store.

    Used by the post-ingest hook (next commit). Returns aggregated stats."""
    from services.patent_watchlist_service import get_active_patent_watchlist_items

    items = get_active_patent_watchlist_items(db=db)
    summary = {"items_scanned": 0, "alerts_created": 0, "errors": 0}
    for item in items:
        try:
            result = scan_and_store(db, item)
            summary["items_scanned"] += 1
            summary["alerts_created"] += result["alerts_created"]
        except Exception:
            logger.exception("scan_and_store failed for watchlist item %s", item.get("id"))
            summary["errors"] += 1
    return summary

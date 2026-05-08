"""Design watchlist scanner.

Scans newly-ingested designs against active design watchlist items and
generates alerts above the conflict floor. Sister to ``watchlist/scanner.py``
(Marka), but design-tuned:

  * Uses the design search combiner: ``0.55*dino + 0.30*clip + 0.10*color +
    0.05*text`` when the watchlist item has an image embedding, else
    ``0.70*text + 0.20*dino + 0.10*clip + 0.0*color`` (which collapses to text
    only for image-less watchlist items).
  * No phonetic/translation/OCR signals.
  * Overall floor for alert storage: ``CONFLICT_FLOOR = 0.50``.
  * Per-item cap to prevent flooding: ``MAX_ALERTS_PER_ITEM = 10``.

Public entry points:
  * ``scan_new_designs(design_ids, source_type, source_reference)`` — called
    after the design ingest pipeline upserts a batch.
  * ``scan_single_design_watchlist(item_id)`` — full corpus scan for one
    watchlist item (e.g. when the user just added it).
  * ``trigger_design_watchlist_scan(...)`` — convenience wrapper that opens
    its own DB connection.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence
from uuid import UUID

from database.crud import Database
from services.design_alert_service import insert_alert_row
from services.design_watchlist_service import (
    get_active_design_watchlist_items,
    update_last_scan_at,
)


logger = logging.getLogger("turkpatent.design_scanner")


CONFLICT_FLOOR = 0.50
MAX_ALERTS_PER_ITEM = 10

# Mirror of services/design_search_service.combine_scores — duplicated here so
# the scanner has no import-cycle risk when called from the ingest pipeline.
WEIGHTS_IMAGE = {"dinov2": 0.55, "clip": 0.30, "color": 0.10, "text": 0.05}
WEIGHTS_TEXT_ONLY = {"text": 0.70, "dinov2": 0.20, "clip": 0.10, "color": 0.0}


def combine_scores(*, text=0.0, dinov2=0.0, clip=0.0, color=0.0, has_image: bool) -> float:
    weights = WEIGHTS_IMAGE if has_image else WEIGHTS_TEXT_ONLY
    score = (
        weights["text"] * max(0.0, float(text or 0.0))
        + weights["dinov2"] * max(0.0, float(dinov2 or 0.0))
        + weights["clip"] * max(0.0, float(clip or 0.0))
        + weights["color"] * max(0.0, float(color or 0.0))
    )
    return min(1.0, score)


def overlap_locarno(a: Sequence[str], b: Sequence[str]) -> List[str]:
    set_a = {str(v).upper() for v in (a or [])}
    set_b = {str(v).upper() for v in (b or [])}
    return sorted(set_a & set_b)


def _select_candidates_for_item(
    cur,
    *,
    watchlist_item: Dict[str, Any],
    candidate_design_ids: Optional[Sequence[str]] = None,
    floor: float = CONFLICT_FLOOR,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Find conflict candidates for a single watchlist item.

    When ``candidate_design_ids`` is set, only those designs are considered
    (post-ingest mode). When None, the full ``designs`` corpus is queried
    (manual single-item scan).
    """
    has_image = watchlist_item.get("dinov2_embedding") is not None
    product_name = watchlist_item.get("product_name") or ""
    locarno = list(watchlist_item.get("locarno_classes") or [])

    # Self-conflict exclusion: never alert on the user's own design row.
    customer_app = watchlist_item.get("customer_application_no")
    customer_reg = watchlist_item.get("customer_registration_no")

    where = ["d.registry_type = 'design'"]
    params: List[Any] = []

    if candidate_design_ids:
        where.append("d.id = ANY(%s::uuid[])")
        params.append([str(i) for i in candidate_design_ids])

    if customer_app:
        where.append("(d.application_no IS NULL OR d.application_no <> %s)")
        params.append(str(customer_app))
    if customer_reg:
        where.append("(d.registration_no IS NULL OR d.registration_no <> %s)")
        params.append(str(customer_reg))

    # Exclude the watchlist's own reference design row, if cloned from one.
    ref_id = watchlist_item.get("reference_design_id")
    if ref_id:
        where.append("d.id <> %s")
        params.append(str(ref_id))

    where_sql = " AND ".join(where)

    text_q = product_name.strip()

    select_parts = [
        "d.id",
        "d.application_no",
        "d.registration_no",
        "d.product_name_tr AS product_name",
        "d.locarno_classes",
        "d.bulletin_no",
        "d.bulletin_date",
        "d.opposition_end",
        "h.name AS holder_name",
    ]

    # Per-signal similarity columns (NULL when corresponding embedding missing).
    if has_image:
        select_parts += [
            "1.0 - (d.dinov2_vitl14_mean <=> %s::halfvec) AS dino_sim",
            "1.0 - (d.clip_vitb32_mean   <=> %s::halfvec) AS clip_sim",
            (
                "(SELECT 1.0 - (color_hsv <=> %s::halfvec) "
                " FROM design_views WHERE design_id = d.id "
                " ORDER BY view_index ASC LIMIT 1) AS color_sim"
            ),
        ]
        # Note: psycopg2 substitutes positionally; the dinov2/clip params
        # come BEFORE the where-clause params.
        head_params: List[Any] = [
            watchlist_item.get("dinov2_embedding"),
            watchlist_item.get("clip_embedding"),
            watchlist_item.get("color_histogram"),
        ]
    else:
        select_parts += [
            "NULL::real AS dino_sim",
            "NULL::real AS clip_sim",
            "NULL::real AS color_sim",
        ]
        head_params = []

    if text_q:
        select_parts.append("similarity(COALESCE(d.product_name_tr,''), %s) AS text_sim")
        text_param: List[Any] = [text_q]
    else:
        select_parts.append("0.0 AS text_sim")
        text_param = []

    image_path_select = (
        "(SELECT image_path FROM design_views WHERE design_id = d.id "
        " ORDER BY view_index ASC LIMIT 1) AS first_image_path"
    )
    select_parts.append(image_path_select)

    select_sql = ",\n               ".join(select_parts)

    sql = f"""
        SELECT {select_sql}
        FROM designs d
        LEFT JOIN holders h ON d.holder_id = h.id
        WHERE {where_sql}
        ORDER BY d.created_at DESC
        LIMIT {int(limit)}
    """
    cur.execute(sql, head_params + text_param + params)
    rows = cur.fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        # cursor_factory may return RealDictRow or dict — both expose .get
        dino = _coerce_score(r.get("dino_sim"))
        clip = _coerce_score(r.get("clip_sim"))
        color = _coerce_score(r.get("color_sim"))
        text = _coerce_score(r.get("text_sim"))

        overall = combine_scores(
            text=text or 0.0,
            dinov2=dino or 0.0,
            clip=clip or 0.0,
            color=color or 0.0,
            has_image=has_image,
        )
        if overall < floor:
            continue

        candidate = {
            "id": r.get("id"),
            "application_no": r.get("application_no"),
            "registration_no": r.get("registration_no"),
            "product_name": r.get("product_name"),
            "locarno_classes": list(r.get("locarno_classes") or []),
            "holder_name": r.get("holder_name"),
            "image_path": r.get("first_image_path"),
            "bulletin_no": r.get("bulletin_no"),
            "bulletin_date": r.get("bulletin_date"),
            "opposition_end": r.get("opposition_end"),
            "scores": {
                "overall": overall,
                "dinov2": dino,
                "clip": clip,
                "color": color,
                "text": text,
                "details": {"has_image_signal": has_image},
            },
            "overlapping_classes": overlap_locarno(locarno, r.get("locarno_classes") or []),
        }
        out.append(candidate)

    out.sort(key=lambda c: c["scores"]["overall"], reverse=True)
    return out[:MAX_ALERTS_PER_ITEM]


def _coerce_score(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def scan_new_designs(
    *,
    design_ids: Sequence[UUID],
    source_type: str,
    source_reference: str,
    db_factory=Database,
) -> int:
    """After-ingest hook. Compares ``design_ids`` against every active
    design watchlist item and writes alerts above the floor. Returns the
    total alerts inserted (deduped by unique pair index).
    """
    if not design_ids:
        logger.info("scan_new_designs: empty design_ids — nothing to do")
        return 0

    with db_factory() as db:
        watchlist_items = get_active_design_watchlist_items(db=db)
        if not watchlist_items:
            logger.info("scan_new_designs: no active design watchlist items")
            return 0

        cur = db.cursor()
        alerts_inserted = 0
        for wl in watchlist_items:
            try:
                # Post-ingest mode: evaluate every newly-ingested design, not
                # just the top-100 by created_at. The default LIMIT in
                # _select_candidates_for_item is for full-corpus scans only.
                candidates = _select_candidates_for_item(
                    cur,
                    watchlist_item=wl,
                    candidate_design_ids=[str(i) for i in design_ids],
                    limit=max(100, len(design_ids)),
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("design watchlist %s scan failed: %r", wl.get("id"), exc)
                continue

            for c in candidates:
                alert_id = insert_alert_row(
                    db=db,
                    watchlist_item=wl,
                    conflicting_design=c,
                    scores=c["scores"],
                    overlapping_classes=c["overlapping_classes"],
                    source_type=source_type,
                    source_reference=source_reference,
                )
                if alert_id is not None:
                    alerts_inserted += 1
                    logger.info(
                        "design alert: '%s' vs design=%s score=%.2f overlap=%s",
                        wl.get("product_name"),
                        c.get("application_no") or c.get("registration_no") or c.get("id"),
                        c["scores"]["overall"],
                        c["overlapping_classes"],
                    )
            update_last_scan_at(item_id=UUID(str(wl["id"])), db=db)

        db.commit()
        logger.info(
            "scan_new_designs: %d alerts inserted across %d watchlist items / %d new designs",
            alerts_inserted, len(watchlist_items), len(design_ids),
        )
        return alerts_inserted


def scan_single_design_watchlist(
    *,
    item_id: UUID,
    db_factory=Database,
) -> int:
    """Full corpus scan for a single watchlist item — used when the user
    creates/updates an item interactively.
    """
    with db_factory() as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM design_watchlist_mt WHERE id = %s AND is_active = TRUE", (str(item_id),))
        wl = cur.fetchone()
        if not wl:
            logger.warning("scan_single_design_watchlist: %s not found or inactive", item_id)
            return 0

        candidates = _select_candidates_for_item(
            cur,
            watchlist_item=dict(wl),
            candidate_design_ids=None,
            limit=200,
        )

        alerts_inserted = 0
        for c in candidates:
            alert_id = insert_alert_row(
                db=db,
                watchlist_item=dict(wl),
                conflicting_design=c,
                scores=c["scores"],
                overlapping_classes=c["overlapping_classes"],
                source_type="manual_scan",
                source_reference=str(item_id),
            )
            if alert_id is not None:
                alerts_inserted += 1
        update_last_scan_at(item_id=UUID(str(wl["id"])), db=db)
        db.commit()
        logger.info(
            "scan_single_design_watchlist: item=%s, candidates=%d, inserted=%d",
            item_id, len(candidates), alerts_inserted,
        )
        return alerts_inserted


def trigger_design_watchlist_scan(
    design_ids: Iterable[UUID],
    source_type: str = "bulletin",
    source_reference: str = "",
) -> int:
    """Convenience wrapper for the ingest pipeline. Swallows exceptions so a
    failed scan never blocks a successful ingest.
    """
    ids = list(design_ids or [])
    if not ids:
        return 0
    try:
        return scan_new_designs(
            design_ids=ids,
            source_type=source_type,
            source_reference=source_reference,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("trigger_design_watchlist_scan failed: %r", exc)
        return 0

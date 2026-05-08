"""Design alert service — list/get/lifecycle over ``design_alerts_mt``.

Sister to ``services/alert_service.py`` (Marka). Lighter surface: designs do
not yet have an opposition deadline classifier, phonetic match, or
translation similarity, so the formatter and queries are simpler.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status

from database.crud import Database


logger = logging.getLogger("turkpatent.design_alert")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

VALID_STATUSES = ("new", "seen", "acknowledged", "resolved", "dismissed")
VALID_SEVERITIES = ("low", "medium", "high", "critical")


def severity_for_score(score: float) -> str:
    """Map an overall similarity score (0..1) to a severity label."""
    if score >= 0.85:
        return "critical"
    if score >= 0.70:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _row_to_dict(row) -> Dict[str, Any]:
    if row is None:
        return {}
    return dict(row)


def _coerce_score_details(raw) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def format_design_alert(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a DB row into a serializable alert payload."""
    if not row:
        return {}
    details = _coerce_score_details(row.get("score_details"))
    return {
        "id": str(row["id"]),
        "watchlist_item_id": str(row["watchlist_item_id"]),
        "organization_id": str(row["organization_id"]),
        "user_id": str(row["user_id"]) if row.get("user_id") else None,
        "conflicting": {
            "design_id": str(row["conflicting_design_id"]) if row.get("conflicting_design_id") else None,
            "application_no": row.get("conflicting_application_no"),
            "registration_no": row.get("conflicting_registration_no"),
            "product_name": row.get("conflicting_product_name"),
            "locarno_classes": list(row.get("conflicting_locarno_classes") or []),
            "holder_name": row.get("conflicting_holder_name"),
            "image_path": row.get("conflicting_image_path"),
            "bulletin_no": row.get("conflicting_bulletin_no"),
            "bulletin_date": (
                row["conflicting_bulletin_date"].isoformat()
                if row.get("conflicting_bulletin_date") else None
            ),
        },
        "scores": {
            "overall": float(row.get("overall_similarity_score") or 0.0),
            "dinov2": _maybe_float(row.get("dino_similarity_score")),
            "clip": _maybe_float(row.get("clip_similarity_score")),
            "color": _maybe_float(row.get("color_similarity_score")),
            "text": _maybe_float(row.get("text_similarity_score")),
            "details": details,
        },
        "overlapping_classes": list(row.get("overlapping_classes") or []),
        "severity": row.get("severity"),
        "status": row.get("status"),
        "alert_type": row.get("alert_type"),
        "source_type": row.get("source_type"),
        "source_reference": row.get("source_reference"),
        "opposition_deadline": (
            row["opposition_deadline"].isoformat()
            if row.get("opposition_deadline") else None
        ),
        "email_sent": bool(row.get("email_sent")),
        "acknowledged_at": (
            row["acknowledged_at"].isoformat()
            if row.get("acknowledged_at") else None
        ),
        "resolved_at": (
            row["resolved_at"].isoformat()
            if row.get("resolved_at") else None
        ),
        "resolution_notes": row.get("resolution_notes"),
        "created_at": (
            row["created_at"].isoformat()
            if row.get("created_at") else None
        ),
    }


def _maybe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Service methods
# ---------------------------------------------------------------------------

def list_design_alerts(
    *,
    current_user,
    page: int = 1,
    page_size: int = 20,
    status_filters: Optional[List[str]] = None,
    severity_filters: Optional[List[str]] = None,
    watchlist_item_id: Optional[UUID] = None,
    min_score: float = 0.0,
    db_factory=Database,
) -> Dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    offset = (page - 1) * page_size

    normalized_min_score = float(min_score) / 100.0 if min_score > 1.0 else float(min_score)

    where = ["a.organization_id = %s"]
    params: List[Any] = [str(current_user.organization_id)]

    if status_filters:
        valid = [s for s in status_filters if s in VALID_STATUSES]
        if valid:
            placeholders = ", ".join(["%s"] * len(valid))
            where.append(f"a.status IN ({placeholders})")
            params.extend(valid)
    if severity_filters:
        valid = [s for s in severity_filters if s in VALID_SEVERITIES]
        if valid:
            placeholders = ", ".join(["%s"] * len(valid))
            where.append(f"a.severity IN ({placeholders})")
            params.extend(valid)
    if watchlist_item_id is not None:
        where.append("a.watchlist_item_id = %s")
        params.append(str(watchlist_item_id))
    if normalized_min_score > 0:
        where.append("a.overall_similarity_score >= %s")
        params.append(normalized_min_score)

    where_sql = " AND ".join(where)

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(f"SELECT COUNT(*) AS c FROM design_alerts_mt a WHERE {where_sql}", params)
        total_row = cur.fetchone()
        total = int(total_row.get("c") if isinstance(total_row, dict) else total_row[0])

        cur.execute(
            f"""
            SELECT a.*, w.product_name AS watched_product_name,
                   w.locarno_classes AS watched_locarno_classes
            FROM design_alerts_mt a
            LEFT JOIN design_watchlist_mt w ON a.watchlist_item_id = w.id
            WHERE {where_sql}
            ORDER BY a.created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        )
        rows = cur.fetchall()

    items = []
    for row in rows:
        payload = format_design_alert(_row_to_dict(row))
        payload["watched_product_name"] = row.get("watched_product_name") if isinstance(row, dict) else None
        payload["watched_locarno_classes"] = list(
            (row.get("watched_locarno_classes") if isinstance(row, dict) else None) or []
        )
        items.append(payload)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }


def get_design_alerts_summary(*, current_user, db_factory=Database) -> Dict[str, Any]:
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT status, COUNT(*) AS c
            FROM design_alerts_mt
            WHERE organization_id = %s
            GROUP BY status
            """,
            (str(current_user.organization_id),),
        )
        by_status = {
            (row.get("status") if isinstance(row, dict) else row[0]):
            int(row.get("c") if isinstance(row, dict) else row[1])
            for row in cur.fetchall()
        }

        cur.execute(
            """
            SELECT severity, COUNT(*) AS c
            FROM design_alerts_mt
            WHERE organization_id = %s AND status = 'new'
            GROUP BY severity
            """,
            (str(current_user.organization_id),),
        )
        by_severity = {
            (row.get("severity") if isinstance(row, dict) else row[0]):
            int(row.get("c") if isinstance(row, dict) else row[1])
            for row in cur.fetchall()
        }

    return {
        "by_status": by_status,
        "by_severity": by_severity,
        "total_new": by_status.get("new", 0),
    }


def get_design_alert(*, alert_id: UUID, current_user, db_factory=Database) -> Dict[str, Any]:
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT * FROM design_alerts_mt
            WHERE id = %s AND organization_id = %s
            """,
            (str(alert_id), str(current_user.organization_id)),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Design alert not found",
            )
        # Mark as 'seen' when first opened
        if row.get("status") == "new":
            cur.execute(
                "UPDATE design_alerts_mt SET status = 'seen', updated_at = NOW() WHERE id = %s",
                (str(alert_id),),
            )
            db.commit()
            row = dict(row)
            row["status"] = "seen"
    return format_design_alert(_row_to_dict(row))


def _transition_alert(
    *,
    alert_id: UUID,
    next_status: str,
    notes: Optional[str],
    current_user,
    db_factory=Database,
) -> Dict[str, Any]:
    if next_status not in VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid alert status: {next_status}",
        )
    set_clauses = ["status = %s", "updated_at = NOW()"]
    params: List[Any] = [next_status]

    if next_status == "acknowledged":
        set_clauses += ["acknowledged_at = NOW()", "acknowledged_by = %s"]
        params.append(str(current_user.id))
    elif next_status == "resolved":
        set_clauses += ["resolved_at = NOW()", "resolved_by = %s"]
        params.append(str(current_user.id))
        if notes is not None:
            set_clauses.append("resolution_notes = %s")
            params.append(notes)
    elif next_status == "dismissed":
        set_clauses += ["resolved_at = NOW()", "resolved_by = %s"]
        params.append(str(current_user.id))
        if notes is not None:
            set_clauses.append("resolution_notes = %s")
            params.append(notes)

    set_sql = ", ".join(set_clauses)
    params += [str(alert_id), str(current_user.organization_id)]

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            UPDATE design_alerts_mt SET {set_sql}
            WHERE id = %s AND organization_id = %s
            RETURNING *
            """,
            params,
        )
        row = cur.fetchone()
        db.commit()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Design alert not found",
        )
    return format_design_alert(_row_to_dict(row))


def acknowledge_design_alert(*, alert_id, notes=None, current_user, db_factory=Database):
    return _transition_alert(
        alert_id=alert_id,
        next_status="acknowledged",
        notes=notes,
        current_user=current_user,
        db_factory=db_factory,
    )


def resolve_design_alert(*, alert_id, notes=None, current_user, db_factory=Database):
    return _transition_alert(
        alert_id=alert_id,
        next_status="resolved",
        notes=notes,
        current_user=current_user,
        db_factory=db_factory,
    )


def dismiss_design_alert(*, alert_id, notes=None, current_user, db_factory=Database):
    return _transition_alert(
        alert_id=alert_id,
        next_status="dismissed",
        notes=notes,
        current_user=current_user,
        db_factory=db_factory,
    )


# ---------------------------------------------------------------------------
# Used by the scanner (not the API)
# ---------------------------------------------------------------------------

def insert_alert_row(
    *,
    db,
    watchlist_item: Dict[str, Any],
    conflicting_design: Dict[str, Any],
    scores: Dict[str, Any],
    overlapping_classes: List[str],
    source_type: str,
    source_reference: str,
) -> Optional[str]:
    """Insert one alert row. ON CONFLICT DO NOTHING per the unique pair index.
    Returns the new alert UUID, or None if it was a duplicate.
    """
    cur = db.cursor()
    overall = float(scores.get("overall") or 0.0)
    severity = severity_for_score(overall)

    params = {
        "watchlist_item_id": str(watchlist_item["id"]),
        "user_id": str(watchlist_item.get("user_id")) if watchlist_item.get("user_id") else None,
        "organization_id": str(watchlist_item["organization_id"]),
        "conflicting_design_id": str(conflicting_design.get("id")) if conflicting_design.get("id") else None,
        "conflicting_application_no": conflicting_design.get("application_no"),
        "conflicting_registration_no": conflicting_design.get("registration_no"),
        "conflicting_product_name": conflicting_design.get("product_name"),
        "conflicting_locarno_classes": list(conflicting_design.get("locarno_classes") or []),
        "conflicting_holder_name": conflicting_design.get("holder_name"),
        "conflicting_image_path": conflicting_design.get("image_path"),
        "conflicting_bulletin_no": conflicting_design.get("bulletin_no"),
        "conflicting_bulletin_date": conflicting_design.get("bulletin_date"),
        "opposition_deadline": conflicting_design.get("opposition_end"),
        "overall_similarity_score": overall,
        "dino_similarity_score": _maybe_float(scores.get("dinov2")),
        "clip_similarity_score": _maybe_float(scores.get("clip")),
        "color_similarity_score": _maybe_float(scores.get("color")),
        "text_similarity_score": _maybe_float(scores.get("text")),
        "score_details": json.dumps(scores.get("details") or {}, ensure_ascii=False),
        "overlapping_classes": list(overlapping_classes or []),
        "severity": severity,
        "alert_type": "conflict",
        "source_type": source_type,
        "source_reference": source_reference,
    }

    cur.execute(
        """
        INSERT INTO design_alerts_mt
            (watchlist_item_id, user_id, organization_id,
             conflicting_design_id, conflicting_application_no, conflicting_registration_no,
             conflicting_product_name, conflicting_locarno_classes, conflicting_holder_name,
             conflicting_image_path, conflicting_bulletin_no, conflicting_bulletin_date,
             opposition_deadline,
             overall_similarity_score, dino_similarity_score, clip_similarity_score,
             color_similarity_score, text_similarity_score, score_details,
             overlapping_classes, severity, alert_type, source_type, source_reference)
        VALUES
            (%(watchlist_item_id)s, %(user_id)s, %(organization_id)s,
             %(conflicting_design_id)s, %(conflicting_application_no)s, %(conflicting_registration_no)s,
             %(conflicting_product_name)s, %(conflicting_locarno_classes)s, %(conflicting_holder_name)s,
             %(conflicting_image_path)s, %(conflicting_bulletin_no)s, %(conflicting_bulletin_date)s,
             %(opposition_deadline)s,
             %(overall_similarity_score)s, %(dino_similarity_score)s, %(clip_similarity_score)s,
             %(color_similarity_score)s, %(text_similarity_score)s, %(score_details)s::jsonb,
             %(overlapping_classes)s, %(severity)s, %(alert_type)s, %(source_type)s, %(source_reference)s)
        ON CONFLICT (watchlist_item_id, conflicting_design_id)
            WHERE conflicting_design_id IS NOT NULL DO NOTHING
        RETURNING id
        """,
        params,
    )
    row = cur.fetchone()
    if row is None:
        return None
    return str(row.get("id") if isinstance(row, dict) else row[0])

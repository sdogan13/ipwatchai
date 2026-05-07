"""Service helpers for alert routes."""

import json
from datetime import date as date_type
from uuid import UUID

from fastapi import HTTPException, status

from database.crud import AlertCRUD, Database
from models.schemas import (
    AlertAcknowledge,
    AlertDismiss,
    AlertResolve,
    AlertResponse,
    AlertScores,
    AlertStatus,
    AlertSeverity,
    ConflictingTrademark,
    PaginatedResponse,
)
from utils.deadline import active_similarity_alert_sql
from utils.watchlist_filters import same_holder_alert_exclusion_sql


def _visible_alert_condition(
    alert_alias: str = "a",
    conflict_alias: str = "t",
    watched_alias: str = "my_tm",
) -> str:
    return same_holder_alert_exclusion_sql(alert_alias, conflict_alias, watched_alias)


def _coerce_score_details(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _score_number(value, default=None):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_alert_scores(alert: dict) -> AlertScores:
    details = _coerce_score_details(alert.get("score_details"))

    text_similarity = _score_number(
        details.get("text_similarity"),
        _score_number(alert.get("text_similarity_score")),
    )
    semantic_similarity = _score_number(
        details.get("semantic_similarity"),
        _score_number(alert.get("semantic_similarity_score")),
    )
    visual_similarity = _score_number(
        details.get("visual_similarity"),
        _score_number(alert.get("visual_similarity_score")),
    )
    translation_similarity = _score_number(
        details.get("translation_similarity"),
        _score_number(alert.get("translation_similarity_score"), 0.0),
    )
    path_a_score = _score_number(details.get("path_a_score"), text_similarity or 0.0)
    path_b_score = _score_number(
        details.get("path_b_score"),
        translation_similarity or 0.0,
    )
    text_idf_score = _score_number(details.get("text_idf_score"))
    if text_idf_score is None:
        text_idf_score = max(path_a_score or 0.0, path_b_score or 0.0)

    return AlertScores(
        total=_score_number(alert.get("overall_risk_score"), 0.0),
        text_similarity=text_similarity,
        semantic_similarity=semantic_similarity,
        visual_similarity=visual_similarity,
        translation_similarity=translation_similarity,
        phonetic_match=alert.get("phonetic_match", False),
        text_idf_score=text_idf_score,
        path_a_score=path_a_score,
        path_b_score=path_b_score,
        scoring_path_source=details.get("scoring_path_source"),
        decision_reason=details.get("decision_reason"),
        textual_breakdown=details.get("textual_breakdown"),
        visual_breakdown=details.get("visual_breakdown"),
    )


def format_alert_response(alert: dict, deadline_classifier=None) -> AlertResponse:
    """Format an alert record into the API response model."""
    classifier = deadline_classifier
    if classifier is None:
        from utils.deadline import classify_deadline_status

        classifier = classify_deadline_status

    conflict_status = (
        alert.get("conflicting_status")
        or alert.get("conflict_live_status")
        or "Unknown"
    )
    conflict_classes = (
        alert.get("conflicting_classes")
        or alert.get("conflict_live_classes")
        or []
    )
    deadline_info = classifier(
        final_status=conflict_status,
        bulletin_date=alert.get("conflict_bulletin_date"),
        appeal_deadline=alert.get("conflict_appeal_deadline")
        or alert.get("opposition_deadline"),
    )

    return AlertResponse(
        id=UUID(str(alert["id"])),
        organization_id=UUID(str(alert["organization_id"])),
        watchlist_id=UUID(str(alert["watchlist_item_id"])),
        watched_brand_name=alert.get("watched_brand_name"),
        watchlist_bulletin_no=alert.get("watchlist_bulletin_no"),
        watchlist_application_no=alert.get("watchlist_application_no"),
        watchlist_classes=alert.get("watchlist_classes", []),
        conflicting=ConflictingTrademark(
            id=UUID(str(alert["conflicting_trademark_id"]))
            if alert.get("conflicting_trademark_id")
            else None,
            name=alert.get("conflicting_name", ""),
            application_no=alert.get("conflicting_application_no", ""),
            status=conflict_status,
            classes=conflict_classes,
            holder=alert.get("conflicting_holder_name"),
            image_path=alert.get("conflicting_image_path"),
            application_date=alert.get("conflict_application_date"),
            has_extracted_goods=bool(alert.get("conflict_has_extracted_goods", False)),
        ),
        conflict_bulletin_no=alert.get("conflict_bulletin_no"),
        overlapping_classes=alert.get("overlapping_classes", []),
        scores=_format_alert_scores(alert),
        severity=alert["severity"],
        status=alert["status"],
        source_type=alert.get("source_type"),
        source_reference=alert.get("source_bulletin"),
        source_date=None,
        appeal_deadline=alert.get("conflict_appeal_deadline"),
        conflict_bulletin_date=alert.get("conflict_bulletin_date"),
        deadline_status=deadline_info["status"],
        deadline_days_remaining=deadline_info["days_remaining"],
        deadline_label=deadline_info["label_tr"],
        deadline_urgency=deadline_info["urgency"],
        detected_at=alert["created_at"],
        seen_at=None,
        acknowledged_at=alert.get("acknowledged_at"),
        resolved_at=alert.get("resolved_at"),
        resolution_notes=alert.get("resolution_notes"),
    )


async def list_alerts_data(
    *,
    page: int,
    page_size: int,
    status_filters,
    severity_filters,
    watchlist_id,
    min_score: float,
    current_user,
    db_factory=Database,
    alert_crud=AlertCRUD,
    alert_formatter=format_alert_response,
):
    """Return paginated alerts for the current organization."""
    normalized_min_score = min_score / 100.0 if min_score > 1.0 else float(min_score)
    with db_factory() as db:
        status_values = [item.value for item in status_filters] if status_filters else None
        severity_values = [item.value for item in severity_filters] if severity_filters else None
        alerts, total = alert_crud.get_by_organization(
            db,
            current_user.organization_id,
            status=status_values,
            severity=severity_values,
            watchlist_id=watchlist_id,
            page=page,
            page_size=page_size,
            min_score=normalized_min_score,
        )

    return PaginatedResponse(
        items=[alert_formatter(alert) for alert in alerts],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


async def get_alerts_summary_data(
    *,
    current_user,
    db_factory=Database,
):
    """Return appealable alert summary counts by status and severity."""
    with db_factory() as db:
        cur = db.cursor()
        visible_alert_condition = _visible_alert_condition()
        cur.execute(
            f"""
            SELECT a.status, COUNT(*) as count
            FROM alerts_mt a
            LEFT JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            LEFT JOIN trademarks my_tm ON w.customer_application_no = my_tm.application_no
            WHERE a.organization_id = %s
              AND {active_similarity_alert_sql("a", "t")}
              AND {visible_alert_condition}
            GROUP BY a.status
        """,
            (str(current_user.organization_id),),
        )
        by_status = {row["status"]: row["count"] for row in cur.fetchall()}

        cur.execute(
            f"""
            SELECT a.severity, COUNT(*) as count
            FROM alerts_mt a
            LEFT JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            LEFT JOIN trademarks my_tm ON w.customer_application_no = my_tm.application_no
            WHERE a.organization_id = %s AND a.status = 'new'
              AND {active_similarity_alert_sql("a", "t")}
              AND {visible_alert_condition}
            GROUP BY a.severity
        """,
            (str(current_user.organization_id),),
        )
        by_severity = {row["severity"]: row["count"] for row in cur.fetchall()}

    return {
        "by_status": by_status,
        "by_severity": by_severity,
        "total_new": by_status.get("new", 0),
    }


async def aggregate_alerts_data(
    *,
    page: int,
    page_size: int,
    severity: str | None,
    current_user,
    db_factory=Database,
    today_getter=None,
):
    """Return a paginated aggregate alert feed across watchlist items."""
    get_today = today_getter or date_type.today
    with db_factory() as db:
        cur = db.cursor()
        org_id = str(current_user.organization_id)
        visible_alert_condition = _visible_alert_condition()

        where_extra = ""
        params = [org_id]
        if severity:
            where_extra = " AND a.severity = %s"
            params.append(severity)

        cur.execute(
            f"""
            SELECT COUNT(*) FROM alerts_mt a
            JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            LEFT JOIN trademarks my_tm ON w.customer_application_no = my_tm.application_no
            WHERE a.organization_id = %s
                AND a.status NOT IN ('dismissed', 'resolved')
                AND {active_similarity_alert_sql("a", "t")}
                AND {visible_alert_condition}
        """
            + where_extra,
            params,
        )
        total_row = cur.fetchone()
        total = total_row.get("count") if isinstance(total_row, dict) else total_row[0]

        offset = (page - 1) * page_size
        cur.execute(
            f"""
            SELECT a.*, w.brand_name AS watched_brand_name,
                   a.opposition_deadline, a.conflicting_name,
                   t.name AS tm_name, t.final_status, t.bulletin_date
            FROM alerts_mt a
            JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            LEFT JOIN trademarks my_tm ON w.customer_application_no = my_tm.application_no
            WHERE a.organization_id = %s
                AND a.status NOT IN ('dismissed', 'resolved')
                AND {active_similarity_alert_sql("a", "t")}
                AND {visible_alert_condition}
        """
            + where_extra
            + """
            ORDER BY
                CASE WHEN a.opposition_deadline IS NOT NULL AND a.opposition_deadline > CURRENT_DATE
                     THEN 0 ELSE 1 END,
                a.opposition_deadline ASC NULLS LAST,
                a.created_at DESC
            LIMIT %s OFFSET %s
        """,
            params + [page_size, offset],
        )
        rows = cur.fetchall()

    today = get_today()
    items = []
    for row in rows:
        deadline = row.get("opposition_deadline")
        deadline_days = None
        if deadline and deadline > today:
            deadline_days = (deadline - today).days
        items.append(
            {
                "id": str(row["id"]),
                "watchlist_item_id": str(row["watchlist_item_id"]),
                "watched_brand_name": row.get("watched_brand_name"),
                "conflicting_brand_name": row.get("conflicting_name") or row.get("tm_name"),
                "conflicting_trademark_id": (
                    str(row["conflicting_trademark_id"])
                    if row.get("conflicting_trademark_id")
                    else None
                ),
                "severity": row.get("severity"),
                "risk_score": row.get("overall_risk_score"),
                "status": row.get("status"),
                "opposition_deadline": deadline.isoformat() if deadline else None,
                "deadline_days": deadline_days,
                "overlapping_classes": row.get("overlapping_classes"),
                "created_at": (
                    row["created_at"].isoformat() if row.get("created_at") else None
                ),
            }
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


async def get_alert_data(
    *,
    alert_id,
    current_user,
    db_factory=Database,
    alert_crud=AlertCRUD,
    alert_formatter=format_alert_response,
):
    """Return a single alert and mark new alerts as seen."""
    with db_factory() as db:
        alert = alert_crud.get_by_id(db, alert_id, current_user.organization_id)
        if not alert:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Alert not found",
            )
        if alert["status"] == "new":
            alert_crud.update_status(
                db,
                alert_id,
                current_user.organization_id,
                AlertStatus.SEEN,
            )
            alert["status"] = "seen"

    return alert_formatter(alert)


async def acknowledge_alert_data(
    *,
    alert_id,
    data: AlertAcknowledge,
    current_user,
    db_factory=Database,
    alert_crud=AlertCRUD,
    alert_formatter=format_alert_response,
):
    """Acknowledge an alert."""
    return await _update_alert_status_data(
        alert_id=alert_id,
        next_status=AlertStatus.ACKNOWLEDGED,
        notes=data.notes,
        current_user=current_user,
        db_factory=db_factory,
        alert_crud=alert_crud,
        alert_formatter=alert_formatter,
    )


async def resolve_alert_data(
    *,
    alert_id,
    data: AlertResolve,
    current_user,
    db_factory=Database,
    alert_crud=AlertCRUD,
    alert_formatter=format_alert_response,
):
    """Resolve an alert."""
    return await _update_alert_status_data(
        alert_id=alert_id,
        next_status=AlertStatus.RESOLVED,
        notes=data.resolution_notes,
        current_user=current_user,
        db_factory=db_factory,
        alert_crud=alert_crud,
        alert_formatter=alert_formatter,
    )


async def dismiss_alert_data(
    *,
    alert_id,
    data: AlertDismiss,
    current_user,
    db_factory=Database,
    alert_crud=AlertCRUD,
    alert_formatter=format_alert_response,
):
    """Dismiss an alert as a false positive."""
    return await _update_alert_status_data(
        alert_id=alert_id,
        next_status=AlertStatus.DISMISSED,
        notes=data.reason,
        current_user=current_user,
        db_factory=db_factory,
        alert_crud=alert_crud,
        alert_formatter=alert_formatter,
    )


async def _update_alert_status_data(
    *,
    alert_id,
    next_status,
    notes,
    current_user,
    db_factory=Database,
    alert_crud=AlertCRUD,
    alert_formatter=format_alert_response,
):
    with db_factory() as db:
        alert = alert_crud.update_status(
            db,
            alert_id,
            current_user.organization_id,
            next_status,
            user_id=current_user.id,
            notes=notes,
        )
        if not alert:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Alert not found",
            )

    return alert_formatter(alert)

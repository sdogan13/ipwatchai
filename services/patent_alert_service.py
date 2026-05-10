"""Patent / Faydalı Model alert service.

Sister to ``services/design_alert_service.py``. Read + lifecycle
operations on ``patent_alerts_mt``:

  * list (paginated, filterable by status / severity / min_score / watchlist)
  * summary (counts by status + severity)
  * get one (transitions status='new' → 'seen' on first read)
  * acknowledge / resolve / dismiss with optional notes

All paths scope by ``organization_id``; alerts belonging to other tenants
are 404 from this user's perspective.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse

from database.crud import Database


MAX_EXPORT_ALERTS = 5000


logger = logging.getLogger("turkpatent.patent_alerts")

ALLOWED_STATUSES = ("new", "seen", "acknowledged", "resolved", "dismissed")
ALLOWED_SEVERITIES = ("low", "medium", "high", "critical")


def _row_to_dict(row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# List + summary
# ---------------------------------------------------------------------------

def list_patent_alerts(
    *,
    current_user,
    page: int = 1,
    page_size: int = 20,
    status_filters: Optional[Sequence[str]] = None,
    severity_filters: Optional[Sequence[str]] = None,
    watchlist_item_id: Optional[UUID] = None,
    min_score: float = 0.0,
    db_factory=Database,
) -> Dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    offset = (page - 1) * page_size

    where = ["a.organization_id = %(org)s"]
    params: Dict[str, Any] = {"org": str(current_user.organization_id)}

    if status_filters:
        valid = [s for s in status_filters if s in ALLOWED_STATUSES]
        if valid:
            where.append("a.status = ANY(%(statuses)s::text[])")
            params["statuses"] = valid
    if severity_filters:
        valid = [s for s in severity_filters if s in ALLOWED_SEVERITIES]
        if valid:
            where.append("a.severity = ANY(%(severities)s::text[])")
            params["severities"] = valid
    if watchlist_item_id:
        where.append("a.watchlist_item_id = %(wl)s")
        params["wl"] = str(watchlist_item_id)
    if min_score and min_score > 0:
        # API exposes 0..100 percent; column stores 0..1
        where.append("a.overall_similarity_score >= %(min_score)s")
        params["min_score"] = float(min_score) / 100.0

    where_sql = " AND ".join(where)
    params["limit"] = page_size
    params["offset"] = offset

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT a.*, w.label AS watchlist_label, w.watch_type AS watchlist_watch_type
            FROM patent_alerts_mt a
            LEFT JOIN patent_watchlist_mt w ON w.id = a.watchlist_item_id
            WHERE {where_sql}
            ORDER BY a.created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        rows = cur.fetchall()
        cur.execute(
            f"SELECT COUNT(*) AS total FROM patent_alerts_mt a WHERE {where_sql}",
            params,
        )
        total_row = cur.fetchone()

    total = int(total_row.get("total") if isinstance(total_row, dict) else total_row[0])
    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_patent_alerts_summary(*, current_user, db_factory=Database) -> Dict[str, Any]:
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT status, severity, COUNT(*) AS n
            FROM patent_alerts_mt
            WHERE organization_id = %s
            GROUP BY status, severity
            """,
            (str(current_user.organization_id),),
        )
        rows = cur.fetchall()

    by_status: Dict[str, int] = {s: 0 for s in ALLOWED_STATUSES}
    by_severity: Dict[str, int] = {s: 0 for s in ALLOWED_SEVERITIES}
    total = 0
    for row in rows:
        s = row.get("status") if isinstance(row, dict) else row[0]
        sev = row.get("severity") if isinstance(row, dict) else row[1]
        n = int(row.get("n") if isinstance(row, dict) else row[2])
        if s in by_status:
            by_status[s] += n
        if sev in by_severity:
            by_severity[sev] += n
        total += n
    return {"total": total, "by_status": by_status, "by_severity": by_severity}


# ---------------------------------------------------------------------------
# Get one (with status transition)
# ---------------------------------------------------------------------------

def get_patent_alert(*, alert_id: UUID, current_user, db_factory=Database) -> Dict[str, Any]:
    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT a.*, w.label AS watchlist_label, w.watch_type AS watchlist_watch_type
            FROM patent_alerts_mt a
            LEFT JOIN patent_watchlist_mt w ON w.id = a.watchlist_item_id
            WHERE a.id = %s AND a.organization_id = %s
            """,
            (str(alert_id), str(current_user.organization_id)),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Alert not found")
        # 'new' → 'seen' on first detail read
        if (row.get("status") if isinstance(row, dict) else None) == "new":
            cur.execute(
                "UPDATE patent_alerts_mt SET status = 'seen', updated_at = NOW() WHERE id = %s",
                (str(alert_id),),
            )
            db.commit()
            row = dict(row)
            row["status"] = "seen"
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Lifecycle transitions (acknowledge / resolve / dismiss)
# ---------------------------------------------------------------------------

def _transition(
    *,
    alert_id: UUID,
    new_status: str,
    notes: Optional[str],
    current_user,
    db_factory=Database,
    set_acknowledged: bool = False,
    set_resolved: bool = False,
) -> Dict[str, Any]:
    if new_status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")
    sets = ["status = %(status)s", "updated_at = NOW()"]
    params: Dict[str, Any] = {
        "status": new_status,
        "id": str(alert_id),
        "org": str(current_user.organization_id),
    }
    if notes is not None:
        sets.append("resolution_notes = %(notes)s")
        params["notes"] = notes
    if set_acknowledged:
        sets.append("acknowledged_at = NOW()")
        sets.append("acknowledged_by = %(user)s")
        params["user"] = str(current_user.id)
    if set_resolved:
        sets.append("resolved_at = NOW()")
        sets.append("resolved_by = %(user)s")
        params["user"] = str(current_user.id)

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            UPDATE patent_alerts_mt
            SET {", ".join(sets)}
            WHERE id = %(id)s AND organization_id = %(org)s
            RETURNING *
            """,
            params,
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Alert not found")
        db.commit()
    return _row_to_dict(row)


CSV_ALERT_HEADERS_TR = [
    "Uyarı ID", "Tarih", "Önem", "Durum", "Eşleşme Tipi", "Skor",
    "Takip Etiketi", "Takip Türü",
    "Başvuru No", "Yayın No", "Tür Kodu", "Başlık",
    "Hak Sahibi", "Ülke", "IPC", "Bülten No", "Bülten Tarihi",
    "Onay Tarihi", "Çözüm Tarihi", "Notlar",
]


def export_patent_alerts_csv(
    *,
    current_user,
    status_filters: Optional[Sequence[str]] = None,
    severity_filters: Optional[Sequence[str]] = None,
    watchlist_item_id: Optional[UUID] = None,
    min_score: float = 0.0,
    db_factory=Database,
) -> StreamingResponse:
    """Export patent alerts as CSV. Same filter args as list_patent_alerts.
    Org-scoped at the SQL layer; capped at MAX_EXPORT_ALERTS rows.

    No plan gate — alerts are user-owned (their watchlist generated
    them) so any authenticated user with watchlist access can export
    their own alerts. The watchlist itself is plan-gated upstream.
    """
    where = ["a.organization_id = %(org)s"]
    params: Dict[str, Any] = {"org": str(current_user.organization_id)}

    if status_filters:
        valid = [s for s in status_filters if s in ALLOWED_STATUSES]
        if valid:
            where.append("a.status = ANY(%(statuses)s::text[])")
            params["statuses"] = valid
    if severity_filters:
        valid = [s for s in severity_filters if s in ALLOWED_SEVERITIES]
        if valid:
            where.append("a.severity = ANY(%(severities)s::text[])")
            params["severities"] = valid
    if watchlist_item_id:
        where.append("a.watchlist_item_id = %(wl)s")
        params["wl"] = str(watchlist_item_id)
    if min_score and min_score > 0:
        where.append("a.overall_similarity_score >= %(min_score)s")
        params["min_score"] = float(min_score) / 100.0

    where_sql = " AND ".join(where)
    params["limit"] = MAX_EXPORT_ALERTS

    with db_factory() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT a.id::text, a.created_at, a.severity, a.status, a.match_type,
                   a.overall_similarity_score,
                   w.label AS watchlist_label, w.watch_type AS watchlist_watch_type,
                   a.conflicting_application_no, a.conflicting_publication_no,
                   a.conflicting_kind_code, a.conflicting_title,
                   a.conflicting_holder_name, a.conflicting_holder_country,
                   a.conflicting_ipc_classes,
                   a.conflicting_bulletin_no, a.conflicting_bulletin_date,
                   a.acknowledged_at, a.resolved_at, a.resolution_notes
            FROM patent_alerts_mt a
            LEFT JOIN patent_watchlist_mt w ON w.id = a.watchlist_item_id
            WHERE {where_sql}
            ORDER BY a.created_at DESC
            LIMIT %(limit)s
            """,
            params,
        )
        rows = cur.fetchall()

    # UTF-8 BOM so Excel opens TR characters correctly
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output)
    writer.writerow(CSV_ALERT_HEADERS_TR)

    def _iso(d):
        return d.isoformat() if d else ""

    for r in rows:
        rd = dict(r)
        score = rd.get("overall_similarity_score") or 0
        try:
            score_pct = f"{float(score) * 100:.1f}%"
        except (TypeError, ValueError):
            score_pct = ""
        writer.writerow([
            rd.get("id") or "",
            _iso(rd.get("created_at")),
            rd.get("severity") or "",
            rd.get("status") or "",
            rd.get("match_type") or "",
            score_pct,
            rd.get("watchlist_label") or "",
            rd.get("watchlist_watch_type") or "",
            rd.get("conflicting_application_no") or "",
            rd.get("conflicting_publication_no") or "",
            rd.get("conflicting_kind_code") or "",
            (rd.get("conflicting_title") or "").replace("\n", " ").replace("\r", " "),
            rd.get("conflicting_holder_name") or "",
            rd.get("conflicting_holder_country") or "",
            ", ".join(rd.get("conflicting_ipc_classes") or []),
            rd.get("conflicting_bulletin_no") or "",
            _iso(rd.get("conflicting_bulletin_date")),
            _iso(rd.get("acknowledged_at")),
            _iso(rd.get("resolved_at")),
            (rd.get("resolution_notes") or "").replace("\n", " "),
        ])

    output.seek(0)
    filename = f"patent_alerts_{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def acknowledge_patent_alert(*, alert_id, notes, current_user, db_factory=Database):
    return _transition(alert_id=alert_id, new_status="acknowledged", notes=notes,
                       current_user=current_user, db_factory=db_factory,
                       set_acknowledged=True)


def resolve_patent_alert(*, alert_id, notes, current_user, db_factory=Database):
    return _transition(alert_id=alert_id, new_status="resolved", notes=notes,
                       current_user=current_user, db_factory=db_factory,
                       set_acknowledged=True, set_resolved=True)


def dismiss_patent_alert(*, alert_id, notes, current_user, db_factory=Database):
    return _transition(alert_id=alert_id, new_status="dismissed", notes=notes,
                       current_user=current_user, db_factory=db_factory,
                       set_acknowledged=True)

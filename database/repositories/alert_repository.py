"""Alert repository operations."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from models.schemas import AlertStatus

if TYPE_CHECKING:
    from database.crud import Database


class AlertCRUD:
    @staticmethod
    def create(
        db: Database,
        org_id: UUID,
        watchlist_id: UUID,
        conflicting_trademark: Dict,
        scores: Dict,
        source_info: Dict,
        user_id: UUID = None,
        overlapping_classes: List[int] = None,
    ) -> Dict:
        """Create new alert."""
        cur = db.cursor()

        if not user_id:
            cur.execute("SELECT user_id FROM watchlist_mt WHERE id = %s", (str(watchlist_id),))
            row = cur.fetchone()
            if row:
                user_id = UUID(row["user_id"])

        from risk_engine import get_risk_level

        similarity_score = scores.get("total", 0)
        severity = get_risk_level(similarity_score)
        if severity == "very_high":
            severity = "high"

        opposition_deadline = None
        conflict_id = conflicting_trademark.get("id")
        if conflict_id:
            cur.execute(
                "SELECT appeal_deadline FROM trademarks WHERE id = %s::uuid",
                (str(conflict_id),),
            )
            row = cur.fetchone()
            if row and row.get("appeal_deadline"):
                opposition_deadline = row["appeal_deadline"]

        alert_id = uuid4()

        cur.execute(
            """
            INSERT INTO alerts_mt (
                id, user_id, organization_id, watchlist_item_id, conflicting_trademark_id,
                conflicting_name, conflicting_application_no,
                conflicting_classes, conflicting_holder_name, conflicting_image_path,
                overall_risk_score, text_similarity_score, semantic_similarity_score,
                visual_similarity_score, translation_similarity_score,
                phonetic_match, severity, source_type, alert_type, status,
                overlapping_classes, opposition_deadline
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """,
            (
                str(alert_id),
                str(user_id) if user_id else None,
                str(org_id),
                str(watchlist_id),
                str(conflict_id) if conflict_id else None,
                conflicting_trademark.get("name"),
                conflicting_trademark.get("application_no"),
                conflicting_trademark.get("classes", []),
                conflicting_trademark.get("holder"),
                conflicting_trademark.get("image_path"),
                similarity_score,
                scores.get("text_similarity"),
                scores.get("semantic_similarity"),
                scores.get("visual_similarity"),
                scores.get("translation_similarity", 0),
                scores.get("phonetic_match", False),
                severity,
                source_info.get("type"),
                "similarity",
                "new",
                overlapping_classes or [],
                opposition_deadline,
            ),
        )

        db.commit()
        return dict(cur.fetchone())

    @staticmethod
    def get_by_id(db: Database, alert_id: UUID, org_id: UUID) -> Optional[Dict]:
        """Get alert by ID, scoped to organization (tenant isolation)."""
        cur = db.cursor()
        cur.execute(
            """
            SELECT a.*,
                   t.appeal_deadline as conflict_appeal_deadline,
                   t.bulletin_date as conflict_bulletin_date,
                   t.bulletin_no as conflict_bulletin_no,
                   t.final_status as conflict_live_status,
                   t.nice_class_numbers as conflict_live_classes,
                   t.application_date as conflict_application_date
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.id = %s AND a.organization_id = %s
        """,
            (str(alert_id), str(org_id)),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_by_organization(
        db: Database,
        org_id: UUID,
        status: Optional[List[str]] = None,
        severity: Optional[List[str]] = None,
        watchlist_id: Optional[UUID] = None,
        page: int = 1,
        page_size: int = 20,
        min_score: float = 0.0,
    ) -> Tuple[List[Dict], int]:
        """Get alerts for organization with filtering."""
        cur = db.cursor()
        min_score = min_score / 100.0 if min_score > 1.0 else float(min_score)

        conditions = ["a.organization_id = %s"]
        params = [str(org_id)]

        if status:
            conditions.append("a.status = ANY(%s)")
            params.append(status)

        if severity:
            conditions.append("a.severity = ANY(%s)")
            params.append(severity)

        if watchlist_id:
            conditions.append("a.watchlist_item_id = %s")
            params.append(str(watchlist_id))

        if min_score > 0.0:
            conditions.append("a.overall_risk_score >= %s")
            params.append(min_score)

        where_clause = " AND ".join(conditions)

        cur.execute(
            f"""
            SELECT COUNT(*) FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE {where_clause}
            AND (t.appeal_deadline IS NULL OR t.appeal_deadline >= CURRENT_DATE)
        """,
            params,
        )
        total = cur.fetchone()["count"]

        offset = (page - 1) * page_size

        cur.execute(
            f"""
            SELECT a.*,
                   w.brand_name as watched_brand_name,
                   w.customer_bulletin_no as watchlist_bulletin_no,
                   w.customer_application_no as watchlist_application_no,
                   w.nice_class_numbers as watchlist_classes,
                   t.bulletin_no as conflict_bulletin_no,
                   t.final_status as conflict_live_status,
                   t.nice_class_numbers as conflict_live_classes,
                   t.appeal_deadline as conflict_appeal_deadline,
                   t.bulletin_date as conflict_bulletin_date,
                   t.application_date as conflict_application_date,
                   (t.extracted_goods IS NOT NULL
                       AND t.extracted_goods != '[]'::jsonb
                       AND t.extracted_goods != 'null'::jsonb) AS conflict_has_extracted_goods
            FROM alerts_mt a
            LEFT JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
            AND (t.appeal_deadline IS NULL OR t.appeal_deadline >= CURRENT_DATE)
            {"AND a.status = ANY(%s)" if status else ""}
            {"AND a.severity = ANY(%s)" if severity else ""}
            {"AND a.watchlist_item_id = %s" if watchlist_id else ""}
            {"AND a.overall_risk_score >= %s" if min_score > 0.0 else ""}
            ORDER BY a.overall_risk_score DESC, a.created_at DESC
            LIMIT %s OFFSET %s
        """,
            [p for p in [str(org_id)]
             + ([status] if status else [])
             + ([severity] if severity else [])
             + ([str(watchlist_id)] if watchlist_id else [])
             + ([min_score] if min_score > 0.0 else [])
             + [page_size, offset]],
        )

        return [dict(row) for row in cur.fetchall()], total

    @staticmethod
    def update_status(
        db: Database,
        alert_id: UUID,
        org_id: UUID,
        status: AlertStatus,
        user_id: Optional[UUID] = None,
        notes: Optional[str] = None,
    ) -> Optional[Dict]:
        """Update alert status."""
        cur = db.cursor()

        updates = ["status = %s"]
        values = [status.value]

        now = datetime.utcnow()

        if status == AlertStatus.SEEN:
            updates.append("seen_at = %s")
            values.append(now)
        elif status == AlertStatus.ACKNOWLEDGED:
            updates.append("acknowledged_at = %s")
            updates.append("acknowledged_by = %s")
            values.extend([now, str(user_id) if user_id else None])
        elif status in [AlertStatus.RESOLVED, AlertStatus.DISMISSED]:
            updates.append("resolved_at = %s")
            updates.append("resolved_by = %s")
            if notes:
                updates.append("resolution_notes = %s")
                values.extend([now, str(user_id) if user_id else None, notes])
            else:
                values.extend([now, str(user_id) if user_id else None])

        values.extend([str(alert_id), str(org_id)])

        cur.execute(
            f"""
            UPDATE alerts_mt SET {', '.join(updates)}
            WHERE id = %s AND organization_id = %s
            RETURNING *
        """,
            values,
        )

        db.commit()
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def mark_notified(db: Database, alert_id: UUID, channel: str):
        """Mark alert as notified via channel."""
        cur = db.cursor()

        if channel == "email":
            cur.execute(
                """
                UPDATE alerts_mt SET email_sent = TRUE, email_sent_at = NOW()
                WHERE id = %s
            """,
                (str(alert_id),),
            )
        elif channel == "webhook":
            cur.execute(
                """
                UPDATE alerts_mt SET webhook_sent = TRUE, webhook_sent_at = NOW()
                WHERE id = %s
            """,
                (str(alert_id),),
            )

        db.commit()

    @staticmethod
    def get_pending_notifications(db: Database, channel: str, frequency: str) -> List[Dict]:
        """Get alerts pending notification."""
        cur = db.cursor()

        if channel == "email":
            cur.execute(
                """
                SELECT a.*, w.brand_name, w.notify_email, w.notification_frequency,
                       u.email as user_email, u.first_name
                FROM alerts_mt a
                JOIN watchlist_mt w ON a.watchlist_item_id = w.id
                JOIN users u ON w.user_id = u.id
                WHERE a.email_sent = FALSE
                  AND w.notify_email = TRUE
                  AND w.notification_frequency = %s
                  AND a.status = 'new'
                ORDER BY a.organization_id, a.created_at
            """,
                (frequency,),
            )

        return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def check_duplicate(
        db: Database,
        watchlist_id: UUID,
        conflicting_app_no: str,
    ) -> bool:
        """Check if alert already exists for this combination."""
        cur = db.cursor()
        cur.execute(
            """
            SELECT id FROM alerts_mt
            WHERE watchlist_item_id = %s AND conflicting_application_no = %s
            AND status NOT IN ('resolved', 'dismissed')
        """,
            (str(watchlist_id), conflicting_app_no),
        )
        return cur.fetchone() is not None

    @staticmethod
    def resolve_below_threshold(db: Database, watchlist_id: UUID, threshold: float) -> int:
        """
        Resolve all active alerts for a watchlist item whose score is below the
        current threshold. Called during every scan so that raising the threshold
        immediately removes alerts that no longer qualify.

        Returns the number of alerts resolved.
        """
        cur = db.cursor()
        cur.execute(
            """
            UPDATE alerts_mt
            SET status = 'resolved',
                resolved_at = NOW(),
                resolution_notes = 'Auto-resolved: score below current alert threshold'
            WHERE watchlist_item_id = %s
              AND overall_risk_score < %s
              AND status NOT IN ('resolved', 'dismissed')
        """,
            (str(watchlist_id), threshold),
        )
        count = cur.rowcount
        db.commit()
        return count

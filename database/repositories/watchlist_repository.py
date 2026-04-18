"""Watchlist repository operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from models.schemas import WatchlistItemCreate, WatchlistItemUpdate

from database.repositories.organization_repository import OrganizationCRUD

if TYPE_CHECKING:
    from database.crud import Database


logger = logging.getLogger(__name__)


class WatchlistCRUD:
    @staticmethod
    def create(db: Database, org_id: UUID, user_id: UUID, data: WatchlistItemCreate) -> Dict:
        """Create watchlist item."""
        cur = db.cursor()

        within_limit, _, _ = OrganizationCRUD.check_limits(db, org_id, "watchlist", user_id=user_id)
        if not within_limit:
            raise ValueError("Organization has reached maximum watchlist items limit")

        item_id = uuid4()
        alert_freq = getattr(data, "alert_frequency", None)
        if alert_freq:
            alert_freq = alert_freq.value if hasattr(alert_freq, "value") else alert_freq
        else:
            alert_freq = "daily"

        app_no = getattr(data, "application_no", None)
        bulletin_no = getattr(data, "bulletin_no", None)

        logger.info(
            "Creating watchlist item: brand=%s, app_no=%s, bulletin_no=%s",
            data.brand_name,
            app_no,
            bulletin_no,
        )

        cur.execute(
            """
            INSERT INTO watchlist_mt (
                id, organization_id, user_id, brand_name, nice_class_numbers, description,
                alert_threshold, monitor_similar_names, monitor_similar_logos, monitor_phonetic,
                alert_frequency, customer_application_no, customer_bulletin_no
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """,
            (
                str(item_id),
                str(org_id),
                str(user_id),
                data.brand_name,
                data.nice_class_numbers,
                data.description,
                data.similarity_threshold,
                data.monitor_text,
                data.monitor_visual,
                data.monitor_phonetic,
                alert_freq,
                app_no,
                bulletin_no,
            ),
        )

        db.commit()
        return dict(cur.fetchone())

    @staticmethod
    def create_with_embeddings(
        db: Database,
        org_id: UUID,
        user_id: UUID,
        data: WatchlistItemCreate,
        logo_path: Optional[str] = None,
        logo_embedding: Optional[List[float]] = None,
        logo_dinov2_embedding: Optional[List[float]] = None,
        logo_color_histogram: Optional[List[float]] = None,
        logo_ocr_text: Optional[str] = None,
        text_embedding: Optional[List[float]] = None,
        auto_commit: bool = True,
    ) -> Dict:
        """Create watchlist item with pre-computed embeddings (from trademark data)."""
        cur = db.cursor()

        within_limit, _, _ = OrganizationCRUD.check_limits(db, org_id, "watchlist", user_id=user_id)
        if not within_limit:
            raise ValueError("Organization has reached maximum watchlist items limit")

        item_id = uuid4()
        alert_freq = getattr(data, "alert_frequency", None)
        if alert_freq:
            alert_freq = alert_freq.value if hasattr(alert_freq, "value") else alert_freq
        else:
            alert_freq = "daily"

        app_no = getattr(data, "application_no", None)
        bulletin_no = getattr(data, "bulletin_no", None)

        monitor_logos = True if logo_embedding is not None else bool(data.monitor_visual)

        cols = [
            "id",
            "organization_id",
            "user_id",
            "brand_name",
            "nice_class_numbers",
            "description",
            "alert_threshold",
            "monitor_similar_names",
            "monitor_similar_logos",
            "monitor_phonetic",
            "alert_frequency",
            "customer_application_no",
            "customer_bulletin_no",
        ]
        vals = [
            str(item_id),
            str(org_id),
            str(user_id),
            data.brand_name,
            data.nice_class_numbers,
            data.description,
            data.similarity_threshold,
            data.monitor_text,
            monitor_logos,
            data.monitor_phonetic,
            alert_freq,
            app_no,
            bulletin_no,
        ]
        placeholders = ["%s"] * len(cols)

        if logo_path:
            cols.append("logo_path")
            vals.append(logo_path)
            placeholders.append("%s")
        if logo_embedding is not None:
            cols.append("logo_embedding")
            vals.append(str(logo_embedding))
            placeholders.append("%s::halfvec")
        if logo_dinov2_embedding is not None:
            cols.append("logo_dinov2_embedding")
            vals.append(str(logo_dinov2_embedding))
            placeholders.append("%s::halfvec")
        if logo_color_histogram is not None:
            cols.append("logo_color_histogram")
            vals.append(str(logo_color_histogram))
            placeholders.append("%s::halfvec")
        if logo_ocr_text is not None:
            cols.append("logo_ocr_text")
            vals.append(logo_ocr_text)
            placeholders.append("%s")
        if text_embedding is not None:
            cols.append("text_embedding")
            vals.append(str(text_embedding))
            placeholders.append("%s::halfvec")

        sql = f"INSERT INTO watchlist_mt ({', '.join(cols)}) VALUES ({', '.join(placeholders)}) RETURNING *"
        cur.execute(sql, vals)
        if auto_commit:
            db.commit()
        return dict(cur.fetchone())

    @staticmethod
    def get_by_id(db: Database, item_id: UUID, org_id: UUID) -> Optional[Dict]:
        """Get watchlist item by ID, scoped to organization (tenant isolation)."""
        cur = db.cursor()
        cur.execute(
            "SELECT * FROM watchlist_mt WHERE id = %s AND organization_id = %s",
            (str(item_id), str(org_id)),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_by_id_internal(db: Database, item_id: UUID) -> Optional[Dict]:
        """Get watchlist item by ID without tenant filter (trusted backend use only)."""
        cur = db.cursor()
        cur.execute(
            "SELECT * FROM watchlist_mt WHERE id = %s",
            (str(item_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_by_organization(
        db: Database,
        org_id: UUID,
        active_only: bool = True,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        sort_by: Optional[str] = None,
        renewal_only: bool = False,
        appeals_only: bool = False,
        status_filter: Optional[str] = None,
        threshold: float = 0.70,
        tm_status: Optional[str] = None,
    ) -> Tuple[List[Dict], int]:
        """Get watchlist items for organization with pagination, search, and sort."""
        cur = db.cursor()
        threshold = threshold / 100.0 if threshold > 1.0 else float(threshold)

        where_parts = ["w.organization_id = %s"]
        params = [str(org_id)]
        if active_only:
            where_parts.append("w.is_active = TRUE")
        if search:
            where_parts.append("w.brand_name ILIKE %s ESCAPE '\\'")
            safe_search = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{safe_search}%")
        if renewal_only:
            where_parts.append(
                """(
                (my_tm.application_date IS NOT NULL
                 AND my_tm.application_date + INTERVAL '10 years 6 months' <= CURRENT_DATE + INTERVAL '12 months')
                OR
                (my_tm.application_date IS NULL
                 AND w.customer_application_no IS NOT NULL
                 AND left(w.customer_application_no, 4) ~ '^[0-9]{4}$'
                 AND EXTRACT(YEAR FROM CURRENT_DATE)::int - left(w.customer_application_no, 4)::int >= 9)
            )"""
            )
        if appeals_only:
            if status_filter in ("new", "acknowledged"):
                where_parts.append(
                    """EXISTS (
                    SELECT 1 FROM alerts_mt a2
                    JOIN trademarks t2 ON a2.conflicting_trademark_id = t2.id
                    WHERE a2.watchlist_item_id = w.id
                      AND a2.status = %s
                      AND t2.appeal_deadline IS NOT NULL
                      AND t2.appeal_deadline >= CURRENT_DATE
                )"""
                )
                params.append(status_filter)
            elif status_filter in ("resolved", "dismissed"):
                where_parts.append(
                    """EXISTS (
                    SELECT 1 FROM alerts_mt a2
                    WHERE a2.watchlist_item_id = w.id
                      AND a2.status = %s
                )"""
                )
                params.append(status_filter)
            else:
                where_parts.append(
                    """EXISTS (
                    SELECT 1 FROM alerts_mt a2
                    JOIN trademarks t2 ON a2.conflicting_trademark_id = t2.id
                    WHERE a2.watchlist_item_id = w.id
                      AND a2.status NOT IN ('dismissed', 'resolved')
                      AND t2.appeal_deadline IS NOT NULL
                      AND t2.appeal_deadline >= CURRENT_DATE
                )"""
                )
        if renewal_only and status_filter:
            if status_filter == "overdue":
                where_parts.append(
                    """(
                    (my_tm.application_date IS NOT NULL
                     AND my_tm.application_date + INTERVAL '10 years 6 months' < CURRENT_DATE)
                    OR
                    (my_tm.application_date IS NULL
                     AND w.customer_application_no IS NOT NULL
                     AND left(w.customer_application_no, 4) ~ '^[0-9]{4}$'
                     AND EXTRACT(YEAR FROM CURRENT_DATE)::int - left(w.customer_application_no, 4)::int >= 11)
                )"""
                )
            elif status_filter == "critical":
                where_parts.append(
                    """(
                    my_tm.application_date IS NOT NULL
                    AND my_tm.application_date + INTERVAL '10 years' <= CURRENT_DATE
                    AND my_tm.application_date + INTERVAL '10 years 6 months' > CURRENT_DATE
                )"""
                )
            elif status_filter == "approaching":
                where_parts.append(
                    """(
                    my_tm.application_date IS NOT NULL
                    AND my_tm.application_date + INTERVAL '10 years' > CURRENT_DATE
                    AND my_tm.application_date + INTERVAL '10 years' <= CURRENT_DATE + INTERVAL '12 months'
                )"""
                )

        if tm_status:
            where_parts.append("my_tm.final_status = %s")
            params.append(tm_status)

        where_clause = " AND ".join(where_parts)

        needs_tm_join = renewal_only or bool(tm_status)
        if needs_tm_join:
            cur.execute(
                f"""
                SELECT COUNT(DISTINCT w.id)
                FROM watchlist_mt w
                LEFT JOIN trademarks my_tm ON w.customer_application_no = my_tm.application_no
                WHERE {where_clause}
            """,
                params,
            )
        else:
            cur.execute(f"SELECT COUNT(*) FROM watchlist_mt w WHERE {where_clause}", params)
        total = cur.fetchone()["count"]

        sort_map = {
            "date_desc": "MAX(my_tm.application_date) DESC NULLS LAST",
            "date_asc": "MAX(my_tm.application_date) ASC NULLS LAST",
            "name_asc": "w.brand_name ASC NULLS LAST",
        }
        if sort_by == "conflicts_desc":
            order_by = (
                "COUNT(a.id) FILTER ("
                "  WHERE a.status NOT IN ('dismissed', 'resolved')"
                "  AND (t.appeal_deadline IS NULL OR t.appeal_deadline >= CURRENT_DATE)"
                f"  AND a.overall_risk_score >= {float(threshold)}"
                ") DESC NULLS LAST, w.created_at DESC"
            )
        elif renewal_only and sort_by not in sort_map:
            order_by = "MAX(my_tm.application_date) ASC NULLS LAST"
        elif appeals_only and sort_by not in sort_map:
            if status_filter in ("resolved", "dismissed"):
                order_by = """(
                    SELECT MAX(a2.updated_at)
                    FROM alerts_mt a2
                    WHERE a2.watchlist_item_id = w.id
                      AND a2.status = '{sf}'
                ) DESC NULLS LAST""".format(sf=status_filter)
            else:
                order_by = """(
                    SELECT MIN(t2.appeal_deadline)
                    FROM alerts_mt a2
                    JOIN trademarks t2 ON a2.conflicting_trademark_id = t2.id
                    WHERE a2.watchlist_item_id = w.id
                      AND a2.status NOT IN ('dismissed', 'resolved')
                      AND t2.appeal_deadline >= CURRENT_DATE
                ) ASC NULLS LAST"""
        else:
            order_by = sort_map.get(sort_by, "w.created_at DESC")

        query = f"""
            SELECT w.*,
                   COUNT(a.id) FILTER (
                       WHERE a.status = 'new'
                       AND a.overall_risk_score >= {float(threshold)}
                   ) AS new_alerts_count,
                   COUNT(a.id) FILTER (
                       WHERE a.status NOT IN ('dismissed', 'resolved')
                       AND (t.appeal_deadline IS NULL OR t.appeal_deadline >= CURRENT_DATE)
                       AND a.overall_risk_score >= {float(threshold)}
                   ) AS total_alerts_count,
                   MAX(my_tm.application_date) AS true_application_date,
                   MAX(my_tm.image_path) AS trademark_image_path,
                   MAX(my_tm.final_status) AS trademark_status
            FROM watchlist_mt w
            LEFT JOIN alerts_mt a ON w.id = a.watchlist_item_id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            LEFT JOIN trademarks my_tm ON w.customer_application_no = my_tm.application_no
            WHERE {where_clause}
            GROUP BY w.id
            ORDER BY {order_by}
            LIMIT %s OFFSET %s
        """

        offset = (page - 1) * page_size
        cur.execute(query, params + [page_size, offset])

        return [dict(row) for row in cur.fetchall()], total

    @staticmethod
    def get_all_active(db: Database) -> List[Dict]:
        """Get all active watchlist items across all organizations (for scanning)."""
        cur = db.cursor()
        cur.execute(
            """
            SELECT w.*, o.name as org_name
            FROM watchlist_mt w
            JOIN organizations o ON w.organization_id = o.id
            WHERE w.is_active = TRUE AND o.is_active = TRUE
            ORDER BY w.organization_id, w.id
        """
        )
        return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def update(db: Database, item_id: UUID, org_id: UUID, data: WatchlistItemUpdate) -> Optional[Dict]:
        """Update watchlist item."""
        cur = db.cursor()

        field_mapping = {
            "application_no": "customer_application_no",
            "bulletin_no": "customer_bulletin_no",
            "registration_no": "customer_registration_no",
            "similarity_threshold": "alert_threshold",
            "monitor_text": "monitor_similar_names",
            "monitor_visual": "monitor_similar_logos",
            "alert_email": "notify_email",
        }

        updates = []
        values = []

        for field, value in data.dict(exclude_unset=True).items():
            if value is not None:
                if field == "alert_frequency":
                    value = value.value
                db_field = field_mapping.get(field, field)
                updates.append(f"{db_field} = %s")
                values.append(value)

        if not updates:
            return WatchlistCRUD.get_by_id(db, item_id, org_id)

        values.extend([str(item_id), str(org_id)])
        cur.execute(
            f"""
            UPDATE watchlist_mt SET {', '.join(updates)}
            WHERE id = %s AND organization_id = %s
            RETURNING *
        """,
            values,
        )

        db.commit()
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def delete(db: Database, item_id: UUID, org_id: UUID) -> bool:
        """Soft delete watchlist item."""
        cur = db.cursor()
        cur.execute(
            """
            UPDATE watchlist_mt SET is_active = FALSE
            WHERE id = %s AND organization_id = %s
        """,
            (str(item_id), str(org_id)),
        )
        db.commit()
        return cur.rowcount > 0

    @staticmethod
    def update_embedding(
        db: Database,
        item_id: UUID,
        text_embedding: List[float],
        logo_embedding: Optional[List[float]] = None,
        logo_ocr_text: Optional[str] = None,
    ):
        """Update watchlist item embeddings and OCR text."""
        cur = db.cursor()

        if logo_embedding:
            cur.execute(
                """
                UPDATE watchlist_mt
                SET text_embedding = %s::halfvec,
                    logo_embedding = %s::halfvec,
                    logo_ocr_text = COALESCE(%s, logo_ocr_text)
                WHERE id = %s
            """,
                (str(text_embedding), str(logo_embedding), logo_ocr_text, str(item_id)),
            )
        else:
            cur.execute(
                """
                UPDATE watchlist_mt
                SET text_embedding = %s::halfvec,
                    logo_ocr_text = COALESCE(%s, logo_ocr_text)
                WHERE id = %s
            """,
                (str(text_embedding), logo_ocr_text, str(item_id)),
            )

        db.commit()

    @staticmethod
    def update_logo(
        db: Database,
        item_id: UUID,
        logo_path: str,
        logo_embedding: Optional[List[float]] = None,
        dino_embedding: Optional[List[float]] = None,
        color_histogram: Optional[List[float]] = None,
        logo_ocr_text: Optional[str] = None,
    ):
        """Update watchlist item logo path and all visual embeddings."""
        cur = db.cursor()

        sets = ["logo_path = %s"]
        vals = [logo_path]

        if logo_embedding is not None:
            sets.append("logo_embedding = %s::halfvec")
            vals.append(str(logo_embedding))
        if dino_embedding is not None:
            sets.append("logo_dinov2_embedding = %s::halfvec")
            vals.append(str(dino_embedding))
        if color_histogram is not None:
            sets.append("logo_color_histogram = %s::halfvec")
            vals.append(str(color_histogram))
        if logo_ocr_text is not None:
            sets.append("logo_ocr_text = %s")
            vals.append(logo_ocr_text)

        vals.append(str(item_id))
        cur.execute(
            f"UPDATE watchlist_mt SET {', '.join(sets)} WHERE id = %s",
            vals,
        )
        db.commit()

    @staticmethod
    def clear_logo(db: Database, item_id: UUID):
        """Remove logo and all visual embeddings from watchlist item."""
        cur = db.cursor()
        cur.execute(
            """
            UPDATE watchlist_mt
            SET logo_path = NULL,
                logo_embedding = NULL,
                logo_dinov2_embedding = NULL,
                logo_color_histogram = NULL,
                logo_ocr_text = NULL
            WHERE id = %s
        """,
            (str(item_id),),
        )
        db.commit()

    @staticmethod
    def update_scanned(db: Database, item_id: UUID):
        """Mark watchlist item as scanned."""
        cur = db.cursor()
        cur.execute(
            """
            UPDATE watchlist_mt SET last_scan_at = NOW()
            WHERE id = %s
        """,
            (str(item_id),),
        )
        db.commit()

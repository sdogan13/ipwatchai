"""Trademark application repository operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from models.schemas import TrademarkApplicationCreate, TrademarkApplicationUpdate

if TYPE_CHECKING:
    from database.crud import Database


class ApplicationCRUD:
    @staticmethod
    def create(db: Database, org_id: UUID, user_id: UUID, data: TrademarkApplicationCreate) -> Dict:
        """Create a new trademark application."""
        cur = db.cursor()
        app_id = uuid4()
        cur.execute(
            """
            INSERT INTO trademark_applications_mt (
                id, organization_id, user_id, status, application_type,
                brand_name, mark_type, nice_class_numbers, goods_services_description,
                applicant_full_name, applicant_id_no, applicant_id_type,
                applicant_address, applicant_phone, applicant_email,
                notes, source_search_query, source_risk_score,
                opposition_target_app_no, opposition_target_brand, opposition_target_holder,
                opposition_target_bulletin_no, opposition_target_bulletin_date,
                opposition_target_classes, opposition_grounds
            ) VALUES (
                %s, %s, %s, 'draft', %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s
            ) RETURNING *
        """,
            (
                str(app_id),
                str(org_id),
                str(user_id),
                data.application_type.value,
                data.brand_name,
                data.mark_type.value,
                data.nice_class_numbers,
                data.goods_services_description,
                data.applicant_full_name,
                data.applicant_id_no,
                data.applicant_id_type,
                data.applicant_address,
                data.applicant_phone,
                str(data.applicant_email) if data.applicant_email else None,
                data.notes,
                data.source_search_query,
                data.source_risk_score,
                data.opposition_target_app_no,
                data.opposition_target_brand,
                data.opposition_target_holder,
                data.opposition_target_bulletin_no,
                data.opposition_target_bulletin_date,
                data.opposition_target_classes,
                data.opposition_grounds,
            ),
        )
        db.commit()
        return dict(cur.fetchone())

    @staticmethod
    def get_by_id(db: Database, app_id: UUID, org_id: UUID) -> Optional[Dict]:
        """Get application by ID scoped to organization."""
        cur = db.cursor()
        cur.execute(
            """
            SELECT * FROM trademark_applications_mt
            WHERE id = %s AND organization_id = %s
        """,
            (str(app_id), str(org_id)),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_by_organization(
        db: Database,
        org_id: UUID,
        status: Optional[str] = None,
        application_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Dict], int]:
        """Get paginated applications for an organization."""
        cur = db.cursor()
        where = "WHERE organization_id = %s"
        params: list = [str(org_id)]

        if status:
            where += " AND status = %s"
            params.append(status)

        if application_type:
            where += " AND application_type = %s"
            params.append(application_type)

        cur.execute(f"SELECT COUNT(*) FROM trademark_applications_mt {where}", params)
        total = cur.fetchone()["count"]

        offset = (page - 1) * page_size
        cur.execute(
            f"""
            SELECT * FROM trademark_applications_mt
            {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """,
            params + [page_size, offset],
        )
        rows = [dict(row) for row in cur.fetchall()]
        return rows, total

    @staticmethod
    def update(db: Database, app_id: UUID, org_id: UUID, data: TrademarkApplicationUpdate) -> Optional[Dict]:
        """Update a draft application."""
        cur = db.cursor()
        cur.execute(
            """
            SELECT status FROM trademark_applications_mt
            WHERE id = %s AND organization_id = %s
        """,
            (str(app_id), str(org_id)),
        )
        row = cur.fetchone()
        if not row:
            return None
        if row["status"] != "draft":
            raise ValueError("Only draft applications can be edited")

        updates = []
        params = []
        for field, value in data.dict(exclude_unset=True).items():
            if value is not None:
                updates.append(f"{field} = %s")
                params.append(value.value if field in ("mark_type", "application_type") and hasattr(value, "value") else value)

        if not updates:
            return ApplicationCRUD.get_by_id(db, app_id, org_id)

        updates.append("updated_at = NOW()")
        params.extend([str(app_id), str(org_id)])

        cur.execute(
            f"""
            UPDATE trademark_applications_mt
            SET {', '.join(updates)}
            WHERE id = %s AND organization_id = %s
            RETURNING *
        """,
            params,
        )
        db.commit()
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def update_status(db: Database, app_id: UUID, org_id: UUID, new_status: str) -> Optional[Dict]:
        """Update application status with the right timestamp column."""
        cur = db.cursor()
        timestamp_field = {
            "submitted": "submitted_at",
            "under_review": "reviewed_at",
            "approved": "reviewed_at",
            "rejected": "reviewed_at",
            "completed": "completed_at",
        }.get(new_status)
        timestamp_clause = f", {timestamp_field} = NOW()" if timestamp_field else ""

        cur.execute(
            f"""
            UPDATE trademark_applications_mt
            SET status = %s, updated_at = NOW(){timestamp_clause}
            WHERE id = %s AND organization_id = %s
            RETURNING *
        """,
            (new_status, str(app_id), str(org_id)),
        )
        db.commit()
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def update_logo(db: Database, app_id: UUID, org_id: UUID, logo_path: str) -> Optional[Dict]:
        """Update application logo path."""
        cur = db.cursor()
        cur.execute(
            """
            UPDATE trademark_applications_mt
            SET logo_path = %s, updated_at = NOW()
            WHERE id = %s AND organization_id = %s
            RETURNING *
        """,
            (logo_path, str(app_id), str(org_id)),
        )
        db.commit()
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def delete(db: Database, app_id: UUID, org_id: UUID) -> bool:
        """Delete a draft application."""
        cur = db.cursor()
        cur.execute(
            """
            DELETE FROM trademark_applications_mt
            WHERE id = %s AND organization_id = %s AND status = 'draft'
        """,
            (str(app_id), str(org_id)),
        )
        db.commit()
        return cur.rowcount > 0

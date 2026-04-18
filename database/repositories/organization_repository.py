"""Organization repository operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional, Tuple
from uuid import UUID, uuid4

import psycopg2

from models.schemas import OrganizationCreate

if TYPE_CHECKING:
    from database.crud import Database


class OrganizationCRUD:
    @staticmethod
    def create(db: Database, data: OrganizationCreate) -> Dict:
        """Create new organization."""
        cur = db.cursor()

        # Generate unique slug by appending number if duplicate exists.
        base_slug = data.slug
        slug = base_slug
        counter = 1
        while True:
            cur.execute("SELECT id FROM organizations WHERE slug = %s", (slug,))
            if not cur.fetchone():
                break
            slug = f"{base_slug}-{counter}"
            counter += 1

        org_id = uuid4()
        cur.execute(
            """
            INSERT INTO organizations (id, name, slug, phone, address)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
        """,
            (str(org_id), data.name, slug, data.phone, data.address),
        )

        db.commit()
        return dict(cur.fetchone())

    @staticmethod
    def get_by_id(db: Database, org_id: UUID) -> Optional[Dict]:
        """Get organization by ID."""
        cur = db.cursor()
        cur.execute("SELECT * FROM organizations WHERE id = %s", (str(org_id),))
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_by_slug(db: Database, slug: str) -> Optional[Dict]:
        """Get organization by slug."""
        cur = db.cursor()
        cur.execute("SELECT * FROM organizations WHERE slug = %s", (slug,))
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def update(db: Database, org_id: UUID, data) -> Optional[Dict]:
        """Update organization - accepts OrganizationUpdate object or dict."""
        cur = db.cursor()

        def get_val(key):
            if isinstance(data, dict):
                return data.get(key)
            return getattr(data, key, None)

        updates = []
        values = []

        field_mappings = [
            ("name", "name"),
            ("email", "email"),
            ("phone", "phone"),
            ("address", "address"),
            ("tax_id", "tax_id"),
            ("industry", "industry"),
            ("website", "website"),
            ("email_notifications", "email_notifications"),
            ("weekly_report", "weekly_report"),
            ("risk_threshold", "default_alert_threshold"),
        ]

        for field_name, column_name in field_mappings:
            val = get_val(field_name)
            if val is not None:
                updates.append(f"{column_name} = %s")
                values.append(val)

        settings_val = get_val("settings")
        if settings_val is not None:
            updates.append("settings = %s")
            values.append(psycopg2.extras.Json(settings_val))

        if not updates:
            return OrganizationCRUD.get_by_id(db, org_id)

        values.append(str(org_id))
        cur.execute(
            f"""
            UPDATE organizations SET {', '.join(updates)}
            WHERE id = %s RETURNING *
        """,
            values,
        )

        db.commit()
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_stats(db: Database, org_id: UUID) -> Dict:
        """Get organization statistics."""
        cur = db.cursor()
        cur.execute(
            """
            SELECT * FROM org_dashboard_stats WHERE organization_id = %s
        """,
            (str(org_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else {}

    @staticmethod
    def check_limits(
        db: Database,
        org_id: UUID,
        resource: str,
        user_id=None,
    ) -> Tuple[bool, int, int]:
        """
        Check if organization is within limits based on subscription plan.
        If user_id is provided, checks superadmin status and per-user plan overrides.
        Returns: (within_limit, current_count, max_allowed)
        """
        from utils.subscription import get_plan_limit

        cur = db.cursor()

        plan_name = "free"
        if user_id:
            cur.execute(
                """
                SELECT u.is_superadmin,
                       COALESCE(sp_user.name, sp_org.name, 'free') as plan_name
                FROM users u
                LEFT JOIN subscription_plans sp_user ON u.individual_plan_id = sp_user.id
                LEFT JOIN organizations o ON u.organization_id = o.id
                LEFT JOIN subscription_plans sp_org ON o.subscription_plan_id = sp_org.id
                WHERE u.id = %s
            """,
                (str(user_id),),
            )
            urow = cur.fetchone()
            if urow:
                plan_name = urow["plan_name"]
                if urow["is_superadmin"]:
                    plan_name = "superadmin"
        else:
            cur.execute(
                """
                SELECT COALESCE(sp.name, 'free') as plan_name
                FROM organizations o
                LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
                WHERE o.id = %s
            """,
                (str(org_id),),
            )
            row = cur.fetchone()
            if not row:
                return False, 0, 0
            plan_name = row["plan_name"]

        if resource == "users":
            cur.execute(
                "SELECT COUNT(*) FROM users WHERE organization_id = %s AND is_active = TRUE",
                (str(org_id),),
            )
            current = cur.fetchone()["count"]
            max_users = get_plan_limit(plan_name, "max_users")
            return current < max_users, current, max_users

        if resource == "watchlist":
            cur.execute(
                "SELECT COUNT(*) FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE",
                (str(org_id),),
            )
            current = cur.fetchone()["count"]
            max_items = get_plan_limit(plan_name, "max_watchlist_items")
            return current < max_items, current, max_items

        return True, 0, 0

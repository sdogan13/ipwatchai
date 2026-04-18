"""User repository operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional
from uuid import UUID, uuid4

from auth.authentication import hash_password
from models.schemas import UserCreate

from database.repositories.organization_repository import OrganizationCRUD

if TYPE_CHECKING:
    from database.crud import Database


class UserCRUD:
    @staticmethod
    def create(db: Database, org_id: UUID, data: UserCreate) -> Dict:
        """Create new user."""
        cur = db.cursor()

        cur.execute("SELECT id FROM users WHERE email = %s", (data.email,))
        if cur.fetchone():
            raise ValueError(f"Email '{data.email}' already registered")

        within_limit, _, _ = OrganizationCRUD.check_limits(db, org_id, "users")
        if not within_limit:
            raise ValueError("Organization has reached maximum user limit")

        user_id = uuid4()
        password_hash = hash_password(data.password)

        cur.execute(
            """
            INSERT INTO users (id, organization_id, email, password_hash, first_name, last_name, phone, role)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, organization_id, email, first_name, last_name, phone, role,
                      is_active, is_email_verified, created_at
        """,
            (
                str(user_id),
                str(org_id),
                data.email,
                password_hash,
                data.first_name,
                data.last_name,
                data.phone,
                data.role.value,
            ),
        )

        db.commit()
        return dict(cur.fetchone())

    @staticmethod
    def get_by_id(db: Database, user_id: UUID) -> Optional[Dict]:
        """Get user by ID."""
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, organization_id, email, password_hash, first_name, last_name, phone, role,
                   is_active, is_email_verified, COALESCE(is_superadmin, FALSE) as is_superadmin,
                   last_login_at, created_at,
                   avatar_url, title, department, linkedin
            FROM users WHERE id = %s
        """,
            (str(user_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_by_email(db: Database, email: str) -> Optional[Dict]:
        """Get user by email (includes password hash for auth)."""
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, organization_id, email, password_hash, first_name, last_name,
                   phone, role, is_active, is_email_verified
            FROM users WHERE email = %s
        """,
            (email,),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_by_organization(db: Database, org_id: UUID, include_inactive: bool = False) -> List[Dict]:
        """Get all users in organization."""
        cur = db.cursor()
        query = """
            SELECT id, organization_id, email, first_name, last_name, phone, role,
                   is_active, is_email_verified, last_login_at, created_at
            FROM users WHERE organization_id = %s
        """
        if not include_inactive:
            query += " AND is_active = TRUE"
        query += " ORDER BY created_at"

        cur.execute(query, (str(org_id),))
        return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def update(db: Database, user_id: UUID, data) -> Optional[Dict]:
        """Update user - accepts UserUpdate object or dict."""
        cur = db.cursor()

        def get_val(key):
            if isinstance(data, dict):
                return data.get(key)
            return getattr(data, key, None)

        updates = []
        values = []

        field_mappings = [
            ("first_name", "first_name"),
            ("last_name", "last_name"),
            ("email", "email"),
            ("phone", "phone"),
            ("title", "title"),
            ("department", "department"),
            ("linkedin", "linkedin"),
            ("avatar_url", "avatar_url"),
            ("password_hash", "password_hash"),
        ]

        for field_name, column_name in field_mappings:
            val = get_val(field_name)
            if val is not None:
                updates.append(f"{column_name} = %s")
                values.append(val)

        updates.append("updated_at = NOW()")

        if len(updates) == 1:
            return UserCRUD.get_by_id(db, user_id)

        values.append(str(user_id))
        cur.execute(
            f"""
            UPDATE users SET {', '.join(updates)}
            WHERE id = %s
            RETURNING id, organization_id, email, first_name, last_name, phone, role,
                      is_active, is_email_verified, last_login_at, created_at,
                      title, department, linkedin, avatar_url, updated_at
        """,
            values,
        )

        db.commit()
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def update_login(db: Database, user_id: UUID):
        """Update last login timestamp."""
        cur = db.cursor()
        cur.execute(
            """
            UPDATE users SET last_login_at = NOW()
            WHERE id = %s
        """,
            (str(user_id),),
        )
        db.commit()

    @staticmethod
    def verify_email(db: Database, user_id: UUID):
        """Mark user email as verified."""
        cur = db.cursor()
        cur.execute(
            """
            UPDATE users SET is_email_verified = TRUE, email_verified_at = NOW()
            WHERE id = %s
        """,
            (str(user_id),),
        )
        db.commit()

    @staticmethod
    def deactivate(db: Database, user_id: UUID):
        """Deactivate user."""
        cur = db.cursor()
        cur.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (str(user_id),))
        db.commit()

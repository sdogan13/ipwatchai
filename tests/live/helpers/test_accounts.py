from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from auth.authentication import hash_password
from database.crud import Database, OrganizationCRUD, UserCRUD
from models.schemas import OrganizationCreate, UserCreate, UserRole
from tests.live.helpers.config import DEFAULT_PASSWORD


@dataclass(frozen=True)
class ManagedPersonaDefinition:
    plan_name: str
    email_env: str
    default_email: str
    password_env: str
    default_password: str
    organization_env: str
    default_organization_name: str
    first_name: str
    last_name: str


@dataclass(frozen=True)
class TestAccountRecord:
    email: str
    user_id: str
    organization_id: str | None
    created_at: Any


MANAGED_PERSONAS: dict[str, ManagedPersonaDefinition] = {
    "free": ManagedPersonaDefinition(
        plan_name="free",
        email_env="TEST_MANAGED_FREE_EMAIL",
        default_email="managed-free-smoke@example.com",
        password_env="TEST_MANAGED_FREE_PASSWORD",
        default_password=DEFAULT_PASSWORD,
        organization_env="TEST_MANAGED_FREE_ORG",
        default_organization_name="Managed Free Smoke Org",
        first_name="Managed",
        last_name="Free",
    ),
    "starter": ManagedPersonaDefinition(
        plan_name="starter",
        email_env="TEST_MANAGED_STARTER_EMAIL",
        default_email="managed-starter-smoke@example.com",
        password_env="TEST_MANAGED_STARTER_PASSWORD",
        default_password=DEFAULT_PASSWORD,
        organization_env="TEST_MANAGED_STARTER_ORG",
        default_organization_name="Managed Starter Smoke Org",
        first_name="Managed",
        last_name="Starter",
    ),
    "professional": ManagedPersonaDefinition(
        plan_name="professional",
        email_env="TEST_MANAGED_PROFESSIONAL_EMAIL",
        default_email="managed-professional-smoke@example.com",
        password_env="TEST_MANAGED_PROFESSIONAL_PASSWORD",
        default_password=DEFAULT_PASSWORD,
        organization_env="TEST_MANAGED_PROFESSIONAL_ORG",
        default_organization_name="Managed Professional Smoke Org",
        first_name="Managed",
        last_name="Professional",
    ),
}

DISPOSABLE_EMAIL_PREFIXES = (
    "live-free-",
    "live-starter-",
    "live-professional-",
    "browser-checkout-",
    "browser-register-",
    "browser-forgot-success-",
)

DISPOSABLE_EXACT_EMAILS = (
    "e2e_resend_test@example.com",
    "e2e_verify_test@example.com",
    "test_refactor_2011423636@test.com",
)


def get_managed_persona_definition(plan_name: str) -> ManagedPersonaDefinition:
    try:
        return MANAGED_PERSONAS[plan_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported managed persona plan: {plan_name}") from exc


def get_managed_persona_credentials(plan_name: str) -> dict[str, str]:
    definition = get_managed_persona_definition(plan_name)
    return {
        "email": os.environ.get(definition.email_env, definition.default_email),
        "password": os.environ.get(definition.password_env, definition.default_password),
        "organization_name": os.environ.get(
            definition.organization_env,
            definition.default_organization_name,
        ),
        "first_name": definition.first_name,
        "last_name": definition.last_name,
    }


def _slugify(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "-", value.lower())
    lowered = lowered.strip("-")
    return lowered or "managed-test-org"


def _get_plan_id(cur, plan_name: str) -> str:
    cur.execute("SELECT id FROM subscription_plans WHERE name = %s", (plan_name,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Subscription plan '{plan_name}' not found")
    return str(row["id"])


def _load_user_row(db: Database, email: str) -> dict[str, Any] | None:
    cur = db.cursor()
    cur.execute(
        """
        SELECT
            u.id,
            u.organization_id,
            u.email,
            u.role,
            u.is_active,
            u.is_email_verified,
            COALESCE(sp.name, 'free') AS plan_name
        FROM users u
        LEFT JOIN organizations o ON o.id = u.organization_id
        LEFT JOIN subscription_plans sp ON sp.id = o.subscription_plan_id
        WHERE u.email = %s
        """,
        (email,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def ensure_managed_persona_account(
    plan_name: str,
    *,
    email: str | None = None,
    password: str | None = None,
    organization_name: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict[str, str]:
    defaults = get_managed_persona_credentials(plan_name)
    email = email or defaults["email"]
    password = password or defaults["password"]
    organization_name = organization_name or defaults["organization_name"]
    first_name = first_name or defaults["first_name"]
    last_name = last_name or defaults["last_name"]

    password_hash = hash_password(password)

    with Database() as db:
        user_row = _load_user_row(db, email)
        org_id = user_row.get("organization_id") if user_row else None
        if not org_id:
            org = OrganizationCRUD.create(
                db,
                OrganizationCreate(
                    name=organization_name,
                    slug=_slugify(organization_name),
                    email=email,
                ),
            )
            org_id = str(org["id"])

        cur = db.cursor()
        plan_id = _get_plan_id(cur, plan_name)
        cur.execute(
            """
            UPDATE organizations
            SET
                name = %s,
                is_active = TRUE,
                subscription_plan_id = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (organization_name, plan_id, str(org_id)),
        )

        if user_row:
            cur.execute(
                """
                UPDATE users
                SET
                    organization_id = %s,
                    password_hash = %s,
                    first_name = %s,
                    last_name = %s,
                    role = %s,
                    is_organization_admin = TRUE,
                    is_superadmin = FALSE,
                    is_active = TRUE,
                    is_email_verified = TRUE,
                    email_verified_at = COALESCE(email_verified_at, NOW()),
                    individual_plan_id = NULL,
                    failed_login_attempts = 0,
                    locked_until = NULL,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, organization_id, email
                """,
                (
                    str(org_id),
                    password_hash,
                    first_name,
                    last_name,
                    UserRole.ADMIN.value,
                    str(user_row["id"]),
                ),
            )
            user = dict(cur.fetchone())
        else:
            created = UserCRUD.create(
                db,
                UUID(str(org_id)),
                UserCreate(
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    role=UserRole.ADMIN,
                ),
            )
            cur.execute(
                """
                UPDATE users
                SET
                    is_organization_admin = TRUE,
                    is_email_verified = TRUE,
                    email_verified_at = NOW(),
                    is_active = TRUE,
                    individual_plan_id = NULL,
                    failed_login_attempts = 0,
                    locked_until = NULL
                WHERE id = %s
                RETURNING id, organization_id, email
                """,
                (str(created["id"]),),
            )
            user = dict(cur.fetchone())

        user_id = str(user["id"])
        cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM email_verification_tokens WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM user_sessions WHERE user_id = %s", (user_id,))
        db.commit()

    return {
        "email": email,
        "password": password,
        "organization_id": str(user["organization_id"]),
        "user_id": user_id,
        "plan_name": plan_name,
    }


def collect_accounts_by_email(emails: list[str]) -> list[TestAccountRecord]:
    if not emails:
        return []

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, organization_id, email, created_at
            FROM users
            WHERE email = ANY(%s)
            ORDER BY created_at DESC
            """,
            (emails,),
        )
        return [
            TestAccountRecord(
                email=row["email"],
                user_id=str(row["id"]),
                organization_id=str(row["organization_id"]) if row["organization_id"] else None,
                created_at=row["created_at"],
            )
            for row in cur.fetchall()
        ]


def collect_disposable_test_accounts() -> list[TestAccountRecord]:
    conditions = ["email LIKE %s" for _ in DISPOSABLE_EMAIL_PREFIXES]
    params: list[str] = [f"{prefix}%" for prefix in DISPOSABLE_EMAIL_PREFIXES]

    conditions.extend("email = %s" for _ in DISPOSABLE_EXACT_EMAILS)
    params.extend(DISPOSABLE_EXACT_EMAILS)

    query = f"""
        SELECT id, organization_id, email, created_at
        FROM users
        WHERE {' OR '.join(conditions)}
        ORDER BY created_at DESC
    """

    with Database() as db:
        cur = db.cursor()
        cur.execute(query, params)
        return [
            TestAccountRecord(
                email=row["email"],
                user_id=str(row["id"]),
                organization_id=str(row["organization_id"]) if row["organization_id"] else None,
                created_at=row["created_at"],
            )
            for row in cur.fetchall()
        ]


def _remove_files(paths: set[str]) -> dict[str, int]:
    removed = 0
    missing = 0

    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file():
            path.unlink()
            removed += 1
        else:
            missing += 1

    return {"files_removed": removed, "files_missing": missing}


def purge_test_accounts(records: list[TestAccountRecord]) -> dict[str, int]:
    summary = {
        "accounts_targeted": len(records),
        "organizations_targeted": len({record.organization_id for record in records if record.organization_id}),
        "organizations_deleted": 0,
        "users_deleted": 0,
        "alerts_acknowledged_cleared": 0,
        "alerts_resolved_cleared": 0,
        "app_settings_unlinked": 0,
        "discount_codes_unlinked": 0,
        "assigned_specialists_cleared": 0,
        "generated_images_deleted": 0,
        "generation_logs_deleted": 0,
        "payments_deleted": 0,
        "discount_usage_deleted": 0,
        "files_removed": 0,
        "files_missing": 0,
    }
    if not records:
        return summary

    user_ids = sorted({record.user_id for record in records})
    org_ids = sorted({record.organization_id for record in records if record.organization_id})

    with Database() as db:
        cur = db.cursor()

        if org_ids:
            file_paths: set[str] = set()
            for table_name, filter_column, value_column in (
                ("reports", "organization_id", "file_path"),
                ("generated_images", "org_id", "image_path"),
                ("watchlist_mt", "organization_id", "logo_path"),
                ("trademark_applications_mt", "organization_id", "logo_path"),
            ):
                cur.execute(
                    f"SELECT {value_column} FROM {table_name} WHERE {filter_column} = ANY(%s::uuid[]) AND {value_column} IS NOT NULL",
                    (org_ids,),
                )
                file_paths.update(
                    row[value_column]
                    for row in cur.fetchall()
                    if row.get(value_column)
                )

            file_summary = _remove_files(file_paths)
            summary["files_removed"] = file_summary["files_removed"]
            summary["files_missing"] = file_summary["files_missing"]

        if user_ids:
            cur.execute(
                "UPDATE alerts_mt SET acknowledged_by = NULL WHERE acknowledged_by = ANY(%s::uuid[])",
                (user_ids,),
            )
            summary["alerts_acknowledged_cleared"] = cur.rowcount or 0

            cur.execute(
                "UPDATE alerts_mt SET resolved_by = NULL WHERE resolved_by = ANY(%s::uuid[])",
                (user_ids,),
            )
            summary["alerts_resolved_cleared"] = cur.rowcount or 0

            cur.execute(
                "UPDATE app_settings SET updated_by = NULL WHERE updated_by = ANY(%s::uuid[])",
                (user_ids,),
            )
            summary["app_settings_unlinked"] = cur.rowcount or 0

            cur.execute(
                "UPDATE discount_codes SET created_by = NULL WHERE created_by = ANY(%s::uuid[])",
                (user_ids,),
            )
            summary["discount_codes_unlinked"] = cur.rowcount or 0

            cur.execute(
                """
                UPDATE trademark_applications_mt
                SET assigned_specialist_id = NULL
                WHERE assigned_specialist_id = ANY(%s::uuid[])
                """,
                (user_ids,),
            )
            summary["assigned_specialists_cleared"] = cur.rowcount or 0

            cur.execute(
                "DELETE FROM generation_logs WHERE user_id = ANY(%s::uuid[])",
                (user_ids,),
            )
            summary["generation_logs_deleted"] += cur.rowcount or 0

            cur.execute(
                "DELETE FROM payments WHERE user_id = ANY(%s::uuid[])",
                (user_ids,),
            )
            summary["payments_deleted"] += cur.rowcount or 0

        if org_ids:
            cur.execute(
                "DELETE FROM generated_images WHERE org_id = ANY(%s::uuid[])",
                (org_ids,),
            )
            summary["generated_images_deleted"] = cur.rowcount or 0

            cur.execute(
                "DELETE FROM generation_logs WHERE org_id = ANY(%s::uuid[])",
                (org_ids,),
            )
            summary["generation_logs_deleted"] += cur.rowcount or 0

            cur.execute(
                "DELETE FROM payments WHERE organization_id = ANY(%s::uuid[])",
                (org_ids,),
            )
            summary["payments_deleted"] += cur.rowcount or 0

            cur.execute(
                "DELETE FROM discount_code_usage WHERE organization_id = ANY(%s::uuid[])",
                (org_ids,),
            )
            summary["discount_usage_deleted"] = cur.rowcount or 0

            cur.execute(
                "DELETE FROM organizations WHERE id = ANY(%s::uuid[])",
                (org_ids,),
            )
            summary["organizations_deleted"] = cur.rowcount or 0

        if user_ids:
            cur.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", (user_ids,))
            summary["users_deleted"] = cur.rowcount or 0

        db.commit()

    return summary


def delete_test_account(email: str) -> dict[str, int]:
    return purge_test_accounts(collect_accounts_by_email([email]))

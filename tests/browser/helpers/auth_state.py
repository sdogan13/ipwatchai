from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from database.crud import Database, UserCRUD
from tests.live.helpers.test_accounts import delete_test_account, ensure_managed_persona_account


def create_verified_browser_account(
    email: str,
    password: str,
    *,
    first_name: str = "Browser",
    last_name: str = "Reset",
    organization_name: str,
) -> None:
    ensure_managed_persona_account(
        "free",
        email=email,
        password=password,
        organization_name=organization_name,
        first_name=first_name,
        last_name=last_name,
    )


def delete_browser_test_account(email: str) -> dict[str, int]:
    return delete_test_account(email)


def lookup_password_reset_code(email: str) -> str:
    with Database() as db:
        user = UserCRUD.get_by_email(db, email)
        if not user:
            raise AssertionError(f"user not found for password reset lookup: {email}")

        cur = db.cursor()
        cur.execute(
            """
            SELECT token_hash, expires_at, used_at
            FROM password_reset_tokens
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(user["id"]),),
        )
        row = cur.fetchone()

    if not row:
        raise AssertionError(f"no password reset token found for {email}")
    if row["used_at"] is not None:
        raise AssertionError(f"password reset token already used for {email}")

    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise AssertionError(f"password reset token already expired for {email}")

    token_hash = row["token_hash"]
    for value in range(1_000_000):
        code = f"{value:06d}"
        if hashlib.sha256(code.encode()).hexdigest() == token_hash:
            return code

    raise AssertionError(f"unable to resolve password reset code for {email}")


def lookup_email_verification_code(email: str) -> str:
    with Database() as db:
        user = UserCRUD.get_by_email(db, email)
        if not user:
            raise AssertionError(f"user not found for email verification lookup: {email}")

        cur = db.cursor()
        cur.execute(
            """
            SELECT token_hash, expires_at, used_at
            FROM email_verification_tokens
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(user["id"]),),
        )
        row = cur.fetchone()

    if not row:
        raise AssertionError(f"no email verification token found for {email}")
    if row["used_at"] is not None:
        raise AssertionError(f"email verification token already used for {email}")

    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise AssertionError(f"email verification token already expired for {email}")

    token_hash = row["token_hash"]
    for value in range(1_000_000):
        code = f"{value:06d}"
        if hashlib.sha256(code.encode()).hexdigest() == token_hash:
            return code

    raise AssertionError(f"unable to resolve email verification code for {email}")

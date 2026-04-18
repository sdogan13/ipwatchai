from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

from database.crud import Database, UserCRUD
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import load_live_config


def _retry_after_seconds(response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass
    return 15.0


def create_verified_browser_account(
    email: str,
    password: str,
    *,
    first_name: str = "Browser",
    last_name: str = "Reset",
    organization_name: str,
) -> None:
    config = load_live_config()
    client = LiveClient(config)
    payload = {
        "email": email,
        "password": password,
        "first_name": first_name,
        "last_name": last_name,
        "organization_name": organization_name,
        "lang": "en",
    }

    response = None
    for attempt in range(1, 6):
        response = client.post("/api/v1/auth/register", json_data=payload, token=False)
        if response.status_code == 200:
            break
        if response.status_code == 429 and attempt < 5:
            time.sleep(_retry_after_seconds(response))
            continue
        raise AssertionError(f"unexpected register status for {email}: {response.status_code}")

    with Database() as db:
        user = UserCRUD.get_by_email(db, email)
        if not user:
            raise AssertionError(f"registered user not found for {email}")
        if not user.get("is_email_verified"):
            UserCRUD.verify_email(db, user["id"])
            cur = db.cursor()
            cur.execute(
                "UPDATE email_verification_tokens SET used_at = NOW() WHERE user_id = %s AND used_at IS NULL",
                (str(user["id"]),),
            )
            db.commit()


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

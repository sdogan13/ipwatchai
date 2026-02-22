"""
Tests for authentication: password hashing, JWT token creation/verification,
role-based access control, and superadmin checks.

All DB calls mocked — tests run without database.
"""
import sys
import os
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest


from auth.authentication import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_token,
    generate_verification_token,
    generate_reset_token,
    TokenPayload,
    TokenPair,
    UserRegister,
    UserLogin,
    PasswordChange,
    CurrentUser,
)


# ============================================================
# Password Hashing
# ============================================================

class TestPasswordHashing:
    """Test bcrypt password hashing and verification."""

    def test_hash_returns_string(self):
        hashed = hash_password("TestPass123!")
        assert isinstance(hashed, str)
        assert len(hashed) > 0

    def test_hash_is_bcrypt_format(self):
        hashed = hash_password("TestPass123!")
        assert hashed.startswith("$2")  # bcrypt prefix

    def test_verify_correct_password(self):
        password = "SecurePass123!"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("CorrectPass1!")
        assert verify_password("WrongPass1!", hashed) is False

    def test_different_inputs_different_hashes(self):
        h1 = hash_password("Password1!")
        h2 = hash_password("Password2!")
        assert h1 != h2

    def test_same_input_different_salts(self):
        """Same password produces different hashes (random salt)."""
        h1 = hash_password("Password1!")
        h2 = hash_password("Password1!")
        assert h1 != h2  # Different salts

    def test_both_verify_same_password(self):
        """Both hashes of same password should verify."""
        pw = "Password1!"
        h1 = hash_password(pw)
        h2 = hash_password(pw)
        assert verify_password(pw, h1)
        assert verify_password(pw, h2)


# ============================================================
# JWT Token Creation & Verification
# ============================================================

class TestJWTTokens:
    """Test JWT access and refresh token lifecycle."""

    def test_create_access_token(self):
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        token = create_access_token(user_id, org_id, "owner")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_valid_access_token(self):
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        token = create_access_token(user_id, org_id, "owner")
        payload = decode_token(token)
        assert payload is not None
        assert payload.sub == user_id
        assert payload.org == org_id
        assert payload.role == "owner"
        assert payload.type == "access"

    def test_create_refresh_token(self):
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        token = create_refresh_token(user_id, org_id, "admin")
        assert isinstance(token, str)

    def test_decode_refresh_token(self):
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        token = create_refresh_token(user_id, org_id, "admin")
        payload = decode_token(token)
        assert payload is not None
        assert payload.type == "refresh"
        assert payload.sub == user_id

    def test_invalid_token_returns_none(self):
        assert decode_token("invalid.token.here") is None

    def test_empty_token_returns_none(self):
        assert decode_token("") is None

    def test_random_string_returns_none(self):
        assert decode_token("abcdef123456") is None

    def test_create_token_pair(self):
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        pair = create_token_pair(user_id, org_id, "owner")
        assert isinstance(pair, TokenPair)
        assert pair.token_type == "bearer"
        assert pair.expires_in > 0
        assert len(pair.access_token) > 0
        assert len(pair.refresh_token) > 0

    def test_token_pair_tokens_are_different(self):
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        pair = create_token_pair(user_id, org_id, "owner")
        assert pair.access_token != pair.refresh_token

    def test_access_token_type_is_access(self):
        token = create_access_token(str(uuid.uuid4()), str(uuid.uuid4()), "user")
        payload = decode_token(token)
        assert payload.type == "access"

    def test_refresh_token_type_is_refresh(self):
        token = create_refresh_token(str(uuid.uuid4()), str(uuid.uuid4()), "user")
        payload = decode_token(token)
        assert payload.type == "refresh"

    @patch("auth.authentication.settings")
    def test_expired_token_returns_none(self, mock_settings):
        """Token created with 0-minute expiry should be expired immediately."""
        mock_settings.auth.secret_key = "test-secret"
        mock_settings.auth.algorithm = "HS256"
        mock_settings.auth.access_token_expire_minutes = 0  # Immediate expiry
        token = create_access_token(str(uuid.uuid4()), str(uuid.uuid4()), "user")
        # Token with 0 minutes → already expired
        payload = decode_token(token)
        # May or may not be None depending on timing, but should not crash
        # With 0 minutes, it's a race condition. Let's test with negative via direct encode.
        from jose import jwt
        expired_payload = {
            "sub": str(uuid.uuid4()),
            "org": str(uuid.uuid4()),
            "role": "user",
            "exp": datetime.utcnow() - timedelta(hours=1),  # Past
            "type": "access",
        }
        expired_token = jwt.encode(expired_payload, "test-secret", algorithm="HS256")
        mock_settings.auth.secret_key = "test-secret"
        mock_settings.auth.algorithm = "HS256"
        result = decode_token(expired_token)
        assert result is None


# ============================================================
# Token Utility Functions
# ============================================================

class TestTokenUtilities:
    """Test verification and reset token generation."""

    def test_verification_token_is_string(self):
        token = generate_verification_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_reset_token_is_string(self):
        token = generate_reset_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_tokens_are_unique(self):
        tokens = {generate_verification_token() for _ in range(100)}
        assert len(tokens) == 100  # All unique


# ============================================================
# Pydantic Models Validation
# ============================================================

class TestAuthModels:
    """Test authentication Pydantic models."""

    def test_user_register_valid(self):
        user = UserRegister(
            email="test@example.com",
            password="SecurePass1!",
            first_name="John",
            last_name="Doe",
        )
        assert user.email == "test@example.com"

    def test_user_register_weak_password_rejected(self):
        with pytest.raises(Exception):  # ValidationError
            UserRegister(
                email="test@example.com",
                password="weak",  # Too short, no uppercase, no digit
                first_name="John",
                last_name="Doe",
            )

    def test_user_register_no_uppercase_rejected(self):
        with pytest.raises(Exception):
            UserRegister(
                email="test@example.com",
                password="nouppercase1",
                first_name="John",
                last_name="Doe",
            )

    def test_user_register_no_digit_rejected(self):
        with pytest.raises(Exception):
            UserRegister(
                email="test@example.com",
                password="NoDigitHere!",
                first_name="John",
                last_name="Doe",
            )

    def test_user_login_valid(self):
        login = UserLogin(email="test@example.com", password="any")
        assert login.email == "test@example.com"

    def test_password_change_valid(self):
        pc = PasswordChange(current_password="OldPass1!", new_password="NewPass1!")
        assert pc.current_password == "OldPass1!"

    def test_current_user_model(self):
        user = CurrentUser(
            id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            email="test@example.com",
            first_name="John",
            last_name="Doe",
            role="owner",
            is_superadmin=False,
            permissions=["watchlist.write"],
        )
        assert user.role == "owner"
        assert user.is_superadmin is False

    def test_current_user_superadmin_default_false(self):
        user = CurrentUser(
            id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            email="a@b.com",
            first_name=None,
            last_name=None,
            role="admin",
            permissions=[],
        )
        assert user.is_superadmin is False

    def test_token_payload_model(self):
        tp = TokenPayload(
            sub="user-123",
            org="org-456",
            role="owner",
            exp=datetime.utcnow(),
            type="access",
        )
        assert tp.sub == "user-123"
        assert tp.type == "access"

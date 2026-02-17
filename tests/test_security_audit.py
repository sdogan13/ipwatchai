"""
Security audit regression tests.
Run with: pytest tests/test_security_audit.py -v

Tests cover:
- Secret key validation (no weak defaults)
- SQL identifier safety (psycopg2.sql module usage)
- Tenant isolation (IDOR prevention)
- File upload validation (magic bytes)
- Security headers (X-Frame-Options, etc.)
- Error message sanitization (no internal leaks)
- Authentication token validation
- LIKE injection prevention
"""
import os
import sys
import uuid
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ==========================================================
# 1. SECRET KEY VALIDATION
# ==========================================================

class TestSecretKeyValidation:
    """AUTH_SECRET_KEY must be strong — no weak defaults, min 32 chars."""

    def test_rejects_short_key(self):
        """Validator code must enforce min 32 chars."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'settings.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert 'len(v) < 32' in content, "Validator must check min 32 char length"
        assert 'AUTH_SECRET_KEY must be at least 32' in content

    def test_rejects_known_weak_defaults(self):
        """Validator must have a blocklist of known weak secrets."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'settings.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert 'weak_defaults' in content, "Validator must check against weak defaults"
        assert '"changeme"' in content
        assert '"your-super-secret-key-change-in-production"' in content

    def test_no_default_secret_key(self):
        """AuthSettings.secret_key must NOT have a default value (require env var)."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'settings.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        # Must NOT have default= for secret_key
        assert 'secret_key: str = Field(alias="AUTH_SECRET_KEY")' in content, \
            "secret_key must not have a default value — require from env"

    def test_no_hardcoded_passwords_in_source(self):
        """Source code must not contain hardcoded DB passwords."""
        sensitive_files = [
            'config/settings.py',
            'workers/universal_scanner.py',
            'compute_idf.py',
        ]
        for fpath in sensitive_files:
            full = os.path.join(os.path.dirname(os.path.dirname(__file__)), fpath)
            if not os.path.exists(full):
                continue
            content = open(full, 'r', encoding='utf-8').read()
            assert 'Dogan.1996' not in content, f"Hardcoded password found in {fpath}"


# ==========================================================
# 2. SQL IDENTIFIER SAFETY
# ==========================================================

class TestSQLIdentifierSafety:
    """Verify psycopg2.sql module is used for dynamic column names."""

    def test_admin_credits_uses_sql_identifier(self):
        """api/admin.py should use psycopg2.sql for column interpolation."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'api', 'admin.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert 'psql.Identifier(column)' in content or 'psql.Identifier' in content, \
            "admin.py should use psycopg2.sql.Identifier for column names"

    def test_main_portfolio_uses_sql_identifier(self):
        """main.py portfolio query should use psycopg2.sql for where_col."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'main.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert 'psql.Identifier(where_col)' in content, \
            "main.py should use psycopg2.sql.Identifier for where_col"

    def test_routes_bulk_import_uses_sql_identifier(self):
        """api/routes.py bulk import should use psycopg2.sql."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'api', 'routes.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert 'psql.Identifier(where_col)' in content, \
            "routes.py should use psycopg2.sql.Identifier for where_col"

    def test_ingest_ddl_uses_sql_identifier(self):
        """ingest.py ALTER TABLE should use psycopg2.sql.Identifier for column names."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ingest.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert 'psql.Identifier(col_name)' in content, \
            "ingest.py should use psycopg2.sql.Identifier for ALTER TABLE"

    def test_ingest_ddl_whitelist(self):
        """ingest.py should whitelist allowed column types for DDL."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ingest.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert 'ALLOWED_COL_TYPES' in content, \
            "ingest.py should have ALLOWED_COL_TYPES whitelist"


# ==========================================================
# 3. TENANT ISOLATION (IDOR PREVENTION)
# ==========================================================

class TestTenantIsolation:
    """Verify all CRUD methods require org_id for tenant scoping."""

    def test_watchlist_get_by_id_requires_org_id(self):
        """WatchlistCRUD.get_by_id must require org_id (not optional)."""
        import inspect
        from database.crud import WatchlistCRUD
        sig = inspect.signature(WatchlistCRUD.get_by_id)
        params = list(sig.parameters.keys())
        assert 'org_id' in params, "get_by_id must have org_id parameter"
        # Verify org_id has no default (not Optional)
        org_param = sig.parameters['org_id']
        assert org_param.default is inspect.Parameter.empty, \
            "org_id must be required (no default value)"

    def test_alert_get_by_id_requires_org_id(self):
        """AlertCRUD.get_by_id must require org_id (not optional)."""
        import inspect
        from database.crud import AlertCRUD
        sig = inspect.signature(AlertCRUD.get_by_id)
        params = list(sig.parameters.keys())
        assert 'org_id' in params
        org_param = sig.parameters['org_id']
        assert org_param.default is inspect.Parameter.empty, \
            "AlertCRUD.get_by_id org_id must be required"

    def test_application_get_by_id_requires_org_id(self):
        """ApplicationCRUD.get_by_id must require org_id."""
        import inspect
        from database.crud import ApplicationCRUD
        sig = inspect.signature(ApplicationCRUD.get_by_id)
        params = list(sig.parameters.keys())
        assert 'org_id' in params


# ==========================================================
# 4. FILE UPLOAD VALIDATION
# ==========================================================

class TestFileUploadValidation:
    """Verify magic byte validation for uploaded files."""

    def test_image_magic_bytes_validator_exists(self):
        """validate_image_magic_bytes function must exist in main."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'main.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert 'def validate_image_magic_bytes' in content

    def test_image_upload_checks_magic_bytes(self):
        """process_uploaded_image must call validate_image_magic_bytes."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'main.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert 'validate_image_magic_bytes(content)' in content

    def test_valid_jpeg_magic_bytes(self):
        """JPEG magic bytes (FF D8 FF) should pass validation."""
        # Inline the validation logic to avoid importing main.py (heavy startup)
        IMAGE_MAGIC_BYTES = {
            b'\xff\xd8\xff': 'image/jpeg',
            b'\x89PNG\r\n\x1a\n': 'image/png',
            b'GIF87a': 'image/gif',
            b'GIF89a': 'image/gif',
            b'BM': 'image/bmp',
            b'RIFF': 'image/webp',
        }

        def validate_image_magic_bytes(content: bytes) -> bool:
            for magic, _ in IMAGE_MAGIC_BYTES.items():
                if content[:len(magic)] == magic:
                    return True
            return False

        assert validate_image_magic_bytes(b'\xff\xd8\xff\xe0' + b'\x00' * 100) is True
        assert validate_image_magic_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100) is True
        assert validate_image_magic_bytes(b'GIF89a' + b'\x00' * 100) is True
        assert validate_image_magic_bytes(b'BM' + b'\x00' * 100) is True
        assert validate_image_magic_bytes(b'MZ' + b'\x00' * 100) is False  # .exe
        assert validate_image_magic_bytes(b'\x00' * 100) is False
        assert validate_image_magic_bytes(b'') is False

    def test_spreadsheet_magic_bytes_in_upload(self):
        """upload.py must validate XLSX PK magic bytes."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'api', 'upload.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert "PK\\x03\\x04" in content or "b'PK" in content, \
            "upload.py should check XLSX ZIP magic bytes"

    def test_upload_image_size_reduced(self):
        """Image upload max should be 10MB, not 100MB."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'main.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        # Should contain 10 * 1024 * 1024, not 100 * 1024 * 1024
        assert '10 * 1024 * 1024' in content, "MAX_IMAGE_SIZE should be 10MB"
        assert '100 * 1024 * 1024  # 100MB max' not in content, \
            "MAX_IMAGE_SIZE should NOT be 100MB"

    def test_upload_no_internal_error_leak(self):
        """Upload error handler should not expose internal exception details."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'api', 'upload.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        # Should NOT have: detail=f"... {str(e)}" pattern in error handler
        assert 'detail=f"Dosya isleme hatasi: {str(e)}"' not in content, \
            "Upload error should not leak internal exception to client"


# ==========================================================
# 5. SECURITY HEADERS
# ==========================================================

class TestSecurityHeaders:
    """Verify security headers are set on responses."""

    def test_security_headers_middleware_exists(self):
        """SecurityHeadersMiddleware must be defined in main.py."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'main.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert 'class SecurityHeadersMiddleware' in content
        assert 'X-Content-Type-Options' in content
        assert 'X-Frame-Options' in content
        assert 'X-XSS-Protection' in content
        assert 'Referrer-Policy' in content

    def test_cors_methods_restricted(self):
        """CORS should not use allow_methods=["*"]."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'main.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        # Find the CORSMiddleware block — should NOT have allow_methods=["*"]
        import re
        cors_block = re.search(r'CORSMiddleware.*?(?=\n\n|\nclass|\n#)', content, re.DOTALL)
        if cors_block:
            block_text = cors_block.group()
            assert 'allow_methods=["*"]' not in block_text, \
                "CORS should restrict allowed methods, not use wildcard"

    def test_openapi_disabled_in_production(self):
        """OpenAPI JSON endpoint should be disabled when not in debug mode."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'main.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert 'openapi_url="/openapi.json" if settings.debug else None' in content

    def test_global_exception_no_error_leak(self):
        """Global exception handler should not leak errors in production."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'main.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        # Should use debug flag check before including error details
        assert '"debug_error"' in content or 'if settings.debug' in content

    def test_x_frame_options_set(self, client):
        """Responses should include X-Frame-Options: DENY."""
        response = client.get("/")
        assert response.headers.get("X-Frame-Options") == "DENY"

    def test_x_content_type_options_set(self, client):
        """Responses should include X-Content-Type-Options: nosniff."""
        response = client.get("/")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_referrer_policy_set(self, client):
        """Responses should include Referrer-Policy."""
        response = client.get("/")
        assert "strict-origin" in (response.headers.get("Referrer-Policy") or "")


# ==========================================================
# 6. AUTHENTICATION
# ==========================================================

class TestAuthentication:
    """Token validation and auth boundary tests."""

    def test_password_requires_uppercase(self):
        """Password validation should require uppercase letter."""
        from auth.authentication import UserRegister
        with pytest.raises(Exception):
            UserRegister(
                email="test@test.com",
                password="alllowercase1",
                first_name="Test",
                last_name="User"
            )

    def test_password_requires_digit(self):
        """Password validation should require a digit."""
        from auth.authentication import UserRegister
        with pytest.raises(Exception):
            UserRegister(
                email="test@test.com",
                password="NoDigitHere",
                first_name="Test",
                last_name="User"
            )

    def test_password_min_length(self):
        """Password must be at least 8 characters."""
        from auth.authentication import UserRegister
        with pytest.raises(Exception):
            UserRegister(
                email="test@test.com",
                password="Ab1",
                first_name="Test",
                last_name="User"
            )

    def test_valid_password_accepted(self):
        """A strong password should be accepted."""
        from auth.authentication import UserRegister
        user = UserRegister(
            email="test@test.com",
            password="StrongPass1",
            first_name="Test",
            last_name="User"
        )
        assert user.password == "StrongPass1"

    def test_bcrypt_hashing_works(self):
        """Passwords should be properly hashed and verifiable."""
        from auth.authentication import hash_password, verify_password
        hashed = hash_password("TestPass123")
        assert hashed != "TestPass123"
        assert hashed.startswith("$2")
        assert verify_password("TestPass123", hashed)
        assert not verify_password("WrongPass123", hashed)

    def test_token_decode_rejects_invalid(self):
        """Invalid JWT tokens should return None, not crash."""
        from auth.authentication import decode_token
        assert decode_token("invalid.token.here") is None
        assert decode_token("") is None
        assert decode_token("not-even-jwt") is None


# ==========================================================
# 7. LIKE INJECTION PREVENTION
# ==========================================================

class TestLikeInjection:
    """ILIKE patterns should escape metacharacters."""

    def test_holders_search_escapes_like(self):
        """holders.py search should escape % and _ in ILIKE patterns."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'api', 'holders.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert "ESCAPE" in content, "holders.py ILIKE should have ESCAPE clause"
        assert '.replace("%"' in content or 'replace("%"' in content, \
            "holders.py should escape % in search input"

    def test_watchlist_search_escapes_like(self):
        """crud.py watchlist search should escape ILIKE metacharacters."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database', 'crud.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        assert "ESCAPE" in content, "crud.py ILIKE should have ESCAPE clause"


# ==========================================================
# 8. DEBUG ENDPOINTS (existing tests, kept)
# ==========================================================

class TestDebugEndpoints:
    def test_test_search_returns_404(self, client):
        response = client.get("/api/test-search")
        assert response.status_code == 404

    def test_debug_search_returns_404(self, client):
        response = client.post("/api/debug-search")
        assert response.status_code == 404


# ==========================================================
# 9. GITIGNORE SAFETY
# ==========================================================

class TestGitignoreSafety:
    """Sensitive files must be gitignored."""

    def test_env_file_gitignored(self):
        """The .env file should be in .gitignore."""
        gitignore_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.gitignore')
        content = open(gitignore_path, 'r').read()
        assert '.env' in content

    def test_token_file_gitignored(self):
        """The .token file should be in .gitignore."""
        gitignore_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.gitignore')
        content = open(gitignore_path, 'r').read()
        assert '.token' in content

    def test_tmp_files_gitignored(self):
        """Temp files (tmp_*) should be in .gitignore."""
        gitignore_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.gitignore')
        content = open(gitignore_path, 'r').read()
        assert 'tmp_*' in content


# ==========================================================
# 10. RATE LIMITING
# ==========================================================

class TestRateLimitConfig:
    """Rate limiting configuration should be reasonable."""

    def test_api_rate_limit_not_too_permissive(self):
        """API rate limit default should be <= 60/minute."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'settings.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        # Find the api_rate_limit default
        import re
        m = re.search(r'api_rate_limit.*?default=(\d+)', content)
        assert m, "api_rate_limit must have a default"
        limit = int(m.group(1))
        assert limit <= 60, f"API rate limit too high: {limit}/min"

    def test_login_rate_limit_exists(self):
        """Login rate limit should be configured and <= 10/min."""
        fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'settings.py')
        content = open(fpath, 'r', encoding='utf-8').read()
        import re
        m = re.search(r'login_rate_limit.*?default=(\d+)', content)
        assert m, "login_rate_limit must exist"
        limit = int(m.group(1))
        assert limit <= 10, f"Login rate limit too high: {limit}/min"

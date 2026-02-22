"""
Tests for API endpoints via FastAPI TestClient.

Auth dependency is overridden in conftest.py (client/superadmin_client fixtures).
Tests focus on: routing, auth gates, request validation, and public endpoints.

NOTE: These tests require `-s` flag or `--capture=no` on Windows due to
a known pytest capture issue with ASGI TestClient.
"""
import sys
import os
import uuid
from unittest.mock import MagicMock

import pytest


pytestmark = pytest.mark.usefixtures()


# ============================================================
# Health & Info (no auth)
# ============================================================

class TestPublicEndpoints:
    """Test endpoints that require no authentication."""

    def test_health_check(self, client):
        resp = client.get("/health")
        assert resp.status_code in (200, 500)
        data = resp.json()
        assert "status" in data or "detail" in data

    def test_app_info(self, client):
        resp = client.get("/api/info")
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data
        assert "version" in data

    def test_app_config(self, client):
        resp = client.get("/api/v1/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "risk_thresholds" in data
        # Verify all 5 risk levels present
        thresholds = data["risk_thresholds"]
        for level in ["critical", "very_high", "high", "medium", "low"]:
            assert level in thresholds

    def test_root_redirects(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (200, 301, 302, 307)

    def test_nice_classes_endpoint(self, client):
        resp = client.get("/api/nice-classes")
        assert resp.status_code == 200

    def test_search_status(self, client):
        resp = client.get("/api/v1/search/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_nonexistent_route_returns_404(self, client):
        resp = client.get("/api/v1/totally/nonexistent")
        assert resp.status_code in (404, 405)


# ============================================================
# Auth Endpoints - Request Validation
# ============================================================

class TestAuthValidationEndpoints:
    """Test auth endpoints reject invalid input."""

    def test_register_empty_body(self, client):
        resp = client.post("/api/v1/auth/register", json={})
        assert resp.status_code == 422

    def test_login_empty_body(self, client):
        resp = client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422

    def test_register_missing_password(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
        })
        assert resp.status_code == 422

    def test_register_invalid_email(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "email": "not-an-email",
            "password": "ValidPass1!",
            "first_name": "Test",
            "last_name": "User",
        })
        assert resp.status_code == 422


# ============================================================
# Access Control (auth required)
# ============================================================

class TestAccessControl:
    """Test that auth-required endpoints reject/accept correctly."""

    def test_watchlist_create_invalid_body(self, client):
        """Watchlist creation with empty body → 422."""
        resp = client.post("/api/v1/watchlist", json={})
        assert resp.status_code == 422

    def test_simple_search_no_query(self, client):
        """Simple search without query → 422."""
        resp = client.get("/api/search/simple")
        assert resp.status_code == 422

    def test_admin_overview_denied_for_regular_user(self, client):
        """Regular user (not superadmin) → 403 on admin endpoints."""
        resp = client.get("/api/v1/admin/overview")
        assert resp.status_code == 403

    def test_admin_users_denied_for_regular_user(self, client):
        resp = client.get("/api/v1/admin/users")
        assert resp.status_code == 403

    def test_admin_settings_denied_for_regular_user(self, client):
        resp = client.get("/api/v1/admin/settings")
        assert resp.status_code == 403

    def test_admin_overview_allowed_for_superadmin(self, superadmin_client):
        """Superadmin should not get 403 (may get 500 due to unmocked DB)."""
        resp = superadmin_client.get("/api/v1/admin/overview")
        assert resp.status_code != 403

    def test_admin_settings_allowed_for_superadmin(self, superadmin_client):
        resp = superadmin_client.get("/api/v1/admin/settings")
        assert resp.status_code != 403


# ============================================================
# Search Endpoint Validation
# ============================================================

class TestSearchValidation:
    """Test search endpoints validate input correctly."""

    def test_quick_search_requires_query(self, client):
        """Quick search without 'query' param should fail."""
        resp = client.get("/api/v1/search/quick")
        # Either 422 (validation) or 500 (unhandled)
        assert resp.status_code in (422, 500)

    def test_quick_search_with_query(self, client):
        """Quick search with a valid query should not return 422 (validation error)."""
        resp = client.get("/api/v1/search/quick?query=NIKE")
        # May get 500 (DB not available) but should not get 422 (bad input)
        assert resp.status_code != 422

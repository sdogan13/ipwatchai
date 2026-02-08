"""
Security audit regression tests -- STUBS.
Run with: pytest tests/test_security_audit.py -v
Requires: running server + database connection.
"""
import pytest


class TestDebugEndpoints:
    async def test_test_search_returns_404(self, client):
        response = await client.get("/api/test-search")
        assert response.status_code == 404

    async def test_debug_search_returns_404(self, client):
        response = await client.post("/api/debug-search")
        assert response.status_code == 404


class TestAuthentication:
    async def test_deactivated_user_rejected(self, client, deactivated_user_token):
        """Deactivated user's valid JWT should return 401."""
        pass  # TODO: implement when server available

    async def test_deactivated_org_rejected(self, client, deactivated_org_user_token):
        """User from deactivated org should return 403."""
        pass  # TODO: implement when server available

    async def test_access_token_type_verified(self, client, refresh_token):
        """Using a refresh token as Authorization header should fail."""
        pass  # TODO: implement when server available


class TestRefreshToken:
    async def test_refresh_with_valid_refresh_token(self, client, refresh_token):
        response = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert response.status_code == 200
        assert "access_token" in response.json()

    async def test_refresh_with_access_token_fails(self, client, access_token):
        response = await client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
        assert response.status_code == 401


class TestRateLimiting:
    async def test_login_rate_limited(self, client):
        """6th login attempt within a minute should return 429."""
        pass  # TODO: implement when server available

    async def test_rate_limit_response_logged(self, client):
        """Rate limit hit should be logged with user/IP info."""
        pass  # TODO: implement when server available


class TestFreePlanLimits:
    async def test_watchlist_capped_at_5(self, client, free_user_headers):
        pass  # TODO: implement when server available

    async def test_name_gen_monthly_cap(self, client, free_user_headers):
        pass  # TODO: implement when server available

    async def test_quick_search_daily_cap(self, client, free_user_headers):
        pass  # TODO: implement when server available

    async def test_holder_portfolio_blocked(self, client, free_user_headers):
        pass  # TODO: implement when server available

    async def test_csv_export_blocked(self, client, free_user_headers):
        pass  # TODO: implement when server available

    async def test_live_search_blocked(self, client, free_user_headers):
        pass  # TODO: implement when server available


class TestAdminEndpoints:
    async def test_idf_stats_requires_admin(self, client, regular_user_headers):
        """Regular user should get 403 on admin IDF endpoints."""
        pass  # TODO: implement when server available

    async def test_test_scoring_requires_admin(self, client, regular_user_headers):
        """Regular user should get 403 on test-scoring."""
        pass  # TODO: implement when server available


class TestUsageSummary:
    async def test_returns_correct_structure(self, client, free_user_headers):
        pass  # TODO: implement when server available

    async def test_requires_auth(self, client):
        """Unauthenticated request should return 401/403."""
        pass  # TODO: implement when server available

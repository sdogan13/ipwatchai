"""Per-registry search endpoints share the unified daily Agentic Search quota.

After Quick Search removal, ``/api/v1/{design,patent,cografi,registry}-search``
all consume from the same daily counter — ``api_usage.live_searches`` — via
``check_live_search_eligibility`` and ``increment_live_search_usage``.

This test fixes two things:

  1. Per-registry routes expose the bare URL (``/design-search``, etc.). The
     old ``/{registry}-search/quick`` paths are gone — 404.
  2. The eligibility gate on each registry route returns **429** for
     ``daily_limit_exceeded`` (per the route handler's choice; the trademark
     route uses 402 — covered in ``test_search_routes.py``). This asymmetry
     is documented behavior, not a bug to "fix" here.

Each call is mocked at the eligibility-helper level — no DB, no AI models.
"""
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import patch, MagicMock


@contextmanager
def _mock_db():
    """Stand-in for ``with Database() as db:`` that yields a Mock without
    touching real Postgres."""
    yield MagicMock()


REGISTRIES = [
    ("design-search", "app_design_search_routes"),
    ("patent-search", "app_patent_search_routes"),
    ("cografi-search", "app_cografi_search_routes"),
    ("registry-search", "app_registry_search_routes"),
]


# ===========================================================================
# /quick routes are gone for every registry
# ===========================================================================

class TestRegistryQuickEndpointsRemoved:
    def test_design_quick_returns_404(self, client):
        resp = client.post("/api/v1/design-search/quick", data={"query": "lamp"})
        assert resp.status_code == 404

    def test_patent_quick_returns_404(self, client):
        resp = client.post("/api/v1/patent-search/quick", data={"query": "battery"})
        assert resp.status_code == 404

    def test_cografi_quick_returns_404(self, client):
        resp = client.post("/api/v1/cografi-search/quick", data={"query": "antep"})
        assert resp.status_code == 404

    def test_registry_quick_returns_404(self, client):
        resp = client.post("/api/v1/registry-search/quick", data={"query": "test"})
        assert resp.status_code == 404


# ===========================================================================
# Bare per-registry routes exist (post-removal canonical URLs)
# ===========================================================================

class TestRegistryBareRoutesExist:
    def test_design_search_bare_route_exists(self, client):
        """POST /api/v1/design-search exists. Empty body yields 422, not 404."""
        resp = client.post("/api/v1/design-search")
        assert resp.status_code != 404

    def test_patent_search_bare_route_exists(self, client):
        resp = client.post("/api/v1/patent-search")
        assert resp.status_code != 404

    def test_cografi_search_bare_route_exists(self, client):
        resp = client.post("/api/v1/cografi-search")
        assert resp.status_code != 404

    def test_registry_search_bare_route_exists(self, client):
        resp = client.post("/api/v1/registry-search")
        assert resp.status_code != 404


# ===========================================================================
# Shared daily quota: 429 from each registry when limit exceeded
# ===========================================================================

OVER_LIMIT_DETAILS = {
    "error": "daily_limit_exceeded",
    "current_plan": "free",
    "display_name": "Free Trial",
    "daily_limit": 5,
    "used_today": 5,
    "remaining": 0,
    "message": "TR",
    "message_en": "EN",
}


class TestSharedDailyQuotaGate:
    """Each per-registry route invokes ``check_live_search_eligibility`` and
    returns 429 with the upgrade-hint payload when over-limit. This proves the
    quota is shared — there's one counter, not four.

    ``database.crud.Database`` is mocked at the import path the inline
    ``from database.crud import Database`` resolves to inside each route.
    """

    @patch("database.crud.Database", side_effect=lambda: _mock_db())
    @patch("utils.subscription.check_live_search_eligibility")
    def test_design_returns_429_when_quota_exhausted(self, mock_check, _mock_db_class, client):
        mock_check.return_value = (False, "daily_limit_exceeded", OVER_LIMIT_DETAILS)
        resp = client.post(
            "/api/v1/design-search",
            data={"query": "lamp"},
        )
        assert resp.status_code == 429
        assert resp.json()["detail"]["error"] == "daily_limit_exceeded"

    @patch("database.crud.Database", side_effect=lambda: _mock_db())
    @patch("utils.subscription.check_live_search_eligibility")
    def test_patent_returns_429_when_quota_exhausted(self, mock_check, _mock_db_class, client):
        mock_check.return_value = (False, "daily_limit_exceeded", OVER_LIMIT_DETAILS)
        resp = client.post(
            "/api/v1/patent-search",
            data={"query": "battery"},
        )
        assert resp.status_code == 429
        assert resp.json()["detail"]["error"] == "daily_limit_exceeded"

    @patch("database.crud.Database", side_effect=lambda: _mock_db())
    @patch("utils.subscription.check_live_search_eligibility")
    def test_cografi_returns_429_when_quota_exhausted(self, mock_check, _mock_db_class, client):
        mock_check.return_value = (False, "daily_limit_exceeded", OVER_LIMIT_DETAILS)
        resp = client.post(
            "/api/v1/cografi-search",
            data={"query": "antep"},
        )
        assert resp.status_code == 429
        assert resp.json()["detail"]["error"] == "daily_limit_exceeded"

    @patch("database.crud.Database", side_effect=lambda: _mock_db())
    @patch("utils.subscription.check_live_search_eligibility")
    def test_registry_returns_429_when_quota_exhausted(self, mock_check, _mock_db_class, client):
        mock_check.return_value = (False, "daily_limit_exceeded", OVER_LIMIT_DETAILS)
        resp = client.post(
            "/api/v1/registry-search",
            data={"query": "test"},
        )
        assert resp.status_code == 429
        assert resp.json()["detail"]["error"] == "daily_limit_exceeded"

    @patch("database.crud.Database", side_effect=lambda: _mock_db())
    @patch("utils.subscription.check_live_search_eligibility")
    def test_429_payload_carries_bilingual_upgrade_hint(self, mock_check, _mock_db_class, client):
        """Frontend upgrade modal needs both `message` (Turkish) and `message_en`."""
        mock_check.return_value = (False, "daily_limit_exceeded", OVER_LIMIT_DETAILS)
        resp = client.post("/api/v1/design-search", data={"query": "lamp"})
        body = resp.json()["detail"]
        assert "message" in body
        assert "message_en" in body
        assert body["remaining"] == 0
        assert body["daily_limit"] == 5

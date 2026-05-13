"""HTTP-level contract for ``/api/v1/search`` after Quick Search removal.

Validates the route surface that's actually live on `main`:

  * ``GET /api/v1/search?query=...`` exists and is the canonical Agentic Search
    endpoint (replaces the old ``/intelligent``).
  * ``GET /api/v1/search/quick`` is gone — must return 404, not just 422 or 500.
  * Eligibility gate: trademark route returns **402** when the daily limit is
    exceeded (this is the legacy "payment-required" status code the route
    chose; per-registry routes return 429 — covered in
    ``test_registry_search_shared_quota.py``).
  * The eligibility helper is called *before* the heavy searcher runs, so an
    over-quota call never instantiates ``AgenticTrademarkSearch``.

The heavy ``AgenticTrademarkSearch`` class (CLIP + DINOv2 + scraper) is mocked
out via ``_run_search_sync`` so this test stays fast and offline.
"""
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

import pytest


@contextmanager
def _mock_db():
    """Stand-in for ``with Database() as db:`` that yields a Mock without
    touching real Postgres."""
    yield MagicMock()


# ===========================================================================
# Quick endpoint no longer exists
# ===========================================================================

class TestQuickEndpointRemoved:
    def test_get_quick_returns_404(self, client):
        """The legacy ``GET /api/v1/search/quick`` route is fully removed.

        404 means "route does not exist" — distinct from 422 (validation),
        429 (rate limit), or 5xx (server error). A 404 confirms the FastAPI
        router has no handler registered at this path.
        """
        resp = client.get("/api/v1/search/quick?query=NIKE")
        assert resp.status_code == 404

    def test_post_quick_returns_404(self, client):
        resp = client.post("/api/v1/search/quick", data={"query": "NIKE"})
        assert resp.status_code == 404

    def test_intelligent_endpoint_also_removed(self, client):
        """``/intelligent`` was the prior bare-Agentic path; collapsed to ``/search``."""
        resp = client.get("/api/v1/search/intelligent?query=NIKE")
        assert resp.status_code == 404


# ===========================================================================
# Bare /api/v1/search is the canonical Agentic Search endpoint
# ===========================================================================

class TestBareSearchExists:
    def test_get_without_query_is_validation_error(self, client):
        """Route exists; missing required `query` param yields 422 (not 404)."""
        resp = client.get("/api/v1/search")
        assert resp.status_code == 422

    def test_get_with_query_is_not_a_validation_error(self, client):
        """Route exists and accepts `query` — gets further than input validation.
        DB/eligibility/searcher dependencies will likely 5xx without mocks; the
        point of this test is just to prove the route is wired.
        """
        resp = client.get("/api/v1/search?query=NIKE")
        assert resp.status_code != 422
        assert resp.status_code != 404


# ===========================================================================
# Eligibility gate: daily_limit_exceeded → 402 (trademark route)
# ===========================================================================

class TestDailyLimitGate:
    @patch("agentic_search._run_search_sync")
    @patch("agentic_search.Database", side_effect=lambda: _mock_db())
    @patch("agentic_search.is_feature_enabled", return_value=True)
    @patch("agentic_search.check_live_search_eligibility")
    def test_402_when_daily_limit_reached(self, mock_eligibility, _mock_flag, _mock_db_class, mock_run, client):
        """Free user who has consumed today's 5 searches gets 402, no scraper run."""
        mock_eligibility.return_value = (
            False,
            "daily_limit_exceeded",
            {
                "error": "daily_limit_exceeded",
                "current_plan": "free",
                "display_name": "Free Trial",
                "daily_limit": 5,
                "used_today": 5,
                "remaining": 0,
                "message": "Gunluk 5 Agentic Search hakkinizin tamamini kullandiniz.",
                "message_en": "You've used all 5 Agentic Searches today.",
            },
        )

        resp = client.get("/api/v1/search?query=NIKE")

        # Trademark route maps daily_limit_exceeded -> 402 Payment Required
        # (per agentic_search.py:1259-1261).
        assert resp.status_code == 402
        body = resp.json()
        assert body["detail"]["error"] == "daily_limit_exceeded"
        assert body["detail"]["remaining"] == 0
        assert body["detail"]["daily_limit"] == 5
        # Searcher must NOT have been instantiated for an over-limit user.
        mock_run.assert_not_called()

    @patch("agentic_search._run_search_sync")
    @patch("agentic_search.Database", side_effect=lambda: _mock_db())
    @patch("agentic_search.is_feature_enabled", return_value=True)
    @patch("agentic_search.check_live_search_eligibility")
    def test_eligibility_failure_payload_is_bilingual(self, mock_eligibility, _mock_flag, _mock_db_class, _mock_run, client):
        """Frontend upgrade modal expects both message + message_en in the 402 payload."""
        mock_eligibility.return_value = (
            False,
            "daily_limit_exceeded",
            {
                "error": "daily_limit_exceeded",
                "current_plan": "free",
                "display_name": "Free Trial",
                "daily_limit": 5,
                "used_today": 5,
                "remaining": 0,
                "message": "Turkish",
                "message_en": "English",
            },
        )

        resp = client.get("/api/v1/search?query=NIKE")
        body = resp.json()["detail"]
        assert "message" in body
        assert "message_en" in body


# ===========================================================================
# Live-scraping kill switch
# ===========================================================================

class TestKillSwitch:
    @patch("agentic_search.is_feature_enabled", return_value=False)
    def test_503_when_live_scraping_disabled(self, _mock_flag, client):
        """`feature.live_scraping_enabled = False` short-circuits with 503."""
        resp = client.get("/api/v1/search?query=NIKE")
        assert resp.status_code == 503

"""
Tests for the SettingsManager singleton (in-memory cache with TTL).

All DB calls are mocked — tests verify cache logic, TTL, and invalidation.
"""
import sys
import os
import time
from unittest.mock import patch, MagicMock

import pytest


from utils.settings_manager import SettingsManager, get_rate_limit_value


# ============================================================
# SettingsManager Cache Logic
# ============================================================

class TestSettingsManagerCache:
    """Test the in-memory cache behavior."""

    def test_not_initialized_returns_default(self):
        sm = SettingsManager()
        assert sm.get("anything", default=42) == 42

    def test_not_initialized_exists_returns_false(self):
        sm = SettingsManager()
        assert sm.exists("anything") is False

    def test_not_initialized_get_category_returns_empty(self):
        sm = SettingsManager()
        assert sm.get_category("any") == {}

    def test_not_initialized_get_all_returns_empty(self):
        sm = SettingsManager()
        assert sm.get_all() == []

    def test_cache_returns_stored_value(self):
        sm = SettingsManager()
        sm._initialized = True
        sm._cache = {"key1": "value1"}
        sm._cache_timestamp = time.time()
        assert sm.get("key1") == "value1"

    def test_cache_miss_returns_default(self):
        sm = SettingsManager()
        sm._initialized = True
        sm._cache = {"key1": "value1"}
        sm._cache_timestamp = time.time()
        assert sm.get("missing", default="fallback") == "fallback"

    def test_exists_returns_true_for_cached_key(self):
        sm = SettingsManager()
        sm._initialized = True
        sm._cache = {"key1": "value1"}
        sm._cache_timestamp = time.time()
        assert sm.exists("key1") is True

    def test_exists_returns_false_for_missing_key(self):
        sm = SettingsManager()
        sm._initialized = True
        sm._cache = {"key1": "value1"}
        sm._cache_timestamp = time.time()
        assert sm.exists("missing") is False

    def test_cache_is_valid_within_ttl(self):
        sm = SettingsManager(cache_ttl_seconds=60)
        sm._cache_timestamp = time.time()
        assert sm._cache_is_valid() is True

    def test_cache_is_invalid_after_ttl(self):
        sm = SettingsManager(cache_ttl_seconds=60)
        sm._cache_timestamp = time.time() - 61
        assert sm._cache_is_valid() is False

    def test_invalidate_cache_resets_timestamp(self):
        sm = SettingsManager()
        sm._cache_timestamp = time.time()
        sm.invalidate_cache()
        assert sm._cache_timestamp == 0

    def test_default_ttl_is_60(self):
        sm = SettingsManager()
        assert sm._cache_ttl == 60

    def test_custom_ttl(self):
        sm = SettingsManager(cache_ttl_seconds=120)
        assert sm._cache_ttl == 120


# ============================================================
# Cache refresh with mocked DB
# ============================================================

class TestSettingsManagerRefresh:
    """Test cache refresh logic with mocked DB connection."""

    @patch("utils.settings_manager.SettingsManager._get_connection")
    def test_refresh_populates_cache(self, mock_conn_fn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            ("key1", "val1"),
            ("key2", "val2"),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn_fn.return_value = mock_conn

        sm = SettingsManager()
        sm._initialized = True
        sm._refresh_cache()

        assert sm._cache == {"key1": "val1", "key2": "val2"}
        assert sm._cache_timestamp > 0

    @patch("utils.settings_manager.SettingsManager._get_connection")
    def test_refresh_error_keeps_stale_cache(self, mock_conn_fn):
        mock_conn_fn.side_effect = Exception("DB down")

        sm = SettingsManager()
        sm._initialized = True
        sm._cache = {"old": "data"}
        sm._refresh_cache()

        assert sm._cache == {"old": "data"}  # Kept stale

    def test_refresh_skips_when_not_initialized(self):
        sm = SettingsManager()
        sm._refresh_cache()  # Should not crash
        assert sm._cache == {}


# ============================================================
# get_rate_limit_value
# ============================================================

class TestGetRateLimitValue:
    """Test the rate limit value helper."""

    def test_returns_default_when_no_override(self):
        # settings_manager.get returns None (autouse mock)
        result = get_rate_limit_value("rate_limit.login", "5/minute")
        assert result == "5/minute"

    @patch("utils.settings_manager.settings_manager")
    def test_returns_db_value_formatted(self, mock_sm):
        mock_sm.get.return_value = 10
        result = get_rate_limit_value("rate_limit.login", "5/minute")
        assert result == "10/minute"

    @patch("utils.settings_manager.settings_manager")
    def test_returns_default_when_none(self, mock_sm):
        mock_sm.get.return_value = None
        result = get_rate_limit_value("rate_limit.login", "5/minute")
        assert result == "5/minute"

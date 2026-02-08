"""
Runtime settings manager with in-memory cache.

Settings are stored in the app_settings DB table and cached in memory with a 60-second TTL.
On write, the cache is immediately invalidated so changes take effect instantly.

Usage:
    from utils.settings_manager import settings_manager

    # Read (cached, fast)
    limit = settings_manager.get("plan.free.max_watchlist_items", default=5)

    # Write (DB + invalidate cache)
    settings_manager.set("plan.free.max_watchlist_items", 10, updated_by=user_id)

    # Read entire category
    plan_limits = settings_manager.get_category("plan_limits")

    # Check if a setting exists in DB (vs using code default)
    has_override = settings_manager.exists("plan.free.max_watchlist_items")
"""

import time
import json
import logging
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)


class SettingsManager:
    def __init__(self, cache_ttl_seconds: int = 60):
        self._cache: Dict[str, Any] = {}
        self._cache_timestamp: float = 0
        self._cache_ttl = cache_ttl_seconds
        self._initialized = False

    def init(self):
        """Mark as initialized. Call during app startup after migrations."""
        self._initialized = True
        logger.info("SettingsManager initialized")

    def _get_connection(self):
        from database.crud import get_db_connection
        return get_db_connection()

    def _cache_is_valid(self) -> bool:
        return (time.time() - self._cache_timestamp) < self._cache_ttl

    def _refresh_cache(self):
        """Load all settings from DB into memory."""
        if not self._initialized:
            return

        conn = None
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM app_settings")
            rows = cur.fetchall()
            self._cache = {row[0]: row[1] for row in rows}
            self._cache_timestamp = time.time()
            cur.close()
            logger.debug(f"Settings cache refreshed: {len(self._cache)} entries")
        except Exception as e:
            logger.error(f"Failed to refresh settings cache: {e}")
            # Keep stale cache rather than failing
        finally:
            if conn:
                conn.close()

    def invalidate_cache(self):
        """Force cache refresh on next read. Call after any write."""
        self._cache_timestamp = 0

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value. Returns default if not found in DB."""
        if not self._initialized:
            return default

        if not self._cache_is_valid():
            self._refresh_cache()

        if key in self._cache:
            return self._cache[key]
        return default

    def get_category(self, category: str) -> Dict[str, Any]:
        """Get all settings in a category."""
        if not self._initialized:
            return {}

        conn = None
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT key, value, description, value_type "
                "FROM app_settings WHERE category = %s",
                (category,),
            )
            rows = cur.fetchall()
            cur.close()
            return {
                row[0]: {"value": row[1], "description": row[2], "type": row[3]}
                for row in rows
            }
        except Exception as e:
            logger.error(f"Failed to get category {category}: {e}")
            return {}
        finally:
            if conn:
                conn.close()

    def set(
        self,
        key: str,
        value: Any,
        category: str = "general",
        description: str = None,
        value_type: str = "string",
        updated_by: str = None,
        conn=None,
    ):
        """Set a setting value. Upserts into DB and invalidates cache."""
        own_conn = conn is None
        try:
            if own_conn:
                conn = self._get_connection()
            cur = conn.cursor()
            json_value = json.dumps(value)
            cur.execute(
                """
                INSERT INTO app_settings (key, value, category, description, value_type, updated_at, updated_by)
                VALUES (%s, %s::jsonb, %s, %s, %s, NOW(), %s)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = NOW(),
                    updated_by = EXCLUDED.updated_by
                """,
                (key, json_value, category, description, value_type, updated_by),
            )
            if own_conn:
                conn.commit()
            cur.close()
        except Exception as e:
            if own_conn and conn:
                conn.rollback()
            logger.error(f"Failed to set setting {key}: {e}")
            raise
        finally:
            if own_conn and conn:
                conn.close()

        self.invalidate_cache()
        logger.info(f"Setting updated: {key} = {value} (by {updated_by})")

    def delete(self, key: str, conn=None):
        """Delete a setting (revert to code default)."""
        own_conn = conn is None
        try:
            if own_conn:
                conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM app_settings WHERE key = %s", (key,))
            if own_conn:
                conn.commit()
            cur.close()
        except Exception as e:
            if own_conn and conn:
                conn.rollback()
            logger.error(f"Failed to delete setting {key}: {e}")
            raise
        finally:
            if own_conn and conn:
                conn.close()

        self.invalidate_cache()

    def exists(self, key: str) -> bool:
        if not self._initialized:
            return False
        if not self._cache_is_valid():
            self._refresh_cache()
        return key in self._cache

    def get_all(self) -> List[Dict[str, Any]]:
        """Get all settings (for admin panel)."""
        if not self._initialized:
            return []

        conn = None
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT key, value, category, description, value_type, "
                "updated_at, updated_by "
                "FROM app_settings ORDER BY category, key"
            )
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "key": row[0],
                    "value": row[1],
                    "category": row[2],
                    "description": row[3],
                    "type": row[4],
                    "updated_at": str(row[5]) if row[5] else None,
                    "updated_by": str(row[6]) if row[6] else None,
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Failed to get all settings: {e}")
            return []
        finally:
            if conn:
                conn.close()


# Singleton instance
settings_manager = SettingsManager()

"""
Seed app_settings with default values from PLAN_FEATURES, rate limits, and feature flags.
Idempotent — uses ON CONFLICT DO NOTHING so it won't overwrite admin changes.
"""
from __future__ import annotations

import json
import logging

from database.crud import get_db_connection

logger = logging.getLogger(__name__)

LEGACY_QUICK_SEARCH_LIMITS = {
    "free": 50,
    "starter": 200,
    "professional": 500,
    "business": 150,
}

PAID_CSV_FEATURES = {
    "can_download_portfolio": {
        "free": False,
        "starter": True,
        "professional": True,
        "business": True,
        "enterprise": True,
        "superadmin": True,
    },
    "can_export_csv_leads": {
        "free": False,
        "starter": True,
        "professional": True,
        "business": True,
        "enterprise": True,
        "superadmin": True,
    },
}


def seed_default_settings():
    """Seed app_settings with current PLAN_FEATURES values. Idempotent."""
    from utils.subscription import PLAN_FEATURES

    settings_to_seed = []

    # Plan limits
    for plan_name, features in PLAN_FEATURES.items():
        for feature, value in features.items():
            if isinstance(value, bool):
                vtype = "boolean"
            elif isinstance(value, int):
                vtype = "integer"
            elif isinstance(value, float):
                vtype = "float"
            elif value is None:
                vtype = "string"
            else:
                vtype = "string"

            settings_to_seed.append({
                "key": f"plan.{plan_name}.{feature}",
                "value": value,
                "category": "plan_limits",
                "description": f"{plan_name} plan: {feature}",
                "value_type": vtype,
            })

    # Rate limits
    rate_limits = {
        "rate_limit.login": {"value": 5, "desc": "Login attempts per minute per IP"},
        "rate_limit.register": {"value": 5, "desc": "Registration attempts per minute per IP"},
        "rate_limit.quick_search": {"value": 60, "desc": "Quick searches per minute per user"},
        "rate_limit.intelligent_search": {"value": 10, "desc": "Intelligent searches per minute per user"},
        "rate_limit.api_general": {"value": 100, "desc": "General API calls per minute per user"},
        "rate_limit.public_search": {"value": 10, "desc": "Public search per minute per IP"},
    }
    for key, info in rate_limits.items():
        settings_to_seed.append({
            "key": key,
            "value": info["value"],
            "category": "rate_limits",
            "description": info["desc"],
            "value_type": "integer",
        })

    # Feature flags
    feature_flags = {
        "feature.live_scraping_enabled": {"value": True, "desc": "Enable live scraping for eligible plans"},
        "feature.ai_studio_enabled": {"value": True, "desc": "Enable AI Studio (Name Lab + Logo Studio)"},
        "feature.opposition_radar_enabled": {"value": True, "desc": "Enable Opposition Radar leads"},
        "feature.auto_scan_enabled": {"value": True, "desc": "Enable automatic watchlist scanning"},
        "feature.public_search_enabled": {"value": True, "desc": "Enable unauthenticated public search"},
    }
    for key, info in feature_flags.items():
        settings_to_seed.append({
            "key": key,
            "value": info["value"],
            "category": "features",
            "description": info["desc"],
            "value_type": "boolean",
        })

    # Seed into DB
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        for s in settings_to_seed:
            cur.execute(
                """
                INSERT INTO app_settings (key, value, category, description, value_type, updated_at)
                VALUES (%s, %s::jsonb, %s, %s, %s, NOW())
                ON CONFLICT (key) DO NOTHING
                """,
                (
                    s["key"],
                    json.dumps(s["value"]),
                    s["category"],
                    s["description"],
                    s["value_type"],
                ),
            )
        conn.commit()
        cur.close()
        logger.info(f"Seeded {len(settings_to_seed)} default settings (ON CONFLICT DO NOTHING)")
    except Exception as e:
        logger.warning(f"Settings seed failed (non-fatal): {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def align_legacy_quick_search_limits():
    """
    Rewrite only the known stale quick-search limit overrides.

    This keeps intentional admin overrides intact while bringing older
    runtime settings back to the current product defaults.
    """
    from utils.settings_manager import settings_manager
    from utils.subscription import PLAN_FEATURES

    target_limits = {
        "free": PLAN_FEATURES["free"]["max_daily_quick_searches"],
        "starter": PLAN_FEATURES["starter"]["max_daily_quick_searches"],
        "professional": PLAN_FEATURES["professional"]["max_daily_quick_searches"],
        "business": PLAN_FEATURES["professional"]["max_daily_quick_searches"],
    }

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        updated = 0

        for plan_name, legacy_value in LEGACY_QUICK_SEARCH_LIMITS.items():
            cur.execute(
                """
                UPDATE app_settings
                SET value = %s::jsonb,
                    updated_at = NOW()
                WHERE key = %s
                  AND value = %s::jsonb
                """,
                (
                    json.dumps(target_limits[plan_name]),
                    f"plan.{plan_name}.max_daily_quick_searches",
                    json.dumps(legacy_value),
                ),
            )
            updated += cur.rowcount or 0

        conn.commit()
        if updated:
            settings_manager.invalidate_cache()
            logger.info(f"Aligned {updated} legacy quick-search plan override(s)")
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logger.warning(f"Quick-search limit alignment failed (non-fatal): {e}")
        return False
    finally:
        if conn:
            conn.close()


def align_paid_csv_feature_limits():
    """Force CSV export/download flags to the current product policy."""
    from utils.settings_manager import settings_manager

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        updated = 0

        for feature, plan_values in PAID_CSV_FEATURES.items():
            for plan_name, enabled in plan_values.items():
                cur.execute(
                    """
                    UPDATE app_settings
                    SET value = %s::jsonb,
                        updated_at = NOW()
                    WHERE key = %s
                      AND value IS DISTINCT FROM %s::jsonb
                    """,
                    (
                        json.dumps(enabled),
                        f"plan.{plan_name}.{feature}",
                        json.dumps(enabled),
                    ),
                )
                updated += cur.rowcount or 0

        conn.commit()
        if updated:
            settings_manager.invalidate_cache()
            logger.info(f"Aligned {updated} CSV export/download plan flag(s)")
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        logger.warning(f"CSV feature alignment failed (non-fatal): {e}")
        return False
    finally:
        if conn:
            conn.close()

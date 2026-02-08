"""
Feature flags backed by app_settings table.

Usage:
    from utils.feature_flags import is_feature_enabled

    if not is_feature_enabled("live_scraping_enabled"):
        raise HTTPException(status_code=503, detail="Live scraping is temporarily disabled")
"""
from utils.settings_manager import settings_manager


def is_feature_enabled(feature_name: str, default: bool = True) -> bool:
    """
    Check if a feature is enabled.
    Reads from app_settings: feature.{feature_name}
    Falls back to default if not set.
    """
    return settings_manager.get(f"feature.{feature_name}", default=default)

"""Startup and shutdown helpers for the legacy FastAPI app."""


def run_startup_tasks(logger, settings):
    """Execute app startup tasks without changing existing behavior."""
    logger.info("Starting Trademark Risk Assessment System...")
    logger.info(f"   Environment: {settings.environment}")
    logger.info(f"   Version: {settings.app_version}")

    from utils.idf_scoring import initialize_idf_scoring_sync, is_cache_loaded, get_cache_stats

    logger.info("   Loading IDF scoring data...")
    try:
        initialize_idf_scoring_sync()
        if is_cache_loaded():
            stats = get_cache_stats()
            logger.info(f"   IDF Scoring ready: {stats['word_count']:,} words loaded")
        else:
            logger.warning("   IDF Scoring: using fallback (run compute_idf.py to populate)")
    except Exception as exc:
        logger.warning(f"   IDF Scoring init failed (non-fatal): {exc}")

    from generative_ai.gemini_client import get_gemini_client

    try:
        gemini = get_gemini_client(settings.creative)
        if gemini.is_available():
            logger.info("   Gemini client ready (Creative Suite enabled)")
        else:
            logger.info("   Gemini client: no API key (Creative Suite disabled, set CREATIVE_GOOGLE_API_KEY)")
    except Exception as exc:
        logger.warning(f"   Gemini client init failed (non-fatal): {exc}")

    from migrations.run_reports_migration import ensure_reports_table

    try:
        if ensure_reports_table():
            logger.info("   Reports table ready")
        else:
            logger.warning("   Reports table migration skipped or failed (non-fatal)")
    except Exception as exc:
        logger.warning(f"   Reports table check failed (non-fatal): {exc}")

    try:
        from migrations.run_payments_migration import ensure_payments_table

        if ensure_payments_table():
            logger.info("   Payments table ready")
        else:
            logger.warning("   Payments table migration skipped or failed (non-fatal)")
    except Exception as exc:
        logger.warning(f"   Payments table check failed (non-fatal): {exc}")

    try:
        from migrations.run_add_payment_refunds import ensure_payment_refund_columns

        if ensure_payment_refund_columns():
            logger.info("   Payment refund columns ready")
        else:
            logger.warning("   Payment refund columns migration skipped or failed (non-fatal)")
    except Exception as exc:
        logger.warning(f"   Payment refund columns check failed (non-fatal): {exc}")

    try:
        from migrations.run_education_progress_migration import ensure_education_progress_table

        if ensure_education_progress_table():
            logger.info("   Education progress table ready")
        else:
            logger.warning("   Education progress migration skipped or failed (non-fatal)")
    except Exception as exc:
        logger.warning(f"   Education progress table check failed (non-fatal): {exc}")

    try:
        from migrations.run_pipeline_runs_migration import run_migration

        if run_migration():
            logger.info("   Pipeline runs table ready")
        else:
            logger.warning("   Pipeline runs migration skipped or failed (non-fatal)")
    except Exception as exc:
        logger.warning(f"   Pipeline runs table check failed (non-fatal): {exc}")

    from utils.settings_manager import settings_manager

    try:
        from migrations.run_add_app_settings import ensure_app_settings_table

        ensure_app_settings_table()
        settings_manager.init()
        logger.info("   Settings manager ready")
    except Exception as exc:
        logger.warning(f"   Settings manager init failed (non-fatal): {exc}")

    from utils.seed_settings import align_legacy_quick_search_limits, seed_default_settings

    try:
        seed_default_settings()
    except Exception as exc:
        logger.warning(f"   Default settings seed failed (non-fatal): {exc}")

    try:
        if align_legacy_quick_search_limits():
            logger.info("   Quick-search plan limits aligned")
        else:
            logger.warning("   Quick-search plan-limit alignment skipped or failed (non-fatal)")
    except Exception as exc:
        logger.warning(f"   Quick-search plan-limit alignment failed (non-fatal): {exc}")

    from utils.superadmin import seed_superadmin

    try:
        seed_superadmin()
    except Exception as exc:
        logger.warning(f"   Superadmin seed failed (non-fatal): {exc}")

    try:
        from workers.scheduler import start_scheduler

        start_scheduler()
        logger.info("   Scheduler started (watchlist scan 03:00, universal scan 04:00)")
    except Exception as exc:
        logger.warning(f"   Scheduler init failed (non-fatal): {exc}")


def run_shutdown_tasks(logger):
    """Execute app shutdown tasks without changing existing behavior."""
    try:
        from workers.scheduler import shutdown_scheduler

        shutdown_scheduler()
    except Exception:
        pass
    logger.info("Shutting down...")

"""Router registration helpers for the legacy FastAPI app."""

from importlib import import_module


def _load_router(module_path, attr="router"):
    module = import_module(module_path)
    return getattr(module, attr)


def _load_optional_router(module_path, logger, router_name, attr="router"):
    try:
        return _load_router(module_path, attr=attr)
    except Exception as exc:
        logger.warning(f"Could not load {router_name} router: {exc}")
        return None


def register_application_routers(app, logger):
    """Register all API routers in the legacy application order."""
    from api.routes import (
        auth_router,
        users_router,
        user_profile_router,
        org_router,
        watchlist_router,
        alerts_router,
        dashboard_router,
        education_router,
        usage_router,
    )

    reports_router = _load_router("api.reports")
    leads_router = _load_router("api.leads")
    holders_router = _load_router("api.holders")
    attorneys_router = _load_optional_router("api.attorneys", logger, "attorneys")
    creative_router = _load_router("api.creative")
    pipeline_router = _load_router("api.pipeline")
    admin_router = _load_router("api.admin")
    billing_router = _load_router("api.billing")
    payments_router = _load_optional_router("api.payments", logger, "payments")
    applications_router = _load_optional_router("api.applications", logger, "applications")
    agentic_router = _load_router("agentic_search")
    trademark_router = _load_router("api.trademark_routes", attr="trademark_router")

    app.include_router(auth_router, prefix="/api/v1")

    app.include_router(users_router, prefix="/api/v1")
    app.include_router(user_profile_router, prefix="/api/v1")
    app.include_router(org_router, prefix="/api/v1")
    app.include_router(watchlist_router, prefix="/api/v1")
    app.include_router(alerts_router, prefix="/api/v1")
    app.include_router(reports_router, prefix="/api/v1")
    app.include_router(dashboard_router, prefix="/api/v1")
    app.include_router(education_router, prefix="/api/v1")
    app.include_router(leads_router, prefix="/api/v1")
    app.include_router(holders_router, prefix="/api/v1")
    if attorneys_router:
        app.include_router(attorneys_router, prefix="/api/v1")
    app.include_router(usage_router, prefix="/api/v1")

    app.include_router(creative_router)
    app.include_router(pipeline_router)
    app.include_router(admin_router)
    app.include_router(billing_router)

    if payments_router:
        app.include_router(payments_router)

    if applications_router:
        app.include_router(applications_router)

    app.include_router(trademark_router, prefix="/api/v1")
    app.include_router(agentic_router)

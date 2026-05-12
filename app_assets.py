"""Static-file, template, and page-route helpers for the legacy FastAPI app."""

from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config.settings import settings


AVATAR_STATIC_PATH = "/static/avatars"
AVATAR_UPLOAD_DIR = Path(settings.paths.upload_dir) / "avatars"


def configure_static_assets(base_dir):
    """Prepare the shared static directory and initialize Jinja templates."""
    static_dir = base_dir / "static"
    AVATAR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    templates_dir = base_dir / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))
    return static_dir, templates


def mount_static_assets(app, static_dir):
    """Mount the static directory after any special-case routes are registered."""
    AVATAR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(AVATAR_STATIC_PATH, StaticFiles(directory=str(AVATAR_UPLOAD_DIR)), name="static-avatars")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def build_service_worker_response(static_dir):
    """Serve the service worker with no-cache headers."""
    return FileResponse(
        static_dir / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


def _get_public_plans():
    """Expose only customer-facing plans to public pages."""
    from utils.subscription import PLAN_FEATURES

    return {key: value for key, value in PLAN_FEATURES.items() if key != "superadmin"}


def _get_public_billing_catalog(request: Request, region: str | None = None):
    """Resolve the public regional catalog for server-rendered billing pages."""
    from services.billing_catalog import get_billing_catalog

    return get_billing_catalog(region=region, headers=request.headers)


def register_asset_routes(app, templates, static_dir):
    """Register service-worker and page routes next to their asset setup."""

    @app.get("/favicon.ico", tags=["Root"], include_in_schema=False)
    async def serve_favicon():
        """Serve the browser favicon fallback from the current logo asset."""
        return FileResponse(
            static_dir / "icons" / "favicon.ico",
            media_type="image/x-icon",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    @app.get("/static/sw.js", tags=["Root"], include_in_schema=False)
    async def serve_service_worker():
        """Serve SW with no-cache headers so browsers always check for updates."""
        return build_service_worker_response(static_dir)

    @app.get("/", response_class=HTMLResponse, tags=["Root"])
    async def root(request: Request):
        """Serve the landing page."""
        return templates.TemplateResponse(
            request=request,
            name="marketing/landing.html",
            context={"plans": _get_public_plans()},
        )

    @app.get("/dashboard", response_class=HTMLResponse, tags=["Root"])
    async def serve_dashboard(request: Request):
        """Serve the dashboard via Jinja2 templates."""
        response = templates.TemplateResponse(
            request=request,
            name="dashboard/page.html",
            context={"plans": _get_public_plans()},
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @app.get("/admin", response_class=HTMLResponse, tags=["Root"])
    async def serve_admin(request: Request):
        """Serve admin panel. Full auth enforced client-side + API-side."""
        response = templates.TemplateResponse(request=request, name="admin/page.html")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @app.get("/pricing", response_class=HTMLResponse, tags=["Root"])
    async def serve_pricing(request: Request, region: str | None = None):
        """Serve the pricing page while rendering limits from PLAN_FEATURES."""
        billing_catalog = _get_public_billing_catalog(request, region)
        return templates.TemplateResponse(
            request=request,
            name="billing/pricing.html",
            context={"plans": _get_public_plans(), "billing_catalog": billing_catalog},
        )

    @app.get("/checkout", response_class=HTMLResponse, tags=["Root"])
    async def serve_checkout(
        request: Request,
        plan: str = "free",
        billing: str = "monthly",
        region: str | None = None,
    ):
        """Serve the checkout page with validated plan and billing selection."""
        public_plans = _get_public_plans()
        if plan not in public_plans:
            plan = "free"
        if billing not in ("monthly", "annual"):
            billing = "monthly"
        billing_catalog = _get_public_billing_catalog(request, region)
        return templates.TemplateResponse(
            request=request,
            name="billing/checkout.html",
            context={
                "plans": public_plans,
                "billing_catalog": billing_catalog,
                "selected_plan": plan,
                "selected_billing": billing,
                "selected_region": billing_catalog["region"],
            },
        )

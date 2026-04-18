"""FastAPI app-construction helpers for the legacy app."""

from fastapi import FastAPI


APP_DESCRIPTION = """
## Trademark Risk Assessment System

AI-powered trademark conflict detection with multi-tenant watchlist monitoring.

### Features
- \U0001F510 **User Authentication** - JWT-based auth with organization support
- \U0001F4CB **Watchlist Monitoring** - Monitor your trademarks against new filings
- \U0001F514 **Smart Alerts** - Get notified of potential conflicts
- \U0001F4CA **Reports** - Generate detailed risk assessment reports
- \U0001F50D **AI Search** - Semantic and visual similarity search

### Authentication
All endpoints except `/auth/*` require a valid JWT token.
Include in header: `Authorization: Bearer <token>`
"""


def create_fastapi_app(settings, lifespan):
    """Create the FastAPI application with the legacy metadata and docs config."""
    return FastAPI(
        title=settings.app_name,
        description=APP_DESCRIPTION,
        version=settings.app_version,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )

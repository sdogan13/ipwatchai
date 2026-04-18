"""Rate-limiting helpers for the legacy FastAPI app."""

from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded


def configure_rate_limiting(app, settings, logger, key_func):
    """Attach the shared limiter and 429 handler to the app."""
    limiter = Limiter(
        key_func=key_func,
        default_limits=[f"{settings.auth.api_rate_limit}/minute"],
    )
    app.state.limiter = limiter

    async def rate_limit_handler(request, exc: RateLimitExceeded):
        ident = getattr(request.state, "_rate_limit_key", None)
        ip = request.client.host if request.client else "unknown"
        logger.warning(
            f"Rate limit hit: ident={ident} endpoint={request.url.path} IP={ip} limit={exc.detail}"
        )
        return JSONResponse(
            status_code=429,
            content={"detail": {"message": "Rate limit exceeded", "limit": str(exc.detail)}},
        )

    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    return limiter

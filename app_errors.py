"""Exception-handler helpers for the legacy FastAPI app."""

from fastapi.responses import JSONResponse


def configure_exception_handlers(app, settings, logger):
    """Register the global exception handler used by the legacy app."""

    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc):
        logger.error(f"Unhandled exception on {request.url.path}: {exc}", exc_info=True)
        content = {"detail": "Internal server error"}
        if settings.debug:
            content["debug_error"] = str(exc)
        return JSONResponse(status_code=500, content=content)

    return global_exception_handler

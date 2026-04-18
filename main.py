"""Compatibility wrapper for the legacy FastAPI app entrypoint."""

from importlib import import_module

from config.settings import settings


_legacy_main = import_module("legacy_main")

# Preserve legacy imports such as `from main import _do_public_search`.
globals().update(
    {
        name: getattr(_legacy_main, name)
        for name in dir(_legacy_main)
        if not (name.startswith("__") and name.endswith("__"))
    }
)

app = _legacy_main.app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        workers=1 if settings.debug else settings.workers,
        log_level="debug" if settings.debug else "info",
    )

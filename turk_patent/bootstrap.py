"""Low-risk package bootstrap helpers for the legacy app entrypoint."""

from functools import lru_cache
from importlib import import_module


@lru_cache(maxsize=1)
def get_legacy_main_module():
    """Import and cache the current root-level app implementation module."""
    return import_module("legacy_main")


def get_app():
    """Return the current FastAPI application without changing boot behavior."""
    return get_legacy_main_module().app

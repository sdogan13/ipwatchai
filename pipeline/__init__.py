"""Canonical pipeline package with lazy submodule loading."""

import importlib

__all__ = ["ai", "ingest", "parallel"]


def __getattr__(name):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

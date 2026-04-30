from __future__ import annotations

import base64
import os
from dataclasses import dataclass


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_EMAIL = "mobiletest@test.com"
DEFAULT_PASSWORD = "Test1234!"
DEFAULT_TIMEOUT = 45

# Historical export name; use a minimally realistic 64x64 logo fixture because
# 1x1 images can crash native visual/OCR model code in live GPU runs.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAABiUlEQVR4nO3aP4rCQBQG8G8+p7WW"
    "Le0CClaCDMYDeAqPoN7IWi9hI5Mo2CvYiF5AiIjFLGzAat1dWCX53P1VQzIMebw3+fOICSFAGSGO"
    "EEeII8QR4ghxhDh774QxBiUTPntkyWeAeNUSuin8XcN8WczyGSDEEeIIcYQ4QhwhjhBHiCPEEeII"
    "cYQ4PmqhwWAwnU7zcavVGo1G+Xg4HM5mM5Q/AOfccrkEcDqdrLVJkuTHkySJ4xgSAaxWKwDe+36/"
    "n2XZ5XK5Xq9ZltVqNRT4QfNDjUZjt9uFEBaLRRzHx+NxvV5XKpV2u41nso9ayBgTRdFms0nTdDwe"
    "7/d77721ttfrQeUu5JxL0/R8PlerVeec9/7ZGwAPD2AymTSbTQBRFG2328PhUK/XoRJAp9OZz+fd"
    "bjevqLcPeDJzr+lw6wWUpysR/htbZUSII8QR4ghxhDhCHCGOEEeII8QR4ghxhDhCHCGOEEeII8TZ"
    "b2eU8LeVl8oAIc4U3vb56xkgxBHiCHGEOBZ9Ab/1Dk4ScGnmRqguAAAAAElFTkSuQmCC"
)


@dataclass(frozen=True)
class LiveConfig:
    base_url: str
    timeout: int
    email: str
    password: str


def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_live_config(
    *,
    default_base_url: str = DEFAULT_BASE_URL,
    default_timeout: int = DEFAULT_TIMEOUT,
    base_url_env: str = "TEST_BASE_URL",
    timeout_env: str = "TEST_TIMEOUT",
    email_env: str = "TEST_EMAIL",
    password_env: str = "TEST_PASSWORD",
) -> LiveConfig:
    return LiveConfig(
        base_url=os.environ.get(base_url_env, default_base_url).rstrip("/"),
        timeout=_read_int_env(timeout_env, default_timeout),
        email=os.environ.get(email_env, DEFAULT_EMAIL),
        password=os.environ.get(password_env, DEFAULT_PASSWORD),
    )


def should_run_watchlist_e2e(env_name: str = "RUN_WATCHLIST_E2E") -> bool:
    return os.environ.get(env_name, "1") != "0"


def should_run_env_flag(env_name: str, default: str = "1") -> bool:
    return os.environ.get(env_name, default) != "0"

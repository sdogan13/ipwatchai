from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_EMAIL = "mobiletest@test.com"
DEFAULT_PASSWORD = "Test1234!"
DEFAULT_TIMEOUT = 45

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
    b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
    b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
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

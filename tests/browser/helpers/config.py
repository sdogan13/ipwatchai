from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tests.live.helpers.config import LiveConfig, load_live_config


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACTS_DIR = ROOT / "tests" / "browser" / "artifacts"


@dataclass(frozen=True)
class BrowserConfig:
    base_url: str
    timeout_ms: int
    email: str
    password: str
    browser_channel: str
    headless: bool
    artifacts_dir: Path


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def load_browser_config() -> BrowserConfig:
    live = load_live_config()
    return BrowserConfig(
        base_url=live.base_url,
        timeout_ms=live.timeout * 1000,
        email=live.email,
        password=live.password,
        browser_channel=os.environ.get("TEST_BROWSER_CHANNEL", "msedge"),
        headless=_read_bool_env("TEST_BROWSER_HEADLESS", True),
        artifacts_dir=Path(os.environ.get("TEST_BROWSER_ARTIFACTS_DIR", str(DEFAULT_ARTIFACTS_DIR))),
    )


def with_live_credentials(base: BrowserConfig, live: LiveConfig) -> BrowserConfig:
    return BrowserConfig(
        base_url=live.base_url,
        timeout_ms=live.timeout * 1000,
        email=live.email,
        password=live.password,
        browser_channel=base.browser_channel,
        headless=base.headless,
        artifacts_dir=base.artifacts_dir,
    )

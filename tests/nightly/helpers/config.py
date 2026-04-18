from __future__ import annotations

import os
from dataclasses import dataclass

from tests.live.helpers.config import load_live_config, should_run_env_flag


@dataclass(frozen=True)
class NightlyConfig:
    base_url: str
    email: str
    password: str
    run_live_smoke: bool
    run_browser: bool
    run_stateful: bool


def load_nightly_config() -> NightlyConfig:
    live = load_live_config()
    return NightlyConfig(
        base_url=live.base_url,
        email=live.email,
        password=live.password,
        run_live_smoke=should_run_env_flag("RUN_NIGHTLY_LIVE_SMOKE"),
        run_browser=should_run_env_flag("RUN_NIGHTLY_BROWSER"),
        run_stateful=should_run_env_flag("RUN_NIGHTLY_STATEFUL"),
    )

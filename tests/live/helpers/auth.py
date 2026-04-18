from __future__ import annotations

import time

from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.client import LiveClient


def _rate_limit_wait_seconds(response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass
    return 15.0


def login_user(
    client: LiveClient,
    reporter: LiveReporter,
    email: str,
    password: str,
    *,
    endpoint: str = "/api/v1/auth/login",
    name: str = "POST /api/v1/auth/login",
    max_attempts: int = 5,
) -> bool:
    response = None
    for attempt in range(1, max_attempts + 1):
        response = client.post(
            endpoint,
            json_data={"email": email, "password": password},
            token=False,
        )
        if response.status_code != 429 or attempt == max_attempts:
            break

        wait_seconds = _rate_limit_wait_seconds(response)
        reporter.warn(f"{name} -> 429 rate limited, retrying in {int(wait_seconds)}s (attempt {attempt}/{max_attempts})")
        time.sleep(wait_seconds)

    if response.status_code != 200:
        reporter.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        reporter.record(name, False, response.text[:200])
        return False

    data = response.json()
    token = data.get("access_token")
    if not token:
        reporter.fail(f"{name} -> no access_token in response")
        reporter.record(name, False, "missing access_token")
        return False

    client.token = token
    reporter.ok(f"{name} -> token obtained")
    reporter.record(name, True)
    return True

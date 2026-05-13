"""Probe EUIPO rate-limit period.

Burns ~30 fast requests against /trademarks and records the X-RateLimit-Remaining
header after each. Decrement pattern tells us:
  - flat -1 per request => single bucket, current value tells us headroom
  - period inferred by checking X-RateLimit-Reset if present
  - if there's a reset value in seconds, that IS the period to reset

Output:
  - prints the decrement curve
  - reports the estimated period (or marks unknown)

NOT part of production data path. Delete after results land in
docs/EUIPO_DATA_NOTES.md §5.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv()

TOKEN_URL = "https://auth-sandbox.euipo.europa.eu/oidc/accessToken"
API_URL = "https://api-sandbox.euipo.europa.eu/trademark-search/trademarks"
N_REQUESTS = 30


def _get_token(key: str, secret: str) -> str:
    r = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials", "scope": "uid"},
        auth=(key, secret),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> int:
    key = os.environ["EUIPO_API_KEY"]
    secret = os.environ["EUIPO_API_SECRET"]
    token = _get_token(key, secret)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-IBM-Client-Id": key,
        "Accept": "application/json",
    }
    params = {"size": "10", "page": "0"}

    print(f"Sending {N_REQUESTS} requests as fast as possible...")
    rl_remaining_series = []
    reset_series = []
    start = time.time()
    for i in range(N_REQUESTS):
        r = requests.get(API_URL, headers=headers, params=params, timeout=30)
        rl_rem = r.headers.get("X-RateLimit-Remaining", "?")
        rl_lim = r.headers.get("X-RateLimit-Limit", "?")
        rl_reset = r.headers.get("X-RateLimit-Reset", "?")
        retry = r.headers.get("Retry-After", "?")
        rl_remaining_series.append(rl_rem)
        reset_series.append(rl_reset)
        print(f"  #{i+1:02d}  status={r.status_code}  remaining={rl_rem}  reset={rl_reset}  retry={retry}")
        if r.status_code == 429:
            print("  -> rate-limited; stopping probe early")
            break
    elapsed = time.time() - start
    print(f"\nElapsed: {elapsed:.2f}s for {len(rl_remaining_series)} requests "
          f"({len(rl_remaining_series)/elapsed:.1f} req/s)")
    print(f"\nX-RateLimit-Reset values seen: {set(reset_series)}")
    print("\nInterpretation:")
    print("  - If 'reset' was a constant ~3600 -> hourly window")
    print("  - If 'reset' was a constant ~86400 -> daily window")
    print("  - If 'reset' was missing/0 -> sliding-window or per-month plan")
    print("  - Decrement per request tells us if the budget is total or per-resource")
    return 0


if __name__ == "__main__":
    sys.exit(main())

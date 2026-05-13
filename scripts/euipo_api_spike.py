"""EUIPO API spike — Trademark Search API v1.1.0 round 3.

Now armed with the OpenAPI spec. Key learnings folded in:
  - Token request MUST include scope='uid' (Oauth2ClientCredentials flow)
  - Required headers: Authorization: Bearer <token> + X-IBM-Client-Id: <key>
  - Search endpoint: GET https://api-sandbox.euipo.europa.eu/trademark-search/trademarks
  - Pagination: size MIN=10, MAX=100; page is zero-indexed
  - Query syntax: RSQL (applicationDate>=2023-05-04 and markFeature==FIGURATIVE etc.)

Run::

    python scripts/euipo_api_spike.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv()

OUTPUT_DIR = PROJECT_ROOT / "scripts" / "euipo_spike_output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_URL = "https://auth-sandbox.euipo.europa.eu/oidc/accessToken"
API_BASE = "https://api-sandbox.euipo.europa.eu/trademark-search"


def _save(name: str, payload: Any) -> Path:
    path = OUTPUT_DIR / name
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")
    return path


def get_token(key: str, secret: str) -> Optional[str]:
    print(f"=== TOKEN: {TOKEN_URL}  scope=uid ===")
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials", "scope": "uid"},
        auth=(key, secret),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    print(f"  status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"  body: {resp.text[:400]}")
        return None
    body = resp.json()
    print(f"  scope returned: {body.get('scope')}")
    print(f"  expires_in:     {body.get('expires_in')}")
    return body.get("access_token")


def probe(token: str, key: str, label: str, path: str, params: dict) -> dict:
    url = f"{API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-IBM-Client-Id": key,
        "Accept": "application/json",
    }
    print(f"\n--- {label}: GET {url} params={params} ---")
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    print(f"  status: {resp.status_code}")
    rl_headers = {k: v for k, v in resp.headers.items() if "RateLimit" in k or "Retry" in k}
    print(f"  rate-limit headers: {rl_headers}")
    snippet = resp.text[:500]
    print(f"  body excerpt: {snippet}{'...' if len(resp.text) > 500 else ''}")
    safe = label.replace(" ", "_").replace("/", "_")
    if resp.headers.get("content-type", "").startswith("application/json"):
        try:
            _save(f"r3_{safe}.json", resp.json())
        except ValueError:
            _save(f"r3_{safe}.txt", resp.text)
    else:
        _save(f"r3_{safe}.txt", resp.text)
    return {
        "label": label,
        "url": url,
        "params": params,
        "status": resp.status_code,
        "headers": dict(resp.headers),
        "body": resp.text,
    }


def main() -> int:
    key = os.environ.get("EUIPO_API_KEY")
    secret = os.environ.get("EUIPO_API_SECRET")
    if not key or not secret:
        print("ERROR: EUIPO_API_KEY / EUIPO_API_SECRET missing from env")
        return 2

    token = get_token(key, secret)
    if not token:
        return 1

    # Probe 1: minimal valid search (size=10 per spec minimum)
    probe(token, key, "tiny_search", "/trademarks", {"size": "10", "page": "0"})

    # Probe 2: tighter — last 3 days of updates, sorted by applicationNumber asc
    # (Smallest possible window to validate updateDate delta strategy.)
    probe(token, key, "recent_updates", "/trademarks", {
        "size": "10",
        "page": "0",
        "query": "updateDate>=2026-05-10",
        "sort": "applicationNumber:asc",
    })

    # Probe 3: get a single record's full detail (the example app number from spec)
    probe(token, key, "detail_example", "/trademarks/000084601", {})

    return 0


if __name__ == "__main__":
    sys.exit(main())

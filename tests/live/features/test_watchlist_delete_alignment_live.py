"""
Live HTTP suite proving patent + cografi watchlist DELETE returns the
``removed_alerts`` / ``success`` / ``message`` fields, matching the
trademark and design pattern.

Why this exists:
    Before commit 492cd377, deleting a patent or cografi watchlist item
    returned ``{"deleted": true, "id": ...}`` only — the FK cascade
    removed alerts silently and the caller never learned how many. The
    trademark and design services have counted alerts since day one and
    returned the count to the user.

Each scenario:
    1. POST a holder-type watchlist item with a recognizable label.
    2. INSERT two fake alert rows directly via Database() so the count
       is non-zero (FK cascade then has something to remove).
    3. DELETE via API; assert status 200 and response contains
       ``success``, ``removed_alerts == 2``, ``id``, ``deleted``, and a
       Turkish ``message`` with the count.
    4. GET the deleted item; assert 404.

Run directly:
    python tests/live/features/test_watchlist_delete_alignment_live.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.crud import Database
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.auth import login_user
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import load_live_config


CONFIG = load_live_config()
REPORTER = LiveReporter()
SEED_ALERT_COUNT = 2
TEST_LABEL_PREFIX = "LIVE WATCHLIST DELETE ALIGNMENT"

pytestmark = pytest.mark.skip(
    reason="Live feature script; run directly with python tests/live/features/test_watchlist_delete_alignment_live.py"
)


def _login() -> LiveClient | None:
    client = LiveClient(CONFIG)
    if not login_user(client, REPORTER, CONFIG.email, CONFIG.password, name="watchlist delete alignment login"):
        return None
    return client


def _fetch_org_id(client: LiveClient) -> str | None:
    response = client.get("/api/v1/auth/me")
    if response.status_code != 200:
        REPORTER.fail(f"GET /api/v1/auth/me -> {response.status_code}: {response.text[:200]}")
        REPORTER.record("GET /api/v1/auth/me", False, response.text[:200])
        return None
    org_id = response.json().get("organization_id")
    if not org_id:
        REPORTER.fail("GET /api/v1/auth/me -> missing organization_id")
        REPORTER.record("GET /api/v1/auth/me", False, "missing organization_id")
        return None
    REPORTER.ok(f"GET /api/v1/auth/me -> org {org_id}")
    REPORTER.record("GET /api/v1/auth/me", True)
    return str(org_id)


def _seed_alerts(table: str, watchlist_item_id: str, org_id: str, match_type: str, count: int) -> bool:
    """Insert ``count`` minimal alert rows for the given watchlist item.

    NOT-NULL columns satisfied: watchlist_item_id, organization_id,
    match_type, overall_similarity_score. Everything else is left to
    its default (uuid for id, NULLs / defaults elsewhere).
    """
    try:
        with Database() as db:
            cur = db.cursor()
            for _ in range(count):
                cur.execute(
                    f"""
                    INSERT INTO {table}
                        (watchlist_item_id, organization_id, match_type, overall_similarity_score)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (watchlist_item_id, org_id, match_type, 1.0),
                )
            db.commit()
    except Exception as exc:  # pragma: no cover — surfaced to reporter
        REPORTER.fail(f"seed alerts in {table} -> {exc}")
        REPORTER.record(f"seed alerts in {table}", False, str(exc)[:200])
        return False
    REPORTER.ok(f"seed alerts in {table} -> {count} row(s)")
    REPORTER.record(f"seed alerts in {table}", True)
    return True


def _assert_delete_response(scenario: str, response, *, expected_alerts: int) -> bool:
    name = f"{scenario} DELETE response shape"
    if response.status_code != 200:
        REPORTER.fail(f"{name} -> status {response.status_code}: {response.text[:200]}")
        REPORTER.record(name, False, response.text[:200])
        return False

    try:
        payload = response.json()
    except Exception as exc:
        REPORTER.fail(f"{name} -> non-JSON body ({exc})")
        REPORTER.record(name, False, response.text[:200])
        return False

    missing = [key for key in ("success", "id", "deleted", "removed_alerts", "message") if key not in payload]
    if missing:
        REPORTER.fail(f"{name} -> missing keys {missing}; payload={payload}")
        REPORTER.record(name, False, f"missing keys {missing}")
        return False

    if payload.get("removed_alerts") != expected_alerts:
        REPORTER.fail(
            f"{name} -> removed_alerts={payload.get('removed_alerts')} (expected {expected_alerts}); payload={payload}"
        )
        REPORTER.record(name, False, f"removed_alerts mismatch ({payload.get('removed_alerts')} != {expected_alerts})")
        return False

    message = str(payload.get("message") or "")
    if str(expected_alerts) not in message:
        REPORTER.fail(f"{name} -> message {message!r} does not include the count")
        REPORTER.record(name, False, "count missing from message")
        return False

    REPORTER.ok(
        f"{name} -> success={payload['success']} removed_alerts={payload['removed_alerts']} message={message!r}"
    )
    REPORTER.record(name, True)
    return True


def _run_scenario(
    client: LiveClient,
    *,
    label: str,
    create_path: str,
    get_path_tpl: str,
    delete_path_tpl: str,
    alerts_table: str,
    org_id: str,
    create_payload: dict,
    match_type: str,
) -> None:
    REPORTER.print_section(label)

    create = client.post(create_path, json_data=create_payload)
    if create.status_code != 200:
        REPORTER.fail(f"{label} POST {create_path} -> {create.status_code}: {create.text[:200]}")
        REPORTER.record(f"{label} create", False, create.text[:200])
        return
    item = create.json()
    item_id = item.get("id")
    if not item_id:
        REPORTER.fail(f"{label} POST {create_path} -> no id; payload={item}")
        REPORTER.record(f"{label} create", False, "no id in response")
        return
    REPORTER.ok(f"{label} POST {create_path} -> {item_id}")
    REPORTER.record(f"{label} create", True)

    if not _seed_alerts(alerts_table, item_id, org_id, match_type, SEED_ALERT_COUNT):
        # Best-effort cleanup of the watchlist row so the test is idempotent.
        client.delete(delete_path_tpl.format(id=item_id))
        return

    delete = client.delete(delete_path_tpl.format(id=item_id))
    _assert_delete_response(label, delete, expected_alerts=SEED_ALERT_COUNT)

    after = client.get(get_path_tpl.format(id=item_id))
    name_after = f"{label} GET after delete is 404"
    if after.status_code == 404:
        REPORTER.ok(f"{name_after} -> 404")
        REPORTER.record(name_after, True)
    else:
        REPORTER.fail(f"{name_after} -> {after.status_code}: {after.text[:200]}")
        REPORTER.record(name_after, False, after.text[:200])


def main() -> int:
    REPORTER.print_heading(
        "Watchlist DELETE alignment (patent + cografi)",
        server=CONFIG.base_url,
        user=CONFIG.email,
    )

    client = _login()
    if client is None:
        return REPORTER.summary("Watchlist DELETE alignment live")

    org_id = _fetch_org_id(client)
    if org_id is None:
        return REPORTER.summary("Watchlist DELETE alignment live")

    unique = uuid.uuid4().hex[:8]

    _run_scenario(
        client,
        label="patent",
        create_path="/api/v1/patent-watchlist",
        get_path_tpl="/api/v1/patent-watchlist/{id}",
        delete_path_tpl="/api/v1/patent-watchlist/{id}",
        alerts_table="patent_alerts_mt",
        org_id=org_id,
        match_type="holder",
        create_payload={
            "watch_type": "holder",
            "label": f"{TEST_LABEL_PREFIX} patent {unique}",
            "holder_name": f"DELETE-ALIGNMENT-HOLDER-{unique}",
            "alert_frequency": "daily",
            "similarity_threshold": 0.5,
        },
    )

    _run_scenario(
        client,
        label="cografi",
        create_path="/api/v1/cografi-watchlist",
        get_path_tpl="/api/v1/cografi-watchlist/{id}",
        delete_path_tpl="/api/v1/cografi-watchlist/{id}",
        alerts_table="cografi_alerts_mt",
        org_id=org_id,
        match_type="holder",
        create_payload={
            "watch_type": "holder",
            "label": f"{TEST_LABEL_PREFIX} cografi {unique}",
            "holder_name": f"DELETE-ALIGNMENT-HOLDER-{unique}",
            "alert_frequency": "daily",
            "similarity_threshold": 0.5,
        },
    )

    return REPORTER.summary("Watchlist DELETE alignment live")


if __name__ == "__main__":
    sys.exit(main())

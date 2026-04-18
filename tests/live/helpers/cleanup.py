from __future__ import annotations

from pathlib import Path

from database.crud import Database
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.client import LiveClient


def cleanup_watchlist_items_by_prefix(
    client: LiveClient,
    reporter: LiveReporter,
    prefix: str,
    *,
    page_size: int = 50,
    name: str = "DELETE stale E2E watchlist items",
) -> bool:
    response = client.get(
        "/api/v1/watchlist",
        params={"search": prefix, "page_size": page_size},
    )
    if response.status_code != 200:
        reporter.warn(f"{name} -> unable to list existing items ({response.status_code})")
        return False

    items = response.json().get("items", [])
    deleted = 0
    for item in items:
        brand_name = item.get("brand_name") or ""
        item_id = item.get("id")
        if not item_id or not brand_name.startswith(prefix):
            continue

        delete_response = client.delete(f"/api/v1/watchlist/{item_id}")
        if delete_response.status_code in (200, 404):
            deleted += 1

    if deleted:
        reporter.info(f"{name} -> removed {deleted} leftover test item(s)")
    return True


def cleanup_applications_by_prefix(
    client: LiveClient,
    reporter: LiveReporter,
    prefix: str,
    *,
    page_size: int = 100,
    name: str = "DELETE stale E2E applications",
) -> bool:
    deleted = 0
    page = 1

    while True:
        response = client.get(
            "/api/v1/applications/",
            params={"page": page, "page_size": page_size},
        )
        if response.status_code != 200:
            reporter.warn(f"{name} -> unable to list existing applications ({response.status_code})")
            return False

        payload = response.json()
        items = payload.get("items", [])
        for item in items:
            brand_name = item.get("brand_name") or ""
            item_id = item.get("id")
            if not item_id or not brand_name.startswith(prefix):
                continue

            delete_response = client.delete(f"/api/v1/applications/{item_id}")
            if delete_response.status_code in (200, 404):
                deleted += 1

        if page >= int(payload.get("total_pages", 1) or 1):
            break
        page += 1

    if deleted:
        reporter.info(f"{name} -> removed {deleted} leftover application(s)")
    return True


def cleanup_reports_by_prefix(
    reporter: LiveReporter,
    organization_id: str | None,
    prefix: str | None = None,
    *,
    name: str = "DELETE stale E2E reports",
) -> bool:
    if not organization_id:
        reporter.warn(f"{name} -> missing organization id")
        return False

    deleted = 0
    removed_files = 0
    try:
        with Database() as db:
            cur = db.cursor()
            if prefix is None:
                cur.execute(
                    """
                    SELECT id, file_path, report_name
                    FROM reports
                    WHERE organization_id = %s
                    """,
                    (str(organization_id),),
                )
            else:
                like_value = f"{prefix}%"
                cur.execute(
                    """
                    SELECT id, file_path, report_name
                    FROM reports
                    WHERE organization_id = %s AND report_name LIKE %s
                    """,
                    (str(organization_id), like_value),
                )
            rows = cur.fetchall()

            for row in rows:
                file_path = row.get("file_path")
                if not file_path:
                    continue

                path = Path(file_path)
                if path.exists():
                    path.unlink()
                    removed_files += 1

            if rows and prefix is None:
                cur.execute(
                    "DELETE FROM reports WHERE organization_id = %s",
                    (str(organization_id),),
                )
                deleted = cur.rowcount or 0
                db.commit()
            elif rows:
                cur.execute(
                    "DELETE FROM reports WHERE organization_id = %s AND report_name LIKE %s",
                    (str(organization_id), like_value),
                )
                deleted = cur.rowcount or 0
                db.commit()
    except Exception as exc:
        reporter.warn(f"{name} -> cleanup failed ({exc})")
        return False

    if deleted:
        reporter.info(f"{name} -> removed {deleted} leftover report row(s), {removed_files} file(s)")
    return True

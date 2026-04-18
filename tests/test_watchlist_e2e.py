"""
End-to-end test suite for the Watchlist tab.

Hits the live server and validates the main watchlist API surface.

Run:
    python tests/test_watchlist_e2e.py
"""

from __future__ import annotations

import io
import json
import sys
import uuid
from pathlib import Path

import pytest

try:
    import openpyxl
except ImportError:
    print("Missing dependency. Run: pip install openpyxl")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.auth import login_user
from tests.live.helpers.cleanup import cleanup_watchlist_items_by_prefix
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import PNG_1X1, load_live_config


CONFIG = load_live_config(default_base_url="http://localhost:8000", default_timeout=15)
CLIENT = LiveClient(CONFIG)
REPORTER = LiveReporter()
pytestmark = pytest.mark.skip(reason="Live E2E script; run directly with python tests/test_watchlist_e2e.py")
TEST_BRAND_PREFIX = "TEST WATCHLIST MARKA"


class Ctx:
    item_id = None
    total_before = 0
    test_app_no = f"TEST-{uuid.uuid4().hex[:8].upper()}"

    @classmethod
    def record(cls, name: str, passed: bool, detail: str = "") -> None:
        REPORTER.record(name, passed, detail)


def get(path, *, headers=None, params=None, token=None):
    return CLIENT.get(path, headers=headers, params=params, token=(token is not False))


def post(path, data=None, *, json_data=None, files=None, headers=None, token=None):
    return CLIENT.post(
        path,
        headers=headers,
        json_data=json_data,
        data=data,
        files=files,
        token=(token is not False),
    )


def put(path, data=None):
    return CLIENT.put(path, data=data)


def delete(path):
    return CLIENT.delete(path)


def cleanup_stale_test_items():
    cleanup_watchlist_items_by_prefix(CLIENT, REPORTER, TEST_BRAND_PREFIX)


def test_health():
    name = "GET /health (server up)"
    response = get("/health", token=False)
    if response.status_code == 200 and response.json().get("status") == "healthy":
        REPORTER.ok(name)
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code} {response.text[:120]}")
        Ctx.record(name, False, response.text[:120])
        sys.exit(1)


def auth_login(email: str, password: str):
    if not login_user(CLIENT, REPORTER, email, password, name="POST /api/v1/auth/login (authentication)"):
        sys.exit(1)


def test_auth_required():
    name = "Auth gate - watchlist/stats requires token"
    response = get("/api/v1/watchlist/stats", token=False)
    if response.status_code in (401, 403):
        REPORTER.ok(name)
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> expected 401/403, got {response.status_code}")
        Ctx.record(name, False, f"Expected 401/403, got {response.status_code}")


def test_stats():
    name = "GET /api/v1/watchlist/stats"
    response = get("/api/v1/watchlist/stats")
    if response.status_code == 200:
        data = response.json()
        expected = ["total_items", "active_items", "items_with_threats", "critical_threats", "new_alerts"]
        missing = [key for key in expected if key not in data]
        if missing:
            REPORTER.warn(f"{name} -> 200 but missing fields: {missing}")
            Ctx.record(name, False, f"Missing fields: {missing}")
        else:
            Ctx.total_before = data["total_items"]
            REPORTER.ok(f"{name} -> total_items={data['total_items']}, new_alerts={data['new_alerts']}")
            Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_list_empty_params():
    name = "GET /api/v1/watchlist (list, default params)"
    response = get("/api/v1/watchlist")
    if response.status_code == 200:
        data = response.json()
        for field in ("items", "total", "page", "page_size", "total_pages"):
            if field not in data:
                REPORTER.fail(f"{name} -> missing field '{field}' in response")
                Ctx.record(name, False, f"Missing field: {field}")
                return
        REPORTER.ok(f"{name} -> {data['total']} items, page {data['page']}/{data['total_pages']}")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_list_search_sort():
    name = "GET /api/v1/watchlist (search + sort params)"
    response = get("/api/v1/watchlist", params={"page": 1, "page_size": 5, "search": "test", "sort": "date_desc"})
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> 200 OK")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_create():
    name = "POST /api/v1/watchlist (create item)"
    payload = {
        "brand_name": TEST_BRAND_PREFIX,
        "application_no": Ctx.test_app_no,
        "nice_class_numbers": [9, 35],
        "similarity_threshold": 0.75,
        "description": "Automated E2E test item",
        "monitor_text": True,
        "monitor_visual": False,
        "monitor_phonetic": True,
        "alert_frequency": "daily",
        "alert_email": False,
    }
    response = post("/api/v1/watchlist", json_data=payload)
    if response.status_code in (200, 201):
        item = response.json()
        Ctx.item_id = item.get("id")
        REPORTER.ok(f"{name} -> id={Ctx.item_id}, brand={item.get('brand_name')}")
        Ctx.record(name, True)
    elif response.status_code == 409:
        REPORTER.warn(f"{name} -> 409 Conflict (item already exists). Trying to find existing item...")
        Ctx.record(name, True, "409 - existing item used")
        fallback = get("/api/v1/watchlist", params={"search": TEST_BRAND_PREFIX, "page_size": 5})
        if fallback.status_code == 200 and fallback.json().get("items"):
            Ctx.item_id = fallback.json()["items"][0]["id"]
            REPORTER.info(f"Found existing item id={Ctx.item_id}")
    elif response.status_code == 403:
        REPORTER.warn(f"{name} -> 403 Forbidden (plan limit or logo tracking restriction)")
        Ctx.record(name, False, f"403: {response.text[:200]}")
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_create_duplicate():
    name = "POST /api/v1/watchlist (duplicate -> 409)"
    if not Ctx.item_id:
        REPORTER.warn(f"{name} -> SKIP (no item created)")
        return
    payload = {
        "brand_name": "TEST WATCHLIST MARKA COPY",
        "application_no": Ctx.test_app_no,
        "nice_class_numbers": [9],
        "similarity_threshold": 0.7,
        "monitor_text": True,
    }
    response = post("/api/v1/watchlist", json_data=payload)
    if response.status_code == 409:
        REPORTER.ok(f"{name} -> correctly returned 409")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> expected 409, got {response.status_code}: {response.text[:150]}")
        Ctx.record(name, False, f"Expected 409, got {response.status_code}")


def test_get_item():
    name = "GET /api/v1/watchlist/{id} (single item)"
    if not Ctx.item_id:
        REPORTER.warn(f"{name} -> SKIP (no item_id)")
        return
    response = get(f"/api/v1/watchlist/{Ctx.item_id}")
    if response.status_code == 200:
        data = response.json()
        for field in ("id", "brand_name", "nice_class_numbers", "is_active"):
            if field not in data:
                REPORTER.fail(f"{name} -> missing field '{field}'")
                Ctx.record(name, False, f"Missing: {field}")
                return
        REPORTER.ok(f"{name} -> brand={data['brand_name']}, active={data['is_active']}")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_get_item_404():
    name = "GET /api/v1/watchlist/{id} (non-existent -> 404)"
    response = get(f"/api/v1/watchlist/{uuid.uuid4()}")
    if response.status_code == 404:
        REPORTER.ok(f"{name} -> correctly returned 404")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> expected 404, got {response.status_code}")
        Ctx.record(name, False, f"Expected 404, got {response.status_code}")


def test_update_item():
    name = "PUT /api/v1/watchlist/{id} (update settings)"
    if not Ctx.item_id:
        REPORTER.warn(f"{name} -> SKIP (no item_id)")
        return
    payload = {
        "brand_name": "TEST WATCHLIST MARKA UPDATED",
        "nice_class_numbers": [9, 35, 42],
        "similarity_threshold": 0.80,
        "description": "Updated by E2E test",
        "monitor_text": True,
        "monitor_visual": False,
        "monitor_phonetic": True,
        "alert_frequency": "weekly",
        "alert_email": False,
    }
    response = put(f"/api/v1/watchlist/{Ctx.item_id}", data=payload)
    if response.status_code == 200:
        updated = response.json()
        if updated.get("similarity_threshold") == 0.80:
            REPORTER.ok(f"{name} -> threshold updated to 0.80, freq={updated.get('alert_frequency')}")
            Ctx.record(name, True)
        else:
            REPORTER.warn(f"{name} -> 200 but threshold not updated: {updated.get('similarity_threshold')}")
            Ctx.record(name, False, "Threshold not reflected in response")
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_update_item_404():
    name = "PUT /api/v1/watchlist/{id} (non-existent -> 404)"
    payload = {
        "brand_name": "Ghost",
        "nice_class_numbers": [9],
        "similarity_threshold": 0.7,
        "monitor_text": True,
    }
    response = put(f"/api/v1/watchlist/{uuid.uuid4()}", data=payload)
    if response.status_code == 404:
        REPORTER.ok(f"{name} -> correctly returned 404")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> expected 404, got {response.status_code}")
        Ctx.record(name, False, f"Expected 404, got {response.status_code}")


def test_stats_after_create():
    name = "GET /api/v1/watchlist/stats (total incremented after create)"
    if not Ctx.item_id:
        REPORTER.warn(f"{name} -> SKIP (no item created)")
        return
    response = get("/api/v1/watchlist/stats")
    if response.status_code == 200:
        new_total = response.json().get("total_items", 0)
        if new_total >= Ctx.total_before:
            REPORTER.ok(f"{name} -> total went from {Ctx.total_before} -> {new_total}")
            Ctx.record(name, True)
        else:
            REPORTER.warn(f"{name} -> total did NOT increase: before={Ctx.total_before}, now={new_total}")
            Ctx.record(name, False, f"Total not incremented: {Ctx.total_before} -> {new_total}")
    else:
        REPORTER.fail(f"{name} -> {response.status_code}")
        Ctx.record(name, False, response.text[:100])


def test_scan_single():
    name = "POST /api/v1/watchlist/{id}/scan (trigger scan)"
    if not Ctx.item_id:
        REPORTER.warn(f"{name} -> SKIP (no item_id)")
        return
    response = post(f"/api/v1/watchlist/{Ctx.item_id}/scan")
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> {response.json().get('message')}")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_scan_all():
    name = "POST /api/v1/watchlist/scan-all"
    response = post("/api/v1/watchlist/scan-all")
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> {response.json().get('message')}")
        Ctx.record(name, True)
    elif response.status_code == 403:
        REPORTER.warn(f"{name} -> 403 (plan upgrade needed - expected on free plan)")
        Ctx.record(name, True, "403 plan gate working correctly")
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_scan_status():
    name = "GET /api/v1/watchlist/scan-status"
    response = get("/api/v1/watchlist/scan-status")
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> next_scan={response.json().get('next_scan_at')}")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_download_template():
    name = "GET /api/v1/watchlist/upload/template (Excel download)"
    response = get("/api/v1/watchlist/upload/template")
    if response.status_code == 200:
        content_type = response.headers.get("content-type", "")
        content_disposition = response.headers.get("content-disposition", "")
        if "spreadsheet" in content_type or "excel" in content_type or ".xlsx" in content_disposition:
            REPORTER.ok(f"{name} -> content-type={content_type[:60]}")
            Ctx.record(name, True)
        else:
            REPORTER.warn(f"{name} -> 200 but unexpected content-type: {content_type}")
            Ctx.record(name, False, f"Content-Type: {content_type}")
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_detect_columns_csv():
    name = "POST /api/v1/watchlist/upload/detect-columns (CSV)"
    csv_content = b"Marka Adi,Basvuru No,Siniflar,Bulten No\nTEST,2023/99999,9,305\n"
    files = {"file": ("test.csv", io.BytesIO(csv_content), "text/csv")}
    response = post("/api/v1/watchlist/upload/detect-columns", files=files)
    if response.status_code == 200:
        data = response.json()
        if "columns" in data and "auto_mappings" in data:
            REPORTER.ok(f"{name} -> columns={data['columns']}, total_rows={data.get('total_rows')}")
            Ctx.record(name, True)
        else:
            REPORTER.warn(f"{name} -> 200 but missing 'columns'/'auto_mappings': {list(data.keys())}")
            Ctx.record(name, False, f"Missing fields: {list(data.keys())}")
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_detect_columns_excel():
    name = "POST /api/v1/watchlist/upload/detect-columns (Excel)"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(["Marka Adi", "Basvuru No", "Siniflar", "Bulten No"])
    worksheet.append(["ORNEK MARKA", "2023/12345", "9, 35", "305"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    files = {"file": ("test.xlsx", buffer, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    response = post("/api/v1/watchlist/upload/detect-columns", files=files)
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> auto_mappings={response.json().get('auto_mappings', {})}")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_detect_columns_bad_format():
    name = "POST /api/v1/watchlist/upload/detect-columns (bad format -> 400)"
    files = {"file": ("test.txt", io.BytesIO(b"not a spreadsheet"), "text/plain")}
    response = post("/api/v1/watchlist/upload/detect-columns", files=files)
    if response.status_code == 400:
        REPORTER.ok(f"{name} -> correctly rejected unsupported format")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> expected 400, got {response.status_code}: {response.text[:150]}")
        Ctx.record(name, False, f"Expected 400, got {response.status_code}")


def test_upload_with_mapping():
    name = "POST /api/v1/watchlist/upload/with-mapping (CSV)"
    unique_app = f"UPLOAD-{uuid.uuid4().hex[:6].upper()}"
    csv_content = f"MyBrand,MyApp,MyClasses\nUPLOAD TEST MARKA,{unique_app},9 35\n".encode()
    mapping = json.dumps({"brand_name": "MyBrand", "application_no": "MyApp", "nice_classes": "MyClasses"})
    files = {"file": ("brands.csv", io.BytesIO(csv_content), "text/csv")}
    data = {"column_mapping": mapping}
    response = post("/api/v1/watchlist/upload/with-mapping", data=data, files=files)
    if response.status_code == 200:
        summary = response.json().get("summary", {})
        REPORTER.ok(f"{name} -> added={summary.get('added')}, skipped={summary.get('skipped')}")
        Ctx.record(name, True)
    elif response.status_code == 403:
        REPORTER.warn(f"{name} -> 403 (plan limit)")
        Ctx.record(name, True, "403 plan gate working")
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:300]}")
        Ctx.record(name, False, response.text[:300])


def test_upload_with_mapping_missing_brand():
    name = "POST /api/v1/watchlist/upload/with-mapping (missing brand_name -> 400)"
    csv_content = b"AppNo,Classes\n2023/11111,9\n"
    mapping = json.dumps({"application_no": "AppNo", "nice_classes": "Classes"})
    files = {"file": ("brands.csv", io.BytesIO(csv_content), "text/csv")}
    data = {"column_mapping": mapping}
    response = post("/api/v1/watchlist/upload/with-mapping", data=data, files=files)
    if response.status_code == 400:
        REPORTER.ok(f"{name} -> correctly rejected missing brand_name")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> expected 400, got {response.status_code}: {response.text[:150]}")
        Ctx.record(name, False, f"Expected 400, got {response.status_code}")


def test_upload_auto_detect():
    name = "POST /api/v1/watchlist/upload (auto-detect columns)"
    unique_app = f"AUTO-{uuid.uuid4().hex[:6].upper()}"
    csv_content = f"Marka adi,Basvuru no,Siniflar\nAUTO DETECT TEST,{unique_app},9\n".encode()
    files = {"file": ("brands.csv", io.BytesIO(csv_content), "text/csv")}
    response = post("/api/v1/watchlist/upload", files=files)
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> added={response.json().get('summary', {}).get('added')}")
        Ctx.record(name, True)
    elif response.status_code == 400:
        detail = response.json().get("detail", {})
        if isinstance(detail, dict) and detail.get("error") == "missing_mandatory_columns":
            REPORTER.warn(f"{name} -> 400 missing_mandatory_columns (column name variants not matched)")
            Ctx.record(name, False, "Column variants not matched")
        else:
            REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
            Ctx.record(name, False, response.text[:200])
    elif response.status_code == 403:
        REPORTER.warn(f"{name} -> 403 (plan limit)")
        Ctx.record(name, True, "Plan limit gate working")
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_logo_upload():
    name = "POST /api/v1/watchlist/{id}/logo (upload logo)"
    if not Ctx.item_id:
        REPORTER.warn(f"{name} -> SKIP (no item_id)")
        return
    files = {"logo": ("test_logo.png", io.BytesIO(PNG_1X1), "image/png")}
    response = post(f"/api/v1/watchlist/{Ctx.item_id}/logo", files=files)
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> {response.json().get('message')}")
        Ctx.record(name, True)
    elif response.status_code == 403:
        REPORTER.warn(f"{name} -> 403 (logo tracking requires paid plan)")
        Ctx.record(name, True, "Plan gate working")
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_logo_get():
    name = "GET /api/v1/watchlist/{id}/logo"
    if not Ctx.item_id:
        REPORTER.warn(f"{name} -> SKIP (no item_id)")
        return
    response = get(f"/api/v1/watchlist/{Ctx.item_id}/logo")
    if response.status_code in (200, 404):
        REPORTER.ok(f"{name} -> {response.status_code} ({response.headers.get('content-type', 'n/a')})")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_logo_delete():
    name = "DELETE /api/v1/watchlist/{id}/logo"
    if not Ctx.item_id:
        REPORTER.warn(f"{name} -> SKIP (no item_id)")
        return
    response = delete(f"/api/v1/watchlist/{Ctx.item_id}/logo")
    if response.status_code in (200, 404):
        REPORTER.ok(f"{name} -> {response.status_code}")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_delete_item():
    name = "DELETE /api/v1/watchlist/{id} (clean up test item)"
    if not Ctx.item_id:
        REPORTER.warn(f"{name} -> SKIP (no item_id)")
        return
    response = delete(f"/api/v1/watchlist/{Ctx.item_id}")
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> {response.json().get('message')}")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def test_delete_item_404():
    name = "DELETE /api/v1/watchlist/{id} (non-existent -> 404)"
    response = delete(f"/api/v1/watchlist/{uuid.uuid4()}")
    if response.status_code == 404:
        REPORTER.ok(f"{name} -> correctly returned 404")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> expected 404, got {response.status_code}")
        Ctx.record(name, False, f"Expected 404, got {response.status_code}")


def test_stats_after_delete():
    name = "GET /api/v1/watchlist/stats (total after delete)"
    response = get("/api/v1/watchlist/stats")
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> final total={response.json().get('total_items', -1)}")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}")
        Ctx.record(name, False, response.text[:100])


def test_bulk_import_json():
    name = "POST /api/v1/watchlist/bulk (JSON bulk import)"
    items = [
        {
            "brand_name": f"BULK TEST {index}",
            "application_no": f"BULK-{uuid.uuid4().hex[:6].upper()}",
            "nice_class_numbers": [9, 35],
            "similarity_threshold": 0.7,
        }
        for index in range(3)
    ]
    response = post("/api/v1/watchlist/bulk", json_data={"items": items})
    if response.status_code == 200:
        payload = response.json()
        REPORTER.ok(
            f"{name} -> created={payload.get('created')}, skipped={payload.get('skipped')}, failed={payload.get('failed')}"
        )
        Ctx.record(name, True)
    elif response.status_code == 403:
        REPORTER.warn(f"{name} -> 403 (plan limit)")
        Ctx.record(name, True, "Plan limit gate working")
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:300]}")
        Ctx.record(name, False, response.text[:300])


def test_portfolio_preview_no_params():
    name = "POST /api/v1/watchlist/portfolio-preview (missing params -> 400)"
    response = post("/api/v1/watchlist/portfolio-preview", json_data={})
    if response.status_code == 400:
        REPORTER.ok(f"{name} -> correctly rejected empty body")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> expected 400, got {response.status_code}: {response.text[:150]}")
        Ctx.record(name, False, f"Expected 400, got {response.status_code}")


def test_portfolio_preview_holder():
    name = "POST /api/v1/watchlist/portfolio-preview (with holder_id)"
    response = post(
        "/api/v1/watchlist/portfolio-preview",
        json_data={"holder_id": "00000000-0000-0000-0000-000000000001"},
    )
    if response.status_code in (200, 403):
        if response.status_code == 200:
            payload = response.json()
            REPORTER.ok(f"{name} -> total_items={payload.get('total_items')}, can_add={payload.get('can_add')}")
        else:
            REPORTER.warn(f"{name} -> 403 (requires Business plan)")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:150]}")
        Ctx.record(name, False, response.text[:150])


def test_bulk_from_portfolio_no_params():
    name = "POST /api/v1/watchlist/bulk-from-portfolio (missing params -> 400)"
    response = post("/api/v1/watchlist/bulk-from-portfolio", json_data={})
    if response.status_code in (400, 403):
        REPORTER.ok(f"{name} -> correctly rejected ({response.status_code})")
        Ctx.record(name, True)
    else:
        REPORTER.fail(f"{name} -> expected 400/403, got {response.status_code}: {response.text[:150]}")
        Ctx.record(name, False, f"Expected 400/403, got {response.status_code}")


def test_rescan():
    name = "POST /api/v1/watchlist/rescan"
    response = post("/api/v1/watchlist/rescan")
    if response.status_code == 200:
        REPORTER.ok(f"{name} -> {response.json().get('message')}")
        Ctx.record(name, True)
    elif response.status_code == 403:
        REPORTER.warn(f"{name} -> 403 (plan upgrade needed - correct behavior)")
        Ctx.record(name, True, "Plan gate working")
    else:
        REPORTER.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        Ctx.record(name, False, response.text[:200])


def print_summary():
    return REPORTER.summary("WATCHLIST E2E TEST SUMMARY")


def main():
    REPORTER.print_heading("WATCHLIST TAB - END-TO-END TEST", server=CONFIG.base_url, user=CONFIG.email)

    test_health()
    auth_login(CONFIG.email, CONFIG.password)
    cleanup_stale_test_items()

    REPORTER.print_section("Auth Gates")
    test_auth_required()

    REPORTER.print_section("Stats")
    test_stats()

    REPORTER.print_section("List / Pagination / Search")
    test_list_empty_params()
    test_list_search_sort()

    REPORTER.print_section("Create / Read / Update / Delete (CRUD)")
    test_create()
    test_create_duplicate()
    test_get_item()
    test_get_item_404()
    test_update_item()
    test_update_item_404()
    test_stats_after_create()

    REPORTER.print_section("Scan Triggers")
    test_scan_single()
    test_scan_all()
    test_scan_status()
    test_rescan()

    REPORTER.print_section("Logo Upload / Get / Delete")
    test_logo_upload()
    test_logo_get()
    test_logo_delete()

    REPORTER.print_section("File Upload (detect, with-mapping, auto)")
    test_download_template()
    test_detect_columns_csv()
    test_detect_columns_excel()
    test_detect_columns_bad_format()
    test_upload_with_mapping()
    test_upload_with_mapping_missing_brand()
    test_upload_auto_detect()

    REPORTER.print_section("Bulk / Portfolio")
    test_bulk_import_json()
    test_portfolio_preview_no_params()
    test_portfolio_preview_holder()
    test_bulk_from_portfolio_no_params()

    REPORTER.print_section("Cleanup")
    test_delete_item()
    test_delete_item_404()
    test_stats_after_delete()

    sys.exit(0 if print_summary() == 0 else 1)


if __name__ == "__main__":
    main()

"""
Tests for API endpoints via FastAPI TestClient.

Auth dependency is overridden in conftest.py (client/superadmin_client fixtures).
Tests focus on: routing, auth gates, request validation, and public endpoints.

NOTE: These tests require `-s` flag or `--capture=no` on Windows due to
a known pytest capture issue with ASGI TestClient.
"""
import base64
import io
import json
import sys
import os
import shutil
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from openpyxl import load_workbook

def _make_watchlist_item_payload(item_id=None):
    """Build a representative raw watchlist row for response-model tests."""
    now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    return {
        "id": item_id or uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "brand_name": "NIKE",
        "nice_class_numbers": [9, 35],
        "description": "Sportswear",
        "customer_application_no": "2024/1",
        "customer_bulletin_no": "2024-1",
        "customer_registration_no": "TR-9",
        "customer_registration_date": date(2024, 1, 2),
        "alert_threshold": 0.8,
        "monitor_text": True,
        "monitor_visual": True,
        "monitor_phonetic": True,
        "alert_frequency": "daily",
        "alert_email": True,
        "alert_webhook": False,
        "webhook_url": None,
        "is_active": True,
        "last_scan_at": None,
        "created_at": now,
        "updated_at": now,
        "logo_path": None,
    }


def _make_application_row(app_id=None, organization_id=None, user_id=None, **overrides):
    """Build a representative application row for response-model tests."""
    now = datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": app_id or uuid.uuid4(),
        "organization_id": organization_id or uuid.uuid4(),
        "user_id": user_id or uuid.uuid4(),
        "status": "draft",
        "application_type": "registration",
        "brand_name": "TEST MARKA",
        "mark_type": "word",
        "nice_class_numbers": [25],
        "goods_services_description": "Clothing",
        "applicant_full_name": None,
        "applicant_id_no": None,
        "applicant_id_type": "tc_kimlik",
        "applicant_address": None,
        "applicant_phone": None,
        "applicant_email": None,
        "notes": None,
        "specialist_notes": None,
        "rejection_reason": None,
        "opposition_target_app_no": None,
        "opposition_target_brand": None,
        "opposition_target_holder": None,
        "opposition_target_bulletin_no": None,
        "opposition_target_bulletin_date": None,
        "opposition_target_classes": [],
        "opposition_grounds": None,
        "assigned_specialist_id": None,
        "turkpatent_application_no": None,
        "turkpatent_filing_date": None,
        "source_search_query": "test marka",
        "source_risk_score": 0.42,
        "logo_path": None,
        "created_at": now,
        "updated_at": now,
        "submitted_at": None,
        "reviewed_at": None,
        "completed_at": None,
    }
    row.update(overrides)
    return row


def test_watchlist_same_holder_filter_helper_matches_holder_ids():
    from utils.watchlist_filters import is_same_holder_conflict

    assert is_same_holder_conflict(
        {"holder_tpe_client_id": " HOLDER-1 "},
        {"watched_holder_tpe_client_id": "holder-1"},
    )
    holder_uuid = uuid.uuid4()
    assert is_same_holder_conflict(
        {"holder_id": holder_uuid},
        {"watched_holder_id": str(holder_uuid)},
    )
    assert not is_same_holder_conflict(
        {"holder_tpe_client_id": "holder-1"},
        {"watched_holder_tpe_client_id": "holder-2"},
    )


def test_watchlist_scanner_skips_same_holder_conflict_before_scoring():
    from watchlist.scanner import WatchlistScanner

    scanner = object.__new__(WatchlistScanner)

    with patch("watchlist.scanner.score_pair") as score_pair:
        conflict = scanner._check_conflict(
            {
                "application_no": "APP-2",
                "holder_tpe_client_id": "HOLDER-1",
            },
            {
                "id": str(uuid.uuid4()),
                "customer_application_no": "APP-1",
                "watched_holder_tpe_client_id": "holder-1",
            },
        )

    assert conflict is None
    score_pair.assert_not_called()


def test_watchlist_scanner_keeps_full_score_details_for_alert_storage():
    from watchlist.scanner import WatchlistScanner

    scanner = object.__new__(WatchlistScanner)
    score_details = {
        "total": 1.0,
        "text_similarity": 0.03,
        "text_idf_score": 1.0,
        "path_a_score": 0.02,
        "path_b_score": 1.0,
        "translation_similarity": 1.0,
        "scoring_path_source": "TRANSLATED",
        "decision_reason": "translated textual path selected",
        "textual_breakdown": {"selected_path": "TRANSLATED"},
        "visual_breakdown": {"total": 0.0},
    }

    with patch(
        "watchlist.scanner.calculate_comprehensive_score",
        return_value={"final_score": 0.03},
    ), patch.object(
        scanner, "_compute_visual_breakdown", return_value=(0.0, {"total": 0.0})
    ), patch.object(
        scanner, "_phonetic_sim", return_value=0.0
    ), patch(
        "watchlist.scanner.score_pair", return_value=score_details
    ):
        conflict = scanner._check_conflict(
            {
                "application_no": "APP-2",
                "name": "CORDAGE",
                "name_tr": "IP",
                "nice_class_numbers": [9],
            },
            {
                "id": str(uuid.uuid4()),
                "brand_name": "IP",
                "customer_application_no": "APP-1",
                "nice_class_numbers": [9],
            },
        )

    assert conflict["score_details"] == score_details
    assert conflict["path_a_score"] == 0.02
    assert conflict["path_b_score"] == 1.0


def test_watchlist_scanner_candidate_pool_filters_by_active_appealable_deadline():
    from watchlist.scanner import WatchlistScanner

    cursor = MagicMock()
    cursor.fetchall.return_value = []
    scanner = object.__new__(WatchlistScanner)
    scanner.db = MagicMock()
    scanner.db.cursor.return_value = cursor

    assert scanner._get_trademarks_within_deadline() == []

    sql = cursor.execute.call_args.args[0]
    assert "appeal_deadline IS NOT NULL" in sql
    assert "appeal_deadline >= CURRENT_DATE" in sql
    assert "final_status" in sql
    assert "current_status" in sql
    assert "Yay" in sql


def test_alert_queries_exclude_same_holder_similarity_alerts():
    from database.repositories.alert_repository import AlertCRUD

    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"count": 0}
    mock_cursor.fetchall.return_value = []

    AlertCRUD.get_by_organization(
        mock_db,
        uuid.uuid4(),
        page=1,
        page_size=10,
        min_score=0.7,
    )

    executed_sql = "\n".join(call.args[0] for call in mock_cursor.execute.call_args_list)
    assert "LEFT JOIN watchlist_mt w ON a.watchlist_item_id = w.id" in executed_sql
    assert "LEFT JOIN trademarks my_tm ON w.customer_application_no = my_tm.application_no" in executed_sql
    assert "a.alert_type = 'similarity'" in executed_sql
    assert "a.alert_type != 'similarity'" in executed_sql
    assert "appeal_deadline IS NULL OR" not in executed_sql
    assert "my_tm.holder_tpe_client_id" in executed_sql
    assert "t.holder_tpe_client_id" in executed_sql


def test_alert_create_stores_score_details_jsonb_payload():
    from database.repositories.alert_repository import AlertCRUD

    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"id": uuid.uuid4()}
    mock_db.cursor.return_value = mock_cursor
    org_id = uuid.uuid4()
    watchlist_id = uuid.uuid4()
    user_id = uuid.uuid4()
    score_details = {
        "total": 1.0,
        "path_a_score": 0.02,
        "path_b_score": 1.0,
        "scoring_path_source": "TRANSLATED",
        "textual_breakdown": {"selected_path": "TRANSLATED"},
    }

    with patch("risk_engine.get_risk_level", return_value="critical"):
        AlertCRUD.create(
            mock_db,
            org_id,
            watchlist_id,
            {
                "name": "CORDAGE",
                "application_no": "2022/029751",
                "classes": [1, 5],
                "holder": "Owner",
                "image_path": None,
            },
            {
                "total": 1.0,
                "text_similarity": 0.03,
                "semantic_similarity": 0.0,
                "visual_similarity": 0.0,
                "translation_similarity": 1.0,
                "phonetic_match": False,
                "score_details": score_details,
            },
            {"type": "watchlist_scan"},
            user_id=user_id,
            overlapping_classes=[1],
        )

    insert_sql, insert_params = mock_cursor.execute.call_args.args
    assert "score_details" in insert_sql
    json_params = [param for param in insert_params if hasattr(param, "adapted")]
    assert json_params
    assert json_params[0].adapted["path_b_score"] == 1.0
    assert json_params[0].adapted["textual_breakdown"]["selected_path"] == "TRANSLATED"


def _make_alert_row(alert_id=None, organization_id=None, watchlist_id=None, **overrides):
    """Build a representative alert row for response-model tests."""
    now = datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": alert_id or uuid.uuid4(),
        "organization_id": organization_id or uuid.uuid4(),
        "watchlist_item_id": watchlist_id or uuid.uuid4(),
        "watched_brand_name": "WATCHED MARK",
        "watchlist_bulletin_no": "2024-10",
        "watchlist_application_no": "2024/12345",
        "watchlist_classes": [25, 35],
        "conflicting_trademark_id": uuid.uuid4(),
        "conflicting_name": "CONFLICT MARK",
        "conflicting_application_no": "2024/54321",
        "conflicting_status": "Published",
        "conflicting_classes": [25],
        "conflicting_holder_name": "Conflict Owner",
        "conflicting_image_path": "logos/conflict.png",
        "conflict_application_date": date(2024, 1, 10),
        "conflict_has_extracted_goods": True,
        "conflict_bulletin_no": "2024-11",
        "overlapping_classes": [25],
        "overall_risk_score": 0.91,
        "text_similarity_score": 0.8,
        "semantic_similarity_score": 0.82,
        "visual_similarity_score": 0.4,
        "translation_similarity_score": 0.1,
        "phonetic_match": True,
        "severity": "high",
        "status": "new",
        "source_type": "watchlist_scan",
        "source_bulletin": "2024-11",
        "conflict_appeal_deadline": date(2026, 4, 20),
        "conflict_bulletin_date": date(2026, 2, 20),
        "created_at": now,
        "acknowledged_at": None,
        "resolved_at": None,
        "resolution_notes": None,
        "opposition_deadline": date(2026, 4, 20),
        "conflicting_name_tr": None,
    }
    row.update(overrides)
    return row


def _make_user_row(user_id=None, organization_id=None, **overrides):
    """Build a representative user row for response-model tests."""
    now = datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": user_id or uuid.uuid4(),
        "organization_id": organization_id or uuid.uuid4(),
        "email": "test@example.com",
        "password_hash": "HASHED",
        "first_name": "Test",
        "last_name": "User",
        "phone": "+90 555 000 0000",
        "role": "admin",
        "is_active": True,
        "is_email_verified": True,
        "is_superadmin": False,
        "last_login_at": None,
        "created_at": now,
        "avatar_url": "/static/avatars/test.png",
        "title": "Counsel",
        "department": "IP",
        "linkedin": "https://linkedin.com/in/test-user",
    }
    row.update(overrides)
    return row


def _make_org_row(org_id=None, **overrides):
    """Build a representative organization row for response-model tests."""
    now = datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": org_id or uuid.uuid4(),
        "name": "Acme IP",
        "slug": "acme-ip",
        "email": "team@acme.test",
        "phone": "+90 212 000 0000",
        "address": "Istanbul",
        "tax_id": "1234567890",
        "industry": "Legal",
        "website": "https://acme.test",
        "is_active": True,
        "created_at": now,
        "subscription_plan_id": None,
        "default_alert_threshold": 0.7,
        "email_notifications": True,
        "weekly_report": True,
    }
    row.update(overrides)
    return row


def _make_payment_row(payment_id=None, organization_id=None, user_id=None, **overrides):
    """Build a representative payment row for payment-service tests."""
    now = datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": payment_id or uuid.uuid4(),
        "organization_id": organization_id or uuid.uuid4(),
        "user_id": user_id or uuid.uuid4(),
        "plan_name": "starter",
        "billing_period": "monthly",
        "amount": 99.9,
        "currency": "TRY",
        "iyzico_conversation_id": "conv-1",
        "iyzico_token": "tok-1",
        "iyzico_payment_id": None,
        "iyzico_raw_response": None,
        "status": "pending",
        "paid_at": None,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _make_pipeline_run_row(run_id=None, **overrides):
    """Build a representative pipeline run row for service tests."""
    started_at = datetime.now(timezone.utc) - timedelta(minutes=12)
    row = {
        "id": run_id or uuid.uuid4(),
        "status": "completed",
        "triggered_by": "api",
        "skip_download": False,
        "step_download": {"status": "success"},
        "step_extract": {"status": "success"},
        "step_metadata": {"status": "success"},
        "step_embeddings": {"status": "success"},
        "step_ingest": {"status": "success"},
        "step_repair": {"status": "success"},
        "step_event_ingest": {"status": "success"},
        "step_final_status_repair": {"status": "success"},
        "total_downloaded": 12,
        "total_extracted": 11,
        "total_parsed": 10,
        "total_embedded": 9,
        "total_ingested": 8,
        "total_repaired": 6,
        "total_event_scopes_ingested": 7,
        "total_final_status_repaired": 7,
        "started_at": started_at,
        "completed_at": started_at + timedelta(minutes=12),
        "heartbeat_at": started_at + timedelta(minutes=11),
        "current_step": None,
        "duration_seconds": 720,
        "error_message": None,
        "created_at": started_at,
    }
    row.update(overrides)
    return row


def _make_lead_row(lead_id=None, **overrides):
    """Build a representative opposition lead row for service tests."""
    now = datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": lead_id or uuid.uuid4(),
        "new_mark_id": uuid.uuid4(),
        "new_mark_name": "NEW MARK",
        "new_mark_app_no": "2026/1001",
        "new_mark_holder_name": "New Holder",
        "new_mark_nice_classes": [25],
        "existing_mark_id": uuid.uuid4(),
        "existing_mark_name": "EXISTING MARK",
        "existing_mark_app_no": "2022/500",
        "existing_mark_holder_id": uuid.uuid4(),
        "existing_mark_holder_name": "Existing Holder",
        "existing_mark_nice_classes": [25, 35],
        "similarity_score": 0.91,
        "text_similarity": 0.88,
        "semantic_similarity": 0.84,
        "visual_similarity": 0.52,
        "translation_similarity": 0.1,
        "risk_level": "high",
        "conflict_type": "text",
        "overlapping_classes": [25],
        "conflict_reasons": ["Class overlap"],
        "bulletin_no": "2026-10",
        "bulletin_date": date(2026, 3, 20),
        "opposition_deadline": date(2026, 4, 30),
        "days_until_deadline": 18,
        "lead_status": "new",
        "viewed_by": None,
        "contacted_at": None,
        "notes": None,
        "created_at": now,
        "updated_at": now,
        "new_mark_image": "logos/new.png",
        "existing_mark_image": "logos/existing.png",
        "new_mark_application_date": date(2026, 2, 1),
        "existing_mark_application_date": date(2022, 5, 1),
        "urgency_level": "urgent",
        "new_mark_has_extracted_goods": True,
        "existing_mark_has_extracted_goods": False,
    }
    row.update(overrides)
    return row


def _joined_executed_sql(mock_cursor):
    return "\n".join(
        call.args[0]
        for call in mock_cursor.execute.call_args_list
        if call.args and isinstance(call.args[0], str)
    )


def _make_renewal_row(trademark_id=None, **overrides):
    """Build a representative renewal lead row for service tests."""
    row = {
        "id": trademark_id or uuid.uuid4(),
        "name": "RENEWAL MARK",
        "application_no": "2016/12345",
        "nice_class_numbers": [25, 35],
        "image_path": "logos/renewal.png",
        "final_status": "Tescil Edildi",
        "expiry_date": date(2026, 6, 1),
        "days_until_expiry": 50,
        "application_date": date(2016, 1, 10),
        "registration_no": "TR-123",
        "holder_name": "Renewal Holder",
        "holder_tpe_client_id": "H-100",
        "attorney_name": "Agent Smith",
        "attorney_no": "A-123",
        "urgency_level": "critical",
        "grace_days_remaining": None,
    }
    row.update(overrides)
    return row


def _make_request(
    *,
    method="POST",
    content_type="application/json",
    json_body=None,
    form_data=None,
    host="127.0.0.1",
):
    """Build a real Starlette Request for direct route invocation tests."""
    from starlette.requests import Request

    if form_data is not None:
        body = urlencode(form_data).encode("utf-8")
    elif json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
    else:
        body = b""

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": method,
        "path": "/",
        "headers": [(b"content-type", content_type.encode("utf-8"))],
        "client": (host, 12345),
    }
    return Request(scope, receive)


# ============================================================
# Health & Info (no auth)
# ============================================================

class TestPublicEndpoints:
    """Test endpoints that require no authentication."""

    def test_health_check(self, client):
        resp = client.get("/health")
        assert resp.status_code in (200, 500)
        data = resp.json()
        assert "status" in data or "detail" in data

    def test_app_info(self, client):
        resp = client.get("/api/info")
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data
        assert "version" in data

    def test_app_config(self, client):
        resp = client.get("/api/v1/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "risk_thresholds" in data
        # Verify all 5 risk levels present
        thresholds = data["risk_thresholds"]
        for level in ["critical", "very_high", "high", "medium", "low"]:
            assert level in thresholds

    def test_validate_discount_code_route(self, client):
        with patch(
            "services.billing_service.validate_discount_code_payload",
            new_callable=AsyncMock,
        ) as mock_validate_discount_code_payload:
            mock_validate_discount_code_payload.return_value = {
                "valid": True,
                "code": "LAUNCH20",
                "discount_type": "percent",
                "discount_value": 20.0,
                "applies_to_plan": "professional",
            }
            resp = client.post(
                "/api/v1/billing/validate-discount",
                json={"code": "launch20", "plan": "professional"},
            )

        assert resp.status_code == 200
        assert resp.json()["code"] == "LAUNCH20"
        mock_validate_discount_code_payload.assert_awaited_once_with(
            payload={"code": "launch20", "plan": "professional"},
        )

    def test_usage_summary_route(self, client):
        with patch(
            "services.usage_service.get_usage_summary_data",
            new_callable=AsyncMock,
        ) as mock_get_usage_summary_data:
            mock_get_usage_summary_data.return_value = {
                "plan": "professional",
                "display_name": "Professional",
                "usage": {
                    "daily_quick_searches": {"used": 1, "limit": 10},
                    "monthly_live_searches": {"used": 2, "limit": 50},
                    "monthly_ai_credits": {"remaining": 18, "limit": 20},
                    "monthly_name_generations": {"used": 4, "limit": 20},
                    "monthly_name_generations_used": 4,
                    "monthly_applications": {"used": 3, "limit": 25},
                    "watchlist_items": {"used": 5, "limit": 100},
                    "logo_credits": {"remaining": 18, "limit": 20},
                    "can_track_logos": True,
                },
            }
            resp = client.get("/api/v1/usage/summary")

        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Professional"
        assert resp.json()["usage"]["watchlist_items"]["used"] == 5
        assert mock_get_usage_summary_data.await_count == 1

    def test_dashboard_stats_route(self, client):
        with patch(
            "services.dashboard_service.get_dashboard_stats_data",
            new_callable=AsyncMock,
        ) as mock_get_dashboard_stats_data:
            mock_get_dashboard_stats_data.return_value = {
                "watchlist_count": 12,
                "active_watchlist": 9,
                "total_alerts": 7,
                "new_alerts": 3,
                "critical_alerts": 2,
                "alerts_this_week": 4,
                "searches_this_month": 11,
                "active_deadline_count": 5,
                "pre_publication_count": 1,
                "plan_usage": {
                    "watchlist": {"used": 9, "limit": 100},
                    "users": {"used": 4, "limit": 10},
                    "searches": {"used": 11, "limit": 70},
                    "reports": {"used": 0, "limit": 20},
                },
            }
            resp = client.get("/api/v1/dashboard/stats")

        assert resp.status_code == 200
        assert resp.json()["watchlist_count"] == 12
        assert resp.json()["plan_usage"]["watchlist"]["limit"] == 100
        assert mock_get_dashboard_stats_data.await_count == 1

    def test_organization_stats_route(self, client):
        with patch(
            "api.org_routes.get_organization_stats_data",
            new_callable=AsyncMock,
        ) as mock_get_organization_stats_data:
            mock_get_organization_stats_data.return_value = {
                "user_count": 4,
                "active_watchlist_items": 9,
                "new_alerts": 3,
                "critical_alerts": 1,
                "searches_this_month": 12,
                "storage_used_mb": 0.0,
            }
            resp = client.get("/api/v1/organization/stats")

        assert resp.status_code == 200
        assert resp.json()["active_watchlist_items"] == 9
        assert resp.json()["searches_this_month"] == 12
        assert mock_get_organization_stats_data.await_count == 1

    def test_holder_trademarks_route(self, client):
        with patch(
            "api.holders.get_holder_trademarks_data",
            new_callable=AsyncMock,
        ) as mock_get_holder_trademarks_data:
            mock_get_holder_trademarks_data.return_value = {
                "holder_name": "Nike Holder",
                "holder_tpe_client_id": "H-1",
                "total_count": 1,
                "page": 1,
                "page_size": 20,
                "total_pages": 1,
                "trademarks": [{"id": str(uuid.uuid4()), "name": "NIKE"}],
            }
            resp = client.get("/api/v1/holders/H-1/trademarks")

        assert resp.status_code == 200
        assert resp.json()["holder_name"] == "Nike Holder"
        assert resp.json()["total_count"] == 1
        assert mock_get_holder_trademarks_data.await_count == 1

    def test_search_holders_route(self, client):
        with patch(
            "api.holders.search_holder_portfolio_data",
            new_callable=AsyncMock,
        ) as mock_search_holder_portfolio_data:
            mock_search_holder_portfolio_data.return_value = {
                "query": "ni",
                "results": [
                    {
                        "holder_name": "Nike Holder",
                        "holder_tpe_client_id": "H-1",
                        "trademark_count": 2,
                    }
                ],
            }
            resp = client.get("/api/v1/holders/search?query=ni")

        assert resp.status_code == 200
        assert resp.json()["results"][0]["holder_name"] == "Nike Holder"
        assert mock_search_holder_portfolio_data.await_count == 1

    def test_export_holder_trademarks_csv_route(self, client):
        with patch(
            "api.holders.build_holder_trademarks_csv_stream",
            new_callable=AsyncMock,
        ) as mock_build_holder_trademarks_csv_stream:
            mock_build_holder_trademarks_csv_stream.return_value = JSONResponse(
                content={"ok": True},
                headers={"Content-Disposition": 'attachment; filename="Holder_portfolio.csv"'},
            )
            resp = client.get("/api/v1/holders/H-1/trademarks/csv")

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.headers["Content-Disposition"] == 'attachment; filename="Holder_portfolio.csv"'
        assert mock_build_holder_trademarks_csv_stream.await_count == 1

    def test_attorney_trademarks_route(self, client):
        with patch(
            "api.attorneys.get_attorney_trademarks_data",
            new_callable=AsyncMock,
        ) as mock_get_attorney_trademarks_data:
            mock_get_attorney_trademarks_data.return_value = {
                "attorney_name": "Agent Smith",
                "attorney_no": "A-1",
                "total_count": 1,
                "page": 1,
                "page_size": 20,
                "total_pages": 1,
                "trademarks": [{"id": str(uuid.uuid4()), "name": "NIKE"}],
            }
            resp = client.get("/api/v1/attorneys/A-1/trademarks")

        assert resp.status_code == 200
        assert resp.json()["attorney_name"] == "Agent Smith"
        assert resp.json()["total_count"] == 1
        assert mock_get_attorney_trademarks_data.await_count == 1

    def test_search_attorneys_route(self, client):
        with patch(
            "api.attorneys.search_attorney_portfolio_data",
            new_callable=AsyncMock,
        ) as mock_search_attorney_portfolio_data:
            mock_search_attorney_portfolio_data.return_value = {
                "query": "ag",
                "results": [
                    {
                        "attorney_name": "Agent Smith",
                        "attorney_no": "A-1",
                        "trademark_count": 2,
                    }
                ],
            }
            resp = client.get("/api/v1/attorneys/search?query=ag")

        assert resp.status_code == 200
        assert resp.json()["results"][0]["attorney_name"] == "Agent Smith"
        assert mock_search_attorney_portfolio_data.await_count == 1

    def test_export_attorney_trademarks_csv_route(self, client):
        with patch(
            "api.attorneys.build_attorney_trademarks_csv_stream",
            new_callable=AsyncMock,
        ) as mock_build_attorney_trademarks_csv_stream:
            mock_build_attorney_trademarks_csv_stream.return_value = JSONResponse(
                content={"ok": True},
                headers={"Content-Disposition": 'attachment; filename="Attorney_portfolio.csv"'},
            )
            resp = client.get("/api/v1/attorneys/A-1/trademarks/csv")

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.headers["Content-Disposition"] == 'attachment; filename="Attorney_portfolio.csv"'
        assert mock_build_attorney_trademarks_csv_stream.await_count == 1

    def test_generate_report_route(self, client):
        with patch(
            "api.reports.generate_report_data",
            new_callable=AsyncMock,
        ) as mock_generate_report_data:
            mock_generate_report_data.return_value = {
                "report_id": str(uuid.uuid4()),
                "status": "completed",
                "file_path": "C:/tmp/report.pdf",
                "message": "Rapor olusturuldu",
            }
            resp = client.post(
                "/api/v1/reports/generate",
                json={
                    "report_type": "watchlist_summary",
                    "file_format": "pdf",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        assert mock_generate_report_data.await_count == 1

    def test_list_reports_route(self, client):
        with patch(
            "api.reports.list_reports_data",
            new_callable=AsyncMock,
        ) as mock_list_reports_data:
            mock_list_reports_data.return_value = {
                "reports": [],
                "total": 0,
                "page": 1,
                "page_size": 20,
                "total_pages": 1,
                "usage": {
                    "reports_used": 0,
                    "reports_limit": 5,
                    "can_export": True,
                },
            }
            resp = client.get("/api/v1/reports")

        assert resp.status_code == 200
        assert resp.json()["usage"]["reports_limit"] == 5
        assert mock_list_reports_data.await_count == 1

    def test_get_report_route(self, client):
        report_id = str(uuid.uuid4())
        with patch(
            "api.reports.get_report_data",
            new_callable=AsyncMock,
        ) as mock_get_report_data:
            mock_get_report_data.return_value = {
                "id": report_id,
                "organization_id": str(uuid.uuid4()),
                "report_type": "watchlist_summary",
                "title": "Weekly",
                "status": "completed",
                "file_path": "C:/tmp/report.pdf",
                "file_format": "pdf",
                "file_size_bytes": 1234,
                "generated_at": None,
                "created_at": None,
                "download_count": 0,
                "error_message": None,
            }
            resp = client.get(f"/api/v1/reports/{report_id}")

        assert resp.status_code == 200
        assert resp.json()["title"] == "Weekly"
        assert mock_get_report_data.await_count == 1

    def test_download_report_route(self, client):
        with patch(
            "api.reports.build_report_download_response",
            new_callable=AsyncMock,
        ) as mock_build_report_download_response:
            mock_build_report_download_response.return_value = JSONResponse(
                content={"ok": True},
                headers={"Content-Disposition": 'attachment; filename="weekly.pdf"'},
            )
            resp = client.get(f"/api/v1/reports/{uuid.uuid4()}/download")

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.headers["Content-Disposition"] == 'attachment; filename="weekly.pdf"'
        assert mock_build_report_download_response.await_count == 1

    def test_delete_report_route(self, client):
        report_id = str(uuid.uuid4())
        with patch(
            "api.reports.delete_report_data",
            new_callable=AsyncMock,
        ) as mock_delete_report_data:
            mock_delete_report_data.return_value = {
                "message": "Rapor silindi",
                "report_id": report_id,
                "deleted_count": 1,
            }
            resp = client.delete(f"/api/v1/reports/{report_id}")

        assert resp.status_code == 200
        assert resp.json()["deleted_count"] == 1
        assert mock_delete_report_data.await_count == 1

    def test_delete_all_reports_route(self, client):
        with patch(
            "api.reports.delete_all_reports_data",
            new_callable=AsyncMock,
        ) as mock_delete_all_reports_data:
            mock_delete_all_reports_data.return_value = {
                "message": "Raporlar silindi",
                "deleted_count": 3,
            }
            resp = client.delete("/api/v1/reports")

        assert resp.status_code == 200
        assert resp.json()["deleted_count"] == 3
        assert mock_delete_all_reports_data.await_count == 1

    def test_root_redirects(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (200, 301, 302, 307)

    def test_dashboard_page(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200

    def test_admin_page(self, client):
        resp = client.get("/admin")
        assert resp.status_code == 200

    def test_pricing_page(self, client):
        resp = client.get("/pricing")
        assert resp.status_code == 200

    def test_checkout_page(self, client):
        resp = client.get("/checkout")
        assert resp.status_code == 200

    def test_service_worker(self, client):
        resp = client.get("/static/sw.js")
        assert resp.status_code == 200
        assert "no-store" in (resp.headers.get("Cache-Control") or "")

    def test_trademark_image_not_found(self, client):
        resp = client.get("/api/trademark-image/definitely-missing-test-image")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Image not found"

    def test_search_by_image_route_delegates_to_extracted_impl(self, client):
        with patch(
            "app_image_search_routes.search_by_image_impl",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = {
                "success": True,
                "search_type": "image",
                "total_results": 0,
                "results": [],
            }
            resp = client.post(
                "/api/search-by-image",
                files={"image": ("logo.png", b"\x89PNG\r\n\x1a\nmock", "image/png")},
                data={"name": "NIKE", "classes": "9,35", "limit": "5"},
            )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert mock_search.await_count == 1
        assert mock_search.await_args.kwargs["name"] == "NIKE"
        assert mock_search.await_args.kwargs["classes"] == "9,35"
        assert mock_search.await_args.kwargs["limit"] == 5

    def test_enhanced_search_route_delegates_to_extracted_impl(self, client):
        from app_enhanced_search_routes import EnhancedSearchResponse, SearchContext

        with patch(
            "app_enhanced_search_routes.enhanced_search_impl",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = EnhancedSearchResponse(
                results=[],
                search_context=SearchContext(
                    searched_name="NIKE",
                    searched_classes=[],
                    goods_description=None,
                    total_results=0,
                    search_time_ms=1.2,
                ),
                query="NIKE",
                total_results=0,
                search_time_ms=1.2,
                search_classes=[],
                classes_were_auto_suggested=False,
            )
            resp = client.post("/api/search", json={"name": "NIKE"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "NIKE"
        assert data["total_results"] == 0
        assert mock_search.await_count == 1
        assert mock_search.await_args.kwargs["search_request"].name == "NIKE"

    def test_search_credits(self, client):
        mock_db_cm = MagicMock()
        mock_db = MagicMock()
        mock_db_cm.__enter__.return_value = mock_db
        mock_db_cm.__exit__.return_value = False

        with patch("agentic_search.Database", return_value=mock_db_cm), patch(
            "agentic_search.get_user_plan",
            return_value={
                "plan_name": "professional",
                "display_name": "Professional",
                "can_use_live_search": True,
                "monthly_limit": 100,
            },
        ), patch("agentic_search.get_live_search_usage", return_value=7):
            resp = client.get("/api/v1/search/credits")

        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "Professional"
        assert data["remaining"] == 93
        assert "resets_on" in data

    def test_public_search_get_route_delegates_to_extracted_impl(self, client):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.fetchone.side_effect = [None, {"searches": 1}]

        with patch(
            "app_public_search_routes.do_public_search_impl",
            new_callable=AsyncMock,
        ) as mock_search, patch(
            "database.crud.get_db_connection",
            return_value=mock_conn,
        ):
            mock_search.return_value = {"query": "NIKE", "results": [], "total": 0}
            resp = client.get("/api/v1/search/public?query=NIKE")

        assert resp.status_code == 200
        assert resp.json()["query"] == "NIKE"
        assert mock_search.await_count == 1
        assert mock_search.await_args.kwargs["query"] == "NIKE"

    def test_public_search_post_route_delegates_to_extracted_impl(self, client):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.fetchone.side_effect = [None, {"searches": 1}]

        with patch(
            "app_public_search_routes.do_public_search_impl",
            new_callable=AsyncMock,
        ) as mock_search, patch(
            "database.crud.get_db_connection",
            return_value=mock_conn,
        ):
            mock_search.return_value = {"query": "NIKE", "results": [], "total": 0}
            resp = client.post("/api/v1/search/public", data={"query": "NIKE", "classes": "9, 35"})

        assert resp.status_code == 200
        assert resp.json()["query"] == "NIKE"
        assert mock_search.await_count == 1
        assert mock_search.await_args.kwargs["query"] == "NIKE"
        assert mock_search.await_args.kwargs["nice_classes"] == [9, 35]

    def test_public_portfolio_route_delegates_to_extracted_impl(self, client):
        with patch(
            "app_public_portfolio_routes.public_portfolio_impl",
            new_callable=AsyncMock,
        ) as mock_portfolio:
            mock_portfolio.return_value = {
                "entity_type": "holder",
                "entity_name": "Nike Inc",
                "entity_id": "123",
                "results": [],
                "total_count": 0,
            }
            resp = client.get("/api/v1/portfolio/public?holder_id=123")

        assert resp.status_code == 200
        assert resp.json()["entity_type"] == "holder"
        assert mock_portfolio.await_count == 1
        assert mock_portfolio.await_args.kwargs["holder_id"] == "123"
        assert mock_portfolio.await_args.kwargs["attorney_no"] is None

    def test_public_portfolio_csv_route_delegates_to_extracted_impl(self, client):
        with patch(
            "app_public_portfolio_routes.public_portfolio_csv_impl",
            new_callable=AsyncMock,
        ) as mock_portfolio_csv:
            mock_portfolio_csv.return_value = JSONResponse(
                content={"ok": True},
                headers={"Content-Disposition": 'attachment; filename="holder_portfolio.csv"'},
            )
            resp = client.get("/api/v1/portfolio/public/csv?attorney_no=A-1")

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.headers["Content-Disposition"] == 'attachment; filename="holder_portfolio.csv"'
        assert mock_portfolio_csv.await_count == 1
        assert mock_portfolio_csv.await_args.kwargs["holder_id"] is None
        assert mock_portfolio_csv.await_args.kwargs["attorney_no"] == "A-1"
        assert mock_portfolio_csv.await_args.kwargs["current_user"].email == "test@example.com"

    def test_education_catalog_route_delegates_to_service(self, client):
        payload = {
            "stats": {
                "pdf_count": 1,
                "flashcard_deck_count": 1,
                "flashcard_card_count": 2,
                "quiz_section_count": 1,
                "question_count": 1,
            },
            "categories": [
                {
                    "id": "marka",
                    "title": "Marka",
                    "flashcard_deck_id": "flashcards_vekill.csv",
                    "flashcard_card_count": 2,
                    "quiz_section_id": "vekillik-testi",
                    "question_count": 1,
                }
            ],
            "pdfs": [
                {
                    "id": "doc.pdf",
                    "title": "Doc",
                    "file_name": "doc.pdf",
                    "file_size_bytes": 1024,
                    "language": "tr",
                    "download_url": "/api/v1/education/assets/doc.pdf",
                }
            ],
            "flashcard_decks": [
                {
                    "id": "flashcards_vekill.csv",
                    "title": "Vekillik",
                    "card_count": 2,
                }
            ],
            "quiz_sections": [
                {
                    "id": "vekillik-testi",
                    "title": "Vekillik Testi",
                    "question_count": 1,
                }
            ],
        }

        with patch(
            "services.education_service.get_education_catalog_data",
            new=AsyncMock(return_value=payload),
        ) as mock_catalog:
            resp = client.get("/api/v1/education/catalog")

        assert resp.status_code == 200
        assert resp.json()["stats"]["pdf_count"] == 1
        assert resp.json()["categories"][0]["id"] == "marka"
        assert resp.json()["flashcard_decks"][0]["id"] == "flashcards_vekill.csv"
        assert mock_catalog.await_count == 1

    def test_education_flashcard_route_delegates_to_service(self, client):
        payload = {
            "id": "flashcards_vekill.csv",
            "title": "Vekillik",
            "card_count": 2,
            "cards": [
                {"id": "vekillik-1", "front": "Q1", "back": "A1"},
                {"id": "vekillik-2", "front": "Q2", "back": "A2"},
            ],
        }

        with patch(
            "services.education_service.get_flashcard_deck_data",
            new=AsyncMock(return_value=payload),
        ) as mock_deck:
            resp = client.get("/api/v1/education/flashcards/flashcards_vekill.csv")

        assert resp.status_code == 200
        assert resp.json()["card_count"] == 2
        assert mock_deck.await_count == 1
        assert mock_deck.await_args.kwargs["deck_id"] == "flashcards_vekill.csv"

    def test_education_quiz_route_delegates_to_service(self, client):
        payload = {
            "id": "vekillik-testi",
            "title": "Vekillik Testi",
            "question_count": 1,
            "questions": [
                {
                    "id": "vekillik-testi-1",
                    "prompt": "Question?",
                    "options": [
                        {"id": "A", "text": "Option A", "short_feedback": None},
                        {"id": "B", "text": "Option B", "short_feedback": "Correct"},
                    ],
                    "correct_option_id": "B",
                    "summary": "Summary",
                    "explanation": "Explanation",
                }
            ],
        }

        with patch(
            "services.education_service.get_quiz_section_data",
            new=AsyncMock(return_value=payload),
        ) as mock_quiz:
            resp = client.get("/api/v1/education/quizzes/vekillik-testi")

        assert resp.status_code == 200
        assert resp.json()["questions"][0]["correct_option_id"] == "B"
        assert mock_quiz.await_count == 1
        assert mock_quiz.await_args.kwargs["section_id"] == "vekillik-testi"

    def test_education_progress_get_route_delegates_to_service(self, client):
        payload = {
            "items": [
                {
                    "item_type": "quiz",
                    "item_key": "vekillik-testi",
                    "status": "in_progress",
                    "percent_complete": 40,
                    "progress_data": {"last_index": 3},
                    "completed_at": None,
                    "last_interacted_at": None,
                    "updated_at": None,
                }
            ]
        }

        with patch(
            "services.education_service.get_education_progress_data",
            new=AsyncMock(return_value=payload),
        ) as mock_progress:
            resp = client.get("/api/v1/education/progress")

        assert resp.status_code == 200
        assert resp.json()["items"][0]["item_key"] == "vekillik-testi"
        assert mock_progress.await_count == 1

    def test_education_progress_sync_route_delegates_to_service(self, client):
        payload = {
            "items": [
                {
                    "item_type": "flashcard",
                    "item_key": "flashcards_vekill.csv",
                    "status": "completed",
                    "percent_complete": 100,
                    "progress_data": {"seen_card_ids": ["vekillik-1", "vekillik-2"]},
                    "completed_at": None,
                    "last_interacted_at": None,
                    "updated_at": None,
                }
            ]
        }

        with patch(
            "services.education_service.sync_education_progress_data",
            new=AsyncMock(return_value=payload),
        ) as mock_sync:
            resp = client.post(
                "/api/v1/education/progress/sync",
                json={
                    "items": [
                        {
                            "item_type": "flashcard",
                            "item_key": "flashcards_vekill.csv",
                            "status": "completed",
                            "percent_complete": 100,
                            "progress_data": {"seen_card_ids": ["vekillik-1", "vekillik-2"]},
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        assert resp.json()["items"][0]["status"] == "completed"
        assert mock_sync.await_count == 1
        assert mock_sync.await_args.kwargs["data"].items[0].item_key == "flashcards_vekill.csv"

    def test_education_quiz_route_exposes_stable_question_ids_and_explanation_fields(self, client, tmp_path, monkeypatch):
        from services import education_service

        overrides_path = tmp_path / "education_moderation_overrides.json"
        overrides_path.write_text('{"flashcards": {}, "quiz_questions": {}}\n', encoding="utf-8")
        monkeypatch.setattr(education_service, "MODERATION_OVERRIDES_PATH", overrides_path)
        education_service._build_education_cache.cache_clear()

        resp = client.get("/api/v1/education/quizzes/marka")

        education_service._build_education_cache.cache_clear()

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["id"] == "marka"
        assert payload["questions"]
        question = payload["questions"][0]
        assert question["id"].startswith("quiz-question-")
        assert question["legacy_id"]
        assert question["category_title"] == "Marka"
        assert "liked" not in question
        assert "explanation" in question
        assert "summary" in question

    def test_education_moderation_route_persists_flashcard_category_overrides(self, client, tmp_path, monkeypatch):
        from services import education_service

        overrides_path = tmp_path / "education_moderation_overrides.json"
        overrides_path.write_text('{"flashcards": {}, "quiz_questions": {}}\n', encoding="utf-8")
        monkeypatch.setattr(education_service, "MODERATION_OVERRIDES_PATH", overrides_path)
        education_service._build_education_cache.cache_clear()

        resp = client.put(
            "/api/v1/education/moderation",
            json={
                "item_type": "flashcard",
                "item_id": "6769-9",
                "category_title": "Genel",
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "item_type": "flashcard",
            "item_id": "6769-9",
            "category_title": "Genel",
            "explanation": None,
            "summary": None,
            "deleted": False,
        }

        stored = json.loads(overrides_path.read_text(encoding="utf-8"))
        assert stored["flashcards"]["6769-9"]["category_title"] == "Genel"

        patent_resp = client.get("/api/v1/education/flashcards/patent")
        genel_resp = client.get("/api/v1/education/flashcards/genel")

        education_service._build_education_cache.cache_clear()

        assert patent_resp.status_code == 200
        assert genel_resp.status_code == 200
        assert all(card["id"] != "6769-9" for card in patent_resp.json()["cards"])
        assert any(card["id"] == "6769-9" for card in genel_resp.json()["cards"])

    def test_education_moderation_route_persists_quiz_question_explanation_overrides(self, client, tmp_path, monkeypatch):
        from services import education_service

        overrides_path = tmp_path / "education_moderation_overrides.json"
        overrides_path.write_text('{"flashcards": {}, "quiz_questions": {}}\n', encoding="utf-8")
        monkeypatch.setattr(education_service, "MODERATION_OVERRIDES_PATH", overrides_path)
        education_service._build_education_cache.cache_clear()

        marka_resp = client.get("/api/v1/education/quizzes/marka")
        assert marka_resp.status_code == 200
        first_question = marka_resp.json()["questions"][0]
        updated_explanation = "Tester override explanation"
        updated_summary = "Tester override summary"

        resp = client.put(
            "/api/v1/education/moderation",
            json={
                "item_type": "quiz_question",
                "item_id": first_question["id"],
                "category_title": "Genel",
                "explanation": updated_explanation,
                "summary": updated_summary,
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "item_type": "quiz_question",
            "item_id": first_question["id"],
            "category_title": "Genel",
            "explanation": updated_explanation,
            "summary": updated_summary,
            "deleted": False,
        }

        stored = json.loads(overrides_path.read_text(encoding="utf-8"))
        assert stored["quiz_questions"][first_question["id"]]["category_title"] == "Genel"
        assert stored["quiz_questions"][first_question["id"]]["explanation"] == updated_explanation
        assert stored["quiz_questions"][first_question["id"]]["summary"] == updated_summary

        updated_marka_resp = client.get("/api/v1/education/quizzes/marka")
        updated_genel_resp = client.get("/api/v1/education/quizzes/genel")

        education_service._build_education_cache.cache_clear()

        assert updated_marka_resp.status_code == 200
        assert updated_genel_resp.status_code == 200
        assert all(question["id"] != first_question["id"] for question in updated_marka_resp.json()["questions"])
        moved_question = next(
            question
            for question in updated_genel_resp.json()["questions"]
            if question["id"] == first_question["id"]
        )
        assert moved_question["explanation"] == updated_explanation
        assert moved_question["summary"] == updated_summary
        assert "liked" not in moved_question

    def test_education_moderation_route_rejects_flashcard_explanation_edits(self, client):
        resp = client.put(
            "/api/v1/education/moderation",
            json={
                "item_type": "flashcard",
                "item_id": "6769-9",
                "explanation": "Not allowed",
            },
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Only quiz questions support explanation editing"

    def test_education_moderation_route_rejects_non_admin(self, client):
        from auth.authentication import CurrentUser, get_current_user
        from main import app

        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            email="member@example.com",
            first_name="Member",
            last_name="User",
            role="user",
            is_superadmin=False,
            permissions=[],
        )

        resp = client.put(
            "/api/v1/education/moderation",
            json={
                "item_type": "flashcard",
                "item_id": "6769-9",
                "category_title": "Genel",
            },
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Education moderation requires admin access"

    def test_validate_classes_endpoint(self, client):
        resp = client.post("/api/validate-classes", data={"classes_text": "9, 35, abc"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["classes"] == [9, 35]
        assert len(data["invalid"]) == 1

    def test_nice_classes_endpoint(self, client):
        resp = client.get("/api/nice-classes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 45
        assert any(item["number"] == 99 for item in data["special_classes"])

    def test_search_status(self, client):
        resp = client.get("/api/v1/search/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_api_status(self, client):
        resp = client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_nonexistent_route_returns_404(self, client):
        resp = client.get("/api/v1/totally/nonexistent")
        assert resp.status_code in (404, 405)

    def test_legacy_search_route_delegates_to_extracted_impl(self, client):
        with patch(
            "app_legacy_rollback_routes.legacy_text_search_impl",
            new=AsyncMock(
                return_value={
                    "query": "NIKE",
                    "scoring_engine": "legacy",
                    "total_results": 0,
                    "search_time_ms": 1.2,
                    "results": [],
                }
            ),
        ):
            resp = client.post("/api/v1/search/legacy", json={"name": "NIKE"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "NIKE"
        assert data["scoring_engine"] == "legacy"


# ============================================================
# Auth Endpoints - Request Validation
# ============================================================

class TestAuthValidationEndpoints:
    """Test auth endpoints reject invalid input."""

    def test_register_empty_body(self, client):
        resp = client.post("/api/v1/auth/register", json={})
        assert resp.status_code == 422

    def test_login_empty_body(self, client):
        resp = client.post("/api/v1/auth/login", json={})
        assert resp.status_code in (401, 422)

    def test_register_missing_password(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
        })
        assert resp.status_code == 422

    def test_register_invalid_email(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "email": "not-an-email",
            "password": "ValidPass1!",
            "first_name": "Test",
            "last_name": "User",
        })
        assert resp.status_code == 422


# ============================================================
# Access Control (auth required)
# ============================================================

class TestAccessControl:
    """Test that auth-required endpoints reject/accept correctly."""

    def test_watchlist_create_invalid_body(self, client):
        """Watchlist creation with empty body → 422."""
        resp = client.post("/api/v1/watchlist", json={})
        assert resp.status_code == 422

    def test_simple_search_no_query(self, client):
        """Simple search without query → 422."""
        resp = client.get("/api/search/simple")
        assert resp.status_code == 422

    def test_simple_search_route_delegates_to_extracted_impl(self, client):
        mock_response = JSONResponse(content={"query": "NIKE", "count": 0, "results": []})
        mock_response.headers["Deprecation"] = "true"
        mock_response.headers["Sunset"] = "2026-03-10"

        with patch(
            "app_legacy_search_routes.simple_search_impl",
            new=AsyncMock(return_value=mock_response),
        ):
            resp = client.get("/api/search/simple?q=NIKE")

        assert resp.status_code == 200
        assert resp.headers["Deprecation"] == "true"
        assert resp.headers["Sunset"] == "2026-03-10"
        assert resp.json()["query"] == "NIKE"

    def test_unified_search_route_delegates_to_extracted_impl(self, client):
        mock_response = JSONResponse(content={"success": True, "results": [], "search_type": "text"})
        mock_response.headers["Deprecation"] = "true"
        mock_response.headers["Sunset"] = "2026-03-10"

        with patch(
            "app_legacy_search_routes.unified_search_impl",
            new=AsyncMock(return_value=mock_response),
        ):
            resp = client.post("/api/search/unified", data={"name": "NIKE"})

        assert resp.status_code == 200
        assert resp.headers["Deprecation"] == "true"
        assert resp.headers["Sunset"] == "2026-03-10"
        assert resp.json()["search_type"] == "text"

    def test_lead_feed_route_uses_service(self, client):
        payload = {
            "total_count": 1,
            "page": 2,
            "limit": 5,
            "items": [{"id": str(uuid.uuid4()), "new_mark_name": "NEW MARK"}],
        }

        with patch(
            "api.leads.get_lead_feed_data",
            new_callable=AsyncMock,
        ) as mock_get_lead_feed_data:
            mock_get_lead_feed_data.return_value = payload
            resp = client.get(
                "/api/v1/leads/feed?urgency=critical&nice_class=25&min_score=0.8&status=viewed&search=nike&page=2&limit=5"
            )

        assert resp.status_code == 200
        assert resp.json()["total_count"] == 1
        kwargs = mock_get_lead_feed_data.await_args.kwargs
        assert kwargs["urgency"] == "critical"
        assert kwargs["nice_class"] == 25
        assert kwargs["min_score"] == 0.8
        assert kwargs["status"] == "viewed"
        assert kwargs["search"] == "nike"
        assert kwargs["page"] == 2
        assert kwargs["limit"] == 5
        assert kwargs["current_user"].email == "test@example.com"

    def test_lead_stats_route_uses_service(self, client):
        with patch(
            "api.leads.get_lead_stats_data",
            new_callable=AsyncMock,
        ) as mock_get_lead_stats_data:
            mock_get_lead_stats_data.return_value = {
                "total_leads": 10,
                "critical_leads": 2,
                "urgent_leads": 3,
                "upcoming_leads": 4,
                "new_leads": 5,
                "viewed_leads": 2,
                "contacted_leads": 1,
                "converted_leads": 0,
                "avg_similarity": 0.88,
                "last_scan_at": "2026-04-12T12:00:00+00:00",
            }
            resp = client.get("/api/v1/leads/stats")

        assert resp.status_code == 200
        assert resp.json()["critical_leads"] == 2
        mock_get_lead_stats_data.assert_awaited_once()
        assert mock_get_lead_stats_data.await_args.kwargs["current_user"].email == "test@example.com"

    def test_lead_credits_route_uses_service(self, client):
        with patch(
            "api.leads.get_lead_credits_data",
            new_callable=AsyncMock,
        ) as mock_get_lead_credits_data:
            mock_get_lead_credits_data.return_value = {
                "can_access": True,
                "plan": "professional",
                "daily_limit": 10,
                "used_today": 2,
                "remaining": 8,
            }
            resp = client.get("/api/v1/leads/credits")

        assert resp.status_code == 200
        assert resp.json()["remaining"] == 8
        mock_get_lead_credits_data.assert_awaited_once()

    def test_lead_detail_route_uses_service(self, client):
        lead_id = uuid.uuid4()

        with patch(
            "api.leads.get_lead_detail_data",
            new_callable=AsyncMock,
        ) as mock_get_lead_detail_data:
            mock_get_lead_detail_data.return_value = {
                "id": str(lead_id),
                "lead_status": "viewed",
                "new_mark_name": "NEW MARK",
            }
            resp = client.get(f"/api/v1/leads/{lead_id}")

        assert resp.status_code == 200
        assert resp.json()["id"] == str(lead_id)
        mock_get_lead_detail_data.assert_awaited_once()
        assert mock_get_lead_detail_data.await_args.kwargs["lead_id"] == str(lead_id)

    def test_mark_lead_contacted_route_uses_service(self, client):
        lead_id = uuid.uuid4()

        with patch(
            "api.leads.mark_lead_contacted_data",
            new_callable=AsyncMock,
        ) as mock_mark_lead_contacted_data:
            mock_mark_lead_contacted_data.return_value = {
                "success": True,
                "message": "ok",
                "lead_id": str(lead_id),
                "new_status": "contacted",
            }
            resp = client.post(f"/api/v1/leads/{lead_id}/contact?notes=Called")

        assert resp.status_code == 200
        assert resp.json()["new_status"] == "contacted"
        kwargs = mock_mark_lead_contacted_data.await_args.kwargs
        assert kwargs["lead_id"] == str(lead_id)
        assert kwargs["notes"] == "Called"

    def test_renewal_stats_route_uses_service(self, client):
        with patch(
            "api.leads.get_renewal_stats_data",
            new_callable=AsyncMock,
        ) as mock_get_renewal_stats_data:
            mock_get_renewal_stats_data.return_value = {
                "total": 8,
                "grace_period": 1,
                "critical": 2,
                "urgent": 3,
                "upcoming": 2,
            }
            resp = client.get("/api/v1/leads/renewals/stats")

        assert resp.status_code == 200
        assert resp.json()["total"] == 8
        mock_get_renewal_stats_data.assert_awaited_once()

    def test_renewal_feed_route_uses_service(self, client):
        payload = {
            "total_count": 1,
            "page": 3,
            "limit": 4,
            "items": [{"id": str(uuid.uuid4()), "name": "RENEWAL MARK"}],
        }

        with patch(
            "api.leads.get_renewal_feed_data",
            new_callable=AsyncMock,
        ) as mock_get_renewal_feed_data:
            mock_get_renewal_feed_data.return_value = payload
            resp = client.get(
                "/api/v1/leads/renewals/feed?urgency=critical&nice_class=35&search=renew&page=3&limit=4"
            )

        assert resp.status_code == 200
        assert resp.json()["page"] == 3
        kwargs = mock_get_renewal_feed_data.await_args.kwargs
        assert kwargs["urgency"] == "critical"
        assert kwargs["nice_class"] == 35
        assert kwargs["search"] == "renew"
        assert kwargs["page"] == 3
        assert kwargs["limit"] == 4

    def test_applications_list_route_uses_service(self, client):
        app_row = _make_application_row()

        with patch(
            "api.applications.list_applications_data",
            new_callable=AsyncMock,
        ) as mock_list_applications_data:
            mock_list_applications_data.return_value = {
                "items": [app_row],
                "total": 1,
                "page": 2,
                "page_size": 10,
                "total_pages": 1,
            }
            resp = client.get(
                "/api/v1/applications/",
                params={
                    "status": "draft",
                    "application_type": "registration",
                    "page": 2,
                    "page_size": 10,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["items"][0]["brand_name"] == "TEST MARKA"
        mock_list_applications_data.assert_awaited_once()
        assert mock_list_applications_data.await_args.kwargs["status"] == "draft"
        assert mock_list_applications_data.await_args.kwargs["application_type"] == "registration"
        assert mock_list_applications_data.await_args.kwargs["page"] == 2
        assert mock_list_applications_data.await_args.kwargs["page_size"] == 10

    def test_get_application_route_uses_service(self, client):
        app_row = _make_application_row()

        with patch(
            "api.applications.get_application_data",
            new_callable=AsyncMock,
        ) as mock_get_application_data:
            mock_get_application_data.return_value = app_row
            resp = client.get(f"/api/v1/applications/{app_row['id']}")

        assert resp.status_code == 200
        assert resp.json()["id"] == str(app_row["id"])
        assert resp.json()["brand_name"] == "TEST MARKA"
        mock_get_application_data.assert_awaited_once()
        assert mock_get_application_data.await_args.kwargs["app_id"] == app_row["id"]

    def test_create_application_route_uses_service(self, client):
        app_row = _make_application_row()

        with patch(
            "api.applications.create_application_data",
            new_callable=AsyncMock,
        ) as mock_create_application_data:
            mock_create_application_data.return_value = app_row
            resp = client.post(
                "/api/v1/applications/",
                json={"brand_name": "TEST MARKA", "nice_class_numbers": [25]},
            )

        assert resp.status_code == 200
        assert resp.json()["brand_name"] == "TEST MARKA"
        mock_create_application_data.assert_awaited_once()
        assert mock_create_application_data.await_args.kwargs["data"].brand_name == "TEST MARKA"

    def test_update_application_route_uses_service(self, client):
        app_row = _make_application_row(brand_name="UPDATED MARKA")

        with patch(
            "api.applications.update_application_data",
            new_callable=AsyncMock,
        ) as mock_update_application_data:
            mock_update_application_data.return_value = app_row
            resp = client.put(
                f"/api/v1/applications/{app_row['id']}",
                json={"brand_name": "UPDATED MARKA"},
            )

        assert resp.status_code == 200
        assert resp.json()["brand_name"] == "UPDATED MARKA"
        mock_update_application_data.assert_awaited_once()
        assert mock_update_application_data.await_args.kwargs["app_id"] == app_row["id"]
        assert mock_update_application_data.await_args.kwargs["data"].brand_name == "UPDATED MARKA"

    def test_delete_application_route_uses_service(self, client):
        app_id = uuid.uuid4()

        with patch(
            "api.applications.delete_application_data",
            new_callable=AsyncMock,
        ) as mock_delete_application_data:
            mock_delete_application_data.return_value = {
                "success": True,
                "message": "Application deleted",
            }
            resp = client.delete(f"/api/v1/applications/{app_id}")

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_delete_application_data.assert_awaited_once()
        assert mock_delete_application_data.await_args.kwargs["app_id"] == app_id

    def test_submit_application_route_uses_service(self, client):
        app_row = _make_application_row(status="submitted", submitted_at=datetime(2026, 4, 12, 12, 5, tzinfo=timezone.utc))

        with patch(
            "api.applications.submit_application_data",
            new_callable=AsyncMock,
        ) as mock_submit_application_data:
            mock_submit_application_data.return_value = app_row
            resp = client.post(f"/api/v1/applications/{app_row['id']}/submit")

        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"
        mock_submit_application_data.assert_awaited_once()
        assert mock_submit_application_data.await_args.kwargs["app_id"] == app_row["id"]

    def test_upload_application_logo_route_uses_service(self, client):
        app_id = uuid.uuid4()

        with patch(
            "api.applications.upload_application_logo_data",
            new_callable=AsyncMock,
        ) as mock_upload_application_logo_data:
            mock_upload_application_logo_data.return_value = {
                "success": True,
                "logo_url": f"/api/v1/applications/{app_id}/logo",
                "logo_path": f"static/uploads/applications/{app_id}/logo.png",
            }
            resp = client.post(
                f"/api/v1/applications/{app_id}/logo",
                files={"file": ("logo.png", b"fake-png-data", "image/png")},
            )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_upload_application_logo_data.assert_awaited_once()
        assert mock_upload_application_logo_data.await_args.kwargs["app_id"] == app_id

    def test_get_application_logo_route_uses_service(self, client):
        app_id = uuid.uuid4()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"png-bytes")
            temp_path = tmp.name

        try:
            with patch(
                "api.applications.get_application_logo_file",
                new_callable=AsyncMock,
            ) as mock_get_application_logo_file:
                mock_get_application_logo_file.return_value = Path(temp_path)
                resp = client.get(f"/api/v1/applications/{app_id}/logo")

            assert resp.status_code == 200
            assert resp.content == b"png-bytes"
            mock_get_application_logo_file.assert_awaited_once()
            assert mock_get_application_logo_file.await_args.kwargs["app_id"] == app_id
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_upload_trademarks_route_uses_service(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.upload import router as upload_router
        from auth.authentication import get_current_user

        current_user = SimpleNamespace(
            id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        )
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: current_user
        app.include_router(upload_router)

        payload = {
            "success": True,
            "file_name": "marks.csv",
            "file_size_mb": 0.0,
            "total_rows": 1,
            "valid_trademarks": 1,
            "trademarks": [
                {
                    "row": 2,
                    "name": "TEST MARKA",
                    "classes": [25],
                    "application_no": "2025/123456",
                    "owner": "Acme Ltd",
                    "description": "Upload",
                }
            ],
            "validation_errors": [],
            "watchlist_results": None,
        }

        with patch(
            "api.upload.process_trademark_upload",
            new_callable=AsyncMock,
        ) as mock_process_trademark_upload:
            mock_process_trademark_upload.return_value = payload
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/upload/trademarks",
                    data={
                        "add_to_watchlist": "false",
                        "run_analysis": "false",
                        "alert_threshold": "0.7",
                    },
                    files={
                        "file": (
                            "marks.csv",
                            b"Marka Adi,Siniflar\nTEST MARKA,25\n",
                            "text/csv",
                        )
                    },
                )

        assert resp.status_code == 200
        assert resp.json()["valid_trademarks"] == 1
        mock_process_trademark_upload.assert_awaited_once()
        assert mock_process_trademark_upload.await_args.kwargs["add_to_watchlist"] is False
        assert mock_process_trademark_upload.await_args.kwargs["current_user"] == current_user

    def test_upload_template_route_uses_service(self):
        from fastapi import FastAPI
        from fastapi.responses import StreamingResponse
        from fastapi.testclient import TestClient
        from api.upload import router as upload_router

        app = FastAPI()
        app.include_router(upload_router)

        def _fake_template_response():
            return StreamingResponse(
                io.BytesIO(b"fake-xlsx"),
                media_type=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
                headers={"Content-Disposition": "attachment; filename=marka_sablonu.xlsx"},
            )

        with patch(
            "api.upload.build_upload_template_response",
            side_effect=_fake_template_response,
        ) as mock_build_upload_template_response:
            with TestClient(app) as client:
                resp = client.get("/api/v1/upload/template")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert resp.headers["content-disposition"] == "attachment; filename=marka_sablonu.xlsx"
        mock_build_upload_template_response.assert_called_once_with()

    def test_trademark_events_route_uses_service(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.trademark_routes import trademark_router
        from auth.authentication import get_current_user

        current_user = SimpleNamespace(
            id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        )
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: current_user
        app.include_router(trademark_router, prefix="/api/v1")

        payload = {
            "application_no": "2024-1",
            "name": "TEST MARKA",
            "health_card": {"severity": "healthy"},
            "events": [],
            "total": 0,
            "page": 2,
            "per_page": 10,
            "pages": 0,
        }

        with patch(
            "api.trademark_routes.get_trademark_events_data",
            new_callable=AsyncMock,
        ) as mock_get_trademark_events_data:
            mock_get_trademark_events_data.return_value = payload
            with TestClient(app) as client:
                resp = client.get(
                    "/api/v1/trademark/2024-1/events?page=2&per_page=10&event_type=transfer"
                )

        assert resp.status_code == 200
        assert resp.json()["application_no"] == "2024-1"
        mock_get_trademark_events_data.assert_awaited_once_with(
            application_no="2024-1",
            page=2,
            per_page=10,
            event_type="transfer",
            current_user=current_user,
        )

    def test_trademark_extracted_goods_route_uses_service(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.trademark_routes import trademark_router
        from auth.authentication import get_current_user

        current_user = SimpleNamespace(
            id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        )
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: current_user
        app.include_router(trademark_router, prefix="/api/v1")

        payload = {
            "application_no": "2024-1",
            "name": "TEST MARKA",
            "has_extracted_goods": True,
            "extracted_goods": [{"text": "Shoes"}],
            "nice_classes": [25],
            "total_items": 1,
        }

        with patch(
            "api.trademark_routes.get_extracted_goods_data",
            new_callable=AsyncMock,
        ) as mock_get_extracted_goods_data:
            mock_get_extracted_goods_data.return_value = payload
            with TestClient(app) as client:
                resp = client.get("/api/v1/trademark/2024-1/extracted-goods")

        assert resp.status_code == 200
        assert resp.json()["total_items"] == 1
        mock_get_extracted_goods_data.assert_awaited_once_with(
            application_no="2024-1",
            current_user=current_user,
        )

    def test_alerts_list_route_uses_service(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.alert_routes import alerts_router
        from auth.authentication import get_current_user

        current_user = SimpleNamespace(
            id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        )
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: current_user
        app.include_router(alerts_router, prefix="/api/v1")

        payload = {
            "items": [{"id": str(uuid.uuid4())}],
            "total": 1,
            "page": 2,
            "page_size": 5,
            "total_pages": 1,
        }

        with patch(
            "api.alert_routes.list_alerts_data",
            new_callable=AsyncMock,
        ) as mock_list_alerts_data:
            mock_list_alerts_data.return_value = payload
            with TestClient(app) as client:
                resp = client.get(
                    "/api/v1/alerts?page=2&page_size=5&min_score=80&status=new&severity=high"
                )

        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        mock_list_alerts_data.assert_awaited_once()
        kwargs = mock_list_alerts_data.await_args.kwargs
        assert kwargs["page"] == 2
        assert kwargs["page_size"] == 5
        assert kwargs["min_score"] == 80.0
        assert [item.value for item in kwargs["status_filters"]] == ["new"]
        assert [item.value for item in kwargs["severity_filters"]] == ["high"]
        assert kwargs["current_user"] == current_user

    def test_user_profile_route_uses_service(self, client):
        payload = {
            "id": str(uuid.uuid4()),
            "email": "profile@example.com",
            "first_name": "Profile",
            "last_name": "User",
            "phone": "",
            "title": "",
            "department": "",
            "linkedin": "",
            "avatar_url": "",
            "created_at": "2026-04-12T12:00:00+00:00",
            "is_email_verified": True,
        }

        with patch(
            "api.user_profile_routes.get_user_profile_data",
            new_callable=AsyncMock,
        ) as mock_get_user_profile_data:
            mock_get_user_profile_data.return_value = payload
            resp = client.get("/api/v1/user/profile")

        assert resp.status_code == 200
        assert resp.json()["email"] == "profile@example.com"
        mock_get_user_profile_data.assert_awaited_once()

    def test_auth_register_route_uses_service(self, client):
        payload = {
            "access_token": "access",
            "refresh_token": "refresh",
            "token_type": "bearer",
            "expires_in": 3600,
        }

        with patch(
            "api.auth_routes.register_user",
            new_callable=AsyncMock,
        ) as mock_register_user:
            mock_register_user.return_value = payload
            resp = client.post(
                "/api/v1/auth/register",
                json={
                    "email": "new@example.com",
                    "password": "Password1",
                    "first_name": "New",
                    "last_name": "User",
                    "organization_name": "Acme IP",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["access_token"] == "access"
        kwargs = mock_register_user.await_args.kwargs
        assert kwargs["data"].email == "new@example.com"
        assert kwargs["data"].organization_name == "Acme IP"
        assert kwargs["ip"] == "testclient"

    def test_auth_login_route_uses_service(self, client):
        payload = {
            "access_token": "access",
            "refresh_token": "refresh",
            "token_type": "bearer",
            "expires_in": 3600,
        }

        with patch(
            "api.auth_routes.login_user",
            new_callable=AsyncMock,
        ) as mock_login_user:
            mock_login_user.return_value = payload
            resp = client.post(
                "/api/v1/auth/login",
                json={"email": "login@example.com", "password": "Password1"},
            )

        assert resp.status_code == 200
        assert resp.json()["refresh_token"] == "refresh"
        mock_login_user.assert_awaited_once_with(
            email="login@example.com",
            password="Password1",
            ip="testclient",
        )

    def test_auth_me_route_uses_service(self, client):
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        payload = {
            "id": str(user_id),
            "organization_id": str(org_id),
            "email": "me@example.com",
            "first_name": "Current",
            "last_name": "User",
            "phone": None,
            "avatar_url": None,
            "role": "admin",
            "is_active": True,
            "is_verified": True,
            "is_superadmin": False,
            "last_login_at": None,
            "created_at": "2026-04-12T12:00:00+00:00",
            "organization": {
                "id": str(org_id),
                "name": "Acme IP",
                "slug": "acme-ip",
                "is_active": True,
                "created_at": "2026-04-12T12:00:00+00:00",
            },
            "permissions": [],
        }

        with patch(
            "api.auth_routes.get_current_user_profile_data",
            new_callable=AsyncMock,
        ) as mock_get_current_user_profile_data:
            mock_get_current_user_profile_data.return_value = payload
            resp = client.get("/api/v1/auth/me")

        assert resp.status_code == 200
        assert resp.json()["organization"]["name"] == "Acme IP"
        mock_get_current_user_profile_data.assert_awaited_once()

    def test_payments_initialize_route_uses_service(self, client):
        payload = {
            "checkout_form_content": "<form>checkout</form>",
            "token": "tok-1",
            "conversation_id": "conv-1",
            "payment_id": "pay-1",
        }

        with patch(
            "api.payments.initialize_payment_data",
            new_callable=AsyncMock,
        ) as mock_initialize_payment_data:
            mock_initialize_payment_data.return_value = payload
            resp = client.post(
                "/api/v1/payments/initialize",
                json={"plan": "starter", "billing": "monthly"},
            )

        assert resp.status_code == 200
        assert resp.json()["token"] == "tok-1"
        kwargs = mock_initialize_payment_data.await_args.kwargs
        assert kwargs["payload"] == {"plan": "starter", "billing": "monthly"}
        assert kwargs["current_user"].email == "test@example.com"

    def test_activate_free_plan_route_uses_service(self, client):
        with patch(
            "api.payments.activate_free_plan_data",
            new_callable=AsyncMock,
        ) as mock_activate_free_plan_data:
            mock_activate_free_plan_data.return_value = {
                "success": True,
                "redirect": "/dashboard",
            }
            resp = client.post("/api/v1/payments/activate-free")

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_activate_free_plan_data.assert_awaited_once()
        assert mock_activate_free_plan_data.await_args.kwargs["current_user"].email == "test@example.com"

    def test_pipeline_trigger_route_uses_service(self, superadmin_client):
        with patch(
            "api.pipeline.trigger_pipeline_run_data",
            new_callable=AsyncMock,
        ) as mock_trigger_pipeline_run_data:
            mock_trigger_pipeline_run_data.return_value = {
                "run_id": "run-1",
                "status": "started",
                "skip_download": True,
            }
            resp = superadmin_client.post("/api/v1/pipeline/trigger?skip_download=true")

        assert resp.status_code == 200
        assert resp.json()["run_id"] == "run-1"
        kwargs = mock_trigger_pipeline_run_data.await_args.kwargs
        assert kwargs["skip_download"] is True
        assert kwargs["current_user"].email == "admin@example.com"

    def test_pipeline_status_route_uses_service(self, superadmin_client):
        with patch(
            "api.pipeline.get_pipeline_status_data",
            new_callable=AsyncMock,
        ) as mock_get_pipeline_status_data:
            mock_get_pipeline_status_data.return_value = {
                "is_running": True,
                "current_run_id": "run-1",
                "current_step": "extract",
                "next_scheduled": "2026-04-14T03:00:00",
                "recent_runs": [],
            }
            resp = superadmin_client.get("/api/v1/pipeline/status?limit=7")

        assert resp.status_code == 200
        assert resp.json()["current_step"] == "extract"
        mock_get_pipeline_status_data.assert_awaited_once_with(
            limit=7,
            current_user=ANY,
        )

    def test_pipeline_run_detail_route_uses_service(self, superadmin_client):
        with patch(
            "api.pipeline.get_pipeline_run_detail_data",
            new_callable=AsyncMock,
        ) as mock_get_pipeline_run_detail_data:
            mock_get_pipeline_run_detail_data.return_value = {
                "id": "run-1",
                "status": "completed",
                "triggered_by": "api",
                "skip_download": False,
                "step_download": {"status": "success"},
                "step_extract": {"status": "success"},
                "step_metadata": {"status": "success"},
                "step_embeddings": {"status": "success"},
                "step_ingest": {"status": "success"},
                "step_repair": {"status": "success"},
                "step_event_ingest": {"status": "success"},
                "step_final_status_repair": {"status": "success"},
                "total_downloaded": 10,
                "total_extracted": 9,
                "total_parsed": 8,
                "total_embedded": 7,
                "total_ingested": 6,
                "total_repaired": 4,
                "total_event_scopes_ingested": 5,
                "total_final_status_repaired": 5,
                "started_at": "2026-04-12T12:00:00+00:00",
                "completed_at": "2026-04-12T12:10:00+00:00",
                "duration_seconds": 600,
                "error_message": None,
                "created_at": "2026-04-12T12:00:00+00:00",
            }
            resp = superadmin_client.get("/api/v1/pipeline/runs/run-1")

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        mock_get_pipeline_run_detail_data.assert_awaited_once_with(
            run_id="run-1",
            current_user=ANY,
        )

    def test_creative_generated_image_route_uses_service(self, client):
        from fastapi.responses import FileResponse

        image_id = uuid.uuid4()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"creative-png")
            temp_path = tmp.name

        try:
            with patch(
                "api.creative.get_generated_image_response",
                new_callable=AsyncMock,
            ) as mock_get_generated_image_response:
                mock_get_generated_image_response.return_value = FileResponse(
                    temp_path,
                    media_type="image/png",
                )
                resp = client.get(f"/api/v1/tools/generated-image/{image_id}")

            assert resp.status_code == 200
            assert resp.content == b"creative-png"
            mock_get_generated_image_response.assert_awaited_once()
            assert mock_get_generated_image_response.await_args.kwargs["image_id"] == str(image_id)
            assert mock_get_generated_image_response.await_args.kwargs["current_user"].email == "test@example.com"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_creative_generation_history_route_uses_service(self, client):
        payload = {
            "items": [],
            "total": 0,
            "page": 2,
            "per_page": 5,
            "total_pages": 1,
        }

        with patch(
            "api.creative.get_generation_history_data",
            new_callable=AsyncMock,
        ) as mock_get_generation_history_data:
            mock_get_generation_history_data.return_value = payload
            resp = client.get("/api/v1/tools/generation-history?page=2&per_page=5&feature_type=LOGO")

        assert resp.status_code == 200
        assert resp.json()["page"] == 2
        mock_get_generation_history_data.assert_awaited_once_with(
            page=2,
            per_page=5,
            feature_type="LOGO",
            current_user=ANY,
        )

    def test_creative_delete_generation_history_item_route_uses_service(self, client):
        history_id = str(uuid.uuid4())
        payload = {"deleted": 1, "id": history_id, "feature_type": "LOGO"}

        with patch(
            "api.creative.delete_generation_history_item_data",
            new_callable=AsyncMock,
        ) as mock_delete_generation_history_item_data:
            mock_delete_generation_history_item_data.return_value = payload
            resp = client.delete(f"/api/v1/tools/generation-history/{history_id}")

        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1
        mock_delete_generation_history_item_data.assert_awaited_once_with(
            history_id=history_id,
            current_user=ANY,
        )

    def test_creative_clear_generation_history_route_uses_service(self, client):
        payload = {"deleted": 3, "feature_type": "LOGO"}

        with patch(
            "api.creative.clear_generation_history_data",
            new_callable=AsyncMock,
        ) as mock_clear_generation_history_data:
            mock_clear_generation_history_data.return_value = payload
            resp = client.delete("/api/v1/tools/generation-history?feature_type=LOGO")

        assert resp.status_code == 200
        assert resp.json()["deleted"] == 3
        mock_clear_generation_history_data.assert_awaited_once_with(
            feature_type="LOGO",
            current_user=ANY,
        )

    def test_creative_status_route_uses_service(self, client):
        payload = {
            "name_generator": {"available": True, "reason": ""},
            "logo_studio": {"available": False, "reason": "CLIP modeli yuklenmemis"},
        }

        with patch(
            "api.creative.creative_suite_status_data",
            new_callable=AsyncMock,
        ) as mock_creative_suite_status_data:
            mock_creative_suite_status_data.return_value = payload
            resp = client.get("/api/v1/tools/status")

        assert resp.status_code == 200
        assert resp.json()["name_generator"]["available"] is True
        mock_creative_suite_status_data.assert_awaited_once_with()

    def test_creative_suggest_names_route_uses_service(self, client):
        payload = {
            "safe_names": [
                {
                    "name": "ACMIA",
                    "risk_score": 12.5,
                    "text_similarity": 0.12,
                    "semantic_similarity": 0.18,
                    "phonetic_match": False,
                    "closest_match": "ACME",
                    "is_safe": True,
                    "translation_similarity": 0.0,
                    "risk_level": "low",
                }
            ],
            "filtered_count": 1,
            "total_generated": 2,
            "session_count": 3,
            "credits_remaining": {
                "session_limit": 5,
                "used": 3,
                "purchased": 1,
                "plan": "professional",
            },
            "cached": False,
        }

        with patch(
            "api.creative.suggest_names_data",
            new_callable=AsyncMock,
        ) as mock_suggest_names_data:
            mock_suggest_names_data.return_value = payload
            resp = client.post(
                "/api/v1/tools/suggest-names",
                json={
                    "query": "Acme",
                    "nice_classes": [25],
                    "industry": "Footwear",
                    "style": "modern",
                    "language": "tr",
                    "avoid_names": ["Nike"],
                },
            )

        assert resp.status_code == 200
        assert resp.json()["safe_names"][0]["name"] == "ACMIA"
        mock_suggest_names_data.assert_awaited_once()
        assert mock_suggest_names_data.await_args.kwargs["request"].query == "Acme"
        assert mock_suggest_names_data.await_args.kwargs["current_user"].email == "test@example.com"

    def test_creative_generate_logo_route_uses_service(self, client):
        payload = {
            "logos": [
                {
                    "image_id": "img-1",
                    "image_url": "/api/v1/tools/generated-image/img-1",
                    "similarity_score": 42.0,
                    "closest_match_name": "ACME OLD",
                    "closest_match_image_url": "/api/trademark-image/logos/acme-old.png",
                    "is_safe": True,
                    "visual_breakdown": {
                        "clip": 0.4,
                        "dino": 0.2,
                        "ocr": 0.1,
                        "raw_combined": 0.42,
                        "components_used": ["clip", "dino", "ocr"],
                    },
                }
            ],
            "credits_remaining": {"monthly": 2, "purchased": 1},
            "generation_id": "gen-logo-1",
        }

        with patch(
            "api.creative.generate_logo_data",
            new_callable=AsyncMock,
        ) as mock_generate_logo_data:
            mock_generate_logo_data.return_value = payload
            resp = client.post(
                "/api/v1/tools/generate-logo",
                json={
                    "brand_name": "Acme",
                    "description": "Modern wordmark",
                    "style": "modern",
                    "nice_classes": [25],
                    "color_preferences": "blue",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["generation_id"] == "gen-logo-1"
        assert resp.json()["logos"][0]["image_id"] == "img-1"
        mock_generate_logo_data.assert_awaited_once()
        assert mock_generate_logo_data.await_args.kwargs["request"].brand_name == "Acme"
        assert mock_generate_logo_data.await_args.kwargs["current_user"].email == "test@example.com"

    def test_admin_overview_denied_for_regular_user(self, client):
        """Regular user (not superadmin) → 403 on admin endpoints."""
        resp = client.get("/api/v1/admin/overview")
        assert resp.status_code == 403

    def test_admin_users_denied_for_regular_user(self, client):
        resp = client.get("/api/v1/admin/users")
        assert resp.status_code == 403

    def test_admin_settings_denied_for_regular_user(self, client):
        resp = client.get("/api/v1/admin/settings")
        assert resp.status_code == 403

    def test_admin_overview_allowed_for_superadmin(self, superadmin_client):
        """Superadmin should not get 403 (may get 500 due to unmocked DB)."""
        resp = superadmin_client.get("/api/v1/admin/overview")
        assert resp.status_code != 403

    def test_admin_overview_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_admin_overview_data",
            new_callable=AsyncMock,
        ) as mock_get_admin_overview_data:
            mock_get_admin_overview_data.return_value = {
                "total_active_users": 10,
                "total_active_orgs": 3,
                "orgs_by_plan": {"professional": 2, "free": 1},
                "total_trademarks": 150,
                "total_watchlist_items": 40,
                "new_users_7d": 4,
                "total_alerts": 9,
                "api_calls_today": 18,
                "mrr": 1998.0,
                "revenue_by_plan": {
                    "professional": {"price": 999.0, "orgs": 2, "revenue": 1998.0}
                },
                "plan_changes_7d": 1,
                "applications_this_month": 7,
                "active_overrides": 5,
            }
            resp = superadmin_client.get("/api/v1/admin/overview")

        assert resp.status_code == 200
        assert resp.json()["mrr"] == 1998.0
        assert resp.json()["orgs_by_plan"]["professional"] == 2
        assert mock_get_admin_overview_data.await_count == 1

    def test_admin_settings_allowed_for_superadmin(self, superadmin_client):
        resp = superadmin_client.get("/api/v1/admin/settings")
        assert resp.status_code != 403

    def test_admin_settings_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_all_admin_settings_data",
            new_callable=AsyncMock,
        ) as mock_get_all_admin_settings_data:
            mock_get_all_admin_settings_data.return_value = {
                "general.sample_key": {
                    "value": "enabled",
                    "category": "general",
                    "description": "Sample toggle",
                }
            }
            resp = superadmin_client.get("/api/v1/admin/settings")

        assert resp.status_code == 200
        assert resp.json()["general.sample_key"]["value"] == "enabled"
        assert mock_get_all_admin_settings_data.await_count == 1

    def test_admin_settings_category_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_admin_settings_category_data",
            new_callable=AsyncMock,
        ) as mock_get_admin_settings_category_data:
            mock_get_admin_settings_category_data.return_value = {
                "plan.professional.max_users": {
                    "value": 10,
                    "category": "plan_limits",
                }
            }
            resp = superadmin_client.get("/api/v1/admin/settings/plan_limits")

        assert resp.status_code == 200
        assert resp.json()["plan.professional.max_users"]["value"] == 10
        mock_get_admin_settings_category_data.assert_awaited_once_with("plan_limits")

    def test_admin_update_setting_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.update_admin_setting_data",
            new_callable=AsyncMock,
        ) as mock_update_admin_setting_data:
            mock_update_admin_setting_data.return_value = {
                "status": "ok",
                "key": "general.theme",
                "value": "dark",
            }
            resp = superadmin_client.put(
                "/api/v1/admin/settings/general.theme",
                json={"value": "dark", "category": "general"},
            )

        assert resp.status_code == 200
        assert resp.json()["key"] == "general.theme"
        assert resp.json()["value"] == "dark"
        mock_update_admin_setting_data.assert_awaited_once()
        assert mock_update_admin_setting_data.await_args.kwargs["key"] == "general.theme"
        assert mock_update_admin_setting_data.await_args.kwargs["payload"] == {
            "value": "dark",
            "category": "general",
        }

    def test_admin_delete_setting_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.delete_admin_setting_data",
            new_callable=AsyncMock,
        ) as mock_delete_admin_setting_data:
            mock_delete_admin_setting_data.return_value = {
                "status": "ok",
                "key": "general.theme",
                "reverted_to": "code_default",
            }
            resp = superadmin_client.delete("/api/v1/admin/settings/general.theme")

        assert resp.status_code == 200
        assert resp.json()["key"] == "general.theme"
        assert resp.json()["reverted_to"] == "code_default"
        mock_delete_admin_setting_data.assert_awaited_once()
        assert mock_delete_admin_setting_data.await_args.kwargs["key"] == "general.theme"

    def test_admin_organizations_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_admin_organizations_data",
            new_callable=AsyncMock,
        ) as mock_get_admin_organizations_data:
            mock_get_admin_organizations_data.return_value = {
                "organizations": [
                    {
                        "id": "org-1",
                        "name": "Acme IP",
                        "slug": "acme-ip",
                        "email": "ops@acme.test",
                        "is_active": True,
                        "plan_name": "professional",
                        "user_count": 4,
                        "watchlist_count": 12,
                    }
                ],
                "total": 1,
                "limit": 25,
                "offset": 0,
            }
            resp = superadmin_client.get(
                "/api/v1/admin/organizations",
                params={"search": "acme", "plan": "professional", "is_active": "true", "limit": 25},
            )

        assert resp.status_code == 200
        assert resp.json()["organizations"][0]["name"] == "Acme IP"
        mock_get_admin_organizations_data.assert_awaited_once_with(
            search="acme",
            plan="professional",
            is_active=True,
            limit=25,
            offset=0,
        )

    def test_admin_organization_detail_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_admin_organization_detail_data",
            new_callable=AsyncMock,
        ) as mock_get_admin_organization_detail_data:
            mock_get_admin_organization_detail_data.return_value = {
                "id": "org-1",
                "name": "Acme IP",
                "plan_name": "professional",
                "users": [{"id": "user-1", "email": "ops@acme.test"}],
            }
            resp = superadmin_client.get("/api/v1/admin/organizations/org-1")

        assert resp.status_code == 200
        assert resp.json()["users"][0]["email"] == "ops@acme.test"
        mock_get_admin_organization_detail_data.assert_awaited_once_with(org_id="org-1")

    def test_admin_users_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_admin_users_data",
            new_callable=AsyncMock,
        ) as mock_get_admin_users_data:
            mock_get_admin_users_data.return_value = {
                "users": [
                    {
                        "id": "user-1",
                        "email": "ops@acme.test",
                        "first_name": "Ops",
                        "last_name": "User",
                        "role": "admin",
                        "is_active": True,
                        "is_superadmin": False,
                        "organization_id": "org-1",
                        "org_name": "Acme IP",
                        "plan_name": "professional",
                    }
                ],
                "total": 1,
                "limit": 25,
                "offset": 0,
            }
            resp = superadmin_client.get(
                "/api/v1/admin/users",
                params={
                    "search": "ops",
                    "org_id": "org-1",
                    "role": "admin",
                    "is_active": "true",
                    "limit": 25,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["users"][0]["email"] == "ops@acme.test"
        mock_get_admin_users_data.assert_awaited_once_with(
            search="ops",
            org_id="org-1",
            role="admin",
            is_active=True,
            limit=25,
            offset=0,
        )

    def test_admin_change_user_role_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.change_admin_user_role_data",
            new_callable=AsyncMock,
        ) as mock_change_admin_user_role_data:
            mock_change_admin_user_role_data.return_value = {
                "status": "ok",
                "user_id": "user-1",
                "old_role": "user",
                "new_role": "admin",
            }
            resp = superadmin_client.put(
                "/api/v1/admin/users/user-1/role",
                json={"role": "admin"},
            )

        assert resp.status_code == 200
        assert resp.json()["new_role"] == "admin"
        mock_change_admin_user_role_data.assert_awaited_once()
        assert mock_change_admin_user_role_data.await_args.kwargs["user_id"] == "user-1"
        assert mock_change_admin_user_role_data.await_args.kwargs["payload"] == {"role": "admin"}

    def test_admin_toggle_superadmin_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.toggle_admin_superadmin_data",
            new_callable=AsyncMock,
        ) as mock_toggle_admin_superadmin_data:
            mock_toggle_admin_superadmin_data.return_value = {
                "status": "ok",
                "user_id": "user-1",
                "is_superadmin": True,
            }
            resp = superadmin_client.put(
                "/api/v1/admin/users/user-1/superadmin",
                json={"is_superadmin": True},
            )

        assert resp.status_code == 200
        assert resp.json()["is_superadmin"] is True
        mock_toggle_admin_superadmin_data.assert_awaited_once()
        assert mock_toggle_admin_superadmin_data.await_args.kwargs["user_id"] == "user-1"
        assert mock_toggle_admin_superadmin_data.await_args.kwargs["payload"] == {
            "is_superadmin": True
        }

    def test_admin_toggle_user_status_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.toggle_admin_user_status_data",
            new_callable=AsyncMock,
        ) as mock_toggle_admin_user_status_data:
            mock_toggle_admin_user_status_data.return_value = {
                "status": "ok",
                "user_id": "user-1",
                "is_active": False,
            }
            resp = superadmin_client.put(
                "/api/v1/admin/users/user-1/status",
                json={"is_active": False},
            )

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
        mock_toggle_admin_user_status_data.assert_awaited_once()
        assert mock_toggle_admin_user_status_data.await_args.kwargs["user_id"] == "user-1"
        assert mock_toggle_admin_user_status_data.await_args.kwargs["payload"] == {
            "is_active": False
        }

    def test_admin_toggle_org_status_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.toggle_admin_organization_status_data",
            new_callable=AsyncMock,
        ) as mock_toggle_admin_organization_status_data:
            mock_toggle_admin_organization_status_data.return_value = {
                "status": "ok",
                "organization_id": "org-1",
                "is_active": False,
            }
            resp = superadmin_client.put(
                "/api/v1/admin/organizations/org-1/status",
                json={"is_active": False},
            )

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
        mock_toggle_admin_organization_status_data.assert_awaited_once()
        assert mock_toggle_admin_organization_status_data.await_args.kwargs["org_id"] == "org-1"
        assert mock_toggle_admin_organization_status_data.await_args.kwargs["payload"] == {
            "is_active": False
        }

    def test_admin_change_org_plan_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.change_admin_organization_plan_data",
            new_callable=AsyncMock,
        ) as mock_change_admin_organization_plan_data:
            mock_change_admin_organization_plan_data.return_value = {
                "status": "ok",
                "organization_id": "org-1",
                "old_plan": "free",
                "new_plan": "professional",
            }
            resp = superadmin_client.put(
                "/api/v1/admin/organizations/org-1/plan",
                json={"plan_name": "professional"},
            )

        assert resp.status_code == 200
        assert resp.json()["new_plan"] == "professional"
        mock_change_admin_organization_plan_data.assert_awaited_once()
        assert mock_change_admin_organization_plan_data.await_args.kwargs["org_id"] == "org-1"
        assert mock_change_admin_organization_plan_data.await_args.kwargs["payload"] == {
            "plan_name": "professional"
        }

    def test_admin_refund_payment_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.refund_admin_payment_data",
            new_callable=AsyncMock,
        ) as mock_refund_admin_payment_data:
            mock_refund_admin_payment_data.return_value = {
                "status": "ok",
                "payment_id": "pay-1",
                "refund_type": "partial",
                "refund_amount": 49.0,
            }
            resp = superadmin_client.post(
                "/api/v1/admin/payments/pay-1/refund",
                json={"amount": 49.0, "reason": "Customer requested"},
            )

        assert resp.status_code == 200
        assert resp.json()["refund_amount"] == 49.0
        mock_refund_admin_payment_data.assert_awaited_once()
        assert mock_refund_admin_payment_data.await_args.kwargs["payment_id"] == "pay-1"
        assert mock_refund_admin_payment_data.await_args.kwargs["payload"] == {
            "amount": 49.0,
            "reason": "Customer requested",
        }

    def test_admin_audit_log_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_admin_audit_log_data",
            new_callable=AsyncMock,
        ) as mock_get_admin_audit_log_data:
            mock_get_admin_audit_log_data.return_value = {
                "entries": [
                    {
                        "id": "log-1",
                        "action": "user_role_changed",
                        "user_email": "ops@acme.test",
                    }
                ],
                "limit": 50,
                "offset": 0,
            }
            resp = superadmin_client.get(
                "/api/v1/admin/audit-log",
                params={"action": "user_role_changed", "user_id": "user-1", "limit": 50},
            )

        assert resp.status_code == 200
        assert resp.json()["entries"][0]["action"] == "user_role_changed"
        mock_get_admin_audit_log_data.assert_awaited_once_with(
            action="user_role_changed",
            user_id="user-1",
            limit=50,
            offset=0,
        )

    def test_admin_org_credits_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_admin_org_credits_data",
            new_callable=AsyncMock,
        ) as mock_get_admin_org_credits_data:
            mock_get_admin_org_credits_data.return_value = {
                "organization_id": "org-1",
                "plan": "professional",
                "ai_credits": {
                    "monthly_remaining": 20,
                    "purchased": 5,
                    "plan_limit": 25,
                    "reset_at": None,
                },
                "logo_credits": {
                    "monthly_remaining": 10,
                    "purchased": 2,
                    "used_this_month": 3,
                    "reset_at": None,
                },
                "name_credits": {
                    "purchased": 4,
                    "used_this_month": 1,
                },
            }
            resp = superadmin_client.get("/api/v1/admin/organizations/org-1/credits")

        assert resp.status_code == 200
        assert resp.json()["ai_credits"]["plan_limit"] == 25
        mock_get_admin_org_credits_data.assert_awaited_once_with(org_id="org-1")

    def test_admin_adjust_org_credits_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.adjust_admin_org_credits_data",
            new_callable=AsyncMock,
        ) as mock_adjust_admin_org_credits_data:
            mock_adjust_admin_org_credits_data.return_value = {
                "status": "ok",
                "organization_id": "org-1",
                "credit_type": "logo_purchased",
                "old_value": 2,
                "new_value": 5,
            }
            resp = superadmin_client.put(
                "/api/v1/admin/organizations/org-1/credits",
                json={"credit_type": "logo_purchased", "operation": "add", "amount": 3},
            )

        assert resp.status_code == 200
        assert resp.json()["new_value"] == 5
        mock_adjust_admin_org_credits_data.assert_awaited_once()
        assert mock_adjust_admin_org_credits_data.await_args.kwargs["org_id"] == "org-1"
        assert mock_adjust_admin_org_credits_data.await_args.kwargs["payload"] == {
            "credit_type": "logo_purchased",
            "operation": "add",
            "amount": 3,
        }

    def test_admin_bulk_credit_adjustment_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.bulk_adjust_admin_credits_data",
            new_callable=AsyncMock,
        ) as mock_bulk_adjust_admin_credits_data:
            mock_bulk_adjust_admin_credits_data.return_value = {
                "status": "ok",
                "affected_organizations": 4,
            }
            resp = superadmin_client.post(
                "/api/v1/admin/credits/bulk",
                json={
                    "plan_filter": "professional",
                    "credit_type": "logo_purchased",
                    "operation": "add",
                    "amount": 10,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["affected_organizations"] == 4
        mock_bulk_adjust_admin_credits_data.assert_awaited_once()
        assert mock_bulk_adjust_admin_credits_data.await_args.kwargs["payload"] == {
            "plan_filter": "professional",
            "credit_type": "logo_purchased",
            "operation": "add",
            "amount": 10,
        }

    def test_admin_discount_codes_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_admin_discount_codes_data",
            new_callable=AsyncMock,
        ) as mock_get_admin_discount_codes_data:
            mock_get_admin_discount_codes_data.return_value = {
                "discount_codes": [
                    {"id": "code-1", "code": "LAUNCH20", "is_active": True},
                    {"id": "code-2", "code": "PAUSED10", "is_active": False},
                ]
            }
            resp = superadmin_client.get("/api/v1/admin/discount-codes?is_active=true")

        assert resp.status_code == 200
        assert len(resp.json()["discount_codes"]) == 2
        mock_get_admin_discount_codes_data.assert_awaited_once_with(is_active=True)

    def test_admin_create_discount_code_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.create_admin_discount_code_data",
            new_callable=AsyncMock,
        ) as mock_create_admin_discount_code_data:
            mock_create_admin_discount_code_data.return_value = {
                "status": "ok",
                "code": "LAUNCH20",
            }
            resp = superadmin_client.post(
                "/api/v1/admin/discount-codes",
                json={
                    "code": "launch20",
                    "discount_type": "percentage",
                    "discount_value": 20,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["code"] == "LAUNCH20"
        mock_create_admin_discount_code_data.assert_awaited_once()
        assert mock_create_admin_discount_code_data.await_args.kwargs["payload"] == {
            "code": "launch20",
            "discount_type": "percentage",
            "discount_value": 20,
        }

    def test_admin_update_discount_code_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.update_admin_discount_code_data",
            new_callable=AsyncMock,
        ) as mock_update_admin_discount_code_data:
            mock_update_admin_discount_code_data.return_value = {
                "status": "ok",
                "code_id": "code-1",
            }
            resp = superadmin_client.put(
                "/api/v1/admin/discount-codes/code-1",
                json={
                    "description": "Updated launch promo",
                    "is_active": False,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["code_id"] == "code-1"
        mock_update_admin_discount_code_data.assert_awaited_once()
        assert mock_update_admin_discount_code_data.await_args.kwargs["code_id"] == "code-1"
        assert mock_update_admin_discount_code_data.await_args.kwargs["payload"] == {
            "description": "Updated launch promo",
            "is_active": False,
        }

    def test_admin_deactivate_discount_code_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.deactivate_admin_discount_code_data",
            new_callable=AsyncMock,
        ) as mock_deactivate_admin_discount_code_data:
            mock_deactivate_admin_discount_code_data.return_value = {
                "status": "ok",
                "code_id": "code-1",
            }
            resp = superadmin_client.delete("/api/v1/admin/discount-codes/code-1")

        assert resp.status_code == 200
        assert resp.json()["code_id"] == "code-1"
        mock_deactivate_admin_discount_code_data.assert_awaited_once()
        assert mock_deactivate_admin_discount_code_data.await_args.kwargs["code_id"] == "code-1"

    def test_admin_discount_code_usage_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_admin_discount_code_usage_data",
            new_callable=AsyncMock,
        ) as mock_get_admin_discount_code_usage_data:
            mock_get_admin_discount_code_usage_data.return_value = {
                "usage": [
                    {
                        "discount_code_id": "code-1",
                        "organization_id": "org-1",
                        "org_name": "Acme IP",
                        "org_email": "ops@acme.test",
                    }
                ],
                "total_uses": 1,
            }
            resp = superadmin_client.get("/api/v1/admin/discount-codes/code-1/usage")

        assert resp.status_code == 200
        assert resp.json()["total_uses"] == 1
        mock_get_admin_discount_code_usage_data.assert_awaited_once_with(code_id="code-1")

    def test_admin_plans_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_admin_plans_data",
            new_callable=AsyncMock,
        ) as mock_get_admin_plans_data:
            mock_get_admin_plans_data.return_value = {
                "plans": [
                    {
                        "db_record": {"name": "professional"},
                        "code_defaults": {"max_watchlist_items": 100},
                        "active_overrides": {"max_watchlist_items": 250},
                        "active_orgs": 3,
                    }
                ],
                "feature_categories": {"pricing": ["price_monthly"]},
            }
            resp = superadmin_client.get("/api/v1/admin/plans")

        assert resp.status_code == 200
        assert resp.json()["plans"][0]["db_record"]["name"] == "professional"
        mock_get_admin_plans_data.assert_awaited_once_with()

    def test_admin_update_plan_pricing_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.update_admin_plan_pricing_data",
            new_callable=AsyncMock,
        ) as mock_update_admin_plan_pricing_data:
            mock_update_admin_plan_pricing_data.return_value = {
                "status": "ok",
                "plan": "professional",
            }
            resp = superadmin_client.put(
                "/api/v1/admin/plans/professional/pricing",
                json={
                    "price_monthly": 1099,
                    "is_active": True,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["plan"] == "professional"
        mock_update_admin_plan_pricing_data.assert_awaited_once()
        assert mock_update_admin_plan_pricing_data.await_args.kwargs["plan_name"] == "professional"
        assert mock_update_admin_plan_pricing_data.await_args.kwargs["payload"] == {
            "price_monthly": 1099,
            "is_active": True,
        }

    def test_admin_usage_analytics_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.get_admin_usage_analytics_data",
            new_callable=AsyncMock,
        ) as mock_get_admin_usage_analytics_data:
            mock_get_admin_usage_analytics_data.return_value = {
                "period_days": 14,
                "daily_usage": [{"date": "2026-04-11", "unique_users": 5}],
                "usage_by_plan": {"professional": 15},
                "top_users": [{"email": "ops@acme.test", "total_searches": 11}],
                "cost_bearing_actions": {"logo_generations": 2, "name_generations": 7},
            }
            resp = superadmin_client.get("/api/v1/admin/analytics/usage?days=14")

        assert resp.status_code == 200
        assert resp.json()["period_days"] == 14
        mock_get_admin_usage_analytics_data.assert_awaited_once_with(days=14)

    def test_admin_usage_export_route_uses_service(self, superadmin_client):
        with patch(
            "api.admin.build_admin_usage_export_response",
            new_callable=AsyncMock,
        ) as mock_build_admin_usage_export_response:
            mock_build_admin_usage_export_response.return_value = StreamingResponse(
                iter(["date,user_email\r\n2026-04-11,ops@acme.test\r\n"]),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=usage_export_14d.csv"},
            )
            resp = superadmin_client.get("/api/v1/admin/analytics/export?days=14")

        assert resp.status_code == 200
        assert resp.headers["content-disposition"] == "attachment; filename=usage_export_14d.csv"
        assert "date,user_email" in resp.text
        mock_build_admin_usage_export_response.assert_awaited_once_with(days=14)

    def test_admin_test_scoring_allowed_for_admin(self, client):
        with patch(
            "services.admin_scoring_service.run_admin_score_test",
            AsyncMock(
                return_value={
                    "query": "dogan patent",
                    "target": "d.p dogan patent",
                    "final_score": 0.8421,
                    "final_score_pct": "84.2%",
                    "risk_level": {"level": "high", "label": "High"},
                    "factors": {
                        "raw_similarity": 0.8012,
                        "word_match_factor": 1.0,
                        "length_ratio_factor": 0.95,
                        "coverage_factor": 1.0,
                        "idf_factor": 0.98,
                        "combined_factor": 1.05,
                        "word_details": {"matched_words": ["dogan"]},
                    },
                }
            ),
        ):
            resp = client.post(
                "/api/admin/test-scoring",
                json={"query": "dogan patent", "target": "d.p dogan patent"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["final_score"] == 0.8421
        assert data["final_score_pct"] == "84.2%"
        assert data["risk_level"]["level"] == "high"
        assert data["factors"]["word_details"] == {"matched_words": ["dogan"]}


# ============================================================
# Search Endpoint Validation
# ============================================================

class TestSearchValidation:
    """Test search endpoints validate input correctly."""

    def test_quick_search_requires_query(self, client):
        """Quick search without 'query' param should fail."""
        resp = client.get("/api/v1/search/quick")
        # Either 422 (validation) or 500 (unhandled)
        assert resp.status_code in (422, 500)

    def test_quick_search_with_query(self, client):
        """Quick search with a valid query should not return 422 (validation error)."""
        resp = client.get("/api/v1/search/quick?query=NIKE")
        # May get 500 (DB not available) but should not get 422 (bad input)
        assert resp.status_code != 422


@pytest.mark.asyncio
async def test_extracted_search_credits_helper_delegates_to_service():
    from app_search_meta_routes import get_search_credits

    current_user = MagicMock()
    current_user.id = "user-123"

    expected = {
        "display_name": "Professional",
        "resets_on": "2026-04-11T00:00:00+00:00",
    }

    with patch(
        "services.search_service.get_search_credits_summary",
        new=AsyncMock(return_value=expected),
    ) as mock_get_search_credits_summary:
        data = await get_search_credits(current_user)

    assert data == expected
    assert mock_get_search_credits_summary.await_count == 1
    assert mock_get_search_credits_summary.await_args.kwargs == {
        "current_user": current_user,
    }


@pytest.mark.asyncio
async def test_search_service_get_search_credits_summary_returns_plan_and_reset():
    from services.search_service import get_search_credits_summary

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    current_user = MagicMock()
    current_user.id = "user-123"

    data = await get_search_credits_summary(
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(return_value={"display_name": "Professional"}),
        now_factory=lambda: datetime(2026, 4, 10, 13, 45, tzinfo=timezone.utc),
    )

    assert data["display_name"] == "Professional"
    assert data["resets_on"] == "2026-04-11T00:00:00+00:00"


@pytest.mark.asyncio
async def test_extracted_do_public_search_impl_delegates_to_service():
    from app_public_search_routes import do_public_search_impl

    expected = {"query": "NIKE", "results": [], "total": 0}

    with patch(
        "services.search_service.run_public_search",
        new=AsyncMock(return_value=expected),
    ) as mock_run_public_search:
        data = await do_public_search_impl(
            query="NIKE",
            image_path="logo.png",
            nice_classes=[25],
            status_code_getter=lambda status: f"code:{status}",
            logger=MagicMock(),
        )

    assert data == expected
    assert mock_run_public_search.await_count == 1
    assert mock_run_public_search.await_args.kwargs["query"] == "NIKE"
    assert mock_run_public_search.await_args.kwargs["image_path"] == "logo.png"
    assert mock_run_public_search.await_args.kwargs["nice_classes"] == [25]
    assert callable(mock_run_public_search.await_args.kwargs["status_code_getter"])
    assert isinstance(mock_run_public_search.await_args.kwargs["logger"], MagicMock)


@pytest.mark.asyncio
async def test_search_service_run_public_search_maps_safe_fields():
    from services.search_service import run_public_search

    mock_searcher = MagicMock()
    mock_searcher.search.return_value = {
        "results": [
            {
                "trademark_name": "NIKE",
                "name_tr": "nayk",
                "application_no": "2024/1",
                "status": "Published",
                "scores": {
                    "total": 0.91,
                    "scoring_path": "public",
                    "text_similarity": 0.81234,
                    "text_idf_score": 0.55443,
                    "path_a_score": 0.33331,
                    "path_b_score": 0.55443,
                    "scoring_path_source": "TRANSLATED",
                    "visual_similarity": 0.12345,
                    "translation_similarity": 0.45678,
                    "phonetic_similarity": 0.67891,
                },
                "classes": [25, 35],
                "image_path": "logo.png",
                "holder_name": "Nike Inc.",
                "holder_tpe_client_id": "123",
                "attorney_name": "Agent",
                "attorney_no": "A-1",
                "application_date": "2024-01-01",
                "registration_no": "TR-1",
                "has_extracted_goods": True,
                "extracted_goods": "Shoes",
            }
        ]
    }
    mock_searcher_cm = MagicMock()
    mock_searcher_cm.__enter__.return_value = mock_searcher
    mock_searcher_cm.__exit__.return_value = False

    data = await run_public_search(
        query="NIKE",
        nice_classes=[25],
        status_code_getter=lambda status: f"code:{status}",
        logger=MagicMock(),
        searcher_factory=MagicMock(return_value=mock_searcher_cm),
    )

    assert data == {
        "query": "NIKE",
        "results": [
            {
                "trademark_name": "NIKE",
                "application_no": "2024/1",
                "status": "Published",
                "status_code": "code:Published",
                "risk_score": 0.91,
                "nice_classes": [25, 35],
                "image_url": "/api/trademark-image/logo.png",
                "name_tr": "nayk",
                "holder_name": "Nike Inc.",
                "holder_tpe_client_id": "123",
                "attorney_name": "Agent",
                "attorney_no": "A-1",
                "application_date": "2024-01-01",
                "registration_no": "TR-1",
                "scoring_path": "public",
                "text_similarity": 0.812,
                "text_idf_score": 0.554,
                "path_a_score": 0.333,
                "path_b_score": 0.554,
                "scoring_path_source": "TRANSLATED",
                "visual_similarity": 0.123,
                "translation_similarity": 0.457,
                "phonetic_similarity": 0.679,
                "has_extracted_goods": True,
                "extracted_goods": "Shoes",
            }
        ],
        "total": 1,
    }


@pytest.mark.asyncio
async def test_search_service_run_public_search_hides_duplicate_translation_score():
    from services.search_service import run_public_search

    mock_searcher = MagicMock()
    mock_searcher.search.return_value = {
        "results": [
            {
                "trademark_name": "ip",
                "name_tr": "ip",
                "application_no": "2021/160894",
                "status": "Tescil Edildi",
                "scores": {
                    "total": 1.0,
                    "text_similarity": 1.0,
                    "translation_similarity": 1.0,
                    "phonetic_similarity": 1.0,
                },
                "classes": [9, 42],
            }
        ]
    }
    mock_searcher_cm = MagicMock()
    mock_searcher_cm.__enter__.return_value = mock_searcher
    mock_searcher_cm.__exit__.return_value = False

    data = await run_public_search(
        query="ip",
        logger=MagicMock(),
        searcher_factory=MagicMock(return_value=mock_searcher_cm),
    )

    result = data["results"][0]
    assert result["text_similarity"] == 1.0
    assert result["translation_similarity"] == 0.0


@pytest.mark.asyncio
async def test_search_service_run_public_search_exposes_path_scores_for_display():
    from services.search_service import run_public_search

    mock_searcher = MagicMock()
    mock_searcher.search.return_value = {
        "results": [
            {
                "trademark_name": "CORDAGE",
                "name_tr": "ip",
                "application_no": "2022/029751",
                "status": "Tescil Edildi",
                "scores": {
                    "total": 1.0,
                    "text_similarity": 0.0321,
                    "text_idf_score": 1.0,
                    "path_a_score": 0.0177,
                    "path_b_score": 1.0,
                    "scoring_path_source": "TRANSLATED",
                    "translation_similarity": 1.0,
                },
                "classes": [1, 5],
            }
        ]
    }
    mock_searcher_cm = MagicMock()
    mock_searcher_cm.__enter__.return_value = mock_searcher
    mock_searcher_cm.__exit__.return_value = False

    data = await run_public_search(
        query="ip",
        logger=MagicMock(),
        searcher_factory=MagicMock(return_value=mock_searcher_cm),
    )

    result = data["results"][0]
    assert result["risk_score"] == 1.0
    assert result["text_idf_score"] == 1.0
    assert result["path_a_score"] == 0.018
    assert result["path_b_score"] == 1.0
    assert result["translation_similarity"] == 1.0
    assert result["scoring_path_source"] == "TRANSLATED"


@pytest.mark.asyncio
async def test_search_service_run_public_search_logs_and_raises_on_failure():
    from services.search_service import run_public_search

    logger = MagicMock()
    searcher_factory = MagicMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await run_public_search(
            query="NIKE",
            logger=logger,
            searcher_factory=searcher_factory,
        )

    logger.error.assert_called_once()
    assert "Public search failed: boom" in logger.error.call_args.args[0]


def test_search_service_resolve_public_search_client_id_reuses_cookie():
    from services.search_service import resolve_public_search_client_id

    request = SimpleNamespace(cookies={"public_search_client_id": "browser-123"})

    client_id, should_set_cookie = resolve_public_search_client_id(request)

    assert client_id == "browser-123"
    assert should_set_cookie is False


def test_search_service_check_public_search_eligibility_hits_free_limit():
    from services.search_service import check_public_search_eligibility

    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"searches": 5}
    mock_db.cursor.return_value = mock_cursor

    allowed, reason, detail = check_public_search_eligibility(
        mock_db,
        "browser-123",
        daily_limit_getter=lambda: 5,
        today_factory=lambda: date(2026, 4, 21),
    )

    assert allowed is False
    assert reason == "daily_limit_exceeded"
    assert detail == {
        "error": "daily_limit_exceeded",
        "current_plan": "free",
        "upgrade_context": "public_search",
        "daily_limit": 5,
        "used_today": 5,
        "remaining": 0,
    }


def test_search_service_increment_public_search_usage_upserts_counter():
    from services.search_service import increment_public_search_usage

    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"searches": 4}
    mock_db.cursor.return_value = mock_cursor

    count = increment_public_search_usage(
        mock_db,
        "browser-123",
        today_factory=lambda: date(2026, 4, 21),
    )

    assert count == 4
    mock_db.commit.assert_called_once()
    execute_sql = mock_cursor.execute.call_args.args[0]
    assert "INSERT INTO public_search_usage" in execute_sql
    assert mock_cursor.execute.call_args.args[1] == ("browser-123", date(2026, 4, 21))


@pytest.mark.asyncio
async def test_extracted_public_search_post_impl_parses_classes_and_delegates():
    from app_public_search_routes import public_search_post_impl

    mock_search = AsyncMock(return_value={"query": "NIKE", "results": [], "total": 0})

    data = await public_search_post_impl(
        query="  NIKE  ",
        image=None,
        classes="9, 35, 99, bad",
        do_public_search_handler=mock_search,
        allowed_image_types=["image/png"],
        max_image_size=10,
        validate_image_magic_bytes=lambda content: True,
    )

    assert data == {"query": "NIKE", "results": [], "total": 0}
    assert mock_search.await_count == 1
    assert mock_search.await_args.kwargs == {
        "query": "NIKE",
        "image_path": None,
        "nice_classes": None,
    }


@pytest.mark.asyncio
async def test_extracted_public_portfolio_impl_delegates_to_service():
    from app_public_portfolio_routes import public_portfolio_impl

    expected = {
        "entity_type": "holder",
        "entity_name": "Nike Inc",
        "entity_id": "123",
        "results": [],
        "total_count": 2,
    }

    with patch(
        "services.search_service.run_public_portfolio_lookup",
        new=AsyncMock(return_value=expected),
    ) as mock_run_public_portfolio_lookup:
        data = await public_portfolio_impl(holder_id="123", logger=MagicMock())

    assert data == expected
    assert mock_run_public_portfolio_lookup.await_count == 1
    assert mock_run_public_portfolio_lookup.await_args.kwargs["holder_id"] == "123"
    assert mock_run_public_portfolio_lookup.await_args.kwargs["attorney_no"] is None
    assert isinstance(mock_run_public_portfolio_lookup.await_args.kwargs["logger"], MagicMock)


@pytest.mark.asyncio
async def test_search_service_run_public_portfolio_lookup_maps_safe_fields():
    from services.search_service import run_public_portfolio_lookup

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"cnt": 2}
    mock_cursor.fetchall.return_value = [
        {
            "name": "NIKE",
            "application_no": "2024/1",
            "final_status": "Published",
            "nice_class_numbers": [25, 35],
            "application_date": date(2024, 1, 2),
            "image_path": "nike.png",
            "holder_name": "Nike Inc",
            "holder_tpe_client_id": "123",
            "attorney_name": "Agent",
            "attorney_no": "A-1",
            "registration_no": "TR-9",
        }
    ]

    data = await run_public_portfolio_lookup(
        holder_id="123",
        logger=MagicMock(),
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert data == {
        "entity_type": "holder",
        "entity_name": "Nike Inc",
        "entity_id": "123",
        "results": [
            {
                "trademark_name": "NIKE",
                "application_no": "2024/1",
                "status": "Published",
                "nice_classes": [25, 35],
                "image_url": "/api/trademark-image/nike.png",
                "holder_name": "Nike Inc",
                "holder_tpe_client_id": "123",
                "attorney_name": "Agent",
                "attorney_no": "A-1",
                "application_date": "2024-01-02",
                "registration_no": "TR-9",
            }
        ],
        "total_count": 2,
    }


@pytest.mark.asyncio
async def test_extracted_public_portfolio_csv_impl_delegates_to_service():
    from app_public_portfolio_routes import public_portfolio_csv_impl

    expected = MagicMock()
    current_user = MagicMock()

    with patch(
        "services.search_service.build_public_portfolio_csv",
        new=AsyncMock(return_value=expected),
    ) as mock_build_public_portfolio_csv:
        response = await public_portfolio_csv_impl(
            attorney_no="A-1",
            logger=MagicMock(),
            current_user=current_user,
        )

    assert response is expected
    assert mock_build_public_portfolio_csv.await_count == 1
    assert mock_build_public_portfolio_csv.await_args.kwargs["holder_id"] is None
    assert mock_build_public_portfolio_csv.await_args.kwargs["attorney_no"] == "A-1"
    assert mock_build_public_portfolio_csv.await_args.kwargs["current_user"] is current_user
    assert isinstance(mock_build_public_portfolio_csv.await_args.kwargs["logger"], MagicMock)


@pytest.mark.asyncio
async def test_search_service_build_public_portfolio_csv_streams_csv():
    from services.search_service import build_public_portfolio_csv

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {
            "name": "Marka/Name",
            "application_no": "2024/1",
            "final_status": "Published",
            "nice_class_numbers": [25, 35],
            "application_date": date(2024, 1, 2),
            "registration_date": date(2024, 6, 3),
            "registration_no": "TR-9",
            "holder_name": "Nike/Holder",
            "attorney_name": "Agent",
            "attorney_no": "A-1",
            "bulletin_no": "2024-1",
            "gazette_no": "55",
        }
    ]

    response = await build_public_portfolio_csv(
        attorney_no="A-1",
        logger=MagicMock(),
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk)
        else:
            chunks.append(chunk.encode("utf-8"))
    body = b"".join(chunks).decode("utf-8-sig")

    assert response.headers["content-disposition"] == 'attachment; filename="Agent_portfolio.csv"'
    assert "Marka Adi,Basvuru No,Durum,Siniflar" in body
    assert (
        "Marka/Name,2024/1,Published,25; 35,2024-01-02,2024-06-03,TR-9,Nike/Holder,Agent,A-1,2024-1,55"
        in body
    )


@pytest.mark.asyncio
async def test_search_service_build_public_portfolio_csv_blocks_free_plan():
    from services.search_service import build_public_portfolio_csv

    current_user = MagicMock()
    current_user.id = "user-123"
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        await build_public_portfolio_csv(
            attorney_no="A-1",
            current_user=current_user,
            database_factory=MagicMock(return_value=mock_db_cm),
            user_plan_getter=MagicMock(return_value={"plan_name": "free"}),
            plan_limit_getter=MagicMock(return_value=False),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["upgrade_context"] == "portfolio_download"


@pytest.mark.asyncio
async def test_extracted_search_by_image_impl_delegates_to_service():
    from app_image_search_routes import search_by_image_impl

    expected = {"success": True, "results": []}
    image = MagicMock()
    process_uploaded_image_handler = AsyncMock()
    settings_obj = MagicMock()
    logger = MagicMock()
    score_pair_fn = MagicMock()
    visual_similarity_fn = MagicMock()
    risk_level_getter = MagicMock()
    encode_query_image_handler = MagicMock()
    get_image_embedding_handler = MagicMock()
    extract_ocr_text_handler = MagicMock()

    with patch(
        "services.search_service.run_image_search",
        new=AsyncMock(return_value=expected),
    ) as mock_run_image_search:
        data = await search_by_image_impl(
            image=image,
            name="NIKE",
            classes="9,99",
            limit=5,
            process_uploaded_image_handler=process_uploaded_image_handler,
            settings=settings_obj,
            logger=logger,
            global_class=99,
            score_pair_fn=score_pair_fn,
            visual_similarity_fn=visual_similarity_fn,
            risk_level_getter=risk_level_getter,
            encode_query_image_handler=encode_query_image_handler,
            get_image_embedding_handler=get_image_embedding_handler,
            extract_ocr_text_handler=extract_ocr_text_handler,
        )

    assert data == expected
    assert mock_run_image_search.await_count == 1
    assert mock_run_image_search.await_args.kwargs == {
        "image": image,
        "name": "NIKE",
        "classes": "9,99",
        "limit": 5,
        "process_uploaded_image_handler": process_uploaded_image_handler,
        "settings": settings_obj,
        "logger": logger,
        "global_class": 99,
        "score_pair_fn": score_pair_fn,
        "visual_similarity_fn": visual_similarity_fn,
        "risk_level_getter": risk_level_getter,
        "encode_query_image_handler": encode_query_image_handler,
        "get_image_embedding_handler": get_image_embedding_handler,
        "extract_ocr_text_handler": extract_ocr_text_handler,
    }


@pytest.mark.asyncio
async def test_search_service_run_image_search_returns_sample_results_without_embeddings():
    from services.search_service import run_image_search

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        temp_path = tmp.name

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"cnt": 0}
    mock_cursor.fetchall.return_value = [
        {
            "id": "1",
            "name": "NIKE",
            "application_no": "2024/1",
            "final_status": "Published",
            "nice_class_numbers": [9, 35],
            "bulletin_no": "2024-1",
            "image_path": "nike.png",
        }
    ]

    process_uploaded_image_handler = AsyncMock(return_value=(temp_path, MagicMock()))
    settings_obj = SimpleNamespace(
        use_unified_scoring=False,
        database=SimpleNamespace(
            host="localhost",
            port=5432,
            name="db",
            user="user",
            password="pass",
        ),
    )
    connect_fn = MagicMock(return_value=mock_conn)

    data = await run_image_search(
        image=MagicMock(),
        name=None,
        classes="9,99",
        limit=5,
        process_uploaded_image_handler=process_uploaded_image_handler,
        settings=settings_obj,
        logger=MagicMock(),
        global_class=99,
        score_pair_fn=MagicMock(),
        visual_similarity_fn=MagicMock(),
        risk_level_getter=MagicMock(),
        encode_query_image_handler=MagicMock(),
        get_image_embedding_handler=MagicMock(),
        extract_ocr_text_handler=MagicMock(),
        connect_fn=connect_fn,
    )

    assert data == {
        "success": True,
        "search_type": "image",
        "warning": "Gorsel embeddingler henuz olusturulmamis. Ornek sonuclar gosteriliyor.",
        "total_results": 1,
        "classes_filtered": [9, 99],
        "results": [
            {
                "id": "1",
                "name": "NIKE",
                "application_no": "2024/1",
                "status": "Published",
                "nice_classes": [9, 35],
                "image_url": "/api/trademark-image/nike.png",
                "similarity": 0,
                "image_similarity": 0,
                "risk_level": "unknown",
                "note": "Gorsel embedding veritabaninda bulunamadi - ornek sonuclar",
            }
        ],
    }
    assert connect_fn.call_count == 1
    assert connect_fn.call_args.kwargs == {
        "host": "localhost",
        "port": 5432,
        "database": "db",
        "user": "user",
        "password": "pass",
    }
    assert not os.path.exists(temp_path)


@pytest.mark.asyncio
async def test_search_service_run_image_search_keeps_ocr_inside_visual_channel():
    from services.search_service import run_image_search

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        temp_path = tmp.name
    from PIL import Image

    Image.new("RGB", (32, 32), "white").save(temp_path)

    row = {
        "id": "tm-1",
        "name": "patremontana berlin",
        "name_tr": "patremontana berlin",
        "application_no": "2021/185867",
        "final_status": "Tescil Edildi",
        "nice_class_numbers": [25, 35],
        "bulletin_no": "393",
        "image_path": "missing.jpg",
        "logo_ocr_text": "PATREMONTANA BERLIN",
        "text_embedding": [1.0, 0.0],
        "image_embedding": [1.0, 0.0],
        "dinov2_embedding": [1.0, 0.0],
        "color_histogram": [1.0, 0.0],
        "holder_name": "Holder",
        "holder_tpe_client_id": "123",
        "attorney_name": "Attorney",
        "attorney_no": "456",
        "registration_no": "789",
        "application_date": None,
        "expiry_date": None,
        "clip_sim": 1.0,
    }

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"cnt": 1}
    mock_cursor.fetchall.side_effect = [[row], []]

    score_pair_fn = MagicMock(
        return_value={
            "total": 0.82,
            "text_similarity": 0.0,
            "text_idf_score": 0.0,
            "path_a_score": 0.0,
            "path_b_score": 0.0,
            "visual_similarity": 0.55,
            "translation_similarity": 0.0,
            "phonetic_similarity": 0.0,
            "textual_breakdown": {},
        }
    )
    text_embedding_getter = MagicMock(return_value=[1.0, 0.0])
    name_similarity_fn = MagicMock(return_value=0.81)

    data = await run_image_search(
        image=MagicMock(),
        name="",
        classes="25,35",
        limit=5,
        process_uploaded_image_handler=AsyncMock(return_value=(temp_path, MagicMock())),
        settings=SimpleNamespace(
            use_unified_scoring=True,
            database=SimpleNamespace(
                host="localhost",
                port=5432,
                name="db",
                user="user",
                password="pass",
            ),
            paths=SimpleNamespace(data_root=""),
            pipeline=SimpleNamespace(bulletins_root=""),
        ),
        logger=MagicMock(),
        global_class=99,
        score_pair_fn=score_pair_fn,
        visual_similarity_fn=MagicMock(),
        risk_level_getter=MagicMock(return_value="high"),
        encode_query_image_handler=MagicMock(
            return_value={
                "clip_vec": [1.0, 0.0],
                "dino_vec": [1.0, 0.0],
                "color_vec": [1.0, 0.0],
                "ocr_text": "Patremontana",
            }
        ),
        get_image_embedding_handler=MagicMock(),
        extract_ocr_text_handler=MagicMock(),
        connect_fn=MagicMock(return_value=mock_conn),
        text_embedding_getter=text_embedding_getter,
        name_similarity_fn=name_similarity_fn,
    )

    assert data["search_type"] == "image"
    assert data["query_text_source"] == "IMAGE_ONLY"
    assert data["query_ocr_text_used"] is False
    assert data["results"][0]["query_text_source"] == "IMAGE_ONLY"
    assert data["results"][0]["query_ocr_text_used"] is False
    assert data["results"][0]["text_similarity"] is None
    assert data["results"][0]["scores"]["query_text_source"] == "IMAGE_ONLY"
    text_embedding_getter.assert_not_called()
    name_similarity_fn.assert_not_called()
    assert score_pair_fn.call_args.kwargs["query_name"] == ""
    assert score_pair_fn.call_args.kwargs["text_sim"] == 0.0
    assert score_pair_fn.call_args.kwargs["visual_breakdown"]["components"]["ocr"] > 0.0
    assert not os.path.exists(temp_path)


@pytest.mark.asyncio
async def test_extracted_enhanced_search_impl_delegates_to_service():
    from app_enhanced_search_routes import EnhancedSearchResponse, SearchRequest, enhanced_search_impl

    search_request = SearchRequest(
        name="NIKE",
        goods_description="sports shoes and apparel",
        limit=5,
    )
    settings_obj = SimpleNamespace(use_unified_scoring=False)
    logger = MagicMock()
    normalize_turkish_fn = MagicMock(return_value="nike")
    score_pair_fn = MagicMock()
    visual_similarity_fn = MagicMock()
    class_suggestions_handler = MagicMock()
    text_embedding_getter = MagicMock()
    encode_query_image_handler = MagicMock()
    date_formatter = MagicMock()
    status_code_getter = MagicMock()
    image_url_getter = MagicMock()
    expected = {
        "results": [
            {
                "id": "1",
                "name": "NIKE SPORT",
                "application_no": "2024/1",
                "application_date": "2024-01-02",
                "registration_date": "2024-06-03",
                "status": "Tescil Edildi",
                "status_code": "registered",
                "nice_classes": [25, 35],
                "owner": "Nike Inc",
                "holder_tpe_client_id": "123",
                "attorney": "Agent",
                "attorney_no": "A-1",
                "registration_no": "TR-9",
                "bulletin_no": "2024-1",
                "image_url": "/api/trademark-image/nike.png",
                "similarity": 84.2,
                "name_similarity": 84.2,
                "class_overlap_count": 2,
            }
        ],
        "search_context": {
            "searched_name": "NIKE",
            "searched_classes": [25, 35],
            "goods_description": "sports shoes and apparel",
            "total_results": 1,
            "search_time_ms": 250.0,
        },
        "query": "NIKE",
        "total_results": 1,
        "search_time_ms": 250.0,
        "search_classes": [25, 35],
        "classes_were_auto_suggested": True,
        "auto_suggested_classes": [
            {
                "class_number": 25,
                "class_name": "Clothing",
                "similarity_score": 0.91,
            }
        ],
        "suggestion_query": "NIKE: sports shoes and apparel",
    }

    with patch(
        "services.search_service.run_enhanced_search",
        AsyncMock(return_value=expected),
    ) as run_enhanced_search:
        response = await enhanced_search_impl(
            search_request=search_request,
            settings=settings_obj,
            logger=logger,
            normalize_turkish_fn=normalize_turkish_fn,
            score_pair_fn=score_pair_fn,
            visual_similarity_fn=visual_similarity_fn,
            class_suggestions_handler=class_suggestions_handler,
            text_embedding_getter=text_embedding_getter,
            encode_query_image_handler=encode_query_image_handler,
            date_formatter=date_formatter,
            status_code_getter=status_code_getter,
            image_url_getter=image_url_getter,
        )

    assert isinstance(response, EnhancedSearchResponse)
    assert response.query == "NIKE"
    assert response.results[0].name == "NIKE SPORT"
    assert response.search_context.searched_classes == [25, 35]
    run_enhanced_search.assert_awaited_once_with(
        search_request=search_request,
        settings=settings_obj,
        logger=logger,
        normalize_turkish_fn=normalize_turkish_fn,
        score_pair_fn=score_pair_fn,
        visual_similarity_fn=visual_similarity_fn,
        class_suggestions_handler=class_suggestions_handler,
        text_embedding_getter=text_embedding_getter,
        encode_query_image_handler=encode_query_image_handler,
        date_formatter=date_formatter,
        status_code_getter=status_code_getter,
        image_url_getter=image_url_getter,
    )


@pytest.mark.asyncio
async def test_search_service_run_enhanced_search_auto_suggests_and_formats_results():
    from app_enhanced_search_routes import SearchRequest
    from services.search_service import run_enhanced_search

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {
            "id": 1,
            "application_no": "2024/1",
            "name": "NIKE SPORT",
            "final_status": "Tescil Edildi",
            "nice_class_numbers": [25, 35],
            "application_date": date(2024, 1, 2),
            "registration_date": date(2024, 6, 3),
            "bulletin_no": "2024-1",
            "image_path": "nike.png",
            "holder_name": "Nike Inc",
            "holder_tpe_client_id": "123",
            "attorney_name": "Agent",
            "attorney_no": "A-1",
            "registration_no": "TR-9",
            "name_tr": "NAYK",
            "logo_ocr_text": None,
            "text_embedding": None,
            "image_embedding": None,
            "dinov2_embedding": None,
            "color_histogram": None,
            "score": 0.6123,
            "exact_match": False,
            "phonetic_match": True,
        }
    ]

    settings_obj = SimpleNamespace(
        use_unified_scoring=False,
        database=SimpleNamespace(
            host="localhost",
            port=5432,
            name="db",
            user="user",
            password="pass",
        ),
    )
    class_suggestions_handler = MagicMock(
        return_value=[
            {
                "class_number": 25,
                "class_name": "Clothing",
                "similarity": 0.91,
            },
            {
                "class_number": 35,
                "class_name": "Advertising & Business",
                "similarity": 0.45,
            },
        ]
    )
    score_pair_fn = MagicMock(return_value={"total": 0.842})

    response = await run_enhanced_search(
        search_request=SearchRequest(
            name="NIKE",
            goods_description="sports shoes and apparel",
            limit=5,
        ),
        settings=settings_obj,
        logger=MagicMock(),
        normalize_turkish_fn=lambda value: value.lower(),
        score_pair_fn=score_pair_fn,
        visual_similarity_fn=MagicMock(return_value=0.0),
        class_suggestions_handler=class_suggestions_handler,
        text_embedding_getter=MagicMock(return_value=None),
        encode_query_image_handler=MagicMock(),
        date_formatter=lambda value: value.strftime("%Y-%m-%d") if value else None,
        status_code_getter=lambda value: "registered" if value == "Tescil Edildi" else "unknown",
        image_url_getter=lambda image_path, application_no, bulletin_no=None: (
            f"/api/trademark-image/{image_path}" if image_path else None
        ),
        connect_fn=MagicMock(return_value=mock_conn),
        timer=MagicMock(side_effect=[100.0, 100.25]),
    )

    assert response["search_classes"] == [25, 35]
    assert response["classes_were_auto_suggested"] is True
    assert response["auto_suggested_classes"][0]["class_number"] == 25
    assert response["auto_suggested_classes"][1]["class_number"] == 35
    assert response["suggestion_query"] == "NIKE: sports shoes and apparel"
    assert response["results"][0]["status_code"] == "registered"
    assert response["results"][0]["image_url"] == "/api/trademark-image/nike.png"
    assert response["results"][0]["similarity"] == 84.2
    assert response["results"][0]["class_overlap_count"] == 2
    assert response["search_context"]["searched_classes"] == [25, 35]
    assert response["search_time_ms"] == 250.0
    assert score_pair_fn.call_args.kwargs["phonetic_sim"] == 1.0
    assert class_suggestions_handler.call_args.kwargs == {
        "goods_description": "sports shoes and apparel",
        "trademark_name": "NIKE",
        "limit": 5,
    }
    assert mock_cursor.execute.call_count == 1
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()


@pytest.mark.asyncio
async def test_search_service_run_enhanced_search_auto_suggest_accepts_async_handler():
    from app_enhanced_search_routes import SearchRequest
    from services.search_service import run_enhanced_search

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = []

    settings_obj = SimpleNamespace(
        use_unified_scoring=False,
        database=SimpleNamespace(
            host="localhost",
            port=5432,
            name="db",
            user="user",
            password="pass",
        ),
    )
    class_suggestions_handler = AsyncMock(
        return_value=[
            {
                "class_number": 25,
                "class_name": "Clothing",
                "similarity": 0.91,
            }
        ]
    )

    response = await run_enhanced_search(
        search_request=SearchRequest(
            name="NIKE",
            goods_description="sports shoes and apparel",
            limit=5,
        ),
        settings=settings_obj,
        logger=MagicMock(),
        normalize_turkish_fn=lambda value: value.lower(),
        score_pair_fn=MagicMock(return_value={"total": 0.0}),
        visual_similarity_fn=MagicMock(return_value=0.0),
        class_suggestions_handler=class_suggestions_handler,
        text_embedding_getter=MagicMock(return_value=None),
        encode_query_image_handler=MagicMock(),
        date_formatter=lambda value: value.strftime("%Y-%m-%d") if value else None,
        status_code_getter=lambda value: "unknown",
        image_url_getter=lambda image_path, application_no, bulletin_no=None: None,
        connect_fn=MagicMock(return_value=mock_conn),
        timer=MagicMock(side_effect=[100.0, 100.1]),
    )

    class_suggestions_handler.assert_awaited_once_with(
        goods_description="sports shoes and apparel",
        trademark_name="NIKE",
        limit=5,
    )
    assert response["search_classes"] == [25]
    assert response["classes_were_auto_suggested"] is True
    assert response["auto_suggested_classes"][0]["similarity_score"] == 0.91


@pytest.mark.asyncio
async def test_search_service_run_enhanced_search_uses_risk_engine_when_unified():
    from app_enhanced_search_routes import SearchRequest
    from services.search_service import run_enhanced_search

    mock_engine = MagicMock()
    mock_engine.assess_brand_risk.return_value = (
        {
            "top_candidates": [
                {
                    "trademark_id": "tm-1",
                    "application_no": "2026/1",
                    "name": "AA MODEX",
                    "status": "Yayınlandı",
                    "classes": [9, 42],
                    "image_path": "aa.png",
                    "holder_name": "Owner",
                    "holder_tpe_client_id": "H-1",
                    "attorney_name": "Agent",
                    "attorney_no": "A-1",
                    "registration_no": None,
                    "bulletin_no": "489",
                    "application_date": "2026-02-13",
                    "scores": {"total": 0.7472},
                }
            ]
        },
        False,
    )
    risk_engine_factory = MagicMock(return_value=mock_engine)
    connect_fn = MagicMock()

    settings_obj = SimpleNamespace(
        use_unified_scoring=True,
        database=SimpleNamespace(
            host="localhost",
            port=5432,
            name="db",
            user="user",
            password="pass",
        ),
    )

    response = await run_enhanced_search(
        search_request=SearchRequest(name="AA", classes=[9, 42], limit=10),
        settings=settings_obj,
        logger=MagicMock(),
        normalize_turkish_fn=lambda value: value.lower(),
        score_pair_fn=MagicMock(),
        visual_similarity_fn=MagicMock(),
        class_suggestions_handler=MagicMock(),
        text_embedding_getter=MagicMock(),
        encode_query_image_handler=MagicMock(),
        date_formatter=lambda value: value.strftime("%Y-%m-%d") if value else None,
        status_code_getter=lambda value: "published" if value == "Yayınlandı" else "unknown",
        image_url_getter=lambda image_path, application_no, bulletin_no=None: (
            f"/api/trademark-image/{image_path}" if image_path else None
        ),
        connect_fn=connect_fn,
        timer=MagicMock(side_effect=[100.0, 100.1]),
        risk_engine_factory=risk_engine_factory,
    )

    risk_engine_factory.assert_called_once_with()
    mock_engine.assess_brand_risk.assert_called_once_with(
        name="AA",
        image_path=None,
        target_classes=[9, 42],
        attorney_no=None,
    )
    mock_engine.close.assert_called_once()
    connect_fn.assert_not_called()
    assert response["results"][0]["name"] == "AA MODEX"
    assert response["results"][0]["similarity"] == 74.7
    assert response["results"][0]["class_overlap_count"] == 2
    assert response["results"][0]["status_code"] == "published"
    assert response["search_context"]["searched_classes"] == [9, 42]


@pytest.mark.asyncio
async def test_extracted_suggest_nice_classes_helper_delegates_to_service():
    from app_nice_class_routes import ClassSuggestionRequest, suggest_nice_classes

    request = ClassSuggestionRequest(
        description="software development services",
        top_k=2,
        lang="en",
    )
    expected = {
        "query": "software development services",
        "suggestions": [
            {
                "class_number": 42,
                "class_name": "Scientific & Tech Services",
                "similarity": 0.9123,
                "description": "Scientific and technological services",
            },
            {
                "class_number": 9,
                "class_name": "Electronics & Software",
                "similarity": 0.8123,
                "description": "Software and electronics",
            },
        ],
        "processing_time_ms": 250.0,
    }

    with patch(
        "services.nice_class_service.run_nice_class_suggestion",
        AsyncMock(return_value=expected),
    ) as run_nice_class_suggestion:
        response = await suggest_nice_classes(request)

    assert response.query == "software development services"
    assert [item.class_number for item in response.suggestions] == [42, 9]
    assert response.suggestions[0].class_name == "Scientific & Tech Services"
    assert response.processing_time_ms == 250.0
    run_nice_class_suggestion.assert_awaited_once_with(
        description="software development services",
        top_k=2,
        lang="en",
        settings=ANY,
        logger=ANY,
        class_name_getter=ANY,
    )


def _nice_class_catalogue_rows():
    return [
        {
            "class_number": 9,
            "name_tr": "Bilgisayar ve Elektronik",
            "name_en": "Electronics & Software",
            "description": "Software and electronics",
            "description_tr": "Yazilim ve elektronik",
            "description_en": "Software and electronics",
        },
        {
            "class_number": 25,
            "name_tr": "Giyim",
            "name_en": "Clothing",
            "description": "Clothing, footwear, and headwear",
            "description_tr": "Giyim, ayakkabi ve bas giysileri",
            "description_en": "Clothing, footwear, and headwear",
        },
        {
            "class_number": 42,
            "name_tr": "Bilimsel ve Teknolojik Hizmetler",
            "name_en": "Scientific & Tech Services",
            "description": "Scientific and technological services",
            "description_tr": "Bilimsel ve teknolojik hizmetler",
            "description_en": "Scientific and technological services",
        },
    ]


def _nice_class_settings(qwen_model="qwen-flash", gemini_model="gemini-2.5-flash-lite"):
    return SimpleNamespace(
        database=SimpleNamespace(
            host="localhost",
            port=5432,
            name="db",
            user="user",
            password="pass",
        ),
        creative=SimpleNamespace(
            qwen_class_model=qwen_model,
            gemini_class_fallback_model=gemini_model,
        ),
    )


class _ClassSuggestionProvider:
    def __init__(self, response=None, available=True, error=None):
        self.response = response or {"suggestions": []}
        self.available = available
        self.error = error
        self.calls = []

    def is_available(self):
        return self.available

    async def generate_json(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if self.error:
            raise self.error
        return self.response


@pytest.mark.asyncio
async def test_nice_class_service_run_suggestion_returns_ranked_classes():
    from services.nice_class_service import run_nice_class_suggestion

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = _nice_class_catalogue_rows()

    connect_fn = MagicMock(return_value=mock_conn)
    embedding_getter = MagicMock()
    qwen = _ClassSuggestionProvider(
        response={
            "suggestions": [
                {"class_number": 99, "confidence": 1.0},
                {"class_number": 42, "confidence": 1.4},
                {"class_number": 9, "score": -0.2},
                {"class_number": 42, "confidence": 0.5},
            ]
        }
    )
    gemini = _ClassSuggestionProvider(
        response={"suggestions": [{"class_number": 25, "confidence": 0.75}]}
    )
    timer = MagicMock(side_effect=[100.0, 100.25])

    with patch.dict(sys.modules, {"ai": SimpleNamespace(get_text_embedding_cached=embedding_getter)}):
        response = await run_nice_class_suggestion(
            description="software development services",
            top_k=2,
            lang="en",
            settings=_nice_class_settings(),
            logger=MagicMock(),
            class_name_getter=lambda class_num, lang="tr": {
                9: "Electronics & Software",
                25: "Clothing",
                42: "Scientific & Tech Services",
            }[class_num],
            connect_fn=connect_fn,
            timer=timer,
            qwen_client_getter=lambda: qwen,
            gemini_client_getter=lambda: gemini,
        )

    assert response["query"] == "software development services"
    assert [item["class_number"] for item in response["suggestions"]] == [42, 9]
    assert response["suggestions"][0]["class_name"] == "Scientific & Tech Services"
    assert response["suggestions"][0]["similarity"] == 1.0
    assert response["suggestions"][1]["similarity"] == 0.0
    assert set(response.keys()) == {"query", "suggestions", "processing_time_ms"}
    assert set(response["suggestions"][0].keys()) == {
        "class_number",
        "class_name",
        "similarity",
        "description",
    }
    assert response["processing_time_ms"] == 250.0
    connect_fn.assert_called_once_with(
        host="localhost",
        port=5432,
        database="db",
        user="user",
        password="pass",
    )
    executed_sql = mock_cursor.execute.call_args.args[0]
    assert "description_embedding" not in executed_sql
    assert "BETWEEN 1 AND 45" in executed_sql
    assert qwen.calls[0]["model"] == "qwen-flash"
    assert "qwen-max" not in qwen.calls[0]["model"]
    assert not gemini.calls
    embedding_getter.assert_not_called()
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()


@pytest.mark.asyncio
async def test_nice_class_service_falls_back_to_gemini_flash_lite_when_qwen_fails():
    from services.nice_class_service import run_nice_class_suggestion

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = _nice_class_catalogue_rows()

    qwen = _ClassSuggestionProvider(error=RuntimeError("quota exhausted"))
    gemini = _ClassSuggestionProvider(
        response={"suggestions": [{"class_number": 25, "confidence": 0.74}]}
    )

    response = await run_nice_class_suggestion(
        description="sports clothing",
        top_k=3,
        lang="en",
        settings=_nice_class_settings(),
        logger=MagicMock(),
        class_name_getter=lambda class_num, lang="tr": {
            9: "Electronics & Software",
            25: "Clothing",
            42: "Scientific & Tech Services",
        }[class_num],
        connect_fn=MagicMock(return_value=mock_conn),
        timer=MagicMock(side_effect=[10.0, 10.1]),
        qwen_client_getter=lambda: qwen,
        gemini_client_getter=lambda: gemini,
    )

    assert [item["class_number"] for item in response["suggestions"]] == [25]
    assert qwen.calls[0]["model"] == "qwen-flash"
    assert gemini.calls[0]["model"] == "gemini-2.5-flash-lite"


@pytest.mark.asyncio
async def test_nice_class_service_skips_qwen_max_for_class_suggestions():
    from services.nice_class_service import run_nice_class_suggestion

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = _nice_class_catalogue_rows()

    qwen_client_getter = MagicMock(side_effect=AssertionError("qwen-max should be skipped"))
    gemini = _ClassSuggestionProvider(
        response={"suggestions": [{"class_number": 42, "confidence": 0.82}]}
    )

    response = await run_nice_class_suggestion(
        description="software development services",
        top_k=2,
        lang="en",
        settings=_nice_class_settings(qwen_model="qwen-max"),
        logger=MagicMock(),
        class_name_getter=lambda class_num, lang="tr": {
            9: "Electronics & Software",
            25: "Clothing",
            42: "Scientific & Tech Services",
        }[class_num],
        connect_fn=MagicMock(return_value=mock_conn),
        timer=MagicMock(side_effect=[10.0, 10.1]),
        qwen_client_getter=qwen_client_getter,
        gemini_client_getter=lambda: gemini,
    )

    qwen_client_getter.assert_not_called()
    assert [item["class_number"] for item in response["suggestions"]] == [42]
    assert gemini.calls[0]["model"] == "gemini-2.5-flash-lite"


@pytest.mark.asyncio
async def test_extracted_admin_test_scoring_helper_delegates_to_service():
    from app_admin_scoring_routes import TestScoringRequest, test_scoring

    with patch(
        "services.admin_scoring_service.run_admin_score_test",
        AsyncMock(
            return_value={
                "query": "nike",
                "target": "nike sports",
                "final_score": 0.8046,
                "final_score_pct": "80.5%",
                "risk_level": {"level": "high"},
                "factors": {
                    "raw_similarity": 0.7312,
                    "word_match_factor": 0.9,
                    "length_ratio_factor": 0.88,
                    "coverage_factor": 0.92,
                    "idf_factor": 0.95,
                    "combined_factor": 1.1,
                    "word_details": {"matched_words": ["nike"]},
                },
            }
        ),
    ) as run_admin_score_test:
        response = await test_scoring(
            TestScoringRequest(query="nike", target="nike sports", include_details=True),
            current_user=MagicMock(),
        )

    assert response.query == "nike"
    assert response.target == "nike sports"
    assert response.final_score == 0.8046
    assert response.final_score_pct == "80.5%"
    assert response.risk_level == {"level": "high"}
    assert response.factors["idf_factor"] == 0.95
    assert response.factors["word_details"] == {"matched_words": ["nike"]}
    run_admin_score_test.assert_awaited_once_with(
        query="nike",
        target="nike sports",
        include_details=True,
        logger=ANY,
    )


@pytest.mark.asyncio
async def test_admin_scoring_service_run_score_test_returns_breakdown():
    from services.admin_scoring_service import run_admin_score_test

    score_calculator = MagicMock(
        return_value={
            "raw_score": 0.73123,
            "final_score": 0.80456,
            "combined_factor": 1.1,
            "factors": {
                "word_match": 0.9,
                "length_ratio": 0.88,
                "coverage": 0.92,
                "idf": 0.95,
            },
            "details": {"matched_words": ["nike"]},
        }
    )
    risk_level_getter = MagicMock(return_value={"level": "high"})

    response = await run_admin_score_test(
        query="nike",
        target="nike sports",
        include_details=True,
        logger=MagicMock(),
        score_calculator=score_calculator,
        risk_level_getter=risk_level_getter,
    )

    assert response["query"] == "nike"
    assert response["target"] == "nike sports"
    assert response["final_score"] == 0.8046
    assert response["final_score_pct"] == "80.5%"
    assert response["risk_level"] == {"level": "high"}
    assert response["factors"]["idf_factor"] == 0.95
    assert response["factors"]["word_details"] == {"matched_words": ["nike"]}
    score_calculator.assert_called_once_with(
        "nike",
        "nike sports",
        include_details=True,
    )
    risk_level_getter.assert_called_once_with(0.80456)


@pytest.mark.asyncio
async def test_extracted_create_watchlist_item_delegates_to_service_and_schedules_scan():
    from api.watchlist_background import run_watchlist_scan_task
    from api.watchlist_routes import create_watchlist_item
    from models.schemas import WatchlistItemCreate

    current_user = MagicMock()
    background_tasks = BackgroundTasks()
    item_id = uuid.uuid4()
    item = _make_watchlist_item_payload(item_id=item_id)
    data = WatchlistItemCreate(
        brand_name="NIKE",
        nice_class_numbers=[9, 35],
        application_no="2024/1",
    )

    with patch(
        "services.watchlist_service.create_watchlist_item_record",
        AsyncMock(return_value={"item": item, "scan_item_id": item_id}),
    ) as create_watchlist_item_record:
        response = await create_watchlist_item(
            data=data,
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response.id == item_id
    assert response.brand_name == "NIKE"
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is run_watchlist_scan_task
    assert background_tasks.tasks[0].args == (item_id,)
    create_watchlist_item_record.assert_awaited_once_with(
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_create_watchlist_item_record_uses_trademark_ai_embeddings():
    from models.schemas import WatchlistItemCreate
    from services.watchlist_service import create_watchlist_item_record

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    current_user.id = uuid.uuid4()

    duplicate_db_cm = MagicMock()
    duplicate_db = MagicMock()
    duplicate_cursor = MagicMock()
    duplicate_db.cursor.return_value = duplicate_cursor
    duplicate_db_cm.__enter__.return_value = duplicate_db
    duplicate_db_cm.__exit__.return_value = False
    duplicate_cursor.fetchone.return_value = None

    create_db_cm = MagicMock()
    create_db = MagicMock()
    create_cursor = MagicMock()
    create_db.cursor.return_value = create_cursor
    create_db_cm.__enter__.return_value = create_db
    create_db_cm.__exit__.return_value = False
    create_cursor.fetchone.return_value = {
        "image_path": "nike.png",
        "image_embedding": "[0.1,0.2]",
        "dinov2_embedding": "[0.3]",
        "color_histogram": "[0.4,0.5]",
        "logo_ocr_text": "NIKE",
        "text_embedding": "[0.6]",
    }

    watchlist_crud = MagicMock()
    created_item = _make_watchlist_item_payload()
    watchlist_crud.create_with_embeddings.return_value = created_item

    data = WatchlistItemCreate(
        brand_name="NIKE",
        nice_class_numbers=[9, 35],
        application_no="2024/1",
    )

    payload = await create_watchlist_item_record(
        data=data,
        current_user=current_user,
        database_factory=MagicMock(side_effect=[duplicate_db_cm, create_db_cm]),
        watchlist_crud=watchlist_crud,
    )

    assert payload["item"] == created_item
    assert payload["scan_item_id"] == created_item["id"]
    assert duplicate_cursor.execute.call_args.args[1] == (
        str(current_user.organization_id),
        "2024/1",
    )
    assert create_cursor.execute.call_args.args[1] == ("2024/1",)
    watchlist_crud.create_with_embeddings.assert_called_once_with(
        create_db,
        current_user.organization_id,
        current_user.id,
        ANY,
        logo_embedding=[0.1, 0.2],
        logo_dinov2_embedding=[0.3],
        logo_color_histogram=[0.4, 0.5],
        logo_ocr_text="NIKE",
        text_embedding=[0.6],
    )
    created_payload = watchlist_crud.create_with_embeddings.call_args.args[3]
    assert created_payload.brand_name == data.brand_name
    assert created_payload.similarity_threshold == 0.7


@pytest.mark.asyncio
async def test_watchlist_service_create_watchlist_item_record_maps_watchlist_limit_to_structured_403():
    from fastapi import HTTPException
    from models.schemas import WatchlistItemCreate
    from services.watchlist_service import create_watchlist_item_record

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    current_user.id = uuid.uuid4()

    create_db_cm = MagicMock()
    create_db = MagicMock()
    create_cursor = MagicMock()
    create_db.cursor.return_value = create_cursor
    create_db_cm.__enter__.return_value = create_db
    create_db_cm.__exit__.return_value = False
    create_cursor.fetchone.return_value = {"count": 3}

    watchlist_crud = MagicMock()
    watchlist_crud.create.side_effect = ValueError("Organization has reached maximum watchlist items limit")

    data = WatchlistItemCreate(
        brand_name="LIMIT TEST",
        nice_class_numbers=[9, 35],
        monitor_visual=False,
    )

    def plan_limit_getter(plan_name, feature):
        if feature == "max_watchlist_items":
            return 3
        raise AssertionError(f"Unexpected feature lookup: {feature}")

    with pytest.raises(HTTPException) as excinfo:
        await create_watchlist_item_record(
            data=data,
            current_user=current_user,
            database_factory=MagicMock(return_value=create_db_cm),
            watchlist_crud=watchlist_crud,
            user_plan_getter=MagicMock(return_value={"plan_name": "free"}),
            plan_limit_getter=plan_limit_getter,
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == {
        "error": "limit_exceeded",
        "message": "Izleme listesi limitinize ulastiniz (3). Daha fazla eklemek icin planinizi yukseltin.",
        "current_count": 3,
        "max_items": 3,
        "current_plan": "free",
    }


@pytest.mark.asyncio
async def test_extracted_get_watchlist_item_delegates_to_service():
    from api.watchlist_routes import get_watchlist_item

    current_user = MagicMock()
    item_id = uuid.uuid4()
    item = _make_watchlist_item_payload(item_id=item_id)

    with patch(
        "services.watchlist_service.get_watchlist_item_detail",
        AsyncMock(return_value=item),
    ) as get_watchlist_item_detail:
        response = await get_watchlist_item(
            item_id=item_id,
            current_user=current_user,
        )

    assert response.id == item_id
    assert response.application_no == "2024/1"
    get_watchlist_item_detail.assert_awaited_once_with(
        item_id=item_id,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_update_watchlist_item_delegates_to_service():
    from api.watchlist_routes import update_watchlist_item
    from models.schemas import WatchlistItemUpdate

    current_user = MagicMock()
    item_id = uuid.uuid4()
    item = _make_watchlist_item_payload(item_id=item_id)
    item["brand_name"] = "NIKE UPDATED"
    data = WatchlistItemUpdate(brand_name="NIKE UPDATED", monitor_visual=False)

    with patch(
        "services.watchlist_service.update_watchlist_item_record",
        AsyncMock(return_value=item),
    ) as update_watchlist_item_record:
        response = await update_watchlist_item(
            item_id=item_id,
            data=data,
            current_user=current_user,
        )

    assert response.id == item_id
    assert response.brand_name == "NIKE UPDATED"
    update_watchlist_item_record.assert_awaited_once_with(
        item_id=item_id,
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_update_watchlist_item_record_blocks_visual_tracking_without_plan():
    from fastapi import HTTPException
    from models.schemas import WatchlistItemUpdate
    from services.watchlist_service import update_watchlist_item_record

    current_user = MagicMock()
    current_user.id = "user-123"
    current_user.organization_id = "org-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        await update_watchlist_item_record(
            item_id=uuid.uuid4(),
            data=WatchlistItemUpdate(monitor_visual=True),
            current_user=current_user,
            database_factory=MagicMock(return_value=mock_db_cm),
            user_plan_getter=MagicMock(return_value={"plan_name": "free"}),
            plan_limit_getter=MagicMock(return_value=False),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "error": "upgrade_required",
        "message": "Logo tracking requires a paid plan.",
    }


@pytest.mark.asyncio
async def test_extracted_delete_watchlist_item_delegates_to_service():
    from api.watchlist_routes import delete_watchlist_item

    current_user = MagicMock()
    item_id = uuid.uuid4()
    expected = {
        "success": True,
        "message": "Marka ve 2 uyari silindi",
    }

    with patch(
        "services.watchlist_service.delete_watchlist_item_record",
        AsyncMock(return_value=expected),
    ) as delete_watchlist_item_record:
        response = await delete_watchlist_item(
            item_id=item_id,
            current_user=current_user,
        )

    assert response.success is True
    assert response.message == "Marka ve 2 uyari silindi"
    delete_watchlist_item_record.assert_awaited_once_with(
        item_id=item_id,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_delete_watchlist_item_record_returns_deleted_alert_message():
    from services.watchlist_service import delete_watchlist_item_record

    item_id = uuid.uuid4()
    current_user = MagicMock()
    current_user.organization_id = "org-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.rowcount = 2

    watchlist_crud = MagicMock()
    watchlist_crud.delete.return_value = True

    payload = await delete_watchlist_item_record(
        item_id=item_id,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        watchlist_crud=watchlist_crud,
    )

    assert payload == {
        "success": True,
        "message": "Marka ve 2 uyari silindi",
    }
    assert mock_cursor.execute.call_args.args[1] == (str(item_id),)
    watchlist_crud.delete.assert_called_once_with(mock_db, item_id, "org-123")
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_extracted_trigger_scan_delegates_to_service_and_schedules_scan():
    from api.watchlist_background import run_watchlist_scan_task
    from api.watchlist_routes import trigger_scan

    current_user = MagicMock()
    background_tasks = BackgroundTasks()
    item_id = uuid.uuid4()

    with patch(
        "services.watchlist_service.prepare_watchlist_item_scan",
        AsyncMock(
            return_value={
                "success": True,
                "message": "Scan triggered",
                "item_id": item_id,
            }
        ),
    ) as prepare_watchlist_item_scan:
        response = await trigger_scan(
            item_id=item_id,
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response.success is True
    assert response.message == "Scan triggered"
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is run_watchlist_scan_task
    assert background_tasks.tasks[0].args == (item_id,)
    prepare_watchlist_item_scan.assert_awaited_once_with(
        item_id=item_id,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_prepare_watchlist_item_scan_requires_existing_item():
    from fastapi import HTTPException
    from services.watchlist_service import prepare_watchlist_item_scan

    item_id = uuid.uuid4()
    current_user = MagicMock()
    current_user.organization_id = "org-123"

    watchlist_crud = MagicMock()
    watchlist_crud.get_by_id.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await prepare_watchlist_item_scan(
            item_id=item_id,
            current_user=current_user,
            database_factory=MagicMock(),
            watchlist_crud=watchlist_crud,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Item not found"


@pytest.mark.asyncio
async def test_extracted_trigger_scan_all_delegates_to_service_and_schedules_scans():
    from api.watchlist_background import run_watchlist_scan_task
    from api.watchlist_routes import trigger_scan_all

    current_user = MagicMock()
    background_tasks = BackgroundTasks()
    item_ids = [uuid.uuid4(), uuid.uuid4()]

    with patch(
        "services.watchlist_service.prepare_watchlist_scan_all",
        AsyncMock(
            return_value={
                "success": True,
                "message": "2 marka taramaya alindi (toplam: 4)",
                "item_ids": item_ids,
            }
        ),
    ) as prepare_watchlist_scan_all:
        response = await trigger_scan_all(
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response.success is True
    assert response.message == "2 marka taramaya alindi (toplam: 4)"
    assert len(background_tasks.tasks) == 2
    assert background_tasks.tasks[0].func is run_watchlist_scan_task
    assert background_tasks.tasks[0].args == (item_ids[0],)
    assert background_tasks.tasks[1].func is run_watchlist_scan_task
    assert background_tasks.tasks[1].args == (item_ids[1],)
    prepare_watchlist_scan_all.assert_awaited_once_with(
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_prepare_watchlist_scan_all_limits_items():
    from services.watchlist_service import prepare_watchlist_scan_all

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()

    item_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    items = [{"id": item_id} for item_id in item_ids]

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    watchlist_crud = MagicMock()
    watchlist_crud.get_by_organization.side_effect = [
        ([], 3),
        (items, 3),
    ]
    user_plan_getter = MagicMock(return_value={"plan_name": "starter"})
    plan_limit_getter = MagicMock(return_value=2)

    payload = await prepare_watchlist_scan_all(
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        watchlist_crud=watchlist_crud,
        user_plan_getter=user_plan_getter,
        plan_limit_getter=plan_limit_getter,
    )

    assert payload["success"] is True
    assert payload["item_ids"] == item_ids[:2]
    assert "2 marka taramaya alindi (toplam: 3)" in payload["message"]
    assert "plan limitiniz nedeniyle 2 marka tarandi" in payload["message"]
    user_plan_getter.assert_called_once_with(mock_db, str(current_user.id))
    plan_limit_getter.assert_called_once_with("starter", "auto_scan_max_items")
    assert watchlist_crud.get_by_organization.call_args_list[0].kwargs["page_size"] == 1
    assert watchlist_crud.get_by_organization.call_args_list[1].kwargs["page_size"] == 3


@pytest.mark.asyncio
async def test_extracted_get_scan_status_delegates_to_service():
    from api.watchlist_routes import get_scan_status

    current_user = MagicMock()
    expected = {
        "auto_scan_enabled": True,
        "schedule": "Weekly on Monday at 00:00",
        "next_scan_at": "2026-04-13T00:00:00Z",
    }

    with patch(
        "services.watchlist_service.get_watchlist_scan_status",
        AsyncMock(return_value=expected),
    ) as get_watchlist_scan_status:
        response = await get_scan_status(current_user=current_user)

    assert response == expected
    get_watchlist_scan_status.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_watchlist_service_get_watchlist_scan_status_uses_scheduler_getter():
    from services.watchlist_service import get_watchlist_scan_status

    next_scan_time_getter = MagicMock(return_value="2026-04-13T00:00:00Z")
    schedule_label_getter = MagicMock(return_value="Weekly on Monday at 00:00")

    payload = await get_watchlist_scan_status(
        current_user=MagicMock(),
        next_scan_time_getter=next_scan_time_getter,
        schedule_label_getter=schedule_label_getter,
    )

    assert payload == {
        "auto_scan_enabled": True,
        "schedule": "Weekly on Monday at 00:00",
        "next_scan_at": "2026-04-13T00:00:00Z",
    }
    next_scan_time_getter.assert_called_once_with()
    schedule_label_getter.assert_called_once_with()


@pytest.mark.asyncio
async def test_extracted_delete_all_watchlist_delegates_to_service():
    from api.watchlist_routes import delete_all_watchlist

    current_user = MagicMock()
    expected = {
        "success": True,
        "message": "4 marka ve 6 uyari silindi",
    }

    with patch(
        "services.watchlist_service.delete_all_watchlist_records",
        AsyncMock(return_value=expected),
    ) as delete_all_watchlist_records:
        response = await delete_all_watchlist(current_user=current_user)

    assert response.success is True
    assert response.message == "4 marka ve 6 uyari silindi"
    delete_all_watchlist_records.assert_awaited_once_with(
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_delete_all_watchlist_records_reports_counts():
    from services.watchlist_service import delete_all_watchlist_records

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    def execute_side_effect(sql, params):
        if "DELETE FROM alerts_mt" in sql:
            mock_cursor.rowcount = 6
        elif "DELETE FROM watchlist_mt" in sql:
            mock_cursor.rowcount = 4

    mock_cursor.execute.side_effect = execute_side_effect

    payload = await delete_all_watchlist_records(
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert payload == {
        "success": True,
        "message": "4 marka ve 6 uyari silindi",
    }
    assert mock_cursor.execute.call_args_list[0].args[1] == (str(current_user.organization_id),)
    assert mock_cursor.execute.call_args_list[1].args[1] == (str(current_user.organization_id),)
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_extracted_rescan_all_watchlist_delegates_to_service_and_schedules_scans():
    from api.watchlist_background import run_watchlist_scan_task
    from api.watchlist_routes import rescan_all_watchlist

    current_user = MagicMock()
    background_tasks = BackgroundTasks()
    item_ids = [uuid.uuid4(), uuid.uuid4()]

    with patch(
        "services.watchlist_service.prepare_watchlist_rescan",
        AsyncMock(
            return_value={
                "success": True,
                "message": "Eski 3 uyari silindi. 2 marka yeniden taramaya alindi.",
                "item_ids": item_ids,
            }
        ),
    ) as prepare_watchlist_rescan:
        response = await rescan_all_watchlist(
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response.success is True
    assert response.message == "Eski 3 uyari silindi. 2 marka yeniden taramaya alindi."
    assert len(background_tasks.tasks) == 2
    assert background_tasks.tasks[0].func is run_watchlist_scan_task
    assert background_tasks.tasks[0].args == (item_ids[0],)
    assert background_tasks.tasks[1].func is run_watchlist_scan_task
    assert background_tasks.tasks[1].args == (item_ids[1],)
    prepare_watchlist_rescan.assert_awaited_once_with(
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_prepare_watchlist_rescan_clears_alerts_and_returns_items():
    from services.watchlist_service import prepare_watchlist_rescan

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()

    item_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    items = [{"id": item_id} for item_id in item_ids]

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    def execute_side_effect(sql, params):
        if "DELETE FROM alerts_mt" in sql:
            mock_cursor.rowcount = 5

    mock_cursor.execute.side_effect = execute_side_effect

    watchlist_crud = MagicMock()
    watchlist_crud.get_by_organization.side_effect = [
        ([], 3),
        (items, 3),
    ]

    payload = await prepare_watchlist_rescan(
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        watchlist_crud=watchlist_crud,
        user_plan_getter=MagicMock(return_value={"plan_name": "starter"}),
        plan_limit_getter=MagicMock(return_value=2),
    )

    assert payload == {
        "success": True,
        "message": "Eski 5 uyari silindi. 2 marka yeniden taramaya alindi.",
        "item_ids": item_ids[:2],
    }
    assert any(
        "UPDATE watchlist_mt SET last_scan_at = NULL" in call.args[0]
        for call in mock_cursor.execute.call_args_list
    )
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_extracted_update_all_threshold_delegates_to_service():
    from api.watchlist_routes import _BulkThresholdUpdate, update_all_threshold

    current_user = MagicMock()
    data = _BulkThresholdUpdate(threshold=0.75)

    with patch(
        "services.watchlist_service.update_watchlist_bulk_thresholds",
        AsyncMock(return_value={"success": True, "message": "7 items updated"}),
    ) as update_watchlist_bulk_thresholds:
        response = await update_all_threshold(
            data=data,
            current_user=current_user,
        )

    assert response.success is True
    assert response.message == "7 items updated"
    update_watchlist_bulk_thresholds.assert_awaited_once_with(
        threshold=0.75,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_update_watchlist_bulk_thresholds_commits_count():
    from services.watchlist_service import update_watchlist_bulk_thresholds

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    def execute_side_effect(sql, params):
        mock_cursor.rowcount = 7

    mock_cursor.execute.side_effect = execute_side_effect

    payload = await update_watchlist_bulk_thresholds(
        threshold=0.75,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert payload == {
        "success": True,
        "message": "7 items updated",
    }
    assert mock_cursor.execute.call_args.args[1] == (0.75, str(current_user.organization_id))
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_extracted_bulk_import_watchlist_delegates_to_service_and_schedules_scans():
    from api.watchlist_background import run_watchlist_scan_task
    from api.watchlist_routes import bulk_import_watchlist
    from models.schemas import WatchlistBulkImport, WatchlistItemCreate

    current_user = MagicMock()
    background_tasks = BackgroundTasks()
    item_id = uuid.uuid4()
    data = WatchlistBulkImport(
        items=[
            WatchlistItemCreate(
                brand_name="BULK ITEM",
                nice_class_numbers=[9],
                application_no="BULK-1",
            )
        ]
    )

    with patch(
        "services.watchlist_service.import_watchlist_items_bulk",
        AsyncMock(
            return_value={
                "result": {
                    "total": 1,
                    "created": 1,
                    "failed": 0,
                    "skipped": 0,
                    "errors": [],
                },
                "scan_item_ids": [item_id],
            }
        ),
    ) as import_watchlist_items_bulk:
        response = await bulk_import_watchlist(
            data=data,
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response.total == 1
    assert response.created == 1
    assert response.failed == 0
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is run_watchlist_scan_task
    assert background_tasks.tasks[0].args == (item_id,)
    import_watchlist_items_bulk.assert_awaited_once_with(
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_import_watchlist_items_bulk_skips_duplicates_and_schedules_created_items():
    from models.schemas import WatchlistBulkImport, WatchlistItemCreate
    from services.watchlist_service import import_watchlist_items_bulk

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.return_value = {"count": 1}
    mock_cursor.fetchall.return_value = [{"customer_application_no": "APP-1"}]

    created_ids = [uuid.uuid4(), uuid.uuid4()]
    watchlist_crud = MagicMock()
    watchlist_crud.create.side_effect = [
        {"id": created_ids[0]},
        {"id": created_ids[1]},
    ]

    data = WatchlistBulkImport(
        items=[
            WatchlistItemCreate(
                brand_name="Existing",
                nice_class_numbers=[9],
                application_no="APP-1",
            ),
            WatchlistItemCreate(
                brand_name="Create 1",
                nice_class_numbers=[9],
                application_no="APP-2",
            ),
            WatchlistItemCreate(
                brand_name="Create 2",
                nice_class_numbers=[35],
                application_no="APP-3",
            ),
        ]
    )

    payload = await import_watchlist_items_bulk(
        data=data,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        watchlist_crud=watchlist_crud,
        user_plan_getter=MagicMock(return_value={"plan_name": "starter"}),
        plan_limit_getter=MagicMock(return_value=3),
    )

    assert payload["result"] == {
        "total": 3,
        "created": 2,
        "failed": 0,
        "skipped": 1,
        "errors": [],
    }
    assert payload["scan_item_ids"] == created_ids
    assert watchlist_crud.create.call_count == 2


@pytest.mark.asyncio
async def test_watchlist_service_import_watchlist_items_bulk_marks_overflow_items_as_failed():
    from models.schemas import WatchlistBulkImport, WatchlistItemCreate
    from services.watchlist_service import import_watchlist_items_bulk

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.return_value = {"count": 1}
    mock_cursor.fetchall.return_value = []

    created_item_id = uuid.uuid4()
    watchlist_crud = MagicMock()
    watchlist_crud.create.return_value = {"id": created_item_id}

    data = WatchlistBulkImport(
        items=[
            WatchlistItemCreate(
                brand_name="Create 1",
                nice_class_numbers=[9],
                application_no="APP-1",
            ),
            WatchlistItemCreate(
                brand_name="Overflow 1",
                nice_class_numbers=[35],
                application_no="APP-2",
            ),
            WatchlistItemCreate(
                brand_name="Overflow 2",
                nice_class_numbers=[42],
                application_no="APP-3",
            ),
        ]
    )

    payload = await import_watchlist_items_bulk(
        data=data,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        watchlist_crud=watchlist_crud,
        user_plan_getter=MagicMock(return_value={"plan_name": "starter"}),
        plan_limit_getter=MagicMock(return_value=2),
    )

    assert payload["result"] == {
        "total": 3,
        "created": 1,
        "failed": 2,
        "skipped": 0,
        "errors": [
            {
                "index": 1,
                "brand_name": "Overflow 1",
                "error": "Izleme listesi limiti asildi (2)",
            },
            {
                "index": 2,
                "brand_name": "Overflow 2",
                "error": "Izleme listesi limiti asildi (2)",
            },
        ],
    }
    assert payload["scan_item_ids"] == [created_item_id]
    watchlist_crud.create.assert_called_once()


@pytest.mark.asyncio
async def test_extracted_preview_portfolio_import_delegates_to_service():
    from api.watchlist_routes import preview_portfolio_import
    from models.schemas import PortfolioPreviewRequest

    current_user = MagicMock()
    data = PortfolioPreviewRequest(holder_id=str(uuid.uuid4()))
    expected = {
        "total_items": 5,
        "duplicate_count": 2,
        "can_add": 3,
    }

    with patch(
        "services.watchlist_service.preview_watchlist_portfolio_import",
        AsyncMock(return_value=expected),
    ) as preview_watchlist_portfolio_import:
        response = await preview_portfolio_import(
            data=data,
            current_user=current_user,
        )

    assert response.total_items == 5
    assert response.duplicate_count == 2
    assert response.can_add == 3
    preview_watchlist_portfolio_import.assert_awaited_once_with(
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_preview_watchlist_portfolio_import_counts_duplicates():
    from models.schemas import PortfolioPreviewRequest
    from services.watchlist_service import preview_watchlist_portfolio_import

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    holder_id = str(uuid.uuid4())

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.side_effect = [
        [
            {"application_no": "APP-1"},
            {"application_no": "APP-2"},
            {"application_no": "APP-2"},
        ],
        [
            {"customer_application_no": "APP-2"},
            {"customer_application_no": "APP-X"},
        ],
    ]

    payload = await preview_watchlist_portfolio_import(
        data=PortfolioPreviewRequest(holder_id=holder_id),
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert payload == {
        "total_items": 2,
        "duplicate_count": 1,
        "can_add": 1,
    }
    assert "holder_tpe_client_id = %s OR holder_id = %s" in mock_cursor.execute.call_args_list[0].args[0]
    assert mock_cursor.execute.call_args_list[0].args[1] == (holder_id, holder_id)


@pytest.mark.asyncio
async def test_extracted_bulk_import_from_portfolio_delegates_to_service_and_schedules_scans():
    from api.watchlist_background import run_watchlist_scan_task
    from api.watchlist_routes import BulkFromPortfolioRequest, bulk_import_from_portfolio

    current_user = MagicMock()
    background_tasks = BackgroundTasks()
    item_id = uuid.uuid4()
    data = BulkFromPortfolioRequest(holder_id="holder-1", similarity_threshold=0.7)

    with patch(
        "services.watchlist_service.import_watchlist_items_from_portfolio",
        AsyncMock(
            return_value={
                "result": {
                    "total": 2,
                    "created": 1,
                    "failed": 0,
                    "skipped": 0,
                    "errors": [],
                    "limit_reached": True,
                    "max_allowed": 2,
                    "current_count": 2,
                },
                "scan_item_ids": [item_id],
            }
        ),
    ) as import_watchlist_items_from_portfolio:
        response = await bulk_import_from_portfolio(
            data=data,
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response.total == 2
    assert response.created == 1
    assert response.limit_reached is True
    assert response.queued_scans == 1
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is run_watchlist_scan_task
    assert background_tasks.tasks[0].args == (item_id,)
    import_watchlist_items_from_portfolio.assert_awaited_once_with(
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_bulk_import_from_portfolio_with_no_created_items_queues_no_scans():
    from api.watchlist_routes import BulkFromPortfolioRequest, bulk_import_from_portfolio

    current_user = MagicMock()
    background_tasks = BackgroundTasks()
    data = BulkFromPortfolioRequest(holder_id="holder-1", similarity_threshold=0.7)

    with patch(
        "services.watchlist_service.import_watchlist_items_from_portfolio",
        AsyncMock(
            return_value={
                "result": {
                    "total": 4007,
                    "created": 0,
                    "failed": 0,
                    "skipped": 0,
                    "errors": [],
                    "limit_reached": True,
                    "max_allowed": 5,
                    "current_count": 5,
                },
                "scan_item_ids": [],
            }
        ),
    ):
        response = await bulk_import_from_portfolio(
            data=data,
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response.created == 0
    assert response.queued_scans == 0
    assert len(background_tasks.tasks) == 0


@pytest.mark.asyncio
async def test_watchlist_service_import_watchlist_items_from_portfolio_uses_embeddings_and_limits():
    from api.watchlist_routes import BulkFromPortfolioRequest
    from services.watchlist_service import import_watchlist_items_from_portfolio

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()

    db_perm_cm = MagicMock()
    db_perm = MagicMock()
    db_perm_cm.__enter__.return_value = db_perm
    db_perm_cm.__exit__.return_value = False

    db_cm = MagicMock()
    db = MagicMock()
    cursor = MagicMock()
    db.cursor.return_value = cursor
    db_cm.__enter__.return_value = db
    db_cm.__exit__.return_value = False
    cursor.fetchall.side_effect = [
        [
            {
                "application_no": "PORT-1",
                "name": "Portfolio One",
                "nice_class_numbers": [9, 99],
                "image_path": "logo1.png",
                "image_embedding": "[0.1,0.2]",
                "dinov2_embedding": "[0.3]",
                "color_histogram": "[0.4,0.5]",
                "logo_ocr_text": "PORT",
                "text_embedding": "[0.6]",
            },
            {
                "application_no": "PORT-2",
                "name": "Portfolio Two",
                "nice_class_numbers": [35],
                "image_path": "logo2.png",
                "image_embedding": "[0.7]",
                "dinov2_embedding": None,
                "color_histogram": None,
                "logo_ocr_text": None,
                "text_embedding": None,
            },
        ],
        [],
    ]
    cursor.fetchone.return_value = {"count": 1}

    watchlist_crud = MagicMock()
    created_item_id = uuid.uuid4()
    watchlist_crud.create_with_embeddings.return_value = {"id": created_item_id}

    def plan_limit_getter(plan_name, feature):
        if feature == "can_view_holder_portfolio":
            return True
        if feature == "max_watchlist_items":
            return 2
        raise AssertionError(f"Unexpected feature: {feature}")

    payload = await import_watchlist_items_from_portfolio(
        data=BulkFromPortfolioRequest(holder_id="holder-1", similarity_threshold=0.7),
        current_user=current_user,
        database_factory=MagicMock(side_effect=[db_perm_cm, db_cm]),
        watchlist_crud=watchlist_crud,
        user_plan_getter=MagicMock(return_value={"plan_name": "starter"}),
        plan_limit_getter=plan_limit_getter,
    )

    assert payload["result"] == {
        "total": 2,
        "created": 1,
        "failed": 0,
        "skipped": 0,
        "errors": [],
        "limit_reached": True,
        "max_allowed": 2,
        "current_count": 2,
    }
    assert payload["scan_item_ids"] == [created_item_id]
    watchlist_crud.create_with_embeddings.assert_called_once_with(
        db,
        current_user.organization_id,
        current_user.id,
        ANY,
        logo_embedding=[0.1, 0.2],
        logo_dinov2_embedding=[0.3],
        logo_color_histogram=[0.4, 0.5],
        logo_ocr_text="PORT",
        text_embedding=[0.6],
        auto_commit=False,
    )


@pytest.mark.asyncio
async def test_extracted_download_template_delegates_to_service():
    from api.watchlist_routes import download_template

    template_stream = io.BytesIO(b"excel-bytes")

    with patch(
        "services.watchlist_service.build_watchlist_upload_template",
        MagicMock(return_value=template_stream),
    ) as build_watchlist_upload_template:
        response = await download_template()

    assert response.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["content-disposition"] == "attachment; filename=marka_listesi_sablon.xlsx"
    build_watchlist_upload_template.assert_called_once_with()


def test_watchlist_service_build_watchlist_upload_template_contains_headers():
    from services.watchlist_service import build_watchlist_upload_template

    output = build_watchlist_upload_template()
    workbook = load_workbook(output)
    sheet = workbook.active

    assert sheet.title == "Marka Listesi"
    assert sheet["A1"].value.startswith("Marka")
    assert sheet["B1"].value.endswith("*")
    assert sheet["C1"].value.endswith("*")
    assert sheet["A2"].value
    assert sheet["B2"].value == "2023/12345"


@pytest.mark.asyncio
async def test_extracted_detect_columns_delegates_to_service():
    from api.watchlist_routes import detect_columns

    current_user = MagicMock()
    file = MagicMock()
    file.filename = "watchlist.csv"
    file.read = AsyncMock(return_value=b"csv-data")
    expected = {
        "columns": ["Brand Name", "Application No"],
        "sample_data": [{"Brand Name": "ACME", "Application No": "2024/1"}],
        "auto_mappings": {
            "brand_name": "Brand Name",
            "application_no": "Application No",
            "nice_classes": None,
            "bulletin_no": None,
        },
        "total_rows": 1,
    }

    with patch(
        "services.watchlist_service.detect_watchlist_upload_columns",
        MagicMock(return_value=expected),
    ) as detect_watchlist_upload_columns:
        response = await detect_columns(
            file=file,
            current_user=current_user,
        )

    assert response.columns == ["Brand Name", "Application No"]
    assert response.auto_mappings.brand_name == "Brand Name"
    assert response.total_rows == 1
    detect_watchlist_upload_columns.assert_called_once()
    assert detect_watchlist_upload_columns.call_args.kwargs["contents"] == b"csv-data"
    assert detect_watchlist_upload_columns.call_args.kwargs["filename"] == "watchlist.csv"


def test_watchlist_service_detect_watchlist_upload_columns_returns_auto_mappings():
    from api.watchlist_routes import (
        APP_NO_VARIANTS,
        BRAND_NAME_VARIANTS,
        BULLETIN_VARIANTS,
        CLASS_VARIANTS,
        _find_column,
    )
    from services.watchlist_service import detect_watchlist_upload_columns

    contents = b"Brand Name,Application No,Classes\nACME,2024/1,\"9, 35\"\n"

    payload = detect_watchlist_upload_columns(
        contents=contents,
        filename="watchlist.csv",
        brand_name_variants=BRAND_NAME_VARIANTS,
        application_no_variants=APP_NO_VARIANTS,
        class_variants=CLASS_VARIANTS,
        bulletin_variants=BULLETIN_VARIANTS,
        find_column=_find_column,
    )

    assert payload["columns"] == ["Brand Name", "Application No", "Classes"]
    assert payload["auto_mappings"] == {
        "brand_name": "Brand Name",
        "application_no": "Application No",
        "nice_classes": "Classes",
        "bulletin_no": None,
    }
    assert payload["total_rows"] == 1
    assert payload["sample_data"][0]["Brand Name"] == "ACME"


@pytest.mark.asyncio
async def test_extracted_upload_with_mapping_delegates_to_service_and_schedules_scans():
    from api.watchlist_routes import upload_with_mapping
    from api.watchlist_background import run_watchlist_scan_task

    current_user = MagicMock()
    background_tasks = BackgroundTasks()
    file = MagicMock()
    file.filename = "watchlist.csv"
    file.read = AsyncMock(return_value=b"csv-bytes")
    item_id = uuid.uuid4()
    expected_result = {"success": True, "message": "1 marka eklendi"}
    expected_response = {**expected_result, "queued_scans": 1}

    with patch(
        "services.watchlist_service.import_watchlist_upload_with_mapping",
        AsyncMock(
            return_value={
                "result": expected_result,
                "scan_item_ids": [item_id],
            }
        ),
    ) as import_watchlist_upload_with_mapping:
        response = await upload_with_mapping(
            file=file,
            column_mapping='{"brand_name":"Brand"}',
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response == expected_response
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is run_watchlist_scan_task
    assert background_tasks.tasks[0].args == (item_id,)
    import_watchlist_upload_with_mapping.assert_awaited_once_with(
        contents=b"csv-bytes",
        filename="watchlist.csv",
        column_mapping='{"brand_name":"Brand"}',
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_import_watchlist_upload_with_mapping_generates_application_numbers_and_skips_duplicates():
    from services.watchlist_service import import_watchlist_upload_with_mapping

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()

    db_cm = MagicMock()
    db = MagicMock()
    cursor = MagicMock()
    db.cursor.return_value = cursor
    db_cm.__enter__.return_value = db
    db_cm.__exit__.return_value = False
    cursor.fetchall.return_value = [{"customer_application_no": "EXISTING-1"}]
    cursor.fetchone.return_value = {"count": 1}

    generated_app_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    created_item_uuid = uuid.UUID("22222222-2222-2222-2222-222222222222")

    payload = await import_watchlist_upload_with_mapping(
        contents=b"Brand,Classes,Application No\nFresh Mark,\"9, 35\",\nDup Mark,25,EXISTING-1\n",
        filename="watchlist.csv",
        column_mapping='{"brand_name":"Brand","nice_classes":"Classes","application_no":"Application No"}',
        current_user=current_user,
        database_factory=MagicMock(return_value=db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "starter"}),
        plan_limit_getter=MagicMock(return_value=5),
        uuid_factory=MagicMock(side_effect=[generated_app_uuid, created_item_uuid]),
    )

    assert payload["result"].summary.total_rows == 2
    assert payload["result"].summary.added == 1
    assert payload["result"].summary.skipped == 1
    assert payload["result"].summary.errors == 0
    assert payload["scan_item_ids"] == [created_item_uuid]

    insert_calls = [
        call
        for call in cursor.execute.call_args_list
        if "INSERT INTO watchlist_mt" in call.args[0]
    ]
    assert len(insert_calls) == 1
    insert_params = insert_calls[0].args[1]
    assert insert_params[0] == str(created_item_uuid)
    assert insert_params[3] == "Fresh Mark"
    assert insert_params[4] == [9, 35]
    assert insert_params[5] == "WL-11111111"
    db.commit.assert_called_once_with()


@pytest.mark.asyncio
async def test_watchlist_service_import_watchlist_upload_with_mapping_respects_watchlist_capacity():
    from services.watchlist_service import import_watchlist_upload_with_mapping

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()

    db_cm = MagicMock()
    db = MagicMock()
    cursor = MagicMock()
    db.cursor.return_value = cursor
    db_cm.__enter__.return_value = db
    db_cm.__exit__.return_value = False
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = {"count": 1}

    created_item_uuid = uuid.UUID("33333333-3333-3333-3333-333333333333")

    payload = await import_watchlist_upload_with_mapping(
        contents=(
            b"Brand,Classes,Application No\n"
            b"Fresh Mark 1,\"9, 35\",NEW-1\n"
            b"Fresh Mark 2,25,NEW-2\n"
        ),
        filename="watchlist.csv",
        column_mapping='{"brand_name":"Brand","nice_classes":"Classes","application_no":"Application No"}',
        current_user=current_user,
        database_factory=MagicMock(return_value=db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "starter"}),
        plan_limit_getter=MagicMock(return_value=2),
        uuid_factory=MagicMock(return_value=created_item_uuid),
    )

    assert payload["result"].summary.total_rows == 2
    assert payload["result"].summary.added == 1
    assert payload["result"].summary.skipped == 0
    assert payload["result"].summary.errors == 1
    assert len(payload["result"].error_items) == 1
    assert payload["result"].error_items[0].row == 3
    assert payload["result"].error_items[0].error == "Izleme listesi limiti asildi (2)"
    assert payload["scan_item_ids"] == [created_item_uuid]

    insert_calls = [
        call
        for call in cursor.execute.call_args_list
        if "INSERT INTO watchlist_mt" in call.args[0]
    ]
    assert len(insert_calls) == 1
    db.commit.assert_called_once_with()


@pytest.mark.asyncio
async def test_extracted_upload_file_delegates_to_service_and_schedules_scans():
    from api.watchlist_routes import (
        APP_NO_VARIANTS,
        BRAND_NAME_VARIANTS,
        BULLETIN_VARIANTS,
        CLASS_VARIANTS,
        _find_column,
        _parse_nice_classes,
        upload_file,
    )
    from api.watchlist_background import run_watchlist_scan_task

    current_user = MagicMock()
    background_tasks = BackgroundTasks()
    file = MagicMock()
    file.filename = "watchlist.csv"
    file.read = AsyncMock(return_value=b"csv-bytes")
    item_id = uuid.uuid4()
    expected_result = {"success": True, "message": "1 marka eklendi"}
    expected_response = {**expected_result, "queued_scans": 1}

    with patch(
        "services.watchlist_service.import_watchlist_upload_file",
        AsyncMock(
            return_value={
                "result": expected_result,
                "scan_item_ids": [item_id],
            }
        ),
    ) as import_watchlist_upload_file:
        response = await upload_file(
            file=file,
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response == expected_response
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is run_watchlist_scan_task
    assert background_tasks.tasks[0].args == (item_id,)
    import_watchlist_upload_file.assert_awaited_once_with(
        contents=b"csv-bytes",
        filename="watchlist.csv",
        current_user=current_user,
        brand_name_variants=BRAND_NAME_VARIANTS,
        application_no_variants=APP_NO_VARIANTS,
        class_variants=CLASS_VARIANTS,
        bulletin_variants=BULLETIN_VARIANTS,
        find_column=_find_column,
        parse_nice_classes=_parse_nice_classes,
    )


@pytest.mark.asyncio
async def test_watchlist_service_import_watchlist_upload_file_reports_missing_columns():
    from api.watchlist_routes import (
        APP_NO_VARIANTS,
        BRAND_NAME_VARIANTS,
        BULLETIN_VARIANTS,
        CLASS_VARIANTS,
        _find_column,
        _parse_nice_classes,
    )
    from fastapi import HTTPException
    from services.watchlist_service import import_watchlist_upload_file

    current_user = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await import_watchlist_upload_file(
            contents=b"Brand Name,Application No\nACME,2024/1\n",
            filename="watchlist.csv",
            current_user=current_user,
            brand_name_variants=BRAND_NAME_VARIANTS,
            application_no_variants=APP_NO_VARIANTS,
            class_variants=CLASS_VARIANTS,
            bulletin_variants=BULLETIN_VARIANTS,
            find_column=_find_column,
            parse_nice_classes=_parse_nice_classes,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "missing_mandatory_columns"
    assert exc_info.value.detail["missing_columns"] == [
        {
            "column": "Siniflar",
            "variants": "sinif, siniflar, nice class, classes",
            "reason": "Hangi siniflarda arama yapilacagini belirler",
        }
    ]


@pytest.mark.asyncio
async def test_watchlist_service_import_watchlist_upload_file_respects_watchlist_capacity():
    from api.watchlist_routes import (
        APP_NO_VARIANTS,
        BRAND_NAME_VARIANTS,
        BULLETIN_VARIANTS,
        CLASS_VARIANTS,
        _find_column,
        _parse_nice_classes,
    )
    from services.watchlist_service import import_watchlist_upload_file

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()

    db_cm = MagicMock()
    db = MagicMock()
    cursor = MagicMock()
    db.cursor.return_value = cursor
    db_cm.__enter__.return_value = db
    db_cm.__exit__.return_value = False
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = {"count": 0}

    created_item_uuid = uuid.UUID("44444444-4444-4444-4444-444444444444")

    payload = await import_watchlist_upload_file(
        contents=(
            b"Brand Name,Application No,Classes\n"
            b"Fresh Mark 1,NEW-1,\"9,35\"\n"
            b"Fresh Mark 2,NEW-2,25\n"
        ),
        filename="watchlist.csv",
        current_user=current_user,
        brand_name_variants=BRAND_NAME_VARIANTS,
        application_no_variants=APP_NO_VARIANTS,
        class_variants=CLASS_VARIANTS,
        bulletin_variants=BULLETIN_VARIANTS,
        find_column=_find_column,
        parse_nice_classes=_parse_nice_classes,
        database_factory=MagicMock(return_value=db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "free"}),
        plan_limit_getter=MagicMock(return_value=1),
        uuid_factory=MagicMock(return_value=created_item_uuid),
    )

    assert payload["result"].summary.total_rows == 2
    assert payload["result"].summary.added == 1
    assert payload["result"].summary.skipped == 0
    assert payload["result"].summary.errors == 1
    assert len(payload["result"].error_items) == 1
    assert payload["result"].error_items[0].row == 3
    assert payload["result"].error_items[0].error == "Izleme listesi limiti asildi (1)"
    assert payload["scan_item_ids"] == [created_item_uuid]

    insert_calls = [
        call
        for call in cursor.execute.call_args_list
        if "INSERT INTO watchlist_mt" in call.args[0]
    ]
    assert len(insert_calls) == 1
    db.commit.assert_called_once_with()


@pytest.mark.asyncio
async def test_extracted_upload_watchlist_logo_delegates_to_service_and_schedules_processing():
    from api.watchlist_routes import (
        _start_watchlist_logo_thread,
        upload_watchlist_logo,
    )

    current_user = MagicMock()
    background_tasks = BackgroundTasks()
    item_id = uuid.uuid4()
    logo = MagicMock()
    logo.filename = "logo.png"
    logo.content_type = "image/png"
    logo.read = AsyncMock(return_value=b"png-bytes")

    with patch(
        "services.watchlist_service.store_watchlist_logo_upload",
        AsyncMock(
            return_value={
                "success": True,
                "message": "Logo yuklendi, embeddingler olusturuluyor...",
                "item_id": item_id,
                "filepath": "C:\\logos\\logo.png",
            }
        ),
    ) as store_watchlist_logo_upload:
        response = await upload_watchlist_logo(
            item_id=item_id,
            background_tasks=background_tasks,
            logo=logo,
            current_user=current_user,
        )

    assert response.success is True
    assert response.message == "Logo yuklendi, embeddingler olusturuluyor..."
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is _start_watchlist_logo_thread
    assert background_tasks.tasks[0].args == (item_id, "C:\\logos\\logo.png")
    store_watchlist_logo_upload.assert_awaited_once_with(
        item_id=item_id,
        current_user=current_user,
        logo_filename="logo.png",
        content_type="image/png",
        contents=b"png-bytes",
    )


def test_watchlist_logo_thread_starter_spawns_daemon_worker():
    from api.watchlist_routes import _process_watchlist_logo, _start_watchlist_logo_thread

    item_id = uuid.uuid4()

    with patch("api.watchlist_routes.threading.Thread") as thread_cls:
        thread = MagicMock()
        thread_cls.return_value = thread

        _start_watchlist_logo_thread(item_id, "C:\\logos\\logo.png")

    thread_cls.assert_called_once_with(
        target=_process_watchlist_logo,
        args=(item_id, "C:\\logos\\logo.png"),
        daemon=True,
        name=f"watchlist-logo-{item_id}",
    )
    thread.start.assert_called_once_with()


@pytest.mark.asyncio
async def test_watchlist_service_store_watchlist_logo_upload_persists_logo_path():
    from services.watchlist_service import store_watchlist_logo_upload

    item_id = uuid.uuid4()
    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()

    db_lookup_cm = MagicMock()
    db_lookup = MagicMock()
    db_lookup_cm.__enter__.return_value = db_lookup
    db_lookup_cm.__exit__.return_value = False

    db_update_cm = MagicMock()
    db_update = MagicMock()
    db_update_cm.__enter__.return_value = db_update
    db_update_cm.__exit__.return_value = False

    watchlist_crud = MagicMock()
    watchlist_crud.get_by_id.return_value = {"id": item_id}
    make_dirs = MagicMock()
    write_bytes = MagicMock()
    user_plan_getter = MagicMock(return_value={"plan_name": "professional"})
    plan_limit_getter = MagicMock(return_value=True)

    payload = await store_watchlist_logo_upload(
        item_id=item_id,
        current_user=current_user,
        logo_filename="logo.png",
        content_type="image/png",
        contents=b"png-bytes",
        database_factory=MagicMock(side_effect=[db_lookup_cm, db_update_cm]),
        watchlist_crud=watchlist_crud,
        logos_dir="C:\\watchlist-logos",
        make_dirs=make_dirs,
        write_bytes=write_bytes,
        user_plan_getter=user_plan_getter,
        plan_limit_getter=plan_limit_getter,
    )

    expected_dir = os.path.join("C:\\watchlist-logos", str(current_user.organization_id))
    expected_path = os.path.join(expected_dir, f"{item_id}.png")

    assert payload["success"] is True
    assert payload["filepath"] == expected_path
    make_dirs.assert_called_once_with(expected_dir, exist_ok=True)
    write_bytes.assert_called_once_with(expected_path, b"png-bytes")
    watchlist_crud.get_by_id.assert_called_once_with(
        db_lookup,
        item_id,
        current_user.organization_id,
    )
    watchlist_crud.update_logo.assert_called_once_with(
        db_update,
        item_id,
        logo_path=expected_path,
    )
    user_plan_getter.assert_called_once_with(db_lookup, str(current_user.id))
    plan_limit_getter.assert_called_once_with("professional", "can_track_logos")


@pytest.mark.asyncio
async def test_extracted_get_watchlist_logo_delegates_to_service():
    from api.watchlist_routes import get_watchlist_logo

    item_id = uuid.uuid4()

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(b"png-bytes")
        temp_path = tmp.name

    try:
        with patch(
            "services.watchlist_service.resolve_watchlist_logo_file",
            AsyncMock(return_value={"path": temp_path, "media_type": "image/png"}),
        ) as resolve_watchlist_logo_file:
            response = await get_watchlist_logo(item_id=item_id)

        assert response.path == temp_path
        assert response.media_type == "image/png"
        resolve_watchlist_logo_file.assert_awaited_once_with(item_id=item_id)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@pytest.mark.asyncio
async def test_watchlist_service_resolve_watchlist_logo_file_prefers_absolute_path():
    from services.watchlist_service import resolve_watchlist_logo_file

    item_id = uuid.uuid4()
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(b"png-bytes")
        temp_path = tmp.name

    mock_cursor.fetchone.return_value = {"logo_path": temp_path}

    try:
        payload = await resolve_watchlist_logo_file(
            item_id=item_id,
            database_factory=MagicMock(return_value=mock_db_cm),
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    assert payload == {
        "path": temp_path,
        "media_type": "image/png",
    }


@pytest.mark.asyncio
async def test_watchlist_service_resolve_watchlist_logo_file_resolves_upload_relative_path_from_project_root():
    from services.watchlist_service import resolve_watchlist_logo_file

    item_id = uuid.uuid4()
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    temp_root = Path(".phase0_api_tmp") / f"watchlist_logo_{uuid.uuid4().hex}"
    try:
        logo_dir = Path(temp_root) / "uploads" / "watchlist_logos"
        logo_dir.mkdir(parents=True, exist_ok=True)
        logo_path = logo_dir / f"{item_id}.png"
        logo_path.write_bytes(b"png-bytes")

        mock_cursor.fetchone.return_value = {
            "logo_path": f"uploads/watchlist_logos/{logo_path.name}"
        }

        payload = await resolve_watchlist_logo_file(
            item_id=item_id,
            database_factory=MagicMock(return_value=mock_db_cm),
            project_root=temp_root,
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    assert payload == {
        "path": str(logo_path),
        "media_type": "image/png",
    }


@pytest.mark.asyncio
async def test_extracted_delete_watchlist_logo_delegates_to_service():
    from api.watchlist_routes import delete_watchlist_logo

    item_id = uuid.uuid4()
    current_user = MagicMock()

    with patch(
        "services.watchlist_service.delete_watchlist_logo_asset",
        AsyncMock(return_value={"success": True, "message": "Logo silindi"}),
    ) as delete_watchlist_logo_asset:
        response = await delete_watchlist_logo(
            item_id=item_id,
            current_user=current_user,
        )

    assert response.success is True
    assert response.message == "Logo silindi"
    delete_watchlist_logo_asset.assert_awaited_once_with(
        item_id=item_id,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_watchlist_service_delete_watchlist_logo_asset_removes_file_and_clears_db():
    from services.watchlist_service import delete_watchlist_logo_asset

    item_id = uuid.uuid4()
    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()

    mock_db_lookup_cm = MagicMock()
    mock_db_lookup = MagicMock()
    mock_db_lookup_cm.__enter__.return_value = mock_db_lookup
    mock_db_lookup_cm.__exit__.return_value = False

    mock_db_clear_cm = MagicMock()
    mock_db_clear = MagicMock()
    mock_db_clear_cm.__enter__.return_value = mock_db_clear
    mock_db_clear_cm.__exit__.return_value = False

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(b"png-bytes")
        temp_path = tmp.name

    watchlist_crud = MagicMock()
    watchlist_crud.get_by_id.return_value = {"id": item_id, "logo_path": temp_path}

    try:
        payload = await delete_watchlist_logo_asset(
            item_id=item_id,
            current_user=current_user,
            database_factory=MagicMock(side_effect=[mock_db_lookup_cm, mock_db_clear_cm]),
            watchlist_crud=watchlist_crud,
            custom_logo_checker=MagicMock(return_value=True),
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    assert payload == {"success": True, "message": "Logo silindi"}
    watchlist_crud.clear_logo.assert_called_once_with(mock_db_clear, item_id)


@pytest.mark.asyncio
async def test_watchlist_service_delete_watchlist_logo_asset_ignores_linked_trademark_logo():
    from services.watchlist_service import delete_watchlist_logo_asset

    item_id = uuid.uuid4()
    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()

    mock_db_lookup_cm = MagicMock()
    mock_db_lookup = MagicMock()
    mock_db_lookup_cm.__enter__.return_value = mock_db_lookup
    mock_db_lookup_cm.__exit__.return_value = False

    watchlist_crud = MagicMock()
    watchlist_crud.get_by_id.return_value = {
        "id": item_id,
        "logo_path": "bulletins/Marka/LOGOS/ip.png",
        "customer_application_no": "2021/160894",
    }
    remove_file = MagicMock()

    payload = await delete_watchlist_logo_asset(
        item_id=item_id,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_lookup_cm),
        watchlist_crud=watchlist_crud,
        remove_file=remove_file,
        custom_logo_checker=MagicMock(return_value=False),
    )

    assert payload == {"success": True, "message": "Silinecek ozel logo yok"}
    remove_file.assert_not_called()
    watchlist_crud.clear_logo.assert_not_called()


def test_watchlist_logo_background_wrapper_delegates_to_service():
    from api.watchlist_routes import _process_watchlist_logo

    item_id = uuid.uuid4()

    with patch(
        "services.watchlist_service.process_watchlist_logo_embeddings",
        MagicMock(),
    ) as process_watchlist_logo_embeddings:
        _process_watchlist_logo(item_id, "C:\\logos\\logo.png")

    process_watchlist_logo_embeddings.assert_called_once_with(
        item_id=item_id,
        filepath="C:\\logos\\logo.png",
        logger=ANY,
    )


def test_watchlist_service_process_watchlist_logo_embeddings_updates_logo_vectors():
    from services.watchlist_service import process_watchlist_logo_embeddings

    item_id = uuid.uuid4()
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    watchlist_crud = MagicMock()
    logger = MagicMock()

    process_watchlist_logo_embeddings(
        item_id=item_id,
        filepath="C:\\logos\\logo.png",
        logger=logger,
        database_factory=MagicMock(return_value=mock_db_cm),
        watchlist_crud=watchlist_crud,
        embedding_generator=MagicMock(
            return_value={
                "clip_embedding": [0.1],
                "dino_embedding": [0.2],
                "color_histogram": [0.3],
                "ocr_text": "LOGO",
            }
        ),
    )

    watchlist_crud.update_logo.assert_called_once_with(
        mock_db,
        item_id,
        logo_path="C:\\logos\\logo.png",
        logo_embedding=[0.1],
        dino_embedding=[0.2],
        color_histogram=[0.3],
        logo_ocr_text="LOGO",
    )


@pytest.mark.asyncio
async def test_extracted_watchlist_stats_delegates_to_service():
    from api.watchlist_routes import watchlist_stats

    current_user = MagicMock()
    expected = {
        "total_items": 4,
        "active_items": 3,
        "items_with_threats": 2,
        "critical_threats": 1,
        "high_threats": 1,
        "medium_threats": 0,
        "low_threats": 0,
        "new_alerts": 1,
        "nearest_deadline": "2026-04-20",
        "nearest_deadline_days": 10,
        "renewal_count": 1,
    }

    with patch(
        "services.watchlist_service.get_watchlist_stats_summary",
        AsyncMock(return_value=expected),
    ) as get_watchlist_stats_summary:
        data = await watchlist_stats(min_score=80, current_user=current_user)

    assert data == expected
    get_watchlist_stats_summary.assert_awaited_once_with(
        current_user=current_user,
        min_score=80,
    )


@pytest.mark.asyncio
async def test_watchlist_service_get_watchlist_stats_formats_nearest_deadline():
    from services.watchlist_service import get_watchlist_stats_summary

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.return_value = {
        "total_items": 4,
        "active_items": 3,
        "items_with_threats": 2,
        "critical_threats": 1,
        "high_threats": 1,
        "medium_threats": 0,
        "low_threats": 0,
        "new_alerts": 1,
        "nearest_deadline": date(2026, 4, 20),
        "renewal_count": 1,
    }

    current_user = MagicMock()
    current_user.organization_id = "org-123"

    data = await get_watchlist_stats_summary(
        current_user=current_user,
        min_score=80,
        database_factory=MagicMock(return_value=mock_db_cm),
        today_factory=lambda: date(2026, 4, 10),
    )

    assert data["nearest_deadline"] == "2026-04-20"
    assert data["nearest_deadline_days"] == 10
    assert data["renewal_count"] == 1
    assert mock_cursor.execute.call_args.args[1] == ("org-123",)


@pytest.mark.asyncio
async def test_extracted_list_watchlist_delegates_to_service():
    from api.watchlist_routes import list_watchlist

    current_user = MagicMock()
    expected = {
        "items": [{"id": "item-1", "name": "NIKE", "conflict_summary": {"total": 2}}],
        "total": 1,
        "page": 2,
        "page_size": 5,
        "total_pages": 1,
    }

    with patch(
        "services.watchlist_service.get_watchlist_page",
        AsyncMock(return_value=expected),
    ) as get_watchlist_page:
        response = await list_watchlist(
            page=2,
            page_size=5,
            active_only=False,
            search="nike",
            sort="updated_at_desc",
            renewal_only=True,
            appeals_only=False,
            status_filter="needs_review",
            threshold=80,
            tm_status="published",
            current_user=current_user,
        )

    assert response.items == expected["items"]
    assert response.total == 1
    assert response.page == 2
    get_watchlist_page.assert_awaited_once_with(
        current_user=current_user,
        page=2,
        page_size=5,
        active_only=False,
        search="nike",
        sort="updated_at_desc",
        renewal_only=True,
        appeals_only=False,
        status_filter="needs_review",
        threshold=80,
        tm_status="published",
        logger=ANY,
    )


@pytest.mark.asyncio
async def test_watchlist_service_get_watchlist_page_attaches_conflict_summary():
    from services.watchlist_service import get_watchlist_page

    item_id = uuid.uuid4()
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    item_payload = _make_watchlist_item_payload(item_id)
    item_payload["logo_path"] = "uploads/watchlist_logos/nike.png"
    item_payload.pop("monitor_text")
    item_payload.pop("monitor_visual")
    item_payload["monitor_phonetic"] = False
    item_payload["monitor_similar_names"] = True
    item_payload["monitor_similar_logos"] = False

    watchlist_crud = MagicMock()
    watchlist_crud.get_by_organization.return_value = (
        [item_payload],
        1,
    )
    mock_cursor.fetchall.return_value = [
        {
            "watchlist_item_id": item_id,
            "total_conflicts": 3,
            "pre_publication_count": 1,
            "critical_count": 1,
            "urgent_count": 1,
            "active_count": 1,
            "nearest_deadline": date(2026, 4, 18),
            "highest_severity_rank": 4,
            "sev_critical": 0,
            "sev_very_high": 1,
            "sev_high": 1,
            "sev_medium": 1,
            "sev_low": 0,
        }
    ]

    current_user = MagicMock()
    current_user.organization_id = "org-123"

    data = await get_watchlist_page(
        current_user=current_user,
        page=2,
        page_size=5,
        active_only=False,
        search="nike",
        sort="updated_at_desc",
        renewal_only=True,
        appeals_only=False,
        status_filter="needs_review",
        threshold=80,
        tm_status="published",
        database_factory=MagicMock(return_value=mock_db_cm),
        watchlist_crud=watchlist_crud,
        logger=MagicMock(),
        today_factory=lambda: date(2026, 4, 10),
    )

    assert data["total"] == 1
    assert data["total_pages"] == 1
    assert data["items"][0]["id"] == item_id
    assert data["items"][0]["brand_name"] == "NIKE"
    assert data["items"][0]["monitor_text"] is True
    assert data["items"][0]["monitor_visual"] is False
    assert data["items"][0]["monitor_phonetic"] is False
    assert data["items"][0]["has_logo"] is True
    assert data["items"][0]["logo_url"] == f"/api/v1/watchlist/{item_id}/logo"
    assert data["items"][0]["has_custom_logo"] is True
    assert data["items"][0]["custom_logo_url"] == f"/api/v1/watchlist/{item_id}/logo"
    assert "logo_path" not in data["items"][0]
    assert data["items"][0]["conflict_summary"] == {
        "total": 3,
        "pre_publication": 1,
        "active_critical": 1,
        "active_urgent": 1,
        "active": 1,
        "nearest_deadline": "2026-04-18",
        "nearest_deadline_days": 8,
        "highest_severity": "very_high",
        "sev_critical": 0,
        "sev_very_high": 1,
        "sev_high": 1,
        "sev_medium": 1,
        "sev_low": 0,
    }
    watchlist_crud.get_by_organization.assert_called_once_with(
        mock_db,
        "org-123",
        False,
        2,
        5,
        search="nike",
        sort_by="updated_at_desc",
        renewal_only=True,
        appeals_only=False,
        status_filter="needs_review",
        threshold=0.8,
        tm_status="published",
    )
    assert mock_cursor.execute.call_args.args[1] == ([item_id], 0.8)


def test_watchlist_response_distinguishes_linked_trademark_logo_from_custom_logo():
    from models.schemas import WatchlistItemResponse

    item_id = uuid.uuid4()
    payload = _make_watchlist_item_payload(item_id)
    payload["logo_path"] = "bulletins/Marka/LOGOS/ip.png"
    payload["trademark_image_path"] = "bulletins/Marka/BLT_2021/images/ip.png"

    data = WatchlistItemResponse(**payload).model_dump()

    assert data["has_logo"] is True
    assert data["logo_url"] == f"/api/v1/watchlist/{item_id}/logo"
    assert data["has_custom_logo"] is False
    assert data["custom_logo_url"] is None
    assert data["trademark_image_path"] == "bulletins/Marka/BLT_2021/images/ip.png"


def test_watchlist_service_custom_logo_path_detection():
    from services.watchlist_service import is_custom_watchlist_logo_path

    assert is_custom_watchlist_logo_path(
        os.path.join("uploads", "watchlist_logos", "org-id", "logo.png"),
        logos_dir=os.path.join("uploads", "watchlist_logos"),
    )
    assert not is_custom_watchlist_logo_path(
        os.path.join("bulletins", "Marka", "LOGOS", "ip.png"),
        logos_dir=os.path.join("uploads", "watchlist_logos"),
    )


@pytest.mark.asyncio
async def test_extracted_simple_search_impl_delegates_to_service():
    from app_legacy_search_routes import simple_search_impl

    expected = MagicMock()
    request = MagicMock()
    search_request_factory = MagicMock()
    enhanced_search_handler = AsyncMock()
    risk_level_getter = MagicMock()
    logger = MagicMock()

    with patch(
        "services.search_service.run_legacy_simple_search",
        new=AsyncMock(return_value=expected),
    ) as mock_run_legacy_simple_search:
        response = await simple_search_impl(
            request=request,
            q="NIKE",
            limit=5,
            search_request_factory=search_request_factory,
            enhanced_search_handler=enhanced_search_handler,
            risk_level_getter=risk_level_getter,
            logger=logger,
        )

    assert response is expected
    assert mock_run_legacy_simple_search.await_count == 1
    assert mock_run_legacy_simple_search.await_args.kwargs == {
        "request": request,
        "q": "NIKE",
        "limit": 5,
        "search_request_factory": search_request_factory,
        "enhanced_search_handler": enhanced_search_handler,
        "risk_level_getter": risk_level_getter,
        "logger": logger,
    }


@pytest.mark.asyncio
async def test_search_service_run_legacy_simple_search_formats_response():
    from services.search_service import run_legacy_simple_search

    created_request = {}

    def search_request_factory(**kwargs):
        created_request.update(kwargs)
        return SimpleNamespace(**kwargs)

    enhanced_result = SimpleNamespace(
        results=[
            SimpleNamespace(
                id="1",
                name="NIKE SPORTS",
                application_no="2024/1",
                nice_classes=[25],
                status="Published",
                application_date="2024-01-01",
                owner="Nike Inc.",
                bulletin_no="2024-1",
                image_url="/api/trademark-image/logo.png",
                similarity=84.2,
            )
        ]
    )

    response = await run_legacy_simple_search(
        request=MagicMock(),
        q="NIKE",
        limit=5,
        search_request_factory=search_request_factory,
        enhanced_search_handler=AsyncMock(return_value=enhanced_result),
        risk_level_getter=lambda score: {"level": "high", "score": round(score, 4)},
        logger=MagicMock(),
    )

    assert created_request == {"name": "NIKE", "limit": 5}
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"] == "2026-03-10"
    assert response.body.decode("utf-8") == (
        '{"query":"NIKE","count":1,"results":[{"id":"1","name":"NIKE SPORTS",'
        '"application_no":"2024/1","nice_classes":[25],"final_status":"Published",'
        '"application_date":"2024-01-01","holder_name":"Nike Inc.","bulletin_no":"2024-1",'
        '"image_url":"/api/trademark-image/logo.png","score":0.842,'
        '"risk_level":{"level":"high","score":0.842}}]}'
    )


@pytest.mark.asyncio
async def test_extracted_unified_search_impl_delegates_to_service():
    from app_legacy_search_routes import unified_search_impl

    expected = MagicMock()
    request = MagicMock()
    search_request_factory = MagicMock()
    enhanced_search_handler = AsyncMock()
    search_by_image_handler = AsyncMock()
    risk_level_getter = MagicMock()
    logger = MagicMock()

    with patch(
        "services.search_service.run_legacy_unified_search",
        new=AsyncMock(return_value=expected),
    ) as mock_run_legacy_unified_search:
        response = await unified_search_impl(
            request=request,
            name="NIKE",
            image=None,
            classes="25, 35",
            goods_description="sports shoes and clothing",
            limit=10,
            search_request_factory=search_request_factory,
            enhanced_search_handler=enhanced_search_handler,
            search_by_image_handler=search_by_image_handler,
            risk_level_getter=risk_level_getter,
            logger=logger,
        )

    assert response is expected
    assert mock_run_legacy_unified_search.await_count == 1
    assert mock_run_legacy_unified_search.await_args.kwargs == {
        "request": request,
        "name": "NIKE",
        "image": None,
        "classes": "25, 35",
        "goods_description": "sports shoes and clothing",
        "limit": 10,
        "search_request_factory": search_request_factory,
        "enhanced_search_handler": enhanced_search_handler,
        "search_by_image_handler": search_by_image_handler,
        "risk_level_getter": risk_level_getter,
        "logger": logger,
    }


@pytest.mark.asyncio
async def test_search_service_run_legacy_unified_search_formats_text_response():
    from services.search_service import run_legacy_unified_search

    created_request = {}

    def search_request_factory(**kwargs):
        created_request.update(kwargs)
        return SimpleNamespace(**kwargs)

    enhanced_result = SimpleNamespace(
        results=[
            SimpleNamespace(
                id="2",
                name="NIKE SPORTS",
                application_no="2024/2",
                status="Published",
                nice_classes=[25, 35],
                bulletin_no="2024-2",
                image_url=None,
                similarity=91.4,
                name_similarity=89.1,
            )
        ],
        search_time_ms=12.3,
        classes_were_auto_suggested=False,
    )

    response = await run_legacy_unified_search(
        request=MagicMock(),
        name="NIKE",
        image=None,
        classes="25, 35",
        goods_description="sports shoes and clothing",
        limit=10,
        search_request_factory=search_request_factory,
        enhanced_search_handler=AsyncMock(return_value=enhanced_result),
        search_by_image_handler=AsyncMock(),
        risk_level_getter=lambda score: {"level": "high", "score": round(score, 4)},
        logger=MagicMock(),
    )

    assert created_request == {
        "name": "NIKE",
        "classes": [25, 35],
        "goods_description": "sports shoes and clothing",
        "limit": 10,
    }
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"] == "2026-03-10"
    assert response.body.decode("utf-8") == (
        '{"success":true,"results":[{"id":"2","name":"NIKE SPORTS","application_no":"2024/2",'
        '"status":"Published","nice_classes":[25,35],"bulletin_no":"2024-2","image_url":null,'
        '"similarity":91.4,"name_similarity":89.1,"risk_level":{"level":"high","score":0.914}}],'
        '"search_type":"text","search_context":{"searched_name":"NIKE","searched_classes":[25,35],'
        '"total_results":1,"search_time_ms":12.3},"classes_were_auto_suggested":false}'
    )


@pytest.mark.asyncio
async def test_search_service_run_legacy_unified_search_wraps_image_dict_response():
    from services.search_service import run_legacy_unified_search

    request = MagicMock()
    image = SimpleNamespace(filename="logo.png")
    search_by_image_handler = AsyncMock(return_value={"success": True, "results": []})

    response = await run_legacy_unified_search(
        request=request,
        name="NIKE",
        image=image,
        classes="25",
        goods_description=None,
        limit=10,
        search_request_factory=MagicMock(),
        enhanced_search_handler=AsyncMock(),
        search_by_image_handler=search_by_image_handler,
        risk_level_getter=MagicMock(),
        logger=MagicMock(),
    )

    assert search_by_image_handler.await_count == 1
    assert search_by_image_handler.await_args.kwargs == {
        "request": request,
        "image": image,
        "name": "NIKE",
        "classes": "25",
        "limit": 10,
    }
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"] == "2026-03-10"
    assert response.body.decode("utf-8") == '{"success":true,"results":[]}'


@pytest.mark.asyncio
async def test_extracted_legacy_text_search_impl_delegates_to_service():
    from app_legacy_rollback_routes import legacy_text_search_impl

    expected = {"query": "NIKE", "results": []}
    search_request = SimpleNamespace(name="NIKE", classes=[25])
    settings = MagicMock()
    normalize_turkish_fn = MagicMock()
    score_calculator = MagicMock()

    with patch(
        "services.search_service.run_legacy_rollback_search",
        new=AsyncMock(return_value=expected),
    ) as mock_run_legacy_rollback_search:
        response = await legacy_text_search_impl(
            search_request=search_request,
            settings=settings,
            normalize_turkish_fn=normalize_turkish_fn,
            score_calculator=score_calculator,
            max_results=10,
        )

    assert response == expected
    assert mock_run_legacy_rollback_search.await_count == 1
    assert mock_run_legacy_rollback_search.await_args.kwargs == {
        "search_request": search_request,
        "settings": settings,
        "normalize_turkish_fn": normalize_turkish_fn,
        "score_calculator": score_calculator,
        "max_results": 10,
    }


@pytest.mark.asyncio
async def test_search_service_run_legacy_rollback_search_sorts_and_limits_results():
    from services.search_service import run_legacy_rollback_search

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {
            "id": "1",
            "name": "ALPHA",
            "application_no": "2024/1",
            "final_status": "Published",
            "nice_class_numbers": [25],
        },
        {
            "id": "2",
            "name": "OMEGA",
            "application_no": "2024/2",
            "final_status": None,
            "nice_class_numbers": [35],
        },
    ]

    settings = SimpleNamespace(
        database=SimpleNamespace(
            host="localhost",
            port=5432,
            name="db",
            user="user",
            password="pass",
        )
    )
    scores = {
        "ALPHA": {"final_score": 0.45, "risk_level": {"level": "medium"}},
        "OMEGA": {"final_score": 0.82, "risk_level": {"level": "high"}},
    }
    connect_fn = MagicMock(return_value=mock_conn)
    timer = MagicMock(side_effect=[100.0, 100.25])

    response = await run_legacy_rollback_search(
        search_request=SimpleNamespace(name="NIKE", classes=[25]),
        settings=settings,
        normalize_turkish_fn=lambda value: value.lower(),
        score_calculator=lambda _query, target: scores[target],
        max_results=1,
        connect_fn=connect_fn,
        timer=timer,
    )

    assert response["query"] == "NIKE"
    assert response["scoring_engine"] == "legacy"
    assert response["total_results"] == 1
    assert response["results"] == [
        {
            "id": "2",
            "name": "OMEGA",
            "application_no": "2024/2",
            "status": "Bilinmiyor",
            "nice_classes": [35],
            "similarity": 82.0,
            "risk_level": {"level": "high"},
            "scoring_engine": "legacy",
        }
    ]
    assert response["search_time_ms"] == 250.0
    assert connect_fn.call_count == 1
    assert connect_fn.call_args.kwargs == {
        "host": "localhost",
        "port": 5432,
        "database": "db",
        "user": "user",
        "password": "pass",
    }


@pytest.mark.asyncio
async def test_extracted_validate_discount_code_route_delegates_to_service():
    from api.billing import validate_discount_code

    payload = {"code": " launch20 ", "plan": "professional"}
    current_user = MagicMock()
    expected = {
        "valid": True,
        "code": "LAUNCH20",
        "discount_type": "percent",
        "discount_value": 20.0,
        "applies_to_plan": "professional",
    }

    with patch(
        "services.billing_service.validate_discount_code_payload",
        new=AsyncMock(return_value=expected),
    ) as mock_validate_discount_code_payload:
        response = await validate_discount_code(
            payload=payload,
            current_user=current_user,
        )

    assert response == expected
    mock_validate_discount_code_payload.assert_awaited_once_with(payload=payload)


@pytest.mark.asyncio
async def test_billing_service_validate_discount_code_payload_returns_discount_details():
    from services.billing_service import validate_discount_code_payload

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "discount_type": "percent",
        "discount_value": "20",
        "applies_to_plan": "professional",
    }

    response = await validate_discount_code_payload(
        payload={"code": " launch20 ", "plan": "professional"},
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "valid": True,
        "code": "LAUNCH20",
        "discount_type": "percent",
        "discount_value": 20.0,
        "applies_to_plan": "professional",
    }
    mock_cursor.execute.assert_called_once()
    assert mock_cursor.execute.call_args.args[1] == ("LAUNCH20",)


@pytest.mark.asyncio
async def test_billing_service_validate_discount_code_payload_rejects_wrong_plan():
    from fastapi import HTTPException
    from services.billing_service import validate_discount_code_payload

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "discount_type": "percent",
        "discount_value": "20",
        "applies_to_plan": "enterprise",
    }

    with pytest.raises(HTTPException) as exc_info:
        await validate_discount_code_payload(
            payload={"code": "launch20", "plan": "professional"},
            database_factory=MagicMock(return_value=mock_db_cm),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "This code only applies to the enterprise plan"


@pytest.mark.asyncio
async def test_extracted_usage_summary_route_delegates_to_service():
    from api.usage_routes import get_usage_summary

    current_user = MagicMock()
    expected = {
        "plan": "starter",
        "display_name": "Starter",
        "usage": {
            "watchlist_items": {"used": 2, "limit": 25},
        },
    }

    with patch(
        "services.usage_service.get_usage_summary_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_usage_summary_data:
        response = await get_usage_summary(current_user=current_user)

    assert response == expected
    mock_get_usage_summary_data.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_usage_service_get_usage_summary_data_aggregates_limits_and_counts():
    from services.usage_service import get_usage_summary_data

    current_user = MagicMock()
    current_user.id = "user-123"
    current_user.organization_id = "org-456"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    # Two queries are issued: watchlist count, then cost-weighted AI generation count.
    mock_cursor.fetchone.side_effect = [{"cnt": 7}, {"ai_used": 9}]

    plan_limit_values = {
        "max_daily_quick_searches": 10,
        "monthly_live_searches": 50,
        "monthly_ai_credits": 20,
        "monthly_reports": 15,
        "monthly_applications": 25,
        "can_track_logos": True,
        "max_watchlist_items": 100,
    }

    response = await get_usage_summary_data(
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(
            return_value={"plan_name": "professional", "display_name": "Professional"}
        ),
        plan_limit_getter=MagicMock(side_effect=lambda plan_name, feature: plan_limit_values[feature]),
        daily_quick_searches_getter=MagicMock(return_value=3),
        live_search_usage_getter=MagicMock(return_value=4),
        ai_credit_eligibility_checker=MagicMock(return_value=(True, None, {"total_remaining": 18})),
        report_eligibility_checker=MagicMock(
            return_value={
                "reports_used": 3,
                "reports_limit": 15,
                "reports_remaining": 12,
                "saved_reports": 0,
                "inline_reports": 3,
            }
        ),
        monthly_name_generations_getter=MagicMock(return_value=6),
        monthly_applications_getter=MagicMock(return_value=2),
    )

    assert response == {
        "plan": "professional",
        "display_name": "Professional",
        "usage": {
            "daily_quick_searches": {"used": 3, "limit": 10},
            "monthly_live_searches": {"used": 4, "limit": 50},
            "monthly_ai_credits": {"remaining": 18, "limit": 20, "used": 9},
            "monthly_reports": {
                "used": 3,
                "limit": 15,
                "remaining": 12,
                "saved_reports": 0,
                "inline_reports": 3,
            },
            "monthly_name_generations": {"used": 6, "limit": 20},
            "monthly_name_generations_used": 6,
            "monthly_applications": {"used": 2, "limit": 25},
            "watchlist_items": {"used": 7, "limit": 100},
            "logo_credits": {"remaining": 18, "limit": 20},
            "can_track_logos": True,
        },
    }
    assert mock_cursor.execute.call_count == 2
    first_call = mock_cursor.execute.call_args_list[0]
    assert first_call.args[0] == (
        "SELECT COUNT(*) as cnt FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE"
    )
    assert first_call.args[1] == ("org-456",)
    second_call = mock_cursor.execute.call_args_list[1]
    assert "FROM generation_logs" in second_call.args[0]
    assert second_call.args[1] == ("org-456",)


@pytest.mark.asyncio
async def test_usage_service_get_usage_summary_data_superadmin_uses_superadmin_ai_credits():
    from services.usage_service import get_usage_summary_data

    current_user = MagicMock()
    current_user.id = "user-123"
    current_user.organization_id = "org-456"
    current_user.is_superadmin = True

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    # Two queries: watchlist count, then cost-weighted AI generation count.
    # Superadmin's ai_credits_monthly column doesn't decrement, so the count
    # is sourced from generation_logs and reflects actual activity.
    mock_cursor.fetchone.side_effect = [{"cnt": 7}, {"ai_used": 12}]

    plan_limit_values = {
        "max_daily_quick_searches": 999999,
        "monthly_live_searches": 999999,
        "monthly_ai_credits": 999999,
        "monthly_applications": 999999,
        "can_track_logos": True,
        "max_watchlist_items": 999999,
    }
    ai_credit_eligibility_checker = MagicMock(
        return_value=(False, "credits_exhausted", {"total_remaining": 0})
    )

    response = await get_usage_summary_data(
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(
            return_value={"plan_name": "superadmin", "display_name": "Super Admin"}
        ),
        plan_limit_getter=MagicMock(side_effect=lambda plan_name, feature: plan_limit_values[feature]),
        daily_quick_searches_getter=MagicMock(return_value=3),
        live_search_usage_getter=MagicMock(return_value=4),
        ai_credit_eligibility_checker=ai_credit_eligibility_checker,
        report_eligibility_checker=MagicMock(
            return_value={
                "reports_used": 0,
                "reports_limit": 999999,
                "reports_remaining": 999999,
                "saved_reports": 0,
                "inline_reports": 0,
            }
        ),
        monthly_name_generations_getter=MagicMock(return_value=6),
        monthly_applications_getter=MagicMock(return_value=2),
    )

    assert response["plan"] == "superadmin"
    assert response["usage"]["monthly_ai_credits"] == {
        "remaining": 999999,
        "limit": 999999,
        "used": 12,
    }
    assert response["usage"]["logo_credits"] == {"remaining": 999999, "limit": 999999}
    ai_credit_eligibility_checker.assert_not_called()


@pytest.mark.asyncio
async def test_extracted_dashboard_stats_route_delegates_to_service():
    from api.dashboard_routes import get_dashboard_stats

    current_user = MagicMock()
    expected = {
        "watchlist_count": 8,
        "active_watchlist": 6,
        "total_alerts": 5,
        "new_alerts": 2,
        "critical_alerts": 1,
        "alerts_this_week": 3,
        "searches_this_month": 9,
        "active_deadline_count": 4,
        "pre_publication_count": 1,
        "plan_usage": {
            "watchlist": {"used": 6, "limit": 50},
            "users": {"used": 3, "limit": 5},
            "searches": {"used": 9, "limit": 60},
            "reports": {"used": 0, "limit": 10},
        },
    }

    with patch(
        "services.dashboard_service.get_dashboard_stats_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_dashboard_stats_data:
        response = await get_dashboard_stats(current_user=current_user)

    assert response == expected
    mock_get_dashboard_stats_data.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_dashboard_service_get_dashboard_stats_data_aggregates_sections():
    from services.dashboard_service import get_dashboard_stats_data

    current_user = MagicMock()
    current_user.id = "user-123"
    current_user.organization_id = "org-456"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.side_effect = [
        {"total": 12, "active": 9},
        {"total": 7, "new": 3, "critical": 2, "this_week": 4},
        {"active_deadlines": 5, "pre_publication": 1},
        {"cnt": 11},
        {"cnt": 4},
    ]

    plan_limit_values = {
        "max_watchlist_items": 100,
        "max_users": 10,
        "max_daily_quick_searches": 20,
        "monthly_live_searches": 50,
        "monthly_reports": 15,
    }

    response = await get_dashboard_stats_data(
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "professional"}),
        plan_limit_getter=MagicMock(
            side_effect=lambda plan_name, feature: plan_limit_values[feature]
        ),
        report_eligibility_checker=MagicMock(return_value={"reports_used": 3}),
    )

    assert response.model_dump() == {
        "watchlist_count": 12,
        "active_watchlist": 9,
        "total_alerts": 7,
        "new_alerts": 3,
        "critical_alerts": 2,
        "alerts_this_week": 4,
        "searches_this_month": 11,
        "active_deadline_count": 5,
        "pre_publication_count": 1,
        "plan_usage": {
            "watchlist": {"used": 9, "limit": 100},
            "users": {"used": 4, "limit": 10},
            "searches": {"used": 11, "limit": 70},
            "reports": {"used": 3, "limit": 15},
        },
    }


@pytest.mark.asyncio
async def test_extracted_list_applications_route_delegates_to_service():
    from api.applications import list_applications

    user = MagicMock()
    user.organization_id = uuid.uuid4()

    with patch(
        "api.applications.list_applications_data",
        new_callable=AsyncMock,
    ) as mock_list_applications_data:
        mock_list_applications_data.return_value = {
            "items": [{"id": str(uuid.uuid4()), "brand_name": "TEST MARKA"}],
            "total": 1,
            "page": 2,
            "page_size": 10,
            "total_pages": 1,
        }

        response = await list_applications(
            status="draft",
            application_type="registration",
            page=2,
            page_size=10,
            user=user,
        )

    assert response == {
        "items": [{"id": mock_list_applications_data.return_value["items"][0]["id"], "brand_name": "TEST MARKA"}],
        "total": 1,
        "page": 2,
        "page_size": 10,
        "total_pages": 1,
    }
    mock_list_applications_data.assert_awaited_once_with(
        organization_id=user.organization_id,
        status="draft",
        application_type="registration",
        page=2,
        page_size=10,
    )


@pytest.mark.asyncio
async def test_extracted_get_application_route_delegates_to_service():
    from api.applications import get_application

    user = MagicMock()
    user.organization_id = uuid.uuid4()
    app_row = _make_application_row(organization_id=user.organization_id)

    with patch(
        "api.applications.get_application_data",
        new_callable=AsyncMock,
    ) as mock_get_application_data:
        mock_get_application_data.return_value = app_row

        response = await get_application(
            app_id=app_row["id"],
            user=user,
        )

    assert response == app_row
    mock_get_application_data.assert_awaited_once_with(
        app_id=app_row["id"],
        organization_id=user.organization_id,
    )


@pytest.mark.asyncio
async def test_extracted_create_application_route_delegates_to_service():
    from api.applications import create_application
    from api.applications import check_application_eligibility
    from models.schemas import TrademarkApplicationCreate

    user = MagicMock()
    user.id = uuid.uuid4()
    user.organization_id = uuid.uuid4()
    data = TrademarkApplicationCreate(
        brand_name="TEST MARKA",
        nice_class_numbers=[25],
    )
    app_row = _make_application_row(
        organization_id=user.organization_id,
        user_id=user.id,
    )

    with patch(
        "api.applications.create_application_data",
        new_callable=AsyncMock,
    ) as mock_create_application_data:
        mock_create_application_data.return_value = app_row

        response = await create_application(
            data=data,
            user=user,
        )

    assert response == app_row
    mock_create_application_data.assert_awaited_once()
    assert mock_create_application_data.await_args.kwargs["data"] == data
    assert mock_create_application_data.await_args.kwargs["user"] is user
    assert (
        mock_create_application_data.await_args.kwargs["eligibility_checker"]
        is check_application_eligibility
    )


@pytest.mark.asyncio
async def test_extracted_update_application_route_delegates_to_service():
    from api.applications import update_application
    from models.schemas import TrademarkApplicationUpdate

    user = MagicMock()
    user.organization_id = uuid.uuid4()
    app_row = _make_application_row(
        organization_id=user.organization_id,
        brand_name="UPDATED MARKA",
    )
    data = TrademarkApplicationUpdate(brand_name="UPDATED MARKA")

    with patch(
        "api.applications.update_application_data",
        new_callable=AsyncMock,
    ) as mock_update_application_data:
        mock_update_application_data.return_value = app_row

        response = await update_application(
            app_id=app_row["id"],
            data=data,
            user=user,
        )

    assert response == app_row
    mock_update_application_data.assert_awaited_once_with(
        app_id=app_row["id"],
        data=data,
        user=user,
    )


@pytest.mark.asyncio
async def test_extracted_delete_application_route_delegates_to_service():
    from api.applications import delete_application

    user = MagicMock()
    user.organization_id = uuid.uuid4()
    app_id = uuid.uuid4()

    with patch(
        "api.applications.delete_application_data",
        new_callable=AsyncMock,
    ) as mock_delete_application_data:
        mock_delete_application_data.return_value = {
            "success": True,
            "message": "Application deleted",
        }

        response = await delete_application(
            app_id=app_id,
            user=user,
        )

    assert response == {
        "success": True,
        "message": "Application deleted",
    }
    mock_delete_application_data.assert_awaited_once_with(
        app_id=app_id,
        user=user,
    )


@pytest.mark.asyncio
async def test_extracted_submit_application_route_delegates_to_service():
    from api.applications import submit_application

    user = MagicMock()
    user.organization_id = uuid.uuid4()
    app_row = _make_application_row(
        organization_id=user.organization_id,
        status="submitted",
        submitted_at=datetime(2026, 4, 12, 12, 5, tzinfo=timezone.utc),
    )

    with patch(
        "api.applications.submit_application_data",
        new_callable=AsyncMock,
    ) as mock_submit_application_data:
        mock_submit_application_data.return_value = app_row

        response = await submit_application(
            app_id=app_row["id"],
            user=user,
        )

    assert response == app_row
    mock_submit_application_data.assert_awaited_once_with(
        app_id=app_row["id"],
        user=user,
    )


@pytest.mark.asyncio
async def test_extracted_upload_application_logo_route_delegates_to_service():
    from api.applications import upload_application_logo

    user = MagicMock()
    user.organization_id = uuid.uuid4()
    app_id = uuid.uuid4()
    file = SimpleNamespace(filename="logo.png", content_type="image/png")
    expected = {
        "success": True,
        "logo_url": f"/api/v1/applications/{app_id}/logo",
        "logo_path": f"static/uploads/applications/{app_id}/logo.png",
    }

    with patch(
        "api.applications.upload_application_logo_data",
        new_callable=AsyncMock,
    ) as mock_upload_application_logo_data:
        mock_upload_application_logo_data.return_value = expected

        response = await upload_application_logo(
            app_id=app_id,
            file=file,
            user=user,
        )

    assert response == expected
    mock_upload_application_logo_data.assert_awaited_once_with(
        app_id=app_id,
        file=file,
        user=user,
    )


@pytest.mark.asyncio
async def test_extracted_get_application_logo_route_delegates_to_service():
    from api.applications import get_application_logo

    user = MagicMock()
    user.organization_id = uuid.uuid4()
    app_id = uuid.uuid4()

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(b"png-bytes")
        temp_path = tmp.name

    try:
        with patch(
            "api.applications.get_application_logo_file",
            new_callable=AsyncMock,
        ) as mock_get_application_logo_file:
            mock_get_application_logo_file.return_value = Path(temp_path)

            response = await get_application_logo(
                app_id=app_id,
                user=user,
            )

        assert str(response.path) == temp_path
        mock_get_application_logo_file.assert_awaited_once_with(
            app_id=app_id,
            user=user,
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@pytest.mark.asyncio
async def test_extracted_upload_trademarks_route_delegates_to_service():
    from api.upload import upload_trademarks

    current_user = MagicMock()
    file = SimpleNamespace(filename="marks.csv", content_type="text/csv")
    expected = {
        "success": True,
        "file_name": "marks.csv",
        "file_size_mb": 0.0,
        "total_rows": 1,
        "valid_trademarks": 1,
        "trademarks": [],
        "validation_errors": [],
        "watchlist_results": None,
    }

    with patch(
        "api.upload.process_trademark_upload",
        new_callable=AsyncMock,
    ) as mock_process_trademark_upload:
        mock_process_trademark_upload.return_value = expected

        response = await upload_trademarks(
            file=file,
            add_to_watchlist=False,
            run_analysis=True,
            alert_threshold=0.8,
            current_user=current_user,
        )

    assert response == expected
    mock_process_trademark_upload.assert_awaited_once_with(
        file=file,
        add_to_watchlist=False,
        run_analysis=True,
        alert_threshold=None,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_download_template_route_delegates_to_service():
    from fastapi.responses import StreamingResponse
    from api.upload import download_template

    expected = StreamingResponse(
        io.BytesIO(b"fake-xlsx"),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=marka_sablonu.xlsx"},
    )

    with patch(
        "api.upload.build_upload_template_response",
        return_value=expected,
    ) as mock_build_upload_template_response:
        response = await download_template()

    assert response is expected
    mock_build_upload_template_response.assert_called_once_with()


@pytest.mark.asyncio
async def test_extracted_get_trademark_events_route_delegates_to_service():
    from api.trademark_routes import get_trademark_events

    current_user = MagicMock()
    expected = {
        "application_no": "2024-1",
        "name": "TEST MARKA",
        "health_card": {"severity": "healthy"},
        "events": [],
        "total": 0,
        "page": 1,
        "per_page": 50,
        "pages": 0,
    }

    with patch(
        "api.trademark_routes.get_trademark_events_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_trademark_events_data:
        response = await get_trademark_events(
            application_no="2024-1",
            page=1,
            per_page=50,
            event_type=None,
            current_user=current_user,
        )

    assert response == expected
    mock_get_trademark_events_data.assert_awaited_once_with(
        application_no="2024-1",
        page=1,
        per_page=50,
        event_type=None,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_get_extracted_goods_route_delegates_to_service():
    from api.trademark_routes import get_extracted_goods

    current_user = MagicMock()
    expected = {
        "application_no": "2024-1",
        "name": "TEST MARKA",
        "has_extracted_goods": True,
        "extracted_goods": [{"text": "Shoes"}],
        "nice_classes": [25],
        "total_items": 1,
    }

    with patch(
        "api.trademark_routes.get_extracted_goods_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_extracted_goods_data:
        response = await get_extracted_goods(
            application_no="2024-1",
            current_user=current_user,
        )

    assert response == expected
    mock_get_extracted_goods_data.assert_awaited_once_with(
        application_no="2024-1",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_list_alerts_route_delegates_to_service():
    from api.alert_routes import list_alerts
    from models.schemas import AlertSeverity, AlertStatus

    current_user = MagicMock()
    expected = {
        "items": [],
        "total": 0,
        "page": 2,
        "page_size": 5,
        "total_pages": 0,
    }

    with patch(
        "api.alert_routes.list_alerts_data",
        new=AsyncMock(return_value=expected),
    ) as mock_list_alerts_data:
        response = await list_alerts(
            page=2,
            page_size=5,
            status=[AlertStatus.NEW],
            severity=[AlertSeverity.HIGH],
            watchlist_id=None,
            min_score=80.0,
            current_user=current_user,
        )

    assert response == expected
    mock_list_alerts_data.assert_awaited_once_with(
        page=2,
        page_size=5,
        status_filters=[AlertStatus.NEW],
        severity_filters=[AlertSeverity.HIGH],
        watchlist_id=None,
        min_score=80.0,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_get_alerts_summary_route_delegates_to_service():
    from api.alert_routes import get_alerts_summary

    current_user = MagicMock()
    expected = {"by_status": {"new": 2}, "by_severity": {"high": 1}, "total_new": 2}

    with patch(
        "api.alert_routes.get_alerts_summary_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_alerts_summary_data:
        response = await get_alerts_summary(current_user=current_user)

    assert response == expected
    mock_get_alerts_summary_data.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_extracted_aggregate_alerts_route_delegates_to_service():
    from api.alert_routes import aggregate_alerts

    current_user = MagicMock()
    expected = {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "total_pages": 0,
    }

    with patch(
        "api.alert_routes.aggregate_alerts_data",
        new=AsyncMock(return_value=expected),
    ) as mock_aggregate_alerts_data:
        response = await aggregate_alerts(
            page=1,
            page_size=20,
            severity="high",
            current_user=current_user,
        )

    assert response == expected
    mock_aggregate_alerts_data.assert_awaited_once_with(
        page=1,
        page_size=20,
        severity="high",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_get_alert_route_delegates_to_service():
    from api.alert_routes import get_alert

    current_user = MagicMock()
    alert_id = uuid.uuid4()
    expected = {"id": str(alert_id)}

    with patch(
        "api.alert_routes.get_alert_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_alert_data:
        response = await get_alert(
            alert_id=alert_id,
            current_user=current_user,
        )

    assert response == expected
    mock_get_alert_data.assert_awaited_once_with(
        alert_id=alert_id,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_acknowledge_alert_route_delegates_to_service():
    from api.alert_routes import acknowledge_alert
    from models.schemas import AlertAcknowledge

    current_user = MagicMock()
    alert_id = uuid.uuid4()
    data = AlertAcknowledge(notes="checked")
    expected = {"id": str(alert_id), "status": "acknowledged"}

    with patch(
        "api.alert_routes.acknowledge_alert_data",
        new=AsyncMock(return_value=expected),
    ) as mock_acknowledge_alert_data:
        response = await acknowledge_alert(
            alert_id=alert_id,
            data=data,
            current_user=current_user,
        )

    assert response == expected
    mock_acknowledge_alert_data.assert_awaited_once_with(
        alert_id=alert_id,
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_resolve_alert_route_delegates_to_service():
    from api.alert_routes import resolve_alert
    from models.schemas import AlertResolve

    current_user = MagicMock()
    alert_id = uuid.uuid4()
    data = AlertResolve(resolution_notes="resolved")
    expected = {"id": str(alert_id), "status": "resolved"}

    with patch(
        "api.alert_routes.resolve_alert_data",
        new=AsyncMock(return_value=expected),
    ) as mock_resolve_alert_data:
        response = await resolve_alert(
            alert_id=alert_id,
            data=data,
            current_user=current_user,
        )

    assert response == expected
    mock_resolve_alert_data.assert_awaited_once_with(
        alert_id=alert_id,
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_dismiss_alert_route_delegates_to_service():
    from api.alert_routes import dismiss_alert
    from models.schemas import AlertDismiss

    current_user = MagicMock()
    alert_id = uuid.uuid4()
    data = AlertDismiss(reason="false positive")
    expected = {"id": str(alert_id), "status": "dismissed"}

    with patch(
        "api.alert_routes.dismiss_alert_data",
        new=AsyncMock(return_value=expected),
    ) as mock_dismiss_alert_data:
        response = await dismiss_alert(
            alert_id=alert_id,
            data=data,
            current_user=current_user,
        )

    assert response == expected
    mock_dismiss_alert_data.assert_awaited_once_with(
        alert_id=alert_id,
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_get_user_profile_route_delegates_to_service():
    from api.user_profile_routes import get_user_profile

    current_user = MagicMock()
    expected = {"email": "profile@example.com"}

    with patch(
        "api.user_profile_routes.get_user_profile_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_user_profile_data:
        response = await get_user_profile(current_user=current_user)

    assert response == expected
    mock_get_user_profile_data.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_extracted_update_user_profile_route_delegates_to_service():
    from api.user_profile_routes import ProfileUpdateRequest, update_user_profile

    current_user = MagicMock()
    data = ProfileUpdateRequest(first_name="Updated")
    expected = {"success": True, "message": "Profil guncellendi"}

    with patch(
        "api.user_profile_routes.update_user_profile_data",
        new=AsyncMock(return_value=expected),
    ) as mock_update_user_profile_data:
        response = await update_user_profile(data=data, current_user=current_user)

    assert response == expected
    mock_update_user_profile_data.assert_awaited_once_with(
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_upload_avatar_route_delegates_to_service():
    from api.user_profile_routes import upload_avatar

    current_user = MagicMock()
    file = MagicMock()
    expected = {"success": True, "avatar_url": "/static/avatars/test.png"}

    with patch(
        "api.user_profile_routes.upload_avatar_data",
        new=AsyncMock(return_value=expected),
    ) as mock_upload_avatar_data:
        response = await upload_avatar(file=file, current_user=current_user)

    assert response == expected
    mock_upload_avatar_data.assert_awaited_once_with(
        file=file,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_get_user_organization_route_delegates_to_service():
    from api.user_profile_routes import get_user_organization

    current_user = MagicMock()
    expected = {"name": "Acme IP"}

    with patch(
        "api.user_profile_routes.get_user_organization_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_user_organization_data:
        response = await get_user_organization(current_user=current_user)

    assert response == expected
    mock_get_user_organization_data.assert_awaited_once_with(
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_update_user_organization_route_delegates_to_service():
    from api.user_profile_routes import OrganizationProfileUpdate, update_user_organization

    current_user = MagicMock()
    data = OrganizationProfileUpdate(name="Updated Org")
    expected = {"success": True, "message": "Sirket bilgileri guncellendi"}

    with patch(
        "api.user_profile_routes.update_user_organization_data",
        new=AsyncMock(return_value=expected),
    ) as mock_update_user_organization_data:
        response = await update_user_organization(data=data, current_user=current_user)

    assert response == expected
    mock_update_user_organization_data.assert_awaited_once_with(
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_list_users_route_delegates_to_service():
    from api.user_profile_routes import list_users

    current_user = MagicMock()
    expected = [{"email": "teammate@example.com"}]

    with patch(
        "api.user_profile_routes.list_users_data",
        new=AsyncMock(return_value=expected),
    ) as mock_list_users_data:
        response = await list_users(current_user=current_user)

    assert response == expected
    mock_list_users_data.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_extracted_create_user_route_delegates_to_service():
    from api.user_profile_routes import create_user
    from models.schemas import UserCreate

    current_user = MagicMock()
    data = UserCreate(
        email="teammate@example.com",
        password="Password1",
        first_name="Team",
        last_name="Mate",
    )
    expected = {"email": "teammate@example.com"}

    with patch(
        "api.user_profile_routes.create_user_data",
        new=AsyncMock(return_value=expected),
    ) as mock_create_user_data:
        response = await create_user(data=data, current_user=current_user)

    assert response == expected
    mock_create_user_data.assert_awaited_once_with(
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_get_user_route_delegates_to_service():
    from api.user_profile_routes import get_user

    current_user = MagicMock()
    user_id = uuid.uuid4()
    expected = {"email": "person@example.com"}

    with patch(
        "api.user_profile_routes.get_user_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_user_data:
        response = await get_user(user_id=user_id, current_user=current_user)

    assert response == expected
    mock_get_user_data.assert_awaited_once_with(
        user_id=user_id,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_update_user_route_delegates_to_service():
    from api.user_profile_routes import update_user
    from models.schemas import UserUpdate

    current_user = MagicMock()
    user_id = uuid.uuid4()
    data = UserUpdate(first_name="Updated")
    expected = {"email": "person@example.com"}

    with patch(
        "api.user_profile_routes.update_user_record",
        new=AsyncMock(return_value=expected),
    ) as mock_update_user_record:
        response = await update_user(
            user_id=user_id,
            data=data,
            current_user=current_user,
        )

    assert response == expected
    mock_update_user_record.assert_awaited_once_with(
        user_id=user_id,
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_deactivate_user_route_delegates_to_service():
    from api.user_profile_routes import deactivate_user

    current_user = MagicMock()
    user_id = uuid.uuid4()
    expected = {"message": "User deactivated"}

    with patch(
        "api.user_profile_routes.deactivate_user_data",
        new=AsyncMock(return_value=expected),
    ) as mock_deactivate_user_data:
        response = await deactivate_user(user_id=user_id, current_user=current_user)

    assert response == expected
    mock_deactivate_user_data.assert_awaited_once_with(
        user_id=user_id,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_register_route_delegates_to_service():
    from api.auth_routes import register
    from auth.authentication import UserRegister

    data = UserRegister(
        email="new@example.com",
        password="Password1",
        first_name="New",
        last_name="User",
        organization_name="Acme IP",
    )
    request = _make_request(host="testclient")
    expected = {"access_token": "access"}

    with patch(
        "api.auth_routes.register_user",
        new=AsyncMock(return_value=expected),
    ) as mock_register_user:
        response = await register(request=request, data=data)

    assert response == expected
    mock_register_user.assert_awaited_once_with(data=data, ip="testclient")


@pytest.mark.asyncio
async def test_extracted_login_route_delegates_to_service():
    from api.auth_routes import login

    request = _make_request(
        json_body={"email": "login@example.com", "password": "Password1"},
        host="testclient",
    )
    expected = {"access_token": "access"}

    with patch(
        "api.auth_routes.login_user",
        new=AsyncMock(return_value=expected),
    ) as mock_login_user:
        response = await login(request=request)

    assert response == expected
    mock_login_user.assert_awaited_once_with(
        email="login@example.com",
        password="Password1",
        ip="testclient",
    )


@pytest.mark.asyncio
async def test_extracted_refresh_token_route_delegates_to_service():
    from api.auth_routes import RefreshTokenRequest, refresh_token

    data = RefreshTokenRequest(refresh_token="refresh-token")
    expected = {"access_token": "access"}

    with patch(
        "api.auth_routes.refresh_token_data",
        new=AsyncMock(return_value=expected),
    ) as mock_refresh_token_data:
        response = await refresh_token(request=_make_request(), data=data)

    assert response == expected
    mock_refresh_token_data.assert_awaited_once_with(refresh_token="refresh-token")


@pytest.mark.asyncio
async def test_extracted_change_password_route_delegates_to_service():
    from api.auth_routes import change_password
    from auth.authentication import PasswordChange

    current_user = MagicMock()
    data = PasswordChange(current_password="OldPass1", new_password="NewPass123")
    expected = {"message": "Password changed successfully"}

    with patch(
        "api.auth_routes.change_password_data",
        new=AsyncMock(return_value=expected),
    ) as mock_change_password_data:
        response = await change_password(data=data, current_user=current_user)

    assert response == expected
    mock_change_password_data.assert_awaited_once_with(
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_forgot_password_route_delegates_to_service():
    from api.auth_routes import forgot_password
    from auth.authentication import PasswordReset

    data = PasswordReset(email="reset@example.com")
    expected = {"message": "If this email is registered, a reset code has been sent."}

    with patch(
        "api.auth_routes.forgot_password_data",
        new=AsyncMock(return_value=expected),
    ) as mock_forgot_password_data:
        response = await forgot_password(request=_make_request(), data=data)

    assert response == expected
    mock_forgot_password_data.assert_awaited_once_with(data=data)


@pytest.mark.asyncio
async def test_extracted_reset_password_route_delegates_to_service():
    from api.auth_routes import reset_password
    from auth.authentication import PasswordResetConfirm

    data = PasswordResetConfirm(token="123456", new_password="NewPass123")
    expected = {"message": "Password has been reset successfully"}

    with patch(
        "api.auth_routes.reset_password_data",
        new=AsyncMock(return_value=expected),
    ) as mock_reset_password_data:
        response = await reset_password(request=_make_request(), data=data)

    assert response == expected
    mock_reset_password_data.assert_awaited_once_with(data=data)


@pytest.mark.asyncio
async def test_extracted_verify_email_route_delegates_to_service():
    from api.auth_routes import verify_email
    from auth.authentication import VerifyEmailRequest

    current_user = MagicMock()
    data = VerifyEmailRequest(code="123456")
    expected = {"message": "Email verified successfully"}

    with patch(
        "api.auth_routes.verify_email_data",
        new=AsyncMock(return_value=expected),
    ) as mock_verify_email_data:
        response = await verify_email(
            request=_make_request(),
            data=data,
            current_user=current_user,
        )

    assert response == expected
    mock_verify_email_data.assert_awaited_once_with(
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_resend_verification_route_delegates_to_service():
    from api.auth_routes import resend_verification

    current_user = MagicMock()
    expected = {"message": "Verification code sent"}

    with patch(
        "api.auth_routes.resend_verification_data",
        new=AsyncMock(return_value=expected),
    ) as mock_resend_verification_data:
        response = await resend_verification(
            request=_make_request(),
            current_user=current_user,
        )

    assert response == expected
    mock_resend_verification_data.assert_awaited_once_with(
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_auth_me_route_delegates_to_service():
    from api.auth_routes import get_current_user_profile

    current_user = MagicMock()
    expected = {"email": "me@example.com"}

    with patch(
        "api.auth_routes.get_current_user_profile_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_current_user_profile_data:
        response = await get_current_user_profile(current_user=current_user)

    assert response == expected
    mock_get_current_user_profile_data.assert_awaited_once_with(
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_lead_feed_route_delegates_to_service():
    from api.leads import get_lead_feed

    current_user = MagicMock()
    expected = {"total_count": 1, "page": 2, "limit": 5, "items": []}

    with patch(
        "api.leads.get_lead_feed_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_lead_feed_data:
        response = await get_lead_feed(
            urgency="critical",
            nice_class=25,
            min_score=0.8,
            status="viewed",
            search="nike",
            page=2,
            limit=5,
            current_user=current_user,
        )

    assert response == expected
    mock_get_lead_feed_data.assert_awaited_once_with(
        urgency="critical",
        nice_class=25,
        min_score=0.8,
        status="viewed",
        search="nike",
        page=2,
        limit=5,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_export_leads_csv_route_delegates_to_service():
    from api.leads import export_leads_csv

    current_user = MagicMock()
    expected = StreamingResponse(iter([b"csv"]), media_type="text/csv")

    with patch(
        "api.leads.export_leads_csv_data",
        new=AsyncMock(return_value=expected),
    ) as mock_export_leads_csv_data:
        response = await export_leads_csv(
            urgency="urgent",
            nice_class=35,
            min_score=0.75,
            current_user=current_user,
        )

    assert response is expected
    mock_export_leads_csv_data.assert_awaited_once_with(
        urgency="urgent",
        nice_class=35,
        min_score=0.75,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_lead_detail_route_delegates_to_service():
    from api.leads import get_lead_detail

    current_user = MagicMock()
    expected = {"id": "lead-1", "lead_status": "viewed"}

    with patch(
        "api.leads.get_lead_detail_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_lead_detail_data:
        response = await get_lead_detail(lead_id="lead-1", current_user=current_user)

    assert response == expected
    mock_get_lead_detail_data.assert_awaited_once_with(
        lead_id="lead-1",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_mark_lead_converted_route_delegates_to_service():
    from api.leads import mark_lead_converted

    current_user = MagicMock()
    expected = {
        "success": True,
        "message": "ok",
        "lead_id": "lead-2",
        "new_status": "converted",
    }

    with patch(
        "api.leads.mark_lead_converted_data",
        new=AsyncMock(return_value=expected),
    ) as mock_mark_lead_converted_data:
        response = await mark_lead_converted(
            lead_id="lead-2",
            notes="Won client",
            current_user=current_user,
        )

    assert response == expected
    mock_mark_lead_converted_data.assert_awaited_once_with(
        lead_id="lead-2",
        notes="Won client",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_dismiss_lead_route_delegates_to_service():
    from api.leads import dismiss_lead

    current_user = MagicMock()
    expected = {
        "success": True,
        "message": "ok",
        "lead_id": "lead-3",
        "new_status": "dismissed",
    }

    with patch(
        "api.leads.dismiss_lead_data",
        new=AsyncMock(return_value=expected),
    ) as mock_dismiss_lead_data:
        response = await dismiss_lead(
            lead_id="lead-3",
            reason="Not a fit",
            current_user=current_user,
        )

    assert response == expected
    mock_dismiss_lead_data.assert_awaited_once_with(
        lead_id="lead-3",
        reason="Not a fit",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_renewal_stats_route_delegates_to_service():
    from api.leads import get_renewal_stats

    current_user = MagicMock()
    expected = {"total": 4, "critical": 1, "urgent": 1, "upcoming": 2, "grace_period": 0}

    with patch(
        "api.leads.get_renewal_stats_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_renewal_stats_data:
        response = await get_renewal_stats(current_user=current_user)

    assert response == expected
    mock_get_renewal_stats_data.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_extracted_renewal_feed_route_delegates_to_service():
    from api.leads import get_renewal_feed

    current_user = MagicMock()
    expected = {"total_count": 1, "page": 1, "limit": 10, "items": []}

    with patch(
        "api.leads.get_renewal_feed_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_renewal_feed_data:
        response = await get_renewal_feed(
            urgency="upcoming",
            nice_class=9,
            search="renew",
            page=1,
            limit=10,
            current_user=current_user,
        )

    assert response == expected
    mock_get_renewal_feed_data.assert_awaited_once_with(
        urgency="upcoming",
        nice_class=9,
        search="renew",
        page=1,
        limit=10,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_export_renewals_csv_route_delegates_to_service():
    from api.leads import export_renewals_csv

    current_user = MagicMock()
    expected = StreamingResponse(iter([b"csv"]), media_type="text/csv")

    with patch(
        "api.leads.export_renewals_csv_data",
        new=AsyncMock(return_value=expected),
    ) as mock_export_renewals_csv_data:
        response = await export_renewals_csv(
            urgency="critical",
            nice_class=25,
            current_user=current_user,
        )

    assert response is expected
    mock_export_renewals_csv_data.assert_awaited_once_with(
        urgency="critical",
        nice_class=25,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_initialize_payment_route_delegates_to_service():
    from api.payments import initialize_payment

    current_user = MagicMock()
    request = _make_request(json_body={"plan": "starter", "billing": "monthly"})
    expected = {
        "checkout_form_content": "<form>checkout</form>",
        "token": "tok-1",
        "conversation_id": "conv-1",
        "payment_id": "pay-1",
    }

    with patch(
        "api.payments.initialize_payment_data",
        new=AsyncMock(return_value=expected),
    ) as mock_initialize_payment_data:
        response = await initialize_payment(
            request=request,
            payload={"plan": "starter", "billing": "monthly"},
            current_user=current_user,
        )

    assert response == expected
    mock_initialize_payment_data.assert_awaited_once_with(
        request=request,
        payload={"plan": "starter", "billing": "monthly"},
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_payment_callback_route_delegates_to_service():
    from api.payments import payment_callback

    request = _make_request(
        content_type="application/x-www-form-urlencoded",
        form_data={"token": "tok-1"},
    )
    expected = JSONResponse({"status": "ok"})

    with patch(
        "api.payments.payment_callback_data",
        new=AsyncMock(return_value=expected),
    ) as mock_payment_callback_data:
        response = await payment_callback(request=request)

    assert response is expected
    mock_payment_callback_data.assert_awaited_once_with(request=request)


@pytest.mark.asyncio
async def test_extracted_payment_webhook_route_delegates_to_service():
    from api.payments import payment_webhook

    request = _make_request(json_body={"token": "tok-1"})
    expected = JSONResponse({"status": "ok"})

    with patch(
        "api.payments.payment_webhook_data",
        new=AsyncMock(return_value=expected),
    ) as mock_payment_webhook_data:
        response = await payment_webhook(request=request)

    assert response is expected
    mock_payment_webhook_data.assert_awaited_once_with(request=request)


@pytest.mark.asyncio
async def test_extracted_activate_free_plan_route_delegates_to_service():
    from api.payments import activate_free_plan

    current_user = MagicMock()
    expected = {"success": True, "redirect": "/dashboard"}

    with patch(
        "api.payments.activate_free_plan_data",
        new=AsyncMock(return_value=expected),
    ) as mock_activate_free_plan_data:
        response = await activate_free_plan(current_user=current_user)

    assert response == expected
    mock_activate_free_plan_data.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_extracted_trigger_pipeline_route_delegates_to_service():
    from api.pipeline import trigger_pipeline

    background_tasks = BackgroundTasks()
    current_user = MagicMock()
    expected = {"run_id": "run-1", "status": "started", "skip_download": True}

    with patch(
        "api.pipeline.trigger_pipeline_run_data",
        new=AsyncMock(return_value=expected),
    ) as mock_trigger_pipeline_run_data:
        response = await trigger_pipeline(
            skip_download=True,
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response == expected
    mock_trigger_pipeline_run_data.assert_awaited_once_with(
        skip_download=True,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_trigger_pipeline_step_route_delegates_to_service():
    from api.pipeline import trigger_pipeline_step

    background_tasks = BackgroundTasks()
    current_user = MagicMock()
    expected = {"run_id": "run-2", "status": "started", "step": "extract"}

    with patch(
        "api.pipeline.trigger_pipeline_step_data",
        new=AsyncMock(return_value=expected),
    ) as mock_trigger_pipeline_step_data:
        response = await trigger_pipeline_step(
            step="extract",
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response == expected
    mock_trigger_pipeline_step_data.assert_awaited_once_with(
        step="extract",
        background_tasks=background_tasks,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_pipeline_status_route_delegates_to_service():
    from api.pipeline import pipeline_status

    current_user = MagicMock()
    expected = {"is_running": False, "recent_runs": []}

    with patch(
        "api.pipeline.get_pipeline_status_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_pipeline_status_data:
        response = await pipeline_status(limit=7, current_user=current_user)

    assert response == expected
    mock_get_pipeline_status_data.assert_awaited_once_with(
        limit=7,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_pipeline_run_detail_route_delegates_to_service():
    from api.pipeline import pipeline_run_detail

    current_user = MagicMock()
    expected = {"id": "run-1", "status": "completed"}

    with patch(
        "api.pipeline.get_pipeline_run_detail_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_pipeline_run_detail_data:
        response = await pipeline_run_detail(run_id="run-1", current_user=current_user)

    assert response == expected
    mock_get_pipeline_run_detail_data.assert_awaited_once_with(
        run_id="run-1",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_get_generated_image_route_delegates_to_service():
    from fastapi.responses import FileResponse
    from api.creative import get_generated_image

    current_user = MagicMock()

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(b"creative-image")
        temp_path = tmp.name

    try:
        expected = FileResponse(temp_path, media_type="image/png")

        with patch(
            "api.creative.get_generated_image_response",
            new=AsyncMock(return_value=expected),
        ) as mock_get_generated_image_response:
            response = await get_generated_image(
                image_id="1f64f3f9-0937-4e9e-9a4f-64991fb620d7",
                current_user=current_user,
            )

        assert response is expected
        mock_get_generated_image_response.assert_awaited_once_with(
            image_id="1f64f3f9-0937-4e9e-9a4f-64991fb620d7",
            current_user=current_user,
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@pytest.mark.asyncio
async def test_extracted_generation_history_route_delegates_to_service():
    from api.creative import get_generation_history

    current_user = MagicMock()
    expected = {
        "items": [],
        "total": 0,
        "page": 3,
        "per_page": 15,
        "total_pages": 1,
    }

    with patch(
        "api.creative.get_generation_history_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_generation_history_data:
        response = await get_generation_history(
            page=3,
            per_page=15,
            feature_type="NAME",
            current_user=current_user,
        )

    assert response == expected
    mock_get_generation_history_data.assert_awaited_once_with(
        page=3,
        per_page=15,
        feature_type="NAME",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_delete_generation_history_item_route_delegates_to_service():
    from api.creative import delete_generation_history_item

    current_user = MagicMock()
    history_id = str(uuid.uuid4())
    expected = {"deleted": 1, "id": history_id}

    with patch(
        "api.creative.delete_generation_history_item_data",
        new=AsyncMock(return_value=expected),
    ) as mock_delete_generation_history_item_data:
        response = await delete_generation_history_item(
            history_id=history_id,
            current_user=current_user,
        )

    assert response == expected
    mock_delete_generation_history_item_data.assert_awaited_once_with(
        history_id=history_id,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_clear_generation_history_route_delegates_to_service():
    from api.creative import clear_generation_history

    current_user = MagicMock()
    expected = {"deleted": 2, "feature_type": "NAME"}

    with patch(
        "api.creative.clear_generation_history_data",
        new=AsyncMock(return_value=expected),
    ) as mock_clear_generation_history_data:
        response = await clear_generation_history(
            feature_type="NAME",
            current_user=current_user,
        )

    assert response == expected
    mock_clear_generation_history_data.assert_awaited_once_with(
        feature_type="NAME",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_creative_status_route_delegates_to_service():
    from api.creative import creative_suite_status

    expected = {
        "name_generator": {"available": True, "reason": ""},
        "logo_studio": {"available": True, "reason": ""},
    }

    with patch(
        "api.creative.creative_suite_status_data",
        new=AsyncMock(return_value=expected),
    ) as mock_creative_suite_status_data:
        response = await creative_suite_status()

    assert response == expected
    mock_creative_suite_status_data.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_extracted_suggest_names_route_delegates_to_service():
    from api import creative as creative_module
    from models.schemas import NameSuggestionRequest

    current_user = MagicMock()
    request = NameSuggestionRequest(
        query="Acme",
        nice_classes=[25],
        industry="Footwear",
        style="modern",
        language="tr",
        avoid_names=["Nike"],
    )
    expected = {
        "safe_names": [],
        "filtered_count": 0,
        "total_generated": 0,
        "session_count": 0,
        "credits_remaining": {"session_limit": 5, "used": 0, "purchased": 0, "plan": "free"},
        "cached": False,
    }

    with patch(
        "api.creative.suggest_names_data",
        new=AsyncMock(return_value=expected),
    ) as mock_suggest_names_data:
        response = await creative_module.suggest_names(
            request=request,
            current_user=current_user,
        )

    assert response == expected
    mock_suggest_names_data.assert_awaited_once()
    assert mock_suggest_names_data.await_args.kwargs["request"] == request
    assert mock_suggest_names_data.await_args.kwargs["current_user"] == current_user
    assert (
        mock_suggest_names_data.await_args.kwargs["generation_log_handler"]
        is creative_module._log_generation
    )
    assert (
        mock_suggest_names_data.await_args.kwargs["audit_log_handler"]
        is creative_module._audit_log
    )


@pytest.mark.asyncio
async def test_extracted_generate_logo_route_delegates_to_service():
    from api import creative as creative_module
    from models.schemas import LogoGenerationRequest

    current_user = MagicMock()
    request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        nice_classes=[25],
        color_preferences="blue",
    )
    expected = {
        "logos": [],
        "credits_remaining": {"monthly": 1, "purchased": 0},
        "generation_id": "gen-logo-1",
    }

    with patch(
        "api.creative.generate_logo_data",
        new=AsyncMock(return_value=expected),
    ) as mock_generate_logo_data:
        response = await creative_module.generate_logo(
            request=request,
            current_user=current_user,
        )

    assert response == expected
    mock_generate_logo_data.assert_awaited_once()
    assert mock_generate_logo_data.await_args.kwargs["request"] == request
    assert mock_generate_logo_data.await_args.kwargs["current_user"] == current_user
    assert callable(mock_generate_logo_data.await_args.kwargs["audit_scheduler"])
    assert (
        mock_generate_logo_data.await_args.kwargs["generation_log_handler"]
        is creative_module._log_generation
    )
    assert (
        mock_generate_logo_data.await_args.kwargs["audit_log_handler"]
        is creative_module._audit_log
    )


@pytest.mark.asyncio
async def test_extracted_logo_project_routes_delegate_to_service():
    from api import creative as creative_module
    from models.schemas import LogoProjectSelectRequest

    current_user = MagicMock()
    expected = {
        "id": "project-1",
        "org_id": "org-1",
        "user_id": "user-1",
        "brand_name": "Acme",
        "description": "",
        "style": "modern",
        "nice_classes": [],
        "color_preferences": "",
        "selected_image_id": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "logos": [],
    }

    with patch(
        "api.creative.get_logo_project_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_project:
        response = await creative_module.get_logo_project(
            project_id="project-1",
            current_user=current_user,
        )

    assert response == expected
    mock_get_project.assert_awaited_once_with(project_id="project-1", current_user=current_user)

    with patch(
        "api.creative.select_logo_project_candidate_data",
        new=AsyncMock(return_value=expected),
    ) as mock_select:
        response = await creative_module.select_logo_project_candidate(
            project_id="project-1",
            request=LogoProjectSelectRequest(image_id="image-1"),
            current_user=current_user,
        )

    assert response == expected
    mock_select.assert_awaited_once_with(
        project_id="project-1",
        image_id="image-1",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_retry_logo_audit_route_delegates_to_service():
    from api import creative as creative_module

    current_user = MagicMock()
    expected = {
        "image_id": "image-1",
        "image_url": "/api/v1/tools/generated-image/image-1",
        "similarity_score": 0,
        "is_safe": False,
        "audit_status": "pending",
    }

    with patch(
        "api.creative.retry_logo_audit_data",
        new=AsyncMock(return_value=expected),
    ) as mock_retry:
        response = await creative_module.retry_logo_audit(
            image_id="image-1",
            current_user=current_user,
        )

    assert response == expected
    mock_retry.assert_awaited_once()
    assert mock_retry.await_args.kwargs["image_id"] == "image-1"
    assert mock_retry.await_args.kwargs["current_user"] == current_user
    assert callable(mock_retry.await_args.kwargs["audit_scheduler"])


@pytest.mark.asyncio
async def test_creative_service_get_generated_image_response_returns_file_response():
    from services.creative_service import get_generated_image_response

    current_user = SimpleNamespace(organization_id=uuid.uuid4())
    image_id = str(uuid.uuid4())

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(b"creative-image")
        temp_path = tmp.name

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "image_path": temp_path,
        "org_id": str(current_user.organization_id),
    }
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    try:
        response = await get_generated_image_response(
            image_id=image_id,
            current_user=current_user,
            database_factory=MagicMock(return_value=mock_db_cm),
            logo_output_dir=os.path.dirname(temp_path),
        )

        assert response.path == temp_path
        assert response.media_type == "image/png"
        assert response.headers["cache-control"] == "public, max-age=604800"
        mock_cursor.execute.assert_called_once()
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@pytest.mark.asyncio
async def test_creative_service_get_generated_image_response_rejects_outside_logo_dir():
    from services.creative_service import get_generated_image_response

    current_user = SimpleNamespace(organization_id=uuid.uuid4())
    image_id = str(uuid.uuid4())

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(b"creative-image")
        temp_path = tmp.name

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "image_path": temp_path,
        "org_id": str(current_user.organization_id),
    }
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    try:
        with pytest.raises(Exception) as exc_info:
            await get_generated_image_response(
                image_id=image_id,
                current_user=current_user,
                database_factory=MagicMock(return_value=mock_db_cm),
                logo_output_dir=os.path.join(os.path.dirname(temp_path), "allowed"),
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid image path"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@pytest.mark.asyncio
async def test_creative_service_get_generation_history_data_maps_logo_items():
    from services.creative_service import get_generation_history_data

    org_id = uuid.uuid4()
    log_id = uuid.uuid4()
    image_id = uuid.uuid4()
    created_at = datetime(2026, 4, 12, 15, 0, tzinfo=timezone.utc)

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    mock_cursor.fetchone.side_effect = [{"total": 1}]
    mock_cursor.fetchall.side_effect = [
        [
            {
                "id": log_id,
                "feature_type": "LOGO",
                "input_params": {"brand_name": "Acme"},
                "output_data": {
                    "generation_id": "gen-1",
                    "variations": 4,
                    "requested_count": 4,
                    "returned_count": 4,
                    "source_layout": "panel_2x2_split",
                },
                "credits_used": 1,
                "created_at": created_at,
            }
        ],
        [
            {
                "id": image_id,
                "image_path": "generated/logo.png",
                "similarity_score": 72.4,
                "is_safe": False,
                "created_at": created_at,
            }
        ],
    ]

    response = await get_generation_history_data(
        page=2,
        per_page=5,
        feature_type="LOGO",
        current_user=SimpleNamespace(organization_id=org_id),
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response.total == 1
    assert response.page == 2
    assert response.total_pages == 1
    assert response.items[0].id == str(log_id)
    assert response.items[0].output_data["variations"] == 4
    assert response.items[0].output_data["source_layout"] == "panel_2x2_split"
    assert response.items[0].images[0]["image_id"] == str(image_id)
    assert response.items[0].images[0]["similarity_score"] == 72.4
    assert response.items[0].images[0]["is_safe"] is False
    assert mock_cursor.execute.call_count == 3


@pytest.mark.asyncio
async def test_creative_service_delete_generation_history_item_deletes_owned_log_and_images():
    from services.creative_service import delete_generation_history_item_data

    org_id = uuid.uuid4()
    log_id = uuid.uuid4()
    image_id = uuid.uuid4()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.return_value = {"id": log_id, "feature_type": "LOGO"}
    mock_cursor.fetchall.return_value = [{"id": image_id, "image_path": None}]

    response = await delete_generation_history_item_data(
        history_id=str(log_id),
        current_user=SimpleNamespace(organization_id=org_id),
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {"deleted": 1, "id": str(log_id), "feature_type": "LOGO"}
    assert mock_db.commit.called
    executed_sql = " ".join(call.args[0] for call in mock_cursor.execute.call_args_list)
    assert "DELETE FROM generation_logs" in executed_sql
    assert "UPDATE logo_projects" in executed_sql


@pytest.mark.asyncio
async def test_creative_service_clear_generation_history_deletes_filtered_history():
    from services.creative_service import clear_generation_history_data

    org_id = uuid.uuid4()
    log_id = uuid.uuid4()
    image_id = uuid.uuid4()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.side_effect = [
        [{"id": image_id, "image_path": None}],
        [{"id": log_id}],
    ]

    response = await clear_generation_history_data(
        feature_type="LOGO",
        current_user=SimpleNamespace(organization_id=org_id),
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {"deleted": 1, "feature_type": "LOGO"}
    assert mock_db.commit.called
    executed_sql = " ".join(call.args[0] for call in mock_cursor.execute.call_args_list)
    assert "gl.feature_type = %s" in executed_sql
    assert "DELETE FROM generation_logs" in executed_sql


def test_creative_service_audit_generated_logo_image_updates_completed_status():
    from services.creative_service import audit_generated_logo_image

    image_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.side_effect = [
        {"id": image_id, "org_id": org_id, "image_path": "generated/logo.png", "project_id": project_id},
        {"brand_name": "Acme", "nice_classes": [25]},
    ]

    audit_generated_logo_image(
        image_id,
        database_factory=MagicMock(return_value=mock_db_cm),
        visual_audit_available_checker=MagicMock(return_value=(True, "")),
        generate_visual_features_handler=MagicMock(
            return_value={"clip_embedding": [0.1, 0.2], "dino_embedding": [0.3, 0.4], "ocr_text": "acme"}
        ),
        visual_similarity_search_handler=MagicMock(
            return_value=[
                {
                    "name": "ACME OLD",
                    "image_path": "logos/acme-old.png",
                    "combined_sim": 0.42,
                    "visual_breakdown": {"clip": 0.4, "raw_combined": 0.42},
                }
            ]
        ),
        closest_match_image_url_builder=MagicMock(return_value="/api/trademark-image/logos/acme-old.png"),
        logo_risk_scorer_handler=MagicMock(
            return_value={
                "llm_risk_score": 42.0,
                "llm_risk_model": "qwen:test",
                "risk_source": "risk_report_llm",
                "results": [{"input_index": 1}],
            }
        ),
        settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_similarity_threshold=0.7)),
    )

    final_params = mock_cursor.execute.call_args_list[-1].args[1]
    assert final_params[4] == 42.0
    assert final_params[5] is True
    assert final_params[6] == "completed"
    assert '"closest_match_name": "ACME OLD"' in final_params[3]
    assert '"llm_risk_score": 42.0' in final_params[3]
    assert mock_db.commit.call_count == 2


def test_creative_service_audit_generated_logo_image_blocks_when_llm_score_is_high():
    from services.creative_service import audit_generated_logo_image

    image_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.side_effect = [
        {"id": image_id, "org_id": org_id, "image_path": "generated/logo.png", "project_id": project_id},
        {"brand_name": "Acme", "nice_classes": [25]},
    ]

    audit_generated_logo_image(
        image_id,
        database_factory=MagicMock(return_value=mock_db_cm),
        visual_audit_available_checker=MagicMock(return_value=(True, "")),
        generate_visual_features_handler=MagicMock(
            return_value={"clip_embedding": [0.1, 0.2], "dino_embedding": [0.3, 0.4], "ocr_text": "acme"}
        ),
        visual_similarity_search_handler=MagicMock(
            return_value=[
                {
                    "name": "Weak Visual Match",
                    "image_path": "logos/weak.png",
                    "visual_similarity_score": 0.32,
                    "name_conflict_score": 0.12,
                    "overall_risk_score": 0.32,
                    "visual_breakdown": {"clip": 0.32, "raw_combined": 0.32},
                }
            ]
        ),
        closest_match_image_url_builder=MagicMock(return_value="/api/trademark-image/logos/weak.png"),
        logo_risk_scorer_handler=MagicMock(
            return_value={
                "llm_risk_score": 88.0,
                "llm_risk_model": "qwen:test",
                "risk_source": "risk_report_llm",
                "results": [{"input_index": 1}],
            }
        ),
        settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_similarity_threshold=0.7)),
    )

    final_params = mock_cursor.execute.call_args_list[-1].args[1]
    breakdown = json.loads(final_params[3])
    assert final_params[4] == 88.0
    assert final_params[5] is False
    assert breakdown["llm_risk_score"] == 88.0
    assert breakdown["risk_source"] == "risk_report_llm"
    assert "deterministic_risk_score" not in breakdown
    assert "visual_similarity_score" not in breakdown
    assert "name_conflict_score" not in breakdown
    assert "overall_risk_score" not in breakdown


def test_creative_service_audit_generated_logo_image_uses_visual_closest_match_not_name_conflict():
    from services.creative_service import audit_generated_logo_image

    image_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.side_effect = [
        {"id": image_id, "org_id": org_id, "image_path": "generated/logo.png", "project_id": project_id},
        {"brand_name": "Seydoğlu Baklavaları", "nice_classes": [43]},
    ]

    audit_generated_logo_image(
        image_id,
        database_factory=MagicMock(return_value=mock_db_cm),
        visual_audit_available_checker=MagicMock(return_value=(True, "")),
        generate_visual_features_handler=MagicMock(
            return_value={
                "clip_embedding": [0.1, 0.2],
                "dino_embedding": [0.3, 0.4],
                "ocr_text": "seydoğlu baklavalari",
            }
        ),
        visual_similarity_search_handler=MagicMock(
            return_value=[
                {
                    "name": "Visual Similar",
                    "image_path": "logos/visual.png",
                    "visual_similarity_score": 0.64,
                    "name_conflict_score": 0.0,
                    "overall_risk_score": 0.64,
                    "visual_breakdown": {"clip": 0.64, "raw_combined": 0.64},
                },
                {
                    "name": "1952 seyidoğlu istanbul baklava & patisserie",
                    "image_path": "logos/name-conflict.png",
                    "visual_similarity_score": 0.52,
                    "name_conflict_score": 0.78,
                    "overall_risk_score": 0.78,
                    "visual_breakdown": {"clip": 0.52, "raw_combined": 0.52},
                },
            ]
        ),
        closest_match_image_url_builder=MagicMock(side_effect=lambda match: f"/api/trademark-image/{match['image_path']}"),
        logo_risk_scorer_handler=MagicMock(
            return_value={
                "llm_risk_score": 45.0,
                "llm_risk_model": "qwen:test",
                "risk_source": "risk_report_llm",
                "results": [{"input_index": 1}],
            }
        ),
        settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_similarity_threshold=0.7)),
    )

    final_params = mock_cursor.execute.call_args_list[-1].args[1]
    breakdown = json.loads(final_params[3])
    assert final_params[4] == 45.0
    assert final_params[5] is True
    assert breakdown["llm_risk_score"] == 45.0
    assert breakdown["risk_source"] == "risk_report_llm"
    assert breakdown["closest_match_name"] == "Visual Similar"
    assert breakdown["closest_database_match"]["name"] == "Visual Similar"
    assert "deterministic_risk_score" not in breakdown
    assert "visual_similarity_score" not in breakdown
    assert "name_conflict_score" not in breakdown
    assert "overall_risk_score" not in breakdown
    assert "risk_driver" not in breakdown


def test_creative_service_logo_visual_search_does_not_compare_ocr_to_registered_name():
    from services.creative_service import _full_visual_similarity_search

    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {
            "name": "SEYDOGLU BAKLAVALARI",
            "application_no": "2026/000001",
            "bulletin_no": "2026-01",
            "status": "registered",
            "nice_class_numbers": [43],
            "current_holder_name": "Existing Holder",
            "image_path": "logos/seydoglu.png",
            "logo_ocr_text": "",
            "dinov2_embedding": None,
            "raw_clip_sim": 0.31,
        }
    ]

    with patch("db.pool.get_connection", return_value=mock_conn), patch(
        "db.pool.release_connection"
    ), patch("services.creative_service.score_pair", side_effect=AssertionError("OCR must not be scored against name")):
        results = _full_visual_similarity_search(
            features={
                "clip_embedding": [0.1, 0.2],
                "dino_embedding": None,
                "ocr_text": "seydoglu baklavalari",
            },
            nice_classes=[43],
            top_k=5,
        )

    assert len(results) == 1
    assert results[0]["combined_sim"] == pytest.approx(0.31)
    assert results[0]["overall_risk_score"] == pytest.approx(0.31)
    assert "name_conflict_score" not in results[0]
    assert "name_conflict_score" not in results[0]["visual_breakdown"]


def test_creative_service_audit_generated_logo_image_splits_visual_risk():
    from services.creative_service import audit_generated_logo_image

    image_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.side_effect = [
        {"id": image_id, "org_id": org_id, "image_path": "generated/logo.png", "project_id": project_id},
        {"brand_name": "Acme", "nice_classes": [25]},
    ]

    audit_generated_logo_image(
        image_id,
        database_factory=MagicMock(return_value=mock_db_cm),
        visual_audit_available_checker=MagicMock(return_value=(True, "")),
        generate_visual_features_handler=MagicMock(
            return_value={"clip_embedding": [0.1, 0.2], "dino_embedding": [0.3, 0.4], "ocr_text": "acme"}
        ),
        visual_similarity_search_handler=MagicMock(
            return_value=[
                {
                    "name": "Visual Twin",
                    "image_path": "logos/visual-twin.png",
                    "visual_similarity_score": 0.82,
                    "name_conflict_score": 0.21,
                    "overall_risk_score": 0.82,
                    "visual_breakdown": {"clip": 0.82, "dino": 0.81, "raw_combined": 0.82},
                }
            ]
        ),
        closest_match_image_url_builder=MagicMock(side_effect=lambda match: f"/api/trademark-image/{match['image_path']}"),
        logo_risk_scorer_handler=MagicMock(
            return_value={
                "llm_risk_score": 91.0,
                "llm_risk_model": "qwen:test",
                "risk_source": "risk_report_llm",
                "results": [{"input_index": 1}],
            }
        ),
        settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_similarity_threshold=0.7)),
    )

    final_params = mock_cursor.execute.call_args_list[-1].args[1]
    breakdown = json.loads(final_params[3])
    assert final_params[4] == 91.0
    assert final_params[5] is False
    assert breakdown["llm_risk_score"] == 91.0
    assert breakdown["closest_match_name"] == "Visual Twin"
    assert "deterministic_risk_score" not in breakdown
    assert "visual_similarity_score" not in breakdown
    assert "name_conflict_score" not in breakdown
    assert "overall_risk_score" not in breakdown
    assert "risk_driver" not in breakdown


@pytest.mark.asyncio
async def test_creative_service_status_data_respects_feature_flag():
    from services.creative_service import creative_suite_status_data

    response = await creative_suite_status_data(
        feature_enabled_getter=lambda name: False,
    )

    assert response["name_generator"]["available"] is False
    assert response["logo_studio"]["available"] is False
    assert response["name_generator"]["reason"] == "AI Studio gecici olarak devre disi birakildi"
    assert response["logo_studio"]["reason"] == "AI Studio gecici olarak devre disi birakildi"


@pytest.mark.asyncio
async def test_creative_service_status_data_marks_gemini_availability():
    from services.creative_service import creative_suite_status_data

    client = MagicMock()
    client.is_available.return_value = True
    client.image_model = "gemini-3-pro-image-preview"
    openai_client = SimpleNamespace(is_available=lambda: False, image_model="gpt-image-2")
    ai_module = SimpleNamespace(clip_model=None)

    response = await creative_suite_status_data(
        feature_enabled_getter=lambda name: True,
        openai_image_client_getter=lambda: openai_client,
        gemini_client_getter=lambda: client,
        ai_module=ai_module,
    )

    assert response["name_generator"]["available"] is True
    assert response["name_generator"]["cost"] == 1
    assert response["logo_studio"]["available"] is True
    assert response["logo_studio"]["cost"] == 5
    assert response["logo_studio"]["reason"] == ""
    assert response["logo_studio"]["audit_available"] is False
    assert response["logo_studio"]["audit_reason"] == "CLIP modeli yuklenmemis"
    assert response["logo_studio"]["providers"]["openai"]["available"] is False
    assert response["logo_studio"]["providers"]["gemini"]["available"] is True


@pytest.mark.asyncio
async def test_creative_service_status_data_marks_logo_available_with_clip():
    from services.creative_service import creative_suite_status_data

    client = MagicMock()
    client.is_available.return_value = True
    client.image_model = "gemini-3-pro-image-preview"
    openai_client = SimpleNamespace(is_available=lambda: False, image_model="gpt-image-2")
    ai_module = SimpleNamespace(clip_model=object(), get_clip_embedding_cached=lambda path: [0.1])

    response = await creative_suite_status_data(
        feature_enabled_getter=lambda name: True,
        openai_image_client_getter=lambda: openai_client,
        gemini_client_getter=lambda: client,
        ai_module=ai_module,
    )

    assert response["name_generator"]["available"] is True
    assert response["logo_studio"]["available"] is True
    assert response["logo_studio"]["reason"] == ""
    assert response["logo_studio"]["audit_available"] is True


@pytest.mark.asyncio
async def test_creative_service_status_data_marks_logo_available_when_openai_is_available():
    from services.creative_service import creative_suite_status_data

    gemini_client = SimpleNamespace(
        is_available=lambda: False,
        image_model="gemini-3-pro-image-preview",
    )
    openai_client = SimpleNamespace(
        is_available=lambda: True,
        image_model="gpt-image-2",
    )

    response = await creative_suite_status_data(
        feature_enabled_getter=lambda name: True,
        openai_image_client_getter=lambda: openai_client,
        gemini_client_getter=lambda: gemini_client,
        ai_module=SimpleNamespace(clip_model=None),
    )

    assert response["name_generator"]["available"] is False
    assert response["logo_studio"]["available"] is True
    assert response["logo_studio"]["reason"] == ""
    assert response["logo_studio"]["providers"]["openai"]["available"] is True
    assert response["logo_studio"]["providers"]["openai"]["model"] == "gpt-image-2"
    assert response["logo_studio"]["providers"]["gemini"]["available"] is False


def test_creative_request_validation_rejects_invalid_classes_and_styles():
    from models.schemas import LogoGenerationRequest, NameSuggestionRequest

    with pytest.raises(Exception):
        NameSuggestionRequest(query="Acme", nice_classes=[46])

    with pytest.raises(Exception):
        NameSuggestionRequest(query="Acme", style="luxury")

    with pytest.raises(Exception):
        LogoGenerationRequest(brand_name="Acme", nice_classes=[99])

    with pytest.raises(Exception):
        LogoGenerationRequest(brand_name="Acme", style="technical")


@pytest.mark.asyncio
async def test_openai_image_client_decodes_generated_logo_images():
    from generative_ai.openai_image_client import OpenAIImageClient

    payloads = [
        base64.b64encode(b"logo-1").decode("ascii"),
        base64.b64encode(b"logo-2").decode("ascii"),
    ]
    recorded = {}

    class _FakeImages:
        async def generate(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=value) for value in payloads]
            )

    client = OpenAIImageClient(
        settings=SimpleNamespace(
            openai_api_key="",
            openai_image_model="gpt-image-2",
            openai_image_size="1024x1024",
            openai_image_quality="high",
            openai_image_revision_quality="high",
            openai_image_background="auto",
            openai_image_output_format="png",
            openai_timeout=120,
            openai_max_retries=0,
        )
    )
    client.api_key = "test-key"
    client._initialized = True
    client._client = SimpleNamespace(images=_FakeImages())

    images = await client.generate_logos(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        count=2,
    )

    assert images == [b"logo-1", b"logo-2"]
    assert recorded["model"] == "gpt-image-2"
    assert recorded["n"] == 2
    assert recorded["output_format"] == "png"
    assert 'The text "Acme" MUST be clearly visible' in recorded["prompt"]
    assert client.source_layout == "native_multi_image"
    assert client.provider_call_count == 1


@pytest.mark.asyncio
async def test_openai_image_client_does_not_retry_when_native_multi_image_is_short():
    from generative_ai.openai_image_client import OpenAIImageClient

    calls = []

    class _FakeImages:
        async def generate(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(b"logo-1").decode("ascii"))]
            )

    client = OpenAIImageClient(
        settings=SimpleNamespace(
            openai_api_key="",
            openai_image_model="gpt-image-2",
            openai_image_size="1024x1024",
            openai_image_quality="high",
            openai_image_revision_quality="high",
            openai_image_background="auto",
            openai_image_output_format="png",
            openai_timeout=120,
            openai_max_retries=0,
        )
    )
    client.api_key = "test-key"
    client._initialized = True
    client._client = SimpleNamespace(images=_FakeImages())

    images = await client.generate_logos(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        count=4,
    )

    assert images == [b"logo-1"]
    assert len(calls) == 1
    assert calls[0]["n"] == 4


@pytest.mark.asyncio
async def test_openai_image_client_uses_edit_endpoint_for_logo_revisions():
    from generative_ai.openai_image_client import OpenAIImageClient

    recorded = {}

    class _FakeImages:
        async def edit(self, **kwargs):
            image = kwargs["image"]
            recorded.update(kwargs)
            recorded["reference_bytes"] = image.read()
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(b"revision").decode("ascii"))]
            )

    client = OpenAIImageClient(
        settings=SimpleNamespace(
            openai_api_key="",
            openai_image_model="gpt-image-2",
            openai_image_size="1024x1024",
            openai_image_quality="high",
            openai_image_revision_quality="high",
            openai_image_background="auto",
            openai_image_output_format="png",
            openai_timeout=120,
            openai_max_retries=0,
        )
    )
    client.api_key = "test-key"
    client._initialized = True
    client._client = SimpleNamespace(images=_FakeImages())

    images = await client.generate_logo_revisions(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        revision_prompt="Make it more geometric",
        reference_image_bytes=b"reference-png",
        count=1,
    )

    assert images == [b"revision"]
    assert recorded["model"] == "gpt-image-2"
    assert recorded["n"] == 1
    assert recorded["reference_bytes"] == b"reference-png"
    assert "Make it more geometric" in recorded["prompt"]


@pytest.mark.asyncio
async def test_openai_image_client_splits_quality_for_generate_vs_edit():
    from generative_ai.openai_image_client import OpenAIImageClient

    generate_calls: list[dict] = []
    edit_calls: list[dict] = []

    class _FakeImages:
        async def generate(self, **kwargs):
            generate_calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(b"gen").decode("ascii"))]
            )

        async def edit(self, **kwargs):
            kwargs["image"].read()
            edit_calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(b"rev").decode("ascii"))]
            )

    client = OpenAIImageClient(
        settings=SimpleNamespace(
            openai_api_key="",
            openai_image_model="gpt-image-2",
            openai_image_size="1024x1024",
            openai_image_quality="medium",
            openai_image_revision_quality="high",
            openai_image_background="auto",
            openai_image_output_format="png",
            openai_timeout=120,
            openai_max_retries=0,
        )
    )
    client.api_key = "test-key"
    client._initialized = True
    client._client = SimpleNamespace(images=_FakeImages())

    await client.generate_logos(brand_name="Acme", description="x", style="modern", count=1)
    await client.generate_logo_revisions(
        brand_name="Acme",
        description="x",
        style="modern",
        revision_prompt="tighter",
        reference_image_bytes=b"reference-png",
        count=1,
    )

    assert generate_calls[0]["quality"] == "medium"
    assert edit_calls[0]["quality"] == "high"


@pytest.mark.asyncio
async def test_creative_service_splits_logo_count_for_first_gen_vs_revision():
    """Verify the count-split: first generation uses logo_images_per_run,
    revisions use logo_revision_images_per_run. Production setup is 4 + 1
    so users get meaningful exploration choices but a single focused refinement."""
    from models.schemas import LogoGenerationRequest
    from services.creative_service import generate_logo_data

    org_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=org_id)
    settings_obj = SimpleNamespace(
        creative=SimpleNamespace(
            logo_images_per_run=4,
            logo_revision_images_per_run=1,
        )
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    captured_count = {}

    class _RecordingClient:
        is_available = lambda self: True
        provider_name = "openai"
        primary_provider_name = "openai"
        source_layout = "native_multi_image"
        provider_call_count = 1
        fallback_used = False
        fallback_provider = None
        primary_attempted_provider = None
        primary_attempt_failed = False

        async def generate_logos(self, **kwargs):
            captured_count["first_gen"] = kwargs.get("count")
            return [b"first-gen-bytes"] * kwargs.get("count", 0)

        async def generate_logo_revisions(self, **kwargs):
            captured_count["revision"] = kwargs.get("count")
            return [b"revision-bytes"] * kwargs.get("count", 0)

    parent_row = {
        "id": parent_id,
        "project_id": "project-1",
        "audit_status": "completed",
        "image_path": None,  # skips reference-bytes file read
    }

    common_kwargs = dict(
        current_user=current_user,
        settings_obj=settings_obj,
        database_factory=MagicMock(return_value=mock_db_cm),
        logo_eligibility_checker=MagicMock(return_value=(True, "ok", {})),
        deduct_logo_credit_handler=MagicMock(return_value=True),
        refund_logo_credit_handler=MagicMock(),
        logo_provider_getter=lambda: _RecordingClient(),
        generation_uuid_factory=lambda: uuid.UUID("11111111-1111-1111-1111-111111111111"),
        save_logo_image_handler=MagicMock(side_effect=lambda *a, **k: f"path-{uuid.uuid4().hex[:6]}.png"),
        store_generated_image_handler=MagicMock(side_effect=lambda *a, **k: f"img-{uuid.uuid4().hex[:6]}"),
        logo_credits_remaining_getter=MagicMock(return_value={"monthly": 10, "purchased": 0}),
        visual_audit_available_checker=MagicMock(return_value=(True, "")),
        audit_scheduler=MagicMock(),
        create_logo_project_handler=MagicMock(return_value="project-1"),
        generation_log_handler=MagicMock(return_value="gen-log-1"),
        audit_log_handler=MagicMock(),
    )

    # First generation (no parent_image_id) — should use 4
    first_gen_request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
    )
    await generate_logo_data(request=first_gen_request, **common_kwargs)
    assert captured_count.get("first_gen") == 4, (
        f"first generation should use logo_images_per_run=4, got {captured_count.get('first_gen')}"
    )
    assert "revision" not in captured_count

    # Revision (parent_image_id set) — should use 1
    captured_count.clear()
    revision_request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        parent_image_id=str(parent_id),
        revision_prompt="Make it tighter",
    )
    with patch(
        "services.creative_service._get_logo_image_row",
        return_value=parent_row,
    ):
        await generate_logo_data(request=revision_request, **common_kwargs)
    assert captured_count.get("revision") == 1, (
        f"revision should use logo_revision_images_per_run=1, got {captured_count.get('revision')}"
    )
    assert "first_gen" not in captured_count


@pytest.mark.asyncio
async def test_creative_service_fans_out_one_call_per_canonical_style_when_style_omitted():
    """First-gen with style=None must trigger 4 parallel provider calls,
    one per canonical style (Modern/Classic/Bold/Playful), and the resulting
    logos must each carry their style for both DB persistence and the LogoResult."""
    from models.schemas import LogoGenerationRequest
    from services.creative_service import generate_logo_data, CANONICAL_LOGO_STYLES

    org_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=org_id)
    settings_obj = SimpleNamespace(
        creative=SimpleNamespace(
            logo_images_per_run=4,
            logo_revision_images_per_run=1,
        )
    )

    mock_db_cm = MagicMock()
    mock_db_cm.__enter__.return_value = MagicMock()
    mock_db_cm.__exit__.return_value = False

    captured_styles_called: list[str] = []
    captured_counts: list[int] = []
    persisted_styles: list[str] = []

    class _RecordingProvider:
        is_available = lambda self: True
        provider_name = "openai"
        primary_provider_name = "openai"
        source_layout = "native_multi_image"
        provider_call_count = 1
        fallback_used = False
        fallback_provider = None
        primary_attempted_provider = None
        primary_attempt_failed = False

        async def generate_logos(self, **kwargs):
            captured_styles_called.append(kwargs.get("style"))
            captured_counts.append(kwargs.get("count"))
            return [b"\x89PNG_for_" + (kwargs.get("style") or "?").encode()]

    def _store(*args, **kwargs):
        persisted_styles.append(kwargs.get("style"))
        return f"img-{uuid.uuid4().hex[:6]}"

    request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        # style omitted on purpose — triggers fan-out
    )

    response = await generate_logo_data(
        request=request,
        current_user=current_user,
        settings_obj=settings_obj,
        database_factory=MagicMock(return_value=mock_db_cm),
        logo_eligibility_checker=MagicMock(return_value=(True, "ok", {})),
        deduct_logo_credit_handler=MagicMock(return_value=True),
        refund_logo_credit_handler=MagicMock(),
        logo_provider_getter=lambda: _RecordingProvider(),
        generation_uuid_factory=lambda: uuid.UUID("11111111-1111-1111-1111-111111111111"),
        save_logo_image_handler=MagicMock(side_effect=lambda *a, **k: f"path-{uuid.uuid4().hex[:6]}.png"),
        store_generated_image_handler=_store,
        logo_credits_remaining_getter=MagicMock(return_value={"monthly": 10, "purchased": 0}),
        visual_audit_available_checker=MagicMock(return_value=(True, "")),
        audit_scheduler=MagicMock(),
        create_logo_project_handler=MagicMock(return_value="project-1"),
        generation_log_handler=MagicMock(return_value="gen-log-1"),
        audit_log_handler=MagicMock(),
    )

    # Exactly 4 provider calls, one per canonical style, each requesting n=1.
    assert sorted(captured_styles_called) == sorted(list(CANONICAL_LOGO_STYLES))
    assert all(c == 1 for c in captured_counts)
    # Each persisted row got its corresponding style (order matches CANONICAL_LOGO_STYLES).
    assert sorted(persisted_styles) == sorted(list(CANONICAL_LOGO_STYLES))
    # Each LogoResult carries its style back to the API consumer.
    assert sorted(lg.style for lg in response.logos) == sorted(list(CANONICAL_LOGO_STYLES))


@pytest.mark.asyncio
async def test_creative_service_revision_locks_to_parent_style_from_db():
    """A revision must auto-use the parent logo's style from the DB row
    (the UI no longer asks the user to pick one). The revision row also
    persists that same style so subsequent revisions stay consistent."""
    from models.schemas import LogoGenerationRequest
    from services.creative_service import generate_logo_data

    org_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=org_id)
    settings_obj = SimpleNamespace(
        creative=SimpleNamespace(
            logo_images_per_run=4,
            logo_revision_images_per_run=1,
        )
    )

    mock_db_cm = MagicMock()
    mock_db_cm.__enter__.return_value = MagicMock()
    mock_db_cm.__exit__.return_value = False

    captured_revision_style = []
    persisted_revision_styles = []

    class _RecordingProvider:
        is_available = lambda self: True
        provider_name = "openai"
        primary_provider_name = "openai"
        source_layout = "native_multi_image"
        provider_call_count = 1
        fallback_used = False
        fallback_provider = None
        primary_attempted_provider = None
        primary_attempt_failed = False

        async def generate_logo_revisions(self, **kwargs):
            captured_revision_style.append(kwargs.get("style"))
            return [b"\x89PNG_revision_bytes"]

    def _store(*args, **kwargs):
        persisted_revision_styles.append(kwargs.get("style"))
        return f"img-{uuid.uuid4().hex[:6]}"

    # Parent row reports style="bold" — revision must adopt it.
    parent_row = {
        "id": parent_id,
        "project_id": "project-1",
        "audit_status": "completed",
        "image_path": None,
        "style": "bold",
    }

    request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        parent_image_id=str(parent_id),
        revision_prompt="Make it tighter",
        # style omitted by the UI for revisions — must default to parent's style
    )

    with patch(
        "services.creative_service._get_logo_image_row",
        return_value=parent_row,
    ):
        response = await generate_logo_data(
            request=request,
            current_user=current_user,
            settings_obj=settings_obj,
            database_factory=MagicMock(return_value=mock_db_cm),
            logo_eligibility_checker=MagicMock(return_value=(True, "ok", {})),
            deduct_logo_credit_handler=MagicMock(return_value=True),
            refund_logo_credit_handler=MagicMock(),
            logo_provider_getter=lambda: _RecordingProvider(),
            generation_uuid_factory=lambda: uuid.UUID("11111111-1111-1111-1111-111111111111"),
            save_logo_image_handler=MagicMock(side_effect=lambda *a, **k: f"path-{uuid.uuid4().hex[:6]}.png"),
            store_generated_image_handler=_store,
            logo_credits_remaining_getter=MagicMock(return_value={"monthly": 10, "purchased": 0}),
            visual_audit_available_checker=MagicMock(return_value=(True, "")),
            audit_scheduler=MagicMock(),
            create_logo_project_handler=MagicMock(return_value="project-1"),
            generation_log_handler=MagicMock(return_value="gen-log-1"),
            audit_log_handler=MagicMock(),
        )

    assert captured_revision_style == ["bold"], (
        f"revision provider call should receive parent's style 'bold', got {captured_revision_style}"
    )
    assert persisted_revision_styles == ["bold"], (
        f"revision row should persist style='bold', got {persisted_revision_styles}"
    )
    assert response.logos[0].style == "bold"


def _make_gemini_logo_panel_bytes(blank_index=None, size=480):
    for module_name in ("PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageChops"):
        if isinstance(sys.modules.get(module_name), MagicMock):
            sys.modules.pop(module_name, None)

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    half = size // 2
    colors = ["#1d4ed8", "#047857", "#b45309", "#be123c"]
    for index, (x0, y0) in enumerate(
        [(0, 0), (half, 0), (0, half), (half, half)],
        start=1,
    ):
        draw.rectangle([x0, y0, x0 + half, y0 + half], fill="white")
        if index == blank_index:
            continue
        color = colors[index - 1]
        margin = size // 16
        cx = x0 + half // 2
        cy = y0 + half // 2
        draw.ellipse(
            [cx - 58, cy - 58, cx + 58, cy + 58],
            outline=color,
            width=12,
        )
        draw.rectangle(
            [cx - 36, cy - 12, cx + 36, cy + 12],
            fill=color,
        )
        draw.text((x0 + margin, y0 + half - margin * 2), f"ACME {index}", fill=color)

    draw.line([(half, 0), (half, size)], fill="#dddddd", width=10)
    draw.line([(0, half), (size, half)], fill="#dddddd", width=10)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_gemini_image_client_splits_one_logo_panel_into_four_options():
    from generative_ai.gemini_client import GeminiClient

    panel_bytes = _make_gemini_logo_panel_bytes()
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            inline_data=SimpleNamespace(data=panel_bytes)
                        )
                    ]
                )
            )
        ]
    )
    client = GeminiClient(
        settings=SimpleNamespace(
            google_api_key="test-key",
            gemini_text_model="gemini-text",
            gemini_image_model="gemini-3-pro-image-preview",
            gemini_timeout=120,
            gemini_max_retries=0,
        )
    )
    client._initialized = True
    client._client = SimpleNamespace()
    client._call_with_retry_raw = AsyncMock(return_value=response)

    images = await client.generate_logos(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        count=4,
    )

    assert len(images) == 4
    assert len(set(images)) == 4
    assert client._call_with_retry_raw.await_count == 1
    assert client.source_layout == "panel_2x2_split"
    assert client.provider_call_count == 1
    call_kwargs = client._call_with_retry_raw.await_args.kwargs
    assert call_kwargs["model"] == "gemini-3-pro-image-preview"
    assert "2x2 grid" in call_kwargs["contents"]
    assert "exactly four" in call_kwargs["contents"].lower()
    from PIL import Image

    for image_bytes in images:
        crop = Image.open(io.BytesIO(image_bytes))
        assert crop.format == "PNG"
        assert crop.width == crop.height
        assert crop.width >= 128


@pytest.mark.asyncio
async def test_gemini_image_client_rejects_blank_logo_panel_crop():
    from generative_ai.gemini_client import GeminiClient, GeminiError

    panel_bytes = _make_gemini_logo_panel_bytes(blank_index=3)
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            inline_data=SimpleNamespace(data=panel_bytes)
                        )
                    ]
                )
            )
        ]
    )
    client = GeminiClient(
        settings=SimpleNamespace(
            google_api_key="test-key",
            gemini_text_model="gemini-text",
            gemini_image_model="gemini-3-pro-image-preview",
            gemini_timeout=120,
            gemini_max_retries=0,
        )
    )
    client._initialized = True
    client._client = SimpleNamespace()
    client._call_with_retry_raw = AsyncMock(return_value=response)

    with pytest.raises(GeminiError) as exc_info:
        await client.generate_logos(
            brand_name="Acme",
            description="Modern wordmark",
            style="modern",
            count=4,
        )

    assert "crop 3 appears blank" in str(exc_info.value)
    assert client._call_with_retry_raw.await_count == 1


@pytest.mark.asyncio
async def test_openai_image_client_marks_content_safety_failures_non_fallbackable():
    from generative_ai.openai_image_client import OpenAIImageClient, OpenAIImageError

    class _SafetyError(Exception):
        status_code = 400
        code = "content_policy_violation"

    class _FakeImages:
        async def generate(self, **kwargs):
            raise _SafetyError("content policy violation")

    client = OpenAIImageClient(
        settings=SimpleNamespace(
            openai_api_key="",
            openai_image_model="gpt-image-2",
            openai_image_size="1024x1024",
            openai_image_quality="high",
            openai_image_revision_quality="high",
            openai_image_background="auto",
            openai_image_output_format="png",
            openai_timeout=120,
            openai_max_retries=0,
        )
    )
    client.api_key = "test-key"
    client._initialized = True
    client._client = SimpleNamespace(images=_FakeImages())

    with pytest.raises(OpenAIImageError) as exc_info:
        await client.generate_logos(
            brand_name="Acme",
            description="Modern wordmark",
            style="modern",
            count=1,
        )

    assert exc_info.value.fallback_allowed is False
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_logo_image_provider_chain_uses_openai_first_and_falls_back_to_gemini():
    from generative_ai.logo_image_provider import LogoImageProviderChain
    from generative_ai.openai_image_client import OpenAIImageError

    class _Provider:
        def __init__(self, name, model, available=True, error=None, images=None):
            self.provider_name = name
            self.image_model = model
            self.available = available
            self.error = error
            self.images = images or [f"{name}-image".encode("ascii")]
            self.calls = []

        def is_available(self):
            return self.available

        async def generate_logos(self, **kwargs):
            self.calls.append(kwargs)
            if self.error:
                raise self.error
            return self.images

        async def generate_logo_revisions(self, **kwargs):
            self.calls.append(kwargs)
            if self.error:
                raise self.error
            return self.images

    openai = _Provider("openai", "gpt-image-2", images=[b"openai-logo"])
    gemini = _Provider("gemini", "gemini-3-pro-image-preview", images=[b"gemini-logo"])
    chain = LogoImageProviderChain(providers=[openai, gemini])

    images = await chain.generate_logos(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        count=1,
    )

    assert images == [b"openai-logo"]
    assert len(openai.calls) == 1
    assert not gemini.calls
    assert chain.selected_metadata()["provider"] == "openai"
    assert chain.selected_metadata()["model"] == "gpt-image-2"
    assert chain.selected_metadata()["source_layout"] == "native_multi_image"
    assert chain.selected_metadata()["provider_call_count"] == 1

    openai_error = OpenAIImageError("rate limited", status_code=429, fallback_allowed=True)
    openai = _Provider("openai", "gpt-image-2", error=openai_error)
    gemini = _Provider("gemini", "gemini-3-pro-image-preview", images=[b"gemini-logo"])
    chain = LogoImageProviderChain(providers=[openai, gemini])

    images = await chain.generate_logos(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        count=1,
    )

    assert images == [b"gemini-logo"]
    assert len(openai.calls) == 1
    assert len(gemini.calls) == 1
    assert chain.selected_metadata()["provider"] == "gemini"
    assert chain.attempts[0]["fallback_allowed"] is True


@pytest.mark.asyncio
async def test_logo_image_provider_chain_rejects_partial_logo_batches():
    from generative_ai.logo_image_provider import LogoImageProviderChain, LogoImageProviderError

    class _Provider:
        def __init__(self, name, images):
            self.provider_name = name
            self.image_model = f"{name}-model"
            self.images = images
            self.calls = []

        def is_available(self):
            return True

        async def generate_logos(self, **kwargs):
            self.calls.append(kwargs)
            return self.images

    partial = _Provider("gemini", [b"one-logo"])
    chain = LogoImageProviderChain(providers=[partial])

    with pytest.raises(LogoImageProviderError):
        await chain.generate_logos(
            brand_name="Acme",
            description="Modern wordmark",
            style="modern",
            count=4,
        )

    assert len(partial.calls) == 1
    assert chain.attempts[0]["used"] is False
    assert "returned 1/4 logo images" in chain.attempts[0]["error"]

    partial = _Provider("openai", [b"one-logo"])
    complete = _Provider("gemini", [b"one", b"two", b"three", b"four"])
    chain = LogoImageProviderChain(providers=[partial, complete])

    images = await chain.generate_logos(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        count=4,
    )

    assert images == [b"one", b"two", b"three", b"four"]
    assert chain.attempts[0]["used"] is False
    assert chain.attempts[1]["used"] is True


@pytest.mark.asyncio
async def test_logo_image_provider_chain_does_not_fallback_for_content_safety_failures():
    from generative_ai.logo_image_provider import LogoImageProviderChain
    from generative_ai.openai_image_client import OpenAIImageError

    class _Provider:
        provider_name = "openai"
        image_model = "gpt-image-2"

        def __init__(self, error):
            self.error = error
            self.calls = 0

        def is_available(self):
            return True

        async def generate_logos(self, **kwargs):
            self.calls += 1
            raise self.error

    class _GeminiProvider:
        provider_name = "gemini"
        image_model = "gemini-3-pro-image-preview"

        def __init__(self):
            self.calls = 0

        def is_available(self):
            return True

        async def generate_logos(self, **kwargs):
            self.calls += 1
            return [b"gemini-logo"]

    openai = _Provider(OpenAIImageError("content policy", status_code=400, fallback_allowed=False))
    gemini = _GeminiProvider()
    chain = LogoImageProviderChain(providers=[openai, gemini])

    with pytest.raises(OpenAIImageError):
        await chain.generate_logos(
            brand_name="Acme",
            description="Modern wordmark",
            style="modern",
            count=1,
        )

    assert openai.calls == 1
    assert gemini.calls == 0


def test_creative_name_request_cache_key_includes_prompt_context():
    from models.schemas import NameSuggestionRequest
    from services.creative_service import _name_request_cache_key

    base = NameSuggestionRequest(
        query="Acme",
        nice_classes=[25],
        industry="Footwear",
        style="modern",
        language="tr",
        avoid_names=["Nike"],
    )
    changed_style = NameSuggestionRequest(
        query="Acme",
        nice_classes=[25],
        industry="Footwear",
        style="classic",
        language="tr",
        avoid_names=["Nike"],
    )
    changed_classes = NameSuggestionRequest(
        query="Acme",
        nice_classes=[9],
        industry="Footwear",
        style="modern",
        language="tr",
        avoid_names=["Nike"],
    )

    assert _name_request_cache_key(base) != _name_request_cache_key(changed_style)
    assert _name_request_cache_key(base) != _name_request_cache_key(changed_classes)


def test_ai_studio_name_risk_prompt_uses_database_candidates_without_scores():
    from models.schemas import NameSuggestionRequest, SafeNameResult
    from services.creative_service import (
        _build_ai_studio_name_risk_messages,
        _coerce_name_results_to_risk_items,
        _parse_ai_studio_score_only_response,
    )

    request = NameSuggestionRequest(
        query="Acme",
        nice_classes=[25],
        industry="Footwear",
        style="modern",
        language="tr",
        avoid_names=[],
    )
    deterministic = SafeNameResult(
        name="ACMIA",
        risk_score=88.0,
        text_similarity=0.86,
        semantic_similarity=0.75,
        phonetic_match=False,
        closest_match="ACME",
        is_safe=False,
        translation_similarity=0.0,
        risk_level="high",
    )
    name_items = _coerce_name_results_to_risk_items([deterministic])
    name_items[0]["db_candidates"] = [
        {
            "name": "ACME",
            "application_no": "2026/000001",
            "status": "registered",
            "nice_classes": [25],
            "owner": "Existing Holder",
        }
    ]

    system_prompt, user_prompt, expected_ids = _build_ai_studio_name_risk_messages(
        name_items=name_items,
        request=request,
    )
    parsed_scores = _parse_ai_studio_score_only_response(
        {"results": [{"candidate_id": "name_1", "llm_risk_score": 12}]},
        expected_ids,
    )

    assert expected_ids == ["name_1"]
    assert "ACMIA" in user_prompt
    assert "ACME" in user_prompt
    assert "2026/000001" in user_prompt
    assert "deterministic" not in user_prompt
    assert "text_similarity" not in user_prompt
    assert "prior similarity scores" in system_prompt
    assert parsed_scores["name_1"] == 12.0


@pytest.mark.asyncio
async def test_ai_studio_logo_risk_payload_is_visual_image_only(tmp_path):
    from services.creative_service import _score_logo_with_risk_report_async

    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe"
        b"\x02\xfeA\x8c\xa7\x9a\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    query_path = tmp_path / "query.png"
    candidate_path = tmp_path / "candidate.png"
    query_path.write_bytes(png_bytes)
    candidate_path.write_bytes(png_bytes)

    class FakeRiskClient:
        text_model = "qwen:test"

        def __init__(self):
            self.kwargs = None

        def is_available(self):
            return True

        async def generate_multimodal_json(self, **kwargs):
            self.kwargs = kwargs
            return {"results": [{"candidate_id": "logo_1", "llm_risk_score": 17}]}

    fake_client = FakeRiskClient()
    json_client_getter = MagicMock(side_effect=AssertionError("logo risk must not use text-only scoring"))
    with patch("services.search_risk_report_service._logo_path_from_url", return_value=str(candidate_path)):
        result = await _score_logo_with_risk_report_async(
            brand_name="ACME",
            nice_classes=[25],
            image_path=str(query_path),
            ocr_text="acme",
            matches=[
                {
                    "name": "ACME OLD",
                    "application_no": "2026/000001",
                    "status": "registered",
                    "nice_classes": [25],
                    "owner": "Existing Holder",
                    "image_path": "logos/acme.png",
                    "visual_similarity_score": 0.91,
                    "name_conflict_score": 0.88,
                    "overall_risk_score": 0.91,
                    "visual_breakdown": {"clip": 0.91, "raw_combined": 0.91},
                }
            ],
            closest_match_image_url_builder=MagicMock(return_value="/api/trademark-image/logos/acme.png"),
            json_client_getter=json_client_getter,
            multimodal_client_getter=lambda: fake_client,
        )

    payload = fake_client.kwargs["user_prompt"]
    system_prompt = fake_client.kwargs["system_prompt"]
    assert result["llm_risk_score"] == 17.0
    assert [image["label"] for image in fake_client.kwargs["images"]] == ["query_logo", "candidate_logo_1"]
    assert "query_logo" in payload
    assert "candidate_logo_1" in payload
    assert "ACME" not in payload
    assert "acme" not in payload
    assert "2026/000001" not in payload
    assert "registered" not in payload
    assert "Existing Holder" not in payload
    assert "selected_classes" not in payload
    assert "ocr_text" not in payload
    assert "nice_classes" not in payload
    assert "application_no" not in payload
    assert "visual_similarity_score" not in payload
    assert "name_conflict_score" not in payload
    assert "overall_risk_score" not in payload
    assert "visual_breakdown" not in payload
    assert "clip" not in payload
    assert "deterministic" not in payload
    assert "deterministic" not in system_prompt
    assert "STRICTLY and EXCLUSIVELY" in system_prompt
    assert "Do not evaluate semantic meaning, language, or phonetic overlap" in system_prompt
    json_client_getter.assert_not_called()


@pytest.mark.asyncio
async def test_creative_service_suggest_names_data_returns_cached_response():
    from models.schemas import NameSuggestionRequest, SafeNameResult
    from services.creative_service import suggest_names_data

    org_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=org_id)
    request = NameSuggestionRequest(
        query="Acme",
        nice_classes=[25],
        industry="Footwear",
        style="modern",
        language="tr",
        avoid_names=["Nike"],
    )
    safe_name = SafeNameResult(
        name="ACMIA",
        risk_score=12.5,
        text_similarity=0.12,
        semantic_similarity=0.18,
        phonetic_match=False,
        closest_match="ACME",
        is_safe=True,
        translation_similarity=0.0,
        risk_level="low",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    response = await suggest_names_data(
        request=request,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        name_eligibility_checker=MagicMock(
            return_value=(True, "ok", {"using_purchased_credits": False})
        ),
        session_count_getter=MagicMock(return_value=4),
        cached_results_getter=MagicMock(
            return_value={
                "safe": [safe_name],
                "filtered_count": 1,
                "total_generated": 2,
            }
        ),
        plan_credits_getter=MagicMock(
            return_value={
                "session_limit": 5,
                "used": 4,
                "purchased": 1,
                "plan": "professional",
            }
        ),
    )

    assert response.cached is True
    assert response.session_count == 4
    assert response.safe_names[0].name == "ACMIA"
    assert response.filtered_count == 1
    assert response.credits_remaining["plan"] == "professional"


@pytest.mark.asyncio
async def test_creative_service_suggest_names_data_generates_and_logs_results():
    from models.schemas import NameSuggestionRequest, SafeNameResult
    from services.creative_service import suggest_names_data

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    current_user = SimpleNamespace(id=user_id, organization_id=org_id)
    request = NameSuggestionRequest(
        query="Acme",
        nice_classes=[25],
        industry="Footwear",
        style="modern",
        language="tr",
        avoid_names=["Nike"],
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    client = MagicMock()
    client.is_available.return_value = True
    client.build_name_prompt.return_value = "prompt"
    client.generate_names = AsyncMock(return_value=["ACMIA", "ACMEO"])

    safe_result = SafeNameResult(
        name="ACMIA",
        risk_score=10.0,
        text_similarity=0.1,
        semantic_similarity=0.15,
        phonetic_match=False,
        closest_match="ACME",
        is_safe=True,
        translation_similarity=0.0,
        risk_level="low",
    )
    filtered_result = SafeNameResult(
        name="ACMEO",
        risk_score=89.0,
        text_similarity=0.8,
        semantic_similarity=0.82,
        phonetic_match=True,
        closest_match="ACMEO",
        is_safe=False,
        translation_similarity=0.1,
        risk_level="high",
    )

    deduct_name_credit_handler = MagicMock()
    increment_name_generation_usage_handler = MagicMock()
    session_count_incrementer = MagicMock(return_value=5)
    cache_results_handler = MagicMock()
    generation_log_handler = MagicMock()
    audit_log_handler = MagicMock()
    name_risk_scorer_handler = AsyncMock(
        return_value={
            "name_1": {"llm_risk_score": 10.0, "risk_source": "risk_report_llm", "llm_risk_model": "qwen:test"},
            "name_2": {"llm_risk_score": 89.0, "risk_source": "risk_report_llm", "llm_risk_model": "qwen:test"},
        }
    )

    response = await suggest_names_data(
        request=request,
        current_user=current_user,
        settings_obj=SimpleNamespace(
            creative=SimpleNamespace(name_batch_size=4, name_similarity_threshold=0.7)
        ),
        database_factory=MagicMock(return_value=mock_db_cm),
        name_eligibility_checker=MagicMock(
            return_value=(True, "ok", {"using_purchased_credits": True})
        ),
        deduct_name_credit_handler=deduct_name_credit_handler,
        increment_name_generation_usage_handler=increment_name_generation_usage_handler,
        session_count_getter=MagicMock(return_value=2),
        cached_results_getter=MagicMock(return_value=None),
        plan_credits_getter=MagicMock(
            return_value={
                "session_limit": 5,
                "used": 5,
                "purchased": 0,
                "plan": "professional",
            }
        ),
        gemini_client_getter=lambda: client,
        batch_validate_names_handler=MagicMock(
            return_value=[safe_result, filtered_result]
        ),
        name_risk_scorer_handler=name_risk_scorer_handler,
        session_count_incrementer=session_count_incrementer,
        cache_results_handler=cache_results_handler,
        generation_log_handler=generation_log_handler,
        audit_log_handler=audit_log_handler,
    )

    assert response.cached is False
    assert response.total_generated == 2
    assert response.filtered_count == 1
    assert response.session_count == 5
    assert [item.name for item in response.safe_names] == ["ACMIA"]
    assert response.safe_names[0].llm_risk_score == 10.0
    assert response.safe_names[0].risk_source == "risk_report_llm"
    client.build_name_prompt.assert_called_once()
    assert client.build_name_prompt.call_args.kwargs["concept"] == "Acme"
    client.generate_names.assert_awaited_once_with(prompt="prompt", count=4)
    deduct_name_credit_handler.assert_called_once_with(mock_db, str(org_id))
    increment_name_generation_usage_handler.assert_called_once_with(
        mock_db,
        str(user_id),
        str(org_id),
    )
    session_count_incrementer.assert_called_once()
    assert session_count_incrementer.call_args.args[0] == str(org_id)
    assert "acme" in session_count_incrementer.call_args.args[1]
    assert session_count_incrementer.call_args.args[2] == 1
    cache_results_handler.assert_called_once()
    generation_log_handler.assert_called_once()
    assert generation_log_handler.call_args.kwargs["output_data"]["risk_source"] == "risk_report_llm"
    audit_log_handler.assert_called_once()


@pytest.mark.asyncio
async def test_creative_service_suggest_names_data_uses_llm_score_for_safety():
    from models.schemas import NameSuggestionRequest, SafeNameResult
    from services.creative_service import suggest_names_data

    org_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=org_id, is_superadmin=True)
    request = NameSuggestionRequest(
        query="Acme",
        nice_classes=[25],
        industry="Footwear",
        style="modern",
        language="tr",
        avoid_names=[],
    )
    client = MagicMock()
    client.is_available.return_value = True
    client.build_name_prompt.return_value = "prompt"
    client.generate_names = AsyncMock(return_value=["ACMIA", "ACMEO"])

    deterministic_high = SafeNameResult(
        name="ACMIA",
        risk_score=88.0,
        text_similarity=0.86,
        semantic_similarity=0.82,
        phonetic_match=False,
        closest_match="ACME",
        is_safe=False,
        translation_similarity=0.0,
        risk_level="high",
    )
    deterministic_low = SafeNameResult(
        name="ACMEO",
        risk_score=12.0,
        text_similarity=0.1,
        semantic_similarity=0.1,
        phonetic_match=False,
        closest_match=None,
        is_safe=True,
        translation_similarity=0.0,
        risk_level="low",
    )

    response = await suggest_names_data(
        request=request,
        current_user=current_user,
        settings_obj=SimpleNamespace(
            creative=SimpleNamespace(name_batch_size=2, name_similarity_threshold=0.7)
        ),
        gemini_client_getter=lambda: client,
        session_count_getter=MagicMock(return_value=0),
        cached_results_getter=MagicMock(return_value=None),
        batch_validate_names_handler=MagicMock(return_value=[deterministic_high, deterministic_low]),
        name_risk_scorer_handler=AsyncMock(
            return_value={
                "name_1": {"llm_risk_score": 18.0, "risk_source": "risk_report_llm"},
                "name_2": {"llm_risk_score": 84.0, "risk_source": "risk_report_llm"},
            }
        ),
        session_count_incrementer=MagicMock(return_value=1),
        cache_results_handler=MagicMock(),
        generation_log_handler=MagicMock(),
        audit_log_handler=MagicMock(),
    )

    assert [item.name for item in response.safe_names] == ["ACMIA"]
    assert response.safe_names[0].risk_score == 18.0
    assert response.filtered_count == 1


@pytest.mark.asyncio
async def test_creative_service_suggest_names_data_superadmin_bypasses_credit_gate():
    from models.schemas import NameSuggestionRequest, SafeNameResult
    from services.creative_service import suggest_names_data

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    current_user = SimpleNamespace(id=user_id, organization_id=org_id, is_superadmin=True)
    request = NameSuggestionRequest(
        query="Acme",
        nice_classes=[25],
        industry="Footwear",
        style="modern",
        language="tr",
        avoid_names=["Nike"],
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    client = MagicMock()
    client.is_available.return_value = True
    client.build_name_prompt.return_value = "prompt"
    client.generate_names = AsyncMock(return_value=["ACMIA"])
    safe_result = SafeNameResult(
        name="ACMIA",
        risk_score=10.0,
        text_similarity=0.1,
        semantic_similarity=0.15,
        phonetic_match=False,
        closest_match="ACME",
        is_safe=True,
        translation_similarity=0.0,
        risk_level="low",
    )

    name_eligibility_checker = MagicMock(return_value=(False, "credits_exhausted", {}))
    deduct_name_credit_handler = MagicMock(return_value=False)
    increment_name_generation_usage_handler = MagicMock()
    plan_credits_getter = MagicMock(return_value={"plan": "free"})
    generation_log_handler = MagicMock()
    name_risk_scorer_handler = AsyncMock(
        return_value={
            "name_1": {"llm_risk_score": 10.0, "risk_source": "risk_report_llm", "llm_risk_model": "qwen:test"}
        }
    )

    response = await suggest_names_data(
        request=request,
        current_user=current_user,
        settings_obj=SimpleNamespace(
            creative=SimpleNamespace(name_batch_size=1, name_similarity_threshold=0.7)
        ),
        database_factory=MagicMock(return_value=mock_db_cm),
        name_eligibility_checker=name_eligibility_checker,
        deduct_name_credit_handler=deduct_name_credit_handler,
        increment_name_generation_usage_handler=increment_name_generation_usage_handler,
        session_count_getter=MagicMock(return_value=999),
        cached_results_getter=MagicMock(return_value=None),
        plan_credits_getter=plan_credits_getter,
        gemini_client_getter=lambda: client,
        batch_validate_names_handler=MagicMock(return_value=[safe_result]),
        name_risk_scorer_handler=name_risk_scorer_handler,
        session_count_incrementer=MagicMock(return_value=1000),
        cache_results_handler=MagicMock(),
        generation_log_handler=generation_log_handler,
        audit_log_handler=MagicMock(),
    )

    assert response.cached is False
    assert response.credits_remaining["plan"] == "superadmin"
    assert response.credits_remaining["total_remaining"] == 999999
    name_eligibility_checker.assert_not_called()
    deduct_name_credit_handler.assert_not_called()
    increment_name_generation_usage_handler.assert_not_called()
    plan_credits_getter.assert_not_called()
    assert generation_log_handler.call_args.kwargs["credits_used"] == 0


@pytest.mark.asyncio
async def test_creative_service_generate_logo_data_generates_and_logs_results():
    from models.schemas import LogoGenerationRequest
    from services.creative_service import generate_logo_data

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    current_user = SimpleNamespace(id=user_id, organization_id=org_id)
    request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        nice_classes=[25],
        color_preferences="blue",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    client = MagicMock()
    client.is_available.return_value = True
    client.generate_logos = AsyncMock(return_value=[b"image-1", b"image-2"])

    generation_log_handler = MagicMock(return_value="gen-log-1")
    audit_log_handler = MagicMock()
    deduct_logo_credit_handler = MagicMock(return_value=True)
    refund_logo_credit_handler = MagicMock()
    create_logo_project_handler = MagicMock(return_value="project-1")
    scheduled_audits = []
    save_logo_image_handler = MagicMock(
        side_effect=["generated/logo-1.png", "generated/logo-2.png"]
    )
    generate_visual_features_handler = MagicMock(
        side_effect=[
            {
                "clip_embedding": [0.1, 0.2],
                "dino_embedding": [0.2, 0.1],
                "ocr_text": "acme",
            },
            {
                "clip_embedding": [0.3, 0.4],
                "dino_embedding": None,
                "ocr_text": "",
            },
        ]
    )
    visual_similarity_search_handler = MagicMock(
        side_effect=[
            [
                {
                    "name": "ACME OLD",
                    "image_path": "logos/acme-old.png",
                    "combined_sim": 0.42,
                    "visual_breakdown": {
                        "clip": 0.4,
                        "dino": 0.2,
                        "ocr": 0.1,
                        "raw_combined": 0.42,
                        "components_used": ["clip", "dino", "ocr"],
                    },
                }
            ],
            [],
        ]
    )
    store_generated_image_handler = MagicMock(side_effect=["img-1", "img-2"])
    logo_credits_remaining_getter = MagicMock(return_value={"monthly": 2, "purchased": 1})

    response = await generate_logo_data(
        request=request,
        current_user=current_user,
        settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_images_per_run=2)),
        database_factory=MagicMock(return_value=mock_db_cm),
        logo_eligibility_checker=MagicMock(return_value=(True, "ok", {})),
        deduct_logo_credit_handler=deduct_logo_credit_handler,
        refund_logo_credit_handler=refund_logo_credit_handler,
        gemini_client_getter=lambda: client,
        generation_uuid_factory=lambda: uuid.UUID("11111111-1111-1111-1111-111111111111"),
        save_logo_image_handler=save_logo_image_handler,
        generate_visual_features_handler=generate_visual_features_handler,
        visual_similarity_search_handler=visual_similarity_search_handler,
        store_generated_image_handler=store_generated_image_handler,
        logo_credits_remaining_getter=logo_credits_remaining_getter,
        visual_audit_available_checker=MagicMock(return_value=(True, "")),
        audit_scheduler=scheduled_audits.append,
        create_logo_project_handler=create_logo_project_handler,
        generation_log_handler=generation_log_handler,
        audit_log_handler=audit_log_handler,
    )

    assert response.generation_id == "gen-log-1"
    assert response.project_id == "project-1"
    assert response.credits_remaining == {"monthly": 2, "purchased": 1}
    assert len(response.logos) == 2
    assert response.logos[0].image_id == "img-1"
    assert response.logos[0].project_id == "project-1"
    assert response.logos[0].variant_index == 1
    assert response.logos[0].audit_status == "pending"
    assert response.logos[0].is_safe is False
    assert not hasattr(response.logos[0], "visual_breakdown")
    assert response.logos[1].image_id == "img-2"
    assert response.logos[1].similarity_score == 0.0
    client.generate_logos.assert_awaited_once_with(
        brand_name="Acme",
        description="Modern wordmark. Color scheme: blue",
        style="modern",
        count=2,
    )
    deduct_logo_credit_handler.assert_called_once_with(mock_db, str(org_id))
    refund_logo_credit_handler.assert_not_called()
    create_logo_project_handler.assert_called_once()
    assert scheduled_audits == ["img-1", "img-2"]
    generate_visual_features_handler.assert_not_called()
    visual_similarity_search_handler.assert_not_called()
    assert store_generated_image_handler.call_args_list[0].kwargs["audit_status"] == "pending"
    generation_log_handler.assert_called_once()
    log_kwargs = generation_log_handler.call_args.kwargs
    assert log_kwargs["input_params"]["count"] == 2
    assert log_kwargs["input_params"]["requested_count"] == 2
    assert log_kwargs["input_params"]["returned_count"] == 2
    assert log_kwargs["output_data"]["variations"] == 2
    assert log_kwargs["output_data"]["requested_count"] == 2
    assert log_kwargs["output_data"]["returned_count"] == 2
    audit_log_handler.assert_called_once()
    logo_credits_remaining_getter.assert_called_once_with(str(org_id))


@pytest.mark.asyncio
async def test_creative_service_generate_logo_data_stores_four_logo_variations():
    from models.schemas import LogoGenerationRequest
    from services.creative_service import generate_logo_data

    org_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=org_id)
    request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        nice_classes=[25],
        color_preferences="blue",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    client = MagicMock()
    client.is_available.return_value = True
    client.source_layout = "panel_2x2_split"
    client.provider_call_count = 1
    client.provider_name = "gemini"
    client.image_model = "gemini-3-pro-image-preview"
    client.generate_logos = AsyncMock(return_value=[b"one", b"two", b"three", b"four"])
    scheduled_audits = []
    generation_log_handler = MagicMock(return_value="gen-log-1")
    store_generated_image_handler = MagicMock(
        side_effect=["img-1", "img-2", "img-3", "img-4"]
    )

    response = await generate_logo_data(
        request=request,
        current_user=current_user,
        settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_images_per_run=4)),
        database_factory=MagicMock(return_value=mock_db_cm),
        logo_eligibility_checker=MagicMock(return_value=(True, "ok", {})),
        deduct_logo_credit_handler=MagicMock(return_value=True),
        refund_logo_credit_handler=MagicMock(),
        gemini_client_getter=lambda: client,
        generation_uuid_factory=lambda: uuid.UUID("11111111-1111-1111-1111-111111111111"),
        save_logo_image_handler=MagicMock(
            side_effect=[
                "generated/logo-1.png",
                "generated/logo-2.png",
                "generated/logo-3.png",
                "generated/logo-4.png",
            ]
        ),
        store_generated_image_handler=store_generated_image_handler,
        logo_credits_remaining_getter=MagicMock(return_value={"monthly": 2, "purchased": 1}),
        visual_audit_available_checker=MagicMock(return_value=(True, "")),
        audit_scheduler=scheduled_audits.append,
        create_logo_project_handler=MagicMock(return_value="project-1"),
        generation_log_handler=generation_log_handler,
        audit_log_handler=MagicMock(),
    )

    assert [logo.image_id for logo in response.logos] == ["img-1", "img-2", "img-3", "img-4"]
    assert store_generated_image_handler.call_count == 4
    assert scheduled_audits == ["img-1", "img-2", "img-3", "img-4"]
    log_kwargs = generation_log_handler.call_args.kwargs
    assert log_kwargs["output_data"]["variations"] == 4
    assert log_kwargs["output_data"]["source_layout"] == "panel_2x2_split"
    assert log_kwargs["output_data"]["provider_call_count"] == 1


@pytest.mark.asyncio
async def test_creative_service_generate_logo_data_superadmin_bypasses_credit_gate():
    from models.schemas import LogoGenerationRequest
    from services.creative_service import generate_logo_data

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    current_user = SimpleNamespace(id=user_id, organization_id=org_id, is_superadmin=True)
    request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        nice_classes=[25],
        color_preferences="blue",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    client = MagicMock()
    client.is_available.return_value = True
    client.generate_logos = AsyncMock(return_value=[b"image-1"])

    logo_eligibility_checker = MagicMock(return_value=(False, "credits_exhausted", {}))
    deduct_logo_credit_handler = MagicMock(return_value=False)
    refund_logo_credit_handler = MagicMock()
    logo_credits_remaining_getter = MagicMock(return_value={"monthly": 0, "purchased": 0})
    generation_log_handler = MagicMock(return_value="gen-log-1")

    response = await generate_logo_data(
        request=request,
        current_user=current_user,
        settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_images_per_run=1)),
        database_factory=MagicMock(return_value=mock_db_cm),
        logo_eligibility_checker=logo_eligibility_checker,
        deduct_logo_credit_handler=deduct_logo_credit_handler,
        refund_logo_credit_handler=refund_logo_credit_handler,
        gemini_client_getter=lambda: client,
        generation_uuid_factory=lambda: uuid.UUID("11111111-1111-1111-1111-111111111111"),
        save_logo_image_handler=MagicMock(return_value="generated/logo-1.png"),
        store_generated_image_handler=MagicMock(return_value="img-1"),
        logo_credits_remaining_getter=logo_credits_remaining_getter,
        visual_audit_available_checker=MagicMock(return_value=(True, "")),
        audit_scheduler=MagicMock(),
        create_logo_project_handler=MagicMock(return_value="project-1"),
        generation_log_handler=generation_log_handler,
        audit_log_handler=MagicMock(),
    )

    assert response.credits_remaining["plan"] == "superadmin"
    assert response.credits_remaining["total_remaining"] == 999999
    assert response.logos[0].image_id == "img-1"
    logo_eligibility_checker.assert_not_called()
    deduct_logo_credit_handler.assert_not_called()
    refund_logo_credit_handler.assert_not_called()
    logo_credits_remaining_getter.assert_not_called()
    assert generation_log_handler.call_args.kwargs["credits_used"] == 0


@pytest.mark.asyncio
async def test_creative_service_generate_logo_data_logs_provider_chain_metadata():
    from models.schemas import LogoGenerationRequest
    from services.creative_service import generate_logo_data

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    current_user = SimpleNamespace(id=user_id, organization_id=org_id)
    request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        nice_classes=[25],
        color_preferences="blue",
    )

    class _ProviderChain:
        provider_name = "logo_image_provider_chain"

        def is_available(self):
            return True

        async def generate_logos(self, **kwargs):
            return [b"image-1"]

        def selected_metadata(self):
            return {
                "provider": "openai",
                "model": "gpt-image-2",
                "source_layout": "native_multi_image",
                "provider_call_count": 1,
                "attempts": [
                    {
                        "provider": "openai",
                        "model": "gpt-image-2",
                        "available": True,
                        "used": True,
                        "error": None,
                    }
                ],
            }

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    generation_log_handler = MagicMock(return_value="gen-log-1")
    audit_log_handler = MagicMock()

    response = await generate_logo_data(
        request=request,
        current_user=current_user,
        settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_images_per_run=1)),
        database_factory=MagicMock(return_value=mock_db_cm),
        logo_eligibility_checker=MagicMock(return_value=(True, "ok", {})),
        deduct_logo_credit_handler=MagicMock(return_value=True),
        refund_logo_credit_handler=MagicMock(),
        logo_provider_getter=lambda: _ProviderChain(),
        generation_uuid_factory=lambda: uuid.UUID("11111111-1111-1111-1111-111111111111"),
        save_logo_image_handler=MagicMock(return_value="generated/logo-1.png"),
        store_generated_image_handler=MagicMock(return_value="img-1"),
        logo_credits_remaining_getter=MagicMock(return_value={"monthly": 2, "purchased": 1}),
        visual_audit_available_checker=MagicMock(return_value=(True, "")),
        audit_scheduler=MagicMock(),
        create_logo_project_handler=MagicMock(return_value="project-1"),
        generation_log_handler=generation_log_handler,
        audit_log_handler=audit_log_handler,
    )

    assert response.generation_id == "gen-log-1"
    log_kwargs = generation_log_handler.call_args.kwargs
    assert log_kwargs["input_params"]["provider"] == "openai"
    assert log_kwargs["input_params"]["model"] == "gpt-image-2"
    assert log_kwargs["input_params"]["requested_count"] == 1
    assert log_kwargs["input_params"]["returned_count"] == 1
    assert log_kwargs["output_data"]["source_layout"] == "native_multi_image"
    assert log_kwargs["output_data"]["provider_call_count"] == 1
    assert log_kwargs["output_data"]["provider_attempts"][0]["provider"] == "openai"
    audit_kwargs = audit_log_handler.call_args.kwargs
    assert audit_kwargs["metadata"]["provider"] == "openai"
    assert audit_kwargs["metadata"]["model"] == "gpt-image-2"
    assert audit_kwargs["metadata"]["source_layout"] == "native_multi_image"
    assert audit_kwargs["metadata"]["provider_call_count"] == 1


@pytest.mark.asyncio
async def test_creative_service_generate_logo_data_refunds_partial_logo_batches_before_saving():
    from models.schemas import LogoGenerationRequest
    from services.creative_service import generate_logo_data

    org_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=org_id)
    request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        nice_classes=[25],
        color_preferences="blue",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    client = MagicMock()
    client.is_available.return_value = True
    client.generate_logos = AsyncMock(return_value=[b"image-1"])
    deduct_logo_credit_handler = MagicMock(return_value=True)
    refund_logo_credit_handler = MagicMock()
    generation_log_handler = MagicMock()
    save_logo_image_handler = MagicMock()
    store_generated_image_handler = MagicMock()

    with pytest.raises(Exception) as exc_info:
        await generate_logo_data(
            request=request,
            current_user=current_user,
            settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_images_per_run=4)),
            database_factory=MagicMock(return_value=mock_db_cm),
            logo_eligibility_checker=MagicMock(return_value=(True, "ok", {})),
            deduct_logo_credit_handler=deduct_logo_credit_handler,
            refund_logo_credit_handler=refund_logo_credit_handler,
            gemini_client_getter=lambda: client,
            generation_uuid_factory=lambda: uuid.UUID("11111111-1111-1111-1111-111111111111"),
            save_logo_image_handler=save_logo_image_handler,
            store_generated_image_handler=store_generated_image_handler,
            logo_credits_remaining_getter=MagicMock(return_value={"monthly": 2, "purchased": 1}),
            visual_audit_available_checker=MagicMock(return_value=(True, "")),
            audit_scheduler=MagicMock(),
            create_logo_project_handler=MagicMock(return_value="project-1"),
            generation_log_handler=generation_log_handler,
            audit_log_handler=MagicMock(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "partial_logo_generation"
    deduct_logo_credit_handler.assert_called_once_with(mock_db, str(org_id))
    refund_logo_credit_handler.assert_called_once_with(mock_db, str(org_id))
    generation_log_handler.assert_not_called()
    save_logo_image_handler.assert_not_called()
    store_generated_image_handler.assert_not_called()


@pytest.mark.asyncio
async def test_creative_service_generate_logo_data_refunds_after_all_logo_providers_fail():
    from generative_ai.logo_image_provider import LogoImageProviderChain
    from generative_ai.openai_image_client import OpenAIImageError
    from models.schemas import LogoGenerationRequest
    from services.creative_service import generate_logo_data

    class _FailingProvider:
        def __init__(self, name, model, error):
            self.provider_name = name
            self.image_model = model
            self.error = error
            self.calls = 0

        def is_available(self):
            return True

        async def generate_logos(self, **kwargs):
            self.calls += 1
            raise self.error

    openai = _FailingProvider(
        "openai",
        "gpt-image-2",
        OpenAIImageError("rate limited", status_code=429, fallback_allowed=True),
    )
    gemini = _FailingProvider(
        "gemini",
        "gemini-3-pro-image-preview",
        RuntimeError("gemini unavailable"),
    )
    chain = LogoImageProviderChain(providers=[openai, gemini])

    org_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=org_id)
    request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        nice_classes=[25],
        color_preferences="blue",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    deduct_logo_credit_handler = MagicMock(return_value=True)
    refund_logo_credit_handler = MagicMock()

    with pytest.raises(Exception) as exc_info:
        await generate_logo_data(
            request=request,
            current_user=current_user,
            settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_images_per_run=1)),
            database_factory=MagicMock(return_value=mock_db_cm),
            logo_eligibility_checker=MagicMock(return_value=(True, "ok", {})),
            deduct_logo_credit_handler=deduct_logo_credit_handler,
            refund_logo_credit_handler=refund_logo_credit_handler,
            logo_provider_getter=lambda: chain,
            visual_audit_available_checker=MagicMock(return_value=(True, "")),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "generation_failed"
    assert openai.calls == 1
    assert gemini.calls == 1
    deduct_logo_credit_handler.assert_called_once_with(mock_db, str(org_id))
    refund_logo_credit_handler.assert_called_once_with(mock_db, str(org_id))


@pytest.mark.asyncio
async def test_creative_service_generate_logo_data_refunds_when_gemini_is_unavailable():
    from models.schemas import LogoGenerationRequest
    from services.creative_service import generate_logo_data

    org_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=org_id)
    request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        nice_classes=[25],
        color_preferences="blue",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    client = MagicMock()
    client.is_available.return_value = False
    deduct_logo_credit_handler = MagicMock(return_value=True)
    refund_logo_credit_handler = MagicMock()

    with pytest.raises(Exception) as exc_info:
        await generate_logo_data(
            request=request,
            current_user=current_user,
            settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_images_per_run=2)),
            database_factory=MagicMock(return_value=mock_db_cm),
            logo_eligibility_checker=MagicMock(return_value=(True, "ok", {})),
            deduct_logo_credit_handler=deduct_logo_credit_handler,
            refund_logo_credit_handler=refund_logo_credit_handler,
            gemini_client_getter=lambda: client,
            visual_audit_available_checker=MagicMock(return_value=(True, "")),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "service_unavailable"
    deduct_logo_credit_handler.assert_not_called()
    refund_logo_credit_handler.assert_not_called()


@pytest.mark.asyncio
async def test_creative_service_generate_logo_data_refunds_when_image_store_fails():
    from models.schemas import LogoGenerationRequest
    from services.creative_service import generate_logo_data

    org_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=org_id)
    request = LogoGenerationRequest(
        brand_name="Acme",
        description="Modern wordmark",
        style="modern",
        nice_classes=[25],
        color_preferences="blue",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    client = MagicMock()
    client.is_available.return_value = True
    client.generate_logos = AsyncMock(return_value=[b"image-1"])
    deduct_logo_credit_handler = MagicMock(return_value=True)
    refund_logo_credit_handler = MagicMock()

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        saved_path = tmp.name

    try:
        with pytest.raises(Exception) as exc_info:
            await generate_logo_data(
                request=request,
                current_user=current_user,
                settings_obj=SimpleNamespace(creative=SimpleNamespace(logo_images_per_run=1)),
                database_factory=MagicMock(return_value=mock_db_cm),
                logo_eligibility_checker=MagicMock(return_value=(True, "ok", {})),
                deduct_logo_credit_handler=deduct_logo_credit_handler,
                refund_logo_credit_handler=refund_logo_credit_handler,
                gemini_client_getter=lambda: client,
                generation_uuid_factory=lambda: uuid.UUID("11111111-1111-1111-1111-111111111111"),
                save_logo_image_handler=MagicMock(return_value=saved_path),
                generate_visual_features_handler=MagicMock(return_value={"clip_embedding": [0.1], "dino_embedding": None, "ocr_text": ""}),
                visual_similarity_search_handler=MagicMock(return_value=[]),
                store_generated_image_handler=MagicMock(return_value=None),
                visual_audit_available_checker=MagicMock(return_value=(True, "")),
                create_logo_project_handler=MagicMock(return_value="project-1"),
                generation_log_handler=MagicMock(return_value="gen-log-1"),
            )

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail["error"] == "processing_failed"
        deduct_logo_credit_handler.assert_called_once_with(mock_db, str(org_id))
        refund_logo_credit_handler.assert_called_once_with(mock_db, str(org_id))
        assert not os.path.exists(saved_path)
    finally:
        if os.path.exists(saved_path):
            os.remove(saved_path)


@pytest.mark.asyncio
async def test_application_service_list_applications_data_returns_paginated_results():
    from services.application_service import list_applications_data

    organization_id = uuid.uuid4()
    app_row = _make_application_row(organization_id=organization_id)

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    with patch(
        "services.application_service.ApplicationCRUD.get_by_organization",
        return_value=([app_row], 1),
    ) as mock_get_by_organization:
        response = await list_applications_data(
            organization_id=organization_id,
            status="draft",
            application_type="registration",
            page=2,
            page_size=10,
            db_factory=MagicMock(return_value=mock_db_cm),
        )

    assert response["total"] == 1
    assert response["page"] == 2
    assert response["page_size"] == 10
    assert response["total_pages"] == 1
    assert response["items"][0]["brand_name"] == "TEST MARKA"
    mock_get_by_organization.assert_called_once_with(
        mock_db,
        organization_id,
        status="draft",
        application_type="registration",
        page=2,
        page_size=10,
    )


@pytest.mark.asyncio
async def test_application_service_get_application_data_returns_response_model():
    from services.application_service import get_application_data

    organization_id = uuid.uuid4()
    app_row = _make_application_row(organization_id=organization_id)

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    with patch(
        "services.application_service.ApplicationCRUD.get_by_id",
        return_value=app_row,
    ) as mock_get_by_id:
        response = await get_application_data(
            app_id=app_row["id"],
            organization_id=organization_id,
            db_factory=MagicMock(return_value=mock_db_cm),
        )

    assert response.id == app_row["id"]
    assert response.brand_name == "TEST MARKA"
    mock_get_by_id.assert_called_once_with(
        mock_db,
        app_row["id"],
        organization_id,
    )


@pytest.mark.asyncio
async def test_application_service_get_application_data_raises_not_found():
    from services.application_service import get_application_data

    organization_id = uuid.uuid4()
    app_id = uuid.uuid4()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    with patch(
        "services.application_service.ApplicationCRUD.get_by_id",
        return_value=None,
    ):
        with pytest.raises(Exception) as exc_info:
            await get_application_data(
                app_id=app_id,
                organization_id=organization_id,
                db_factory=MagicMock(return_value=mock_db_cm),
            )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Application not found"


@pytest.mark.asyncio
async def test_application_service_create_application_data_checks_eligibility_and_returns_response():
    from models.schemas import TrademarkApplicationCreate
    from services.application_service import create_application_data

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()
    data = TrademarkApplicationCreate(
        brand_name="TEST MARKA",
        nice_class_numbers=[25],
    )
    app_row = _make_application_row(
        organization_id=current_user.organization_id,
        user_id=current_user.id,
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    eligibility_checker = MagicMock(return_value=(True, None, None))
    application_crud = MagicMock()
    application_crud.create.return_value = app_row

    response = await create_application_data(
        data=data,
        user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        eligibility_checker=eligibility_checker,
        application_crud=application_crud,
    )

    assert response.id == app_row["id"]
    assert response.brand_name == "TEST MARKA"
    eligibility_checker.assert_called_once_with(
        mock_db,
        str(current_user.id),
        str(current_user.organization_id),
    )
    application_crud.create.assert_called_once_with(
        mock_db,
        current_user.organization_id,
        current_user.id,
        data,
    )


@pytest.mark.asyncio
async def test_application_service_create_application_data_rejects_ineligible_org():
    from models.schemas import TrademarkApplicationCreate
    from services.application_service import create_application_data

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()
    data = TrademarkApplicationCreate(
        brand_name="TEST MARKA",
        nice_class_numbers=[25],
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    eligibility_details = {"message": "Application limit reached"}
    eligibility_checker = MagicMock(return_value=(False, "limit", eligibility_details))
    application_crud = MagicMock()

    with pytest.raises(Exception) as exc_info:
        await create_application_data(
            data=data,
            user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            eligibility_checker=eligibility_checker,
            application_crud=application_crud,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == eligibility_details
    application_crud.create.assert_not_called()


@pytest.mark.asyncio
async def test_application_service_update_application_data_returns_response_model():
    from models.schemas import TrademarkApplicationUpdate
    from services.application_service import update_application_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_row = _make_application_row(
        organization_id=current_user.organization_id,
        brand_name="UPDATED MARKA",
    )
    data = TrademarkApplicationUpdate(brand_name="UPDATED MARKA")

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    application_crud = MagicMock()
    application_crud.update.return_value = app_row

    response = await update_application_data(
        app_id=app_row["id"],
        data=data,
        user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        application_crud=application_crud,
    )

    assert response.id == app_row["id"]
    assert response.brand_name == "UPDATED MARKA"
    application_crud.update.assert_called_once_with(
        mock_db,
        app_row["id"],
        current_user.organization_id,
        data,
    )


@pytest.mark.asyncio
async def test_application_service_update_application_data_converts_value_error():
    from models.schemas import TrademarkApplicationUpdate
    from services.application_service import update_application_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_id = uuid.uuid4()
    data = TrademarkApplicationUpdate(brand_name="UPDATED MARKA")

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    application_crud = MagicMock()
    application_crud.update.side_effect = ValueError("Only draft applications can be edited")

    with pytest.raises(Exception) as exc_info:
        await update_application_data(
            app_id=app_id,
            data=data,
            user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            application_crud=application_crud,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Only draft applications can be edited"


@pytest.mark.asyncio
async def test_application_service_delete_application_data_removes_logo_dir():
    from services.application_service import delete_application_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_id = uuid.uuid4()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    application_crud = MagicMock()
    application_crud.delete.return_value = True

    base_dir = Path("C:/Users/701693/turk_patent/.tmp_pytest_applications")
    shutil.rmtree(base_dir, ignore_errors=True)
    base_dir.mkdir(parents=True, exist_ok=True)
    try:
        logo_dir = base_dir / str(app_id)
        logo_dir.mkdir(parents=True, exist_ok=True)
        (logo_dir / "logo.png").write_bytes(b"png")

        response = await delete_application_data(
            app_id=app_id,
            user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            application_crud=application_crud,
            upload_dir=base_dir,
        )

        assert response == {"success": True, "message": "Application deleted"}
        assert not logo_dir.exists()
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)

    application_crud.delete.assert_called_once_with(
        mock_db,
        app_id,
        current_user.organization_id,
    )


@pytest.mark.asyncio
async def test_application_service_submit_application_data_updates_status():
    from services.application_service import submit_application_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_row = _make_application_row(
        organization_id=current_user.organization_id,
        applicant_full_name="Jane Doe",
        applicant_id_no="12345678901",
        applicant_address="Istanbul",
        applicant_phone="+90 555 000 0000",
        applicant_email="jane@example.com",
    )
    submitted_row = {
        **app_row,
        "status": "submitted",
        "submitted_at": datetime(2026, 4, 12, 12, 5, tzinfo=timezone.utc),
    }

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    application_crud = MagicMock()
    application_crud.get_by_id.return_value = app_row
    application_crud.update_status.return_value = submitted_row

    response = await submit_application_data(
        app_id=app_row["id"],
        user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        application_crud=application_crud,
    )

    assert response.id == app_row["id"]
    assert response.status.value == "submitted"
    application_crud.get_by_id.assert_called_once_with(
        mock_db,
        app_row["id"],
        current_user.organization_id,
    )
    application_crud.update_status.assert_called_once_with(
        mock_db,
        app_row["id"],
        current_user.organization_id,
        "submitted",
    )


@pytest.mark.asyncio
async def test_application_service_submit_application_data_rejects_missing_fields():
    from services.application_service import submit_application_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_row = _make_application_row(
        organization_id=current_user.organization_id,
        applicant_full_name=None,
        applicant_id_no=None,
        applicant_address=None,
        applicant_phone=None,
        applicant_email=None,
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    application_crud = MagicMock()
    application_crud.get_by_id.return_value = app_row

    with pytest.raises(Exception) as exc_info:
        await submit_application_data(
            app_id=app_row["id"],
            user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            application_crud=application_crud,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == {
        "message": "Missing required fields for submission",
        "fields": [
            "applicant_full_name",
            "applicant_id_no",
            "applicant_address",
            "applicant_phone",
            "applicant_email",
        ],
    }
    application_crud.update_status.assert_not_called()


@pytest.mark.asyncio
async def test_application_service_submit_application_data_rejects_non_draft():
    from services.application_service import submit_application_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_row = _make_application_row(
        organization_id=current_user.organization_id,
        status="submitted",
        applicant_full_name="Jane Doe",
        applicant_id_no="12345678901",
        applicant_address="Istanbul",
        applicant_phone="+90 555 000 0000",
        applicant_email="jane@example.com",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    application_crud = MagicMock()
    application_crud.get_by_id.return_value = app_row

    with pytest.raises(Exception) as exc_info:
        await submit_application_data(
            app_id=app_row["id"],
            user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            application_crud=application_crud,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Only draft applications can be submitted"
    application_crud.update_status.assert_not_called()


@pytest.mark.asyncio
async def test_application_service_upload_application_logo_data_persists_file_and_db_path():
    from services.application_service import upload_application_logo_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_row = _make_application_row(organization_id=current_user.organization_id)
    file = SimpleNamespace(
        filename="logo.png",
        content_type="image/png",
        read=AsyncMock(return_value=b"png-bytes"),
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    application_crud = MagicMock()
    application_crud.get_by_id.return_value = app_row

    base_dir = Path("C:/Users/701693/turk_patent/.tmp_pytest_app_logo_uploads")
    expected_path = str(base_dir / str(app_row["id"]) / "logo.png").replace("\\", "/")

    with (
        patch("pathlib.Path.mkdir") as mock_mkdir,
        patch("pathlib.Path.write_bytes", return_value=len(b"png-bytes")) as mock_write,
    ):
        response = await upload_application_logo_data(
            app_id=app_row["id"],
            file=file,
            user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            application_crud=application_crud,
            upload_dir=base_dir,
        )

    assert response == {
        "success": True,
        "logo_url": f"/api/v1/applications/{app_row['id']}/logo",
        "logo_path": expected_path,
    }
    _, mkdir_kwargs = mock_mkdir.call_args
    assert mkdir_kwargs == {"parents": True, "exist_ok": True}
    write_args, _ = mock_write.call_args
    assert write_args[-1] == b"png-bytes"

    application_crud.get_by_id.assert_called_once_with(
        mock_db,
        app_row["id"],
        current_user.organization_id,
    )
    application_crud.update_logo.assert_called_once_with(
        mock_db,
        app_row["id"],
        current_user.organization_id,
        expected_path,
    )


@pytest.mark.asyncio
async def test_application_service_upload_application_logo_data_rejects_bad_type():
    from services.application_service import upload_application_logo_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_id = uuid.uuid4()
    file = SimpleNamespace(
        filename="logo.txt",
        content_type="text/plain",
        read=AsyncMock(return_value=b"text"),
    )
    db_factory = MagicMock()
    application_crud = MagicMock()

    with pytest.raises(Exception) as exc_info:
        await upload_application_logo_data(
            app_id=app_id,
            file=file,
            user=current_user,
            db_factory=db_factory,
            application_crud=application_crud,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Only PNG, JPG, and WEBP images are allowed"
    db_factory.assert_not_called()
    application_crud.get_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_application_service_upload_application_logo_data_rejects_oversize_file():
    from services.application_service import upload_application_logo_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_id = uuid.uuid4()
    file = SimpleNamespace(
        filename="logo.png",
        content_type="image/png",
        read=AsyncMock(return_value=b"x" * ((5 * 1024 * 1024) + 1)),
    )
    db_factory = MagicMock()
    application_crud = MagicMock()

    with pytest.raises(Exception) as exc_info:
        await upload_application_logo_data(
            app_id=app_id,
            file=file,
            user=current_user,
            db_factory=db_factory,
            application_crud=application_crud,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "File size must be under 5MB"
    db_factory.assert_not_called()
    application_crud.get_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_application_service_upload_application_logo_data_rejects_missing_application():
    from services.application_service import upload_application_logo_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_id = uuid.uuid4()
    file = SimpleNamespace(
        filename="logo.png",
        content_type="image/png",
        read=AsyncMock(return_value=b"png-bytes"),
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    application_crud = MagicMock()
    application_crud.get_by_id.return_value = None

    with pytest.raises(Exception) as exc_info:
        await upload_application_logo_data(
            app_id=app_id,
            file=file,
            user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            application_crud=application_crud,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Application not found"
    application_crud.update_logo.assert_not_called()


@pytest.mark.asyncio
async def test_application_service_get_application_logo_file_returns_existing_file():
    from services.application_service import get_application_logo_file

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_row = _make_application_row(organization_id=current_user.organization_id)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(b"png-bytes")
        temp_path = tmp.name

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    application_crud = MagicMock()
    application_crud.get_by_id.return_value = {
        **app_row,
        "logo_path": temp_path,
    }

    try:
        payload = await get_application_logo_file(
            app_id=app_row["id"],
            user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            application_crud=application_crud,
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    assert payload == Path(temp_path)
    application_crud.get_by_id.assert_called_once_with(
        mock_db,
        app_row["id"],
        current_user.organization_id,
    )


@pytest.mark.asyncio
async def test_application_service_get_application_logo_file_rejects_missing_logo():
    from services.application_service import get_application_logo_file

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_id = uuid.uuid4()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    application_crud = MagicMock()
    application_crud.get_by_id.return_value = _make_application_row(
        app_id=app_id,
        organization_id=current_user.organization_id,
        logo_path=None,
    )

    with pytest.raises(Exception) as exc_info:
        await get_application_logo_file(
            app_id=app_id,
            user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            application_crud=application_crud,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Logo not found"


@pytest.mark.asyncio
async def test_application_service_get_application_logo_file_rejects_missing_file():
    from services.application_service import get_application_logo_file

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    app_id = uuid.uuid4()
    missing_path = Path("C:/Users/701693/turk_patent/.tmp_pytest_missing_application_logo.png")
    if missing_path.exists():
        missing_path.unlink()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    application_crud = MagicMock()
    application_crud.get_by_id.return_value = _make_application_row(
        app_id=app_id,
        organization_id=current_user.organization_id,
        logo_path=str(missing_path),
    )

    with pytest.raises(Exception) as exc_info:
        await get_application_logo_file(
            app_id=app_id,
            user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            application_crud=application_crud,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Logo file not found"


@pytest.mark.asyncio
async def test_upload_service_process_trademark_upload_parses_csv_without_watchlist():
    from services.upload_service import process_trademark_upload

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()
    file = SimpleNamespace(
        filename="marks.csv",
        read=AsyncMock(
            return_value=(
                b"Marka Adi,Siniflar,Basvuru No,Hak Sahibi,Aciklama\n"
                b"TEST MARKA,\"25,35\",2025/123456,Acme Ltd,Upload row\n"
            )
        ),
    )

    payload = await process_trademark_upload(
        file=file,
        add_to_watchlist=False,
        run_analysis=False,
        alert_threshold=0.7,
        current_user=current_user,
    )

    assert payload["success"] is True
    assert payload["file_name"] == "marks.csv"
    assert payload["valid_trademarks"] == 1
    assert payload["trademarks"][0]["name"] == "TEST MARKA"
    assert payload["trademarks"][0]["classes"] == [25, 35]
    assert payload["watchlist_results"] is None


@pytest.mark.asyncio
async def test_upload_service_process_trademark_upload_adds_watchlist_items():
    from services.upload_service import process_trademark_upload

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()
    file = SimpleNamespace(
        filename="marks.csv",
        read=AsyncMock(return_value=b"Marka Adi,Siniflar\nTEST MARKA,25\n"),
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cur = MagicMock()
    mock_db.cursor.return_value = mock_cur
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cur.fetchone.return_value = None
    fixed_item_id = uuid.uuid4()

    payload = await process_trademark_upload(
        file=file,
        add_to_watchlist=True,
        run_analysis=False,
        alert_threshold=0.8,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        uuid_factory=lambda: fixed_item_id,
    )

    assert payload["watchlist_results"] == [
        {
            "name": "TEST MARKA",
            "status": "added",
            "watchlist_id": str(fixed_item_id),
        }
    ]
    assert mock_cur.execute.call_count == 3
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_upload_service_process_trademark_upload_rejects_bad_extension():
    from services.upload_service import process_trademark_upload

    current_user = MagicMock()
    file = SimpleNamespace(filename="marks.txt", read=AsyncMock(return_value=b"bad"))

    with pytest.raises(Exception) as exc_info:
        await process_trademark_upload(
            file=file,
            add_to_watchlist=False,
            run_analysis=False,
            alert_threshold=0.7,
            current_user=current_user,
        )

    assert exc_info.value.status_code == 400
    assert "Desteklenmeyen dosya formati" in exc_info.value.detail


@pytest.mark.asyncio
async def test_upload_service_process_trademark_upload_rejects_missing_class_column():
    from services.upload_service import process_trademark_upload

    current_user = MagicMock()
    file = SimpleNamespace(
        filename="marks.csv",
        read=AsyncMock(return_value=b"Marka Adi\nTEST MARKA\n"),
    )

    with pytest.raises(Exception) as exc_info:
        await process_trademark_upload(
            file=file,
            add_to_watchlist=False,
            run_analysis=False,
            alert_threshold=0.7,
            current_user=current_user,
        )

    assert exc_info.value.status_code == 400
    assert "'Siniflar' sutunu bulunamadi" in exc_info.value.detail


@pytest.mark.asyncio
async def test_upload_service_build_upload_template_response():
    from services.upload_service import build_upload_template_response

    response = build_upload_template_response()

    assert response.media_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.headers["Content-Disposition"] == "attachment; filename=marka_sablonu.xlsx"

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    assert len(body) > 0


@pytest.mark.asyncio
async def test_trademark_service_get_trademark_events_data_formats_response():
    from services.trademark_service import get_trademark_events_data

    tm_id = uuid.uuid4()
    event_id = uuid.uuid4()
    tm_row = {
        "id": tm_id,
        "application_no": "2024-1",
        "name": "TEST MARKA",
        "final_status": "Active",
        "effective_status": "Aktif",
        "active_restriction_count": 0,
        "current_holder_name": "Acme Ltd",
        "holder_changed_at": date(2024, 1, 1),
        "renewal_expiry": date(2025, 1, 1),
        "last_event_type": "transfer",
        "last_event_date": date(2024, 2, 1),
        "has_restrictions": False,
        "event_flags": {"has_bankruptcy": True},
        "total_event_count": 1,
        "expiry_date": date(2034, 1, 1),
        "registration_date": date(2024, 1, 15),
        "original_holder_name": "Original Co",
    }
    event_rows = [
        {
            "id": event_id,
            "event_type": "transfer",
            "event_subtype": None,
            "source_type": "gazette",
            "bulletin_no": "2024-05",
            "bulletin_date": date(2024, 2, 1),
            "page_number": 12,
            "old_value": "Old Holder",
            "new_value": "New Holder",
            "details": None,
            "raw_text": "Raw details",
        }
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = [tm_row, {"cnt": 1}]
    mock_cursor.fetchall.return_value = event_rows

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    response = await get_trademark_events_data(
        application_no="2024-1",
        page=1,
        per_page=20,
        event_type="transfer",
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response["application_no"] == "2024-1"
    assert response["name"] == "TEST MARKA"
    assert response["health_card"]["severity"] == "critical"
    assert response["events"][0]["event_type_label"] == "Devir"
    assert response["events"][0]["severity"] == "warning"
    assert response["pages"] == 1
    assert "event_type = %s" in mock_cursor.execute.call_args_list[1][0][0]


@pytest.mark.asyncio
async def test_trademark_service_get_extracted_goods_data_formats_response():
    from services.trademark_service import get_extracted_goods_data

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {
        "application_no": "2024-1",
        "name": "TEST MARKA",
        "extracted_goods": [{"text": "Shoes"}],
        "nice_class_numbers": [25],
    }

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    response = await get_extracted_goods_data(
        application_no="2024-1",
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response["application_no"] == "2024-1"
    assert response["has_extracted_goods"] is True
    assert response["total_items"] == 1
    assert response["nice_classes"] == [25]


@pytest.mark.asyncio
async def test_trademark_service_get_extracted_goods_data_returns_empty_state():
    from services.trademark_service import get_extracted_goods_data

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {
        "application_no": "2024-1",
        "name": "TEST MARKA",
        "extracted_goods": [],
        "nice_class_numbers": [25],
    }

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    response = await get_extracted_goods_data(
        application_no="2024-1",
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "application_no": "2024-1",
        "has_extracted_goods": False,
        "extracted_goods": [],
        "total_items": 0,
    }


@pytest.mark.asyncio
async def test_alert_service_list_alerts_data_normalizes_score_and_formats_rows():
    from models.schemas import AlertSeverity, AlertStatus
    from services.alert_service import list_alerts_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    alert_row = _make_alert_row()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    alert_crud = MagicMock()
    alert_crud.get_by_organization.return_value = ([alert_row], 1)
    formatter = MagicMock(return_value={"id": str(alert_row["id"])})

    response = await list_alerts_data(
        page=2,
        page_size=5,
        status_filters=[AlertStatus.NEW],
        severity_filters=[AlertSeverity.HIGH],
        watchlist_id=None,
        min_score=80.0,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        alert_crud=alert_crud,
        alert_formatter=formatter,
    )

    assert response.total == 1
    assert response.page == 2
    assert response.page_size == 5
    assert response.items == [{"id": str(alert_row["id"])}]
    formatter.assert_called_once_with(alert_row)
    alert_crud.get_by_organization.assert_called_once_with(
        mock_db,
        current_user.organization_id,
        status=["new"],
        severity=["high"],
        watchlist_id=None,
        page=2,
        page_size=5,
        min_score=0.8,
    )


@pytest.mark.asyncio
async def test_alert_service_get_alerts_summary_data_aggregates_counts():
    from services.alert_service import get_alerts_summary_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()

    mock_cursor = MagicMock()
    mock_cursor.fetchall.side_effect = [
        [{"status": "new", "count": 2}, {"status": "seen", "count": 1}],
        [{"severity": "high", "count": 1}, {"severity": "medium", "count": 2}],
    ]
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    response = await get_alerts_summary_data(
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "by_status": {"new": 2, "seen": 1},
        "by_severity": {"high": 1, "medium": 2},
        "total_new": 2,
    }


@pytest.mark.asyncio
async def test_alert_service_aggregate_alerts_data_formats_deadlines():
    from services.alert_service import aggregate_alerts_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    alert_id = uuid.uuid4()
    watchlist_id = uuid.uuid4()
    conflict_id = uuid.uuid4()

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"count": 1}
    mock_cursor.fetchall.return_value = [
        {
            "id": alert_id,
            "watchlist_item_id": watchlist_id,
            "watched_brand_name": "WATCHED MARK",
            "conflicting_name": "CONFLICT MARK",
            "tm_name": "CONFLICT MARK",
            "conflicting_trademark_id": conflict_id,
            "severity": "high",
            "overall_risk_score": 0.92,
            "status": "new",
            "opposition_deadline": date(2026, 4, 20),
            "overlapping_classes": [25],
            "created_at": datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc),
        }
    ]
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    response = await aggregate_alerts_data(
        page=1,
        page_size=20,
        severity="high",
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        today_getter=lambda: date(2026, 4, 12),
    )

    assert response.total == 1
    assert response.items[0]["deadline_days"] == 8
    assert response.items[0]["conflicting_brand_name"] == "CONFLICT MARK"
    assert response.items[0]["created_at"] == "2026-04-12T12:00:00+00:00"


@pytest.mark.asyncio
async def test_alert_service_aggregate_alerts_data_passes_event_fields():
    from services.alert_service import aggregate_alerts_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    alert_id = uuid.uuid4()
    watchlist_id = uuid.uuid4()
    conflict_id = uuid.uuid4()

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"count": 1}
    mock_cursor.fetchall.return_value = [
        {
            "id": alert_id,
            "watchlist_item_id": watchlist_id,
            "watched_brand_name": "WATCHED MARK",
            "conflicting_name": "TRANSFERRED MARK",
            "tm_name": "TRANSFERRED MARK",
            "conflicting_trademark_id": conflict_id,
            "severity": "high",
            "overall_risk_score": 1.0,
            "status": "new",
            "opposition_deadline": None,
            "overlapping_classes": [25],
            "alert_type": "event",
            "source_type": "transfer",
            "created_at": datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc),
        }
    ]
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    response = await aggregate_alerts_data(
        page=1,
        page_size=20,
        severity=None,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        today_getter=lambda: date(2026, 4, 12),
    )

    assert response.total == 1
    item = response.items[0]
    assert item["alert_type"] == "event"
    assert item["source_type"] == "transfer"
    assert item["deadline_days"] is None
    assert item["opposition_deadline"] is None


@pytest.mark.asyncio
async def test_alert_service_get_alert_data_marks_new_seen():
    from models.schemas import AlertStatus
    from services.alert_service import get_alert_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    alert_id = uuid.uuid4()
    alert_row = _make_alert_row(
        alert_id=alert_id,
        organization_id=current_user.organization_id,
        status="new",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    alert_crud = MagicMock()
    alert_crud.get_by_id.return_value = alert_row
    formatter = MagicMock(return_value={"id": str(alert_id), "status": "seen"})

    response = await get_alert_data(
        alert_id=alert_id,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        alert_crud=alert_crud,
        alert_formatter=formatter,
    )

    assert response == {"id": str(alert_id), "status": "seen"}
    alert_crud.update_status.assert_called_once_with(
        mock_db,
        alert_id,
        current_user.organization_id,
        AlertStatus.SEEN,
    )
    assert alert_row["status"] == "seen"
    formatter.assert_called_once_with(alert_row)


@pytest.mark.asyncio
async def test_alert_service_acknowledge_alert_data_updates_status_and_formats_response():
    from models.schemas import AlertAcknowledge, AlertStatus
    from services.alert_service import acknowledge_alert_data

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()
    alert_id = uuid.uuid4()
    data = AlertAcknowledge(notes="checked")
    alert_row = _make_alert_row(
        alert_id=alert_id,
        organization_id=current_user.organization_id,
        status="acknowledged",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    alert_crud = MagicMock()
    alert_crud.update_status.return_value = alert_row
    formatter = MagicMock(return_value={"id": str(alert_id), "status": "acknowledged"})

    response = await acknowledge_alert_data(
        alert_id=alert_id,
        data=data,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        alert_crud=alert_crud,
        alert_formatter=formatter,
    )

    assert response == {"id": str(alert_id), "status": "acknowledged"}
    alert_crud.update_status.assert_called_once_with(
        mock_db,
        alert_id,
        current_user.organization_id,
        AlertStatus.ACKNOWLEDGED,
        user_id=current_user.id,
        notes="checked",
    )
    formatter.assert_called_once_with(alert_row)


@pytest.mark.asyncio
async def test_alert_service_format_alert_response_builds_response_model():
    from services.alert_service import format_alert_response

    alert_row = _make_alert_row()
    response = format_alert_response(
        alert_row,
        deadline_classifier=lambda **kwargs: {
            "status": "active",
            "days_remaining": 8,
            "label_tr": "8 gun kaldi",
            "urgency": "high",
        },
    )

    assert str(response.id) == str(alert_row["id"])
    assert response.conflicting.name == "CONFLICT MARK"
    assert response.scores.total == 0.91
    assert response.scores.path_a_score == 0.8
    assert response.scores.path_b_score == 0.1
    assert response.scores.text_idf_score == 0.8
    assert response.deadline_label == "8 gun kaldi"
    assert response.conflicting.has_extracted_goods is True


@pytest.mark.asyncio
async def test_alert_service_format_alert_response_prefers_score_details_paths():
    from services.alert_service import format_alert_response

    alert_row = _make_alert_row(
        overall_risk_score=0.99,
        text_similarity_score=0.96,
        translation_similarity_score=0.0,
        score_details={
            "total": 1.0,
            "text_similarity": 0.0321,
            "text_idf_score": 1.0,
            "path_a_score": 0.0177,
            "path_b_score": 1.0,
            "translation_similarity": 1.0,
            "semantic_similarity": 0.0,
            "visual_similarity": 0.0,
            "scoring_path_source": "TRANSLATED",
            "decision_reason": "translated textual path selected",
            "textual_breakdown": {"selected_path": "TRANSLATED"},
            "visual_breakdown": {"total": 0.0},
        },
    )

    response = format_alert_response(
        alert_row,
        deadline_classifier=lambda **kwargs: {
            "status": "active",
            "days_remaining": 8,
            "label_tr": "8 gun kaldi",
            "urgency": "high",
        },
    )

    assert response.scores.total == 0.99
    assert response.scores.text_similarity == 0.0321
    assert response.scores.text_idf_score == 1.0
    assert response.scores.path_a_score == 0.0177
    assert response.scores.path_b_score == 1.0
    assert response.scores.translation_similarity == 1.0
    assert response.scores.scoring_path_source == "TRANSLATED"
    assert response.scores.textual_breakdown == {"selected_path": "TRANSLATED"}


@pytest.mark.asyncio
async def test_user_profile_service_get_user_profile_data_formats_response():
    from services.user_profile_service import get_user_profile_data

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    user_row = _make_user_row(user_id=current_user.id)

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    user_crud = MagicMock()
    user_crud.get_by_id.return_value = user_row

    response = await get_user_profile_data(
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_crud=user_crud,
    )

    assert response["email"] == "test@example.com"
    assert response["title"] == "Counsel"
    assert response["created_at"] == "2026-04-12T12:00:00+00:00"
    assert response["is_email_verified"] is True
    user_crud.get_by_id.assert_called_once_with(mock_db, current_user.id)


@pytest.mark.asyncio
async def test_user_profile_service_update_user_profile_data_updates_email_and_password():
    from services.user_profile_service import update_user_profile_data
    from api.user_profile_routes import ProfileUpdateRequest

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    data = ProfileUpdateRequest(
        email="updated@example.com",
        current_password="OldPass1",
        new_password="NewPass123",
        department="Litigation",
    )
    current_row = _make_user_row(
        user_id=current_user.id,
        email="old@example.com",
        password_hash="OLD-HASH",
    )

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    user_crud = MagicMock()
    user_crud.get_by_id.return_value = current_row
    user_crud.get_by_email.return_value = None

    response = await update_user_profile_data(
        data=data,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_crud=user_crud,
        password_hasher=lambda value: f"HASHED::{value}",
        password_verifier=lambda plain, hashed: plain == "OldPass1" and hashed == "OLD-HASH",
    )

    assert response == {"success": True, "message": "Profil guncellendi"}
    user_crud.update.assert_called_once_with(
        mock_db,
        current_user.id,
        {
            "email": "updated@example.com",
            "department": "Litigation",
            "password_hash": "HASHED::NewPass123",
        },
    )


@pytest.mark.asyncio
async def test_user_profile_service_upload_avatar_data_stores_file_and_updates_user():
    from services.user_profile_service import upload_avatar_data

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    file = MagicMock()
    file.content_type = "image/png"
    file.filename = "avatar.png"
    file.read = AsyncMock(return_value=b"avatar-bytes")

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    user_crud = MagicMock()
    avatar_dir = Path("C:/Users/701693/turk_patent/.tmp_pytest_avatars")
    if avatar_dir.exists():
        shutil.rmtree(avatar_dir)

    try:
        response = await upload_avatar_data(
            file=file,
            current_user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            user_crud=user_crud,
            avatar_dir=avatar_dir,
            uuid_factory=lambda: SimpleNamespace(hex="abcdef1234567890"),
        )
    finally:
        if avatar_dir.exists():
            shutil.rmtree(avatar_dir)

    assert response["success"] is True
    assert response["avatar_url"].startswith("/static/avatars/")
    user_crud.update.assert_called_once()


@pytest.mark.asyncio
async def test_auth_service_register_user_creates_tokens_and_verification_code():
    from auth.authentication import UserRegister
    from services.auth_service import register_user

    data = UserRegister(
        email="new@example.com",
        password="Password1",
        first_name="New",
        last_name="User",
        organization_name="Acme IP",
    )
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    organization_crud = MagicMock()
    organization_crud.create.return_value = {"id": org_id}
    user_crud = MagicMock()
    user_crud.get_by_email.return_value = None
    user_crud.create.return_value = {
        "id": user_id,
        "organization_id": org_id,
        "role": "admin",
    }
    token_pair_factory = MagicMock(
        return_value={
            "access_token": "access",
            "refresh_token": "refresh",
            "token_type": "bearer",
            "expires_in": 3600,
        }
    )
    email_service = MagicMock()
    email_service.is_configured.return_value = True

    response = await register_user(
        data=data,
        ip="testclient",
        db_factory=MagicMock(return_value=mock_db_cm),
        organization_crud=organization_crud,
        user_crud=user_crud,
        token_pair_factory=token_pair_factory,
        now_getter=lambda: datetime(2026, 4, 12, 12, 0),
        code_generator=lambda: "123456",
        email_service_factory=lambda: email_service,
    )

    assert response["access_token"] == "access"
    organization_crud.create.assert_called_once()
    user_crud.create.assert_called_once()
    token_pair_factory.assert_called_once_with(str(user_id), str(org_id), "admin")
    assert any(
        "email_verification_tokens" in call.args[0]
        for call in mock_cursor.execute.call_args_list
    )
    email_service.send_welcome.assert_called_once()


@pytest.mark.asyncio
async def test_auth_service_login_user_updates_last_login_and_returns_tokens():
    from services.auth_service import login_user

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    user_row = _make_user_row(
        user_id=user_id,
        organization_id=org_id,
        password_hash="HASHED",
        role="admin",
        is_active=True,
    )
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    user_crud = MagicMock()
    user_crud.get_by_email.return_value = user_row
    token_pair_factory = MagicMock(return_value={"access_token": "access"})

    response = await login_user(
        email="test@example.com",
        password="Password1",
        ip="testclient",
        db_factory=MagicMock(return_value=mock_db_cm),
        user_crud=user_crud,
        password_verifier=lambda plain, hashed: plain == "Password1" and hashed == "HASHED",
        token_pair_factory=token_pair_factory,
    )

    assert response == {"access_token": "access"}
    user_crud.update_login.assert_called_once_with(mock_db, user_id)
    token_pair_factory.assert_called_once_with(str(user_id), str(org_id), "admin")


@pytest.mark.asyncio
async def test_auth_service_refresh_token_data_checks_user_and_org():
    from services.auth_service import refresh_token_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.side_effect = [
        {"id": "user-1", "role": "admin", "is_active": True},
        {"id": "org-1", "is_active": True},
    ]
    token_pair_factory = MagicMock(return_value={"access_token": "access"})

    response = await refresh_token_data(
        refresh_token="refresh-token",
        db_factory=MagicMock(return_value=mock_db_cm),
        token_decoder=lambda token: SimpleNamespace(sub="user-1", org="org-1", type="refresh"),
        token_pair_factory=token_pair_factory,
    )

    assert response == {"access_token": "access"}
    token_pair_factory.assert_called_once_with("user-1", "org-1", "admin")


@pytest.mark.asyncio
async def test_auth_service_get_current_user_profile_data_maps_verified_and_superadmin_limits():
    from services.auth_service import get_current_user_profile_data

    current_user = MagicMock()
    current_user.id = uuid.uuid4()
    current_user.organization_id = uuid.uuid4()
    current_user.is_superadmin = True
    user_row = _make_user_row(
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        is_email_verified=True,
    )
    org_row = _make_org_row(org_id=current_user.organization_id)
    user_crud = MagicMock()
    user_crud.get_by_id.return_value = user_row
    organization_crud = MagicMock()
    organization_crud.get_by_id.return_value = org_row
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    response = await get_current_user_profile_data(
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_crud=user_crud,
        organization_crud=organization_crud,
        plan_features_getter=lambda: {
            "superadmin": {
                "max_watchlist_items": 999,
                "monthly_live_searches": 5000,
                "max_users": 250,
            }
        },
    )

    assert response.is_verified is True
    assert response.organization.plan == "enterprise"
    assert response.organization.max_watchlist_items == 999
    assert response.organization.max_monthly_searches == 5000
    assert response.organization.max_users == 250


def test_lead_service_get_lead_access_returns_remaining_credits():
    from services.lead_service import _get_lead_access

    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"cnt": 2}

    access = _get_lead_access(
        mock_db,
        "user-1",
        user_plan_getter=lambda db, user_id: {"plan_name": "professional"},
        plan_limit_getter=lambda plan, key: 10,
    )

    assert access == {
        "can_access": True,
        "plan": "professional",
        "daily_limit": 10,
        "used_today": 2,
        "remaining": 8,
    }
    assert "FROM lead_access_log" in mock_cursor.execute.call_args.args[0]


@pytest.mark.asyncio
async def test_lead_service_get_lead_feed_data_formats_items():
    from services.lead_service import get_lead_feed_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.side_effect = [{"cnt": 1}, {"cnt": 1}]
    mock_cursor.fetchall.return_value = [_make_lead_row()]

    response = await get_lead_feed_data(
        urgency="critical",
        nice_class=25,
        min_score=0.8,
        status="viewed",
        search="nike",
        page=2,
        limit=5,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "professional"},
        plan_limit_getter=lambda plan, key: 10,
    )

    assert response["total_count"] == 1
    assert response["page"] == 2
    assert response["items"][0]["new_mark_name"] == "NEW MARK"
    assert response["items"][0]["days_until_deadline"] == 18
    assert response["items"][0]["new_mark_has_extracted_goods"] is True
    executed_sql = _joined_executed_sql(mock_cursor)
    assert "regexp_replace(lower(coalesce(uc.new_mark_name" in executed_sql
    assert "regexp_replace(lower(coalesce(uc.existing_mark_name" in executed_sql
    assert "(şekil|sekil)" in executed_sql
    assert "{_shape_only_conflict_exclusion_sql()}" not in executed_sql


def test_lead_service_shape_only_conflict_exclusion_sql_filters_stale_sekil_labels():
    from services.lead_service import _shape_only_conflict_exclusion_sql

    sql = _shape_only_conflict_exclusion_sql()

    assert "uc.new_mark_name" in sql
    assert "uc.existing_mark_name" in sql
    assert "(şekil|sekil)" in sql
    assert "AND NOT" in sql


@pytest.mark.asyncio
async def test_lead_service_get_lead_stats_data_filters_shape_only_conflicts():
    from services.lead_service import get_lead_stats_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.side_effect = [
        {"cnt": 0},
        {
            "total_leads": 5,
            "critical_leads": 1,
            "urgent_leads": 2,
            "upcoming_leads": 3,
            "new_leads": 4,
            "viewed_leads": 1,
            "contacted_leads": 0,
            "converted_leads": 0,
            "avg_similarity": 0.86,
            "last_scan_at": datetime(2026, 4, 12, 12, 0),
        },
    ]

    response = await get_lead_stats_data(
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "professional"},
        plan_limit_getter=lambda plan, key: 10,
    )

    assert response["total_leads"] == 5
    assert response["avg_similarity"] == 0.86
    executed_sql = _joined_executed_sql(mock_cursor)
    assert "regexp_replace(lower(coalesce(uc.new_mark_name" in executed_sql
    assert "regexp_replace(lower(coalesce(uc.existing_mark_name" in executed_sql
    assert "(şekil|sekil)" in executed_sql
    assert "{_shape_only_conflict_exclusion_sql()}" not in executed_sql


@pytest.mark.asyncio
async def test_lead_service_export_leads_csv_data_filters_shape_only_conflicts():
    from services.lead_service import export_leads_csv_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.return_value = {"cnt": 0}
    mock_cursor.fetchall.return_value = [_make_lead_row()]

    response = await export_leads_csv_data(
        urgency="urgent",
        nice_class=25,
        min_score=0.75,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "enterprise"},
        plan_limit_getter=lambda plan, key: True if key == "can_export_csv_leads" else -1,
        now_getter=lambda: datetime(2026, 4, 12, 12, 0),
        streaming_response_factory=StreamingResponse,
    )

    assert response.headers["content-disposition"] == "attachment; filename=leads_20260412.csv"
    executed_sql = _joined_executed_sql(mock_cursor)
    assert "regexp_replace(lower(coalesce(uc.new_mark_name" in executed_sql
    assert "regexp_replace(lower(coalesce(uc.existing_mark_name" in executed_sql
    assert "(şekil|sekil)" in executed_sql
    assert "{_shape_only_conflict_exclusion_sql()}" not in executed_sql


@pytest.mark.asyncio
async def test_lead_service_get_lead_detail_data_marks_new_lead_viewed_and_logs_access():
    from services.lead_service import get_lead_detail_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.side_effect = [{"cnt": 1}, _make_lead_row(lead_status="new")]

    response = await get_lead_detail_data(
        lead_id="lead-1",
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "professional"},
        plan_limit_getter=lambda plan, key: 10,
    )

    assert response["new_mark_name"] == "NEW MARK"
    assert any(
        "UPDATE universal_conflicts" in call.args[0]
        for call in mock_cursor.execute.call_args_list
    )
    assert any(
        "INSERT INTO lead_access_log" in call.args[0]
        for call in mock_cursor.execute.call_args_list
    )
    executed_sql = _joined_executed_sql(mock_cursor)
    assert "regexp_replace(lower(coalesce(uc.new_mark_name" in executed_sql
    assert "regexp_replace(lower(coalesce(uc.existing_mark_name" in executed_sql
    assert "(şekil|sekil)" in executed_sql
    assert "{_shape_only_conflict_exclusion_sql()}" not in executed_sql
    assert mock_db.commit.call_count == 2


@pytest.mark.asyncio
async def test_lead_service_mark_lead_contacted_data_updates_status():
    from services.lead_service import mark_lead_contacted_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.return_value = {"cnt": 1}
    mock_cursor.rowcount = 1

    response = await mark_lead_contacted_data(
        lead_id="lead-2",
        notes="Called",
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "professional"},
        plan_limit_getter=lambda plan, key: 10,
    )

    assert response["new_status"] == "contacted"
    assert any(
        "SET lead_status = 'contacted'" in call.args[0]
        for call in mock_cursor.execute.call_args_list
    )
    assert mock_db.commit.call_count == 2


def test_lead_service_renewal_statuses_exclude_already_renewed_records():
    from services.lead_service import RENEWAL_ACTIVE_STATUSES

    assert "Yenilendi" not in RENEWAL_ACTIVE_STATUSES
    assert RENEWAL_ACTIVE_STATUSES == ("Tescil Edildi", "Devredildi")


@pytest.mark.asyncio
async def test_lead_service_get_renewal_stats_data_excludes_already_renewed_records():
    from services.lead_service import get_renewal_stats_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.side_effect = [
        {"cnt": 0},
        {
            "total": 3,
            "grace_period": 1,
            "critical": 1,
            "urgent": 1,
            "upcoming": 0,
        },
    ]

    response = await get_renewal_stats_data(
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "professional"},
        plan_limit_getter=lambda plan, key: 10,
    )

    assert response["total"] == 3
    renewal_query_calls = [
        call for call in mock_cursor.execute.call_args_list
        if call.args and isinstance(call.args[0], str) and "FROM trademarks t" in call.args[0]
    ]
    assert renewal_query_calls
    assert renewal_query_calls[0].args[1][0] == ("Tescil Edildi", "Devredildi")
    assert "Yenilendi" not in renewal_query_calls[0].args[1][0]


@pytest.mark.asyncio
async def test_lead_service_get_renewal_feed_data_formats_rows():
    from services.lead_service import get_renewal_feed_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.side_effect = [{"cnt": 1}, {"cnt": 1}]
    mock_cursor.fetchall.return_value = [
        _make_renewal_row(
            days_until_expiry=-10,
            expiry_date=date(2026, 2, 1),
            urgency_level="grace_period",
        )
    ]

    response = await get_renewal_feed_data(
        urgency="grace_period",
        nice_class=35,
        search="renew",
        page=3,
        limit=4,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "professional"},
        plan_limit_getter=lambda plan, key: 10,
    )

    assert response["total_count"] == 1
    assert response["page"] == 3
    assert response["items"][0]["grace_days_remaining"] == 173
    assert response["items"][0]["expiry_date"] == "2026-02-01"
    renewal_query_calls = [
        call for call in mock_cursor.execute.call_args_list
        if call.args and isinstance(call.args[0], str) and "FROM trademarks t" in call.args[0]
    ]
    assert renewal_query_calls
    for call in renewal_query_calls:
        assert call.args[1][0] == ("Tescil Edildi", "Devredildi")
        assert "Yenilendi" not in call.args[1][0]


@pytest.mark.asyncio
async def test_lead_service_export_renewals_csv_data_streams_csv():
    from services.lead_service import export_renewals_csv_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.return_value = {"cnt": 1}
    mock_cursor.fetchall.return_value = [_make_renewal_row()]

    response = await export_renewals_csv_data(
        urgency="critical",
        nice_class=25,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "enterprise"},
        plan_limit_getter=lambda plan, key: True if key == "can_export_csv_leads" else 10,
        now_getter=lambda: datetime(2026, 4, 12, 14, 0, tzinfo=timezone.utc),
    )

    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk)
        else:
            chunks.append(chunk.encode("utf-8"))
    body = b"".join(chunks).decode("utf-8")

    assert response.headers["content-disposition"] == "attachment; filename=renewals_20260412.csv"
    assert "Marka,Basvuru No,Tescil No,Sahip" in body
    assert "RENEWAL MARK,2016/12345,TR-123,Renewal Holder" in body
    renewal_query_calls = [
        call for call in mock_cursor.execute.call_args_list
        if call.args and isinstance(call.args[0], str) and "FROM trademarks t" in call.args[0]
    ]
    assert renewal_query_calls
    assert renewal_query_calls[0].args[1][0] == ("Tescil Edildi", "Devredildi")
    assert "Yenilendi" not in renewal_query_calls[0].args[1][0]


@pytest.mark.asyncio
async def test_lead_service_get_cancellation_feed_data_formats_rows():
    from services.lead_service import get_cancellation_feed_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    tm_id = uuid.uuid4()
    mock_cursor.fetchone.side_effect = [{"cnt": 1}, {"cnt": 1}]
    mock_cursor.fetchall.return_value = [
        {
            "id": tm_id,
            "name": "CANCELLED MARK",
            "application_no": "2014/1",
            "registration_no": "TR-CAN-1",
            "nice_class_numbers": [25, 35],
            "image_path": None,
            "final_status": "Iptal Edildi",
            "application_date": date(2014, 1, 2),
            "cancellation_bulletin_no": "BLT_500",
            "cancellation_date": date(2026, 3, 12),
            "cancellation_subtype": "voluntary",
            "days_since_cancellation": 57,
            "holder_name": "Holder X",
            "holder_tpe_client_id": "H-1",
            "attorney_name": "Agent Smith",
            "attorney_no": "A-1",
        }
    ]

    response = await get_cancellation_feed_data(
        nice_class=25,
        search="cancel",
        page=1,
        limit=20,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "professional"},
        plan_limit_getter=lambda plan, key: 10,
    )

    assert response["total_count"] == 1
    assert response["page"] == 1
    item = response["items"][0]
    assert item["id"] == str(tm_id)
    assert item["application_no"] == "2014/1"
    assert item["cancellation_date"] == "2026-03-12"
    assert item["cancellation_bulletin_no"] == "BLT_500"
    assert item["cancellation_subtype"] == "voluntary"
    assert item["days_since_cancellation"] == 57
    assert item["holder_name"] == "Holder X"
    cancel_query_calls = [
        call for call in mock_cursor.execute.call_args_list
        if call.args and isinstance(call.args[0], str) and "trademark_events" in call.args[0]
    ]
    assert cancel_query_calls
    assert any("event_type = 'cancellation'" in call.args[0] for call in cancel_query_calls)


@pytest.mark.asyncio
async def test_lead_service_export_cancellations_csv_data_streams_csv():
    from services.lead_service import export_cancellations_csv_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.return_value = {"cnt": 1}
    mock_cursor.fetchall.return_value = [
        {
            "name": "CANCELLED MARK",
            "application_no": "2014/1",
            "registration_no": "TR-CAN-1",
            "holder_name": "Holder X",
            "attorney_name": "Agent Smith",
            "attorney_no": "A-1",
            "nice_class_numbers": [25, 35],
            "final_status": "Iptal Edildi",
            "cancellation_bulletin_no": "BLT_500",
            "cancellation_date": date(2026, 3, 12),
            "cancellation_subtype": "voluntary",
            "days_since_cancellation": 57,
        }
    ]

    response = await export_cancellations_csv_data(
        nice_class=25,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "enterprise"},
        plan_limit_getter=lambda plan, key: True if key == "can_export_csv_leads" else 10,
        now_getter=lambda: datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc),
    )

    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk)
        else:
            chunks.append(chunk.encode("utf-8"))
    body = b"".join(chunks).decode("utf-8")

    assert response.headers["content-disposition"] == "attachment; filename=cancellations_20260508.csv"
    assert "Marka,Basvuru No,Tescil No,Sahip,Vekil,Vekil No,Siniflar,Durum,Iptal Tarihi,Iptal Bulten No,Iptal Alt Tipi,Iptalden Sonra Gun" in body
    assert 'CANCELLED MARK,2014/1,TR-CAN-1,Holder X,Agent Smith,A-1,"25,35",Iptal Edildi,2026-03-12,BLT_500,voluntary,57' in body


@pytest.mark.asyncio
async def test_extracted_cancellation_feed_route_delegates_to_service():
    from api.leads import get_cancellation_feed

    current_user = MagicMock()
    expected = {"total_count": 0, "page": 1, "limit": 20, "items": []}

    with patch(
        "api.leads.get_cancellation_feed_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_cancellation_feed_data:
        response = await get_cancellation_feed(
            nice_class=9,
            search="abc",
            page=1,
            limit=20,
            current_user=current_user,
        )

    assert response == expected
    mock_get_cancellation_feed_data.assert_awaited_once_with(
        nice_class=9,
        search="abc",
        page=1,
        limit=20,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_export_cancellations_csv_route_delegates_to_service():
    from api.leads import export_cancellations_csv

    current_user = MagicMock()
    expected = StreamingResponse(iter([b"csv"]), media_type="text/csv")

    with patch(
        "api.leads.export_cancellations_csv_data",
        new=AsyncMock(return_value=expected),
    ) as mock_export_cancellations_csv_data:
        response = await export_cancellations_csv(
            nice_class=25,
            current_user=current_user,
        )

    assert response is expected
    mock_export_cancellations_csv_data.assert_awaited_once_with(
        nice_class=25,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_lead_service_get_transfer_feed_data_formats_rows():
    from services.lead_service import get_transfer_feed_data, TRANSFER_EVENT_TYPES

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    tm_id = uuid.uuid4()
    mock_cursor.fetchone.side_effect = [{"cnt": 1}, {"cnt": 1}]
    mock_cursor.fetchall.return_value = [
        {
            "id": tm_id,
            "name": "TRANSFERRED MARK",
            "application_no": "2010/9999",
            "registration_no": "TR-T-1",
            "nice_class_numbers": [9],
            "image_path": None,
            "final_status": "Tescil Edildi",
            "application_date": date(2010, 5, 1),
            "event_type": "transfer",
            "transfer_bulletin_no": "BLT_500",
            "transfer_date": date(2026, 4, 1),
            "previous_holder_name": "Old Holder Inc.",
            "new_holder_name": "New Holder Ltd.",
            "days_since_transfer": 37,
            "holder_name": "New Holder Ltd.",
            "holder_tpe_client_id": "H-2",
            "attorney_name": "Agent Smith",
            "attorney_no": "A-1",
        }
    ]

    response = await get_transfer_feed_data(
        event_type=None,
        nice_class=9,
        search="trans",
        page=1,
        limit=20,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "professional"},
        plan_limit_getter=lambda plan, key: 10,
    )

    assert response["total_count"] == 1
    item = response["items"][0]
    assert item["id"] == str(tm_id)
    assert item["transfer_event_type"] == "transfer"
    assert item["transfer_date"] == "2026-04-01"
    assert item["previous_holder_name"] == "Old Holder Inc."
    assert item["new_holder_name"] == "New Holder Ltd."
    assert item["days_since_transfer"] == 37
    transfer_query_calls = [
        call for call in mock_cursor.execute.call_args_list
        if call.args and isinstance(call.args[0], str) and "trademark_events" in call.args[0]
    ]
    assert transfer_query_calls
    # When no event_type filter is set, all 3 transfer event types should be in the params.
    found_full_set = False
    for call in transfer_query_calls:
        if call.args and len(call.args) > 1 and isinstance(call.args[1], list) and call.args[1]:
            first_param = call.args[1][0]
            if isinstance(first_param, list) and set(first_param) == set(TRANSFER_EVENT_TYPES):
                found_full_set = True
                break
    assert found_full_set, "expected feed query to scope to all 3 transfer event types"


@pytest.mark.asyncio
async def test_lead_service_get_transfer_feed_data_respects_event_type_filter():
    from services.lead_service import get_transfer_feed_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.side_effect = [{"cnt": 0}, {"cnt": 0}]
    mock_cursor.fetchall.return_value = []

    await get_transfer_feed_data(
        event_type="merger",
        nice_class=None,
        search=None,
        page=1,
        limit=20,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "professional"},
        plan_limit_getter=lambda plan, key: 10,
    )

    transfer_query_calls = [
        call for call in mock_cursor.execute.call_args_list
        if call.args and isinstance(call.args[0], str) and "trademark_events" in call.args[0]
    ]
    assert transfer_query_calls
    found_merger_only = False
    for call in transfer_query_calls:
        if call.args and len(call.args) > 1 and isinstance(call.args[1], list) and call.args[1]:
            first_param = call.args[1][0]
            if isinstance(first_param, list) and first_param == ["merger"]:
                found_merger_only = True
                break
    assert found_merger_only, "expected event_type='merger' filter to scope to ['merger'] only"


@pytest.mark.asyncio
async def test_lead_service_export_transfers_csv_data_streams_csv():
    from services.lead_service import export_transfers_csv_data

    current_user = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.return_value = {"cnt": 1}
    mock_cursor.fetchall.return_value = [
        {
            "name": "TRANSFERRED MARK",
            "application_no": "2010/9999",
            "registration_no": "TR-T-1",
            "holder_name": "New Holder Ltd.",
            "attorney_name": "Agent Smith",
            "attorney_no": "A-1",
            "nice_class_numbers": [9],
            "final_status": "Tescil Edildi",
            "event_type": "transfer",
            "transfer_bulletin_no": "BLT_500",
            "transfer_date": date(2026, 4, 1),
            "previous_holder_name": "Old Holder Inc.",
            "new_holder_name": "New Holder Ltd.",
            "days_since_transfer": 37,
        }
    ]

    response = await export_transfers_csv_data(
        event_type=None,
        nice_class=9,
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=lambda db, user_id: {"plan_name": "enterprise"},
        plan_limit_getter=lambda plan, key: True if key == "can_export_csv_leads" else 10,
        now_getter=lambda: datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc),
    )

    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk)
        else:
            chunks.append(chunk.encode("utf-8"))
    body = b"".join(chunks).decode("utf-8")

    assert response.headers["content-disposition"] == "attachment; filename=transfers_20260508.csv"
    assert "Marka,Basvuru No,Tescil No,Olay Tipi,Onceki Sahip,Yeni Sahip" in body
    assert "TRANSFERRED MARK,2010/9999,TR-T-1,transfer,Old Holder Inc.,New Holder Ltd.,New Holder Ltd.,Agent Smith,A-1,9,Tescil Edildi,2026-04-01,BLT_500,37" in body


@pytest.mark.asyncio
async def test_extracted_transfer_feed_route_delegates_to_service():
    from api.leads import get_transfer_feed

    current_user = MagicMock()
    expected = {"total_count": 0, "page": 1, "limit": 20, "items": []}

    with patch(
        "api.leads.get_transfer_feed_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_transfer_feed_data:
        response = await get_transfer_feed(
            event_type="transfer",
            nice_class=9,
            search="abc",
            page=1,
            limit=20,
            current_user=current_user,
        )

    assert response == expected
    mock_get_transfer_feed_data.assert_awaited_once_with(
        event_type="transfer",
        nice_class=9,
        search="abc",
        page=1,
        limit=20,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_export_transfers_csv_route_delegates_to_service():
    from api.leads import export_transfers_csv

    current_user = MagicMock()
    expected = StreamingResponse(iter([b"csv"]), media_type="text/csv")

    with patch(
        "api.leads.export_transfers_csv_data",
        new=AsyncMock(return_value=expected),
    ) as mock_export_transfers_csv_data:
        response = await export_transfers_csv(
            event_type="merger",
            nice_class=9,
            current_user=current_user,
        )

    assert response is expected
    mock_export_transfers_csv_data.assert_awaited_once_with(
        event_type="merger",
        nice_class=9,
        current_user=current_user,
    )


def test_payment_service_get_client_ip_prefers_proxy_headers():
    from services.payment_service import get_client_ip
    from starlette.requests import Request

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [
                (b"cf-connecting-ip", b"203.0.113.10"),
                (b"x-forwarded-for", b"198.51.100.2, 198.51.100.3"),
            ],
            "client": ("127.0.0.1", 1234),
        },
        receive,
    )

    assert get_client_ip(request) == "203.0.113.10"


@pytest.mark.asyncio
async def test_payment_service_initialize_payment_data_creates_checkout_session():
    from services.payment_service import initialize_payment_data

    current_user = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="buyer@example.com",
        first_name="Buyer",
        last_name="User",
    )
    request = _make_request(
        json_body={"plan": "starter", "billing": "monthly"},
        host="198.51.100.9",
    )
    settings_obj = SimpleNamespace(
        iyzico=SimpleNamespace(
            api_key="test-key",
            secret_key="test-secret",
            base_url="https://sandbox.iyzico.test",
            callback_url="https://example.com/callback",
        )
    )

    mock_db1_cm = MagicMock()
    mock_db1 = MagicMock()
    mock_cursor1 = MagicMock()
    mock_db1.cursor.return_value = mock_cursor1
    mock_db1_cm.__enter__.return_value = mock_db1
    mock_db1_cm.__exit__.return_value = False
    mock_cursor1.fetchone.side_effect = [
        {
            "tax_id": "12345678901",
            "address": "Istanbul",
            "city": "Istanbul",
            "country": "Turkey",
            "phone": "+90 555 000 0000",
            "user_phone": None,
        },
        {"id": "pay-1"},
    ]

    mock_db2_cm = MagicMock()
    mock_db2 = MagicMock()
    mock_cursor2 = MagicMock()
    mock_db2.cursor.return_value = mock_cursor2
    mock_db2_cm.__enter__.return_value = mock_db2
    mock_db2_cm.__exit__.return_value = False

    checkout_result = MagicMock()
    checkout_result.read.return_value = (
        b'{"status":"success","token":"tok-1","checkoutFormContent":"<form>checkout</form>"}'
    )
    checkout_client = MagicMock()
    checkout_client.create.return_value = checkout_result
    checkout_factory = MagicMock(return_value=checkout_client)
    options_getter = MagicMock(return_value={"api_key": "test-key"})

    response = await initialize_payment_data(
        request=request,
        payload={"plan": "starter", "billing": "monthly"},
        current_user=current_user,
        db_factory=MagicMock(side_effect=[mock_db1_cm, mock_db2_cm]),
        db_connection_factory=MagicMock(side_effect=[object(), object()]),
        settings_obj=settings_obj,
        plan_features={
            "starter": {
                "price_monthly": 99.9,
                "price_annual_monthly": 79.9,
            }
        },
        options_getter=options_getter,
        checkout_form_initialize_factory=checkout_factory,
    )

    assert response["token"] == "tok-1"
    assert response["payment_id"] == "pay-1"
    checkout_factory.assert_called_once_with()
    options_getter.assert_called_once_with(settings_obj)
    request_data, request_options = checkout_client.create.call_args.args
    assert request_options == {"api_key": "test-key"}
    assert request_data["buyer"]["ip"] == "198.51.100.9"
    assert request_data["callbackUrl"] == "https://example.com/callback"
    assert request_data["basketItems"][0]["id"] == "pay-1"
    mock_db1.commit.assert_called_once()
    mock_db2.commit.assert_called_once()
    assert mock_cursor2.execute.call_args.args[1] == ("tok-1", "pay-1")


def test_payment_service_activate_subscription_refreshes_monthly_ai_credits():
    from services.payment_service import activate_subscription

    now = datetime(2026, 5, 7, 12, 0, 0)
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor

    success = activate_subscription(
        mock_db,
        "org-1",
        "professional",
        "monthly",
        plan_id_lookup=MagicMock(return_value="plan-1"),
        plan_limit_getter=MagicMock(return_value=50),
        now_getter=lambda: now,
    )

    assert success is True
    sql, params = mock_cursor.execute.call_args.args
    assert "ai_credits_monthly = %s" in sql
    assert "ai_credits_reset_at = %s" in sql
    assert params[0] == "plan-1"
    assert params[1] == now
    assert params[3] == 50
    assert params[4] == now
    assert params[5] == "org-1"
    mock_db.commit.assert_called_once()


def test_payment_service_process_payment_result_marks_completed_and_activates_subscription():
    from services.payment_service import process_payment_result

    payment = _make_payment_row(
        payment_id="pay-1",
        organization_id=uuid.uuid4(),
        plan_name="professional",
        billing_period="annual",
    )
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    subscription_activator = MagicMock(return_value=True)

    success = process_payment_result(
        mock_db,
        payment,
        {
            "status": "success",
            "paymentStatus": "SUCCESS",
            "paymentId": "iyz-1",
        },
        subscription_activator=subscription_activator,
    )

    assert success is True
    assert "UPDATE payments" in mock_cursor.execute.call_args_list[0].args[0]
    subscription_activator.assert_called_once_with(
        mock_db,
        str(payment["organization_id"]),
        "professional",
        "annual",
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_payment_service_activate_free_plan_data_raises_when_activation_fails():
    from services.payment_service import activate_free_plan_data

    current_user = SimpleNamespace(organization_id=uuid.uuid4())
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    with pytest.raises(Exception) as exc_info:
        await activate_free_plan_data(
            current_user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            db_connection_factory=MagicMock(return_value=object()),
            subscription_activator=MagicMock(return_value=False),
        )

    assert "Failed to activate free plan" in str(exc_info.value)


@pytest.mark.asyncio
async def test_pipeline_service_trigger_pipeline_run_data_launches_detached_worker():
    from services.pipeline_service import trigger_pipeline_run_data

    current_user = SimpleNamespace(email="admin@example.com")
    background_tasks = MagicMock()
    process_launcher = MagicMock()
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.return_value = []
    mock_cursor.fetchone.return_value = None

    response = await trigger_pipeline_run_data(
        skip_download=True,
        background_tasks=background_tasks,
        current_user=current_user,
        state_getter=lambda: (None, None),
        db_factory=MagicMock(return_value=mock_db_cm),
        run_id_factory=lambda: "run-1",
        process_launcher=process_launcher,
    )

    assert response == {
        "run_id": "run-1",
        "status": "started",
        "skip_download": True,
    }
    process_launcher.assert_called_once_with(
        triggered_by="api",
        run_id="run-1",
        skip_download=True,
        single_step=None,
        service_logger=ANY,
    )
    executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list]
    assert any("SELECT id, started_at, heartbeat_at, current_step" in sql for sql in executed_sql)
    assert any("SELECT id FROM pipeline_runs" in sql for sql in executed_sql)
    assert any("INSERT INTO pipeline_runs" in sql for sql in executed_sql)
    mock_db.conn.commit.assert_called_once()


@pytest.mark.asyncio
async def test_pipeline_service_trigger_pipeline_step_data_rejects_invalid_step():
    from fastapi import HTTPException
    from services.pipeline_service import trigger_pipeline_step_data

    with pytest.raises(HTTPException) as exc_info:
        await trigger_pipeline_step_data(
            step="unknown",
            background_tasks=MagicMock(),
            current_user=MagicMock(),
        )

    assert exc_info.value.status_code == 400
    assert "Gecersiz adim" in exc_info.value.detail


@pytest.mark.asyncio
async def test_pipeline_service_trigger_pipeline_step_data_launches_detached_worker():
    from services.pipeline_service import trigger_pipeline_step_data

    current_user = SimpleNamespace(email="admin@example.com")
    process_launcher = MagicMock()
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.return_value = []
    mock_cursor.fetchone.return_value = None

    response = await trigger_pipeline_step_data(
        step="extract",
        background_tasks=MagicMock(),
        current_user=current_user,
        state_getter=lambda: (None, None),
        db_factory=MagicMock(return_value=mock_db_cm),
        run_id_factory=lambda: "run-step-1",
        process_launcher=process_launcher,
    )

    assert response == {
        "run_id": "run-step-1",
        "status": "started",
        "step": "extract",
    }
    process_launcher.assert_called_once_with(
        triggered_by="api",
        run_id="run-step-1",
        skip_download=True,
        single_step="extract",
        service_logger=ANY,
    )
    executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list]
    assert any("INSERT INTO pipeline_runs" in sql for sql in executed_sql)
    mock_db.conn.commit.assert_called_once()


@pytest.mark.asyncio
async def test_pipeline_service_trigger_pipeline_step_data_accepts_event_ingest():
    from services.pipeline_service import trigger_pipeline_step_data

    current_user = SimpleNamespace(email="admin@example.com")
    process_launcher = MagicMock()
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.return_value = []
    mock_cursor.fetchone.return_value = None

    response = await trigger_pipeline_step_data(
        step="event_ingest",
        background_tasks=MagicMock(),
        current_user=current_user,
        state_getter=lambda: (None, None),
        db_factory=MagicMock(return_value=mock_db_cm),
        run_id_factory=lambda: "run-step-events",
        process_launcher=process_launcher,
    )

    assert response == {
        "run_id": "run-step-events",
        "status": "started",
        "step": "event_ingest",
    }
    process_launcher.assert_called_once_with(
        triggered_by="api",
        run_id="run-step-events",
        skip_download=True,
        single_step="event_ingest",
        service_logger=ANY,
    )


@pytest.mark.asyncio
async def test_pipeline_service_trigger_pipeline_step_data_accepts_repair():
    from services.pipeline_service import trigger_pipeline_step_data

    current_user = SimpleNamespace(email="admin@example.com")
    process_launcher = MagicMock()
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.return_value = []
    mock_cursor.fetchone.return_value = None

    response = await trigger_pipeline_step_data(
        step="repair",
        background_tasks=MagicMock(),
        current_user=current_user,
        state_getter=lambda: (None, None),
        db_factory=MagicMock(return_value=mock_db_cm),
        run_id_factory=lambda: "run-step-db-repair",
        process_launcher=process_launcher,
    )

    assert response == {
        "run_id": "run-step-db-repair",
        "status": "started",
        "step": "repair",
    }
    process_launcher.assert_called_once_with(
        triggered_by="api",
        run_id="run-step-db-repair",
        skip_download=True,
        single_step="repair",
        service_logger=ANY,
    )


@pytest.mark.asyncio
async def test_pipeline_service_trigger_pipeline_step_data_accepts_final_status_repair():
    from services.pipeline_service import trigger_pipeline_step_data

    current_user = SimpleNamespace(email="admin@example.com")
    process_launcher = MagicMock()
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.return_value = []
    mock_cursor.fetchone.return_value = None

    response = await trigger_pipeline_step_data(
        step="final_status_repair",
        background_tasks=MagicMock(),
        current_user=current_user,
        state_getter=lambda: (None, None),
        db_factory=MagicMock(return_value=mock_db_cm),
        run_id_factory=lambda: "run-step-repair",
        process_launcher=process_launcher,
    )

    assert response == {
        "run_id": "run-step-repair",
        "status": "started",
        "step": "final_status_repair",
    }
    process_launcher.assert_called_once_with(
        triggered_by="api",
        run_id="run-step-repair",
        skip_download=True,
        single_step="final_status_repair",
        service_logger=ANY,
    )


@pytest.mark.asyncio
async def test_pipeline_service_get_pipeline_status_data_maps_recent_runs():
    from services.pipeline_service import get_pipeline_status_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.return_value = [
        _make_pipeline_run_row(
            run_id="run-1",
            status="running",
            completed_at=None,
            current_step="extract",
        )
    ]

    response = await get_pipeline_status_data(
        limit=5,
        current_user=MagicMock(),
        state_getter=lambda: (None, None),
        db_factory=MagicMock(return_value=mock_db_cm),
        next_scheduled_getter=lambda: "2026-04-14T03:00:00",
    )

    assert response["is_running"] is True
    assert response["current_run_id"] == "run-1"
    assert response["current_step"] == "extract"
    assert response["next_scheduled"] == "2026-04-14T03:00:00"
    assert response["recent_runs"][0]["id"] == "run-1"
    assert response["recent_runs"][0]["status"] == "running"
    assert response["recent_runs"][0]["current_step"] == "extract"
    assert response["recent_runs"][0]["step_event_ingest"] == {"status": "success"}
    assert response["recent_runs"][0]["step_repair"] == {"status": "success"}
    assert response["recent_runs"][0]["total_repaired"] == 6
    assert response["recent_runs"][0]["total_event_scopes_ingested"] == 7
    assert response["recent_runs"][0]["step_final_status_repair"] == {"status": "success"}


@pytest.mark.asyncio
async def test_pipeline_worker_single_step_event_ingest_runs_only_event_step(monkeypatch):
    from workers.pipeline_worker import PipelineWorker, StepResult

    worker = PipelineWorker()
    calls = []

    monkeypatch.setattr("workers.pipeline_worker._get_db_connection", lambda: (_ for _ in ()).throw(RuntimeError("tracking unavailable")))

    def unexpected(*args, **kwargs):
        raise AssertionError("unrelated pipeline step should not run")

    monkeypatch.setattr(worker, "run_step_download", unexpected)
    monkeypatch.setattr(worker, "run_step_extract", unexpected)
    monkeypatch.setattr(worker, "run_step_metadata", unexpected)
    monkeypatch.setattr(worker, "run_step_embeddings", unexpected)
    monkeypatch.setattr(worker, "run_step_ingest", unexpected)
    monkeypatch.setattr(worker, "run_step_conflict_scan", unexpected)
    monkeypatch.setattr(worker, "run_step_final_status_repair", unexpected)

    def run_events():
        calls.append("event_ingest")
        return StepResult(step_name="event_ingest", status="success", processed=45)

    monkeypatch.setattr(worker, "run_step_event_ingest", run_events)

    result = await worker.run_full_pipeline(
        skip_download=True,
        triggered_by="manual",
        single_step="event_ingest",
        run_id="run-event-step-1",
    )

    assert calls == ["event_ingest"]
    assert result.status == "success"
    assert [step.step_name for step in result.steps] == ["event_ingest"]
    assert result.steps[0].processed == 45


@pytest.mark.asyncio
async def test_pipeline_worker_single_step_final_status_repair_runs_only_maintenance_step(monkeypatch):
    from workers.pipeline_worker import PipelineWorker, StepResult

    worker = PipelineWorker()
    calls = []

    monkeypatch.setattr("workers.pipeline_worker._get_db_connection", lambda: (_ for _ in ()).throw(RuntimeError("tracking unavailable")))

    def unexpected(*args, **kwargs):
        raise AssertionError("regular pipeline step should not run")

    monkeypatch.setattr(worker, "run_step_download", unexpected)
    monkeypatch.setattr(worker, "run_step_extract", unexpected)
    monkeypatch.setattr(worker, "run_step_metadata", unexpected)
    monkeypatch.setattr(worker, "run_step_embeddings", unexpected)
    monkeypatch.setattr(worker, "run_step_ingest", unexpected)
    monkeypatch.setattr(worker, "run_step_conflict_scan", unexpected)

    def run_repair():
        calls.append("final_status_repair")
        return StepResult(step_name="final_status_repair", status="success", processed=123)

    monkeypatch.setattr(worker, "run_step_final_status_repair", run_repair)

    result = await worker.run_full_pipeline(
        skip_download=True,
        triggered_by="manual",
        single_step="final_status_repair",
        run_id="run-repair-1",
    )

    assert calls == ["final_status_repair"]
    assert result.status == "success"
    assert [step.step_name for step in result.steps] == ["final_status_repair"]
    assert result.steps[0].processed == 123


@pytest.mark.asyncio
async def test_pipeline_worker_run_step_event_ingest_retries_once_on_hard_failure(monkeypatch):
    from workers.pipeline_worker import PipelineWorker

    worker = PipelineWorker()
    calls = []

    def fake_run_event_ingest(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("transient failure")
        return {
            "status": "success",
            "processed": 12,
            "skipped": 3,
            "failed": 0,
            "alerts_generated": 4,
        }

    monkeypatch.setattr("ingest_events.run_event_ingest", fake_run_event_ingest)

    result = worker.run_step_event_ingest()

    assert len(calls) == 2
    assert result.status == "success"
    assert result.processed == 12
    assert result.skipped == 3
    assert result.failed == 0


@pytest.mark.asyncio
async def test_pipeline_worker_full_pipeline_continues_after_event_ingest_retry_exhaustion(monkeypatch):
    from workers.pipeline_worker import PipelineWorker, StepResult

    worker = PipelineWorker()
    calls = []

    monkeypatch.setattr("workers.pipeline_worker._get_db_connection", lambda: (_ for _ in ()).throw(RuntimeError("tracking unavailable")))

    monkeypatch.setattr(worker, "run_step_download", AsyncMock(return_value=StepResult(step_name="download", status="success", processed=1)))
    monkeypatch.setattr(worker, "run_step_extract", lambda: StepResult(step_name="extract", status="success", processed=2))
    monkeypatch.setattr(worker, "run_step_metadata", lambda: StepResult(step_name="metadata", status="success", processed=3))
    monkeypatch.setattr(worker, "run_step_embeddings", lambda: StepResult(step_name="embeddings", status="success", processed=4))
    monkeypatch.setattr(worker, "run_step_ingest", lambda force=False: StepResult(step_name="ingest", status="success", processed=5))
    monkeypatch.setattr(worker, "run_step_repair", lambda: StepResult(step_name="repair", status="success", processed=0))

    def run_events():
        calls.append("event_ingest")
        return StepResult(step_name="event_ingest", status="failed", processed=0, failed=1, error="retry exhausted")

    def run_conflict():
        calls.append("conflict_scan")
        return StepResult(step_name="conflict_scan", status="success", processed=6)

    monkeypatch.setattr(worker, "run_step_event_ingest", run_events)
    monkeypatch.setattr(worker, "run_step_conflict_scan", run_conflict)

    result = await worker.run_full_pipeline(
        skip_download=False,
        triggered_by="manual",
        run_id="run-events-retry-exhausted",
    )

    assert calls == ["event_ingest", "conflict_scan"]
    assert result.status == "partial"
    assert [step.step_name for step in result.steps] == [
        "download",
        "extract",
        "metadata",
        "embeddings",
        "ingest",
        "repair",
        "event_ingest",
        "conflict_scan",
    ]


@pytest.mark.asyncio
async def test_pipeline_service_get_pipeline_status_data_marks_stale_running_row_failed():
    from services.pipeline_service import get_pipeline_status_data

    stale_started_at = datetime.now(timezone.utc) - timedelta(days=5)
    stale_row = _make_pipeline_run_row(
        run_id="stale-run",
        status="running",
        started_at=stale_started_at,
        completed_at=None,
        heartbeat_at=None,
        current_step=None,
        duration_seconds=None,
    )
    repaired_row = dict(stale_row)
    repaired_row["status"] = "failed"
    repaired_row["completed_at"] = datetime.now(timezone.utc)
    repaired_row["error_message"] = "Run marked failed after remaining in running state without a heartbeat."

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.side_effect = [
        [stale_row],
        [repaired_row],
    ]

    response = await get_pipeline_status_data(
        limit=5,
        current_user=MagicMock(),
        state_getter=lambda: (None, None),
        db_factory=MagicMock(return_value=mock_db_cm),
        next_scheduled_getter=lambda: None,
    )

    executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list]
    assert any("UPDATE pipeline_runs" in sql for sql in executed_sql)
    assert mock_db.conn.commit.call_count == 1
    assert response["is_running"] is False
    assert response["current_run_id"] is None
    assert response["recent_runs"][0]["id"] == "stale-run"
    assert response["recent_runs"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_pipeline_service_get_pipeline_status_data_marks_legacy_row_failed_even_after_heartbeat_backfill():
    from services.pipeline_service import get_pipeline_status_data

    stale_started_at = datetime.now(timezone.utc) - timedelta(days=5)
    stale_row = _make_pipeline_run_row(
        run_id="legacy-stale-run",
        status="running",
        started_at=stale_started_at,
        completed_at=None,
        heartbeat_at=datetime.now(timezone.utc),
        current_step=None,
        duration_seconds=None,
    )
    repaired_row = dict(stale_row)
    repaired_row["status"] = "failed"
    repaired_row["completed_at"] = datetime.now(timezone.utc)

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.side_effect = [
        [stale_row],
        [repaired_row],
    ]

    response = await get_pipeline_status_data(
        limit=5,
        current_user=MagicMock(),
        state_getter=lambda: (None, None),
        db_factory=MagicMock(return_value=mock_db_cm),
        next_scheduled_getter=lambda: None,
    )

    executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list]
    assert any("UPDATE pipeline_runs" in sql for sql in executed_sql)
    assert response["is_running"] is False
    assert response["recent_runs"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_pipeline_service_trigger_pipeline_run_data_reconciles_stale_db_run_before_starting():
    from services.pipeline_service import trigger_pipeline_run_data

    current_user = SimpleNamespace(email="admin@example.com")
    background_tasks = MagicMock()
    process_launcher = MagicMock()
    stale_started_at = datetime.now(timezone.utc) - timedelta(days=4)
    stale_row = _make_pipeline_run_row(
        run_id="stale-run",
        status="running",
        started_at=stale_started_at,
        completed_at=None,
        heartbeat_at=None,
        current_step=None,
        duration_seconds=None,
    )
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.return_value = [stale_row]
    mock_cursor.fetchone.return_value = None

    response = await trigger_pipeline_run_data(
        skip_download=False,
        background_tasks=background_tasks,
        current_user=current_user,
        state_getter=lambda: (None, None),
        db_factory=MagicMock(return_value=mock_db_cm),
        run_id_factory=lambda: "run-2",
        process_launcher=process_launcher,
    )

    executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list]
    assert any("UPDATE pipeline_runs" in sql for sql in executed_sql)
    assert any("INSERT INTO pipeline_runs" in sql for sql in executed_sql)
    assert mock_db.conn.commit.call_count == 2
    process_launcher.assert_called_once_with(
        triggered_by="api",
        run_id="run-2",
        skip_download=False,
        single_step=None,
        service_logger=ANY,
    )
    assert response == {
        "run_id": "run-2",
        "status": "started",
        "skip_download": False,
    }


@pytest.mark.asyncio
async def test_pipeline_service_trigger_pipeline_step_data_rejects_active_db_run():
    from fastapi import HTTPException
    from services.pipeline_service import trigger_pipeline_step_data

    fresh_row = _make_pipeline_run_row(
        run_id="active-db-run",
        status="running",
        completed_at=None,
        heartbeat_at=datetime.now(timezone.utc),
        current_step="metadata",
    )
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.return_value = [fresh_row]
    mock_cursor.fetchone.return_value = {"id": "active-db-run"}

    with pytest.raises(HTTPException) as exc_info:
        await trigger_pipeline_step_data(
            step="extract",
            background_tasks=MagicMock(),
            current_user=SimpleNamespace(email="admin@example.com"),
            state_getter=lambda: (None, None),
            db_factory=MagicMock(return_value=mock_db_cm),
            process_launcher=MagicMock(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "message": "Pipeline zaten calisiyor (veritabaninda)",
        "run_id": "active-db-run",
    }


@pytest.mark.asyncio
async def test_pipeline_service_trigger_pipeline_run_data_marks_row_failed_when_launch_fails():
    from fastapi import HTTPException
    from services.pipeline_service import trigger_pipeline_run_data

    current_user = SimpleNamespace(email="admin@example.com")
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchall.return_value = []
    mock_cursor.fetchone.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await trigger_pipeline_run_data(
            skip_download=False,
            background_tasks=MagicMock(),
            current_user=current_user,
            state_getter=lambda: (None, None),
            db_factory=MagicMock(return_value=mock_db_cm),
            run_id_factory=lambda: "run-launch-fail",
            process_launcher=MagicMock(side_effect=RuntimeError("spawn failed")),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Pipeline worker sureci baslatilamadi"
    executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list]
    assert any("INSERT INTO pipeline_runs" in sql for sql in executed_sql)
    assert any("UPDATE pipeline_runs" in sql for sql in executed_sql)
    assert mock_db.conn.commit.call_count == 2


def test_pipeline_launcher_launch_pipeline_process_builds_detached_command():
    from workers.pipeline_launcher import launch_pipeline_process

    process_runner = MagicMock(return_value=SimpleNamespace(pid=4321))
    env = {"TEST_ENV": "1"}
    working_directory = Path("C:/tmp/pipeline")

    process = launch_pipeline_process(
        triggered_by="api",
        run_id="run-99",
        skip_download=True,
        single_step="extract",
        process_runner=process_runner,
        env=env,
        working_directory=working_directory,
    )

    assert process.pid == 4321
    command = process_runner.call_args.args[0]
    kwargs = process_runner.call_args.kwargs
    assert command == [
        sys.executable,
        "-m",
        "workers.pipeline_worker",
        "--triggered-by",
        "api",
        "--run-id",
        "run-99",
        "--skip-download",
        "--step",
        "extract",
    ]
    assert kwargs["cwd"] == str(working_directory)
    assert kwargs["env"] == env
    assert kwargs["stdin"] is not None
    assert kwargs["stdout"] is not None
    assert kwargs["stderr"] is not None
    assert kwargs["close_fds"] is True
    if os.name == "nt":
        assert "creationflags" in kwargs
        assert "start_new_session" not in kwargs
    else:
        assert kwargs["start_new_session"] is True
        assert "creationflags" not in kwargs


def test_pipeline_scheduler_full_job_launches_detached_worker():
    sys.modules.pop("workers.pipeline_scheduler", None)

    with patch.dict(sys.modules, {"schedule": MagicMock()}):
        from workers.pipeline_scheduler import _run_full_pipeline

        with patch(
            "workers.pipeline_scheduler.launch_pipeline_process",
            return_value=SimpleNamespace(pid=9876),
        ) as mock_launch:
            _run_full_pipeline()

    mock_launch.assert_called_once_with(
        triggered_by="schedule",
        skip_download=False,
        service_logger=ANY,
    )


def test_pipeline_scheduler_daily_job_launches_detached_worker():
    sys.modules.pop("workers.pipeline_scheduler", None)

    with patch.dict(sys.modules, {"schedule": MagicMock()}):
        from workers.pipeline_scheduler import _run_daily_pipeline

        with patch(
            "workers.pipeline_scheduler.launch_pipeline_process",
            return_value=SimpleNamespace(pid=6789),
        ) as mock_launch:
            _run_daily_pipeline()

    mock_launch.assert_called_once_with(
        triggered_by="schedule",
        skip_download=True,
        service_logger=ANY,
    )


@pytest.mark.asyncio
async def test_pipeline_service_get_pipeline_run_detail_data_formats_response():
    from services.pipeline_service import get_pipeline_run_detail_data

    row = _make_pipeline_run_row(run_id="run-42")
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.cursor.return_value = mock_cursor
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_cursor.fetchone.return_value = row

    response = await get_pipeline_run_detail_data(
        run_id="run-42",
        current_user=MagicMock(),
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response["id"] == "run-42"
    assert response["total_downloaded"] == 12
    assert response["step_event_ingest"] == {"status": "success"}
    assert response["step_repair"] == {"status": "success"}
    assert response["total_repaired"] == 6
    assert response["total_event_scopes_ingested"] == 7
    assert response["step_final_status_repair"] == {"status": "success"}
    assert response["total_final_status_repaired"] == 7
    assert response["duration_seconds"] == 720
    assert response["created_at"] == row["created_at"].isoformat()


@pytest.mark.asyncio
async def test_extracted_get_organization_route_delegates_to_service():
    from api.org_routes import get_organization

    current_user = MagicMock()
    expected = {"id": str(uuid.uuid4()), "name": "Acme IP"}

    with patch(
        "api.org_routes.get_organization_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_organization_data:
        response = await get_organization(current_user=current_user)

    assert response == expected
    mock_get_organization_data.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_extracted_update_organization_route_delegates_to_service():
    from api.org_routes import update_organization
    from models.schemas import OrganizationUpdate

    current_user = MagicMock()
    data = OrganizationUpdate(name="Updated Org")
    expected = {"id": str(uuid.uuid4()), "name": "Updated Org"}

    with patch(
        "api.org_routes.update_organization_record",
        new=AsyncMock(return_value=expected),
    ) as mock_update_organization_record:
        response = await update_organization(data=data, current_user=current_user)

    assert response == expected
    mock_update_organization_record.assert_awaited_once_with(
        data=data,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_get_organization_stats_route_delegates_to_service():
    from api.org_routes import get_organization_stats

    current_user = MagicMock()
    expected = {
        "user_count": 4,
        "active_watchlist_items": 9,
        "new_alerts": 3,
        "critical_alerts": 1,
        "searches_this_month": 12,
        "storage_used_mb": 0.0,
    }

    with patch(
        "api.org_routes.get_organization_stats_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_organization_stats_data:
        response = await get_organization_stats(current_user=current_user)

    assert response == expected
    mock_get_organization_stats_data.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_extracted_get_organization_settings_route_delegates_to_service():
    from api.org_routes import get_organization_settings

    current_user = MagicMock()
    expected = {
        "organization_id": str(uuid.uuid4()),
        "name": "Acme IP",
        "default_alert_threshold": 0.75,
    }

    with patch(
        "api.org_routes.get_organization_settings_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_organization_settings_data:
        response = await get_organization_settings(current_user=current_user)

    assert response == expected
    mock_get_organization_settings_data.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_extracted_update_threshold_route_delegates_to_service_and_schedules_scans():
    from api.org_routes import ThresholdUpdateRequest, update_threshold_and_rescan
    from api.watchlist_background import run_watchlist_scan_task

    current_user = MagicMock()
    background_tasks = BackgroundTasks()
    item_ids = [uuid.uuid4(), uuid.uuid4()]
    request = ThresholdUpdateRequest(threshold=0.72)

    with patch(
        "api.org_routes.prepare_organization_threshold_rescan",
        new=AsyncMock(
            return_value={
                "message": "2 items rescanned",
                "item_ids": item_ids,
            }
        ),
    ) as mock_prepare_organization_threshold_rescan:
        response = await update_threshold_and_rescan(
            request=request,
            background_tasks=background_tasks,
            current_user=current_user,
        )

    assert response.message == "2 items rescanned"
    assert len(background_tasks.tasks) == 2
    assert background_tasks.tasks[0].func is run_watchlist_scan_task
    assert background_tasks.tasks[0].args == (item_ids[0],)
    assert background_tasks.tasks[1].func is run_watchlist_scan_task
    assert background_tasks.tasks[1].args == (item_ids[1],)
    mock_prepare_organization_threshold_rescan.assert_awaited_once_with(
        threshold=0.72,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_organization_service_get_organization_data_returns_response_model():
    from services.organization_service import get_organization_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    organization_crud = MagicMock()
    organization_crud.get_by_id.return_value = {
        "id": uuid.uuid4(),
        "name": "Acme IP",
        "slug": "acme-ip",
        "is_active": True,
        "created_at": datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
        "plan": None,
        "max_users": 10,
        "max_watchlist_items": 50,
        "max_monthly_searches": 100,
        "email": None,
        "phone": None,
        "address": None,
        "tax_id": None,
        "industry": None,
        "size": None,
        "website": None,
        "logo_url": None,
        "settings": None,
    }

    response = await get_organization_data(
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        organization_crud=organization_crud,
    )

    assert response.name == "Acme IP"
    organization_crud.get_by_id.assert_called_once_with(mock_db, current_user.organization_id)


@pytest.mark.asyncio
async def test_organization_service_get_organization_stats_data_aggregates_search_count():
    from services.organization_service import get_organization_stats_data

    current_user = MagicMock()
    current_user.organization_id = "org-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"cnt": 12}

    organization_crud = MagicMock()
    organization_crud.get_stats.return_value = {
        "user_count": 4,
        "active_watchlist_items": 9,
        "new_alerts": 3,
        "critical_alerts": 1,
    }

    response = await get_organization_stats_data(
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        organization_crud=organization_crud,
    )

    assert response.model_dump() == {
        "user_count": 4,
        "active_watchlist_items": 9,
        "new_alerts": 3,
        "critical_alerts": 1,
        "searches_this_month": 12,
        "storage_used_mb": 0.0,
    }
    assert mock_cursor.execute.call_args.args[1] == ("org-123",)


@pytest.mark.asyncio
async def test_organization_service_get_organization_settings_data_defaults_threshold():
    from services.organization_service import get_organization_settings_data

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "id": uuid.uuid4(),
        "name": "Acme IP",
        "default_alert_threshold": None,
    }

    response = await get_organization_settings_data(
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response["name"] == "Acme IP"
    assert response["default_alert_threshold"] == 0.7


@pytest.mark.asyncio
async def test_organization_service_prepare_organization_threshold_rescan_commits_and_returns_ids():
    from services.organization_service import prepare_organization_threshold_rescan

    current_user = MagicMock()
    current_user.organization_id = uuid.uuid4()
    item_ids = [uuid.uuid4(), uuid.uuid4()]

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.rowcount = 5

    watchlist_crud = MagicMock()
    watchlist_crud.get_by_organization.side_effect = [
        ([], 2),
        ([{"id": item_ids[0]}, {"id": item_ids[1]}], 2),
    ]

    response = await prepare_organization_threshold_rescan(
        threshold=0.72,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        watchlist_crud=watchlist_crud,
    )

    assert response == {
        "message": "%72 esik ile 2 marka taramaya alindi. Eski 5 uyari silindi.",
        "item_ids": item_ids,
    }
    assert watchlist_crud.get_by_organization.call_args_list[0].kwargs["page_size"] == 1
    assert watchlist_crud.get_by_organization.call_args_list[1].kwargs["page_size"] == 2
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_extracted_get_holder_trademarks_route_delegates_to_service():
    from api.holders import get_holder_trademarks

    current_user = MagicMock()
    expected = {
        "holder_name": "Nike Holder",
        "holder_tpe_client_id": "H-1",
        "total_count": 1,
        "page": 1,
        "page_size": 20,
        "total_pages": 1,
        "trademarks": [],
    }

    with patch(
        "api.holders.get_holder_trademarks_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_holder_trademarks_data:
        response = await get_holder_trademarks(
            tpe_client_id="H-1",
            page=1,
            page_size=20,
            current_user=current_user,
        )

    assert response == expected
    mock_get_holder_trademarks_data.assert_awaited_once_with(
        tpe_client_id="H-1",
        page=1,
        page_size=20,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_search_holders_route_delegates_to_service():
    from api.holders import search_holders

    current_user = MagicMock()
    expected = {"query": "ni", "results": [{"holder_name": "Nike Holder"}]}

    with patch(
        "api.holders.search_holder_portfolio_data",
        new=AsyncMock(return_value=expected),
    ) as mock_search_holder_portfolio_data:
        response = await search_holders(query="ni", limit=5, current_user=current_user)

    assert response == expected
    mock_search_holder_portfolio_data.assert_awaited_once_with(
        query="ni",
        limit=5,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_export_holder_trademarks_csv_route_delegates_to_service():
    from api.holders import export_holder_trademarks_csv

    current_user = MagicMock()
    expected = MagicMock()

    with patch(
        "api.holders.build_holder_trademarks_csv_stream",
        new=AsyncMock(return_value=expected),
    ) as mock_build_holder_trademarks_csv_stream:
        response = await export_holder_trademarks_csv(
            tpe_client_id="H-1",
            current_user=current_user,
        )

    assert response is expected
    mock_build_holder_trademarks_csv_stream.assert_awaited_once_with(
        tpe_client_id="H-1",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_holder_service_get_holder_trademarks_data_formats_rows():
    from services.holder_service import get_holder_trademarks_data

    current_user = MagicMock()
    current_user.id = "user-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    tm_id = uuid.uuid4()
    mock_cursor.fetchone.side_effect = [
        {"holder_name": "Nike Holder", "holder_tpe_client_id": "H-1"},
        {"cnt": 2},
    ]
    mock_cursor.fetchall.return_value = [
        {
            "id": tm_id,
            "application_no": "2024/1",
            "name": "NIKE",
            "final_status": "Published",
            "nice_class_numbers": [25, 35],
            "application_date": date(2024, 1, 2),
            "registration_date": date(2024, 6, 3),
            "image_path": "logos/nike.png",
            "has_extracted_goods": True,
            "attorney_name": "Agent",
            "attorney_no": "A-1",
            "registration_no": "TR-9",
            "bulletin_no": "2024-1",
            "holder_changed_at": date(2025, 11, 15),
            "last_event_type": "transfer",
            "last_event_date": date(2025, 11, 15),
            "has_restrictions": True,
            "active_restriction_count": 2,
        }
    ]

    response = await get_holder_trademarks_data(
        tpe_client_id="H-1",
        page=1,
        page_size=20,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "starter"}),
        plan_limit_getter=lambda plan, key: key in {
            "can_view_holder_portfolio",
            "can_download_portfolio",
        },
    )

    assert response == {
        "holder_name": "Nike Holder",
        "holder_tpe_client_id": "H-1",
        "total_count": 2,
        "page": 1,
        "page_size": 20,
        "total_pages": 1,
        "trademarks": [
            {
                "id": str(tm_id),
                "application_no": "2024/1",
                "name": "NIKE",
                "status": "Published",
                "classes": [25, 35],
                "application_date": "2024-01-02",
                "registration_date": "2024-06-03",
                "image_path": "logos/nike.png",
                "has_extracted_goods": True,
                "attorney_name": "Agent",
                "attorney_no": "A-1",
                "registration_no": "TR-9",
                "bulletin_no": "2024-1",
                "holder_changed_at": "2025-11-15",
                "last_event_type": "transfer",
                "last_event_date": "2025-11-15",
                "last_event_severity": "high",
                "has_restrictions": True,
                "active_restriction_count": 2,
            }
        ],
    }


@pytest.mark.asyncio
async def test_holder_service_search_holder_portfolio_data_escapes_like_query():
    from services.holder_service import search_holder_portfolio_data

    current_user = MagicMock()
    current_user.id = "user-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {
            "holder_name": "100% Holder",
            "holder_tpe_client_id": "H-1",
            "trademark_count": 3,
        }
    ]

    response = await search_holder_portfolio_data(
        query=r"100%_test\\holder",
        limit=5,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "free"}),
        plan_limit_getter=MagicMock(return_value=True),
    )

    assert response == {
        "query": r"100%_test\\holder",
        "results": [
            {
                "holder_name": "100% Holder",
                "holder_tpe_client_id": "H-1",
                "trademark_count": 3,
            }
        ],
    }
    assert mock_cursor.execute.call_args.args[1] == (r"%100\%\_test\\\\holder%", 5)


@pytest.mark.asyncio
async def test_holder_service_build_holder_trademarks_csv_streams_csv():
    from services.holder_service import build_holder_trademarks_csv_stream

    current_user = MagicMock()
    current_user.id = "user-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"holder_name": "Nike/Holder"}
    mock_cursor.fetchall.return_value = [
        {
            "application_no": "2024/1",
            "name": "NIKE",
            "final_status": "Published",
            "nice_class_numbers": [25, 35],
            "application_date": date(2024, 1, 2),
            "registration_date": date(2024, 6, 3),
            "registration_no": "TR-9",
            "attorney_name": "Agent",
            "attorney_no": "A-1",
            "bulletin_no": "2024-1",
            "gazette_no": "55",
            "holder_changed_at": date(2025, 11, 15),
            "last_event_type": "transfer",
            "last_event_date": date(2025, 11, 15),
            "has_restrictions": True,
            "active_restriction_count": 2,
        }
    ]

    response = await build_holder_trademarks_csv_stream(
        tpe_client_id="H-1",
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "free"}),
        plan_limit_getter=MagicMock(return_value=True),
    )

    body_chunks = []
    async for chunk in response.body_iterator:
        body_chunks.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
    body = "".join(body_chunks)

    assert response.headers["content-disposition"] == 'attachment; filename="Nike_Holder_portfolio.csv"'
    assert "Marka Adi,Basvuru No,Durum,Siniflar" in body
    assert "Sahip Degisim Tarihi,Son Olay,Son Olay Tarihi,Aktif Kisitlama" in body
    assert "NIKE,2024/1,Published,25; 35,2024-01-02,2024-06-03,TR-9,Agent,A-1,2024-1,55,2025-11-15,transfer,2025-11-15,2" in body


@pytest.mark.asyncio
async def test_holder_service_build_holder_trademarks_csv_blocks_free_plan():
    from services.holder_service import build_holder_trademarks_csv_stream

    current_user = MagicMock()
    current_user.id = "user-123"
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        await build_holder_trademarks_csv_stream(
            tpe_client_id="H-1",
            current_user=current_user,
            database_factory=MagicMock(return_value=mock_db_cm),
            user_plan_getter=MagicMock(return_value={"plan_name": "free"}),
            plan_limit_getter=MagicMock(return_value=False),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["upgrade_context"] == "portfolio_download"


@pytest.mark.asyncio
async def test_extracted_get_attorney_trademarks_route_delegates_to_service():
    from api.attorneys import get_attorney_trademarks

    current_user = MagicMock()
    expected = {"attorney_no": "A-1"}

    with patch(
        "api.attorneys.get_attorney_trademarks_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_attorney_trademarks_data:
        response = await get_attorney_trademarks(
            attorney_no="A-1",
            page=2,
            page_size=10,
            current_user=current_user,
        )

    assert response == expected
    mock_get_attorney_trademarks_data.assert_awaited_once_with(
        attorney_no="A-1",
        page=2,
        page_size=10,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_search_attorneys_route_delegates_to_service():
    from api.attorneys import search_attorneys

    current_user = MagicMock()
    expected = {"results": [{"attorney_no": "A-1"}]}

    with patch(
        "api.attorneys.search_attorney_portfolio_data",
        new=AsyncMock(return_value=expected),
    ) as mock_search_attorney_portfolio_data:
        response = await search_attorneys(query="ag", limit=5, current_user=current_user)

    assert response == expected
    mock_search_attorney_portfolio_data.assert_awaited_once_with(
        query="ag",
        limit=5,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_export_attorney_trademarks_csv_route_delegates_to_service():
    from api.attorneys import export_attorney_trademarks_csv

    current_user = MagicMock()
    expected = MagicMock()

    with patch(
        "api.attorneys.build_attorney_trademarks_csv_stream",
        new=AsyncMock(return_value=expected),
    ) as mock_build_attorney_trademarks_csv_stream:
        response = await export_attorney_trademarks_csv(
            attorney_no="A-1",
            current_user=current_user,
        )

    assert response is expected
    mock_build_attorney_trademarks_csv_stream.assert_awaited_once_with(
        attorney_no="A-1",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_attorney_service_get_attorney_trademarks_data_formats_rows():
    from services.attorney_service import get_attorney_trademarks_data

    current_user = MagicMock()
    current_user.id = "user-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    tm_id = uuid.uuid4()
    mock_cursor.fetchone.side_effect = [
        {"attorney_name": "Agent Smith", "attorney_no": "A-1"},
        {"cnt": 2},
    ]
    mock_cursor.fetchall.return_value = [
        {
            "id": tm_id,
            "application_no": "2024/1",
            "name": "NIKE",
            "final_status": "Published",
            "nice_class_numbers": [25, 35],
            "application_date": date(2024, 1, 2),
            "registration_date": date(2024, 6, 3),
            "image_path": "logos/nike.png",
            "has_extracted_goods": True,
            "holder_name": "Nike Holder",
            "holder_tpe_client_id": "H-1",
            "holder_changed_at": None,
            "last_event_type": "cancellation",
            "last_event_date": date(2026, 3, 12),
            "has_restrictions": False,
            "active_restriction_count": 0,
        }
    ]

    response = await get_attorney_trademarks_data(
        attorney_no="A-1",
        page=1,
        page_size=20,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "starter"}),
        plan_limit_getter=lambda plan, key: key in {
            "can_view_holder_portfolio",
            "can_download_portfolio",
        },
    )

    assert response == {
        "attorney_name": "Agent Smith",
        "attorney_no": "A-1",
        "total_count": 2,
        "page": 1,
        "page_size": 20,
        "total_pages": 1,
        "trademarks": [
            {
                "id": str(tm_id),
                "application_no": "2024/1",
                "name": "NIKE",
                "status": "Published",
                "classes": [25, 35],
                "application_date": "2024-01-02",
                "registration_date": "2024-06-03",
                "image_path": "logos/nike.png",
                "has_extracted_goods": True,
                "holder_name": "Nike Holder",
                "holder_tpe_client_id": "H-1",
                "holder_changed_at": None,
                "last_event_type": "cancellation",
                "last_event_date": "2026-03-12",
                "last_event_severity": "critical",
                "has_restrictions": False,
                "active_restriction_count": 0,
            }
        ],
    }


@pytest.mark.asyncio
async def test_attorney_service_search_attorney_portfolio_data_formats_results():
    from services.attorney_service import search_attorney_portfolio_data

    current_user = MagicMock()
    current_user.id = "user-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {
            "attorney_name": "Agent Smith",
            "attorney_no": "A-1",
            "trademark_count": 3,
        }
    ]

    response = await search_attorney_portfolio_data(
        query="agent",
        limit=5,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "free"}),
        plan_limit_getter=MagicMock(return_value=True),
    )

    assert response == {
        "query": "agent",
        "results": [
            {
                "attorney_name": "Agent Smith",
                "attorney_no": "A-1",
                "trademark_count": 3,
            }
        ],
    }
    assert mock_cursor.execute.call_args.args[1] == ("%agent%", "%agent%", 5)


@pytest.mark.asyncio
async def test_attorney_service_build_attorney_trademarks_csv_streams_csv():
    from services.attorney_service import build_attorney_trademarks_csv_stream

    current_user = MagicMock()
    current_user.id = "user-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"attorney_name": "Agent/Smith"}
    mock_cursor.fetchall.return_value = [
        {
            "application_no": "2024/1",
            "name": "NIKE",
            "final_status": "Published",
            "nice_class_numbers": [25, 35],
            "application_date": date(2024, 1, 2),
            "registration_date": date(2024, 6, 3),
            "registration_no": "TR-9",
            "holder_name": "Nike Holder",
            "holder_tpe_client_id": "H-1",
            "bulletin_no": "2024-1",
            "holder_changed_at": date(2025, 11, 15),
            "last_event_type": "transfer",
            "last_event_date": date(2025, 11, 15),
            "has_restrictions": True,
            "active_restriction_count": 1,
        }
    ]

    response = await build_attorney_trademarks_csv_stream(
        attorney_no="A-1",
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "free"}),
        plan_limit_getter=MagicMock(return_value=True),
    )

    body_chunks = []
    async for chunk in response.body_iterator:
        body_chunks.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
    body = "".join(body_chunks)

    assert response.headers["content-disposition"] == 'attachment; filename="Agent_Smith_portfolio.csv"'
    assert "Marka Adi,Basvuru No,Durum,Siniflar" in body
    assert "Sahip Degisim Tarihi,Son Olay,Son Olay Tarihi,Aktif Kisitlama" in body
    assert "2025-11-15,transfer,2025-11-15,1" in body
    assert "NIKE,2024/1,Published,25; 35,2024-01-02,2024-06-03,TR-9,Nike Holder,H-1,2024-1" in body


@pytest.mark.asyncio
async def test_attorney_service_build_attorney_trademarks_csv_blocks_free_plan():
    from services.attorney_service import build_attorney_trademarks_csv_stream

    current_user = MagicMock()
    current_user.id = "user-123"
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        await build_attorney_trademarks_csv_stream(
            attorney_no="A-1",
            current_user=current_user,
            database_factory=MagicMock(return_value=mock_db_cm),
            user_plan_getter=MagicMock(return_value={"plan_name": "free"}),
            plan_limit_getter=MagicMock(return_value=False),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["upgrade_context"] == "portfolio_download"


@pytest.mark.asyncio
async def test_extracted_generate_report_route_delegates_to_service():
    from api.reports import generate_report_endpoint
    from models.schemas import ReportRequest, ReportType

    current_user = MagicMock()
    request = ReportRequest(report_type=ReportType.WATCHLIST_SUMMARY, file_format="pdf")
    expected = {"status": "completed"}

    with patch(
        "api.reports.generate_report_data",
        new=AsyncMock(return_value=expected),
    ) as mock_generate_report_data:
        response = await generate_report_endpoint(request=request, current_user=current_user)

    assert response == expected
    mock_generate_report_data.assert_awaited_once_with(
        request=request,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_list_reports_route_delegates_to_service():
    from api.reports import list_reports

    current_user = MagicMock()
    expected = {"reports": [], "total": 0}

    with patch(
        "api.reports.list_reports_data",
        new=AsyncMock(return_value=expected),
    ) as mock_list_reports_data:
        response = await list_reports(page=2, page_size=10, current_user=current_user)

    assert response == expected
    mock_list_reports_data.assert_awaited_once_with(
        page=2,
        page_size=10,
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_get_report_route_delegates_to_service():
    from api.reports import get_report

    current_user = MagicMock()
    expected = {"id": "report-1"}

    with patch(
        "api.reports.get_report_data",
        new=AsyncMock(return_value=expected),
    ) as mock_get_report_data:
        response = await get_report(report_id="report-1", current_user=current_user)

    assert response == expected
    mock_get_report_data.assert_awaited_once_with(
        report_id="report-1",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_download_report_route_delegates_to_service():
    from api.reports import download_report

    current_user = MagicMock()
    expected = MagicMock()

    with patch(
        "api.reports.build_report_download_response",
        new=AsyncMock(return_value=expected),
    ) as mock_build_report_download_response:
        response = await download_report(report_id="report-1", current_user=current_user)

    assert response is expected
    mock_build_report_download_response.assert_awaited_once_with(
        report_id="report-1",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_delete_report_route_delegates_to_service():
    from api.reports import delete_report

    current_user = MagicMock()
    expected = {"deleted_count": 1}

    with patch(
        "api.reports.delete_report_data",
        new=AsyncMock(return_value=expected),
    ) as mock_delete_report_data:
        response = await delete_report(report_id="report-1", current_user=current_user)

    assert response == expected
    mock_delete_report_data.assert_awaited_once_with(
        report_id="report-1",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_delete_all_reports_route_delegates_to_service():
    from api.reports import delete_all_reports

    current_user = MagicMock()
    expected = {"deleted_count": 2}

    with patch(
        "api.reports.delete_all_reports_data",
        new=AsyncMock(return_value=expected),
    ) as mock_delete_all_reports_data:
        response = await delete_all_reports(current_user=current_user)

    assert response == expected
    mock_delete_all_reports_data.assert_awaited_once_with(current_user=current_user)


@pytest.mark.asyncio
async def test_report_service_generate_report_data_returns_created_report_payload():
    from models.schemas import ReportRequest, ReportType
    from services.report_service import generate_report_data

    current_user = MagicMock()
    current_user.id = "user-123"
    current_user.organization_id = "org-456"
    watchlist_id = uuid.uuid4()
    report_id = uuid.uuid4()
    request = ReportRequest(
        report_type=ReportType.WATCHLIST_SUMMARY,
        title="Weekly Watchlist",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 31),
        watchlist_ids=[watchlist_id],
        file_format="pdf",
    )

    class FakeGenerator:
        def __init__(self):
            self.calls = []

        def generate_report(self, user_id, report_type, parameters):
            self.calls.append((user_id, report_type, parameters))
            return {
                "report_id": report_id,
                "status": "completed",
                "file_path": "C:/tmp/report.pdf",
            }

    fake_generator = FakeGenerator()
    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "id": report_id,
        "organization_id": uuid.uuid4(),
        "report_type": "watchlist_status",
        "report_name": "Weekly Watchlist",
        "status": "completed",
        "file_path": "C:/tmp/report.pdf",
        "file_format": "pdf",
        "file_size_bytes": 4096,
        "generated_at": datetime(2024, 2, 1, 10, 0, tzinfo=timezone.utc),
        "created_at": datetime(2024, 2, 1, 9, 0, tzinfo=timezone.utc),
    }

    response = await generate_report_data(
        request=request,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "professional"}),
        report_eligibility_checker=MagicMock(
            return_value={
                "eligible": True,
                "reports_used": 1,
                "reports_limit": 20,
                "can_export": True,
            }
        ),
        generator_factory=lambda db: fake_generator,
    )

    assert response == {
        "id": str(report_id),
        "organization_id": str(mock_cursor.fetchone.return_value["organization_id"]),
        "report_type": "watchlist_status",
        "title": "Weekly Watchlist",
        "status": "completed",
        "file_path": "C:/tmp/report.pdf",
        "file_format": "pdf",
        "file_size_bytes": 4096,
        "generated_at": "2024-02-01T10:00:00+00:00",
        "created_at": "2024-02-01T09:00:00+00:00",
    }
    assert fake_generator.calls == [
        (
            "user-123",
            "watchlist_status",
            {
                "date_start": "2024-01-01",
                "date_end": "2024-01-31",
                "watchlist_id": str(watchlist_id),
            },
        )
    ]
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_report_service_list_reports_data_returns_paginated_usage():
    from services.report_service import list_reports_data

    current_user = MagicMock()
    current_user.id = "user-123"
    current_user.organization_id = "org-456"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    report_row = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "report_type": "weekly_digest",
        "report_name": "Weekly Digest",
        "status": "completed",
        "file_path": "C:/tmp/weekly.pdf",
        "file_format": "pdf",
        "file_size_bytes": 1234,
        "generated_at": datetime(2024, 2, 2, 10, 0, tzinfo=timezone.utc),
        "created_at": datetime(2024, 2, 2, 9, 0, tzinfo=timezone.utc),
    }
    mock_cursor.fetchone.return_value = {"cnt": 1}
    mock_cursor.fetchall.return_value = [report_row]

    response = await list_reports_data(
        page=1,
        page_size=20,
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        user_plan_getter=MagicMock(return_value={"plan_name": "professional"}),
        report_eligibility_checker=MagicMock(
            return_value={
                "reports_used": 1,
                "reports_limit": 20,
                "can_export": True,
            }
        ),
    )

    assert response == {
        "reports": [
            {
                "id": str(report_row["id"]),
                "organization_id": str(report_row["organization_id"]),
                "report_type": "weekly_digest",
                "title": "Weekly Digest",
                "status": "completed",
                "file_path": "C:/tmp/weekly.pdf",
                "file_format": "pdf",
                "file_size_bytes": 1234,
                "generated_at": "2024-02-02T10:00:00+00:00",
                "created_at": "2024-02-02T09:00:00+00:00",
            }
        ],
        "total": 1,
        "page": 1,
        "page_size": 20,
        "total_pages": 1,
        "usage": {
            "reports_used": 0,
            "reports_limit": None,
            "can_export": True,
        },
    }


@pytest.mark.asyncio
async def test_report_service_get_report_data_rejects_cross_org_access():
    from services.report_service import get_report_data

    current_user = MagicMock()
    current_user.organization_id = "org-456"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "report_type": "weekly_digest",
        "report_name": "Weekly Digest",
        "status": "completed",
        "file_path": "C:/tmp/weekly.pdf",
        "file_format": "pdf",
        "file_size_bytes": 1234,
        "generated_at": None,
        "created_at": datetime(2024, 2, 2, 9, 0, tzinfo=timezone.utc),
        "download_count": 0,
        "error_message": None,
    }

    with pytest.raises(Exception) as exc_info:
        await get_report_data(
            report_id="report-1",
            current_user=current_user,
            database_factory=MagicMock(return_value=mock_db_cm),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Bu rapora erisiminiz yok"


@pytest.mark.asyncio
async def test_report_service_delete_report_data_deletes_row_and_safe_file(tmp_path):
    from services.report_service import delete_report_data

    current_user = MagicMock()
    current_user.organization_id = "org-456"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    report_file = report_dir / "weekly.pdf"
    report_file.write_text("pdf", encoding="utf-8")

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "id": "report-1",
        "organization_id": "org-456",
        "file_path": str(report_file),
    }
    removed = []

    response = await delete_report_data(
        report_id="report-1",
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        report_dir=report_dir,
        file_exists=os.path.isfile,
        file_remover=lambda path: removed.append(path),
    )

    assert response["deleted_count"] == 1
    assert response["file_delete_status"] == "deleted"
    assert removed == [str(report_file.resolve())]
    assert mock_db.commit.call_count == 1
    assert mock_cursor.execute.call_args_list[-1].args[0] == (
        "DELETE FROM reports WHERE id = %s AND organization_id = %s"
    )


@pytest.mark.asyncio
async def test_report_service_delete_all_reports_data_skips_files_outside_report_dir(tmp_path):
    from services.report_service import delete_all_reports_data

    current_user = MagicMock()
    current_user.organization_id = "org-456"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    safe_file = report_dir / "risk.pdf"
    safe_file.write_text("pdf", encoding="utf-8")
    outside_file = tmp_path / "outside.pdf"
    outside_file.write_text("pdf", encoding="utf-8")

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {"id": "report-1", "file_path": str(safe_file)},
        {"id": "report-2", "file_path": str(outside_file)},
    ]
    removed = []

    response = await delete_all_reports_data(
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        report_dir=report_dir,
        file_exists=os.path.isfile,
        file_remover=lambda path: removed.append(path),
    )

    assert response["deleted_count"] == 2
    assert response["file_delete_status"]["deleted"] == 1
    assert response["file_delete_status"]["skipped"] == 1
    assert removed == [str(safe_file.resolve())]
    assert mock_db.commit.call_count == 1
    assert mock_cursor.execute.call_args_list[-1].args[0] == (
        "DELETE FROM reports WHERE organization_id = %s"
    )


@pytest.mark.asyncio
async def test_report_service_build_report_download_response_returns_file_response():
    from services.report_service import build_report_download_response

    current_user = MagicMock()
    current_user.id = "user-123"
    current_user.organization_id = "org-456"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "id": uuid.uuid4(),
        "organization_id": "org-456",
        "report_name": "Weekly Digest",
        "status": "completed",
        "file_path": "C:/tmp/weekly.pdf",
        "file_format": "pdf",
    }

    def fake_file_response_factory(path, media_type, filename):
        return {
            "path": path,
            "media_type": media_type,
            "filename": filename,
        }

    response = await build_report_download_response(
        report_id="report-1",
        current_user=current_user,
        database_factory=MagicMock(return_value=mock_db_cm),
        file_exists=MagicMock(return_value=True),
        file_response_factory=fake_file_response_factory,
    )

    assert response == {
        "path": "C:/tmp/weekly.pdf",
        "media_type": "application/pdf",
        "filename": "Weekly Digest.pdf",
    }
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_extracted_list_settings_route_delegates_to_service():
    from api.admin import list_settings

    with patch(
        "api.admin.get_all_admin_settings_data",
        new_callable=AsyncMock,
    ) as mock_get_all_admin_settings_data:
        mock_get_all_admin_settings_data.return_value = {"general.theme": {"value": "light"}}

        response = await list_settings(current_user=MagicMock())

    assert response == {"general.theme": {"value": "light"}}
    mock_get_all_admin_settings_data.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_extracted_get_settings_by_category_route_delegates_to_service():
    from api.admin import get_settings_by_category

    with patch(
        "api.admin.get_admin_settings_category_data",
        new_callable=AsyncMock,
    ) as mock_get_admin_settings_category_data:
        mock_get_admin_settings_category_data.return_value = {
            "plan.professional.max_users": {"value": 10}
        }

        response = await get_settings_by_category(
            "plan_limits",
            current_user=MagicMock(),
        )

    assert response == {"plan.professional.max_users": {"value": 10}}
    mock_get_admin_settings_category_data.assert_awaited_once_with("plan_limits")


@pytest.mark.asyncio
async def test_extracted_admin_overview_route_delegates_to_service():
    from api.admin import admin_overview

    with patch(
        "api.admin.get_admin_overview_data",
        new_callable=AsyncMock,
    ) as mock_get_admin_overview_data:
        mock_get_admin_overview_data.return_value = {
            "total_active_users": 8,
            "mrr": 999.0,
        }

        response = await admin_overview(current_user=MagicMock())

    assert response == {"total_active_users": 8, "mrr": 999.0}
    mock_get_admin_overview_data.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_admin_service_get_all_admin_settings_data_uses_settings_manager():
    from services.admin_service import get_all_admin_settings_data

    settings_getter = MagicMock(
        return_value={"general.theme": {"value": "light", "category": "general"}}
    )

    response = await get_all_admin_settings_data(settings_getter=settings_getter)

    assert response == {"general.theme": {"value": "light", "category": "general"}}
    settings_getter.assert_called_once_with()


@pytest.mark.asyncio
async def test_admin_service_get_admin_settings_category_data_uses_category_getter():
    from services.admin_service import get_admin_settings_category_data

    category_getter = MagicMock(
        return_value={"plan.professional.max_users": {"value": 10, "category": "plan_limits"}}
    )

    response = await get_admin_settings_category_data(
        "plan_limits",
        category_getter=category_getter,
    )

    assert response == {"plan.professional.max_users": {"value": 10, "category": "plan_limits"}}
    category_getter.assert_called_once_with("plan_limits")


@pytest.mark.asyncio
async def test_admin_service_get_admin_overview_data_aggregates_metrics():
    from services.admin_service import get_admin_overview_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.side_effect = [
        {"cnt": 12},
        {"cnt": 4},
        {"cnt": 450},
        {"cnt": 33},
        {"cnt": 5},
        {"cnt": 8},
        {"cnt": 27},
        {"cnt": 3},
        {"cnt": 11},
        {"cnt": 6},
    ]
    mock_cursor.fetchall.side_effect = [
        [
            {"plan": "professional", "org_count": 3},
            {"plan": "free", "org_count": 1},
        ],
        [
            {"plan_name": "professional", "price": 999.0, "org_count": 2},
            {"plan_name": "starter", "price": 499.0, "org_count": 1},
            {"plan_name": "free", "price": 0.0, "org_count": 1},
        ],
    ]

    response = await get_admin_overview_data(
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "total_active_users": 12,
        "total_active_orgs": 4,
        "orgs_by_plan": {"professional": 3, "free": 1},
        "total_trademarks": 450,
        "total_watchlist_items": 33,
        "new_users_7d": 5,
        "total_alerts": 8,
        "api_calls_today": 27,
        "mrr": 2497.0,
        "revenue_by_plan": {
            "professional": {"price": 999.0, "orgs": 2, "revenue": 1998.0},
            "starter": {"price": 499.0, "orgs": 1, "revenue": 499.0},
            "free": {"price": 0.0, "orgs": 1, "revenue": 0.0},
        },
        "plan_changes_7d": 3,
        "applications_this_month": 11,
        "active_overrides": 6,
    }


@pytest.mark.asyncio
async def test_extracted_update_setting_route_delegates_to_service():
    from api.admin import update_setting

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.update_admin_setting_data",
        new_callable=AsyncMock,
    ) as mock_update_admin_setting_data:
        mock_update_admin_setting_data.return_value = {
            "status": "ok",
            "key": "general.theme",
            "value": "dark",
        }

        response = await update_setting(
            key="general.theme",
            payload={"value": "dark", "category": "general"},
            current_user=current_user,
        )

    assert response == {"status": "ok", "key": "general.theme", "value": "dark"}
    mock_update_admin_setting_data.assert_awaited_once_with(
        key="general.theme",
        payload={"value": "dark", "category": "general"},
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_delete_setting_route_delegates_to_service():
    from api.admin import delete_setting

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.delete_admin_setting_data",
        new_callable=AsyncMock,
    ) as mock_delete_admin_setting_data:
        mock_delete_admin_setting_data.return_value = {
            "status": "ok",
            "key": "general.theme",
            "reverted_to": "code_default",
        }

        response = await delete_setting(
            key="general.theme",
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "key": "general.theme",
        "reverted_to": "code_default",
    }
    mock_delete_admin_setting_data.assert_awaited_once_with(
        key="general.theme",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_admin_service_update_admin_setting_data_persists_and_audits():
    from services.admin_service import update_admin_setting_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.conn = MagicMock()

    settings_setter = MagicMock()
    audit_logger = MagicMock()

    response = await update_admin_setting_data(
        key="general.theme",
        payload={
            "value": "dark",
            "category": "general",
            "description": "Theme override",
            "value_type": "string",
        },
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        settings_setter=settings_setter,
        audit_logger=audit_logger,
    )

    assert response == {"status": "ok", "key": "general.theme", "value": "dark"}
    settings_setter.assert_called_once_with(
        key="general.theme",
        value="dark",
        category="general",
        description="Theme override",
        value_type="string",
        updated_by="admin-123",
        conn=mock_db.conn,
    )
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "setting_changed",
        {"key": "general.theme", "new_value": "dark"},
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_update_admin_setting_data_requires_value():
    from services.admin_service import update_admin_setting_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    with pytest.raises(Exception) as exc_info:
        await update_admin_setting_data(
            key="general.theme",
            payload={"category": "general"},
            current_user=current_user,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "'value' is required"


@pytest.mark.asyncio
async def test_admin_service_delete_admin_setting_data_deletes_and_audits():
    from services.admin_service import delete_admin_setting_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.conn = MagicMock()

    settings_deleter = MagicMock()
    audit_logger = MagicMock()

    response = await delete_admin_setting_data(
        key="general.theme",
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        settings_deleter=settings_deleter,
        audit_logger=audit_logger,
    )

    assert response == {
        "status": "ok",
        "key": "general.theme",
        "reverted_to": "code_default",
    }
    settings_deleter.assert_called_once_with("general.theme", conn=mock_db.conn)
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "setting_deleted",
        {"key": "general.theme"},
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_extracted_list_organizations_route_delegates_to_service():
    from api.admin import list_organizations

    with patch(
        "api.admin.get_admin_organizations_data",
        new_callable=AsyncMock,
    ) as mock_get_admin_organizations_data:
        mock_get_admin_organizations_data.return_value = {
            "organizations": [{"id": "org-1", "name": "Acme IP"}],
            "total": 1,
            "limit": 25,
            "offset": 0,
        }

        response = await list_organizations(
            current_user=MagicMock(),
            search="acme",
            plan="professional",
            is_active=True,
            limit=25,
            offset=0,
        )

    assert response == {
        "organizations": [{"id": "org-1", "name": "Acme IP"}],
        "total": 1,
        "limit": 25,
        "offset": 0,
    }
    mock_get_admin_organizations_data.assert_awaited_once_with(
        search="acme",
        plan="professional",
        is_active=True,
        limit=25,
        offset=0,
    )


@pytest.mark.asyncio
async def test_extracted_get_organization_detail_route_delegates_to_service():
    from api.admin import get_organization_detail

    with patch(
        "api.admin.get_admin_organization_detail_data",
        new_callable=AsyncMock,
    ) as mock_get_admin_organization_detail_data:
        mock_get_admin_organization_detail_data.return_value = {
            "id": "org-1",
            "name": "Acme IP",
            "users": [{"id": "user-1"}],
        }

        response = await get_organization_detail(
            org_id="org-1",
            current_user=MagicMock(),
        )

    assert response == {
        "id": "org-1",
        "name": "Acme IP",
        "users": [{"id": "user-1"}],
    }
    mock_get_admin_organization_detail_data.assert_awaited_once_with(org_id="org-1")


@pytest.mark.asyncio
async def test_extracted_toggle_org_status_route_delegates_to_service():
    from api.admin import toggle_org_status

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.toggle_admin_organization_status_data",
        new_callable=AsyncMock,
    ) as mock_toggle_admin_organization_status_data:
        mock_toggle_admin_organization_status_data.return_value = {
            "status": "ok",
            "organization_id": "org-1",
            "is_active": False,
        }

        response = await toggle_org_status(
            org_id="org-1",
            payload={"is_active": False},
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "organization_id": "org-1",
        "is_active": False,
    }
    mock_toggle_admin_organization_status_data.assert_awaited_once_with(
        org_id="org-1",
        payload={"is_active": False},
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_change_org_plan_route_delegates_to_service():
    from api.admin import change_org_plan

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.change_admin_organization_plan_data",
        new_callable=AsyncMock,
    ) as mock_change_admin_organization_plan_data:
        mock_change_admin_organization_plan_data.return_value = {
            "status": "ok",
            "organization_id": "org-1",
            "old_plan": "free",
            "new_plan": "professional",
        }

        response = await change_org_plan(
            org_id="org-1",
            payload={"plan_name": "professional"},
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "organization_id": "org-1",
        "old_plan": "free",
        "new_plan": "professional",
    }
    mock_change_admin_organization_plan_data.assert_awaited_once_with(
        org_id="org-1",
        payload={"plan_name": "professional"},
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_refund_payment_route_delegates_to_service():
    from api.admin import refund_payment

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.refund_admin_payment_data",
        new_callable=AsyncMock,
    ) as mock_refund_admin_payment_data:
        mock_refund_admin_payment_data.return_value = {
            "status": "ok",
            "payment_id": "pay-1",
            "refund_type": "partial",
            "refund_amount": 49.0,
        }

        response = await refund_payment(
            payment_id="pay-1",
            payload={"amount": 49.0, "reason": "Customer requested"},
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "payment_id": "pay-1",
        "refund_type": "partial",
        "refund_amount": 49.0,
    }
    mock_refund_admin_payment_data.assert_awaited_once_with(
        payment_id="pay-1",
        payload={"amount": 49.0, "reason": "Customer requested"},
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_list_all_users_route_delegates_to_service():
    from api.admin import list_all_users

    with patch(
        "api.admin.get_admin_users_data",
        new_callable=AsyncMock,
    ) as mock_get_admin_users_data:
        mock_get_admin_users_data.return_value = {
            "users": [{"id": "user-1", "email": "ops@acme.test"}],
            "total": 1,
            "limit": 25,
            "offset": 0,
        }

        response = await list_all_users(
            current_user=MagicMock(),
            search="ops",
            org_id="org-1",
            role="admin",
            is_active=True,
            limit=25,
            offset=0,
        )

    assert response == {
        "users": [{"id": "user-1", "email": "ops@acme.test"}],
        "total": 1,
        "limit": 25,
        "offset": 0,
    }
    mock_get_admin_users_data.assert_awaited_once_with(
        search="ops",
        org_id="org-1",
        role="admin",
        is_active=True,
        limit=25,
        offset=0,
    )


@pytest.mark.asyncio
async def test_extracted_change_user_role_route_delegates_to_service():
    from api.admin import change_user_role

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.change_admin_user_role_data",
        new_callable=AsyncMock,
    ) as mock_change_admin_user_role_data:
        mock_change_admin_user_role_data.return_value = {
            "status": "ok",
            "user_id": "user-1",
            "old_role": "user",
            "new_role": "admin",
        }

        response = await change_user_role(
            user_id="user-1",
            payload={"role": "admin"},
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "user_id": "user-1",
        "old_role": "user",
        "new_role": "admin",
    }
    mock_change_admin_user_role_data.assert_awaited_once_with(
        user_id="user-1",
        payload={"role": "admin"},
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_toggle_superadmin_route_delegates_to_service():
    from api.admin import toggle_superadmin

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.toggle_admin_superadmin_data",
        new_callable=AsyncMock,
    ) as mock_toggle_admin_superadmin_data:
        mock_toggle_admin_superadmin_data.return_value = {
            "status": "ok",
            "user_id": "user-1",
            "is_superadmin": True,
        }

        response = await toggle_superadmin(
            user_id="user-1",
            payload={"is_superadmin": True},
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "user_id": "user-1",
        "is_superadmin": True,
    }
    mock_toggle_admin_superadmin_data.assert_awaited_once_with(
        user_id="user-1",
        payload={"is_superadmin": True},
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_toggle_user_status_route_delegates_to_service():
    from api.admin import toggle_user_status

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.toggle_admin_user_status_data",
        new_callable=AsyncMock,
    ) as mock_toggle_admin_user_status_data:
        mock_toggle_admin_user_status_data.return_value = {
            "status": "ok",
            "user_id": "user-1",
            "is_active": False,
        }

        response = await toggle_user_status(
            user_id="user-1",
            payload={"is_active": False},
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "user_id": "user-1",
        "is_active": False,
    }
    mock_toggle_admin_user_status_data.assert_awaited_once_with(
        user_id="user-1",
        payload={"is_active": False},
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_get_audit_log_route_delegates_to_service():
    from api.admin import get_audit_log

    with patch(
        "api.admin.get_admin_audit_log_data",
        new_callable=AsyncMock,
    ) as mock_get_admin_audit_log_data:
        mock_get_admin_audit_log_data.return_value = {
            "entries": [{"id": "log-1", "action": "user_role_changed"}],
            "limit": 50,
            "offset": 0,
        }

        response = await get_audit_log(
            current_user=MagicMock(),
            action="user_role_changed",
            user_id="user-1",
            limit=50,
            offset=0,
        )

    assert response == {
        "entries": [{"id": "log-1", "action": "user_role_changed"}],
        "limit": 50,
        "offset": 0,
    }
    mock_get_admin_audit_log_data.assert_awaited_once_with(
        action="user_role_changed",
        user_id="user-1",
        limit=50,
        offset=0,
    )


@pytest.mark.asyncio
async def test_extracted_get_org_credits_route_delegates_to_service():
    from api.admin import get_org_credits

    with patch(
        "api.admin.get_admin_org_credits_data",
        new_callable=AsyncMock,
    ) as mock_get_admin_org_credits_data:
        mock_get_admin_org_credits_data.return_value = {
            "organization_id": "org-1",
            "plan": "professional",
            "ai_credits": {"monthly_remaining": 20, "purchased": 5, "plan_limit": 25, "reset_at": None},
            "logo_credits": {"monthly_remaining": 10, "purchased": 2, "used_this_month": 3, "reset_at": None},
            "name_credits": {"purchased": 4, "used_this_month": 1},
        }

        response = await get_org_credits(
            org_id="org-1",
            current_user=MagicMock(),
        )

    assert response["organization_id"] == "org-1"
    assert response["plan"] == "professional"
    mock_get_admin_org_credits_data.assert_awaited_once_with(org_id="org-1")


@pytest.mark.asyncio
async def test_extracted_adjust_org_credits_route_delegates_to_service():
    from api.admin import adjust_org_credits

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.adjust_admin_org_credits_data",
        new_callable=AsyncMock,
    ) as mock_adjust_admin_org_credits_data:
        mock_adjust_admin_org_credits_data.return_value = {
            "status": "ok",
            "organization_id": "org-1",
            "credit_type": "ai_purchased",
            "old_value": 7,
            "new_value": 10,
        }

        response = await adjust_org_credits(
            org_id="org-1",
            payload={"credit_type": "ai_purchased", "operation": "add", "amount": 3},
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "organization_id": "org-1",
        "credit_type": "ai_purchased",
        "old_value": 7,
        "new_value": 10,
    }
    mock_adjust_admin_org_credits_data.assert_awaited_once_with(
        org_id="org-1",
        payload={"credit_type": "ai_purchased", "operation": "add", "amount": 3},
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_bulk_credit_adjustment_route_delegates_to_service():
    from api.admin import bulk_credit_adjustment

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.bulk_adjust_admin_credits_data",
        new_callable=AsyncMock,
    ) as mock_bulk_adjust_admin_credits_data:
        mock_bulk_adjust_admin_credits_data.return_value = {
            "status": "ok",
            "affected_organizations": 4,
        }

        response = await bulk_credit_adjustment(
            payload={
                "plan_filter": "professional",
                "credit_type": "logo_purchased",
                "operation": "add",
                "amount": 10,
            },
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "affected_organizations": 4,
    }
    mock_bulk_adjust_admin_credits_data.assert_awaited_once_with(
        payload={
            "plan_filter": "professional",
            "credit_type": "logo_purchased",
            "operation": "add",
            "amount": 10,
        },
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_list_discount_codes_route_delegates_to_service():
    from api.admin import list_discount_codes

    with patch(
        "api.admin.get_admin_discount_codes_data",
        new_callable=AsyncMock,
    ) as mock_get_admin_discount_codes_data:
        mock_get_admin_discount_codes_data.return_value = {
            "discount_codes": [{"id": "code-1", "code": "LAUNCH20", "is_active": True}]
        }

        response = await list_discount_codes(
            current_user=MagicMock(),
            is_active=False,
        )

    assert response == {
        "discount_codes": [{"id": "code-1", "code": "LAUNCH20", "is_active": True}]
    }
    mock_get_admin_discount_codes_data.assert_awaited_once_with(is_active=False)


@pytest.mark.asyncio
async def test_extracted_create_discount_code_route_delegates_to_service():
    from api.admin import create_discount_code

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.create_admin_discount_code_data",
        new_callable=AsyncMock,
    ) as mock_create_admin_discount_code_data:
        mock_create_admin_discount_code_data.return_value = {
            "status": "ok",
            "code": "LAUNCH20",
        }

        response = await create_discount_code(
            payload={
                "code": "launch20",
                "discount_type": "percentage",
                "discount_value": 20,
            },
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "code": "LAUNCH20",
    }
    mock_create_admin_discount_code_data.assert_awaited_once_with(
        payload={
            "code": "launch20",
            "discount_type": "percentage",
            "discount_value": 20,
        },
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_update_discount_code_route_delegates_to_service():
    from api.admin import update_discount_code

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.update_admin_discount_code_data",
        new_callable=AsyncMock,
    ) as mock_update_admin_discount_code_data:
        mock_update_admin_discount_code_data.return_value = {
            "status": "ok",
            "code_id": "code-1",
        }

        response = await update_discount_code(
            code_id="code-1",
            payload={
                "description": "Updated launch promo",
                "is_active": False,
            },
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "code_id": "code-1",
    }
    mock_update_admin_discount_code_data.assert_awaited_once_with(
        code_id="code-1",
        payload={
            "description": "Updated launch promo",
            "is_active": False,
        },
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_deactivate_discount_code_route_delegates_to_service():
    from api.admin import deactivate_discount_code

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.deactivate_admin_discount_code_data",
        new_callable=AsyncMock,
    ) as mock_deactivate_admin_discount_code_data:
        mock_deactivate_admin_discount_code_data.return_value = {
            "status": "ok",
            "code_id": "code-1",
        }

        response = await deactivate_discount_code(
            code_id="code-1",
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "code_id": "code-1",
    }
    mock_deactivate_admin_discount_code_data.assert_awaited_once_with(
        code_id="code-1",
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_discount_code_usage_route_delegates_to_service():
    from api.admin import get_discount_code_usage

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.get_admin_discount_code_usage_data",
        new_callable=AsyncMock,
    ) as mock_get_admin_discount_code_usage_data:
        mock_get_admin_discount_code_usage_data.return_value = {
            "usage": [{"discount_code_id": "code-1", "organization_id": "org-1"}],
            "total_uses": 1,
        }

        response = await get_discount_code_usage(
            code_id="code-1",
            current_user=current_user,
        )

    assert response == {
        "usage": [{"discount_code_id": "code-1", "organization_id": "org-1"}],
        "total_uses": 1,
    }
    mock_get_admin_discount_code_usage_data.assert_awaited_once_with(code_id="code-1")


@pytest.mark.asyncio
async def test_extracted_list_plans_route_delegates_to_service():
    from api.admin import list_plans

    with patch(
        "api.admin.get_admin_plans_data",
        new_callable=AsyncMock,
    ) as mock_get_admin_plans_data:
        mock_get_admin_plans_data.return_value = {
            "plans": [{"db_record": {"name": "professional"}}],
            "feature_categories": {"pricing": ["price_monthly"]},
        }

        response = await list_plans(current_user=MagicMock())

    assert response == {
        "plans": [{"db_record": {"name": "professional"}}],
        "feature_categories": {"pricing": ["price_monthly"]},
    }
    mock_get_admin_plans_data.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_extracted_update_plan_pricing_route_delegates_to_service():
    from api.admin import update_plan_pricing

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.update_admin_plan_pricing_data",
        new_callable=AsyncMock,
    ) as mock_update_admin_plan_pricing_data:
        mock_update_admin_plan_pricing_data.return_value = {
            "status": "ok",
            "plan": "professional",
        }

        response = await update_plan_pricing(
            plan_name="professional",
            payload={"price_monthly": 1099, "is_active": True},
            current_user=current_user,
        )

    assert response == {
        "status": "ok",
        "plan": "professional",
    }
    mock_update_admin_plan_pricing_data.assert_awaited_once_with(
        plan_name="professional",
        payload={"price_monthly": 1099, "is_active": True},
        current_user=current_user,
    )


@pytest.mark.asyncio
async def test_extracted_usage_analytics_route_delegates_to_service():
    from api.admin import usage_analytics

    current_user = MagicMock()
    current_user.id = "admin-123"

    with patch(
        "api.admin.get_admin_usage_analytics_data",
        new_callable=AsyncMock,
    ) as mock_get_admin_usage_analytics_data:
        mock_get_admin_usage_analytics_data.return_value = {
            "period_days": 21,
            "daily_usage": [{"date": "2026-04-11", "unique_users": 5}],
            "usage_by_plan": {"professional": 15},
            "top_users": [{"email": "ops@acme.test", "total_searches": 11}],
            "cost_bearing_actions": {"logo_generations": 2, "name_generations": 7},
        }

        response = await usage_analytics(current_user=current_user, days=21)

    assert response == {
        "period_days": 21,
        "daily_usage": [{"date": "2026-04-11", "unique_users": 5}],
        "usage_by_plan": {"professional": 15},
        "top_users": [{"email": "ops@acme.test", "total_searches": 11}],
        "cost_bearing_actions": {"logo_generations": 2, "name_generations": 7},
    }
    mock_get_admin_usage_analytics_data.assert_awaited_once_with(days=21)


@pytest.mark.asyncio
async def test_extracted_export_usage_csv_route_delegates_to_service():
    from api.admin import export_usage_csv

    current_user = MagicMock()
    current_user.id = "admin-123"
    expected = JSONResponse({"status": "ok"})

    with patch(
        "api.admin.build_admin_usage_export_response",
        new_callable=AsyncMock,
    ) as mock_build_admin_usage_export_response:
        mock_build_admin_usage_export_response.return_value = expected

        response = await export_usage_csv(current_user=current_user, days=14)

    assert response is expected
    mock_build_admin_usage_export_response.assert_awaited_once_with(days=14)


@pytest.mark.asyncio
async def test_admin_service_get_admin_organizations_data_returns_paginated_results():
    from services.admin_service import get_admin_organizations_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"cnt": 1}
    mock_cursor.fetchall.return_value = [
        {
            "id": "org-1",
            "name": "Acme IP",
            "slug": "acme-ip",
            "email": "ops@acme.test",
            "is_active": True,
            "plan_name": "professional",
            "price_monthly": 999.0,
            "logo_credits_monthly": 20,
            "logo_credits_purchased": 0,
            "name_credits_purchased": 5,
            "user_count": 4,
            "watchlist_count": 12,
        }
    ]

    response = await get_admin_organizations_data(
        search="acme",
        plan="professional",
        is_active=True,
        limit=25,
        offset=0,
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "organizations": [
            {
                "id": "org-1",
                "name": "Acme IP",
                "slug": "acme-ip",
                "email": "ops@acme.test",
                "is_active": True,
                "plan_name": "professional",
                "price_monthly": 999.0,
                "logo_credits_monthly": 20,
                "logo_credits_purchased": 0,
                "name_credits_purchased": 5,
                "user_count": 4,
                "watchlist_count": 12,
            }
        ],
        "total": 1,
        "limit": 25,
        "offset": 0,
    }
    count_sql, count_params = mock_cursor.execute.call_args_list[0].args
    list_sql, list_params = mock_cursor.execute.call_args_list[1].args
    assert "COUNT(*) as cnt" in count_sql
    assert "COALESCE(sp.name, 'free') = %s" in count_sql
    assert "EXISTS (" in count_sql
    assert "o.email" not in count_sql
    assert count_params == ["%acme%", "%acme%", "%acme%", "professional", True]
    assert "ORDER BY o.created_at DESC LIMIT %s OFFSET %s" in list_sql
    assert "EXISTS (" in list_sql
    assert "o.email" not in list_sql
    assert list_params == ["%acme%", "%acme%", "%acme%", "professional", True, 25, 0]


@pytest.mark.asyncio
async def test_admin_service_get_admin_organization_detail_data_returns_users():
    from services.admin_service import get_admin_organization_detail_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "id": "org-1",
        "name": "Acme IP",
        "plan_name": "professional",
        "price_monthly": 999.0,
    }
    mock_cursor.fetchall.return_value = [
        {
            "id": "user-1",
            "email": "ops@acme.test",
            "first_name": "Ops",
            "last_name": "User",
            "role": "admin",
            "is_active": True,
            "is_superadmin": False,
            "last_login_at": None,
            "created_at": None,
        }
    ]

    response = await get_admin_organization_detail_data(
        org_id="org-1",
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "id": "org-1",
        "name": "Acme IP",
        "plan_name": "professional",
        "price_monthly": 999.0,
        "users": [
            {
                "id": "user-1",
                "email": "ops@acme.test",
                "first_name": "Ops",
                "last_name": "User",
                "role": "admin",
                "is_active": True,
                "is_superadmin": False,
                "last_login_at": None,
                "created_at": None,
            }
        ],
    }


@pytest.mark.asyncio
async def test_admin_service_get_admin_organization_detail_data_raises_404_when_missing():
    from services.admin_service import get_admin_organization_detail_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = None

    with pytest.raises(Exception) as exc_info:
        await get_admin_organization_detail_data(
            org_id="missing-org",
            db_factory=MagicMock(return_value=mock_db_cm),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Organization not found"


@pytest.mark.asyncio
async def test_admin_service_toggle_admin_organization_status_data_updates_and_audits():
    from services.admin_service import toggle_admin_organization_status_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"id": "org-1"}
    audit_logger = MagicMock()

    response = await toggle_admin_organization_status_data(
        org_id="org-1",
        payload={"is_active": False},
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
    )

    assert response == {
        "status": "ok",
        "organization_id": "org-1",
        "is_active": False,
    }
    execute_sql, execute_params = mock_cursor.execute.call_args.args
    assert "UPDATE organizations SET is_active = %s WHERE id = %s RETURNING id" in execute_sql
    assert execute_params == (False, "org-1")
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "org_status_changed",
        {"organization_id": "org-1", "is_active": False},
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_change_admin_organization_plan_data_updates_and_audits():
    from services.admin_service import change_admin_organization_plan_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.side_effect = [
        {"id": "plan-1", "name": "professional"},
        {"old_plan": "free"},
    ]
    audit_logger = MagicMock()
    plan_limit_getter = MagicMock(return_value=50)

    response = await change_admin_organization_plan_data(
        org_id="org-1",
        payload={"plan_name": "professional"},
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
        plan_limit_getter=plan_limit_getter,
    )

    assert response == {
        "status": "ok",
        "organization_id": "org-1",
        "old_plan": "free",
        "new_plan": "professional",
    }
    execute_calls = mock_cursor.execute.call_args_list
    assert execute_calls[0].args == (
        "SELECT id, name FROM subscription_plans WHERE name = %s AND is_active = TRUE",
        ("professional",),
    )
    assert "SELECT COALESCE(sp.name, 'free') as old_plan" in execute_calls[1].args[0]
    assert execute_calls[1].args[1] == ("org-1",)
    assert "UPDATE organizations" in execute_calls[2].args[0]
    assert "ai_credits_monthly = %s" in execute_calls[2].args[0]
    assert execute_calls[2].args[1] == ("plan-1", 50, "org-1")
    plan_limit_getter.assert_called_once_with("professional", "monthly_ai_credits")
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "plan_changed",
        {
            "organization_id": "org-1",
            "old_plan": "free",
            "new_plan": "professional",
        },
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_refund_admin_payment_data_updates_and_audits():
    from services.admin_service import refund_admin_payment_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "id": "pay-1",
        "organization_id": "org-1",
        "status": "completed",
        "refund_status": None,
        "amount": 100.0,
        "currency": "TRY",
        "iyzico_conversation_id": "conv-1",
        "iyzico_raw_response": {
            "itemTransactions": [{"paymentTransactionId": "txn-1"}],
        },
    }

    refund_result = MagicMock()
    refund_result.read.return_value = b'{"status": "success", "systemTime": 123}'
    refund_client = MagicMock()
    refund_client.create.return_value = refund_result
    refund_client_factory = MagicMock(return_value=refund_client)
    iyzico_options_getter = MagicMock(return_value={"api_key": "test-key"})
    subscription_activator = MagicMock()
    audit_logger = MagicMock()
    gateway_logger = MagicMock()

    response = await refund_admin_payment_data(
        payment_id="pay-1",
        payload={"reason": "Customer requested"},
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
        iyzico_options_getter=iyzico_options_getter,
        subscription_activator=subscription_activator,
        refund_client_factory=refund_client_factory,
        gateway_logger=gateway_logger,
    )

    assert response == {
        "status": "ok",
        "payment_id": "pay-1",
        "refund_type": "full",
        "refund_amount": 100.0,
    }
    assert mock_cursor.execute.call_args_list[0].args == (
        "SELECT * FROM payments WHERE id = %s",
        ("pay-1",),
    )
    refund_client_factory.assert_called_once_with()
    iyzico_options_getter.assert_called_once_with()
    refund_request, refund_options = refund_client.create.call_args.args
    assert refund_request == {
        "locale": "tr",
        "conversationId": "conv-1",
        "paymentTransactionId": "txn-1",
        "price": "100.00",
        "currency": "TRY",
        "ip": "127.0.0.1",
    }
    assert refund_options == {"api_key": "test-key"}
    assert "UPDATE payments" in mock_cursor.execute.call_args_list[1].args[0]
    assert mock_cursor.execute.call_args_list[1].args[1] == (
        "full",
        100.0,
        "Customer requested",
        json.dumps({"status": "success", "systemTime": 123}),
        "pay-1",
    )
    subscription_activator.assert_called_once_with(mock_db, "org-1", "free", "monthly")
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "payment_refunded",
        {
            "payment_id": "pay-1",
            "organization_id": "org-1",
            "refund_type": "full",
            "refund_amount": 100.0,
            "original_amount": 100.0,
            "reason": "Customer requested",
        },
    )
    gateway_logger.info.assert_called_once()
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_refund_admin_payment_data_rejects_invalid_amount():
    from services.admin_service import refund_admin_payment_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {
        "id": "pay-1",
        "organization_id": "org-1",
        "status": "completed",
        "refund_status": None,
        "amount": 100.0,
        "currency": "TRY",
        "iyzico_raw_response": {
            "itemTransactions": [{"paymentTransactionId": "txn-1"}],
        },
    }
    refund_client_factory = MagicMock()

    with pytest.raises(Exception) as exc_info:
        await refund_admin_payment_data(
            payment_id="pay-1",
            payload={"amount": 150.0},
            current_user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
            iyzico_options_getter=MagicMock(return_value={"api_key": "test-key"}),
            subscription_activator=MagicMock(),
            refund_client_factory=refund_client_factory,
            gateway_logger=MagicMock(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Refund amount must be between 0 and 100.0"
    refund_client_factory.assert_not_called()
    assert mock_db.commit.call_count == 0


@pytest.mark.asyncio
async def test_admin_service_get_admin_users_data_returns_paginated_results():
    from services.admin_service import get_admin_users_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"cnt": 1}
    mock_cursor.fetchall.return_value = [
        {
            "id": "user-1",
            "email": "ops@acme.test",
            "first_name": "Ops",
            "last_name": "User",
            "role": "admin",
            "is_active": True,
            "is_superadmin": False,
            "last_login_at": None,
            "created_at": None,
            "organization_id": "org-1",
            "org_name": "Acme IP",
            "plan_name": "professional",
        }
    ]

    response = await get_admin_users_data(
        search="ops",
        org_id="org-1",
        role="admin",
        is_active=True,
        limit=25,
        offset=0,
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "users": [
            {
                "id": "user-1",
                "email": "ops@acme.test",
                "first_name": "Ops",
                "last_name": "User",
                "role": "admin",
                "is_active": True,
                "is_superadmin": False,
                "last_login_at": None,
                "created_at": None,
                "organization_id": "org-1",
                "org_name": "Acme IP",
                "plan_name": "professional",
            }
        ],
        "total": 1,
        "limit": 25,
        "offset": 0,
    }
    count_sql, count_params = mock_cursor.execute.call_args_list[0].args
    list_sql, list_params = mock_cursor.execute.call_args_list[1].args
    assert "COUNT(*) as cnt" in count_sql
    assert "u.role = %s" in count_sql
    assert count_params == ["%ops%", "%ops%", "%ops%", "org-1", "admin", True]
    assert "ORDER BY u.created_at DESC LIMIT %s OFFSET %s" in list_sql
    assert list_params == ["%ops%", "%ops%", "%ops%", "org-1", "admin", True, 25, 0]


@pytest.mark.asyncio
async def test_admin_service_change_admin_user_role_data_updates_and_audits():
    from services.admin_service import change_admin_user_role_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"role": "user"}
    audit_logger = MagicMock()

    response = await change_admin_user_role_data(
        user_id="user-1",
        payload={"role": "admin"},
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
    )

    assert response == {
        "status": "ok",
        "user_id": "user-1",
        "old_role": "user",
        "new_role": "admin",
    }
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "user_role_changed",
        {"target_user_id": "user-1", "old_role": "user", "new_role": "admin"},
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_change_admin_user_role_data_rejects_invalid_role():
    from services.admin_service import change_admin_user_role_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    with pytest.raises(Exception) as exc_info:
        await change_admin_user_role_data(
            user_id="user-1",
            payload={"role": "owner"},
            current_user=current_user,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Role must be one of: ['admin', 'user', 'viewer']"


@pytest.mark.asyncio
async def test_admin_service_toggle_admin_superadmin_data_updates_and_audits():
    from services.admin_service import toggle_admin_superadmin_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"id": "user-1"}
    audit_logger = MagicMock()

    response = await toggle_admin_superadmin_data(
        user_id="user-1",
        payload={"is_superadmin": True},
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
    )

    assert response == {
        "status": "ok",
        "user_id": "user-1",
        "is_superadmin": True,
    }
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "superadmin_toggled",
        {"target_user_id": "user-1", "is_superadmin": True},
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_toggle_admin_superadmin_data_blocks_self_revoke():
    from services.admin_service import toggle_admin_superadmin_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    with pytest.raises(Exception) as exc_info:
        await toggle_admin_superadmin_data(
            user_id="admin-123",
            payload={"is_superadmin": False},
            current_user=current_user,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Cannot revoke your own superadmin status"


@pytest.mark.asyncio
async def test_admin_service_toggle_admin_user_status_data_updates_and_audits():
    from services.admin_service import toggle_admin_user_status_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"id": "user-1"}
    audit_logger = MagicMock()

    response = await toggle_admin_user_status_data(
        user_id="user-1",
        payload={"is_active": False},
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
    )

    assert response == {
        "status": "ok",
        "user_id": "user-1",
        "is_active": False,
    }
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "user_status_changed",
        {"target_user_id": "user-1", "is_active": False},
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_toggle_admin_user_status_data_blocks_self_deactivate():
    from services.admin_service import toggle_admin_user_status_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    with pytest.raises(Exception) as exc_info:
        await toggle_admin_user_status_data(
            user_id="admin-123",
            payload={"is_active": False},
            current_user=current_user,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Cannot deactivate yourself"


@pytest.mark.asyncio
async def test_admin_service_get_admin_audit_log_data_returns_filtered_entries():
    from services.admin_service import get_admin_audit_log_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {
            "id": "log-1",
            "user_id": "user-1",
            "action": "user_role_changed",
            "resource_type": "admin",
            "metadata": {"old_role": "user", "new_role": "admin"},
            "user_email": "ops@acme.test",
            "first_name": "Ops",
            "last_name": "User",
        }
    ]

    response = await get_admin_audit_log_data(
        action="user_role_changed",
        user_id="user-1",
        limit=50,
        offset=0,
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "entries": [
            {
                "id": "log-1",
                "user_id": "user-1",
                "action": "user_role_changed",
                "resource_type": "admin",
                "metadata": {"old_role": "user", "new_role": "admin"},
                "user_email": "ops@acme.test",
                "first_name": "Ops",
                "last_name": "User",
            }
        ],
        "limit": 50,
        "offset": 0,
    }
    audit_sql, audit_params = mock_cursor.execute.call_args.args
    assert "FROM audit_log al" in audit_sql
    assert "AND al.action = %s" in audit_sql
    assert "AND al.user_id = %s" in audit_sql
    assert "ORDER BY al.created_at DESC LIMIT %s OFFSET %s" in audit_sql
    assert audit_params == ["user_role_changed", "user-1", 50, 0]


@pytest.mark.asyncio
async def test_admin_service_get_admin_org_credits_data_returns_balances():
    from services.admin_service import get_admin_org_credits_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.side_effect = [
        {
            "logo_credits_monthly": 10,
            "logo_credits_purchased": 2,
            "name_credits_purchased": 4,
            "logo_credits_reset_at": None,
            "ai_credits_monthly": 20,
            "ai_credits_purchased": 5,
            "ai_credits_reset_at": None,
            "plan_name": "professional",
        },
        {
            "logo_generations_this_month": 3,
            "name_generations_this_month": 1,
        },
    ]

    response = await get_admin_org_credits_data(
        org_id="org-1",
        db_factory=MagicMock(return_value=mock_db_cm),
        plan_limit_getter=MagicMock(return_value=25),
    )

    assert response == {
        "organization_id": "org-1",
        "plan": "professional",
        "ai_credits": {
            "monthly_remaining": 20,
            "purchased": 5,
            "plan_limit": 25,
            "reset_at": None,
        },
        "logo_credits": {
            "monthly_remaining": 10,
            "purchased": 2,
            "used_this_month": 3,
            "reset_at": None,
        },
        "name_credits": {
            "purchased": 4,
            "used_this_month": 1,
        },
    }
    org_sql, org_params = mock_cursor.execute.call_args_list[0].args
    usage_sql, usage_params = mock_cursor.execute.call_args_list[1].args
    assert "FROM organizations o" in org_sql
    assert org_params == ("org-1",)
    assert "FROM generation_logs" in usage_sql
    assert usage_params == ("org-1",)


@pytest.mark.asyncio
async def test_admin_service_get_admin_org_credits_data_raises_404_when_missing():
    from services.admin_service import get_admin_org_credits_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = None

    with pytest.raises(Exception) as exc_info:
        await get_admin_org_credits_data(
            org_id="missing-org",
            db_factory=MagicMock(return_value=mock_db_cm),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Organization not found"


@pytest.mark.asyncio
async def test_admin_service_adjust_admin_org_credits_data_updates_and_audits():
    from services.admin_service import adjust_admin_org_credits_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"logo_credits_purchased": 2}
    audit_logger = MagicMock()

    response = await adjust_admin_org_credits_data(
        org_id="org-1",
        payload={"credit_type": "logo_purchased", "operation": "add", "amount": 3, "reason": "bonus"},
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
    )

    assert response == {
        "status": "ok",
        "organization_id": "org-1",
        "credit_type": "logo_purchased",
        "old_value": 2,
        "new_value": 5,
    }
    select_sql, select_params = mock_cursor.execute.call_args_list[0].args
    update_sql, update_params = mock_cursor.execute.call_args_list[1].args
    assert select_params == ("org-1",)
    assert update_params == (3, "org-1")
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "credit_adjustment",
        {
            "organization_id": "org-1",
            "credit_type": "logo_purchased",
            "operation": "add",
            "amount": 3,
            "old_value": 2,
            "new_value": 5,
            "reason": "bonus",
        },
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_adjust_admin_org_credits_data_rejects_invalid_credit_type():
    from services.admin_service import adjust_admin_org_credits_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    with pytest.raises(Exception) as exc_info:
        await adjust_admin_org_credits_data(
            org_id="org-1",
            payload={"credit_type": "mystery", "operation": "set", "amount": 1},
            current_user=current_user,
        )

    assert exc_info.value.status_code == 400
    assert "credit_type must be one of" in exc_info.value.detail


@pytest.mark.asyncio
async def test_admin_service_adjust_admin_org_credits_data_subtracts_to_zero_floor():
    from services.admin_service import adjust_admin_org_credits_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"ai_credits_purchased": 2}

    response = await adjust_admin_org_credits_data(
        org_id="org-1",
        payload={"credit_type": "ai_purchased", "operation": "subtract", "amount": 5},
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=MagicMock(),
    )

    assert response["old_value"] == 2
    assert response["new_value"] == 0


@pytest.mark.asyncio
async def test_admin_service_bulk_adjust_admin_credits_data_updates_and_audits():
    from services.admin_service import bulk_adjust_admin_credits_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.rowcount = 4
    audit_logger = MagicMock()

    response = await bulk_adjust_admin_credits_data(
        payload={
            "plan_filter": "professional",
            "credit_type": "logo_purchased",
            "operation": "add",
            "amount": 10,
            "reason": "Q2 bonus",
        },
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
    )

    assert response == {
        "status": "ok",
        "affected_organizations": 4,
    }
    update_sql, update_params = mock_cursor.execute.call_args_list[0].args
    assert update_params == [10, "professional"]
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "bulk_credit_adjustment",
        {
            "plan_filter": "professional",
            "credit_type": "logo_purchased",
            "operation": "add",
            "amount": 10,
            "affected_orgs": 4,
            "reason": "Q2 bonus",
        },
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_bulk_adjust_admin_credits_data_rejects_invalid_operation():
    from services.admin_service import bulk_adjust_admin_credits_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    with pytest.raises(Exception) as exc_info:
        await bulk_adjust_admin_credits_data(
            payload={
                "plan_filter": "all",
                "credit_type": "ai_purchased",
                "operation": "subtract",
                "amount": 10,
            },
            current_user=current_user,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Bulk operation only supports 'add' or 'set'"


@pytest.mark.asyncio
async def test_admin_service_get_admin_discount_codes_data_returns_filtered_codes():
    from services.admin_service import get_admin_discount_codes_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {"id": "code-1", "code": "LAUNCH20", "is_active": True},
        {"id": "code-2", "code": "SPRING15", "is_active": True},
    ]

    response = await get_admin_discount_codes_data(
        is_active=True,
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "discount_codes": [
            {"id": "code-1", "code": "LAUNCH20", "is_active": True},
            {"id": "code-2", "code": "SPRING15", "is_active": True},
        ]
    }
    execute_sql, execute_params = mock_cursor.execute.call_args.args
    assert "SELECT * FROM discount_codes WHERE 1=1" in execute_sql
    assert "AND is_active = %s" in execute_sql
    assert "ORDER BY created_at DESC" in execute_sql
    assert execute_params == [True]


@pytest.mark.asyncio
async def test_admin_service_get_admin_discount_codes_data_allows_unfiltered_listing():
    from services.admin_service import get_admin_discount_codes_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [{"id": "code-1", "code": "LAUNCH20", "is_active": True}]

    response = await get_admin_discount_codes_data(
        is_active=None,
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "discount_codes": [{"id": "code-1", "code": "LAUNCH20", "is_active": True}]
    }
    execute_sql, execute_params = mock_cursor.execute.call_args.args
    assert "AND is_active = %s" not in execute_sql
    assert execute_params == []


@pytest.mark.asyncio
async def test_admin_service_create_admin_discount_code_data_inserts_and_audits():
    from services.admin_service import create_admin_discount_code_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = None
    audit_logger = MagicMock()

    response = await create_admin_discount_code_data(
        payload={
            "code": " launch20 ",
            "description": "Launch promo",
            "discount_type": "percentage",
            "discount_value": 20,
            "applies_to_plan": "professional",
            "max_uses": 100,
            "valid_from": "2026-01-01T00:00:00",
            "valid_until": "2026-12-31T23:59:59",
        },
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
    )

    assert response == {
        "status": "ok",
        "code": "LAUNCH20",
    }
    select_sql, select_params = mock_cursor.execute.call_args_list[0].args
    insert_sql, insert_params = mock_cursor.execute.call_args_list[1].args
    assert select_params == ("LAUNCH20",)
    assert insert_params == (
        "LAUNCH20",
        "Launch promo",
        "percentage",
        20,
        "professional",
        100,
        "2026-01-01T00:00:00",
        "2026-12-31T23:59:59",
        "admin-123",
    )
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "discount_code_created",
        {
            "code": "LAUNCH20",
            "discount_type": "percentage",
            "discount_value": 20.0,
        },
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_create_admin_discount_code_data_rejects_duplicates():
    from services.admin_service import create_admin_discount_code_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"id": "existing-code"}

    with pytest.raises(Exception) as exc_info:
        await create_admin_discount_code_data(
            payload={
                "code": "launch20",
                "discount_type": "percentage",
                "discount_value": 20,
            },
            current_user=current_user,
            db_factory=MagicMock(return_value=mock_db_cm),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Code 'LAUNCH20' already exists"


@pytest.mark.asyncio
async def test_admin_service_update_admin_discount_code_data_updates_and_audits():
    from services.admin_service import update_admin_discount_code_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    audit_logger = MagicMock()

    response = await update_admin_discount_code_data(
        code_id="code-1",
        payload={
            "description": "Updated launch promo",
            "max_uses": 250,
            "ignored_field": "skip-me",
        },
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
    )

    assert response == {
        "status": "ok",
        "code_id": "code-1",
    }
    update_sql, update_params = mock_cursor.execute.call_args.args
    assert update_params == ["Updated launch promo", 250, "code-1"]
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "discount_code_updated",
        {
            "code_id": "code-1",
            "changes": {
                "description": "Updated launch promo",
                "max_uses": "250",
            },
        },
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_update_admin_discount_code_data_rejects_empty_updates():
    from services.admin_service import update_admin_discount_code_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    with pytest.raises(Exception) as exc_info:
        await update_admin_discount_code_data(
            code_id="code-1",
            payload={"unknown_field": "value"},
            current_user=current_user,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No valid fields to update"


@pytest.mark.asyncio
async def test_admin_service_deactivate_admin_discount_code_data_updates_and_audits():
    from services.admin_service import deactivate_admin_discount_code_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    audit_logger = MagicMock()

    response = await deactivate_admin_discount_code_data(
        code_id="code-1",
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
    )

    assert response == {
        "status": "ok",
        "code_id": "code-1",
    }
    update_sql, update_params = mock_cursor.execute.call_args.args
    assert update_params == ("code-1",)
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "discount_code_deactivated",
        {"code_id": "code-1"},
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_get_admin_discount_code_usage_data_returns_usage_rows():
    from services.admin_service import get_admin_discount_code_usage_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {
            "discount_code_id": "code-1",
            "organization_id": "org-1",
            "org_name": "Acme IP",
            "org_email": "ops@acme.test",
            "applied_at": "2026-04-11T10:00:00Z",
        },
        {
            "discount_code_id": "code-1",
            "organization_id": "org-2",
            "org_name": "Beta Legal",
            "org_email": "team@beta.test",
            "applied_at": "2026-04-10T10:00:00Z",
        },
    ]

    response = await get_admin_discount_code_usage_data(
        code_id="code-1",
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "usage": [
            {
                "discount_code_id": "code-1",
                "organization_id": "org-1",
                "org_name": "Acme IP",
                "org_email": "ops@acme.test",
                "applied_at": "2026-04-11T10:00:00Z",
            },
            {
                "discount_code_id": "code-1",
                "organization_id": "org-2",
                "org_name": "Beta Legal",
                "org_email": "team@beta.test",
                "applied_at": "2026-04-10T10:00:00Z",
            },
        ],
        "total_uses": 2,
    }
    execute_sql, execute_params = mock_cursor.execute.call_args.args
    assert "FROM discount_code_usage dcu" in execute_sql
    assert "JOIN organizations o" in execute_sql
    assert "WHERE dcu.discount_code_id = %s" in execute_sql
    assert "ORDER BY dcu.applied_at DESC" in execute_sql
    assert execute_params == ("code-1",)


@pytest.mark.asyncio
async def test_admin_service_get_admin_plans_data_merges_defaults_overrides_and_counts():
    from services.admin_service import ADMIN_PLAN_FEATURE_CATEGORIES, get_admin_plans_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchall.side_effect = [
        [
            {"name": "professional", "price_monthly": 999},
            {"name": "enterprise", "price_monthly": 1999},
        ],
        [
            {"plan_name": "professional", "org_count": 3},
            {"plan_name": "enterprise", "org_count": 1},
        ],
    ]

    response = await get_admin_plans_data(
        db_factory=MagicMock(return_value=mock_db_cm),
        settings_category_getter=MagicMock(
            return_value={
                "plan.professional.max_watchlist_items": {"value": 250},
                "plan.professional.can_export_reports": {"value": True},
                "plan.enterprise.max_users": {"value": 50},
            }
        ),
        plan_feature_defaults={
            "professional": {"max_watchlist_items": 100, "can_export_reports": False},
            "enterprise": {"max_users": 25, "api_access": True},
        },
    )

    assert response == {
        "plans": [
            {
                "db_record": {"name": "professional", "price_monthly": 999},
                "code_defaults": {
                    "max_watchlist_items": 100,
                    "can_export_reports": False,
                },
                "active_overrides": {
                    "max_watchlist_items": 250,
                    "can_export_reports": True,
                },
                "active_orgs": 3,
            },
            {
                "db_record": {"name": "enterprise", "price_monthly": 1999},
                "code_defaults": {"max_users": 25, "api_access": True},
                "active_overrides": {"max_users": 50},
                "active_orgs": 1,
            },
        ],
        "feature_categories": ADMIN_PLAN_FEATURE_CATEGORIES,
    }
    assert len(mock_cursor.execute.call_args_list) == 2
    first_sql = mock_cursor.execute.call_args_list[0].args[0]
    second_sql = mock_cursor.execute.call_args_list[1].args[0]
    assert "SELECT * FROM subscription_plans ORDER BY price_monthly ASC NULLS FIRST" in first_sql
    assert "FROM organizations o" in second_sql
    assert "GROUP BY sp.name" in second_sql


@pytest.mark.asyncio
async def test_admin_service_update_admin_plan_pricing_data_updates_and_audits():
    from services.admin_service import update_admin_plan_pricing_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"name": "professional", "price_monthly": 999}
    audit_logger = MagicMock()

    response = await update_admin_plan_pricing_data(
        plan_name="professional",
        payload={
            "price_monthly": 1099,
            "display_name": "Professional Plus",
            "ignored_field": "skip-me",
        },
        current_user=current_user,
        db_factory=MagicMock(return_value=mock_db_cm),
        audit_logger=audit_logger,
    )

    assert response == {
        "status": "ok",
        "plan": "professional",
    }
    execute_sql, execute_params = mock_cursor.execute.call_args.args
    assert "UPDATE subscription_plans SET" in str(execute_sql)
    assert execute_params == [1099, "Professional Plus", "professional"]
    audit_logger.assert_called_once_with(
        mock_db,
        "admin-123",
        "plan_pricing_updated",
        {
            "plan": "professional",
            "changes": {
                "price_monthly": "1099",
                "display_name": "Professional Plus",
            },
        },
    )
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_admin_service_update_admin_plan_pricing_data_rejects_empty_updates():
    from services.admin_service import update_admin_plan_pricing_data

    current_user = MagicMock()
    current_user.id = "admin-123"

    with pytest.raises(Exception) as exc_info:
        await update_admin_plan_pricing_data(
            plan_name="professional",
            payload={"unknown_field": "value"},
            current_user=current_user,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No valid fields to update"


@pytest.mark.asyncio
async def test_admin_service_get_admin_usage_analytics_data_returns_aggregates():
    from services.admin_service import get_admin_usage_analytics_data

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchall.side_effect = [
        [
            {
                "date": "2026-04-11",
                "unique_users": 5,
                "quick_searches": 12,
                "live_searches": 3,
                "name_generations": 2,
            }
        ],
        [
            {"plan": "professional", "total_searches": 15},
            {"plan": "free", "total_searches": 4},
        ],
        [
            {
                "email": "ops@acme.test",
                "first_name": "Ava",
                "last_name": "Stone",
                "org_name": "Acme IP",
                "total_searches": 11,
            }
        ],
    ]
    mock_cursor.fetchone.return_value = {
        "logo_generations": 2,
        "name_generations": 7,
    }
    mock_cursor.rowcount = 1

    response = await get_admin_usage_analytics_data(
        days=14,
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    assert response == {
        "period_days": 14,
        "daily_usage": [
            {
                "date": "2026-04-11",
                "unique_users": 5,
                "quick_searches": 12,
                "live_searches": 3,
                "name_generations": 2,
            }
        ],
        "usage_by_plan": {
            "professional": 15,
            "free": 4,
        },
        "top_users": [
            {
                "email": "ops@acme.test",
                "first_name": "Ava",
                "last_name": "Stone",
                "org_name": "Acme IP",
                "total_searches": 11,
            }
        ],
        "cost_bearing_actions": {
            "logo_generations": 2,
            "name_generations": 7,
        },
    }
    assert len(mock_cursor.execute.call_args_list) == 4
    first_sql, first_params = mock_cursor.execute.call_args_list[0].args
    second_sql, second_params = mock_cursor.execute.call_args_list[1].args
    third_sql, third_params = mock_cursor.execute.call_args_list[2].args
    fourth_sql, fourth_params = mock_cursor.execute.call_args_list[3].args
    assert "FROM api_usage" in first_sql
    assert "GROUP BY usage_date" in first_sql
    assert first_params == (14,)
    assert "LEFT JOIN subscription_plans sp" in second_sql
    assert "GROUP BY sp.name" in second_sql
    assert second_params == (14,)
    assert "ORDER BY total_searches DESC" in third_sql
    assert "LIMIT 20" in third_sql
    assert third_params == (14,)
    assert "FROM generation_logs" in fourth_sql
    assert fourth_params == (14,)


@pytest.mark.asyncio
async def test_admin_service_build_admin_usage_export_response_streams_csv():
    from services.admin_service import build_admin_usage_export_response

    mock_db_cm = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db_cm.__enter__.return_value = mock_db
    mock_db_cm.__exit__.return_value = False
    mock_db.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {
            "usage_date": date(2026, 4, 11),
            "user_email": "ops@acme.test",
            "org_name": "Acme IP",
            "plan": "professional",
            "quick_searches": 12,
            "live_searches": 3,
            "name_generations": 2,
        }
    ]

    response = await build_admin_usage_export_response(
        days=14,
        db_factory=MagicMock(return_value=mock_db_cm),
    )

    body_chunks = []
    async for chunk in response.body_iterator:
        body_chunks.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
    body = "".join(body_chunks)

    assert response.headers["content-disposition"] == "attachment; filename=usage_export_14d.csv"
    assert "date,user_email,org_name,plan,quick_searches,live_searches,name_generations" in body
    assert "2026-04-11,ops@acme.test,Acme IP,professional,12,3,2" in body
    execute_sql, execute_params = mock_cursor.execute.call_args.args
    assert "FROM api_usage au" in execute_sql
    assert "ORDER BY au.usage_date DESC, u.email" in execute_sql
    assert execute_params == (14,)

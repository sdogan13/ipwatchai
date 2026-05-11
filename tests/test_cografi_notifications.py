"""Unit tests for the cografi notification additions in
``notifications.service``.

DB-touching code paths (digest query + worker loops) are exercised
manually via ``python -m notifications.service cografi-daily-digest``;
these tests pin the pure-Python pieces:

  * ``EmailService.send_cografi_digest`` body building (severity colours,
    no-alerts short-circuit, bilingual phrasing, watch_type column
    variation, alert truncation past 20).
  * ``WebhookService.send_cografi_alert_webhook`` payload shape (4
    watch_types correctly discriminated, conflicting record fields
    present, score breakdown + section_keys[]).
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# EmailService.send_cografi_digest
# ---------------------------------------------------------------------------

def _email_service():
    from notifications.service import EmailService
    es = EmailService()
    # Patch out the actual SMTP send so we can inspect body building only.
    es.send_email = MagicMock(return_value=True)
    return es


def test_send_cografi_digest_short_circuits_when_no_alerts():
    """An empty alert list shouldn't trigger any SMTP work — return True
    so the worker can mark a no-op run as successful."""
    es = _email_service()
    result = es.send_cografi_digest(
        to_email="user@example.com", user_name="A", alerts=[],
    )
    assert result is True
    es.send_email.assert_not_called()


def test_send_cografi_digest_subject_includes_count_and_critical_prefix():
    es = _email_service()
    es.send_cografi_digest(
        to_email="u@x.com", user_name="A",
        alerts=[
            {"severity": "critical", "overall_similarity_score": 0.9, "match_type": "region"},
            {"severity": "high", "overall_similarity_score": 0.75, "match_type": "holder"},
            {"severity": "medium", "overall_similarity_score": 0.6, "match_type": "reference_text"},
        ],
        period="daily", lang="tr",
    )
    args, _ = es.send_email.call_args
    subject = args[1]
    # "🌐 Coğrafi İşaret Bildirim Özeti: 3 yeni uyarı" + critical prefix
    assert "3 yeni uyarı" in subject
    assert "Kritik" in subject  # critical prefix surfaces


def test_send_cografi_digest_includes_region_in_match_column():
    """A region-watch match should surface the matched
    geographical_boundary in the Eşleşme column."""
    es = _email_service()
    es.send_cografi_digest(
        to_email="u@x.com", user_name="A",
        alerts=[{
            "severity": "high",
            "overall_similarity_score": 0.8,
            "match_type": "region",
            "conflicting_name": "Karapınar Halısı",
            "conflicting_geographical_boundary": "Konya ili Karapınar ilçesi",
            "conflicting_gi_type": "Mahreç işareti",
            "watchlist_label": "Konya bölgesi izleme",
        }],
        period="daily", lang="tr",
    )
    body = es.send_email.call_args[0][2]
    assert "Karapınar Halısı" in body
    assert "Konya ili Karapınar ilçesi" in body
    # Region appears both in the record cell + Eşleşme column for region matches.
    assert body.count("Konya ili Karapınar ilçesi") >= 2


def test_send_cografi_digest_lifecycle_match_shows_registration_no():
    es = _email_service()
    es.send_cografi_digest(
        to_email="u@x.com", user_name="A",
        alerts=[{
            "severity": "high",
            "overall_similarity_score": 1.0,
            "match_type": "lifecycle_change_request",
            "conflicting_name": "İzmir Kumrusu",
            "conflicting_existing_registration_no": 262,
            "conflicting_geographical_boundary": "İzmir ili",
            "conflicting_gi_type": "Mahreç işareti",
        }],
        period="daily", lang="tr",
    )
    body = es.send_email.call_args[0][2]
    assert "Tescil #262" in body
    assert "İzmir Kumrusu" in body


def test_send_cografi_digest_holder_match_surfaces_watched_holder_name():
    es = _email_service()
    es.send_cografi_digest(
        to_email="u@x.com", user_name="A",
        alerts=[{
            "severity": "medium",
            "overall_similarity_score": 1.0,
            "match_type": "holder",
            "conflicting_name": "Some GI",
            "conflicting_geographical_boundary": "İstanbul",
            "watchlist_holder_name": "Karapınar Belediyesi",
        }],
        period="daily", lang="tr",
    )
    body = es.send_email.call_args[0][2]
    assert "Karapınar Belediyesi" in body


def test_send_cografi_digest_truncates_to_20_alerts_with_note():
    es = _email_service()
    alerts = [
        {
            "severity": "low", "overall_similarity_score": 0.3,
            "match_type": "region",
            "conflicting_name": f"Record {i}",
            "conflicting_geographical_boundary": "Test",
        }
        for i in range(25)
    ]
    es.send_cografi_digest(
        to_email="u@x.com", user_name="A", alerts=alerts, period="daily", lang="tr",
    )
    body = es.send_email.call_args[0][2]
    assert "İlk 20 uyarı gösteriliyor (toplam 25)" in body
    # Records 21-25 should NOT be in the body.
    assert "Record 24" not in body
    # But records 0-19 should be.
    assert "Record 0" in body
    assert "Record 19" in body


def test_send_cografi_digest_english_locale():
    es = _email_service()
    es.send_cografi_digest(
        to_email="u@x.com", user_name="Alice",
        alerts=[{
            "severity": "high",
            "overall_similarity_score": 0.8,
            "match_type": "region",
            "conflicting_name": "Cyprus Halloumi",
            "conflicting_geographical_boundary": "Cyprus",
        }],
        period="weekly", lang="en",
    )
    args, _ = es.send_email.call_args
    subject = args[1]
    body = args[2]
    assert "Cografi GI Alert Digest" in subject or "Cografi" in subject
    assert "Hi Alice" in body
    assert "Showing first" not in body  # < 20 alerts so no truncation note
    assert "the past week" in body


# ---------------------------------------------------------------------------
# WebhookService.send_cografi_alert_webhook
# ---------------------------------------------------------------------------

def _capture_webhook_payload():
    """Patch send_webhook to capture the payload instead of POSTing."""
    captured: Dict[str, Any] = {}

    def fake_send_webhook(url, payload, headers=None):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return True
    return captured, fake_send_webhook


def test_webhook_event_name_and_envelope():
    from notifications.service import WebhookService
    captured, fake = _capture_webhook_payload()
    with patch.object(WebhookService, "send_webhook", staticmethod(fake)):
        WebhookService.send_cografi_alert_webhook(
            "https://example.test/hook",
            alert={"id": "00000000-0000-0000-0000-000000000001"},
            watchlist_item={"watch_type": "region", "label": "Test"},
        )
    assert captured["url"] == "https://example.test/hook"
    payload = captured["payload"]
    assert payload["event"] == "cografi.alert.new"
    assert "timestamp" in payload
    assert payload["data"]["alert_id"] == "00000000-0000-0000-0000-000000000001"


def test_webhook_holder_watch_includes_holder_dict():
    from notifications.service import WebhookService
    captured, fake = _capture_webhook_payload()
    with patch.object(WebhookService, "send_webhook", staticmethod(fake)):
        WebhookService.send_cografi_alert_webhook(
            "https://x", alert={"id": "x"}, watchlist_item={
                "watch_type": "holder",
                "label": "Watch ACME",
                "holder_name": "ACME Belediyesi",
                "holder_id": "uuid-1",
                "holder_tpe_client_id": "12345",
            },
        )
    watched = captured["payload"]["data"]["watched"]
    assert watched["watch_type"] == "holder"
    assert watched["holder"]["name"] == "ACME Belediyesi"
    assert watched["holder"]["id"] == "uuid-1"
    assert watched["holder"]["tpe_client_id"] == "12345"


def test_webhook_reference_watch_includes_reference_dict():
    from notifications.service import WebhookService
    captured, fake = _capture_webhook_payload()
    with patch.object(WebhookService, "send_webhook", staticmethod(fake)):
        WebhookService.send_cografi_alert_webhook(
            "https://x", alert={"id": "x"}, watchlist_item={
                "watch_type": "reference",
                "label": "Karapınar reference",
                "reference_record_id": "uuid-2",
                "reference_query": "Karapınar Halısı",
            },
        )
    watched = captured["payload"]["data"]["watched"]
    assert watched["watch_type"] == "reference"
    assert watched["reference"]["record_id"] == "uuid-2"
    assert watched["reference"]["query"] == "Karapınar Halısı"


def test_webhook_region_watch_includes_region_dict():
    from notifications.service import WebhookService
    captured, fake = _capture_webhook_payload()
    with patch.object(WebhookService, "send_webhook", staticmethod(fake)):
        WebhookService.send_cografi_alert_webhook(
            "https://x", alert={"id": "x"}, watchlist_item={
                "watch_type": "region",
                "label": "Konya bölgesi",
                "region_query": "Konya",
                "region_terms": ["Konya", "Karaman"],
            },
        )
    watched = captured["payload"]["data"]["watched"]
    assert watched["watch_type"] == "region"
    assert watched["region"]["query"] == "Konya"
    assert watched["region"]["terms"] == ["Konya", "Karaman"]


def test_webhook_lifecycle_watch_includes_registration_no():
    from notifications.service import WebhookService
    captured, fake = _capture_webhook_payload()
    with patch.object(WebhookService, "send_webhook", staticmethod(fake)):
        WebhookService.send_cografi_alert_webhook(
            "https://x", alert={"id": "x"}, watchlist_item={
                "watch_type": "lifecycle",
                "label": "İzmir Kumrusu lifecycle",
                "lifecycle_registration_no": 262,
            },
        )
    watched = captured["payload"]["data"]["watched"]
    assert watched["watch_type"] == "lifecycle"
    assert watched["registration_no"] == 262


def test_webhook_conflicting_record_carries_full_field_set():
    from notifications.service import WebhookService
    captured, fake = _capture_webhook_payload()
    with patch.object(WebhookService, "send_webhook", staticmethod(fake)):
        WebhookService.send_cografi_alert_webhook(
            "https://x",
            alert={
                "id": "alert-1",
                "severity": "high",
                "status": "new",
                "overall_similarity_score": 0.82,
                "text_similarity_score": 0.7,
                "embedding_similarity_score": 0.85,
                "region_similarity_score": None,
                "match_type": "reference_hybrid",
                "conflicting_record_id": "uuid-rec",
                "conflicting_section_key": "examined",
                "conflicting_record_type": "GI",
                "conflicting_application_no": "C2025/000999",
                "conflicting_registration_no": None,
                "conflicting_existing_registration_no": None,
                "conflicting_name": "Some New GI",
                "conflicting_gi_type": "Mahreç işareti",
                "conflicting_geographical_boundary": "Aydın ili",
                "conflicting_bulletin_no": 220,
                "overlapping_section_keys": ["examined"],
            },
            watchlist_item={"watch_type": "reference", "label": "X"},
        )
    data = captured["payload"]["data"]
    assert data["severity"] == "high"
    assert data["match_type"] == "reference_hybrid"
    rec = data["conflicting_record"]
    assert rec["record_id"] == "uuid-rec"
    assert rec["section_key"] == "examined"
    assert rec["name"] == "Some New GI"
    assert rec["application_no"] == "C2025/000999"
    assert rec["bulletin"]["no"] == 220
    assert data["scores"]["text_similarity"] == 0.7
    assert data["scores"]["embedding_similarity"] == 0.85
    assert data["overlapping_section_keys"] == ["examined"]


def test_webhook_includes_section_keys_and_gi_type_in_watched_when_set():
    from notifications.service import WebhookService
    captured, fake = _capture_webhook_payload()
    with patch.object(WebhookService, "send_webhook", staticmethod(fake)):
        WebhookService.send_cografi_alert_webhook(
            "https://x", alert={"id": "x"}, watchlist_item={
                "watch_type": "region",
                "label": "Konya scope",
                "region_query": "Konya",
                "section_keys": ["examined", "registered"],
                "gi_type": "Mahreç işareti",
            },
        )
    watched = captured["payload"]["data"]["watched"]
    assert watched["section_keys"] == ["examined", "registered"]
    assert watched["gi_type"] == "Mahreç işareti"

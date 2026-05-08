"""Tests for utils/status_reconciler.py — final_status reconciliation logic."""
from datetime import date
import utils.status_reconciler as status_reconciler
from utils.status_reconciler import (
    compute_ingest_status_date,
    reconcile_status,
    repair_final_statuses,
)


class TestReconcileStatus:
    """Test the pure reconcile_status() function."""

    def test_effective_null_returns_current(self):
        status, source, at = reconcile_status("Yayında", None, date(2025, 1, 1), None)
        assert status == "Yayında"
        assert source == "ingest"
        assert at == date(2025, 1, 1)

    def test_current_null_returns_effective(self):
        status, source, at = reconcile_status(None, "Devredildi", None, date(2025, 6, 1))
        assert status == "Devredildi"
        assert source == "event"
        assert at == date(2025, 6, 1)

    def test_both_null(self):
        status, source, at = reconcile_status(None, None, None, None)
        assert status is None
        assert source == "ingest"

    def test_event_date_newer_uses_effective(self):
        status, source, at = reconcile_status(
            "Yayında", "Geri Çekildi",
            date(2025, 1, 1), date(2025, 6, 1)
        )
        assert status == "Geri Çekildi"
        assert source == "event"
        assert at == date(2025, 6, 1)

    def test_ingest_date_newer_uses_current(self):
        status, source, at = reconcile_status(
            "Tescil Edildi", "Devredildi",
            date(2025, 6, 1), date(2025, 1, 1)
        )
        assert status == "Tescil Edildi"
        assert source == "ingest"
        assert at == date(2025, 6, 1)

    def test_same_date_uses_effective(self):
        """When dates are equal, effective_status wins (>= comparison)."""
        status, source, at = reconcile_status(
            "Yayında", "Yenilendi",
            date(2025, 3, 15), date(2025, 3, 15)
        )
        assert status == "Yenilendi"
        assert source == "event"

    def test_event_date_null_uses_effective(self):
        """When event_date is NULL but effective_status exists, prefer effective."""
        status, source, at = reconcile_status(
            "Yayında", "İptal Edildi",
            date(2025, 1, 1), None
        )
        assert status == "İptal Edildi"
        assert source == "event"

    def test_ingest_date_null_uses_effective(self):
        """When ingest_date is NULL but both statuses exist, prefer effective."""
        status, source, at = reconcile_status(
            "Tescil Edildi", "Devredildi",
            None, date(2025, 6, 1)
        )
        assert status == "Devredildi"
        assert source == "event"
        assert at == date(2025, 6, 1)

    def test_both_dates_null_uses_effective(self):
        """When neither date is available, prefer effective_status."""
        status, source, at = reconcile_status(
            "Yayında", "Geri Çekildi", None, None
        )
        assert status == "Geri Çekildi"
        assert source == "event"
        assert at is None

    def test_effective_null_with_no_dates(self):
        status, source, at = reconcile_status("Başvuruldu", None, None, None)
        assert status == "Başvuruldu"
        assert source == "ingest"
        assert at is None


class TestComputeIngestStatusDate:
    """Test the date derivation helper."""

    def test_blt_uses_bulletin_date(self):
        result = compute_ingest_status_date("BLT", date(2025, 1, 15), date(2025, 3, 1), None)
        assert result == date(2025, 1, 15)

    def test_gz_uses_gazette_date(self):
        result = compute_ingest_status_date("GZ", date(2025, 1, 15), date(2025, 3, 1), None)
        assert result == date(2025, 3, 1)

    def test_app_uses_updated_at(self):
        from datetime import datetime
        dt = datetime(2025, 5, 20, 14, 30, 0)
        result = compute_ingest_status_date("APP", None, None, dt)
        assert result == date(2025, 5, 20)

    def test_fallback_to_updated_at(self):
        from datetime import datetime
        dt = datetime(2025, 5, 20, 14, 30, 0)
        result = compute_ingest_status_date("BLT", None, None, dt)
        assert result == date(2025, 5, 20)

    def test_all_none(self):
        result = compute_ingest_status_date(None, None, None, None)
        assert result is None


class TestRepairFinalStatuses:
    def test_repairs_in_chunked_scoped_batches(self, monkeypatch):
        seen_batches = []

        monkeypatch.setattr(
            status_reconciler,
            "iter_application_no_batches",
            lambda conn, batch_size: iter([["2024/001", "2024/002"], ["2024/003"]]),
        )

        def fake_update(conn, app_nos=None):
            seen_batches.append(list(app_nos or []))
            return len(app_nos or [])

        monkeypatch.setattr(status_reconciler, "update_final_status_batch", fake_update)

        stats = repair_final_statuses(object(), batch_size=2)

        assert seen_batches == [["2024/001", "2024/002"], ["2024/003"]]
        assert stats == {
            "batches": 2,
            "processed": 3,
            "updated": 3,
        }

    def test_empty_repair_run_returns_zeroes(self, monkeypatch):
        monkeypatch.setattr(
            status_reconciler,
            "iter_application_no_batches",
            lambda conn, batch_size: iter([]),
        )

        stats = repair_final_statuses(object(), batch_size=5)

        assert stats == {
            "batches": 0,
            "processed": 0,
            "updated": 0,
        }

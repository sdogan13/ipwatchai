"""
Subscription Limit Enforcement Tests
=====================================
Verifies that ALL plan limits are properly enforced across endpoints.

Tests are organized by feature:
1. PLAN_FEATURES consistency
2. Watchlist limits (single, bulk, upload, bulk-from-portfolio)
3. Search limits (quick, live)
4. Lead access limits
5. Report limits
6. Application limits
7. AI credit limits
8. Auto-scan limits
9. Portfolio access (can_view_holder_portfolio)
10. Logo tracking (can_track_logos)
11. CSV export (can_export_csv_leads)
12. Report export (can_export_reports)
"""

import pytest
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock

from utils.subscription import (
    PLAN_FEATURES,
    get_plan_limit,
    get_user_plan,
    check_live_search_eligibility,
    check_quick_search_eligibility,
    check_application_eligibility,
    check_report_eligibility,
    check_ai_credit_eligibility,
    check_name_generation_eligibility,
    check_logo_generation_eligibility,
    get_lead_access,
    get_monthly_applications,
)


# ===========================================================================
# 1. PLAN_FEATURES consistency
# ===========================================================================

class TestPlanFeaturesConsistency:
    """Verify PLAN_FEATURES has all required keys for every plan."""

    REQUIRED_KEYS = [
        "price_monthly", "price_annual_monthly",
        "monthly_live_searches", "daily_lead_views", "monthly_reports",
        "can_export_reports", "name_suggestions_per_session",
        "monthly_ai_credits", "monthly_applications",
        "can_track_logos", "can_view_holder_portfolio",
        "can_export_csv_leads", "can_use_live_scraping",
        "max_users", "max_watchlist_items", "max_daily_quick_searches",
        "auto_scan_max_items", "auto_scan_frequency",
        "priority_support", "api_access", "dedicated_account_manager",
    ]

    PLAN_NAMES = ["free", "starter", "business", "professional", "enterprise", "superadmin"]

    def test_all_plans_exist(self):
        for plan in self.PLAN_NAMES:
            assert plan in PLAN_FEATURES, f"Plan '{plan}' missing from PLAN_FEATURES"

    def test_all_required_keys_present(self):
        for plan_name, features in PLAN_FEATURES.items():
            for key in self.REQUIRED_KEYS:
                assert key in features, f"Plan '{plan_name}' missing key '{key}'"

    def test_free_plan_is_most_restrictive(self):
        """Free plan should have 0 or False for all paid features."""
        free = PLAN_FEATURES["free"]
        assert free["monthly_live_searches"] == 0
        assert free["daily_lead_views"] == 0
        assert free["monthly_ai_credits"] == 0
        assert free["monthly_applications"] == 0
        assert free["auto_scan_max_items"] == 0
        assert free["can_export_reports"] is False
        assert free["can_track_logos"] is False
        assert free["can_view_holder_portfolio"] is False
        assert free["can_export_csv_leads"] is False
        assert free["can_use_live_scraping"] is False
        assert free["api_access"] is False

    def test_limits_increase_with_plan_tier(self):
        """Higher plans should have >= limits of lower plans."""
        ordered = ["free", "starter", "business", "professional", "enterprise"]
        numeric_keys = [
            "monthly_live_searches", "daily_lead_views", "monthly_reports",
            "monthly_ai_credits", "monthly_applications",
            "max_users", "max_watchlist_items", "max_daily_quick_searches",
            "auto_scan_max_items",
        ]
        for key in numeric_keys:
            prev_val = -1
            for plan_name in ordered:
                val = PLAN_FEATURES[plan_name][key]
                assert val >= prev_val, (
                    f"Plan '{plan_name}' has {key}={val} which is less than "
                    f"previous tier ({prev_val})"
                )
                prev_val = val

    def test_superadmin_has_unlimited_access(self):
        """Superadmin should have 999999 for all numeric limits and True for all booleans."""
        sa = PLAN_FEATURES["superadmin"]
        assert sa["monthly_live_searches"] == 999999
        assert sa["daily_lead_views"] == 999999
        assert sa["monthly_reports"] == 999999
        assert sa["monthly_ai_credits"] == 999999
        assert sa["monthly_applications"] == 999999
        assert sa["max_users"] == 999999
        assert sa["max_watchlist_items"] == 999999
        assert sa["max_daily_quick_searches"] == 999999
        assert sa["can_export_reports"] is True
        assert sa["can_track_logos"] is True
        assert sa["can_view_holder_portfolio"] is True
        assert sa["can_export_csv_leads"] is True
        assert sa["can_use_live_scraping"] is True
        assert sa["api_access"] is True

    def test_get_plan_limit_unknown_plan_falls_back_to_free(self):
        """Unknown plan names should fall back to free tier limits."""
        assert get_plan_limit("nonexistent", "max_watchlist_items") == 3
        assert get_plan_limit("nonexistent", "monthly_live_searches") == 0

    def test_get_plan_limit_unknown_feature_returns_zero(self):
        """Unknown feature names should return 0."""
        assert get_plan_limit("free", "nonexistent_feature") == 0


# ===========================================================================
# 2. Watchlist limit enforcement
# ===========================================================================

class TestWatchlistLimits:
    """Verify watchlist item creation respects max_watchlist_items."""

    @pytest.fixture
    def mock_db_watchlist(self):
        db = MagicMock()
        cursor = MagicMock()
        db.cursor.return_value = cursor
        return db, cursor

    def test_free_plan_max_3_items(self):
        assert PLAN_FEATURES["free"]["max_watchlist_items"] == 3

    def test_starter_plan_max_15_items(self):
        assert PLAN_FEATURES["starter"]["max_watchlist_items"] == 15

    def test_business_plan_max_50_items(self):
        assert PLAN_FEATURES["business"]["max_watchlist_items"] == 50

    def test_professional_plan_max_1000_items(self):
        assert PLAN_FEATURES["professional"]["max_watchlist_items"] == 1000

    def test_enterprise_unlimited(self):
        assert PLAN_FEATURES["enterprise"]["max_watchlist_items"] == 999999


# ===========================================================================
# 3. Search limit enforcement
# ===========================================================================

class TestSearchLimits:
    """Verify quick and live search limits per plan."""

    def test_free_daily_quick_search_limit(self):
        assert PLAN_FEATURES["free"]["max_daily_quick_searches"] == 5

    def test_starter_daily_quick_search_limit(self):
        assert PLAN_FEATURES["starter"]["max_daily_quick_searches"] == 50

    def test_free_monthly_live_search_limit(self):
        assert PLAN_FEATURES["free"]["monthly_live_searches"] == 0

    def test_starter_monthly_live_search_limit(self):
        assert PLAN_FEATURES["starter"]["monthly_live_searches"] == 10

    @patch("utils.subscription.get_user_plan")
    @patch("utils.subscription.get_daily_quick_searches")
    def test_quick_search_blocked_when_limit_reached(self, mock_qs, mock_plan):
        mock_plan.return_value = {"plan_name": "free", "display_name": "Free", "can_use_live_search": False, "monthly_limit": 0}
        mock_qs.return_value = 5  # Free limit is 5
        db = MagicMock()

        can_search, reason, details = check_quick_search_eligibility(db, "user-123")
        assert not can_search
        assert reason == "daily_limit_exceeded"
        assert details["remaining"] == 0

    @patch("utils.subscription.get_user_plan")
    @patch("utils.subscription.get_daily_quick_searches")
    def test_quick_search_allowed_under_limit(self, mock_qs, mock_plan):
        mock_plan.return_value = {"plan_name": "starter", "display_name": "Starter", "can_use_live_search": True, "monthly_limit": 10}
        mock_qs.return_value = 3
        db = MagicMock()

        can_search, reason, details = check_quick_search_eligibility(db, "user-123")
        assert can_search
        assert reason == "ok"
        assert details["remaining"] == 47  # 50 - 3

    @patch("utils.subscription.get_user_plan")
    @patch("utils.subscription.get_live_search_usage")
    def test_live_search_blocked_for_free_plan(self, mock_ls, mock_plan):
        mock_plan.return_value = {"plan_name": "free", "display_name": "Free", "can_use_live_search": False, "monthly_limit": 0}
        db = MagicMock()

        can_search, reason, details = check_live_search_eligibility(db, "user-123")
        assert not can_search
        assert reason == "upgrade_required"

    @patch("utils.subscription.get_user_plan")
    @patch("utils.subscription.get_live_search_usage")
    def test_live_search_blocked_when_monthly_limit_reached(self, mock_ls, mock_plan):
        mock_plan.return_value = {"plan_name": "starter", "display_name": "Starter", "can_use_live_search": True, "monthly_limit": 10}
        mock_ls.return_value = 10  # Used all 10
        db = MagicMock()

        can_search, reason, details = check_live_search_eligibility(db, "user-123")
        assert not can_search
        assert reason == "limit_exceeded"
        assert details["remaining"] == 0

    @patch("utils.subscription.get_user_plan")
    @patch("utils.subscription.get_live_search_usage")
    def test_live_search_allowed_under_limit(self, mock_ls, mock_plan):
        mock_plan.return_value = {"plan_name": "professional", "display_name": "Pro", "can_use_live_search": True, "monthly_limit": 100}
        mock_ls.return_value = 50
        db = MagicMock()

        can_search, reason, details = check_live_search_eligibility(db, "user-123")
        assert can_search
        assert reason == "ok"
        assert details["remaining"] == 50


# ===========================================================================
# 4. Lead access limits
# ===========================================================================

class TestLeadAccessLimits:
    """Verify lead access enforcement per plan."""

    def test_free_no_lead_access(self):
        assert PLAN_FEATURES["free"]["daily_lead_views"] == 0

    def test_starter_no_lead_access(self):
        assert PLAN_FEATURES["starter"]["daily_lead_views"] == 0

    def test_business_5_daily_leads(self):
        assert PLAN_FEATURES["business"]["daily_lead_views"] == 5

    def test_professional_10_daily_leads(self):
        assert PLAN_FEATURES["professional"]["daily_lead_views"] == 10

    @patch("utils.subscription.get_user_plan")
    def test_lead_access_denied_for_free_plan(self, mock_plan):
        mock_plan.return_value = {"plan_name": "free", "display_name": "Free", "can_use_live_search": False, "monthly_limit": 0}
        db = MagicMock()

        access = get_lead_access(db, "user-123")
        assert not access["can_access"]
        assert access["daily_limit"] == 0

    @patch("utils.subscription.get_user_plan")
    def test_lead_access_granted_for_business_plan(self, mock_plan):
        mock_plan.return_value = {"plan_name": "business", "display_name": "Business", "can_use_live_search": True, "monthly_limit": 30}
        db = MagicMock()
        cursor = MagicMock()
        db.cursor.return_value = cursor
        cursor.fetchone.return_value = {"cnt": 2}

        access = get_lead_access(db, "user-123")
        assert access["can_access"]
        assert access["daily_limit"] == 5
        assert access["remaining"] == 3  # 5 - 2


# ===========================================================================
# 5. Report limits
# ===========================================================================

class TestReportLimits:
    """Verify report generation and export limits."""

    def test_free_1_report_per_month(self):
        assert PLAN_FEATURES["free"]["monthly_reports"] == 1

    def test_free_cannot_export(self):
        assert PLAN_FEATURES["free"]["can_export_reports"] is False

    def test_starter_can_export(self):
        assert PLAN_FEATURES["starter"]["can_export_reports"] is True

    def test_report_limit_enforced(self):
        db = MagicMock()
        cursor = MagicMock()
        db.cursor.return_value = cursor
        cursor.fetchone.return_value = {"cnt": 1}  # Already used 1

        result = check_report_eligibility(db, "free", str(uuid.uuid4()))
        assert not result["eligible"]
        assert result["reports_used"] == 1
        assert result["reports_limit"] == 1

    def test_report_allowed_under_limit(self):
        db = MagicMock()
        cursor = MagicMock()
        db.cursor.return_value = cursor
        cursor.fetchone.return_value = {"cnt": 0}

        result = check_report_eligibility(db, "free", str(uuid.uuid4()))
        assert result["eligible"]
        assert result["reports_used"] == 0


# ===========================================================================
# 6. Application limits
# ===========================================================================

class TestApplicationLimits:
    """Verify trademark application limits."""

    def test_free_no_applications(self):
        assert PLAN_FEATURES["free"]["monthly_applications"] == 0

    def test_starter_1_application(self):
        assert PLAN_FEATURES["starter"]["monthly_applications"] == 1

    @patch("utils.subscription.get_user_plan")
    @patch("utils.subscription.get_monthly_applications")
    def test_application_blocked_for_free_plan(self, mock_apps, mock_plan):
        mock_plan.return_value = {"plan_name": "free", "display_name": "Free", "can_use_live_search": False, "monthly_limit": 0}
        db = MagicMock()

        can_create, reason, details = check_application_eligibility(db, "user-123", "org-123")
        assert not can_create
        assert reason == "upgrade_required"

    @patch("utils.subscription.get_user_plan")
    @patch("utils.subscription.get_monthly_applications")
    def test_application_blocked_when_limit_reached(self, mock_apps, mock_plan):
        mock_plan.return_value = {"plan_name": "starter", "display_name": "Starter", "can_use_live_search": True, "monthly_limit": 10}
        mock_apps.return_value = 1  # Starter limit is 1
        db = MagicMock()

        can_create, reason, details = check_application_eligibility(db, "user-123", "org-123")
        assert not can_create
        assert reason == "limit_exceeded"

    @patch("utils.subscription.get_user_plan")
    @patch("utils.subscription.get_monthly_applications")
    def test_application_allowed_under_limit(self, mock_apps, mock_plan):
        mock_plan.return_value = {"plan_name": "professional", "display_name": "Pro", "can_use_live_search": True, "monthly_limit": 100}
        mock_apps.return_value = 2
        db = MagicMock()

        can_create, reason, details = check_application_eligibility(db, "user-123", "org-123")
        assert can_create
        assert reason == "ok"
        assert details["remaining"] == 3  # 5 - 2


# ===========================================================================
# 7. AI credit limits
# ===========================================================================

class TestAICreditLimits:
    """Verify AI credit enforcement for name gen (cost=1) and logo gen (cost=5)."""

    def test_free_no_ai_credits(self):
        assert PLAN_FEATURES["free"]["monthly_ai_credits"] == 0

    def test_starter_30_ai_credits(self):
        assert PLAN_FEATURES["starter"]["monthly_ai_credits"] == 30

    @patch("utils.subscription._reset_monthly_ai_credits_if_needed")
    @patch("utils.subscription.get_org_plan")
    def test_ai_credits_blocked_when_insufficient(self, mock_org, mock_reset):
        mock_org.return_value = {"plan_name": "starter", "display_name": "Starter"}
        db = MagicMock()
        cursor = MagicMock()
        db.cursor.return_value = cursor
        cursor.fetchone.return_value = {"ai_credits_monthly": 3, "ai_credits_purchased": 0}

        # Logo gen costs 5, only 3 available
        can_use, reason, details = check_ai_credit_eligibility(db, "org-123", cost=5)
        assert not can_use
        assert reason == "credits_exhausted"

    @patch("utils.subscription._reset_monthly_ai_credits_if_needed")
    @patch("utils.subscription.get_org_plan")
    def test_ai_credits_allowed_when_sufficient(self, mock_org, mock_reset):
        mock_org.return_value = {"plan_name": "starter", "display_name": "Starter"}
        db = MagicMock()
        cursor = MagicMock()
        db.cursor.return_value = cursor
        cursor.fetchone.return_value = {"ai_credits_monthly": 10, "ai_credits_purchased": 0}

        can_use, reason, details = check_ai_credit_eligibility(db, "org-123", cost=1)
        assert can_use
        assert details["total_remaining"] == 10


# ===========================================================================
# 8. Auto-scan limits
# ===========================================================================

class TestAutoScanLimits:
    """Verify auto-scan max items per plan."""

    def test_free_no_auto_scan(self):
        assert PLAN_FEATURES["free"]["auto_scan_max_items"] == 0

    def test_starter_15_auto_scan(self):
        assert PLAN_FEATURES["starter"]["auto_scan_max_items"] == 15

    def test_business_50_auto_scan(self):
        assert PLAN_FEATURES["business"]["auto_scan_max_items"] == 50

    def test_professional_100_auto_scan(self):
        assert PLAN_FEATURES["professional"]["auto_scan_max_items"] == 100


# ===========================================================================
# 9. Portfolio access (can_view_holder_portfolio)
# ===========================================================================

class TestPortfolioAccess:
    """Verify holder/attorney portfolio access per plan."""

    def test_free_no_portfolio_access(self):
        assert PLAN_FEATURES["free"]["can_view_holder_portfolio"] is False

    def test_starter_no_portfolio_access(self):
        assert PLAN_FEATURES["starter"]["can_view_holder_portfolio"] is False

    def test_business_has_portfolio_access(self):
        assert PLAN_FEATURES["business"]["can_view_holder_portfolio"] is True

    def test_professional_has_portfolio_access(self):
        assert PLAN_FEATURES["professional"]["can_view_holder_portfolio"] is True


# ===========================================================================
# 10. Logo tracking (can_track_logos)
# ===========================================================================

class TestLogoTracking:
    """Verify logo tracking enforcement."""

    def test_free_no_logo_tracking(self):
        assert PLAN_FEATURES["free"]["can_track_logos"] is False

    def test_starter_has_logo_tracking(self):
        assert PLAN_FEATURES["starter"]["can_track_logos"] is True


# ===========================================================================
# 11. CSV export (can_export_csv_leads)
# ===========================================================================

class TestCSVExport:
    """Verify CSV lead export enforcement."""

    def test_free_no_csv_export(self):
        assert PLAN_FEATURES["free"]["can_export_csv_leads"] is False

    def test_starter_no_csv_export(self):
        assert PLAN_FEATURES["starter"]["can_export_csv_leads"] is False

    def test_business_no_csv_export(self):
        assert PLAN_FEATURES["business"]["can_export_csv_leads"] is False

    def test_professional_no_csv_export(self):
        assert PLAN_FEATURES["professional"]["can_export_csv_leads"] is False

    def test_enterprise_has_csv_export(self):
        assert PLAN_FEATURES["enterprise"]["can_export_csv_leads"] is True


# ===========================================================================
# 12. Application table fix (trademark_applications_mt)
# ===========================================================================

class TestApplicationTableFix:
    """Verify get_monthly_applications queries correct table."""

    def test_queries_mt_table(self):
        """get_monthly_applications() should query trademark_applications_mt (not trademark_applications)."""
        import inspect
        source = inspect.getsource(get_monthly_applications)
        assert "trademark_applications_mt" in source, (
            "get_monthly_applications() should query 'trademark_applications_mt' table"
        )
        assert "trademark_applications\n" not in source.replace("trademark_applications_mt", ""), (
            "get_monthly_applications() should NOT query old 'trademark_applications' table"
        )


# ===========================================================================
# 13. User plan expiry
# ===========================================================================

class TestPlanExpiry:
    """Verify expired subscriptions fall back to free."""

    @patch("utils.subscription.RealDictCursor")
    def test_expired_plan_returns_free(self, mock_cursor_cls):
        db = MagicMock()
        cursor = MagicMock()
        db.cursor.return_value = cursor
        cursor.fetchone.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
            "can_use_live_search": True,
            "is_superadmin": False,
            "subscription_end_date": date.today() - timedelta(days=1),  # Expired yesterday
        }

        result = get_user_plan(db, "user-123")
        assert result["plan_name"] == "free"

    @patch("utils.subscription.RealDictCursor")
    def test_active_plan_returns_correct_plan(self, mock_cursor_cls):
        db = MagicMock()
        cursor = MagicMock()
        db.cursor.return_value = cursor
        cursor.fetchone.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
            "can_use_live_search": True,
            "is_superadmin": False,
            "subscription_end_date": date.today() + timedelta(days=30),  # Active
        }

        result = get_user_plan(db, "user-123")
        assert result["plan_name"] == "professional"

    @patch("utils.subscription.RealDictCursor")
    def test_superadmin_bypasses_expiry(self, mock_cursor_cls):
        db = MagicMock()
        cursor = MagicMock()
        db.cursor.return_value = cursor
        cursor.fetchone.return_value = {
            "plan_name": "professional",
            "display_name": "Professional",
            "can_use_live_search": True,
            "is_superadmin": True,
            "subscription_end_date": date.today() - timedelta(days=100),  # Expired
        }

        result = get_user_plan(db, "user-123")
        assert result["plan_name"] == "superadmin"  # Superadmin overrides


# ===========================================================================
# 14. Price consistency
# ===========================================================================

class TestPricing:
    """Verify pricing structure."""

    def test_annual_cheaper_than_monthly(self):
        """Annual price per month should be < monthly price for paid plans."""
        for plan_name in ["starter", "business", "professional", "enterprise"]:
            features = PLAN_FEATURES[plan_name]
            monthly = features["price_monthly"]
            annual = features["price_annual_monthly"]
            if monthly > 0:
                assert annual < monthly, (
                    f"Plan '{plan_name}': annual ({annual}) should be < monthly ({monthly})"
                )

    def test_free_plan_is_free(self):
        assert PLAN_FEATURES["free"]["price_monthly"] == 0
        assert PLAN_FEATURES["free"]["price_annual_monthly"] == 0


# ===========================================================================
# 15. Bulk endpoint limit enforcement (code verification)
# ===========================================================================

class TestBulkEndpointLimitEnforcement:
    """Verify bulk endpoints have limit checks in their source code."""

    def test_bulk_import_has_limit_check(self):
        """POST /watchlist/bulk should check max_watchlist_items."""
        from api.routes import bulk_import_watchlist
        import inspect
        source = inspect.getsource(bulk_import_watchlist)
        assert "max_watchlist_items" in source, "bulk_import_watchlist must check max_watchlist_items"
        assert "remaining_slots" in source, "bulk_import_watchlist must track remaining_slots"

    def test_bulk_from_portfolio_has_limit_check(self):
        """POST /watchlist/bulk-from-portfolio should check max_watchlist_items."""
        from api.routes import bulk_import_from_portfolio
        import inspect
        source = inspect.getsource(bulk_import_from_portfolio)
        assert "max_watchlist_items" in source, "bulk_import_from_portfolio must check max_watchlist_items"
        assert "remaining_slots" in source, "bulk_import_from_portfolio must track remaining_slots"

    def test_bulk_from_portfolio_has_portfolio_access_check(self):
        """POST /watchlist/bulk-from-portfolio should check can_view_holder_portfolio."""
        from api.routes import bulk_import_from_portfolio
        import inspect
        source = inspect.getsource(bulk_import_from_portfolio)
        assert "can_view_holder_portfolio" in source, (
            "bulk_import_from_portfolio must check can_view_holder_portfolio"
        )

    def test_upload_with_mapping_has_limit_check(self):
        """POST /watchlist/upload/with-mapping should check max_watchlist_items."""
        from api.routes import upload_with_mapping
        import inspect
        source = inspect.getsource(upload_with_mapping)
        assert "max_watchlist_items" in source, "upload_with_mapping must check max_watchlist_items"

    def test_upload_file_has_limit_check(self):
        """POST /watchlist/upload should check max_watchlist_items."""
        from api.routes import upload_file
        import inspect
        source = inspect.getsource(upload_file)
        assert "max_watchlist_items" in source or "remaining_slots" in source, (
            "upload_file must check max_watchlist_items"
        )


# ===========================================================================
# 16. Lead action endpoint access checks (code verification)
# ===========================================================================

class TestLeadActionAccessChecks:
    """Verify lead action endpoints check access before modification."""

    def test_contact_lead_has_access_check(self):
        from api.leads import mark_lead_contacted
        import inspect
        source = inspect.getsource(mark_lead_contacted)
        assert "_require_lead_access" in source, (
            "mark_lead_contacted must call _require_lead_access"
        )

    def test_convert_lead_has_access_check(self):
        from api.leads import mark_lead_converted
        import inspect
        source = inspect.getsource(mark_lead_converted)
        assert "_require_lead_access" in source, (
            "mark_lead_converted must call _require_lead_access"
        )

    def test_dismiss_lead_has_access_check(self):
        from api.leads import dismiss_lead
        import inspect
        source = inspect.getsource(dismiss_lead)
        assert "_require_lead_access" in source, (
            "dismiss_lead must call _require_lead_access"
        )


# ===========================================================================
# 17. Auto-scan endpoint access checks (code verification)
# ===========================================================================

class TestAutoScanAccessChecks:
    """Verify scan endpoints check auto_scan_max_items."""

    def test_scan_all_has_limit_check(self):
        from api.routes import trigger_scan_all
        import inspect
        source = inspect.getsource(trigger_scan_all)
        assert "auto_scan_max_items" in source, (
            "trigger_scan_all must check auto_scan_max_items"
        )

    def test_rescan_has_limit_check(self):
        from api.routes import rescan_all_watchlist
        import inspect
        source = inspect.getsource(rescan_all_watchlist)
        assert "auto_scan_max_items" in source, (
            "rescan_all_watchlist must check auto_scan_max_items"
        )


# ===========================================================================
# 18. Report download checks (code verification)
# ===========================================================================

class TestReportDownloadChecks:
    """Verify report download checks can_export_reports."""

    def test_download_report_checks_export_permission(self):
        from api.reports import download_report
        import inspect
        source = inspect.getsource(download_report)
        assert "can_export_reports" in source, (
            "download_report must check can_export_reports"
        )


# ===========================================================================
# 19. Application eligibility check (code verification)
# ===========================================================================

class TestApplicationEligibilityCheck:
    """Verify application creation checks eligibility."""

    def test_create_application_checks_eligibility(self):
        from api.applications import create_application
        import inspect
        source = inspect.getsource(create_application)
        assert "check_application_eligibility" in source, (
            "create_application must call check_application_eligibility"
        )


# ===========================================================================
# 20. Cross-cutting: all boolean feature flags per plan
# ===========================================================================

class TestBooleanFeatureFlags:
    """Verify all boolean flags are consistent across plans."""

    BOOLEAN_FEATURES = [
        "can_export_reports", "can_track_logos",
        "can_view_holder_portfolio", "can_export_csv_leads",
        "can_use_live_scraping", "priority_support",
        "api_access", "dedicated_account_manager",
    ]

    def test_all_boolean_features_are_bool(self):
        """All boolean features should be actual bool type."""
        for plan_name, features in PLAN_FEATURES.items():
            for key in self.BOOLEAN_FEATURES:
                val = features[key]
                assert isinstance(val, bool), (
                    f"Plan '{plan_name}' feature '{key}' = {val!r} "
                    f"(expected bool, got {type(val).__name__})"
                )

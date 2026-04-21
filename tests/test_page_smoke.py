import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
APP_ASSETS = ROOT / "app_assets.py"


def _read_locale(locale: str) -> dict:
    return json.loads((STATIC / "locales" / f"{locale}.json").read_text(encoding="utf-8"))


def _nested_get(data: dict, dotted_key: str):
    current = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def test_admin_feature_page_loads_canonical_script_url():
    html = (TEMPLATES / "admin" / "page.html").read_text(encoding="utf-8")
    assert "{% include 'admin/partials/_overview.html' %}" in html
    assert '<script src="/static/js/admin/panel.js"></script>' in html


def test_admin_feature_bundle_contains_panel_bootstrap():
    script = (STATIC / "js" / "admin" / "panel.js").read_text(encoding="utf-8")
    assert "function adminPanel()" in script
    assert "function adminOverview()" in script


def test_asset_routes_render_canonical_feature_templates():
    source = APP_ASSETS.read_text(encoding="utf-8")
    assert 'name="marketing/landing.html"' in source
    assert 'name="dashboard/page.html"' in source
    assert 'name="admin/page.html"' in source
    assert 'name="billing/pricing.html"' in source
    assert 'name="billing/checkout.html"' in source


def test_pricing_feature_page_keeps_checkout_links():
    html = (TEMPLATES / "billing" / "pricing.html").read_text(encoding="utf-8")
    assert 'x-data="pricingPage()"' in html
    assert 'href="/checkout?plan=free&billing=monthly"' in html


def test_checkout_feature_page_keeps_checkout_bootstrap():
    html = (TEMPLATES / "billing" / "checkout.html").read_text(encoding="utf-8")
    assert 'x-data="checkoutPage()"' in html
    assert "function checkoutPage()" in html


def test_checkout_feature_page_keeps_forgot_password_reset_flow():
    html = (TEMPLATES / "billing" / "checkout.html").read_text(encoding="utf-8")
    assert "openForgotPassword()" in html
    assert "forgotRequestCode()" in html
    assert "forgotResetPassword()" in html
    assert "/api/v1/auth/forgot-password" in html
    assert "/api/v1/auth/reset-password" in html
    assert "t('auth.forgot_password')" in html


def test_billing_feature_pages_use_app_i18n_without_local_fallback_tables():
    pricing_html = (TEMPLATES / "billing" / "pricing.html").read_text(encoding="utf-8")
    checkout_html = (TEMPLATES / "billing" / "checkout.html").read_text(encoding="utf-8")

    for html in (pricing_html, checkout_html):
        assert "window.AppI18n ? window.AppI18n.t(key, params) : key" in html
        assert "window.AppI18n.onReady" in html
        assert "locale-changed" in html
        assert "AppUtils.t" not in html
        assert "FALLBACKS" not in html


def test_billing_feature_pages_keep_mobile_responsive_guards():
    pricing_html = (TEMPLATES / "billing" / "pricing.html").read_text(encoding="utf-8")
    checkout_html = (TEMPLATES / "billing" / "checkout.html").read_text(encoding="utf-8")

    assert "grid grid-cols-1 gap-4 sm:gap-5 md:grid-cols-2 xl:grid-cols-4" in pricing_html
    assert "glass-panel w-full rounded-[28px] p-5 sm:min-w-[320px] sm:w-auto" in pricing_html
    assert "order-first lg:order-none lg:col-span-1" in checkout_html
    assert "order-last space-y-6 lg:order-none lg:col-span-2" in checkout_html
    assert "mb-6 flex w-full max-w-md rounded-full bg-slate-100/90 p-1.5 shadow-inner" in checkout_html


def test_checkout_feature_page_translates_unlimited_summary_limits():
    checkout_html = (TEMPLATES / "billing" / "checkout.html").read_text(encoding="utf-8")

    assert "summaryFeatureText('search')" in checkout_html
    assert "summaryFeatureText('watchlist')" in checkout_html
    assert "summaryFeatureText('live')" in checkout_html
    assert "isUnlimitedLimit(value)" in checkout_html
    assert "pricing.f_unlimited_searches" in checkout_html
    assert "pricing.f_unlimited_watchlist" in checkout_html
    assert "pricing.f_unlimited_live" in checkout_html


def test_billing_locale_files_include_required_keys_for_all_supported_languages():
    required_keys = [
        "pricing.page_title",
        "pricing.header_badge",
        "pricing.hero_eyebrow",
        "pricing.billing_cadence",
        "pricing.badge_entry",
        "pricing.badge_solo",
        "pricing.badge_most_used",
        "pricing.badge_scale",
        "pricing.annual_save_inline",
        "pricing.starter_name",
        "pricing.professional_name",
        "pricing.enterprise_name",
        "checkout.page_title",
        "checkout.header_badge",
        "checkout.hero_eyebrow",
        "checkout.hero_title",
        "checkout.hero_desc",
        "checkout.session_label",
        "checkout.step_label",
        "checkout.step_account_desc",
        "checkout.step_payment_desc",
        "checkout.annual_save_inline",
        "checkout.account_eyebrow",
        "checkout.account_desc",
        "checkout.payment_eyebrow",
        "checkout.payment_desc",
        "checkout.summary_billing_label",
        "search.rate_limited",
        "upgrade.eyebrow",
        "upgrade.generic_title",
        "upgrade.generic_description",
        "upgrade.recommended_badge",
    ]

    for locale in ("en", "tr", "ar"):
        data = _read_locale(locale)
        for key in required_keys:
            assert _nested_get(data, key) is not None, f"{locale} missing {key}"

    assert _read_locale("ar")["dir"] == "rtl"


def test_billing_locale_files_localize_paid_plan_names_for_turkish_and_arabic():
    assert _nested_get(_read_locale("tr"), "pricing.starter_name") == "Başlangıç"
    assert _nested_get(_read_locale("tr"), "pricing.professional_name") == "Profesyonel"
    assert _nested_get(_read_locale("tr"), "pricing.enterprise_name") == "Kurumsal"
    assert _nested_get(_read_locale("ar"), "pricing.starter_name") == "أساسي"
    assert _nested_get(_read_locale("ar"), "pricing.professional_name") == "احترافي"
    assert _nested_get(_read_locale("ar"), "pricing.enterprise_name") == "مؤسسات"


def test_pricing_feature_page_omits_plan_micro_badges():
    pricing_html = (TEMPLATES / "billing" / "pricing.html").read_text(encoding="utf-8")

    assert "t('pricing.badge_entry')" not in pricing_html
    assert "t('pricing.badge_solo')" not in pricing_html
    assert "t('pricing.badge_most_used')" not in pricing_html
    assert "t('pricing.badge_scale')" not in pricing_html


def test_pricing_annual_bill_strings_do_not_duplicate_currency_symbols():
    for locale in ("en", "tr", "ar"):
        billed_annually = _nested_get(_read_locale(locale), "pricing.billed_annually")
        assert billed_annually is not None
        assert "{total}" in billed_annually
        assert "₺" not in billed_annually


def test_pricing_annual_bill_strings_stay_readable_in_turkish_and_arabic():
    assert _nested_get(_read_locale("tr"), "pricing.billed_annually") == "Yıllık fatura: {total}"
    assert _nested_get(_read_locale("ar"), "pricing.billed_annually") == "الفاتورة السنوية: {total}"


def test_i18n_loader_bypasses_stale_locale_cache():
    script = (STATIC / "js" / "utils" / "i18n.js").read_text(encoding="utf-8")
    assert "cache: 'no-store'" in script
    assert "window.AppI18n._localeAssetVersion" in script


def test_dashboard_feature_page_loads_canonical_dashboard_script_url():
    html = (TEMPLATES / "dashboard" / "page.html").read_text(encoding="utf-8")
    modals_html = (TEMPLATES / "dashboard" / "partials" / "_modals.html").read_text(encoding="utf-8")
    assert "{% include 'dashboard/partials/_navbar.html' %}" in html
    assert "window.SERVER_PLANS = {{ plans | default({}, true) | tojson }};" in html
    assert "{% include 'shared/_upgrade_modal.html' %}" in modals_html
    assert 'id="bulk-watchlist-modal"' in modals_html
    assert 'id="bulk-upgrade-offer"' in modals_html
    assert "watchlist.bulk_upgrade_title" in modals_html
    assert "required_feature_value = requiredCapacity" in modals_html
    assert '<script src="/static/js/utils/upgrade-modal.js?v=3"></script>' in html
    assert '<script src="/static/js/dashboard/app.js?v=57"></script>' in html


def test_dashboard_feature_bundle_contains_dashboard_bootstrap():
    script = (STATIC / "js" / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert "function dashboard()" in script
    assert "showUpgradeModal(err, 'applications')" in script
    assert "showUpgradeModal(err, 'reports')" in script
    assert "showUpgradeModal(e, 'watchlist_logo')" in script
    assert "window.AppUpgradeModal.maybeHandle(detail, 'quick_search')" in script
    assert "this.t('search.rate_limited')" in script
    assert "this.t('watchlist.added_toast')" in script
    assert "this.t('watchlist.added_success')" not in script


def test_landing_feature_page_loads_canonical_landing_script_url():
    html = (TEMPLATES / "marketing" / "landing.html").read_text(encoding="utf-8")
    assert "window.SERVER_PLANS = {{ plans | default({}, true) | tojson }};" in html
    assert "{% include 'shared/_upgrade_modal.html' %}" in html
    assert '<script src="/static/js/utils/upgrade-modal.js?v=3"></script>' in html
    assert '<script src="/static/js/marketing/landing.js?v=36"></script>' in html
    assert 'x-text="t(\'landing.nav_pricing\')"' in html
    assert 'href="/checkout?plan=enterprise&billing=monthly"' in html
    assert 'x-text="t(\'landing.cta_start\')"' in html
    assert 'x-text="t(\'landing.cta_contact\')"' not in html
    assert 'class="block w-full py-2.5 rounded-lg text-sm font-medium text-white text-center no-underline" style="background:var(--color-primary)" x-text="t(\'landing.cta_start\')"' in html


def test_landing_feature_bundle_contains_landing_bootstrap():
    script = (STATIC / "js" / "marketing" / "landing.js").read_text(encoding="utf-8")
    assert "function landing()" in script
    assert "window.AppUpgradeModal.maybeHandle(detail, 'public_search')" in script
    assert "self.t('search.rate_limited')" in script


def test_shared_upgrade_modal_bundle_keeps_plan_handoff_logic():
    script = (STATIC / "js" / "utils" / "upgrade-modal.js").read_text(encoding="utf-8")
    template = (TEMPLATES / "shared" / "_upgrade_modal.html").read_text(encoding="utf-8")

    assert "window.AppUpgradeModal" in script
    assert "resolveOffer" in script
    assert "allowedPlans: ['enterprise']" in script
    assert "public_search: { feature: 'max_daily_quick_searches', kind: 'numeric' }" in script
    assert "leads: { feature: 'daily_lead_views', kind: 'numeric' }" in script
    assert "Object.assign({}, FALLBACK_PLANS[planName] || {}, sourcePlan || {})" in script
    assert "FALLBACK_PLANS[planName] || FALLBACK_PLANS.free" in script
    assert "required_feature_value != null" in script
    assert "toNumber(candidateValue) >= requiredValue" in script
    assert "monthly_limit_exceeded" in script
    assert 'id="upgrade-plan-code"' in template
    assert 'id="upgrade-feature-list"' in template

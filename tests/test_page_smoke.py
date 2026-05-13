import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
APP_ASSETS = ROOT / "app_assets.py"
DOCKER_COMPOSE = ROOT / "docker-compose.yml"
ENV_PRODUCTION_EXAMPLE = ROOT / ".env.production.example"


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
    assert '<script src="/static/js/admin/panel.js?v=2"></script>' in html


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


def test_docker_backend_uses_env_file_for_llm_provider_keys():
    compose = DOCKER_COMPOSE.read_text(encoding="utf-8")
    env_example = ENV_PRODUCTION_EXAMPLE.read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "- .env.production" in compose
    assert "CREATIVE_GOOGLE_API_KEY: ${CREATIVE_GOOGLE_API_KEY:-}" not in compose
    assert "./generative_ai:/app/generative_ai:ro" in compose
    assert "openai>=" in requirements
    assert "CREATIVE_DEEPSEEK_API_KEY=YOUR_DEEPSEEK_API_KEY" in env_example
    assert "CREATIVE_DEEPSEEK_TEXT_MODEL=deepseek-v4-pro" in env_example
    assert "CREATIVE_DEEPSEEK_TIMEOUT=120" in env_example
    assert "CREATIVE_QWEN_API_KEY=YOUR_QWEN_OR_DASHSCOPE_API_KEY" in env_example
    assert "CREATIVE_QWEN_TEXT_MODEL=qwen-max" in env_example
    assert "CREATIVE_QWEN_VL_MODEL=qwen3-vl-plus" in env_example
    assert "CREATIVE_QWEN_TIMEOUT=120" in env_example
    assert "CREATIVE_GOOGLE_API_KEY=YOUR_GEMINI_API_KEY" in env_example


def test_risk_report_score_label_is_fully_localized_without_llm_copy():
    assert _nested_get(_read_locale("en"), "search.risk_report_llm_score") == "Risk score"
    assert _nested_get(_read_locale("tr"), "search.risk_report_llm_score") == "Risk skoru"
    assert _nested_get(_read_locale("ar"), "search.risk_report_llm_score") == "\u062f\u0631\u062c\u0629 \u0627\u0644\u0645\u062e\u0627\u0637\u0631"
    assert _nested_get(_read_locale("en"), "search.risk_report_ready_view") == "Your risk report is ready to view"
    assert _nested_get(_read_locale("en"), "search.risk_report_login_to_view") == "Log in to view this risk report."
    assert _nested_get(_read_locale("tr"), "search.risk_report_open") == "Raporu a\u00e7"
    assert _nested_get(_read_locale("tr"), "search.risk_report_login_to_view") == "Bu risk raporunu g\u00f6r\u00fcnt\u00fclemek i\u00e7in giri\u015f yap\u0131n."
    assert _nested_get(_read_locale("ar"), "search.risk_report_open") == "\u0641\u062a\u062d \u0627\u0644\u062a\u0642\u0631\u064a\u0631"
    assert _nested_get(_read_locale("ar"), "search.risk_report_login_to_view") == "\u0633\u062c\u0644 \u0627\u0644\u062f\u062e\u0648\u0644 \u0644\u0639\u0631\u0636 \u062a\u0642\u0631\u064a\u0631 \u0627\u0644\u0645\u062e\u0627\u0637\u0631 \u0647\u0630\u0627."
    assert _nested_get(_read_locale("en"), "sort.risk_report_desc") == "Risk report \u2193"
    assert _nested_get(_read_locale("tr"), "sort.risk_report_desc") == "Risk raporu \u2193"
    assert _nested_get(_read_locale("ar"), "sort.risk_report_desc") == "\u062a\u0642\u0631\u064a\u0631 \u0627\u0644\u0645\u062e\u0627\u0637\u0631 \u2193"


def test_search_score_cards_separate_original_text_from_translation_score():
    score_badge_js = (STATIC / "js" / "components" / "score-badge.js").read_text(
        encoding="utf-8"
    )
    dashboard_js = (STATIC / "js" / "dashboard" / "app.js").read_text(encoding="utf-8")
    dashboard_search = (
        TEMPLATES / "dashboard" / "partials" / "_search_panel.html"
    ).read_text(encoding="utf-8")
    landing_js = (STATIC / "js" / "marketing" / "landing.js").read_text(
        encoding="utf-8"
    )
    landing_html = (TEMPLATES / "marketing" / "landing.html").read_text(
        encoding="utf-8"
    )

    assert "getOriginalTextScore" in score_badge_js
    assert "scores.path_a_score" in score_badge_js
    assert "getEffectiveTextScoreForResult(sc)" in dashboard_js
    assert "sc.path_b_score" in dashboard_js
    assert "var textCombined = Math.max(textSim, semanticSim)" not in dashboard_js
    assert "Math.round(textSim * 100)" in dashboard_js
    assert "getTextScore(r)" in dashboard_search
    assert "getOriginalTextScore(result)" in landing_js
    assert "getTextScore(r)" in landing_html


def test_logo_only_trademark_names_use_localized_display_label_only():
    helpers_js = (STATIC / "js" / "utils" / "helpers.js").read_text(encoding="utf-8")
    result_card_js = (STATIC / "js" / "components" / "result-card.js").read_text(
        encoding="utf-8"
    )
    api_js = (STATIC / "js" / "api.js").read_text(encoding="utf-8")
    dashboard_js = (STATIC / "js" / "dashboard" / "app.js").read_text(
        encoding="utf-8"
    )
    dashboard_search = (
        TEMPLATES / "dashboard" / "partials" / "_search_panel.html"
    ).read_text(encoding="utf-8")
    dashboard_results = (
        TEMPLATES / "dashboard" / "partials" / "_results_panel.html"
    ).read_text(encoding="utf-8")
    landing_html = (TEMPLATES / "marketing" / "landing.html").read_text(
        encoding="utf-8"
    )

    assert _nested_get(_read_locale("tr"), "common.logo_only_mark") == "\"Şekil\""
    assert _nested_get(_read_locale("en"), "common.logo_only_mark") == "\"Logo\""
    assert _nested_get(_read_locale("ar"), "common.logo_only_mark") == "\"شعار\""

    assert "window.AppUtils.isLogoOnlyTrademarkName" in helpers_js
    assert "window.AppUtils.getTrademarkDisplayName" in helpers_js
    assert "record.trademark_name" in helpers_js
    assert "record.name" in helpers_js
    assert "common.logo_only_mark" in helpers_js
    assert "raw === '-'" in helpers_js
    assert "raw === '—'" in helpers_js
    assert "noWhitespace === 'n/a'" in helpers_js
    assert "alnumOnly === 'sekil'" in helpers_js

    assert 'x-text="getTrademarkDisplayName(r)"' in dashboard_search
    assert 'x-text="getTrademarkDisplayName(tm)"' in dashboard_search
    assert 'x-text="getTrademarkDisplayName(r)"' in landing_html
    assert 'x-text="getTrademarkDisplayName(tm)"' in landing_html
    assert "getTrademarkDisplayName(r)" in result_card_js
    assert "getTrademarkDisplayName(item)" in api_js
    assert "getTrademarkDisplayName(a.conflicting_brand_name)" in dashboard_js
    assert "getTrademarkDisplayName(a.watched_brand_name)" in dashboard_js
    assert "getTrademarkDisplayName(a.conflicting_brand)" in dashboard_js
    assert 'x-text="getTrademarkDisplayName(d.conflicting_brand)"' in dashboard_results

    assert "r.trademark_name || r.name || '\\u2014'" not in dashboard_search
    assert "r.trademark_name || '\\u2014'" not in landing_html
    assert "name: r.name || ''" in result_card_js
    assert "name: getTrademarkDisplayName" not in result_card_js
    assert "conflicting_brand: c.name || 'N/A'" in dashboard_js


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
    # Post-Quick-removal: `search` and `live` both render via `pricing.f_unlimited_searches`
    # (a single Agentic budget); the legacy `pricing.f_unlimited_live` key is no longer used.
    assert "pricing.f_unlimited_searches" in checkout_html
    assert "pricing.f_unlimited_watchlist" in checkout_html


def test_ai_studio_credit_exhaustion_uses_shared_upgrade_modal():
    api_js = (STATIC / "js" / "api.js").read_text(encoding="utf-8")
    dashboard_js = (STATIC / "js" / "dashboard" / "app.js").read_text(encoding="utf-8")
    upgrade_js = (STATIC / "js" / "utils" / "upgrade-modal.js").read_text(encoding="utf-8")

    assert "showCreditsModal(data.detail);" not in api_js
    assert "showLogoCreditsExhausted(data.detail);" not in api_js
    assert "showUpgradeModal(data.detail || data, 'ai_credits')" in api_js
    assert "showUpgradeModal(detail || { error: 'credits_exhausted' }, 'ai_credits')" in dashboard_js
    assert "'credits_exhausted'" in upgrade_js


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
        "common.logo_only_mark",
        "search.rate_limited",
        "upgrade.eyebrow",
        "upgrade.generic_title",
        "upgrade.generic_description",
        "upgrade.search_limit_title",
        "upgrade.search_limit_description",
        "upgrade.watchlist_title",
        "upgrade.watchlist_description",
        "upgrade.live_search_title",
        "upgrade.live_search_description",
        "upgrade.leads_title",
        "upgrade.leads_description",
        "upgrade.recommended_badge",
        "watchlist.upload_upgrade_title",
        "watchlist.upload_upgrade_desc",
        "watchlist.upload_limit_result",
        "landing.nav_education",
        "landing.education_title",
        "landing.education_subtitle",
        "landing.education_materials_note",
        "landing.education_pdf_library",
        "landing.education_flashcards",
        "landing.education_quizzes",
        "landing.education_categories",
        "landing.education_categories_started",
        "landing.education_choose_category",
        "landing.education_active_category",
        "landing.education_category_progress",
        "landing.education_progress_title",
        "landing.education_completed_modules",
        "landing.education_progress_synced",
        "landing.education_progress_local",
        "landing.education_in_progress_label",
        "landing.education_sync_hint",
        "landing.education_sign_in_to_sync",
        "landing.education_sync_now",
        "landing.education_loading",
        "landing.education_pdf_hint",
        "landing.education_open_pdf",
        "landing.education_mark_reviewed",
        "landing.education_decks_label",
        "landing.education_cards",
        "landing.education_continue_deck",
        "landing.education_view_deck",
        "landing.education_select_deck",
        "landing.education_front_label",
        "landing.education_back_label",
        "landing.education_tap_to_flip",
        "landing.education_previous",
        "landing.education_next",
        "landing.education_finish_deck",
        "landing.education_sections_label",
        "landing.education_questions",
        "landing.education_continue_quiz",
        "landing.education_start_quiz",
        "landing.education_select_quiz",
        "landing.education_answered_label",
        "landing.education_score",
        "landing.education_question_label",
        "landing.education_explain",
        "landing.education_hide_explanation",
        "landing.education_explanation_title",
        "landing.education_preparing_explanation",
        "landing.education_thats_right",
        "landing.education_right_answer",
        "landing.education_not_quite",
        "landing.education_correct_answer",
        "landing.education_review_answer",
        "landing.education_finish_quiz",
        "landing.education_status_not_started",
        "landing.education_status_in_progress",
        "landing.education_status_completed",
        "landing.education_no_flashcards",
        "landing.education_no_quiz",
        "landing.education_progress_local_only",
        "landing.education_load_failed",
        "landing.education_tester_tools",
        "landing.education_tester_category",
        "landing.education_tester_edit_explanation",
        "landing.education_tester_explanation_label",
        "landing.education_tester_summary_label",
        "landing.education_tester_save",
        "landing.education_tester_cancel",
        "landing.education_tester_delete",
        "landing.education_tester_delete_confirm",
        "landing.education_tester_save_failed",
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


def test_landing_education_nav_label_is_localized_for_supported_languages():
    assert _nested_get(_read_locale("en"), "landing.nav_education") == "Education"
    assert _nested_get(_read_locale("tr"), "landing.nav_education") == "Eğitim"
    assert _nested_get(_read_locale("ar"), "landing.nav_education") == "التعليم"


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


def test_i18n_loader_uses_versioned_locale_bundle_cache():
    script = (STATIC / "js" / "utils" / "i18n.js").read_text(encoding="utf-8")
    assert "window.AppI18n._localeBundleCachePrefix" in script
    assert "window.AppI18n._getLocaleCacheKey" in script
    assert "window.AppI18n._hydrateLocaleFromCache(window.AppI18n._locale, false);" in script
    assert "window.AppI18n._writeCachedLocaleData(locale, data);" in script
    assert "cache: 'no-store'" not in script
    assert "window.AppI18n._localeAssetVersion" in script
    assert "window.AppI18n.setLocale(window.AppI18n._locale, { skipCacheHydrate: true });" in script


def test_i18n_feature_pages_load_current_bundle_version():
    expected_script = '<script src="/static/js/utils/i18n.js?v=51"></script>'
    for relative_path in (
        TEMPLATES / "marketing" / "landing.html",
        TEMPLATES / "dashboard" / "page.html",
        TEMPLATES / "billing" / "pricing.html",
        TEMPLATES / "billing" / "checkout.html",
    ):
        html = relative_path.read_text(encoding="utf-8")
        assert expected_script in html


def test_dashboard_feature_page_loads_canonical_dashboard_script_url():
    html = (TEMPLATES / "dashboard" / "page.html").read_text(encoding="utf-8")
    modals_html = (TEMPLATES / "dashboard" / "partials" / "_modals.html").read_text(encoding="utf-8")
    watchlist_panel_html = (TEMPLATES / "dashboard" / "partials" / "_watchlist_panel.html").read_text(encoding="utf-8")
    assert "{% include 'dashboard/partials/_navbar.html' %}" in html
    assert "window.SERVER_PLANS = {{ plans | default({}, true) | tojson }};" in html
    assert "{% include 'shared/_upgrade_modal.html' %}" in modals_html
    assert 'id="bulk-watchlist-modal"' in modals_html
    assert 'id="bulk-upgrade-offer"' in modals_html
    assert "watchlist.bulk_upgrade_title" in modals_html
    assert "required_feature_value = requiredCapacity" in modals_html
    assert '<script src="/static/js/utils/upgrade-modal.js?v=8"></script>' in html
    assert '<script src="/static/js/api.js?v=39"></script>' in html
    assert '<script src="/static/js/dashboard/app.js?v=88"></script>' in html
    assert "claim_risk_report" in html
    assert 'id="watchlist-upload-modal-card"' in watchlist_panel_html
    assert 'id="upload-wl-upgrade-offer"' in watchlist_panel_html
    assert "watchlist.upload_upgrade_title" in watchlist_panel_html
    assert 'onclick="showBulkUploadStepOne()"' in watchlist_panel_html
    assert 'id="wl-view-select"' in watchlist_panel_html
    assert 'wl-view-tab-appeals' not in watchlist_panel_html
    assert 'wl-view-tab-renewal' not in watchlist_panel_html


def test_dashboard_feature_bundle_contains_dashboard_bootstrap():
    script = (STATIC / "js" / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert "function dashboard()" in script
    assert "showUpgradeModal(err, 'applications')" in script
    assert "showUpgradeModal(err, 'reports')" in script
    assert "showUpgradeModal(e, 'watchlist_logo')" in script
    assert "var DASHBOARD_SEARCH_RESULT_LIMIT = 20;" in script
    assert "slice(0, 30)" not in script
    assert "Math.min(data.total || 0, 30)" not in script
    assert "Math.min(data.total || results.length, 30)" not in script
    assert "window.AppUpgradeModal.maybeHandle(detail, 'agentic_search')" in script
    assert "this.t('search.rate_limited')" in script
    assert "this.t('watchlist.added_toast')" in script
    assert "this.t('watchlist.added_success')" not in script
    assert "renderUploadUpgradeOffer(totalRows)" in script
    assert "showBulkUploadStepOne()" in script
    assert "isUploadLimitOnlyResult(data)" in script
    assert "'watchlist.upload_limit_result'" in script
    assert "function onWatchlistViewFilterChange()" in script
    assert "var _wlView = 'all';" in script
    assert "plan-limits-info" not in script
    assert "fetch('/api/v1/auth/me', usageOpts)" not in script
    assert "item.custom_logo_url || (item.has_custom_logo ? item.logo_url : null)" in script
    assert "item.trademark_image_path" in script
    assert "(item.has_custom_logo" in script


def test_landing_feature_page_loads_canonical_landing_script_url():
    html = (TEMPLATES / "marketing" / "landing.html").read_text(encoding="utf-8")
    assert "window.SERVER_PLANS = {{ plans | default({}, true) | tojson }};" in html
    assert "{% include 'shared/_upgrade_modal.html' %}" in html
    assert '<script src="/static/js/utils/i18n.js?v=51"></script>' in html
    assert '<script src="/static/js/utils/upgrade-modal.js?v=8"></script>' in html
    assert '<script src="/static/js/marketing/landing.js?v=64"></script>' in html
    assert 'id="landing-risk-report-btn"' in html
    assert "generateRiskReport()" in html
    assert "riskReport && !riskReportLoading && !riskReportError" in html
    assert 'data-testid="landing-risk-report-ready-card"' in html
    assert 'data-testid="landing-risk-report-open-button"' in html
    assert "openRiskReportPdf(riskReport)" in html
    assert "t('search.risk_report_ready_view')" in html
    assert "t('search.risk_report_title')" in html
    assert "candidate.image_url" in html
    assert 'x-text="t(\'landing.nav_pricing\')"' in html
    assert 'x-text="t(\'landing.nav_education\')"' in html
    assert "activeTab === 'education'" in html
    assert 'x-text="t(\'landing.education_categories\')"' in html
    assert 'x-text="t(\'landing.education_active_category\')"' in html
    assert "education-theme-shell" in html
    assert "education-theme-panel" in html
    assert "education-theme-chip-active" in html
    assert 'id="education-progress-overview"' in html
    assert 'id="education-mobile-workspace-nav"' in html
    assert 'id="education-flashcards-panel"' in html
    assert 'id="education-quiz-panel"' in html
    assert 'id="education-pdf-library-panel"' in html
    assert 'data-testid="education-mobile-quick-quiz"' in html
    assert 'data-testid="education-mobile-nav-quiz"' in html
    assert 'data-testid="education-quiz-explain-button"' in html
    assert 'data-testid="education-quiz-explanation-loading"' in html
    assert 'data-testid="education-quiz-explanation-panel"' in html
    assert 'data-testid="education-flashcard-tester-tools"' in html
    assert 'data-testid="education-flashcard-category-select"' in html
    assert 'data-testid="education-flashcard-delete-button"' in html
    assert 'data-testid="education-quiz-tester-tools"' in html
    assert 'data-testid="education-quiz-category-select"' in html
    assert 'data-testid="education-quiz-edit-explanation-button"' in html
    assert 'data-testid="education-quiz-explanation-editor"' in html
    assert 'data-testid="education-quiz-explanation-input"' in html
    assert 'data-testid="education-quiz-summary-input"' in html
    landing_js = (STATIC / "js" / "marketing" / "landing.js").read_text(encoding="utf-8")
    assert "showRiskReportReadyNotification(data)" in landing_js
    assert "self.riskReport = data" in landing_js
    assert "'/api/v1/search/risk-report/public'" in landing_js
    assert "claim_risk_report" in landing_js
    assert "new FormData()" in landing_js
    assert "body.append('query_image', this.selectedImage" in landing_js
    assert 'data-testid="education-quiz-explanation-save-button"' in html
    assert 'data-testid="education-quiz-explanation-cancel-button"' in html
    assert 'data-testid="education-quiz-delete-button"' in html
    assert 'href="/checkout?plan=enterprise&billing=monthly"' in html
    assert 'x-text="t(\'landing.cta_start\')"' in html
    assert 'x-text="t(\'landing.cta_contact\')"' not in html
    assert "pricing.includes_free" not in html
    assert "pricing.includes_starter" not in html
    assert "pricing.includes_professional" not in html
    assert "hover:-translate-y-2" in html
    assert "hover:shadow-2xl" in html
    assert 'class="block w-full py-2.5 rounded-lg text-sm font-medium text-white text-center no-underline" style="background:var(--color-primary)" x-text="t(\'landing.cta_start\')"' in html


def test_portfolio_modals_are_cloaked_before_alpine_initializes():
    landing_html = (TEMPLATES / "marketing" / "landing.html").read_text(encoding="utf-8")
    dashboard_search_html = (
        TEMPLATES / "dashboard" / "partials" / "_search_panel.html"
    ).read_text(encoding="utf-8")

    assert '<div x-show="showPortfolio" x-cloak' in landing_html
    assert '<div x-show="showPortfolio" x-cloak' in dashboard_search_html


def test_landing_feature_bundle_contains_landing_bootstrap():
    script = (STATIC / "js" / "marketing" / "landing.js").read_text(encoding="utf-8")
    assert "function landing()" in script
    assert "window.AppUpgradeModal.maybeHandle(detail, 'public_search')" in script
    assert "self.t('search.rate_limited')" in script
    assert "riskReportLoading" in script
    assert "visibleResults = this.searchResults.slice(0, 20)" in script
    assert "buildRiskReportCandidate" in script
    assert "getRiskReportLanguage" in script
    assert "applyRiskReportOrdering" in script
    assert "deterministic_score" not in script
    assert "'/api/v1/search/risk-report'" in script
    assert "'/api/v1/search/risk-report/public'" in script
    assert "risk_report_login_to_view" in script
    assert "new FormData()" in script
    assert "body.append('query_image', this.selectedImage" in script
    assert "window.AppUpgradeModal.maybeHandle(detail, 'reports')" in script
    assert "loadEducationCatalog" in script
    assert "syncEducationProgress" in script
    assert "getEducationCategoryProgress" in script
    assert "setEducationCategory" in script
    assert "setEducationMobileSection" in script
    assert "openEducationMobileSection" in script
    assert "isEducationMobileSectionActive" in script
    assert "loadEducationMobileSectionPreferences" in script
    assert "saveEducationMobileSectionPreferences" in script
    assert "scrollEducationSection" in script
    assert "getEducationQuickActionLabel" in script
    assert "getEducationCategoryTheme" in script
    assert "getEducationCategoryThemeVars" in script
    assert "normalizeCategoryId" in script
    assert "shouldShowEducationQuizExplainButton" in script
    assert "toggleEducationQuizExplanation" in script
    assert "educationQuizExplanationLoading" in script
    assert "resetEducationQuizExplanationState" in script
    assert "clearEducationQuizExplanationTimer" in script
    assert "loadEducationTesterContext" in script
    assert "educationCanModerate" in script
    assert "normalizeEducationQuizAnswers" in script
    assert "applyEducationModeration" in script
    assert "refreshEducationAfterModeration" in script
    assert "deleteEducationFlashcard" in script
    assert "setEducationFlashcardTesterCategory" in script
    assert "openEducationQuizExplanationEditor" in script
    assert "saveEducationQuizExplanationEdit" in script
    assert "resetEducationQuizExplanationEditorState" in script
    assert "isEducationQuizExplanationEditorOpen" in script
    assert "deleteEducationQuizQuestion" in script
    assert "setEducationQuizTesterCategory" in script


def test_shared_upgrade_modal_bundle_keeps_plan_handoff_logic():
    script = (STATIC / "js" / "utils" / "upgrade-modal.js").read_text(encoding="utf-8")
    template = (TEMPLATES / "shared" / "_upgrade_modal.html").read_text(encoding="utf-8")

    assert "window.AppUpgradeModal" in script
    assert "resolveOffer" in script
    assert "csv_export: { feature: 'can_export_csv_leads', kind: 'boolean' }" in script
    assert "agentic_search: { feature: 'max_daily_live_searches', kind: 'numeric' }" in script
    assert "leads: { feature: 'daily_lead_views', kind: 'numeric' }" in script
    assert "function copyForContext(context)" in script
    assert "upgrade.watchlist_title" in script
    assert "upgrade.live_search_title" in script
    assert "upgrade.leads_title" in script
    assert "Object.assign({}, FALLBACK_PLANS[planName] || {}, sourcePlan || {})" in script
    assert "FALLBACK_PLANS[planName] || FALLBACK_PLANS.free" in script
    assert "required_feature_value != null" in script
    assert "toNumber(candidateValue) >= requiredValue" in script
    assert "monthly_limit_exceeded" in script
    assert 'id="upgrade-modal-eyebrow"' in template
    assert 'id="upgrade-plan-code"' in template
    assert 'id="upgrade-feature-list"' in template

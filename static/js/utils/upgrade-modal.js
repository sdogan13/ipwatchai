window.AppUpgradeModal = window.AppUpgradeModal || (function () {
    var PLAN_ORDER = ['free', 'starter', 'professional', 'enterprise'];
    var PLAN_NAME_KEYS = {
        free: 'pricing.free_name',
        starter: 'pricing.starter_name',
        professional: 'pricing.professional_name',
        enterprise: 'pricing.enterprise_name'
    };
    var FALLBACK_PLANS = {
        free: {
            price_monthly: 0,
            monthly_live_searches: 0,
            daily_lead_views: 0,
            monthly_reports: 1,
            monthly_ai_credits: 0,
            monthly_applications: 0,
            can_track_logos: false,
            can_download_portfolio: false,
            can_export_csv_leads: false,
            can_use_live_scraping: false,
            max_users: 1,
            max_watchlist_items: 3,
            max_daily_quick_searches: 5,
            auto_scan_max_items: 0,
            priority_support: false,
            api_access: false,
            dedicated_account_manager: false
        },
        starter: {
            price_monthly: 499,
            monthly_live_searches: 10,
            daily_lead_views: 0,
            monthly_reports: 10,
            monthly_ai_credits: 10,
            monthly_applications: 1,
            can_track_logos: true,
            can_download_portfolio: true,
            can_export_csv_leads: true,
            can_use_live_scraping: true,
            max_users: 3,
            max_watchlist_items: 15,
            max_daily_quick_searches: 50,
            auto_scan_max_items: 15,
            priority_support: false,
            api_access: false,
            dedicated_account_manager: false
        },
        professional: {
            price_monthly: 1999,
            monthly_live_searches: 100,
            daily_lead_views: 10,
            monthly_reports: 30,
            monthly_ai_credits: 50,
            monthly_applications: 3,
            can_track_logos: true,
            can_download_portfolio: true,
            can_export_csv_leads: true,
            can_use_live_scraping: true,
            max_users: 10,
            max_watchlist_items: 1000,
            max_daily_quick_searches: 2000,
            auto_scan_max_items: 100,
            priority_support: true,
            api_access: false,
            dedicated_account_manager: true
        },
        enterprise: {
            price_monthly: 4999,
            monthly_live_searches: 999999,
            daily_lead_views: 999999,
            monthly_reports: 999999,
            monthly_ai_credits: 500,
            monthly_applications: 10,
            can_track_logos: true,
            can_download_portfolio: true,
            can_export_csv_leads: true,
            can_use_live_scraping: true,
            max_users: 999999,
            max_watchlist_items: 999999,
            max_daily_quick_searches: 999999,
            auto_scan_max_items: 999999,
            priority_support: true,
            api_access: true,
            dedicated_account_manager: true
        }
    };
    var CONTEXT_RULES = {
        public_search: { feature: 'max_daily_quick_searches', kind: 'numeric' },
        quick_search: { feature: 'max_daily_quick_searches', kind: 'numeric' },
        live_search: { feature: 'monthly_live_searches', kind: 'numeric' },
        watchlist_items: { feature: 'max_watchlist_items', kind: 'numeric' },
        watchlist_logo: { feature: 'can_track_logos', kind: 'boolean' },
        reports: { feature: 'monthly_reports', kind: 'numeric' },
        report_export: { feature: 'can_export_reports', kind: 'boolean' },
        applications: { feature: 'monthly_applications', kind: 'numeric' },
        ai_credits: { feature: 'monthly_ai_credits', kind: 'numeric' },
        name_suggestions: { feature: 'name_suggestions_per_session', kind: 'numeric' },
        leads: { feature: 'daily_lead_views', kind: 'numeric' },
        csv_export: { feature: 'can_export_csv_leads', kind: 'boolean' },
        auto_scan: { feature: 'auto_scan_max_items', kind: 'numeric' },
        portfolio_download: { feature: 'can_download_portfolio', kind: 'boolean' },
        api_access: { feature: 'api_access', kind: 'boolean' }
    };
    var CONTEXT_PRIORITIES = {
        public_search: ['max_daily_quick_searches', 'max_watchlist_items', 'monthly_live_searches'],
        quick_search: ['max_daily_quick_searches', 'max_watchlist_items', 'monthly_live_searches'],
        live_search: ['monthly_live_searches', 'max_daily_quick_searches', 'monthly_reports'],
        watchlist_items: ['max_watchlist_items', 'auto_scan_max_items', 'monthly_reports'],
        watchlist_logo: ['can_track_logos', 'max_watchlist_items', 'monthly_live_searches'],
        reports: ['monthly_reports', 'monthly_live_searches', 'max_daily_quick_searches'],
        report_export: ['can_export_reports', 'monthly_live_searches', 'can_download_portfolio'],
        applications: ['monthly_applications', 'monthly_ai_credits', 'can_track_logos'],
        ai_credits: ['monthly_ai_credits', 'name_suggestions_per_session', 'can_track_logos'],
        name_suggestions: ['name_suggestions_per_session', 'monthly_ai_credits', 'can_track_logos'],
        leads: ['daily_lead_views', 'can_export_csv_leads', 'can_download_portfolio'],
        csv_export: ['api_access', 'daily_lead_views', 'can_download_portfolio'],
        auto_scan: ['auto_scan_max_items', 'max_watchlist_items', 'monthly_live_searches'],
        portfolio_download: ['can_download_portfolio', 'max_watchlist_items', 'max_daily_quick_searches'],
        api_access: ['api_access', 'priority_support', 'dedicated_account_manager']
    };
    var CONTEXT_COPY = {
        public_search: {
            eyebrow: 'upgrade.search_limit_eyebrow',
            title: 'upgrade.search_limit_title',
            description: 'upgrade.search_limit_description'
        },
        quick_search: {
            eyebrow: 'upgrade.search_limit_eyebrow',
            title: 'upgrade.search_limit_title',
            description: 'upgrade.search_limit_description'
        },
        live_search: {
            eyebrow: 'upgrade.live_search_eyebrow',
            title: 'upgrade.live_search_title',
            description: 'upgrade.live_search_description'
        },
        watchlist_items: {
            eyebrow: 'upgrade.watchlist_eyebrow',
            title: 'upgrade.watchlist_title',
            description: 'upgrade.watchlist_description'
        },
        watchlist_logo: {
            eyebrow: 'upgrade.watchlist_logo_eyebrow',
            title: 'upgrade.watchlist_logo_title',
            description: 'upgrade.watchlist_logo_description'
        },
        reports: {
            eyebrow: 'upgrade.reports_eyebrow',
            title: 'upgrade.reports_title',
            description: 'upgrade.reports_description'
        },
        report_export: {
            eyebrow: 'upgrade.report_export_eyebrow',
            title: 'upgrade.report_export_title',
            description: 'upgrade.report_export_description'
        },
        applications: {
            eyebrow: 'upgrade.applications_eyebrow',
            title: 'upgrade.applications_title',
            description: 'upgrade.applications_description'
        },
        ai_credits: {
            eyebrow: 'upgrade.ai_credits_eyebrow',
            title: 'upgrade.ai_credits_title',
            description: 'upgrade.ai_credits_description'
        },
        name_suggestions: {
            eyebrow: 'upgrade.ai_credits_eyebrow',
            title: 'upgrade.ai_credits_title',
            description: 'upgrade.ai_credits_description'
        },
        leads: {
            eyebrow: 'upgrade.leads_eyebrow',
            title: 'upgrade.leads_title',
            description: 'upgrade.leads_description'
        },
        csv_export: {
            eyebrow: 'upgrade.csv_export_eyebrow',
            title: 'upgrade.csv_export_title',
            description: 'upgrade.csv_export_description'
        },
        auto_scan: {
            eyebrow: 'upgrade.auto_scan_eyebrow',
            title: 'upgrade.auto_scan_title',
            description: 'upgrade.auto_scan_description'
        },
        portfolio_download: {
            eyebrow: 'upgrade.portfolio_download_eyebrow',
            title: 'upgrade.portfolio_download_title',
            description: 'upgrade.portfolio_download_description'
        },
        api_access: {
            eyebrow: 'upgrade.api_access_eyebrow',
            title: 'upgrade.api_access_title',
            description: 'upgrade.api_access_description'
        }
    };
    var FALLBACK_CONTEXT = 'quick_search';

    function canonicalPlanName(plan) {
        var normalized = String(plan || 'free').trim().toLowerCase();
        if (normalized === 'business') return 'professional';
        if (normalized === 'superadmin') return 'enterprise';
        return PLAN_ORDER.indexOf(normalized) >= 0 ? normalized : 'free';
    }

    function plans() {
        var source = window.SERVER_PLANS;
        var merged = {};

        PLAN_ORDER.forEach(function (planName) {
            var sourcePlan = source && typeof source === 'object' ? source[planName] : null;
            merged[planName] = Object.assign({}, FALLBACK_PLANS[planName] || {}, sourcePlan || {});
        });

        return merged;
    }

    function getPlan(plan) {
        var allPlans = plans();
        var planName = canonicalPlanName(plan);
        return allPlans[planName] || FALLBACK_PLANS[planName] || FALLBACK_PLANS.free;
    }

    function getFeatureValue(planName, featureKey) {
        var plan = getPlan(planName);
        switch (featureKey) {
            case 'can_export_reports':
                return canonicalPlanName(planName) !== 'free' && toNumber(plan.monthly_reports) > 0;
            case 'can_export_csv_leads':
                if (typeof plan.can_export_csv_leads !== 'undefined') return !!plan.can_export_csv_leads;
                return canonicalPlanName(planName) === 'enterprise';
            case 'name_suggestions_per_session':
                if (plan.name_suggestions_per_session != null) return plan.name_suggestions_per_session;
                return plan.monthly_ai_credits;
            default:
                return plan[featureKey];
        }
    }

    function toNumber(value) {
        var numeric = Number(value);
        return isNaN(numeric) ? 0 : numeric;
    }

    function isUnlimited(value) {
        return toNumber(value) >= 999999;
    }

    function t(key, params) {
        if (window.AppI18n && typeof window.AppI18n.t === 'function') {
            return window.AppI18n.t(key, params);
        }
        return key;
    }

    function formatPrice(amount) {
        var locale = (window.AppI18n && window.AppI18n._locale) || document.documentElement.lang || 'tr';
        return new Intl.NumberFormat(locale, {
            style: 'currency',
            currency: 'TRY',
            maximumFractionDigits: 0
        }).format(toNumber(amount || 0));
    }

    function nextPlan(plan) {
        var current = canonicalPlanName(plan);
        var index = PLAN_ORDER.indexOf(current);
        if (index < 0 || index === PLAN_ORDER.length - 1) return 'enterprise';
        return PLAN_ORDER[index + 1];
    }

    function extractDetail(detail) {
        if (!detail) return {};
        if (detail.data && detail.data.detail) {
            return typeof detail.data.detail === 'object' ? detail.data.detail : { message: String(detail.data.detail) };
        }
        if (detail.detail) {
            return typeof detail.detail === 'object' ? detail.detail : { message: String(detail.detail) };
        }
        if (typeof detail === 'string') return { message: detail };
        if (typeof detail === 'object') return detail;
        return {};
    }

    function mergeDetail(detail, fallbackContext) {
        var normalized = extractDetail(detail);
        if (!normalized.upgrade_context && fallbackContext) normalized.upgrade_context = fallbackContext;
        if (!normalized.upgrade_context && normalized.error === 'credits_exhausted') {
            normalized.upgrade_context = 'ai_credits';
        }
        if (!normalized.current_plan && window.AppAuth && window.AppAuth.currentUserPlan) {
            normalized.current_plan = window.AppAuth.currentUserPlan;
        }
        normalized.current_plan = canonicalPlanName(normalized.current_plan || 'free');
        return normalized;
    }

    function requiredFeatureValue(detail, featureKey) {
        if (!detail || !featureKey) return null;

        if (detail.required_feature && detail.required_feature !== featureKey) {
            return null;
        }

        var rawValue = null;
        if (detail.required_feature_value != null) rawValue = detail.required_feature_value;
        else if (detail.required_value != null) rawValue = detail.required_value;
        else if (detail.required_total != null) rawValue = detail.required_total;

        if (rawValue == null) return null;

        var numeric = toNumber(rawValue);
        return numeric > 0 ? numeric : null;
    }

    function planMeetsRequirement(planName, rule, requiredValue) {
        if (!rule || requiredValue == null) return true;
        var candidateValue = getFeatureValue(planName, rule.feature);
        if (rule.kind === 'boolean') return !!candidateValue;
        return isUnlimited(candidateValue) || toNumber(candidateValue) >= toNumber(requiredValue);
    }

    function recommendedPlanFor(detail, fallbackContext) {
        var normalized = mergeDetail(detail, fallbackContext);

        var context = normalized.upgrade_context || FALLBACK_CONTEXT;
        var rule = CONTEXT_RULES[context];
        var currentPlan = normalized.current_plan;
        var requiredValue = rule && rule.kind === 'numeric'
            ? requiredFeatureValue(normalized, rule.feature)
            : null;

        if (normalized.recommended_plan && getPlan(normalized.recommended_plan)) {
            var explicitPlan = canonicalPlanName(normalized.recommended_plan);
            if (planMeetsRequirement(explicitPlan, rule, requiredValue)) {
                return explicitPlan;
            }
        }

        if (!rule) return nextPlan(currentPlan);

        if (rule.allowedPlans && rule.allowedPlans.length) {
            for (var allowedIndex = 0; allowedIndex < rule.allowedPlans.length; allowedIndex += 1) {
                var allowedPlan = canonicalPlanName(rule.allowedPlans[allowedIndex]);
                if (PLAN_ORDER.indexOf(allowedPlan) > PLAN_ORDER.indexOf(currentPlan)) {
                    return allowedPlan;
                }
            }
            return rule.allowedPlans[rule.allowedPlans.length - 1];
        }

        var currentValue = getFeatureValue(currentPlan, rule.feature);
        for (var index = PLAN_ORDER.indexOf(currentPlan) + 1; index < PLAN_ORDER.length; index += 1) {
            var candidatePlan = PLAN_ORDER[index];
            var candidateValue = getFeatureValue(candidatePlan, rule.feature);
            if (rule.kind === 'boolean') {
                if (!!candidateValue && !currentValue) return candidatePlan;
            } else if (requiredValue != null) {
                if (isUnlimited(candidateValue) || toNumber(candidateValue) >= requiredValue) {
                    return candidatePlan;
                }
            } else if (isUnlimited(candidateValue) || toNumber(candidateValue) > toNumber(currentValue)) {
                return candidatePlan;
            }
        }

        return nextPlan(currentPlan);
    }

    function featureLabel(planName, featureKey) {
        var plan = getPlan(planName);
        switch (featureKey) {
            case 'max_daily_quick_searches':
                return isUnlimited(plan.max_daily_quick_searches)
                    ? t('pricing.f_unlimited_searches')
                    : t('pricing.f_daily_searches', { n: plan.max_daily_quick_searches });
            case 'max_watchlist_items':
                return isUnlimited(plan.max_watchlist_items)
                    ? t('pricing.f_unlimited_watchlist')
                    : t('pricing.f_watchlist', { n: plan.max_watchlist_items });
            case 'monthly_live_searches':
                return isUnlimited(plan.monthly_live_searches)
                    ? t('pricing.f_unlimited_live')
                    : t('pricing.f_live_monthly', { n: plan.monthly_live_searches });
            case 'monthly_reports':
                return isUnlimited(plan.monthly_reports)
                    ? t('pricing.f_unlimited_reports')
                    : t('pricing.f_reports', { n: plan.monthly_reports });
            case 'can_export_reports':
                return t('upgrade.report_export_eyebrow');
            case 'monthly_applications':
                return isUnlimited(plan.monthly_applications)
                    ? t('pricing.f_unlimited_applications')
                    : t('pricing.f_applications', { n: plan.monthly_applications });
            case 'monthly_ai_credits':
                return isUnlimited(plan.monthly_ai_credits)
                    ? t('pricing.f_unlimited_ai_credits')
                    : t('pricing.f_ai_credits', { n: plan.monthly_ai_credits });
            case 'name_suggestions_per_session':
                return isUnlimited(plan.name_suggestions_per_session)
                    ? t('pricing.f_unlimited_suggestions')
                    : t('pricing.f_name_suggestions', { n: plan.name_suggestions_per_session });
            case 'daily_lead_views':
                return isUnlimited(plan.daily_lead_views)
                    ? t('pricing.f_unlimited_leads')
                    : t('pricing.f_leads_daily', { n: plan.daily_lead_views });
            case 'can_track_logos':
                return t('pricing.f_logo_tracking');
            case 'can_download_portfolio':
                return t('pricing.f_download_portfolio');
            case 'can_export_csv_leads':
                return t('pricing.f_csv_export');
            case 'auto_scan_max_items':
                return t('pricing.f_auto_scan');
            case 'priority_support':
                return t('pricing.f_priority_support');
            case 'dedicated_account_manager':
                return t('pricing.f_dedicated_manager');
            case 'api_access':
                return t('pricing.f_api_access');
            default:
                return '';
        }
    }

    function highlightsFor(planName, context) {
        var order = CONTEXT_PRIORITIES[context] || CONTEXT_PRIORITIES.quick_search;
        var unique = [];
        for (var index = 0; index < order.length; index += 1) {
            var label = featureLabel(planName, order[index]);
            if (label && unique.indexOf(label) === -1) unique.push(label);
            if (unique.length >= 3) break;
        }
        if (context === 'portfolio_download') return unique;
        if (unique.length < 3) {
            ['max_daily_quick_searches', 'max_watchlist_items', 'monthly_live_searches'].forEach(function (featureKey) {
                var label = featureLabel(planName, featureKey);
                if (label && unique.indexOf(label) === -1 && unique.length < 3) unique.push(label);
            });
        }
        return unique;
    }

    function copyForContext(context) {
        var copy = CONTEXT_COPY[context] || {};
        return {
            eyebrow: t(copy.eyebrow || 'upgrade.eyebrow'),
            title: t(copy.title || 'upgrade.generic_title'),
            description: t(copy.description || 'upgrade.generic_description')
        };
    }

    function resolveOffer(detail, fallbackContext) {
        var normalized = mergeDetail(detail, fallbackContext);
        var recommendedPlan = recommendedPlanFor(normalized, fallbackContext);
        var context = normalized.upgrade_context || fallbackContext || FALLBACK_CONTEXT;
        var copy = copyForContext(context);
        return {
            detail: normalized,
            context: context,
            currentPlan: normalized.current_plan,
            recommendedPlan: recommendedPlan,
            planKey: PLAN_NAME_KEYS[recommendedPlan] || PLAN_NAME_KEYS.starter,
            planName: t(PLAN_NAME_KEYS[recommendedPlan] || PLAN_NAME_KEYS.starter),
            priceMonthly: toNumber(getPlan(recommendedPlan).price_monthly),
            priceLabel: formatPrice(getPlan(recommendedPlan).price_monthly),
            perMonthLabel: '/' + t('pricing.per_month'),
            features: highlightsFor(recommendedPlan, context),
            checkoutUrl: '/checkout?plan=' + encodeURIComponent(recommendedPlan) + '&billing=monthly',
            eyebrow: copy.eyebrow,
            title: copy.title,
            description: copy.description,
            recommendedBadge: t('upgrade.recommended_badge'),
            includesLabel: t('checkout.includes')
        };
    }

    function render(detail, fallbackContext) {
        var modal = document.getElementById('upgrade-modal');
        if (!modal) return null;

        var offer = resolveOffer(detail, fallbackContext);
        modal.dataset.upgradeUrl = offer.checkoutUrl;
        modal.dataset.recommendedPlan = offer.recommendedPlan;
        modal.dataset.upgradeContext = offer.context;

        var eyebrowEl = document.getElementById('upgrade-modal-eyebrow');
        var titleEl = document.getElementById('upgrade-modal-title');
        var descEl = document.getElementById('upgrade-modal-description');
        var nameEl = document.getElementById('upgrade-plan-name');
        var badgeEl = document.getElementById('upgrade-plan-badge');
        var priceEl = document.getElementById('upgrade-plan-price');
        var periodEl = document.getElementById('upgrade-plan-period');
        var codeEl = document.getElementById('upgrade-plan-code');
        var featureListEl = document.getElementById('upgrade-feature-list');

        if (eyebrowEl) eyebrowEl.textContent = offer.eyebrow;
        if (titleEl) titleEl.textContent = offer.title;
        if (descEl) descEl.textContent = offer.description;
        if (nameEl) nameEl.textContent = offer.planName;
        if (badgeEl) badgeEl.textContent = offer.recommendedBadge;
        if (priceEl) priceEl.textContent = offer.priceLabel;
        if (periodEl) periodEl.textContent = offer.perMonthLabel;
        if (codeEl) codeEl.textContent = offer.recommendedPlan;
        if (featureListEl) {
            featureListEl.innerHTML = offer.features.map(function (feature) {
                return '<li class="flex items-center gap-2">'
                    + '<span class="text-green-400">&#x2713;</span>'
                    + '<span>' + feature + '</span>'
                    + '</li>';
            }).join('');
        }

        return offer;
    }

    function show(detail, fallbackContext) {
        var modal = document.getElementById('upgrade-modal');
        if (!modal) return null;
        var offer = render(detail, fallbackContext);
        modal.style.zIndex = 'calc(var(--z-modal) + 20)';
        modal.classList.remove('hidden');
        if (typeof lockBodyScroll === 'function') lockBodyScroll();
        return offer;
    }

    function hide() {
        var modal = document.getElementById('upgrade-modal');
        if (!modal) return;
        modal.classList.add('hidden');
        if (typeof unlockBodyScroll === 'function') unlockBodyScroll();
    }

    function redirect() {
        var modal = document.getElementById('upgrade-modal');
        var target = (modal && modal.dataset.upgradeUrl) || '/pricing';
        hide();
        window.location.href = target;
    }

    function shouldHandle(detail, fallbackContext) {
        var normalized = mergeDetail(detail, fallbackContext);
        if (normalized.recommended_plan) return true;
        return ['upgrade_required', 'limit_exceeded', 'daily_limit_exceeded', 'monthly_limit_exceeded', 'credits_exhausted'].indexOf(normalized.error || '') >= 0;
    }

    function maybeHandle(detail, fallbackContext) {
        if (!shouldHandle(detail, fallbackContext)) return false;
        show(detail, fallbackContext);
        return true;
    }

    return {
        canonicalPlanName: canonicalPlanName,
        resolveOffer: resolveOffer,
        render: render,
        show: show,
        hide: hide,
        redirect: redirect,
        maybeHandle: maybeHandle
    };
}());

function showUpgradeModal(detail, fallbackContext) {
    if (window.AppUpgradeModal && typeof window.AppUpgradeModal.show === 'function') {
        return window.AppUpgradeModal.show(detail, fallbackContext);
    }
    return null;
}

function hideUpgradeModal() {
    if (window.AppUpgradeModal && typeof window.AppUpgradeModal.hide === 'function') {
        window.AppUpgradeModal.hide();
    }
}

function redirectToUpgrade() {
    if (window.AppUpgradeModal && typeof window.AppUpgradeModal.redirect === 'function') {
        window.AppUpgradeModal.redirect();
    }
}

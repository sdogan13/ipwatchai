/**
 * auth.js - Authentication helpers + usage tracking
 */
window.AppAuth = window.AppAuth || {};

window.AppAuth.currentUserPlan = 'free';
window.AppAuth.currentUserRole = '';
window.AppAuth.currentUserName = '';
window.AppAuth.currentUserIsSuperadmin = false;
window.AppAuth.usage = null; // populated by fetchUsageSummary

window.AppAuth.getAuthToken = function() {
    return localStorage.getItem('auth_token') || sessionStorage.getItem('auth_token') || '';
};

window.AppAuth.fetchUserPlan = function() {
    var token = window.AppAuth.getAuthToken();
    if (!token) return;
    fetch('/api/v1/auth/me', {
        headers: { 'Authorization': 'Bearer ' + token }
    }).then(function(res) {
        if (res.ok) return res.json();
        return null;
    }).then(function(profile) {
        if (profile) {
            window.AppAuth.currentUserPlan = (profile.organization && profile.organization.plan) || 'free';
            currentUserPlan = window.AppAuth.currentUserPlan;
            window.AppAuth.currentUserRole = profile.role || '';
            currentUserRole = window.AppAuth.currentUserRole;
            window.AppAuth.currentUserName = profile.first_name || profile.email || 'User';
            window.AppAuth.currentUserIsSuperadmin = !!profile.is_superadmin;
            // Initialize admin-only features
            if (currentUserRole === 'admin' || currentUserRole === 'owner') {
                initPipelineStatus();
            }
            // Load portfolio for all authenticated users
            if (typeof loadPortfolio === 'function') loadPortfolio();
            // Fetch usage summary and update UI badges
            window.AppAuth.fetchUsageSummary();
        }
    }).catch(function(e) { /* silent */ });
};

window.AppAuth.fetchUsageSummary = function() {
    var token = window.AppAuth.getAuthToken();
    if (!token) return;
    fetch('/api/v1/usage/summary', {
        headers: { 'Authorization': 'Bearer ' + token }
    }).then(function(res) {
        if (res.ok) return res.json();
        return null;
    }).then(function(data) {
        if (data) {
            window.AppAuth.usage = data.usage;
            window.AppAuth.updatePlanBadges();
        }
    }).catch(function(e) { /* silent */ });
};

/**
 * Update UI badges based on plan and remaining credits.
 * Called after usage summary is fetched.
 */
window.AppAuth.updatePlanBadges = function() {
    var plan = window.AppAuth.currentUserPlan;
    var usage = window.AppAuth.usage;
    if (!usage) return;

    // Search bar: show remaining daily quick searches
    var searchBadge = document.getElementById('quick-search-credits-badge');
    if (searchBadge && usage.daily_quick_searches) {
        var qs = usage.daily_quick_searches;
        var remaining = qs.limit - qs.used;
        searchBadge.textContent = remaining + '/' + qs.limit;
        searchBadge.classList.remove('hidden');
        if (remaining <= 5 && remaining > 0) {
            searchBadge.className = searchBadge.className.replace('text-gray-500', 'text-amber-600 font-bold');
        } else if (remaining <= 0) {
            searchBadge.className = searchBadge.className.replace('text-gray-500', 'text-red-600 font-bold');
        }
    }

    // Live search badge: show monthly remaining
    var liveBadge = document.getElementById('live-search-credits-badge');
    if (liveBadge && usage.monthly_live_searches) {
        var ls = usage.monthly_live_searches;
        if (ls.limit > 0) {
            liveBadge.textContent = (ls.limit - ls.used) + '/' + ls.limit;
            liveBadge.classList.remove('hidden');
        }
    }

    // Holder portfolio: show PRO badge for non-pro users
    var holderBadges = document.querySelectorAll('.holder-pro-badge');
    if (plan === 'free' || plan === 'starter') {
        holderBadges.forEach(function(el) { el.classList.remove('hidden'); });
    }

    // CSV export: show Enterprise badge for non-enterprise
    var csvBadge = document.getElementById('csv-enterprise-badge');
    if (csvBadge && plan !== 'enterprise') {
        csvBadge.classList.remove('hidden');
    }
};

// Expose as globals for inline onclick handlers
var getAuthToken = window.AppAuth.getAuthToken;
var currentUserPlan = window.AppAuth.currentUserPlan;
var currentUserRole = window.AppAuth.currentUserRole;

// Auto-fetch plan on load
window.AppAuth.fetchUserPlan();

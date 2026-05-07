/**
 * api.js - All fetch/API calls
 */
window.AppAPI = window.AppAPI || {};

async function _readApiErrorData(response) {
    return await response.json().catch(function () { return {}; });
}

function _apiErrorMessage(data, fallbackMessage) {
    if (data && data.detail) {
        if (typeof data.detail === 'object' && data.detail.message) return data.detail.message;
        return data.detail;
    }
    if (data && data.message) return data.message;
    return fallbackMessage;
}

function _buildApiError(response, data, fallbackMessage) {
    var err = new Error(_apiErrorMessage(data, fallbackMessage));
    err.status = response.status;
    err.data = data || {};
    return err;
}

// ============================================
// QUICK (DB-ONLY) SEARCH
// ============================================
window.AppAPI.handleQuickSearch = async function () {
    var input = document.getElementById('search-input');
    var query = (input && input.value || '').trim();
    if (!query) { showToast(t('search.enter_brand_name'), 'error'); return; }

    var classes = getSelectedNiceClasses();
    var attorneyEl = document.getElementById('attorney-filter-value');
    var attorneyVal = attorneyEl ? attorneyEl.value : '';
    var url = '/api/v1/search/quick?query=' + encodeURIComponent(query);
    if (classes.length) url += '&classes=' + classes.join(',');
    if (attorneyVal) url += '&attorney_no=' + encodeURIComponent(attorneyVal);

    try {
        var res = await fetch(url, {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (res.status === 401) { showToast(t('auth.session_expired'), 'error'); return; }
        if (res.status === 429) {
            var errData = await _readApiErrorData(res);
            var detail = errData.detail || errData;
            if (window.AppUpgradeModal && typeof window.AppUpgradeModal.maybeHandle === 'function'
                && window.AppUpgradeModal.maybeHandle(detail, 'quick_search')) {
                return;
            }
            showToast(t('search.rate_limited'), 'warning');
            return;
        }
        if (!res.ok) throw new Error(t('search.search_failed'));
        var data = await res.json();
        currentSearchType = 'quick';
        displayAgenticResults(data);
        showToast(t('search.results_found_db', { count: Math.min(data.total || 0, 30) }), 'success');
    } catch (e) {
        console.error('Quick search error:', e);
        showToast(t('common.error') + ': ' + e.message, 'error');
    }
};

// ============================================
// AGENTIC (LIVE) SEARCH
// ============================================
window.AppAPI.handleAgenticSearch = async function () {
    var input = document.getElementById('search-input');
    var query = (input && input.value || '').trim();
    if (!query) { showToast(t('search.enter_brand_name'), 'error'); return; }

    var classes = getSelectedNiceClasses();
    var attorneyEl = document.getElementById('attorney-filter-value');
    var attorneyVal = attorneyEl ? attorneyEl.value : '';
    var imageInput = document.getElementById('search-image');
    var imageFile = imageInput && imageInput.files && imageInput.files[0];

    agenticSearchAborted = false;
    _agenticAbortController = new AbortController();
    showAgenticLoadingModal();

    var signal = _agenticAbortController ? _agenticAbortController.signal : undefined;

    try {
        var res;
        if (imageFile) {
            // POST with FormData (multipart) when image is provided
            var formData = new FormData();
            formData.append('query', query);
            formData.append('image', imageFile);
            if (classes.length) formData.append('classes', classes.join(','));
            if (attorneyVal) formData.append('attorney_no', attorneyVal);

            res = await fetch('/api/v1/search/intelligent', {
                method: 'POST',
                headers: { 'Authorization': 'Bearer ' + getAuthToken() },
                body: formData,
                signal: signal
            });
        } else {
            // GET without image (backward compatible)
            var url = '/api/v1/search/intelligent?query=' + encodeURIComponent(query);
            if (classes.length) url += '&classes=' + classes.join(',');
            if (attorneyVal) url += '&attorney_no=' + encodeURIComponent(attorneyVal);

            res = await fetch(url, {
                headers: { 'Authorization': 'Bearer ' + getAuthToken() },
                signal: signal
            });
        }

        if (agenticSearchAborted) return;
        var data = await res.json();

        if (res.status === 403) { hideAgenticLoadingModal(); showUpgradeModal(data.detail || data, 'live_search'); return; }
        if (res.status === 402) { hideAgenticLoadingModal(); showUpgradeModal(data.detail || data, 'live_search'); return; }
        if (res.status === 401) { hideAgenticLoadingModal(); showToast(t('auth.session_expired'), 'error'); return; }
        if (!res.ok) throw new Error(data.detail?.message || data.detail || t('search.search_failed'));

        hideAgenticLoadingModal();
        currentSearchType = 'intelligent';
        displayAgenticResults(data);

        var creditsMsg = data.scrape_triggered
            ? t('search.credits_remaining', { count: data.credits_remaining })
            : t('search.from_database');
        var resultMsg = data.image_used
            ? t('search.results_found_image', { count: Math.min(data.total || 0, 30), credits: creditsMsg })
            : t('search.results_found', { count: Math.min(data.total || 0, 30) }) + '. ' + creditsMsg;
        showToast(resultMsg, 'success');

    } catch (e) {
        if (!agenticSearchAborted) {
            hideAgenticLoadingModal();
            console.error('Agentic search error:', e);
            showToast(t('common.error') + ': ' + e.message, 'error');
        }
    }
};

// ============================================
// OPPOSITION RADAR - LEADS
// ============================================
window.AppAPI.loadLeadStats = async function () {
    try {
        var response = await fetch('/api/v1/leads/stats', {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });

        if (response.status === 403) {
            var denied = await _readApiErrorData(response);
            showLeadUpgradePrompt(denied.detail || denied);
            return;
        }
        if (!response.ok) return;

        var stats = await response.json();
        document.getElementById('stat-critical').textContent = stats.critical_leads || 0;
        document.getElementById('stat-urgent').textContent = stats.urgent_leads || 0;
        document.getElementById('stat-total').textContent = stats.total_leads || 0;
        document.getElementById('stat-converted').textContent = stats.converted_leads || 0;
        var upcomingEl = document.getElementById('stat-upcoming');
        if (upcomingEl) upcomingEl.textContent = stats.upcoming_leads || 0;

        // Render urgency summary bar
        renderUrgencySummary(stats);

        // A3: Workflow stage segments
        var workflowEl = document.getElementById('lead-workflow-stats');
        if (workflowEl) {
            var newL = stats.new_leads || 0;
            var viewedL = stats.viewed_leads || 0;
            var contactedL = stats.contacted_leads || 0;
            if (newL + viewedL + contactedL > 0) {
                workflowEl.innerHTML = '<div class="flex items-center gap-3 text-xs" style="color:var(--color-text-faint)">'
                    + '<span>' + t('leads.stat_new') + ': <strong style="color:var(--color-text-secondary)">' + newL + '</strong></span>'
                    + '<span style="color:var(--color-border)">&bull;</span>'
                    + '<span>' + t('leads.stat_viewed') + ': <strong style="color:var(--color-text-secondary)">' + viewedL + '</strong></span>'
                    + '<span style="color:var(--color-border)">&bull;</span>'
                    + '<span>' + t('leads.stat_contacted') + ': <strong style="color:var(--color-text-secondary)">' + contactedL + '</strong></span>'
                    + '</div>';
            }
        }

        // A4: Last scan timestamp
        var lastScanEl = document.getElementById('lead-last-scan');
        if (lastScanEl && stats.last_scan_at) {
            lastScanEl.innerHTML = '<span class="text-xs" style="color:var(--color-text-faint)">' + t('leads.last_scan') + ': ' + timeAgo(stats.last_scan_at) + '</span>';
        }

        // Avg similarity indicator (textual category, no percentage)
        var avgSimEl = document.getElementById('avg-similarity-indicator');
        if (avgSimEl && stats.avg_similarity !== null && stats.avg_similarity !== undefined) {
            var pct = Math.round(stats.avg_similarity * 100);
            var avgCategory = window.AppComponents.getSimilarityCategory(pct);
            avgSimEl.innerHTML = '<span class="text-xs" style="color:var(--color-text-muted)">' + t('leads.avg_similarity') + ': <strong style="color:var(--color-text-primary)">' + t('risk_level.' + avgCategory) + '</strong></span>';
            avgSimEl.classList.remove('hidden');
        }
    } catch (error) {
        console.error('Failed to load lead stats:', error);
    }
};

window.AppAPI.loadLeadCredits = async function () {
    try {
        var response = await fetch('/api/v1/leads/credits', {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (!response.ok) return;

        var credits = await response.json();
        var remaining = credits.remaining === 'unlimited' ? '\u221E' : credits.remaining;
        document.getElementById('lead-credits-remaining').textContent = remaining;

        if (credits.plan === 'enterprise') {
            document.getElementById('export-leads-btn').classList.remove('hidden');
        }

        // A2: Daily view limit indicator
        var dailyLimit = credits.daily_limit;
        var usedToday = credits.used_today;
        var dailyEl = document.getElementById('lead-daily-usage');
        if (dailyEl && dailyLimit != null && usedToday != null) {
            var pct = Math.min(100, Math.round((usedToday / dailyLimit) * 100));
            var barColor = pct >= 80 ? 'var(--color-risk-critical-text)' : pct >= 50 ? 'var(--color-risk-medium-text)' : 'var(--color-risk-low-text)';
            dailyEl.innerHTML = '<div class="flex items-center gap-2 text-xs" style="color:var(--color-text-faint)">'
                + '<div class="w-16 h-1.5 rounded-full" style="background:var(--color-border)">'
                + '<div class="h-full rounded-full" style="width:' + pct + '%;background:' + barColor + '"></div>'
                + '</div>'
                + '<span>' + usedToday + '/' + dailyLimit + ' ' + t('leads.views_today') + '</span>'
                + '</div>';
        }
    } catch (error) {
        console.error('Failed to load lead credits:', error);
    }
};

window.AppAPI.loadLeadFeed = async function (page) {
    if (page === undefined) page = 1;
    currentLeadPage = page;

    var container = document.getElementById('lead-feed-list');
    var tableWrapper = document.getElementById('lead-feed-table-wrapper');
    var loading = document.getElementById('lead-feed-loading');
    var empty = document.getElementById('lead-feed-empty');
    var pagination = document.getElementById('lead-pagination');

    loading.classList.remove('hidden');
    container.innerHTML = '';
    empty.classList.add('hidden');
    if (tableWrapper) tableWrapper.classList.add('hidden');

    var urgency = document.getElementById('filter-urgency').value;
    var risk = document.getElementById('filter-risk').value;
    var niceClass = document.getElementById('filter-nice-class').value;
    var status = document.getElementById('filter-status').value;
    var searchEl = document.getElementById('filter-lead-search');
    var search = searchEl ? searchEl.value.trim() : '';

    var url = '/api/v1/leads/feed?page=' + page + '&limit=' + LEADS_PER_PAGE;
    if (urgency) url += '&urgency=' + urgency;
    if (risk) url += '&min_score=' + risk;
    if (niceClass) url += '&nice_class=' + niceClass;
    if (status) url += '&status=' + status;
    if (search) url += '&search=' + encodeURIComponent(search);

    try {
        var response = await fetch(url, {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });

        loading.classList.add('hidden');

        if (response.status === 403) {
            var denied = await _readApiErrorData(response);
            showLeadUpgradePrompt(denied.detail || denied);
            return;
        }
        if (response.status === 429) {
            var throttled = await _readApiErrorData(response);
            showUpgradeModal(throttled.detail || throttled, 'leads');
            return;
        }
        if (!response.ok) throw new Error('Failed to load leads');

        var data = await response.json();
        var leads = data.items || data;
        var totalCount = data.total_count != null ? data.total_count : leads.length;

        if (leads.length === 0) {
            empty.classList.remove('hidden');
            pagination.classList.add('hidden');
            return;
        }

        // Render as table rows (desktop) + mobile cards
        container.innerHTML = leads.map(renderLeadRow).join('');
        var mobileContainer = document.getElementById('lead-feed-mobile');
        if (mobileContainer) mobileContainer.innerHTML = leads.map(renderLeadMobileCard).join('');
        if (tableWrapper) tableWrapper.classList.remove('hidden');

        // Pagination with total count
        var totalPages = Math.ceil(totalCount / LEADS_PER_PAGE);
        pagination.classList.remove('hidden');

        var totalInfoEl = document.getElementById('lead-total-info');
        if (totalInfoEl) totalInfoEl.textContent = t('leads.total_results', { count: totalCount });

        document.getElementById('lead-page-info').textContent = t('leads.page_of', { current: page, total: totalPages });
        document.getElementById('lead-prev-btn').disabled = page === 1;
        document.getElementById('lead-next-btn').disabled = page >= totalPages;

    } catch (error) {
        loading.classList.add('hidden');
        console.error('Failed to load leads:', error);
        showToast(t('leads.load_error'), 'error');
    }
};

// ============================================
// RENEWAL LEADS
// ============================================

var currentRenewalPage = 1;
var currentRadarMode = 'conflicts';

window.AppAPI.switchRadarMode = function (mode) {
    currentRadarMode = mode;
    var conflictsBtn = document.getElementById('radar-mode-conflicts');
    var renewalsBtn = document.getElementById('radar-mode-renewals');
    var conflictsSection = document.getElementById('radar-conflicts-section');
    var renewalsSection = document.getElementById('radar-renewals-section');

    if (mode === 'renewals') {
        conflictsBtn.style.background = 'transparent';
        conflictsBtn.style.color = 'var(--color-text-secondary)';
        renewalsBtn.style.background = 'var(--color-primary)';
        renewalsBtn.style.color = 'white';
        conflictsSection.classList.add('hidden');
        renewalsSection.classList.remove('hidden');
        loadRenewalStats();
        loadRenewalFeed(1);
    } else {
        renewalsBtn.style.background = 'transparent';
        renewalsBtn.style.color = 'var(--color-text-secondary)';
        conflictsBtn.style.background = 'var(--color-primary)';
        conflictsBtn.style.color = 'white';
        renewalsSection.classList.add('hidden');
        conflictsSection.classList.remove('hidden');
    }
};

window.AppAPI.loadRenewalStats = async function () {
    try {
        var response = await fetch('/api/v1/leads/renewals/stats', {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (!response.ok) return;

        var stats = await response.json();
        var graceEl = document.getElementById('renewal-stat-grace');
        var criticalEl = document.getElementById('renewal-stat-critical');
        var urgentEl = document.getElementById('renewal-stat-urgent');
        var totalEl = document.getElementById('renewal-stat-total');

        if (graceEl) graceEl.textContent = stats.grace_period || 0;
        if (criticalEl) criticalEl.textContent = stats.critical || 0;
        if (urgentEl) urgentEl.textContent = stats.urgent || 0;
        if (totalEl) totalEl.textContent = stats.total || 0;
    } catch (error) {
        console.error('Failed to load renewal stats:', error);
    }
};

window.AppAPI.loadRenewalFeed = async function (page) {
    if (page === undefined) page = 1;
    currentRenewalPage = page;

    var container = document.getElementById('renewal-feed-cards');
    var loading = document.getElementById('renewal-feed-loading');
    var empty = document.getElementById('renewal-feed-empty');
    var pagination = document.getElementById('renewal-pagination');

    loading.classList.remove('hidden');
    container.innerHTML = '';
    container.classList.add('hidden');
    empty.classList.add('hidden');

    var urgency = document.getElementById('filter-renewal-urgency').value;
    var niceClass = document.getElementById('filter-renewal-nice-class').value;
    var searchEl = document.getElementById('filter-renewal-search');
    var search = searchEl ? searchEl.value.trim() : '';

    var url = '/api/v1/leads/renewals/feed?page=' + page + '&limit=' + LEADS_PER_PAGE;
    if (urgency) url += '&urgency=' + urgency;
    if (niceClass) url += '&nice_class=' + niceClass;
    if (search) url += '&search=' + encodeURIComponent(search);

    try {
        var response = await fetch(url, {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });

        loading.classList.add('hidden');

        if (response.status === 403) {
            var denied = await _readApiErrorData(response);
            showLeadUpgradePrompt(denied.detail || denied);
            return;
        }
        if (!response.ok) throw new Error('Failed to load renewals');

        var data = await response.json();
        var items = data.items || [];
        var totalCount = data.total_count != null ? data.total_count : items.length;

        if (items.length === 0) {
            empty.classList.remove('hidden');
            pagination.classList.add('hidden');
            return;
        }

        container.innerHTML = items.map(renderRenewalCard).join('');
        container.classList.remove('hidden');

        var totalPages = Math.ceil(totalCount / LEADS_PER_PAGE);
        pagination.classList.remove('hidden');

        var totalInfoEl = document.getElementById('renewal-total-info');
        if (totalInfoEl) totalInfoEl.textContent = t('leads.total_results', { count: totalCount });
        document.getElementById('renewal-page-info').textContent = t('leads.page_of', { current: page, total: totalPages });
        document.getElementById('renewal-prev-btn').disabled = page === 1;
        document.getElementById('renewal-next-btn').disabled = page >= totalPages;

    } catch (error) {
        loading.classList.add('hidden');
        console.error('Failed to load renewals:', error);
        showToast(t('leads.load_error'), 'error');
    }
};

function renderRenewalCard(item) {
    var urgencyMap = {
        'grace_period': { riskLevel: 'critical', label: t('leads.renewal_grace_short') || 'EK SÜRE' },
        'critical':     { riskLevel: 'high',     label: t('leads.renewal_critical_short') || 'KRİTİK' },
        'urgent':       { riskLevel: 'medium',   label: t('leads.renewal_urgent_short') || 'ACİL' },
        'upcoming':     { riskLevel: 'low',      label: t('leads.renewal_upcoming_short') || 'YAKLAŞAN' }
    };
    var um = urgencyMap[item.urgency_level] || { riskLevel: 'low', label: '-' };

    // Urgency badge (top-right, replaces score ring)
    var urgencyColor = window.AppComponents.getScoreColor(
        um.riskLevel === 'critical' ? 95 : um.riskLevel === 'high' ? 85 : um.riskLevel === 'medium' ? 60 : 30
    );
    var days = item.days_until_expiry;
    var daysDisplay = '';
    if (days < 0) {
        var graceDays = item.grace_days_remaining;
        daysDisplay = graceDays != null
            ? t('leads.renewal_grace_days', { days: graceDays })
            : t('leads.days_overdue', { days: Math.abs(days) });
    } else {
        daysDisplay = t('leads.days_left', { days: days });
    }
    var urgencyBadge = '<div class="flex flex-col items-center gap-1 flex-shrink-0">'
        + '<span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold" style="' + urgencyColor + '">' + um.label + '</span>'
        + '<span class="text-xs font-medium" style="color:' + (days < 0 ? 'var(--color-risk-critical-text)' : 'var(--color-text-secondary)') + '">' + daysDisplay + '</span>'
        + '</div>';

    // Reuse shared components
    var displayName = getTrademarkDisplayName(item);
    var thumbnail = window.AppComponents.renderThumbnail(item.image_path, displayName, item.application_no);
    var classBadges = window.AppComponents.renderNiceClassBadges(item.nice_classes);
    var turkpatentBtn = window.AppComponents.renderTurkpatentButton(item.application_no);
    var regNo = window.AppComponents.renderRegistrationNo(item.registration_no);
    var holderLink = window.AppComponents.renderHolderLink(item.holder_name, item.holder_tpe_client_id || null);
    var attorneyLink = window.AppComponents.renderAttorneyLink(item.attorney_name, item.attorney_no);
    var eventsBtn = window.AppComponents.renderEventsButton(item.application_no);

    // Status badge
    var statusHtml = '<span class="text-xs px-2 py-0.5 rounded-full font-medium" style="color:' + getStatusColor(item.status) + ';background:' + getStatusBg(item.status) + '">' + getStatusText(item.status) + '</span>';

    // Expiry date line
    var expiryHtml = item.expiry_date
        ? '<div class="text-xs mt-0.5" style="color:var(--color-text-faint)">'
            + '<svg class="w-3 h-3 inline-block mr-0.5 -mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
            + t('leads.renewal_col_expiry') + ': ' + formatDateTRShort(item.expiry_date)
            + '</div>'
        : '';

    // Application date line
    var appDateHtml = item.application_date
        ? '<div class="text-xs" style="color:var(--color-text-faint)">' + t('common.application_date') + ' ' + formatDateTRShort(item.application_date) + '</div>'
        : '';

    var inner = '<div class="flex justify-between items-start gap-4">'
        + '<div class="flex items-start gap-3 flex-1 min-w-0">'
        + thumbnail
        + '<div class="flex-1 min-w-0">'
        + '<div class="font-semibold truncate" style="color:var(--color-text-primary)">' + escapeHtml(displayName) + '</div>'
        + '<div class="mt-0.5">' + statusHtml + '</div>'
        + appDateHtml
        + expiryHtml
        + classBadges
        + turkpatentBtn
        + regNo
        + holderLink
        + attorneyLink
        + '<div class="mt-1">' + eventsBtn + '</div>'
        + '</div>'
        + '</div>'
        + urgencyBadge
        + '</div>';

    return window.AppComponents.renderCardShell(inner, { riskLevel: um.riskLevel });
}

window.AppAPI.exportRenewalsCSV = async function () {
    try {
        var urgency = document.getElementById('filter-renewal-urgency').value;
        var niceClass = document.getElementById('filter-renewal-nice-class').value;
        var url = '/api/v1/leads/renewals/export/csv?';
        if (urgency) url += 'urgency=' + urgency + '&';
        if (niceClass) url += 'nice_class=' + niceClass + '&';

        var response = await fetch(url, {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (response.status === 403) {
            var denied = await _readApiErrorData(response);
            showUpgradeModal(denied.detail || denied, 'csv_export');
            return;
        }
        if (!response.ok) throw new Error('Export failed');

        var blob = await response.blob();
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'renewals_' + new Date().toISOString().slice(0, 10) + '.csv';
        a.click();
    } catch (error) {
        showToast(t('common.error') + ': ' + error.message, 'error');
    }
};

var switchRadarMode = window.AppAPI.switchRadarMode;
var loadRenewalStats = window.AppAPI.loadRenewalStats;
var loadRenewalFeed = window.AppAPI.loadRenewalFeed;
var exportRenewalsCSV = window.AppAPI.exportRenewalsCSV;

window.AppAPI.showLeadDetail = async function (leadId) {
    currentLeadId = leadId;
    var modal = document.getElementById('lead-detail-modal');
    var content = document.getElementById('lead-detail-content');

    modal.classList.remove('hidden');
    if (typeof lockBodyScroll === 'function') lockBodyScroll();
    content.innerHTML = '<div class="text-center py-8"><div class="animate-spin inline-block w-8 h-8 border-4 border-indigo-200 border-t-indigo-600 rounded-full"></div></div>';

    try {
        var response = await fetch('/api/v1/leads/' + leadId, {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (!response.ok) throw new Error('Failed to load lead');

        var lead = await response.json();
        var scorePercent = Math.round(lead.similarity_score * 100);

        // Conflict reason pills (shown in footer)
        var reasonPills = '';
        if (lead.conflict_reasons && lead.conflict_reasons.length) {
            reasonPills = lead.conflict_reasons.map(function (r) {
                return '<span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">'
                    + '<span style="color:var(--color-risk-medium-text)">&#8226;</span> ' + r + '</span>';
            }).join('');
        }

        // Similarity category badge (radar 4-bucket scheme; replaces numeric score)
        var headerCategoryBadge = window.AppComponents.renderSimilarityCategoryBadge(scorePercent, { size: 'lg' });
        var centerCategoryBadge = window.AppComponents.renderSimilarityCategoryBadge(scorePercent, { size: 'lg' });

        // VS comparison (borderless cards via updated renderVsComparison)
        var vsHtml = window.AppComponents.renderVsComparison({
            centerHtml: centerCategoryBadge,
            newMark: {
                image: lead.new_mark_image,
                name: lead.new_mark_name,
                app_no: lead.new_mark_app_no,
                holder: lead.new_mark_holder_name,
                classes: lead.new_mark_nice_classes,
                has_extracted_goods: lead.new_mark_has_extracted_goods
            },
            existingMark: {
                image: lead.existing_mark_image,
                name: lead.existing_mark_name,
                app_no: lead.existing_mark_app_no,
                holder: lead.existing_mark_holder_name,
                classes: lead.existing_mark_nice_classes,
                has_extracted_goods: lead.existing_mark_has_extracted_goods
            }
        });

        // Timeline bar only (no duplicate bordered box)
        var timelineHtml = window.AppComponents.renderTimelineBar(lead.bulletin_date, lead.opposition_deadline);

        content.innerHTML = '<div class="space-y-4">'

            // ── TOP: Similarity category (left) │ Timeline (right) ──
            + '<div class="flex gap-4 items-start">'
            +   '<div class="flex-1 min-w-0">'
            +     headerCategoryBadge
            +   '</div>'
            +   (timelineHtml
                  ? '<div class="flex-shrink-0" style="min-width:155px;max-width:185px">' + timelineHtml + '</div>'
                  : '')
            + '</div>'

            // ── MIDDLE: VS Comparison ──
            + vsHtml

            // ── BOTTOM: Reasons + Metadata in one clean footer ──
            + '<div class="pt-3 space-y-2" style="border-top:1px solid var(--color-border)">'
            + (reasonPills ? '<div class="flex flex-wrap gap-1.5">' + reasonPills + '</div>' : '')
            + '<div class="flex items-center gap-1.5 flex-wrap text-xs" style="color:var(--color-text-faint)">'
            +   '<span>' + t('leads.bulletin_label') + ' <strong style="color:var(--color-text-secondary)">' + (lead.bulletin_no || t('common.na')) + '</strong></span>'
            +   '<span>&bull;</span>'
            +   '<span>' + (lead.bulletin_date ? window.AppComponents.formatTimelineDate(lead.bulletin_date) : t('common.na')) + '</span>'
            +   '<span>&bull;</span>'
            +   '<span>' + (lead.conflict_type || '') + '</span>'
            + '</div>'
            + '</div>'

            + '</div>';

        loadLeadFeed(currentLeadPage);

    } catch (error) {
        console.error('Failed to load lead detail:', error);
        content.innerHTML = '<div class="text-center py-8 text-red-500">' + t('leads.load_detail_error') + '</div>';
    }
};

window.AppAPI.updateLeadStatus = async function (leadId, action) {
    try {
        var response = await fetch('/api/v1/leads/' + leadId + '/' + action, {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (!response.ok) throw new Error('Failed');

        var result = await response.json();
        showToast(result.message, 'success');
        hideLeadDetailModal();
        loadLeadFeed(currentLeadPage);
        loadLeadStats();
    } catch (error) {
        console.error('Failed to update lead:', error);
        showToast(t('leads.update_failed'), 'error');
    }
};

window.AppAPI.exportLeadsCSV = async function () {
    try {
        var urgency = document.getElementById('filter-urgency').value;
        var niceClass = document.getElementById('filter-nice-class').value;

        var url = '/api/v1/leads/export/csv?';
        if (urgency) url += 'urgency=' + urgency + '&';
        if (niceClass) url += 'nice_class=' + niceClass;

        var response = await fetch(url, {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (response.status === 403) {
            var denied = await _readApiErrorData(response);
            showUpgradeModal(denied.detail || denied, 'csv_export');
            return;
        }
        if (!response.ok) throw new Error('Export failed');

        var blob = await response.blob();
        var downloadUrl = window.URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = downloadUrl;
        a.download = 'leads_' + new Date().toISOString().split('T')[0] + '.csv';
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(downloadUrl);
        showToast(t('leads.csv_success'), 'success');
    } catch (error) {
        console.error('Export failed:', error);
        showToast(t('leads.csv_failed'), 'error');
    }
};

// ============================================
// GENERIC ENTITY PORTFOLIO (holder + attorney)
// ============================================
window.AppAPI._loadEntityTrademarks = async function (entityType, entityId, page) {
    var apiPath = entityType === 'attorney'
        ? '/api/v1/attorneys/' + encodeURIComponent(entityId) + '/trademarks'
        : '/api/v1/holders/' + encodeURIComponent(entityId) + '/trademarks';
    var nameKey = entityType === 'attorney' ? 'attorney_name' : 'holder_name';
    var idKey = entityType === 'attorney' ? 'attorney_no' : 'holder_tpe_client_id';
    var i18nPrefix = entityType === 'attorney' ? 'attorney' : 'holder';
    var subtitleParam = entityType === 'attorney' ? 'attorneyNo' : 'tpeId';

    try {
        var res = await fetch(apiPath + '?page=' + page + '&page_size=20', {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });

        if (res.status === 403) {
            closeEntityPortfolio();
            var denied = await _readApiErrorData(res);
            showUpgradeModal(denied.detail || denied, 'portfolio_download');
            return;
        }
        if (!res.ok) throw new Error('HTTP ' + res.status);

        var data = await res.json();

        document.getElementById('entityModalTitle').textContent = data[nameKey];
        var subtitleParams = { count: data.total_count };
        subtitleParams[subtitleParam] = data[idKey];
        document.getElementById('entityModalSubtitle').textContent = t(i18nPrefix + '.subtitle', subtitleParams);

        document.getElementById('entityTotalCount').textContent = data.total_count;
        window._entityPortfolioTotalCount = data.total_count || 0;
        var registered = 0, pending = 0;
        data.trademarks.forEach(function (tm) {
            if (tm.status === 'Tescil Edildi' || tm.status === 'Yenilendi') registered++;
            if (tm.status === 'Başvuruldu' || tm.status === 'Yayında' || tm.status === 'İtiraz Edildi') pending++;
        });
        document.getElementById('entityRegisteredCount').textContent = registered;
        document.getElementById('entityPendingCount').textContent = pending;

        renderEntityTrademarks(data.trademarks);
        renderEntityPagination(data.page, data.total_pages, entityId);

        document.getElementById('entityPortfolioLoading').classList.add('hidden');
        document.getElementById('entityPortfolioResults').classList.remove('hidden');

        // Show footer with Watch All + CSV buttons
        var footer = document.getElementById('entityPortfolioFooter');
        if (footer) {
            footer.classList.remove('hidden');
            var footerTotal = document.getElementById('entityFooterTotal');
            if (footerTotal) footerTotal.textContent = t('holder.total_trademarks_label', { count: data.total_count });
        }

    } catch (e) {
        document.getElementById('entityPortfolioLoading').classList.add('hidden');
        document.getElementById('entityPortfolioError').classList.remove('hidden');
        document.getElementById('entityErrorMessage').textContent = t(i18nPrefix + '.load_error');
    }
};

// Thin wrappers for backward compat and pagination onclick
window.AppAPI.loadHolderTrademarks = function (tpeClientId, page) {
    return window.AppAPI._loadEntityTrademarks('holder', tpeClientId, page);
};
window.AppAPI.loadAttorneyTrademarks = function (attorneyNo, page) {
    return window.AppAPI._loadEntityTrademarks('attorney', attorneyNo, page);
};

// ============================================
// CREATIVE SUITE - NAME GENERATOR
// ============================================
window.AppAPI.generateNames = async function (params) {
    var res = await fetch('/api/v1/tools/suggest-names', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + getAuthToken(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(params)
    });

    var data = await res.json();

    if (res.status === 403) {
        showUpgradeModal(data.detail || data, 'name_suggestions');
        throw new Error('upgrade_required');
    }
    if (res.status === 402) {
        showUpgradeModal(data.detail || data, 'ai_credits');
        throw new Error('credits_exhausted');
    }
    if (res.status === 401) {
        showToast(t('auth.session_expired'), 'error');
        throw new Error('unauthorized');
    }
    if (!res.ok) {
        var msg = (data.detail && data.detail.message) || data.detail || t('studio.name_gen_failed');
        throw new Error(msg);
    }

    return data;
};

// ============================================
// CREATIVE SUITE - LOGO GENERATOR
// ============================================
window.AppAPI.generateLogos = async function (params) {
    var res = await fetch('/api/v1/tools/generate-logo', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + getAuthToken(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(params)
    });

    var data = await res.json();

    if (res.status === 403) {
        showUpgradeModal(data.detail || data, 'ai_credits');
        throw new Error('upgrade_required');
    }
    if (res.status === 402) {
        showUpgradeModal(data.detail || data, 'ai_credits');
        throw new Error('credits_exhausted');
    }
    if (res.status === 401) {
        showToast(t('auth.session_expired'), 'error');
        throw new Error('unauthorized');
    }
    if (!res.ok) {
        var msg = (data.detail && data.detail.message) || data.detail || t('studio.logo_gen_failed');
        throw new Error(msg);
    }

    return data;
};

// ============================================
// CREATIVE SUITE - GENERATION HISTORY
// ============================================
window.AppAPI.getGenerationHistory = async function (page, featureType) {
    if (page === undefined) page = 1;
    var url = '/api/v1/tools/generation-history?page=' + page + '&per_page=20';
    if (featureType) url += '&feature_type=' + featureType;

    var res = await fetch(url, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) throw new Error(t('studio.history_failed'));
    return await res.json();
};

window.AppAPI.deleteGenerationHistoryItem = async function (historyId) {
    var res = await fetch('/api/v1/tools/generation-history/' + encodeURIComponent(historyId), {
        method: 'DELETE',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) {
        throw _buildApiError(res, data, t('studio.history_delete_failed'));
    }
    return data;
};

window.AppAPI.clearGenerationHistory = async function (featureType) {
    var url = '/api/v1/tools/generation-history';
    if (featureType) url += '?feature_type=' + encodeURIComponent(featureType);
    var res = await fetch(url, {
        method: 'DELETE',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) {
        throw _buildApiError(res, data, t('studio.history_delete_failed'));
    }
    return data;
};

window.AppAPI.getLogoProject = async function (projectId) {
    var res = await fetch('/api/v1/tools/logo-projects/' + encodeURIComponent(projectId), {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) throw new Error(t('studio.project_load_failed'));
    return await res.json();
};

window.AppAPI.selectLogoCandidate = async function (projectId, imageId) {
    var res = await fetch('/api/v1/tools/logo-projects/' + encodeURIComponent(projectId) + '/select', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + getAuthToken(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ image_id: imageId })
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) {
        throw _buildApiError(res, data, t('studio.select_logo_failed'));
    }
    return data;
};

window.AppAPI.retryLogoAudit = async function (imageId) {
    var res = await fetch('/api/v1/tools/generated-image/' + encodeURIComponent(imageId) + '/audit-retry', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) {
        throw _buildApiError(res, data, t('studio.audit_retry_failed'));
    }
    return data;
};

// ============================================
// PIPELINE MANAGEMENT (admin only)
// ============================================
window.AppAPI.getPipelineStatus = async function () {
    var res = await fetch('/api/v1/pipeline/status', {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (res.status === 403) return null;
    if (!res.ok) throw new Error(t('pipeline.status_fetch_failed'));
    return await res.json();
};

window.AppAPI.triggerPipeline = async function (skipDownload) {
    var res = await fetch('/api/v1/pipeline/trigger?skip_download=' + (skipDownload ? 'true' : 'false'), {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    var data = await res.json();
    if (res.status === 409) throw new Error(data.detail.message || t('pipeline.already_running'));
    if (res.status === 403) throw new Error(t('pipeline.no_permission'));
    if (!res.ok) throw new Error(data.detail || t('pipeline.start_failed'));
    return data;
};

window.AppAPI.triggerPipelineStep = async function (step) {
    var res = await fetch('/api/v1/pipeline/trigger-step?step=' + encodeURIComponent(step), {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    var data = await res.json();
    if (res.status === 409) throw new Error(data.detail.message || t('pipeline.already_running'));
    if (res.status === 403) throw new Error(t('pipeline.no_permission'));
    if (!res.ok) throw new Error(data.detail || t('pipeline.step_start_failed'));
    return data;
};

// ============================================
// WATCHLIST LOGO MANAGEMENT
// ============================================
window.AppAPI.uploadWatchlistLogo = async function (itemId, file) {
    var formData = new FormData();
    formData.append('logo', file);

    var res = await fetch('/api/v1/watchlist/' + itemId + '/logo', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() },
        body: formData
    });
    if (!res.ok) {
        var data = await _readApiErrorData(res);
        throw _buildApiError(res, data, t('watchlist.logo_upload_failed'));
    }
    return await res.json();
};

window.AppAPI.deleteWatchlistLogo = async function (itemId) {
    var res = await fetch('/api/v1/watchlist/' + itemId + '/logo', {
        method: 'DELETE',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var data = await res.json();
        throw new Error(data.detail || t('watchlist.logo_delete_failed'));
    }
    return await res.json();
};

window.AppAPI.getWatchlistItems = async function (page, pageSize, search, sort, renewalOnly, appealsOnly, statusFilter, threshold, tmStatus) {
    if (page === undefined) page = 1;
    if (pageSize === undefined) pageSize = 20;
    var url = '/api/v1/watchlist?page=' + page + '&page_size=' + pageSize;
    if (search) url += '&search=' + encodeURIComponent(search);
    if (sort) url += '&sort=' + encodeURIComponent(sort);
    if (renewalOnly) url += '&renewal_only=true';
    if (appealsOnly) url += '&appeals_only=true';
    if (statusFilter) url += '&status_filter=' + encodeURIComponent(statusFilter);
    if (threshold != null) url += '&threshold=' + threshold;
    if (tmStatus) url += '&tm_status=' + encodeURIComponent(tmStatus);
    var res = await fetch(url, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) throw new Error(t('watchlist.load_failed'));
    return await res.json();
};

window.AppAPI.getWatchlistStats = async function (minScore) {
    var url = '/api/v1/watchlist/stats';
    if (minScore != null && minScore > 0) url += '?min_score=' + minScore;
    var res = await fetch(url, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) throw new Error('Failed to load stats');
    return await res.json();
};

window.AppAPI.getAggregateAlerts = async function (page, pageSize, severity) {
    if (page === undefined) page = 1;
    if (pageSize === undefined) pageSize = 20;
    var url = '/api/v1/alerts/aggregate?page=' + page + '&page_size=' + pageSize;
    if (severity) url += '&severity=' + encodeURIComponent(severity);
    var res = await fetch(url, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) throw new Error('Failed to load aggregate alerts');
    return await res.json();
};

window.AppAPI.addWatchlistItem = async function (data) {
    var res = await fetch('/api/v1/watchlist', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + getAuthToken(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    });
    var body = await res.json().catch(function () { return {}; });
    if (!res.ok) {
        throw _buildApiError(res, body, t('watchlist.add_failed'));
    }
    return body;
};

window.AppAPI.deleteWatchlistItem = async function (itemId) {
    var res = await fetch('/api/v1/watchlist/' + itemId, {
        method: 'DELETE',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var data = await res.json().catch(function () { return {}; });
        throw new Error(data.detail || t('watchlist.delete_failed'));
    }
    return await res.json();
};

window.AppAPI.updateWatchlistItem = async function (itemId, data) {
    var res = await fetch('/api/v1/watchlist/' + itemId, {
        method: 'PUT',
        headers: {
            'Authorization': 'Bearer ' + getAuthToken(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        var body = await _readApiErrorData(res);
        throw _buildApiError(res, body, t('watchlist.update_failed'));
    }
    return await res.json();
};

window.AppAPI.scanWatchlistItem = async function (itemId) {
    var res = await fetch('/api/v1/watchlist/' + itemId + '/scan', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var data = await res.json().catch(function () { return {}; });
        throw new Error(data.detail || t('watchlist.scan_failed'));
    }
    return await res.json();
};

window.AppAPI.scanAllWatchlist = async function () {
    var res = await fetch('/api/v1/watchlist/scan-all', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var data = await res.json().catch(function () { return {}; });
        throw new Error(data.detail || t('watchlist.scan_all_failed'));
    }
    return await res.json();
};

window.AppAPI.updateAllThreshold = async function (threshold) {
    var res = await fetch('/api/v1/watchlist/bulk-threshold', {
        method: 'PUT',
        headers: {
            'Authorization': 'Bearer ' + getAuthToken(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ threshold: threshold })
    });
    if (!res.ok) throw new Error('Failed to update threshold');
    return await res.json();
};

window.AppAPI.deleteAllWatchlist = async function () {
    var res = await fetch('/api/v1/watchlist/all', {
        method: 'DELETE',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var data = await res.json().catch(function () { return {}; });
        throw new Error(data.detail || t('watchlist.delete_all_failed'));
    }
    return await res.json();
};

window.AppAPI.downloadWatchlistTemplate = function () {
    var token = getAuthToken();
    window.open('/api/v1/watchlist/upload/template?token=' + encodeURIComponent(token), '_blank');
};

window.AppAPI.detectWatchlistColumns = async function (file) {
    var formData = new FormData();
    formData.append('file', file);
    var res = await fetch('/api/v1/watchlist/upload/detect-columns', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() },
        body: formData
    });
    if (!res.ok) {
        var data = await _readApiErrorData(res);
        throw _buildApiError(res, data, t('watchlist.upload_detect_failed'));
    }
    return await res.json();
};

window.AppAPI.uploadWatchlistFile = async function (file, columnMapping) {
    var formData = new FormData();
    formData.append('file', file);
    if (columnMapping) {
        formData.append('column_mapping', JSON.stringify(columnMapping));
    }
    var url = columnMapping ? '/api/v1/watchlist/upload/with-mapping' : '/api/v1/watchlist/upload';
    var res = await fetch(url, {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() },
        body: formData
    });
    if (!res.ok) {
        var data = await _readApiErrorData(res);
        throw _buildApiError(res, data, t('watchlist.upload_failed'));
    }
    return await res.json();
};

// ============================================
// ENTITY SEARCH (holder + attorney)
// ============================================
window.AppAPI.searchHolders = async function (query, limit) {
    if (limit === undefined) limit = 10;
    var res = await fetch('/api/v1/holders/search?query=' + encodeURIComponent(query) + '&limit=' + limit, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var data = await res.json().catch(function () { return {}; });
        var err = new Error(data.detail || t('search.search_failed'));
        err.status = res.status;
        err.data = data;
        throw err;
    }
    return await res.json();
};

window.AppAPI.searchAttorneys = async function (query, limit) {
    if (limit === undefined) limit = 10;
    var res = await fetch('/api/v1/attorneys/search?query=' + encodeURIComponent(query) + '&limit=' + limit, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var data = await res.json().catch(function () { return {}; });
        var err = new Error(data.detail || t('search.search_failed'));
        err.status = res.status;
        err.data = data;
        throw err;
    }
    return await res.json();
};

// ============================================
// REPORTS
// ============================================
window.AppAPI.generateReport = async function (reportData) {
    var res = await fetch('/api/v1/reports/generate', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + getAuthToken(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(reportData)
    });
    var data = await res.json();
    if (!res.ok) {
        var err = new Error((data.detail && data.detail.message) || data.detail || t('reports.generate_failed'));
        err.status = res.status;
        err.data = data;
        throw err;
    }
    return data;
};

window.AppAPI.claimRiskReport = async function (claimToken) {
    var res = await fetch('/api/v1/search/risk-report/claim', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + getAuthToken(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ claim_token: claimToken })
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) {
        var detail = data.detail || data;
        var err = new Error((detail && detail.message) || detail || t('reports.generate_failed'));
        err.status = res.status;
        err.data = data;
        throw err;
    }
    return data;
};

window.AppAPI.loadReports = async function (page, pageSize) {
    if (page === undefined) page = 1;
    if (pageSize === undefined) pageSize = 10;
    var res = await fetch('/api/v1/reports?page=' + page + '&page_size=' + pageSize, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var err = new Error(t('reports.load_failed'));
        err.status = res.status;
        throw err;
    }
    return await res.json();
};

window.AppAPI.downloadReport = async function (reportId) {
    var res = await fetch('/api/v1/reports/' + reportId + '/download', {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var data = await res.json().catch(function () { return {}; });
        var err = new Error((data.detail && data.detail.message) || data.detail || t('reports.download_failed'));
        err.status = res.status;
        err.data = data;
        throw err;
    }
    var disposition = res.headers.get('Content-Disposition');
    var filename = 'rapor.pdf';
    if (disposition) {
        var match = disposition.match(/filename[^;=\n]*=["']?([^"';\n]*)["']?/);
        if (match && match[1]) filename = match[1];
    }
    var blob = await res.blob();
    blob._filename = filename;
    return blob;
};

window.AppAPI.deleteReport = async function (reportId) {
    var res = await fetch('/api/v1/reports/' + encodeURIComponent(reportId), {
        method: 'DELETE',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) {
        var err = new Error((data.detail && data.detail.message) || data.detail || t('reports.delete_failed'));
        err.status = res.status;
        err.data = data;
        throw err;
    }
    return data;
};

window.AppAPI.deleteAllReports = async function () {
    var res = await fetch('/api/v1/reports', {
        method: 'DELETE',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) {
        var err = new Error((data.detail && data.detail.message) || data.detail || t('reports.delete_all_failed'));
        err.status = res.status;
        err.data = data;
        throw err;
    }
    return data;
};

// ============================================
// EXTRACTED GOODS LAZY LOAD
// ============================================
window.AppAPI.loadExtractedGoods = async function (applicationNo) {
    var resp = await fetch('/api/v1/trademark/' + encodeURIComponent(applicationNo) + '/extracted-goods', {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!resp.ok) {
        throw new Error('Failed to load extracted goods: ' + resp.status);
    }
    return await resp.json();
};

// Expose as globals for inline onclick handlers
var handleQuickSearch = window.AppAPI.handleQuickSearch;
var handleAgenticSearch = window.AppAPI.handleAgenticSearch;
var loadLeadStats = window.AppAPI.loadLeadStats;
var loadLeadCredits = window.AppAPI.loadLeadCredits;
var loadLeadFeed = window.AppAPI.loadLeadFeed;
var showLeadDetail = window.AppAPI.showLeadDetail;
var updateLeadStatus = window.AppAPI.updateLeadStatus;
var exportLeadsCSV = window.AppAPI.exportLeadsCSV;
var loadHolderTrademarks = window.AppAPI.loadHolderTrademarks;
var loadAttorneyTrademarks = window.AppAPI.loadAttorneyTrademarks;
var searchHolders = window.AppAPI.searchHolders;
var searchAttorneys = window.AppAPI.searchAttorneys;
var generateNamesAPI = window.AppAPI.generateNames;
var generateLogosAPI = window.AppAPI.generateLogos;
var getGenerationHistory = window.AppAPI.getGenerationHistory;
var deleteGenerationHistoryItemAPI = window.AppAPI.deleteGenerationHistoryItem;
var clearGenerationHistoryAPI = window.AppAPI.clearGenerationHistory;
var getLogoProjectAPI = window.AppAPI.getLogoProject;
var selectLogoCandidateAPI = window.AppAPI.selectLogoCandidate;
var retryLogoAuditAPI = window.AppAPI.retryLogoAudit;
var generateReport = window.AppAPI.generateReport;
var claimRiskReportAPI = window.AppAPI.claimRiskReport;
var loadReportsAPI = window.AppAPI.loadReports;
var downloadReportAPI = window.AppAPI.downloadReport;
var deleteReportAPI = window.AppAPI.deleteReport;
var deleteAllReportsAPI = window.AppAPI.deleteAllReports;
var loadExtractedGoods = window.AppAPI.loadExtractedGoods;

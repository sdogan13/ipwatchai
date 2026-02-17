/**
 * result-card.js - Search result card rendering
 * Uses shared helpers from score-badge.js
 */
window.AppComponents = window.AppComponents || {};

window.AppComponents.renderHolderLink = function(holderName, holderTpeId) {
    if (!holderName) return '';

    var displayHolderName = holderName;
    if (displayHolderName.length > 80) {
        displayHolderName = displayHolderName.substring(0, 77) + '...';
    }

    if (!holderTpeId) {
        return '<div class="text-sm mt-1" style="color:var(--color-text-secondary)">'
            + t('holder.applicant') + '<span style="color:var(--color-text-primary)">' + escapeHtml(displayHolderName) + '</span>'
            + '</div>';
    }

    var holderDisplay = escapeHtml(displayHolderName)
        + ' <span style="color:var(--color-text-faint)">(' + escapeHtml(holderTpeId) + ')</span>';
    var isPro = (currentUserPlan === 'professional' || currentUserPlan === 'enterprise');

    if (isPro) {
        return '<div class="text-sm mt-1" style="color:var(--color-text-secondary)">'
            + t('holder.applicant') + ''
            + '<a href="#" onclick="showHolderPortfolio(\'' + escapeHtml(holderTpeId) + '\', \'' + escapeHtml(holderName) + '\'); return false;" '
            + 'class="text-blue-600 hover:text-blue-800 hover:underline font-medium inline-flex items-center gap-1">'
            + holderDisplay + ' <span class="text-blue-500">&#x1f4cb;</span></a></div>';
    } else {
        return '<div class="text-sm mt-1" style="color:var(--color-text-secondary)">'
            + t('holder.applicant') + ''
            + '<span class="cursor-pointer inline-flex items-center gap-1" style="color:var(--color-text-primary)" '
            + 'title="' + t('upgrade.description').replace(/"/g, '&quot;') + '" '
            + 'onclick="showUpgradeModal()">'
            + holderDisplay + ' <span style="color:var(--color-text-faint)">&#x1f512;</span></span></div>';
    }
};

window.AppComponents.renderAttorneyLink = function(attorneyName, attorneyNo) {
    if (!attorneyName) return '';

    var displayName = attorneyName;
    if (displayName.length > 80) {
        displayName = displayName.substring(0, 77) + '...';
    }

    if (!attorneyNo) {
        return '<div class="text-sm mt-1" style="color:var(--color-text-secondary)">'
            + t('attorney.label') + '<span style="color:var(--color-text-primary)">' + escapeHtml(displayName) + '</span>'
            + '</div>';
    }

    var attorneyDisplay = escapeHtml(displayName)
        + ' <span style="color:var(--color-text-faint)">(' + escapeHtml(attorneyNo) + ')</span>';
    var isPro = (currentUserPlan === 'professional' || currentUserPlan === 'enterprise');

    if (isPro) {
        return '<div class="text-sm mt-1" style="color:var(--color-text-secondary)">'
            + t('attorney.label') + ''
            + '<a href="#" onclick="showAttorneyPortfolio(\'' + escapeHtml(attorneyNo) + '\', \'' + escapeHtml(displayName) + '\'); return false;" '
            + 'class="text-blue-600 hover:text-blue-800 hover:underline font-medium inline-flex items-center gap-1">'
            + attorneyDisplay + ' <span class="text-blue-500">&#x1f4cb;</span></a></div>';
    } else {
        return '<div class="text-sm mt-1" style="color:var(--color-text-secondary)">'
            + t('attorney.label') + ''
            + '<span class="cursor-pointer inline-flex items-center gap-1" style="color:var(--color-text-primary)" '
            + 'title="' + t('upgrade.description').replace(/"/g, '&quot;') + '" '
            + 'onclick="showUpgradeModal()">'
            + attorneyDisplay + ' <span style="color:var(--color-text-faint)">&#x1f512;</span></span></div>';
    }
};

window.AppComponents.renderRegistrationNo = function(registrationNo) {
    if (!registrationNo) return '';
    return '<div class="text-xs mt-0.5" style="color:var(--color-text-muted)">'
        + '<span style="color:var(--color-text-faint)">Reg:</span> '
        + '<span class="font-medium font-mono-id">' + escapeHtml(registrationNo) + '</span>'
        + '</div>';
};

window.AppComponents.renderResultCard = function(r) {
    var score = getResultScore(r);
    var pct = Math.round(score * 100);
    var riskLevel = window.AppComponents.getScoreRiskLevel(pct);

    // Use score ring instead of flat badge
    var scoreRing = window.AppComponents.renderScoreRing(pct, 44);
    var breakdownHtml = window.AppComponents.renderSimilarityBadges(r.scores);
    var scoringPathHtml = window.AppComponents.renderScoringPathBadge(r.scores);
    var weightsHtml = window.AppComponents.renderDynamicWeights(r.scores);
    var thumbnail = window.AppComponents.renderThumbnail(r.image_path, r.name, r.application_no);

    // Exact match badge
    var exactMatchHtml = '';
    if (r.exact_match || (r.scores && r.scores.exact_match)) {
        exactMatchHtml = '<span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-bold" style="background:var(--color-risk-critical-bg);color:var(--color-risk-critical-text)">'
            + '<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>'
            + t('scores.exact_match') + '</span>';
    }

    // Bulletin number
    var bulletinHtml = '';
    if (r.bulletin_no) {
        bulletinHtml = '<span class="text-xs" style="color:var(--color-text-faint)">'
            + t('common.bulletin_label') + ' ' + escapeHtml(r.bulletin_no) + '</span>';
    }

    // AI Studio CTAs for high-risk / Register CTAs for low-risk results
    var studioCta = '';
    var queryName = r._query_name || '';
    var queryClasses = r._query_classes || [];

    if (pct >= 65 && queryName) {
        // Risky (>=65%): suggest safe alternatives via AI Studio
        var nameCtx = encodeURIComponent(JSON.stringify({ query: queryName, nice_classes: queryClasses }));
        studioCta += '<div class="mt-2 pt-2" style="border-top:1px solid var(--color-border-light)">'
            + '<button onclick="openStudioWithContext(\'name\', JSON.parse(decodeURIComponent(\'' + nameCtx + '\')))" '
            + 'class="text-xs text-purple-600 hover:text-purple-800 font-medium flex items-center gap-1 btn-press">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>'
            + t('studio.risky_name_cta') + '</button>';

        var visSim = r.scores ? (r.scores.visual_similarity || 0) : 0;
        if (visSim > 0.65) {
            studioCta += '<button onclick="openStudioWithContext(\'logo\', JSON.parse(decodeURIComponent(\'' + nameCtx + '\')))" '
                + 'class="text-xs text-purple-600 hover:text-purple-800 font-medium flex items-center gap-1 mt-1 btn-press">'
                + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
                + t('studio.similar_logo_cta') + '</button>';
        }

        studioCta += '</div>';
    } else if (pct < 65 && queryName) {
        // Safe (<65%): encourage in-app trademark application
        var appCtx = encodeURIComponent(JSON.stringify({ name: queryName, classes: queryClasses }));
        studioCta += '<div class="mt-2 pt-2" style="border-top:1px solid var(--color-border-light)">'
            + '<button onclick="var _c=JSON.parse(decodeURIComponent(\'' + appCtx + '\'));openApplicationWithContext(_c.name,_c.classes)" '
            + 'class="text-xs text-green-600 hover:text-green-800 font-medium flex items-center gap-1 btn-press">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'
            + t('studio.safe_name_cta') + '</button>';

        var visSim2 = r.scores ? (r.scores.visual_similarity || 0) : 0;
        if (visSim2 < 0.65) {
            studioCta += '<button onclick="var _c=JSON.parse(decodeURIComponent(\'' + appCtx + '\'));openApplicationWithContext(_c.name,_c.classes)" '
                + 'class="text-xs text-green-600 hover:text-green-800 font-medium flex items-center gap-1 mt-1 btn-press">'
                + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
                + t('studio.safe_logo_cta') + '</button>';
        }

        studioCta += '</div>';
    }

    // Watchlist add/status button — hide for rejected/withdrawn/expired/cancelled applications
    var watchlistBtnHtml = '';
    var _st = (r.status || '').toLowerCase();
    var _isDeadStatus = (_st === 'refused' || _st === 'withdrawn' || _st === 'expired' || _st === 'cancelled'
        || _st === 'reddedildi' || _st === 'geri çekildi' || _st === 'geri cekildi'
        || _st === 'süresi doldu' || _st === 'iptal edildi');
    if (r.application_no && !_isDeadStatus) {
        if (typeof isInWatchlist === 'function' && isInWatchlist(r.application_no)) {
            watchlistBtnHtml = '<span class="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-green-700 bg-green-50 rounded mt-1.5">'
                + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>'
                + t('watchlist.already_watching') + '</span>';
        } else {
            var wlData = JSON.stringify({
                name: r.name || '',
                application_no: r.application_no || '',
                classes: r.classes || [],
                image_path: r.image_path || '',
                holder_name: r.holder_name || '',
                bulletin_no: r.bulletin_no || ''
            }).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
            watchlistBtnHtml = '<button data-watchlist-appno="' + escapeHtml(r.application_no) + '" '
                + 'onclick="event.stopPropagation(); openQuickWatchlistAdd(JSON.parse(this.getAttribute(\'data-wl-payload\')))" '
                + 'data-wl-payload="' + wlData + '" '
                + 'class="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 rounded transition mt-1.5 btn-press min-h-[28px]">'
                + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>'
                + t('watchlist.add_to_watchlist') + '</button>';
        }
    }

    // Extracted goods indicator
    var extractedGoodsHtml = '';
    if (r.has_extracted_goods) {
        var safeAppNo = (r.application_no || '').replace(/'/g, "\\'");
        extractedGoodsHtml = '<div class="mt-1.5">'
            + '<button onclick="showExtractedGoods(\'' + safeAppNo + '\', this)" '
            + 'class="inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer transition-colors btn-press min-h-[28px]">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
            + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/>'
            + '</svg>'
            + t('extracted_goods.label') + ' <span class="underline">' + t('extracted_goods.yes') + '</span>'
            + '</button></div>';
    }

    // Scoring metadata row (path + weights + exact match)
    var scoringMetaHtml = '';
    if (exactMatchHtml || scoringPathHtml || weightsHtml || bulletinHtml) {
        scoringMetaHtml = '<div class="flex flex-wrap items-center gap-1.5 mt-1">'
            + exactMatchHtml + scoringPathHtml + weightsHtml + bulletinHtml
            + '</div>';
    }

    var inner = '<div class="flex justify-between items-start gap-4">'
        + '<div class="flex items-start gap-3 flex-1 min-w-0">'
        + thumbnail
        + '<div class="flex-1 min-w-0">'
        + '<div class="font-semibold truncate" style="color:var(--color-text-primary)">' + highlightMatches(r.name || 'N/A', r.scores && r.scores.matched_words) + '</div>'
        + (r.name_tr && r.name_tr.toLowerCase() !== (r.name || '').toLowerCase() ? '<div class="text-xs mt-0.5" style="color:var(--color-text-faint)">TR: ' + escapeHtml(r.name_tr) + '</div>' : '')
        + '<div class="text-sm" style="color:var(--color-text-muted)">' + (r.status || 'N/A') + '</div>'
        + (r.application_date ? '<div class="text-xs" style="color:var(--color-text-faint)">' + t('common.application_date') + ' ' + formatDateTRShort(r.application_date) + '</div>' : '')
        + window.AppComponents.renderNiceClassBadges(r.classes)
        + window.AppComponents.renderTurkpatentButton(r.application_no)
        + window.AppComponents.renderRegistrationNo(r.registration_no)
        + window.AppComponents.renderHolderLink(r.holder_name, r.holder_tpe_client_id)
        + window.AppComponents.renderAttorneyLink(r.attorney_name || r.attorney, r.attorney_no)
        + scoringMetaHtml
        + breakdownHtml
        + watchlistBtnHtml
        + extractedGoodsHtml
        + studioCta
        + '</div>'
        + '</div>'
        + scoreRing
        + '</div>';

    return window.AppComponents.renderCardShell(inner, { riskLevel: riskLevel });
};

// Expose as globals
var renderHolderLink = window.AppComponents.renderHolderLink;
var renderAttorneyLink = window.AppComponents.renderAttorneyLink;
var renderResultCard = window.AppComponents.renderResultCard;

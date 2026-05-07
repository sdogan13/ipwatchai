/**
 * studio-card.js - AI Studio card rendering (Name Lab + Logo Studio)
 * Uses shared helpers from score-badge.js
 * Uses CSS custom properties for dark mode compatibility.
 */
window.AppComponents = window.AppComponents || {};

// ============================================
// A) Name suggestion card
// ============================================
window.AppComponents.renderNameCard = function(name, index) {
    var riskPct = Math.round(name.risk_score || 0);
    var isSafe = name.is_safe;
    var riskLevel = window.AppComponents.getScoreRiskLevel(riskPct);

    var safetyBadge = isSafe
        ? '<span class="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full" style="' + window.AppComponents.getScoreColor(20) + '">&#x2713; ' + t('studio.safe_badge') + '</span>'
        : '<span class="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full" style="' + window.AppComponents.getScoreColor(70) + '">&#x26a0; ' + t('studio.caution_badge') + '</span>';

    var scoreRing = window.AppComponents.renderScoreRing(riskPct, 40);

    // Risk level badge with color
    var riskLevelHtml = '';
    if (name.risk_level && name.risk_level !== 'low') {
        var rlStyle = window.AppComponents.getRiskBadgeSmall(name.risk_level);
        var rlLabels = { 'critical': t('risk_level.critical'), 'very_high': t('risk_level.very_high'), 'high': t('risk_level.high'), 'medium': t('risk_level.medium') };
        var rlLabel = rlLabels[name.risk_level] || name.risk_level;
        riskLevelHtml = '<span class="inline-flex items-center text-xs font-medium px-2 py-0.5 rounded-full" style="' + rlStyle + '">' + rlLabel + '</span>';
    }

    var closestHtml = '';
    if (name.closest_match) {
        closestHtml = '<div class="text-xs mt-1.5" style="color:var(--color-text-muted)">'
            + t('studio.closest_label') + ' <span class="font-medium" style="color:var(--color-text-secondary)">' + escapeHtml(name.closest_match) + '</span>';
        var maxSim = Math.max(name.text_similarity || 0, name.semantic_similarity || 0);
        if (maxSim > 0) {
            closestHtml += ' <span style="color:var(--color-text-faint)">(' + t('studio.similarity_pct', { pct: Math.round(maxSim * 100) }) + ')</span>';
        }
        closestHtml += '</div>';
    }

    var badgesHtml = window.AppComponents.renderSimilarityBadges(name);
    // Phonetic match badge (binary signal, shown separately)
    if (name.phonetic_match) {
        badgesHtml += '<div class="mt-1"><span class="text-xs px-1.5 py-0.5 rounded" style="' + window.AppComponents.getScoreColor(90) + '">' + t('studio.phonetic_match') + '</span></div>';
    }

    var useForLogoBtn = isSafe
        ? '<button onclick="useNameForLogo(\'' + escapeHtml(name.name).replace(/'/g, "\\'") + '\')" '
          + 'class="studio-logo-action-button mt-3 btn-press">'
          + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
          + t('studio.generate_logo_btn') + '</button>'
        : '';

    var inner = '<div class="flex justify-between items-start gap-3">'
        + '<div class="flex-1 min-w-0">'
        + '<div class="flex items-center gap-2 mb-1 flex-wrap">'
        + '<span class="text-lg font-bold" style="color:var(--color-text-primary)">' + escapeHtml(name.name) + '</span>'
        + safetyBadge
        + riskLevelHtml
        + '</div>'
        + closestHtml
        + badgesHtml
        + useForLogoBtn
        + '</div>'
        + '<div class="flex-shrink-0">' + scoreRing + '</div>'
        + '</div>';

    return '<div class="studio-name-card risk-stripe-' + riskLevel + '">' + inner + '</div>';
};

// ============================================
// B) Logo result card
// ============================================
window.AppComponents.renderLogoCard = function(logo) {
    function normalizeLogoPct(value, fallback) {
        var raw = value;
        if (raw === undefined || raw === null || raw === '') raw = fallback;
        var n = Number(raw || 0);
        if (!isFinite(n)) n = 0;
        if (n > 0 && n <= 1) n = n * 100;
        return Math.round(n);
    }
    var simPct = normalizeLogoPct(
        logo.llm_risk_score != null ? logo.llm_risk_score : logo.similarity_score,
        logo.similarity_score || 0
    );
    var isSafe = logo.is_safe;
    var auditStatus = logo.audit_status || 'completed';
    var auditDone = auditStatus === 'completed';
    var auditPending = auditStatus === 'pending' || auditStatus === 'running';
    var auditFailed = auditStatus === 'failed';
    var canDownload = auditDone && isSafe;

    var safetyBadge = '';
    if (auditPending) {
        safetyBadge = '<span class="studio-audit-badge is-pending">' + t('studio.audit_status_' + auditStatus) + '</span>';
    } else if (auditFailed) {
        safetyBadge = '<span class="studio-audit-badge is-failed">' + t('studio.audit_status_failed') + '</span>';
    } else {
        safetyBadge = isSafe
            ? '<span class="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full" style="' + window.AppComponents.getScoreColor(20) + '">&#x2713; ' + t('studio.safe_badge') + '</span>'
            : '<span class="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full" style="' + window.AppComponents.getScoreColor(90) + '">&#x26a0; ' + t('studio.risk_exists') + '</span>';
    }

    var closestMatchName = logo.closest_match_name;
    var closestMatchUrl = logo.closest_match_image_url;
    var closestSubtitle = t('studio.relevant_existing_mark');

    var closestHtml = '';
    if (auditDone && closestMatchName) {
        closestHtml = '<div class="text-xs mt-1" style="color:var(--color-text-muted)">'
            + t('studio.closest_label') + ' <span class="font-medium" style="color:var(--color-text-secondary)">' + escapeHtml(closestMatchName) + '</span>'
            + '</div>';

        // Show the closest match's logo image alongside the name. Render the
        // panel in red-warning styling for unsafe candidates and in neutral
        // styling for safe ones (still informational, not alarming).
        if (closestMatchUrl) {
            var matchUrl = closestMatchUrl;
            var matchName = (closestMatchName || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
            var panelBg = isSafe ? 'var(--color-bg-muted)' : 'var(--color-risk-critical-bg)';
            var panelBorder = isSafe ? 'var(--color-border)' : 'var(--color-risk-critical-border)';
            var labelColor = isSafe ? 'var(--color-text-muted)' : 'var(--color-risk-critical-text)';
            var hoverRing = isSafe ? 'hover:ring-indigo-200' : 'hover:ring-red-300';
            closestHtml += '<div class="mt-2 flex items-center gap-2 rounded-lg p-2" style="background:' + panelBg + ';border:1px solid ' + panelBorder + '">'
                + '<img src="' + matchUrl + '" alt="' + closestSubtitle + '" '
                + 'class="w-10 h-10 object-contain rounded cursor-pointer hover:ring-2 ' + hoverRing + ' transition" '
                + 'style="background:var(--color-bg-card);border:1px solid ' + panelBorder + '" '
                + 'onclick="window.dispatchEvent(new CustomEvent(\'open-lightbox\', { detail: { src: \'' + matchUrl.replace(/'/g, "\\'") + '\', title: \'' + matchName + '\', subtitle: \'' + closestSubtitle.replace(/'/g, "\\'") + '\' } }))" '
                + 'onerror="this.style.display=\'none\'">'
                + '<span class="text-xs" style="color:' + labelColor + '">' + closestSubtitle + '</span>'
                + '</div>';
        }
    }

    var imgUrl = logo.image_url || '';
    var imgPlaceholderId = 'logo-img-' + logo.image_id;

    var simColor = simPct >= 70 ? 'var(--color-risk-critical-text)' : simPct >= 50 ? 'var(--color-risk-medium-text)' : 'var(--color-risk-low-text)';
    var scoreHtml = auditDone
        ? '<span class="text-right leading-tight" style="color:' + simColor + '"><span class="block text-xs font-medium">' + t('studio.ai_risk_score_short') + '</span><span class="text-sm font-bold">' + simPct + '%</span></span>'
        : '<span class="text-xs font-semibold" style="color:var(--color-text-muted)">' + t(auditPending ? 'studio.audit_preview_only' : 'studio.audit_failed_short') + '</span>';
    var cardClasses = 'studio-logo-card';
    if (logo.is_revision_target) cardClasses += ' is-revision-target';
    var actions = '';
    if (auditPending) {
        actions = '<span class="studio-logo-action-note">' + t('studio.audit_pending_note') + '</span>';
    } else if (auditFailed) {
        actions = '<button onclick="retryLogoAudit(\'' + escapeHtml(logo.image_id) + '\')" class="studio-logo-action-button btn-press">' + t('studio.retry_audit') + '</button>';
    } else {
        actions = '<button onclick="chooseLogoForRevision(\'' + escapeHtml(logo.image_id) + '\')" class="studio-logo-action-button btn-press">' + t('studio.revise_btn') + '</button>';
        if (canDownload) {
            actions += '<button onclick="downloadLogo(\'' + escapeHtml(logo.image_id) + '\')" class="studio-logo-action-button is-primary btn-press">'
                + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>'
                + t('studio.download_png') + '</button>';
        } else {
            actions += '<span class="studio-logo-action-note">' + t('studio.risky_download_blocked') + '</span>';
        }
        actions += '<button onclick="toggleLogoDetail(\'' + escapeHtml(logo.image_id) + '\')" class="studio-logo-action-button btn-press">' + t('studio.detail_btn') + '</button>';
    }

    // Style badge — shows which canonical style this candidate represents
    // (Modern / Classic / Bold / Playful). Only renders for new logos that
    // have a style stored; legacy rows without one degrade gracefully.
    var styleBadge = '';
    if (logo.style) {
        var styleKey = 'studio.style_' + String(logo.style).toLowerCase();
        styleBadge = '<span class="studio-style-badge">' + escapeHtml(t(styleKey)) + '</span>';
    }

    var html = '<div data-studio-logo-card="true" class="' + cardClasses + '">'
        // Logo image area — loaded via JS fetch for auth
        + '<div id="' + imgPlaceholderId + '" class="studio-logo-image-frame">'
        + '<div class="animate-spin w-8 h-8 rounded-full" style="border:4px solid var(--color-border);border-top-color:var(--color-primary)"></div>'
        + '</div>'
        // Info area
        + '<div class="studio-logo-card-body">'
        + '<div class="flex items-center justify-between mb-1 gap-2 flex-wrap">'
        + '<div class="flex items-center gap-1.5 flex-wrap">' + safetyBadge + styleBadge + '</div>'
        + scoreHtml
        + '</div>'
        + closestHtml
        // Actions
        + '<div class="studio-logo-actions">'
        + actions
        + '</div>'
        + '</div>'
        + '</div>';

    return html;
};

// ============================================
// C) Helper: download logo
// ============================================
window.AppComponents.downloadLogo = function(imageId) {
    var logo = (typeof _studioLogos !== 'undefined' && _studioLogos) ? _studioLogos[imageId] : null;
    if (logo && ((logo.audit_status || 'completed') !== 'completed' || !logo.is_safe)) {
        showToast(t('studio.download_requires_safe_audit'), 'error');
        return;
    }
    var url = '/api/v1/tools/generated-image/' + imageId;
    var a = document.createElement('a');
    a.href = url;
    a.download = 'logo_' + imageId.substring(0, 8) + '.png';

    // Fetch with auth then trigger download
    fetch(url, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    }).then(function(res) {
        if (!res.ok) throw new Error('Download failed');
        return res.blob();
    }).then(function(blob) {
        var blobUrl = window.URL.createObjectURL(blob);
        a.href = blobUrl;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(blobUrl);
    }).catch(function(err) {
        showToast(t('studio.logo_download_failed') + ': ' + err.message, 'error');
    });
};

// ============================================
// D) Load logo images with auth headers
// ============================================
window.AppComponents.loadLogoImages = function(logos) {
    logos.forEach(function(logo) {
        if (!logo.image_url || !logo.image_id) return;
        var container = document.getElementById('logo-img-' + logo.image_id);
        if (!container) return;

        fetch(logo.image_url, {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        }).then(function(res) {
            if (!res.ok) throw new Error('Failed');
            return res.blob();
        }).then(function(blob) {
            if (container.dataset.blobUrl) {
                window.URL.revokeObjectURL(container.dataset.blobUrl);
            }
            var url = window.URL.createObjectURL(blob);
            container.dataset.blobUrl = url;
            container.innerHTML = '<img src="' + url + '" alt="' + t('studio.logo_studio') + '" class="max-w-full max-h-full object-contain">';
        }).catch(function() {
            container.innerHTML = '<div class="text-center" style="color:var(--color-text-faint)">'
                + '<svg class="w-12 h-12 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
                + '<p class="text-xs mt-1">' + t('dashboard.load_failed_short') + '</p></div>';
        });
    });
};

// ============================================
// E) Skeleton cards for loading states
// ============================================
window.AppComponents.renderSkeletonCards = function(count, type) {
    count = count || 3;
    type = type || 'result';
    var html = '';
    for (var i = 0; i < count; i++) {
        if (type === 'logo') {
            html += '<div class="rounded-xl overflow-hidden" style="background:var(--color-bg-card);border:1px solid var(--color-border)">'
                + '<div class="skeleton aspect-square"></div>'
                + '<div class="p-4 space-y-2"><div class="skeleton h-4 w-2/3 rounded"></div><div class="skeleton h-3 w-1/2 rounded"></div></div></div>';
        } else {
            html += '<div class="card-base p-5 space-y-3" style="animation-delay:' + (i * 100) + 'ms">'
                + '<div class="flex justify-between"><div class="skeleton h-5 w-1/3 rounded"></div><div class="skeleton h-10 w-10 rounded-full"></div></div>'
                + '<div class="skeleton h-3 w-2/3 rounded"></div>'
                + '<div class="skeleton h-3 w-1/2 rounded"></div>'
                + '<div class="flex gap-2"><div class="skeleton h-6 w-16 rounded"></div><div class="skeleton h-6 w-16 rounded"></div></div></div>';
        }
    }
    return html;
};

// Expose as globals
var renderNameCard = window.AppComponents.renderNameCard;
var renderLogoCard = window.AppComponents.renderLogoCard;
var downloadLogo = window.AppComponents.downloadLogo;
var loadLogoImages = window.AppComponents.loadLogoImages;

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
          + 'class="text-xs text-purple-600 hover:text-purple-800 font-medium mt-2 flex items-center gap-1 btn-press">'
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

    return window.AppComponents.renderCardShell(inner, { noMargin: true, riskLevel: riskLevel });
};

// ============================================
// B) Logo result card
// ============================================
window.AppComponents.renderLogoCard = function(logo) {
    var simPct = Math.round(logo.similarity_score || 0);
    var isSafe = logo.is_safe;

    var safetyBadge = isSafe
        ? '<span class="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full" style="' + window.AppComponents.getScoreColor(20) + '">&#x2713; ' + t('studio.safe_badge') + '</span>'
        : '<span class="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full" style="' + window.AppComponents.getScoreColor(90) + '">&#x26a0; ' + t('studio.risk_exists') + '</span>';

    var closestHtml = '';
    if (logo.closest_match_name) {
        closestHtml = '<div class="text-xs mt-1" style="color:var(--color-text-muted)">'
            + t('studio.closest_match_full') + ' <span class="font-medium" style="color:var(--color-text-secondary)">' + escapeHtml(logo.closest_match_name) + '</span>'
            + ' <span style="color:var(--color-text-faint)">(' + t('studio.similarity_pct', { pct: simPct }) + ')</span>'
            + '</div>';

        // Show closest match image if unsafe (clickable for lightbox)
        if (!isSafe && logo.closest_match_image_url) {
            var matchUrl = logo.closest_match_image_url;
            var matchName = (logo.closest_match_name || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
            closestHtml += '<div class="mt-2 flex items-center gap-2 rounded-lg p-2" style="background:var(--color-risk-critical-bg);border:1px solid var(--color-risk-critical-border)">'
                + '<img src="' + matchUrl + '" alt="' + t('studio.similar_brand') + '" '
                + 'class="w-10 h-10 object-contain rounded cursor-pointer hover:ring-2 hover:ring-red-300 transition" '
                + 'style="background:var(--color-bg-card);border:1px solid var(--color-risk-critical-border)" '
                + 'onclick="window.dispatchEvent(new CustomEvent(\'open-lightbox\', { detail: { src: \'' + matchUrl.replace(/'/g, "\\'") + '\', title: \'' + matchName + '\', subtitle: \'' + t('studio.similar_brand').replace(/'/g, "\\'") + '\' } }))" '
                + 'onerror="this.style.display=\'none\'">'
                + '<span class="text-xs" style="color:var(--color-risk-critical-text)">' + t('studio.similar_brand') + '</span>'
                + '</div>';
        }
    }

    var imgUrl = logo.image_url || '';
    var imgPlaceholderId = 'logo-img-' + logo.image_id;

    var simColor = simPct >= 70 ? 'var(--color-risk-critical-text)' : simPct >= 50 ? 'var(--color-risk-medium-text)' : 'var(--color-risk-low-text)';

    var html = '<div class="rounded-xl shadow-sm hover:shadow-md transition-all overflow-hidden" style="background:var(--color-bg-card);border:1px solid var(--color-border)">'
        // Logo image area — loaded via JS fetch for auth
        + '<div id="' + imgPlaceholderId + '" class="aspect-square flex items-center justify-center p-4" style="background:var(--color-bg-muted);border-bottom:1px solid var(--color-border)">'
        + '<div class="animate-spin w-8 h-8 border-4 border-purple-200 border-t-purple-600 rounded-full"></div>'
        + '</div>'
        // Info area
        + '<div class="p-4">'
        + '<div class="flex items-center justify-between mb-1">'
        + safetyBadge
        + '<span class="text-sm font-bold" style="color:' + simColor + '">' + simPct + '%</span>'
        + '</div>'
        + closestHtml
        // Actions
        + '<div class="flex items-center gap-2 mt-3">'
        + '<button onclick="downloadLogo(\'' + escapeHtml(logo.image_id) + '\')" '
        + 'class="flex-1 text-xs px-3 py-1.5 rounded-lg flex items-center justify-center gap-1 font-medium transition-colors btn-press min-h-[36px]" style="background:var(--color-bg-muted);color:var(--color-text-secondary);border:1px solid var(--color-border)">'
        + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>'
        + t('studio.download_png') + '</button>'
        + '<button onclick="toggleLogoDetail(\'' + escapeHtml(logo.image_id) + '\')" '
        + 'class="text-xs px-3 py-1.5 rounded-lg font-medium transition-colors btn-press min-h-[36px]" style="background:var(--color-bg-muted);color:var(--color-text-secondary);border:1px solid var(--color-border)">'
        + t('studio.detail_btn') + '</button>'
        + '</div>'
        + '</div>'
        + '</div>';

    return html;
};

// ============================================
// C) Helper: download logo
// ============================================
window.AppComponents.downloadLogo = function(imageId) {
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
            var url = window.URL.createObjectURL(blob);
            container.innerHTML = '<img src="' + url + '" alt="Logo" class="max-w-full max-h-full object-contain">';
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

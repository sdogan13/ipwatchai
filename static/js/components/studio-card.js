/**
 * studio-card.js - AI Studio card rendering (Name Lab + Logo Studio)
 * Uses shared helpers from score-badge.js
 */
window.AppComponents = window.AppComponents || {};

// ============================================
// A) Name suggestion card
// ============================================
window.AppComponents.renderNameCard = function(name, index) {
    var riskPct = Math.round(name.risk_score || 0);
    var isSafe = name.is_safe;
    var safetyBadge = isSafe
        ? '<span class="inline-flex items-center gap-1 text-xs font-medium bg-green-100 text-green-700 px-2 py-0.5 rounded-full border border-green-200">&#x2713; Guvenli</span>'
        : '<span class="inline-flex items-center gap-1 text-xs font-medium bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full border border-amber-200">&#x26a0; Dikkat</span>';

    var scoreBadge = window.AppComponents.renderScoreBadge(riskPct, 'Risk');

    // Risk level badge with color
    var riskLevelHtml = '';
    if (name.risk_level && name.risk_level !== 'low') {
        var rlColors = {
            'critical': 'bg-red-600 text-white',
            'very_high': 'bg-red-100 text-red-700 border border-red-200',
            'high': 'bg-orange-100 text-orange-700 border border-orange-200',
            'medium': 'bg-amber-100 text-amber-700 border border-amber-200'
        };
        var rlLabels = { 'critical': 'Kritik', 'very_high': 'Cok Yuksek', 'high': 'Yuksek', 'medium': 'Orta' };
        var rlClass = rlColors[name.risk_level] || 'bg-gray-100 text-gray-600';
        var rlLabel = rlLabels[name.risk_level] || name.risk_level;
        riskLevelHtml = '<span class="inline-flex items-center text-xs font-medium px-2 py-0.5 rounded-full ' + rlClass + '">' + rlLabel + '</span>';
    }

    var closestHtml = '';
    if (name.closest_match) {
        closestHtml = '<div class="text-xs text-gray-500 mt-1.5">'
            + 'En yakin: <span class="font-medium text-gray-700">' + escapeHtml(name.closest_match) + '</span>';
        var maxSim = Math.max(name.text_similarity || 0, name.semantic_similarity || 0);
        if (maxSim > 0) {
            closestHtml += ' <span class="text-gray-400">(%' + Math.round(maxSim * 100) + ' benzerlik)</span>';
        }
        closestHtml += '</div>';
    }

    var badgesHtml = window.AppComponents.renderSimilarityBadges(name);
    // Phonetic match badge (binary signal, shown separately)
    if (name.phonetic_match) {
        badgesHtml += '<div class="mt-1"><span class="text-xs bg-red-100 text-red-600 px-1.5 py-0.5 rounded">Fonetik Eslesme</span></div>';
    }

    var useForLogoBtn = isSafe
        ? '<button onclick="useNameForLogo(\'' + escapeHtml(name.name).replace(/'/g, "\\'") + '\')" '
          + 'class="text-xs text-purple-600 hover:text-purple-800 font-medium mt-2 flex items-center gap-1">'
          + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
          + 'Logo Olustur</button>'
        : '';

    var inner = '<div class="flex justify-between items-start gap-3">'
        + '<div class="flex-1 min-w-0">'
        + '<div class="flex items-center gap-2 mb-1">'
        + '<span class="text-lg font-bold text-gray-900">' + escapeHtml(name.name) + '</span>'
        + safetyBadge
        + riskLevelHtml
        + '</div>'
        + closestHtml
        + badgesHtml
        + useForLogoBtn
        + '</div>'
        + '<div class="flex-shrink-0">' + scoreBadge + '</div>'
        + '</div>';

    return window.AppComponents.renderCardShell(inner, { noMargin: true });
};

// ============================================
// B) Logo result card
// ============================================
window.AppComponents.renderLogoCard = function(logo) {
    var simPct = Math.round(logo.similarity_score || 0);
    var isSafe = logo.is_safe;

    var safetyBadge = isSafe
        ? '<span class="inline-flex items-center gap-1 text-xs font-medium bg-green-100 text-green-700 px-2 py-0.5 rounded-full border border-green-200">&#x2713; Guvenli</span>'
        : '<span class="inline-flex items-center gap-1 text-xs font-medium bg-red-100 text-red-700 px-2 py-0.5 rounded-full border border-red-200">&#x26a0; Risk Var</span>';

    var closestHtml = '';
    if (logo.closest_match_name) {
        closestHtml = '<div class="text-xs text-gray-500 mt-1">'
            + 'En yakin esleme: <span class="font-medium text-gray-700">' + escapeHtml(logo.closest_match_name) + '</span>'
            + ' <span class="text-gray-400">(%' + simPct + ' benzerlik)</span>'
            + '</div>';

        // Show closest match image if unsafe (clickable for lightbox)
        if (!isSafe && logo.closest_match_image_url) {
            var matchUrl = logo.closest_match_image_url;
            var matchName = (logo.closest_match_name || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
            closestHtml += '<div class="mt-2 flex items-center gap-2 bg-red-50 rounded-lg p-2 border border-red-100">'
                + '<img src="' + matchUrl + '" alt="Benzer marka" '
                + 'class="w-10 h-10 object-contain rounded border border-red-200 bg-white cursor-pointer hover:ring-2 hover:ring-red-300 transition" '
                + 'onclick="window.dispatchEvent(new CustomEvent(\'open-lightbox\', { detail: { src: \'' + matchUrl.replace(/'/g, "\\'") + '\', title: \'' + matchName + '\', subtitle: \'Benzer mevcut marka\' } }))" '
                + 'onerror="this.style.display=\'none\'">'
                + '<span class="text-xs text-red-600">Benzer mevcut marka</span>'
                + '</div>';
        }
    }

    var imgUrl = logo.image_url || '';
    var imgPlaceholderId = 'logo-img-' + logo.image_id;

    var html = '<div class="bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-all overflow-hidden">'
        // Logo image area — loaded via JS fetch for auth
        + '<div id="' + imgPlaceholderId + '" class="aspect-square bg-gray-50 flex items-center justify-center p-4 border-b border-gray-100">'
        + '<div class="animate-spin w-8 h-8 border-4 border-purple-200 border-t-purple-600 rounded-full"></div>'
        + '</div>'
        // Info area
        + '<div class="p-4">'
        + '<div class="flex items-center justify-between mb-1">'
        + safetyBadge
        + '<span class="text-sm font-bold ' + (simPct >= 70 ? 'text-red-600' : simPct >= 50 ? 'text-amber-600' : 'text-green-600') + '">' + simPct + '%</span>'
        + '</div>'
        + closestHtml
        // Actions
        + '<div class="flex items-center gap-2 mt-3">'
        + '<button onclick="downloadLogo(\'' + escapeHtml(logo.image_id) + '\')" '
        + 'class="flex-1 text-xs px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg flex items-center justify-center gap-1 font-medium transition-colors">'
        + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>'
        + 'PNG Indir</button>'
        + '<button onclick="toggleLogoDetail(\'' + escapeHtml(logo.image_id) + '\')" '
        + 'class="text-xs px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg font-medium transition-colors">'
        + 'Detay</button>'
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
        showToast('Logo indirilemedi: ' + err.message, 'error');
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
            container.innerHTML = '<div class="text-gray-300 text-center">'
                + '<svg class="w-12 h-12 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
                + '<p class="text-xs mt-1">Yuklenemedi</p></div>';
        });
    });
};

// Expose as globals
var renderNameCard = window.AppComponents.renderNameCard;
var renderLogoCard = window.AppComponents.renderLogoCard;
var downloadLogo = window.AppComponents.downloadLogo;
var loadLogoImages = window.AppComponents.loadLogoImages;

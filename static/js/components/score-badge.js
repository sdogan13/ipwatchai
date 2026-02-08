/**
 * score-badge.js - Shared risk score color/badge rendering
 * Single source of truth for all score-related UI
 */
window.AppComponents = window.AppComponents || {};

// ============================================
// A) getScoreColor - Single source of truth for risk color thresholds
//    Matches backend RISK_THRESHOLDS exactly (5 levels):
//    critical >= 90, very_high >= 80, high >= 70, medium >= 50, low < 50
// ============================================
window.AppComponents.getScoreColor = function(pct) {
    if (pct >= 90) return 'bg-red-100 text-red-800 border-red-200';
    if (pct >= 80) return 'bg-orange-100 text-orange-800 border-orange-200';
    if (pct >= 70) return 'bg-amber-100 text-amber-800 border-amber-200';
    if (pct >= 50) return 'bg-yellow-100 text-yellow-800 border-yellow-200';
    return 'bg-green-100 text-green-800 border-green-200';
};

window.AppComponents.getRiskColorClass = function(riskLevel) {
    riskLevel = (riskLevel || '').toUpperCase();
    if (riskLevel === 'CRITICAL') return 'text-red-600';
    if (riskLevel === 'VERY_HIGH') return 'text-orange-600';
    if (riskLevel === 'HIGH') return 'text-amber-600';
    if (riskLevel === 'MEDIUM') return 'text-yellow-600';
    return 'text-green-600';
};

window.AppComponents.getRiskBadgeColor = function(riskLevel) {
    riskLevel = (riskLevel || '').toUpperCase();
    if (riskLevel === 'CRITICAL') return 'bg-red-100 text-red-700 border-red-200';
    if (riskLevel === 'VERY_HIGH') return 'bg-orange-100 text-orange-700 border-orange-200';
    if (riskLevel === 'HIGH') return 'bg-amber-100 text-amber-700 border-amber-200';
    if (riskLevel === 'MEDIUM') return 'bg-yellow-100 text-yellow-700 border-yellow-200';
    return 'bg-green-100 text-green-700 border-green-200';
};

window.AppComponents.getRiskBadgeSmall = function(riskLevel) {
    riskLevel = (riskLevel || '').toUpperCase();
    if (riskLevel === 'CRITICAL') return 'bg-red-100 text-red-700';
    if (riskLevel === 'VERY_HIGH') return 'bg-orange-100 text-orange-700';
    if (riskLevel === 'HIGH') return 'bg-amber-100 text-amber-700';
    return 'bg-yellow-100 text-yellow-700';
};

// ============================================
// B) renderScoreBadge - Color-coded score badge HTML
// ============================================
window.AppComponents.renderScoreBadge = function(score, label) {
    var pct = Math.round(score);
    var color = window.AppComponents.getScoreColor(pct);
    var html = '<div class="flex-shrink-0 ' + color + ' font-bold px-3 py-1.5 rounded-lg text-lg border">';
    if (label) {
        html += '<span class="text-xs font-normal block leading-none">' + label + '</span>';
    }
    html += pct + '%</div>';
    return html;
};

// ============================================
// C) renderSimilarityBadges - 3-bucket score breakdown badges
//    Metin = max(text_similarity, semantic_similarity) — all text-based signals
//    Gorsel = visual_similarity (CLIP+DINOv2+color+OCR composite)
//    Ceviri = translation_similarity
//    Normalizes field names: accepts scores.X or data.X directly
//    Only shows badges where score > 30%
// ============================================
window.AppComponents.renderSimilarityBadges = function(data) {
    if (!data) return '';
    var scores = data.scores || data;

    var textScore = Math.max(scores.text_similarity || 0, scores.semantic_similarity || 0);
    var visualScore = scores.visual_similarity || 0;
    var translationScore = scores.translation_similarity || 0;

    var html = '<div class="flex gap-2 mt-1.5 flex-wrap">';
    var hasBadge = false;

    if (textScore > 0.3) {
        html += '<span class="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">Metin ' + Math.round(textScore * 100) + '%</span>';
        hasBadge = true;
    }

    if (visualScore > 0.3) {
        html += '<span class="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">Gorsel ' + Math.round(visualScore * 100) + '%</span>';
        hasBadge = true;
    }

    if (translationScore > 0.3) {
        html += '<span class="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded font-medium">Ceviri ' + Math.round(translationScore * 100) + '%</span>';
        hasBadge = true;
    }

    html += '</div>';
    return hasBadge ? html : '';
};

// ============================================
// D) renderCardShell - Common card wrapper
// ============================================
window.AppComponents.renderCardShell = function(innerHtml, opts) {
    opts = opts || {};
    var onclick = opts.onclick ? ' onclick="' + opts.onclick + '"' : '';
    var cursor = opts.onclick ? ' cursor-pointer' : '';
    var mb = opts.noMargin ? '' : ' mb-2';
    return '<div class="bg-white rounded-xl shadow-sm border border-gray-200 p-5 hover:border-indigo-300 hover:shadow-md transition-all' + cursor + mb + '"'
        + onclick + '>'
        + innerHtml
        + '</div>';
};

// ============================================
// D) renderNiceClassBadges - Nice class number badges with smart truncation
// ============================================
window.AppComponents.renderNiceClassBadges = function(classes, maxShow) {
    if (!classes || classes.length === 0) return '';
    maxShow = maxShow || 5;

    var sorted = classes.slice().sort(function(a, b) { return a - b; });
    var visible = sorted.slice(0, maxShow);
    var remaining = sorted.length - maxShow;

    var html = '<div class="flex flex-wrap gap-1 mt-1">';
    visible.forEach(function(cls) {
        html += '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-600">' + cls + '</span>';
    });

    if (remaining > 0) {
        var extraBadges = sorted.slice(maxShow).map(function(cls) {
            return '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-600">' + cls + '</span>';
        }).join('');
        html += '<span class="cursor-pointer text-xs text-blue-600 hover:text-blue-800 px-1" '
            + 'onclick="this.outerHTML=\'' + extraBadges.replace(/'/g, "\\'") + '\'">+' + remaining + ' daha</span>';
    }

    html += '</div>';
    return html;
};
var renderNiceClassBadges = window.AppComponents.renderNiceClassBadges;

// ============================================
// Image thumbnail placeholder SVG
// ============================================
window.AppComponents.IMG_PLACEHOLDER_SVG = '<svg class="w-6 h-6 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
    + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>'
    + '</svg>';

// Render a clickable thumbnail that opens lightbox, with fallback
// @param imagePath - image_path from API
// @param name - trademark name (lightbox title)
// @param appNo - application number (lightbox subtitle)
// @param size - Tailwind size class (default: 'w-12 h-12')
window.AppComponents.renderThumbnail = function(imagePath, name, appNo, size) {
    size = size || 'w-12 h-12';
    var placeholder = '<div class="' + size + ' bg-gray-50 rounded border border-gray-200 flex items-center justify-center flex-shrink-0">'
        + window.AppComponents.IMG_PLACEHOLDER_SVG + '</div>';

    if (!imagePath) return placeholder;

    var url = '/api/trademark-image/' + encodeURIComponent(imagePath);
    var escapedName = (name || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    var escapedAppNo = (appNo || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    var placeholderEscaped = window.AppComponents.IMG_PLACEHOLDER_SVG.replace(/'/g, "\\'");

    return '<div class="' + size + ' bg-gray-50 rounded border border-gray-200 flex items-center justify-center flex-shrink-0 overflow-hidden cursor-pointer hover:ring-2 hover:ring-blue-300 transition" '
        + 'onclick="window.dispatchEvent(new CustomEvent(\'open-lightbox\', { detail: { src: \'' + url.replace(/'/g, "\\'") + '\', title: \'' + escapedName + '\', subtitle: \'' + escapedAppNo + '\' } }))">'
        + '<img src="' + url + '" alt="' + (name || '').replace(/"/g, '&quot;') + '" class="w-full h-full object-contain"'
        + ' onerror="this.style.display=\'none\'; this.parentElement.innerHTML=\'' + placeholderEscaped + '\'; this.parentElement.style.cursor=\'default\'; this.parentElement.onclick=null;">'
        + '</div>';
};

// ============================================
// F) renderTurkpatentButton - TURKPATENT Dosya Takibi copy+link
//    Shows copy-to-clipboard button + link to TURKPATENT login
// ============================================
window.AppComponents.renderTurkpatentButton = function(applicationNo) {
    if (!applicationNo) return '';
    var safeNo = (applicationNo || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    var btnId = 'tp-' + safeNo.replace(/[^a-zA-Z0-9]/g, '');
    return '<div class="inline-flex items-center gap-1 mt-1">'
        + '<button onclick="event.stopPropagation(); navigator.clipboard.writeText(\'' + safeNo + '\'); '
        + 'var el=document.getElementById(\'' + btnId + '\'); if(el){el.textContent=\'Kopyalandi!\'; setTimeout(function(){el.textContent=\'' + safeNo + '\';},2000);}" '
        + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 cursor-pointer transition-colors" '
        + 'title="Basvuru numarasini kopyala">'
        + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>'
        + '<span id="' + btnId + '">' + escapeHtml(applicationNo) + '</span>'
        + '</button>'
        + '<a href="https://opts.turkpatent.gov.tr/login" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation();" '
        + 'class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs bg-gray-50 text-gray-600 border border-gray-200 hover:bg-gray-100 hover:text-gray-800 transition-colors" '
        + 'title="Basvuru numarasini kopyalayin, TURKPATENT&#39;e tiklayin, e-Devlet ile giris yapin ve numarayi yapistirarak dosyayi goruntuleyin.">'
        + 'TURKPATENT'
        + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>'
        + '</a>'
        + '</div>';
};
var renderTurkpatentButton = window.AppComponents.renderTurkpatentButton;

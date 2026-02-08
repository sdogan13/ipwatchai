/**
 * result-card.js - Search result card rendering
 * Uses shared helpers from score-badge.js
 */
window.AppComponents = window.AppComponents || {};

window.AppComponents.renderHolderLink = function(holderName, holderTpeId) {
    if (!holderName) return '';

    if (!holderTpeId) {
        return '<div class="text-sm text-gray-600 mt-1">'
            + 'Basvuru Sahibi: <span class="text-gray-700">' + escapeHtml(holderName) + '</span>'
            + '</div>';
    }

    var holderDisplay = escapeHtml(holderName)
        + ' <span class="text-gray-400">(' + escapeHtml(holderTpeId) + ')</span>';
    var isPro = (currentUserPlan === 'professional' || currentUserPlan === 'enterprise');

    if (isPro) {
        return '<div class="text-sm text-gray-600 mt-1">'
            + 'Basvuru Sahibi: '
            + '<a href="#" onclick="showHolderPortfolio(\'' + escapeHtml(holderTpeId) + '\', \'' + escapeHtml(holderName) + '\'); return false;" '
            + 'class="text-blue-600 hover:text-blue-800 hover:underline font-medium inline-flex items-center gap-1">'
            + holderDisplay + ' <span class="text-blue-500">&#x1f4cb;</span></a></div>';
    } else {
        return '<div class="text-sm text-gray-600 mt-1">'
            + 'Basvuru Sahibi: '
            + '<span class="text-gray-700 cursor-pointer inline-flex items-center gap-1" '
            + 'title="Sahip portfolyunu goruntulmek icin PRO\'ya yukseltin" '
            + 'onclick="showUpgradeModal()">'
            + holderDisplay + ' <span class="text-gray-400">&#x1f512;</span></span></div>';
    }
};

window.AppComponents.renderResultCard = function(r) {
    var score = getResultScore(r);
    var pct = Math.round(score * 100);

    // Use shared helpers
    var scoreBadge = window.AppComponents.renderScoreBadge(pct);
    var breakdownHtml = window.AppComponents.renderSimilarityBadges(r.scores);
    var thumbnail = window.AppComponents.renderThumbnail(r.image_path, r.bulletin_no);

    // AI Studio conversion CTAs for high-risk results
    var studioCta = '';
    var queryName = r._query_name || '';
    var queryClasses = r._query_classes || [];

    if (pct >= 70 && queryName) {
        var nameCtx = encodeURIComponent(JSON.stringify({ query: queryName, nice_classes: queryClasses }));
        studioCta += '<div class="mt-2 pt-2 border-t border-gray-100">'
            + '<button onclick="openStudioWithContext(\'name\', JSON.parse(decodeURIComponent(\'' + nameCtx + '\')))" '
            + 'class="text-xs text-purple-600 hover:text-purple-800 font-medium flex items-center gap-1">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>'
            + 'Bu isim riskli &mdash; Guvenli alternatif oner</button>';

        // Add logo CTA for visual similarity > 75%
        var visSim = r.scores ? (r.scores.visual_similarity || 0) : 0;
        if (visSim > 0.75) {
            studioCta += '<button onclick="openStudioWithContext(\'logo\', JSON.parse(decodeURIComponent(\'' + nameCtx + '\')))" '
                + 'class="text-xs text-purple-600 hover:text-purple-800 font-medium flex items-center gap-1 mt-1">'
                + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
                + 'Benzer logo &mdash; Ozgun logo olustur</button>';
        }

        studioCta += '</div>';
    }

    // Extracted goods indicator
    var extractedGoodsHtml = '';
    if (r.has_extracted_goods) {
        var safeAppNo = (r.application_no || '').replace(/'/g, "\\'");
        extractedGoodsHtml = '<div class="mt-1.5">'
            + '<button onclick="showExtractedGoods(\'' + safeAppNo + '\', this)" '
            + 'class="inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer transition-colors">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
            + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/>'
            + '</svg>'
            + 'CIKARILMIS URUN: <span class="underline">EVET</span>'
            + '</button></div>';
    }

    var inner = '<div class="flex justify-between items-start gap-4">'
        + '<div class="flex items-start gap-3 flex-1 min-w-0">'
        + thumbnail
        + '<div class="flex-1 min-w-0">'
        + '<div class="font-semibold text-gray-900 truncate">' + (r.name || 'N/A') + '</div>'
        + '<div class="text-sm text-gray-500">' + (r.status || 'N/A') + '</div>'
        + window.AppComponents.renderTurkpatentButton(r.application_no)
        + window.AppComponents.renderHolderLink(r.holder_name, r.holder_tpe_client_id)
        + breakdownHtml
        + extractedGoodsHtml
        + studioCta
        + '</div>'
        + '</div>'
        + scoreBadge
        + '</div>';

    return window.AppComponents.renderCardShell(inner);
};

// Expose as globals
var renderHolderLink = window.AppComponents.renderHolderLink;
var renderResultCard = window.AppComponents.renderResultCard;

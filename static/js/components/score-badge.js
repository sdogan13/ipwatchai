/**
 * score-badge.js - Shared risk score color/badge rendering
 * Single source of truth for all score-related UI.
 * Uses CSS custom properties for dark mode compatibility.
 */
window.AppComponents = window.AppComponents || {};

// ============================================
// A0) Similarity category — 4-bucket scheme used by Opposition Radar and search results.
//     very_high >= 90, high 70-89, medium 50-69, low < 50.
//     Kept separate from getScoreRiskLevel (5-bucket) to give a simpler text-only UI.
//     The top bucket is 'very_high' (not 'critical') so radar urgency badges that use
//     risk_level.critical for "deadline <=7 days" stay visually distinct.
// ============================================
window.AppComponents.getSimilarityCategory = function(pct) {
    if (pct >= 90) return 'very_high';
    if (pct >= 70) return 'high';
    if (pct >= 50) return 'medium';
    return 'low';
};

window.AppComponents.renderSimilarityCategoryBadge = function(pct, opts) {
    opts = opts || {};
    var category = window.AppComponents.getSimilarityCategory(pct);
    var styleMap = {
        very_high: 'background:var(--color-risk-critical-bg);color:var(--color-risk-critical-text);border-color:var(--color-risk-critical-border)',
        high: 'background:var(--color-risk-high-bg);color:var(--color-risk-high-text);border-color:var(--color-risk-high-border)',
        medium: 'background:var(--color-risk-elevated-bg);color:var(--color-risk-elevated-text);border-color:var(--color-risk-elevated-border)',
        low: 'background:var(--color-risk-low-bg);color:var(--color-risk-low-text);border-color:var(--color-risk-low-border)'
    };
    var size = opts.size || 'md';
    var sizeClass = size === 'sm'
        ? 'text-xs px-2 py-0.5'
        : size === 'lg'
            ? 'text-sm px-3 py-1.5'
            : 'text-xs px-2.5 py-1';
    return '<span class="inline-flex items-center rounded-full font-semibold border whitespace-nowrap ' + sizeClass + '" '
        + 'style="' + styleMap[category] + '">'
        + t('risk_level.' + category)
        + '</span>';
};

// ============================================
// A) getScoreColor - Returns inline style string for risk level
//    Uses CSS variables so dark mode works automatically.
//    Matches backend RISK_THRESHOLDS exactly (5 levels):
//    critical >= 90, very_high >= 80, high >= 70, medium >= 50, low < 50
// ============================================
window.AppComponents.getScoreColor = function(pct) {
    if (pct >= 90) return 'background:var(--color-risk-critical-bg);color:var(--color-risk-critical-text);border-color:var(--color-risk-critical-border)';
    if (pct >= 80) return 'background:var(--color-risk-high-bg);color:var(--color-risk-high-text);border-color:var(--color-risk-high-border)';
    if (pct >= 70) return 'background:var(--color-risk-medium-bg);color:var(--color-risk-medium-text);border-color:var(--color-risk-medium-border)';
    if (pct >= 50) return 'background:var(--color-risk-elevated-bg);color:var(--color-risk-elevated-text);border-color:var(--color-risk-elevated-border)';
    return 'background:var(--color-risk-low-bg);color:var(--color-risk-low-text);border-color:var(--color-risk-low-border)';
};

// Returns the risk level string for a given percentage
window.AppComponents.getScoreRiskLevel = function(pct) {
    if (pct >= 90) return 'critical';
    if (pct >= 80) return 'high';
    if (pct >= 70) return 'medium';
    if (pct >= 50) return 'elevated';
    return 'low';
};

// Returns CSS class for Tailwind text color (used by Alpine.js :class bindings)
window.AppComponents.getScoreColorClass = function(pct) {
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
    if (riskLevel === 'CRITICAL') return 'background:var(--color-risk-critical-bg);color:var(--color-risk-critical-text);border-color:var(--color-risk-critical-border)';
    if (riskLevel === 'VERY_HIGH') return 'background:var(--color-risk-high-bg);color:var(--color-risk-high-text);border-color:var(--color-risk-high-border)';
    if (riskLevel === 'HIGH') return 'background:var(--color-risk-medium-bg);color:var(--color-risk-medium-text);border-color:var(--color-risk-medium-border)';
    if (riskLevel === 'MEDIUM') return 'background:var(--color-risk-elevated-bg);color:var(--color-risk-elevated-text);border-color:var(--color-risk-elevated-border)';
    return 'background:var(--color-risk-low-bg);color:var(--color-risk-low-text);border-color:var(--color-risk-low-border)';
};

window.AppComponents.getRiskBadgeSmall = function(riskLevel) {
    riskLevel = (riskLevel || '').toUpperCase();
    if (riskLevel === 'CRITICAL') return 'background:var(--color-risk-critical-bg);color:var(--color-risk-critical-text)';
    if (riskLevel === 'VERY_HIGH') return 'background:var(--color-risk-high-bg);color:var(--color-risk-high-text)';
    if (riskLevel === 'HIGH') return 'background:var(--color-risk-medium-bg);color:var(--color-risk-medium-text)';
    return 'background:var(--color-risk-elevated-bg);color:var(--color-risk-elevated-text)';
};

// ============================================
// B) renderScoreRing - SVG ring + number score badge
// ============================================
window.AppComponents.renderScoreRing = function(pct, size) {
    size = size || 44;
    var r = (size - 6) / 2;
    var circ = 2 * Math.PI * r;
    var offset = circ - (pct / 100) * circ;
    var riskLevel = window.AppComponents.getScoreRiskLevel(pct);
    var strokeColors = {
        critical: 'var(--color-risk-critical-text)',
        high: 'var(--color-risk-high-text)',
        medium: 'var(--color-risk-medium-text)',
        elevated: 'var(--color-risk-elevated-text)',
        low: 'var(--color-risk-low-text)'
    };
    var strokeColor = strokeColors[riskLevel] || strokeColors.low;
    var fontSize = size <= 32 ? '10' : '13';

    return '<svg width="' + size + '" height="' + size + '" class="score-ring flex-shrink-0">'
        + '<circle cx="' + (size/2) + '" cy="' + (size/2) + '" r="' + r + '" class="score-ring-track" stroke-width="3"/>'
        + '<circle cx="' + (size/2) + '" cy="' + (size/2) + '" r="' + r + '" class="score-ring-fill" '
        + 'stroke="' + strokeColor + '" stroke-width="3" '
        + 'stroke-dasharray="' + circ + '" stroke-dashoffset="' + offset + '"/>'
        + '<text x="50%" y="50%" text-anchor="middle" dominant-baseline="central" '
        + 'font-size="' + fontSize + '" font-weight="700" fill="' + strokeColor + '" '
        + 'style="transform:rotate(90deg);transform-origin:center">'
        + pct + '</text>'
        + '</svg>';
};

// ============================================
// C) renderScoreBadge - Color-coded score badge HTML
//    Now uses inline styles with CSS variables for dark mode support
// ============================================
window.AppComponents.renderScoreBadge = function(score, label) {
    var pct = Math.round(score);
    var colorStyle = window.AppComponents.getScoreColor(pct);
    var html = '<div class="flex-shrink-0 font-bold px-3 py-1.5 rounded-lg text-lg border" style="' + colorStyle + '">';
    if (label) {
        html += '<span class="text-xs font-normal block leading-none">' + label + '</span>';
    }
    html += pct + '%</div>';
    return html;
};

window.AppComponents.getOriginalTextScore = function(data) {
    var scores = data && data.scores ? data.scores : (data || {});
    var textualBreakdown = scores.textual_breakdown || {};
    var selectedSource = scores.scoring_path_source || textualBreakdown.selected_path || '';
    var candidates = [
        scores.path_a_score,
        textualBreakdown.path_a_score,
        scores.original_text_score,
        scores.direct_text_similarity,
        scores.text_similarity
    ];

    if (String(selectedSource).toUpperCase() !== 'TRANSLATED') {
        candidates.push(scores.text_idf_score);
        candidates.push(textualBreakdown.selected_text_score);
    }

    for (var i = 0; i < candidates.length; i++) {
        if (candidates[i] !== undefined && candidates[i] !== null && candidates[i] !== '') {
            var n = parseFloat(candidates[i]);
            if (!isNaN(n)) return Math.max(0, Math.min(1, n));
        }
    }
    return 0;
};

window.AppComponents.getEffectiveTextScore = window.AppComponents.getOriginalTextScore;

// ============================================
// D) renderSimilarityBadges - Multi-dimension score breakdown with mini progress bars
//    Shows all non-zero scoring dimensions from the backend
// ============================================
window.AppComponents.renderSimilarityBadges = function(data) {
    if (!data) return '';
    var scores = data.scores || data;

    var textScore = window.AppComponents.getOriginalTextScore(scores);
    var visualScore = scores.visual_similarity || 0;
    var translationScore = scores.translation_similarity || 0;
    var phoneticScore = scores.phonetic_similarity || 0;
    var semanticScore = scores.semantic_similarity || 0;
    var containmentScore = scores.containment || 0;

    var html = '<div class="flex gap-2 mt-1.5 flex-wrap">';
    var hasBadge = false;

    function miniBar(label, value) {
        var pct = Math.round(value * 100);
        var barColor = pct >= 70 ? 'var(--color-risk-critical-text)' : pct >= 50 ? 'var(--color-risk-medium-text)' : 'var(--color-risk-low-text)';
        return '<span class="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-md" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">'
            + '<span>' + label + '</span>'
            + '<span class="inline-block w-12 h-1.5 rounded-full" style="background:var(--color-border)">'
            + '<span class="block h-full rounded-full" style="width:' + pct + '%;background:' + barColor + '"></span>'
            + '</span>'
            + '<span class="font-medium" style="color:var(--color-text-primary)">' + pct + '%</span>'
            + '</span>';
    }

    // Primary dimensions (always show if above threshold)
    if (textScore > 0.3) {
        html += miniBar(t('scores.text'), textScore);
        hasBadge = true;
    }

    if (visualScore > 0.3) {
        html += miniBar(t('scores.visual'), visualScore);
        hasBadge = true;
    }

    if (translationScore > 0.3) {
        html += miniBar(t('scores.translation'), translationScore);
        hasBadge = true;
    }

    // Secondary dimensions (show if meaningful and different from text)
    if (phoneticScore > 0.3 && Math.abs(phoneticScore - textScore) > 0.05) {
        html += miniBar(t('scores.phonetic'), phoneticScore);
        hasBadge = true;
    }

    if (semanticScore > 0.3 && Math.abs(semanticScore - textScore) > 0.1) {
        html += miniBar(t('scores.semantic'), semanticScore);
        hasBadge = true;
    }

    if (containmentScore > 0.3 && containmentScore < 1.0 && Math.abs(containmentScore - textScore) > 0.1) {
        html += miniBar(t('scores.containment'), containmentScore);
        hasBadge = true;
    }

    var tokenOverlapScore = scores.token_overlap || 0;
    if (tokenOverlapScore > 0.3 && tokenOverlapScore < 1.0 && Math.abs(tokenOverlapScore - textScore) > 0.1) {
        html += miniBar(t('scores.token_overlap'), tokenOverlapScore);
        hasBadge = true;
    }

    html += '</div>';
    return hasBadge ? html : '';
};

// ============================================
// D1b) renderSimilarityCategoryBreakdown - Per-component category badges
//      Same dimensions as renderSimilarityBadges but emits text categories
//      (Critical / High / Medium / Low) instead of numeric percentages.
// ============================================
window.AppComponents.renderSimilarityCategoryBreakdown = function(data) {
    if (!data) return '';
    var scores = data.scores || data;

    var textScore = window.AppComponents.getOriginalTextScore(scores);
    var visualScore = scores.visual_similarity || 0;
    var translationScore = scores.translation_similarity || 0;
    var phoneticScore = scores.phonetic_similarity || 0;
    var semanticScore = scores.semantic_similarity || 0;
    var containmentScore = scores.containment || 0;

    function chip(label, value) {
        var pct = Math.round(value * 100);
        var category = window.AppComponents.getSimilarityCategory(pct);
        var colorMap = {
            very_high: 'var(--color-risk-critical-text)',
            high: 'var(--color-risk-high-text)',
            medium: 'var(--color-risk-medium-text)',
            low: 'var(--color-risk-low-text)'
        };
        return '<span class="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-md" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">'
            + '<span>' + label + ':</span>'
            + '<span class="font-semibold" style="color:' + colorMap[category] + '">' + t('risk_level.' + category) + '</span>'
            + '</span>';
    }

    var html = '<div class="flex gap-2 mt-1.5 flex-wrap">';
    var hasBadge = false;

    if (textScore > 0.3) { html += chip(t('scores.text'), textScore); hasBadge = true; }
    if (visualScore > 0.3) { html += chip(t('scores.visual'), visualScore); hasBadge = true; }
    if (translationScore > 0.3) { html += chip(t('scores.translation'), translationScore); hasBadge = true; }
    if (phoneticScore > 0.3 && Math.abs(phoneticScore - textScore) > 0.05) {
        html += chip(t('scores.phonetic'), phoneticScore); hasBadge = true;
    }
    if (semanticScore > 0.3 && Math.abs(semanticScore - textScore) > 0.1) {
        html += chip(t('scores.semantic'), semanticScore); hasBadge = true;
    }
    if (containmentScore > 0.3 && containmentScore < 1.0 && Math.abs(containmentScore - textScore) > 0.1) {
        html += chip(t('scores.containment'), containmentScore); hasBadge = true;
    }
    var tokenOverlapScore = scores.token_overlap || 0;
    if (tokenOverlapScore > 0.3 && tokenOverlapScore < 1.0 && Math.abs(tokenOverlapScore - textScore) > 0.1) {
        html += chip(t('scores.token_overlap'), tokenOverlapScore); hasBadge = true;
    }

    html += '</div>';
    return hasBadge ? html : '';
};

// ============================================
// D2) renderScoringPathBadge - Shows the scoring algorithm path used
// ============================================
window.AppComponents.renderScoringPathBadge = function(scores) {
    if (!scores || !scores.scoring_path) return '';

    var path = scores.scoring_path;
    var pathLabels = {
        'EXACT_MATCH': t('scores.path_exact'),
        'HIGH_SIMILARITY': t('scores.path_high_sim'),
        'PARTIAL_MATCH': t('scores.path_partial'),
        'SEMANTIC_MATCH': t('scores.path_semantic')
    };
    var label = pathLabels[path] || t('scores.path_default');

    var bgStyle = path === 'EXACT_MATCH'
        ? 'background:var(--color-risk-critical-bg);color:var(--color-risk-critical-text)'
        : 'background:var(--color-bg-muted);color:var(--color-text-secondary)';

    return '<span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium" style="' + bgStyle + '">'
        + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/></svg>'
        + label + '</span>';
};

// ============================================
// D3) renderDynamicWeights - Shows the dynamic weight distribution as tiny label
// ============================================
window.AppComponents.renderDynamicWeights = function(scores) {
    if (!scores || !scores.dynamic_weights) return '';
    var w = scores.dynamic_weights;
    var textW = Math.round((w.text || 0) * 100);
    var visualW = Math.round((w.visual || 0) * 100);
    var transW = Math.round((w.translation || 0) * 100);

    // Only show if non-default distribution
    if (textW === 60 && visualW === 25 && transW === 15) return '';

    return '<span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full" style="background:var(--color-bg-muted);color:var(--color-text-faint)">'
        + t('scores.weight_text') + ':' + textW
        + ' ' + t('scores.weight_visual') + ':' + visualW
        + ' ' + t('scores.weight_translation') + ':' + transW
        + '</span>';
};

// ============================================
// E) renderCardShell - Common card wrapper with risk stripe
// ============================================
window.AppComponents.renderCardShell = function(innerHtml, opts) {
    opts = opts || {};
    var onclick = opts.onclick ? ' onclick="' + opts.onclick + '"' : '';
    var cursor = opts.onclick ? ' cursor-pointer' : '';
    var mb = opts.noMargin ? '' : ' mb-2';
    var riskStripe = opts.riskLevel ? ' risk-stripe-' + opts.riskLevel : '';

    return '<div class="card-base p-5 hover:border-indigo-300' + cursor + mb + riskStripe + '"'
        + ' style="background:var(--color-bg-card);border-color:var(--color-border)"'
        + onclick + '>'
        + innerHtml
        + '</div>';
};

// ============================================
// F) renderNiceClassBadges - Nice class number badges with smart truncation
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
            return "<span class='inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-600'>" + cls + "</span>";
        }).join('');
        html += '<span class="cursor-pointer text-xs text-blue-600 hover:text-blue-800 px-1" '
            + 'onclick="this.outerHTML=\'' + extraBadges.replace(/'/g, "\\'") + '\'">' + t('scores.more', {count: remaining}) + '</span>';
    }

    html += '</div>';
    return html;
};
var renderNiceClassBadges = window.AppComponents.renderNiceClassBadges;

// ============================================
// Image thumbnail placeholder SVG
// ============================================
window.AppComponents.IMG_PLACEHOLDER_SVG = '<svg class="w-6 h-6" style="color:var(--color-text-faint)" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
    + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>'
    + '</svg>';

// Render a clickable thumbnail that opens lightbox, with fallback
window.AppComponents.renderThumbnail = function(imagePath, name, appNo, size) {
    size = size || 'w-12 h-12';
    var placeholder = '<div class="' + size + ' rounded flex items-center justify-center flex-shrink-0" style="background:var(--color-bg-muted);border:1px solid var(--color-border)">'
        + window.AppComponents.IMG_PLACEHOLDER_SVG + '</div>';

    if (!imagePath) return placeholder;

    var url = '/api/trademark-image/' + imagePath.split('/').map(encodeURIComponent).join('/');
    var escapedName = (name || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    var escapedAppNo = (appNo || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    var placeholderEscaped = window.AppComponents.IMG_PLACEHOLDER_SVG.replace(/"/g, '&quot;').replace(/'/g, "\\'");

    return '<div class="' + size + ' rounded flex items-center justify-center flex-shrink-0 overflow-hidden cursor-pointer hover:ring-2 hover:ring-blue-300 transition" '
        + 'style="background:var(--color-bg-muted);border:1px solid var(--color-border)" '
        + 'onclick="window.dispatchEvent(new CustomEvent(\'open-lightbox\', { detail: { src: \'' + url.replace(/'/g, "\\'") + '\', title: \'' + escapedName + '\', subtitle: \'' + escapedAppNo + '\' } }))">'
        + '<img src="' + url + '" alt="' + (name || '').replace(/"/g, '&quot;') + '" class="w-full h-full object-contain"'
        + ' onerror="this.style.display=\'none\'; this.parentElement.innerHTML=\'' + placeholderEscaped + '\'; this.parentElement.style.cursor=\'default\'; this.parentElement.onclick=null;">'
        + '</div>';
};

// ============================================
// G) renderTurkpatentButton - TURKPATENT Dosya Takibi copy+link
// ============================================
window.AppComponents.renderTurkpatentButton = function(applicationNo) {
    if (!applicationNo) return '';
    var safeNo = (applicationNo || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    var btnId = 'tp-' + safeNo.replace(/[^a-zA-Z0-9]/g, '');
    return '<div class="inline-flex items-center gap-1 mt-1">'
        + '<button onclick="event.stopPropagation(); navigator.clipboard.writeText(\'' + safeNo + '\'); '
        + 'var el=document.getElementById(\'' + btnId + '\'); if(el){el.textContent=t(\'holder.copied\'); setTimeout(function(){el.textContent=\'' + safeNo + '\';},2000);}" '
        + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 cursor-pointer transition-colors btn-press min-h-[28px]" '
        + 'title="' + t('holder.copy_app_no') + '" aria-label="' + t('holder.copy_app_no') + '">'
        + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>'
        + '<span id="' + btnId + '" class="font-mono-id">' + escapeHtml(applicationNo) + '</span>'
        + '</button>'
        + '<button onclick="event.stopPropagation(); navigator.clipboard.writeText(\'' + safeNo + '\'); '
        + 'window.open(\'https://opts.turkpatent.gov.tr/trademark\', \'_blank\'); '
        + 'if(window.AppToast) window.AppToast.showToast(t(\'holder.turkpatent_copied_toast\'), \'success\');" '
        + 'class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs border hover:opacity-80 transition-colors min-h-[28px] cursor-pointer" '
        + 'style="background:var(--color-bg-muted);color:var(--color-text-muted);border-color:var(--color-border)" '
        + 'title="' + t('holder.turkpatent_hint').replace(/'/g, '&#39;') + '" aria-label="TURKPATENT">'
        + 'TÜRKPATENT'
        + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>'
        + '</button>'
        + '</div>';
};
var renderTurkpatentButton = window.AppComponents.renderTurkpatentButton;

// ============================================
// G2) renderEventsButton — opens event timeline modal
// ============================================
window.AppComponents.renderEventsButton = function(applicationNo) {
    if (!applicationNo) return '';
    var safeNo = (applicationNo || '').replace(/'/g, "\\'");
    return '<button onclick="event.stopPropagation(); showEventsTimeline(\'' + safeNo + '\')" '
        + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs border hover:opacity-80 transition-colors min-h-[28px] cursor-pointer" '
        + 'style="background:var(--color-bg-muted);color:var(--color-text-muted);border-color:var(--color-border)" '
        + 'title="' + t('events.view_events') + '">'
        + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
        + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>'
        + '</svg>'
        + t('events.view_events')
        + '</button>';
};

// ============================================
// G3) Event-derived signal badges
//     holder-changed pill, restriction warning, last-activity line.
//     Each helper returns '' when its source field is absent so it
//     gracefully degrades on responses that don't yet surface events.
// ============================================
window.AppComponents.HOLDER_CHANGE_RECENT_MONTHS = 12;

window.AppComponents.isRecentDate = function(dateStr, months) {
    if (!dateStr) return false;
    var d = new Date(dateStr);
    if (isNaN(d.getTime())) return false;
    var windowMs = (months || 12) * 30.4375 * 24 * 60 * 60 * 1000;
    return (Date.now() - d.getTime()) <= windowMs;
};

window.AppComponents.renderHolderChangedBadge = function(item) {
    if (!item || !item.holder_changed_at) return '';
    if (!window.AppComponents.isRecentDate(item.holder_changed_at, window.AppComponents.HOLDER_CHANGE_RECENT_MONTHS)) return '';
    var dateLabel = (typeof formatDateTRShort === 'function') ? formatDateTRShort(item.holder_changed_at) : item.holder_changed_at;
    var tooltip = (t('events.holder_changed_at') + ': ' + dateLabel).replace(/"/g, '&quot;');
    return '<span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded font-medium" '
        + 'style="background:var(--color-warning-bg, #fef3c7);color:var(--color-warning-text, #92400e)" '
        + 'data-event-badge="holder-changed" '
        + 'title="' + tooltip + '">'
        + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
        + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4"/>'
        + '</svg>'
        + t('events.holder_changed')
        + '</span>';
};

window.AppComponents.renderRestrictionBadge = function(item) {
    if (!item || !item.has_restrictions) return '';
    var count = item.active_restriction_count || 0;
    if (count < 1) return '';
    var critical = count >= 3;
    var bg = critical ? 'var(--color-risk-critical-bg, #fee2e2)' : 'var(--color-warning-bg, #fef3c7)';
    var fg = critical ? 'var(--color-risk-critical-text, #991b1b)' : 'var(--color-warning-text, #92400e)';
    return '<span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded font-medium" '
        + 'style="background:' + bg + ';color:' + fg + '" '
        + 'data-event-badge="restriction" '
        + 'data-restriction-count="' + count + '">'
        + '<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">'
        + '<path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>'
        + '</svg>'
        + t('events.active_restrictions_count', { count: count })
        + '</span>';
};

window.AppComponents.renderLastEventLine = function(item) {
    if (!item || !item.last_event_type || !item.last_event_date) return '';
    if (item.last_event_type === 'correction') return '';
    var sev = item.last_event_severity || 'low';
    var color;
    if (sev === 'critical') color = 'var(--color-risk-critical-text, #991b1b)';
    else if (sev === 'high') color = 'var(--color-risk-high-text, #b45309)';
    else if (sev === 'medium') color = 'var(--color-text-secondary)';
    else color = 'var(--color-text-faint)';
    var typeKey = 'events.type_' + item.last_event_type;
    var typeLabel = t(typeKey);
    if (typeLabel === typeKey) typeLabel = item.last_event_type;
    var dateLabel = (typeof formatDateTRShort === 'function') ? formatDateTRShort(item.last_event_date) : item.last_event_date;
    return '<div class="text-xs mt-0.5" data-event-line="last" data-event-severity="' + sev + '" style="color:' + color + '">'
        + t('events.last_event') + ': '
        + '<span class="font-medium">' + escapeHtml(typeLabel) + '</span>'
        + ' · ' + escapeHtml(dateLabel)
        + '</div>';
};

window.AppComponents.renderEventDerivedBadges = function(item) {
    var hc = window.AppComponents.renderHolderChangedBadge(item);
    var rb = window.AppComponents.renderRestrictionBadge(item);
    if (!hc && !rb) return '';
    return '<div class="flex flex-wrap items-center gap-1 mt-1">' + hc + rb + '</div>';
};

var isRecentDate = window.AppComponents.isRecentDate;
var renderHolderChangedBadge = window.AppComponents.renderHolderChangedBadge;
var renderRestrictionBadge = window.AppComponents.renderRestrictionBadge;
var renderLastEventLine = window.AppComponents.renderLastEventLine;
var renderEventDerivedBadges = window.AppComponents.renderEventDerivedBadges;

// ============================================
// H) VS Comparison Layout
//    Side-by-side layout: [New App] -- VS Ring -- [Existing]
// ============================================
window.AppComponents.renderVsComparison = function(opts) {
    opts = opts || {};
    var scorePercent = opts.scorePercent || 0;
    var ringSize = opts.ringSize || 56;

    // Build party card
    function buildPartyCard(party, borderColor, bgColor, labelKey) {
        var thumb = window.AppComponents.renderThumbnail(party.image, party.name, party.app_no, 'w-14 h-14');
        var tp = window.AppComponents.renderTurkpatentButton(party.app_no);
        var classesHtml = party.classes ? window.AppComponents.renderNiceClassBadges(party.classes, 3) : '';
        var egHtml = '';
        if (party.has_extracted_goods && party.app_no) {
            var safeApp = (party.app_no || '').replace(/'/g, "\\'");
            egHtml = '<button onclick="event.stopPropagation(); showExtractedGoods(\'' + safeApp + '\', this)" '
                + 'class="inline-block text-[10px] bg-yellow-100 text-yellow-800 px-1.5 py-0.5 rounded mt-1 btn-press">'
                + t('extracted_goods.label') + ': ' + t('extracted_goods.yes') + '</button>';
        }
        return '<div class="flex-1 min-w-0 rounded-xl p-3" style="background:' + bgColor + ';border-left:3px solid ' + borderColor + '">'
            + '<div class="text-[10px] font-semibold mb-1.5 uppercase tracking-wide" style="color:' + borderColor + '">' + t(labelKey) + '</div>'
            + '<div class="flex items-start gap-2">'
            + thumb
            + '<div class="flex-1 min-w-0 space-y-0.5">'
            + '<div class="text-sm font-semibold truncate" style="color:var(--color-text-primary)">' + (party.name || t('common.na')) + '</div>'
            + tp
            + window.AppComponents.renderHolderLink(party.holder || null, null)
            + classesHtml
            + egHtml
            + '</div></div></div>';
    }

    var leftCard = buildPartyCard(opts.newMark || {}, 'rgba(239,68,68,0.4)', 'rgba(239,68,68,0.05)', 'leads.new_application');
    var rightCard = buildPartyCard(opts.existingMark || {}, 'rgba(34,197,94,0.4)', 'rgba(34,197,94,0.05)', 'leads.potential_client');

    // Center: caller-supplied HTML (e.g. category badge) or default score ring
    var centerHtml = opts.centerHtml != null
        ? opts.centerHtml
        : window.AppComponents.renderScoreRing(scorePercent, ringSize);

    return '<div class="vs-comparison w-full flex items-stretch justify-between gap-0">'
        + '<div class="flex-1 min-w-0">' + leftCard + '</div>'
        + '<div class="flex flex-col items-center justify-center mx-2 flex-shrink-0">'
        + '<div class="mb-1">' + centerHtml + '</div>'
        + '<span class="text-xs font-bold" style="color:var(--color-text-faint)">VS</span>'
        + '</div>'
        + '<div class="flex-1 min-w-0">' + rightCard + '</div>'
        + '</div>';
};

// ============================================
// I) Mini usage ring (for plan usage cards)
//    Renders a tiny SVG ring with percentage, custom color
// ============================================
window.AppComponents.renderUsageRing = function(used, limit, color) {
    if (!limit || limit <= 0) return '';
    var pct = Math.min(100, Math.round((used / limit) * 100));
    var size = 32;
    var r = 12;
    var circ = 2 * Math.PI * r;
    var offset = circ - (pct / 100) * circ;
    var strokeColor = pct >= 90 ? 'var(--color-deadline-critical)' : (color || 'var(--color-primary)');

    return '<svg width="' + size + '" height="' + size + '" class="score-ring">'
        + '<circle cx="' + (size/2) + '" cy="' + (size/2) + '" r="' + r + '" class="score-ring-track" stroke-width="2.5"/>'
        + '<circle cx="' + (size/2) + '" cy="' + (size/2) + '" r="' + r + '" class="score-ring-fill" '
        + 'stroke="' + strokeColor + '" stroke-width="2.5" '
        + 'stroke-dasharray="' + circ + '" stroke-dashoffset="' + offset + '"/>'
        + '<text x="50%" y="50%" text-anchor="middle" dominant-baseline="central" '
        + 'font-size="8" font-weight="700" fill="' + strokeColor + '" '
        + 'style="transform:rotate(90deg);transform-origin:center">'
        + pct + '</text></svg>';
};

// ============================================
// J) Score animation helper
// ============================================
window.AppComponents.animateScore = function(element, target, duration) {
    duration = duration || 600;
    var start = null;
    var step = function(timestamp) {
        if (!start) start = timestamp;
        var progress = Math.min((timestamp - start) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3);
        element.textContent = Math.round(eased * target) + '%';
        if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
};

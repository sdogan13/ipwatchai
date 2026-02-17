/**
 * score-badge.js - Shared risk score color/badge rendering
 * Single source of truth for all score-related UI.
 * Uses CSS custom properties for dark mode compatibility.
 */
window.AppComponents = window.AppComponents || {};

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

// ============================================
// D) renderSimilarityBadges - Multi-dimension score breakdown with mini progress bars
//    Shows all non-zero scoring dimensions from the backend
// ============================================
window.AppComponents.renderSimilarityBadges = function(data) {
    if (!data) return '';
    var scores = data.scores || data;

    var textScore = scores.text_similarity || 0;
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
            return '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-600">' + cls + '</span>';
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
    var placeholderEscaped = window.AppComponents.IMG_PLACEHOLDER_SVG.replace(/'/g, "\\'");

    return '<div class="' + size + ' rounded flex items-center justify-center flex-shrink-0 overflow-hidden cursor-pointer hover:ring-2 hover:ring-blue-300 transition" '
        + 'style="background:var(--color-bg-muted);border:1px solid var(--color-border)" '
        + 'onclick="window.dispatchEvent(new CustomEvent(\'open-lightbox\', { detail: { src: \'' + url.replace(/'/g, "\\'") + '\', title: \'' + escapedName + '\', subtitle: \'' + escapedAppNo + '\' } }))">'
        + '<img src="' + url + '" alt="' + (name || '').replace(/"/g, '&quot;') + '" class="w-full h-full object-contain" loading="lazy"'
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
        + 'TURKPATENT'
        + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>'
        + '</button>'
        + '</div>';
};
var renderTurkpatentButton = window.AppComponents.renderTurkpatentButton;

// ============================================
// G2) TURKPATENT Bookmarklet - Auto-fill "Başvuru Numarası" on opts.turkpatent.gov.tr
//     Reads clipboard, finds the Angular Material input, sets value + dispatches events, clicks Sorgula
// ============================================
window.AppComponents.TURKPATENT_BOOKMARKLET = 'javascript:void((async()=>{try{var t=(await navigator.clipboard.readText()||"").trim();if(!t){alert("\\u00d6nce IP Watch AI\\u0027dan bir ba\\u015fvuru numaras\\u0131 kopyalay\\u0131n");return}var f=document.querySelectorAll("mat-form-field"),n;for(var i=0;i<f.length;i++){var l=f[i].querySelectorAll("mat-label,label");for(var j=0;j<l.length;j++)if(/ba.vuru/i.test(l[j].textContent)){n=f[i].querySelector("input");break}if(n)break}if(!n){var a=document.querySelectorAll("mat-form-field input");if(a.length)n=a[0]}if(!n){alert("Ba\\u015fvuru Numaras\\u0131 alan\\u0131 bulunamad\\u0131");return}var s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set;s.call(n,t);n.dispatchEvent(new Event("input",{bubbles:!0}));n.dispatchEvent(new Event("change",{bubbles:!0}));n.focus();n.style.transition="background .3s";n.style.background="#c8e6c9";setTimeout(function(){n.style.background=""},1500);var b=document.querySelectorAll("button");for(var k=0;k<b.length;k++)if(/sorgula/i.test(b[k].textContent)){(function(x){setTimeout(function(){x.click()},400)})(b[k]);break}}catch(e){alert("\\u00d6nce IP Watch AI\\u0027dan bir ba\\u015fvuru numaras\\u0131 kopyalay\\u0131n")}})())';

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
                + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer mt-1 btn-press min-h-[28px]">'
                + t('extracted_goods.label') + ' <span class="underline">' + t('extracted_goods.yes') + '</span></button>';
        }
        return '<div class="flex-1 min-w-0 rounded-xl p-4 border" style="background:' + bgColor + ';border-color:' + borderColor + '">'
            + '<div class="text-xs font-semibold mb-2" style="color:' + borderColor + '">' + t(labelKey) + '</div>'
            + '<div class="flex items-start gap-3">'
            + thumb
            + '<div class="flex-1 min-w-0 space-y-1 text-sm">'
            + '<div class="font-semibold truncate" style="color:var(--color-text-primary)">' + (party.name || t('common.na')) + '</div>'
            + tp
            + '<div class="text-xs truncate" style="color:var(--color-text-faint)">' + (party.holder || t('leads.unknown_holder')) + '</div>'
            + classesHtml
            + egHtml
            + '</div></div></div>';
    }

    var leftCard = buildPartyCard(opts.newMark || {}, 'rgba(239,68,68,0.4)', 'rgba(239,68,68,0.05)', 'leads.new_application');
    var rightCard = buildPartyCard(opts.existingMark || {}, 'rgba(34,197,94,0.4)', 'rgba(34,197,94,0.05)', 'leads.potential_client');

    // Score ring in center
    var ringHtml = window.AppComponents.renderScoreRing(scorePercent, ringSize);

    return '<div class="vs-comparison flex items-stretch gap-0">'
        + '<div class="flex-1 min-w-0">' + leftCard + '</div>'
        + '<div class="flex flex-col items-center justify-center px-2 flex-shrink-0">'
        + '<div class="mb-1">' + ringHtml + '</div>'
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

/**
 * lead-card.js - Opposition Radar lead card rendering
 * Uses shared helpers from score-badge.js
 */
window.AppComponents = window.AppComponents || {};

window.AppComponents.renderLeadCard = function(lead) {
    var scorePercent = Math.round(lead.similarity_score * 100);
    var riskLevel = window.AppComponents.getScoreRiskLevel(scorePercent);

    // Use score ring instead of flat badge
    var scoreRing = window.AppComponents.renderScoreRing(scorePercent, 40);

    var urgencyHtml = '';
    if (lead.urgency_level === 'critical') urgencyHtml = '<span class="text-xs px-2 py-0.5 rounded-full font-medium" style="' + window.AppComponents.getScoreColor(90) + '">' + t('risk_level.critical') + '</span>';
    else if (lead.urgency_level === 'urgent') urgencyHtml = '<span class="text-xs px-2 py-0.5 rounded-full font-medium" style="' + window.AppComponents.getScoreColor(80) + '">' + t('deadline.active_urgent') + '</span>';
    else if (lead.urgency_level === 'soon') urgencyHtml = '<span class="text-xs px-2 py-0.5 rounded-full font-medium" style="' + window.AppComponents.getScoreColor(60) + '">' + t('leads.filter_soon').replace(/\s*\(.*\)/, '') + '</span>';

    var statusHtml = '';
    if (lead.lead_status === 'viewed') statusHtml = '<span class="bg-blue-100 text-blue-700 text-xs px-2 py-0.5 rounded-full font-medium ml-1">' + t('leads.filter_viewed') + '</span>';
    else if (lead.lead_status === 'contacted') statusHtml = '<span class="bg-purple-100 text-purple-700 text-xs px-2 py-0.5 rounded-full font-medium ml-1">' + t('leads.contacted') + '</span>';

    var classesHtml = window.AppComponents.renderNiceClassBadges(lead.overlapping_classes, 4);

    // Thumbnails for both parties
    var newMarkThumb = window.AppComponents.renderThumbnail(lead.new_mark_image, lead.new_mark_name, lead.new_mark_app_no, 'w-10 h-10');
    var existMarkThumb = window.AppComponents.renderThumbnail(lead.existing_mark_image, lead.existing_mark_name, lead.existing_mark_app_no, 'w-10 h-10');

    // 4-component score breakdown
    var breakdownScores = {
        text_similarity: lead.text_similarity,
        semantic_similarity: lead.semantic_similarity,
        visual_similarity: lead.visual_similarity,
        translation_similarity: lead.translation_similarity
    };
    var breakdownHtml = window.AppComponents.renderSimilarityBadges(breakdownScores);

    // Extracted goods indicators
    var newMarkEgHtml = '';
    if (lead.new_mark_has_extracted_goods && lead.new_mark_app_no) {
        var safeNewApp = (lead.new_mark_app_no || '').replace(/'/g, "\\'");
        newMarkEgHtml = '<button onclick="event.stopPropagation(); showExtractedGoods(\'' + safeNewApp + '\', this)" '
            + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer mt-1 btn-press min-h-[28px]">'
            + t('extracted_goods.label') + ' <span class="underline">' + t('extracted_goods.yes') + '</span></button>';
    }
    var existMarkEgHtml = '';
    if (lead.existing_mark_has_extracted_goods && lead.existing_mark_app_no) {
        var safeExistApp = (lead.existing_mark_app_no || '').replace(/'/g, "\\'");
        existMarkEgHtml = '<button onclick="event.stopPropagation(); showExtractedGoods(\'' + safeExistApp + '\', this)" '
            + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer mt-1 btn-press min-h-[28px]">'
            + t('extracted_goods.label') + ' <span class="underline">' + t('extracted_goods.yes') + '</span></button>';
    }

    // Opposition timeline progress bar (compact for card) + text
    var timelineBarHtml = window.AppComponents.renderTimelineBar(
        lead.bulletin_date, lead.opposition_deadline, { height: 'sm' }
    );
    var timelineHtml = window.AppComponents.renderOppositionTimeline(
        lead.bulletin_date, lead.opposition_deadline, { compact: true }
    );

    var inner = '<div class="flex items-start justify-between mb-3">'
        + '<div class="flex items-center gap-2 flex-wrap">' + urgencyHtml + statusHtml + '</div>'
        + scoreRing
        + '</div>'
        + (timelineBarHtml ? '<div class="mb-3">' + timelineBarHtml + '</div>'
           : timelineHtml ? '<div class="mb-3">' + timelineHtml + '</div>' : '')
        + (breakdownHtml ? '<div class="mb-3">' + breakdownHtml + '</div>' : '')
        + '<div class="grid grid-cols-1 md:grid-cols-2 gap-3">'
        + '<div class="rounded-lg p-3 border" style="background:rgba(239,68,68,0.05);border-color:rgba(239,68,68,0.15)">'
        + '<div class="text-xs font-semibold mb-1" style="color:var(--color-risk-critical-text)">' + t('leads.new_application').toUpperCase() + '</div>'
        + '<div class="flex items-start gap-2">'
        + newMarkThumb
        + '<div class="flex-1 min-w-0">'
        + '<div class="font-semibold truncate" style="color:var(--color-text-primary)">' + (lead.new_mark_name || 'N/A') + '</div>'
        + window.AppComponents.renderTurkpatentButton(lead.new_mark_app_no)
        + '<div class="text-xs truncate mt-0.5" style="color:var(--color-text-faint)">' + (lead.new_mark_holder_name || t('leads.unknown_holder')) + '</div>'
        + (lead.new_mark_application_date ? '<div class="text-xs mt-0.5" style="color:var(--color-text-faint)">' + t('common.application_date') + ' ' + formatDateTRShort(lead.new_mark_application_date) + '</div>' : '')
        + newMarkEgHtml
        + '</div></div>'
        + '</div>'
        + '<div class="rounded-lg p-3 border" style="background:rgba(34,197,94,0.05);border-color:rgba(34,197,94,0.15)">'
        + '<div class="text-xs font-semibold mb-1" style="color:var(--color-risk-low-text)">' + t('leads.potential_client').toUpperCase() + '</div>'
        + '<div class="flex items-start gap-2">'
        + existMarkThumb
        + '<div class="flex-1 min-w-0">'
        + '<div class="font-semibold truncate" style="color:var(--color-text-primary)">' + (lead.existing_mark_name || 'N/A') + '</div>'
        + window.AppComponents.renderTurkpatentButton(lead.existing_mark_app_no)
        + '<div class="text-xs truncate mt-0.5" style="color:var(--color-text-faint)">' + (lead.existing_mark_holder_name || t('leads.unknown_holder')) + '</div>'
        + (lead.existing_mark_application_date ? '<div class="text-xs mt-0.5" style="color:var(--color-text-faint)">' + t('common.application_date') + ' ' + formatDateTRShort(lead.existing_mark_application_date) + '</div>' : '')
        + existMarkEgHtml
        + '</div></div>'
        + '</div>'
        + '</div>'
        + '<div class="flex items-center justify-between mt-3 pt-3 text-sm" style="border-top:1px solid var(--color-border);color:var(--color-text-faint)">'
        + '<div class="flex items-center gap-3">'
        + '<span>' + t('leads.bulletin_label') + ' ' + (lead.bulletin_no || t('common.na')) + '</span>'
        + '<span>' + lead.conflict_type + '</span>'
        + classesHtml
        + '</div>'
        + (lead.created_at ? '<span class="text-xs" style="color:var(--color-text-faint)">' + t('leads.detected') + ': ' + timeAgo(lead.created_at) + '</span>' : '')
        + '</div>';

    return window.AppComponents.renderCardShell(inner, { onclick: "showLeadDetail('" + lead.id + "')", riskLevel: riskLevel });
};

/**
 * renderLeadRow - Compact table row for Opposition Radar
 * Renders a single <tr> for the leads table view.
 */
window.AppComponents.renderLeadRow = function(lead) {
    var scorePercent = Math.round(lead.similarity_score * 100);

    // Urgency badge (compact)
    var urgencyHtml = '';
    var urgencyStyle = '';
    if (lead.urgency_level === 'critical') {
        urgencyStyle = window.AppComponents.getScoreColor(90);
        urgencyHtml = '<span class="inline-block text-xs px-1.5 py-0.5 rounded font-semibold whitespace-nowrap" style="' + urgencyStyle + '">' + lead.days_until_deadline + t('leads.days_short', {n:''}).trim() + '</span>';
    } else if (lead.urgency_level === 'urgent') {
        urgencyStyle = window.AppComponents.getScoreColor(80);
        urgencyHtml = '<span class="inline-block text-xs px-1.5 py-0.5 rounded font-semibold whitespace-nowrap" style="' + urgencyStyle + '">' + lead.days_until_deadline + t('leads.days_short', {n:''}).trim() + '</span>';
    } else if (lead.urgency_level === 'soon') {
        urgencyStyle = window.AppComponents.getScoreColor(60);
        urgencyHtml = '<span class="inline-block text-xs px-1.5 py-0.5 rounded font-medium whitespace-nowrap" style="' + urgencyStyle + '">' + lead.days_until_deadline + t('leads.days_short', {n:''}).trim() + '</span>';
    } else {
        urgencyHtml = '<span class="text-xs whitespace-nowrap" style="color:var(--color-text-faint)">' + lead.days_until_deadline + t('leads.days_short', {n:''}).trim() + '</span>';
    }

    // Score ring (small)
    var scoreRing = window.AppComponents.renderScoreRing(scorePercent, 32);

    // Thumbnails (small, clickable to open lightbox)
    var newThumb = window.AppComponents.renderThumbnail(lead.new_mark_image, lead.new_mark_name, lead.new_mark_app_no, 'w-8 h-8');
    var existThumb = window.AppComponents.renderThumbnail(lead.existing_mark_image, lead.existing_mark_name, lead.existing_mark_app_no, 'w-8 h-8');

    // Nice classes (compact, max 3)
    var classesHtml = window.AppComponents.renderNiceClassBadges(lead.overlapping_classes, 3);

    // Deadline date
    var deadlineStr = lead.opposition_deadline ? formatDateTRShort(lead.opposition_deadline) : '-';

    // Status badge
    var statusHtml = '';
    if (lead.lead_status === 'new') statusHtml = '<span class="inline-block w-2 h-2 rounded-full bg-green-500" title="' + t('leads.filter_new') + '"></span>';
    else if (lead.lead_status === 'viewed') statusHtml = '<span class="inline-block w-2 h-2 rounded-full bg-blue-500" title="' + t('leads.filter_viewed') + '"></span>';
    else if (lead.lead_status === 'contacted') statusHtml = '<span class="inline-block w-2 h-2 rounded-full bg-purple-500" title="' + t('leads.contacted') + '"></span>';
    else if (lead.lead_status === 'converted') statusHtml = '<span class="inline-block w-2 h-2 rounded-full bg-emerald-500" title="' + t('leads.converted') + '"></span>';

    // Row hover color
    var hoverBg = lead.lead_status === 'new' ? 'font-medium' : '';

    return '<tr class="cursor-pointer hover:bg-black/5 dark:hover:bg-white/5 transition-colors ' + hoverBg + '" '
        + 'style="border-bottom:1px solid var(--color-border)" '
        + 'onclick="showLeadDetail(\'' + lead.id + '\')">'
        + '<td class="px-3 py-2 text-center">' + urgencyHtml + '</td>'
        + '<td class="px-3 py-2 text-center">' + scoreRing + '</td>'
        + '<td class="px-3 py-2">'
        +   '<div class="flex items-center gap-2">'
        +     '<div onclick="event.stopPropagation()">' + newThumb + '</div>'
        +     '<div class="min-w-0">'
        +       '<div class="truncate max-w-[160px]" style="color:var(--color-text-primary)" title="' + (lead.new_mark_name || '') + '">' + (lead.new_mark_name || 'N/A') + '</div>'
        +       '<div class="text-xs truncate max-w-[160px]" style="color:var(--color-text-faint)" title="' + (lead.new_mark_holder_name || '') + '">' + (lead.new_mark_holder_name || '-') + '</div>'
        +     '</div>'
        +   '</div>'
        + '</td>'
        + '<td class="px-3 py-2">'
        +   '<div class="flex items-center gap-2">'
        +     '<div onclick="event.stopPropagation()">' + existThumb + '</div>'
        +     '<div class="min-w-0">'
        +       '<div class="truncate max-w-[160px]" style="color:var(--color-text-primary)" title="' + (lead.existing_mark_name || '') + '">' + (lead.existing_mark_name || 'N/A') + '</div>'
        +       '<div class="text-xs truncate max-w-[160px]" style="color:var(--color-text-faint)" title="' + (lead.existing_mark_holder_name || '') + '">' + (lead.existing_mark_holder_name || '-') + '</div>'
        +     '</div>'
        +   '</div>'
        + '</td>'
        + '<td class="px-3 py-2 text-center hidden lg:table-cell">' + classesHtml + '</td>'
        + '<td class="px-3 py-2 text-center"><span class="text-xs whitespace-nowrap" style="color:var(--color-text-secondary)">' + deadlineStr + '</span></td>'
        + '<td class="px-3 py-2 text-center hidden md:table-cell">' + statusHtml + '</td>'
        + '</tr>';
};

/**
 * renderLeadMobileCard - Compact card for mobile screens
 */
window.AppComponents.renderLeadMobileCard = function(lead) {
    var scorePercent = Math.round(lead.similarity_score * 100);
    var scoreRing = window.AppComponents.renderScoreRing(scorePercent, 28);
    var newThumb = window.AppComponents.renderThumbnail(lead.new_mark_image, lead.new_mark_name, lead.new_mark_app_no, 'w-7 h-7');
    var existThumb = window.AppComponents.renderThumbnail(lead.existing_mark_image, lead.existing_mark_name, lead.existing_mark_app_no, 'w-7 h-7');

    // Urgency
    var urgencyHtml = '';
    if (lead.urgency_level === 'critical') {
        urgencyHtml = '<span class="text-xs px-1.5 py-0.5 rounded font-semibold" style="' + window.AppComponents.getScoreColor(90) + '">' + lead.days_until_deadline + t('leads.days_short', {n:''}).trim() + '</span>';
    } else if (lead.urgency_level === 'urgent') {
        urgencyHtml = '<span class="text-xs px-1.5 py-0.5 rounded font-semibold" style="' + window.AppComponents.getScoreColor(80) + '">' + lead.days_until_deadline + t('leads.days_short', {n:''}).trim() + '</span>';
    } else {
        urgencyHtml = '<span class="text-xs" style="color:var(--color-text-faint)">' + lead.days_until_deadline + t('leads.days_short', {n:''}).trim() + '</span>';
    }

    var deadlineStr = lead.opposition_deadline ? formatDateTRShort(lead.opposition_deadline) : '';

    return '<div class="px-3 py-2.5 cursor-pointer active:bg-black/5" style="border-color:var(--color-border)" '
        + 'onclick="showLeadDetail(\'' + lead.id + '\')">'
        // Row 1: urgency + score + deadline
        + '<div class="flex items-center justify-between mb-1.5">'
        +   '<div class="flex items-center gap-2">' + urgencyHtml + scoreRing + '</div>'
        +   '<span class="text-xs" style="color:var(--color-text-faint)">' + deadlineStr + '</span>'
        + '</div>'
        // Row 2: new mark vs existing mark
        + '<div class="flex items-center gap-3">'
        +   '<div class="flex items-center gap-1.5 flex-1 min-w-0" onclick="event.stopPropagation()">'
        +     newThumb
        +     '<div class="min-w-0"><div class="text-xs font-medium truncate" style="color:var(--color-text-primary)">' + (lead.new_mark_name || 'N/A') + '</div></div>'
        +   '</div>'
        +   '<span class="text-xs flex-shrink-0" style="color:var(--color-text-faint)">vs</span>'
        +   '<div class="flex items-center gap-1.5 flex-1 min-w-0" onclick="event.stopPropagation()">'
        +     existThumb
        +     '<div class="min-w-0"><div class="text-xs font-medium truncate" style="color:var(--color-text-primary)">' + (lead.existing_mark_name || 'N/A') + '</div></div>'
        +   '</div>'
        + '</div>'
        + '</div>';
};

// Expose as globals
var renderLeadCard = window.AppComponents.renderLeadCard;
var renderLeadRow = window.AppComponents.renderLeadRow;
var renderLeadMobileCard = window.AppComponents.renderLeadMobileCard;

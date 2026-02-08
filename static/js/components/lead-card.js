/**
 * lead-card.js - Opposition Radar lead card rendering
 * Uses shared helpers from score-badge.js
 */
window.AppComponents = window.AppComponents || {};

window.AppComponents.renderLeadCard = function(lead) {
    var scorePercent = Math.round(lead.similarity_score * 100);

    // Use shared score color
    var scoreBadge = window.AppComponents.renderScoreBadge(scorePercent);

    var urgencyHtml = '';
    if (lead.urgency_level === 'critical') urgencyHtml = '<span class="bg-red-100 text-red-700 text-xs px-2 py-0.5 rounded-full font-medium">Kritik</span>';
    else if (lead.urgency_level === 'urgent') urgencyHtml = '<span class="bg-orange-100 text-orange-700 text-xs px-2 py-0.5 rounded-full font-medium">Acil</span>';
    else if (lead.urgency_level === 'soon') urgencyHtml = '<span class="bg-yellow-100 text-yellow-700 text-xs px-2 py-0.5 rounded-full font-medium">Yakinda</span>';

    var statusHtml = '';
    if (lead.lead_status === 'viewed') statusHtml = '<span class="bg-blue-100 text-blue-700 text-xs px-2 py-0.5 rounded-full font-medium ml-1">Goruntulendi</span>';
    else if (lead.lead_status === 'contacted') statusHtml = '<span class="bg-purple-100 text-purple-700 text-xs px-2 py-0.5 rounded-full font-medium ml-1">Iletisim</span>';

    var classesHtml = lead.overlapping_classes && lead.overlapping_classes.length
        ? '<span class="text-gray-400">Siniflar: ' + lead.overlapping_classes.join(', ') + '</span>' : '';

    // Thumbnails for both parties
    var newMarkThumb = window.AppComponents.renderThumbnail(lead.new_mark_image, lead.bulletin_no, 'w-10 h-10');
    var existMarkThumb = window.AppComponents.renderThumbnail(lead.existing_mark_image, lead.bulletin_no, 'w-10 h-10');

    // 4-component score breakdown (Feature 3)
    var breakdownScores = {
        text_similarity: lead.text_similarity,
        semantic_similarity: lead.semantic_similarity,
        visual_similarity: lead.visual_similarity,
        translation_similarity: lead.translation_similarity
    };
    var breakdownHtml = window.AppComponents.renderSimilarityBadges(breakdownScores);

    // Extracted goods indicators for both parties
    var newMarkEgHtml = '';
    if (lead.new_mark_has_extracted_goods && lead.new_mark_app_no) {
        var safeNewApp = (lead.new_mark_app_no || '').replace(/'/g, "\\'");
        newMarkEgHtml = '<button onclick="event.stopPropagation(); showExtractedGoods(\'' + safeNewApp + '\', this)" '
            + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer mt-1">'
            + 'CIKARILMIS URUN: <span class="underline">EVET</span></button>';
    }
    var existMarkEgHtml = '';
    if (lead.existing_mark_has_extracted_goods && lead.existing_mark_app_no) {
        var safeExistApp = (lead.existing_mark_app_no || '').replace(/'/g, "\\'");
        existMarkEgHtml = '<button onclick="event.stopPropagation(); showExtractedGoods(\'' + safeExistApp + '\', this)" '
            + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer mt-1">'
            + 'CIKARILMIS URUN: <span class="underline">EVET</span></button>';
    }

    var inner = '<div class="flex items-start justify-between mb-3">'
        + '<div class="flex items-center gap-2 flex-wrap">' + urgencyHtml + statusHtml
        + '<span class="text-gray-400 text-sm">' + lead.days_until_deadline + ' gun kaldi</span>'
        + '</div>'
        + scoreBadge
        + '</div>'
        + (breakdownHtml ? '<div class="mb-3">' + breakdownHtml + '</div>' : '')
        + '<div class="grid grid-cols-1 md:grid-cols-2 gap-3">'
        + '<div class="bg-red-50 rounded-lg p-3 border border-red-100">'
        + '<div class="text-xs text-red-600 font-semibold mb-1">YENI BASVURU</div>'
        + '<div class="flex items-start gap-2">'
        + newMarkThumb
        + '<div class="flex-1 min-w-0">'
        + '<div class="font-semibold text-gray-900 truncate">' + (lead.new_mark_name || 'N/A') + '</div>'
        + window.AppComponents.renderTurkpatentButton(lead.new_mark_app_no)
        + '<div class="text-xs text-gray-400 truncate mt-0.5">' + (lead.new_mark_holder_name || 'Bilinmiyor') + '</div>'
        + newMarkEgHtml
        + '</div></div>'
        + '</div>'
        + '<div class="bg-green-50 rounded-lg p-3 border border-green-100">'
        + '<div class="text-xs text-green-600 font-semibold mb-1">POTANSIYEL MUSTERI</div>'
        + '<div class="flex items-start gap-2">'
        + existMarkThumb
        + '<div class="flex-1 min-w-0">'
        + '<div class="font-semibold text-gray-900 truncate">' + (lead.existing_mark_name || 'N/A') + '</div>'
        + window.AppComponents.renderTurkpatentButton(lead.existing_mark_app_no)
        + '<div class="text-xs text-gray-400 truncate mt-0.5">' + (lead.existing_mark_holder_name || 'Bilinmiyor') + '</div>'
        + existMarkEgHtml
        + '</div></div>'
        + '</div>'
        + '</div>'
        + '<div class="flex items-center justify-between mt-3 pt-3 border-t border-gray-100 text-sm text-gray-400">'
        + '<div class="flex items-center gap-3">'
        + '<span>Bulten: ' + (lead.bulletin_no || 'N/A') + '</span>'
        + '<span>' + lead.conflict_type + '</span>'
        + classesHtml
        + '</div>'
        + '<span class="text-amber-600 font-medium">Son: ' + lead.opposition_deadline + '</span>'
        + '</div>';

    return window.AppComponents.renderCardShell(inner, { onclick: "showLeadDetail('" + lead.id + "')" });
};

// Expose as global
var renderLeadCard = window.AppComponents.renderLeadCard;

/**
 * helpers.js - Shared utility functions
 */
window.AppUtils = window.AppUtils || {};

window.AppUtils.escapeHtml = function(text) {
    if (!text) return '';
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
};

window.AppUtils.getSelectedNiceClasses = function() {
    var select = document.getElementById('nice-class-select');
    if (!select) return [];
    return Array.from(select.selectedOptions).map(function(o) { return parseInt(o.value); }).filter(function(v) { return !isNaN(v); });
};

window.AppUtils.getResultScore = function(r) {
    if (r.scores && typeof r.scores.total === 'number') return r.scores.total;
    if (typeof r.risk_score === 'number') return r.risk_score;
    return 0;
};

window.AppUtils.parseResultDate = function(dateStr) {
    if (!dateStr) return 0;
    try { return new Date(dateStr).getTime() || 0; } catch(e) { return 0; }
};

window.AppUtils.getStatusBadgeClass = function(status) {
    var c = {
        'Registered': 'bg-green-100 text-green-700', 'Renewed': 'bg-green-100 text-green-700',
        'Published': 'bg-blue-100 text-blue-700', 'Applied': 'bg-yellow-100 text-yellow-700',
        'Opposed': 'bg-orange-100 text-orange-700', 'Refused': 'bg-red-100 text-red-700',
        'Withdrawn': 'bg-gray-100 text-gray-600', 'Expired': 'bg-gray-100 text-gray-500'
    };
    return c[status] || 'bg-gray-100 text-gray-600';
};

window.AppUtils.getStatusText = function(status) {
    var labels = {
        'Registered': t('status.registered'), 'Published': t('status.published'), 'Applied': t('status.applied'),
        'Opposed': t('status.opposed'), 'Refused': t('status.refused'), 'Withdrawn': t('status.withdrawn'),
        'Expired': t('status.expired'), 'Renewed': t('status.renewed')
    };
    return labels[status] || status || t('status.unknown');
};

window.AppUtils.formatHolderDate = function(dateStr) {
    if (!dateStr) return '';
    try { return new Date(dateStr).toLocaleDateString('tr-TR'); }
    catch(e) { return dateStr; }
};

// ============================================
// PIPELINE STATUS HELPERS
// ============================================
window.AppUtils.stepStatusClass = function(step) {
    if (!step) return 'bg-gray-50';
    switch(step.status) {
        case 'success': return 'bg-green-50 border border-green-200';
        case 'partial': return 'bg-yellow-50 border border-yellow-200';
        case 'failed':  return 'bg-red-50 border border-red-200';
        case 'skipped': return 'bg-gray-100 border border-gray-200';
        default:        return 'bg-gray-50';
    }
};

window.AppUtils.stepStatusText = function(step) {
    if (!step) return '';
    switch(step.status) {
        case 'success': return t('pipeline.status_success');
        case 'partial': return t('pipeline.status_partial');
        case 'failed':  return t('pipeline.status_failed');
        case 'skipped': return t('pipeline.status_skipped');
        default:        return '';
    }
};

window.AppUtils.formatDuration = function(seconds) {
    if (!seconds) return '';
    if (seconds < 60) return t('duration.seconds', { n: Math.round(seconds) });
    if (seconds < 3600) return t('duration.minutes', { n: Math.round(seconds / 60) });
    return t('duration.hours', { n: Math.round(seconds / 3600 * 10) / 10 });
};

window.AppUtils.formatDateTR = function(isoStr) {
    if (!isoStr) return '';
    try {
        var loc = (window.AppI18n && window.AppI18n.getLocale) ? window.AppI18n.getLocale() : 'tr';
        var localeMap = { 'tr': 'tr-TR', 'en': 'en-US', 'ar': 'ar-SA' };
        var dl = localeMap[loc] || 'tr-TR';
        var d = new Date(isoStr);
        return d.toLocaleDateString(dl) + ' ' + d.toLocaleTimeString(dl, {hour:'2-digit', minute:'2-digit'});
    } catch(e) { return isoStr; }
};

window.AppUtils.timeAgo = function(dateStr) {
    if (!dateStr) return '';
    var seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
    if (seconds < 60) return t('common.just_now') || 'just now';
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + 'm ' + (t('common.ago') || 'ago');
    var hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + 'h ' + (t('common.ago') || 'ago');
    var days = Math.floor(hours / 24);
    if (days < 30) return days + 'd ' + (t('common.ago') || 'ago');
    var months = Math.floor(days / 30);
    return months + 'mo ' + (t('common.ago') || 'ago');
};

window.AppUtils.escapeRegex = function(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
};

window.AppUtils.highlightMatches = function(name, matchedWords) {
    if (!matchedWords || !matchedWords.length || !name) return window.AppUtils.escapeHtml(name);
    var safe = window.AppUtils.escapeHtml(name);
    matchedWords.forEach(function(word) {
        if (!word) return;
        var regex = new RegExp('(' + window.AppUtils.escapeRegex(window.AppUtils.escapeHtml(word)) + ')', 'gi');
        safe = safe.replace(regex,
            '<mark class="bg-yellow-200/60 dark:bg-yellow-900/40 rounded px-0.5 font-semibold">$1</mark>');
    });
    return safe;
};

// Expose as globals for inline onclick handlers in HTML
var escapeHtml = window.AppUtils.escapeHtml;
var timeAgo = window.AppUtils.timeAgo;
var highlightMatches = window.AppUtils.highlightMatches;
var getSelectedNiceClasses = window.AppUtils.getSelectedNiceClasses;
var getResultScore = window.AppUtils.getResultScore;
var parseResultDate = window.AppUtils.parseResultDate;
var getStatusBadgeClass = window.AppUtils.getStatusBadgeClass;
var getStatusText = window.AppUtils.getStatusText;
var formatHolderDate = window.AppUtils.formatHolderDate;

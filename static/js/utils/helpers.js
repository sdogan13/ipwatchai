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

// ============================================
// SINGLE SOURCE OF TRUTH: Trademark Status Colors
// All status color helpers derive from _STATUS_COLORS.
// ============================================
var _STATUS_COLORS = {
    'green':  { tw: 'bg-green-100 text-green-700',  hex: '#16a34a', rgba: 'rgba(22, 163, 74, 0.12)' },
    'yellow': { tw: 'bg-yellow-100 text-yellow-700', hex: '#ca8a04', rgba: 'rgba(202, 138, 4, 0.12)' },
    'blue':   { tw: 'bg-blue-100 text-blue-700',     hex: '#2563eb', rgba: 'rgba(37, 99, 235, 0.12)' },
    'orange': { tw: 'bg-orange-100 text-orange-700',  hex: '#ea580c', rgba: 'rgba(234, 88, 12, 0.12)' },
    'red':    { tw: 'bg-red-100 text-red-700',        hex: '#dc2626', rgba: 'rgba(220, 38, 38, 0.12)' },
    'gray':   { tw: 'bg-gray-100 text-gray-500',      hex: '#9ca3af', rgba: 'rgba(156, 163, 175, 0.10)' }
};

var _STATUS_GROUP = {
    'Tescil Edildi': 'green', 'Yenilendi': 'green', 'Devredildi': 'green',
    'Yayında': 'yellow',
    'Başvuruldu': 'blue',
    'İtiraz Edildi': 'orange', 'Kısmi Red': 'orange',
    'Reddedildi': 'red', 'Geri Çekildi': 'red', 'İptal Edildi': 'red', 'Süresi Doldu': 'red',
    'Bilinmiyor': 'gray'
};

// English / lowercase aliases → canonical Turkish key
var _STATUS_ALIASES = {
    'registered': 'Tescil Edildi', 'tescil edildi': 'Tescil Edildi', 'tescilli': 'Tescil Edildi', 'tescil': 'Tescil Edildi',
    'renewed': 'Yenilendi', 'yenilendi': 'Yenilendi',
    'transferred': 'Devredildi', 'devredildi': 'Devredildi',
    'published': 'Yayında', 'yayında': 'Yayında', 'yayinda': 'Yayında',
    'applied': 'Başvuruldu', 'pending': 'Başvuruldu', 'başvuruldu': 'Başvuruldu', 'basvuruldu': 'Başvuruldu',
    'opposed': 'İtiraz Edildi', 'itiraz edildi': 'İtiraz Edildi', 'i\u0307tiraz edildi': 'İtiraz Edildi',
    'partial_refusal': 'Kısmi Red', 'kısmi red': 'Kısmi Red', 'kismi red': 'Kısmi Red',
    'rejected': 'Reddedildi', 'refused': 'Reddedildi', 'reddedildi': 'Reddedildi',
    'withdrawn': 'Geri Çekildi', 'geri çekildi': 'Geri Çekildi', 'geri cekildi': 'Geri Çekildi',
    'cancelled': 'İptal Edildi', 'iptal edildi': 'İptal Edildi', 'i\u0307ptal edildi': 'İptal Edildi',
    'expired': 'Süresi Doldu', 'süresi doldu': 'Süresi Doldu', 'suresi doldu': 'Süresi Doldu',
    'unknown': 'Bilinmiyor', 'bilinmiyor': 'Bilinmiyor'
};

function _resolveStatusGroup(status) {
    if (!status) return 'gray';
    if (_STATUS_GROUP[status]) return _STATUS_GROUP[status];
    var key = _STATUS_ALIASES[(status || '').toLowerCase().replace(/\u0307/g, '')];
    return key ? (_STATUS_GROUP[key] || 'gray') : 'gray';
}

window.AppUtils.getStatusBadgeClass = function(status) {
    return _STATUS_COLORS[_resolveStatusGroup(status)].tw;
};

window.AppUtils.getStatusColor = function(status) {
    return _STATUS_COLORS[_resolveStatusGroup(status)].hex;
};

window.AppUtils.getStatusBg = function(status) {
    return _STATUS_COLORS[_resolveStatusGroup(status)].rgba;
};

window.AppUtils.getStatusText = function(status) {
    var labels = {
        'Tescil Edildi': t('status.registered'), 'Yayında': t('status.published'), 'Başvuruldu': t('status.applied'),
        'İtiraz Edildi': t('status.opposed'), 'Reddedildi': t('status.refused'), 'Geri Çekildi': t('status.withdrawn'),
        'Süresi Doldu': t('status.expired'), 'Yenilendi': t('status.renewed'),
        'İptal Edildi': t('status.cancelled'), 'Devredildi': t('status.transferred'),
        'Kısmi Red': t('status.partial_refusal')
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
var getStatusColor = window.AppUtils.getStatusColor;
var getStatusBg = window.AppUtils.getStatusBg;
var getStatusText = window.AppUtils.getStatusText;
var formatHolderDate = window.AppUtils.formatHolderDate;

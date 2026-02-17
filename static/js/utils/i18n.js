/**
 * i18n.js - Internationalization system
 * Provides t(key, params) function for all UI strings.
 * Locale files: /static/locales/{locale}.json
 */
window.AppI18n = window.AppI18n || {};

// Default locale + loaded translations
window.AppI18n._locale = localStorage.getItem('app_locale') || 'tr';
window.AppI18n._strings = {};
window.AppI18n._ready = false;
window.AppI18n._callbacks = [];

/**
 * Deep-get a nested key from an object.
 * e.g. _deepGet(obj, 'search.button') -> obj.search.button
 */
function _deepGet(obj, path) {
    var parts = path.split('.');
    var cur = obj;
    for (var i = 0; i < parts.length; i++) {
        if (cur === undefined || cur === null) return undefined;
        cur = cur[parts[i]];
    }
    return cur;
}

/**
 * t(key, params) - Main translation function.
 * @param {string} key - Dot-notation key, e.g. 'search.button'
 * @param {object} params - Optional interpolation params, e.g. { count: 5 }
 * @returns {string} Translated string, or the key if not found
 *
 * Interpolation: {count} in the string is replaced by params.count
 */
window.AppI18n.t = function(key, params) {
    var val = _deepGet(window.AppI18n._strings, key);
    if (val === undefined) return key;
    if (typeof val !== 'string') return key;
    if (params) {
        Object.keys(params).forEach(function(k) {
            val = val.replace(new RegExp('\\{' + k + '\\}', 'g'), params[k]);
        });
    }
    return val;
};

/**
 * getLocale() - Returns current locale code ('tr', 'en', 'ar')
 */
window.AppI18n.getLocale = function() {
    return window.AppI18n._locale;
};

/**
 * getDir() - Returns text direction for current locale ('ltr' or 'rtl')
 */
window.AppI18n.getDir = function() {
    return window.AppI18n._strings.dir || 'ltr';
};

/**
 * setLocale(locale) - Switch to a new locale.
 * Fetches the locale file, updates strings, persists preference,
 * applies RTL/LTR, and dispatches 'locale-changed' event.
 */
window.AppI18n.setLocale = async function(locale) {
    try {
        var opts = typeof AbortSignal.timeout === 'function' ? { signal: AbortSignal.timeout(10000) } : {};
        var res = await fetch('/static/locales/' + locale + '.json?v=28', opts);
        if (!res.ok) throw new Error('Locale file not found: ' + locale);
        var data = await res.json();
        window.AppI18n._strings = data;
        window.AppI18n._locale = locale;
        window.AppI18n._ready = true;
        localStorage.setItem('app_locale', locale);

        // Apply text direction
        var dir = data.dir || 'ltr';
        document.documentElement.setAttribute('dir', dir);
        document.documentElement.setAttribute('lang', locale);
        document.body.style.direction = dir;

        // Apply/remove RTL class
        if (dir === 'rtl') {
            document.documentElement.classList.add('rtl');
        } else {
            document.documentElement.classList.remove('rtl');
        }

        // Dispatch event for Alpine.js and other listeners to re-render
        window.dispatchEvent(new CustomEvent('locale-changed', { detail: { locale: locale, dir: dir } }));

        // Run any queued callbacks
        window.AppI18n._callbacks.forEach(function(cb) { try { cb(); } catch(e) {} });

    } catch (e) {
        console.error('Failed to load locale:', locale, e);
    }
};

/**
 * onReady(callback) - Execute callback when locale strings are loaded.
 * If already loaded, executes immediately.
 */
window.AppI18n.onReady = function(callback) {
    if (window.AppI18n._ready) {
        callback();
    } else {
        window.AppI18n._callbacks.push(callback);
    }
};

/**
 * formatDateLocale(isoStr) - Format a date string using current locale.
 */
window.AppI18n.formatDateLocale = function(isoStr) {
    if (!isoStr) return '';
    try {
        var locale = window.AppI18n._locale === 'ar' ? 'ar-SA' : (window.AppI18n._locale === 'en' ? 'en-US' : 'tr-TR');
        var d = new Date(isoStr);
        return d.toLocaleDateString(locale) + ' ' + d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
    } catch (e) { return isoStr; }
};

// Expose as global
var t = window.AppI18n.t;

// Auto-load saved locale on script load
window.AppI18n.setLocale(window.AppI18n._locale);

/**
 * i18n.js - Internationalization system
 * Provides t(key, params) function for all UI strings.
 * Locale files: /static/locales/{locale}.json
 */
window.AppI18n = window.AppI18n || {};

function _safeStorageGet(key) {
    try {
        if (!window.localStorage) return null;
        return window.localStorage.getItem(key);
    } catch (e) {
        return null;
    }
}

function _safeStorageSet(key, value) {
    try {
        if (!window.localStorage) return;
        window.localStorage.setItem(key, value);
    } catch (e) {}
}

function _safeStorageRemove(key) {
    try {
        if (!window.localStorage) return;
        window.localStorage.removeItem(key);
    } catch (e) {}
}

// Default locale + loaded translations
window.AppI18n._locale = _safeStorageGet('app_locale') || 'tr';
window.AppI18n._strings = {};
window.AppI18n._ready = false;
window.AppI18n._callbacks = [];
window.AppI18n._localeAssetVersion = '56';
window.AppI18n._localeBundleCachePrefix = 'app_locale_bundle::';
window.AppI18n._requestToken = 0;

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

window.AppI18n._getLocaleCacheKey = function(locale) {
    return window.AppI18n._localeBundleCachePrefix + locale + '::v' + window.AppI18n._localeAssetVersion;
};

window.AppI18n._readCachedLocaleData = function(locale) {
    var raw = _safeStorageGet(window.AppI18n._getLocaleCacheKey(locale));
    if (!raw) return null;
    try {
        var data = JSON.parse(raw);
        return data && typeof data === 'object' ? data : null;
    } catch (e) {
        _safeStorageRemove(window.AppI18n._getLocaleCacheKey(locale));
        return null;
    }
};

window.AppI18n._pruneLocaleCache = function(locale) {
    try {
        if (!window.localStorage) return;
        var keepKey = window.AppI18n._getLocaleCacheKey(locale);
        var prefix = window.AppI18n._localeBundleCachePrefix + locale + '::v';
        for (var i = window.localStorage.length - 1; i >= 0; i--) {
            var key = window.localStorage.key(i);
            if (key && key.indexOf(prefix) === 0 && key !== keepKey) {
                window.localStorage.removeItem(key);
            }
        }
    } catch (e) {}
};

window.AppI18n._writeCachedLocaleData = function(locale, data) {
    _safeStorageSet(window.AppI18n._getLocaleCacheKey(locale), JSON.stringify(data));
    window.AppI18n._pruneLocaleCache(locale);
};

window.AppI18n._applyLocaleData = function(locale, data, options) {
    if (!data || typeof data !== 'object') return false;

    window.AppI18n._strings = data;
    window.AppI18n._locale = locale;
    window.AppI18n._ready = true;
    _safeStorageSet('app_locale', locale);

    if (!(options && options.persistCache === false)) {
        window.AppI18n._writeCachedLocaleData(locale, data);
    }

    // Apply text direction
    var dir = data.dir || 'ltr';
    document.documentElement.setAttribute('dir', dir);
    document.documentElement.setAttribute('lang', locale);
    if (document.body) {
        document.body.style.direction = dir;
    }

    // Apply/remove RTL class
    if (dir === 'rtl') {
        document.documentElement.classList.add('rtl');
    } else {
        document.documentElement.classList.remove('rtl');
    }

    // Dispatch event for Alpine.js and other listeners to re-render
    if (!(options && options.dispatch === false)) {
        window.dispatchEvent(new CustomEvent('locale-changed', { detail: { locale: locale, dir: dir } }));
    }

    // Run any queued callbacks
    window.AppI18n._callbacks.forEach(function(cb) { try { cb(); } catch(e) {} });
    return true;
};

window.AppI18n._hydrateLocaleFromCache = function(locale, dispatch) {
    var data = window.AppI18n._readCachedLocaleData(locale);
    if (!data) return false;
    return window.AppI18n._applyLocaleData(locale, data, {
        dispatch: dispatch !== false,
        persistCache: false
    });
};

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
window.AppI18n.setLocale = async function(locale, options) {
    locale = locale || 'tr';
    options = options || {};
    var requestToken = ++window.AppI18n._requestToken;

    if (!options.skipCacheHydrate) {
        window.AppI18n._hydrateLocaleFromCache(locale, options.dispatchCached !== false);
    }

    try {
        var opts = {};
        if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
            // Locale bundles can be fetched on a cold app shell, so give them a
            // slightly wider timeout before we log a hard failure to the console.
            opts.signal = AbortSignal.timeout(20000);
        }
        var res = await fetch('/static/locales/' + locale + '.json?v=' + window.AppI18n._localeAssetVersion, opts);
        if (!res.ok) throw new Error('Locale file not found: ' + locale);
        var data = await res.json();
        if (requestToken !== window.AppI18n._requestToken) return;
        window.AppI18n._applyLocaleData(locale, data);

    } catch (e) {
        if (requestToken !== window.AppI18n._requestToken) return;
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

// Restore the last versioned locale bundle synchronously so Alpine bindings can
// render translated copy immediately while the network refresh happens.
window.AppI18n._hydrateLocaleFromCache(window.AppI18n._locale, false);

// Auto-load saved locale on script load
window.AppI18n.setLocale(window.AppI18n._locale, { skipCacheHydrate: true });

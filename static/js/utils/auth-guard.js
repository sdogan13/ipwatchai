/**
 * auth-guard.js - shared browser-side session expiry guard.
 *
 * The API enforces JWT validity. This file keeps the browser UI in sync by
 * clearing stale auth state and redirecting protected pages before stale UI is
 * left on screen.
 */
(function () {
    'use strict';

    var AppAuth = window.AppAuth = window.AppAuth || {};
    var ACCESS_TOKEN_KEYS = ['auth_token', 'access_token', 'token'];
    var AUTH_STATE_KEYS = ACCESS_TOKEN_KEYS.concat(['refresh_token']);
    var EXPIRY_SKEW_MS = 5000;
    var MAX_TIMER_DELAY_MS = 2147483647;
    var expiryTimer = null;
    var redirecting = false;

    function storageAreas() {
        return [window.localStorage, window.sessionStorage];
    }

    function storageGet(storage, key) {
        try { return storage.getItem(key); } catch (e) { return null; }
    }

    function storageSet(storage, key, value) {
        try { storage.setItem(key, value); } catch (e) { /* ignore */ }
    }

    function storageRemove(storage, key) {
        try { storage.removeItem(key); } catch (e) { /* ignore */ }
    }

    function forEachAuthKey(callback) {
        storageAreas().forEach(function (storage) {
            AUTH_STATE_KEYS.forEach(function (key) {
                callback(storage, key);
            });
        });
    }

    function decodeBase64Url(value) {
        var base64 = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
        while (base64.length % 4) base64 += '=';
        var raw = window.atob(base64);
        try {
            return decodeURIComponent(Array.prototype.map.call(raw, function (char) {
                return '%' + ('00' + char.charCodeAt(0).toString(16)).slice(-2);
            }).join(''));
        } catch (e) {
            return raw;
        }
    }

    function getTokenPayload(token) {
        if (!token || String(token).split('.').length < 2) return null;
        try {
            return JSON.parse(decodeBase64Url(String(token).split('.')[1]));
        } catch (e) {
            return null;
        }
    }

    function getTokenExpiryMs(token) {
        var payload = getTokenPayload(token);
        var exp = payload && Number(payload.exp);
        return Number.isFinite(exp) && exp > 0 ? exp * 1000 : 0;
    }

    function getStoredTokenRaw() {
        var areas = storageAreas();
        for (var i = 0; i < areas.length; i++) {
            for (var j = 0; j < ACCESS_TOKEN_KEYS.length; j++) {
                var token = storageGet(areas[i], ACCESS_TOKEN_KEYS[j]);
                if (token && String(token).trim()) return String(token).trim();
            }
        }
        return '';
    }

    function isTokenExpired(token, skewMs) {
        var expiryMs = getTokenExpiryMs(token);
        if (!expiryMs) return !!token;
        return Date.now() >= expiryMs - (skewMs || 0);
    }

    function clearExpiryTimer() {
        if (expiryTimer) {
            window.clearTimeout(expiryTimer);
            expiryTimer = null;
        }
    }

    function clearAuthState() {
        clearExpiryTimer();
        forEachAuthKey(function (storage, key) {
            storageRemove(storage, key);
        });
        AppAuth.currentUserPlan = 'free';
        AppAuth.currentUserRole = '';
        AppAuth.currentUserName = '';
        AppAuth.currentUserIsSuperadmin = false;
        AppAuth.usage = null;
    }

    function loginUrl(reason) {
        var url = new URL('/?login=1', window.location.origin);
        if (reason === 'expired' || reason === 'unauthorized') {
            url.searchParams.set('session', 'expired');
        }
        return url.pathname + url.search + url.hash;
    }

    function queueReturnToCurrentPage() {
        var path = window.location.pathname + window.location.search + window.location.hash;
        if (!path || path === '/' || path.indexOf('/?login=') === 0) return;
        try {
            window.localStorage.setItem('pending_post_auth_redirect', path);
        } catch (e) { /* ignore */ }
    }

    function isProtectedPage() {
        if (window.IPWATCH_AUTH_PROTECTED) return true;
        return /^\/(?:dashboard|admin)(?:\/|$)/.test(window.location.pathname);
    }

    function redirectToLogin(reason) {
        if (redirecting) return;
        redirecting = true;
        queueReturnToCurrentPage();
        window.location.replace(loginUrl(reason));
    }

    function expireSession(options) {
        options = options || {};
        clearAuthState();
        try {
            window.dispatchEvent(new CustomEvent('app-auth-expired', {
                detail: { reason: options.reason || 'expired' }
            }));
        } catch (e) { /* ignore */ }
        if (options.redirect !== false) {
            redirectToLogin(options.reason || 'expired');
        }
    }

    function logout(options) {
        options = options || {};
        clearAuthState();
        if (options.redirect === false) return;
        window.location.href = options.redirectTo || '/';
    }

    function scheduleExpiryCheck(token) {
        clearExpiryTimer();
        var expiryMs = getTokenExpiryMs(token);
        if (!expiryMs) return;
        var delay = expiryMs - Date.now();
        if (delay <= 0) {
            expireSession({ reason: 'expired' });
            return;
        }
        expiryTimer = window.setTimeout(function () {
            var currentToken = getStoredTokenRaw();
            if (!currentToken || isTokenExpired(currentToken, 0)) {
                expireSession({ reason: 'expired' });
                return;
            }
            scheduleExpiryCheck(currentToken);
        }, Math.min(delay, MAX_TIMER_DELAY_MS));
    }

    function getToken() {
        var token = getStoredTokenRaw();
        if (token && isTokenExpired(token, EXPIRY_SKEW_MS)) {
            clearAuthState();
            return '';
        }
        if (token) scheduleExpiryCheck(token);
        return token;
    }

    function hasValidAccessToken() {
        return !!getToken();
    }

    function validatePageAuth() {
        var token = getStoredTokenRaw();
        if (!token) {
            if (isProtectedPage()) redirectToLogin('missing');
            return false;
        }
        if (isTokenExpired(token, 0)) {
            clearAuthState();
            if (isProtectedPage() || window.IPWATCH_AUTH_REDIRECT_ON_EXPIRE) {
                redirectToLogin('expired');
            }
            return false;
        }
        scheduleExpiryCheck(token);
        return true;
    }

    function storeTokenPair(data, options) {
        options = options || {};
        if (!data || !data.access_token) return;
        clearAuthState();
        var storage = options.session ? window.sessionStorage : window.localStorage;
        storageSet(storage, 'auth_token', data.access_token);
        if (data.refresh_token) storageSet(storage, 'refresh_token', data.refresh_token);
        scheduleExpiryCheck(data.access_token);
    }

    function isIgnoredUnauthorizedUrl(url) {
        try {
            var parsed = new URL(url, window.location.origin);
            return [
                '/api/v1/auth/login',
                '/api/v1/auth/register',
                '/api/v1/auth/forgot-password',
                '/api/v1/auth/reset-password',
                '/api/v1/auth/refresh',
                // Class-suggester endpoints intentionally return 401 with an
                // `anon_limit_reached` payload to trigger the upgrade modal.
                // That 401 is NOT a session expiry — never wipe tokens or
                // redirect to /login, or the upgrade modal vanishes.
                '/api/suggest-classes',
                '/api/v1/tools/suggest-locarno-classes'
            ].indexOf(parsed.pathname) >= 0;
        } catch (e) {
            return false;
        }
    }

    function handleUnauthorized(response, requestUrl) {
        var url = (response && response.url) || requestUrl;
        if (url && isIgnoredUnauthorizedUrl(url)) return;
        expireSession({ reason: 'unauthorized' });
    }

    function installFetchInterceptor() {
        if (AppAuth._fetchInterceptorInstalled || typeof window.fetch !== 'function') return;
        AppAuth._fetchInterceptorInstalled = true;
        var nativeFetch = window.fetch;
        window.fetch = function () {
            var requestUrl = arguments[0] && arguments[0].url ? arguments[0].url : arguments[0];
            return nativeFetch.apply(this, arguments).then(function (response) {
                if (response && response.status === 401) {
                    handleUnauthorized(response, requestUrl);
                }
                return response;
            });
        };
    }

    AppAuth.getAuthToken = getToken;
    AppAuth.getToken = getToken;
    AppAuth.getTokenPayload = function () { return getTokenPayload(getToken()); };
    AppAuth.getTokenExpiryMs = function () { return getTokenExpiryMs(getToken()); };
    AppAuth.hasValidAccessToken = hasValidAccessToken;
    AppAuth.clearAuthState = clearAuthState;
    AppAuth.expireSession = expireSession;
    AppAuth.handleUnauthorized = handleUnauthorized;
    AppAuth.redirectToLogin = redirectToLogin;
    AppAuth.logout = logout;
    AppAuth.storeTokenPair = storeTokenPair;
    AppAuth.validatePageAuth = validatePageAuth;

    installFetchInterceptor();
    validatePageAuth();

    window.addEventListener('focus', validatePageAuth);
    window.addEventListener('pageshow', validatePageAuth);
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) validatePageAuth();
    });
    window.addEventListener('storage', function (event) {
        if (AUTH_STATE_KEYS.indexOf(event.key) >= 0) validatePageAuth();
    });
})();

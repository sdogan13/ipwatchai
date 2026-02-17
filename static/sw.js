/**
 * Service Worker — IP WATⒸH AI v6
 * Minimal: only cache static assets (JS, CSS, images).
 * Never cache HTML or API responses.
 */
var CACHE_NAME = 'ipwatch-v6';

// Install: skip waiting immediately
self.addEventListener('install', function(event) {
    self.skipWaiting();
});

// Activate: clean ALL old caches, claim clients
self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys().then(function(names) {
            return Promise.all(
                names.filter(function(name) { return name !== CACHE_NAME; })
                    .map(function(name) { return caches.delete(name); })
            );
        })
    );
    self.clients.claim();
});

// Fetch: network-first, only cache static assets
self.addEventListener('fetch', function(event) {
    var url = new URL(event.request.url);

    // Only handle same-origin GET requests
    if (url.origin !== self.location.origin) return;
    if (event.request.method !== 'GET') return;

    // Never intercept HTML pages or API calls — let them go straight to network
    var accept = event.request.headers.get('Accept') || '';
    if (accept.indexOf('text/html') !== -1) return;
    if (url.pathname.indexOf('/api/') === 0) return;

    // For static assets (JS, CSS, images): network-first with cache fallback
    var isStatic = url.pathname.indexOf('/static/') === 0;
    if (!isStatic) return;

    event.respondWith(
        fetch(event.request).then(function(response) {
            if (response.ok) {
                var responseClone = response.clone();
                caches.open(CACHE_NAME).then(function(cache) {
                    cache.put(event.request, responseClone);
                });
            }
            return response;
        }).catch(function() {
            return caches.match(event.request);
        })
    );
});

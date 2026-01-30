// Service Worker for Safron Live Prices PWA
const CACHE_NAME = 'safron-prices-v1';
const urlsToCache = [
    '/',
    '/index.html',
    '/logo.png',
    '/manifest.json'
];

// Install event
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(urlsToCache))
            .then(() => self.skipWaiting())
    );
});

// Activate event
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames.map(cacheName => {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// Fetch event - Network first for API, cache first for assets
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);
    
    // Always fetch API calls from network (live prices)
    if (url.pathname.includes('/api/')) {
        event.respondWith(fetch(event.request));
        return;
    }
    
    // For other requests, try network first, fall back to cache
    event.respondWith(
        fetch(event.request)
            .then(response => {
                // Clone and cache the response
                const responseClone = response.clone();
                caches.open(CACHE_NAME).then(cache => {
                    cache.put(event.request, responseClone);
                });
                return response;
            })
            .catch(() => caches.match(event.request))
    );
});

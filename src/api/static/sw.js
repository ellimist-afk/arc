/**
 * TalkBot Service Worker
 * Provides offline functionality, caching, and background sync
 */

const CACHE_NAME = 'talkbot-v1.0.0';
const STATIC_CACHE_NAME = 'talkbot-static-v1.0.0';
const API_CACHE_NAME = 'talkbot-api-v1.0.0';
const IMAGE_CACHE_NAME = 'talkbot-images-v1.0.0';

// Cache duration in milliseconds
const CACHE_DURATION = {
    STATIC: 7 * 24 * 60 * 60 * 1000,    // 1 week
    API: 5 * 60 * 1000,                  // 5 minutes
    IMAGES: 30 * 24 * 60 * 60 * 1000,    // 30 days
    HTML: 1 * 60 * 1000                  // 1 minute
};

// Static assets to cache immediately
const STATIC_ASSETS = [
    '/',
    '/dashboard',
    '/settings',
    '/analytics',
    '/monitoring',
    '/static/v2/css/theme-twitch.css',
    '/static/v2/css/mobile-first.css',
    '/static/v2/css/base.css',
    '/static/v2/js/api-client.js',
    '/static/v2/js/mobile-navigation.js',
    '/static/v2/js/websocket-manager.js',
    '/static/manifest.json',
    '/static/icons/icon-192x192.png',
    '/static/icons/icon-512x512.png'
];

// API endpoints to cache
const API_CACHE_PATTERNS = [
    /^\/api\/v2\/monitoring\/health$/,
    /^\/api\/v2\/monitoring\/metrics$/,
    /^\/api\/v2\/analytics\/\w+\/(engagement|metrics|voice)$/,
    /^\/api\/v2\/settings\/consolidated\/\w+$/,
    /^\/api\/v2\/bots\/\w+\/status$/
];

// Image patterns to cache
const IMAGE_PATTERNS = [
    /\.(png|jpg|jpeg|gif|webp|svg)$/i,
    /^\/static\/icons\//,
    /^\/static\/screenshots\//,
    /^\/static\/images\//
];

// =============================================================================
// SERVICE WORKER EVENTS
// =============================================================================

self.addEventListener('install', (event) => {
    console.log('[SW] Installing service worker');
    
    event.waitUntil(
        Promise.all([
            // Cache static assets
            caches.open(STATIC_CACHE_NAME).then(cache => {
                console.log('[SW] Caching static assets');
                return cache.addAll(STATIC_ASSETS);
            }),
            
            // Skip waiting to activate immediately
            self.skipWaiting()
        ])
    );
});

self.addEventListener('activate', (event) => {
    console.log('[SW] Activating service worker');
    
    event.waitUntil(
        Promise.all([
            // Clean up old caches
            cleanupOldCaches(),
            
            // Claim all clients
            self.clients.claim()
        ])
    );
});

self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);
    
    // Skip non-GET requests and extension requests
    if (request.method !== 'GET' || url.protocol === 'chrome-extension:') {
        return;
    }
    
    // Route to appropriate cache strategy
    if (isStaticAsset(url)) {
        event.respondWith(cacheFirst(request, STATIC_CACHE_NAME));
    } else if (isAPIRequest(url)) {
        event.respondWith(networkFirst(request, API_CACHE_NAME, CACHE_DURATION.API));
    } else if (isImageRequest(url)) {
        event.respondWith(cacheFirst(request, IMAGE_CACHE_NAME, CACHE_DURATION.IMAGES));
    } else if (isHTMLRequest(url)) {
        event.respondWith(networkFirst(request, CACHE_NAME, CACHE_DURATION.HTML));
    } else {
        // Default: network with fallback
        event.respondWith(networkWithFallback(request));
    }
});

// Handle background sync for offline actions
self.addEventListener('sync', (event) => {
    console.log('[SW] Background sync:', event.tag);
    
    switch (event.tag) {
        case 'background-sync-settings':
            event.waitUntil(syncPendingSettings());
            break;
        case 'background-sync-analytics':
            event.waitUntil(syncPendingAnalytics());
            break;
    }
});

// Handle push notifications
self.addEventListener('push', (event) => {
    console.log('[SW] Push received:', event.data?.text());
    
    if (!event.data) return;
    
    try {
        const data = event.data.json();
        const options = {
            body: data.message || 'New notification from TalkBot',
            icon: '/static/icons/icon-192x192.png',
            badge: '/static/icons/badge-72x72.png',
            tag: data.tag || 'talkbot-notification',
            data: data.data || {},
            actions: [
                {
                    action: 'open',
                    title: 'Open TalkBot',
                    icon: '/static/icons/action-open.png'
                },
                {
                    action: 'dismiss',
                    title: 'Dismiss',
                    icon: '/static/icons/action-dismiss.png'
                }
            ],
            requireInteraction: data.requireInteraction || false,
            silent: data.silent || false
        };
        
        event.waitUntil(
            self.registration.showNotification(
                data.title || 'TalkBot',
                options
            )
        );
    } catch (error) {
        console.error('[SW] Error processing push notification:', error);
    }
});

// Handle notification clicks
self.addEventListener('notificationclick', (event) => {
    console.log('[SW] Notification clicked:', event.notification.tag, event.action);
    
    event.notification.close();
    
    if (event.action === 'dismiss') {
        return;
    }
    
    // Open or focus the app
    event.waitUntil(
        clients.matchAll({ type: 'window' })
            .then(clientList => {
                // Try to focus existing window
                for (const client of clientList) {
                    if (client.url.includes('dashboard') && 'focus' in client) {
                        return client.focus();
                    }
                }
                
                // Open new window
                if (clients.openWindow) {
                    return clients.openWindow('/dashboard');
                }
            })
    );
});

// =============================================================================
// CACHE STRATEGIES
// =============================================================================

async function cacheFirst(request, cacheName = CACHE_NAME, maxAge = CACHE_DURATION.STATIC) {
    try {
        const cache = await caches.open(cacheName);
        const cached = await cache.match(request);
        
        if (cached) {
            // Check if cache is still valid
            const cachedDate = new Date(cached.headers.get('sw-cached-date') || 0);
            const now = new Date();
            
            if (now.getTime() - cachedDate.getTime() < maxAge) {
                return cached;
            }
        }
        
        // Fetch fresh version
        const response = await fetch(request);
        
        if (response.ok) {
            const responseToCache = response.clone();
            
            // Add timestamp header
            const headers = new Headers(responseToCache.headers);
            headers.set('sw-cached-date', new Date().toISOString());
            
            const cachedResponse = new Response(responseToCache.body, {
                status: responseToCache.status,
                statusText: responseToCache.statusText,
                headers
            });
            
            cache.put(request, cachedResponse);
        }
        
        return response;
    } catch (error) {
        // Return cached version if network fails
        const cache = await caches.open(cacheName);
        const cached = await cache.match(request);
        
        if (cached) {
            return cached;
        }
        
        // Return offline fallback
        return new Response('Offline', {
            status: 503,
            statusText: 'Service Unavailable',
            headers: { 'Content-Type': 'text/plain' }
        });
    }
}

async function networkFirst(request, cacheName = CACHE_NAME, maxAge = CACHE_DURATION.API) {
    try {
        const response = await fetch(request);
        
        if (response.ok) {
            const cache = await caches.open(cacheName);
            const responseToCache = response.clone();
            
            // Add timestamp header
            const headers = new Headers(responseToCache.headers);
            headers.set('sw-cached-date', new Date().toISOString());
            
            const cachedResponse = new Response(responseToCache.body, {
                status: responseToCache.status,
                statusText: responseToCache.statusText,
                headers
            });
            
            cache.put(request, cachedResponse);
        }
        
        return response;
    } catch (error) {
        // Fallback to cache
        const cache = await caches.open(cacheName);
        const cached = await cache.match(request);
        
        if (cached) {
            return cached;
        }
        
        // Return error response for API calls
        if (isAPIRequest(new URL(request.url))) {
            return new Response(JSON.stringify({
                error: 'Offline',
                message: 'No network connection available'
            }), {
                status: 503,
                statusText: 'Service Unavailable',
                headers: { 'Content-Type': 'application/json' }
            });
        }
        
        // Return offline page for navigation
        return getOfflinePage();
    }
}

async function networkWithFallback(request) {
    try {
        return await fetch(request);
    } catch (error) {
        // Return cached version or offline page
        const cache = await caches.open(CACHE_NAME);
        const cached = await cache.match(request);
        
        if (cached) {
            return cached;
        }
        
        return getOfflinePage();
    }
}

// =============================================================================
// UTILITY FUNCTIONS
// =============================================================================

function isStaticAsset(url) {
    return STATIC_ASSETS.some(asset => url.pathname === asset) ||
           url.pathname.startsWith('/static/') ||
           url.pathname === '/manifest.json';
}

function isAPIRequest(url) {
    return url.pathname.startsWith('/api/') ||
           API_CACHE_PATTERNS.some(pattern => pattern.test(url.pathname));
}

function isImageRequest(url) {
    return IMAGE_PATTERNS.some(pattern => pattern.test(url.pathname));
}

function isHTMLRequest(url) {
    return url.pathname === '/' ||
           !url.pathname.includes('.') ||
           url.pathname.endsWith('.html');
}

async function cleanupOldCaches() {
    const cacheNames = await caches.keys();
    const validCacheNames = [CACHE_NAME, STATIC_CACHE_NAME, API_CACHE_NAME, IMAGE_CACHE_NAME];
    
    return Promise.all(
        cacheNames
            .filter(cacheName => !validCacheNames.includes(cacheName))
            .map(cacheName => {
                console.log('[SW] Deleting old cache:', cacheName);
                return caches.delete(cacheName);
            })
    );
}

async function getOfflinePage() {
    try {
        const cache = await caches.open(STATIC_CACHE_NAME);
        return await cache.match('/') || new Response(`
            <!DOCTYPE html>
            <html>
            <head>
                <title>TalkBot - Offline</title>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                    body {
                        font-family: Inter, sans-serif;
                        background: #0D1117;
                        color: #FFFFFF;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        min-height: 100vh;
                        margin: 0;
                        text-align: center;
                    }
                    .offline-container {
                        max-width: 400px;
                        padding: 2rem;
                    }
                    .offline-icon {
                        font-size: 4rem;
                        margin-bottom: 1rem;
                    }
                    .offline-title {
                        font-size: 1.5rem;
                        margin-bottom: 1rem;
                        color: #9146FF;
                    }
                    .offline-message {
                        color: #8B949E;
                        margin-bottom: 2rem;
                    }
                    .retry-button {
                        background: #9146FF;
                        color: white;
                        border: none;
                        padding: 0.75rem 1.5rem;
                        border-radius: 0.5rem;
                        cursor: pointer;
                        font-size: 1rem;
                    }
                </style>
            </head>
            <body>
                <div class="offline-container">
                    <div class="offline-icon">📡</div>
                    <h1 class="offline-title">You're Offline</h1>
                    <p class="offline-message">
                        TalkBot needs an internet connection to function properly.
                        Please check your connection and try again.
                    </p>
                    <button class="retry-button" onclick="window.location.reload()">
                        Try Again
                    </button>
                </div>
            </body>
            </html>
        `, {
            headers: { 'Content-Type': 'text/html' }
        });
    } catch (error) {
        return new Response('Offline', { status: 503 });
    }
}

// =============================================================================
// BACKGROUND SYNC FUNCTIONS
// =============================================================================

async function syncPendingSettings() {
    try {
        // Get pending settings from IndexedDB
        const pendingSettings = await getPendingData('settings');
        
        for (const setting of pendingSettings) {
            try {
                const response = await fetch(setting.url, {
                    method: setting.method,
                    headers: setting.headers,
                    body: setting.body
                });
                
                if (response.ok) {
                    await removePendingData('settings', setting.id);
                    console.log('[SW] Synced setting:', setting.id);
                }
            } catch (error) {
                console.error('[SW] Failed to sync setting:', setting.id, error);
            }
        }
    } catch (error) {
        console.error('[SW] Background sync failed for settings:', error);
    }
}

async function syncPendingAnalytics() {
    try {
        // Analytics sync implementation
        console.log('[SW] Syncing pending analytics data');
        
        // Get pending analytics from IndexedDB
        const pendingData = await getPendingData('analytics');
        
        for (const data of pendingData) {
            try {
                // Send analytics data
                const response = await fetch('/api/v2/analytics/sync', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data.payload)
                });
                
                if (response.ok) {
                    await removePendingData('analytics', data.id);
                }
            } catch (error) {
                console.error('[SW] Failed to sync analytics:', error);
            }
        }
    } catch (error) {
        console.error('[SW] Background sync failed for analytics:', error);
    }
}

// Simple IndexedDB wrapper for pending data
async function getPendingData(type) {
    // Simplified implementation - would use IndexedDB in production
    return [];
}

async function removePendingData(type, id) {
    // Simplified implementation - would use IndexedDB in production
    return true;
}

// =============================================================================
// SERVICE WORKER MESSAGING
// =============================================================================

self.addEventListener('message', (event) => {
    if (event.data && event.data.type) {
        switch (event.data.type) {
            case 'SKIP_WAITING':
                self.skipWaiting();
                break;
                
            case 'CACHE_UPDATE':
                caches.open(CACHE_NAME).then(cache => {
                    cache.addAll(event.data.urls || []);
                });
                break;
                
            case 'CLEAR_CACHE':
                caches.delete(event.data.cacheName || CACHE_NAME);
                break;
        }
    }
});

console.log('[SW] TalkBot Service Worker loaded');
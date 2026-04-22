// Timer cleanup manager for preventing memory leaks
const timerManager = window.timerManager || { 
    setInterval: (cb, delay) => timerManager.setInterval(cb, delay),
    setTimeout: (cb, delay) => timerManager.setTimeout(cb, delay),
    clearInterval: (id) => timerManager.clearInterval(id),
    clearTimeout: (id) => timerManager.clearTimeout(id)
};

/**
 * TalkBot Cache Manager
 * Efficient client-side caching with TTL support
 */

class CacheManager {
    constructor() {
        this.cache = new Map();
        this.timestamps = new Map();
        this.accessCounts = new Map();
        this.defaultTTL = 60000; // 60 seconds default
        
        // Performance tracking
        this.stats = {
            hits: 0,
            misses: 0,
            evictions: 0,
            totalRequests: 0
        };
        
        // Start cleanup timer
        this.startCleanupTimer();
    }
    
    /**
     * Set a value in the cache with optional TTL
     */
    set(key, value, ttl = this.defaultTTL) {
        // Store the value
        this.cache.set(key, value);
        this.timestamps.set(key, Date.now() + ttl);
        this.accessCounts.set(key, 0);
        
        // Schedule automatic cleanup
        if (ttl > 0) {
            timerManager.setTimeout(() => this.delete(key), ttl);
        }
        
        return value;
    }
    
    /**
     * Get a value from the cache
     */
    get(key) {
        this.stats.totalRequests++;
        
        // Check if key exists and is not expired
        const expiry = this.timestamps.get(key);
        
        if (!expiry || Date.now() > expiry) {
            // Expired or doesn't exist
            this.delete(key);
            this.stats.misses++;
            return null;
        }
        
        // Update access count
        const count = this.accessCounts.get(key) || 0;
        this.accessCounts.set(key, count + 1);
        
        this.stats.hits++;
        return this.cache.get(key);
    }
    
    /**
     * Check if a key exists and is not expired
     */
    has(key) {
        const expiry = this.timestamps.get(key);
        
        if (!expiry || Date.now() > expiry) {
            this.delete(key);
            return false;
        }
        
        return this.cache.has(key);
    }
    
    /**
     * Delete a key from the cache
     */
    delete(key) {
        const deleted = this.cache.delete(key);
        this.timestamps.delete(key);
        this.accessCounts.delete(key);
        
        if (deleted) {
            this.stats.evictions++;
        }
        
        return deleted;
    }
    
    /**
     * Clear all cached data
     */
    clear() {
        const size = this.cache.size;
        
        this.cache.clear();
        this.timestamps.clear();
        this.accessCounts.clear();
        
        this.stats.evictions += size;
    }
    
    /**
     * Get or fetch data with caching
     */
    async fetch(key, fetcher, ttl = this.defaultTTL) {
        // Check cache first
        const cached = this.get(key);
        if (cached !== null) {
            console.log(`[Cache] Hit for key: ${key}`);
            return cached;
        }
        
        console.log(`[Cache] Miss for key: ${key}, fetching...`);
        
        try {
            // Fetch the data
            const data = await fetcher();
            
            // Cache the result
            this.set(key, data, ttl);
            
            return data;
        } catch (error) {
            console.error(`[Cache] Error fetching data for key ${key}:`, error);
            throw error;
        }
    }
    
    /**
     * Batch fetch with caching
     */
    async fetchBatch(requests) {
        const results = [];
        const toFetch = [];
        
        // Check cache for each request
        for (const request of requests) {
            const cached = this.get(request.key);
            
            if (cached !== null) {
                results.push({ key: request.key, data: cached, cached: true });
            } else {
                toFetch.push(request);
            }
        }
        
        // Fetch missing data
        if (toFetch.length > 0) {
            const fetchPromises = toFetch.map(async (request) => {
                try {
                    const data = await request.fetcher();
                    this.set(request.key, data, request.ttl || this.defaultTTL);
                    return { key: request.key, data, cached: false };
                } catch (error) {
                    return { key: request.key, error, cached: false };
                }
            });
            
            const fetchResults = await Promise.all(fetchPromises);
            results.push(...fetchResults);
        }
        
        return results;
    }
    
    /**
     * Get cache statistics
     */
    getStats() {
        const hitRate = this.stats.totalRequests > 0 
            ? (this.stats.hits / this.stats.totalRequests * 100).toFixed(2)
            : 0;
        
        return {
            ...this.stats,
            hitRate: `${hitRate}%`,
            cacheSize: this.cache.size,
            averageAccessCount: this.getAverageAccessCount()
        };
    }
    
    /**
     * Get average access count for cached items
     */
    getAverageAccessCount() {
        if (this.accessCounts.size === 0) return 0;
        
        let total = 0;
        for (const count of this.accessCounts.values()) {
            total += count;
        }
        
        return (total / this.accessCounts.size).toFixed(2);
    }
    
    /**
     * Get most accessed keys
     */
    getMostAccessed(limit = 10) {
        const entries = Array.from(this.accessCounts.entries());
        entries.sort((a, b) => b[1] - a[1]);
        
        return entries.slice(0, limit).map(([key, count]) => ({
            key,
            count,
            value: this.cache.get(key)
        }));
    }
    
    /**
     * Cleanup expired entries periodically
     */
    startCleanupTimer() {
        // Run cleanup every minute
        timerManager.setInterval(() => {
            this.cleanup();
        }, 60000);
    }
    
    /**
     * Remove expired entries
     */
    cleanup() {
        const now = Date.now();
        let cleaned = 0;
        
        for (const [key, expiry] of this.timestamps.entries()) {
            if (now > expiry) {
                this.delete(key);
                cleaned++;
            }
        }
        
        if (cleaned > 0) {
            console.log(`[Cache] Cleaned up ${cleaned} expired entries`);
        }
    }
    
    /**
     * Create a namespaced cache key
     */
    static createKey(...parts) {
        return parts.join(':');
    }
    
    /**
     * Parse a namespaced cache key
     */
    static parseKey(key) {
        return key.split(':');
    }
}

// Specialized cache for API responses
class APICache extends CacheManager {
    constructor() {
        super();
        
        // Different TTLs for different endpoint types
        this.ttlConfig = {
            'bot-status': 5000,      // 5 seconds for real-time status
            'settings': 30000,       // 30 seconds for settings
            'analytics': 30000,      // 30 seconds for analytics
            'memory': 60000,         // 1 minute for memory stats
            'health': 15000,         // 15 seconds for health checks
            'default': 60000         // 1 minute default
        };
    }
    
    /**
     * Create cache key for API endpoint
     */
    createAPIKey(endpoint, params = {}) {
        const paramString = Object.keys(params)
            .sort()
            .map(key => `${key}=${params[key]}`)
            .join('&');
        
        return paramString ? `${endpoint}?${paramString}` : endpoint;
    }
    
    /**
     * Get TTL for endpoint type
     */
    getTTLForEndpoint(endpoint) {
        // Extract endpoint type from URL
        for (const [type, ttl] of Object.entries(this.ttlConfig)) {
            if (endpoint.includes(type)) {
                return ttl;
            }
        }
        
        return this.ttlConfig.default;
    }
    
    /**
     * Fetch API data with caching
     */
    async fetchAPI(url, options = {}) {
        const key = this.createAPIKey(url, options.params);
        const ttl = options.ttl || this.getTTLForEndpoint(url);
        
        return this.fetch(key, async () => {
            const response = await fetch(url, {
                ...options,
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                    ...options.headers
                }
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            return response.json();
        }, ttl);
    }
    
    /**
     * Invalidate cache for specific patterns
     */
    invalidatePattern(pattern) {
        let invalidated = 0;
        
        for (const key of this.cache.keys()) {
            if (key.includes(pattern)) {
                this.delete(key);
                invalidated++;
            }
        }
        
        console.log(`[APICache] Invalidated ${invalidated} entries matching pattern: ${pattern}`);
        return invalidated;
    }
    
    /**
     * Invalidate cache for specific streamer
     */
    invalidateStreamer(streamerId) {
        return this.invalidatePattern(`/${streamerId}/`);
    }
}

// Create global cache instances
window.cacheManager = new CacheManager();
window.apiCache = new APICache();

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { CacheManager, APICache };
}
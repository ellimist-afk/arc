// Timer cleanup manager for preventing memory leaks
const timerManager = window.timerManager || { 
    setInterval: (cb, delay) => timerManager.setInterval(cb, delay),
    setTimeout: (cb, delay) => timerManager.setTimeout(cb, delay),
    clearInterval: (id) => timerManager.clearInterval(id),
    clearTimeout: (id) => timerManager.clearTimeout(id)
};

/**
 * TalkBot Analytics - Real-time Data Manager
 * Connects analytics mockup to live backend data
 */

class AnalyticsManager {
    constructor(streamerId) {
        this.streamerId = streamerId;
        
        // Data storage
        this.data = {
            engagement: {
                unique_chatters: 0,
                total_messages: 0,
                bot_responses: 0,
                peak_hour: null,
                top_chatters: [],
                message_velocity: []
            },
            performance: {
                response_rate: 0,
                average_response_time: 0,
                success_rate: 100,
                errors_count: 0,
                response_times: [],
                hourly_stats: []
            },
            memory: {
                total_memories: 0,
                storage_size_mb: 0,
                memory_types: {},
                recent_memories: []
            },
            health: {
                cpu_usage: 0,
                memory_usage: 0,
                uptime_hours: 0,
                status: 'healthy',
                components: {}
            }
        };
        
        // Charts storage
        this.charts = {};
        
        // Polling timers
        this.pollTimers = [];
        
        // Cache manager
        this.cache = new Map();
        this.cacheTimestamps = new Map();
        
        // Loading states
        this.loading = {
            engagement: false,
            performance: false,
            memory: false,
            health: false
        };
        
        // Error tracking
        this.errors = {
            engagement: null,
            performance: null,
            memory: null,
            health: null
        };
    }
    
    async init() {
        console.log('[Analytics] Initializing analytics manager...');
        
        // Load all initial data
        await this.loadAllData();
        
        // Initialize charts
        this.initializeCharts();
        
        // Setup real-time updates
        this.setupRealtimeUpdates();
        
        // Start polling for non-realtime data
        this.startPolling();
        
        console.log('[Analytics] Initialization complete');
    }
    
    async loadAllData() {
        // Load all data categories in parallel
        const promises = [
            this.loadEngagement(),
            this.loadPerformance(),
            this.loadMemory(),
            this.loadHealth()
        ];
        
        await Promise.allSettled(promises);
    }
    
    async loadEngagement() {
        const cacheKey = `engagement_${this.streamerId}`;
        const cached = this.getCached(cacheKey, 30000); // 30s cache
        
        if (cached) {
            this.data.engagement = cached;
            this.updateUI('engagement', cached);
            return cached;
        }
        
        this.loading.engagement = true;
        this.errors.engagement = null;
        
        try {
            const response = await fetch(`/api/v2/analytics/${this.streamerId}/engagement`, {
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });
            
            if (!response.ok) {
                if (response.status === 404) {
                    console.log('[Analytics] No engagement data available yet');
                    return;
                }
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            this.data.engagement = { ...this.data.engagement, ...data };
            
            // Cache the data
            this.setCache(cacheKey, this.data.engagement, 30000);
            
            // Update UI
            this.updateUI('engagement', this.data.engagement);
            
            console.log('[Analytics] Engagement data loaded:', this.data.engagement);
            
        } catch (error) {
            console.error('[Analytics] Failed to load engagement:', error);
            this.errors.engagement = error.message;
        } finally {
            this.loading.engagement = false;
        }
    }
    
    async loadPerformance() {
        const cacheKey = `performance_${this.streamerId}`;
        const cached = this.getCached(cacheKey, 30000);
        
        if (cached) {
            this.data.performance = cached;
            this.updateUI('performance', cached);
            return cached;
        }
        
        this.loading.performance = true;
        this.errors.performance = null;
        
        try {
            const response = await fetch(`/api/v2/analytics/${this.streamerId}/performance`, {
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });
            
            if (!response.ok) {
                if (response.status === 404) {
                    console.log('[Analytics] No performance data available yet');
                    return;
                }
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            this.data.performance = { ...this.data.performance, ...data };
            
            // Cache the data
            this.setCache(cacheKey, this.data.performance, 30000);
            
            // Update UI
            this.updateUI('performance', this.data.performance);
            
            // Update charts
            this.updatePerformanceCharts();
            
            console.log('[Analytics] Performance data loaded:', this.data.performance);
            
        } catch (error) {
            console.error('[Analytics] Failed to load performance:', error);
            this.errors.performance = error.message;
        } finally {
            this.loading.performance = false;
        }
    }
    
    async loadMemory() {
        const cacheKey = `memory_${this.streamerId}`;
        const cached = this.getCached(cacheKey, 60000); // 60s cache for memory
        
        if (cached) {
            this.data.memory = cached;
            this.updateUI('memory', cached);
            return cached;
        }
        
        this.loading.memory = true;
        this.errors.memory = null;
        
        try {
            const response = await fetch(`/api/v2/analytics/${this.streamerId}/memory`, {
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });
            
            if (!response.ok) {
                if (response.status === 404) {
                    console.log('[Analytics] No memory data available yet');
                    return;
                }
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            this.data.memory = { ...this.data.memory, ...data };
            
            // Cache the data
            this.setCache(cacheKey, this.data.memory, 60000);
            
            // Update UI
            this.updateUI('memory', this.data.memory);
            
            console.log('[Analytics] Memory data loaded:', this.data.memory);
            
        } catch (error) {
            console.error('[Analytics] Failed to load memory:', error);
            this.errors.memory = error.message;
        } finally {
            this.loading.memory = false;
        }
    }
    
    async loadHealth() {
        const cacheKey = 'health_system';
        const cached = this.getCached(cacheKey, 15000); // 15s cache for health
        
        if (cached) {
            this.data.health = cached;
            this.updateUI('health', cached);
            return cached;
        }
        
        this.loading.health = true;
        this.errors.health = null;
        
        try {
            const response = await fetch('/api/v2/monitoring/health', {
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            this.data.health = { ...this.data.health, ...data };
            
            // Cache the data
            this.setCache(cacheKey, this.data.health, 15000);
            
            // Update UI
            this.updateUI('health', this.data.health);
            
            // Update system health indicators
            this.updateHealthIndicators();
            
            console.log('[Analytics] Health data loaded:', this.data.health);
            
        } catch (error) {
            console.error('[Analytics] Failed to load health:', error);
            this.errors.health = error.message;
        } finally {
            this.loading.health = false;
        }
    }
    
    initializeCharts() {
        // Message Velocity Chart
        const velocityCtx = document.getElementById('message-velocity-chart');
        if (velocityCtx) {
            this.charts.velocity = new Chart(velocityCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Messages/min',
                        data: [],
                        borderColor: '#9146FF',
                        backgroundColor: 'rgba(145, 70, 255, 0.1)',
                        tension: 0.4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        y: { beginAtZero: true }
                    }
                }
            });
        }
        
        // Response Time Chart
        const responseTimeCtx = document.getElementById('response-time-chart');
        if (responseTimeCtx) {
            this.charts.responseTime = new Chart(responseTimeCtx, {
                type: 'bar',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Response Time (s)',
                        data: [],
                        backgroundColor: '#00D4AA'
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        y: { beginAtZero: true }
                    }
                }
            });
        }
        
        // Memory Usage Chart
        const memoryCtx = document.getElementById('memory-usage-chart');
        if (memoryCtx) {
            this.charts.memory = new Chart(memoryCtx, {
                type: 'doughnut',
                data: {
                    labels: ['Short-term', 'Long-term', 'Context'],
                    datasets: [{
                        data: [0, 0, 0],
                        backgroundColor: ['#9146FF', '#00D4AA', '#FFB800']
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: 'bottom'
                        }
                    }
                }
            });
        }
    }
    
    updatePerformanceCharts() {
        // Update response time chart
        if (this.charts.responseTime && this.data.performance.response_times) {
            const times = this.data.performance.response_times.slice(-20); // Last 20
            this.charts.responseTime.data.labels = times.map((_, i) => `${i + 1}`);
            this.charts.responseTime.data.datasets[0].data = times;
            this.charts.responseTime.update();
        }
    }
    
    updateHealthIndicators() {
        // Update CPU indicator
        const cpuElement = document.querySelector('[data-health-cpu]');
        if (cpuElement) {
            cpuElement.textContent = `${Math.round(this.data.health.cpu_usage || 0)}%`;
            cpuElement.classList.toggle('text-red-400', this.data.health.cpu_usage > 80);
            cpuElement.classList.toggle('text-yellow-400', this.data.health.cpu_usage > 60 && this.data.health.cpu_usage <= 80);
            cpuElement.classList.toggle('text-green-400', this.data.health.cpu_usage <= 60);
        }
        
        // Update memory indicator
        const memElement = document.querySelector('[data-health-memory]');
        if (memElement) {
            memElement.textContent = `${Math.round(this.data.health.memory_usage || 0)}%`;
            memElement.classList.toggle('text-red-400', this.data.health.memory_usage > 80);
            memElement.classList.toggle('text-yellow-400', this.data.health.memory_usage > 60 && this.data.health.memory_usage <= 80);
            memElement.classList.toggle('text-green-400', this.data.health.memory_usage <= 60);
        }
    }
    
    setupRealtimeUpdates() {
        // Listen for WebSocket events
        window.addEventListener('ws-metrics', (event) => {
            const data = event.detail;
            console.log('[Analytics] Received metrics update:', data);
            
            // Update engagement metrics
            if (data.messages_received !== undefined) {
                this.data.engagement.total_messages = data.messages_received;
                
                // Update message velocity
                this.updateMessageVelocity(data.messages_received);
            }
            
            if (data.unique_chatters !== undefined) {
                this.data.engagement.unique_chatters = data.unique_chatters;
            }
            
            // Update performance metrics
            if (data.responses_sent !== undefined) {
                this.data.performance.bot_responses = data.responses_sent;
            }
            
            if (data.average_response_time !== undefined) {
                this.data.performance.average_response_time = data.average_response_time;
                
                // Add to response times array
                if (!this.data.performance.response_times) {
                    this.data.performance.response_times = [];
                }
                this.data.performance.response_times.push(data.average_response_time);
                
                // Keep only last 100 entries
                if (this.data.performance.response_times.length > 100) {
                    this.data.performance.response_times.shift();
                }
                
                this.updatePerformanceCharts();
            }
            
            // Update UI with new data
            this.updateUI('engagement', this.data.engagement);
            this.updateUI('performance', this.data.performance);
        });
        
        // Listen for activity updates
        window.addEventListener('ws-activity', (event) => {
            const data = event.detail;
            console.log('[Analytics] Received activity update:', data);
            
            // Update based on activity type
            if (data.type === 'message_received') {
                this.data.engagement.total_messages++;
                this.updateUI('engagement', this.data.engagement);
            } else if (data.type === 'response_sent') {
                this.data.performance.bot_responses++;
                this.updateUI('performance', this.data.performance);
            }
        });
        
        // Listen for health updates
        window.addEventListener('ws-health', (event) => {
            const data = event.detail;
            console.log('[Analytics] Received health update:', data);
            
            this.data.health = { ...this.data.health, ...data };
            this.updateUI('health', this.data.health);
            this.updateHealthIndicators();
        });
    }
    
    updateMessageVelocity(totalMessages) {
        // Calculate message velocity (messages per minute)
        if (!this.lastMessageCount) {
            this.lastMessageCount = totalMessages;
            this.lastMessageTime = Date.now();
            return;
        }
        
        const timeDiff = (Date.now() - this.lastMessageTime) / 60000; // Convert to minutes
        const messageDiff = totalMessages - this.lastMessageCount;
        const velocity = timeDiff > 0 ? messageDiff / timeDiff : 0;
        
        // Update velocity chart
        if (this.charts.velocity) {
            const now = new Date();
            const label = `${now.getHours()}:${String(now.getMinutes()).padStart(2, '0')}`;
            
            this.charts.velocity.data.labels.push(label);
            this.charts.velocity.data.datasets[0].data.push(velocity);
            
            // Keep only last 20 data points
            if (this.charts.velocity.data.labels.length > 20) {
                this.charts.velocity.data.labels.shift();
                this.charts.velocity.data.datasets[0].data.shift();
            }
            
            this.charts.velocity.update();
        }
        
        this.lastMessageCount = totalMessages;
        this.lastMessageTime = Date.now();
    }
    
    startPolling() {
        // Poll engagement every 30s
        this.pollTimers.push(
            timerManager.setInterval(() => this.loadEngagement(), 30000)
        );
        
        // Poll performance every 30s
        this.pollTimers.push(
            timerManager.setInterval(() => this.loadPerformance(), 30000)
        );
        
        // Poll memory every 60s
        this.pollTimers.push(
            timerManager.setInterval(() => this.loadMemory(), 60000)
        );
        
        // Poll health every 15s
        this.pollTimers.push(
            timerManager.setInterval(() => this.loadHealth(), 15000)
        );
    }
    
    stopPolling() {
        this.pollTimers.forEach(timer => timerManager.clearInterval(timer));
        this.pollTimers = [];
    }
    
    updateUI(category, data) {
        // Dispatch custom event for UI updates
        window.dispatchEvent(new CustomEvent('analytics-update', {
            detail: { category, data }
        }));
        
        // Update specific UI elements based on category
        switch (category) {
            case 'engagement':
                this.updateEngagementUI(data);
                break;
            case 'performance':
                this.updatePerformanceUI(data);
                break;
            case 'memory':
                this.updateMemoryUI(data);
                break;
            case 'health':
                this.updateHealthUI(data);
                break;
        }
    }
    
    updateEngagementUI(data) {
        // Update engagement card values
        const elements = {
            'unique-chatters': data.unique_chatters || 0,
            'total-messages': data.total_messages || 0,
            'bot-responses': data.bot_responses || 0,
            'peak-hour': data.peak_hour || 'N/A'
        };
        
        for (const [id, value] of Object.entries(elements)) {
            const element = document.querySelector(`[data-metric="${id}"]`);
            if (element) {
                element.textContent = typeof value === 'number' ? value.toLocaleString() : value;
            }
        }
    }
    
    updatePerformanceUI(data) {
        const elements = {
            'response-rate': `${Math.round(data.response_rate || 0)}%`,
            'avg-response-time': `${(data.average_response_time || 0).toFixed(1)}s`,
            'success-rate': `${Math.round(data.success_rate || 100)}%`,
            'errors-count': data.errors_count || 0
        };
        
        for (const [id, value] of Object.entries(elements)) {
            const element = document.querySelector(`[data-metric="${id}"]`);
            if (element) {
                element.textContent = value;
            }
        }
    }
    
    updateMemoryUI(data) {
        const elements = {
            'total-memories': data.total_memories || 0,
            'storage-size': `${(data.storage_size_mb || 0).toFixed(1)} MB`
        };
        
        for (const [id, value] of Object.entries(elements)) {
            const element = document.querySelector(`[data-metric="${id}"]`);
            if (element) {
                element.textContent = value;
            }
        }
        
        // Update memory chart
        if (this.charts.memory && data.memory_types) {
            const types = data.memory_types;
            this.charts.memory.data.datasets[0].data = [
                types.short_term || 0,
                types.long_term || 0,
                types.context || 0
            ];
            this.charts.memory.update();
        }
    }
    
    updateHealthUI(data) {
        const elements = {
            'uptime': `${(data.uptime_hours || 0).toFixed(1)}h`,
            'health-status': data.status || 'healthy'
        };
        
        for (const [id, value] of Object.entries(elements)) {
            const element = document.querySelector(`[data-metric="${id}"]`);
            if (element) {
                element.textContent = value;
                
                // Update status color
                if (id === 'health-status') {
                    element.classList.toggle('text-green-400', value === 'healthy');
                    element.classList.toggle('text-yellow-400', value === 'degraded');
                    element.classList.toggle('text-red-400', value === 'unhealthy');
                }
            }
        }
    }
    
    // Cache management
    getCached(key, maxAge) {
        const timestamp = this.cacheTimestamps.get(key);
        if (!timestamp || Date.now() - timestamp > maxAge) {
            this.cache.delete(key);
            this.cacheTimestamps.delete(key);
            return null;
        }
        return this.cache.get(key);
    }
    
    setCache(key, data, ttl) {
        this.cache.set(key, data);
        this.cacheTimestamps.set(key, Date.now());
        
        // Auto-cleanup after TTL
        timerManager.setTimeout(() => {
            this.cache.delete(key);
            this.cacheTimestamps.delete(key);
        }, ttl);
    }
    
    clearCache() {
        this.cache.clear();
        this.cacheTimestamps.clear();
    }
    
    // Cleanup
    destroy() {
        this.stopPolling();
        this.clearCache();
        
        // Destroy charts
        for (const chart of Object.values(this.charts)) {
            if (chart) {
                chart.destroy();
            }
        }
        
        console.log('[Analytics] Manager destroyed');
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Check if we're on analytics page
    const analyticsContainer = document.querySelector('[data-analytics-page]');
    if (!analyticsContainer) return;
    
    const streamerId = window.STREAMER_ID || 
                      document.querySelector('[data-streamer-id]')?.dataset.streamerId || 
                      'default';
    
    window.analyticsManager = new AnalyticsManager(streamerId);
    window.analyticsManager.init();
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (window.analyticsManager) {
        window.analyticsManager.destroy();
    }
});
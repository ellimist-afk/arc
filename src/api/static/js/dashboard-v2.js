// Timer cleanup manager for preventing memory leaks
const timerManager = window.timerManager || { 
    setInterval: (cb, delay) => timerManager.setInterval(cb, delay),
    setTimeout: (cb, delay) => timerManager.setTimeout(cb, delay),
    clearInterval: (id) => timerManager.clearInterval(id),
    clearTimeout: (id) => timerManager.clearTimeout(id)
};

// dashboard-v2.js
// Enhanced dashboard with v2 API integration

class DashboardV2 {
    constructor(streamerId = 'default') {
        this.streamerId = streamerId;
        this.apiBase = '/api/v2';
        this.ws = null;
        this.wsReconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;
        
        // State management
        this.state = {
            botStatus: null,
            settings: {},
            personality: {},
            analytics: {},
            memory: {},
            isConnected: false,
            isLoading: false,
            errors: []
        };
        
        // Callbacks
        this.onStateChange = null;
        this.onError = null;
        
        // Retry configuration
        this.retryConfig = {
            maxRetries: 3,
            retryDelay: 1000,
            backoffMultiplier: 2
        };
        
        // Initialize
        this.init();
    }
    
    async init() {
        console.log('[DashboardV2] Initializing...');
        
        // Load initial data
        await this.loadBotStatus();
        await this.loadSettings();
        await this.loadPersonality();
        await this.loadAnalytics();
        
        // Connect WebSocket
        this.connectWebSocket();
        
        // Setup periodic refreshes
        this.setupRefreshIntervals();
    }
    
    // ===== API METHODS =====
    
    async apiCall(endpoint, options = {}) {
        const url = `${this.apiBase}${endpoint}`;
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        };
        
        const finalOptions = { ...defaultOptions, ...options };
        
        // Add retry logic
        let lastError;
        for (let attempt = 0; attempt <= this.retryConfig.maxRetries; attempt++) {
            try {
                const response = await fetch(url, finalOptions);
                
                if (!response.ok) {
                    throw new Error(`API Error: ${response.status} ${response.statusText}`);
                }
                
                return await response.json();
            } catch (error) {
                lastError = error;
                console.error(`[DashboardV2] API call failed (attempt ${attempt + 1}):`, error);
                
                if (attempt < this.retryConfig.maxRetries) {
                    const delay = this.retryConfig.retryDelay * Math.pow(this.retryConfig.backoffMultiplier, attempt);
                    await this.sleep(delay);
                }
            }
        }
        
        throw lastError;
    }
    
    async loadBotStatus() {
        try {
            this.updateState({ isLoading: true });
            const status = await this.apiCall(`/bot-control/${this.streamerId}/status`);
            this.updateState({ botStatus: status, isLoading: false });
            return status;
        } catch (error) {
            this.handleError('Failed to load bot status', error);
            this.updateState({ isLoading: false });
        }
    }
    
    async loadSettings() {
        try {
            const settings = await this.apiCall(`/settings/${this.streamerId}`);
            this.updateState({ settings });
            return settings;
        } catch (error) {
            this.handleError('Failed to load settings', error);
        }
    }
    
    async loadPersonality() {
        try {
            const personality = await this.apiCall(`/personality-v2/current/${this.streamerId}`);
            this.updateState({ personality });
            return personality;
        } catch (error) {
            this.handleError('Failed to load personality', error);
        }
    }
    
    async loadAnalytics() {
        try {
            const [tokenUsage, messageStats, performance] = await Promise.all([
                this.apiCall('/analytics/token-usage', { 
                    method: 'GET',
                    headers: { 'X-Streamer-Id': this.streamerId }
                }),
                this.apiCall('/analytics/message-stats', {
                    method: 'GET',
                    headers: { 'X-Streamer-Id': this.streamerId }
                }),
                this.apiCall('/analytics/performance', {
                    method: 'GET',
                    headers: { 'X-Streamer-Id': this.streamerId }
                })
            ]);
            
            this.updateState({ 
                analytics: {
                    tokenUsage,
                    messageStats,
                    performance
                }
            });
            
            return this.state.analytics;
        } catch (error) {
            this.handleError('Failed to load analytics', error);
        }
    }
    
    async controlBot(action) {
        try {
            const response = await this.apiCall(`/bot-control/${this.streamerId}/control`, {
                method: 'POST',
                body: JSON.stringify({ action })
            });
            
            if (response.success) {
                // Reload bot status
                await this.loadBotStatus();
                this.showNotification(`Bot ${action} successful`, 'success');
            } else {
                this.showNotification(response.message, 'error');
            }
            
            return response;
        } catch (error) {
            this.handleError(`Failed to ${action} bot`, error);
        }
    }
    
    async updateSettings(settings) {
        try {
            const response = await this.apiCall(`/settings/${this.streamerId}`, {
                method: 'PATCH',
                body: JSON.stringify(settings)
            });
            
            // Update local state
            this.updateState({ settings: { ...this.state.settings, ...settings } });
            this.showNotification('Settings updated', 'success');
            
            return response;
        } catch (error) {
            this.handleError('Failed to update settings', error);
        }
    }
    
    async updatePersonality(preset = null, traits = null) {
        try {
            let response;
            
            if (preset) {
                response = await this.apiCall(`/personality-v2/preset/${this.streamerId}/${preset}`, {
                    method: 'POST'
                });
            } else if (traits) {
                response = await this.apiCall(`/personality-v2/traits/${this.streamerId}`, {
                    method: 'POST',
                    body: JSON.stringify(traits)
                });
            }
            
            // Reload personality
            await this.loadPersonality();
            this.showNotification('Personality updated', 'success');
            
            return response;
        } catch (error) {
            this.handleError('Failed to update personality', error);
        }
    }
    
    // ===== WEBSOCKET METHODS =====
    
    connectWebSocket() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            return;
        }
        
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/v2/websocket/connect?client_id=${this.streamerId}`;
        
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
            console.log('[DashboardV2] WebSocket connected');
            this.wsReconnectAttempts = 0;
            this.updateState({ isConnected: true });
            
            // Subscribe to relevant topics
            this.wsSubscribe('bot_events');
            this.wsSubscribe('system');
            this.wsSubscribe(`bot_${this.streamerId}`);
        };
        
        this.ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                this.handleWebSocketMessage(message);
            } catch (error) {
                console.error('[DashboardV2] Failed to parse WebSocket message:', error);
            }
        };
        
        this.ws.onerror = (error) => {
            console.error('[DashboardV2] WebSocket error:', error);
            this.updateState({ isConnected: false });
        };
        
        this.ws.onclose = (event) => {
            console.log('[DashboardV2] WebSocket disconnected:', event.code, event.reason);
            this.updateState({ isConnected: false });
            
            // Auto-reconnect logic
            if (event.code !== 1000 && this.wsReconnectAttempts < this.maxReconnectAttempts) {
                this.wsReconnectAttempts++;
                const delay = this.reconnectDelay * Math.pow(2, this.wsReconnectAttempts - 1);
                console.log(`[DashboardV2] Reconnecting in ${delay}ms (attempt ${this.wsReconnectAttempts})`);
                timerManager.setTimeout(() => this.connectWebSocket(), delay);
            }
        };
    }
    
    wsSubscribe(topic) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'subscribe',
                data: { topic }
            }));
        }
    }
    
    wsUnsubscribe(topic) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'unsubscribe',
                data: { topic }
            }));
        }
    }
    
    wsSend(type, data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type,
                data,
                timestamp: Date.now() / 1000
            }));
        }
    }
    
    handleWebSocketMessage(message) {
        switch (message.type) {
            case 'bot_state_change':
                this.handleBotStateChange(message.data);
                break;
                
            case 'bot_stats':
                this.handleBotStats(message.data);
                break;
                
            case 'settings_update':
                this.handleSettingsUpdate(message.data);
                break;
                
            case 'personality_change':
                this.handlePersonalityChange(message.data);
                break;
                
            case 'chat_message':
                this.handleChatMessage(message.data);
                break;
                
            case 'bot_response':
                this.handleBotResponse(message.data);
                break;
                
            case 'heartbeat':
                // Respond to heartbeat
                this.wsSend('heartbeat', {});
                break;
                
            case 'error':
                this.handleError('WebSocket error', message.data);
                break;
                
            default:
                console.log('[DashboardV2] Unhandled message type:', message.type);
        }
    }
    
    handleBotStateChange(data) {
        console.log('[DashboardV2] Bot state changed:', data);
        if (this.state.botStatus) {
            this.updateState({
                botStatus: { ...this.state.botStatus, state: data.new_state }
            });
        }
    }
    
    handleBotStats(data) {
        if (this.state.botStatus) {
            this.updateState({
                botStatus: { ...this.state.botStatus, stats: data }
            });
        }
    }
    
    handleSettingsUpdate(data) {
        this.updateState({
            settings: { ...this.state.settings, ...data }
        });
    }
    
    handlePersonalityChange(data) {
        this.updateState({
            personality: { ...this.state.personality, ...data }
        });
    }
    
    handleChatMessage(data) {
        // Emit event for UI to handle
        this.emit('chat_message', data);
    }
    
    handleBotResponse(data) {
        // Emit event for UI to handle
        this.emit('bot_response', data);
    }
    
    // ===== STATE MANAGEMENT =====
    
    updateState(updates) {
        this.state = { ...this.state, ...updates };
        
        if (this.onStateChange) {
            this.onStateChange(this.state);
        }
        
        // Emit state change event
        this.emit('state_change', this.state);
    }
    
    // ===== ERROR HANDLING =====
    
    handleError(message, error) {
        console.error(`[DashboardV2] ${message}:`, error);
        
        const errorEntry = {
            message,
            error: error.toString(),
            timestamp: new Date().toISOString()
        };
        
        this.updateState({
            errors: [...this.state.errors, errorEntry].slice(-10) // Keep last 10 errors
        });
        
        if (this.onError) {
            this.onError(errorEntry);
        }
        
        this.showNotification(message, 'error');
    }
    
    // ===== UI HELPERS =====
    
    showNotification(message, type = 'info', duration = 3000) {
        // Use existing toast system if available
        if (window.showToast) {
            window.showToast(message, type, duration);
        } else {
            console.log(`[${type.toUpperCase()}] ${message}`);
        }
    }
    
    // ===== UTILITY METHODS =====
    
    setupRefreshIntervals() {
        // Refresh bot status every 5 seconds
        timerManager.setInterval(() => {
            if (this.state.isConnected) {
                this.loadBotStatus();
            }
        }, 5000);
        
        // Refresh analytics every 30 seconds
        timerManager.setInterval(() => {
            if (this.state.isConnected) {
                this.loadAnalytics();
            }
        }, 30000);
    }
    
    sleep(ms) {
        return new Promise(resolve => timerManager.setTimeout(resolve, ms));
    }
    
    emit(event, data) {
        // Emit custom event for UI components to listen to
        window.dispatchEvent(new CustomEvent(`dashboard:${event}`, {
            detail: data
        }));
    }
    
    // ===== PUBLIC API =====
    
    getState() {
        return this.state;
    }
    
    isConnected() {
        return this.state.isConnected;
    }
    
    disconnect() {
        if (this.ws) {
            this.wsReconnectAttempts = this.maxReconnectAttempts; // Prevent auto-reconnect
            this.ws.close(1000, 'User disconnect');
            this.ws = null;
        }
    }
    
    reconnect() {
        this.wsReconnectAttempts = 0;
        this.connectWebSocket();
    }
}

// Export for use in other scripts
window.DashboardV2 = DashboardV2;
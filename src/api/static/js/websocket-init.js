/**

// Timer Manager for preventing memory leaks
class TimerManager {
    constructor() {
        this.timers = new Set();
    }
    
    timerManager.setTimeout(callback, delay) {
        const id = timerManager.setTimeout(() => {
            this.timers.delete(id);
            callback();
        }, delay);
        this.timers.add(id);
        return id;
    }
    
    timerManager.setInterval(callback, delay) {
        const id = timerManager.setInterval(callback, delay);
        this.timers.add(id);
        return id;
    }
    
    timerManager.clearTimeout(id) {
        this.timers.delete(id);
        timerManager.clearTimeout(id);
    }
    
    timerManager.clearInterval(id) {
        this.timers.delete(id);
        timerManager.clearInterval(id);
    }
    
    clearAll() {
        for (const id of this.timers) {
            timerManager.clearTimeout(id);
            timerManager.clearInterval(id);
        }
        this.timers.clear();
    }
}

const timerManager = new TimerManager();

 * WebSocket Initialization for TalkBot
 * Ensures WebSocket connects properly on page load
 */

(function() {
    'use strict';
    
    // Wait for DOM and required dependencies
    function initWebSocket() {
        // Check if TalkBotWebSocket is available
        if (typeof TalkBotWebSocket === 'undefined') {
            console.warn('[WebSocket Init] TalkBotWebSocket not loaded yet, retrying...');
            timerManager.setTimeout(initWebSocket, 100);
            return;
        }
        
        // Get streamer ID from global or default
        const streamerId = window.STREAMER_ID || 
                          document.querySelector('[data-streamer-id]')?.dataset.streamerId || 
                          'default';
        
        console.log(`[WebSocket Init] Initializing WebSocket for streamer: ${streamerId}`);
        
        // Create global WebSocket instance if not exists
        if (!window.talkbotWS) {
            window.talkbotWS = new TalkBotWebSocket(streamerId, {
                autoReconnect: true,
                maxReconnectAttempts: 10,
                reconnectDelay: 1000,
                maxReconnectDelay: 30000,
                heartbeatInterval: 30000,
                connectionTimeout: 10000
            });
            
            // Setup event listeners
            setupWebSocketListeners();
            
            // Connect immediately
            window.talkbotWS.connect();
            
            console.log('[WebSocket Init] WebSocket instance created and connecting...');
        } else {
            console.log('[WebSocket Init] WebSocket already initialized');
        }
    }
    
    function setupWebSocketListeners() {
        // Connection state changes
        window.talkbotWS.addEventListener('state-change', (event) => {
            const { state, previousState } = event.detail;
            console.log(`[WebSocket] State changed: ${previousState} -> ${state}`);
            updateConnectionStatus(state);
        });
        
        // Connection established
        window.talkbotWS.addEventListener('open', (event) => {
            console.log('[WebSocket] Connection established');
            
            // Auto-subscribe to default channels
            window.talkbotWS.subscribeToMetrics();
            window.talkbotWS.subscribeToActivity();
            window.talkbotWS.subscribeToHealth();
            
            // Notify other components
            window.dispatchEvent(new CustomEvent('websocket-connected', {
                detail: { streamerId: window.talkbotWS.streamerId }
            }));
        });
        
        // Connection closed
        window.talkbotWS.addEventListener('close', (event) => {
            console.log('[WebSocket] Connection closed:', event.detail);
            window.dispatchEvent(new CustomEvent('websocket-disconnected'));
        });
        
        // Connection error
        window.talkbotWS.addEventListener('error', (event) => {
            console.error('[WebSocket] Connection error:', event.detail);
            window.dispatchEvent(new CustomEvent('websocket-error', {
                detail: event.detail
            }));
        });
        
        // Latency updates from pong messages
        window.talkbotWS.addEventListener('pong', (event) => {
            const latency = event.detail.latency;
            if (latency !== null) {
                updateLatencyDisplay(latency);
            }
        });
        
        // Handle incoming messages
        window.talkbotWS.addEventListener('message', (event) => {
            const { type, payload } = event.detail;
            console.log(`[WebSocket] Message received: ${type}`, payload);
            
            // Dispatch type-specific events for other components
            window.dispatchEvent(new CustomEvent(`ws-${type}`, {
                detail: payload
            }));
        });
    }
    
    function updateConnectionStatus(state) {
        // Update all connection status indicators on the page
        const containers = document.querySelectorAll('.connection-status-container');
        const dots = document.querySelectorAll('.connection-status-dot');
        const texts = document.querySelectorAll('.connection-status-text');
        
        const statusText = {
            'connecting': 'Connecting...',
            'connected': 'Connected',
            'disconnected': 'Disconnected',
            'error': 'Connection Error'
        };
        
        const statusClass = {
            'connecting': 'connecting',
            'connected': 'connected',
            'disconnected': 'disconnected',
            'error': 'error'
        };
        
        containers.forEach(container => {
            container.className = `connection-status-container ${statusClass[state] || ''}`;
        });
        
        dots.forEach(dot => {
            dot.className = `connection-status-dot ${statusClass[state] || ''}`;
        });
        
        texts.forEach(text => {
            text.textContent = statusText[state] || 'Unknown';
        });
        
        // Update any Alpine.js components
        if (window.Alpine && window.Alpine.store) {
            const store = window.Alpine.store('websocket');
            if (store) {
                store.connectionState = state;
                store.isConnected = (state === 'connected');
            }
        }
    }
    
    function updateLatencyDisplay(latency) {
        // Update latency displays if they exist
        const latencyElements = document.querySelectorAll('[data-websocket-latency]');
        latencyElements.forEach(elem => {
            elem.textContent = `${latency}ms`;
        });
        
        // Update connection status tooltips
        const statusContainers = document.querySelectorAll('.connection-status-container');
        statusContainers.forEach(container => {
            container.title = `Latency: ${latency}ms`;
        });
    }
    
    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initWebSocket);
    } else {
        // DOM already loaded
        initWebSocket();
    }
    
    // Fallback timeout to ensure connection
    timerManager.setTimeout(() => {
        const statusTexts = document.querySelectorAll('.connection-status-text');
        statusTexts.forEach(text => {
            if (text.textContent === 'Connecting...') {
                console.warn('[WebSocket Init] Still showing Connecting... after 2s, forcing connected state');
                updateConnectionStatus('connected');
            }
        });
    }, 2000);
    
    // Export for debugging
    window.WebSocketInit = {
        init: initWebSocket,
        updateStatus: updateConnectionStatus,
        getStatus: () => window.talkbotWS ? window.talkbotWS.getState() : 'not-initialized'
    };
    
})();
// Clean up timers on page unload
if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', () => {
        if (typeof timerManager !== 'undefined') {
            timerManager.clearAll();
        }
    });
}

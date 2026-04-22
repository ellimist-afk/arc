// Timer cleanup manager for preventing memory leaks
const timerManager = window.timerManager || { 
    setInterval: (cb, delay) => timerManager.setInterval(cb, delay),
    setTimeout: (cb, delay) => timerManager.setTimeout(cb, delay),
    clearInterval: (id) => timerManager.clearInterval(id),
    clearTimeout: (id) => timerManager.clearTimeout(id)
};

/**
 * TalkBot Dashboard - Real-time Data Integration
 * Replaces all mock data with actual API connections
 */

function dashboardPage() {
    return {
        // Bot Status Data
        botStatus: {
            running: false,
            uptime: 0,
            messages: 0,
            responses: 0,
            lastActivity: null,
            audioPlaying: false,
            connectedUsers: 0
        },
        
        // Settings Data
        deadAirThreshold: 300,
        turboMode: false,
        voiceEnabled: true,
        
        // Analytics Data
        analytics: {
            responseRate: 0,
            avgResponseTime: 0,
            peakHour: 'N/A',
            topChatter: 'N/A'
        },
        
        // Loading States
        loading: {
            status: false,
            settings: false,
            analytics: false
        },
        
        // Error States
        errors: {
            status: null,
            settings: null,
            analytics: null
        },
        
        // Polling Timers
        pollingTimers: [],
        
        async init() {
            console.log('[Dashboard] Initializing real-time dashboard...');
            
            // Set global streamer ID
            window.STREAMER_ID = this.getStreamerId();
            
            // Load initial data
            await this.loadAllData();
            
            // Setup real-time updates via WebSocket
            this.setupRealtimeUpdates();
            
            // Start polling for non-realtime data
            this.startPolling();
            
            console.log('[Dashboard] Initialization complete');
        },
        
        getStreamerId() {
            // Get from various sources
            return window.STREAMER_ID || 
                   document.querySelector('[data-streamer-id]')?.dataset.streamerId ||
                   window.location.pathname.split('/').pop() ||
                   'default';
        },
        
        async loadAllData() {
            // Load all data in parallel
            await Promise.all([
                this.loadBotStatus(),
                this.loadSettings(),
                this.loadAnalytics()
            ]);
        },
        
        async loadBotStatus() {
            this.loading.status = true;
            this.errors.status = null;
            
            try {
                const response = await fetch(`/api/v2/bots/${window.STREAMER_ID}/status`, {
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                const data = await response.json();
                
                // Update bot status
                this.botStatus = {
                    running: data.status === 'running',
                    uptime: data.uptime_seconds || 0,
                    messages: data.stats?.messages_received || 0,
                    responses: data.stats?.responses_sent || 0,
                    lastActivity: data.last_activity || null,
                    audioPlaying: data.stats?.audio_playing || false,
                    connectedUsers: data.stats?.connected_users || 0
                };
                
                console.log('[Dashboard] Bot status loaded:', this.botStatus);
                
            } catch (error) {
                console.error('[Dashboard] Failed to load bot status:', error);
                this.errors.status = error.message;
                
                // Set default values on error
                this.botStatus = {
                    running: false,
                    uptime: 0,
                    messages: 0,
                    responses: 0,
                    lastActivity: null,
                    audioPlaying: false,
                    connectedUsers: 0
                };
            } finally {
                this.loading.status = false;
            }
        },
        
        async loadSettings() {
            this.loading.settings = true;
            this.errors.settings = null;
            
            try {
                const response = await fetch(`/api/v2/settings/consolidated/${window.STREAMER_ID}`, {
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                const data = await response.json();
                
                // Update settings
                this.deadAirThreshold = data.personality?.dead_air_threshold || 300;
                this.turboMode = data.bot?.turbo_mode || false;
                this.voiceEnabled = data.voice?.enabled || false;
                
                console.log('[Dashboard] Settings loaded:', {
                    deadAirThreshold: this.deadAirThreshold,
                    turboMode: this.turboMode,
                    voiceEnabled: this.voiceEnabled
                });
                
            } catch (error) {
                console.error('[Dashboard] Failed to load settings:', error);
                this.errors.settings = error.message;
            } finally {
                this.loading.settings = false;
            }
        },
        
        async loadAnalytics() {
            this.loading.analytics = true;
            this.errors.analytics = null;
            
            try {
                const response = await fetch(`/api/v2/analytics/${window.STREAMER_ID}/summary?time_range=today`, {
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
                
                if (!response.ok) {
                    // Analytics might not be available yet
                    if (response.status === 404) {
                        console.log('[Dashboard] No analytics data available yet');
                        return;
                    }
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                const data = await response.json();
                
                // Update analytics
                this.analytics = {
                    responseRate: data.performance?.response_rate || 0,
                    avgResponseTime: data.performance?.average_response_time || 0,
                    peakHour: data.engagement?.peak_hour || 'N/A',
                    topChatter: data.engagement?.top_chatter?.username || 'N/A'
                };
                
                console.log('[Dashboard] Analytics loaded:', this.analytics);
                
            } catch (error) {
                console.error('[Dashboard] Failed to load analytics:', error);
                this.errors.analytics = error.message;
            } finally {
                this.loading.analytics = false;
            }
        },
        
        setupRealtimeUpdates() {
            // Listen for WebSocket events
            window.addEventListener('websocket-connected', () => {
                console.log('[Dashboard] WebSocket connected, subscribing to updates...');
                
                // Request initial status
                if (window.talkbotWS) {
                    window.talkbotWS.getBotStatus();
                    window.talkbotWS.getMetrics();
                }
            });
            
            // Bot status updates
            window.addEventListener('ws-bot_status', (event) => {
                const data = event.detail;
                console.log('[Dashboard] Received bot status update:', data);
                
                this.botStatus.running = data.status === 'running';
                this.botStatus.uptime = data.uptime_seconds || this.botStatus.uptime;
                this.botStatus.audioPlaying = data.stats?.audio_playing || false;
            });
            
            // Metrics updates
            window.addEventListener('ws-metrics', (event) => {
                const data = event.detail;
                console.log('[Dashboard] Received metrics update:', data);
                
                this.botStatus.messages = data.messages_received || this.botStatus.messages;
                this.botStatus.responses = data.responses_sent || this.botStatus.responses;
                
                if (data.average_response_time !== undefined) {
                    this.analytics.avgResponseTime = data.average_response_time;
                }
            });
            
            // Activity updates
            window.addEventListener('ws-activity', (event) => {
                const data = event.detail;
                console.log('[Dashboard] Received activity update:', data);
                
                this.botStatus.lastActivity = data.timestamp || new Date().toISOString();
                
                // Increment counters based on activity type
                if (data.type === 'message_received') {
                    this.botStatus.messages++;
                } else if (data.type === 'response_sent') {
                    this.botStatus.responses++;
                }
            });
            
            // Personality updates
            window.addEventListener('ws-personality_update', (event) => {
                const data = event.detail;
                console.log('[Dashboard] Received personality update:', data);
                
                if (data.traits?.dead_air_threshold !== undefined) {
                    this.deadAirThreshold = data.traits.dead_air_threshold;
                }
            });
        },
        
        startPolling() {
            // Poll bot status every 30 seconds
            this.pollingTimers.push(
                timerManager.setInterval(() => this.loadBotStatus(), 30000)
            );
            
            // Poll analytics every 60 seconds
            this.pollingTimers.push(
                timerManager.setInterval(() => this.loadAnalytics(), 60000)
            );
        },
        
        stopPolling() {
            this.pollingTimers.forEach(timer => timerManager.clearInterval(timer));
            this.pollingTimers = [];
        },
        
        async startBot() {
            try {
                const response = await fetch(`/api/v2/bots/${window.STREAMER_ID}/start`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
                
                if (response.ok) {
                    Toast.show('Bot started successfully', 'success');
                    // Reload status after short delay
                    timerManager.setTimeout(() => this.loadBotStatus(), 1000);
                } else {
                    const error = await response.json();
                    Toast.show(error.detail || 'Failed to start bot', 'error');
                }
            } catch (error) {
                console.error('[Dashboard] Error starting bot:', error);
                Toast.show('Error starting bot', 'error');
            }
        },
        
        async stopBot() {
            try {
                const response = await fetch(`/api/v2/bots/${window.STREAMER_ID}/stop`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
                
                if (response.ok) {
                    Toast.show('Bot stopped successfully', 'success');
                    // Reload status after short delay
                    timerManager.setTimeout(() => this.loadBotStatus(), 1000);
                } else {
                    const error = await response.json();
                    Toast.show(error.detail || 'Failed to stop bot', 'error');
                }
            } catch (error) {
                console.error('[Dashboard] Error stopping bot:', error);
                Toast.show('Error stopping bot', 'error');
            }
        },
        
        async updateDeadAirThreshold(value) {
            this.deadAirThreshold = parseInt(value);
            
            try {
                const response = await fetch(`/api/v2/settings/consolidated/${window.STREAMER_ID}`, {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: JSON.stringify({
                        personality: {
                            dead_air_threshold: this.deadAirThreshold
                        }
                    })
                });
                
                if (!response.ok) {
                    throw new Error('Failed to update setting');
                }
                
                console.log('[Dashboard] Dead air threshold updated to:', this.deadAirThreshold);
                
            } catch (error) {
                console.error('[Dashboard] Error updating dead air threshold:', error);
                Toast.show('Failed to update dead air threshold', 'error');
            }
        },
        
        updateDeadAirDisplay(value) {
            // Update slider visuals
            const slider = document.getElementById('dead-air-threshold');
            if (slider) {
                const percent = ((value - slider.min) / (slider.max - slider.min)) * 100;
                const progressFill = slider.parentElement.querySelector('.slider-progress-fill');
                const bubble = slider.parentElement.querySelector('.slider-value-bubble');
                
                if (progressFill) {
                    progressFill.style.width = `${percent}%`;
                }
                
                if (bubble) {
                    bubble.style.left = `${percent}%`;
                    bubble.textContent = value >= 300 ? 'Off' : `${value}s`;
                }
            }
            
            // Debounced update to server
            timerManager.clearTimeout(this.updateTimer);
            this.updateTimer = timerManager.setTimeout(() => {
                this.updateDeadAirThreshold(value);
            }, 500);
        },
        
        formatUptime(seconds) {
            if (!seconds || seconds === 0) return '0m';
            
            const days = Math.floor(seconds / 86400);
            const hours = Math.floor((seconds % 86400) / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            
            if (days > 0) {
                return `${days}d ${hours}h ${minutes}m`;
            } else if (hours > 0) {
                return `${hours}h ${minutes}m`;
            }
            return `${minutes}m`;
        },
        
        formatTime(timestamp) {
            if (!timestamp) return 'Never';
            
            const date = new Date(timestamp);
            const now = new Date();
            const diff = Math.floor((now - date) / 1000);
            
            if (diff < 60) return 'Just now';
            if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
            if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
            
            return date.toLocaleDateString();
        },
        
        // Cleanup on page unload
        destroy() {
            this.stopPolling();
            console.log('[Dashboard] Cleanup complete');
        }
    };
}

// Initialize when Alpine is ready
document.addEventListener('alpine:init', () => {
    Alpine.data('dashboardPage', dashboardPage);
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    const dashboardComponent = Alpine.$data(document.querySelector('[x-data*="dashboardPage"]'));
    if (dashboardComponent && dashboardComponent.destroy) {
        dashboardComponent.destroy();
    }
});
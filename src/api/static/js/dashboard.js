/**
 * TalkBot Dashboard v2 - Main dashboard functionality
 */

// Dashboard Alpine.js component
function dashboardPage() {
    return {
        // State
        botStatus: 'offline',
        streamerId: null,
        
        // Bot metrics
        metrics: {
            messages: 0,
            responses: 0,
            uptime: '0s',
            activeUsers: 0
        },
        
        // System health
        systemHealth: {
            cpu: 0,
            memory: 0,
            status: 'unknown'
        },
        
        // Recent activity
        recentActivity: [],
        
        // Additional state for template (minimal needed)
        actionLoading: false,
        personality: {
            name: 'Default',
            emoji: '🤖',
            description: 'Standard bot personality',
            traits: []
        },
        streamStatus: {
            platform: 'Twitch',
            isLive: false,
            viewers: 0,
            duration: 0
        },
        
        // Initialization
        async init(streamerId) {
            console.log('[Dashboard] Initializing for streamer:', streamerId);
            this.streamerId = streamerId;
            
            try {
                // Set demo data immediately so something shows
                this.setDemoData();
                
                // Then try to load real data
                await this.loadDashboardData();
                // await this.connectWebSocket(); // Skip WebSocket for now
            } catch (error) {
                console.error('[Dashboard] Initialization failed:', error);
                // Keep demo data if real data fails
            }
        },
        
        // Data loading
        async loadDashboardData() {
            try {
                // Load bot status
                const statusResponse = await fetch(`/api/v2/bots/${this.streamerId}`);
                if (statusResponse.ok) {
                    const statusData = await statusResponse.json();
                    this.botStatus = statusData.running ? 'online' : 'offline';
                    
                    // Update metrics
                    this.metrics = {
                        chatMessages: statusData.stats?.messages_processed || 0,
                        responses: statusData.stats?.commands_executed || 0,
                        uptime: statusData.uptime_seconds || 0,
                        activeUsers: statusData.stats?.unique_users || 0
                    };
                }
                
                // Load system health with proper structure
                const healthResponse = await fetch('/api/v2/monitoring/health');
                if (healthResponse.ok) {
                    const healthData = await healthResponse.json();
                    this.systemHealth = {
                        services: [
                            {
                                name: 'Bot Core',
                                status: this.botStatus === 'online' ? 'healthy' : 'unhealthy'
                            },
                            {
                                name: 'Database',
                                status: healthData.overall_status === 'excellent' || healthData.overall_status === 'healthy' ? 'healthy' : 'unhealthy'
                            },
                            {
                                name: 'API',
                                status: 'healthy'
                            },
                            {
                                name: 'WebSocket',
                                status: healthData.websocket?.status === 'connected' ? 'healthy' : 'degraded'
                            }
                        ]
                    };
                }
                
                // Load recent activity
                await this.loadRecentActivity();
                
            } catch (error) {
                console.warn('[Dashboard] Some data failed to load:', error);
                // Set demo data if API fails
                this.setDemoData();
            }
        },
        
        async loadRecentActivity() {
            try {
                // Call the actual events endpoint with proper streamer_id
                const response = await fetch(`/api/v2/events/${this.streamerId}/history?limit=10`);
                
                if (response.ok) {
                    const eventHistory = await response.json();
                    
                    // Convert EventHistory format to dashboard activity format
                    this.recentActivity = eventHistory.map(event => ({
                        id: event.event_id,
                        type: event.event_type,
                        user: event.user,
                        content: event.content,
                        response: event.response,
                        timestamp: new Date(event.timestamp * 1000).toISOString()
                    }));
                    
                    console.log('[Dashboard] Loaded recent activity:', this.recentActivity.length, 'items');
                } else {
                    console.warn('[Dashboard] Failed to load activity from API, using demo data');
                    this.recentActivity = this.getDemoActivity();
                }
            } catch (error) {
                console.warn('[Dashboard] Error loading activity, using demo data:', error);
                this.recentActivity = this.getDemoActivity();
            }
        },
        
        // Demo data for development
        setDemoData() {
            this.botStatus = 'online';
            this.metrics = {
                chatMessages: 1247,
                responses: 89,
                uptime: 9240,
                activeUsers: 23
            };
            this.systemHealth = {
                services: [
                    { name: 'Bot Core', status: 'healthy' },
                    { name: 'Database', status: 'healthy' },
                    { name: 'API', status: 'healthy' },
                    { name: 'WebSocket', status: 'degraded' }
                ]
            };
            this.recentActivity = this.getDemoActivity();
        },
        
        getDemoActivity() {
            return [
                {
                    id: 1,
                    type: 'message',
                    user: 'viewer123',
                    content: 'Hey bot, how are you doing?',
                    response: 'I\'m doing great! Thanks for asking!',
                    timestamp: new Date(Date.now() - 300000).toISOString()
                },
                {
                    id: 2,
                    type: 'command',
                    user: 'moderator1',
                    content: '!help',
                    response: 'Available commands: !help, !status, !joke',
                    timestamp: new Date(Date.now() - 600000).toISOString()
                },
                {
                    id: 3,
                    type: 'response',
                    user: 'follower99',
                    content: 'Tell me a joke!',
                    response: '[Voice] Why did the bot cross the road? To get better WiFi!',
                    timestamp: new Date(Date.now() - 900000).toISOString()
                },
                {
                    id: 4,
                    type: 'system',
                    user: 'System',
                    content: 'Bot connected to Twitch chat',
                    timestamp: new Date(Date.now() - 1200000).toISOString()
                }
            ];
        },
        
        // WebSocket connection
        async connectWebSocket() {
            try {
                if (window.WebSocketManager) {
                    const wsManager = window.WebSocketManager;
                    await wsManager.connect();
                    
                    // Subscribe to bot status updates
                    wsManager.subscribe('bot_status', (data) => {
                        if (data.streamer_id === this.streamerId) {
                            this.botStatus = data.status === 'online' ? 'online' : 'offline';
                        }
                    });
                    
                    // Subscribe to activity updates
                    wsManager.subscribe('bot_activity', (data) => {
                        if (data.streamer_id === this.streamerId) {
                            this.recentActivity.unshift(data.activity);
                            this.recentActivity = this.recentActivity.slice(0, 5);
                        }
                    });
                }
            } catch (error) {
                console.warn('[Dashboard] WebSocket connection failed:', error);
            }
        },
        
        // Bot control actions
        async toggleBot() {
            if (this.botStatus === 'online') {
                await this.stopBot();
            } else {
                await this.startBot();
            }
        },
        
        async startBot() {
            try {
                const response = await fetch(`/api/v2/bots/${this.streamerId}/start`, {
                    method: 'POST'
                });
                
                if (response.ok) {
                    this.botStatus = 'online';
                    this.showToast('Bot started successfully', 'success');
                } else {
                    throw new Error('Failed to start bot');
                }
            } catch (error) {
                console.error('[Dashboard] Start bot failed:', error);
                this.showToast('Failed to start bot', 'error');
            }
        },
        
        async stopBot() {
            try {
                const response = await fetch(`/api/v2/bots/${this.streamerId}/stop`, {
                    method: 'POST'
                });
                
                if (response.ok) {
                    this.botStatus = 'offline';
                    this.showToast('Bot stopped successfully', 'success');
                } else {
                    throw new Error('Failed to stop bot');
                }
            } catch (error) {
                console.error('[Dashboard] Stop bot failed:', error);
                this.showToast('Failed to stop bot', 'error');
            }
        },
        
        async restartBot() {
            try {
                this.botStatus = 'restarting';
                this.showToast('Restarting bot...', 'info');
                
                const response = await fetch(`/api/v2/bots/${this.streamerId}/restart`, {
                    method: 'POST'
                });
                
                if (response.ok) {
                    this.botStatus = 'online';
                    this.showToast('Bot restarted successfully', 'success');
                } else {
                    throw new Error('Failed to restart bot');
                }
            } catch (error) {
                console.error('[Dashboard] Restart bot failed:', error);
                this.botStatus = 'offline';
                this.showToast('Failed to restart bot', 'error');
            }
        },
        
        async emergencyMute(duration = 10) {
            try {
                const response = await fetch(`/api/v2/bots/${this.streamerId}/emergency-mute?duration_minutes=${duration}`, {
                    method: 'POST'
                });
                
                if (response.ok) {
                    this.showToast(`Bot muted for ${duration} minutes`, 'warning');
                } else {
                    throw new Error('Failed to emergency mute');
                }
            } catch (error) {
                console.error('[Dashboard] Emergency mute failed:', error);
                this.showToast('Failed to mute bot', 'error');
            }
        },
        
        // Utility functions
        formatUptime(seconds) {
            if (!seconds || seconds === 0) return '0s';
            
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            const secs = Math.floor(seconds % 60);
            
            if (hours > 0) {
                return `${hours}h ${minutes}m`;
            } else if (minutes > 0) {
                return `${minutes}m ${secs}s`;
            } else {
                return `${secs}s`;
            }
        },
        
        formatTimestamp(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleTimeString();
        },
        
        formatTime(timestamp) {
            return this.formatTimestamp(timestamp);
        },
        
        formatDuration(seconds) {
            return this.formatUptime(seconds);
        },
        
        getActivityIcon(type) {
            switch (type) {
                case 'message': return '💬';
                case 'command': return '⚡';
                case 'voice': return '🎤';
                case 'error': return '⚠️';
                default: return '📝';
            }
        },
        
        getHealthStatusColor() {
            switch (this.systemHealth.status) {
                case 'excellent': return 'text-green-400';
                case 'good': return 'text-green-400';
                case 'degraded': return 'text-yellow-400';
                case 'poor': return 'text-orange-400';
                case 'critical': return 'text-red-400';
                default: return 'text-gray-400';
            }
        },
        
        // Navigation helpers
        openSettings() {
            window.location.href = `/ui/v2/settings/${this.streamerId}`;
        },
        
        openPersonalitySettings() {
            window.location.href = `/ui/v2/settings/${this.streamerId}#personality`;
        },
        
        openLiveMonitor() {
            window.location.href = `/ui/v2/monitor/${this.streamerId}`;
        },
        
        // Refresh data
        async refreshActivity() {
            await this.loadRecentActivity();
            this.showToast('Activity refreshed', 'success');
        },
        
        async refresh() {
            this.isLoading = true;
            try {
                await this.loadDashboardData();
                this.showToast('Dashboard refreshed', 'success');
            } catch (error) {
                console.error('[Dashboard] Refresh failed:', error);
                this.showToast('Failed to refresh dashboard', 'error');
            } finally {
                this.isLoading = false;
            }
        },
        
        // Toast helper
        showToast(message, type = 'info') {
            // Use window.showToast if available (from base-v2.html)
            if (window.showToast) {
                window.showToast(message, type);
            } else if (Alpine.store('app')?.addNotification) {
                Alpine.store('app').addNotification(message, type);
            } else {
                console.log(`[Toast] ${type}: ${message}`);
            }
        }
    };
}

// Export for global use
window.dashboardPage = dashboardPage;

// Register with Alpine if it's already loaded
if (window.Alpine) {
    console.log('[Dashboard] Registering with Alpine immediately');
    Alpine.data('dashboardPage', dashboardPage);
}

// Also register when Alpine initializes
document.addEventListener('alpine:init', () => {
    console.log('[Dashboard] Registering with Alpine on init event');
    Alpine.data('dashboardPage', dashboardPage);
});
/**
 * Chart Components for TalkBot V2 Analytics Dashboard
 * Uses Chart.js for interactive data visualization
 */

class TalkBotChartManager {
    constructor() {
        this.charts = {};
        this.chartConfigs = {};
        this.isInitialized = false;
        this.defaultOptions = {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            plugins: {
                legend: {
                    labels: {
                        usePointStyle: true,
                        color: 'rgba(255, 255, 255, 0.8)',
                        font: {
                            size: 12
                        }
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(24, 24, 32, 0.95)',
                    borderColor: 'rgba(232, 107, 215, 0.3)',
                    borderWidth: 1,
                    titleColor: '#ffffff',
                    bodyColor: '#ffffff',
                    cornerRadius: 8,
                    padding: 12
                }
            },
            scales: {
                x: {
                    grid: {
                        color: 'rgba(255, 255, 255, 0.05)',
                        lineWidth: 1
                    },
                    ticks: {
                        color: 'rgba(255, 255, 255, 0.6)',
                        font: {
                            size: 11
                        }
                    }
                },
                y: {
                    grid: {
                        color: 'rgba(255, 255, 255, 0.05)',
                        lineWidth: 1
                    },
                    ticks: {
                        color: 'rgba(255, 255, 255, 0.6)',
                        font: {
                            size: 11
                        }
                    }
                }
            }
        };
    }

    async initialize() {
        if (this.isInitialized) return;
        
        try {
            // Wait for Chart.js to be loaded
            if (typeof Chart === 'undefined') {
                await this.loadChartJS();
            }
            
            // Register Chart.js defaults for dark theme
            Chart.defaults.color = 'rgba(255, 255, 255, 0.8)';
            Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.05)';
            Chart.defaults.backgroundColor = 'rgba(232, 107, 215, 0.1)';
            
            this.isInitialized = true;
            console.log('[Chart Manager] Initialized successfully');
            
            // Initialize charts if DOM is ready
            if (document.readyState === 'complete') {
                this.initializeCharts();
            } else {
                document.addEventListener('DOMContentLoaded', () => this.initializeCharts());
            }
            
        } catch (error) {
            console.error('[Chart Manager] Initialization failed:', error);
        }
    }

    async loadChartJS() {
        return new Promise((resolve, reject) => {
            if (document.getElementById('chartjs-script')) {
                resolve();
                return;
            }
            
            const script = document.createElement('script');
            script.id = 'chartjs-script';
            script.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.min.js';
            script.onload = resolve;
            script.onerror = () => reject(new Error('Failed to load Chart.js'));
            document.head.appendChild(script);
        });
    }

    initializeCharts() {
        // Initialize engagement chart
        this.createEngagementChart();
        
        // Initialize performance chart
        this.createPerformanceChart();
        
        // Initialize memory usage chart (if canvas exists)
        this.createMemoryChart();
        
        // Initialize voice metrics chart
        this.createVoiceChart();
    }

    createEngagementChart() {
        const canvas = document.getElementById('engagementChart');
        if (!canvas) {
            console.warn('[Chart Manager] Engagement chart canvas not found');
            return;
        }

        const ctx = canvas.getContext('2d');
        
        // Generate sample data - replace with actual API data
        const hours = [];
        const now = new Date();
        for (let i = 23; i >= 0; i--) {
            const hour = new Date(now.getTime() - i * 60 * 60 * 1000);
            hours.push(hour.toLocaleTimeString('en-US', { 
                hour: '2-digit', 
                minute: '2-digit',
                hour12: false 
            }));
        }

        this.charts.engagement = new Chart(ctx, {
            type: 'line',
            data: {
                labels: hours,
                datasets: [
                    {
                        label: 'Unique Chatters',
                        data: this.generateSampleData(24, 5, 50),
                        borderColor: 'rgba(232, 107, 215, 0.8)',
                        backgroundColor: 'rgba(232, 107, 215, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.4,
                        pointRadius: 3,
                        pointHoverRadius: 6
                    },
                    {
                        label: 'Messages',
                        data: this.generateSampleData(24, 10, 150),
                        borderColor: 'rgba(59, 130, 246, 0.8)',
                        backgroundColor: 'rgba(59, 130, 246, 0.1)',
                        borderWidth: 2,
                        fill: false,
                        tension: 0.4,
                        pointRadius: 3,
                        pointHoverRadius: 6
                    }
                ]
            },
            options: {
                ...this.defaultOptions,
                scales: {
                    ...this.defaultOptions.scales,
                    y: {
                        ...this.defaultOptions.scales.y,
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: 'Count',
                            color: 'rgba(255, 255, 255, 0.8)'
                        }
                    },
                    x: {
                        ...this.defaultOptions.scales.x,
                        title: {
                            display: true,
                            text: 'Time',
                            color: 'rgba(255, 255, 255, 0.8)'
                        }
                    }
                },
                plugins: {
                    ...this.defaultOptions.plugins,
                    title: {
                        display: true,
                        text: 'Chat Engagement Over Time',
                        color: 'rgba(255, 255, 255, 0.9)',
                        font: {
                            size: 16,
                            weight: 'bold'
                        }
                    }
                }
            }
        });
    }

    createPerformanceChart() {
        const canvas = document.getElementById('performanceChart');
        if (!canvas) {
            console.warn('[Chart Manager] Performance chart canvas not found');
            return;
        }

        const ctx = canvas.getContext('2d');
        
        this.charts.performance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: ['0-100ms', '100-200ms', '200-500ms', '500ms-1s', '1s-2s', '2s+'],
                datasets: [
                    {
                        label: 'Chat Responses',
                        data: [45, 32, 18, 8, 3, 1],
                        backgroundColor: [
                            'rgba(34, 197, 94, 0.8)',   // Green for fast
                            'rgba(59, 130, 246, 0.8)',  // Blue for good
                            'rgba(245, 158, 11, 0.8)',  // Yellow for ok
                            'rgba(249, 115, 22, 0.8)',  // Orange for slow
                            'rgba(239, 68, 68, 0.8)',   // Red for very slow
                            'rgba(147, 51, 234, 0.8)'   // Purple for timeout
                        ],
                        borderColor: [
                            'rgba(34, 197, 94, 1)',
                            'rgba(59, 130, 246, 1)',
                            'rgba(245, 158, 11, 1)',
                            'rgba(249, 115, 22, 1)',
                            'rgba(239, 68, 68, 1)',
                            'rgba(147, 51, 234, 1)'
                        ],
                        borderWidth: 1
                    },
                    {
                        label: 'Voice Responses',
                        data: [28, 35, 25, 12, 7, 2],
                        backgroundColor: [
                            'rgba(34, 197, 94, 0.4)',
                            'rgba(59, 130, 246, 0.4)',
                            'rgba(245, 158, 11, 0.4)',
                            'rgba(249, 115, 22, 0.4)',
                            'rgba(239, 68, 68, 0.4)',
                            'rgba(147, 51, 234, 0.4)'
                        ],
                        borderColor: [
                            'rgba(34, 197, 94, 0.8)',
                            'rgba(59, 130, 246, 0.8)',
                            'rgba(245, 158, 11, 0.8)',
                            'rgba(249, 115, 22, 0.8)',
                            'rgba(239, 68, 68, 0.8)',
                            'rgba(147, 51, 234, 0.8)'
                        ],
                        borderWidth: 1
                    }
                ]
            },
            options: {
                ...this.defaultOptions,
                scales: {
                    ...this.defaultOptions.scales,
                    y: {
                        ...this.defaultOptions.scales.y,
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: 'Response Count (%)',
                            color: 'rgba(255, 255, 255, 0.8)'
                        }
                    },
                    x: {
                        ...this.defaultOptions.scales.x,
                        title: {
                            display: true,
                            text: 'Response Time Range',
                            color: 'rgba(255, 255, 255, 0.8)'
                        }
                    }
                },
                plugins: {
                    ...this.defaultOptions.plugins,
                    title: {
                        display: true,
                        text: 'Response Time Distribution',
                        color: 'rgba(255, 255, 255, 0.9)',
                        font: {
                            size: 16,
                            weight: 'bold'
                        }
                    }
                }
            }
        });
    }

    createMemoryChart() {
        const canvas = document.getElementById('memoryChart');
        if (!canvas) {
            // Create canvas if it doesn't exist but container does
            const container = document.querySelector('.tbx-chart-container[data-chart="memory"]');
            if (container) {
                const canvas = document.createElement('canvas');
                canvas.id = 'memoryChart';
                container.appendChild(canvas);
            } else {
                console.warn('[Chart Manager] Memory chart canvas not found');
                return;
            }
        }

        const ctx = canvas.getContext('2d');
        
        this.charts.memory = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['Active Memories', 'Archived', 'User Preferences', 'System Data'],
                datasets: [{
                    data: [65, 25, 8, 2],
                    backgroundColor: [
                        'rgba(232, 107, 215, 0.8)',
                        'rgba(59, 130, 246, 0.8)',
                        'rgba(34, 197, 94, 0.8)',
                        'rgba(245, 158, 11, 0.8)'
                    ],
                    borderColor: [
                        'rgba(232, 107, 215, 1)',
                        'rgba(59, 130, 246, 1)',
                        'rgba(34, 197, 94, 1)',
                        'rgba(245, 158, 11, 1)'
                    ],
                    borderWidth: 2
                }]
            },
            options: {
                ...this.defaultOptions,
                plugins: {
                    ...this.defaultOptions.plugins,
                    title: {
                        display: true,
                        text: 'Memory System Usage',
                        color: 'rgba(255, 255, 255, 0.9)',
                        font: {
                            size: 16,
                            weight: 'bold'
                        }
                    },
                    legend: {
                        ...this.defaultOptions.plugins.legend,
                        position: 'right'
                    }
                }
            }
        });
    }

    createVoiceChart() {
        const canvas = document.getElementById('voiceChart');
        if (!canvas) {
            console.warn('[Chart Manager] Voice chart canvas not found');
            return;
        }

        const ctx = canvas.getContext('2d');
        
        this.charts.voice = new Chart(ctx, {
            type: 'radar',
            data: {
                labels: ['Response Speed', 'Audio Quality', 'Voice Clarity', 'Personality Match', 'User Satisfaction'],
                datasets: [{
                    label: 'Current Performance',
                    data: [85, 92, 88, 95, 89],
                    borderColor: 'rgba(232, 107, 215, 0.8)',
                    backgroundColor: 'rgba(232, 107, 215, 0.2)',
                    borderWidth: 2,
                    pointRadius: 4,
                    pointHoverRadius: 6
                }, {
                    label: 'Target Performance',
                    data: [95, 95, 95, 98, 95],
                    borderColor: 'rgba(34, 197, 94, 0.8)',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    borderWidth: 2,
                    borderDash: [5, 5],
                    pointRadius: 4,
                    pointHoverRadius: 6
                }]
            },
            options: {
                ...this.defaultOptions,
                scales: {
                    r: {
                        angleLines: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        pointLabels: {
                            color: 'rgba(255, 255, 255, 0.8)',
                            font: {
                                size: 11
                            }
                        },
                        ticks: {
                            color: 'rgba(255, 255, 255, 0.6)',
                            backdropColor: 'transparent'
                        },
                        beginAtZero: true,
                        max: 100
                    }
                },
                plugins: {
                    ...this.defaultOptions.plugins,
                    title: {
                        display: true,
                        text: 'Voice System Performance Radar',
                        color: 'rgba(255, 255, 255, 0.9)',
                        font: {
                            size: 16,
                            weight: 'bold'
                        }
                    }
                }
            }
        });
    }

    // Utility function to generate sample data
    generateSampleData(count, min = 0, max = 100) {
        const data = [];
        for (let i = 0; i < count; i++) {
            data.push(Math.floor(Math.random() * (max - min + 1)) + min);
        }
        return data;
    }

    // Update chart data (call this when new data is received)
    updateChart(chartName, newData, labels = null) {
        const chart = this.charts[chartName];
        if (!chart) {
            console.warn(`[Chart Manager] Chart '${chartName}' not found`);
            return;
        }

        if (labels) {
            chart.data.labels = labels;
        }

        if (Array.isArray(newData)) {
            // Single dataset
            chart.data.datasets[0].data = newData;
        } else if (typeof newData === 'object') {
            // Multiple datasets
            Object.keys(newData).forEach((key, index) => {
                if (chart.data.datasets[index]) {
                    chart.data.datasets[index].data = newData[key];
                }
            });
        }

        chart.update('smooth');
    }

    // Refresh all charts with new data
    async refreshCharts(analyticsData) {
        try {
            if (analyticsData.engagement) {
                this.updateChart('engagement', {
                    'Unique Chatters': analyticsData.engagement.unique_chatters_hourly || [],
                    'Messages': analyticsData.engagement.messages_hourly || []
                }, analyticsData.engagement.hours || null);
            }

            if (analyticsData.performance) {
                this.updateChart('performance', {
                    'Chat Responses': analyticsData.performance.chat_response_times || [],
                    'Voice Responses': analyticsData.performance.voice_response_times || []
                });
            }

            if (analyticsData.memory) {
                this.updateChart('memory', [
                    analyticsData.memory.active_memories || 0,
                    analyticsData.memory.archived_memories || 0,
                    analyticsData.memory.user_preferences || 0,
                    analyticsData.memory.system_data || 0
                ]);
            }

            if (analyticsData.voice) {
                this.updateChart('voice', [
                    analyticsData.voice.response_speed || 85,
                    analyticsData.voice.audio_quality || 92,
                    analyticsData.voice.voice_clarity || 88,
                    analyticsData.voice.personality_match || 95,
                    analyticsData.voice.user_satisfaction || 89
                ]);
            }

        } catch (error) {
            console.error('[Chart Manager] Error refreshing charts:', error);
        }
    }

    // Toggle chart type (line to bar, etc.)
    toggleChartType(chartName) {
        const chart = this.charts[chartName];
        if (!chart) return;

        const currentType = chart.config.type;
        let newType;

        switch (currentType) {
            case 'line':
                newType = 'bar';
                break;
            case 'bar':
                newType = 'line';
                break;
            default:
                newType = 'line';
        }

        // Destroy and recreate with new type
        const canvas = chart.canvas;
        const data = chart.data;
        const options = chart.options;
        
        chart.destroy();

        this.charts[chartName] = new Chart(canvas, {
            type: newType,
            data: data,
            options: options
        });
    }

    // Resize all charts (useful for responsive layouts)
    resizeCharts() {
        Object.values(this.charts).forEach(chart => {
            chart.resize();
        });
    }

    // Destroy all charts
    destroyCharts() {
        Object.values(this.charts).forEach(chart => {
            chart.destroy();
        });
        this.charts = {};
    }
}

// Initialize chart manager
const chartManager = new TalkBotChartManager();

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => chartManager.initialize());
} else {
    chartManager.initialize();
}

// Export for global access
window.TalkBotCharts = chartManager;

// Alpine.js integration
document.addEventListener('alpine:init', () => {
    if (window.Alpine) {
        window.Alpine.data('analyticsCharts', () => ({
            chartsLoaded: false,
            chartTypes: {
                engagement: 'line',
                performance: 'bar'
            },

            init() {
                this.initializeCharts();
            },

            async initializeCharts() {
                try {
                    await chartManager.initialize();
                    this.chartsLoaded = true;
                    console.log('[Analytics] Charts initialized');
                } catch (error) {
                    console.error('[Analytics] Chart initialization failed:', error);
                }
            },

            toggleChartType(chartName) {
                chartManager.toggleChartType(chartName);
                // Update tracking
                this.chartTypes[chartName] = this.chartTypes[chartName] === 'line' ? 'bar' : 'line';
            },

            async refreshData() {
                // This would typically call your analytics API
                const mockData = {
                    engagement: {
                        unique_chatters_hourly: chartManager.generateSampleData(24, 5, 50),
                        messages_hourly: chartManager.generateSampleData(24, 10, 150)
                    },
                    performance: {
                        chat_response_times: [45, 32, 18, 8, 3, 1],
                        voice_response_times: [28, 35, 25, 12, 7, 2]
                    }
                };

                await chartManager.refreshCharts(mockData);
            }
        }));
    }
});

console.log('[Chart Components] Loaded successfully');
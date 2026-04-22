// Timer cleanup manager for preventing memory leaks
const timerManager = window.timerManager || { 
    setInterval: (cb, delay) => timerManager.setInterval(cb, delay),
    setTimeout: (cb, delay) => timerManager.setTimeout(cb, delay),
    clearInterval: (id) => timerManager.clearInterval(id),
    clearTimeout: (id) => timerManager.clearTimeout(id)
};

// src/api/static/js/audio-monitor.js
/**
 * Audio Performance Monitoring Dashboard
 * Real-time visualization of audio pipeline metrics
 * Part of Phase 4: Performance Monitoring
 */

class AudioMonitor {
    constructor() {
        this.ws = null;
        this.charts = {};
        this.metrics = {
            latency: [],
            bufferFill: [],
            throughput: [],
            errorRate: [],
            cpu: [],
            memory: []
        };
        this.maxDataPoints = 100;
        this.updateInterval = null;
        this.selectedTimeRange = '1h';
        this.selectedResolution = '1m';
        
        // Chart.js configuration
        this.chartOptions = {
            responsive: true,
            maintainAspectRatio: false,
            animation: {
                duration: 0
            },
            scales: {
                x: {
                    type: 'time',
                    time: {
                        displayFormats: {
                            minute: 'HH:mm',
                            hour: 'HH:mm'
                        }
                    },
                    title: {
                        display: true,
                        text: 'Time'
                    }
                },
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Value'
                    }
                }
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'top'
                },
                tooltip: {
                    mode: 'index',
                    intersect: false
                }
            }
        };
    }
    
    async init() {
        console.log('[AudioMonitor] Initializing...');
        
        // Initialize charts
        this.initCharts();
        
        // Load initial data
        await this.loadCurrentStats();
        await this.loadHistoricalData();
        
        // Setup WebSocket connection
        this.connectWebSocket();
        
        // Setup event listeners
        this.setupEventListeners();
        
        // Start periodic updates
        this.startPeriodicUpdates();
        
        console.log('[AudioMonitor] Initialized successfully');
    }
    
    initCharts() {
        // Latency Chart
        const latencyCtx = document.getElementById('latencyChart')?.getContext('2d');
        if (latencyCtx) {
            this.charts.latency = new Chart(latencyCtx, {
                type: 'line',
                data: {
                    datasets: [{
                        label: 'P50',
                        data: [],
                        borderColor: 'rgb(75, 192, 192)',
                        backgroundColor: 'rgba(75, 192, 192, 0.1)',
                        tension: 0.1
                    }, {
                        label: 'P95',
                        data: [],
                        borderColor: 'rgb(255, 159, 64)',
                        backgroundColor: 'rgba(255, 159, 64, 0.1)',
                        tension: 0.1
                    }, {
                        label: 'P99',
                        data: [],
                        borderColor: 'rgb(255, 99, 132)',
                        backgroundColor: 'rgba(255, 99, 132, 0.1)',
                        tension: 0.1
                    }]
                },
                options: {
                    ...this.chartOptions,
                    scales: {
                        ...this.chartOptions.scales,
                        y: {
                            ...this.chartOptions.scales.y,
                            title: {
                                display: true,
                                text: 'Latency (ms)'
                            }
                        }
                    },
                    plugins: {
                        ...this.chartOptions.plugins,
                        title: {
                            display: true,
                            text: 'Audio Latency Percentiles'
                        }
                    }
                }
            });
        }
        
        // Buffer Fill Chart
        const bufferCtx = document.getElementById('bufferChart')?.getContext('2d');
        if (bufferCtx) {
            this.charts.buffer = new Chart(bufferCtx, {
                type: 'line',
                data: {
                    datasets: [{
                        label: 'Buffer Fill %',
                        data: [],
                        borderColor: 'rgb(54, 162, 235)',
                        backgroundColor: 'rgba(54, 162, 235, 0.1)',
                        tension: 0.1
                    }]
                },
                options: {
                    ...this.chartOptions,
                    scales: {
                        ...this.chartOptions.scales,
                        y: {
                            ...this.chartOptions.scales.y,
                            min: 0,
                            max: 100,
                            title: {
                                display: true,
                                text: 'Buffer Fill (%)'
                            }
                        }
                    },
                    plugins: {
                        ...this.chartOptions.plugins,
                        title: {
                            display: true,
                            text: 'Audio Buffer Levels'
                        },
                        annotation: {
                            annotations: {
                                warningLine: {
                                    type: 'line',
                                    yMin: 20,
                                    yMax: 20,
                                    borderColor: 'rgb(255, 159, 64)',
                                    borderWidth: 2,
                                    borderDash: [5, 5],
                                    label: {
                                        content: 'Warning',
                                        enabled: true,
                                        position: 'start'
                                    }
                                },
                                criticalLine: {
                                    type: 'line',
                                    yMin: 10,
                                    yMax: 10,
                                    borderColor: 'rgb(255, 99, 132)',
                                    borderWidth: 2,
                                    borderDash: [5, 5],
                                    label: {
                                        content: 'Critical',
                                        enabled: true,
                                        position: 'start'
                                    }
                                }
                            }
                        }
                    }
                }
            });
        }
        
        // Throughput Chart
        const throughputCtx = document.getElementById('throughputChart')?.getContext('2d');
        if (throughputCtx) {
            this.charts.throughput = new Chart(throughputCtx, {
                type: 'line',
                data: {
                    datasets: [{
                        label: 'Throughput',
                        data: [],
                        borderColor: 'rgb(153, 102, 255)',
                        backgroundColor: 'rgba(153, 102, 255, 0.1)',
                        tension: 0.1
                    }]
                },
                options: {
                    ...this.chartOptions,
                    scales: {
                        ...this.chartOptions.scales,
                        y: {
                            ...this.chartOptions.scales.y,
                            title: {
                                display: true,
                                text: 'Throughput (bytes/s)'
                            }
                        }
                    },
                    plugins: {
                        ...this.chartOptions.plugins,
                        title: {
                            display: true,
                            text: 'Audio Throughput'
                        }
                    }
                }
            });
        }
        
        // System Resources Chart
        const resourcesCtx = document.getElementById('resourcesChart')?.getContext('2d');
        if (resourcesCtx) {
            this.charts.resources = new Chart(resourcesCtx, {
                type: 'line',
                data: {
                    datasets: [{
                        label: 'CPU %',
                        data: [],
                        borderColor: 'rgb(255, 206, 86)',
                        backgroundColor: 'rgba(255, 206, 86, 0.1)',
                        tension: 0.1,
                        yAxisID: 'y'
                    }, {
                        label: 'Memory MB',
                        data: [],
                        borderColor: 'rgb(75, 192, 192)',
                        backgroundColor: 'rgba(75, 192, 192, 0.1)',
                        tension: 0.1,
                        yAxisID: 'y1'
                    }]
                },
                options: {
                    ...this.chartOptions,
                    scales: {
                        x: this.chartOptions.scales.x,
                        y: {
                            type: 'linear',
                            display: true,
                            position: 'left',
                            title: {
                                display: true,
                                text: 'CPU Usage (%)'
                            },
                            min: 0,
                            max: 100
                        },
                        y1: {
                            type: 'linear',
                            display: true,
                            position: 'right',
                            title: {
                                display: true,
                                text: 'Memory (MB)'
                            },
                            grid: {
                                drawOnChartArea: false
                            }
                        }
                    },
                    plugins: {
                        ...this.chartOptions.plugins,
                        title: {
                            display: true,
                            text: 'System Resources'
                        }
                    }
                }
            });
        }
    }
    
    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/v2/audio/stats/ws`;
        
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
            console.log('[AudioMonitor] WebSocket connected');
            this.updateConnectionStatus('connected');
        };
        
        this.ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                this.handleWebSocketMessage(message);
            } catch (error) {
                console.error('[AudioMonitor] Failed to parse WebSocket message:', error);
            }
        };
        
        this.ws.onerror = (error) => {
            console.error('[AudioMonitor] WebSocket error:', error);
            this.updateConnectionStatus('error');
        };
        
        this.ws.onclose = () => {
            console.log('[AudioMonitor] WebSocket disconnected');
            this.updateConnectionStatus('disconnected');
            
            // Attempt to reconnect after 5 seconds
            timerManager.setTimeout(() => {
                this.connectWebSocket();
            }, 5000);
        };
    }
    
    handleWebSocketMessage(message) {
        switch (message.type) {
            case 'initial':
                this.updateAllMetrics(message.data);
                break;
            case 'periodic':
                this.updateRealtimeMetrics(message.data);
                break;
            case 'audio_metrics_alert':
                this.handleAlert(message.data);
                break;
            default:
                console.log('[AudioMonitor] Unknown message type:', message.type);
        }
    }
    
    async loadCurrentStats() {
        try {
            const response = await fetch('/api/v2/audio/stats/current');
            const result = await response.json();
            
            if (result.status === 'success') {
                this.updateAllMetrics(result.data);
            }
        } catch (error) {
            console.error('[AudioMonitor] Failed to load current stats:', error);
        }
    }
    
    async loadHistoricalData() {
        try {
            const response = await fetch(
                `/api/v2/audio/stats/history?duration=${this.selectedTimeRange}&resolution=${this.selectedResolution}`
            );
            const result = await response.json();
            
            if (result.status === 'success') {
                this.updateHistoricalCharts(result.data.history);
            }
        } catch (error) {
            console.error('[AudioMonitor] Failed to load historical data:', error);
        }
    }
    
    updateAllMetrics(data) {
        // Update metric cards
        this.updateMetricCard('latency', data.current_latency_ms, 'ms');
        this.updateMetricCard('buffer', data.buffer_fill_percent, '%');
        this.updateMetricCard('streams', data.active_streams, '');
        this.updateMetricCard('errors', (data.error_rate * 100).toFixed(2), '%');
        
        // Update percentile stats
        if (data.latency_percentiles) {
            this.updatePercentileStats('latency', data.latency_percentiles);
        }
        if (data.buffer_percentiles) {
            this.updatePercentileStats('buffer', data.buffer_percentiles);
        }
        
        // Update recovery metrics
        if (data.recovery_metrics) {
            this.updateRecoveryMetrics(data.recovery_metrics);
        }
        
        // Update fallback usage
        if (data.fallback_tier_usage) {
            this.updateFallbackUsage(data.fallback_tier_usage);
        }
        
        // Update cache metrics
        if (data.cache_metrics) {
            this.updateCacheMetrics(data.cache_metrics);
        }
        
        // Update health status
        if (data.recovery_health) {
            this.updateHealthStatus(data.recovery_health);
        }
    }
    
    updateRealtimeMetrics(data) {
        const timestamp = new Date();
        
        // Add data point to charts
        if (this.charts.latency && data.latency_ms !== undefined) {
            this.addChartDataPoint(this.charts.latency, 0, {
                x: timestamp,
                y: data.latency_ms
            });
        }
        
        if (this.charts.buffer && data.buffer_fill !== undefined) {
            this.addChartDataPoint(this.charts.buffer, 0, {
                x: timestamp,
                y: data.buffer_fill
            });
        }
        
        // Update metric cards
        this.updateMetricCard('latency', data.latency_ms, 'ms');
        this.updateMetricCard('buffer', data.buffer_fill, '%');
        this.updateMetricCard('streams', data.active_streams, '');
        this.updateMetricCard('errors', (data.error_rate * 100).toFixed(2), '%');
    }
    
    updateHistoricalCharts(history) {
        if (!history || history.length === 0) return;
        
        // Clear existing data
        Object.values(this.charts).forEach(chart => {
            chart.data.datasets.forEach(dataset => {
                dataset.data = [];
            });
        });
        
        // Process historical data
        history.forEach(point => {
            const timestamp = new Date(point.timestamp * 1000);
            
            // Update buffer chart
            if (this.charts.buffer) {
                this.charts.buffer.data.datasets[0].data.push({
                    x: timestamp,
                    y: point.buffer_fill_percent
                });
            }
            
            // Update throughput chart
            if (this.charts.throughput) {
                this.charts.throughput.data.datasets[0].data.push({
                    x: timestamp,
                    y: point.throughput_bps
                });
            }
            
            // Update resources chart
            if (this.charts.resources) {
                this.charts.resources.data.datasets[0].data.push({
                    x: timestamp,
                    y: point.cpu_percent
                });
                this.charts.resources.data.datasets[1].data.push({
                    x: timestamp,
                    y: point.memory_mb
                });
            }
        });
        
        // Update all charts
        Object.values(this.charts).forEach(chart => chart.update());
    }
    
    addChartDataPoint(chart, datasetIndex, dataPoint) {
        const dataset = chart.data.datasets[datasetIndex];
        dataset.data.push(dataPoint);
        
        // Limit data points
        if (dataset.data.length > this.maxDataPoints) {
            dataset.data.shift();
        }
        
        chart.update('none'); // Update without animation
    }
    
    updateMetricCard(metric, value, unit) {
        const element = document.getElementById(`metric-${metric}`);
        if (element) {
            element.textContent = `${value.toFixed(1)}${unit}`;
            
            // Update status color based on thresholds
            const card = element.closest('.metric-card');
            if (card) {
                card.classList.remove('status-good', 'status-warning', 'status-critical');
                
                if (metric === 'latency') {
                    if (value > 200) {
                        card.classList.add('status-critical');
                    } else if (value > 100) {
                        card.classList.add('status-warning');
                    } else {
                        card.classList.add('status-good');
                    }
                } else if (metric === 'buffer') {
                    if (value < 10) {
                        card.classList.add('status-critical');
                    } else if (value < 20) {
                        card.classList.add('status-warning');
                    } else {
                        card.classList.add('status-good');
                    }
                } else if (metric === 'errors') {
                    if (value > 5) {
                        card.classList.add('status-critical');
                    } else if (value > 1) {
                        card.classList.add('status-warning');
                    } else {
                        card.classList.add('status-good');
                    }
                }
            }
        }
    }
    
    updatePercentileStats(metric, percentiles) {
        const container = document.getElementById(`${metric}-percentiles`);
        if (container) {
            container.innerHTML = `
                <div class="percentile-stat">
                    <span class="label">P50:</span>
                    <span class="value">${percentiles.p50.toFixed(1)}</span>
                </div>
                <div class="percentile-stat">
                    <span class="label">P90:</span>
                    <span class="value">${percentiles.p90.toFixed(1)}</span>
                </div>
                <div class="percentile-stat">
                    <span class="label">P95:</span>
                    <span class="value">${percentiles.p95.toFixed(1)}</span>
                </div>
                <div class="percentile-stat">
                    <span class="label">P99:</span>
                    <span class="value">${percentiles.p99.toFixed(1)}</span>
                </div>
            `;
        }
    }
    
    updateRecoveryMetrics(metrics) {
        const container = document.getElementById('recovery-metrics');
        if (container) {
            container.innerHTML = `
                <div class="recovery-stat">
                    <span class="label">Circuit Trips:</span>
                    <span class="value">${metrics.circuit_breaker_trips}</span>
                </div>
                <div class="recovery-stat">
                    <span class="label">Recovery Success:</span>
                    <span class="value">${metrics.recovery_successes}/${metrics.recovery_attempts}</span>
                </div>
                <div class="recovery-stat">
                    <span class="label">Predicted Failures:</span>
                    <span class="value">${metrics.predicted_failures}</span>
                </div>
                <div class="recovery-stat">
                    <span class="label">Prevented:</span>
                    <span class="value">${metrics.prevented_failures}</span>
                </div>
            `;
        }
    }
    
    updateFallbackUsage(usage) {
        const container = document.getElementById('fallback-usage');
        if (container) {
            const total = Object.values(usage).reduce((a, b) => a + b, 0);
            
            let html = '<div class="fallback-bars">';
            for (const [tier, count] of Object.entries(usage)) {
                const percentage = total > 0 ? (count / total * 100) : 0;
                html += `
                    <div class="fallback-bar">
                        <span class="tier-name">${tier}:</span>
                        <div class="bar-container">
                            <div class="bar" style="width: ${percentage}%"></div>
                        </div>
                        <span class="count">${count}</span>
                    </div>
                `;
            }
            html += '</div>';
            container.innerHTML = html;
        }
    }
    
    updateCacheMetrics(metrics) {
        const container = document.getElementById('cache-metrics');
        if (container) {
            container.innerHTML = `
                <div class="cache-stat">
                    <span class="label">Hit Rate:</span>
                    <span class="value">${metrics.hit_rate.toFixed(1)}%</span>
                </div>
                <div class="cache-stat">
                    <span class="label">Hits:</span>
                    <span class="value">${metrics.hits}</span>
                </div>
                <div class="cache-stat">
                    <span class="label">Misses:</span>
                    <span class="value">${metrics.misses}</span>
                </div>
            `;
        }
    }
    
    updateHealthStatus(health) {
        const container = document.getElementById('health-status');
        if (container) {
            container.className = `health-status health-${health.overall_state}`;
            container.innerHTML = `
                <div class="health-indicator">
                    <span class="status-dot"></span>
                    <span class="status-text">${health.overall_state.toUpperCase()}</span>
                </div>
                <div class="uptime">
                    Uptime: ${health.uptime_percentage.toFixed(2)}%
                </div>
            `;
            
            // Update component health
            const componentsContainer = document.getElementById('component-health');
            if (componentsContainer) {
                let html = '';
                for (const [component, status] of Object.entries(health.components)) {
                    html += `
                        <div class="component-status component-${status.state}">
                            <span class="component-name">${component}:</span>
                            <span class="component-state">${status.state}</span>
                            <span class="component-failures">(${status.failures} failures)</span>
                        </div>
                    `;
                }
                componentsContainer.innerHTML = html;
            }
        }
    }
    
    updateConnectionStatus(status) {
        const indicator = document.getElementById('ws-status');
        if (indicator) {
            indicator.className = `connection-status status-${status}`;
            indicator.textContent = status;
        }
    }
    
    handleAlert(alert) {
        console.log('[AudioMonitor] Alert received:', alert);
        
        // Display alert in UI
        const alertsContainer = document.getElementById('alerts-container');
        if (alertsContainer) {
            const alertElement = document.createElement('div');
            alertElement.className = `alert alert-${alert.level}`;
            alertElement.innerHTML = `
                <span class="alert-time">${new Date().toLocaleTimeString()}</span>
                <span class="alert-message">${alert.message}</span>
                <button class="alert-close" onclick="this.parentElement.remove()">×</button>
            `;
            
            alertsContainer.insertBefore(alertElement, alertsContainer.firstChild);
            
            // Limit number of alerts displayed
            while (alertsContainer.children.length > 10) {
                alertsContainer.removeChild(alertsContainer.lastChild);
            }
        }
    }
    
    setupEventListeners() {
        // Time range selector
        const timeRangeSelector = document.getElementById('time-range');
        if (timeRangeSelector) {
            timeRangeSelector.addEventListener('change', (e) => {
                this.selectedTimeRange = e.target.value;
                this.loadHistoricalData();
            });
        }
        
        // Resolution selector
        const resolutionSelector = document.getElementById('resolution');
        if (resolutionSelector) {
            resolutionSelector.addEventListener('change', (e) => {
                this.selectedResolution = e.target.value;
                this.loadHistoricalData();
            });
        }
        
        // Export button
        const exportButton = document.getElementById('export-metrics');
        if (exportButton) {
            exportButton.addEventListener('click', () => {
                this.exportMetrics();
            });
        }
        
        // Reset button
        const resetButton = document.getElementById('reset-metrics');
        if (resetButton) {
            resetButton.addEventListener('click', () => {
                if (confirm('Are you sure you want to reset all audio metrics?')) {
                    this.resetMetrics();
                }
            });
        }
        
        // Refresh button
        const refreshButton = document.getElementById('refresh-metrics');
        if (refreshButton) {
            refreshButton.addEventListener('click', () => {
                this.loadCurrentStats();
                this.loadHistoricalData();
            });
        }
    }
    
    startPeriodicUpdates() {
        // Update every 5 seconds
        this.updateInterval = timerManager.setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send('stats');
            }
        }, 5000);
    }
    
    async exportMetrics() {
        try {
            const format = document.getElementById('export-format')?.value || 'json';
            const duration = this.selectedTimeRange;
            
            const response = await fetch(
                `/api/v2/audio/stats/export?format=${format}&duration=${duration}`
            );
            
            if (response.ok) {
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `audio_metrics_${new Date().toISOString()}.${format}`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
                
                console.log('[AudioMonitor] Metrics exported successfully');
            } else {
                console.error('[AudioMonitor] Failed to export metrics');
            }
        } catch (error) {
            console.error('[AudioMonitor] Export error:', error);
        }
    }
    
    async resetMetrics() {
        try {
            const response = await fetch('/api/v2/audio/stats/reset', {
                method: 'POST'
            });
            
            if (response.ok) {
                console.log('[AudioMonitor] Metrics reset successfully');
                
                // Clear charts
                Object.values(this.charts).forEach(chart => {
                    chart.data.datasets.forEach(dataset => {
                        dataset.data = [];
                    });
                    chart.update();
                });
                
                // Reload data
                await this.loadCurrentStats();
            } else {
                console.error('[AudioMonitor] Failed to reset metrics');
            }
        } catch (error) {
            console.error('[AudioMonitor] Reset error:', error);
        }
    }
    
    destroy() {
        // Cleanup
        if (this.ws) {
            this.ws.close();
        }
        
        if (this.updateInterval) {
            timerManager.clearInterval(this.updateInterval);
        }
        
        Object.values(this.charts).forEach(chart => {
            chart.destroy();
        });
    }
}

// Initialize when document is ready
document.addEventListener('DOMContentLoaded', () => {
    window.audioMonitor = new AudioMonitor();
    window.audioMonitor.init();
});
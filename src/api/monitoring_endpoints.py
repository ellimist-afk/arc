"""
Monitoring API Endpoints for TalkBot
Provides health, metrics, and dashboard endpoints
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import PlainTextResponse, HTMLResponse
from typing import Dict, Any, Optional
import json
from datetime import datetime

router = APIRouter(prefix="/api/v2/monitoring", tags=["monitoring"])


def get_metrics_collector():
    """Dependency to get metrics collector"""
    # This would be injected from the main app
    from src.bot.bot import TalkBot
    # In production, this would come from app state
    return getattr(TalkBot, 'metrics_collector', None)


def get_health_checker():
    """Dependency to get health checker"""
    # This would be injected from the main app
    from src.bot.bot import TalkBot
    # In production, this would come from app state
    return getattr(TalkBot, 'health_checker', None)


@router.get("/health")
async def health_check(checker = Depends(get_health_checker)):
    """
    Overall health check endpoint
    Returns 200 if healthy, 503 if unhealthy
    """
    if not checker:
        raise HTTPException(status_code=503, detail="Health checker not available")
    
    health = checker.get_overall_health()
    
    if health['status'] == 'unhealthy':
        raise HTTPException(status_code=503, detail=health)
    
    return health


@router.get("/health/live")
async def liveness_probe(checker = Depends(get_health_checker)):
    """
    Kubernetes liveness probe endpoint
    Returns 200 if the service is alive
    """
    if not checker:
        return {"alive": True, "timestamp": datetime.now().isoformat()}
    
    return await checker.get_liveness()


@router.get("/health/ready")
async def readiness_probe(checker = Depends(get_health_checker)):
    """
    Kubernetes readiness probe endpoint
    Returns 200 if ready to serve traffic, 503 if not
    """
    if not checker:
        raise HTTPException(status_code=503, detail="Health checker not available")
    
    readiness = await checker.get_readiness()
    
    if not readiness['ready']:
        raise HTTPException(status_code=503, detail=readiness)
    
    return readiness


@router.get("/metrics")
async def get_metrics(collector = Depends(get_metrics_collector)):
    """
    Get current performance metrics
    """
    if not collector:
        raise HTTPException(status_code=503, detail="Metrics collector not available")
    
    metrics = collector.get_current_metrics()
    
    return {
        "timestamp": metrics.timestamp.isoformat(),
        "response_times": {
            "p50": metrics.response_time_p50,
            "p95": metrics.response_time_p95,
            "p99": metrics.response_time_p99
        },
        "throughput": {
            "messages_per_second": metrics.messages_per_second,
            "audio_queue_length": metrics.audio_queue_length
        },
        "resources": {
            "cpu_percent": metrics.cpu_percent,
            "memory_mb": metrics.memory_mb
        },
        "cache": {
            "hit_rate": metrics.cache_hit_rate,
            "context_build_time_ms": metrics.context_build_time_ms
        },
        "health": {
            "error_rate": metrics.error_rate,
            "twitch_connected": metrics.twitch_connected,
            "voice_active": metrics.voice_active
        }
    }


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
async def prometheus_metrics(collector = Depends(get_metrics_collector)):
    """
    Export metrics in Prometheus format
    """
    if not collector:
        raise HTTPException(status_code=503, detail="Metrics collector not available")
    
    return collector.to_prometheus()


@router.get("/dashboard")
async def monitoring_dashboard(collector = Depends(get_metrics_collector)):
    """
    Get dashboard data for monitoring UI
    """
    if not collector:
        raise HTTPException(status_code=503, detail="Metrics collector not available")
    
    return collector.get_dashboard_data()


@router.get("/dashboard/ui", response_class=HTMLResponse)
async def dashboard_ui():
    """
    Simple HTML dashboard for monitoring
    """
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>TalkBot Monitoring Dashboard</title>
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
            }
            .container {
                max-width: 1400px;
                margin: 0 auto;
            }
            h1 {
                text-align: center;
                margin-bottom: 30px;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
            }
            .metrics-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }
            .metric-card {
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 15px;
                padding: 20px;
                box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                border: 1px solid rgba(255, 255, 255, 0.18);
            }
            .metric-title {
                font-size: 14px;
                opacity: 0.8;
                margin-bottom: 5px;
            }
            .metric-value {
                font-size: 32px;
                font-weight: bold;
                margin-bottom: 5px;
            }
            .metric-subtitle {
                font-size: 12px;
                opacity: 0.6;
            }
            .status-indicator {
                display: inline-block;
                width: 10px;
                height: 10px;
                border-radius: 50%;
                margin-right: 5px;
            }
            .status-healthy { background: #4ade80; }
            .status-unhealthy { background: #f87171; }
            .alert {
                background: rgba(248, 113, 113, 0.2);
                border-left: 4px solid #f87171;
                padding: 10px;
                margin-bottom: 10px;
                border-radius: 5px;
            }
            .chart-container {
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 15px;
                padding: 20px;
                margin-bottom: 20px;
                height: 300px;
            }
            #refresh-status {
                text-align: center;
                margin-top: 20px;
                opacity: 0.8;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 TalkBot Monitoring Dashboard</h1>
            
            <div class="metrics-grid" id="metrics-grid">
                <!-- Metrics will be populated here -->
            </div>
            
            <div class="chart-container">
                <h3>Response Times (last 10 minutes)</h3>
                <canvas id="response-chart"></canvas>
            </div>
            
            <div id="alerts-container">
                <!-- Alerts will be shown here -->
            </div>
            
            <div id="refresh-status">Auto-refreshing every 5 seconds...</div>
        </div>
        
        <script>
            async function fetchDashboardData() {
                try {
                    const response = await fetch('/api/v2/monitoring/dashboard');
                    const data = await response.json();
                    updateDashboard(data);
                } catch (error) {
                    console.error('Failed to fetch dashboard data:', error);
                }
            }
            
            function updateDashboard(data) {
                // Update metrics grid
                const metricsGrid = document.getElementById('metrics-grid');
                metricsGrid.innerHTML = `
                    <div class="metric-card">
                        <div class="metric-title">Response Time (P95)</div>
                        <div class="metric-value">${data.performance.response_times.p95.toFixed(0)}ms</div>
                        <div class="metric-subtitle">Target: <200ms</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Messages/Second</div>
                        <div class="metric-value">${data.performance.throughput.messages_per_second.toFixed(2)}</div>
                        <div class="metric-subtitle">Total: ${data.performance.throughput.total_messages}</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Cache Hit Rate</div>
                        <div class="metric-value">${data.performance.cache.hit_rate}%</div>
                        <div class="metric-subtitle">Target: >40%</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Memory Usage</div>
                        <div class="metric-value">${data.resources.memory_mb.toFixed(0)}MB</div>
                        <div class="metric-subtitle">Limit: 500MB</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">CPU Usage</div>
                        <div class="metric-value">${data.resources.cpu_percent}%</div>
                        <div class="metric-subtitle">Limit: 80%</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Uptime</div>
                        <div class="metric-value">${data.uptime.split('.')[0]}</div>
                        <div class="metric-subtitle">Since last restart</div>
                    </div>
                `;
                
                // Update health indicators
                for (const [service, healthy] of Object.entries(data.health)) {
                    const statusClass = healthy ? 'status-healthy' : 'status-unhealthy';
                    // Add health indicators to the grid
                }
                
                // Update alerts
                const alertsContainer = document.getElementById('alerts-container');
                if (data.alerts && data.alerts.length > 0) {
                    alertsContainer.innerHTML = '<h3>Recent Alerts</h3>' + 
                        data.alerts.map(alert => `
                            <div class="alert">
                                <strong>${alert.type}</strong>: ${alert.message}
                                <br><small>${alert.timestamp}</small>
                            </div>
                        `).join('');
                } else {
                    alertsContainer.innerHTML = '';
                }
            }
            
            // Initial fetch
            fetchDashboardData();
            
            // Auto-refresh every 5 seconds
            setInterval(fetchDashboardData, 5000);
        </script>
    </body>
    </html>
    """
    
    return html_content


@router.get("/alerts")
async def get_alerts(
    collector = Depends(get_metrics_collector),
    limit: int = 10
):
    """
    Get recent alerts
    """
    if not collector:
        raise HTTPException(status_code=503, detail="Metrics collector not available")
    
    return {
        "alerts": collector.alerts[-limit:],
        "total": len(collector.alerts)
    }


@router.post("/alerts/clear")
async def clear_alerts(collector = Depends(get_metrics_collector)):
    """
    Clear all alerts
    """
    if not collector:
        raise HTTPException(status_code=503, detail="Metrics collector not available")
    
    collector.alerts.clear()
    
    return {"message": "Alerts cleared"}
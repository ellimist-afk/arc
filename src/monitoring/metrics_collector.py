"""
Metrics Collector for TalkBot
Implements PRD monitoring requirements with <100ms overhead
"""

import asyncio
import time
import psutil
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from collections import deque, defaultdict
from dataclasses import dataclass, field
import json

logger = logging.getLogger(__name__)


@dataclass
class MetricPoint:
    """Single metric data point"""
    timestamp: float
    value: float
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class PerformanceMetrics:
    """Performance metrics snapshot"""
    timestamp: datetime
    # Response times
    response_time_p50: float
    response_time_p95: float
    response_time_p99: float
    # Throughput
    messages_per_second: float
    audio_queue_length: int
    # Resource usage
    cpu_percent: float
    memory_mb: float
    # Cache metrics
    cache_hit_rate: float
    context_build_time_ms: float
    # Health indicators
    error_rate: float
    twitch_connected: bool
    voice_active: bool


class MetricsCollector:
    """
    Lightweight metrics collection with minimal overhead
    Implements PRD requirement for monitoring without external dependencies
    """
    
    def __init__(self, max_history_minutes: int = 60):
        # Metric storage (in-memory ring buffers)
        self.max_points = max_history_minutes * 60  # 1 point per second
        self.metrics: Dict[str, deque] = defaultdict(lambda: deque(maxlen=self.max_points))
        
        # Performance tracking
        self.response_times = deque(maxlen=1000)  # Last 1000 responses
        self.error_counts = deque(maxlen=60)  # Last 60 seconds
        self.message_counts = deque(maxlen=60)  # Last 60 seconds
        
        # Resource monitoring
        self.process = psutil.Process()
        self.start_time = datetime.now()
        
        # Aggregated stats
        self.total_messages = 0
        self.total_errors = 0
        self.total_audio_generated = 0
        self.cache_hits = 0
        self.cache_misses = 0
        
        # Health status
        self.health_checks: Dict[str, bool] = {
            'database': True,
            'redis': True,
            'twitch': True,
            'openai': True,
            'voice': False
        }
        
        # Alerts
        self.alerts: List[Dict[str, Any]] = []
        self.alert_thresholds = {
            'response_time_p95': 500,  # ms
            'error_rate': 0.05,  # 5%
            'memory_mb': 500,  # MB
            'cpu_percent': 80,  # %
            'audio_queue_length': 10
        }
    
    def record_response_time(self, duration_ms: float, labels: Optional[Dict[str, str]] = None):
        """Record response time metric"""
        self.response_times.append(duration_ms)
        self.metrics['response_time'].append(
            MetricPoint(time.time(), duration_ms, labels or {})
        )
        
        # Check for slow responses
        if duration_ms > self.alert_thresholds['response_time_p95']:
            self._create_alert('slow_response', f"Response time {duration_ms:.0f}ms exceeds threshold")
    
    def record_message(self, message_type: str = 'chat'):
        """Record message processed"""
        self.total_messages += 1
        self.message_counts.append(time.time())
        self.metrics['messages'].append(
            MetricPoint(time.time(), 1, {'type': message_type})
        )
    
    def record_error(self, error_type: str, error_message: str):
        """Record error occurrence"""
        self.total_errors += 1
        self.error_counts.append(time.time())
        self.metrics['errors'].append(
            MetricPoint(time.time(), 1, {'type': error_type, 'message': error_message[:100]})
        )
        
        # Check error rate
        error_rate = self.get_error_rate()
        if error_rate > self.alert_thresholds['error_rate']:
            self._create_alert('high_error_rate', f"Error rate {error_rate:.1%} exceeds threshold")
    
    def record_cache_access(self, hit: bool):
        """Record cache hit/miss"""
        if hit:
            self.cache_hits += 1
        else:
            self.cache_misses += 1
        
        self.metrics['cache'].append(
            MetricPoint(time.time(), 1 if hit else 0, {'result': 'hit' if hit else 'miss'})
        )
    
    def record_audio_generation(self, duration_ms: float, cached: bool = False):
        """Record audio generation"""
        self.total_audio_generated += 1
        self.metrics['audio'].append(
            MetricPoint(time.time(), duration_ms, {'cached': str(cached)})
        )
    
    def update_health(self, service: str, healthy: bool):
        """Update service health status"""
        self.health_checks[service] = healthy
        if not healthy:
            self._create_alert('service_unhealthy', f"Service {service} is unhealthy")
    
    def get_current_metrics(self) -> PerformanceMetrics:
        """Get current performance metrics snapshot"""
        now = datetime.now()
        
        # Calculate percentiles for response times
        sorted_times = sorted(self.response_times) if self.response_times else [0]
        p50_idx = len(sorted_times) // 2
        p95_idx = int(len(sorted_times) * 0.95)
        p99_idx = int(len(sorted_times) * 0.99)
        
        # Calculate throughput
        recent_messages = [t for t in self.message_counts if time.time() - t < 60]
        messages_per_second = len(recent_messages) / 60.0 if recent_messages else 0
        
        # Get resource usage
        try:
            cpu_percent = self.process.cpu_percent()
            memory_info = self.process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
        except:
            cpu_percent = 0
            memory_mb = 0
        
        # Calculate cache hit rate
        total_cache = self.cache_hits + self.cache_misses
        cache_hit_rate = self.cache_hits / total_cache if total_cache > 0 else 0
        
        # Get context build time (from recent metrics)
        context_times = [p.value for p in list(self.metrics['context_build'])[-100:]]
        avg_context_time = sum(context_times) / len(context_times) if context_times else 0
        
        # Check resource alerts
        if memory_mb > self.alert_thresholds['memory_mb']:
            self._create_alert('high_memory', f"Memory usage {memory_mb:.0f}MB exceeds threshold")
        if cpu_percent > self.alert_thresholds['cpu_percent']:
            self._create_alert('high_cpu', f"CPU usage {cpu_percent:.0f}% exceeds threshold")
        
        return PerformanceMetrics(
            timestamp=now,
            response_time_p50=sorted_times[p50_idx] if sorted_times else 0,
            response_time_p95=sorted_times[min(p95_idx, len(sorted_times)-1)] if sorted_times else 0,
            response_time_p99=sorted_times[min(p99_idx, len(sorted_times)-1)] if sorted_times else 0,
            messages_per_second=messages_per_second,
            audio_queue_length=len([p for p in list(self.metrics['audio_queue'])[-1:] if p.value > 0]),
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
            cache_hit_rate=cache_hit_rate,
            context_build_time_ms=avg_context_time,
            error_rate=self.get_error_rate(),
            twitch_connected=self.health_checks.get('twitch', False),
            voice_active=self.health_checks.get('voice', False)
        )
    
    def get_error_rate(self) -> float:
        """Calculate current error rate"""
        recent_errors = [t for t in self.error_counts if time.time() - t < 60]
        recent_messages = [t for t in self.message_counts if time.time() - t < 60]
        
        if not recent_messages:
            return 0.0
        
        return len(recent_errors) / len(recent_messages)
    
    def get_uptime(self) -> timedelta:
        """Get bot uptime"""
        return datetime.now() - self.start_time
    
    def _create_alert(self, alert_type: str, message: str):
        """Create an alert"""
        alert = {
            'timestamp': datetime.now().isoformat(),
            'type': alert_type,
            'message': message
        }
        self.alerts.append(alert)
        
        # Keep only recent alerts (last 100)
        if len(self.alerts) > 100:
            self.alerts = self.alerts[-100:]
        
        logger.warning(f"Alert: {alert_type} - {message}")
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get data for monitoring dashboard"""
        metrics = self.get_current_metrics()
        
        return {
            'timestamp': metrics.timestamp.isoformat(),
            'uptime': str(self.get_uptime()),
            'performance': {
                'response_times': {
                    'p50': round(metrics.response_time_p50, 2),
                    'p95': round(metrics.response_time_p95, 2),
                    'p99': round(metrics.response_time_p99, 2)
                },
                'throughput': {
                    'messages_per_second': round(metrics.messages_per_second, 2),
                    'total_messages': self.total_messages,
                    'total_errors': self.total_errors
                },
                'cache': {
                    'hit_rate': round(metrics.cache_hit_rate * 100, 1),
                    'hits': self.cache_hits,
                    'misses': self.cache_misses
                }
            },
            'resources': {
                'cpu_percent': round(metrics.cpu_percent, 1),
                'memory_mb': round(metrics.memory_mb, 1),
                'audio_queue_length': metrics.audio_queue_length
            },
            'health': self.health_checks,
            'alerts': self.alerts[-10:],  # Last 10 alerts
            'graphs': {
                'response_times': self._get_time_series('response_time', minutes=10),
                'messages': self._get_time_series('messages', minutes=10),
                'errors': self._get_time_series('errors', minutes=10),
                'memory': self._get_resource_history('memory', minutes=10),
                'cpu': self._get_resource_history('cpu', minutes=10)
            }
        }
    
    def _get_time_series(self, metric_name: str, minutes: int = 10) -> List[Dict[str, Any]]:
        """Get time series data for graphing"""
        cutoff = time.time() - (minutes * 60)
        points = [p for p in self.metrics[metric_name] if p.timestamp > cutoff]
        
        # Aggregate by second
        aggregated = defaultdict(list)
        for point in points:
            second = int(point.timestamp)
            aggregated[second].append(point.value)
        
        return [
            {
                'timestamp': second,
                'value': sum(values) / len(values)
            }
            for second, values in sorted(aggregated.items())
        ]
    
    def _get_resource_history(self, resource: str, minutes: int = 10) -> List[Dict[str, Any]]:
        """Get resource usage history"""
        # This would need to be collected periodically
        # For now, return current value
        metrics = self.get_current_metrics()
        current_value = metrics.memory_mb if resource == 'memory' else metrics.cpu_percent
        
        return [{
            'timestamp': int(time.time()),
            'value': current_value
        }]
    
    async def export_to_database(self, db_session):
        """Export metrics to database"""
        try:
            from sqlalchemy import text
            
            metrics = self.get_current_metrics()
            
            # Insert aggregated metrics
            await db_session.execute(
                text("""
                    INSERT INTO metrics (metric_name, metric_value, metric_type, labels, timestamp)
                    VALUES 
                        ('response_time_p95', :p95, 'gauge', '{}', NOW()),
                        ('messages_per_second', :mps, 'gauge', '{}', NOW()),
                        ('cache_hit_rate', :chr, 'gauge', '{}', NOW()),
                        ('memory_mb', :mem, 'gauge', '{}', NOW()),
                        ('error_rate', :err, 'gauge', '{}', NOW())
                """),
                {
                    'p95': metrics.response_time_p95,
                    'mps': metrics.messages_per_second,
                    'chr': metrics.cache_hit_rate,
                    'mem': metrics.memory_mb,
                    'err': metrics.error_rate
                }
            )
            
            await db_session.commit()
            
        except Exception as e:
            logger.error(f"Failed to export metrics: {e}")
    
    def to_prometheus(self) -> str:
        """Export metrics in Prometheus format"""
        metrics = self.get_current_metrics()
        lines = []
        
        # Response times
        lines.append(f"# HELP talkbot_response_time_ms Response time in milliseconds")
        lines.append(f"# TYPE talkbot_response_time_ms summary")
        lines.append(f'talkbot_response_time_ms{{quantile="0.5"}} {metrics.response_time_p50}')
        lines.append(f'talkbot_response_time_ms{{quantile="0.95"}} {metrics.response_time_p95}')
        lines.append(f'talkbot_response_time_ms{{quantile="0.99"}} {metrics.response_time_p99}')
        
        # Throughput
        lines.append(f"# HELP talkbot_messages_total Total messages processed")
        lines.append(f"# TYPE talkbot_messages_total counter")
        lines.append(f"talkbot_messages_total {self.total_messages}")
        
        # Errors
        lines.append(f"# HELP talkbot_errors_total Total errors")
        lines.append(f"# TYPE talkbot_errors_total counter")
        lines.append(f"talkbot_errors_total {self.total_errors}")
        
        # Resources
        lines.append(f"# HELP talkbot_memory_mb Memory usage in MB")
        lines.append(f"# TYPE talkbot_memory_mb gauge")
        lines.append(f"talkbot_memory_mb {metrics.memory_mb}")
        
        # Cache
        lines.append(f"# HELP talkbot_cache_hit_rate Cache hit rate")
        lines.append(f"# TYPE talkbot_cache_hit_rate gauge")
        lines.append(f"talkbot_cache_hit_rate {metrics.cache_hit_rate}")
        
        # Health
        lines.append(f"# HELP talkbot_health Service health status")
        lines.append(f"# TYPE talkbot_health gauge")
        for service, healthy in self.health_checks.items():
            lines.append(f'talkbot_health{{service="{service}"}} {1 if healthy else 0}')
        
        return '\n'.join(lines)


# Singleton instance
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get the singleton metrics collector"""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector
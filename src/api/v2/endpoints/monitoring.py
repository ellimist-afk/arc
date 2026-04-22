"""
Monitoring Endpoint - V2 API
PRD: <300 lines, real-time monitoring data
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from datetime import datetime
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/metrics", response_model=Dict[str, Any])
async def get_monitoring_metrics() -> Dict[str, Any]:
    """
    Get current performance metrics for monitoring dashboard.
    Mirrors metrics_collector data.
    """
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot:
            raise HTTPException(status_code=503, detail="Bot not initialized")

        metrics = {
            "timestamp": datetime.now().isoformat(),
            "status": "healthy"
        }

        # Get metrics from metrics collector
        if hasattr(bot, 'metrics_collector') and bot.metrics_collector:
            collector_data = bot.metrics_collector.get_dashboard_data()
            metrics["metrics"] = collector_data
        else:
            # Fallback metrics
            metrics["metrics"] = {
                "messages": {
                    "total": getattr(bot, 'message_count', 0),
                    "responses": getattr(bot, 'response_count', 0),
                    "audio": getattr(bot, 'audio_count', 0)
                },
                "performance": {}
            }

            # Add performance if tracked
            if hasattr(bot, 'response_times') and bot.response_times:
                times = bot.response_times
                metrics["metrics"]["performance"] = {
                    "avg_response_ms": sum(times) / len(times) if times else 0,
                    "max_response_ms": max(times) if times else 0,
                    "min_response_ms": min(times) if times else 0,
                    "sample_count": len(times)
                }

        return metrics

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Monitoring metrics error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard-data", response_model=Dict[str, Any])
async def get_dashboard_data() -> Dict[str, Any]:
    """
    Get all data needed for the monitoring dashboard.
    Comprehensive view including health, metrics, services, and cache.
    """
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        data = {
            "timestamp": datetime.now().isoformat(),
            "health": "unknown",
            "uptime": "0s",
            "metrics": {},
            "services": {},
            "cache_stats": {},
            "memory_stats": {},
            "audio_stats": {}
        }

        if not bot:
            data["health"] = "no_bot"
            return data

        data["health"] = "healthy"

        # Uptime
        if hasattr(bot, 'start_time'):
            uptime_seconds = (datetime.now() - bot.start_time).total_seconds()
            data["uptime"] = f"{int(uptime_seconds)}s"
            data["uptime_seconds"] = uptime_seconds

        # Connection status
        data["connected"] = False
        if hasattr(bot, 'twitch_client') and bot.twitch_client:
            data["connected"] = bot.twitch_client.is_connected()

        # Metrics from collector
        if hasattr(bot, 'metrics_collector') and bot.metrics_collector:
            data["metrics"] = bot.metrics_collector.get_dashboard_data()

        # Service registry stats
        if hasattr(bot, 'service_registry') and bot.service_registry:
            service_stats = bot.service_registry.get_stats()
            data["services"] = service_stats
            data["service_count"] = len(service_stats.get('services', {}))

        # Memory system stats
        if hasattr(bot, 'memory_system') and bot.memory_system:
            try:
                memory_stats = await bot.memory_system.get_stats()
                data["memory_stats"] = memory_stats
            except Exception as e:
                logger.warning(f"Failed to get memory stats: {e}")

        # Audio queue and TTS cache stats
        if hasattr(bot, 'audio_queue') and bot.audio_queue:
            # Queue stats
            if hasattr(bot.audio_queue, 'get_stats'):
                try:
                    audio_stats = await bot.audio_queue.get_stats()
                    data["audio_stats"]["queue"] = audio_stats
                except Exception as e:
                    logger.warning(f"Failed to get audio stats: {e}")

            # TTS cache stats
            if hasattr(bot.audio_queue, 'persistent_cache'):
                try:
                    cache_stats = bot.audio_queue.persistent_cache.get_stats()
                    data["cache_stats"] = cache_stats
                except Exception as e:
                    logger.warning(f"Failed to get cache stats: {e}")

        # Add message counts
        data["message_count"] = getattr(bot, 'message_count', 0)
        data["response_count"] = getattr(bot, 'response_count', 0)
        data["audio_count"] = getattr(bot, 'audio_count', 0)

        return data

    except Exception as e:
        logger.error(f"Dashboard data error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health-check", response_model=Dict[str, Any])
async def monitoring_health_check() -> Dict[str, Any]:
    """
    Quick health check for monitoring systems.
    Returns minimal data for fast polling.
    """
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot:
            return {
                "status": "no_bot",
                "healthy": False,
                "timestamp": datetime.now().isoformat()
            }

        # Quick checks
        connected = False
        if hasattr(bot, 'twitch_client') and bot.twitch_client:
            connected = bot.twitch_client.is_connected()

        service_count = 0
        if hasattr(bot, 'service_registry') and bot.service_registry:
            stats = bot.service_registry.get_stats()
            service_count = len(stats.get('services', {}))

        return {
            "status": "running",
            "healthy": connected and service_count > 0,
            "connected": connected,
            "services": service_count,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Health check error: {e}")
        return {
            "status": "error",
            "healthy": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

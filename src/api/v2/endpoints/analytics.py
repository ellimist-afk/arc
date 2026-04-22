"""
Analytics Endpoint - V2 API
PRD: <300 lines, performance metrics
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/metrics", response_model=Dict[str, Any])
async def get_metrics() -> Dict[str, Any]:
    """Get current performance metrics."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot:
            raise HTTPException(status_code=503, detail="Bot not initialized")

        metrics = {}

        # Get metrics from metrics collector
        if hasattr(bot, 'metrics_collector') and bot.metrics_collector:
            metrics = bot.metrics_collector.get_dashboard_data()
        else:
            # Fallback to basic metrics
            metrics = {
                "messages": {
                    "total": getattr(bot, 'message_count', 0),
                    "responses": getattr(bot, 'response_count', 0)
                },
                "performance": {}
            }

            if hasattr(bot, 'response_times') and bot.response_times:
                times = bot.response_times
                metrics["performance"] = {
                    "avg_response_ms": sum(times) / len(times) if times else 0,
                    "count": len(times)
                }

        return {
            "status": "ok",
            "metrics": metrics
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Metrics error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard", response_model=Dict[str, Any])
async def get_dashboard_data() -> Dict[str, Any]:
    """Get all data needed for monitoring dashboard."""
    try:
        from src.api.app import app
        from datetime import datetime
        bot = getattr(app.state, 'bot', None)

        data = {
            "health": "unknown",
            "uptime": "0s",
            "metrics": {},
            "services": {},
            "cache_stats": {},
            "timestamp": datetime.now().isoformat()
        }

        if not bot:
            data["health"] = "no_bot"
            return data

        data["health"] = "healthy"

        # Uptime
        if hasattr(bot, 'start_time'):
            uptime_seconds = (datetime.now() - bot.start_time).total_seconds()
            data["uptime"] = f"{int(uptime_seconds)}s"

        # Metrics
        if hasattr(bot, 'metrics_collector') and bot.metrics_collector:
            data["metrics"] = bot.metrics_collector.get_dashboard_data()

        # Services
        if hasattr(bot, 'service_registry') and bot.service_registry:
            data["services"] = bot.service_registry.get_stats()

        # Cache stats
        if hasattr(bot, 'audio_queue') and bot.audio_queue:
            if hasattr(bot.audio_queue, 'persistent_cache'):
                data["cache_stats"] = bot.audio_queue.persistent_cache.get_stats()

        return data

    except Exception as e:
        logger.error(f"Dashboard data error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

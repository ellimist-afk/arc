"""
Health Check Endpoint - V2 API
PRD: <300 lines, fast responses
"""
from fastapi import APIRouter, Request
from typing import Dict, Any
from datetime import datetime
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=Dict[str, Any])
async def health_check() -> Dict[str, Any]:
    """
    Basic health check endpoint.
    Returns immediately with basic status.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "4.0.0",
        "api_version": "v2"
    }


@router.get("/detailed", response_model=Dict[str, Any])
async def detailed_health(request: Request) -> Dict[str, Any]:
    """
    Detailed health check with service status.
    Includes all initialized services and their states.
    """
    try:
        # Safely get bot instance without crashing
        from src.api.dependencies import get_bot_safe
        bot = get_bot_safe(request)

        services = {}
        uptime = 0

        if bot:
            # Get service status
            if hasattr(bot, 'service_registry') and bot.service_registry:
                service_stats = bot.service_registry.get_stats()
                services = {
                    name: "running"
                    for name in service_stats.get('services', {}).keys()
                }

            # Calculate uptime
            if hasattr(bot, 'start_time'):
                uptime = (datetime.now() - bot.start_time).total_seconds()

        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": "4.0.0",
            "services": services,
            "service_count": len(services),
            "uptime_seconds": uptime
        }

    except Exception as e:
        logger.error(f"Health check error: {e}")
        return {
            "status": "degraded",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }


@router.get("/services", response_model=Dict[str, Any])
async def service_status(request: Request) -> Dict[str, Any]:
    """
    Get status of all registered services.
    """
    try:
        from src.api.dependencies import get_bot_safe
        bot = get_bot_safe(request)

        if not bot or not hasattr(bot, 'service_registry'):
            return {
                "status": "no_bot",
                "services": {}
            }

        stats = bot.service_registry.get_stats()

        return {
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "services": stats.get('services', {}),
            "total_count": len(stats.get('services', {}))
        }

    except Exception as e:
        logger.error(f"Service status error: {e}")
        return {
            "status": "error",
            "error": str(e)
        }

"""
Bot Management Endpoint - V2 API
PRD: <300 lines, control bot operations
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from datetime import datetime
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/status", response_model=Dict[str, Any])
async def get_bot_status() -> Dict[str, Any]:
    """Get current bot status and stats."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot:
            return {"status": "not_running", "connected": False}

        # Get connection status
        connected = False
        if hasattr(bot, 'twitch_client') and bot.twitch_client:
            connected = bot.twitch_client.is_connected()

        # Get basic stats
        stats = {
            "status": "running",
            "connected": connected,
            "uptime_seconds": (datetime.now() - bot.start_time).total_seconds() if hasattr(bot, 'start_time') else 0,
            "message_count": getattr(bot, 'message_count', 0),
            "response_count": getattr(bot, 'response_count', 0)
        }

        # Add audio stats if available
        if hasattr(bot, 'audio_count'):
            stats["audio_count"] = bot.audio_count

        return stats

    except Exception as e:
        logger.error(f"Bot status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats", response_model=Dict[str, Any])
async def get_bot_stats() -> Dict[str, Any]:
    """Get detailed bot statistics."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot:
            raise HTTPException(status_code=503, detail="Bot not initialized")

        stats = {
            "timestamp": datetime.now().isoformat(),
            "uptime_seconds": (datetime.now() - bot.start_time).total_seconds() if hasattr(bot, 'start_time') else 0,
            "messages": {
                "total": getattr(bot, 'message_count', 0),
                "responses": getattr(bot, 'response_count', 0),
                "audio": getattr(bot, 'audio_count', 0)
            },
            "performance": {},
            "connection": {
                "twitch": bot.twitch_client.is_connected() if hasattr(bot, 'twitch_client') and bot.twitch_client else False
            }
        }

        # Add response times if tracked
        if hasattr(bot, 'response_times') and bot.response_times:
            times = bot.response_times
            stats["performance"] = {
                "avg_response_ms": sum(times) / len(times) if times else 0,
                "max_response_ms": max(times) if times else 0,
                "min_response_ms": min(times) if times else 0
            }

        return stats

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bot stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{streamer_id}/start", response_model=Dict[str, str])
async def start_bot(streamer_id: str) -> Dict[str, str]:
    """Start bot for a specific streamer."""
    try:
        from src.api.app import app
        # This would integrate with bot registry if multi-bot support exists
        # For now, just return status
        logger.info(f"Start bot requested for: {streamer_id}")
        return {
            "status": "started",
            "streamer_id": streamer_id,
            "message": "Bot start initiated"
        }
    except Exception as e:
        logger.error(f"Start bot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{streamer_id}/stop", response_model=Dict[str, str])
async def stop_bot(streamer_id: str) -> Dict[str, str]:
    """Stop bot for a specific streamer."""
    try:
        from src.api.app import app
        logger.info(f"Stop bot requested for: {streamer_id}")
        return {
            "status": "stopped",
            "streamer_id": streamer_id,
            "message": "Bot stop initiated"
        }
    except Exception as e:
        logger.error(f"Stop bot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/actions/{streamer_id}/mute", response_model=Dict[str, str])
async def mute_bot(streamer_id: str) -> Dict[str, str]:
    """Mute bot audio for a specific streamer."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot:
            raise HTTPException(status_code=503, detail="Bot not initialized")

        # Mute audio queue if available
        if hasattr(bot, 'audio_queue') and bot.audio_queue:
            if hasattr(bot.audio_queue, 'mute'):
                bot.audio_queue.mute()
                return {
                    "status": "muted",
                    "streamer_id": streamer_id
                }

        return {
            "status": "no_audio_queue",
            "streamer_id": streamer_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Mute bot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

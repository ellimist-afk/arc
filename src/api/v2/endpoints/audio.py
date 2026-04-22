"""
Audio/TTS Management Endpoint - V2 API
PRD: <300 lines, TTS cache stats
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/stats", response_model=Dict[str, Any])
async def get_audio_stats() -> Dict[str, Any]:
    """Get audio queue and TTS cache statistics."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot or not hasattr(bot, 'audio_queue'):
            raise HTTPException(status_code=503, detail="Audio queue not initialized")

        stats = {}

        # Get audio queue stats
        if hasattr(bot.audio_queue, 'get_stats'):
            queue_stats = await bot.audio_queue.get_stats()
            stats["queue"] = queue_stats

        # Get TTS cache stats
        if hasattr(bot.audio_queue, 'persistent_cache'):
            cache_stats = bot.audio_queue.persistent_cache.get_stats()
            stats["cache"] = cache_stats

        return {
            "status": "ok",
            "stats": stats
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audio stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/queue", response_model=Dict[str, Any])
async def get_queue_status() -> Dict[str, Any]:
    """Get current audio queue status."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot or not hasattr(bot, 'audio_queue'):
            raise HTTPException(status_code=503, detail="Audio queue not initialized")

        # Get queue size and status
        queue_size = 0
        is_playing = False

        if hasattr(bot.audio_queue, 'queue'):
            queue_size = bot.audio_queue.queue.qsize()

        if hasattr(bot.audio_queue, 'is_playing'):
            is_playing = bot.audio_queue.is_playing

        return {
            "queue_size": queue_size,
            "is_playing": is_playing,
            "status": "active" if queue_size > 0 or is_playing else "idle"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Queue status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

"""
Memory Management Endpoint - V2 API
PRD: <300 lines, memory queries
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/stats", response_model=Dict[str, Any])
async def get_memory_stats() -> Dict[str, Any]:
    """Get memory system statistics."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot or not hasattr(bot, 'memory_system'):
            raise HTTPException(status_code=503, detail="Memory system not initialized")

        # ResilientMemorySystem.get_stats() is sync
        stats = bot.memory_system.get_stats()

        return {
            "status": "ok",
            "stats": stats
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Memory stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/{username}", response_model=Dict[str, Any])
async def get_user_memory(username: str) -> Dict[str, Any]:
    """Get memory for a specific user."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot or not hasattr(bot, 'memory_system'):
            raise HTTPException(status_code=503, detail="Memory system not initialized")

        context = await bot.memory_system.get_user_context(username)
        stats = await bot.memory_system.get_user_stats(username)

        return {
            "username": username,
            "memory": {
                "context": context or {},
                "stats": stats or {}
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get user memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

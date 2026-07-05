"""
Personality Management Endpoint - V2 API
PRD: <300 lines, personality control
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from pydantic import BaseModel
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


class PersonalitySwitch(BaseModel):
    """Model for personality switch requests."""
    personality: str


@router.get("/current", response_model=Dict[str, Any])
async def get_current_personality() -> Dict[str, Any]:
    """Get currently active personality."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot or not hasattr(bot, 'personality_engine'):
            raise HTTPException(status_code=503, detail="Personality engine not initialized")

        current = "unknown"
        preset = getattr(bot.personality_engine, 'current_preset', None)
        if preset is not None:
            current = getattr(preset, 'value', str(preset))

        return {
            "current": current,
            "status": "ok"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get personality error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/switch", response_model=Dict[str, str])
async def switch_personality(switch: PersonalitySwitch) -> Dict[str, str]:
    """Switch to a different personality."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot or not hasattr(bot, 'personality_engine'):
            raise HTTPException(status_code=503, detail="Personality engine not initialized")

        success = await bot.personality_engine.switch_personality_by_name(switch.personality)
        if not success:
            raise HTTPException(status_code=404, detail=f"Unknown personality: {switch.personality}")

        return {
            "status": "switched",
            "personality": switch.personality
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Switch personality error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list", response_model=Dict[str, Any])
async def list_personalities() -> Dict[str, Any]:
    """List all available personalities."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot or not hasattr(bot, 'personality_engine'):
            raise HTTPException(status_code=503, detail="Personality engine not initialized")

        personalities = list(getattr(bot.personality_engine, 'all_personalities', {}).keys())

        return {
            "personalities": personalities,
            "count": len(personalities)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List personalities error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

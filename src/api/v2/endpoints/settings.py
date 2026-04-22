"""
Settings Management Endpoint - V2 API
PRD: <300 lines, settings CRUD
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from pydantic import BaseModel
import logging
import json
import os

router = APIRouter()
logger = logging.getLogger(__name__)


class SettingsUpdate(BaseModel):
    """Model for settings updates."""
    key: str
    value: Any


@router.get("", response_model=Dict[str, Any])
async def get_settings() -> Dict[str, Any]:
    """Get all bot settings."""
    try:
        settings_path = "bot_settings.json"
        if not os.path.exists(settings_path):
            return {"settings": {}}

        with open(settings_path, 'r') as f:
            settings = json.load(f)

        return {"settings": settings}

    except Exception as e:
        logger.error(f"Get settings error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=Dict[str, str])
async def update_settings(update: SettingsUpdate) -> Dict[str, str]:
    """Update a setting value."""
    try:
        settings_path = "bot_settings.json"

        # Load current settings
        settings = {}
        if os.path.exists(settings_path):
            with open(settings_path, 'r') as f:
                settings = json.load(f)

        # Update the value
        settings[update.key] = update.value

        # Save back
        with open(settings_path, 'w') as f:
            json.dump(settings, f, indent=2)

        return {"status": "updated", "key": update.key}

    except Exception as e:
        logger.error(f"Update settings error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reload", response_model=Dict[str, str])
async def reload_settings() -> Dict[str, str]:
    """Reload settings in the running bot."""
    try:
        from src.api.app import app
        bot = getattr(app.state, 'bot', None)

        if not bot:
            raise HTTPException(status_code=503, detail="Bot not initialized")

        # Reload settings if bot has the method
        if hasattr(bot, 'reload_settings'):
            await bot.reload_settings()
            return {"status": "reloaded"}

        return {"status": "no_reload_method"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reload settings error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

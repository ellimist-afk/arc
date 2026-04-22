"""
API Dependencies - Safe bot access without circular imports
"""
from typing import Optional
from fastapi import Request
import logging

logger = logging.getLogger(__name__)

_bot_instance = None


def set_bot_instance(bot):
    """Set the bot instance for API access."""
    global _bot_instance
    _bot_instance = bot


def get_bot_safe(request: Request = None):
    """
    Safely get bot instance without crashing.
    Returns None if bot not available.
    """
    # Try global instance first
    if _bot_instance:
        return _bot_instance

    # Try request.app.state
    if request and hasattr(request, 'app'):
        return getattr(request.app.state, 'bot', None)

    return None


def require_bot(request: Request):
    """
    Dependency that returns bot or raises 503.
    Use with Depends() for endpoints that need a running bot.
    """
    bot = get_bot_safe(request)
    if not bot:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="Bot not initialized - start the bot first"
        )
    return bot


def get_bot_optional():
    """
    Returns bot instance or None.
    For endpoints that can work without a bot.
    """
    return _bot_instance

"""
V2 API Endpoints - Export all endpoint routers
"""
from . import health, bot, settings, memory, audio, analytics, personality, monitoring

__all__ = [
    "health",
    "bot",
    "settings",
    "memory",
    "audio",
    "analytics",
    "personality",
    "monitoring"
]
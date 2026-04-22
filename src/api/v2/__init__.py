"""
V2 API Router - Main entry point for all V2 endpoints
"""
from fastapi import APIRouter

# Create main v2 router
v2_router = APIRouter(prefix="/v2")

# Import and include all endpoint routers
from .endpoints import health, bot, settings, memory, audio, analytics, personality, monitoring

v2_router.include_router(health.router, prefix="/health", tags=["Health"])
v2_router.include_router(bot.router, prefix="/bot", tags=["Bot Management"])
v2_router.include_router(settings.router, prefix="/settings", tags=["Settings"])
v2_router.include_router(memory.router, prefix="/memory", tags=["Memory"])
v2_router.include_router(audio.router, prefix="/audio", tags=["Audio"])
v2_router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
v2_router.include_router(personality.router, prefix="/personality", tags=["Personality"])
v2_router.include_router(monitoring.router, prefix="/monitoring", tags=["Monitoring"])

__all__ = ["v2_router"]
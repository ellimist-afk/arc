"""
Rate Limiter Middleware - Stub Implementation
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from dataclasses import dataclass
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Rate limit configuration."""
    requests_per_second: float = 10.0
    burst_size: int = 20
    window_size: int = 60
    authenticated_multiplier: float = 2.0
    premium_multiplier: float = 5.0
    endpoint_limits: Optional[Dict[str, "RateLimitConfig"]] = None


class EnhancedRateLimitMiddleware(BaseHTTPMiddleware):
    """Stub rate limiter - no actual rate limiting applied."""

    def __init__(self, app, config: RateLimitConfig = None):
        super().__init__(app)
        self.config = config or RateLimitConfig()
        logger.info("Rate limiter initialized (stub mode - no limits enforced)")

    async def dispatch(self, request: Request, call_next):
        # Stub: Just pass through without rate limiting
        return await call_next(request)

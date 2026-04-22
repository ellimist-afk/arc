"""
Response Optimization Middleware - Stub Implementation
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


class ResponseOptimizationMiddleware(BaseHTTPMiddleware):
    """Stub response optimization - passes through without optimization."""

    def __init__(self, app, config: Dict[str, Any] = None):
        super().__init__(app)
        self.config = config or {}
        logger.info("Response optimization initialized (stub mode)")

    async def dispatch(self, request: Request, call_next):
        # Stub: Just pass through without optimization
        response = await call_next(request)
        return response

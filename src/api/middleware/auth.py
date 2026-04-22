"""
Authentication Middleware - Stub Implementation
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import logging

logger = logging.getLogger(__name__)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Stub authentication middleware - no auth required."""

    async def dispatch(self, request: Request, call_next):
        # Stub: Just pass through without authentication
        return await call_next(request)

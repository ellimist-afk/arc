"""
Content Security Policy and Security Headers Middleware
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import secrets
import logging

logger = logging.getLogger(__name__)

# Store nonces per request
_nonces = {}

def get_csp_nonce(request: Request = None) -> str:
    """Get or generate CSP nonce for current request."""
    if request and hasattr(request.state, "csp_nonce"):
        return request.state.csp_nonce
    return secrets.token_urlsafe(16)


class CSPMiddleware(BaseHTTPMiddleware):
    """Content Security Policy middleware."""

    def __init__(self, app, debug_mode: bool = False):
        super().__init__(app)
        self.debug_mode = debug_mode

    async def dispatch(self, request: Request, call_next):
        # Generate nonce for this request
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        response = await call_next(request)

        # Set CSP header
        if self.debug_mode:
            # More permissive in debug mode
            csp = f"default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' 'nonce-{nonce}'"
        else:
            csp = f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'"

        response.headers["Content-Security-Policy"] = csp
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Security headers middleware."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        return response

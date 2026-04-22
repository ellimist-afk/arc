"""
API Middleware - Stub implementations for testing
"""
from .csp import CSPMiddleware, SecurityHeadersMiddleware, get_csp_nonce
from .error_handler import ErrorHandlerMiddleware
from .rate_limiter import EnhancedRateLimitMiddleware, RateLimitConfig
from .response_optimization import ResponseOptimizationMiddleware
from .auth import AuthenticationMiddleware

__all__ = [
    "CSPMiddleware",
    "SecurityHeadersMiddleware",
    "get_csp_nonce",
    "ErrorHandlerMiddleware",
    "EnhancedRateLimitMiddleware",
    "RateLimitConfig",
    "ResponseOptimizationMiddleware",
    "AuthenticationMiddleware"
]

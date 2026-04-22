"""
Error Handler Middleware
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
import logging
import traceback

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Global error handler middleware."""

    def __init__(self, app, debug: bool = False):
        super().__init__(app)
        self.debug = debug

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as e:
            logger.error(f"Unhandled error in {request.method} {request.url.path}: {e}")

            if self.debug:
                logger.error(traceback.format_exc())
                return JSONResponse(
                    {
                        "error": str(e),
                        "type": type(e).__name__,
                        "traceback": traceback.format_exc()
                    },
                    status_code=500
                )
            else:
                return JSONResponse(
                    {"error": "Internal server error"},
                    status_code=500
                )

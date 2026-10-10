"""
Authentication Middleware

There is no login yet. Until there is, the API listens on loopback only and
this middleware refuses state-changing requests a browser sends from any
other origin, so a web page the streamer has open cannot mute the bot or
rewrite its settings through localhost.
"""
from typing import Iterable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Rejects cross-origin writes. Requests without an Origin header
    (curl, scripts, same-origin GETs) pass: they are not a browser acting
    for another site."""

    def __init__(self, app, allowed_origins: Optional[Iterable[str]] = None):
        super().__init__(app)
        self.allowed_origins = set(allowed_origins or ())

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.method not in SAFE_METHODS
            and origin
            and origin not in self.allowed_origins
        ):
            logger.warning(f"Refused {request.method} {request.url.path} from origin {origin}")
            return JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
        return await call_next(request)

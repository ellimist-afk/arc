"""
Error Handling Utilities
"""
from starlette.responses import JSONResponse
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Validation error exception."""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        self.details = {}
        super().__init__(f"{field}: {message}")


def create_error_response(
    error: Exception,
    request_id: Optional[str] = None,
    include_details: bool = False
) -> JSONResponse:
    """Create standardized error response."""
    response_data = {
        "error": str(error),
        "type": type(error).__name__
    }

    if request_id:
        response_data["request_id"] = request_id

    if include_details and hasattr(error, "details"):
        response_data["details"] = error.details

    # Determine status code
    if isinstance(error, ValidationError):
        status_code = 400
    elif isinstance(error, PermissionError):
        status_code = 403
    elif isinstance(error, FileNotFoundError):
        status_code = 404
    else:
        status_code = 500

    return JSONResponse(response_data, status_code=status_code)

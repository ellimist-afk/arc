"""
Logging utilities for API
"""
import logging


class APILogger:
    """API logger wrapper."""

    def __init__(self):
        self.logger = logging.getLogger("src.api")

    def validation_error(self, field: str, errors: list, message: str):
        """Log validation error."""
        self.logger.warning(f"Validation error in {field}: {message}")

    def request_error(self, method: str, path: str, error: Exception):
        """Log request error."""
        self.logger.error(f"Request error {method} {path}: {error}")


api_logger = APILogger()

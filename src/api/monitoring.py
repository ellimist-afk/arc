"""
API Monitoring - Stub Implementation
"""
import logging

logger = logging.getLogger(__name__)


class APIMonitor:
    """Stub API monitor."""

    async def start(self):
        """Start monitoring."""
        pass

    async def stop(self):
        """Stop monitoring."""
        pass


_monitor = APIMonitor()


def get_api_monitor() -> APIMonitor:
    """Get API monitor instance."""
    return _monitor

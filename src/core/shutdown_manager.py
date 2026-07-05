"""
Shutdown manager module stub - to be implemented
"""

import asyncio
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class ShutdownPriority(Enum):
    """Shutdown priority levels"""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    MEDIUM = 2  # Alias for NORMAL
    LOW = 3


class ShutdownManager:
    """Manages graceful shutdown"""

    def __init__(self):
        self.shutdown_handlers = []

    def register(self, name: str, shutdown_func, priority: ShutdownPriority, timeout: float):
        """Register a shutdown handler"""
        self.shutdown_handlers.append({
            'name': name,
            'func': shutdown_func,
            'priority': priority,
            'timeout': timeout
        })

    def install_signal_handlers(self):
        """Install signal handlers for graceful shutdown"""
        logger.debug("Signal handlers installation (stub)")
        pass

    async def shutdown(self, timeout: float = 30.0, reason: str = "unknown"):
        """Execute shutdown sequence"""
        logger.info(f"Shutdown manager executing (reason: {reason}, timeout: {timeout}s)")
        # Sort by priority and execute
        sorted_handlers = sorted(self.shutdown_handlers, key=lambda h: h['priority'].value)
        for handler in sorted_handlers:
            try:
                if handler['func']:
                    await handler['func']()
            except Exception as e:
                logger.error(f"Error during shutdown of {handler['name']}: {e}")


_shutdown_manager = None


def get_shutdown_manager():
    """Get singleton shutdown manager"""
    global _shutdown_manager
    if _shutdown_manager is None:
        _shutdown_manager = ShutdownManager()
    return _shutdown_manager
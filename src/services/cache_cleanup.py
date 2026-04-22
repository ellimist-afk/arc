"""
Cache cleanup service stub - to be implemented
"""

import asyncio
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class CleanupStrategy(Enum):
    """Cleanup strategies"""
    LRU = "lru"
    TTL = "ttl"
    SIZE = "size"


async def start_cache_cleanup():
    """Start cache cleanup service"""
    logger.info("Cache cleanup service started (stub)")
    pass


async def stop_cache_cleanup():
    """Stop cache cleanup service"""
    logger.info("Cache cleanup service stopped (stub)")
    pass
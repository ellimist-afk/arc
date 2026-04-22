"""
Optimized context builder for sub-100ms performance.
Multi-level caching and parallel fetching for fast context generation.
"""

import asyncio
import time
from typing import Dict, Any, Optional, List, Tuple
from collections import OrderedDict
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class ContextTemplate:
    """Pre-built context template for common scenarios."""
    scenario: str
    base_context: Dict[str, Any]
    required_fields: List[str]
    ttl: float = 300  # 5 minutes


class LRUCache:
    """Simple LRU cache implementation."""

    def __init__(self, maxsize: int = 100):
        self.cache: OrderedDict = OrderedDict()
        self.maxsize = maxsize
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            self.hits += 1
            self.cache.move_to_end(key)
            return self.cache[key]
        self.misses += 1
        return None

    def put(self, key: str, value: Any):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.maxsize:
            self.cache.popitem(last=False)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class OptimizedContextBuilder:
    """Multi-level caching context builder for sub-100ms performance."""

    def __init__(self, memory_system):
        self.memory = memory_system

        # Multi-level cache system
        self.l1_cache: Dict[str, Tuple[Dict, float]] = {}  # Hot cache for active conversations
        self.l2_cache = LRUCache(maxsize=100)  # Recent contexts

        # Pre-built templates for common scenarios
        self.templates: Dict[str, ContextTemplate] = {
            "first_message": ContextTemplate(
                scenario="first_message",
                base_context={
                    "interaction_type": "initial",
                    "context_depth": "minimal",
                    "personality_boost": 1.2
                },
                required_fields=["viewer", "channel"]
            ),
            "returning_viewer": ContextTemplate(
                scenario="returning_viewer",
                base_context={
                    "interaction_type": "continuation",
                    "context_depth": "full",
                    "personality_boost": 1.0
                },
                required_fields=["viewer", "channel", "history"]
            ),
            "mention": ContextTemplate(
                scenario="mention",
                base_context={
                    "interaction_type": "direct",
                    "context_depth": "focused",
                    "personality_boost": 1.1,
                    "priority": "high"
                },
                required_fields=["viewer", "channel", "message"]
            ),
            "voice_input": ContextTemplate(
                scenario="voice_input",
                base_context={
                    "interaction_type": "voice",
                    "context_depth": "immediate",
                    "personality_boost": 1.0,
                    "priority": "highest"
                },
                required_fields=["speaker", "channel", "transcription"]
            )
        }

        # Cache configuration
        self.l1_ttl = 60  # 1 minute for hot cache
        self.l2_ttl = 300  # 5 minutes for warm cache

        # Performance tracking
        self.build_times: List[float] = []
        self.max_build_times = 100

        logger.info("OptimizedContextBuilder initialized with multi-level caching")

    def _get_cache_key(self, viewer: str, channel: str, scenario: str = "") -> str:
        """Generate cache key for context."""
        return f"{channel}:{viewer}:{scenario}"

    def _check_l1_cache(self, key: str) -> Optional[Dict]:
        """Check L1 (hot) cache with TTL."""
        if key in self.l1_cache:
            context, timestamp = self.l1_cache[key]
            if time.time() - timestamp < self.l1_ttl:
                logger.debug(f"L1 cache hit for {key}")
                return context
            else:
                del self.l1_cache[key]
        return None

    def _check_l2_cache(self, key: str) -> Optional[Dict]:
        """Check L2 (warm) cache."""
        context = self.l2_cache.get(key)
        if context:
            logger.debug(f"L2 cache hit for {key}")
            # Promote to L1
            self.l1_cache[key] = (context, time.time())
        return context

    async def _parallel_fetch(self, viewer: str, channel: str) -> Dict[str, Any]:
        """Fetch all context data in parallel."""
        start = time.time()

        async def fetch_with_timeout(coro, name: str, timeout: float = 0.05):
            """Fetch with timeout and error handling."""
            try:
                return await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                logger.debug(f"Timeout fetching {name}")
                return None
            except Exception as e:
                logger.debug(f"Error fetching {name}: {e}")
                return None

        # Launch all fetches in parallel
        tasks = {
            "viewer_data": fetch_with_timeout(
                self.memory.get_viewer_context(viewer, channel),
                "viewer_data"
            ),
            "recent_messages": fetch_with_timeout(
                self.memory.get_recent_messages(channel, limit=10),
                "recent_messages"
            ),
            "channel_context": fetch_with_timeout(
                self.memory.get_channel_context(channel),
                "channel_context"
            ),
            "interaction_history": fetch_with_timeout(
                self.memory.get_interaction_history(viewer, channel, limit=5),
                "interaction_history"
            )
        }

        results = await asyncio.gather(*tasks.values())

        fetch_time = time.time() - start
        logger.debug(f"Parallel fetch completed in {fetch_time:.3f}s")

        return dict(zip(tasks.keys(), results))

    async def build_context(
        self,
        viewer: str,
        channel: str,
        message: Optional[str] = None,
        scenario: str = "general",
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Build context with multi-level caching and parallel fetching.
        Target: <100ms for cached, <200ms for uncached.
        """
        start = time.time()

        # Generate cache key
        cache_key = self._get_cache_key(viewer, channel, scenario)

        # Check caches unless forced refresh
        if not force_refresh:
            # L1 cache check (fastest)
            context = self._check_l1_cache(cache_key)
            if context:
                build_time = (time.time() - start) * 1000
                self._track_build_time(build_time)
                logger.debug(f"Context built from L1 in {build_time:.1f}ms")
                return context

            # L2 cache check (fast)
            context = self._check_l2_cache(cache_key)
            if context:
                build_time = (time.time() - start) * 1000
                self._track_build_time(build_time)
                logger.debug(f"Context built from L2 in {build_time:.1f}ms")
                return context

        # Use template if available
        template = self.templates.get(scenario)
        if template:
            context = template.base_context.copy()
        else:
            context = {}

        # Parallel fetch all data
        try:
            data = await self._parallel_fetch(viewer, channel)

            # Build context from fetched data
            context.update({
                "viewer": viewer,
                "channel": channel,
                "message": message,
                "scenario": scenario,
                "timestamp": time.time(),
                "viewer_data": data.get("viewer_data", {}),
                "recent_context": self._summarize_messages(data.get("recent_messages", [])),
                "channel_info": data.get("channel_context", {}),
                "history_summary": self._summarize_history(data.get("interaction_history", [])),
                "is_returning": bool(data.get("interaction_history")),
                "engagement_level": self._calculate_engagement(data)
            })

        except Exception as e:
            logger.error(f"Error building context: {e}")
            # Return minimal context on error
            context.update({
                "viewer": viewer,
                "channel": channel,
                "message": message,
                "scenario": scenario,
                "error": str(e)
            })

        # Update caches
        self.l1_cache[cache_key] = (context, time.time())
        self.l2_cache.put(cache_key, context)

        # Track performance
        build_time = (time.time() - start) * 1000
        self._track_build_time(build_time)

        if build_time > 100:
            logger.warning(f"Context build exceeded 100ms: {build_time:.1f}ms")
        else:
            logger.debug(f"Context built in {build_time:.1f}ms")

        return context

    def _summarize_messages(self, messages: List[Dict]) -> str:
        """Quickly summarize recent messages for context."""
        if not messages:
            return "No recent activity"

        # Take last 5 messages
        recent = messages[-5:]
        summary_parts = []

        for msg in recent:
            viewer = msg.get("viewer", "Unknown")
            text = msg.get("message", "")[:50]  # Truncate long messages
            summary_parts.append(f"{viewer}: {text}")

        return " | ".join(summary_parts)

    def _summarize_history(self, history: List[Dict]) -> str:
        """Quickly summarize interaction history."""
        if not history:
            return "First interaction"

        count = len(history)
        if count == 1:
            return "Seen once before"
        elif count < 5:
            return f"Regular viewer ({count} interactions)"
        else:
            return f"Frequent viewer ({count}+ interactions)"

    def _calculate_engagement(self, data: Dict) -> str:
        """Calculate viewer engagement level."""
        history = data.get("interaction_history", [])

        if not history:
            return "new"
        elif len(history) < 3:
            return "casual"
        elif len(history) < 10:
            return "regular"
        else:
            return "loyal"

    def _track_build_time(self, build_time_ms: float):
        """Track build times for performance monitoring."""
        self.build_times.append(build_time_ms)
        if len(self.build_times) > self.max_build_times:
            self.build_times.pop(0)

    def invalidate_cache(self, viewer: Optional[str] = None, channel: Optional[str] = None):
        """Invalidate cache entries."""
        if viewer and channel:
            # Invalidate specific viewer
            pattern = f"{channel}:{viewer}:"
            keys_to_remove = [k for k in self.l1_cache if k.startswith(pattern)]
            for key in keys_to_remove:
                del self.l1_cache[key]
            logger.debug(f"Invalidated cache for {viewer} in {channel}")
        elif channel:
            # Invalidate entire channel
            pattern = f"{channel}:"
            keys_to_remove = [k for k in self.l1_cache if k.startswith(pattern)]
            for key in keys_to_remove:
                del self.l1_cache[key]
            logger.debug(f"Invalidated cache for channel {channel}")
        else:
            # Clear all caches
            self.l1_cache.clear()
            self.l2_cache = LRUCache(maxsize=100)
            logger.info("Cleared all context caches")

    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        avg_build_time = sum(self.build_times) / len(self.build_times) if self.build_times else 0

        return {
            "l1_cache_size": len(self.l1_cache),
            "l2_cache_hit_rate": self.l2_cache.hit_rate,
            "avg_build_time_ms": round(avg_build_time, 1),
            "max_build_time_ms": round(max(self.build_times), 1) if self.build_times else 0,
            "min_build_time_ms": round(min(self.build_times), 1) if self.build_times else 0,
            "cache_performance": "optimal" if avg_build_time < 100 else "degraded"
        }
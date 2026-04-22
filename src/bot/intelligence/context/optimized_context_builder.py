"""
OptimizedContextBuilder - PRD Required Component
Multi-level caching context builder for sub-100ms performance
Target: Reduce context building from ~200ms to <100ms
"""

import asyncio
import time
import hashlib
import json
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from collections import OrderedDict
import logging

logger = logging.getLogger(__name__)


class LRUCache:
    """Simple LRU cache implementation for L2 cache"""
    
    def __init__(self, maxsize: int = 100):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        
    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            # Move to end (most recently used)
            self.cache.move_to_end(key)
            return self.cache[key]
        return None
        
    def put(self, key: str, value: Any) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.maxsize:
            # Remove least recently used
            self.cache.popitem(last=False)
            
    def clear(self):
        self.cache.clear()


class OptimizedContextBuilder:
    """
    Multi-level caching context builder for sub-100ms performance
    Implements PRD requirements from section 2.3 and Technical Implementation Notes
    """
    
    def __init__(self, memory_system):
        """
        Initialize the optimized context builder
        
        Args:
            memory_system: SingleMemorySystem instance
        """
        self.memory = memory_system
        
        # L1 Cache: Hot cache for active conversations (5 min TTL)
        self.l1_cache = {}  # {cache_key: (context, timestamp)}
        self.l1_ttl = 300  # 5 minutes
        
        # L2 Cache: Recent contexts with LRU eviction
        self.l2_cache = LRUCache(maxsize=100)
        
        # Template Cache: Pre-built context templates for common scenarios
        self.template_cache = self._initialize_templates()
        
        # Performance metrics
        self.cache_hits = 0
        self.cache_misses = 0
        self.total_build_time = 0
        self.build_count = 0
        
    def _initialize_templates(self) -> Dict[str, Dict]:
        """Initialize pre-built context templates for common scenarios"""
        return {
            "first_time_viewer": {
                "viewer_history": [],
                "interaction_count": 0,
                "personality_hint": "Be welcoming and introduce yourself",
                "context_type": "new_viewer"
            },
            "regular_viewer": {
                "personality_hint": "Be familiar and friendly",
                "context_type": "regular"
            },
            "subscriber": {
                "personality_hint": "Show appreciation for their support",
                "context_type": "subscriber"
            },
            "raid": {
                "personality_hint": "Welcome the raiders enthusiastically",
                "context_type": "raid"
            },
            "voice_input": {
                "personality_hint": "Respond conversationally to voice",
                "context_type": "voice",
                "priority": "high"
            },
            "mention": {
                "personality_hint": "They mentioned you directly, give full attention",
                "context_type": "mention",
                "priority": "high"
            }
        }
        
    def _generate_cache_key(self, viewer: str, channel: str, context_type: str = "chat") -> str:
        """Generate a cache key for the context"""
        key_data = f"{channel}:{viewer}:{context_type}"
        return hashlib.md5(key_data.encode()).hexdigest()
        
    def _is_cache_valid(self, timestamp: datetime, ttl: int) -> bool:
        """Check if cached entry is still valid"""
        return (datetime.now() - timestamp).seconds < ttl
        
    async def build_context(
        self, 
        viewer: str, 
        channel: str,
        message: str = "",
        context_type: str = "chat",
        metadata: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Build context with multi-level caching for <100ms performance
        
        Args:
            viewer: Viewer username
            channel: Channel name
            message: Current message
            context_type: Type of context (chat, voice, mention, raid)
            metadata: Additional context metadata
            
        Returns:
            Context dictionary for response generation
        """
        start_time = time.perf_counter()
        
        try:
            # Generate cache key
            cache_key = self._generate_cache_key(viewer, channel, context_type)
            
            # Check L1 cache (hot cache)
            if cache_key in self.l1_cache:
                context, timestamp = self.l1_cache[cache_key]
                if self._is_cache_valid(timestamp, self.l1_ttl):
                    # Update with current message
                    context['current_message'] = message
                    self.cache_hits += 1
                    
                    elapsed = (time.perf_counter() - start_time) * 1000
                    logger.debug(f"L1 cache hit for {viewer}: {elapsed:.1f}ms")
                    return context
                else:
                    # Expired, remove from L1
                    del self.l1_cache[cache_key]
                    
            # Check L2 cache (LRU cache)
            cached_context = self.l2_cache.get(cache_key)
            if cached_context:
                # Refresh data if not too stale
                context = await self._refresh_context(cached_context, viewer, channel)
                context['current_message'] = message
                
                # Promote to L1
                self.l1_cache[cache_key] = (context, datetime.now())
                self.cache_hits += 1
                
                elapsed = (time.perf_counter() - start_time) * 1000
                logger.debug(f"L2 cache hit for {viewer}: {elapsed:.1f}ms")
                return context
                
            # Cache miss - build new context
            self.cache_misses += 1
            context = await self._build_fresh_context(viewer, channel, message, context_type, metadata)
            
            # Store in both caches
            self.l1_cache[cache_key] = (context, datetime.now())
            self.l2_cache.put(cache_key, context)
            
            elapsed = (time.perf_counter() - start_time) * 1000
            self.total_build_time += elapsed
            self.build_count += 1
            
            logger.debug(f"Fresh context build for {viewer}: {elapsed:.1f}ms")
            
            # Clean L1 cache periodically
            if len(self.l1_cache) > 20:
                self._cleanup_l1_cache()
                
            return context
            
        except Exception as e:
            logger.error(f"Error building context for {viewer}: {e}")
            # Return minimal context on error
            elapsed = (time.perf_counter() - start_time) * 1000
            logger.warning(f"Returning fallback context after {elapsed:.1f}ms")
            return self._get_fallback_context(viewer, channel, message, context_type)
            
    async def _build_fresh_context(
        self,
        viewer: str,
        channel: str,
        message: str,
        context_type: str,
        metadata: Optional[Dict]
    ) -> Dict[str, Any]:
        """
        Build fresh context with parallel data fetching
        All operations run concurrently for maximum speed
        """
        # Start with template if available
        template = self.template_cache.get(context_type, {}).copy()
        
        # Parallel fetch all context data
        tasks = [
            self._fetch_viewer_history(viewer),
            self._fetch_recent_messages(channel),
            self._fetch_viewer_stats(viewer),
            self._fetch_stream_context(channel)
        ]
        
        try:
            # Wait for all with timeout
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=0.08  # 80ms timeout for parallel fetches
            )
            
            viewer_history, recent_messages, viewer_stats, stream_context = results
            
            # Handle exceptions in results
            if isinstance(viewer_history, Exception):
                viewer_history = []
            if isinstance(recent_messages, Exception):
                recent_messages = []
            if isinstance(viewer_stats, Exception):
                viewer_stats = {}
            if isinstance(stream_context, Exception):
                stream_context = {}
                
        except asyncio.TimeoutError:
            logger.warning("Context fetch timeout - using partial data")
            viewer_history = []
            recent_messages = []
            viewer_stats = {}
            stream_context = {}
            
        # Build context dictionary
        context = {
            **template,  # Start with template
            'viewer': viewer,
            'channel': channel,
            'current_message': message,
            'viewer_history': viewer_history[-5:] if viewer_history else [],  # Last 5 messages
            'recent_messages': recent_messages[-10:] if recent_messages else [],  # Last 10 channel messages
            'interaction_count': viewer_stats.get('message_count', 0),
            'is_subscriber': viewer_stats.get('is_subscriber', False),
            'is_mod': viewer_stats.get('is_mod', False),
            'stream_context': stream_context,
            'context_type': context_type,
            'timestamp': datetime.now().isoformat()
        }
        
        # Add metadata if provided
        if metadata:
            context['metadata'] = metadata
            
        return context
        
    async def _fetch_viewer_history(self, viewer: str) -> List[Dict]:
        """Fetch viewer's message history"""
        try:
            if hasattr(self.memory, 'get_user_history'):
                # Use memory system's optimized method if available
                return await self.memory.get_user_history(viewer, limit=5)
            else:
                # Fallback to basic query
                return []
        except Exception as e:
            logger.debug(f"Error fetching viewer history: {e}")
            return []
            
    async def _fetch_recent_messages(self, channel: str) -> List[Dict]:
        """Fetch recent channel messages"""
        try:
            if hasattr(self.memory, 'get_recent_messages'):
                return await self.memory.get_recent_messages(channel, limit=10)
            else:
                return []
        except Exception as e:
            logger.debug(f"Error fetching recent messages: {e}")
            return []
            
    async def _fetch_viewer_stats(self, viewer: str) -> Dict:
        """Fetch viewer statistics"""
        try:
            if hasattr(self.memory, 'get_user_stats'):
                return await self.memory.get_user_stats(viewer)
            else:
                return {'message_count': 0}
        except Exception as e:
            logger.debug(f"Error fetching viewer stats: {e}")
            return {}
            
    async def _fetch_stream_context(self, channel: str) -> Dict:
        """Fetch current stream context"""
        try:
            # This would integrate with stream info if available
            return {
                'uptime': 'unknown',
                'game': 'unknown',
                'title': 'unknown'
            }
        except Exception as e:
            logger.debug(f"Error fetching stream context: {e}")
            return {}
            
    async def _refresh_context(self, cached_context: Dict, viewer: str, channel: str) -> Dict:
        """
        Refresh stale parts of cached context
        Only updates what's likely to have changed
        """
        try:
            # Only refresh recent messages and counts
            recent_messages = await self._fetch_recent_messages(channel)
            
            cached_context['recent_messages'] = recent_messages[-10:] if recent_messages else []
            cached_context['timestamp'] = datetime.now().isoformat()
            
            return cached_context
            
        except Exception as e:
            logger.debug(f"Error refreshing context: {e}")
            return cached_context
            
    def _get_fallback_context(self, viewer: str, channel: str, message: str, context_type: str) -> Dict:
        """Get minimal fallback context when building fails"""
        template = self.template_cache.get(context_type, {}).copy()
        
        return {
            **template,
            'viewer': viewer,
            'channel': channel,
            'current_message': message,
            'viewer_history': [],
            'recent_messages': [],
            'interaction_count': 0,
            'context_type': context_type,
            'timestamp': datetime.now().isoformat(),
            'is_fallback': True
        }
        
    def _cleanup_l1_cache(self):
        """Remove expired entries from L1 cache"""
        now = datetime.now()
        expired_keys = [
            key for key, (_, timestamp) in self.l1_cache.items()
            if (now - timestamp).seconds > self.l1_ttl
        ]
        
        for key in expired_keys:
            del self.l1_cache[key]
            
        if expired_keys:
            logger.debug(f"Cleaned {len(expired_keys)} expired L1 cache entries")
            
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache performance statistics"""
        hit_rate = (self.cache_hits / (self.cache_hits + self.cache_misses) * 100) if (self.cache_hits + self.cache_misses) > 0 else 0
        avg_build_time = (self.total_build_time / self.build_count) if self.build_count > 0 else 0
        
        return {
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses,
            'hit_rate': f"{hit_rate:.1f}%",
            'l1_size': len(self.l1_cache),
            'l2_size': len(self.l2_cache.cache),
            'avg_build_time_ms': f"{avg_build_time:.1f}",
            'total_builds': self.build_count
        }
        
    def clear_caches(self):
        """Clear all caches"""
        self.l1_cache.clear()
        self.l2_cache.clear()
        self.cache_hits = 0
        self.cache_misses = 0
        logger.info("All context caches cleared")
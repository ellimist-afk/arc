"""
SingleMemorySystem - The ONE TRUE memory system
Consolidates all memory operations with full backward compatibility
"""

import asyncio
import logging
import json
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timedelta
from dateutil import parser
import hashlib
from collections import deque
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text, select, and_, or_, desc
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

class SingleMemorySystem:
    """
    Unified memory system that consolidates all previous memory implementations
    Provides single write path and eliminates redundancy
    """
    
    def __init__(self, database_url: str, redis_url: str = None):
        """
        Initialize the SingleMemorySystem
        
        Args:
            database_url: PostgreSQL connection string
            redis_url: Redis connection string for caching
        """
        self.database_url = database_url
        self.redis_url = redis_url
        
        # Database components
        self.engine = None
        self.async_session = None
        
        # Redis cache
        self.redis_client = None
        self.cache_ttl = 3600  # 1 hour default TTL
        
        # In-memory buffers for performance
        self.recent_messages = deque(maxlen=100)
        self.recent_memories = deque(maxlen=50)
        self.user_context_cache: Dict[str, Dict] = {}
        
        # Performance tracking
        self.write_count = 0
        self.read_count = 0
        self.cache_hits = 0
        self.cache_misses = 0
        
        # Context optimization cache
        self.context_cache: Dict[str, Dict] = {}
        self.context_cache_ttl = 60  # 60 seconds
        
    async def initialize(self) -> None:
        """
        Initialize database and cache connections
        """
        try:
            # Initialize database
            logger.info("Initializing database connection...")
            self.engine = create_async_engine(
                self.database_url,
                pool_size=20,
                max_overflow=10,
                pool_pre_ping=True,
                echo=False
            )
            self.async_session = async_sessionmaker(
                self.engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            
            # Test database connection
            async with self.engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("Database connection established")
            
            # Initialize Redis if URL provided
            if self.redis_url:
                logger.info("Initializing Redis cache...")
                self.redis_client = await redis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True
                )
                await self.redis_client.ping()
                logger.info("Redis cache connection established")
                
        except Exception as e:
            logger.error(f"Failed to initialize memory system: {e}")
            raise
            
    @asynccontextmanager
    async def get_session(self):
        """Get a database session with proper cleanup"""
        async with self.async_session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
                
    async def store_message(self, message: Dict[str, Any]) -> None:
        """
        Store a chat message with single write path
        
        Args:
            message: Message data including text, user, timestamp
        """
        try:
            self.write_count += 1
            
            # Add to recent messages buffer
            self.recent_messages.append(message)
            
            # Store in database
            async with self.get_session() as session:
                await session.execute(
                    text("""
                        INSERT INTO chat_messages 
                        (user_id, username, message, timestamp, channel)
                        VALUES (:user_id, :username, :message, :timestamp, :channel)
                    """),
                    {
                        'user_id': message.get('user_id'),
                        'username': message.get('username'),
                        'message': message.get('text'),
                        'timestamp': self._parse_timestamp(message.get('timestamp', datetime.now())),
                        'channel': message.get('channel')
                    }
                )
                
            # Cache in Redis if available
            if self.redis_client:
                cache_key = f"msg:{message.get('user_id')}:{datetime.now().timestamp()}"
                await self.redis_client.setex(
                    cache_key,
                    self.cache_ttl,
                    json.dumps(message, default=str)
                )
                
            # Invalidate context cache for this user
            user_id = message.get('user_id')
            if user_id and user_id in self.context_cache:
                del self.context_cache[user_id]
                
        except Exception as e:
            logger.error(f"Failed to store message: {e}")
            
    async def store_memory(self, memory_item: Dict[str, Any]) -> None:
        """
        Store a memory item (personality trait, user preference, etc.)
        
        Args:
            memory_item: Memory data to store
        """
        try:
            self.write_count += 1
            
            # Add to recent memories buffer
            self.recent_memories.append(memory_item)
            
            # Generate memory ID
            memory_id = self._generate_memory_id(memory_item)
            
            # Store in database
            async with self.get_session() as session:
                await session.execute(
                    text("""
                        INSERT INTO memories 
                        (memory_id, user_id, type, content, metadata, timestamp)
                        VALUES (:memory_id, :user_id, :type, :content, :metadata, :timestamp)
                        ON CONFLICT (memory_id) DO UPDATE
                        SET content = EXCLUDED.content,
                            metadata = EXCLUDED.metadata,
                            timestamp = EXCLUDED.timestamp
                    """),
                    {
                        'memory_id': memory_id,
                        'user_id': memory_item.get('user_id'),
                        'type': memory_item.get('type', 'general'),
                        'content': json.dumps(memory_item.get('content', {})),
                        'metadata': json.dumps(memory_item.get('metadata', {})),
                        'timestamp': datetime.now()
                    }
                )
                
            # Cache in Redis
            if self.redis_client:
                cache_key = f"mem:{memory_id}"
                await self.redis_client.setex(
                    cache_key,
                    self.cache_ttl,
                    json.dumps(memory_item, default=str)
                )
                
        except Exception as e:
            logger.error(f"Failed to store memory: {e}")
            
    async def get_context_optimized(
        self,
        user_id: str,
        message_text: str = None,
        max_time_ms: int = 80
    ) -> Dict[str, Any]:
        """
        Get optimized context for a user within time constraint
        Implements <100ms context building
        
        Args:
            user_id: User ID to get context for
            message_text: Current message text
            max_time_ms: Maximum time allowed in milliseconds
            
        Returns:
            Context dictionary with relevant information
        """
        start_time = asyncio.get_event_loop().time()
        self.read_count += 1
        
        # Check L1 cache (hot cache)
        cache_key = f"{user_id}:{hash(message_text) if message_text else 'default'}"
        if cache_key in self.context_cache:
            cache_entry = self.context_cache[cache_key]
            if (datetime.now() - cache_entry['timestamp']).seconds < self.context_cache_ttl:
                self.cache_hits += 1
                return cache_entry['context']
                
        self.cache_misses += 1
        
        # Build context with aggressive timeout
        context = {
            'user_id': user_id,
            'recent_messages': [],
            'memories': [],
            'personality_traits': {},
            'response_history': []
        }
        
        try:
            # Create tasks for parallel fetching
            tasks = []
            
            # Recent messages from buffer (instant)
            context['recent_messages'] = [
                msg for msg in list(self.recent_messages)[-10:]
                if msg.get('user_id') == user_id
            ]
            
            # Only fetch from database if we have time
            remaining_time = max_time_ms - (asyncio.get_event_loop().time() - start_time) * 1000
            if remaining_time > 20:  # Need at least 20ms
                # Fetch from database with timeout
                timeout = remaining_time / 1000
                
                # Use wait_for for Python 3.10 compatibility
                async def fetch_db_context():
                    async with self.get_session() as session:
                        # Get recent memories
                        result = await session.execute(
                            text("""
                                SELECT content, metadata 
                                FROM memories 
                                WHERE user_id = :user_id 
                                ORDER BY timestamp DESC 
                                LIMIT 5
                            """),
                            {'user_id': user_id}
                        )
                        memories = result.fetchall()
                        context['memories'] = [
                            json.loads(row[0]) for row in memories
                        ]
                    return context
                    
                try:
                    context = await asyncio.wait_for(
                        fetch_db_context(),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.debug(f"Context building timeout for user {user_id}")
                except Exception as e:
                    logger.error(f"Error building context: {e}")
                    
        except Exception as e:
            logger.error(f"Error in context building: {e}")
            
        # Cache the context
        self.context_cache[cache_key] = {
            'context': context,
            'timestamp': datetime.now()
        }
        
        # Log performance
        elapsed_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        if elapsed_ms > max_time_ms:
            logger.warning(f"Context building took {elapsed_ms:.1f}ms (target: {max_time_ms}ms)")
            
        return context
        
    def _parse_timestamp(self, timestamp):
        """Convert timestamp to datetime object if it's a string"""
        if isinstance(timestamp, str):
            return parser.parse(timestamp)
        return timestamp
        
    def _get_fallback_context(self) -> Dict[str, Any]:
        """Return minimal fallback context when building fails"""
        return {
            'user_context': {},
            'recent_messages': list(self.recent_messages)[-10:],
            'memories': [],
            'key_topics': []
        }
        
    async def get_user_history(
        self,
        user_id: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get chat history for a user
        
        Args:
            user_id: User ID
            limit: Maximum number of messages
            
        Returns:
            List of messages
        """
        try:
            # Check Redis cache first
            if self.redis_client:
                cache_key = f"history:{user_id}:{limit}"
                cached = await self.redis_client.get(cache_key)
                if cached:
                    self.cache_hits += 1
                    return json.loads(cached)
                    
            self.cache_misses += 1
            
            # Fetch from database
            async with self.get_session() as session:
                result = await session.execute(
                    text("""
                        SELECT username, message, timestamp 
                        FROM chat_messages 
                        WHERE user_id = :user_id 
                        ORDER BY timestamp DESC 
                        LIMIT :limit
                    """),
                    {'user_id': user_id, 'limit': limit}
                )
                messages = [
                    {
                        'username': row[0],
                        'text': row[1],
                        'timestamp': row[2].isoformat() if row[2] else None
                    }
                    for row in result.fetchall()
                ]
                
            # Cache result
            if self.redis_client:
                await self.redis_client.setex(
                    cache_key,
                    300,  # 5 minute TTL for history
                    json.dumps(messages, default=str)
                )
                
            return messages
            
        except Exception as e:
            logger.error(f"Failed to get user history: {e}")
            return []
            
    async def search_memories(
        self,
        query: str,
        user_id: Optional[str] = None,
        memory_type: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search memories with optional filters
        
        Args:
            query: Search query
            user_id: Optional user filter
            memory_type: Optional type filter
            limit: Maximum results
            
        Returns:
            List of matching memories
        """
        try:
            async with self.get_session() as session:
                sql = """
                    SELECT memory_id, user_id, type, content, metadata, timestamp
                    FROM memories
                    WHERE content::text ILIKE :query
                """
                params = {'query': f'%{query}%', 'limit': limit}
                
                if user_id:
                    sql += " AND user_id = :user_id"
                    params['user_id'] = user_id
                    
                if memory_type:
                    sql += " AND type = :memory_type"
                    params['memory_type'] = memory_type
                    
                sql += " ORDER BY timestamp DESC LIMIT :limit"
                
                result = await session.execute(text(sql), params)
                memories = [
                    {
                        'memory_id': row[0],
                        'user_id': row[1],
                        'type': row[2],
                        'content': json.loads(row[3]) if row[3] else {},
                        'metadata': json.loads(row[4]) if row[4] else {},
                        'timestamp': row[5].isoformat() if row[5] else None
                    }
                    for row in result.fetchall()
                ]
                
            return memories
            
        except Exception as e:
            logger.error(f"Failed to search memories: {e}")
            return []
            
    def _generate_memory_id(self, memory_item: Dict[str, Any]) -> str:
        """
        Generate a unique memory ID
        
        Args:
            memory_item: Memory data
            
        Returns:
            Unique memory ID
        """
        # Create unique ID from user and content
        unique_str = f"{memory_item.get('user_id', '')}:{memory_item.get('type', '')}:{json.dumps(memory_item.get('content', {}))}"
        return hashlib.sha256(unique_str.encode()).hexdigest()[:16]
        
    async def cleanup_old_data(self, days: int = 30) -> int:
        """
        Clean up old data for GDPR compliance
        
        Args:
            days: Days to keep data
            
        Returns:
            Number of records deleted
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=days)
            deleted = 0
            
            async with self.get_session() as session:
                # Delete old messages
                result = await session.execute(
                    text("""
                        DELETE FROM chat_messages 
                        WHERE timestamp < :cutoff
                    """),
                    {'cutoff': cutoff_date}
                )
                deleted += result.rowcount
                
                # Delete old memories (keep personality traits)
                result = await session.execute(
                    text("""
                        DELETE FROM memories 
                        WHERE timestamp < :cutoff 
                        AND type != 'personality_trait'
                    """),
                    {'cutoff': cutoff_date}
                )
                deleted += result.rowcount
                
            logger.info(f"Cleaned up {deleted} old records")
            return deleted
            
        except Exception as e:
            logger.error(f"Failed to cleanup old data: {e}")
            return 0
            
    async def get_stats(self) -> Dict[str, Any]:
        """
        Get memory system statistics
        
        Returns:
            Statistics dictionary
        """
        cache_hit_rate = (
            self.cache_hits / (self.cache_hits + self.cache_misses)
            if (self.cache_hits + self.cache_misses) > 0
            else 0
        )
        
        return {
            'write_count': self.write_count,
            'read_count': self.read_count,
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses,
            'cache_hit_rate': cache_hit_rate,
            'recent_messages_count': len(self.recent_messages),
            'recent_memories_count': len(self.recent_memories),
            'context_cache_size': len(self.context_cache)
        }
        
    async def shutdown(self) -> None:
        """
        Gracefully shutdown the memory system
        """
        logger.info("Shutting down SingleMemorySystem...")
        
        # Close Redis connection
        if self.redis_client:
            await self.redis_client.close()
            
        # Close database connection
        if self.engine:
            await self.engine.dispose()
            
        # Clear caches
        self.recent_messages.clear()
        self.recent_memories.clear()
        self.context_cache.clear()
        self.user_context_cache.clear()
        
        logger.info("SingleMemorySystem shutdown complete")
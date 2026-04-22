"""
ResilientMemorySystem - Memory system with database connection resilience
Wraps SingleMemorySystem with automatic fallback when database is unavailable
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from collections import deque
import redis.asyncio as redis

from database.session import DatabaseSessionManager, ResilientDatabaseConnection
from memory.single_memory_system import SingleMemorySystem

logger = logging.getLogger(__name__)


class ResilientMemorySystem:
    """
    Memory system that gracefully handles database outages
    Falls back to in-memory storage when database is unavailable
    """
    
    def __init__(self, database_url: str, redis_url: str = None, max_retries: int = 3):
        """
        Initialize the ResilientMemorySystem
        
        Args:
            database_url: PostgreSQL connection string
            redis_url: Redis connection string for caching
            max_retries: Maximum database connection attempts
        """
        self.database_url = database_url
        self.redis_url = redis_url
        self.max_retries = max_retries
        
        # Database session manager
        self.db_manager = DatabaseSessionManager(database_url, max_retries)
        self.db: Optional[ResilientDatabaseConnection] = None
        
        # Redis cache
        self.redis_client = None
        self.cache_ttl = 3600  # 1 hour default TTL
        
        # In-memory fallback storage
        self.memory_buffer = deque(maxlen=1000)  # Store last 1000 items
        self.user_memory: Dict[str, List[Dict]] = {}
        self.context_memory: Dict[str, Any] = {}
        
        # Track database availability
        self.db_available = False
        self.last_db_check = datetime.now()
        self.db_check_interval = 30  # seconds
        
        # Performance tracking
        self.write_count = 0
        self.read_count = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.db_failures = 0
        
    async def initialize(self) -> None:
        """
        Initialize database and cache connections with resilience
        """
        # Try to initialize database
        logger.info("Initializing resilient memory system...")
        
        self.db_available = await self.db_manager.initialize()
        self.db = self.db_manager.get_session()
        
        if self.db_available:
            logger.info("Database connection established")
        else:
            logger.warning("Running without database - using in-memory storage only")
            self.db_failures += 1
        
        # Initialize Redis if URL provided
        if self.redis_url:
            try:
                logger.info("Initializing Redis cache...")
                self.redis_client = await redis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True
                )
                await self.redis_client.ping()
                logger.info("Redis cache connection established")
            except Exception as e:
                logger.warning(f"Redis unavailable: {e}. Continuing without cache.")
                self.redis_client = None
        
        # Start background task to check database availability
        asyncio.create_task(self._monitor_database_health())
    
    async def _monitor_database_health(self):
        """Background task to periodically check database availability"""
        while True:
            await asyncio.sleep(self.db_check_interval)
            
            if not self.db_available:
                # Try to reconnect
                logger.debug("Attempting to reconnect to database...")
                self.db_available = await self.db_manager.initialize()
                self.db = self.db_manager.get_session()
                
                if self.db_available:
                    logger.info("Database connection restored")
                    # Flush buffered data to database
                    await self._flush_buffer_to_database()
            else:
                # Check if connection is still healthy
                is_healthy = await self.db_manager.health_check()
                if not is_healthy:
                    logger.warning("Database connection lost")
                    self.db_available = False
                    self.db_failures += 1
    
    async def _flush_buffer_to_database(self):
        """Flush in-memory buffer to database when connection is restored"""
        if not self.db or not self.memory_buffer:
            return
        
        logger.info(f"Flushing {len(self.memory_buffer)} buffered items to database")
        flushed = 0
        
        for item in list(self.memory_buffer):
            try:
                if item['type'] == 'message':
                    await self._store_message_to_db(item['data'])
                elif item['type'] == 'memory':
                    await self._store_memory_to_db(item['data'])
                flushed += 1
            except Exception as e:
                logger.error(f"Failed to flush item to database: {e}")
                break
        
        if flushed > 0:
            logger.info(f"Successfully flushed {flushed} items to database")
            # Clear flushed items
            for _ in range(flushed):
                self.memory_buffer.popleft()
    
    async def store_message(self, message: Dict[str, Any]) -> None:
        """
        Store a chat message with automatic fallback
        
        Args:
            message: Message data including text, user, timestamp
        """
        self.write_count += 1
        
        # Always store in memory buffer
        self.memory_buffer.append({
            'type': 'message',
            'data': message,
            'timestamp': datetime.now()
        })
        
        # Store in user memory
        username = message.get('username', 'unknown')
        if username not in self.user_memory:
            self.user_memory[username] = []
        self.user_memory[username].append(message)
        
        # Try to store in database if available
        if self.db_available and self.db:
            success = await self._store_message_to_db(message)
            if not success:
                self.db_failures += 1
    
    async def _store_message_to_db(self, message: Dict[str, Any]) -> bool:
        """Store message in database with error handling"""
        if not self.db:
            return False

        try:
            user_id = message.get('user_id')
            username = message.get('username')

            # Upsert user first to avoid foreign key violation
            if user_id and username:
                await self.db.execute(
                    """
                    INSERT INTO users (user_id, username, first_seen, last_seen)
                    VALUES ($1, $2, $3, $3)
                    ON CONFLICT (user_id) DO UPDATE
                    SET last_seen = $3, username = $2
                    """,
                    user_id,
                    username,
                    datetime.now()
                )

            # Now insert the message
            await self.db.execute(
                """
                INSERT INTO chat_messages
                (user_id, username, message, timestamp, channel)
                VALUES ($1, $2, $3, $4, $5)
                """,
                user_id,
                username,
                message.get('message'),
                message.get('timestamp', datetime.now()),
                message.get('channel', 'twitch')
            )
            return True
        except Exception as e:
            logger.error(f"Failed to store message in database: {e}")
            return False
    
    async def get_recent_messages(
        self,
        channel: str = None,
        limit: int = 50,
        username: str = None
    ) -> List[Dict[str, Any]]:
        """
        Get recent messages with fallback to in-memory storage

        Args:
            channel: Filter by specific channel
            limit: Maximum number of messages to return
            username: Filter by specific user

        Returns:
            List of recent messages
        """
        self.read_count += 1

        # Try database first if available
        if self.db_available and self.db:
            try:
                if username and channel:
                    result = await self.db.fetch(
                        """
                        SELECT user_id, username, message, timestamp, channel
                        FROM chat_messages
                        WHERE username = $1 AND channel = $2
                        ORDER BY timestamp DESC
                        LIMIT $3
                        """,
                        username,
                        channel,
                        limit
                    )
                elif username:
                    result = await self.db.fetch(
                        """
                        SELECT user_id, username, message, timestamp, channel
                        FROM chat_messages
                        WHERE username = $1
                        ORDER BY timestamp DESC
                        LIMIT $2
                        """,
                        username,
                        limit
                    )
                elif channel:
                    result = await self.db.fetch(
                        """
                        SELECT user_id, username, message, timestamp, channel
                        FROM chat_messages
                        WHERE channel = $1
                        ORDER BY timestamp DESC
                        LIMIT $2
                        """,
                        channel,
                        limit
                    )
                else:
                    result = await self.db.fetch(
                        """
                        SELECT user_id, username, message, timestamp, channel
                        FROM chat_messages
                        ORDER BY timestamp DESC
                        LIMIT $1
                        """,
                        limit
                    )

                if result:
                    return [dict(row) for row in result]
            except Exception as e:
                logger.debug(f"Failed to fetch messages from database: {e}")
                self.db_failures += 1

        # Fallback to in-memory storage
        messages = []

        if username and username in self.user_memory:
            messages = list(self.user_memory[username])[-limit:]
        else:
            # Get from memory buffer
            for item in reversed(self.memory_buffer):
                if item['type'] == 'message':
                    data = item['data']
                    if username and data.get('username') != username:
                        continue
                    if channel and data.get('channel') != channel:
                        continue
                    messages.append(data)
                    if len(messages) >= limit:
                        break

        return messages
    
    async def store_memory(self, memory: Dict[str, Any]) -> None:
        """
        Store a memory/context item with automatic fallback
        
        Args:
            memory: Memory data to store
        """
        self.write_count += 1
        
        # Always store in memory buffer
        self.memory_buffer.append({
            'type': 'memory',
            'data': memory,
            'timestamp': datetime.now()
        })
        
        # Store in context memory
        key = memory.get('key', 'general')
        self.context_memory[key] = memory
        
        # Try to store in database if available
        if self.db_available and self.db:
            success = await self._store_memory_to_db(memory)
            if not success:
                self.db_failures += 1
    
    async def _store_memory_to_db(self, memory: Dict[str, Any]) -> bool:
        """Store memory in database with error handling"""
        if not self.db:
            return False
        
        try:
            await self.db.execute(
                """
                INSERT INTO memories 
                (user_id, type, content, metadata, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id, type) 
                DO UPDATE SET content = $3, metadata = $4, updated_at = NOW()
                """,
                memory.get('user_id'),
                memory.get('type', 'general'),
                memory.get('content'),
                memory.get('metadata', {}),
                memory.get('created_at', datetime.now())
            )
            return True
        except Exception as e:
            logger.error(f"Failed to store memory in database: {e}")
            return False
    
    async def get_user_context(self, username: str) -> Dict[str, Any]:
        """
        Get user context with fallback to in-memory storage
        
        Args:
            username: Username to get context for
            
        Returns:
            User context dictionary
        """
        self.read_count += 1
        
        # Check cache first
        if username in self.context_memory:
            self.cache_hits += 1
            return self.context_memory[username]
        
        self.cache_misses += 1
        
        # Try database if available
        if self.db_available and self.db:
            try:
                result = await self.db.fetchrow(
                    """
                    SELECT * FROM users
                    WHERE username = $1
                    """,
                    username
                )
                
                if result:
                    context = dict(result)
                    self.context_memory[username] = context
                    return context
            except Exception as e:
                logger.error(f"Failed to fetch user context from database: {e}")
                self.db_failures += 1
        
        # Fallback to basic context from in-memory data
        context = {
            'username': username,
            'message_count': len(self.user_memory.get(username, [])),
            'last_seen': datetime.now(),
            'from_memory': True
        }
        
        self.context_memory[username] = context
        return context
    
    async def get_user_history(self, username: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get user's message history for context building
        
        Args:
            username: Username to get history for
            limit: Maximum number of messages to return
            
        Returns:
            List of user's recent messages
        """
        # Reuse existing get_recent_messages with username filter
        return await self.get_recent_messages(limit=limit, username=username)
    
    async def get_user_stats(self, username: str) -> Dict[str, Any]:
        """
        Get user statistics for context building
        
        Args:
            username: Username to get stats for
            
        Returns:
            Dictionary with user statistics
        """
        stats = {
            'message_count': 0,
            'is_subscriber': False,
            'is_mod': False,
            'first_seen': None,
            'last_seen': None
        }
        
        # Try database if available
        if self.db_available and self.db:
            try:
                result = await self.db.fetchrow(
                    """
                    SELECT 
                        message_count,
                        is_subscriber,
                        is_mod,
                        first_seen,
                        last_seen
                    FROM users
                    WHERE username = $1
                    """,
                    username
                )
                
                if result:
                    stats.update(dict(result))
                    return stats
            except Exception as e:
                logger.debug(f"Failed to fetch user stats from database: {e}")
        
        # Fallback to in-memory data
        if username in self.user_memory:
            messages = self.user_memory[username]
            if messages:
                stats['message_count'] = len(messages)
                stats['first_seen'] = messages[0].get('timestamp')
                stats['last_seen'] = messages[-1].get('timestamp')
        
        return stats
    
    async def get_viewer_context(self, viewer: str, channel: str) -> Dict[str, Any]:
        """
        Get viewer context for a specific channel

        Args:
            viewer: Username to get context for
            channel: Channel to get context for

        Returns:
            Viewer context dictionary with safe defaults
        """
        try:
            return await self.get_user_context(viewer)
        except Exception as e:
            logger.debug(f"Error getting viewer context: {e}")
            return {
                'username': viewer,
                'channel': channel,
                'message_count': 0,
                'from_memory': True
            }

    async def get_channel_context(self, channel: str) -> Dict[str, Any]:
        """
        Get channel context with safe defaults

        Args:
            channel: Channel to get context for

        Returns:
            Channel context dictionary
        """
        self.read_count += 1

        # Try database if available
        if self.db_available and self.db:
            try:
                result = await self.db.fetchrow(
                    """
                    SELECT channel, COUNT(*) as message_count,
                           MAX(timestamp) as last_activity
                    FROM chat_messages
                    WHERE channel = $1
                    GROUP BY channel
                    """,
                    channel
                )

                if result:
                    return dict(result)
            except Exception as e:
                logger.debug(f"Failed to fetch channel context: {e}")
                self.db_failures += 1

        # Fallback to in-memory data
        message_count = 0
        for item in self.memory_buffer:
            if item['type'] == 'message' and item['data'].get('channel') == channel:
                message_count += 1

        return {
            'channel': channel,
            'message_count': message_count,
            'last_activity': datetime.now(),
            'from_memory': True
        }

    async def get_interaction_history(
        self,
        viewer: str,
        channel: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Get viewer's interaction history for a specific channel

        Args:
            viewer: Username to get history for
            channel: Channel to get history for
            limit: Maximum number of interactions to return

        Returns:
            List of recent interactions with safe defaults
        """
        try:
            return await self.get_recent_messages(
                channel=channel,
                username=viewer,
                limit=limit
            )
        except Exception as e:
            logger.debug(f"Error getting interaction history: {e}")
            return []

    def get_session(self):
        """Get database session for direct database operations"""
        return self.db_manager.get_session()
    
    async def close(self):
        """Clean up connections"""
        await self.db_manager.close()
        
        if self.redis_client:
            await self.redis_client.close()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get memory system statistics"""
        return {
            'db_available': self.db_available,
            'write_count': self.write_count,
            'read_count': self.read_count,
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_hits + self.cache_misses > 0 and 
                           self.cache_hits / (self.cache_hits + self.cache_misses) * 100 or 0,
            'db_failures': self.db_failures,
            'buffer_size': len(self.memory_buffer),
            'users_in_memory': len(self.user_memory)
        }
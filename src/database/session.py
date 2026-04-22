"""
Database session management with connection resilience
"""

import asyncio
import logging
from typing import Optional
import asyncpg
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class ResilientDatabaseConnection:
    """Database connection with automatic retry and fallback"""
    
    def __init__(self, database_url: str, max_retries: int = 3):
        self.database_url = database_url
        self.max_retries = max_retries
        self.connection: Optional[asyncpg.Connection] = None
        self.is_connected = False
        
    async def connect(self) -> bool:
        """Connect to database with exponential backoff"""
        for attempt in range(self.max_retries):
            try:
                # Convert SQLAlchemy URL to asyncpg format if needed
                url = self.database_url
                if url.startswith('postgresql+asyncpg://'):
                    url = url.replace('postgresql+asyncpg://', 'postgresql://')
                
                self.connection = await asyncpg.connect(url)
                self.is_connected = True
                logger.info("Database connection established")
                return True
                
            except asyncpg.InvalidCatalogNameError:
                logger.error("Database does not exist")
                return False
                
            except Exception as e:
                if attempt == self.max_retries - 1:
                    logger.error(f"Database connection failed after {self.max_retries} attempts: {e}")
                    return False
                    
                wait_time = 2 ** attempt
                logger.warning(f"Database connection failed (attempt {attempt + 1}/{self.max_retries}), "
                             f"retrying in {wait_time}s... Error: {e}")
                await asyncio.sleep(wait_time)
        
        return False
    
    async def disconnect(self):
        """Disconnect from database"""
        if self.connection:
            await self.connection.close()
            self.connection = None
            self.is_connected = False
            logger.info("Database connection closed")
    
    async def execute(self, query: str, *args, **kwargs):
        """Execute a query with automatic reconnection on failure"""
        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                logger.warning("Database unavailable, skipping query execution")
                return None
        
        try:
            return await self.connection.execute(query, *args, **kwargs)
        except (asyncpg.InterfaceError, asyncpg.ConnectionDoesNotExistError):
            logger.warning("Connection lost, attempting to reconnect...")
            self.is_connected = False
            connected = await self.connect()
            if connected:
                return await self.connection.execute(query, *args, **kwargs)
            return None
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            return None
    
    async def fetch(self, query: str, *args, **kwargs):
        """Fetch results with automatic reconnection on failure"""
        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                logger.warning("Database unavailable, returning empty results")
                return []
        
        try:
            return await self.connection.fetch(query, *args, **kwargs)
        except (asyncpg.InterfaceError, asyncpg.ConnectionDoesNotExistError):
            logger.warning("Connection lost, attempting to reconnect...")
            self.is_connected = False
            connected = await self.connect()
            if connected:
                return await self.connection.fetch(query, *args, **kwargs)
            return []
        except Exception as e:
            logger.error(f"Query fetch failed: {e}")
            return []
    
    async def fetchrow(self, query: str, *args, **kwargs):
        """Fetch single row with automatic reconnection on failure"""
        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                logger.warning("Database unavailable, returning None")
                return None
        
        try:
            return await self.connection.fetchrow(query, *args, **kwargs)
        except (asyncpg.InterfaceError, asyncpg.ConnectionDoesNotExistError):
            logger.warning("Connection lost, attempting to reconnect...")
            self.is_connected = False
            connected = await self.connect()
            if connected:
                return await self.connection.fetchrow(query, *args, **kwargs)
            return None
        except Exception as e:
            logger.error(f"Query fetchrow failed: {e}")
            return None
    
    async def fetchval(self, query: str, *args, **kwargs):
        """Fetch single value with automatic reconnection on failure"""
        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                logger.warning("Database unavailable, returning None")
                return None
        
        try:
            return await self.connection.fetchval(query, *args, **kwargs)
        except (asyncpg.InterfaceError, asyncpg.ConnectionDoesNotExistError):
            logger.warning("Connection lost, attempting to reconnect...")
            self.is_connected = False
            connected = await self.connect()
            if connected:
                return await self.connection.fetchval(query, *args, **kwargs)
            return None
        except Exception as e:
            logger.error(f"Query fetchval failed: {e}")
            return None
    
    @asynccontextmanager
    async def transaction(self):
        """Transaction context manager with automatic rollback on failure"""
        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                logger.warning("Database unavailable, skipping transaction")
                yield None
                return
        
        try:
            async with self.connection.transaction():
                yield self.connection
        except Exception as e:
            logger.error(f"Transaction failed: {e}")
            raise


async def create_db_session_with_retry(url: str, max_retries: int = 3) -> Optional[ResilientDatabaseConnection]:
    """Create database session with exponential backoff
    
    Args:
        url: Database connection URL
        max_retries: Maximum number of connection attempts
        
    Returns:
        ResilientDatabaseConnection instance or None if connection fails
    """
    db = ResilientDatabaseConnection(url, max_retries)
    connected = await db.connect()
    
    if not connected:
        logger.error(f"Failed to establish database connection after {max_retries} attempts")
        logger.info("Bot will continue running without database functionality")
        return None
    
    return db


class DatabaseSessionManager:
    """Manages database session lifecycle"""
    
    def __init__(self, database_url: str, max_retries: int = 3):
        self.database_url = database_url
        self.max_retries = max_retries
        self.db: Optional[ResilientDatabaseConnection] = None
        
    async def initialize(self) -> bool:
        """Initialize database connection"""
        self.db = await create_db_session_with_retry(
            self.database_url, 
            self.max_retries
        )
        return self.db is not None
    
    async def close(self):
        """Close database connection"""
        if self.db:
            await self.db.disconnect()
            self.db = None
    
    def get_session(self) -> Optional[ResilientDatabaseConnection]:
        """Get current database session"""
        return self.db
    
    async def health_check(self) -> bool:
        """Check database health"""
        if not self.db:
            return False
        
        try:
            result = await self.db.fetchval("SELECT 1")
            return result == 1
        except Exception:
            return False
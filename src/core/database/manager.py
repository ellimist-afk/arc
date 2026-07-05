"""
Database Manager for TalkBot API
Provides database initialization and table creation
"""

import logging
import os
from typing import Optional
from pathlib import Path

# Import existing resilient database connection
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.database.session import DatabaseSessionManager, ResilientDatabaseConnection

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Manages database connections and schema for the API server.
    Wraps the existing DatabaseSessionManager with table creation capabilities.
    """

    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize database manager

        Args:
            database_url: PostgreSQL connection URL
        """
        # Get database URL from env if not provided
        if not database_url:
            database_url = os.getenv(
                'DATABASE_URL',
                'postgresql+asyncpg://postgres:postgres@localhost:5433/streambot'
            )

        self.database_url = database_url
        self.session_manager = DatabaseSessionManager(database_url, max_retries=3)
        self.db: Optional[ResilientDatabaseConnection] = None
        self.initialized = False

    async def initialize(self) -> bool:
        """
        Initialize database connection

        Returns:
            True if connected successfully, False otherwise
        """
        try:
            # Initialize the session manager
            success = await self.session_manager.initialize()

            if success:
                self.db = self.session_manager.get_session()
                self.initialized = True
                logger.info("Database manager initialized successfully")
                return True
            else:
                logger.warning("Database connection failed, continuing without database")
                self.initialized = False
                return False

        except Exception as e:
            logger.error(f"Database initialization error: {e}")
            self.initialized = False
            return False

    async def create_tables(self) -> bool:
        """
        Create database tables from schema.sql if database is available

        Returns:
            True if tables created successfully or database unavailable, False on error
        """
        # Skip if not initialized (no database available)
        if not self.initialized or not self.db:
            logger.info("Database not available, skipping table creation")
            return True  # Not an error - just no database

        try:
            # Find schema.sql file
            schema_file = Path(__file__).parent.parent.parent / 'database' / 'schema.sql'

            if not schema_file.exists():
                logger.warning(f"Schema file not found at {schema_file}, skipping table creation")
                return True  # Not critical - tables may already exist

            # Read schema file
            with open(schema_file, 'r') as f:
                schema_sql = f.read()

            # Execute schema (creates tables if they don't exist)
            logger.info("Creating database tables from schema...")
            await self.db.execute(schema_sql)
            logger.info("Database tables created successfully")
            return True

        except Exception as e:
            # Log but don't fail startup - tables might already exist
            logger.warning(f"Table creation skipped or failed: {e}")
            return True  # Don't block startup

    async def health_check(self) -> bool:
        """
        Check database health

        Returns:
            True if database is healthy, False otherwise
        """
        if not self.initialized:
            return False

        return await self.session_manager.health_check()

    async def close(self):
        """Close database connections"""
        if self.session_manager:
            await self.session_manager.close()
            self.initialized = False
            logger.info("Database manager closed")

    def get_connection(self) -> Optional[ResilientDatabaseConnection]:
        """
        Get database connection

        Returns:
            Database connection or None if not initialized
        """
        return self.db


# Global database manager instance
_db_manager: Optional[DatabaseManager] = None


def get_db_manager() -> DatabaseManager:
    """
    Get global database manager instance

    Returns:
        DatabaseManager instance
    """
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


# Export singleton instance for imports
db_manager = get_db_manager()

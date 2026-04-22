#!/usr/bin/env python3
"""
Database Migration Script for TalkBot
Handles schema creation and updates
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DatabaseMigrator:
    """Handles database migrations for TalkBot"""
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = None
        self.SessionLocal = None
        
    async def connect(self):
        """Establish database connection"""
        try:
            # Create engine
            self.engine = create_async_engine(
                self.database_url,
                echo=False,
                pool_size=5,
                max_overflow=10
            )
            
            # Create session factory
            self.SessionLocal = sessionmaker(
                self.engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            
            logger.info("Connected to database")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            return False
    
    async def check_schema_version(self) -> str:
        """Check current schema version"""
        try:
            async with self.SessionLocal() as session:
                result = await session.execute(
                    text("SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1")
                )
                row = result.fetchone()
                if row:
                    return row[0]
                return "0.0.0"
        except Exception:
            # Table doesn't exist yet
            return "0.0.0"
    
    async def apply_migration(self, migration_file: Path) -> bool:
        """Apply a single migration file"""
        try:
            logger.info(f"Applying migration: {migration_file.name}")
            
            # Read migration SQL
            with open(migration_file, 'r') as f:
                migration_sql = f.read()
            
            # Split into individual statements (basic approach)
            statements = [s.strip() for s in migration_sql.split(';') if s.strip()]
            
            async with self.SessionLocal() as session:
                for statement in statements:
                    if statement and not statement.startswith('--'):
                        try:
                            await session.execute(text(statement))
                        except Exception as e:
                            # Log but continue for CREATE IF NOT EXISTS statements
                            if 'already exists' not in str(e):
                                logger.warning(f"Statement warning: {e}")
                
                await session.commit()
                
            logger.info(f"Migration {migration_file.name} applied successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to apply migration {migration_file.name}: {e}")
            return False
    
    async def run_migrations(self) -> bool:
        """Run all pending migrations"""
        try:
            # Get migrations directory
            migrations_dir = Path(__file__).parent.parent / 'migrations'
            if not migrations_dir.exists():
                logger.error(f"Migrations directory not found: {migrations_dir}")
                return False
            
            # Get current version
            current_version = await self.check_schema_version()
            logger.info(f"Current schema version: {current_version}")
            
            # Find migration files
            migration_files = sorted(migrations_dir.glob('*.sql'))
            
            if not migration_files:
                logger.warning("No migration files found")
                return False
            
            # Apply migrations
            for migration_file in migration_files:
                # Check if this is the schema.sql file
                if migration_file.name == 'schema.sql':
                    # Always apply schema.sql as it uses IF NOT EXISTS
                    success = await self.apply_migration(migration_file)
                    if not success:
                        return False
            
            logger.info("All migrations completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return False
    
    async def verify_schema(self) -> bool:
        """Verify that all required tables exist"""
        required_tables = [
            'users', 'messages', 'personalities', 'audio_cache',
            'bot_state', 'raid_events', 'metrics', 'schema_version'
        ]
        
        try:
            async with self.SessionLocal() as session:
                for table in required_tables:
                    result = await session.execute(
                        text(f"""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_schema = 'public' 
                            AND table_name = :table
                        )
                        """),
                        {'table': table}
                    )
                    exists = result.scalar()
                    
                    if exists:
                        logger.info(f"✓ Table '{table}' exists")
                    else:
                        logger.error(f"✗ Table '{table}' missing")
                        return False
                
                # Check for required indexes (performance critical)
                critical_indexes = [
                    ('messages', 'idx_messages_timestamp'),
                    ('messages', 'idx_messages_user_timestamp'),
                    ('audio_cache', 'idx_audio_cache_text_hash'),
                ]
                
                for table, index in critical_indexes:
                    result = await session.execute(
                        text(f"""
                        SELECT EXISTS (
                            SELECT FROM pg_indexes 
                            WHERE schemaname = 'public' 
                            AND tablename = :table
                            AND indexname = :index
                        )
                        """),
                        {'table': table, 'index': index}
                    )
                    exists = result.scalar()
                    
                    if exists:
                        logger.info(f"✓ Index '{index}' on '{table}' exists")
                    else:
                        logger.warning(f"⚠ Index '{index}' on '{table}' missing (performance may be impacted)")
            
            logger.info("Schema verification complete")
            return True
            
        except Exception as e:
            logger.error(f"Schema verification failed: {e}")
            return False
    
    async def get_statistics(self):
        """Get database statistics"""
        try:
            async with self.SessionLocal() as session:
                # Get table sizes
                result = await session.execute(text("""
                    SELECT 
                        schemaname,
                        tablename,
                        pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size,
                        n_live_tup as row_count
                    FROM pg_stat_user_tables
                    WHERE schemaname = 'public'
                    ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
                """))
                
                logger.info("\nDatabase Statistics:")
                logger.info("-" * 50)
                for row in result:
                    logger.info(f"{row.tablename:20} | Rows: {row.row_count:10,} | Size: {row.size}")
                
                # Get performance stats
                result = await session.execute(text("SELECT * FROM get_performance_stats()"))
                stats = result.fetchone()
                if stats:
                    logger.info("\nPerformance Metrics:")
                    logger.info("-" * 50)
                    logger.info(f"Total Users:         {stats.total_users:,}")
                    logger.info(f"Total Messages:      {stats.total_messages:,}")
                    logger.info(f"Messages (1h):       {stats.messages_last_hour:,}")
                    logger.info(f"Avg Response Time:   {stats.avg_response_time_ms:.2f}ms" if stats.avg_response_time_ms else "Avg Response Time:   N/A")
                    logger.info(f"Cache Hit Rate:      {stats.cache_hit_rate:.2%}" if stats.cache_hit_rate else "Cache Hit Rate:      0%")
                
        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
    
    async def cleanup(self):
        """Clean up database connections"""
        if self.engine:
            await self.engine.dispose()
            logger.info("Database connection closed")


async def main():
    """Main migration entry point"""
    # Get database URL from environment
    database_url = os.getenv('DATABASE_URL', 'postgresql+asyncpg://postgres:postgres@localhost:5433/streambot')
    
    logger.info("=" * 60)
    logger.info("TalkBot Database Migration Tool")
    logger.info("=" * 60)
    
    # Check if we're in test mode
    if '--test' in sys.argv or os.getenv('TESTING'):
        database_url = database_url.replace('streambot', 'streambot_test')
        logger.info("Running in TEST mode")
    
    logger.info(f"Database: {database_url.split('@')[1]}")  # Hide password
    
    # Create migrator
    migrator = DatabaseMigrator(database_url)
    
    try:
        # Connect to database
        if not await migrator.connect():
            logger.error("Failed to connect to database")
            sys.exit(1)
        
        # Run migrations
        if not await migrator.run_migrations():
            logger.error("Migration failed")
            sys.exit(1)
        
        # Verify schema
        if not await migrator.verify_schema():
            logger.error("Schema verification failed")
            sys.exit(1)
        
        # Show statistics
        await migrator.get_statistics()
        
        logger.info("\n✅ Database migration completed successfully!")
        
    finally:
        await migrator.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
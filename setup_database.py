#!/usr/bin/env python3
"""
Database setup script for TalkBot
Creates tables and initial data
"""

import asyncio
import sys
import os
from pathlib import Path
import logging
from dotenv import load_dotenv
import asyncpg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def setup_database():
    """Setup PostgreSQL database with schema"""
    
    # Load environment
    env_path = Path(__file__).parent / '.env'
    if not env_path.exists():
        env_path = Path(__file__).parent / 'talkbot-old' / 'talkbot' / '.env'
    
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"Loaded configuration from {env_path}")
    
    # Get database URL
    database_url = os.getenv(
        'DATABASE_URL',
        'postgresql+asyncpg://postgres:postgres@localhost:5433/streambot'
    )
    
    # Convert SQLAlchemy URL to asyncpg format
    if database_url.startswith('postgresql+asyncpg://'):
        database_url = database_url.replace('postgresql+asyncpg://', 'postgresql://')
    
    logger.info(f"Connecting to database: {database_url}")
    
    try:
        # Connect to database
        conn = await asyncpg.connect(database_url)
        
        # Read schema file
        schema_path = Path(__file__).parent / 'src' / 'database' / 'schema.sql'
        if not schema_path.exists():
            logger.error(f"Schema file not found: {schema_path}")
            return False
        
        with open(schema_path, 'r') as f:
            schema_sql = f.read()
        
        # Execute schema
        logger.info("Creating database schema...")
        
        # Split by semicolons but ignore those in functions
        statements = []
        current = []
        in_function = False
        
        for line in schema_sql.split('\n'):
            if 'CREATE OR REPLACE FUNCTION' in line.upper():
                in_function = True
            elif '$$' in line and in_function:
                in_function = False
            
            current.append(line)
            
            if ';' in line and not in_function:
                statements.append('\n'.join(current))
                current = []
        
        if current:
            statements.append('\n'.join(current))
        
        # Execute each statement
        success_count = 0
        error_count = 0
        
        for statement in statements:
            statement = statement.strip()
            if not statement or statement.startswith('--'):
                continue
            
            try:
                await conn.execute(statement)
                success_count += 1
                if 'CREATE TABLE' in statement.upper():
                    table_name = statement.split()[2].replace('IF', '').replace('NOT', '').replace('EXISTS', '').strip()
                    logger.info(f"  ✓ Created table: {table_name}")
                elif 'CREATE INDEX' in statement.upper():
                    index_name = statement.split()[2].replace('IF', '').replace('NOT', '').replace('EXISTS', '').strip()
                    logger.info(f"  ✓ Created index: {index_name}")
            except asyncpg.DuplicateTableError:
                logger.debug(f"  - Table already exists")
            except asyncpg.DuplicateObjectError:
                logger.debug(f"  - Object already exists")
            except Exception as e:
                error_count += 1
                logger.error(f"  ✗ Error executing statement: {e}")
                logger.debug(f"    Statement: {statement[:100]}...")
        
        logger.info(f"Schema setup complete: {success_count} successful, {error_count} errors")
        
        # Verify tables
        result = await conn.fetch("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        
        tables = [row['table_name'] for row in result]
        logger.info(f"Created tables: {', '.join(tables)}")
        
        await conn.close()
        return True
        
    except asyncpg.InvalidCatalogNameError:
        logger.error("Database does not exist. Please create the database first:")
        logger.error("  createdb streambot")
        return False
    except Exception as e:
        logger.error(f"Database setup failed: {e}")
        return False


async def main():
    """Main setup function"""
    logger.info("=" * 60)
    logger.info("TalkBot Database Setup")
    logger.info("=" * 60)
    
    success = await setup_database()
    
    if success:
        logger.info("\n✅ Database setup complete!")
        logger.info("\nYou can now run the bot with:")
        logger.info("  python main.py")
    else:
        logger.error("\n❌ Database setup failed")
        logger.error("Please check your database configuration and try again")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
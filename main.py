#!/usr/bin/env python3
"""
TalkBot Main Entry Point
Rebuilt with all fixes
"""

import asyncio
import sys
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent))

from src.bot.bot import TalkBot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('talkbot.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

def load_configuration():
    """
    Load configuration from environment variables
    
    Returns:
        Configuration dictionary
    """
    # Load .env file if it exists (override system env vars)
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path, override=True)
        logger.info(f"Loaded configuration from {env_path}")
    else:
        # Try the old location
        old_env_path = Path(__file__).parent / 'talkbot-old' / 'talkbot' / '.env'
        if old_env_path.exists():
            load_dotenv(old_env_path, override=True)
            logger.info(f"Loaded configuration from {old_env_path}")
    
    # Build configuration
    config = {
        # Database
        'DATABASE_URL': os.getenv(
            'DATABASE_URL',
            'postgresql+asyncpg://postgres:postgres@localhost:5433/streambot'
        ),
        'REDIS_URL': os.getenv('REDIS_URL', 'redis://localhost:6379'),
        
        # OpenAI
        'OPENAI_API_KEY': os.getenv('OPENAI_API_KEY'),
        
        # Twitch
        'TWITCH_ACCESS_TOKEN': os.getenv('TWITCH_ACCESS_TOKEN'),
        'TWITCH_CLIENT_ID': os.getenv('TWITCH_CLIENT_ID'),
        'TWITCH_CLIENT_SECRET': os.getenv('TWITCH_CLIENT_SECRET'),
        'TWITCH_BOT_USERNAME': os.getenv('TWITCH_BOT_USERNAME', 'elimist_'),
        'TWITCH_CHANNEL': os.getenv('TWITCH_CHANNEL', 'confusedamish'),
        'AUTO_START_CHANNEL': os.getenv('AUTO_START_CHANNEL', 'confusedamish'),
        'TWITCH_BROADCASTER_ID': os.getenv('TWITCH_BROADCASTER_ID'),
        
        # Features
        'TTS_ENABLED': os.getenv('TTS_ENABLED', 'true').lower() == 'true',
        'VOICE_ENABLED': os.getenv('VOICE_ENABLED', 'true').lower() == 'true',
        'VOICE_INPUT_ENABLED': os.getenv('VOICE_INPUT_ENABLED', 'true').lower() == 'true',
        
        # Performance
        'FAST_STARTUP': os.getenv('FAST_STARTUP', 'true').lower() == 'true',
        'VOICE_FIRST_STARTUP': os.getenv('VOICE_FIRST_STARTUP', 'true').lower() == 'true',
        
        # Debug
        'DEBUG': os.getenv('DEBUG', 'false').lower() == 'true',
        'LOG_LEVEL': os.getenv('LOG_LEVEL', 'INFO'),
        
        # API
        'API_PORT': int(os.getenv('API_PORT', '8000')),
        
        # OBS
        'OBS_ENABLED': os.getenv('OBS_ENABLED', 'false').lower() == 'true',
    }
    
    return config

def validate_configuration(config):
    """
    Validate required configuration
    
    Args:
        config: Configuration dictionary
        
    Raises:
        ValueError: If required configuration is missing
    """
    required_keys = [
        'OPENAI_API_KEY',
        'TWITCH_ACCESS_TOKEN',
        'TWITCH_CLIENT_ID'
    ]
    
    missing = []
    for key in required_keys:
        if not config.get(key):
            missing.append(key)
    
    if missing:
        raise ValueError(f"Missing required configuration: {', '.join(missing)}")
    
    logger.info("Configuration validated successfully")

async def setup_database(config):
    """
    Setup database if needed
    
    Args:
        config: Configuration dictionary
    """
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        
        # Create engine
        engine = create_async_engine(
            config['DATABASE_URL'],
            echo=False
        )
        
        # Test connection
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            logger.info("Database connection successful")
            
            # Check if tables exist
            result = await conn.execute(
                text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'users'
                    )
                """)
            )
            tables_exist = result.scalar()
            
            if not tables_exist:
                logger.warning("Database tables not found. Please run schema.sql to create tables.")
                logger.info("Schema file: src/database/schema.sql")
                
        await engine.dispose()
        
    except Exception as e:
        logger.error(f"Database setup error: {e}")
        logger.warning("Bot will run without database persistence")

async def main():
    """
    Main application entry point
    """
    try:
        logger.info("=" * 60)
        logger.info("TalkBot Starting...")
        logger.info("=" * 60)
        
        # Load configuration
        config = load_configuration()
        
        # Set log level
        log_level = getattr(logging, config['LOG_LEVEL'].upper(), logging.INFO)
        logging.getLogger().setLevel(log_level)
        
        # Validate configuration
        validate_configuration(config)
        
        # Setup database
        await setup_database(config)
        
        # Create and initialize bot
        logger.info("Initializing TalkBot...")
        bot = TalkBot(config)
        await bot.setup()
        
        # Log startup information
        logger.info("=" * 60)
        logger.info(f"Bot Username: {config['TWITCH_BOT_USERNAME']}")
        logger.info(f"Channel: {config['TWITCH_CHANNEL']}")
        logger.info(f"TTS Enabled: {config['TTS_ENABLED']}")
        logger.info(f"Voice Input: {config['VOICE_INPUT_ENABLED']}")
        logger.info(f"Debug Mode: {config['DEBUG']}")
        logger.info("=" * 60)
        
        # Run bot
        logger.info("Starting bot main loop...")
        await bot.run()
        
    except KeyboardInterrupt:
        logger.info("\nReceived interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("TalkBot shutdown complete")

if __name__ == "__main__":
    # Run the bot
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye!")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
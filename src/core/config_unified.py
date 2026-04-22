"""
Unified configuration management for StreamerBot
"""

import os
import json
from typing import Dict, Any, Optional
from pathlib import Path
from dataclasses import dataclass, field, asdict
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class Environment(Enum):
    """Application environment"""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass
class DatabaseConfig:
    """Database configuration"""
    url: str = ""
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30
    echo: bool = False


@dataclass
class RedisConfig:
    """Redis configuration"""
    url: str = ""
    enabled: bool = False
    ttl: int = 3600
    max_connections: int = 50


@dataclass
class TwitchConfig:
    """Twitch configuration"""
    access_token: str = ""
    broadcaster_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    bot_username: str = ""
    channel: str = ""
    command_prefix: str = "!"


@dataclass
class OpenAIConfig:
    """OpenAI configuration"""
    api_key: str = ""
    model: str = "gpt-3.5-turbo"
    max_tokens: int = 150
    temperature: float = 0.7
    tts_model: str = "tts-1"
    tts_voice: str = "nova"


@dataclass
class AudioConfig:
    """Audio configuration"""
    tts_enabled: bool = True
    cache_enabled: bool = True
    cache_size: int = 1000
    output_device: Optional[str] = None
    volume: float = 0.8


@dataclass
class FeatureFlags:
    """Feature flags configuration"""
    raider_welcome: bool = False
    advanced_personality: bool = False
    context_caching: bool = True
    voice_commands: bool = False
    web_ui: bool = True
    monitoring: bool = True


@dataclass
class LoggingConfig:
    """Logging configuration"""
    level: str = "INFO"
    json_format: bool = False
    file_enabled: bool = True
    file_path: str = "talkbot.log"
    structured: bool = False


@dataclass
class Settings:
    """
    Unified application settings
    """
    # Environment
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    
    # Component configs
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    twitch: TwitchConfig = field(default_factory=TwitchConfig)
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    features: FeatureFlags = field(default_factory=FeatureFlags)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list = field(default_factory=lambda: ["*"])

    # Compatibility properties (uppercase)
    @property
    def DEBUG(self) -> bool:
        return self.debug

    @property
    def CORS_ORIGINS(self) -> list:
        return self.cors_origins

    @property
    def ENABLE_JWT_AUTH(self) -> bool:
        return False  # Disabled by default

    @property
    def AUTO_START_CHANNEL(self) -> str:
        return os.getenv("AUTO_START_CHANNEL", "")

    # Performance settings
    max_workers: int = 10
    task_timeout: int = 30
    
    @classmethod
    def from_env(cls) -> 'Settings':
        """
        Load settings from environment variables
        
        Returns:
            Settings instance
        """
        settings = cls()
        
        # Environment
        env_str = os.getenv('ENVIRONMENT', 'development').lower()
        settings.environment = Environment(env_str) if env_str in [e.value for e in Environment] else Environment.DEVELOPMENT
        settings.debug = os.getenv('DEBUG', 'false').lower() == 'true'
        
        # Database
        settings.database.url = os.getenv('DATABASE_URL', '')
        settings.database.pool_size = int(os.getenv('DB_POOL_SIZE', '10'))
        settings.database.echo = os.getenv('DB_ECHO', 'false').lower() == 'true'
        
        # Redis
        settings.redis.url = os.getenv('REDIS_URL', '')
        settings.redis.enabled = bool(settings.redis.url)
        settings.redis.ttl = int(os.getenv('REDIS_TTL', '3600'))
        
        # Twitch
        settings.twitch.access_token = os.getenv('TWITCH_ACCESS_TOKEN', '')
        settings.twitch.broadcaster_token = os.getenv('TWITCH_BROADCASTER_TOKEN', '')
        settings.twitch.client_id = os.getenv('TWITCH_CLIENT_ID', '')
        settings.twitch.client_secret = os.getenv('TWITCH_CLIENT_SECRET', '')
        settings.twitch.bot_username = os.getenv('TWITCH_BOT_USERNAME', '')
        settings.twitch.channel = os.getenv('TWITCH_CHANNEL', '')
        settings.twitch.command_prefix = os.getenv('COMMAND_PREFIX', '!')
        
        # OpenAI
        settings.openai.api_key = os.getenv('OPENAI_API_KEY', '')
        settings.openai.model = os.getenv('OPENAI_MODEL', 'gpt-3.5-turbo')
        settings.openai.max_tokens = int(os.getenv('OPENAI_MAX_TOKENS', '150'))
        settings.openai.temperature = float(os.getenv('OPENAI_TEMPERATURE', '0.7'))
        settings.openai.tts_model = os.getenv('TTS_MODEL', 'tts-1')
        settings.openai.tts_voice = os.getenv('TTS_VOICE', 'nova')
        
        # Audio
        settings.audio.tts_enabled = os.getenv('TTS_ENABLED', 'true').lower() == 'true'
        settings.audio.cache_enabled = os.getenv('TTS_CACHE_ENABLED', 'true').lower() == 'true'
        settings.audio.cache_size = int(os.getenv('TTS_CACHE_SIZE', '1000'))
        settings.audio.volume = float(os.getenv('AUDIO_VOLUME', '0.8'))
        
        # Feature flags
        settings.features.raider_welcome = os.getenv('FEATURE_RAIDER_WELCOME', 'false').lower() == 'true'
        settings.features.advanced_personality = os.getenv('FEATURE_ADVANCED_PERSONALITY', 'false').lower() == 'true'
        settings.features.context_caching = os.getenv('FEATURE_CONTEXT_CACHING', 'true').lower() == 'true'
        settings.features.voice_commands = os.getenv('FEATURE_VOICE_COMMANDS', 'false').lower() == 'true'
        settings.features.web_ui = os.getenv('FEATURE_WEB_UI', 'true').lower() == 'true'
        settings.features.monitoring = os.getenv('FEATURE_MONITORING', 'true').lower() == 'true'
        
        # Logging
        settings.logging.level = os.getenv('LOG_LEVEL', 'INFO').upper()
        settings.logging.json_format = os.getenv('LOG_JSON', 'false').lower() == 'true'
        settings.logging.structured = os.getenv('STRUCTURED_LOGGING', 'false').lower() == 'true'
        
        # API
        settings.api_host = os.getenv('API_HOST', '0.0.0.0')
        settings.api_port = int(os.getenv('API_PORT', '8000'))
        
        # Performance
        settings.max_workers = int(os.getenv('MAX_WORKERS', '10'))
        settings.task_timeout = int(os.getenv('TASK_TIMEOUT', '30'))
        
        return settings
    
    @classmethod
    def from_file(cls, file_path: str) -> 'Settings':
        """
        Load settings from JSON file
        
        Args:
            file_path: Path to configuration file
            
        Returns:
            Settings instance
        """
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        settings = cls()
        
        # Update from file data
        for key, value in data.items():
            if hasattr(settings, key):
                if isinstance(getattr(settings, key), (DatabaseConfig, RedisConfig, TwitchConfig, 
                                                       OpenAIConfig, AudioConfig, FeatureFlags, LoggingConfig)):
                    # Handle nested configs
                    config_class = type(getattr(settings, key))
                    setattr(settings, key, config_class(**value))
                else:
                    setattr(settings, key, value)
        
        return settings
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert settings to dictionary"""
        data = asdict(self)
        data['environment'] = self.environment.value
        return data
    
    def save_to_file(self, file_path: str) -> None:
        """
        Save settings to JSON file
        
        Args:
            file_path: Path to save configuration
        """
        with open(file_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
    
    def validate(self) -> bool:
        """
        Validate settings
        
        Returns:
            True if valid, raises ValueError otherwise
        """
        errors = []
        
        # Check required fields
        if not self.database.url:
            errors.append("Database URL is required")
        
        if not self.twitch.access_token:
            errors.append("Twitch access token is required")
        
        if not self.twitch.bot_username:
            errors.append("Twitch bot username is required")
        
        if not self.twitch.channel:
            errors.append("Twitch channel is required")
        
        if not self.openai.api_key:
            errors.append("OpenAI API key is required")
        
        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")
        
        return True


# Singleton instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the singleton settings instance"""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
        try:
            _settings.validate()
        except ValueError as e:
            logger.warning(f"Configuration validation failed: {e}")
    return _settings


def reload_settings() -> Settings:
    """Reload settings from environment"""
    global _settings
    _settings = Settings.from_env()
    return _settings
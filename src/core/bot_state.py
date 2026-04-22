"""
BotState - PRD Required Component (Section 3.3)
Single source of truth for bot state
Simple, serializable dataclass with no complex nested objects
"""

from dataclasses import dataclass, asdict, field
from typing import Literal, Optional
import json
from datetime import datetime


@dataclass
class BotState:
    """
    Single source of truth for bot state
    PRD Section 3.3 - lines 115-132
    
    Rules:
    - No complex nested objects
    - No manager references  
    - Just simple, serializable data
    """
    
    # Core identification
    streamer_id: str
    
    # Runtime state
    is_running: bool = False
    
    # Feature toggles
    voice_enabled: bool = True
    tts_enabled: bool = True
    
    # Response configuration
    response_cooldown: int = 30  # seconds
    personality_preset: str = "friendly"
    
    # Model configuration
    primary_model: str = "gpt-3.5-turbo"
    fallback_model: str = "gpt-3.5-turbo"
    
    # Raider welcome feature flags
    raider_welcome_enabled: bool = False
    raider_analysis_depth: Literal["basic", "full"] = "basic"
    
    # Response coordination settings
    response_timing_mode: Literal["simultaneous", "chat_first", "voice_first"] = "chat_first"
    dead_air_prevention_enabled: bool = True
    dead_air_threshold: int = 60  # seconds
    
    # Performance settings
    context_cache_enabled: bool = True
    max_response_time_ms: int = 2000
    
    # Monitoring
    startup_time: Optional[datetime] = field(default=None, repr=False)
    last_activity: Optional[datetime] = field(default=None, repr=False)
    
    def to_dict(self) -> dict:
        """
        Convert to dictionary for serialization
        Handles datetime objects properly
        """
        data = asdict(self)
        
        # Convert datetime objects to ISO format
        if data.get('startup_time'):
            data['startup_time'] = data['startup_time'].isoformat()
        if data.get('last_activity'):
            data['last_activity'] = data['last_activity'].isoformat()
            
        return data
    
    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), indent=2)
    
    @classmethod
    def from_dict(cls, data: dict) -> "BotState":
        """
        Create BotState from dictionary
        Handles datetime parsing
        """
        # Parse datetime strings back to datetime objects
        if data.get('startup_time') and isinstance(data['startup_time'], str):
            data['startup_time'] = datetime.fromisoformat(data['startup_time'])
        if data.get('last_activity') and isinstance(data['last_activity'], str):
            data['last_activity'] = datetime.fromisoformat(data['last_activity'])
            
        return cls(**data)
    
    @classmethod
    def from_json(cls, json_str: str) -> "BotState":
        """Create BotState from JSON string"""
        return cls.from_dict(json.loads(json_str))
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.now()
    
    def get_uptime_seconds(self) -> Optional[float]:
        """Get uptime in seconds if running"""
        if self.startup_time and self.is_running:
            return (datetime.now() - self.startup_time).total_seconds()
        return None
    
    def validate(self) -> bool:
        """
        Validate state consistency
        Returns True if state is valid
        """
        # Validate response cooldown range
        if not 0 <= self.response_cooldown <= 300:
            return False
            
        # Validate dead air threshold
        if not 1 <= self.dead_air_threshold <= 300:
            return False
            
        # Validate max response time
        if not 100 <= self.max_response_time_ms <= 10000:
            return False
            
        # Validate personality preset
        valid_presets = ["friendly", "sassy", "educational", "chaotic", "custom"]
        if self.personality_preset not in valid_presets:
            return False
            
        return True
    
    def merge_settings(self, settings: dict):
        """
        Merge settings from external source (like bot_settings.json)
        Only updates fields that exist in the dataclass
        """
        for key, value in settings.items():
            if hasattr(self, key):
                # Type checking for literal fields
                if key == "raider_analysis_depth" and value not in ["basic", "full"]:
                    continue
                if key == "response_timing_mode" and value not in ["simultaneous", "chat_first", "voice_first"]:
                    continue
                    
                setattr(self, key, value)
    
    def __str__(self) -> str:
        """Human-readable string representation"""
        uptime = self.get_uptime_seconds()
        uptime_str = f"{uptime:.0f}s" if uptime else "not running"
        
        return (
            f"BotState(streamer={self.streamer_id}, "
            f"running={self.is_running}, "
            f"uptime={uptime_str}, "
            f"personality={self.personality_preset}, "
            f"voice={self.voice_enabled}, "
            f"tts={self.tts_enabled})"
        )
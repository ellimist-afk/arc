"""
OptimizedAudioQueue with intelligent prioritization and TTS caching
"""

import asyncio
import logging
import hashlib
import json
import os
import time
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import tempfile
import wave
import pyaudio
from openai import AsyncOpenAI
import sys
from pathlib import Path

# Add parent directory to path for imports
if str(Path(__file__).parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from audio.tts_cache_sqlite import TTSCacheSQLite

logger = logging.getLogger(__name__)

class Priority(Enum):
    """Audio priority levels"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4

@dataclass
class AudioItem:
    """Represents an item in the audio queue"""
    text: str
    priority: Priority
    user: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    is_mention: bool = False
    cache_key: Optional[str] = None
    audio_data: Optional[bytes] = None
    ttl: int = 300  # Time to live in seconds
    
    def __lt__(self, other):
        """Compare items for priority queue"""
        if self.priority.value != other.priority.value:
            return self.priority.value > other.priority.value
        return self.timestamp < other.timestamp

# TTSCache implementation moved to tts_cache_sqlite.py
# Using SQLite-backed cache for better performance and persistence

class OptimizedAudioQueue:
    """
    Enhanced audio queue with intelligent prioritization and caching
    Implements all optimizations
    """
    
    def __init__(
        self,
        openai_api_key: str,
        cache_size_mb: int = 500,
        enable_pre_buffering: bool = True
    ):
        """
        Initialize the optimized audio queue
        
        Args:
            openai_api_key: OpenAI API key for TTS
            cache_size_mb: Maximum cache size in MB
            enable_pre_buffering: Whether to pre-buffer common responses
        """
        self.openai_client = AsyncOpenAI(api_key=openai_api_key)
        
        # Load voice settings early for cache consistency
        self.voice = "nova"  # default
        self.speed = 1.0  # default
        try:
            import json
            import os
            settings_file = 'bot_settings.json'
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    settings = json.load(f)
                    self.voice = settings.get('voice', {}).get('model', 'nova')
                    self.speed = settings.get('voice', {}).get('speed', 1.0)
                    logger.info(f"Loaded TTS settings: voice={self.voice}, speed={self.speed}")
        except Exception as e:
            logger.debug(f"Could not load voice settings: {e}")
        
        # Use SQLite TTS cache (initialized in initialize() method)
        self.cache = TTSCacheSQLite(max_size_mb=cache_size_mb)
        self.enhanced_cache = True
        logger.info("Using SQLite TTS cache")
            
        self.enable_pre_buffering = enable_pre_buffering
        
        # Initialize circuit breaker for TTS API
        self.tts_circuit_breaker = CircuitBreaker(
            name="OpenAI_TTS",
            failure_threshold=3,
            recovery_timeout=20.0,
            success_threshold=1,
            expected_exception=Exception
        )
        
        # Queue management
        self.queue: List[AudioItem] = []
        self.processing = False
        self.current_item: Optional[AudioItem] = None
        self.processing_task = None
        
        # Audio playback
        self.pyaudio = None
        self.stream = None
        
        # Performance tracking
        self.items_processed = 0
        self.total_processing_time = 0
        self.quality_degradations = 0
        
        # Volume control for VAD ducking
        self.current_volume = 1.0
        
        # Pre-buffered common responses
        self.common_responses = [
            "Hello!",
            "Thanks for following!",
            "Welcome to the stream!",
            "Good to see you!",
            "Have a great day!"
        ]
        
    def _list_audio_devices(self) -> None:
        """List all available audio devices for debugging"""
        try:
            info = self.pyaudio.get_host_api_info_by_index(0)
            num_devices = info.get('deviceCount')
            
            logger.info(f"Found {num_devices} audio devices:")
            for i in range(num_devices):
                device_info = self.pyaudio.get_device_info_by_host_api_device_index(0, i)
                if device_info.get('maxOutputChannels') > 0:
                    logger.info(f"  Output Device {i}: {device_info.get('name')} "
                              f"(channels: {device_info.get('maxOutputChannels')}, "
                              f"rate: {device_info.get('defaultSampleRate')})")
                              
            # Log default output device
            default_output = self.pyaudio.get_default_output_device_info()
            logger.info(f"Default output device: {default_output.get('name')} (index: {default_output.get('index')})")
        except Exception as e:
            logger.error(f"Error listing audio devices: {e}")
    
    async def initialize(self) -> None:
        """Initialize the audio queue and pre-buffer if enabled"""
        logger.info("Initializing OptimizedAudioQueue...")

        # Initialize SQLite cache
        await self.cache.initialize()

        # Log cache stats on startup
        stats = await self.cache.get_stats()
        logger.info(f"Loaded TTS cache: {stats['entry_count']} entries, {stats['cache_size_mb']:.1f} MB, hit rate: {stats['hit_rate']}")
        
        # Initialize PyAudio
        self.pyaudio = pyaudio.PyAudio()
        
        # List available audio devices for debugging
        self._list_audio_devices()
        
        # Pre-buffer common responses if enabled - but do it asynchronously
        if self.enable_pre_buffering:
            asyncio.create_task(self._pre_buffer_responses())
        
        # Start the processing task
        self.processing_task = asyncio.create_task(self._process_queue())
        logger.info("Started audio queue processing task")
            
        logger.info("OptimizedAudioQueue initialized")
    
    async def _process_queue(self) -> None:
        """Background task to process the audio queue"""
        while True:
            try:
                await self.process_next()
                await asyncio.sleep(0.1)  # Small delay between items
            except Exception as e:
                logger.error(f"Error in audio queue processing: {e}")
                await asyncio.sleep(1)  # Longer delay on error
        
    async def _pre_buffer_responses(self) -> None:
        """Pre-buffer common responses during idle time"""
        logger.info("Pre-buffering common responses...")

        for response in self.common_responses:
            # Check if already cached with correct voice/speed
            cached = await self.cache.get(response, voice=self.voice, speed=self.speed)
            if not cached:
                try:
                    audio_data = await self._generate_tts(response)
                    await self.cache.put(response, audio_data, voice=self.voice, speed=self.speed)
                    await asyncio.sleep(0.5)  # Rate limiting
                except Exception as e:
                    logger.error(f"Failed to pre-buffer response '{response}': {e}")

        logger.info(f"Pre-buffered {len(self.common_responses)} responses")
        
    async def queue_audio(
        self,
        text: str,
        priority: str = "normal",
        user: Optional[str] = None
    ) -> None:
        """
        Queue audio for playback with intelligent prioritization
        
        Args:
            text: Text to convert to speech
            priority: Priority level (low, normal, high, critical)
            user: User who triggered the audio
        """
        # Detect @mention and boost priority
        is_mention = "@" in text and any(
            word.startswith("@") for word in text.split()
        )
        
        # Convert priority string to enum
        priority_map = {
            "low": Priority.LOW,
            "normal": Priority.NORMAL,
            "high": Priority.HIGH,
            "critical": Priority.CRITICAL
        }
        priority_enum = priority_map.get(priority.lower(), Priority.NORMAL)
        if is_mention:
            if priority_enum == Priority.NORMAL:
                priority_enum = Priority.HIGH
            elif priority_enum == Priority.LOW:
                priority_enum = Priority.NORMAL
                
        # Create audio item
        item = AudioItem(
            text=text,
            priority=priority_enum,
            user=user,
            is_mention=is_mention,
            ttl=600 if is_mention else 300  # Extended TTL for mentions
        )
        
        # Check if we should merge with existing item
        if self._should_merge(item):
            self._merge_item(item)
        else:
            self.queue.append(item)
            self.queue.sort()  # Sort by priority
            
        logger.info(f"Audio queued: text='{text[:50]}...', priority={priority_enum.name}, mention={is_mention}, user={user}, queue_size={len(self.queue)}")
        
    def _should_merge(self, item: AudioItem) -> bool:
        """
        Check if item should be merged with existing queue item
        
        Args:
            item: Audio item to check
            
        Returns:
            True if should merge
        """
        if not self.queue:
            return False
            
        # Look for similar items from same user within 5 seconds
        for existing in self.queue:
            if existing.user == item.user:
                time_diff = abs((item.timestamp - existing.timestamp).total_seconds())
                if time_diff < 5 and existing.priority == item.priority:
                    return True
                    
        return False
        
    def _merge_item(self, item: AudioItem) -> None:
        """
        Merge item with existing queue item
        
        Args:
            item: Audio item to merge
        """
        for existing in self.queue:
            if existing.user == item.user and existing.priority == item.priority:
                # Merge text
                existing.text += f" {item.text}"
                # Update mention status
                existing.is_mention = existing.is_mention or item.is_mention
                # Extend TTL if mention
                if item.is_mention:
                    existing.ttl = max(existing.ttl, item.ttl)
                logger.debug(f"Merged audio from {item.user}")
                break
                
    async def process_next(self) -> None:
        """Process the next item in the queue"""
        if self.processing or not self.queue:
            return
            
        self.processing = True
        start_time = time.time()
        
        try:
            # Get highest priority item
            self.current_item = self.queue.pop(0)
            
            # Check TTL
            age = (datetime.now() - self.current_item.timestamp).total_seconds()
            if age > self.current_item.ttl:
                logger.debug(f"Dropping expired audio item (age: {age}s)")
                return
                
            # Get or generate audio
            audio_data = await self._get_or_generate_audio(self.current_item)
            
            if audio_data:
                # Play audio
                logger.info(f"[AUDIO PLAYBACK] Starting playback of {len(audio_data)} bytes")
                await self._play_audio(audio_data)
                logger.info(f"[AUDIO PLAYBACK] Completed successfully")
                
            # Update stats
            self.items_processed += 1
            self.total_processing_time += (time.time() - start_time)
            
        except Exception as e:
            logger.error(f"Error processing audio: {e}")
        finally:
            self.current_item = None
            self.processing = False
            
    async def _get_or_generate_audio(self, item: AudioItem) -> Optional[bytes]:
        """
        Get audio from cache or generate new
        
        Args:
            item: Audio item
            
        Returns:
            Audio data bytes or None
        """
        # Check cache first with correct voice/speed
        cached = await self.cache.get(item.text, voice=self.voice, speed=self.speed)
        if cached:
            return cached

        # Generate new audio
        try:
            audio_data = await self._generate_tts(item.text)
            # Cache for future use with correct voice/speed
            await self.cache.put(item.text, audio_data, voice=self.voice, speed=self.speed)
            return audio_data
        except Exception as e:
            logger.error(f"Failed to generate TTS: {e}")
            return None
            
    async def _generate_tts(self, text: str) -> bytes:
        """
        Generate TTS audio using OpenAI
        
        Args:
            text: Text to convert
            
        Returns:
            Audio data bytes
        """
        # Use the voice settings loaded at initialization
        # Wrap TTS call with circuit breaker
        async def call_tts():
            return await self.openai_client.audio.speech.create(
                model="tts-1",
                voice=self.voice,
                input=text,
                response_format="pcm",
                speed=self.speed
            )
        
        try:
            response = await self.tts_circuit_breaker.call(call_tts)
        except CircuitBreakerOpenError as e:
            logger.warning(f"TTS circuit breaker open: {e}")
            # Generate silence or use cached generic response
            return self._generate_silence_audio(duration=1.0)
        
        # Get audio data
        audio_data = response.content
            
        return audio_data
        
    async def _play_audio(self, audio_data: bytes) -> None:
        """
        Play audio data
        
        Args:
            audio_data: PCM audio data to play
        """
        try:
            logger.debug(f"Playing audio: {len(audio_data)} bytes")
            
            # Open audio stream if not already open
            if not self.stream:
                logger.debug("Opening audio stream: 24kHz, mono, 16-bit")
                # Get default output device index to ensure audio goes to headphones
                default_device = self.pyaudio.get_default_output_device_info()
                device_index = default_device.get('index')
                logger.debug(f"Using audio device: {default_device.get('name')} (index: {device_index})")
                
                self.stream = self.pyaudio.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=24000,  # 24kHz as per OpenAI TTS
                    output=True,
                    output_device_index=device_index
                )
            if audio_data.startswith(b'RIFF'):
                # Skip WAV header (44 bytes)
                logger.debug("Stripping 44-byte WAV header")
                audio_data = audio_data[44:]
            
            # Apply volume scaling for VAD ducking
            if self.current_volume != 1.0:
                # Convert to numpy for volume scaling
                import numpy as np
                audio_array = np.frombuffer(audio_data, dtype=np.int16)
                audio_array = (audio_array * self.current_volume).astype(np.int16)
                audio_data = audio_array.tobytes()
                logger.debug(f"Applied volume scaling: {self.current_volume:.2f}")
                
            self.stream.write(audio_data)
            logger.info(f"AUDIO PLAYED: {len(audio_data)} bytes, volume={self.current_volume}")
            
        except Exception as e:
            logger.error(f"Error playing audio: {e}")
            
    def get_queue_load(self) -> float:
        """
        Get current queue load percentage
        
        Returns:
            Load percentage (0-100)
        """
        # Consider queue full at 20 items
        return min(100, (len(self.queue) / 20) * 100)
        
    def should_degrade_quality(self) -> bool:
        """
        Check if quality should be degraded due to load
        
        Returns:
            True if should degrade
        """
        load = self.get_queue_load()
        if self.current_item and self.current_item.is_mention:
            load *= 0.5
            
        if load > 90:
            self.quality_degradations += 1
            return True
            
        return False
    
    def set_volume(self, volume: float) -> None:
        """
        Set playback volume for VAD ducking
        
        Args:
            volume: Volume level (0.0-1.0)
        """
        self.current_volume = max(0.0, min(1.0, volume))
        logger.debug(f"Audio volume set to {self.current_volume:.2f}")
        
    def get_volume(self) -> float:
        """Get current playback volume"""
        return self.current_volume
        
    async def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        avg_processing_time = (
            self.total_processing_time / self.items_processed
            if self.items_processed > 0
            else 0
        )

        return {
            'queue_length': len(self.queue),
            'queue_load': self.get_queue_load(),
            'items_processed': self.items_processed,
            'avg_processing_time': avg_processing_time,
            'quality_degradations': self.quality_degradations,
            'cache_stats': await self.cache.get_stats(),
            'processing': self.processing
        }
        
    def _generate_silence_audio(self, duration: float = 1.0) -> bytes:
        """Generate silence audio as fallback when TTS is unavailable"""
        # PCM format: 24kHz, 16-bit, mono
        sample_rate = 24000
        num_samples = int(sample_rate * duration)
        # Generate silence (all zeros)
        silence = b'\x00\x00' * num_samples
        return silence
    
    async def shutdown(self) -> None:
        """Shutdown the audio queue"""
        logger.info("Shutting down OptimizedAudioQueue...")
        
        # Close SQLite cache database
        await self.cache.close()
        logger.info("Closed TTS cache database")
        
        # Cancel processing task
        if self.processing_task:
            self.processing_task.cancel()
            try:
                await self.processing_task
            except asyncio.CancelledError:
                pass
        
        # Clear queue
        self.queue.clear()
        
        # Close audio stream
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            
        # Terminate PyAudio
        if self.pyaudio:
            self.pyaudio.terminate()
            
        logger.info("OptimizedAudioQueue shutdown complete")
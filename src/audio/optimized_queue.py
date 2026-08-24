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
from audio.utterance_player import UtterancePlayer, UtteranceStats
from audio.tts_cache_sqlite import TTSCacheSQLite
from utils.task_registry import get_global_registry, cancel_and_wait

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
    # Streamed utterance: sentences arrive over time and are spoken as they
    # do. One queue item = one whole reply, so ordering/merging stay atomic.
    utterance: Optional[UtterancePlayer] = None
    sentences: Optional[Any] = None  # AsyncIterator[str]
    
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
        
        # Volume control for VAD ducking. Read live inside the chunked
        # playback loop so ducking takes effect mid-clip, not just per-clip.
        self.current_volume = 1.0

        # Set to interrupt the clip currently being written (skip command).
        # Checked between chunks in the playback worker thread; cleared at the
        # start of each new clip.
        self._skip_playback = False

        # Streamed-utterance metrics
        self.utterances_played = 0
        self.last_time_to_first_audio: Optional[float] = None
        self.utterances_abandoned = 0
        self._abandon_seq = 0  # unique TaskRegistry names for cleanup tasks
        # Shared shutdown outcome: the first caller tears down, every
        # concurrent or later caller awaits the same future (see shutdown()).
        self._shutdown_future: Optional[asyncio.Future] = None

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
        
        # Pre-buffer common responses if enabled - but do it asynchronously.
        # Use TaskRegistry (never raw asyncio.create_task) per CLAUDE.md.
        registry = get_global_registry()
        if self.enable_pre_buffering:
            registry.create_task(
                self._pre_buffer_responses(),
                name="audio_pre_buffer"
            )

        # Start the processing task
        self.processing_task = registry.create_task(
            self._process_queue(),
            name="audio_queue_process"
        )
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

    async def queue_utterance(
        self,
        sentences: Any,
        priority: str = "normal",
        user: Optional[str] = None,
        is_mention: bool = False,
        *,
        prefetch_depth: int = 2,
    ) -> UtterancePlayer:
        """
        Queue a streamed reply: `sentences` is an async iterator that yields
        TTS-ready sentences as the model writes them. Playback of sentence N
        overlaps TTS of sentence N+1 (see UtterancePlayer). The whole reply is
        one queue item, so it is never merged with, or interleaved by, other
        audio. Returns the player (for skip / stats).
        """
        priority_map = {
            "low": Priority.LOW, "normal": Priority.NORMAL,
            "high": Priority.HIGH, "critical": Priority.CRITICAL,
        }
        priority_enum = priority_map.get(priority.lower(), Priority.NORMAL)
        if is_mention and priority_enum in (Priority.LOW, Priority.NORMAL):
            priority_enum = Priority(priority_enum.value + 1)

        player = UtterancePlayer(
            tts=self._tts_for_text,
            play=self._play_audio,
            prefetch_depth=prefetch_depth,
        )
        item = AudioItem(
            text="<streamed utterance>",
            priority=priority_enum,
            user=user,
            is_mention=is_mention,
            ttl=600 if is_mention else 300,
            utterance=player,
            sentences=sentences,
        )
        self.queue.append(item)
        self.queue.sort()
        logger.info(f"Utterance queued: priority={priority_enum.name}, mention={is_mention}, "
                    f"user={user}, queue_size={len(self.queue)}")
        return player

    async def _tts_for_text(self, text: str) -> Optional[bytes]:
        """Cache-aware TTS for one sentence (the UtterancePlayer's producer)."""
        return await self._get_or_generate_audio(AudioItem(text=text, priority=Priority.NORMAL))

    async def _play_utterance(self, item: AudioItem) -> UtteranceStats:
        """Run one streamed reply to completion (or skip)."""
        stats = await item.utterance.run(item.sentences)
        self.utterances_played += 1
        self.last_time_to_first_audio = stats.time_to_first_audio
        ttfa = f"{stats.time_to_first_audio * 1000:.0f}ms" if stats.time_to_first_audio is not None else "n/a"
        logger.info(f"[UTTERANCE] {stats.played}/{stats.sentences} sentences played, "
                    f"first audio after {ttfa}, total {stats.duration:.2f}s"
                    + (", skipped" if item.utterance.skipped else "")
                    + (f", {stats.failed} TTS failures" if stats.failed else ""))
        return stats

    def _should_merge(self, item: AudioItem) -> bool:
        """
        Check if item should be merged with existing queue item
        
        Args:
            item: Audio item to check
            
        Returns:
            True if should merge
        """
        if not self.queue or item.utterance:
            return False

        # Look for similar items from same user within 5 seconds
        for existing in self.queue:
            if existing.utterance:
                continue  # a streamed reply is atomic; never fold text into it
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
                if self.current_item.sentences is not None and hasattr(self.current_item.sentences, 'aclose'):
                    # Never consumed: close the generator so whoever awaits
                    # the reply's completion is released
                    await self.current_item.sentences.aclose()
                return

            # Streamed reply: sentences are generated and spoken as they arrive
            if self.current_item.utterance:
                await self._play_utterance(self.current_item)
                self.items_processed += 1
                self.total_processing_time += (time.time() - start_time)
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
        Play audio data.

        CRITICAL: every blocking PyAudio operation here (opening the output
        stream, and above all ``stream.write()``, which blocks for the full
        clip duration) is dispatched to a worker thread via
        ``asyncio.to_thread``. Running it inline starved the event loop for
        hundreds of milliseconds per clip -- the documented ~761ms API stall
        and the root cause of Twitch EventSub 4002 disconnects (the loop
        couldn't send a timely WebSocket PONG while blocked in write()).

        Args:
            audio_data: PCM audio data to play
        """
        try:
            logger.debug(f"Playing audio: {len(audio_data)} bytes")

            if audio_data.startswith(b'RIFF'):
                # Skip WAV header (44 bytes)
                logger.debug("Stripping 44-byte WAV header")
                audio_data = audio_data[44:]

            # Fresh clip: clear any stale skip request from a prior one.
            self._skip_playback = False

            # Blocking parts (stream open + the full-duration write loop) run
            # off the event loop. The worker re-reads self.current_volume and
            # self._skip_playback between chunks, so VAD ducking and the skip
            # command both take effect mid-clip.
            await asyncio.to_thread(self._play_audio_blocking, audio_data)

            logger.info(f"AUDIO PLAYED: {len(audio_data)} bytes")

        except Exception as e:
            logger.error(f"Error playing audio: {e}")

    # ~4096 frames per chunk. 16-bit mono => 2 bytes/frame => 8192 bytes.
    # Small enough that volume/skip are re-checked ~3x/sec at 24kHz, large
    # enough that per-chunk overhead is negligible.
    _PLAYBACK_CHUNK_FRAMES = 4096
    _BYTES_PER_FRAME = 2  # paInt16, mono

    def _play_audio_blocking(self, audio_data: bytes) -> None:
        """
        Synchronous chunked audio playback -- runs in a worker thread, NEVER
        on the loop.

        Opens the PyAudio output stream on first use, then writes the PCM in
        ~4096-frame chunks. Between chunks it re-reads ``self.current_volume``
        (so VAD ducking applies mid-clip) and ``self._skip_playback`` (so the
        skip voice command interrupts the current clip, not just the queue).
        Each ``stream.write`` blocks only for its chunk's duration.

        Single-writer by construction: the queue processes items sequentially
        (``self.processing`` guard in ``process_next``), so only one
        ``_play_audio`` -> ``to_thread`` is ever in flight, and the shared
        ``self.stream`` is not touched concurrently.
        """
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

        import numpy as np

        chunk_bytes = self._PLAYBACK_CHUNK_FRAMES * self._BYTES_PER_FRAME
        for i in range(0, len(audio_data), chunk_bytes):
            if self._skip_playback:
                logger.info("Playback interrupted by skip request")
                break

            chunk = audio_data[i:i + chunk_bytes]

            # Re-read volume per chunk so mid-clip VAD ducking is audible.
            volume = self.current_volume
            if volume != 1.0:
                audio_array = np.frombuffer(chunk, dtype=np.int16)
                audio_array = (audio_array * volume).astype(np.int16)
                chunk = audio_array.tobytes()

            self.stream.write(chunk)

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

    def _abandon_items(self, items: List[AudioItem], reason: str) -> None:
        """Release every streamed reply among `items`.

        A streamed item owns a sentence stream that some handler is awaiting.
        Dropping the item without telling that stream leaves the handler stuck
        until its 90s timeout, so the notification happens synchronously here
        (skip/shutdown cannot await) and the generator's own cleanup is
        scheduled. Both halves are idempotent.
        """
        streams = [i.sentences for i in items if getattr(i, 'sentences', None) is not None]
        if not streams:
            return
        for stream in streams:
            abandon = getattr(stream, 'abandon', None)
            if abandon is None:
                continue
            try:
                abandon()
            except Exception as e:  # noqa: BLE001 — never block a skip
                logger.debug(f"Could not abandon streamed reply: {e}")
        self.utterances_abandoned += len(streams)
        logger.info(f"Abandoned {len(streams)} streamed utterance(s) on {reason}")

        async def close_all():
            for stream in streams:
                aclose = getattr(stream, 'aclose', None)
                if aclose is None:
                    continue
                try:
                    await aclose()
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"Error closing abandoned sentence stream: {e}")

        self._abandon_seq += 1
        try:
            get_global_registry().create_task(
                close_all(), name=f"audio_abandon_utterance_{self._abandon_seq}")
        except Exception as e:  # noqa: BLE001 — no loop (e.g. sync shutdown path)
            logger.debug(f"Deferred sentence-stream cleanup skipped: {e}")

    def _drain_queue(self, reason: str) -> None:
        """Clear the pending queue, releasing any streamed replies in it."""
        if not self.queue:
            return
        dropped, self.queue = list(self.queue), []
        self._abandon_items(dropped, reason)

    def skip(self) -> None:
        """
        Skip audio: interrupt the clip currently playing AND drop what's queued.

        Sets the skip flag (read between chunks in the playback worker, so the
        current clip stops within one chunk ~170ms) and clears the pending
        queue. The flag is auto-cleared when the next clip starts.
        """
        self._skip_playback = True
        if self.current_item and self.current_item.utterance:
            self.current_item.utterance.skip()  # drop the rest of the streamed reply too
        self._drain_queue("skip")
        logger.info("Skip requested: interrupting current clip and clearing queue")

    def cancel_utterance(self, player: Any) -> bool:
        """Abandon one streamed reply, wherever it currently is.

        Used when the caller has stopped waiting (timeout or cancellation):
        the utterance must not be played afterwards, or the streamer hears a
        reply whose text was never posted -- or worse, hears it twice once a
        blocking fallback is generated.
        """
        if player is None:
            return False
        if self.current_item is not None and self.current_item.utterance is player:
            player.skip()
            self._skip_playback = True
            self._abandon_items([self.current_item], "cancel (playing)")
            return True
        for i, item in enumerate(self.queue):
            if item.utterance is player:
                del self.queue[i]
                self._abandon_items([item], "cancel (queued)")
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
            'processing': self.processing,
            'utterances_played': self.utterances_played,
            'last_time_to_first_audio': self.last_time_to_first_audio,
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
        """Shutdown the audio queue.

        Single-owner with a shared outcome: the first caller performs the
        teardown, and every concurrent or later caller returns only when that
        teardown has finished -- "close once" alone let a second caller return
        while the first was still closing. Failures are reported to all
        callers; cancellation of the owner is never recorded as success.
        """
        if self._shutdown_future is not None:
            await asyncio.shield(self._shutdown_future)
            return
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._shutdown_future = fut
        try:
            failures = await self._shutdown_impl()
        except asyncio.CancelledError:
            self._shutdown_future = None   # retriable: closed handles are already None
            fut.cancel()
            raise
        except BaseException as e:
            fut.set_exception(e)
            fut.exception()
            raise
        if failures:
            err = RuntimeError("OptimizedAudioQueue shutdown: "
                               + "; ".join(f"{name} ({type(e).__name__}: {e})" for name, e in failures))
            fut.set_exception(err)
            fut.exception()
            raise err
        fut.set_result(True)

    async def _shutdown_impl(self) -> List[Tuple[str, BaseException]]:
        """Order matters: stop the processor first (it may still be reading
        the cache or writing the stream), release streamed waiters, then the
        device, then the cache last. Every handle is cleared before it is
        closed so nothing can be closed twice; every step is attempted."""
        failures: List[Tuple[str, BaseException]] = []
        logger.info("Shutting down OptimizedAudioQueue...")

        # 1. Stop processing before touching anything it uses. The handle is
        # cleared only once the processor has actually finished: if the owner
        # is cancelled while the processor is still cleaning up, a retried
        # shutdown must wait for it again rather than tear down under it.
        if self.processing_task is not None:
            await cancel_and_wait(self.processing_task, what="audio processor")
            self.processing_task = None
        self._skip_playback = True

        # 2. Release anyone waiting on a streamed reply
        if self.current_item is not None and self.current_item.utterance:
            self.current_item.utterance.skip()
            self._abandon_items([self.current_item], "shutdown")
        self._drain_queue("shutdown")

        # 3. Close the output device
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception as e:  # noqa: BLE001 — device may already be gone
                logger.warning(f"Error closing audio stream: {e}")
                failures.append(("stream", e))

        pa, self.pyaudio = self.pyaudio, None
        if pa is not None:
            try:
                pa.terminate()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Error terminating PyAudio: {e}")
                failures.append(("pyaudio", e))

        # 4. Cache last: nothing can use it any more
        try:
            await self.cache.close()
            logger.info("Closed TTS cache database")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Error closing TTS cache: {e}")
            failures.append(("cache", e))

        logger.info("OptimizedAudioQueue shutdown complete"
                    + (f" with {len(failures)} failure(s)" if failures else ""))
        return failures

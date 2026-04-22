"""TTS Service with SINGLE PyAudio instance - CRITICAL for audio stability."""
import asyncio
import pyaudio
from typing import Optional
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class TTSService:
    """Single PyAudio instance - NEVER create another!
    
    This is the ONLY place PyAudio should be instantiated in the entire application.
    Multiple instances cause audio distortion and overlap issues.
    """
    
    # Critical configuration - DO NOT CHANGE
    SAMPLE_RATE = 24000  # OpenAI TTS output
    CHANNELS = 1         # Mono
    FORMAT = pyaudio.paInt16  # 16-bit PCM
    CHUNK_SIZE = 2048
    
    def __init__(self, task_registry=None):
        """Initialize the SINGLE PyAudio instance."""
        # ONE PyAudio instance for entire application
        self.pa = pyaudio.PyAudio()
        self.stream = None
        self.task_registry = task_registry
        self.audio_queue = asyncio.Queue(maxsize=10)
        self._is_playing = False
        self._lock = asyncio.Lock()
        
        # OpenAI client for TTS
        self.openai_client = AsyncOpenAI()
        
        logger.info("TTSService initialized with SINGLE PyAudio instance")
        
    async def synthesize(self, text: str, voice: str = "nova") -> bytes:
        """Convert text to speech using OpenAI.
        
        Args:
            text: Text to synthesize
            voice: OpenAI voice model (nova, alloy, echo, fable, onyx, shimmer)
            
        Returns:
            Raw PCM audio bytes
        """
        try:
            response = await self.openai_client.audio.speech.create(
                model="tts-1",
                voice=voice,
                input=text,
                response_format="pcm"  # Raw PCM, no container
            )
            
            # Get raw audio bytes
            audio_content = response.content
            logger.debug(f"Synthesized {len(audio_content)} bytes of audio")
            return audio_content
            
        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            raise
            
    async def play_audio(self, audio_bytes: bytes):
        """Play audio through single PyAudio stream.
        
        CRITICAL: Sequential playback only to prevent overlap.
        """
        async with self._lock:
            if self._is_playing:
                # Queue it - never parallel playback
                logger.debug("Audio already playing, queueing")
                await self.audio_queue.put(audio_bytes)
                return
                
            self._is_playing = True
            
        try:
            # Strip 44-byte WAV header from FIRST chunk only
            if len(audio_bytes) > 44 and audio_bytes[:4] == b'RIFF':
                logger.debug("Stripping WAV header")
                audio_bytes = audio_bytes[44:]
                
            # Open stream if needed
            if not self.stream or not self.stream.is_active():
                logger.debug("Opening audio stream")
                self.stream = self.pa.open(
                    format=self.FORMAT,
                    channels=self.CHANNELS,
                    rate=self.SAMPLE_RATE,
                    output=True,
                    frames_per_buffer=self.CHUNK_SIZE
                )
                
            # Play audio in chunks
            for i in range(0, len(audio_bytes), self.CHUNK_SIZE):
                chunk = audio_bytes[i:i + self.CHUNK_SIZE]
                if len(chunk) < self.CHUNK_SIZE:
                    # Pad last chunk with silence
                    chunk += b'\x00' * (self.CHUNK_SIZE - len(chunk))
                self.stream.write(chunk)
                
            logger.debug("Audio playback completed")
            
        except Exception as e:
            logger.error(f"Audio playback failed: {e}")
            raise
            
        finally:
            self._is_playing = False
            
            # Process queue if not empty
            if not self.audio_queue.empty():
                next_audio = await self.audio_queue.get()
                logger.debug("Playing queued audio")
                await self.play_audio(next_audio)
                
    async def speak(self, text: str, voice: str = "nova") -> None:
        """Synthesize and play text.
        
        Convenience method that combines synthesis and playback.
        """
        try:
            logger.info(f"Speaking: {text[:50]}...")
            audio_bytes = await self.synthesize(text, voice)
            await self.play_audio(audio_bytes)
        except Exception as e:
            logger.error(f"Failed to speak text: {e}")
            raise
            
    def cleanup(self):
        """Cleanup PyAudio - call on shutdown."""
        logger.info("Cleaning up TTSService")
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            
        self.pa.terminate()
        logger.info("TTSService cleanup complete")
        
    async def get_queue_size(self) -> int:
        """Get number of audio clips in queue."""
        return self.audio_queue.qsize()
        
    async def clear_queue(self):
        """Clear the audio queue."""
        while not self.audio_queue.empty():
            await self.audio_queue.get()
        logger.info("Audio queue cleared")
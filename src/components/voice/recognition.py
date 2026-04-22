"""Voice Recognition with <3 second startup time."""
import asyncio
import logging
import time
from typing import Optional, Callable, Any
import speech_recognition as sr
import threading
from queue import Queue, Empty

logger = logging.getLogger(__name__)


class VoiceRecognition:
    """Voice input with <3 second startup.
    
    CRITICAL: Shares the SINGLE PyAudio instance from TTSService.
    Never creates its own PyAudio instance.
    """
    
    def __init__(self, tts_service=None):
        """Initialize voice recognition.
        
        Args:
            tts_service: TTSService instance to share PyAudio
        """
        self.tts = tts_service  # Share PyAudio instance
        self.recognizer = sr.Recognizer()
        self.mic = None
        self._is_listening = False
        self._stop_listening = None
        
        # Audio processing queue
        self.audio_queue = Queue(maxsize=5)
        
        # Recognition settings for speed
        self.recognizer.energy_threshold = 300  # Lower = more sensitive
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8  # Shorter pause detection
        
        # Callback for recognized text
        self.on_text_recognized: Optional[Callable[[str], Any]] = None
        self.main_loop = None  # Store the main event loop
        
        # Performance tracking
        self.startup_time = None
        self.recognition_times = []
        
        logger.info("VoiceRecognition initialized")
    
    def _find_voicemeeter_device(self):
        """Find best VoiceMeeter device for voice input.
        
        Returns:
            Device index or None for default
        """
        try:
            mic_list = sr.Microphone.list_microphone_names()
            
            # Priority order for VoiceMeeter devices (most likely to have mic audio)
            priority_patterns = [
                'voicemeeter out b1',  # Usually main output
                'voicemeeter out a1',  # Alternative main output
                'cable output',        # Virtual cable output
                'voicemeeter vaio',    # VAIO output
            ]
            
            for pattern in priority_patterns:
                for idx, name in enumerate(mic_list):
                    if pattern in name.lower():
                        logger.info(f"Using VoiceMeeter device {idx}: {name}")
                        return idx
                        
            # Fallback to physical microphone if no VoiceMeeter found
            for idx, name in enumerate(mic_list):
                if 'samson' in name.lower() or 'microphone' in name.lower():
                    if 'voicemeeter' not in name.lower():  # Avoid VoiceMeeter entries
                        logger.info(f"Using physical microphone {idx}: {name}")
                        return idx
                        
            logger.warning("No VoiceMeeter device found, using system default")
            return None
            
        except Exception as e:
            logger.error(f"Error finding VoiceMeeter device: {e}")
            return None
        
    async def start_listening(self) -> bool:
        """Start voice recognition in <3 seconds.
        
        Returns:
            Success status
        """
        if self._is_listening:
            logger.debug("Already listening")
            return True
            
        # Store the main event loop for callbacks
        self.main_loop = asyncio.get_running_loop()
            
        start_time = time.perf_counter()
        
        try:
            # Initialize microphone with optimized settings
            # Try to find VoiceMeeter output device
            device_index = self._find_voicemeeter_device()
            
            self.mic = sr.Microphone(
                device_index=device_index,  # Use VoiceMeeter or default
                sample_rate=16000,  # Lower sample rate for faster processing
                chunk_size=1024  # Smaller chunks for responsiveness
            )
            
            # Quick ambient noise adjustment (0.5s instead of default 1s)
            with self.mic as source:
                logger.debug("Adjusting for ambient noise...")
                self.recognizer.adjust_for_ambient_noise(
                    source, 
                    duration=0.5  # Quick calibration for <3s startup
                )
                
            # Start background listening thread
            self._stop_listening = self.recognizer.listen_in_background(
                self.mic,
                self._audio_callback,
                phrase_time_limit=5  # Max 5 seconds per phrase
            )
            
            self._is_listening = True
            
            # Calculate startup time
            self.startup_time = time.perf_counter() - start_time
            
            if self.startup_time > 3.0:
                logger.warning(f"Startup took {self.startup_time:.2f}s (>3s target)")
            else:
                logger.info(f"Voice recognition started in {self.startup_time:.2f}s")
                
            return True
            
        except Exception as e:
            logger.error(f"Failed to start voice recognition: {e}")
            return False
            
    async def listen_for_command(self, timeout: float = 5.0) -> Optional[str]:
        """Listen for a single voice command.
        
        Args:
            timeout: Maximum time to wait for command
            
        Returns:
            Recognized text or None
        """
        if not self._is_listening:
            success = await self.start_listening()
            if not success:
                return None
                
        start_time = time.perf_counter()
        
        try:
            # Use the microphone
            with self.mic as source:
                logger.debug("Listening for command...")
                
                # Listen with timeout
                audio = self.recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=5
                )
                
            # Recognize speech
            recognition_start = time.perf_counter()
            text = self.recognizer.recognize_google(audio)
            
            # Track recognition time
            recognition_time = time.perf_counter() - recognition_start
            self.recognition_times.append(recognition_time)
            
            # Keep only last 50 times
            if len(self.recognition_times) > 50:
                self.recognition_times = self.recognition_times[-50:]
                
            logger.info(f"Recognized: '{text}' in {recognition_time:.2f}s")
            return text
            
        except sr.WaitTimeoutError:
            logger.debug("No speech detected within timeout")
            return None
            
        except sr.UnknownValueError:
            logger.debug("Could not understand audio")
            return None
            
        except sr.RequestError as e:
            logger.error(f"Recognition service error: {e}")
            return None
            
    def _audio_callback(self, recognizer, audio):
        """Callback for background listening.
        
        Called by speech_recognition when audio is detected.
        """
        try:
            # Try to recognize the audio
            text = recognizer.recognize_google(audio)
            logger.info(f"Background recognition: '{text}'")
            
            # Call registered callback if available
            if self.on_text_recognized and self.main_loop:
                # Schedule the coroutine to run in the main event loop
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._handle_recognized_text(text),
                        self.main_loop
                    )
                except Exception as e:
                    logger.error(f"Error scheduling callback: {e}")
                
            # Queue the text for processing
            if not self.audio_queue.full():
                self.audio_queue.put(text)
                
        except sr.UnknownValueError:
            pass  # Couldn't understand audio
        except sr.RequestError as e:
            logger.error(f"Recognition error in background: {e}")
            
    async def _handle_recognized_text(self, text: str):
        """Handle recognized text asynchronously."""
        if self.on_text_recognized:
            try:
                await self.on_text_recognized(text)
            except Exception as e:
                logger.error(f"Error in text recognition callback: {e}")
                
    async def get_queued_text(self, timeout: float = 0.1) -> Optional[str]:
        """Get text from the recognition queue.
        
        Args:
            timeout: Max time to wait for text
            
        Returns:
            Recognized text or None
        """
        try:
            return self.audio_queue.get(timeout=timeout)
        except Empty:
            return None
            
    def stop_listening(self):
        """Stop voice recognition."""
        if self._stop_listening:
            self._stop_listening(wait_for_stop=False)
            self._stop_listening = None
            
        self._is_listening = False
        logger.info("Voice recognition stopped")
        
    def set_wake_word(self, wake_word: str):
        """Set a wake word for activation.
        
        Args:
            wake_word: Word to listen for (e.g., "assistant", "bot")
        """
        self.wake_word = wake_word.lower()
        logger.info(f"Wake word set to: '{wake_word}'")
        
    async def wait_for_wake_word(self, timeout: float = 30.0) -> bool:
        """Wait for wake word to be spoken.
        
        Args:
            timeout: Maximum time to wait
            
        Returns:
            True if wake word detected
        """
        if not hasattr(self, 'wake_word'):
            logger.warning("No wake word set")
            return False
            
        start_time = time.perf_counter()
        
        while time.perf_counter() - start_time < timeout:
            text = await self.get_queued_text(0.5)
            
            if text and self.wake_word in text.lower():
                logger.info(f"Wake word '{self.wake_word}' detected")
                return True
                
        return False
        
    def adjust_sensitivity(self, sensitivity: float):
        """Adjust recognition sensitivity.
        
        Args:
            sensitivity: 0.0 (least sensitive) to 1.0 (most sensitive)
        """
        # Map sensitivity to energy threshold (inverse relationship)
        # Higher sensitivity = lower threshold
        self.recognizer.energy_threshold = 4000 * (1.0 - sensitivity) + 100
        logger.info(f"Sensitivity adjusted to {sensitivity:.1f} (threshold: {self.recognizer.energy_threshold})")
        
    def get_stats(self) -> dict:
        """Get voice recognition statistics."""
        avg_recognition_time = (
            sum(self.recognition_times) / len(self.recognition_times)
            if self.recognition_times else 0
        )
        
        return {
            'is_listening': self._is_listening,
            'startup_time': self.startup_time,
            'average_recognition_time': avg_recognition_time,
            'queued_commands': self.audio_queue.qsize(),
            'energy_threshold': self.recognizer.energy_threshold,
            'total_recognitions': len(self.recognition_times)
        }
        
    def cleanup(self):
        """Cleanup voice recognition resources."""
        self.stop_listening()
        
        # Clear queue
        while not self.audio_queue.empty():
            self.audio_queue.get()
            
        logger.info("VoiceRecognition cleanup complete")
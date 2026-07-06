"""Voice Recognition with <3 second startup time."""
import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional, Callable, Any
import numpy as np
import speech_recognition as sr
import threading
from queue import Queue, Empty

logger = logging.getLogger(__name__)

# Local Whisper model cache (gitignored); shared with the install smoke test
_WHISPER_MODEL_DIR = Path(__file__).resolve().parents[3] / 'models' / 'whisper'

_cuda_dlls_registered = False


def _prepend_cuda_dll_dirs():
    """Put the nvidia pip wheels' DLL dirs on PATH for ctranslate2.

    ctranslate2 resolves cublas64_12.dll at inference time through the
    legacy PATH search on Windows -- os.add_dll_directory is not consulted
    for that load, so without this the first transcription raises
    'Library cublas64_12.dll is not found or cannot be loaded'.
    """
    global _cuda_dlls_registered
    if _cuda_dlls_registered:
        return
    site_packages = Path(np.__file__).resolve().parents[1]
    dll_dirs = [
        str(site_packages / 'nvidia' / sub / 'bin')
        for sub in ('cublas', 'cudnn', 'cuda_nvrtc')
    ]
    dll_dirs = [d for d in dll_dirs if Path(d).is_dir()]
    if dll_dirs:
        os.environ['PATH'] = os.pathsep.join(
            dll_dirs + [os.environ.get('PATH', '')]
        )
    _cuda_dlls_registered = True


class VoiceRecognition:
    """Voice input with <3 second startup.
    
    CRITICAL: Shares the SINGLE PyAudio instance from TTSService.
    Never creates its own PyAudio instance.
    """
    
    def __init__(self, tts_service=None, asr_engine: str = 'whisper',
                 whisper_model: str = 'small.en'):
        """Initialize voice recognition.

        Args:
            tts_service: TTSService instance to share PyAudio
            asr_engine: 'whisper' (local faster-whisper on GPU) or 'google'
                (legacy cloud ASR fallback)
            whisper_model: faster-whisper model name (small.en / medium.en)
        """
        self.tts = tts_service  # Share PyAudio instance
        self.recognizer = sr.Recognizer()
        self.mic = None
        self._is_listening = False
        self._stop_listening = None

        # ASR engine selection; whisper falls back to google if the model
        # fails to load (see _load_whisper_model)
        self.asr_engine = asr_engine
        self.whisper_model_name = whisper_model
        self._whisper_model = None
        
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
        
        logger.info(f"VoiceRecognition initialized (ASR engine: {self.asr_engine})")
    
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

    def _load_whisper_model(self) -> bool:
        """Load faster-whisper on CUDA. On any failure, fall back to Google.

        Returns:
            True if Whisper is loaded and active
        """
        try:
            _prepend_cuda_dll_dirs()
            from faster_whisper import WhisperModel

            t0 = time.perf_counter()
            self._whisper_model = WhisperModel(
                self.whisper_model_name,
                device='cuda',
                compute_type='int8_float16',
                download_root=str(_WHISPER_MODEL_DIR),
            )
            logger.info(
                f"Whisper model '{self.whisper_model_name}' loaded on CUDA "
                f"in {time.perf_counter() - t0:.2f}s"
            )
            return True

        except Exception as e:
            # Loud on purpose: the operator must never stream on Google
            # thinking they are on Whisper.
            logger.error(
                f"WHISPER MODEL LOAD FAILED -- falling back to Google ASR "
                f"for this session. Voice recognition is NOT running on "
                f"Whisper. Reason: {e}"
            )
            self._whisper_model = None
            self.asr_engine = 'google'
            return False

    def _transcribe(self, audio) -> str:
        """Transcribe one utterance with the active ASR engine.

        Same contract as recognize_google: returns the transcript, raises
        sr.UnknownValueError when nothing intelligible was heard.
        """
        if self.asr_engine == 'whisper' and self._whisper_model is not None:
            raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
            samples = (
                np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            )
            # vad_filter: Whisper hallucinates text on silence/noise
            # (verified: pure silence -> "You"); Silero VAD drops
            # non-speech segments in ~3ms before they hit the decoder
            segments, _info = self._whisper_model.transcribe(
                samples, language='en', beam_size=1, vad_filter=True
            )
            text = ' '.join(seg.text for seg in segments)
            # Downstream matching (trigger_match, voice-command substring
            # checks) was built against Google's punctuation-free
            # transcripts -- "hey, bud." must still contain "hey bud".
            text = re.sub(r'[,.?!;:"]', '', text)
            text = ' '.join(text.split())
            if not text:
                raise sr.UnknownValueError()
            return text

        return self.recognizer.recognize_google(audio)

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
            # Load the ASR model up front so the one-time CUDA warmup
            # happens at startup, not on the first mid-stream utterance.
            # Blocking load runs off the loop; falls back to google inside.
            if self.asr_engine == 'whisper':
                await asyncio.to_thread(self._load_whisper_model)

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
            text = self._transcribe(audio)
            
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
            # Try to recognize the audio (runs on the listener thread,
            # off the event loop -- Whisper inference included)
            text = self._transcribe(audio)
            logger.info(f"Background recognition: '{text}'")
            
            # Exactly ONE delivery path per utterance: with a callback
            # registered, also queueing the text hands it to any pull-based
            # consumer as well and the bot processes it twice.
            if self.on_text_recognized and self.main_loop:
                # Schedule the coroutine to run in the main event loop
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._handle_recognized_text(text),
                        self.main_loop
                    )
                except Exception as e:
                    logger.error(f"Error scheduling callback: {e}")
            elif not self.audio_queue.full():
                # No callback: queue for pull-based consumers (get_queued_text)
                self.audio_queue.put(text)
                
        except sr.UnknownValueError:
            pass  # Couldn't understand audio
        except sr.RequestError as e:
            logger.error(f"Recognition error in background: {e}")
        except Exception as e:
            # A transient ASR failure (e.g. CUDA hiccup) must not kill the
            # listener thread -- log and keep listening
            logger.error(f"ASR error in background: {e}")
            
    async def _handle_recognized_text(self, text: str):
        """Handle recognized text asynchronously."""
        if self.on_text_recognized:
            try:
                await self.on_text_recognized(text)
            except Exception as e:
                logger.error(f"Error in text recognition callback: {e}")
                
    async def get_queued_text(self, timeout: float = 0.1) -> Optional[str]:
        """Get text from the recognition queue.

        Never blocks the event loop: the blocking queue.Queue.get runs in a
        worker thread. (A previous version called it inline from this async
        method, stalling the loop for up to `timeout` per call -- the chronic
        ~240ms/1.24s loop-lag source when polled with timeout=0.5.)

        Args:
            timeout: Max time to wait for text

        Returns:
            Recognized text or None
        """
        try:
            return await asyncio.to_thread(self.audio_queue.get, True, timeout)
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
            'asr_engine': self.asr_engine,
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
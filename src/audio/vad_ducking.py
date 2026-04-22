#!/usr/bin/env python3
"""
VAD (Voice Activity Detection) Ducking System
Automatically lowers bot TTS volume when streamer is speaking for natural interrupts
"""

import asyncio
import logging
import numpy as np
import pyaudio
import threading
import time
from typing import Optional, Callable
from datetime import datetime, timedelta
import queue

logger = logging.getLogger(__name__)

class VADDucking:
    """
    Voice Activity Detection with automatic TTS ducking
    
    Features:
    - Real-time microphone monitoring
    - Configurable sensitivity and timing
    - Smooth volume transitions
    - Integration with TTS audio queue
    """
    
    def __init__(
        self,
        audio_queue,
        mic_device_index: Optional[int] = None,
        sample_rate: int = 16000,
        chunk_size: int = 1024,
        sensitivity: float = 0.3,
        duck_level: float = 0.1,
        fade_time: float = 0.2,
        hold_time: float = 0.5
    ):
        """
        Initialize VAD ducking system
        
        Args:
            audio_queue: TTS audio queue to duck
            mic_device_index: Microphone device index (None = default)
            sample_rate: Audio sample rate
            chunk_size: Audio chunk size for processing
            sensitivity: Voice detection sensitivity (0.0-1.0)
            duck_level: Volume level when ducking (0.0-1.0)
            fade_time: Time to fade in/out (seconds)
            hold_time: Time to hold duck after voice stops (seconds)
        """
        self.audio_queue = audio_queue
        self.mic_device_index = mic_device_index
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.sensitivity = sensitivity
        self.duck_level = duck_level
        self.fade_time = fade_time
        self.hold_time = hold_time
        
        # Audio processing
        self.pyaudio = None
        self.stream = None
        self.running = False
        
        # VAD state
        self.is_voice_active = False
        self.last_voice_time = datetime.now()
        self.current_volume = 1.0
        self.target_volume = 1.0
        
        # Threading
        self.vad_thread = None
        self.fade_thread = None
        self.audio_buffer = queue.Queue(maxsize=10)
        
        # Callbacks
        self.on_voice_start: Optional[Callable] = None
        self.on_voice_stop: Optional[Callable] = None
        
        # Performance tracking
        self.voice_detections = 0
        self.false_positives = 0
        
    def initialize(self) -> bool:
        """Initialize audio system and find microphone"""
        try:
            self.pyaudio = pyaudio.PyAudio()
            
            # Find microphone device
            if self.mic_device_index is None:
                self.mic_device_index = self._find_microphone()
                
            if self.mic_device_index is None:
                logger.error("No suitable microphone found")
                return False
                
            # Get device info
            device_info = self.pyaudio.get_device_info_by_index(self.mic_device_index)
            logger.info(f"Using microphone: {device_info['name']} (index: {self.mic_device_index})")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize VAD ducking: {e}")
            return False
            
    def _find_microphone(self) -> Optional[int]:
        """Find the best microphone device"""
        try:
            # Look for specific microphone patterns
            preferred_mics = [
                "samson q2u",
                "blue yeti", 
                "audio-technica",
                "shure",
                "rode",
                "voicemeeter"
            ]
            
            device_count = self.pyaudio.get_device_count()
            best_device = None
            best_score = -1
            
            for i in range(device_count):
                try:
                    info = self.pyaudio.get_device_info_by_index(i)
                    
                    # Skip output devices
                    if info['maxInputChannels'] == 0:
                        continue
                        
                    name_lower = info['name'].lower()
                    
                    # Check for preferred microphones
                    score = 0
                    for mic in preferred_mics:
                        if mic in name_lower:
                            score += 10
                            
                    # Prefer devices with "microphone" in name
                    if "microphone" in name_lower:
                        score += 5
                        
                    # Prefer higher channel count (stereo mics)
                    score += info['maxInputChannels']
                    
                    if score > best_score:
                        best_score = score
                        best_device = i
                        
                    logger.debug(f"Mic {i}: {info['name']} (score: {score})")
                    
                except:
                    continue
                    
            return best_device
            
        except Exception as e:
            logger.error(f"Error finding microphone: {e}")
            return None
            
    def start_monitoring(self) -> bool:
        """Start VAD monitoring"""
        if self.running:
            return True
            
        try:
            # Open microphone stream
            self.stream = self.pyaudio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.mic_device_index,
                frames_per_buffer=self.chunk_size
            )
            
            self.running = True
            
            # Start VAD processing thread
            self.vad_thread = threading.Thread(target=self._vad_loop, daemon=True)
            self.vad_thread.start()
            
            # Start volume fading thread
            self.fade_thread = threading.Thread(target=self._fade_loop, daemon=True)
            self.fade_thread.start()
            
            logger.info("VAD ducking started successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start VAD monitoring: {e}")
            return False
            
    def stop_monitoring(self):
        """Stop VAD monitoring"""
        self.running = False
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
            
        if self.vad_thread:
            self.vad_thread.join(timeout=1.0)
            
        if self.fade_thread:
            self.fade_thread.join(timeout=1.0)
            
        # Restore full volume
        self.current_volume = 1.0
        self.target_volume = 1.0
        if self.audio_queue:
            self.audio_queue.set_volume(1.0)
            
        logger.info("VAD ducking stopped")
        
    def _vad_loop(self):
        """Main VAD processing loop"""
        while self.running:
            try:
                # Read audio data
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                
                # Convert to numpy array
                audio_data = np.frombuffer(data, dtype=np.int16)
                
                # Simple energy-based VAD
                energy = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
                normalized_energy = min(energy / 32768.0, 1.0)
                
                # Voice detection
                voice_detected = normalized_energy > self.sensitivity
                
                # Update state
                now = datetime.now()
                
                if voice_detected:
                    if not self.is_voice_active:
                        # Voice started
                        self.is_voice_active = True
                        self.target_volume = self.duck_level
                        self.voice_detections += 1
                        
                        if self.on_voice_start:
                            self.on_voice_start()
                            
                        logger.debug(f"Voice detected (energy: {normalized_energy:.3f})")
                        
                    self.last_voice_time = now
                    
                else:
                    # Check if voice stopped (with hold time)
                    if self.is_voice_active:
                        time_since_voice = (now - self.last_voice_time).total_seconds()
                        
                        if time_since_voice > self.hold_time:
                            # Voice stopped
                            self.is_voice_active = False
                            self.target_volume = 1.0
                            
                            if self.on_voice_stop:
                                self.on_voice_stop()
                                
                            logger.debug("Voice stopped, restoring volume")
                            
                # Small delay to prevent excessive CPU usage
                time.sleep(0.01)
                
            except Exception as e:
                logger.error(f"Error in VAD loop: {e}")
                time.sleep(0.1)
                
    def _fade_loop(self):
        """Volume fading loop"""
        while self.running:
            try:
                if abs(self.current_volume - self.target_volume) > 0.01:
                    # Calculate fade step
                    fade_step = (self.target_volume - self.current_volume) / (self.fade_time / 0.02)
                    
                    # Apply fade step
                    self.current_volume += fade_step
                    
                    # Clamp to target if close enough
                    if abs(self.current_volume - self.target_volume) < abs(fade_step):
                        self.current_volume = self.target_volume
                        
                    # Apply volume to audio queue
                    if self.audio_queue and hasattr(self.audio_queue, 'set_volume'):
                        self.audio_queue.set_volume(self.current_volume)
                        
                time.sleep(0.02)  # 50 FPS for smooth fading
                
            except Exception as e:
                logger.error(f"Error in fade loop: {e}")
                time.sleep(0.1)
                
    def set_sensitivity(self, sensitivity: float):
        """Update voice detection sensitivity"""
        self.sensitivity = max(0.0, min(1.0, sensitivity))
        logger.info(f"VAD sensitivity set to {self.sensitivity}")
        
    def set_duck_level(self, level: float):
        """Update ducking level"""
        self.duck_level = max(0.0, min(1.0, level))
        logger.info(f"Duck level set to {self.duck_level}")
        
    def get_stats(self) -> dict:
        """Get VAD statistics"""
        return {
            'voice_active': self.is_voice_active,
            'current_volume': self.current_volume,
            'target_volume': self.target_volume,
            'voice_detections': self.voice_detections,
            'sensitivity': self.sensitivity,
            'duck_level': self.duck_level,
            'running': self.running
        }
        
    def shutdown(self):
        """Clean shutdown"""
        self.stop_monitoring()
        
        if self.pyaudio:
            self.pyaudio.terminate()
            self.pyaudio = None
"""
ResponseCoordinator - Manages chat and audio response timing
Based on PRD specifications for synchronized but non-blocking delivery
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, Callable, Tuple
from datetime import datetime, timedelta
from enum import Enum
import json
import os

from utils.task_registry import cancel_and_wait

logger = logging.getLogger(__name__)


class TimingMode(Enum):
    """Response timing modes"""
    SIMULTANEOUS = "simultaneous"
    CHAT_FIRST = "chat_first"
    VOICE_FIRST = "voice_first"


class ResponseCoordinator:
    """
    Manages chat and audio response timing for optimal user experience
    Implements PRD section 2.2 Response Coordination
    """
    
    # Timing mode configurations - returns (chat_delay, voice_delay)
    TIMING_MODES = {
        TimingMode.SIMULTANEOUS: lambda msg: (0, 0),
        TimingMode.CHAT_FIRST: lambda msg: (0, min(0.3 + len(msg) * 0.01, 1.5)),
        TimingMode.VOICE_FIRST: lambda msg: (0.5, 0)
    }
    
    def __init__(
        self,
        twitch_client: Any,
        audio_queue: Any,
        settings_path: str = "bot_settings.json"
    ):
        """
        Initialize ResponseCoordinator
        
        Args:
            twitch_client: TwitchClient instance for sending chat messages
            audio_queue: OptimizedAudioQueue instance for TTS
            settings_path: Path to bot settings file
        """
        self.twitch_client = twitch_client
        self.audio_queue = audio_queue
        self.settings_path = settings_path
        
        # Default configuration
        self.timing_mode = TimingMode.CHAT_FIRST
        self.dead_air_threshold = 300  # 5 minutes - much longer to avoid spam
        self.dead_air_enabled = True
        self.last_activity_time = datetime.now()
        self.startup_time = datetime.now()  # Track when we started
        
        # Dead air prevention - will be dynamically generated
        self.filler_messages = []
        self.filler_index = 0
        self.personality_engine = None  # Will be set by bot
        # Returns the situational context for a dead-air line (game,
        # session summary, the chat that preceded the lull). Set by the
        # bot; without it the filler is generated blind.
        self.context_provider = None
        self.fillers_sent = 0
        
        # Performance metrics
        self.response_count = 0
        self.total_chat_delay = 0
        self.total_voice_delay = 0
        
        # Load settings
        self._load_settings()
        
        # Start dead air prevention task
        self.dead_air_task = None
        
    def _load_settings(self) -> None:
        """Load settings from configuration file"""
        try:
            if os.path.exists(self.settings_path):
                with open(self.settings_path, 'r') as f:
                    settings = json.load(f)
                    
                # Load response coordination settings if present
                if 'response_coordination' in settings:
                    rc_settings = settings['response_coordination']
                    
                    # Load timing mode
                    mode_str = rc_settings.get('timing_mode', 'chat_first')
                    try:
                        self.timing_mode = TimingMode(mode_str)
                    except ValueError:
                        logger.warning(f"Invalid timing mode: {mode_str}, using default")
                        self.timing_mode = TimingMode.CHAT_FIRST
                    
                    # Load dead air settings
                    self.dead_air_threshold = rc_settings.get('dead_air_threshold', 60)
                    self.dead_air_enabled = rc_settings.get('dead_air_enabled', True)
                    
                    # Validate threshold range (1-300 seconds per PRD)
                    self.dead_air_threshold = max(1, min(300, self.dead_air_threshold))
                    
                    logger.info(f"Loaded response coordination settings: mode={self.timing_mode.value}, "
                              f"dead_air={self.dead_air_threshold}s")
                    
        except Exception as e:
            logger.error(f"Failed to load response coordination settings: {e}")
            # Continue with defaults
            
    async def coordinate_response(
        self,
        chat_msg: str,
        audio_task: Optional[Callable] = None,
        priority: str = "normal",
        is_mention: bool = False,
        is_voice: bool = False
    ) -> None:
        """
        Coordinate chat and audio response with optimal timing
        
        Args:
            chat_msg: The chat message to send
            audio_task: Optional async callable for TTS generation
            priority: Response priority ('low', 'normal', 'high', 'critical')
            is_mention: Whether this is a response to an @mention
            is_voice: Whether this is a response to voice input
        """
        try:
            start_time = time.perf_counter()
            
            # Update last activity time
            self.last_activity_time = datetime.now()
            
            # Priority system: @mentions and voice always get immediate response
            if is_mention or is_voice or priority in ['high', 'critical']:
                # Override to simultaneous for high priority
                chat_delay, voice_delay = 0, 0
                logger.debug(f"High priority response - using immediate timing")
            else:
                # Calculate delays based on timing mode
                delay_func = self.TIMING_MODES[self.timing_mode]
                chat_delay, voice_delay = delay_func(chat_msg)
                
            # Log timing decision
            logger.debug(f"Response timing: mode={self.timing_mode.value}, "
                        f"chat_delay={chat_delay:.2f}s, voice_delay={voice_delay:.2f}s")
            
            # Create tasks for parallel execution
            tasks = []
            
            # Chat task with delay
            async def send_chat():
                if chat_delay > 0:
                    await asyncio.sleep(chat_delay)
                if self.twitch_client and self.twitch_client.is_connected():
                    await self.twitch_client.send_message(chat_msg)
                    logger.debug(f"Chat sent after {chat_delay:.2f}s delay")
                    
            tasks.append(asyncio.create_task(send_chat()))
            
            # Audio task with delay (if provided)
            if audio_task:
                logger.info(f"Audio task provided for message: '{chat_msg[:50]}...'")
                async def send_audio():
                    if voice_delay > 0:
                        await asyncio.sleep(voice_delay)
                    if self.audio_queue:
                        # Call the audio task (e.g., queue_audio)
                        logger.info(f"Calling audio task for: '{chat_msg[:50]}...'")
                        await audio_task()
                        logger.info(f"Audio task completed after {voice_delay:.2f}s delay")
                    else:
                        logger.warning("No audio queue available in ResponseCoordinator!")
                        
                tasks.append(asyncio.create_task(send_audio()))
            else:
                logger.info(f"No audio task for message: '{chat_msg[:50]}...'")
            
            # Execute tasks in parallel (non-blocking)
            await asyncio.gather(*tasks, return_exceptions=True)
            
            # Update metrics
            self.response_count += 1
            self.total_chat_delay += chat_delay
            self.total_voice_delay += voice_delay
            
            # Log performance
            elapsed = (time.perf_counter() - start_time) * 1000
            logger.info(f"Response coordinated in {elapsed:.2f}ms "
                       f"(chat: {chat_delay:.2f}s, voice: {voice_delay:.2f}s)")
            
        except Exception as e:
            logger.error(f"Error coordinating response: {e}", exc_info=True)
            # Fallback: try to send without delays
            try:
                if self.twitch_client and self.twitch_client.is_connected():
                    await self.twitch_client.send_message(chat_msg)
                if audio_task and self.audio_queue:
                    await audio_task()
            except Exception as fallback_error:
                logger.error(f"Fallback response failed: {fallback_error}")
                
    async def coordinate_streamed_response(
        self,
        reply: Any,
        priority: str = "normal",
        is_mention: bool = False,
        is_voice: bool = False,
        user: Optional[str] = None,
        *,
        prefetch_depth: int = 2,
        timeout: float = 90.0,
    ) -> Optional[str]:
        """
        Deliver a StreamedReply: audio starts on the first sentence, chat is
        posted once the full text is known. Timing modes don't apply — the
        chat text doesn't exist until generation ends, and the whole point is
        to not wait for that before speaking.

        Returns the chat text that was sent (None if nothing was produced).
        """
        self.last_activity_time = datetime.now()
        start_time = time.perf_counter()
        player = None
        try:
            if self.audio_queue:
                player = await self.audio_queue.queue_utterance(
                    reply.sentences, priority=priority, user=user,
                    is_mention=is_mention, prefetch_depth=prefetch_depth,
                )
            else:
                # Nobody will pull sentences otherwise; drain so the text finalizes
                async for _ in reply.sentences:
                    pass

            text = await asyncio.wait_for(reply.wait(), timeout=timeout)
            if text and self.twitch_client and self.twitch_client.is_connected():
                await self.twitch_client.send_message(text)

            self.response_count += 1
            elapsed = (time.perf_counter() - start_time) * 1000
            logger.info(f"Streamed response coordinated in {elapsed:.0f}ms "
                        f"(fell_back={getattr(reply, 'fell_back', False)}, aborted={getattr(reply, 'aborted', False)})")
            return text
        except asyncio.TimeoutError:
            # We have stopped waiting, so this utterance must stop existing:
            # left queued it would speak minutes later, and alongside the
            # caller's blocking fallback the streamer would hear both.
            logger.error("Streamed response timed out waiting for completion; "
                         "abandoning the utterance so it cannot play later")
            self._abandon_streamed(reply, player)
            return None
        except asyncio.CancelledError:
            self._abandon_streamed(reply, player)
            raise
        except Exception as e:
            logger.error(f"Error coordinating streamed response: {e}", exc_info=True)
            self._abandon_streamed(reply, player)
            return reply.text

    def _abandon_streamed(self, reply: Any, player: Any) -> None:
        """Stop a streamed reply from playing and release its waiters."""
        try:
            if player is not None and self.audio_queue is not None:
                cancel = getattr(self.audio_queue, 'cancel_utterance', None)
                if cancel is not None:
                    cancel(player)
                else:  # older queue: at least stop the player itself
                    player.skip()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Could not cancel streamed utterance: {e}")
        try:
            stream = getattr(reply, 'sentences', None)
            if stream is not None and hasattr(stream, 'abandon'):
                stream.abandon()
            elif hasattr(reply, 'abandon'):
                reply.abandon()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Could not abandon streamed reply: {e}")

    async def start_dead_air_prevention(self) -> None:
        """Start the dead air prevention task"""
        if self.dead_air_task:
            return  # Already running
            
        self.dead_air_task = asyncio.create_task(self._dead_air_monitor())
        logger.info(f"Dead air prevention started (threshold: {self.dead_air_threshold}s)")
        
    async def stop_dead_air_prevention(self) -> None:
        """Stop the dead air prevention task"""
        if self.dead_air_task:
            await cancel_and_wait(self.dead_air_task, what="dead-air monitor")
            self.dead_air_task = None
            logger.info("Dead air prevention stopped")
            
    async def _dead_air_monitor(self) -> None:
        """Monitor for dead air and inject filler content"""
        while True:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds
                
                if not self.dead_air_enabled:
                    continue
                
                # Add startup grace period - don't trigger dead air for first 10 minutes
                startup_grace_period = 600  # 10 minutes
                time_since_startup = (datetime.now() - self.startup_time).total_seconds()
                
                if time_since_startup < startup_grace_period:
                    continue  # Skip dead air detection during startup grace period
                    
                # Calculate time since last activity
                time_since_activity = (datetime.now() - self.last_activity_time).total_seconds()
                
                if time_since_activity >= self.dead_air_threshold:
                    # Generate dynamic filler using personality engine if available
                    if self.personality_engine:
                        try:
                            # Generate contextual dead air filler
                            context = {
                                'type': 'dead_air',
                                'time_since_activity': time_since_activity,
                                'stream_duration': time_since_startup
                            }
                            if self.context_provider is not None:
                                try:
                                    context.update(self.context_provider() or {})
                                except Exception as e:  # noqa: BLE001
                                    logger.debug(f"Dead-air context unavailable: {e}")
                            
                            response = await self.personality_engine.generate_response(
                                message="[DEAD_AIR_FILLER]",
                                context=context,
                                user="system"
                            )
                            
                            if response and response.get('text'):
                                filler_msg = response['text']
                            else:
                                # Fallback to simple message
                                filler_msg = "chat seems quiet"
                        except Exception as e:
                            logger.error(f"Error generating dynamic filler: {e}")
                            filler_msg = "anyone there"
                    else:
                        # Ultra simple fallback
                        filler_msg = "hello"
                    
                    self.fillers_sent += 1
                    logger.info(f"Dead air detected ({time_since_activity:.0f}s), "
                                f"sending filler: {filler_msg[:60]!r}")
                    
                    # Send filler with low priority
                    await self.coordinate_response(
                        chat_msg=filler_msg,
                        audio_task=lambda: self.audio_queue.queue_audio(
                            text=filler_msg,
                            priority='low'
                        ) if self.audio_queue else None,
                        priority='low'
                    )
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in dead air monitor: {e}")
                await asyncio.sleep(30)  # Back off on error
                
    def switch_timing_mode(self, mode: str) -> bool:
        """
        Switch to a different timing mode
        
        Args:
            mode: The timing mode name ('simultaneous', 'chat_first', 'voice_first')
            
        Returns:
            True if mode was switched successfully
        """
        try:
            new_mode = TimingMode(mode)
            self.timing_mode = new_mode
            logger.info(f"Switched timing mode to: {mode}")
            return True
        except ValueError:
            logger.error(f"Invalid timing mode: {mode}")
            return False
            
    def set_dead_air_threshold(self, seconds: int) -> None:
        """
        Set the dead air threshold
        
        Args:
            seconds: Threshold in seconds (1-300)
        """
        # Clamp to valid range per PRD
        self.dead_air_threshold = max(1, min(300, seconds))
        logger.info(f"Dead air threshold set to: {self.dead_air_threshold}s")
        
    def get_stats(self) -> Dict[str, Any]:
        """Get coordinator statistics"""
        avg_chat_delay = (self.total_chat_delay / self.response_count 
                         if self.response_count > 0 else 0)
        avg_voice_delay = (self.total_voice_delay / self.response_count 
                          if self.response_count > 0 else 0)
        time_since_activity = (datetime.now() - self.last_activity_time).total_seconds()
        
        return {
            'timing_mode': self.timing_mode.value,
            'response_count': self.response_count,
            'avg_chat_delay': avg_chat_delay,
            'avg_voice_delay': avg_voice_delay,
            'dead_air_enabled': self.dead_air_enabled,
            'dead_air_threshold': self.dead_air_threshold,
            'time_since_activity': time_since_activity,
            'dead_air_active': time_since_activity >= self.dead_air_threshold
        }
        
    async def reload_settings(self) -> None:
        """Reload settings from configuration file"""
        self._load_settings()
        logger.info("Response coordination settings reloaded")
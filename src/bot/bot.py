"""
Core TalkBot implementation with TaskRegistry pattern
"""

import asyncio
import logging
import os
import sys
import time
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import signal

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.task_registry import TaskRegistry
from services.service_registry import ServiceRegistry
from memory.single_memory_system import SingleMemorySystem
from memory.resilient_memory_system import ResilientMemorySystem
from twitch.twitch_client import TwitchClient
from audio.optimized_queue import OptimizedAudioQueue
from personality.personality_engine import PersonalityEngine
from api.websocket_manager import WebSocketManager
from components.voice.recognition import VoiceRecognition
from components.voice.trigger_match import match_hey_trigger
from bot.optimized_context_builder import OptimizedContextBuilder
from bot.channel_chat_buffer import ChannelChatBuffer
from core.bot_state import BotState
from core.network_resilience import get_resilience
from bot.response_coordinator import ResponseCoordinator
from twitch.eventsub_websocket import EventSubWebSocket
from features.ad_announcer import AdAnnouncer
from features.event_announcer import EventAnnouncer
from monitoring.metrics_collector import MetricsCollector
from monitoring.loop_lag_monitor import LoopLagMonitor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def strip_mentions_for_tts(text: str) -> str:
    """Remove @mentions from text for TTS (sounds unnatural when read aloud)."""
    return re.sub(r'@\w+\s*', '', text).strip()

class TalkBot:
    """
    Main bot class implementing all documented fixes
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize TalkBot with configuration
        
        Args:
            config: Configuration dictionary with API keys and settings
        """
        self.config = config  # Keep for backward compatibility
        
        # Initialize BotState (PRD Section 3.3 - Single source of truth)
        self.state = BotState(
            streamer_id=config.get('TWITCH_CHANNEL', 'unknown'),
            voice_enabled=config.get('VOICE_INPUT_ENABLED', True),
            tts_enabled=config.get('TTS_ENABLED', True)
        )
        
        # Network resilience layer (PRD Section 1.3)
        self.resilience = get_resilience()
        
        self.running = False
        self.task_registry = TaskRegistry()
        self.service_registry = ServiceRegistry()
        
        # Core components (initialized in setup)
        self.memory_system: Optional[SingleMemorySystem] = None
        self.twitch_client: Optional[TwitchClient] = None
        self.audio_queue: Optional[OptimizedAudioQueue] = None
        self.personality_engine: Optional[PersonalityEngine] = None
        self.websocket_manager: Optional[WebSocketManager] = None
        self.voice_recognition: Optional[VoiceRecognition] = None
        self.raider_welcome = None  # Optional feature per PRD
        self.context_builder: Optional[OptimizedContextBuilder] = None  # PRD required component
        self.session_summarizer = None  # rolling 'earlier this stream' memory
        self.stream_info = None         # live category/title (features.stream_info)
        self.stream_recap = None        # post-stream recap counters (features.stream_recap)
        self.first_timer = None         # first-time chatter greeting policy (features.first_timer)
        self.current_game: Optional[str] = None
        # Sentence-streamed TTS (bot_settings.json -> tts_streaming). Off by default.
        self._tts_streaming: Dict[str, Any] = dict(self._TTS_STREAMING_DEFAULTS)
        self.response_coordinator: Optional[ResponseCoordinator] = None  # PRD critical component
        self.vad_ducking = None  # VAD ducking for natural interrupts
        self.realtime_backend = None  # VOICE_BACKEND=realtime (doc SS3)
        self.eventsub: Optional[EventSubWebSocket] = None  # EventSub for automatic ad detection
        self.ad_announcer: Optional[AdAnnouncer] = None  # Ad announcer
        self.api_server = None  # Embedded uvicorn server (API_ENABLED=true)

        # Metrics tracking (enabled for PRD compliance)
        self.metrics_collector: Optional[MetricsCollector] = None  # Metrics collection
        self.metrics_enabled = True  # Enable metrics for performance tracking
        
        # Performance metrics
        self.start_time = datetime.now()
        self.message_count = 0
        self.audio_count = 0
        self.response_times: List[float] = []
        
        # Voice input anti-spam
        self.last_voice_response = datetime.now()  # Initialize to now to enforce cooldown from start
        self.voice_cooldown_seconds = 5  # Balance between conversation and spam
        self.recent_voice_texts = []  # Track recent voice inputs for duplicate detection
        self.in_conversation = False  # Track if we're in active conversation
        self.conversation_timeout = 30  # End conversation after 30s of no interaction
        
        # Memory health tracking - degradation must never be silent
        self._memory_healthy = True

        # Event-loop lag probe. Blocking work on the loop (PyAudio writes,
        # numpy, sync I/O) starves latency-sensitive coroutines and, most
        # visibly, causes Twitch EventSub 4002 disconnects. This monitor
        # surfaces any stall >100ms so regressions are caught loudly.
        self._loop_lag_monitor: Optional[LoopLagMonitor] = None

        # Bot state
        self.muted = False  # Can be toggled via voice commands
        self.tts_enabled = True  # TTS on/off state
        self.last_response = None  # Track last response for repeat command
        self.voice_commands = None  # Will be initialized if voice enabled
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        asyncio.create_task(self.shutdown())
        
    _TTS_STREAMING_DEFAULTS = {'enabled': False, 'min_sentence_chars': 12, 'prefetch_depth': 2}

    def _apply_tts_streaming_settings(self, bot_settings: Dict[str, Any]) -> None:
        cfg = dict(self._TTS_STREAMING_DEFAULTS)
        cfg.update(bot_settings.get('tts_streaming') or {})
        if cfg != self._tts_streaming:
            logger.info(f"TTS streaming: {'ON' if cfg.get('enabled') else 'off'} "
                        f"(min_sentence_chars={cfg.get('min_sentence_chars')}, prefetch_depth={cfg.get('prefetch_depth')})")
        self._tts_streaming = cfg

    async def _load_bot_settings(self) -> None:
        """Load bot settings from configuration file"""
        try:
            settings_file = 'bot_settings.json'
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    bot_settings = json.load(f)

                # Update TTS settings if present
                if 'TTS_ENABLED' in bot_settings:
                    self.config['TTS_ENABLED'] = bot_settings['TTS_ENABLED']
                    logger.info(f"TTS_ENABLED set to {bot_settings['TTS_ENABLED']} from bot_settings.json")

                # Update voice settings if present
                if 'voice' in bot_settings:
                    self.config.update({
                        'TTS_VOICE': bot_settings['voice'].get('model', 'nova'),
                        'TTS_SPEED': bot_settings['voice'].get('speed', 1.0)
                    })

                # Update conversation settings if present
                if 'conversation' in bot_settings:
                    self.voice_cooldown_seconds = bot_settings['conversation'].get('cooldown_seconds', 5)
                    self.conversation_timeout = bot_settings['conversation'].get('conversation_timeout', 30)

                self._apply_tts_streaming_settings(bot_settings)

                logger.info(f"Loaded bot settings from {settings_file}")
            else:
                logger.info("No bot settings file found, using defaults")
        except Exception as e:
            logger.error(f"Failed to load bot settings: {e}")
            # Continue with defaults if loading fails
    
    async def _reload_settings(self) -> None:
        """Reload settings from configuration file and apply changes"""
        try:
            settings_file = 'bot_settings.json'
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    bot_settings = json.load(f)
                    
                # Update TTS settings if present  
                if 'TTS_ENABLED' in bot_settings:
                    self.config['TTS_ENABLED'] = bot_settings['TTS_ENABLED']
                    logger.info(f"TTS_ENABLED set to {bot_settings['TTS_ENABLED']} from bot_settings.json")
                    
                # Update voice settings if present
                if 'voice' in bot_settings:
                    self.config.update({
                        'TTS_VOICE': bot_settings['voice'].get('model', 'nova'),
                        'TTS_SPEED': bot_settings['voice'].get('speed', 1.0)
                    })
                    
                # Update conversation settings if present  
                if 'conversation' in bot_settings:
                    self.voice_cooldown_seconds = bot_settings['conversation'].get('cooldown_seconds', 5)
                    self.conversation_timeout = bot_settings['conversation'].get('conversation_timeout', 30)
                
                # Reload response coordinator settings if present
                if self.response_coordinator and 'response_coordination' in bot_settings:
                    await self.response_coordinator.reload_settings()
                
                # Update personality if present and personality engine is initialized
                if self.personality_engine and 'personality' in bot_settings and 'preset' in bot_settings['personality']:
                    preset_name = bot_settings['personality']['preset']
                    # Use asyncio to call the async method
                    success = await self.personality_engine.switch_personality_by_name(preset_name)
                    if success:
                        logger.info(f"Switched personality to: {preset_name}")
                    else:
                        logger.warning(f"Could not switch personality to: {preset_name}")

                # Pick up llm_model / streamer_name changes
                if self.personality_engine:
                    self.personality_engine.reload_llm_settings()

                self._apply_tts_streaming_settings(bot_settings)

                logger.info("Reloaded bot settings")
        except Exception as e:
            logger.error(f"Failed to reload bot settings: {e}")
        
    async def setup(self) -> None:
        """
        Initialize all bot components with proper error handling
        Implements startup optimizations
        """
        try:
            logger.info("Starting TalkBot setup...")
            
            # Mark bot as starting up
            self.state.startup_time = datetime.now()
            
            # Load bot settings if available
            await self._load_bot_settings()
            
            # Initialize memory system first (required by other components)
            logger.info("Initializing ResilientMemorySystem...")
            # Use ResilientMemorySystem for better database connection handling
            self.memory_system = ResilientMemorySystem(
                database_url=self.config.get('DATABASE_URL'),
                redis_url=self.config.get('REDIS_URL'),
                max_retries=3
            )
            await self.memory_system.initialize()
            self.service_registry.register('MemoryService', self.memory_system)

            # Startup gate: DB connectivity AND schema completeness.
            # Degradation is allowed (bot still runs) but never silent.
            self._memory_healthy, memory_reason = await self._check_memory_health()
            if not self._memory_healthy:
                self._log_memory_degraded(memory_reason)

            # Initialize ChannelChatBuffer for real-time conversational context
            logger.info("Initializing ChannelChatBuffer...")
            self.chat_buffer = ChannelChatBuffer(max_turns_per_channel=200)
            self.service_registry.register('ChatBuffer', self.chat_buffer)

            # Initialize OptimizedContextBuilder for <100ms performance (PRD requirement)
            logger.info("Initializing OptimizedContextBuilder...")
            self.context_builder = OptimizedContextBuilder(
                self.memory_system,
                chat_buffer=self.chat_buffer
            )
            self.service_registry.register('ContextBuilder', self.context_builder)

            # Initialize MetricsCollector for performance tracking (PRD requirement)
            if self.metrics_enabled:
                logger.info("Initializing MetricsCollector...")
                self.metrics_collector = MetricsCollector()
                self.service_registry.register('MetricsService', self.metrics_collector)

            # Ensure voice user exists in database
            await self._ensure_voice_user_exists()
            
            # Initialize personality engine
            logger.info("Initializing PersonalityEngine...")
            self.personality_engine = PersonalityEngine(
                memory_system=self.memory_system,
                openai_api_key=self.config.get('OPENAI_API_KEY')
            )
            await self.personality_engine.initialize()
            
            # Load all personalities from JSON file (already loaded in PersonalityEngine.__init__)
            # The PersonalityEngine automatically loads from all_personalities.json
            
            # Set personality from bot_settings.json if available
            if os.path.exists('bot_settings.json'):
                with open('bot_settings.json', 'r') as f:
                    bot_settings = json.load(f)
                    if 'personality' in bot_settings and 'preset' in bot_settings['personality']:
                        preset_name = bot_settings['personality']['preset']
                        # Use asyncio to call the async method
                        success = await self.personality_engine.switch_personality_by_name(preset_name)
                        if success:
                            logger.info(f"Set personality to: {preset_name}")
                        else:
                            logger.warning(f"Could not set personality to: {preset_name}")
            
            self.service_registry.register('PersonalityService', self.personality_engine)

            # Rolling session summary: folds chat the LLM can no longer see into
            # one bounded paragraph. Needs the engine's LLM client, so it's
            # attached to the context builder here rather than at construction.
            self._setup_session_summarizer()

            # Initialize TwitchTokenRefresher BEFORE TwitchClient for fresh tokens
            from twitch.token_refresher import TwitchTokenRefresher

            logger.info("Initializing TwitchTokenRefresher...")
            self.token_refresher = TwitchTokenRefresher(
                client_id=self.config.get('TWITCH_CLIENT_ID'),
                client_secret=self.config.get('TWITCH_CLIENT_SECRET'),
            )

            bot_account = self.config.get('TWITCH_BOT_USERNAME', 'elimist_').lower()
            channel = self.config.get('TWITCH_CHANNEL', 'cassova_').lower()

            self.token_refresher.register_account(
                account_name=bot_account,
                env_var_name='TWITCH_ACCESS_TOKEN',
                token_file_path=f'twitch_tokens_{bot_account}.txt'
            )
            self.token_refresher.register_account(
                account_name=channel,
                env_var_name='TWITCH_BROADCASTER_TOKEN',
                token_file_path=f'twitch_tokens_{channel}.txt'
            )

            # Refresh immediately so we start with fresh tokens. If refresh
            # fails, fall back to whatever's in .env (best-effort).
            logger.info("Performing initial token refresh...")
            refresh_results = await self.token_refresher.refresh_all()
            for account, success in refresh_results.items():
                if success:
                    logger.info(f"Initial token refresh succeeded for {account}")
                else:
                    logger.warning(
                        f"Initial token refresh FAILED for {account} — "
                        f"continuing with existing tokens from .env"
                    )

            # Re-read .env into self.config so updated values flow into the
            # rest of setup. Use os.environ refresh + dotenv reload pattern.
            from dotenv import load_dotenv
            load_dotenv(override=True)
            # Update self.config dict for any keys that changed
            self.config['TWITCH_ACCESS_TOKEN'] = os.getenv('TWITCH_ACCESS_TOKEN', self.config.get('TWITCH_ACCESS_TOKEN'))
            self.config['TWITCH_BROADCASTER_TOKEN'] = os.getenv('TWITCH_BROADCASTER_TOKEN', self.config.get('TWITCH_BROADCASTER_TOKEN'))

            # Initialize Twitch client (but connect async)
            logger.info("Initializing Twitch client...")
            self.twitch_client = TwitchClient(
                access_token=self.config['TWITCH_ACCESS_TOKEN'],
                client_id=self.config['TWITCH_CLIENT_ID'],
                channel=self.config['TWITCH_CHANNEL'],
                bot_username=self.config['TWITCH_BOT_USERNAME']
            )
            # Connect to Twitch asynchronously - don't block startup
            twitch_connect_task = asyncio.create_task(self.twitch_client.connect())
            self.service_registry.register('TwitchService', self.twitch_client)
            
            # Load feature flags
            feature_flags = self._load_feature_flags()
            
            # Raider Welcome will be initialized after ResponseCoordinator
            
            
            # Initialize audio queue with optimizations
            logger.info("Initializing OptimizedAudioQueue...")
            self.audio_queue = OptimizedAudioQueue(
                openai_api_key=self.config['OPENAI_API_KEY'],
                cache_size_mb=500,
                enable_pre_buffering=True
            )
            await self.audio_queue.initialize()
            self.service_registry.register('AudioService', self.audio_queue)
            
            # Initialize ResponseCoordinator for synchronized delivery (PRD critical)
            logger.info("Initializing ResponseCoordinator...")
            self.response_coordinator = ResponseCoordinator(
                twitch_client=self.twitch_client,
                audio_queue=self.audio_queue,
                settings_path='bot_settings.json'
            )
            # Connect personality engine for dynamic dead air messages
            self.response_coordinator.personality_engine = self.personality_engine
            await self.response_coordinator.start_dead_air_prevention()
            self.service_registry.register('ResponseCoordinator', self.response_coordinator)
            
            # Initialize WebSocket manager for real-time communication
            logger.info("Setting up WebSocket manager...")
            self.websocket_manager = WebSocketManager()
            await self.websocket_manager.initialize()
            self.service_registry.register('WebSocketService', self.websocket_manager)
            
            # Initialize EventSub WebSocket for automatic ad detection
            logger.info("Connecting to EventSub for ad detection...")
            self.eventsub = EventSubWebSocket(
                client_id=self.config['TWITCH_CLIENT_ID'],
                access_token=self.config['TWITCH_ACCESS_TOKEN'],
                channel_name=self.config.get('TWITCH_CHANNEL'),
                broadcaster_id=self.config.get('TWITCH_BROADCASTER_ID')
            )
            
            # Start EventSub connection in background
            asyncio.create_task(self.eventsub.connect())

            # Register token refresh callback and start background refresher
            def on_token_refresh(account_name, new_access_token):
                try:
                    if account_name == bot_account:
                        if hasattr(self.twitch_client, 'access_token'):
                            self.twitch_client.access_token = new_access_token
                        if hasattr(self.eventsub, 'access_token'):
                            self.eventsub.access_token = new_access_token
                        logger.info(f"Live tokens updated for bot account {bot_account}")
                    elif account_name == channel:
                        if hasattr(self.eventsub, 'broadcaster_token'):
                            self.eventsub.broadcaster_token = new_access_token
                        logger.info(f"Live broadcaster token updated for {channel}")
                except Exception as e:
                    logger.error(f"Failed to apply refreshed token to live components: {e}")

            self.token_refresher.on_refresh_callback(on_token_refresh)
            await self.token_refresher.start()
            self.service_registry.register('TokenRefresher', self.token_refresher)

            # Initialize Ad Announcer
            logger.info("Initializing Ad Announcer...")
            self.ad_announcer = AdAnnouncer(
                twitch_client=self.twitch_client,
                audio_queue=self.audio_queue,
                response_coordinator=self.response_coordinator,
                personality_engine=self.personality_engine,
                chat_buffer=self.chat_buffer,
                channel_name=self.config.get('TWITCH_CHANNEL')
            )
            
            # Register EventSub handler for automatic ad detection
            self.eventsub.on_event('channel.ad_break.begin', self.ad_announcer.handle_ad_break_begin)

            # Initialize Event Announcer for follow/sub/cheer events
            logger.info("Initializing Event Announcer...")
            self.event_announcer = EventAnnouncer(self)

            # Register EventSub handlers for follow, sub, and cheer events
            self.eventsub.on_event('channel.follow', self._on_follow)
            self.eventsub.on_event('channel.subscribe', self._on_subscribe)
            self.eventsub.on_event('channel.cheer', self._on_cheer)
            logger.info("Event announcer handlers registered")

            # Category/title awareness, stream lifecycle, recap, first-timer policy
            self._setup_stream_awareness()

            self.service_registry.register('EventSubService', self.eventsub)
            self.service_registry.register('AdAnnouncer', self.ad_announcer)
            self.service_registry.register('EventAnnouncer', self.event_announcer)
            
            # Register raid handler with Twitch client (IRC-based - more reliable)
            self.twitch_client.on_event('raid', self._handle_raid_event)
            
            # Initialize Raider Welcome system (optional feature)
            if feature_flags.get('raider_welcome', False) or self.config.get('RAIDER_WELCOME_ENABLED', False):
                logger.info("Initializing Raider Welcome system...")
                from features.raider_welcome import RaiderWelcome
                
                # Initialize with response coordinator
                self.raider_welcome = RaiderWelcome(
                    twitch_client=self.twitch_client,
                    llm_service=self.personality_engine,
                    tts_service=self.audio_queue,
                    response_coordinator=self.response_coordinator
                )
                
                # Register raid handler (EventSub uses full event type name)
                self.eventsub.on_event('channel.raid', self.on_raid)
                logger.info("Raider Welcome feature enabled - LLM-powered dynamic welcomes active")
            else:
                self.raider_welcome = None
                logger.info("Raider Welcome feature disabled")
            
            # Initialize Voice Recognition if enabled (check multiple config keys)
            voice_enabled = (
                self.config.get('VOICE_INPUT_ENABLED', False) or 
                self.config.get('VOICE_ENABLED', False)
            )
            if voice_enabled:
                logger.info("Initializing Voice Recognition...")
                self.voice_recognition = VoiceRecognition(
                    tts_service=self.audio_queue,
                    asr_engine=self.config.get('VOICE_ASR_ENGINE', 'whisper'),
                    whisper_model=self.config.get('WHISPER_MODEL', 'small.en'),
                )
                
                # Initialize voice command system
                from components.voice.voice_commands import VoiceCommandSystem
                self.voice_commands = VoiceCommandSystem(bot=self)
                logger.info("Voice command system initialized")
                
                # Set up voice callback to handle recognized text
                self.voice_recognition.on_text_recognized = self._handle_voice_input
                
                # Start listening with <3s startup target
                success = await self.voice_recognition.start_listening()
                if success:
                    logger.info("Voice recognition active and listening")
                    self.service_registry.register('VoiceService', self.voice_recognition)
                else:
                    logger.warning("Voice recognition failed to start")

                # Realtime backend (doc SS17 Phase 2). Legacy recognition stays
                # up: in PASSIVE it is the local wake-phrase detector (U3), and
                # it is the fallback if the Realtime session fails.
                if self.config.get('VOICE_BACKEND', 'legacy') == 'realtime':
                    await self._setup_realtime_backend()

            # Initialize VAD ducking if enabled
            if self.config.get('VAD_DUCKING_ENABLED', True) and self.audio_queue:
                try:
                    logger.info("Initializing VAD ducking system...")
                    from audio.vad_ducking import VADDucking
                    
                    self.vad_ducking = VADDucking(
                        audio_queue=self.audio_queue,
                        sensitivity=0.3,  # Configurable sensitivity
                        duck_level=0.15,  # Duck to 15% volume
                        fade_time=0.3,    # 300ms fade
                        hold_time=0.8     # 800ms hold after voice stops
                    )
                    
                    if self.vad_ducking.initialize():
                        if self.vad_ducking.start_monitoring():
                            logger.info("VAD ducking active - natural interrupts enabled")
                            self.service_registry.register('VADService', self.vad_ducking)
                        else:
                            logger.warning("VAD ducking failed to start monitoring")
                    else:
                        logger.warning("VAD ducking failed to initialize")
                        self.vad_ducking = None
                        
                except Exception as e:
                    logger.error(f"Failed to initialize VAD ducking: {e}")
                    self.vad_ducking = None
            
            # Register core message handler
            self.twitch_client.on_message(self._handle_chat_message)
            
            # Register ad command handler
            self.twitch_client.on_message(self._handle_ad_commands)
            self._setup_clip_command()

            # Wait for Twitch connection to complete
            await twitch_connect_task
            logger.info("Twitch connection established")

            # If memory came up degraded, say so in chat now that we can
            if not self._memory_healthy:
                await self._send_memory_offline_notice()

            # Start embedded API server if enabled
            if self.config.get('API_ENABLED', False):
                await self._start_api_server()

            logger.info("TalkBot setup complete!")
            
        except Exception as e:
            logger.error(f"Failed to setup TalkBot: {e}", exc_info=True)
            await self.shutdown()
            raise
            
    async def _handle_chat_message(self, message: Dict[str, Any]) -> None:
        """
        Handle incoming chat messages with all documented fixes
        
        Implements:
        - Identity checking (no self-responses)
        - @mention priority boost
        - Context building <100ms
        - Response coordination
        """
        try:
            username_lower = message.get('username', '').lower()
            
            # Skip if message is from self (feedback loop prevention)
            if username_lower == self.config['TWITCH_BOT_USERNAME'].lower():
                return
                
            # Skip messages from known bots to prevent bot conversations
            known_bots = ['nightbot', 'streamelements', 'streamlabs', 'moobot', 'fossabot', 
                         'wizebot', 'botisimo', 'coebot', 'phantombot', 'deepbot']
            if username_lower in known_bots:
                logger.debug(f"Ignoring message from bot: {username_lower}")
                return
                
            # Track message for metrics
            self.message_count += 1
            start_time = time.perf_counter()
            
            # CRITICAL: Update dead air timer for ANY chat activity
            if self.response_coordinator:
                self.response_coordinator.last_activity_time = datetime.now()
            
            # Check for @mention or "hey bot" and boost priority
            text_lower = message.get('text', '').lower()
            is_mention = (
                f"@{self.config['TWITCH_BOT_USERNAME'].lower()}" in text_lower or
                "hey bot" in text_lower or
                "hey talkbot" in text_lower
            )
            priority = 'high' if is_mention else 'normal'
            
            # Store in memory system
            await self.memory_system.store_message(message)

            # Add to real-time chat buffer
            self.chat_buffer.append_viewer(
                channel=message.get('channel', ''),
                username=message.get('username', 'unknown'),
                message=message.get('text') or message.get('message', '')
            )
            if self.stream_recap:
                self.stream_recap.record_message(message.get('username', ''))

            # Log if this is a mention
            if is_mention:
                logger.info(f"Processing mention from {message.get('username')}: '{message.get('text', '')}'")
            
            # Build context using OptimizedContextBuilder for <100ms performance
            if self.context_builder:
                context = await self.context_builder.build_context(
                    viewer=message.get('username', 'unknown'),
                    channel=self.config.get('TWITCH_CHANNEL', 'unknown'),
                    message=message.get('text', ''),
                    scenario='mention' if is_mention else 'general'
                )
            else:
                # Fallback to old method if context builder not available
                context = await self.memory_system.get_context_optimized(
                    user_id=message.get('user_id', message.get('username', 'unknown')),
                    message_text=message.get('text', ''),
                    max_time_ms=80
                )
            
            # First-time chatter: upgrade to a must-reply welcome. Detection is
            # the context builder's (positive evidence only); whether to act
            # on it right now is features/first_timer.py's call.
            greet = bool(self.first_timer and self.first_timer.should_greet(context))
            if greet:
                context['greet_first_timer'] = True
                is_mention = True
                priority = 'high'
                logger.info(f"First-time chatter: {message.get('username')}")

            # Sentence-streamed path (flag-gated): speaks as the model writes.
            # Falls through to the blocking path when off, when not speaking,
            # or when streaming produced nothing at all.
            handled, streamed_text = await self._try_streamed_reply(
                message_text=message.get('text', ''),
                context=context,
                user=message.get('username'),
                is_mention=is_mention,
                priority=priority,
                is_voice=False,
            )
            if handled:
                if streamed_text and greet:
                    self.first_timer.mark_greeted()
                response = None
            else:
                # Get personality response
                response = await self.personality_engine.generate_response(
                    message=message.get('text'),
                    context=context,
                    user=message.get('username'),
                    is_mention=is_mention
                )

            if response:
                # Track last response for repeat command
                self.last_response = response['text']
                if greet:
                    self.first_timer.mark_greeted()

                # Use ResponseCoordinator for synchronized delivery
                if self.response_coordinator:
                    # Create audio task for TTS if enabled and response says to speak
                    audio_task = None
                    should_speak = response.get('should_speak', is_mention)  # Default to speaking for mentions
                    tts_enabled = self.config.get('TTS_ENABLED', True)
                    logger.info(f"TTS Decision: TTS_ENABLED={tts_enabled}, should_speak={should_speak}, is_mention={is_mention}")
                    
                    if tts_enabled and should_speak:
                        logger.info(f"Queueing TTS for: '{response['text'][:50]}...'")
                        async def queue_tts():
                            await self.audio_queue.queue_audio(
                                text=strip_mentions_for_tts(response.get('speech_text') or response['text']),
                                priority=priority
                            )
                            self.audio_count += 1
                        audio_task = queue_tts
                    else:
                        logger.info(f"Skipping TTS: enabled={tts_enabled}, should_speak={should_speak}")
                    
                    # Coordinate response with proper timing
                    await self.response_coordinator.coordinate_response(
                        chat_msg=response['text'],
                        audio_task=audio_task,
                        priority=priority,
                        is_mention=is_mention,
                        is_voice=False
                    )
                    if self.stream_recap:
                        self.stream_recap.record_response(spoken=audio_task is not None)
                else:
                    # Fallback to direct sending if coordinator not available
                    await self.twitch_client.send_message(response['text'])
                    if self.config.get('TTS_ENABLED', True):
                        await self.audio_queue.queue_audio(
                            text=strip_mentions_for_tts(response.get('speech_text') or response['text']),
                            priority=priority
                        )
                        self.audio_count += 1

                # Add bot's response to chat buffer
                self.chat_buffer.append_assistant(
                    channel=self.config.get('TWITCH_CHANNEL', ''),
                    username=self.config.get('TWITCH_BOT_USERNAME', 'bot'),
                    message=response['text']
                )

            # Fold older chat into the session summary if a batch is due
            self._schedule_summary()

            # Track overall response time
            response_time = (time.perf_counter() - start_time) * 1000
            self.response_times.append(response_time)

            # Metrics tracking (PRD requirement)
            if self.metrics_enabled and self.metrics_collector:
                self.metrics_collector.record_response_time(response_time)
                self.metrics_collector.record_message('chat')
            
            # Log performance metrics periodically
            if self.message_count % 100 == 0:
                avg_response_time = sum(self.response_times[-100:]) / min(100, len(self.response_times))
                logger.info(f"Performance: {avg_response_time:.3f}s avg response time")
                
        except Exception as e:
            logger.error(f"Error handling chat message: {e}", exc_info=True)
            
    async def run(self) -> None:
        """
        Main bot loop with health monitoring
        """
        self.running = True
        self.state.is_running = True
        logger.info(f"TalkBot running for channel: {self.state.streamer_id}")
        
        # Start background tasks
        health_task = self.task_registry.create_task(
            self._health_monitor(),
            name="health_monitor"
        )
        
        audio_processor = self.task_registry.create_task(
            self._process_audio_queue(),
            name="audio_processor"
        )

        # Loop-lag probe: warns loudly if any single loop tick stalls >100ms,
        # the bar for "no stall under active TTS + voice load". Cheap enough
        # to leave on in production; it's how we'd notice a future regression
        # that reintroduces blocking work on the loop.
        self._loop_lag_monitor = LoopLagMonitor(interval=1.0, warn_threshold_ms=100.0)
        self.task_registry.create_task(
            self._loop_lag_monitor.run(),
            name="loop_lag_monitor"
        )

        try:
            while self.running:
                await asyncio.sleep(1)
                
                # Check for WebSocket reconnection needs
                if self.websocket_manager and not self.websocket_manager.is_connected():
                    logger.warning("WebSocket disconnected, attempting reconnect...")
                    await self.websocket_manager.reconnect()
                    
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            await self.shutdown()
            
    async def _process_audio_queue(self) -> None:
        """
        Process audio queue sequentially to prevent overlap
        Implements fix
        """
        while self.running:
            try:
                # Process next audio item (sequential await prevents overlap)
                await self.audio_queue.process_next()
                # Small delay between audio clips
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error processing audio queue: {e}")
                await asyncio.sleep(1)
                
    # Tables the memory system reads/writes; schema drift here means the bot
    # silently loses viewer memory (April 2026: ran 10 weeks without noticing)
    MEMORY_REQUIRED_TABLES = ('users', 'chat_messages')

    async def _check_memory_health(self) -> tuple:
        """
        Verify DB connectivity AND schema completeness.

        Returns:
            (healthy, reason) - healthy only if the DB is reachable and every
            table the memory system uses exists.
        """
        if not (self.memory_system
                and getattr(self.memory_system, 'db_available', False)
                and self.memory_system.db):
            return False, "database unreachable"
        try:
            rows = await self.memory_system.db.fetch(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY($1::text[])
                """,
                list(self.MEMORY_REQUIRED_TABLES)
            )
            found = {r['table_name'] for r in rows} if rows else set()
            missing = [t for t in self.MEMORY_REQUIRED_TABLES if t not in found]
            if missing:
                return False, f"missing tables: {', '.join(missing)}"
            return True, "ok"
        except Exception as e:
            return False, f"schema check failed: {e}"

    def _log_memory_degraded(self, reason: str) -> None:
        """CRITICAL banner - ASCII only (cp1252 console chokes on unicode)."""
        logger.critical("!" * 60)
        logger.critical("!!! MEMORY OFFLINE - BOT RUNNING WITHOUT VIEWER MEMORY !!!")
        logger.critical(f"!!! Reason: {reason}")
        logger.critical("!!! Conversations will NOT persist until this is fixed")
        logger.critical("!" * 60)

    async def _send_memory_offline_notice(self) -> None:
        """One chat line so degradation is visible in-stream, never spammed."""
        try:
            if self.twitch_client and self.twitch_client.is_connected():
                await self.twitch_client.send_message(
                    "⚠ memory offline - running without viewer memory"
                )
        except Exception as e:
            logger.error(f"Failed to send memory-offline chat notice: {e}")

    async def _health_monitor(self) -> None:
        """
        Monitor bot health and performance metrics
        """
        last_settings_mtime = 0
        while self.running:
            try:
                # Check if bot_settings.json has changed
                if os.path.exists('bot_settings.json'):
                    current_mtime = os.path.getmtime('bot_settings.json')
                    if current_mtime > last_settings_mtime:
                        last_settings_mtime = current_mtime
                        # Reload settings
                        await self._reload_settings()
                
                # Memory health: alert on healthy->degraded transition only
                memory_ok, memory_reason = await self._check_memory_health()
                if not memory_ok and self._memory_healthy:
                    self._memory_healthy = False
                    self._log_memory_degraded(memory_reason)
                    await self._send_memory_offline_notice()
                elif memory_ok and not self._memory_healthy:
                    self._memory_healthy = True
                    logger.info("Memory system recovered - DB and schema healthy")

                # Check memory usage
                memory_stats = self.memory_system.get_stats()  # ResilientMemorySystem.get_stats() is not async
                
                # Check audio queue health (get_stats is async)
                audio_stats = await self.audio_queue.get_stats()
                
                # Check response coordinator stats
                coordinator_stats = None
                if self.response_coordinator:
                    coordinator_stats = self.response_coordinator.get_stats()
                
                # Check Twitch connection
                twitch_connected = self.twitch_client.is_connected()
                
                # Log health status
                logger.info(f"Health: Memory={memory_stats}, Audio={audio_stats}, Twitch={twitch_connected}, Coordinator={coordinator_stats}")
                
                # Broadcast health via WebSocket (only if running)
                if self.websocket_manager and self.websocket_manager.is_running:
                    await self.websocket_manager.broadcast({
                        'type': 'health',
                        'data': {
                            'memory': memory_stats,
                            'audio': audio_stats,
                            'twitch': twitch_connected,
                            'coordinator': coordinator_stats,
                            'uptime': (datetime.now() - self.start_time).total_seconds(),
                            'message_count': self.message_count,
                            'audio_count': self.audio_count
                        }
                    })
                    
                await asyncio.sleep(30)  # Health check every 30 seconds
                
            except Exception as e:
                logger.error(f"Error in health monitor: {e}")
                await asyncio.sleep(60)
                
    async def _ensure_voice_user_exists(self) -> None:
        """
        Ensure the streamer user exists in the database for voice input
        """
        try:
            # Use the memory system's database connection directly
            if self.memory_system.db_available and self.memory_system.db:
                # Use the streamer's username
                username = self.config.get('TWITCH_CHANNEL', 'streamer')
                user_id = username.lower()
                
                # Check if user exists using the database connection's fetch method
                result = await self.memory_system.db.fetch(
                    "SELECT user_id FROM users WHERE user_id = $1",
                    user_id
                )
                
                if not result:
                    # Create streamer user if doesn't exist using the database connection
                    await self.memory_system.db.execute(
                        """
                        INSERT INTO users (user_id, username, first_seen, last_seen, message_count)
                        VALUES ($1, $2, NOW(), NOW(), 0)
                        ON CONFLICT (user_id) DO NOTHING
                        """,
                        user_id, username
                    )
                    logger.info(f"Created {username} user in database for voice input")
            else:
                logger.debug("Database not available, skipping voice user creation")
                    
        except Exception as e:
            logger.error(f"Error ensuring streamer user exists: {e}")
    
    async def _handle_voice_message(self, message: Dict[str, Any]) -> None:
        """
        Handle voice messages with guaranteed response
        
        Args:
            message: Voice message dictionary
        """
        try:
            # Track message for metrics
            self.message_count += 1
            start_time = time.perf_counter()
            
            # Voice messages are always high priority
            priority = 'high'

            # Store in memory system
            await self.memory_system.store_message(message)

            # Add to real-time chat buffer under the real channel — context is
            # always read for TWITCH_CHANNEL, so 'voice' entries would be invisible
            self.chat_buffer.append_viewer(
                channel=self.config.get('TWITCH_CHANNEL', message.get('channel', '')),
                username=message.get('username', 'unknown'),
                message=message.get('text') or message.get('message', '')
            )

            # Build context using OptimizedContextBuilder for <100ms performance
            if self.context_builder:
                context = await self.context_builder.build_context(
                    viewer=message.get('username', 'voice_user'),
                    channel=self.config.get('TWITCH_CHANNEL', 'unknown'),
                    message=message.get('text', ''),
                    scenario='voice_input'
                )
            else:
                # Fallback to old method if context builder not available
                context = await self.memory_system.get_context_optimized(
                    user_id=message.get('user_id', 'voice_user'),
                    message_text=message.get('text', ''),
                    max_time_ms=80
                )
            
            # Get personality response - force response for voice.
            # Label the speaker so the LLM knows this is the streamer talking,
            # not a viewer (attribution only — storage keeps the plain username)
            speaker = f"{message.get('username')} (the streamer, your co-host partner)"
            handled, streamed_text = await self._try_streamed_reply(
                message_text=message.get('text', ''),
                context=context,
                user=message.get('username'),
                is_mention=True,
                priority='high',
                is_voice=True,
                speaker_label=speaker,
            )
            if handled and streamed_text:
                response = None
                voice_streamed = True
            else:
                voice_streamed = False
                response = await self.personality_engine.generate_response(
                    message=message.get('text'),
                    context=context,
                    user=speaker,
                    is_mention=True  # Treat all voice as mentions
                )
            
            if response:
                # Track last response for repeat command
                self.last_response = response['text']
                
                # Use ResponseCoordinator for synchronized delivery
                if self.response_coordinator:
                    # Create audio task for TTS (always enabled for voice)
                    async def queue_tts():
                        if self.audio_queue:
                            await self.audio_queue.queue_audio(
                                text=strip_mentions_for_tts(response.get('speech_text') or response['text']),
                                priority='high'  # Voice responses are high priority
                            )
                            self.audio_count += 1
                            logger.info(f"Voice response queued: '{response['text']}'")
                    
                    # Coordinate response with proper timing
                    await self.response_coordinator.coordinate_response(
                        chat_msg=response['text'],
                        audio_task=queue_tts,
                        priority='high',
                        is_mention=True,  # Treat voice as mention
                        is_voice=True
                    )
                else:
                    # Fallback to direct sending if coordinator not available
                    if self.twitch_client and self.twitch_client.is_connected():
                        await self.twitch_client.send_message(response['text'])
                    if self.audio_queue:
                        await self.audio_queue.queue_audio(
                            text=strip_mentions_for_tts(response.get('speech_text') or response['text']),
                            priority='high'
                        )
                        self.audio_count += 1
                        logger.info(f"Voice response queued: '{response['text']}'")

                # Add bot's response to chat buffer
                self.chat_buffer.append_assistant(
                    channel=self.config.get('TWITCH_CHANNEL', ''),
                    username=self.config.get('TWITCH_BOT_USERNAME', 'bot'),
                    message=response['text']
                )
            elif voice_streamed:
                pass  # already delivered by the streamed path
            else:
                # Fallback response if personality engine doesn't respond
                fallback = "I heard you, but I'm not sure what to say."
                self.last_response = fallback
                
                if self.response_coordinator:
                    # Create audio task for fallback
                    async def queue_fallback():
                        if self.audio_queue:
                            await self.audio_queue.queue_audio(
                                text=strip_mentions_for_tts(fallback),
                                priority='high'
                            )

                    await self.response_coordinator.coordinate_response(
                        chat_msg=fallback,
                        audio_task=queue_fallback,
                        priority='high',
                        is_mention=True,
                        is_voice=True
                    )
                else:
                    # Direct fallback
                    if self.audio_queue:
                        await self.audio_queue.queue_audio(
                            text=strip_mentions_for_tts(fallback),
                            priority='high'
                        )
                logger.warning("No response from personality engine for voice input")
            
            # Fold older chat into the session summary if a batch is due
            self._schedule_summary()

            # Track overall response time
            response_time = (time.perf_counter() - start_time) * 1000
            self.response_times.append(response_time)
            logger.info(f"Voice response time: {response_time:.2f}ms")
                    
        except Exception as e:
            logger.error(f"Error handling voice message: {e}", exc_info=True)
            # Try to provide audio feedback on error
            if self.audio_queue:
                await self.audio_queue.queue_audio(
                    text=strip_mentions_for_tts("Sorry, I had trouble processing that."),
                    priority='high'
                )
    
    async def _setup_realtime_backend(self) -> None:
        """Wire VOICE_BACKEND=realtime. Any failure falls back to legacy."""
        try:
            from attention.config import AttentionConfig
            from attention.router import AttentionRouter
            from realtime.audio_router import AudioRouter
            from realtime.backend import RealtimeVoiceBackend
            from realtime.session import RealtimeVoiceSession

            in_spec = self.config.get('REALTIME_INPUT_DEVICE', '')
            out_spec = self.config.get('REALTIME_OUTPUT_DEVICE', '')
            if not in_spec or not out_spec:
                raise RuntimeError(
                    "REALTIME_INPUT_DEVICE and REALTIME_OUTPUT_DEVICE must be "
                    "set (no default device is ever guessed)")

            audio = AudioRouter(
                input_spec=in_spec, output_spec=out_spec,
                preroll_ms=self.config.get('REALTIME_PREROLL_MS', 2000),
                loop=asyncio.get_running_loop(),
                pa=self.audio_queue.pyaudio if self.audio_queue else None)

            session = RealtimeVoiceSession(
                model=self.config.get('REALTIME_MODEL', 'gpt-realtime-2.1-mini'),
                voice=self.config.get('REALTIME_VOICE', 'marin'),
                vad=self.config.get('REALTIME_VAD', 'server_vad'),
                instructions_provider=self._realtime_instructions,
                api_key=self.config.get('OPENAI_API_KEY'),
                create_task=lambda coro: self.task_registry.create_task(
                    coro, name="realtime_session"))

            router = AttentionRouter(AttentionConfig(
                bot_username=self.config.get('TWITCH_BOT_USERNAME', ''),
                streamer_username=self.config.get('TWITCH_CHANNEL', ''),
                grace_s=self.config.get('REALTIME_GRACE_MS', 400) / 1000.0,
                window_s=self.config.get('REALTIME_WINDOW_S', 45.0)))

            self.realtime_backend = RealtimeVoiceBackend(
                audio=audio, session=session, router=router,
                streamer_username=self.config.get('TWITCH_CHANNEL', ''),
                channel=self.config.get('TWITCH_CHANNEL', ''),
                create_task=lambda coro: self.task_registry.create_task(
                    coro, name="realtime_backend"))
            await self.realtime_backend.start()
            self.service_registry.register('RealtimeVoiceService',
                                           self.realtime_backend)
            logger.info("VOICE_BACKEND=realtime active; legacy recognition is "
                        "the wake-phrase detector and the fallback")
        except Exception as e:
            # Loud: the operator must never think they are on realtime when
            # they are not (same rule as the Whisper->Google fallback).
            logger.error(
                f"REALTIME BACKEND FAILED TO START -- staying on the legacy "
                f"voice pipeline for this session. Reason: {e}")
            self.realtime_backend = None

    def _realtime_instructions(self) -> str:
        """Persona text for the Realtime session (doc SS11 'in')."""
        try:
            if self.personality_engine:
                return self.personality_engine._build_personality_prompt()
        except Exception as e:
            logger.warning(f"Falling back to a minimal realtime persona: {e}")
        return ("You are a witty, concise AI co-host on a Twitch stream. The "
                "person speaking to you is the streamer. Keep replies to one "
                "or two short sentences.")

    async def _handle_voice_input(self, text: str) -> None:
        """
        Handle voice input from recognition system
        Only respond during dead air periods to avoid spam
        
        Args:
            text: Recognized text from voice
        """
        try:
            logger.info(f"[VOICE INPUT] Received: '{text}'")

            # VOICE_BACKEND=realtime: legacy transcripts are wake-phrase
            # candidates only. The Realtime session owns the conversation, so
            # the staged pipeline below must not also answer.
            if self.realtime_backend is not None:
                await self.realtime_backend.on_legacy_transcript(text)
                return
            
            # Filter out short/noisy inputs
            if len(text) < 4:
                logger.debug(f"[VOICE INPUT] Too short, ignoring: '{text}'")
                return
            
            # Simply skip dead air check - let other filters handle spam prevention
            # Dead air is for the BOT to fill silence, not for blocking voice
                
            # Check cooldown for voice responses
            now = datetime.now()
            if self.last_voice_response:
                time_since_last = (now - self.last_voice_response).total_seconds()
                
                # Reasonable cooldown to prevent spam
                min_cooldown = 5  # 5 seconds between voice responses (was 30!)
                
                if time_since_last < min_cooldown:
                    logger.info(f"[VOICE] Cooldown active: {time_since_last:.1f}s < {min_cooldown}s for: '{text}'")
                    return
            
            logger.info(f"[VOICE] Processing input: '{text}'")
            
            # Check for duplicate/similar recent inputs
            text_lower = text.lower()
            for recent in self.recent_voice_texts[-5:]:  # Check last 5
                if text_lower == recent or text_lower in recent or recent in text_lower:
                    logger.debug(f"Duplicate voice input filtered: '{text}'")
                    return
            
            # Check if it's a voice command first
            if self.voice_commands:
                command_handled = await self.voice_commands.process_input(text)
                if command_handled:
                    logger.info(f"Voice command processed: '{text}'")
                    self.last_voice_response = now
                    return
            
            # MORE RESTRICTIVE: Only respond to direct questions or bot mentions
            bot_name = self.config.get('BOT_NAME', 'talkbot').lower()
            needs_response = False
            
            # 1. Respond to various "hey" greetings directed at the bot.
            # The trigger list and the normalization for recognizer misfires
            # ("hey b..." heard as "play ...") live in trigger_match.py.
            trigger_matched, trigger_how = match_hey_trigger(text_lower)
            if trigger_matched:
                needs_response = True
                if trigger_how != 'exact':
                    # Loud on purpose: non-exact hits are how false positives
                    # get diagnosed from the stream log
                    logger.info(f"[VOICE] Trigger via {trigger_how} match "
                                f"(recognizer misfire tolerated): '{text}'")
                logger.info(f"Voice: Bot triggered - '{text}'")
            # Also respond if bot name is mentioned
            elif bot_name in text_lower and len(text_lower.split()) <= 10:
                needs_response = True
                logger.info(f"Voice: Bot name mentioned - '{text}'")
            
            # 2. DISABLE all other triggers - too noisy
            # elif '?' in text and any(word in text_lower for word in ['what', 'why', 'how', 'when', 'where', 'who']):
            #     needs_response = True
            #     logger.info(f"Voice: Clear question - '{text}'")
            
            # 3. ONLY allow mute/unmute commands
            elif any(cmd in text_lower for cmd in ['mute talkbot', 'unmute talkbot', 'hey talkbot mute', 'hey talkbot unmute']):
                needs_response = True
                logger.info(f"Voice: Mute command - '{text}'")
            
            if not needs_response:
                logger.info(f"[VOICE] NO TRIGGER in: '{text}'")
                logger.info(f"[VOICE] Available triggers: hey bud, hey boss, hey bot, etc.")
                # Store for context
                voice_message = {
                    'username': self.config.get('TWITCH_CHANNEL', 'streamer'),
                    'user_id': self.config.get('TWITCH_CHANNEL', 'streamer').lower(),
                    'message': text,  # Database expects 'message' not 'text'
                    'text': text,  # Keep for compatibility
                    'timestamp': datetime.now(),
                    'channel': 'voice',
                    'is_voice': True,
                    'is_mention': False
                }
                await self.memory_system.store_message(voice_message)
                return
            
            logger.info(f"Processing voice input: '{text}'")
            
            # Check if bot is muted
            if self.muted:
                logger.debug("Bot is muted, ignoring voice input")
                return
            
            # Track this input
            self.recent_voice_texts.append(text_lower)
            if len(self.recent_voice_texts) > 10:
                self.recent_voice_texts = self.recent_voice_texts[-10:]
            
            # Create a pseudo-message for processing
            # Use the streamer's username for voice input
            username = self.config.get('TWITCH_CHANNEL', 'streamer')
            voice_message = {
                'username': username,
                'user_id': username.lower(),
                'message': text,  # Database expects 'message' not 'text'
                'text': text,  # Keep for compatibility
                'timestamp': datetime.now(),
                'channel': 'voice',
                'is_voice': True,
                'is_mention': True  # Treat as high priority since it passed all filters
            }
            
            # Process with response
            await self._handle_voice_message(voice_message)
            
            # Update activity time to prevent immediate dead air trigger
            if self.response_coordinator:
                self.response_coordinator.last_activity_time = datetime.now()
            
            # Update last response time
            self.last_voice_response = now
            
        except Exception as e:
            logger.error(f"Error handling voice input: {e}")
            
    def _setup_session_summarizer(self) -> None:
        """Build the StreamSessionSummarizer from bot_settings.json -> session_summary."""
        cfg: Dict[str, Any] = {}
        try:
            with open('bot_settings.json', 'r') as f:
                cfg = json.load(f).get('session_summary') or {}
        except Exception as e:
            logger.debug(f"No session_summary settings: {e}")

        if not cfg.get('enabled', True):
            logger.info("Session summary disabled via settings")
            return

        from bot.session_summarizer import StreamSessionSummarizer

        kwargs = {k: cfg[k] for k in (
            'turns_per_update', 'min_turns', 'max_interval_s', 'max_words', 'persist_dir'
        ) if k in cfg}
        self.session_summarizer = StreamSessionSummarizer(
            chat_buffer=self.chat_buffer,
            llm_call=self.personality_engine.complete,
            bot_name=self.config.get('TWITCH_BOT_USERNAME', 'the co-host'),
            **kwargs,
        )
        if self.context_builder:
            self.context_builder.session_summarizer = self.session_summarizer
        self.service_registry.register('SessionSummarizer', self.session_summarizer)
        logger.info("StreamSessionSummarizer initialized "
                    f"(every {self.session_summarizer.turns_per_update} turns "
                    f"or {self.session_summarizer.max_interval_s:.0f}s)")

    def _schedule_summary(self) -> None:
        """O(1) check; spawns a tracked background fold when a batch is due."""
        if not self.session_summarizer:
            return
        try:
            self.session_summarizer.maybe_schedule(
                self.config.get('TWITCH_CHANNEL', ''),
                lambda coro, name: self.task_registry.create_task(coro, name=name),
            )
        except Exception as e:
            logger.debug(f"Could not schedule session summary: {e}")

    async def _try_streamed_reply(
        self,
        *,
        message_text: str,
        context: Dict[str, Any],
        user: str,
        is_mention: bool,
        priority: str,
        is_voice: bool,
        speaker_label: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Sentence-streamed reply: audio starts on the first sentence instead of
        after the full completion + full TTS. Gated by bot_settings.json ->
        tts_streaming.enabled; only used when the reply would be spoken.

        Returns (handled, chat_text). handled=False means "use the blocking
        path"; handled=True with chat_text=None means the personality chose
        not to respond.
        """
        cfg = self._tts_streaming
        if not cfg.get('enabled'):
            return False, None
        if not (self.config.get('TTS_ENABLED', True) and self.audio_queue
                and self.response_coordinator and self.personality_engine):
            return False, None
        if not (is_mention or self.personality_engine.would_speak(message_text, is_mention)):
            return False, None

        reply = await self.personality_engine.generate_response_streamed(
            message=message_text,
            context=context,
            user=speaker_label or user,
            is_mention=is_mention,
            min_sentence_chars=int(cfg.get('min_sentence_chars', 12)),
            speech_filter=strip_mentions_for_tts,
        )
        if reply is None:
            return True, None  # personality declined; don't roll the dice twice

        text = await self.response_coordinator.coordinate_streamed_response(
            reply, priority=priority, is_mention=is_mention, is_voice=is_voice,
            user=user, prefetch_depth=int(cfg.get('prefetch_depth', 2)),
        )
        if not text:
            if getattr(reply, 'aborted', False):
                # Expired in the queue or skipped before it started: the moment
                # has passed, a second (blocking) attempt would be just as stale
                logger.info("Streamed reply abandoned before it started; not retrying")
                return True, None
            logger.warning("Streamed reply produced nothing; using blocking path")
            return False, None

        self.last_response = text
        self.audio_count += 1
        self.chat_buffer.append_assistant(
            channel=self.config.get('TWITCH_CHANNEL', ''),
            username=self.config.get('TWITCH_BOT_USERNAME', 'bot'),
            message=text,
        )
        if self.stream_recap:
            self.stream_recap.record_response(spoken=True)
        return True, text

    def _setup_stream_awareness(self) -> None:
        """Wire StreamInfo (category/title), stream lifecycle, recap, first-timer policy."""
        from features.first_timer import FirstTimerGreeter
        from features.stream_info import StreamInfo
        from features.stream_recap import StreamRecap

        channel = self.config.get('TWITCH_CHANNEL', '')

        def on_game_change(old: Optional[str], new: str, title: str) -> None:
            self.current_game = new
            if self.raider_welcome:
                self.raider_welcome.set_current_game(new)
            if old:  # a real switch, not the boot-time seed
                if self.session_summarizer:
                    self.session_summarizer.note_event(channel, f"{channel} switched category to {new}")
                if self.stream_recap:
                    self.stream_recap.record_event(f"category changed: {old} -> {new}")

        self.stream_info = StreamInfo(
            client_id=self.config.get('TWITCH_CLIENT_ID', ''),
            token_getter=lambda: (getattr(self.twitch_client, 'access_token', None)
                                  or self.config.get('TWITCH_ACCESS_TOKEN')),
            channel_name=channel,
            broadcaster_id=self.config.get('TWITCH_BROADCASTER_ID'),
            on_change=on_game_change,
        )
        if self.context_builder:
            self.context_builder.stream_info = self.stream_info
        self.eventsub.on_event('channel.update', self.stream_info.handle_channel_update)
        self.eventsub.on_event('stream.online', self._on_stream_online)
        self.eventsub.on_event('stream.offline', self._on_stream_offline)
        self.task_registry.create_task(self.stream_info.refresh(), name="stream_info_refresh")

        out_dir = 'session_state'
        if self.session_summarizer and self.session_summarizer.persist_dir:
            out_dir = str(self.session_summarizer.persist_dir)
        self.stream_recap = StreamRecap(channel, out_dir=out_dir)
        self.first_timer = FirstTimerGreeter.from_settings()

        self.service_registry.register('StreamInfo', self.stream_info)
        self.service_registry.register('StreamRecap', self.stream_recap)
        self.service_registry.register('FirstTimerGreeter', self.first_timer)
        logger.info("Stream awareness initialized (category, lifecycle, recap, first-timer)")

    async def _on_stream_online(self, event: dict) -> None:
        """New stream: fresh session memory and recap counters."""
        if self.stream_info:
            await self.stream_info.handle_stream_online(event)
        channel = self.config.get('TWITCH_CHANNEL', '')
        if self.session_summarizer:
            self.session_summarizer.reset(channel)
        if self.stream_recap:
            self.stream_recap.reset()
        if self.stream_info:
            self.task_registry.create_task(self.stream_info.refresh(), name="stream_info_refresh_online")

    async def _on_stream_offline(self, event: dict) -> None:
        if self.stream_info:
            await self.stream_info.handle_stream_offline(event)
        await self._write_recap("stream offline")

    async def _write_recap(self, reason: str) -> None:
        """Fold any remaining chat into the summary, then write the recap file."""
        if not self.stream_recap or not self.stream_recap.has_activity:
            return
        channel = self.config.get('TWITCH_CHANNEL', '')
        summary = ""
        if self.session_summarizer:
            try:
                stats = self.session_summarizer.stats(channel)
                if stats['unsummarized_turns'] >= self.session_summarizer.min_turns or stats['pending_events']:
                    await asyncio.wait_for(self.session_summarizer.update(channel), timeout=10.0)
            except Exception as e:
                logger.warning(f"Final summary fold skipped: {e}")
            summary = self.session_summarizer.get_summary(channel)

        extra: Dict[str, Any] = {"Reason": reason}
        if self.current_game:
            extra["Category"] = self.current_game
        if self.personality_engine:
            st = self.personality_engine.get_stats()
            extra["Repetition guard"] = (f"{st.get('repetition_rejections', 0)} drafts rejected, "
                                         f"{st.get('repetition_forced', 0)} forced through")
        if self.first_timer:
            ft = self.first_timer.stats()
            extra["First-timers greeted"] = f"{ft['greeted']} ({ft['suppressed']} suppressed)"

        path = self.stream_recap.write(summary, extra)
        if path:
            logger.info(f"Stream recap ({reason}): {path}")
        self.stream_recap.reset()

    # ------------------------------------------------------------------ clips

    def _setup_clip_command(self) -> None:
        """`!clip` in chat (mods/broadcaster) and "clip that" by voice."""
        self.twitch_client.on_message(self._handle_clip_command)
        if self.voice_commands:
            from components.voice.voice_commands import CommandType
            self.voice_commands.register_command(
                "clip",
                r"\b(clip (that|it|this)|make a clip|save that clip)\b",
                self._voice_clip,
                CommandType.MEDIA,
                "Clips the last 30 seconds of the stream",
                cooldown=30.0,
            )
        logger.info("Clip command registered (!clip, voice: 'clip that')")

    async def _handle_clip_command(self, message: Dict[str, Any]) -> None:
        try:
            text = message.get('text', '').lower().strip()
            if text != '!clip':
                return
            username = message.get('username', '').lower()
            is_broadcaster = username == self.config.get('TWITCH_CHANNEL', '').lower()
            if not (message.get('is_mod', False) or is_broadcaster):
                return
            clip = await self._create_clip(requested_by=username)
            if not clip:
                await self.twitch_client.send_message("couldn't make a clip right now")
        except Exception as e:
            logger.error(f"Error handling !clip: {e}")

    async def _voice_clip(self) -> None:
        clip = await self._create_clip(requested_by="voice")
        if self.audio_queue:
            await self.audio_queue.queue_audio("Clipped" if clip else "Couldn't clip that",
                                               priority="high")

    async def _create_clip(self, requested_by: str = "") -> Optional[Dict[str, Any]]:
        """Clip the last ~30s. Tries the broadcaster token first (needs clips:edit), then the bot's."""
        from twitch import helix

        client_id = self.config.get('TWITCH_CLIENT_ID', '')
        broadcaster_id = (
            (self.stream_info.broadcaster_id if self.stream_info else None)
            or getattr(self.eventsub, 'broadcaster_id', None)
            or self.config.get('TWITCH_BROADCASTER_ID')
        )
        if not broadcaster_id:
            logger.warning("Clip: no broadcaster id available")
            return None

        tokens = [
            getattr(self.eventsub, 'broadcaster_token', None) or self.config.get('TWITCH_BROADCASTER_TOKEN'),
            getattr(self.twitch_client, 'access_token', None) or self.config.get('TWITCH_ACCESS_TOKEN'),
        ]
        clip = None
        for token in [t for t in tokens if t]:
            clip = await helix.create_clip(client_id, token, broadcaster_id)
            if clip:
                break
        if not clip:
            logger.warning("Clip failed (both tokens). Does either token have the clips:edit scope?")
            return None

        channel = self.config.get('TWITCH_CHANNEL', '')
        note = f"clip saved by {requested_by}: {clip['url']}" if requested_by else f"clip saved: {clip['url']}"
        logger.info(note)
        if self.session_summarizer:
            self.session_summarizer.note_event(channel, f"a clip was made ({requested_by or 'chat'})")
        if self.stream_recap:
            self.stream_recap.record_event(note)
        if self.twitch_client and self.twitch_client.is_connected():
            await self.twitch_client.send_message(note)
        return clip

    async def _handle_raid_event(self, event: Dict[str, Any]) -> None:
        """
        Handle raid events from IRC USERNOTICE
        
        Args:
            event: Raid event data from IRC
        """
        try:
            # IRC raid event contains (from twitch_client.py):
            # - from_broadcaster_login
            # - from_broadcaster_name
            # - viewers
            
            raider_name = event.get('from_broadcaster_name', 'Unknown')
            viewer_count = event.get('viewers', 0)
            
            logger.info(f"Raid event: {raider_name} with {viewer_count} viewers")

            if self.session_summarizer:
                self.session_summarizer.note_event(
                    self.config.get('TWITCH_CHANNEL', ''),
                    f"{raider_name} raided with {viewer_count} viewers"
                )
            if self.first_timer:
                self.first_timer.note_raid()  # raiders aren't "first-timers"; suppress greetings
            if self.stream_recap:
                self.stream_recap.record_event(f"{raider_name} raided with {viewer_count} viewers")

            # If raider welcome is enabled, pass to it
            if self.raider_welcome:
                await self.raider_welcome.handle_raid({
                    'from_broadcaster_name': raider_name,
                    'viewers': viewer_count
                })
            else:
                # Simple announcement if no raider welcome
                message = f"Welcome raiders from {raider_name}! Thanks for bringing {viewer_count} viewers!"
                await self.twitch_client.send_message(message)
                
        except Exception as e:
            logger.error(f"Error handling raid event: {e}")
    
    async def _handle_ad_commands(self, message: Dict[str, Any]) -> None:
        """
        Handle ad-related commands from chat
        
        Args:
            message: Chat message
        """
        try:
            text = message.get('text', '').lower().strip()
            username = message.get('username', '').lower()
            
            # Only allow streamer/mods to use ad commands
            is_mod = message.get('is_mod', False)
            is_broadcaster = username == self.config.get('TWITCH_CHANNEL', '').lower()
            
            if not (is_mod or is_broadcaster):
                return
                
            # Check for ad commands
            if text.startswith('!ad'):
                parts = text.split()
                
                if parts[0] == '!ad':
                    # Manual ad break: !ad [duration]
                    duration = 90  # Default 90 seconds (standard Twitch ad)
                    if len(parts) > 1 and parts[1].isdigit():
                        duration = min(int(parts[1]), 180)  # Max 3 minutes
                        
                    await self.ad_announcer.start_ad_break(duration, manual=True)
                    
                elif parts[0] == '!adstatus':
                    # Check ad status
                    status = self.ad_announcer.get_status()
                    if status['ad_active']:
                        msg = f"Ad break active: {status['time_remaining']}s remaining"
                    else:
                        msg = f"No ad break active | Announcer: {'Enabled' if status['enabled'] else 'Disabled'}"
                    await self.twitch_client.send_message(msg)
                    
                elif parts[0] == '!adtoggle':
                    # Toggle ad announcer
                    current = self.ad_announcer.enabled
                    self.ad_announcer.update_settings({'enabled': not current})
                    await self.twitch_client.send_message(
                        f"Ad announcer {'enabled' if not current else 'disabled'}"
                    )
                    
        except Exception as e:
            logger.error(f"Error handling ad command: {e}")
    
    async def _start_api_server(self) -> None:
        """
        Launch the V2 FastAPI app inside the bot's event loop (API_ENABLED=true).
        Import is lazy so disabled mode never pays the heavy app import.
        """
        try:
            import uvicorn
            from src.api.app import app as api_app
            from src.api import dependencies as api_dependencies

            # Endpoints reach the bot two ways; both must see this instance
            api_app.state.bot = self
            api_dependencies.set_bot_instance(self)

            port = int(self.config.get('API_PORT', 8000))
            server_config = uvicorn.Config(
                api_app,
                host='127.0.0.1',  # auth middleware is a stub - localhost only
                port=port,
                # lifespan="off": the app's standalone lifespan would auto-start
                # a second TalkBot (second PyAudio). Endpoints need only app.state.bot.
                lifespan='off',
                log_level='warning',
            )
            self.api_server = uvicorn.Server(server_config)
            # The bot owns SIGINT/SIGTERM; uvicorn must not claim them
            self.api_server.install_signal_handlers = lambda: None

            self.task_registry.create_task(
                self._serve_api(),
                name="api_server"
            )
            logger.info(f"Embedded API server starting on http://127.0.0.1:{port}")
        except Exception as e:
            # Bot survival is strictly senior to dashboard availability
            self.api_server = None
            logger.error(f"Embedded API server failed to start (bot continues): {e}")

    async def _serve_api(self) -> None:
        """Run uvicorn inside the bot loop; an API failure must never take the bot down."""
        try:
            await self.api_server.serve()
            logger.info("Embedded API server stopped")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Embedded API server crashed (bot continues): {e}")

    async def shutdown(self) -> None:
        """
        Gracefully shutdown all bot components
        """
        logger.info("Starting graceful shutdown...")
        self.running = False

        # Recap needs the LLM and the task registry, so it goes before either is torn down
        try:
            await self._write_recap("shutdown")
        except Exception as e:
            logger.error(f"Recap on shutdown failed: {e}")

        # Stop accepting API requests before the components endpoints read go away
        if self.api_server:
            self.api_server.should_exit = True
        
        # Cancel all tasks via TaskRegistry
        await self.task_registry.shutdown()
        
        # Shutdown components in reverse order
        if hasattr(self, 'token_refresher'):
            try:
                await self.token_refresher.stop()
            except Exception as e:
                logger.error(f"Error stopping token refresher: {e}")

        if self.response_coordinator:
            await self.response_coordinator.stop_dead_air_prevention()

        if self.voice_recognition:
            self.voice_recognition.stop_listening()
            
        if self.realtime_backend:
            try:
                await self.realtime_backend.stop()
            except Exception as e:
                logger.warning(f"Realtime backend shutdown error: {e}")
            self.realtime_backend = None

        if self.vad_ducking:
            self.vad_ducking.shutdown()
            
        if self.websocket_manager:
            await self.websocket_manager.shutdown()
            
        if self.audio_queue:
            await self.audio_queue.shutdown()
            
        if self.twitch_client:
            await self.twitch_client.disconnect()
            
        if self.personality_engine:
            await self.personality_engine.shutdown()
            
        if self.memory_system:
            # ResilientMemorySystem doesn't have shutdown method, just close connections
            try:
                if hasattr(self.memory_system, 'redis_client') and self.memory_system.redis_client:
                    await self.memory_system.redis_client.aclose()
                if hasattr(self.memory_system, 'db_manager') and self.memory_system.db_manager:
                    await self.memory_system.db_manager.cleanup()
            except Exception as e:
                logger.error(f"Error during memory system cleanup: {e}")
            
        logger.info("Shutdown complete")
        
    def get_stats(self) -> Dict[str, Any]:
        """
        Get current bot statistics
        """
        uptime = (datetime.now() - self.start_time).total_seconds()
        avg_response_time = sum(self.response_times[-100:]) / min(100, len(self.response_times)) if self.response_times else 0
        
        return {
            'uptime': uptime,
            'message_count': self.message_count,
            'audio_count': self.audio_count,
            'avg_response_time': avg_response_time,
            'active_tasks': len(self.task_registry.active_tasks),
            'services': list(self.service_registry.services.keys())
        }
    
    async def _on_follow(self, event: dict):
        """Handle follow event."""
        if hasattr(self, 'event_announcer'):
            await self.event_announcer.handle_follow(event)

    async def _on_subscribe(self, event: dict):
        """Handle subscribe event."""
        if hasattr(self, 'event_announcer'):
            # Check if it's a resub
            if event.get('cumulative_months', 1) > 1:
                await self.event_announcer.handle_resub(event)
            elif event.get('is_gift'):
                await self.event_announcer.handle_gift_sub(event)
            else:
                await self.event_announcer.handle_subscribe(event)

    async def _on_cheer(self, event: dict):
        """Handle cheer event."""
        if hasattr(self, 'event_announcer'):
            await self.event_announcer.handle_cheer(event)

    async def on_raid(self, event: dict):
        """Handle raid events with dynamic LLM-powered welcomes"""
        if hasattr(self, 'raider_welcome') and self.raider_welcome:
            # Update current game context if available
            if hasattr(self, 'current_game') and self.current_game:
                self.raider_welcome.set_current_game(self.current_game)

            # Fire and forget with timeout
            self.task_registry.create_task(
                self.raider_welcome.handle_raid(event),
                name=f"raid_welcome_{event.get('from_broadcaster_login', 'unknown')}",
                timeout=3.0
            )
    
    def _load_feature_flags(self) -> Dict[str, bool]:
        """Load feature flags from configuration file per PRD section 10"""
        try:
            import json
            import os
            
            flags_file = 'feature_flags.json'
            if os.path.exists(flags_file):
                with open(flags_file, 'r') as f:
                    data = json.load(f)
                    return data.get('flags', {})
            else:
                # Default flags per PRD
                return {
                    "raider_welcome": False,  # Disabled by default
                    "raider_vod_analysis": False,  # Even more optional
                    "advanced_personality": False,  # Future enhancement
                }
        except Exception as e:
            logger.error(f"Error loading feature flags: {e}")
            # Return safe defaults on error
            return {
                "raider_welcome": False,
                "raider_vod_analysis": False,
                "advanced_personality": False,
            }

async def main():
    """
    Main entry point for TalkBot
    """
    # Load configuration from environment
    config = {
        'DATABASE_URL': os.getenv('DATABASE_URL', 'postgresql+asyncpg://postgres:postgres@localhost:5433/streambot'),
        'REDIS_URL': os.getenv('REDIS_URL', 'redis://localhost:6379'),
        'OPENAI_API_KEY': os.getenv('OPENAI_API_KEY'),
        'TWITCH_ACCESS_TOKEN': os.getenv('TWITCH_ACCESS_TOKEN'),
        'TWITCH_CLIENT_ID': os.getenv('TWITCH_CLIENT_ID'),
        'TWITCH_CHANNEL': os.getenv('TWITCH_CHANNEL', 'confusedamish'),
        'TWITCH_BOT_USERNAME': os.getenv('TWITCH_BOT_USERNAME', 'elimist_'),
        'TTS_ENABLED': os.getenv('TTS_ENABLED', 'true').lower() == 'true',
        'VOICE_INPUT_ENABLED': os.getenv('VOICE_INPUT_ENABLED', 'true').lower() == 'true',
        'VOICE_ENABLED': os.getenv('VOICE_ENABLED', 'true').lower() == 'true',
        'DEBUG': os.getenv('DEBUG', 'false').lower() == 'true'
    }
    
    # Validate required configuration
    required = ['OPENAI_API_KEY', 'TWITCH_ACCESS_TOKEN', 'TWITCH_CLIENT_ID']
    missing = [key for key in required if not config.get(key)]
    if missing:
        logger.error(f"Missing required configuration: {missing}")
        sys.exit(1)
        
    # Create and run bot
    bot = TalkBot(config)
    await bot.setup()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())
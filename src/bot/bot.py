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
from typing import Optional, Dict, Any, List
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
from bot.optimized_context_builder import OptimizedContextBuilder
from bot.channel_chat_buffer import ChannelChatBuffer
from core.bot_state import BotState
from core.network_resilience import get_resilience
from bot.response_coordinator import ResponseCoordinator
from twitch.eventsub_websocket import EventSubWebSocket
from features.ad_announcer import AdAnnouncer
from features.event_announcer import EventAnnouncer
from monitoring.metrics_collector import MetricsCollector

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
        self.response_coordinator: Optional[ResponseCoordinator] = None  # PRD critical component
        self.vad_ducking = None  # VAD ducking for natural interrupts
        self.eventsub: Optional[EventSubWebSocket] = None  # EventSub for automatic ad detection
        self.ad_announcer: Optional[AdAnnouncer] = None  # Ad announcer

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

            # Initialize ChannelChatBuffer for real-time conversational context
            logger.info("Initializing ChannelChatBuffer...")
            self.chat_buffer = ChannelChatBuffer(max_turns_per_channel=50)
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
                response_coordinator=self.response_coordinator
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
                self.voice_recognition = VoiceRecognition(tts_service=self.audio_queue)
                
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
                    
                    # Start voice processing task
                    self.task_registry.create_task(
                        self._process_voice_commands(),
                        name="voice_processor"
                    )
                else:
                    logger.warning("Voice recognition failed to start")
                    
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
            
            # Wait for Twitch connection to complete
            await twitch_connect_task
            logger.info("Twitch connection established")
            
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
                                text=strip_mentions_for_tts(response['text']),
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
                else:
                    # Fallback to direct sending if coordinator not available
                    await self.twitch_client.send_message(response['text'])
                    if self.config.get('TTS_ENABLED', True):
                        await self.audio_queue.queue_audio(
                            text=strip_mentions_for_tts(response['text']),
                            priority=priority
                        )
                        self.audio_count += 1

                # Add bot's response to chat buffer
                self.chat_buffer.append_assistant(
                    channel=self.config.get('TWITCH_CHANNEL', ''),
                    username=self.config.get('TWITCH_BOT_USERNAME', 'bot'),
                    message=response['text']
                )

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
                
                # Check memory usage
                memory_stats = self.memory_system.get_stats()  # ResilientMemorySystem.get_stats() is not async
                
                # Check audio queue health
                audio_stats = self.audio_queue.get_stats()
                
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

            # Add to real-time chat buffer
            self.chat_buffer.append_viewer(
                channel=message.get('channel', ''),
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
            
            # Get personality response - force response for voice
            response = await self.personality_engine.generate_response(
                message=message.get('text'),
                context=context,
                user=message.get('username'),
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
                                text=strip_mentions_for_tts(response['text']),
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
                            text=strip_mentions_for_tts(response['text']),
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
    
    async def _handle_voice_input(self, text: str) -> None:
        """
        Handle voice input from recognition system
        Only respond during dead air periods to avoid spam
        
        Args:
            text: Recognized text from voice
        """
        try:
            logger.info(f"[VOICE INPUT] Received: '{text}'")
            
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
            
            # 1. Respond to various "hey" greetings directed at the bot
            # Include common misrecognitions
            hey_triggers = ['hey bot', 'hey talkbot', 'hey bud', 'hey buddy', 
                          'hey boss', 'hey there', 'hey elimist', 'yo bot',
                          'ok bot', 'alright bot', 'listen bot',
                          # Common misrecognitions of "hey bot"
                          'hey bought', 'hey but', 'hey thought', 'hey bart',
                          'hey bott', 'hay bot', 'hey bod', 'hey pot']
            
            if any(trigger in text_lower for trigger in hey_triggers):
                needs_response = True
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
            
    async def _process_voice_commands(self) -> None:
        """
        Process queued voice commands from recognition system
        """
        while self.running:
            try:
                if self.voice_recognition:
                    # Check for queued voice text
                    text = await self.voice_recognition.get_queued_text(timeout=0.5)
                    
                    if text:
                        await self._handle_voice_input(text)
                        
                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error processing voice commands: {e}")
                await asyncio.sleep(1)
    
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
    
    async def shutdown(self) -> None:
        """
        Gracefully shutdown all bot components
        """
        logger.info("Starting graceful shutdown...")
        self.running = False
        
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
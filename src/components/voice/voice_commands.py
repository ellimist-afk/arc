"""Voice command system for TalkBot with wake word and command recognition."""
import asyncio
import logging
import re
from typing import Dict, Callable, Optional, Any, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class CommandType(Enum):
    """Types of voice commands."""
    CONTROL = "control"  # Bot control commands
    CHAT = "chat"  # Chat interaction commands
    SETTINGS = "settings"  # Settings/config commands
    MEDIA = "media"  # Media control commands
    CUSTOM = "custom"  # User-defined commands


@dataclass
class VoiceCommand:
    """Represents a voice command."""
    pattern: str  # Regex pattern to match
    callback: Callable  # Async function to call
    type: CommandType
    description: str
    requires_confirmation: bool = False
    cooldown: float = 0.0  # Seconds between uses
    last_used: float = 0.0


class VoiceCommandSystem:
    """Advanced voice command recognition and handling."""
    
    def __init__(self, bot=None):
        """Initialize voice command system.
        
        Args:
            bot: TalkBot instance for command execution
        """
        self.bot = bot
        self.commands: Dict[str, VoiceCommand] = {}
        self.wake_words = ["hey bot", "ok bot", "yo bot", "bot"]
        self.confirmation_words = ["yes", "yeah", "yep", "confirm", "do it"]
        self.cancel_words = ["no", "nope", "cancel", "nevermind", "stop"]
        
        # Command state
        self.awaiting_confirmation: Optional[VoiceCommand] = None
        self.confirmation_timeout = 5.0  # Seconds to wait for confirmation
        
        # Register default commands
        self._register_default_commands()
        
        logger.info("VoiceCommandSystem initialized")
    
    def _register_default_commands(self):
        """Register built-in voice commands."""
        
        # Control commands
        self.register_command(
            "mute",
            r"(mute|shut up|be quiet|silence)",
            self._cmd_mute,
            CommandType.CONTROL,
            "Mutes the bot temporarily"
        )
        
        self.register_command(
            "unmute",
            r"(unmute|speak|talk|you can talk)",
            self._cmd_unmute,
            CommandType.CONTROL,
            "Unmutes the bot"
        )
        
        self.register_command(
            "volume",
            r"(volume|louder|quieter|turn (up|down))",
            self._cmd_volume,
            CommandType.CONTROL,
            "Adjusts bot volume"
        )
        
        # Chat commands
        self.register_command(
            "respond_more",
            r"(talk more|be more active|respond more)",
            self._cmd_increase_response_rate,
            CommandType.CHAT,
            "Increases bot response frequency"
        )
        
        self.register_command(
            "respond_less",
            r"(talk less|be quieter|respond less|chill)",
            self._cmd_decrease_response_rate,
            CommandType.CHAT,
            "Decreases bot response frequency"
        )
        
        # Settings commands
        self.register_command(
            "change_personality",
            r"(change personality|be more (friendly|sarcastic|professional|casual))",
            self._cmd_change_personality,
            CommandType.SETTINGS,
            "Changes bot personality",
            requires_confirmation=True
        )
        
        self.register_command(
            "toggle_tts",
            r"(toggle (tts|text to speech)|turn (on|off) voice)",
            self._cmd_toggle_tts,
            CommandType.SETTINGS,
            "Toggles text-to-speech"
        )
        
        # Media commands
        self.register_command(
            "skip",
            r"(skip|next|stop talking)",
            self._cmd_skip_audio,
            CommandType.MEDIA,
            "Skips current audio"
        )
        
        self.register_command(
            "repeat",
            r"(repeat|say that again|what did you say)",
            self._cmd_repeat_last,
            CommandType.MEDIA,
            "Repeats last message"
        )
    
    def register_command(
        self,
        name: str,
        pattern: str,
        callback: Callable,
        type: CommandType,
        description: str,
        requires_confirmation: bool = False,
        cooldown: float = 0.0
    ):
        """Register a new voice command.
        
        Args:
            name: Command identifier
            pattern: Regex pattern to match
            callback: Async function to execute
            type: Command category
            description: Human-readable description
            requires_confirmation: Whether to confirm before executing
            cooldown: Minimum seconds between uses
        """
        self.commands[name] = VoiceCommand(
            pattern=pattern,
            callback=callback,
            type=type,
            description=description,
            requires_confirmation=requires_confirmation,
            cooldown=cooldown
        )
        logger.info(f"Registered voice command: {name}")
    
    async def process_input(self, text: str) -> bool:
        """Process voice input for commands.
        
        Args:
            text: Recognized voice text
            
        Returns:
            True if command was handled, False otherwise
        """
        text_lower = text.lower().strip()
        
        # Check if we're awaiting confirmation
        if self.awaiting_confirmation:
            return await self._handle_confirmation(text_lower)
        
        # Check for wake word
        wake_word_found = any(wake in text_lower for wake in self.wake_words)
        
        # Process commands
        for name, command in self.commands.items():
            if re.search(command.pattern, text_lower):
                # Check if wake word required
                if not wake_word_found and command.type != CommandType.MEDIA:
                    continue  # Skip non-media commands without wake word
                
                # Check cooldown
                import time
                current_time = time.time()
                if command.cooldown > 0:
                    time_since_last = current_time - command.last_used
                    if time_since_last < command.cooldown:
                        logger.debug(f"Command {name} on cooldown ({time_since_last:.1f}s < {command.cooldown}s)")
                        continue
                
                # Execute or request confirmation
                if command.requires_confirmation:
                    await self._request_confirmation(command)
                else:
                    await self._execute_command(command)
                    command.last_used = current_time
                
                return True
        
        return False
    
    async def _handle_confirmation(self, text: str) -> bool:
        """Handle confirmation response.
        
        Args:
            text: User's response text
            
        Returns:
            True if handled
        """
        if any(word in text for word in self.confirmation_words):
            # Execute the pending command
            await self._execute_command(self.awaiting_confirmation)
            self.awaiting_confirmation = None
            return True
        elif any(word in text for word in self.cancel_words):
            # Cancel the pending command
            if self.bot:
                await self.bot.audio_queue.queue_audio(
                    "Command cancelled",
                    priority="high"
                )
            self.awaiting_confirmation = None
            return True
        
        return False
    
    async def _request_confirmation(self, command: VoiceCommand):
        """Request confirmation for a command.
        
        Args:
            command: Command requiring confirmation
        """
        self.awaiting_confirmation = command
        
        if self.bot:
            await self.bot.audio_queue.queue_audio(
                f"Are you sure you want to {command.description.lower()}? Say yes or no.",
                priority="high"
            )
        
        # Set timeout to cancel confirmation
        asyncio.create_task(self._confirmation_timeout())
    
    async def _confirmation_timeout(self):
        """Cancel confirmation after timeout."""
        await asyncio.sleep(self.confirmation_timeout)
        if self.awaiting_confirmation:
            self.awaiting_confirmation = None
            if self.bot:
                await self.bot.audio_queue.queue_audio(
                    "Command timeout, cancelled",
                    priority="low"
                )
    
    async def _execute_command(self, command: VoiceCommand):
        """Execute a voice command.
        
        Args:
            command: Command to execute
        """
        try:
            logger.info(f"Executing voice command: {command.description}")
            await command.callback()
        except Exception as e:
            logger.error(f"Error executing voice command: {e}")
            if self.bot:
                await self.bot.audio_queue.queue_audio(
                    "Sorry, command failed",
                    priority="high"
                )
    
    # Command implementations
    async def _cmd_mute(self):
        """Mute the bot."""
        if self.bot:
            self.bot.muted = True
            await self.bot.audio_queue.queue_audio(
                "Muted",
                priority="high"
            )
            logger.info("Bot muted via voice command")
    
    async def _cmd_unmute(self):
        """Unmute the bot."""
        if self.bot:
            self.bot.muted = False
            await self.bot.audio_queue.queue_audio(
                "Unmuted, I'm back!",
                priority="high"
            )
            logger.info("Bot unmuted via voice command")
    
    async def _cmd_volume(self):
        """Adjust volume (placeholder)."""
        if self.bot:
            await self.bot.audio_queue.queue_audio(
                "Volume control not yet implemented",
                priority="high"
            )
    
    async def _cmd_increase_response_rate(self):
        """Increase response frequency."""
        if self.bot and self.bot.personality_engine:
            current = self.bot.personality_engine.current_traits.chattiness
            new_value = min(100, current + 20)
            self.bot.personality_engine.current_traits.chattiness = new_value
            await self.bot.audio_queue.queue_audio(
                f"I'll be more talkative now",
                priority="high"
            )
            logger.info(f"Increased chattiness: {current} -> {new_value}")
    
    async def _cmd_decrease_response_rate(self):
        """Decrease response frequency."""
        if self.bot and self.bot.personality_engine:
            current = self.bot.personality_engine.current_traits.chattiness
            new_value = max(0, current - 20)
            self.bot.personality_engine.current_traits.chattiness = new_value
            await self.bot.audio_queue.queue_audio(
                f"I'll talk less now",
                priority="high"
            )
            logger.info(f"Decreased chattiness: {current} -> {new_value}")
    
    async def _cmd_change_personality(self):
        """Change personality preset."""
        if self.bot and self.bot.personality_engine:
            # Simple cycle through presets
            presets = ["friendly", "sarcastic", "professional", "casual"]
            current = self.bot.personality_engine.preset_name
            try:
                current_idx = presets.index(current)
                new_preset = presets[(current_idx + 1) % len(presets)]
            except ValueError:
                new_preset = "friendly"
            
            await self.bot.personality_engine.load_preset(new_preset)
            await self.bot.audio_queue.queue_audio(
                f"Personality changed to {new_preset}",
                priority="high"
            )
            logger.info(f"Changed personality to {new_preset}")
    
    async def _cmd_toggle_tts(self):
        """Toggle TTS on/off."""
        if self.bot:
            self.bot.tts_enabled = not getattr(self.bot, 'tts_enabled', True)
            status = "on" if self.bot.tts_enabled else "off"
            await self.bot.audio_queue.queue_audio(
                f"Text to speech is now {status}",
                priority="high"
            )
            logger.info(f"TTS toggled to {status}")
    
    async def _cmd_skip_audio(self):
        """Skip current audio."""
        if self.bot and self.bot.audio_queue:
            # Clear the queue (it's a list, not a Queue)
            self.bot.audio_queue.queue.clear()
            logger.info("Skipped audio queue")
    
    async def _cmd_repeat_last(self):
        """Repeat last message."""
        if self.bot:
            # Get last message from bot's history
            last_message = getattr(self.bot, 'last_response', None)
            if last_message:
                await self.bot.audio_queue.queue_audio(
                    last_message,
                    priority="high"
                )
            else:
                await self.bot.audio_queue.queue_audio(
                    "Nothing to repeat",
                    priority="high"
                )
    
    def get_help(self) -> List[str]:
        """Get list of available commands.
        
        Returns:
            List of command descriptions
        """
        help_text = []
        for name, command in self.commands.items():
            help_text.append(f"{name}: {command.description}")
        return help_text
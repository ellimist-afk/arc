"""
PersonalityEngine with 4 presets and custom configuration
"""

import logging
import json
import os
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, asdict, replace
from enum import Enum
import random
from openai import AsyncOpenAI
import sys
from pathlib import Path

# Add src to path for imports
if str(Path(__file__).parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from core.network_resilience import get_resilience
from core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from personality.repetition_guard import RepetitionGuard

logger = logging.getLogger(__name__)

class PersonalityPreset(Enum):
    """Personality preset types"""
    FRIENDLY = "friendly"
    SASSY = "sassy"
    EDUCATIONAL = "educational"
    CHAOTIC = "chaotic"
    CUSTOM = "custom"

@dataclass
class PersonalityTraits:
    """Personality trait configuration (0-100 scale)"""
    humor: int = 50
    formality: int = 50
    enthusiasm: int = 50
    sarcasm: int = 20
    helpfulness: int = 80
    chattiness: int = 60
    creativity: int = 50
    empathy: int = 70
    assertiveness: int = 50
    curiosity: int = 60
    
    def validate(self) -> None:
        """Validate trait values are within bounds"""
        for field, value in asdict(self).items():
            if not 0 <= value <= 100:
                raise ValueError(f"Trait {field} must be between 0-100, got {value}")

class PersonalityEngine:
    """
    Manages bot personality and response generation
    Supports 4 presets + custom with <1s switching
    """
    
    # Load all presets from file if available
    @classmethod
    def load_presets(cls):
        """Load personality presets from file"""
        presets = {}
        try:
            import json
            with open('all_personalities.json', 'r') as f:
                personalities = json.load(f)
                # Convert first 4 to enum presets for compatibility
                if 'friendly' in personalities:
                    presets[PersonalityPreset.FRIENDLY] = PersonalityTraits(**personalities['friendly']['traits'])
                if 'sassy' in personalities:
                    presets[PersonalityPreset.SASSY] = PersonalityTraits(**personalities['sassy']['traits'])
                if 'educational' in personalities:
                    presets[PersonalityPreset.EDUCATIONAL] = PersonalityTraits(**personalities['educational']['traits'])
                if 'chaotic' in personalities:
                    presets[PersonalityPreset.CHAOTIC] = PersonalityTraits(**personalities['chaotic']['traits'])
        except:
            # Fallback to defaults
            presets = {
                PersonalityPreset.FRIENDLY: PersonalityTraits(
                    humor=70, formality=30, enthusiasm=80, sarcasm=10, helpfulness=90,
                    chattiness=70, creativity=60, empathy=85, assertiveness=40, curiosity=70
                ),
                PersonalityPreset.SASSY: PersonalityTraits(
                    humor=50, formality=20, enthusiasm=60, sarcasm=90, helpfulness=60,
                    chattiness=80, creativity=70, empathy=50, assertiveness=80, curiosity=60
                ),
                PersonalityPreset.EDUCATIONAL: PersonalityTraits(
                    humor=10, formality=70, enthusiasm=60, sarcasm=5, helpfulness=95,
                    chattiness=50, creativity=40, empathy=70, assertiveness=60, curiosity=90
                ),
                PersonalityPreset.CHAOTIC: PersonalityTraits(
                    humor=50, formality=10, enthusiasm=95, sarcasm=60, helpfulness=50,
                    chattiness=90, creativity=95, empathy=40, assertiveness=70, curiosity=80
                )
            }
        return presets
    
    # Load presets on class definition
    PRESETS = {}
    
    def __init__(
        self,
        memory_system: Any,
        openai_api_key: Optional[str] = None,
        config_path: str = "personality_settings",
        openai_base_url: Optional[str] = None,
    ):
        """
        Initialize the personality engine

        Args:
            memory_system: Memory system for context
            openai_api_key: Optional OpenAI API key for response generation
            config_path: Path to personality configuration files
            openai_base_url: Optional OpenAI-compatible endpoint (Ollama,
                LM Studio, vLLM, OpenRouter...). Falls back to the
                OPENAI_BASE_URL env var. Only affects chat completions —
                TTS keeps its own client on the real OpenAI API.
        """
        self.memory_system = memory_system
        self.config_path = config_path
        self.openai_base_url = openai_base_url or os.getenv('OPENAI_BASE_URL') or None
        if openai_api_key:
            self.openai_client = AsyncOpenAI(api_key=openai_api_key, base_url=self.openai_base_url)
            if self.openai_base_url:
                logger.info(f"LLM endpoint: {self.openai_base_url}")
        else:
            self.openai_client = None
        self.resilience = get_resilience()

        # Anti-loop guard over the bot's own recent outputs. Thresholds are
        # tunable via bot_settings.json -> repetition_guard (history_size is
        # construction-only).
        self.repetition_guard = RepetitionGuard()
        self.repetition_guard_enabled = True
        self.repetition_rejections = 0
        self.repetition_forced = 0  # mention/voice replies delivered despite a failed retry

        # LLM model and streamer identity are configurable via bot_settings.json
        self.llm_model = "gpt-4o-mini"
        self.streamer_name: Optional[str] = None
        self.reload_llm_settings()
        
        # Initialize circuit breaker for OpenAI API
        self.circuit_breaker = CircuitBreaker(
            name="OpenAI_API",
            failure_threshold=3,
            recovery_timeout=30.0,
            success_threshold=2,
            expected_exception=Exception
        )
        
        # Load presets if not already loaded
        if not self.PRESETS:
            PersonalityEngine.PRESETS = PersonalityEngine.load_presets()
        
        # Current personality state
        self.current_preset = PersonalityPreset.FRIENDLY
        self.current_traits = replace(self.PRESETS[PersonalityPreset.FRIENDLY])
        self.custom_traits: Optional[PersonalityTraits] = None
        
        # Store all personalities for extended access
        self.all_personalities = {}
        try:
            import json
            with open('all_personalities.json', 'r') as f:
                self.all_personalities = json.load(f)
        except:
            pass
        
        # Response modifiers based on traits
        self.response_modifiers: Dict[str, Any] = {}
        
        # Performance tracking
        self.responses_generated = 0
        self.total_response_time = 0
        self.last_switch_time = datetime.now()
        
    def reload_llm_settings(self) -> None:
        """Load llm_model and streamer_name from bot_settings.json"""
        try:
            with open('bot_settings.json', 'r') as f:
                settings = json.load(f)
            self.llm_model = settings.get('llm_model', self.llm_model)
            self.streamer_name = settings.get('streamer_name', self.streamer_name)

            guard_cfg = settings.get('repetition_guard') or {}
            self.repetition_guard_enabled = bool(guard_cfg.get('enabled', True))
            for key in ('similarity_threshold', 'opening_cooldown',
                        'phrase_cooldown', 'catchphrase_min_uses'):
                if key in guard_cfg:
                    setattr(self.repetition_guard, key, guard_cfg[key])
        except Exception as e:
            logger.debug(f"Could not load LLM settings from bot_settings.json: {e}")

    async def initialize(self) -> None:
        """Initialize the personality engine"""
        logger.info("Initializing PersonalityEngine...")
        
        # Create config directory if it doesn't exist
        os.makedirs(self.config_path, exist_ok=True)
        
        # Load saved personality if exists
        await self.load_personality()
        
        logger.info(f"PersonalityEngine initialized with preset: {self.current_preset.value}")
        
    async def load_personality(self, streamer_id: str = "default") -> None:
        """
        Load personality configuration from file
        
        Args:
            streamer_id: Streamer ID for personality file
        """
        config_file = os.path.join(self.config_path, f"{streamer_id}.json")
        
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    
                # Load preset or custom traits
                if config.get('preset'):
                    preset = PersonalityPreset(config['preset'])
                    await self.switch_preset(preset)
                elif config.get('traits'):
                    traits = PersonalityTraits(**config['traits'])
                    await self.set_custom_traits(traits)
                    
                logger.info(f"Loaded personality from {config_file}")
                
            except Exception as e:
                logger.error(f"Failed to load personality config: {e}")
                
    async def save_personality(self, streamer_id: str = "default") -> None:
        """
        Save current personality configuration
        
        Args:
            streamer_id: Streamer ID for personality file
        """
        config_file = os.path.join(self.config_path, f"{streamer_id}.json")
        
        config = {
            'preset': self.current_preset.value,
            'traits': asdict(self.current_traits),
            'modified': datetime.now().isoformat()
        }
        
        try:
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info(f"Saved personality to {config_file}")
        except Exception as e:
            logger.error(f"Failed to save personality config: {e}")
            
    async def switch_personality_by_name(self, name: str) -> bool:
        """
        Switch to a personality by name (includes all extended personalities)
        
        Args:
            name: Personality name (e.g., 'roast', 'cozy', 'chaos_goblin')
            
        Returns:
            True if switched successfully
        """
        if name.lower() in self.all_personalities:
            personality = self.all_personalities[name.lower()]
            traits = PersonalityTraits(**personality['traits'])
            await self.set_custom_traits(traits)
            logger.info(f"Switched to {name} personality")
            return True
        return False
    
    async def switch_preset(self, preset: PersonalityPreset) -> None:
        """
        Switch to a personality preset (<1s as per spec)
        
        Args:
            preset: Preset to switch to
        """
        start_time = datetime.now()
        
        if preset == PersonalityPreset.CUSTOM:
            if self.custom_traits:
                self.current_traits = replace(self.custom_traits)
            else:
                logger.warning("No custom traits defined, using friendly preset")
                self.current_traits = replace(self.PRESETS[PersonalityPreset.FRIENDLY])
        else:
            self.current_traits = replace(self.PRESETS[preset])
            
        self.current_preset = preset
        self._update_response_modifiers()
        
        # Track switch time
        switch_time = (datetime.now() - start_time).total_seconds()
        self.last_switch_time = datetime.now()
        
        logger.info(f"Switched to {preset.value} personality in {switch_time:.3f}s")
        
    async def set_custom_traits(self, traits: PersonalityTraits) -> None:
        """
        Set custom personality traits
        
        Args:
            traits: Custom trait configuration
        """
        traits.validate()
        self.custom_traits = traits
        self.current_traits = traits
        self.current_preset = PersonalityPreset.CUSTOM
        self._update_response_modifiers()
        
        logger.info("Applied custom personality traits")
        
    def _update_response_modifiers(self) -> None:
        """Update response modifiers based on current traits"""
        traits = self.current_traits
        
        self.response_modifiers = {
            'temperature': 0.7 + (traits.creativity / 200),  # 0.7-1.2
            'max_tokens': 100 + int(traits.chattiness * 2.5),  # 100-350
            'presence_penalty': -0.5 + (traits.assertiveness / 100),  # -0.5 to 0.5
            'frequency_penalty': (traits.creativity / 200),  # 0-0.5
            'use_emojis': traits.enthusiasm > 70,
            'use_caps': traits.enthusiasm > 80 and traits.formality < 30,
            'response_style': self._determine_response_style()
        }
        
    def _determine_response_style(self) -> str:
        """Determine response style based on traits"""
        traits = self.current_traits
        
        if traits.sarcasm > 70:
            return "sarcastic"
        elif traits.formality > 70:
            return "formal"
        elif traits.humor > 70:
            return "witty"
        elif traits.helpfulness > 80:
            return "helpful"
        elif traits.enthusiasm > 80:
            return "enthusiastic"
        else:
            return "balanced"
            
    async def generate_response(
        self,
        message: str,
        context: Dict[str, Any],
        user: str,
        is_mention: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Generate a response based on personality
        
        Args:
            message: Input message
            context: Context from memory system
            user: Username
            is_mention: Whether user mentioned the bot
            
        Returns:
            Response dictionary with text and metadata
        """
        start_time = datetime.now()
        
        try:
            # Update response modifiers first
            self._update_response_modifiers()
            
            # Build personality prompt
            # Special handling for dead air filler requests
            if message == "[DEAD_AIR_FILLER]":
                prompt = "Generate ONLY a very short casual twitch chat message (3-6 words max). Be natural. Examples: 'anyone there' or 'chat seems quiet' or 'whats up chat'. IMPORTANT: Output ONLY the message text, nothing else. No punctuation. No capitals. No sarcasm about dead air."
            else:
                prompt = self._build_personality_prompt()
            
            # Determine if should respond (always respond to dead air)
            if message != "[DEAD_AIR_FILLER]" and not self._should_respond(message, is_mention):
                return None
                
            # Generate response text
            response_text = await self._generate_text(
                message=message,
                context=context,
                user=user,
                prompt=prompt
            )
            
            if not response_text:
                return None

            # Anti-loop: reject near-repeats of our own recent output, retry
            # once with an explicit "don't say that again" hint
            response_text = await self._enforce_variety(
                response_text, message, context, user, prompt, is_mention
            )
            if not response_text:
                return None
            self.repetition_guard.record(response_text)

            # Keep the original for TTS — punctuation drives speech pacing
            speech_text = response_text

            # Apply personality modifications (chat-text styling only)
            response_text = self._apply_personality_modifications(response_text)
            
            # Track performance
            self.responses_generated += 1
            response_time = (datetime.now() - start_time).total_seconds()
            self.total_response_time += response_time
            
            should_speak = self._should_speak(message, is_mention)
            logger.info(f"PersonalityEngine: is_mention={is_mention}, should_speak={should_speak}, message='{message[:50]}...'")
            
            return {
                'text': response_text,
                'speech_text': speech_text,
                'should_speak': should_speak,
                'personality': self.current_preset.value,
                'traits': asdict(self.current_traits),
                'response_time': response_time
            }
            
        except Exception as e:
            logger.error(f"Failed to generate response: {e}")
            return None
            
    async def _enforce_variety(
        self,
        text: str,
        message: str,
        context: Dict[str, Any],
        user: str,
        prompt: str,
        is_mention: bool,
    ) -> Optional[str]:
        """
        Run the repetition guard over a draft. On failure, regenerate once with
        the guard's avoid-hint appended to the system prompt.

        Returns the text to deliver, or None to stay silent. Mentions and voice
        (is_mention=True) must always get a reply, so the less-repetitive of the
        two drafts is returned even if both fail; unsolicited chatter is dropped
        instead — a skipped interjection is invisible, a looping one is not.
        """
        if not self.repetition_guard_enabled:
            return text

        verdict = self.repetition_guard.check(text)
        if verdict.ok:
            return text

        self.repetition_rejections += 1
        logger.info(f"Repetition guard rejected draft ({verdict.reason}); regenerating")

        retry_prompt = prompt + "\n\n" + self.repetition_guard.avoid_hint(verdict)
        retry = await self._generate_text(
            message=message, context=context, user=user, prompt=retry_prompt
        )
        if retry:
            retry_verdict = self.repetition_guard.check(retry)
            if retry_verdict.ok:
                return retry
            if not is_mention:
                logger.info(f"Repetition guard: retry also repetitive ({retry_verdict.reason}); skipping")
                return None
            self.repetition_forced += 1
            best = retry if retry_verdict.score <= verdict.score else text
            logger.warning(
                f"Repetition guard: both drafts repetitive, delivering least-bad "
                f"({min(retry_verdict.score, verdict.score):.2f}) because reply is required"
            )
            return best

        if is_mention:
            self.repetition_forced += 1
            return text
        return None

    async def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int = 300,
        temperature: float = 0.3,
    ) -> Optional[str]:
        """
        Plain chat completion on the configured model, behind the same circuit
        breaker as response generation. For side tasks (session summary) that
        need an LLM but none of the personality framing.
        """
        if not self.openai_client:
            return None

        async def call():
            response = await self.openai_client.chat.completions.create(
                model=self.llm_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content

        try:
            return await self.circuit_breaker.call(call)
        except CircuitBreakerOpenError as e:
            logger.warning(f"Circuit breaker open, skipping completion: {e}")
            return None

    def _build_personality_prompt(self) -> str:
        """Build system prompt: substance-first co-host base, intensity modulated by traits"""
        traits = self.current_traits
        streamer = self.streamer_name or "the streamer"

        # Chattiness sets spoken length (max_tokens scales with it too)
        if traits.chattiness > 70:
            length_rule = "2-4 short spoken sentences"
        elif traits.chattiness >= 40:
            length_rule = "1-3 short spoken sentences"
        else:
            length_rule = "1-2 short spoken sentences"

        # The sass framing only makes sense when sarcasm is actually dialed up
        delivery = "sass" if traits.sarcasm >= 40 else "personality"

        prompt_parts = [
            f"You are the AI co-host on {streamer}'s Twitch stream. Your words are spoken "
            f"aloud through TTS, live — you're half of a duo, not a chat gimmick.",
            "",
            f"Core rule: {delivery} is your delivery, never your answer. ALWAYS engage with the "
            f"actual substance of what was said. Asked about a build? Judge the build. "
            f"Asked an opinion? Take a real stance and give a specific reason. A comeback "
            f"with no content is a failed response.",
            "",
            "Style:",
            f"- {length_rule}. No emoji, no lists, no asterisks, no stage directions — "
            f"this is read aloud.",
            "- Have opinions and commit to them. Hedging is boring; being wrong "
            "confidently is funnier than being safe.",
            "- Use specifics: the game being played, what just happened in chat, names "
            "of the people talking.",
            "- Vary your openings — never start two replies the same way.",
            "- Playfully mock, never punch down. Twitch-appropriate always.",
            "",
            "Personality calibration:",
        ]

        # Trait-driven modifiers so preset switching still changes the voice
        if traits.sarcasm > 70:
            prompt_parts.append(
                "- Sarcasm dialed high: dry, biting, quick with comebacks — but every "
                "comeback carries your actual take."
            )
        elif traits.sarcasm > 40:
            prompt_parts.append("- A light sarcastic edge: tease, but keep it warm.")
        else:
            prompt_parts.append("- Sincere delivery, minimal sarcasm.")
        if traits.humor > 70:
            prompt_parts.append("- Witty: go for the joke when there is one.")
        if traits.enthusiasm > 70:
            prompt_parts.append("- High energy, genuinely hyped.")
        if traits.formality > 70:
            prompt_parts.append("- Composed and articulate.")
        elif traits.formality < 30:
            prompt_parts.append("- Casual and relaxed, like talking to a friend.")
        if traits.empathy > 70:
            prompt_parts.append("- Read the room; be supportive when someone is struggling.")
        if traits.helpfulness > 70:
            prompt_parts.append("- When someone needs real help, drop the bit and actually help.")

        return "\n".join(prompt_parts)
        
    def _should_respond(self, message: str, is_mention: bool) -> bool:
        """
        Determine if bot should respond based on personality
        
        Args:
            message: Input message
            is_mention: Whether bot was mentioned
            
        Returns:
            True if should respond
        """
        # Always respond to mentions
        if is_mention:
            return True
            
        # MUCH LOWER base probability - chattiness/2000 for very rare responses
        # So 70 chattiness = 3.5% base chance
        base_probability = self.current_traits.chattiness / 2000
        
        # Very small increase for questions
        if "?" in message:
            base_probability += 0.02  # Only 2% boost for questions
            
        # Very small increase for greetings
        greetings = ["hello", "hi", "hey", "sup", "yo"]
        if any(greeting in message.lower() for greeting in greetings):
            base_probability += 0.03  # Only 3% boost for greetings
            
        # Random decision based on probability
        return random.random() < min(base_probability, 0.1)  # Cap at 10% maximum
        
    def _should_speak(self, message: str, is_mention: bool) -> bool:
        """
        Determine if response should be spoken via TTS
        
        Args:
            message: Input message
            is_mention: Whether bot was mentioned
            
        Returns:
            True if should speak
        """
        # Always speak for mentions
        if is_mention:
            return True
            
        # Speak based on enthusiasm and importance
        speak_probability = self.current_traits.enthusiasm / 200
        
        # Higher probability for questions
        if "?" in message:
            speak_probability += 0.2
            
        return random.random() < min(speak_probability, 0.6)
        
    async def _generate_text(
        self,
        message: str,
        context: Dict[str, Any],
        user: str,
        prompt: str
    ) -> Optional[str]:
        """
        Generate response text using LLM or templates
        
        Args:
            message: Input message
            context: Context dictionary
            user: Username
            prompt: System prompt
            
        Returns:
            Generated text or None
        """
        # Use OpenAI if available
        if self.openai_client:
            try:
                # Append what the context builder knows (viewer_data, history_summary,
                # engagement) — previously assembled and then discarded
                knowledge = self._format_context_knowledge(context, user)
                system_content = prompt + ("\n\n" + knowledge if knowledge else "")

                # Build messages for chat completion
                messages = [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": f"{user}: {message}"}
                ]
                
                # Add context if available
                if context.get('recent_messages'):
                    # Take last 5 messages, iterate oldest-first so insertion order
                    # is chronological (oldest just after system prompt, newest just
                    # before current message)
                    recent = context['recent_messages'][-15:]
                    for msg in reversed(recent):
                        text = msg.get('message') or msg.get('text', '')
                        if not text:
                            continue
                        username = msg.get('username', 'User')
                        role = msg.get('role', 'viewer')
                        if role == 'assistant':
                            # Bot's own past response — tell the LLM "I said this"
                            messages.insert(1, {
                                "role": "assistant",
                                "content": text
                            })
                        else:
                            # Someone in chat said this
                            messages.insert(1, {
                                "role": "user",
                                "content": f"{username}: {text}"
                            })
                        
                # Filter response_modifiers to only include valid OpenAI parameters
                openai_params = {}
                valid_params = ['temperature', 'max_tokens', 'presence_penalty', 'frequency_penalty']
                for param in valid_params:
                    if param in self.response_modifiers:
                        openai_params[param] = self.response_modifiers[param]
                
                # Define primary OpenAI call
                async def openai_call():
                    response = await self.openai_client.chat.completions.create(
                        model=self.llm_model,
                        messages=messages,
                        **openai_params
                    )
                    return response.choices[0].message.content

                # Define fallback with reduced tokens
                async def openai_fallback():
                    fallback_params = openai_params.copy()
                    fallback_params['max_tokens'] = min(fallback_params.get('max_tokens', 150), 100)
                    response = await self.openai_client.chat.completions.create(
                        model=self.llm_model,
                        messages=messages,
                        **fallback_params
                    )
                    return response.choices[0].message.content
                
                # Wrap OpenAI call with circuit breaker
                async def wrapped_openai_call():
                    return await self.resilience.call_with_resilience(
                        service_name="openai_chat",
                        primary_func=openai_call,
                        fallback_func=openai_fallback,
                        max_retries=2
                    )
                
                # Use circuit breaker for the entire resilient call
                try:
                    result = await self.circuit_breaker.call(wrapped_openai_call)
                except CircuitBreakerOpenError as e:
                    logger.warning(f"Circuit breaker open for OpenAI: {e}")
                    # Generate a simple fallback response
                    result = self._generate_simple_fallback_response(message, user)
                
                return result
                
            except Exception as e:
                logger.error(f"OpenAI generation failed after all retries: {e}")
                
        # Fallback to template responses
        return self._generate_template_response(message, user)
        
    def _format_context_knowledge(self, context: Dict[str, Any], user: str) -> str:
        """
        Format the context builder's output into a compact system-prompt block.
        Only includes what's actually present — returns "" for empty context.
        """
        lines = []

        # Widest frame first: what the stream is, then what's happened on it
        stream_now = context.get('stream_now')
        if stream_now:
            lines.append(f"- Stream right now: {stream_now}")

        summary = context.get('session_summary')
        if summary:
            lines.append(f"- Earlier this stream: {summary}")

        viewer_data = context.get('viewer_data') or {}
        if isinstance(viewer_data, dict):
            facts = []
            for key, value in viewer_data.items():
                # Only simple scalar facts; skip internals and empty values
                if key in ('from_memory', 'channel') or value in (None, '', 0, [], {}):
                    continue
                if isinstance(value, (str, int, float)):
                    facts.append(f"{key.replace('_', ' ')}: {value}")
            if facts:
                lines.append(f"- About {user}: " + ", ".join(facts))

        history = context.get('history_summary')
        if history:
            lines.append(f"- History with {user}: {history}")

        engagement = context.get('engagement_level')
        if engagement:
            lines.append(f"- Engagement level: {engagement}")

        # recent_context duplicates the chat turns inserted into messages,
        # so only use it when there is no message history to insert
        if not context.get('recent_messages') and context.get('recent_context'):
            lines.append(f"- Recent chat: {context['recent_context']}")

        # Last so it's freshest in the model's attention when it applies
        if context.get('greet_first_timer'):
            lines.append(
                f"- {user} is chatting here for the FIRST time ever. Welcome them "
                f"by name in a few words, then engage with what they actually said."
            )

        if not lines:
            return ""
        return "What you know right now:\n" + "\n".join(lines)

    def _generate_template_response(self, message: str, user: str) -> str:
        """
        Generate response from templates
        
        Args:
            message: Input message
            user: Username
            
        Returns:
            Template response
        """
        message_lower = message.lower()
        
        # Greeting responses
        if any(word in message_lower for word in ["hello", "hi", "hey"]):
            greetings = [
                f"Hey {user}!",
                f"Hello there {user}!",
                f"Hi {user} how's it going?",
                f"Hey {user} welcome!"
            ]
            return random.choice(greetings)
            
        # Question responses
        if "?" in message:
            responses = [
                "That's a good question!",
                "Hmm let me think about that...",
                "Interesting question!",
                f"Good question {user}!"
            ]
            return random.choice(responses)
            
        # Default responses
        defaults = [
            "Interesting!",
            "I see what you mean!",
            f"Thanks for sharing {user}!",
            "That's cool!"
        ]
        return random.choice(defaults)
        
    def _apply_personality_modifications(self, text: str) -> str:
        """
        Apply personality-based modifications to text
        Make bot text look like real Twitch chat
        
        Args:
            text: Original response text
            
        Returns:
            Modified text for natural chat appearance
        """
        # Strip to look like real chat:
        # 1. Make lowercase (real people don't capitalize properly in chat)
        text = text.lower()
        
        # 2. Remove trailing periods only (keep periods in abbreviations like "i.e." or "U.S.")
        text = text.rstrip('.')
        
        # 3. Remove ellipses (already covered by removing periods)
        
        # 4. Remove exclamation marks (too enthusiastic for casual chat)
        text = text.replace('!', '')
        
        # 5. Remove commas (no one uses commas in chat)
        text = text.replace(',', '')
        
        # 6. Keep question marks (those are natural)
        
        return text
        
    def _generate_simple_fallback_response(self, message: str, user: str) -> str:
        """Generate a simple fallback response when OpenAI is unavailable"""
        responses = [
            f"Hey {user}! I'm having a bit of trouble thinking right now, but I heard you!",
            f"Thanks for the message, {user}! My brain is taking a quick break.",
            f"{user}, I appreciate you being here! Give me a moment to collect my thoughts.",
            "I'm experiencing some technical difficulties, but I'm still here!",
            "My AI brain needs a quick reboot, but don't worry, I'm still listening!"
        ]
        return random.choice(responses)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get personality engine statistics"""
        avg_response_time = (
            self.total_response_time / self.responses_generated
            if self.responses_generated > 0
            else 0
        )
        
        return {
            'current_preset': self.current_preset.value,
            'current_traits': asdict(self.current_traits),
            'responses_generated': self.responses_generated,
            'avg_response_time': avg_response_time,
            'repetition_rejections': self.repetition_rejections,
            'repetition_forced': self.repetition_forced,
            'last_switch': self.last_switch_time.isoformat() if self.last_switch_time else None
        }
        
    async def shutdown(self) -> None:
        """Shutdown the personality engine"""
        logger.info("Shutting down PersonalityEngine...")
        
        # Save current configuration
        await self.save_personality()
        
        logger.info("PersonalityEngine shutdown complete")
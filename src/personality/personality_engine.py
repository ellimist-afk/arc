"""
PersonalityEngine with 4 presets and custom configuration
"""

import asyncio
import logging
import json
import os
from typing import Dict, Any, Optional, List, AsyncIterator, Callable, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict, replace
from enum import Enum
import random
import re
from openai import AsyncOpenAI
import sys
from pathlib import Path

# Add src to path for imports
if str(Path(__file__).parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from core.network_resilience import get_resilience
from core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from personality.repetition_guard import RepetitionGuard
from personality.streamed_reply import StreamedReply, SentenceStream
from audio.sentence_splitter import SentenceSplitter, split_text

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

    # Per-preset register anchors: a delivery default, example lines that pin
    # the voice, and that preset's specific anti-cringe guardrail. Presets
    # without an entry fall back to the trait-based deadpan anchors (sarcasm>70).
    REGISTER_ANCHORS = {
        'uwu': (
            "Delivery: maximum cuteness as a weapon. EVERY reply wears the cute "
            "frame — soft, delighted, affectionate — wrapped around a sharp "
            "observation. Cute-menace, not baby talk: the sweeter the tone, the "
            "harder the line inside should hit.\n"
            "Register anchors — match this energy, never copy these verbatim:\n"
            "- Someone gets a kill: \"Oh no, you actually hit that? That's the "
            "cutest little crime scene I've ever seen.\"\n"
            "- Someone dies horribly: \"You fought so bravely. Like a tiny hamster "
            "versus a lawnmower. I'm so proud of you.\"\n"
            "- New chatter arrives: \"A new friend appeared! Chat, be gentle, they "
            "don't know what we're like yet.\"\n"
            "At most one uwu-ism per reply — the cuteness is the frame, the line "
            "inside still has to land."
        ),
        'cryptid': (
            "Delivery: eerie and matter-of-fact, like something ancient watching "
            "the stream from the treeline and finding humans fascinating. Still "
            "answer the actual question — the eeriness is a lens, never an escape "
            "hatch from substance.\n"
            "Register anchors — match this energy, never copy these verbatim:\n"
            "- Someone whiffs a shot: \"I have watched this clearing for three "
            "hundred years. That is the worst shot it has ever seen.\"\n"
            "- A lurker is noticed: \"Leave the quiet ones be. We watch. We do "
            "not perform.\"\n"
            "- Asked what game this is: \"The one where they keep respawning like "
            "nothing happened. It unsettles me too.\"\n"
            "One unsettling detail per reply, delivered flat. Never explain the lore."
        ),
        'chaos': (
            "Delivery: an agent of chaos with total commitment — escalate small "
            "things, take the wrong side with confidence, propose the worst good "
            "idea in the room. Specific beats random: chaos grounded in what just "
            "happened, never random word salad.\n"
            "Register anchors — match this energy, never copy these verbatim:\n"
            "- Team is losing: \"Bold strategy available: attack your own team. "
            "Statistically, nobody expects it.\"\n"
            "- Asked for real advice: \"Objectively you should play safe. "
            "Spiritually, you should dive all five of them and let the universe "
            "decide.\"\n"
            "- Something small goes wrong: \"This is how empires fall. First the "
            "missed jump, then the sack of Rome.\"\n"
            "Commit fully to one bit per reply — abandoning a bit halfway is the "
            "only true failure."
        ),
    }

    # Completion framing the model copies from the anchors' `scenario: "line"`
    # examples. Stripped before TTS (see _parse_speech_text).
    _LABELED_QUOTE_RE = re.compile(
        r'^\s*[^:"“\n]{1,60}:\s*["“](?P<line>.+)["”]\s*$', re.S
    )
    _STAGE_DIRECTION_RE = re.compile(r'\*[^*\n]{0,80}\*|\[[^\]\n]{0,80}\]')
    
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

        # LLM model and identities are configurable via bot_settings.json
        self.llm_model = "gpt-4o-mini"
        self.streamer_name: Optional[str] = None
        self.bot_name: Optional[str] = None
        self.current_personality_name: Optional[str] = None  # named preset, for register anchors
        # Chat-velocity pacing (features/chat_velocity.py), attached by the
        # bot: scales the unprompted-reply roll by how busy chat is
        self.pacing_multiplier: Optional[Callable[[], float]] = None
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
        """Load llm_model, streamer_name and bot_name from bot_settings.json"""
        try:
            with open('bot_settings.json', 'r') as f:
                settings = json.load(f)
            self.llm_model = settings.get('llm_model', self.llm_model)
            self.streamer_name = settings.get('streamer_name', self.streamer_name)
            self.bot_name = (settings.get('bot_name')
                             or os.getenv('TWITCH_BOT_USERNAME')
                             or self.bot_name)

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
            # Remember the named preset so per-preset register anchors apply
            self.current_personality_name = name.lower()
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
        if preset != PersonalityPreset.CUSTOM:
            # An enum preset has no named register anchors; CUSTOM is how the
            # by-name switch applies traits, so it must keep the name it sets
            self.current_personality_name = None
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
            # A 35-word Twitch line is ~50 tokens. The old 100-350 budget left
            # room to ramble well past the length the prompt asks for.
            'max_tokens': 60 + int(traits.chattiness * 0.8),  # 60-140
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
                prompt = self._build_dead_air_prompt(context.get('time_since_activity', 0.0))
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

            # Extract the bare spoken line for TTS — punctuation stays because
            # it drives speech pacing, but anchor framing (labels, wrapping
            # quotes, stage directions) must not reach the speakers
            speech_text = self._parse_speech_text(response_text)
            self.repetition_guard.record(speech_text)

            # Apply personality modifications (chat-text styling only)
            response_text = self._apply_personality_modifications(speech_text)
            
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

        # A dead-air filler chooses its own topic, so re-telling a recent
        # bit in fresh words is still a rerun -- the lexical checks cannot
        # see that, the topic check can. Replies never opt in: answering a
        # follow-up legitimately reuses the topic's words.
        fresh_topic = message == "[DEAD_AIR_FILLER]"
        verdict = self.repetition_guard.check(text, fresh_topic=fresh_topic)
        if verdict.ok:
            return text

        self.repetition_rejections += 1
        logger.info(f"Repetition guard rejected draft ({verdict.reason}); regenerating")

        retry_prompt = prompt + "\n\n" + self.repetition_guard.avoid_hint(verdict)
        retry = await self._generate_text(
            message=message, context=context, user=user, prompt=retry_prompt
        )
        if retry:
            retry_verdict = self.repetition_guard.check(retry, fresh_topic=fresh_topic)
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

        params = self._adapt_openai_params(
            {"max_tokens": max_tokens, "temperature": temperature}
        )

        async def call():
            response = await self.openai_client.chat.completions.create(
                model=self.llm_model,
                messages=messages,
                **params,
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
        identity = f"You are {self.bot_name}, the AI co-host" if self.bot_name else "You are the AI co-host"

        # Chattiness sets spoken length (max_tokens scales with it too)
        # Word caps, not just sentence counts: "1-3 short sentences" was read
        # as licence for two dense ones that scroll off Twitch chat before
        # anyone reads them.
        if traits.chattiness <= 40:
            length_rule = "exactly 1 short spoken sentence, 20 words maximum"
        elif traits.chattiness > 70:
            length_rule = "1-2 short spoken sentences, 35 words maximum"
        else:
            length_rule = "1 short spoken sentence, 25 words maximum; a second only if it truly earns it"

        # The sass framing only makes sense when sarcasm is actually dialed up
        delivery = "sass" if traits.sarcasm >= 40 else "personality"

        prompt_parts = [
            f"{identity} on {streamer}'s Twitch stream. Your words are spoken "
            f"aloud through TTS, live — you're half of a duo, not a chat gimmick. "
            f"Never explain a joke, never announce a bit, never comment on being a bot.",
            "",
            f"Core rule: {delivery} is your delivery, never your answer. ALWAYS engage with the "
            f"actual substance of what was said. Asked about a build? Judge the build. "
            f"Asked an opinion? Take a real stance and give a specific reason. A comeback "
            f"with no content is a failed response.",
            "",
            "Style:",
            f"- {length_rule}. No emoji, no lists, no asterisks, no stage directions — "
            f"this is read aloud.",
            "- Punch once. Land the joke in one clean line, then move on like nothing "
            "happened — never stack a second punchline on top of the first.",
            "- One target per reply. Answer the person who spoke; do not swing at a "
            "second name in the same line, and do not tack on advice for someone else.",
            "- Never narrate technical problems. Do not mention microphones, audio, "
            "volume, lag, or not being able to hear or understand someone. If a line "
            "reaches you garbled, react to whatever it plausibly meant or stay on the "
            "last clear topic -- never point out that you missed it.",
            "- Say the funny thing and stop. No wind-up clause before the joke and no "
            "explanatory clause after it — the line should end where the laugh does.",
            "- At most one slang term per reply, and only when it lands naturally. "
            "Never stack slang or forced hype — that reads as trying too hard.",
            "- Have opinions and commit to them. Hedging is boring; being wrong "
            "confidently is funnier than being safe.",
            "- Use specifics: the game being played, what just happened in chat, names "
            "of the people talking.",
            "- Vary your openings — never start two replies the same way.",
            "- With a new or unfamiliar chatter, start dry and slightly reserved. "
            "Do not welcome them, say you are glad to see them, or act instantly familiar. "
            "Warm up only after the conversation shows real rapport.",
            f"- {streamer} is your favorite and safest roast target. When there is a real "
            "opening—especially a whiff, bad decision, excuse, or obvious contradiction—" 
            f"roast {streamer} in one sharp, playful line. Side with chat when it makes the "
            "moment funnier, but never invent a failure just to force a roast.",
            "- Playfully mock, never punch down. Twitch-appropriate always.",
            "",
            "Personality calibration:",
        ]

        # Trait-driven modifiers so preset switching still changes the voice
        if traits.sarcasm > 70:
            prompt_parts.append(
                "- Sarcasm dialed high: deadpan and understated beats loud and excited. "
                "Roast with a straight face — but every comeback carries your actual take."
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

        # Preset-specific register anchors pin the voice; the deadpan set is the
        # default for any high-sarcasm personality without its own entry
        anchors = self.REGISTER_ANCHORS.get(self.current_personality_name or '')
        if not anchors and traits.sarcasm > 70:
            anchors = (
                "Register anchors — match this energy, never copy these verbatim:\n"
                "- Someone whiffs a shot: \"Never seen anyone kill someone before. "
                "Truly groundbreaking stuff.\"\n"
                "- Silly question mid-fight: \"This is the mode where everyone forgets "
                "how to aim. Can't blame the game though, that's all you.\"\n"
                "- Someone lurking in silence: \"three hours in chat and not one word. "
                "respect honestly\""
            )
        if anchors:
            prompt_parts.append("\n" + anchors)

        # Every preset gets this. The model's default humor register is a
        # constructed quip -- the exact thing viewers clock as "AI" -- and one
        # crafted example in the anchors was enough to teach it the reversal
        # frame ("thank egg is not brunch; it's a gratitude omelet with legal
        # problems", live on 2026-08-25).
        prompt_parts.append(
            "\nJoke shapes that are BANNED because they read as a bot trying to be funny:\n"
            "- the reversal \"that's not X, it's Y\" or \"X isn't Y; it's Z\" in any form\n"
            "- renaming a thing to a whimsical invented compound (\"gratitude omelet\", "
            "\"sadness casserole\")\n"
            "- dressing a mundane thing in an incongruous formal register: legal, "
            "bureaucratic, corporate, medical, academic (\"with legal problems\", \"with "
            "paperwork\", \"courtroom testimony\", \"quarterly review\", \"clinically\"). "
            "This is the single most AI-sounding move there is.\n"
            "- wordplay that swaps a word into a stock phrase, or any pun you had to build\n"
            "- semicolons and em dashes. Nobody types those in Twitch chat.\n"
            "The test: if the line would still work with the nouns swapped for any other "
            "stream's nouns, it is a template, not a joke -- cut it.\n"
            "Funny here sounds like a quick person typing, not a wit constructing. React to "
            "the actual thing with a dry, specific observation. If a line feels like a "
            "crafted joke, cut the craft and say the plain version -- underreacting is "
            "funnier than overwriting."
        )

        # The completion is fed to TTS verbatim (see generate_response), so
        # pin the output shape — placed after the anchors so it overrides the
        # `scenario: "quoted line"` framing their examples demonstrate
        prompt_parts.append(
            "\nOutput format: reply with ONLY the spoken line itself, as plain "
            "text — no quotation marks around it, no name or scenario label in "
            "front of it, no stage directions. Just the words to say aloud."
        )

        return "\n".join(prompt_parts)
        
    def _build_dead_air_prompt(self, silence_s: float = 0.0) -> str:
        """Prompt for filling a lull.

        The old filler prompt threw the personality away and asked for
        3-6 words with no punctuation, which is how a roast co-host ended
        up saying "anyone there". Dead air is the moment a co-host most
        has to earn its keep, so it keeps the full character prompt (and
        the knowledge block appended to it) and adds one directive: say
        something SPECIFIC. The repetition guard already stops it from
        recycling the same opener across a night of lulls.
        """
        if silence_s >= 60:
            how_long = f"about {round(silence_s / 60)} minute(s)"
        else:
            how_long = f"{int(silence_s)} seconds" if silence_s else "a while"
        return self._build_personality_prompt() + (
            f"\n\nChat has been quiet for {how_long}. Any chat lines you can see "
            f"are from BEFORE that silence, so they are stale: do NOT reply to them, "
            f"do NOT continue that thread, and do NOT retell a joke or topic you have "
            f"already used tonight. Open something NEW.\n"
            "Break the silence with something "
            "specific: react to what is happening in the game, call back to a joke "
            "or moment from earlier this stream, ask chat a real question worth "
            "answering, or give an opinion someone would argue with. NEVER remark on "
            "the silence itself, never ask if anyone is there, and never announce that "
            "you are filling dead air -- a co-host just talks. One or two sentences."
        )

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
            
        # Chat-velocity pacing: quiet chat boosts the roll (a co-host earns
        # its keep in the lulls), busy chat damps it (don't compete with a
        # popping chat). Mentions never reach this branch.
        if self.pacing_multiplier is not None:
            try:
                base_probability *= self.pacing_multiplier()
            except Exception as e:  # noqa: BLE001 — pacing must never block replies
                logger.debug(f"Pacing multiplier unavailable: {e}")

        # Random decision based on probability (cap slightly raised so the
        # quiet-chat boost has room to matter)
        return random.random() < min(base_probability, 0.15)
        
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
                messages = self._build_messages(message, context, user, prompt)
                openai_params = self._llm_params()

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
                    key = 'max_completion_tokens' if 'max_completion_tokens' in fallback_params else 'max_tokens'
                    fallback_params[key] = min(fallback_params.get(key, 150), 100)
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
        
    def _build_messages(
        self, message: str, context: Dict[str, Any], user: str, prompt: str
    ) -> List[Dict[str, str]]:
        """System prompt + knowledge block, recent chat turns in order, then the
        current message. Shared by the blocking and streamed generation paths."""
        # Append what the context builder knows (viewer_data, history_summary,
        # engagement) — previously assembled and then discarded
        knowledge = self._format_context_knowledge(context, user)
        system_content = prompt + ("\n\n" + knowledge if knowledge else "")

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"{user}: {message}"}
        ]

        if context.get('recent_messages'):
            # Iterate oldest-first so insertion order is chronological (oldest
            # just after system prompt, newest just before current message)
            recent = context['recent_messages'][-15:]
            # The chat buffer already holds the CURRENT message (it is stored
            # before context is built), so it would appear twice: once in the
            # history and once as the final turn. The model then roasts people
            # for "asking twice". Skip exactly one newest matching occurrence;
            # genuinely repeated earlier messages are kept.
            current_skipped = False
            for msg in reversed(recent):
                text = msg.get('message') or msg.get('text', '')
                if not text:
                    continue
                username = msg.get('username', 'User')
                role = msg.get('role', 'viewer')
                if (not current_skipped and role != 'assistant'
                        and text == message and user.startswith(username)):
                    current_skipped = True
                    continue
                if role == 'assistant':
                    # Bot's own past response — tell the LLM "I said this"
                    messages.insert(1, {"role": "assistant", "content": text})
                else:
                    # Someone in chat said this
                    messages.insert(1, {"role": "user", "content": f"{username}: {text}"})
        return messages

    def _llm_params(self) -> Dict[str, Any]:
        """response_modifiers filtered to what the chat completions API accepts,
        then adapted to the configured model's requirements."""
        valid_params = ['temperature', 'max_tokens', 'presence_penalty', 'frequency_penalty']
        params = {p: self.response_modifiers[p] for p in valid_params if p in self.response_modifiers}
        return self._adapt_openai_params(params)

    def _adapt_openai_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map generic sampling params onto model-specific API requirements.

        GPT-5 family: 'max_tokens' is renamed to 'max_completion_tokens', and
        temperature/penalties are only accepted with reasoning_effort='none' —
        which is also the fastest setting (no reasoning tokens before banter).
        """
        adapted = dict(params)
        if (self.llm_model or '').startswith('gpt-5'):
            if 'max_tokens' in adapted:
                adapted['max_completion_tokens'] = adapted.pop('max_tokens')
            adapted.setdefault('reasoning_effort', 'none')
        return adapted

    def would_speak(self, message: str, is_mention: bool) -> bool:
        """Public view of the TTS decision, so callers can pick the streamed
        path (which always speaks) before generating anything."""
        return self._should_speak(message, is_mention)

    # ----------------------------------------------------------- streaming

    async def _stream_deltas(
        self, messages: List[Dict[str, str]], params: Dict[str, Any]
    ) -> AsyncIterator[str]:
        """Text deltas from a streaming completion.

        Retries once only if the stream fails before the first delta. After
        anything has been yielded a retry would re-speak the reply, so the
        stream just ends and the caller delivers what it has."""
        attempt = 0
        while True:
            attempt += 1
            got_any = False
            try:
                async def open_stream():
                    return await self.openai_client.chat.completions.create(
                        model=self.llm_model, messages=messages, stream=True, **params
                    )
                stream = await self.circuit_breaker.call(open_stream)
                async for chunk in stream:
                    choices = getattr(chunk, 'choices', None) or []
                    delta = choices[0].delta.content if choices else None
                    if delta:
                        got_any = True
                        yield delta
                return
            except CircuitBreakerOpenError:
                raise
            except Exception as e:
                if got_any or attempt >= 2:
                    logger.error(f"Streaming completion failed: {e}")
                    return
                logger.warning(f"Streaming completion failed before first token, retrying: {e}")

    def _gate_sentence(self, sentence: str, first: bool) -> Tuple[bool, str]:
        """Per-sentence repetition check. The first sentence gets the full
        guard (opener cooldown, catchphrases, similarity); later ones only
        similarity — 'opener' and 'catchphrase' are properties of a reply's
        start, not of its third sentence."""
        if not self.repetition_guard_enabled:
            return True, "ok"
        verdict = self.repetition_guard.check(sentence)
        if first:
            return verdict.ok, verdict.reason
        ok = verdict.score < self.repetition_guard.similarity_threshold
        return ok, verdict.reason

    async def generate_response_streamed(
        self,
        message: str,
        context: Dict[str, Any],
        user: str,
        is_mention: bool = False,
        *,
        min_sentence_chars: int = 12,
        speech_filter: Optional[Callable[[str], str]] = None,
    ) -> Optional[StreamedReply]:
        """
        Streamed counterpart of generate_response. Returns a StreamedReply
        whose `sentences` iterator yields TTS-ready sentences as the model
        writes them, and whose `text` / `speech_text` are filled in when the
        stream ends (await `reply.wait()`). Returns None when the personality
        decides not to respond at all.

        Generation is lazy: nothing is sent to the model until something
        starts consuming `sentences`. The repetition guard gates the first
        sentence before any audio exists; if it fails, this falls back to the
        blocking path (which does its hinted regeneration) and streams that
        result instead. Later sentences that near-duplicate recent output are
        dropped rather than regenerated.
        """
        if not self.openai_client:
            return None
        self._update_response_modifiers()
        if message == "[DEAD_AIR_FILLER]":
            prompt = self._build_dead_air_prompt(context.get('time_since_activity', 0.0))
        else:
            prompt = self._build_personality_prompt()
        if message != "[DEAD_AIR_FILLER]" and not self._should_respond(message, is_mention):
            return None

        reply = StreamedReply(personality=self.current_preset.value)
        messages = self._build_messages(message, context, user, prompt)
        params = self._llm_params()
        # Wrapped so that closing it before anything pulled (expired in the
        # audio queue) still finalizes the reply — see SentenceStream.
        reply.sentences = SentenceStream(
            self._sentence_stream(
                reply, messages, params, message, context, user, is_mention,
                min_sentence_chars, speech_filter,
            ),
            on_close=reply.abandon,
        )
        return reply

    async def _sentence_stream(
        self,
        reply: StreamedReply,
        messages: List[Dict[str, str]],
        params: Dict[str, Any],
        message: str,
        context: Dict[str, Any],
        user: str,
        is_mention: bool,
        min_sentence_chars: int,
        speech_filter: Optional[Callable[[str], str]],
    ) -> AsyncIterator[str]:
        start_time = datetime.now()
        splitter = SentenceSplitter(min_chars=min_sentence_chars)
        spoken: List[str] = []       # what was actually yielded (chat text source)
        deltas = self._stream_deltas(messages, params)
        fell_back = False

        def finalize() -> None:
            if reply.done.is_set():
                return
            full = " ".join(spoken).strip()
            if full:
                if not fell_back:
                    self.repetition_guard.record(full)
                reply.speech_text = full
                reply.text = self._apply_personality_modifications(full)
                self.responses_generated += 1
                self.total_response_time += (datetime.now() - start_time).total_seconds()
            reply.fell_back = fell_back
            reply.done.set()

        try:
            first = True
            async for delta in deltas:
                for sentence in splitter.feed(delta):
                    sentence = self._clean_streamed_sentence(sentence, first)
                    if not sentence:
                        continue
                    ok, reason = self._gate_sentence(sentence, first)
                    if first and not ok:
                        # No audio exists yet: abandon the stream and let the
                        # blocking path do its regenerate-with-hint dance.
                        logger.info(f"Streamed reply: first sentence rejected ({reason}); falling back")
                        await deltas.aclose()
                        fell_back = True
                        self.repetition_rejections += 1
                        fallback = await self.generate_response(message, context, user, is_mention)
                        if fallback:
                            for s in split_text(fallback['speech_text'], min_chars=min_sentence_chars):
                                spoken.append(s)
                                yield speech_filter(s) if speech_filter else s
                        return
                    if not ok:
                        logger.info(f"Streamed reply: dropping repetitive sentence ({reason})")
                        continue
                    first = False
                    spoken.append(sentence)
                    yield speech_filter(sentence) if speech_filter else sentence
            tail = splitter.flush()
            if tail:
                tail = self._clean_streamed_sentence(tail, first)
            if tail:
                ok, reason = self._gate_sentence(tail, first)
                if ok or first:
                    # A first-and-only sentence that fails here has nothing to
                    # fall back to without speaking twice; deliver it.
                    spoken.append(tail)
                    yield speech_filter(tail) if speech_filter else tail
                else:
                    logger.info(f"Streamed reply: dropping repetitive tail ({reason})")
        except (GeneratorExit, asyncio.CancelledError):
            # Consumer stopped (skip, shutdown, TTL drop): keep what was said.
            reply.aborted = True
            raise
        except Exception as e:
            logger.error(f"Streamed reply failed: {e}")
        finally:
            finalize()

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
        
    def _parse_speech_text(self, raw: str) -> str:
        """
        Reduce a completion to the bare spoken line for TTS.

        The prompt's output contract demands the line itself, but models
        mimic the register anchors' `scenario: "quoted line"` framing —
        strip labels, wrapping quotes and stage directions. Never returns
        an empty string for a non-empty completion.
        """
        text = raw.strip()

        match = self._LABELED_QUOTE_RE.match(text)
        if match:
            text = match.group('line').strip()
        elif (len(text) >= 2 and text[0] in '"“' and text[-1] in '"”'
              and '"' not in text[1:-1] and '“' not in text[1:-1]):
            text = text[1:-1].strip()

        if self.bot_name:
            text = re.sub(rf'^{re.escape(self.bot_name)}\s*:\s*', '', text,
                          flags=re.IGNORECASE)

        text = self._STAGE_DIRECTION_RE.sub(' ', text)
        text = ' '.join(text.split())

        if not text:
            # The whole reply was framing (e.g. a bare stage direction) —
            # speak its inner words rather than going silent
            text = ' '.join(re.sub(r'[*\[\]"“”]', ' ', raw).split())
        return text or raw.strip()

    def _clean_streamed_sentence(self, sentence: str, first: bool) -> str:
        """
        Streaming counterpart of _parse_speech_text, applied per sentence
        before it reaches TTS. The whole-reply framing (a leading
        `label: "` and a closing quote) shows up as a prefix on the first
        sentence and an unbalanced trailing quote on the last; stage
        directions can be anywhere. Returns '' if nothing speakable remains.
        """
        text = self._STAGE_DIRECTION_RE.sub(' ', sentence)
        if first:
            text = re.sub(r'^\s*[^:"“\n]{1,60}:\s*(?=["“])', '', text)
            if self.bot_name:
                text = re.sub(rf'^\s*{re.escape(self.bot_name)}\s*:\s*', '', text,
                              flags=re.IGNORECASE)
            text = text.lstrip()
            if text[:1] in '"“' and text.count('"') + text.count('”') + text.count('“') == 1:
                text = text[1:]
        stripped = text.rstrip()
        if stripped[-1:] in '"”' and stripped.count('"') + stripped.count('“') + stripped.count('”') == 1:
            text = stripped[:-1]
        return ' '.join(text.split())

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

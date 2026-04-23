"""
Twitch Ad Announcer
Monitors for ad breaks and announces them in chat/voice
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime
import random

logger = logging.getLogger(__name__)


class AdAnnouncer:
    """Handles ad break announcements for Twitch streams"""

    def __init__(self, twitch_client, audio_queue=None, response_coordinator=None,
                 personality_engine=None, chat_buffer=None, openai_client=None, channel_name=None):
        self.twitch_client = twitch_client
        self.audio_queue = audio_queue
        self.response_coordinator = response_coordinator
        self.personality_engine = personality_engine
        self.chat_buffer = chat_buffer
        self.channel_name = channel_name
        self.openai_client = openai_client or (getattr(personality_engine, 'openai_client', None) if personality_engine else None)

        # Ad state tracking
        self.ad_active = False
        self.ad_start_time = None
        self.ad_duration = 0
        self.last_ad_time = None

        # Configuration
        self.enabled = True
        self.announce_in_chat = True
        self.announce_with_voice = True
        self.min_time_between_ads = 480  # 8 minutes

        # Fallback messages
        self.ad_start_messages = [
            "Ad break time! Be right back in {duration} seconds",
            "Running a {duration} second ad break",
            "Ad time! Back in {duration} seconds",
            "BRB - {duration} second ad break",
            "Ads rolling for {duration} seconds, hang tight",
            "Quick ad break - back in about {minutes} minute{s}"
        ]
        self.ad_end_messages = [
            "And we're back!",
            "Ad break's over, let's go!",
            "Welcome back everyone!",
            "Alright, we're back in action",
            "Thanks for waiting, we're back!"
        ]
        self.standard_ad_messages = [
            "Standard ad break - back in 90 seconds",
            "90-second ad break, perfect time to grab a drink",
            "Ad time! See you in a minute and a half",
            "Taking a quick 90-second ad break",
            "BRB - standard ad break incoming"
        ]
        self.long_ad_messages = [
            "Grab a snack, this is a long one - {duration} seconds",
            "Perfect time for a bathroom break - {minutes} minute ad",
            "Long ad alert! {minutes} minutes to stretch those legs"
        ]
        logger.info("AdAnnouncer initialized")

    async def handle_ad_break_begin(self, event: Dict[str, Any]) -> None:
        """Handle ad break begin event from EventSub"""
        try:
            duration = event.get('duration_seconds', 30)
            is_automatic = event.get('is_automatic', False)
            logger.info(f"Ad break detected via EventSub: {duration}s, automatic={is_automatic}")
            await self._handle_ad_start({'type': 'commercial_start', 'length': duration, 'is_automatic': is_automatic})
        except Exception as e:
            logger.error(f"Error handling ad break event: {e}")

    async def start_ad_break(self, duration: int = 30, manual: bool = True) -> None:
        """Start an ad break announcement (manual command)"""
        try:
            logger.info(f"Ad break manually triggered: {duration}s")
            await self._handle_ad_start({'type': 'commercial_start', 'length': duration, 'is_automatic': not manual})
        except Exception as e:
            logger.error(f"Error starting ad break: {e}")

    async def _get_chat_context(self, max_turns: int = 8) -> str:
        """Get recent chat context for LLM"""
        if not self.chat_buffer or not self.channel_name:
            return ""
        try:
            history = self.chat_buffer.get_recent(self.channel_name, limit=max_turns)
            if history:
                lines = []
                for msg in history[-max_turns:]:
                    username = msg.get('username', 'User')
                    text = msg.get('message', '')
                    role = msg.get('role', 'viewer')
                    if not text:
                        continue
                    if role == 'assistant':
                        lines.append(f"bot: {text}")
                    else:
                        lines.append(f"{username}: {text}")
                return "\n".join(lines)
        except Exception as e:
            logger.debug(f"Could not fetch chat context: {e}")
        return ""

    async def _get_personality_info(self) -> str:
        """Get personality info for LLM prompts"""
        if not hasattr(self.personality_engine, 'current_preset'):
            return ""
        preset = self.personality_engine.current_preset
        if not hasattr(preset, 'value'):
            return ""
        info = f"\nPersonality: {preset.value}"

        # Get distinctive traits (extreme values only)
        traits = getattr(self.personality_engine, 'current_traits', None)
        if traits:
            distinctive = []
            for field in ['sarcasm', 'humor', 'enthusiasm', 'formality', 'helpfulness', 'assertiveness']:
                value = getattr(traits, field, 50)
                if value >= 70:
                    distinctive.append(f"high {field} ({value})")
                elif value <= 30:
                    distinctive.append(f"low {field} ({value})")
            if distinctive:
                info += f"\nDistinctive: {', '.join(distinctive[:4])}"
        return info

    def _trim_to_length(self, text: str, max_len: int) -> str:
        """Trim text to max length, preserving sentence boundaries"""
        if len(text) <= max_len:
            return text
        trim_point = max_len - 3
        sentence_ends = [text.rfind(c, 0, trim_point) for c in ['.', '!', '?']]
        last_sentence = max(sentence_ends)
        if last_sentence > 0:
            return text[:last_sentence + 1]
        last_space = text.rfind(' ', 0, trim_point)
        if last_space > 0:
            return text[:last_space] + "..."
        return text[:trim_point] + "..."

    async def _call_llm(self, prompt: str, max_tokens: int = 80) -> Optional[str]:
        """Make LLM call with timeout and error handling"""
        try:
            response = await asyncio.wait_for(
                self.openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "system", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.9
                ),
                timeout=4.0
            )
            return response.choices[0].message.content.strip().replace('"', '').replace("'", '')
        except asyncio.TimeoutError:
            logger.debug("LLM call timed out")
        except Exception as e:
            logger.debug(f"LLM call failed: {e}")
        return None

    async def _generate_hook_message(self, duration_seconds: int) -> Optional[str]:
        """Generate engaging LLM hook to keep viewers during ads"""
        if not self.openai_client or not self.personality_engine:
            return None

        chat_ctx = await self._get_chat_context(8)
        personality = await self._get_personality_info()

        prompt = f"""You are a Twitch streamer bot announcing an ad break. Keep viewers engaged so they don't leave.

CRITICAL RULES:
- SHORT message (under 200 characters)
- DO NOT just say "BRB" or "back in {duration_seconds} seconds" - boring, loses viewers
- Give a REASON to stay: ask a question, share a hot take, tease what's next, make a fun observation, or roast someone in chat
- Match this personality{personality}
- Mention ad duration naturally, not as the focus
- Be engaging and compelling

{"Recent chat:" + chr(10) + chat_ctx if chat_ctx else ""}

Generate ONE engaging hook for a {duration_seconds}-second ad:"""

        msg = await self._call_llm(prompt, 80)
        if not msg:
            return None
        if len(msg) < 10:
            logger.debug(f"LLM hook too short: {len(msg)} chars")
            return None
        if len(msg) > 200:
            msg = self._trim_to_length(msg, 200)
        logger.debug(f"Generated hook: {msg}")
        return msg

    async def _generate_return_message(self) -> Optional[str]:
        """Generate engaging LLM 'we're back' message"""
        if not self.openai_client or not self.personality_engine:
            return None

        chat_ctx = await self._get_chat_context(10)
        personality = await self._get_personality_info()

        prompt = f"""You are a Twitch streamer bot returning from an ad break.

RULES:
- SHORT message (under 150 characters)
- Welcome viewers back naturally
- If recent chat shows an interesting topic from before the ad, briefly callback to it
- Match this personality{personality}
- Be energetic and ready to continue

{"Recent chat (including pre-ad hook):" + chr(10) + chat_ctx if chat_ctx else ""}

Generate ONE welcoming return message:"""

        msg = await self._call_llm(prompt, 60)
        if not msg:
            return None
        if len(msg) < 5:
            logger.debug(f"LLM return too short: {len(msg)} chars")
            return None
        if len(msg) > 150:
            msg = self._trim_to_length(msg, 150)
        logger.debug(f"Generated return: {msg}")
        return msg

    async def _handle_ad_start(self, event: Dict[str, Any]) -> None:
        """Handle ad break start"""
        if not self.enabled:
            return
        if self.last_ad_time and (datetime.now() - self.last_ad_time).total_seconds() < self.min_time_between_ads:
            logger.debug(f"Skipping ad announcement, too soon")
            return

        self.ad_active = True
        self.ad_start_time = datetime.now()
        self.ad_duration = event.get('length', 90)

        # Try LLM first
        message = await self._generate_hook_message(self.ad_duration)
        if message:
            logger.info("Ad hook: LLM-generated")
        else:
            # Fallback to hardcoded
            logger.info("Ad hook: fallback pool")
            if self.ad_duration >= 180:
                message = random.choice(self.long_ad_messages).format(
                    duration=self.ad_duration, minutes=self.ad_duration // 60)
            elif 85 <= self.ad_duration <= 95:
                message = random.choice(self.standard_ad_messages)
            else:
                minutes = round(self.ad_duration / 60, 1)
                message = random.choice(self.ad_start_messages).format(
                    duration=self.ad_duration, minutes=minutes, s="s" if minutes != 1 else "")

        await self._announce_ad(message, is_start=True)
        asyncio.create_task(self._schedule_ad_end())
        self.last_ad_time = datetime.now()
        logger.info(f"Ad break started: {self.ad_duration} seconds")

    async def _handle_ad_end(self, event: Dict[str, Any]) -> None:
        """Handle ad break end"""
        if not self.ad_active:
            return
        self.ad_active = False

        if self.enabled:
            # Try LLM first
            message = await self._generate_return_message()
            if not message:
                message = random.choice(self.ad_end_messages)
            await self._announce_ad(message, is_start=False)
        logger.info("Ad break ended")

    async def _schedule_ad_end(self) -> None:
        """Schedule ad end announcement"""
        await asyncio.sleep(self.ad_duration)
        if self.ad_active:
            await self._handle_ad_end({})

    async def _announce_ad(self, message: str, is_start: bool = True) -> None:
        """Announce ad break in chat and/or voice"""
        try:
            if self.announce_in_chat and self.twitch_client:
                await self.twitch_client.send_message(message)
            if self.announce_with_voice and self.audio_queue:
                if self.response_coordinator:
                    async def queue_tts():
                        if self.audio_queue:
                            await self.audio_queue.queue_audio(text=message, priority='high')

                    await self.response_coordinator.coordinate_response(
                        chat_msg=message,
                        audio_task=queue_tts,
                        priority='high',
                        is_mention=False,
                        is_voice=False
                    )
                else:
                    await self.audio_queue.queue_audio(text=message, priority='high')
        except Exception as e:
            logger.error(f"Error announcing ad: {e}")

    def update_settings(self, settings: Dict[str, Any]) -> None:
        """Update ad announcer settings"""
        if 'enabled' in settings:
            self.enabled = settings['enabled']
        if 'announce_in_chat' in settings:
            self.announce_in_chat = settings['announce_in_chat']
        if 'announce_with_voice' in settings:
            self.announce_with_voice = settings['announce_with_voice']
        if 'min_time_between_ads' in settings:
            self.min_time_between_ads = settings['min_time_between_ads']
        logger.info(f"Ad announcer settings updated: enabled={self.enabled}")

    def get_status(self) -> Dict[str, Any]:
        """Get current ad announcer status"""
        return {
            'enabled': self.enabled,
            'ad_active': self.ad_active,
            'ad_duration': self.ad_duration if self.ad_active else 0,
            'time_remaining': self._get_time_remaining(),
            'announce_in_chat': self.announce_in_chat,
            'announce_with_voice': self.announce_with_voice
        }

    def _get_time_remaining(self) -> int:
        """Get time remaining in current ad break"""
        if not self.ad_active or not self.ad_start_time:
            return 0
        elapsed = (datetime.now() - self.ad_start_time).total_seconds()
        return int(max(0, self.ad_duration - elapsed))

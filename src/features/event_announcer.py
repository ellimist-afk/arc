"""
Event Announcer - Handles follow, sub, and cheer announcements
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class EventAnnouncer:
    """Announces follows, subs, and cheers."""

    def __init__(self, bot):
        self.bot = bot
        self.enabled = True

        # Cooldowns to prevent spam
        self.last_follow_time: Optional[datetime] = None
        self.follow_cooldown = 10  # seconds between follow announcements
        self.follow_queue = []  # Queue follows to batch announce

        # Message templates
        self.follow_messages = [
            "Welcome to the community, {user}!",
            "Hey {user}, thanks for the follow! Glad to have you!",
            "{user} just followed! Welcome aboard!",
            "New follower alert! Welcome {user}!",
        ]

        self.sub_messages = [
            "HUGE thanks to {user} for subscribing!",
            "{user} just subscribed! You're amazing!",
            "Welcome to the sub club, {user}! Thank you!",
            "{user} with the sub! Let's gooo!",
        ]

        self.resub_messages = [
            "{user} resubbed for {months} months! Incredible loyalty!",
            "{months} months of support from {user}! Thank you so much!",
            "{user} keeping the streak alive at {months} months!",
        ]

        self.gift_sub_messages = [
            "{user} just gifted {count} sub(s)! What a legend!",
            "Gift sub alert! {user} gifted {count} to the community!",
            "{user} spreading the love with {count} gift sub(s)!",
        ]

        self.cheer_messages = [
            "{user} cheered {bits} bits! Thank you!",
            "{bits} bits from {user}! You're awesome!",
            "Cheer alert! {user} with {bits} bits!",
        ]

        self.big_cheer_messages = [
            "MASSIVE cheer! {user} just dropped {bits} bits!",
            "WOW! {user} with {bits} bits! Absolutely incredible!",
            "{bits} BITS from {user}! The chat goes wild!",
        ]

    async def handle_follow(self, event: dict):
        """Handle follow event."""
        if not self.enabled:
            return

        user = event.get('user_name', 'Someone')

        # Check cooldown
        now = datetime.now()
        if self.last_follow_time:
            elapsed = (now - self.last_follow_time).total_seconds()
            if elapsed < self.follow_cooldown:
                # Queue for batch announcement
                self.follow_queue.append(user)
                logger.debug(f"Follow queued: {user} (cooldown)")
                return

        self.last_follow_time = now

        # Include any queued follows
        if self.follow_queue:
            users = self.follow_queue + [user]
            self.follow_queue = []
            if len(users) > 3:
                message = f"Welcome to all our new followers: {', '.join(users[:3])} and {len(users)-3} more!"
            else:
                message = f"Welcome to our new followers: {', '.join(users)}!"
        else:
            message = random.choice(self.follow_messages).format(user=user)

        await self._announce(message)
        logger.info(f"Follow announced: {user}")

    async def handle_subscribe(self, event: dict):
        """Handle subscription event."""
        if not self.enabled:
            return

        user = event.get('user_name', 'Someone')
        tier = event.get('tier', '1000')  # 1000, 2000, 3000
        is_gift = event.get('is_gift', False)

        # Determine tier name
        tier_names = {'1000': 'Tier 1', '2000': 'Tier 2', '3000': 'Tier 3'}
        tier_name = tier_names.get(tier, 'Tier 1')

        if is_gift:
            # This is someone receiving a gift
            gifter = event.get('gifter_name', 'Anonymous')
            message = f"{user} just got a gift sub from {gifter}! Welcome!"
        else:
            message = random.choice(self.sub_messages).format(user=user)
            if tier != '1000':
                message = f"{tier_name} sub! {message}"

        await self._announce(message, priority="high")
        logger.info(f"Sub announced: {user} ({tier_name})")

    async def handle_resub(self, event: dict):
        """Handle resubscription event."""
        if not self.enabled:
            return

        user = event.get('user_name', 'Someone')
        months = event.get('cumulative_months', 1)
        message_text = event.get('message', {}).get('text', '')

        message = random.choice(self.resub_messages).format(user=user, months=months)

        if message_text:
            message += f" They said: \"{message_text[:100]}\""

        await self._announce(message, priority="high")
        logger.info(f"Resub announced: {user} ({months} months)")

    async def handle_gift_sub(self, event: dict):
        """Handle gift subscription event."""
        if not self.enabled:
            return

        user = event.get('user_name', 'Anonymous')
        count = event.get('total', 1)

        message = random.choice(self.gift_sub_messages).format(user=user, count=count)

        await self._announce(message, priority="high")
        logger.info(f"Gift sub announced: {user} ({count} gifts)")

    async def handle_cheer(self, event: dict):
        """Handle bits/cheer event."""
        if not self.enabled:
            return

        user = event.get('user_name', 'Anonymous')
        bits = event.get('bits', 0)
        cheer_message = event.get('message', '')

        # Use big messages for large cheers
        if bits >= 1000:
            message = random.choice(self.big_cheer_messages).format(user=user, bits=bits)
        else:
            message = random.choice(self.cheer_messages).format(user=user, bits=bits)

        # Include their message if present
        if cheer_message and len(cheer_message) < 100:
            message += f" \"{cheer_message}\""

        priority = "high" if bits >= 500 else "normal"
        await self._announce(message, priority=priority)
        logger.info(f"Cheer announced: {user} ({bits} bits)")

    async def _announce(self, message: str, priority: str = "normal"):
        """Send announcement to chat and TTS."""
        try:
            # Send to chat
            if hasattr(self.bot, 'twitch_client') and self.bot.twitch_client:
                await self.bot.twitch_client.send_message(message)

            # Queue TTS
            if hasattr(self.bot, 'audio_queue') and self.bot.audio_queue:
                # Try priority method first, fall back to regular queue
                if hasattr(self.bot.audio_queue, 'queue_audio'):
                    await self.bot.audio_queue.queue_audio(message, priority=priority)
                elif hasattr(self.bot.audio_queue, 'speak'):
                    await self.bot.audio_queue.speak(message)

        except Exception as e:
            logger.error(f"Failed to announce: {e}")

    def toggle(self) -> bool:
        """Toggle announcer on/off."""
        self.enabled = not self.enabled
        return self.enabled

"""
Twitch Ad Announcer
Monitors for ad breaks and announces them in chat/voice
"""

import asyncio
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import random

logger = logging.getLogger(__name__)


class AdAnnouncer:
    """
    Handles ad break announcements for Twitch streams
    """
    
    def __init__(
        self,
        twitch_client,
        audio_queue=None,
        response_coordinator=None
    ):
        """
        Initialize the ad announcer
        
        Args:
            twitch_client: Twitch client for sending messages
            audio_queue: Audio queue for TTS announcements
            response_coordinator: Response coordinator for synchronized delivery
        """
        self.twitch_client = twitch_client
        self.audio_queue = audio_queue
        self.response_coordinator = response_coordinator
        
        # Ad state tracking
        self.ad_active = False
        self.ad_start_time = None
        self.ad_duration = 0
        self.last_ad_time = None
        
        # Configuration
        self.enabled = True
        self.announce_in_chat = True
        self.announce_with_voice = True
        self.min_time_between_ads = 480  # 8 minutes minimum between ad announcements
        
        # Ad messages (randomized for variety)
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
        
        # Messages for 90-second ads (most common)
        self.standard_ad_messages = [
            "Standard ad break - back in 90 seconds",
            "90-second ad break, perfect time to grab a drink",
            "Ad time! See you in a minute and a half",
            "Taking a quick 90-second ad break",
            "BRB - standard ad break incoming"
        ]
        
        # Fun messages for long ads (3+ minutes)
        self.long_ad_messages = [
            "Grab a snack, this is a long one - {duration} seconds",
            "Perfect time for a bathroom break - {minutes} minute ad",
            "Long ad alert! {minutes} minutes to stretch those legs"
        ]
        
        logger.info("AdAnnouncer initialized")
        
    async def handle_ad_break_begin(self, event: Dict[str, Any]) -> None:
        """
        Handle ad break begin event from EventSub (automatic detection)
        
        Args:
            event: Ad break event data from Twitch EventSub
        """
        try:
            # EventSub provides these fields:
            # - duration_seconds: Length of the ad break
            # - started_at: Timestamp when ad break started  
            # - is_automatic: Whether the ad was automatically triggered
            
            duration = event.get('duration_seconds', 30)
            is_automatic = event.get('is_automatic', False)
            
            logger.info(f"Ad break detected via EventSub: {duration}s, automatic={is_automatic}")
            
            # Create event in expected format
            ad_event = {
                'type': 'commercial_start',
                'length': duration,
                'is_automatic': is_automatic
            }
            
            await self._handle_ad_start(ad_event)
            
        except Exception as e:
            logger.error(f"Error handling ad break event: {e}")
            
    async def start_ad_break(self, duration: int = 30, manual: bool = True) -> None:
        """
        Start an ad break announcement (manual command)
        
        Args:
            duration: Length of ad break in seconds
            manual: Whether this was manually triggered
        """
        try:
            logger.info(f"Ad break manually triggered: {duration}s")
            
            # Create event in expected format
            ad_event = {
                'type': 'commercial_start',
                'length': duration,
                'is_automatic': not manual
            }
            
            await self._handle_ad_start(ad_event)
            
        except Exception as e:
            logger.error(f"Error starting ad break: {e}")
            
    async def _handle_ad_start(self, event: Dict[str, Any]) -> None:
        """
        Handle ad break start
        
        Args:
            event: Ad start event data
        """
        # Check if we should announce this ad
        if not self.enabled:
            return
            
        # Avoid spamming ad announcements
        if self.last_ad_time:
            time_since_last = (datetime.now() - self.last_ad_time).total_seconds()
            if time_since_last < self.min_time_between_ads:
                logger.debug(f"Skipping ad announcement, only {time_since_last}s since last ad")
                return
        
        # Track ad state
        self.ad_active = True
        self.ad_start_time = datetime.now()
        self.ad_duration = event.get('length', 90)  # Default 90 seconds (most common)
        
        # Choose appropriate message based on ad length
        if self.ad_duration >= 180:  # 3+ minutes
            messages = self.long_ad_messages
            minutes = self.ad_duration // 60
            message = random.choice(messages).format(
                duration=self.ad_duration,
                minutes=minutes
            )
        elif 85 <= self.ad_duration <= 95:  # Standard 90-second ad
            message = random.choice(self.standard_ad_messages)
        else:
            messages = self.ad_start_messages
            minutes = round(self.ad_duration / 60, 1)
            s = "s" if minutes != 1 else ""
            message = random.choice(messages).format(
                duration=self.ad_duration,
                minutes=minutes,
                s=s
            )
        
        # Send announcements
        await self._announce_ad(message, is_start=True)
        
        # Schedule ad end announcement
        asyncio.create_task(self._schedule_ad_end())
        
        self.last_ad_time = datetime.now()
        logger.info(f"Ad break started: {self.ad_duration} seconds")
        
    async def _handle_ad_end(self, event: Dict[str, Any]) -> None:
        """
        Handle ad break end
        
        Args:
            event: Ad end event data
        """
        if not self.ad_active:
            return
            
        self.ad_active = False
        
        # Only announce if enabled
        if self.enabled:
            message = random.choice(self.ad_end_messages)
            await self._announce_ad(message, is_start=False)
            
        logger.info("Ad break ended")
        
    async def _schedule_ad_end(self) -> None:
        """
        Schedule an ad end announcement if we don't receive an end event
        """
        # Wait for the ad duration
        await asyncio.sleep(self.ad_duration)
        
        # If ad is still marked as active, announce the end
        if self.ad_active:
            await self._handle_ad_end({})
            
    async def _announce_ad(self, message: str, is_start: bool = True) -> None:
        """
        Announce ad break in chat and/or voice
        
        Args:
            message: Message to announce
            is_start: Whether this is an ad start (True) or end (False)
        """
        try:
            # Send to chat if enabled
            if self.announce_in_chat and self.twitch_client:
                await self.twitch_client.send_message(message)
                
            # Send to voice if enabled
            if self.announce_with_voice and self.audio_queue:
                # Use higher priority for ad announcements
                if self.response_coordinator:
                    # Create tasks for synchronized delivery
                    tasks = []
                    
                    # Always include chat for ad announcements
                    chat_task = {
                        'type': 'chat',
                        'content': message,
                        'priority': 'high'
                    }
                    tasks.append(chat_task)
                    
                    # Add voice if TTS is enabled
                    if self.audio_queue:
                        audio_task = {
                            'type': 'audio',
                            'content': message,
                            'priority': 'high'
                        }
                        tasks.append(audio_task)
                        
                    # Send through coordinator
                    await self.response_coordinator.coordinate_response(
                        tasks=tasks,
                        response_id=f"ad_{datetime.now().timestamp()}"
                    )
                else:
                    # Direct audio queue if no coordinator
                    await self.audio_queue.queue_audio(
                        text=message,
                        priority='high'
                    )
                    
        except Exception as e:
            logger.error(f"Error announcing ad: {e}")
            
    def update_settings(self, settings: Dict[str, Any]) -> None:
        """
        Update ad announcer settings
        
        Args:
            settings: New settings dictionary
        """
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
        """
        Get current ad announcer status
        
        Returns:
            Status dictionary
        """
        return {
            'enabled': self.enabled,
            'ad_active': self.ad_active,
            'ad_duration': self.ad_duration if self.ad_active else 0,
            'time_remaining': self._get_time_remaining(),
            'announce_in_chat': self.announce_in_chat,
            'announce_with_voice': self.announce_with_voice
        }
        
    def _get_time_remaining(self) -> int:
        """
        Get time remaining in current ad break
        
        Returns:
            Seconds remaining, or 0 if no ad active
        """
        if not self.ad_active or not self.ad_start_time:
            return 0
            
        elapsed = (datetime.now() - self.ad_start_time).total_seconds()
        remaining = max(0, self.ad_duration - elapsed)
        return int(remaining)
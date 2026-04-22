"""Dynamic raider welcome - LLM analyzes everything."""
import asyncio
import time
import os
import aiohttp
from collections import deque
from typing import Optional, Dict, Any
import json
import logging

logger = logging.getLogger(__name__)


class RaiderWelcome:
    """Fully dynamic content analysis - no hardcoded patterns."""
    
    def __init__(self, twitch_client, llm_service, tts_service, response_coordinator=None):
        self.twitch = twitch_client
        self.llm = llm_service
        self.tts = tts_service
        self.coordinator = response_coordinator
        self.recent_raids = deque(maxlen=10)
        self.insight_cache = {}  # Cache LLM insights
        self.current_game = None
        logger.info("RaiderWelcome initialized")
        
    async def handle_raid(self, raid_event: Dict[str, Any]) -> None:
        """Handle raid with dynamic analysis."""
        raider = raid_event.get('from_broadcaster_login', 'unknown')
        raider_display = raid_event.get('from_broadcaster_name', raider)
        size = raid_event.get('viewers', 0)
        
        logger.info(f"Processing raid from {raider_display} with {size} viewers")
        
        # Check repeat raider
        is_repeat = any(r['raider'] == raider for r in self.recent_raids)
        
        try:
            # Use wait_for instead of async with timeout for Python 3.10 compatibility
            async def _process():
                # Fetch all content
                content = await self._fetch_content(raider)
                
                # Get dynamic insight from LLM
                insight = await self._get_dynamic_insight(content)
                
                # Generate welcome
                return await self._generate_welcome(
                    raider_display, size, insight, is_repeat
                )
            
            welcome = await asyncio.wait_for(_process(), timeout=2.0)
            
        except asyncio.TimeoutError:
            logger.warning(f"Timeout processing raid from {raider_display}")
            welcome = self._simple_welcome(raider_display, size, is_repeat)
        except Exception as e:
            logger.error(f"Error processing raid: {e}")
            welcome = self._simple_welcome(raider_display, size, is_repeat)
        
        # Deliver
        await self._deliver_welcome(welcome)
        
        # Track
        self.recent_raids.append({
            'raider': raider,
            'size': size,
            'time': time.time()
        })
    
    async def _fetch_content(self, raider: str) -> Dict:
        """
        Fetch raider's content from Twitch API.

        Returns:
            Dict with channel, vods, and clip information
        """
        client_id = os.getenv('TWITCH_CLIENT_ID')
        token = os.getenv('TWITCH_BROADCASTER_TOKEN') or os.getenv('TWITCH_ACCESS_TOKEN')

        if not client_id or not token:
            logger.debug("Missing Twitch credentials for content fetch")
            return {'channel': None, 'vods': None, 'clip': None}

        headers = {
            'Client-ID': client_id,
            'Authorization': f'Bearer {token}'
        }

        content = {}

        try:
            async with aiohttp.ClientSession() as session:
                # First, get user ID from username
                raider_id = None
                async with session.get(
                    f'https://api.twitch.tv/helix/users?login={raider}',
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=2.0)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('data'):
                            raider_id = data['data'][0]['id']
                            logger.debug(f"Found raider ID: {raider_id}")

                if not raider_id:
                    return {'channel': None, 'vods': None, 'clip': None}

                # 1. Get channel info (current/last stream title and game)
                async with session.get(
                    f'https://api.twitch.tv/helix/channels?broadcaster_id={raider_id}',
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=2.0)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('data'):
                            channel = data['data'][0]
                            content['channel'] = {
                                'title': channel.get('title', ''),
                                'game': channel.get('game_name', ''),
                                'tags': channel.get('tags', [])[:3]
                            }
                            logger.debug(f"Raider channel: {content['channel']['game']} - {content['channel']['title']}")

                # 2. Get recent VODs (last 3)
                async with session.get(
                    f'https://api.twitch.tv/helix/videos?user_id={raider_id}&type=archive&first=3',
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=2.0)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('data'):
                            content['vods'] = [
                                {
                                    'title': v.get('title', ''),
                                    'game': v.get('game_name', ''),
                                    'duration': v.get('duration', '')
                                }
                                for v in data['data']
                            ]
                            logger.debug(f"Raider VODs: {len(content.get('vods', []))} found")

                # 3. Get top clip
                async with session.get(
                    f'https://api.twitch.tv/helix/clips?broadcaster_id={raider_id}&first=1',
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=2.0)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('data'):
                            clip = data['data'][0]
                            content['clip'] = {
                                'title': clip.get('title', ''),
                                'views': clip.get('view_count', 0),
                                'game': clip.get('game_name', '')
                            }
                            logger.debug(f"Raider top clip: {content['clip']['title']} ({content['clip']['views']} views)")

            return content if content else {'channel': None, 'vods': None, 'clip': None}

        except asyncio.TimeoutError:
            logger.debug(f"Timeout fetching content for {raider}")
            return {'channel': None, 'vods': None, 'clip': None}
        except Exception as e:
            logger.error(f"Error fetching raider content: {e}")
            return {'channel': None, 'vods': None, 'clip': None}
    
    async def _get_dynamic_insight(self, content: Dict) -> Optional[str]:
        """Let LLM find the interesting angle - no hardcoding."""
        
        # Check cache first
        cache_key = json.dumps(content, sort_keys=True, default=str)[:100]
        if cache_key in self.insight_cache:
            cached = self.insight_cache[cache_key]
            if time.time() - cached['time'] < 300:  # 5 min cache
                return cached['insight']
        
        # Build context for LLM
        context_parts = []
        
        if content.get('channel'):
            context_parts.append(f"Currently streaming: {content['channel'].get('title', 'Unknown')}")
            context_parts.append(f"Game: {content['channel'].get('game', 'Unknown')}")
            if content['channel'].get('tags'):
                context_parts.append(f"Tags: {', '.join(content['channel']['tags'][:3])}")
        
        if content.get('vods'):
            vods = content['vods']
            if vods:
                context_parts.append(f"Recent VOD: {vods[0]['title']}")
                if vods[0].get('duration'):
                    context_parts.append(f"VOD length: {vods[0]['duration']}")
                if len(vods) > 1:
                    games = [v['game'] for v in vods if v.get('game')]
                    if games:
                        context_parts.append(f"Recent games: {', '.join(games[:3])}")
        
        if content.get('clip'):
            context_parts.append(f"Popular clip: {content['clip']['title']}")
            context_parts.append(f"Clip views: {content['clip']['views']}")
        
        if not context_parts:
            return None
        
        # Add current game context
        if self.current_game:
            context_parts.append(f"We're currently playing: {self.current_game}")
        
        # LLM extracts insight
        prompt = f"""Analyze this streamer's content and extract ONE interesting characteristic.

Content:
{chr(10).join(context_parts)}

Instructions:
- Find the MOST interesting/impressive thing about them
- Return a 2-4 word descriptor
- Be creative but accurate
- Focus on achievements, style, or unique aspects
- Don't just repeat the game name

Examples of good responses:
"speedrun champion"
"variety legend"  
"marathon warrior"
"creative genius"
"community builder"
"challenge seeker"
"rising star"

If nothing stands out, return "awesome streamer"

Your response (2-4 words only):"""
        
        try:
            # Use the LLM service's generate_response method
            response = await self.llm.generate_response(
                prompt,
                max_length=20
            )
            
            insight = response.strip().lower()
            
            # Remove quotes if present
            insight = insight.replace('"', '').replace("'", '')
            
            # Basic validation
            if len(insight.split()) > 5 or len(insight) > 30:
                insight = "awesome streamer"
            
            # Cache it
            self.insight_cache[cache_key] = {
                'insight': insight,
                'time': time.time()
            }
            
            # Clean old cache entries
            if len(self.insight_cache) > 100:
                self._clean_cache()
            
            return insight
            
        except Exception as e:
            logger.debug(f"Insight extraction failed: {e}")
            return "awesome streamer"
    
    async def _generate_welcome(self, raider: str, size: int, 
                               insight: Optional[str], is_repeat: bool) -> str:
        """Generate contextual welcome."""
        
        # Let LLM generate the entire welcome
        prompt = f"""Generate an enthusiastic raid welcome message.

Details:
- Raider: {raider}
- Viewers: {size}
- {"Returning raider" if is_repeat else "First time raider"}
{f"- They are: {insight}" if insight else ""}

Requirements:
- ONE sentence only
- Maximum 15 words
- Include raider's name
- Match energy to viewer count (higher = more excited)
- Natural and genuine, not robotic
{f"- Naturally mention they're a {insight}" if insight else ""}

Generate the welcome:"""
        
        try:
            response = await self.llm.generate_response(
                prompt,
                max_length=40
            )
            
            welcome = response.strip()
            
            # Remove quotes if LLM added them
            welcome = welcome.replace('"', '').replace("'", '')
            
            # Validate length
            if len(welcome.split()) > 20:
                return self._simple_welcome(raider, size, is_repeat)
            
            return welcome
            
        except Exception as e:
            logger.debug(f"Welcome generation failed: {e}")
            return self._simple_welcome(raider, size, is_repeat)
    
    def _simple_welcome(self, raider: str, size: int, is_repeat: bool) -> str:
        """Ultra-simple fallback."""
        if is_repeat:
            return f"Welcome back {raider} and {size} friends!"
        elif size > 50:
            return f"HUGE raid from {raider}! {size} viewers!"
        else:
            return f"Welcome {raider} and {size} awesome viewers!"
    
    async def _deliver_welcome(self, welcome: str) -> None:
        """Deliver welcome via chat and voice."""
        try:
            if self.coordinator:
                # Use response coordinator for synchronized delivery
                await self.coordinator.coordinate_response(
                    welcome,
                    welcome,
                    priority='high'
                )
            else:
                # Direct delivery
                tasks = []
                
                # Send chat message
                if self.twitch:
                    tasks.append(self.twitch.send_message(welcome))
                
                # Send TTS with instant priority
                if self.tts:
                    if hasattr(self.tts, 'speak_with_priority'):
                        tasks.append(self.tts.speak_with_priority(welcome, priority='instant'))
                    else:
                        tasks.append(self.tts.speak(welcome))
                
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                    
            logger.info(f"Delivered welcome: {welcome[:50]}...")
            
        except Exception as e:
            logger.error(f"Error delivering welcome: {e}")
    
    def _clean_cache(self):
        """Remove old cache entries."""
        current_time = time.time()
        self.insight_cache = {
            k: v for k, v in self.insight_cache.items()
            if current_time - v['time'] < 300
        }
    
    def set_current_game(self, game: str) -> None:
        """Update the current game being streamed."""
        self.current_game = game
        logger.debug(f"Current game updated to: {game}")
    
    def get_current_game(self) -> str:
        """Get the current game being streamed."""
        return self.current_game or 'Just Chatting'
    
    def get_stats(self) -> Dict[str, Any]:
        """Get raider welcome statistics."""
        return {
            'recent_raids_count': len(self.recent_raids),
            'cache_size': len(self.insight_cache),
            'current_game': self.get_current_game()
        }
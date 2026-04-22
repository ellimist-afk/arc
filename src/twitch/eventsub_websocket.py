"""
Twitch EventSub WebSocket Client
Connects directly to Twitch - no webhooks or public URLs needed!
"""

import asyncio
import logging
import json
import os
import websockets
import aiohttp
from typing import Optional, Dict, Any, Callable, List
from datetime import datetime

logger = logging.getLogger(__name__)


class EventSubWebSocket:
    """
    WebSocket-based EventSub client - works just like Twitch chat connection
    """
    
    def __init__(
        self,
        client_id: str,
        access_token: str,
        channel_name: str = None,
        broadcaster_id: str = None
    ):
        """
        Initialize EventSub WebSocket client

        Args:
            client_id: Twitch app client ID
            access_token: User access token (bot token)
            channel_name: Channel to monitor (defaults to token owner)
            broadcaster_id: Optional broadcaster ID (will fetch if not provided)
        """
        self.client_id = client_id
        self.access_token = access_token  # Bot token
        self.broadcaster_token = os.getenv('TWITCH_BROADCASTER_TOKEN', '')  # Broadcaster token
        self.channel_name = channel_name

        # WebSocket connection
        self.websocket = None
        self.session_id = None
        self.connected = False
        self.reconnect_url = None

        # Event handlers
        self.event_handlers: Dict[str, List[Callable]] = {}

        # User info
        self.broadcaster_id = broadcaster_id
        self.moderator_id = broadcaster_id  # Use same for now

        logger.info("EventSub WebSocket client initialized")
        if self.broadcaster_token:
            logger.info("Broadcaster token loaded for privileged subscriptions")
        
    async def connect(self) -> None:
        """Connect to Twitch EventSub WebSocket"""
        try:
            # Get user info first
            await self._get_user_info()
            
            # Connect to EventSub WebSocket
            logger.info("Connecting to Twitch EventSub WebSocket...")
            
            # EventSub WebSocket URL
            url = "wss://eventsub.wss.twitch.tv/ws"
            
            async with websockets.connect(url) as websocket:
                self.websocket = websocket
                self.connected = True
                logger.info("Connected to EventSub WebSocket")
                
                # Handle messages
                await self._handle_messages()
                
        except Exception as e:
            logger.error(f"EventSub WebSocket connection failed: {e}")
            self.connected = False
            # Reconnect after delay
            await asyncio.sleep(5)
            asyncio.create_task(self.connect())
            
    async def _handle_messages(self) -> None:
        """Handle incoming WebSocket messages"""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self._process_message(data)
                
        except websockets.exceptions.ConnectionClosed:
            logger.warning("EventSub WebSocket connection closed")
            self.connected = False
            await self._reconnect()
            
        except Exception as e:
            logger.error(f"Error handling EventSub messages: {e}")
            
    async def _process_message(self, data: Dict[str, Any]) -> None:
        """Process EventSub WebSocket message"""
        metadata = data.get('metadata', {})
        message_type = metadata.get('message_type', '')
        
        if message_type == 'session_welcome':
            # Save session ID and subscribe to events
            payload = data.get('payload', {})
            session = payload.get('session', {})
            self.session_id = session.get('id')
            self.reconnect_url = session.get('reconnect_url')
            
            logger.info(f"EventSub session established: {self.session_id}")
            
            # Subscribe to events
            await self._subscribe_to_events()
            
        elif message_type == 'notification':
            # Event notification
            payload = data.get('payload', {})
            subscription = payload.get('subscription', {})
            event = payload.get('event', {})
            event_type = subscription.get('type', '')
            
            logger.info(f"EventSub event received: {event_type}")
            
            # Call registered handlers
            if event_type in self.event_handlers:
                for handler in self.event_handlers[event_type]:
                    asyncio.create_task(handler(event))
                    
        elif message_type == 'session_keepalive':
            # Keepalive message
            logger.debug("EventSub keepalive received")
            
        elif message_type == 'session_reconnect':
            # Need to reconnect
            payload = data.get('payload', {})
            session = payload.get('session', {})
            self.reconnect_url = session.get('reconnect_url')
            logger.info("EventSub reconnect requested")
            await self._reconnect()
            
        elif message_type == 'revocation':
            # Subscription revoked
            logger.warning("EventSub subscription revoked")
            
    async def _reconnect(self) -> None:
        """Reconnect to EventSub WebSocket"""
        if self.reconnect_url:
            logger.info(f"Reconnecting to: {self.reconnect_url}")
            # Close current connection
            if self.websocket:
                await self.websocket.close()
            # Connect to new URL
            async with websockets.connect(self.reconnect_url) as websocket:
                self.websocket = websocket
                self.connected = True
                await self._handle_messages()
        else:
            # Full reconnect
            await self.connect()
            
    async def _get_user_info(self) -> None:
        """Get broadcaster and moderator IDs"""
        # Skip if already provided
        if self.broadcaster_id:
            logger.info(f"Using provided broadcaster ID: {self.broadcaster_id}")
            return

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'Client-ID': self.client_id,
                    'Authorization': f'Bearer {self.access_token}'
                }

                # Get user info
                params = {}
                if self.channel_name:
                    params['login'] = self.channel_name

                async with session.get(
                    'https://api.twitch.tv/helix/users',
                    headers=headers,
                    params=params
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        users = data.get('data', [])
                        if users:
                            user = users[0]
                            self.broadcaster_id = user['id']
                            self.moderator_id = user['id']  # Use same for now
                            logger.info(f"Got user info: {user['display_name']} (ID: {self.broadcaster_id})")

        except Exception as e:
            logger.error(f"Failed to get user info: {e}")
            
    async def _subscribe_to_events(self) -> None:
        """Subscribe to Twitch events via API"""
        if not self.session_id or not self.broadcaster_id:
            logger.error("Cannot subscribe: missing session ID or broadcaster ID")
            return
            
        # Events to subscribe to
        subscriptions = [
            # Ad breaks
            {
                'type': 'channel.ad_break.begin',
                'version': '1',  # Use version 1
                'condition': {
                    'broadcaster_user_id': self.broadcaster_id
                }
            },
            # Raids
            {
                'type': 'channel.raid',
                'version': '1',
                'condition': {
                    'to_broadcaster_user_id': self.broadcaster_id
                }
            },
            # Follows
            {
                'type': 'channel.follow',
                'version': '2',
                'condition': {
                    'broadcaster_user_id': self.broadcaster_id,
                    'moderator_user_id': self.moderator_id
                }
            },
            # Subscriptions
            {
                'type': 'channel.subscribe',
                'version': '1',
                'condition': {
                    'broadcaster_user_id': self.broadcaster_id
                }
            },
            # Bits/Cheers
            {
                'type': 'channel.cheer',
                'version': '1',
                'condition': {
                    'broadcaster_user_id': self.broadcaster_id
                }
            }
        ]
        
        # Create subscriptions
        for sub in subscriptions:
            await self._create_subscription(sub)
            
    async def _create_subscription(self, subscription: Dict[str, Any]) -> bool:
        """Create an EventSub subscription"""
        try:
            # Use broadcaster token for ALL subscriptions if available
            # Twitch requires same token for all subs on one WebSocket
            if self.broadcaster_token:
                token = self.broadcaster_token
                logger.debug(f"Using broadcaster token for {subscription['type']}")
            else:
                token = self.access_token
                logger.debug(f"Using bot token for {subscription['type']}")

            async with aiohttp.ClientSession() as session:
                headers = {
                    'Client-ID': self.client_id,
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json'
                }

                body = {
                    'type': subscription['type'],
                    'version': subscription['version'],
                    'condition': subscription['condition'],
                    'transport': {
                        'method': 'websocket',
                        'session_id': self.session_id
                    }
                }

                async with session.post(
                    'https://api.twitch.tv/helix/eventsub/subscriptions',
                    headers=headers,
                    json=body
                ) as response:
                    if response.status in [202, 409]:  # 409 = already exists
                        logger.info(f"Subscribed to: {subscription['type']}")
                        return True
                    else:
                        text = await response.text()
                        logger.error(f"Failed to subscribe to {subscription['type']}: {response.status} - {text}")
                        return False

        except Exception as e:
            logger.error(f"Error creating subscription: {e}")
            return False
            
    def on_event(self, event_type: str, handler: Callable) -> None:
        """
        Register an event handler
        
        Args:
            event_type: Type of event (e.g., 'channel.ad_break.begin')
            handler: Async function to call when event occurs
        """
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        self.event_handlers[event_type].append(handler)
        logger.info(f"Registered handler for {event_type}")
        
    async def disconnect(self) -> None:
        """Disconnect from EventSub WebSocket"""
        if self.websocket:
            await self.websocket.close()
            self.connected = False
            logger.info("Disconnected from EventSub WebSocket")
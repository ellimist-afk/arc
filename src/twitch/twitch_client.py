"""
Twitch IRC client implementation
Handles connection, authentication, and message handling
"""

import asyncio
import logging
import re
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime
import ssl

logger = logging.getLogger(__name__)


class TwitchClient:
    """
    Twitch IRC client for chat integration
    """
    
    def __init__(
        self,
        access_token: str,
        client_id: str,
        channel: str,
        bot_username: str,
        ssl_context: Optional[ssl.SSLContext] = None
    ):
        """
        Initialize the Twitch client
        
        Args:
            access_token: OAuth token for authentication
            client_id: Twitch application client ID
            channel: Channel to join
            bot_username: Bot's username
            ssl_context: Optional SSL context
        """
        self.access_token = access_token
        self.client_id = client_id
        self.channel = channel.lower().strip('#')
        self.bot_username = bot_username.lower()
        self.ssl_context = ssl_context or ssl.create_default_context()
        
        # Connection state
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        
        # Message handlers
        self.message_handlers: List[Callable] = []
        self.event_handlers: Dict[str, List[Callable]] = {}
        
        # Rate limiting
        self.last_message_time = 0
        self.message_cooldown = 1.5  # Seconds between messages
        
        # Ping/Pong for connection health
        self.last_ping = datetime.now()
        self.ping_interval = 60  # Send PING every 60 seconds
        
    async def connect(self) -> None:
        """
        Connect to Twitch IRC
        """
        try:
            logger.info(f"Connecting to Twitch IRC for channel #{self.channel}...")
            
            # Connect to Twitch IRC
            self.reader, self.writer = await asyncio.open_connection(
                'irc.chat.twitch.tv',
                6697,
                ssl=self.ssl_context
            )
            
            # Authenticate
            await self._send_raw(f'PASS oauth:{self.access_token}')
            await self._send_raw(f'NICK {self.bot_username}')
            
            # Request capabilities
            await self._send_raw('CAP REQ :twitch.tv/membership')
            await self._send_raw('CAP REQ :twitch.tv/tags')
            await self._send_raw('CAP REQ :twitch.tv/commands')
            
            # Join channel
            await self._send_raw(f'JOIN #{self.channel}')
            
            self.connected = True
            self.reconnect_attempts = 0
            
            # Start message handler
            asyncio.create_task(self._handle_messages())
            
            # Start ping task
            asyncio.create_task(self._ping_task())
            
            logger.info(f"Connected to Twitch channel #{self.channel}")
            
        except Exception as e:
            logger.error(f"Failed to connect to Twitch: {e}")
            self.connected = False
            await self._handle_reconnect()
            
    async def _send_raw(self, message: str) -> None:
        """
        Send raw IRC message
        
        Args:
            message: Raw IRC message to send
        """
        if self.writer:
            self.writer.write(f'{message}\r\n'.encode('utf-8'))
            await self.writer.drain()
            logger.debug(f"Sent: {message}")
            
    async def send_message(self, message: str) -> None:
        """
        Send a chat message to the channel
        
        Args:
            message: Message to send
        """
        if not self.connected:
            logger.warning("Not connected to Twitch, cannot send message")
            return
            
        # Rate limiting
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - self.last_message_time
        if time_since_last < self.message_cooldown:
            await asyncio.sleep(self.message_cooldown - time_since_last)
            
        # Send message
        await self._send_raw(f'PRIVMSG #{self.channel} :{message}')
        self.last_message_time = asyncio.get_event_loop().time()
        
    async def _handle_messages(self) -> None:
        """
        Handle incoming IRC messages
        """
        while self.connected and self.reader:
            try:
                data = await self.reader.readline()
                if not data:
                    logger.warning("Connection closed by Twitch")
                    self.connected = False
                    await self._handle_reconnect()
                    break
                    
                message = data.decode('utf-8', errors='ignore').strip()
                
                # Handle PING
                if message.startswith('PING'):
                    await self._send_raw('PONG :tmi.twitch.tv')
                    continue
                    
                # Parse and handle message
                await self._parse_message(message)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error handling message: {e}")
                
    async def _parse_message(self, raw_message: str) -> None:
        """
        Parse IRC message and trigger handlers
        
        Args:
            raw_message: Raw IRC message
        """
        try:
            # Parse IRC message
            tags = {}
            prefix = None
            command = None
            params = []
            
            # Parse tags
            if raw_message.startswith('@'):
                tags_part, raw_message = raw_message.split(' ', 1)
                for tag in tags_part[1:].split(';'):
                    if '=' in tag:
                        key, value = tag.split('=', 1)
                        tags[key] = value
                        
            # Parse prefix
            if raw_message.startswith(':'):
                prefix, raw_message = raw_message.split(' ', 1)
                prefix = prefix[1:]
                
            # Parse command and params
            if ' :' in raw_message:
                raw_message, trailing = raw_message.split(' :', 1)
                params = raw_message.split()
                command = params.pop(0) if params else raw_message
                params.append(trailing)
            else:
                params = raw_message.split()
                command = params.pop(0) if params else raw_message
                
            # Handle PRIVMSG (chat messages)
            if command == 'PRIVMSG':
                await self._handle_privmsg(tags, prefix, params)
                
            # Handle USERNOTICE (raids, subs, etc)
            elif command == 'USERNOTICE':
                await self._handle_usernotice(tags, prefix, params)
                
            # Handle other events
            elif command in self.event_handlers:
                for handler in self.event_handlers[command]:
                    asyncio.create_task(handler(tags, prefix, params))
                    
        except Exception as e:
            logger.error(f"Error parsing message: {e}")
            
    async def _handle_privmsg(
        self,
        tags: Dict[str, str],
        prefix: str,
        params: List[str]
    ) -> None:
        """
        Handle PRIVMSG (chat message)
        
        Args:
            tags: IRC tags
            prefix: Message prefix
            params: Message parameters
        """
        try:
            # Extract user info
            username = tags.get('display-name', '')
            if not username and prefix:
                username = prefix.split('!')[0]
                
            user_id = tags.get('user-id', '')
            
            # Extract message
            message_text = params[-1] if params else ''
            
            # Create message object
            message = {
                'type': 'chat',
                'username': username,
                'user_id': user_id,
                'message': message_text,  # Database expects 'message'
                'text': message_text,     # Keep for compatibility
                'channel': self.channel,
                'timestamp': datetime.now(),
                'tags': tags,
                'is_subscriber': tags.get('subscriber') == '1',
                'is_mod': tags.get('mod') == '1',
                'is_vip': tags.get('vip') == '1',
                'bits': int(tags.get('bits', 0))
            }
            
            # Trigger message handlers
            for handler in self.message_handlers:
                asyncio.create_task(handler(message))
                
        except Exception as e:
            logger.error(f"Error handling PRIVMSG: {e}")
            
    async def _handle_usernotice(
        self,
        tags: Dict[str, str],
        prefix: str,
        params: List[str]
    ) -> None:
        """
        Handle USERNOTICE (raids, subs, gifts, etc)
        
        Args:
            tags: IRC tags
            prefix: Message prefix
            params: Message parameters
        """
        try:
            msg_id = tags.get('msg-id', '')
            
            # Handle raids
            if msg_id == 'raid':
                raider_name = tags.get('msg-param-login', '')
                raider_display = tags.get('msg-param-displayName', raider_name)
                viewer_count = int(tags.get('msg-param-viewerCount', 0))
                
                raid_event = {
                    'type': 'raid',
                    'from_broadcaster_login': raider_name,
                    'from_broadcaster_name': raider_display,
                    'viewers': viewer_count,
                    'timestamp': datetime.now(),
                    'tags': tags
                }
                
                # Trigger raid handlers
                if 'raid' in self.event_handlers:
                    for handler in self.event_handlers['raid']:
                        asyncio.create_task(handler(raid_event))
                        
            # Handle subscriptions
            elif msg_id in ['sub', 'resub']:
                sub_event = {
                    'type': msg_id,
                    'username': tags.get('login', ''),
                    'display_name': tags.get('display-name', ''),
                    'months': int(tags.get('msg-param-cumulative-months', 1)),
                    'tier': tags.get('msg-param-sub-plan', '1000'),
                    'message': params[-1] if params else '',
                    'timestamp': datetime.now(),
                    'tags': tags
                }
                
                if 'subscription' in self.event_handlers:
                    for handler in self.event_handlers['subscription']:
                        asyncio.create_task(handler(sub_event))
                        
            # Handle gift subs
            elif msg_id == 'subgift':
                gift_event = {
                    'type': 'subgift',
                    'gifter': tags.get('login', ''),
                    'gifter_display': tags.get('display-name', ''),
                    'recipient': tags.get('msg-param-recipient-user-name', ''),
                    'recipient_display': tags.get('msg-param-recipient-display-name', ''),
                    'tier': tags.get('msg-param-sub-plan', '1000'),
                    'timestamp': datetime.now(),
                    'tags': tags
                }
                
                if 'subgift' in self.event_handlers:
                    for handler in self.event_handlers['subgift']:
                        asyncio.create_task(handler(gift_event))
                        
        except Exception as e:
            logger.error(f"Error handling USERNOTICE: {e}")
            
    def on_message(self, handler: Callable) -> None:
        """
        Register a message handler
        
        Args:
            handler: Async function to handle messages
        """
        self.message_handlers.append(handler)
        
    def on_event(self, event: str, handler: Callable) -> None:
        """
        Register an event handler
        
        Args:
            event: IRC event name
            handler: Async function to handle event
        """
        if event not in self.event_handlers:
            self.event_handlers[event] = []
        self.event_handlers[event].append(handler)
        
    async def _ping_task(self) -> None:
        """
        Send periodic PING to keep connection alive
        """
        while self.connected:
            try:
                await asyncio.sleep(self.ping_interval)
                await self._send_raw('PING :tmi.twitch.tv')
                self.last_ping = datetime.now()
            except Exception as e:
                logger.error(f"Error sending ping: {e}")
                
    async def _handle_reconnect(self) -> None:
        """
        Handle reconnection logic
        """
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error("Max reconnection attempts reached")
            return
            
        self.reconnect_attempts += 1
        wait_time = min(2 ** self.reconnect_attempts, 60)
        
        logger.info(f"Reconnecting in {wait_time} seconds (attempt {self.reconnect_attempts})...")
        await asyncio.sleep(wait_time)
        
        await self.connect()
        
    def is_connected(self) -> bool:
        """
        Check if client is connected
        
        Returns:
            True if connected
        """
        return self.connected
        
    async def disconnect(self) -> None:
        """
        Disconnect from Twitch IRC
        """
        logger.info("Disconnecting from Twitch...")
        self.connected = False
        
        if self.writer:
            try:
                await self._send_raw(f'PART #{self.channel}')
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                logger.error(f"Error during disconnect: {e}")
                
        self.reader = None
        self.writer = None
        
        logger.info("Disconnected from Twitch")
        
    async def join_channel(self, channel: str) -> None:
        """
        Join a channel
        
        Args:
            channel: Channel name to join
        """
        channel = channel.lower().strip('#')
        await self._send_raw(f'JOIN #{channel}')
        logger.info(f"Joined channel #{channel}")
        
    async def leave_channel(self, channel: str) -> None:
        """
        Leave a channel
        
        Args:
            channel: Channel name to leave
        """
        channel = channel.lower().strip('#')
        await self._send_raw(f'PART #{channel}')
        logger.info(f"Left channel #{channel}")
        
    async def send_whisper(self, username: str, message: str) -> None:
        """
        Send a whisper to a user
        
        Args:
            username: Target username
            message: Message to send
        """
        await self._send_raw(f'PRIVMSG #jtv :/w {username} {message}')
        
    async def timeout_user(
        self,
        username: str,
        duration: int = 600,
        reason: str = ""
    ) -> None:
        """
        Timeout a user
        
        Args:
            username: Username to timeout
            duration: Timeout duration in seconds
            reason: Timeout reason
        """
        command = f"/timeout {username} {duration}"
        if reason:
            command += f" {reason}"
        await self.send_message(command)
        
    async def ban_user(self, username: str, reason: str = "") -> None:
        """
        Ban a user
        
        Args:
            username: Username to ban
            reason: Ban reason
        """
        command = f"/ban {username}"
        if reason:
            command += f" {reason}"
        await self.send_message(command)
        
    async def unban_user(self, username: str) -> None:
        """
        Unban a user
        
        Args:
            username: Username to unban
        """
        await self.send_message(f"/unban {username}")
        
    def get_stats(self) -> Dict[str, Any]:
        """
        Get client statistics
        
        Returns:
            Statistics dictionary
        """
        return {
            'connected': self.connected,
            'channel': self.channel,
            'bot_username': self.bot_username,
            'reconnect_attempts': self.reconnect_attempts,
            'message_handlers': len(self.message_handlers),
            'last_ping': self.last_ping.isoformat() if self.last_ping else None
        }
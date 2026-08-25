"""
Twitch EventSub WebSocket Client
Connects directly to Twitch - no webhooks or public URLs needed!
"""

import asyncio
import logging
import json
import os
import time
import websockets
import aiohttp
from typing import Optional, Dict, Any, Callable, List
from datetime import datetime

from utils.task_registry import create_task as registry_create_task

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
        self._stopping = False  # set by disconnect() to stop the supervisor loop

        # Event handlers
        self.event_handlers: Dict[str, List[Callable]] = {}

        # User info
        self.broadcaster_id = broadcaster_id
        self.moderator_id = broadcaster_id  # Use same for now

        # Liveness / diagnostics (monotonic clock)
        self._connected_at: Optional[float] = None
        self._disconnected_at: Optional[float] = None
        self._last_message_at: Optional[float] = None
        self._last_keepalive_at: Optional[float] = None
        self._keepalive_timeout: int = 10  # updated from session_welcome
        self._watchdog_generation: int = 0  # invalidates stale watchdogs on reconnect

        logger.info("EventSub WebSocket client initialized")
        if self.broadcaster_token:
            logger.info("Broadcaster token loaded for privileged subscriptions")
        
    async def connect(self) -> None:
        """
        Connect to Twitch EventSub and supervise the connection.

        This is a single long-lived loop: it opens the socket, pumps messages
        until the connection drops, then reconnects. Reconnecting iteratively
        (rather than recursively from the read loop) keeps the call stack flat
        and avoids nesting a new `async with` for every reconnect over a stream.
        """
        await self._get_user_info()

        backoff = 1.0
        while not self._stopping:
            # After the first session, Twitch may hand us a reconnect_url that
            # carries session state; prefer it, else do a fresh connect.
            url = self.reconnect_url or "wss://eventsub.wss.twitch.tv/ws"
            self.reconnect_url = None  # consume it; refreshed by session messages

            try:
                logger.info(f"Connecting to EventSub WebSocket: {url}")
                async with websockets.connect(url, **self._ws_options()) as websocket:
                    self.websocket = websocket
                    self.connected = True
                    self._connected_at = time.monotonic()
                    self._last_message_at = None
                    self._last_keepalive_at = None
                    backoff = 1.0  # reset after a successful open
                    logger.info("Connected to EventSub WebSocket")
                    self._start_keepalive_watchdog()

                    # Pump messages until the connection drops.
                    await self._handle_messages()

            except Exception as e:
                logger.error(f"EventSub WebSocket connection failed: {e}")

            self.connected = False
            if self._stopping:
                break

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _handle_messages(self) -> None:
        """
        Pump incoming WebSocket messages until the connection closes.

        Returns (rather than reconnecting itself) so the supervisor loop in
        connect() owns all reconnect timing and backoff.
        """
        try:
            async for message in self.websocket:
                now = time.monotonic()
                data = json.loads(message)
                self._last_message_at = now

                await self._process_message(data)

                # A slow handler blocks the read loop, which delays our PONG to
                # Twitch's server pings and can get us dropped (close 4002). Log
                # it so the underlying loop-blocking is visible where it bites.
                handler_time = time.monotonic() - now
                if handler_time > 1.0:
                    msg_type = data.get('metadata', {}).get('message_type', '?')
                    logger.warning(
                        f"EventSub handler for '{msg_type}' blocked the read loop "
                        f"for {handler_time:.2f}s - event loop is starved"
                    )

        except websockets.exceptions.ConnectionClosed as e:
            now = time.monotonic()
            self._disconnected_at = now
            uptime = (now - self._connected_at) if self._connected_at else -1.0
            # Capture the close frames: code 1011 = client-side pong timeout,
            # 4002 = Twitch dropped us for a missed pong, 4004 = reconnect grace.
            rcvd = getattr(e, 'rcvd', None)
            sent = getattr(e, 'sent', None)
            logger.warning(
                f"EventSub connection closed after {uptime:.1f}s "
                f"(server_close={rcvd!r}, client_close={sent!r}) - reconnecting"
            )

        except Exception as e:
            self._disconnected_at = time.monotonic()
            logger.error(f"Error handling EventSub messages: {e} - reconnecting")
            
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
            self._keepalive_timeout = session.get('keepalive_timeout_seconds') or self._keepalive_timeout

            logger.info(
                f"EventSub session established: {self.session_id} "
                f"(keepalive_timeout={self._keepalive_timeout}s)"
            )
            if self._disconnected_at is not None:
                offline = time.monotonic() - self._disconnected_at
                self._disconnected_at = None
                logger.warning(
                    f"EventSub session re-established after {offline:.1f}s offline - "
                    f"any follow/sub/cheer events in that window were dropped"
                )

            # Subscribe to events OFF the read loop: the ~13 sequential
            # subscription POSTs took 3.11s inline, blocking the message pump
            # (and the PONGs to Twitch's server pings) the whole time. No
            # ordering hazard -- notifications can't arrive for subscriptions
            # that don't exist yet.
            registry_create_task(
                self._subscribe_to_events(),
                name=f"eventsub_subscribe_{self.session_id}",
            )
            
        elif message_type == 'notification':
            # Event notification
            payload = data.get('payload', {})
            subscription = payload.get('subscription', {})
            event = payload.get('event', {})
            event_type = subscription.get('type', '')
            
            logger.info(f"EventSub event received: {event_type}")
            
            # Call registered handlers. Dispatch off the read loop so a slow
            # handler can't delay our PONG to Twitch's next server ping.
            if event_type in self.event_handlers:
                for i, handler in enumerate(self.event_handlers[event_type]):
                    registry_create_task(
                        handler(event),
                        name=f"eventsub_handler_{event_type}_{i}_{metadata.get('message_id', '')}",
                    )
                    
        elif message_type == 'session_keepalive':
            # Keepalive message. Twitch sends these on a fixed interval and
            # closes the socket if IT stops hearing from us; we use their
            # arrival as our liveness signal (see _keepalive_watchdog).
            self._last_keepalive_at = time.monotonic()
            logger.debug("EventSub keepalive received")
            
        elif message_type == 'session_reconnect':
            # Twitch is asking us to move to a new URL (planned server maintenance).
            # Store it and close the current socket; the supervisor loop in
            # connect() will reconnect to reconnect_url with session state intact.
            payload = data.get('payload', {})
            session = payload.get('session', {})
            self.reconnect_url = session.get('reconnect_url')
            logger.info("EventSub reconnect requested by Twitch")
            try:
                if self.websocket:
                    await self.websocket.close()
            except Exception:
                pass
            
        elif message_type == 'revocation':
            # Subscription revoked
            logger.warning("EventSub subscription revoked")
            
    def _ws_options(self) -> Dict[str, Any]:
        """
        Connection options for websockets.connect().

        We disable the client library's own ping/pong keepalive
        (ping_interval=None). By default websockets pings the server every 20s
        and, if no pong arrives within ping_timeout (20s), tears the connection
        down itself with close code 1011. Because the pong is sent from the same
        event-loop coroutine that also runs blocking audio/LLM work in this bot,
        a starved loop misses that deadline and the library drops a perfectly
        healthy connection. Twitch already sends application-level
        session_keepalive messages (~every keepalive_timeout_seconds), so we let
        _keepalive_watchdog watch those instead.

        NOTE: this does NOT stop Twitch's *server-side* pings. Twitch also sends
        WebSocket PING frames and closes with code 4002 if we fail to PONG. That
        PONG is likewise sent from the (potentially starved) read coroutine, so a
        badly blocked loop can still be disconnected by the server. The real cure
        is to keep the loop unblocked (tracked separately: move blocking audio/LLM
        work off the loop). This change removes the self-inflicted 1011 teardown
        and makes any residual disconnect window short and loudly logged.
        """
        return {
            "ping_interval": None,   # do not run the client-side pinger
            "close_timeout": 5,
            "max_queue": 64,
        }

    def _start_keepalive_watchdog(self) -> None:
        """
        Start a fresh keepalive watchdog, invalidating any prior one.

        Each watchdog gets a unique task name so TaskRegistry does NOT cancel the
        previous one (name-based cancellation would fire a CancelledError through
        the registry's done-callback and spam the log on every reconnect). The
        prior watchdog instead notices the bumped generation and exits cleanly on
        its own within one keepalive interval.
        """
        self._watchdog_generation += 1
        registry_create_task(
            self._keepalive_watchdog(self._watchdog_generation),
            name=f"eventsub_keepalive_watchdog_{self._watchdog_generation}",
        )

    async def _keepalive_watchdog(self, generation: int) -> None:
        """
        Detect a genuinely dead connection and force a reconnect.

        Twitch closes the socket if IT stops hearing from us, and we should
        reconnect if WE stop hearing from Twitch. Liveness is judged purely from
        the monotonic-clock gap since the last inbound message (any message, not
        just keepalives), so a delayed tick cannot fabricate staleness.

        The threshold is deliberately generous (>= 3x keepalive_timeout, floor
        30s). This watchdog runs on the same event loop that gets starved, so its
        own asyncio.sleep can drift by seconds; a tight threshold would
        false-positive and cause the very reconnect churn we're trying to
        eliminate. We only act on multi-interval silence, which means the
        connection really is gone.
        """
        while self.connected and generation == self._watchdog_generation:
            await asyncio.sleep(self._keepalive_timeout)
            if (not self.connected or self._stopping
                    or generation != self._watchdog_generation):
                return

            last = self._last_message_at
            if last is None:
                continue  # nothing received yet on this connection

            silence = time.monotonic() - last
            stale_after = max(self._keepalive_timeout * 3, 30)
            if silence > stale_after:
                logger.warning(
                    f"No EventSub message for {silence:.1f}s "
                    f"(keepalive_timeout={self._keepalive_timeout}s) - "
                    f"connection presumed dead, forcing reconnect"
                )
                # Trip out of the read loop; closing wakes _handle_messages,
                # which returns and lets the connect() supervisor reconnect.
                try:
                    if self.websocket:
                        await self.websocket.close()
                except Exception:
                    pass
                return

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
            # Gift subs. Twitch also sends one channel.subscribe per
            # RECIPIENT for the same gift; this event is the only one that
            # names the gifter and carries the count, so it is the one we
            # announce (see _on_subscribe, which stays quiet for recipients).
            {
                'type': 'channel.subscription.gift',
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
            },
            # Category / title changes (no scope required)
            {
                'type': 'channel.update',
                'version': '2',
                'condition': {
                    'broadcaster_user_id': self.broadcaster_id
                }
            },
            # Stream lifecycle, for session reset + post-stream recap
            {
                'type': 'stream.online',
                'version': '1',
                'condition': {
                    'broadcaster_user_id': self.broadcaster_id
                }
            },
            {
                'type': 'stream.offline',
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
        """Disconnect from EventSub WebSocket and stop the supervisor loop."""
        self._stopping = True
        self.connected = False
        self._watchdog_generation += 1  # invalidate any running watchdog
        if self.websocket:
            await self.websocket.close()
            logger.info("Disconnected from EventSub WebSocket")
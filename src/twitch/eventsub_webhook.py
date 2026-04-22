"""
Twitch EventSub Webhook Handler
Receives real-time events from Twitch including ad breaks
"""

import asyncio
import logging
import hmac
import hashlib
import json
from typing import Optional, Dict, Any, Callable
from datetime import datetime
from aiohttp import web
import aiohttp
import ssl

logger = logging.getLogger(__name__)


class EventSubWebhook:
    """
    Handles Twitch EventSub webhooks for real-time events
    """
    
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        webhook_secret: str,
        callback_url: str,
        port: int = 8080
    ):
        """
        Initialize EventSub webhook handler
        
        Args:
            client_id: Twitch app client ID
            client_secret: Twitch app client secret
            access_token: User access token
            webhook_secret: Secret for verifying webhooks (you create this)
            callback_url: Public URL for webhook callbacks (e.g., https://yourdomain.com/webhooks/callback)
            port: Port to listen on (default 8080)
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.webhook_secret = webhook_secret
        self.callback_url = callback_url
        self.port = port
        
        # Webhook server
        self.app = web.Application()
        self.runner = None
        self.site = None
        
        # Event handlers
        self.event_handlers: Dict[str, list[Callable]] = {}
        
        # Active subscriptions
        self.subscriptions = {}
        
        # Setup routes
        self.app.router.add_post('/webhooks/callback', self.handle_webhook)
        self.app.router.add_get('/webhooks/callback', self.handle_verification)
        
        logger.info(f"EventSub webhook initialized on port {port}")
        
    async def start(self) -> None:
        """Start the webhook server"""
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, '0.0.0.0', self.port)
            await self.site.start()
            logger.info(f"EventSub webhook server started on port {self.port}")
            
            # Subscribe to events after server starts
            await self.subscribe_to_events()
            
        except Exception as e:
            logger.error(f"Failed to start webhook server: {e}")
            raise
            
    async def stop(self) -> None:
        """Stop the webhook server"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("EventSub webhook server stopped")
        
    async def handle_verification(self, request: web.Request) -> web.Response:
        """
        Handle webhook verification challenge from Twitch
        
        Args:
            request: Incoming GET request
            
        Returns:
            Challenge response
        """
        challenge = request.query.get('hub.challenge')
        if challenge:
            logger.info("Webhook verification challenge received")
            return web.Response(text=challenge)
        return web.Response(status=400)
        
    async def handle_webhook(self, request: web.Request) -> web.Response:
        """
        Handle incoming webhook events
        
        Args:
            request: Incoming POST request
            
        Returns:
            Response
        """
        try:
            # Read body
            body = await request.read()
            
            # Verify signature
            signature = request.headers.get('Twitch-Eventsub-Message-Signature', '')
            if not self._verify_signature(request.headers, body):
                logger.warning("Invalid webhook signature")
                return web.Response(status=403)
                
            # Parse event
            data = json.loads(body)
            
            # Handle message type
            message_type = request.headers.get('Twitch-Eventsub-Message-Type', '')
            
            if message_type == 'webhook_callback_verification':
                # Verification challenge
                challenge = data.get('challenge', '')
                logger.info("Responding to verification challenge")
                return web.Response(text=challenge)
                
            elif message_type == 'notification':
                # Event notification
                await self._process_event(data)
                return web.Response(status=204)
                
            elif message_type == 'revocation':
                # Subscription revoked
                subscription = data.get('subscription', {})
                logger.warning(f"Subscription revoked: {subscription.get('type')}")
                return web.Response(status=204)
                
            else:
                logger.warning(f"Unknown message type: {message_type}")
                return web.Response(status=400)
                
        except Exception as e:
            logger.error(f"Error handling webhook: {e}")
            return web.Response(status=500)
            
    def _verify_signature(self, headers: dict, body: bytes) -> bool:
        """
        Verify webhook signature
        
        Args:
            headers: Request headers
            body: Request body
            
        Returns:
            True if signature is valid
        """
        try:
            message_id = headers.get('Twitch-Eventsub-Message-Id', '')
            timestamp = headers.get('Twitch-Eventsub-Message-Timestamp', '')
            signature = headers.get('Twitch-Eventsub-Message-Signature', '')
            
            # Construct the message
            hmac_message = message_id + timestamp + body.decode('utf-8')
            
            # Calculate expected signature
            expected_sig = 'sha256=' + hmac.new(
                self.webhook_secret.encode('utf-8'),
                hmac_message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Compare signatures
            return hmac.compare_digest(expected_sig, signature)
            
        except Exception as e:
            logger.error(f"Error verifying signature: {e}")
            return False
            
    async def _process_event(self, data: Dict[str, Any]) -> None:
        """
        Process incoming event
        
        Args:
            data: Event data
        """
        try:
            subscription = data.get('subscription', {})
            event_type = subscription.get('type', '')
            event_data = data.get('event', {})
            
            logger.info(f"Received event: {event_type}")
            
            # Call registered handlers
            if event_type in self.event_handlers:
                for handler in self.event_handlers[event_type]:
                    asyncio.create_task(handler(event_data))
                    
        except Exception as e:
            logger.error(f"Error processing event: {e}")
            
    async def subscribe_to_events(self) -> None:
        """Subscribe to Twitch events"""
        try:
            # Get broadcaster ID
            broadcaster_id = await self._get_broadcaster_id()
            if not broadcaster_id:
                logger.error("Failed to get broadcaster ID")
                return
                
            # Subscribe to channel.ad_break.begin
            await self._create_subscription(
                event_type='channel.ad_break.begin',
                condition={'broadcaster_user_id': broadcaster_id}
            )
            
            logger.info("Subscribed to ad break events")
            
        except Exception as e:
            logger.error(f"Failed to subscribe to events: {e}")
            
    async def _get_broadcaster_id(self) -> Optional[str]:
        """
        Get broadcaster user ID from access token
        
        Returns:
            Broadcaster user ID or None
        """
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'Client-ID': self.client_id,
                    'Authorization': f'Bearer {self.access_token}'
                }
                
                async with session.get(
                    'https://api.twitch.tv/helix/users',
                    headers=headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        users = data.get('data', [])
                        if users:
                            return users[0]['id']
                            
        except Exception as e:
            logger.error(f"Failed to get broadcaster ID: {e}")
            
        return None
        
    async def _create_subscription(
        self,
        event_type: str,
        condition: Dict[str, str]
    ) -> bool:
        """
        Create an EventSub subscription
        
        Args:
            event_type: Type of event to subscribe to
            condition: Subscription condition
            
        Returns:
            Success status
        """
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'Client-ID': self.client_id,
                    'Authorization': f'Bearer {self.access_token}',
                    'Content-Type': 'application/json'
                }
                
                body = {
                    'type': event_type,
                    'version': '1',
                    'condition': condition,
                    'transport': {
                        'method': 'webhook',
                        'callback': self.callback_url,
                        'secret': self.webhook_secret
                    }
                }
                
                async with session.post(
                    'https://api.twitch.tv/helix/eventsub/subscriptions',
                    headers=headers,
                    json=body
                ) as response:
                    if response.status in [202, 409]:  # 409 = already exists
                        data = await response.json()
                        logger.info(f"Subscription created/exists: {event_type}")
                        return True
                    else:
                        text = await response.text()
                        logger.error(f"Failed to create subscription: {response.status} - {text}")
                        return False
                        
        except Exception as e:
            logger.error(f"Error creating subscription: {e}")
            return False
            
    def on_event(self, event_type: str, handler: Callable) -> None:
        """
        Register an event handler
        
        Args:
            event_type: Type of event to handle
            handler: Async function to call when event occurs
        """
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        self.event_handlers[event_type].append(handler)
        logger.info(f"Registered handler for {event_type}")
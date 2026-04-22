"""
WebSocket manager for real-time communication
Handles client connections and broadcasts
"""

import asyncio
import logging
import json
from typing import Dict, Set, Any, Optional
from datetime import datetime
import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Manages WebSocket connections for real-time updates
    """
    
    def __init__(self, host: str = "localhost", port: int = 8765):
        """
        Initialize WebSocket manager
        
        Args:
            host: Host to bind to
            port: Port to bind to
        """
        self.host = host
        self.port = port
        self.server = None
        self.clients: Set[WebSocketServerProtocol] = set()
        self.client_metadata: Dict[WebSocketServerProtocol, Dict[str, Any]] = {}
        self.is_running = False
        
        # Message statistics
        self.messages_sent = 0
        self.messages_received = 0
        self.broadcasts_sent = 0
        
    async def initialize(self) -> None:
        """Initialize and start the WebSocket server"""
        try:
            logger.info(f"Starting WebSocket server on ws://{self.host}:{self.port}")
            
            self.server = await websockets.serve(
                self.handle_client,
                self.host,
                self.port
            )
            self.is_running = True
            logger.info(f"WebSocket server successfully started on ws://{self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start WebSocket server: {e}")
            self.is_running = False
            # Don't raise - let the bot continue without WebSocket
        
    async def handle_client(self, websocket: WebSocketServerProtocol, path: str) -> None:
        """
        Handle a WebSocket client connection
        
        Args:
            websocket: WebSocket connection
            path: Connection path
        """
        # Register client
        self.clients.add(websocket)
        self.client_metadata[websocket] = {
            'connected_at': datetime.now(),
            'path': path,
            'messages_sent': 0,
            'messages_received': 0
        }
        
        client_id = id(websocket)
        logger.info(f"Client {client_id} connected from {websocket.remote_address}")
        
        # Send welcome message
        await self.send_to_client(websocket, {
            'type': 'welcome',
            'data': {
                'message': 'Connected to TalkBot WebSocket',
                'timestamp': datetime.now().isoformat()
            }
        })
        
        try:
            # Handle messages from client
            async for message in websocket:
                await self.handle_message(websocket, message)
                
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client {client_id} disconnected")
        except Exception as e:
            logger.error(f"Error handling client {client_id}: {e}")
        finally:
            # Unregister client
            self.clients.discard(websocket)
            if websocket in self.client_metadata:
                del self.client_metadata[websocket]
                
    async def handle_message(self, websocket: WebSocketServerProtocol, message: str) -> None:
        """
        Handle a message from a client
        
        Args:
            websocket: Client connection
            message: Message data
        """
        try:
            # Parse message
            data = json.loads(message)
            message_type = data.get('type')
            
            # Update statistics
            self.messages_received += 1
            if websocket in self.client_metadata:
                self.client_metadata[websocket]['messages_received'] += 1
                
            logger.debug(f"Received message type: {message_type}")
            
            # Handle different message types
            if message_type == 'ping':
                await self.send_to_client(websocket, {
                    'type': 'pong',
                    'data': {'timestamp': datetime.now().isoformat()}
                })
                
            elif message_type == 'subscribe':
                # Handle subscription to specific events
                events = data.get('events', [])
                if websocket in self.client_metadata:
                    self.client_metadata[websocket]['subscriptions'] = events
                logger.info(f"Client subscribed to events: {events}")
                
            elif message_type == 'command':
                # Handle commands (to be implemented based on needs)
                command = data.get('command')
                logger.info(f"Received command: {command}")
                
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received: {message}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            
    async def send_to_client(
        self,
        websocket: WebSocketServerProtocol,
        data: Dict[str, Any]
    ) -> bool:
        """
        Send data to a specific client
        
        Args:
            websocket: Client connection
            data: Data to send
            
        Returns:
            True if sent successfully
        """
        try:
            message = json.dumps(data)
            await websocket.send(message)
            
            # Update statistics
            self.messages_sent += 1
            if websocket in self.client_metadata:
                self.client_metadata[websocket]['messages_sent'] += 1
                
            return True
            
        except websockets.exceptions.ConnectionClosed:
            logger.debug("Connection closed while sending")
            self.clients.discard(websocket)
            return False
        except Exception as e:
            logger.error(f"Error sending to client: {e}")
            return False
            
    async def broadcast(self, data: Dict[str, Any], event_type: Optional[str] = None) -> int:
        """
        Broadcast data to all connected clients
        
        Args:
            data: Data to broadcast
            event_type: Optional event type for filtered broadcasting
            
        Returns:
            Number of clients that received the message
        """
        if not self.clients:
            return 0
            
        # Add timestamp if not present
        if 'timestamp' not in data:
            data['timestamp'] = datetime.now().isoformat()
            
        successful_sends = 0
        disconnected_clients = set()
        
        for client in self.clients.copy():
            # Check if client is subscribed to this event type
            if event_type:
                metadata = self.client_metadata.get(client, {})
                subscriptions = metadata.get('subscriptions', [])
                if event_type not in subscriptions and '*' not in subscriptions:
                    continue
                    
            # Send to client
            if await self.send_to_client(client, data):
                successful_sends += 1
            else:
                disconnected_clients.add(client)
                
        # Clean up disconnected clients
        for client in disconnected_clients:
            self.clients.discard(client)
            if client in self.client_metadata:
                del self.client_metadata[client]
                
        self.broadcasts_sent += 1
        logger.debug(f"Broadcast sent to {successful_sends} clients")
        
        return successful_sends
        
    async def broadcast_chat_message(self, username: str, message: str) -> None:
        """
        Broadcast a chat message to all clients
        
        Args:
            username: Username of sender
            message: Chat message
        """
        await self.broadcast({
            'type': 'chat_message',
            'data': {
                'username': username,
                'message': message,
                'timestamp': datetime.now().isoformat()
            }
        }, event_type='chat')
        
    async def broadcast_audio_status(self, status: str, details: Dict[str, Any]) -> None:
        """
        Broadcast audio playback status
        
        Args:
            status: Status (playing, queued, finished)
            details: Additional details
        """
        await self.broadcast({
            'type': 'audio_status',
            'data': {
                'status': status,
                **details
            }
        }, event_type='audio')
        
    async def broadcast_bot_status(self, status: Dict[str, Any]) -> None:
        """
        Broadcast bot status update
        
        Args:
            status: Bot status information
        """
        await self.broadcast({
            'type': 'bot_status',
            'data': status
        }, event_type='status')
        
    def is_connected(self) -> bool:
        """
        Check if server is running (regardless of clients)
        
        Returns:
            True if server is running
        """
        return self.is_running
        
    async def reconnect(self) -> None:
        """
        Attempt to restart the server if it's not running
        """
        if not self.is_running:
            await self.initialize()
            
    def get_stats(self) -> Dict[str, Any]:
        """
        Get WebSocket manager statistics
        
        Returns:
            Statistics dictionary
        """
        client_stats = []
        for client, metadata in self.client_metadata.items():
            client_stats.append({
                'connected_at': metadata['connected_at'].isoformat(),
                'path': metadata['path'],
                'messages_sent': metadata.get('messages_sent', 0),
                'messages_received': metadata.get('messages_received', 0),
                'subscriptions': metadata.get('subscriptions', [])
            })
            
        return {
            'is_running': self.is_running,
            'client_count': len(self.clients),
            'messages_sent': self.messages_sent,
            'messages_received': self.messages_received,
            'broadcasts_sent': self.broadcasts_sent,
            'clients': client_stats[:10]  # Limit to 10 for display
        }
        
    async def shutdown(self) -> None:
        """Shutdown the WebSocket server"""
        logger.info("Shutting down WebSocket server...")
        
        # Notify all clients
        await self.broadcast({
            'type': 'server_shutdown',
            'data': {
                'message': 'Server is shutting down',
                'timestamp': datetime.now().isoformat()
            }
        })
        
        # Close all client connections
        for client in self.clients.copy():
            await client.close()
            
        self.clients.clear()
        self.client_metadata.clear()
        
        # Stop server
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            
        self.is_running = False

        logger.info("WebSocket server shutdown complete")


# Global manager instance
manager = WebSocketManager()
"""
WebSocket handler for real-time communication
"""

import asyncio
import json
import logging
from typing import Dict, Set, Optional, Any
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect
from enum import Enum

logger = logging.getLogger(__name__)


class MessageType(Enum):
    """WebSocket message types"""
    CHAT = "chat"
    STATUS = "status"
    METRICS = "metrics"
    ERROR = "error"
    COMMAND = "command"
    NOTIFICATION = "notification"


class WebSocketHub:
    """
    Central hub for managing WebSocket connections
    """
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.connection_metadata: Dict[WebSocket, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self.message_queue: asyncio.Queue = asyncio.Queue()
        self._broadcast_task: Optional[asyncio.Task] = None
    
    async def connect(self, websocket: WebSocket, client_id: str = None) -> None:
        """
        Accept and register a new WebSocket connection
        
        Args:
            websocket: The WebSocket connection
            client_id: Optional client identifier
        """
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
            self.connection_metadata[websocket] = {
                'client_id': client_id,
                'connected_at': datetime.now(),
                'last_ping': datetime.now()
            }
        
        # Start broadcast task if not running
        if not self._broadcast_task or self._broadcast_task.done():
            self._broadcast_task = asyncio.create_task(self._broadcast_worker())
        
        logger.info(f"WebSocket connected: {client_id or 'anonymous'}")
        
        # Send initial status
        await self.send_personal_message(
            websocket,
            MessageType.STATUS,
            {"status": "connected", "timestamp": datetime.now().isoformat()}
        )
    
    async def disconnect(self, websocket: WebSocket) -> None:
        """
        Remove a WebSocket connection
        
        Args:
            websocket: The WebSocket connection to remove
        """
        async with self._lock:
            self.active_connections.discard(websocket)
            metadata = self.connection_metadata.pop(websocket, {})
        
        client_id = metadata.get('client_id', 'anonymous')
        logger.info(f"WebSocket disconnected: {client_id}")
    
    async def send_personal_message(
        self,
        websocket: WebSocket,
        message_type: MessageType,
        data: Any
    ) -> None:
        """
        Send a message to a specific WebSocket connection
        
        Args:
            websocket: Target WebSocket connection
            message_type: Type of message
            data: Message payload
        """
        try:
            message = {
                'type': message_type.value,
                'data': data,
                'timestamp': datetime.now().isoformat()
            }
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Error sending personal message: {e}")
            await self.disconnect(websocket)
    
    async def broadcast(self, message_type: MessageType, data: Any) -> None:
        """
        Queue a message for broadcast to all connected clients
        
        Args:
            message_type: Type of message
            data: Message payload
        """
        message = {
            'type': message_type.value,
            'data': data,
            'timestamp': datetime.now().isoformat()
        }
        await self.message_queue.put(message)
    
    async def _broadcast_worker(self) -> None:
        """Background task to handle message broadcasting"""
        while True:
            try:
                # Get message from queue
                message = await self.message_queue.get()
                
                # Send to all connected clients
                disconnected = []
                for websocket in list(self.active_connections):
                    try:
                        await websocket.send_json(message)
                    except Exception as e:
                        logger.debug(f"Failed to send to websocket: {e}")
                        disconnected.append(websocket)
                
                # Clean up disconnected clients
                for websocket in disconnected:
                    await self.disconnect(websocket)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in broadcast worker: {e}")
                await asyncio.sleep(1)
    
    async def handle_message(
        self,
        websocket: WebSocket,
        message: Dict[str, Any]
    ) -> None:
        """
        Handle incoming WebSocket message
        
        Args:
            websocket: Source WebSocket connection
            message: Received message
        """
        try:
            msg_type = message.get('type')
            data = message.get('data', {})
            
            if msg_type == 'ping':
                # Update last ping time
                if websocket in self.connection_metadata:
                    self.connection_metadata[websocket]['last_ping'] = datetime.now()
                
                # Send pong response
                await self.send_personal_message(
                    websocket,
                    MessageType.STATUS,
                    {"type": "pong"}
                )
            
            elif msg_type == 'command':
                # Handle command
                command = data.get('command')
                logger.info(f"Received command: {command}")
                # Command handling would be implemented here
                
            else:
                logger.warning(f"Unknown message type: {msg_type}")
                
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            await self.send_personal_message(
                websocket,
                MessageType.ERROR,
                {"error": str(e)}
            )
    
    async def get_connection_count(self) -> int:
        """Get the number of active connections"""
        return len(self.active_connections)
    
    async def cleanup_stale_connections(self, timeout_seconds: int = 60) -> None:
        """
        Remove connections that haven't sent a ping recently
        
        Args:
            timeout_seconds: Ping timeout in seconds
        """
        now = datetime.now()
        stale_connections = []
        
        async with self._lock:
            for websocket, metadata in self.connection_metadata.items():
                last_ping = metadata.get('last_ping')
                if last_ping:
                    elapsed = (now - last_ping).total_seconds()
                    if elapsed > timeout_seconds:
                        stale_connections.append(websocket)
        
        # Disconnect stale connections
        for websocket in stale_connections:
            logger.info("Removing stale WebSocket connection")
            await self.disconnect(websocket)
            try:
                await websocket.close()
            except:
                pass


# Singleton instance
_websocket_hub: Optional[WebSocketHub] = None


def get_websocket_hub() -> WebSocketHub:
    """Get the singleton WebSocket hub instance"""
    global _websocket_hub
    if _websocket_hub is None:
        _websocket_hub = WebSocketHub()
    return _websocket_hub
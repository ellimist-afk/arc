"""
In-memory chat buffer for conversational context.

WHY THIS EXISTS:
Real-time chat is a stream, not a database query. When someone says
"what about that thing you just mentioned", the bot needs instant
access to the last few turns — not a 60-second cached DB query.

The database stores long-term history. This buffer stores the active
conversation for each channel (last ~50 turns). When building context
for a response, we pull from HERE, not from a slow/stale DB fetch.
"""

from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class ChatTurn:
    """A single turn in the conversation."""
    username: str
    message: str
    role: str  # 'viewer' or 'assistant'
    timestamp: float


class ChannelChatBuffer:
    """In-memory ring buffer for recent chat messages per channel."""

    def __init__(self, max_turns_per_channel: int = 50):
        self.max_turns = max_turns_per_channel
        self.buffers: Dict[str, deque] = {}

    def _normalize_channel(self, channel: str) -> str:
        """Normalize channel name: lowercase, strip leading #."""
        return channel.lower().lstrip('#')

    def append_viewer(self, channel: str, username: str, message: str) -> None:
        """Add a viewer's message to the buffer."""
        if not message or not message.strip():
            return

        channel = self._normalize_channel(channel)
        if channel not in self.buffers:
            self.buffers[channel] = deque(maxlen=self.max_turns)

        turn = ChatTurn(
            username=username,
            message=message,
            role='viewer',
            timestamp=datetime.now().timestamp()
        )
        self.buffers[channel].append(turn)

    def append_assistant(self, channel: str, username: str, message: str) -> None:
        """Add the bot's own response to the buffer."""
        if not message or not message.strip():
            return

        channel = self._normalize_channel(channel)
        if channel not in self.buffers:
            self.buffers[channel] = deque(maxlen=self.max_turns)

        turn = ChatTurn(
            username=username,
            message=message,
            role='assistant',
            timestamp=datetime.now().timestamp()
        )
        self.buffers[channel].append(turn)

    def get_recent(self, channel: str, limit: int = 10) -> List[Dict]:
        """Get recent messages for a channel, oldest-first."""
        channel = self._normalize_channel(channel)

        if channel not in self.buffers:
            return []

        # Get last N turns, convert to dicts, return oldest-first
        turns = list(self.buffers[channel])[-limit:]
        return [asdict(turn) for turn in turns]

    def clear(self, channel: Optional[str] = None) -> None:
        """Clear buffer for a channel, or all channels if None."""
        if channel:
            channel = self._normalize_channel(channel)
            if channel in self.buffers:
                self.buffers[channel].clear()
        else:
            self.buffers.clear()

    def stats(self) -> Dict:
        """Get buffer statistics."""
        return {
            'channels': list(self.buffers.keys()),
            'total_channels': len(self.buffers),
            'turns_per_channel': {
                ch: len(buf) for ch, buf in self.buffers.items()
            }
        }

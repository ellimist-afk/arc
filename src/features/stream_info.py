"""
What the stream is right now: category, title, live/offline.

WHY THIS EXISTS:
The system prompt tells the model to "use specifics: the game being played"
but nothing ever told it the game. `bot.current_game` was read in one place
and written nowhere. This module owns that fact: seeded from Helix at boot,
kept fresh by EventSub `channel.update` / `stream.online` / `stream.offline`.

It's a feature module, not a manager: one class, no I/O of its own beyond
the Helix helper, and an optional `on_change` callback so the bot can react
(note it in the session summary, update the raider welcome, bust caches)
without this module knowing any of those exist.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from twitch import helix

logger = logging.getLogger(__name__)

# on_change(old_game, new_game, title) — called only when the game actually changes
ChangeCallback = Callable[[Optional[str], str, str], None]


class StreamInfo:
    def __init__(
        self,
        client_id: str,
        token_getter: Callable[[], Optional[str]],
        channel_name: str,
        broadcaster_id: Optional[str] = None,
        on_change: Optional[ChangeCallback] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client_id = client_id
        self._token = token_getter
        self.channel_name = (channel_name or "").lower().lstrip("#")
        self.broadcaster_id = broadcaster_id
        self.on_change = on_change
        self._clock = clock

        self.game: Optional[str] = None
        self.title: Optional[str] = None
        self.is_live: Optional[bool] = None  # unknown until an event or refresh says so
        self.updated_at: float = 0.0
        self.source: Optional[str] = None

    # ----------------------------------------------------------------- reads

    def snapshot(self) -> Dict[str, Any]:
        return {
            "game": self.game,
            "title": self.title,
            "is_live": self.is_live,
            "updated_at": self.updated_at,
            "source": self.source,
        }

    def describe(self) -> str:
        """One line for the prompt, or "" if we know nothing yet."""
        parts = []
        if self.game:
            parts.append(f"playing {self.game}")
        if self.title:
            parts.append(f'stream title "{self.title}"')
        if self.is_live is False:
            parts.append("stream is currently offline")
        return ", ".join(parts)

    # ---------------------------------------------------------------- writes

    def _apply(self, game: Optional[str], title: Optional[str], source: str) -> None:
        old_game = self.game
        game = (game or "").strip() or None
        title = (title or "").strip() or None
        self.game = game
        self.title = title
        self.updated_at = self._clock()
        self.source = source
        if game != old_game:
            logger.info("Stream category: %s -> %s (%s)", old_game or "?", game or "?", source)
            if self.on_change and game:
                try:
                    self.on_change(old_game, game, title or "")
                except Exception as e:  # noqa: BLE001 — callback must not break updates
                    logger.debug("StreamInfo on_change failed: %s", e)

    async def refresh(self) -> bool:
        """Seed/refresh from Helix. Resolves the broadcaster id on first use."""
        token = self._token() or ""
        if not self.broadcaster_id:
            self.broadcaster_id = await helix.get_user_id(self.client_id, token, self.channel_name)
            if not self.broadcaster_id:
                logger.warning("StreamInfo: could not resolve broadcaster id for %s", self.channel_name)
                return False
        info = await helix.get_channel_info(self.client_id, token, self.broadcaster_id)
        if not info:
            logger.warning("StreamInfo: Helix returned no channel info for %s (id %s); "
                           "the model won't know the category until EventSub channel.update fires",
                           self.channel_name, self.broadcaster_id)
            return False
        self._apply(info.get("game"), info.get("title"), "helix")
        logger.info("StreamInfo seeded from Helix: %s", self.describe() or "no category/title set")
        return True

    # EventSub handlers — signatures match how EventSubWebSocket dispatches:
    # `await handler(event)` with the notification's `payload.event` dict.

    async def handle_channel_update(self, event: Dict[str, Any]) -> None:
        # channel.update v2: category_name, title, broadcaster_user_login, ...
        self._apply(event.get("category_name"), event.get("title"), "eventsub")

    async def handle_stream_online(self, event: Dict[str, Any]) -> None:
        self.is_live = True
        self.updated_at = self._clock()
        logger.info("Stream online (%s)", event.get("started_at", "?"))

    async def handle_stream_offline(self, event: Dict[str, Any]) -> None:
        self.is_live = False
        self.updated_at = self._clock()
        logger.info("Stream offline")

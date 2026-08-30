"""!persona - switch the co-host's personality from chat.

Twelve presets ship in all_personalities.json and the only way to pick one
was to hand-edit bot_settings.json and wait for the file watcher. This puts
it in chat, where the moment a bit is happening is the moment you want the
voice to change.

  !persona            -> what it is now, and what else there is
  !persona uwu        -> switch (mods and broadcaster only)

Switching is mod-gated on purpose: it changes how the co-host talks to
everyone, so it is not a viewer toy. The change is written back to
bot_settings.json so it survives a restart, which is also how the running
bot picks it up -- the settings watcher reloads and applies the preset.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SETTINGS_PATH = "bot_settings.json"
PERSONALITIES_PATH = "all_personalities.json"


class PersonaCommand:
    """Handles !persona in chat."""

    def __init__(
        self,
        personality_engine: Any = None,
        twitch_client: Any = None,
        settings_path: str = SETTINGS_PATH,
        personalities_path: str = PERSONALITIES_PATH,
        cooldown_s: float = 30.0,
        enabled: bool = True,
    ):
        self.personality_engine = personality_engine
        self.twitch_client = twitch_client
        self.settings_path = settings_path
        self.personalities_path = personalities_path
        self.cooldown_s = cooldown_s
        self.enabled = enabled
        self._last_switch_at: float = 0.0
        self.switches = 0

    # ------------------------------------------------------------- reading

    def available(self) -> List[str]:
        """Preset names, alphabetical. Empty if the file is unreadable."""
        try:
            with open(self.personalities_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return sorted(k for k in data if isinstance(data[k], dict))
        except Exception as e:  # noqa: BLE001 - a listing must never crash chat
            logger.warning(f"Could not read personalities: {e}")
            return []

    def current(self) -> Optional[str]:
        engine = self.personality_engine
        if engine is None:
            return None
        return getattr(engine, "current_personality_name", None)

    # ------------------------------------------------------------ parsing

    @staticmethod
    def parse(text: str) -> Tuple[bool, Optional[str]]:
        """(is_persona_command, requested_name).

        Matches the whole word only, so "!personality" and "!personas" are
        not this command.
        """
        stripped = (text or "").strip()
        if not stripped:
            return False, None
        parts = stripped.split()
        if parts[0].lower() != "!persona":
            return False, None
        if len(parts) == 1:
            return True, None
        return True, parts[1].lower().strip()

    # ------------------------------------------------------------ writing

    def _persist(self, name: str) -> bool:
        """Record the preset so it survives a restart.

        This is also how the running bot applies it: the health monitor
        watches this file and calls switch_personality_by_name on change.
        """
        try:
            path = Path(self.settings_path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("personality", {})["preset"] = name
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not persist persona {name!r}: {e}")
            return False

    # ------------------------------------------------------------ handling

    async def handle(self, message: Dict[str, Any]) -> Optional[str]:
        """Process a chat message. Returns the reply sent, or None."""
        if not self.enabled:
            return None
        is_command, requested = self.parse(message.get("text", ""))
        if not is_command:
            return None

        if requested is None:
            return await self._say(self._describe())

        username = (message.get("username") or "").lower()
        broadcaster = (message.get("channel") or "").lower()
        is_broadcaster = bool(username) and username == broadcaster
        if not (message.get("is_mod", False) or is_broadcaster):
            logger.info(f"!persona from non-mod {username!r} ignored")
            return None

        names = self.available()
        if names and requested not in names:
            return await self._say(
                f"No persona called '{requested}'. Try: {', '.join(names)}")

        held = self.cooldown_s - (time.monotonic() - self._last_switch_at)
        if self._last_switch_at and held > 0:
            return await self._say(f"Just switched -- give it {int(held) + 1}s.")

        engine = self.personality_engine
        if engine is None:
            return None
        try:
            ok = await engine.switch_personality_by_name(requested)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Persona switch to {requested!r} failed: {e}")
            return await self._say(f"Could not switch to {requested}.")
        if not ok:
            return await self._say(f"Could not switch to {requested}.")

        self._last_switch_at = time.monotonic()
        self.switches += 1
        persisted = self._persist(requested)
        logger.info(f"Persona switched to {requested} by {username}"
                    f"{'' if persisted else ' (not persisted)'}")
        return await self._say(f"Now running {requested}.")

    def _describe(self) -> str:
        names = self.available()
        now = self.current()
        if not names:
            return f"Current persona: {now or 'unknown'}."
        listed = ", ".join(names)
        return f"Persona: {now or 'unknown'}. Available: {listed}"

    async def _say(self, text: str) -> Optional[str]:
        if not self.twitch_client:
            return text
        try:
            await self.twitch_client.send_message(text)
        except Exception as e:  # noqa: BLE001 - chat send is best effort
            logger.warning(f"Could not send persona reply: {e}")
            return None
        return text

    def stats(self) -> Dict[str, Any]:
        return {"switches": self.switches, "current": self.current()}

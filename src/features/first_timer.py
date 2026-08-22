"""
First-time chatter greeting policy.

WHY THIS EXISTS:
"The bot remembered me" is the single most-praised thing in competitor
reviews, and the cheapest version of it is noticing someone's *first*
message. The context builder can already tell (it sees the viewer's
interaction history); this module decides whether acting on it is a good
idea right now.

It is deliberately conservative. A false "welcome, first time here!" to a
regular is worse than silence, so:
- the context builder only flags a first message when it has positive
  evidence from persistent memory (not the in-memory fallback that resets
  on restart);
- greetings are rate-limited, because a raid turns 40 strangers into 40
  "first-timers" in ten seconds and the raider welcome already covers that;
- raids suppress greetings for a window afterwards for the same reason.

Pure policy, injected clock, no I/O except the optional settings read.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


@dataclass
class FirstTimerGreeter:
    enabled: bool = True
    min_interval_s: float = 45.0
    suppress_after_raid_s: float = 120.0
    clock: Callable[[], float] = field(default=time.time, repr=False)

    _last_greet_at: float = field(default=0.0, init=False, repr=False)
    _last_raid_at: float = field(default=0.0, init=False, repr=False)
    greeted: int = field(default=0, init=False)
    suppressed: int = field(default=0, init=False)

    @classmethod
    def from_settings(cls, path: str = "bot_settings.json", **overrides: Any) -> "FirstTimerGreeter":
        cfg: Dict[str, Any] = {}
        try:
            with open(path, "r") as f:
                cfg = json.load(f).get("first_timer_greeting") or {}
        except Exception as e:  # noqa: BLE001 — settings are optional
            logger.debug("No first_timer_greeting settings: %s", e)
        kwargs = {
            "enabled": bool(cfg.get("enabled", True)),
            "min_interval_s": float(cfg.get("min_interval_s", 45.0)),
            "suppress_after_raid_s": float(cfg.get("suppress_after_raid_s", 120.0)),
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    def note_raid(self) -> None:
        self._last_raid_at = self.clock()

    def should_greet(self, context: Dict[str, Any]) -> bool:
        """True if this turn should be upgraded to a must-reply welcome."""
        if not self.enabled or not context.get("is_first_message"):
            return False
        now = self.clock()
        if self._last_raid_at and (now - self._last_raid_at) < self.suppress_after_raid_s:
            self.suppressed += 1
            return False
        if self._last_greet_at and (now - self._last_greet_at) < self.min_interval_s:
            self.suppressed += 1
            return False
        return True

    def mark_greeted(self) -> None:
        self._last_greet_at = self.clock()
        self.greeted += 1

    def stats(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "greeted": self.greeted, "suppressed": self.suppressed}

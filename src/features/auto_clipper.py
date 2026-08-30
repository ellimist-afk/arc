"""
Auto-clip policy: when chat explodes, save the moment.

WHY THIS EXISTS:
ChatVelocity.is_burst() already knows when the message rate spikes far above
the channel's own baseline — which on a stream means *something just
happened*. This module decides whether that signal should become a clip:
at most one per cooldown (a burst outlives a single message; without the
cooldown one hype moment would clip itself a dozen times), and it counts
what it suppressed so the recap can show the shape of the night.

Pure policy: injected clock, no I/O. The bot owns the actual Helix call.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


@dataclass
class AutoClipper:
    enabled: bool = True
    cooldown_s: float = 180.0
    # Whether a vision-flagged moment (a death, a win) may also clip. Bursts
    # clip what the room noticed; this clips what the screen did.
    clip_notable_moments: bool = True
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    _last_clip_at: float = field(default=0.0, init=False, repr=False)
    clips_triggered: int = field(default=0, init=False)
    bursts_suppressed: int = field(default=0, init=False)
    # Counted apart from bursts so the recap can say which source was held.
    moment_clips: int = field(default=0, init=False)
    moments_suppressed: int = field(default=0, init=False)

    @classmethod
    def from_settings(cls, path: str = "bot_settings.json", **overrides: Any) -> "AutoClipper":
        cfg: Dict[str, Any] = {}
        try:
            with open(path, "r") as f:
                cfg = json.load(f).get("auto_clip") or {}
        except Exception as e:  # noqa: BLE001 — settings are optional
            logger.debug("No auto_clip settings: %s", e)
        kwargs = {
            "enabled": bool(cfg.get("enabled", True)),
            "cooldown_s": float(cfg.get("cooldown_s", 180.0)),
            "clip_notable_moments": bool(cfg.get("clip_notable_moments", True)),
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    def should_clip(self, is_burst: bool) -> bool:
        """True when a chat burst should become a clip right now."""
        if not self.enabled or not is_burst:
            return False
        if self._cooling_down():
            self.bursts_suppressed += 1
            return False
        return True

    def should_clip_moment(self) -> bool:
        """True when a vision-flagged moment should become a clip.

        Shares the one cooldown with bursts -- a death during a hype burst
        must not clip twice -- but counts its own suppressions, so the recap
        does not report a held moment as a held "burst signal".
        """
        if not self.enabled or not self.clip_notable_moments:
            return False
        if self._cooling_down():
            self.moments_suppressed += 1
            return False
        return True

    def _cooling_down(self) -> bool:
        if not self._last_clip_at:
            return False
        return (self.clock() - self._last_clip_at) < self.cooldown_s

    def mark_triggered(self, source: str = "burst") -> None:
        """Start the cooldown. Called on the ATTEMPT, not on success — a
        failing clip (offline, missing scope) must not retry every message
        of the same burst."""
        self._last_clip_at = self.clock()
        self.clips_triggered += 1
        if source == "moment":
            self.moment_clips += 1

    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "clips_triggered": self.clips_triggered,
            "moment_clips": self.moment_clips,
            "bursts_suppressed": self.bursts_suppressed,
            "moments_suppressed": self.moments_suppressed,
        }

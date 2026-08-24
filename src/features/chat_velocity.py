"""
Chat velocity: how fast is chat moving, and how should the co-host pace itself?

WHY THIS EXISTS:
A flat unprompted-reply probability (chattiness/2000 per message) has the
dynamics exactly backwards: the busier chat is, the more messages there are
to roll on, so the bot talks MOST when it's least needed and sits silent in
the lulls where a co-host earns its keep. This module turns the current
message rate into a pacing multiplier: quiet chat boosts the roll, busy
chat damps it, and a slow-moving baseline adapts "quiet/busy" to the size
of the channel instead of hardcoding one streamer's normal.

Pure: injected clock, no I/O, no timers. The bot feeds note_message() and
the personality engine multiplies its dice roll by multiplier(). The same
rate data is what a future auto-clip trigger would read (is_burst()).
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

logger = logging.getLogger(__name__)


class ChatVelocity:
    """
    quiet_per_min / busy_per_min: absolute floors for the two regimes; the
        effective thresholds also scale with the channel's own baseline so a
        big channel's "quiet" isn't a small channel's "busy".
    quiet_boost / busy_damp: multiplier at (or beyond) the two extremes;
        between them the multiplier is log-interpolated through 1.0.
    baseline_alpha: per-minute EMA smoothing for the long-run rate.
    """

    def __init__(
        self,
        *,
        window_s: float = 60.0,
        quiet_per_min: float = 2.0,
        busy_per_min: float = 12.0,
        quiet_boost: float = 2.5,
        busy_damp: float = 0.35,
        baseline_alpha: float = 0.1,
        burst_ratio: float = 3.0,
        burst_min_messages: int = 8,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        assert quiet_per_min < busy_per_min
        self.window_s = window_s
        self.quiet_per_min = quiet_per_min
        self.busy_per_min = busy_per_min
        self.quiet_boost = quiet_boost
        self.busy_damp = busy_damp
        self.baseline_alpha = baseline_alpha
        self.burst_ratio = burst_ratio
        self.burst_min_messages = burst_min_messages
        self._clock = clock

        self._times: Deque[float] = deque()
        self._baseline: Optional[float] = None   # msgs/min EMA
        self._baseline_at: Optional[float] = None
        self._regime = "normal"
        self.messages_seen = 0
        self.peak_per_minute = 0.0

    # --------------------------------------------------------------- inputs

    def note_message(self) -> None:
        now = self._clock()
        self._times.append(now)
        self.messages_seen += 1
        self._trim(now)
        rate = self.per_minute(now)
        self.peak_per_minute = max(self.peak_per_minute, rate)
        self._update_baseline(now, rate)
        regime = self.regime(now)
        if regime != self._regime:
            logger.info("Chat pace: %s -> %s (%.1f msg/min, baseline %.1f)",
                        self._regime, regime, rate, self._baseline or 0.0)
            self._regime = regime

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._times and self._times[0] < cutoff:
            self._times.popleft()

    def _update_baseline(self, now: float, rate: float) -> None:
        if self._baseline is None:
            self._baseline, self._baseline_at = rate, now
            return
        # Time-scaled EMA so the smoothing is per-minute, not per-message —
        # a burst of 50 messages must not drag the baseline up instantly
        dt_min = max(0.0, (now - (self._baseline_at or now)) / 60.0)
        weight = 1.0 - (1.0 - self.baseline_alpha) ** dt_min
        self._baseline += weight * (rate - self._baseline)
        self._baseline_at = now

    # -------------------------------------------------------------- outputs

    def per_minute(self, now: Optional[float] = None) -> float:
        now = self._clock() if now is None else now
        self._trim(now)
        return len(self._times) * 60.0 / self.window_s

    def baseline(self) -> float:
        return self._baseline if self._baseline is not None else 0.0

    def _thresholds(self) -> tuple:
        base = self.baseline()
        quiet = max(self.quiet_per_min, base * 0.5)
        busy = max(self.busy_per_min, base * 2.0)
        if quiet >= busy:                       # degenerate config/baseline
            quiet = busy / 2.0
        return quiet, busy

    def regime(self, now: Optional[float] = None) -> str:
        rate = self.per_minute(now)
        quiet, busy = self._thresholds()
        if rate <= quiet:
            return "quiet"
        if rate >= busy:
            return "busy"
        return "normal"

    def multiplier(self, now: Optional[float] = None) -> float:
        """Pacing factor for the unprompted-reply roll.

        <= quiet threshold -> quiet_boost; >= busy threshold -> busy_damp;
        log-interpolated between them (passing 1.0 at the geometric middle),
        so each doubling of chat speed costs about the same amount of bot."""
        rate = self.per_minute(now)
        quiet, busy = self._thresholds()
        if rate <= quiet:
            return self.quiet_boost
        if rate >= busy:
            return self.busy_damp
        span = math.log(busy / quiet)
        pos = math.log(rate / quiet) / span     # 0.0 at quiet .. 1.0 at busy
        return self.quiet_boost * (self.busy_damp / self.quiet_boost) ** pos

    def is_burst(self, now: Optional[float] = None) -> bool:
        """A sudden spike over the channel's own baseline (auto-clip signal)."""
        rate = self.per_minute(now)
        if len(self._times) < self.burst_min_messages:
            return False
        base = max(self.baseline(), self.quiet_per_min)
        return rate >= base * self.burst_ratio

    def stats(self) -> Dict[str, float]:
        return {
            "per_minute": round(self.per_minute(), 2),
            "baseline": round(self.baseline(), 2),
            "peak_per_minute": round(self.peak_per_minute, 2),
            "messages_seen": self.messages_seen,
            "regime": self._regime,
        }

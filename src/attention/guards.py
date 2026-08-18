"""Stimulus hygiene for the AttentionRouter: dedupe, staleness, self, ignore
list (rules R0.* and R7). Pure; owns only its dedupe memory."""
import collections
from typing import Deque, Optional, Set, Tuple

from attention.config import AttentionConfig
from attention.stimulus import Stimulus


class StimulusHygiene:
    def __init__(self, cfg: AttentionConfig):
        self.cfg = cfg
        self._seen: Set[str] = set()
        self._order: Deque[str] = collections.deque()

    def check(self, stim: Stimulus, now: float) -> Optional[Tuple[str, str]]:
        """Return (rule, reason) if the stimulus must be dropped, else None."""
        if stim.id in self._seen:
            return ("R0.duplicate", "duplicate stimulus id")
        self._seen.add(stim.id)
        self._order.append(stim.id)
        while len(self._order) > self.cfg.dedupe_window:
            self._seen.discard(self._order.popleft())
        if now - stim.ts > self.cfg.stale_after_s:
            return ("R0.stale",
                    f"stimulus is {now - stim.ts:.1f}s old "
                    f"(> {self.cfg.stale_after_s:.0f}s)")
        actor = stim.actor.username.lower()
        if actor and actor == self.cfg.bot_username:
            return ("R0.self", "own message; never self-respond")
        if actor and actor in self.cfg.ignored_users:
            return ("R7.ignored_user", f"'{actor}' is on the ignore list")
        return None

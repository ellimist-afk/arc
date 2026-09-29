"""Topic wells: code decides WHAT an unprompted line is about.

Live 2026-09-28 every one of the last 14 dead-air fillers was about the
game on screen. Asking the model to vary did not hold; the variety has to
be decided before the model writes, and the material for the other topics
has to be the ONLY material in its prompt.

Pure: no I/O, no clock, no logging. The bot builds the material for each
well, asks `pick()` for one, and calls `note()` when a line goes out.
Screen reactions are always noted as 'game'.

Game share, defined strictly: the game is eligible only when one more game
line would keep the game count in the last `window` lines at or under
floor(game_share_max * window). With the shipped 0.34 and 6 that cap is 2,
so 1 game in the last 6 allows another and 2 do not ("at most one line in
three"). Integer arithmetic, so 1/3 and 0.34 give the same cap of 2.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

WELLS = ('game', 'streamer_said', 'earlier_tonight', 'ask_chat',
         'viewer_callback', 'off_topic')

DEFAULT_OFF_TOPIC = ["ai tools", "homelab and pc hardware", "food",
                     "sleep schedule", "shows and movies", "stream setup"]


@dataclass
class WellChoice:
    well: str
    material: Dict[str, Any] = field(default_factory=dict)
    reason: str = ''


class TopicWells:
    """Ledger of the wells the last unprompted lines drew from, and the
    picker that reads it."""

    def __init__(self, game_share_max: float = 1 / 3, window: int = 6,
                 solo_min_gap_s: float = 600.0):
        try:
            share = float(game_share_max)
        except (TypeError, ValueError):
            share = 1 / 3
        try:
            size = int(window)
        except (TypeError, ValueError):
            size = 6
        try:
            gap = float(solo_min_gap_s)
        except (TypeError, ValueError):
            gap = 600.0
        self.game_share_max = min(1.0, max(0.0, share))
        self.window = max(2, size)
        self.solo_min_gap_s = max(0.0, gap)
        self._history: Deque[str] = deque(maxlen=self.window)
        self._seq = 0
        self._last_used: Dict[str, int] = {}
        self._counts: Dict[str, int] = {}
        self._noted_at: Dict[str, float] = {}
        self.last_off_topic: Optional[str] = None

    # ------------------------------------------------------------ ledger
    def note(self, well: str, at: Optional[float] = None) -> None:
        """A line drawn from this well went out.

        `at`, when given by the caller, is recorded as the time this well
        last fired -- the only clock reference this module keeps, and only
        because the caller handed it one."""
        if well not in WELLS:
            return
        self._seq += 1
        self._history.append(well)
        self._last_used[well] = self._seq
        self._counts[well] = self._counts.get(well, 0) + 1
        if at is not None:
            self._noted_at[well] = at

    def history(self) -> List[str]:
        """The last `window` wells, oldest first."""
        return list(self._history)

    def stats(self) -> Dict[str, int]:
        """Lines per well since start (every well present, zero included)."""
        return {w: self._counts.get(w, 0) for w in WELLS}

    def game_cap(self) -> int:
        """Most game lines allowed in any `window` lines.

        Floored at 1 whenever `game_share_max` is above 0: a share that
        rounds down to 0 (e.g. 0.34 of a window of 1 or 2) must not disable
        game silently. A share of exactly 0 still means "never game"."""
        cap = int(self.game_share_max * self.window + 1e-9)
        if cap < 1 and self.game_share_max > 0:
            return 1
        return cap

    def game_allowed(self) -> bool:
        """Game rules: not used in the last 2, and one more would stay at or
        under the cap."""
        hist = list(self._history)
        if 'game' in hist[-2:]:
            return False
        return hist.count('game') + 1 <= self.game_cap()

    # ------------------------------------------------------------ picker
    def eligible(self, available: Dict[str, Dict[str, Any]]) -> List[str]:
        """Wells that have material and pass the rules, in WELLS order."""
        last = self._history[-1] if self._history else None
        out = []
        for well in WELLS:
            if well not in (available or {}):
                continue
            if well == last:
                continue
            if well == 'game' and not self.game_allowed():
                continue
            out.append(well)
        return out

    def pick(self, available: Dict[str, Dict[str, Any]],
             rng, now: Optional[float] = None) -> Optional[WellChoice]:
        """Choose the well for the next filler, or None to stay quiet.

        Rules in order: never the well used last; game only under the share
        cap and not in the last 2; among the rest the least recently used
        (never used counts as oldest); ties broken by rng.

        Both rules above (never-twice-in-a-row, and the game share cap) bind
        only when there is an alternative well with material. If `available`
        holds exactly one well, that well is eligible regardless of history
        -- except it must not fire back to back faster than
        `solo_min_gap_s`: with `now` given and that well noted less than
        `solo_min_gap_s` ago, this stays quiet instead.
        """
        available = available or {}
        if len(available) == 1:
            (well,) = available.keys()
            last_at = self._noted_at.get(well)
            if now is not None and last_at is not None and \
                    (now - last_at) < self.solo_min_gap_s:
                return None
            return WellChoice(well=well, material=dict(available[well] or {}),
                              reason='only well with material')
        pool = self.eligible(available)
        if not pool:
            return None
        oldest = min(self._last_used.get(w, 0) for w in pool)
        tied = [w for w in pool if self._last_used.get(w, 0) == oldest]
        well = tied[0] if len(tied) == 1 else rng.choice(tied)
        hist = list(self._history)
        reason = (f"{len(pool)} eligible, last "
                  f"{hist[-1] if hist else 'none'}, game "
                  f"{hist.count('game')}/{self.window}")
        return WellChoice(well=well, material=dict(available[well] or {}),
                          reason=reason)

    def pick_off_topic(self, entries: List[str], rng) -> Optional[str]:
        """One entry from the off-topic list, never the same twice running."""
        pool = [e for e in entries if e != self.last_off_topic] or list(entries)
        if not pool:
            return None
        topic = rng.choice(pool)
        self.last_off_topic = topic
        return topic


def off_topic_list(value: Any) -> List[str]:
    """Tolerant read of topic_wells.off_topic.

    Missing -> the shipped default; a hand-typed string -> one entry; a list
    -> its non-empty entries as text; anything else -> the default. Never
    raises.
    """
    if value is None:
        return list(DEFAULT_OFF_TOPIC)
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if item is None or isinstance(item, (dict, list, tuple)):
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return list(DEFAULT_OFF_TOPIC)


def directive(well: str, material: Dict[str, Any]) -> str:
    """The one sentence (section 6) that tells the model what this line is
    about, with the well's material in it. '' for game or unknown wells."""
    m = material or {}
    if well == 'streamer_said':
        quotes = " / ".join(f'"{q}"' for q in (m.get('quotes') or [])[-3:])
        return (f"The streamer just said this out loud (it was not said to "
                f"you): {quotes}. React to what he said, in one line, as his "
                f"co-host.")
    if well == 'earlier_tonight':
        return (f"Earlier tonight: {m.get('summary', '')}. Call back to ONE "
                f"specific thing from it. Do not mention the game on screen.")
    if well == 'ask_chat':
        names = ", ".join(m.get('names') or [])
        return (f"Ask chat one real question that has nothing to do with the "
                f"game. People recently here: {names}.")
    if well == 'viewer_callback':
        return (f"{m.get('viewer', '')} is around. You know this about them: "
                f"{m.get('fact', '')}. Say one line to them about it.")
    if well == 'off_topic':
        return (f"Say one line about: {m.get('topic', '')}. An opinion someone "
                f"would argue with. Nothing about the game.")
    return ''

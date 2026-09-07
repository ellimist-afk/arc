"""Did anything the co-host said actually land?

Every prompt-level fix for "it's unfunny" so far has asked the model to
behave. Funniness has had no measurement at all -- the audit counts guards,
not whether a line worked. Chat supplies the measurement for free: people
laugh in text, within a few seconds, and they do it for free.

So every bot line gets a score: how many DISTINCT viewers laughed within the
window after it. The bot's own lines and the streamer never count. Two things
come out of that: the model is shown how its last few lines were taken, and
when the last few UNPROMPTED lines got nothing it stops volunteering until
someone laughs or talks to it.

Pure: no clock, no I/O. Callers pass `at` (wall-clock seconds, matching
ChannelChatBuffer timestamps) so the whole thing is deterministic in tests.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Set

# "dead" only as a whole word ("deadline" is not a laugh); "W" alone is far
# too ambiguous to count. The lookarounds keep these out of the middle of
# ordinary words -- "lollipop" is not a laugh, and that mistake would credit
# lines that nobody reacted to at all.
LAUGH_RE = re.compile(
    r"(?<![a-z])(lo+l+|lmao+|lmfao+|ha(ha)+|he(he)+|kekw|lul+|omegalul"
    r"|pepelaugh|icant|dead)(?![a-z])"
    r"|[\U0001F602\U0001F923\U0001F480]",
    re.I,
)


def is_laugh(text: str) -> bool:
    """Does this message contain a laugh?"""
    return bool(text) and bool(LAUGH_RE.search(text))


@dataclass
class BotLine:
    """One thing the co-host said, and who laughed at it."""
    seq: int
    text: str
    at: float
    unsolicited: bool
    laughers: Set[str] = field(default_factory=set)

    @property
    def laughs(self) -> int:
        return len(self.laughers)


class LaughLedger:
    """Bot lines, in order, with the laughs they earned.

    `window_s`   how long after a line a laugh still counts as being at it.
    `quiet_after` how many dry unprompted lines in a row buys silence.
    `history`    how many lines to keep (a ring; older ones are forgotten).
    """

    def __init__(self, window_s: float = 30.0, quiet_after: int = 3,
                 history: int = 40):
        self.window_s = float(window_s)
        self.quiet_after = int(quiet_after)
        self.history = int(history)
        self._lines: Deque[BotLine] = deque(maxlen=self.history)
        self._by_seq: Dict[int, BotLine] = {}
        # When somebody last addressed the co-host. A dry streak never
        # reaches back past this: being talked to is the room's attention,
        # which is exactly what the quiet rule is waiting for.
        self._engaged_at: float = 0.0

    # ------------------------------------------------------------ writing

    def note_bot_line(self, seq: int, text: str, at: float,
                      unsolicited: bool = False) -> None:
        """Record a line the co-host just said."""
        if not seq or not text:
            return
        line = BotLine(seq=int(seq), text=str(text), at=float(at),
                       unsolicited=bool(unsolicited))
        if len(self._lines) == self._lines.maxlen and self._lines:
            self._by_seq.pop(self._lines[0].seq, None)
        self._lines.append(line)
        self._by_seq[line.seq] = line

    def note_viewer(self, username: str, text: str, at: float, *,
                    exclude: Optional[Iterable[str]] = None) -> Optional[int]:
        """Credit a laugh to the newest bot line still inside its window.

        Returns the seq credited, or None (not a laugh, nobody said anything
        recently enough, the speaker is excluded, or they already laughed at
        that line).
        """
        name = (username or "").strip().lower().lstrip('@')
        if not name or not is_laugh(text):
            return None
        blocked = {(e or "").strip().lower().lstrip('@') for e in (exclude or ())}
        if name in blocked:
            return None
        at = float(at)
        for line in reversed(self._lines):
            if line.at <= at <= line.at + self.window_s:
                if name in line.laughers:
                    return None
                line.laughers.add(name)
                return line.seq
            if line.at + self.window_s < at:
                # Ordered by time: everything older is further out of window.
                break
        return None

    def mark_engaged(self, at: float) -> None:
        """Somebody just talked TO the co-host. The room is present."""
        self._engaged_at = max(self._engaged_at, float(at))

    # ------------------------------------------------------------ reading

    def line(self, seq: int) -> Optional[BotLine]:
        return self._by_seq.get(int(seq)) if seq else None

    def score(self, seq: int) -> int:
        """How many distinct viewers laughed at this line."""
        line = self.line(seq)
        return line.laughs if line else 0

    def settled(self, line: BotLine, before: float) -> bool:
        """Has this line's window closed by `before`? An unsettled line is
        not evidence either way -- it has not had its chance yet."""
        return line.at + self.window_s <= float(before)

    def recent(self, n: int, before: float) -> List[BotLine]:
        """The last `n` SETTLED lines, newest first."""
        out: List[BotLine] = []
        for line in reversed(self._lines):
            if not self.settled(line, before):
                continue
            out.append(line)
            if len(out) >= n:
                break
        return out

    def dry_streak(self, now: float) -> int:
        """Settled unprompted lines, newest first, that got nothing.

        Stops at the first line that got a laugh, and never reaches back
        past the last time somebody addressed the co-host. Replies are
        skipped, not counted: answering a person is not volunteering.
        """
        n = 0
        for line in reversed(self._lines):
            if not self.settled(line, now):
                continue
            if line.at <= self._engaged_at:
                break
            if line.laughers:
                break
            if not line.unsolicited:
                continue
            n += 1
        return n

    def should_stay_quiet(self, now: float) -> bool:
        """Has the room stopped reacting to the lines nobody asked for?"""
        if self.quiet_after <= 0:
            return False
        return self.dry_streak(now) >= self.quiet_after

    def best(self, n: int) -> List[BotLine]:
        """The lines that landed hardest, most laughs first, newest first
        among ties. Lines nobody laughed at are not 'best' at anything."""
        landed = [line for line in self._lines if line.laughers]
        landed.sort(key=lambda line: (line.laughs, line.at), reverse=True)
        return landed[:n]

    def stats(self) -> Dict:
        """Counts for the recap. The dry streak is measured at the moment
        every recorded line has settled, so this needs no clock."""
        settled_at = (self._lines[-1].at + self.window_s) if self._lines else 0.0
        return {
            'lines': len(self._lines),
            'laughed_lines': sum(1 for line in self._lines if line.laughers),
            'laughs': sum(line.laughs for line in self._lines),
            'dry_streak': self.dry_streak(settled_at),
        }

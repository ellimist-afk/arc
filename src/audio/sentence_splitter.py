"""
Incremental sentence splitter for streamed LLM output.

WHY THIS EXISTS:
To start speaking before the model has finished writing, the token stream
has to be cut into TTS-sized pieces at points that sound natural. Cutting
on every '.' is wrong ("Mr. Beast", "v2.5", "e.g. this"); waiting for the
whole reply is what we're trying to stop doing. This does the middle thing:
emit a sentence as soon as a terminator is followed by whitespace and the
text before it isn't an abbreviation or a decimal, hold sentences that are
too short to be worth a TTS round-trip, and force a cut on long run-ons so
no single chunk delays the next one for too long.

Pure, synchronous, no I/O: feed() deltas in, completed sentences out.
"""

from __future__ import annotations

import re
from typing import List, Optional

# Terminator run, optional closing quotes/brackets, then the whitespace that
# proves the sentence is really over (streams end mid-word otherwise).
_BOUNDARY = re.compile(r"([.!?]+[\"'\)\]]*)(\s+)")

_ABBREVIATIONS = frozenset(
    """
    mr mrs ms dr prof sr jr st vs etc e.g i.e no vol fig approx dept est
    """.split()
)

# Soft cut points for run-on sentences, in preference order.
_SOFT_CUT = re.compile(r"[,;:]\s+|\s+—\s+|\s+-\s+")


class SentenceSplitter:
    """
    min_chars:   sentences shorter than this are held and prepended to the
                 next one ("Oh." + "Chat, no." -> "Oh. Chat, no.")
    max_chars:   a buffer longer than this with no terminator is cut at the
                 last soft punctuation (or whitespace) past `max_chars * 0.6`
    """

    def __init__(self, min_chars: int = 12, max_chars: int = 220) -> None:
        self.min_chars = min_chars
        self.max_chars = max_chars
        self._buf = ""
        self._held = ""  # short sentence waiting for a companion
        self.emitted = 0

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _is_false_boundary(before: str, after: str) -> bool:
        """`before` ends with the terminator run; `after` is what follows the whitespace."""
        core = before.rstrip("\"')]")
        if not core.endswith("."):
            return False  # '!' and '?' are never abbreviations
        word = core[:-1].rsplit(None, 1)[-1] if core[:-1].strip() else ""
        word_l = word.lower().strip("(\"'")
        if not word_l:
            return False
        if word_l in _ABBREVIATIONS or word_l.rstrip(".") in _ABBREVIATIONS:
            return True
        # Initials and single letters: "J. K. Rowling", "plan A. then B."
        if len(word_l) == 1 and word_l.isalpha():
            return True
        # Version / numbered markers followed by a lowercase continuation:
        # "v2. beta", "no. 3. then" — a real sentence end is followed by a capital
        if after[:1].islower() and any(ch.isdigit() for ch in word_l):
            return True
        return False

    def _next_boundary(self, start: int = 0):
        """First real sentence boundary at or after `start`, skipping false ones."""
        while True:
            m = _BOUNDARY.search(self._buf, start)
            if not m:
                return None
            if self._is_false_boundary(self._buf[:m.end(1)], self._buf[m.end(2):]):
                start = m.end(2)
                continue
            return m

    def _release(self, sentence: str) -> Optional[str]:
        """Apply the min-length hold. Returns a sentence to emit, or None."""
        sentence = sentence.strip()
        if not sentence:
            return None
        if self._held:
            sentence = f"{self._held} {sentence}"
            self._held = ""
        if len(sentence) < self.min_chars:
            self._held = sentence
            return None
        self.emitted += 1
        return sentence

    def _force_cut(self) -> Optional[str]:
        """Cut a run-on buffer at the best soft point past 60% of max_chars."""
        if len(self._buf) <= self.max_chars:
            return None
        floor = int(self.max_chars * 0.6)
        best = -1
        for m in _SOFT_CUT.finditer(self._buf):
            if floor <= m.end() <= self.max_chars:
                best = m.end()
        if best < 0:
            ws = self._buf.rfind(" ", floor, self.max_chars)
            best = ws + 1 if ws > 0 else self.max_chars
        head, self._buf = self._buf[:best], self._buf[best:].lstrip()
        return self._release(head)

    # --------------------------------------------------------------- API

    def feed(self, delta: str) -> List[str]:
        """Append streamed text; return any sentences completed by it."""
        if not delta:
            return []
        self._buf += delta
        out: List[str] = []
        while True:
            m = self._next_boundary()
            if m and m.end(1) <= self.max_chars:
                sentence = self._buf[:m.end(1)]
                self._buf = self._buf[m.end(2):]
                released = self._release(sentence)
                if released:
                    out.append(released)
                continue
            # No boundary within reach: if the buffer is already over budget,
            # cut it now rather than letting a run-on delay the next sentence
            if len(self._buf) > self.max_chars:
                cut = self._force_cut()
                if cut:
                    out.append(cut)
                continue
            break
        return out

    def flush(self) -> Optional[str]:
        """End of stream: emit whatever is left (held + buffer), if anything."""
        tail = self._buf.strip()
        self._buf = ""
        if self._held and tail:
            tail = f"{self._held} {tail}"
        elif self._held:
            tail = self._held
        self._held = ""
        if not tail:
            return None
        self.emitted += 1
        return tail

    @property
    def pending(self) -> str:
        """Text seen but not yet emitted (for diagnostics)."""
        return (self._held + " " + self._buf).strip()


def split_text(text: str, **kw) -> List[str]:
    """Non-streaming convenience: split a complete string."""
    s = SentenceSplitter(**kw)
    out = s.feed(text)
    tail = s.flush()
    if tail:
        out.append(tail)
    return out

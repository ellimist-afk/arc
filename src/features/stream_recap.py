"""
Post-stream recap.

WHY THIS EXISTS:
Every hosted competitor sells "post-stream insights" as a tier feature.
Arc already has the expensive part — the rolling session summary — so a
recap is mostly bookkeeping: count who talked, what the bot did, what
happened, and write it next to the summary when the stream ends.

Counters live here (not in bot.py) so the bot's handlers add one line each.
`render()` is pure; `write()` is the only I/O. Output is Markdown in
`session_state/` so it's readable as-is and easy to post somewhere later.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class StreamRecap:
    def __init__(
        self,
        channel: str,
        out_dir: str = "session_state",
        clock: Callable[[], float] = time.time,
        top_n: int = 5,
    ) -> None:
        self.channel = (channel or "").lower().lstrip("#")
        self.out_dir = Path(out_dir)
        self._clock = clock
        self.top_n = top_n
        self.reset()

    # -------------------------------------------------------------- counters

    def reset(self) -> None:
        self.started_at: float = self._clock()
        self.first_message_at: Optional[float] = None
        self.last_message_at: Optional[float] = None
        self.messages = 0
        self.chatters: Counter = Counter()
        self.responses = 0
        self.spoken = 0
        self.events: List[Tuple[float, str]] = []

    def record_message(self, username: str) -> None:
        now = self._clock()
        if self.first_message_at is None:
            self.first_message_at = now
        self.last_message_at = now
        self.messages += 1
        if username:
            self.chatters[username.lower()] += 1

    def record_response(self, spoken: bool = False) -> None:
        self.responses += 1
        if spoken:
            self.spoken += 1

    def record_event(self, text: str) -> None:
        if text and text.strip():
            self.events.append((self._clock(), text.strip()))

    @property
    def has_activity(self) -> bool:
        return self.messages > 0 or bool(self.events)

    # --------------------------------------------------------------- render

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, _ = divmod(rem, 60)
        return f"{h}h {m:02d}m" if h else f"{m}m"

    @staticmethod
    def _fmt_clock(ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%H:%M")

    def render(self, summary: str = "", extra_stats: Optional[Dict[str, Any]] = None) -> str:
        now = self._clock()
        start = self.first_message_at or self.started_at
        end = self.last_message_at or now
        lines = [
            f"# Stream recap — {self.channel} — {datetime.fromtimestamp(start).strftime('%Y-%m-%d')}",
            "",
            f"- Window: {self._fmt_clock(start)}–{self._fmt_clock(end)} ({self._fmt_duration(end - start)})",
            f"- Chat messages: {self.messages} from {len(self.chatters)} chatters",
            f"- Co-host replies: {self.responses} ({self.spoken} spoken)",
        ]
        for key, val in (extra_stats or {}).items():
            lines.append(f"- {key}: {val}")

        if self.chatters:
            lines += ["", "## Top chatters"]
            for name, n in self.chatters.most_common(self.top_n):
                lines.append(f"- {name}: {n}")

        if self.events:
            lines += ["", "## Events"]
            for ts, text in self.events:
                lines.append(f"- {self._fmt_clock(ts)} {text}")

        lines += ["", "## What happened", "", summary.strip() or "_No session summary was produced._", ""]
        return "\n".join(lines)

    def write(self, summary: str = "", extra_stats: Optional[Dict[str, Any]] = None) -> Optional[Path]:
        start = self.first_message_at or self.started_at
        stamp = datetime.fromtimestamp(start).strftime("%Y-%m-%d_%H%M")
        path = self.out_dir / f"recap_{self.channel}_{stamp}.md"
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(self.render(summary, extra_stats), encoding="utf-8")
            logger.info("Stream recap written: %s", path)
            return path
        except OSError as e:
            logger.error("Could not write stream recap: %s", e)
            return None

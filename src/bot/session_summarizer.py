"""
Rolling stream-session summary.

WHY THIS EXISTS:
The LLM only ever sees the last ~15 chat turns. Two hours in, the co-host
has no idea what game this is, that a raid came through at minute 40, that
viewer X has been riffing on the same joke all night, or that it promised
to "never mention pineapple pizza again". The ring buffer forgets; this
does not.

Instead of letting the context window fill and reset, the summarizer folds
every batch of unsummarized turns into one bounded paragraph (prev summary
+ new turns -> new summary). The paragraph is injected into every prompt as
"earlier this stream", so persona and callbacks stay coherent for hours at
a fixed token cost.

Design constraints:
- Never on the hot path. `maybe_schedule()` is O(1); the LLM call runs as a
  registered background task.
- One in-flight summarization per channel; a batch that fails is retried on
  the next trigger (watermark only advances on success).
- Watermark is the chat buffer's per-channel `seq`, so eviction from the
  ring buffer can't cause double-counting or gaps we don't notice.
- Optional on-disk persistence so a mid-stream bot restart doesn't wipe the
  stream's memory. Stale files (older than `max_age_s`) are ignored.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# messages -> completion text. Injected so the summarizer has no opinion
# about which model, client, or resilience wrapper is in use.
LLMCall = Callable[[List[Dict[str, str]]], Awaitable[Optional[str]]]

SUMMARY_SYSTEM_PROMPT = (
    "You maintain the running memory of an AI co-host on a live Twitch stream. "
    "You will get the previous summary (may be empty) and a batch of newer chat. "
    "Write the UPDATED summary that replaces the previous one.\n"
    "Keep, in priority order: what is being played or done right now; running "
    "jokes and callbacks and who started them; notable viewers with one concrete "
    "fact each (what they said, did, gifted, raided with); anything the co-host "
    "itself promised, claimed, or strongly committed to; the current mood of chat.\n"
    "Drop anything stale or trivial. Merge, don't append. Plain prose, third "
    "person, refer to the co-host by name. Maximum {max_words} words. Output "
    "only the summary, no preamble."
)


@dataclass
class _ChannelState:
    summary: str = ""
    watermark: int = 0          # highest chat-buffer seq folded into summary
    started_at: float = 0.0     # when this channel's state was created
    updated_at: float = 0.0     # monotonic-ish wall time of last success
    last_attempt: float = 0.0
    in_flight: bool = False
    pending_events: List[str] = field(default_factory=list)
    updates: int = 0
    failures: int = 0


class StreamSessionSummarizer:
    """Folds chat history into a bounded rolling summary per channel."""

    def __init__(
        self,
        chat_buffer: Any,
        llm_call: LLMCall,
        bot_name: str = "the co-host",
        *,
        turns_per_update: int = 40,
        min_turns: int = 8,
        max_interval_s: float = 600.0,
        max_words: int = 120,
        max_chars: int = 900,
        persist_dir: Optional[str] = None,
        max_age_s: float = 6 * 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.chat_buffer = chat_buffer
        self.llm_call = llm_call
        self.bot_name = bot_name
        self.turns_per_update = turns_per_update
        self.min_turns = min_turns
        self.max_interval_s = max_interval_s
        self.max_words = max_words
        self.max_chars = max_chars
        self.persist_dir = Path(persist_dir) if persist_dir else None
        self.max_age_s = max_age_s
        self._clock = clock
        self._channels: Dict[str, _ChannelState] = {}

    # ------------------------------------------------------------ accessors

    @staticmethod
    def _norm(channel: str) -> str:
        return (channel or "").lower().lstrip("#")

    def _state(self, channel: str) -> _ChannelState:
        key = self._norm(channel)
        if key not in self._channels:
            st = self._load(key) or _ChannelState()
            st.started_at = self._clock()
            self._channels[key] = st
        return self._channels[key]

    def get_summary(self, channel: str) -> str:
        return self._state(channel).summary

    def reset(self, channel: str) -> None:
        """Forget this channel's summary (new stream started). Keeps the
        watermark so turns already folded aren't re-summarized."""
        st = self._state(channel)
        st.summary = ""
        st.pending_events.clear()
        st.updated_at = 0.0
        st.started_at = self._clock()
        path = self._path(self._norm(channel))
        if path and path.exists():
            try:
                path.unlink()
            except OSError as e:
                logger.debug("Could not remove persisted summary: %s", e)
        logger.info("Session summary reset for %s", channel)

    def note_event(self, channel: str, text: str) -> None:
        """Record a non-chat happening (raid, ad break, game change) for the
        next fold. Cheap; safe to call from event handlers."""
        if text and text.strip():
            self._state(channel).pending_events.append(text.strip())

    def stats(self, channel: str) -> Dict[str, Any]:
        st = self._state(channel)
        return {
            "summary_chars": len(st.summary),
            "watermark": st.watermark,
            "unsummarized_turns": self._unsummarized(channel, st),
            "pending_events": len(st.pending_events),
            "in_flight": st.in_flight,
            "updates": st.updates,
            "failures": st.failures,
            "updated_at": st.updated_at,
        }

    # ------------------------------------------------------------ scheduling

    def _unsummarized(self, channel: str, st: _ChannelState) -> int:
        return max(0, self.chat_buffer.last_seq(channel) - st.watermark)

    def should_update(self, channel: str) -> bool:
        st = self._state(channel)
        if st.in_flight:
            return False
        n = self._unsummarized(channel, st)
        if n <= 0 and not st.pending_events:
            return False
        if n >= self.turns_per_update:
            return True
        # Slow chat: fold a smaller batch once enough time has gone by so
        # the summary never lags more than max_interval behind reality.
        anchor = st.last_attempt or st.updated_at or st.started_at
        aged = (self._clock() - anchor) >= self.max_interval_s
        return aged and (n >= self.min_turns or bool(st.pending_events))

    def maybe_schedule(self, channel: str, spawn: Callable[[Awaitable[None], str], Any]) -> bool:
        """O(1) check; on trigger, hands `update(channel)` to `spawn`.

        `spawn(coro, name)` is expected to be the bot's TaskRegistry.create_task
        so the background work is tracked like everything else.
        Returns True if an update was scheduled."""
        if not self.should_update(channel):
            return False
        st = self._state(channel)
        st.in_flight = True
        st.last_attempt = self._clock()
        spawn(self.update(channel), f"session_summary_{self._norm(channel)}")
        return True

    # ------------------------------------------------------------ the fold

    def _render_turns(self, turns: List[Dict[str, Any]]) -> str:
        lines = []
        for t in turns:
            text = (t.get("message") or "").strip()
            if not text:
                continue
            if t.get("role") == "assistant":
                lines.append(f"{self.bot_name} (the co-host): {text}")
            else:
                lines.append(f"{t.get('username', 'someone')}: {text}")
        return "\n".join(lines)

    def build_messages(self, channel: str) -> Optional[List[Dict[str, str]]]:
        """Prompt for one fold, or None if there's nothing to fold."""
        st = self._state(channel)
        turns = self.chat_buffer.get_since(channel, st.watermark)
        rendered = self._render_turns(turns)
        events = "\n".join(f"[event] {e}" for e in st.pending_events)
        if not rendered and not events:
            return None

        user_parts = [
            "PREVIOUS SUMMARY:",
            st.summary or "(none yet - stream just started)",
            "",
            "NEW SINCE THEN:",
        ]
        if events:
            user_parts.append(events)
        if rendered:
            user_parts.append(rendered)

        return [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT.format(max_words=self.max_words)},
            {"role": "user", "content": "\n".join(user_parts)},
        ]

    async def update(self, channel: str) -> bool:
        """Run one fold. Returns True if the summary changed."""
        st = self._state(channel)
        st.in_flight = True
        try:
            # Snapshot the watermark *before* the call so turns arriving
            # during the LLM round-trip are picked up next time.
            target = self.chat_buffer.last_seq(channel)
            events_taken = len(st.pending_events)
            messages = self.build_messages(channel)
            if messages is None:
                st.watermark = target
                return False

            result = await self.llm_call(messages)
            text = (result or "").strip()
            if not text:
                st.failures += 1
                logger.warning("Session summary: empty result for %s", channel)
                return False

            if len(text) > self.max_chars:
                text = text[: self.max_chars].rsplit(" ", 1)[0] + "..."

            st.summary = text
            st.watermark = target
            del st.pending_events[:events_taken]
            st.updated_at = self._clock()
            st.updates += 1
            self._save(self._norm(channel), st)
            logger.info("Session summary updated for %s (%d chars, watermark=%d)",
                        channel, len(text), target)
            return True
        except Exception as e:  # noqa: BLE001 - background task must not die loudly
            st.failures += 1
            logger.error("Session summary failed for %s: %s", channel, e)
            return False
        finally:
            st.in_flight = False

    # ------------------------------------------------------------ persistence

    def _path(self, key: str) -> Optional[Path]:
        if not self.persist_dir:
            return None
        return self.persist_dir / f"session_summary_{key}.json"

    def _save(self, key: str, st: _ChannelState) -> None:
        path = self._path(key)
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "summary": st.summary,
                "updated_at": st.updated_at,
                "updates": st.updates,
            }), encoding="utf-8")
        except OSError as e:
            logger.debug("Could not persist session summary: %s", e)

    def _load(self, key: str) -> Optional[_ChannelState]:
        path = self._path(key)
        if not path or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            age = self._clock() - float(data.get("updated_at", 0))
            if age > self.max_age_s:
                logger.info("Ignoring stale session summary for %s (%.0fs old)", key, age)
                return None
            st = _ChannelState(
                summary=str(data.get("summary", "")),
                updated_at=float(data.get("updated_at", 0)),
                updates=int(data.get("updates", 0)),
            )
            # The chat buffer restarted at seq 0; everything in it is new.
            st.watermark = 0
            logger.info("Restored session summary for %s (%.0fs old)", key, age)
            return st
        except (OSError, ValueError) as e:
            logger.debug("Could not load session summary: %s", e)
            return None

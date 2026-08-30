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
    # Turns harvested out of the chat buffer but not yet folded. The buffer is
    # a small ring sized for prompting; anything still owed to the summary
    # lives here so eviction can't lose it.
    pending_turns: List[Dict[str, Any]] = field(default_factory=list)
    harvest_seq: int = 0        # highest seq pulled out of the chat buffer
    # The batch a running fold is summarizing. Moved out of pending_turns /
    # pending_events for the duration so harvesting (and the backlog cap)
    # can only ever touch turns that are NOT in the batch; committed on
    # success, restored to the front on failure.
    in_flight_turns: List[Dict[str, Any]] = field(default_factory=list)
    in_flight_events: List[str] = field(default_factory=list)
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
        max_pending_turns: int = 500,
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
        # Hard cap on our own backlog so a wedged LLM can't grow it forever.
        self.max_pending_turns = max_pending_turns
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
        # A new stream: the previous one's backlog is not worth folding, but
        # we must not re-harvest it either.
        st.pending_turns.clear()
        st.in_flight_turns, st.in_flight_events = [], []
        try:
            st.harvest_seq = self.chat_buffer.last_seq(channel)
            st.watermark = st.harvest_seq
        except Exception as e:  # noqa: BLE001 — reset must not fail on a stub buffer
            logger.debug("Could not read last_seq on reset: %s", e)
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
            "harvest_seq": st.harvest_seq,
            "in_flight_turns": len(st.in_flight_turns),
            "pending_events": len(st.pending_events) + len(st.in_flight_events),
            "in_flight": st.in_flight,
            "updates": st.updates,
            "failures": st.failures,
            "updated_at": st.updated_at,
        }

    # ------------------------------------------------------------ scheduling

    def _harvest(self, channel: str, st: _ChannelState) -> int:
        """Move newly-arrived turns out of the chat buffer into our own list.

        The chat buffer is a ~50-turn ring sized for *prompting*, not for
        bookkeeping. Reading it only at fold time meant a busy channel could
        evict turns that had never been summarized -- they simply vanished
        from the co-host's memory of the stream. Harvesting on every
        append-driven check (maybe_schedule runs once per message) means
        eviction can only ever drop turns we already hold a copy of.

        Returns the number of turns harvested. A detected gap is logged
        rather than hidden: it means the buffer outran us anyway.
        """
        new = self.chat_buffer.get_since(channel, st.harvest_seq)
        if not new:
            return 0
        first = int(new[0].get("seq", 0))
        if st.harvest_seq and first > st.harvest_seq + 1:
            logger.warning(
                "Session summary: %d turn(s) were evicted from the chat buffer "
                "before they could be harvested for %s (seq %d..%d)",
                first - st.harvest_seq - 1, channel, st.harvest_seq + 1, first - 1)
        st.pending_turns.extend(new)
        st.harvest_seq = max(st.harvest_seq,
                             max(int(t.get("seq", 0)) for t in new))
        overflow = len(st.pending_turns) - self.max_pending_turns
        if overflow > 0:
            del st.pending_turns[:overflow]
            logger.warning("Session summary: backlog for %s exceeded %d turns; "
                           "dropped the %d oldest", channel,
                           self.max_pending_turns, overflow)
        return len(new)

    def _unsummarized(self, channel: str, st: _ChannelState) -> int:
        # Harvest before counting so the number reflects what has actually
        # arrived, not just what a previous call happened to pull in.
        # _harvest is idempotent: it only ever pulls seq > harvest_seq.
        self._harvest(channel, st)
        return len(st.pending_turns) + len(st.in_flight_turns)

    def should_update(self, channel: str) -> bool:
        st = self._state(channel)
        # Harvest first: this is the per-message hook, and it must run even
        # while a fold is in flight or the buffer could evict turns mid-call.
        self._harvest(channel, st)
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

    def build_messages(
        self,
        channel: str,
        turns: Optional[List[Dict[str, Any]]] = None,
        events: Optional[List[str]] = None,
    ) -> Optional[List[Dict[str, str]]]:
        """Prompt for one fold, or None if there's nothing to fold.

        `update()` passes the exact in-flight batch; other callers get
        whatever is currently pending."""
        st = self._state(channel)
        if turns is None:
            turns = st.pending_turns
        if events is None:
            events = st.pending_events
        rendered = self._render_turns(turns)
        events = "\n".join(f"[event] {e}" for e in events)
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
            # Harvest anything that landed since the last check, then snapshot
            # exactly what this fold covers. Turns that arrive during the LLM
            # round-trip stay in pending_turns for the next fold; nothing is
            # removed until the summary that contains it actually succeeded,
            # so a failed fold retries the same turns without duplicating them.
            self._harvest(channel, st)
            # Move the batch out of the pending lists. Anything that arrives
            # while the LLM call is in flight lands in pending_* and is
            # untouched by this fold, whichever way it ends.
            st.in_flight_turns, st.pending_turns = st.pending_turns, []
            st.in_flight_events, st.pending_events = st.pending_events, []
            target = max((int(t.get("seq", 0)) for t in st.in_flight_turns),
                         default=st.watermark)
            messages = self.build_messages(channel, st.in_flight_turns, st.in_flight_events)
            if messages is None:
                st.in_flight_turns, st.in_flight_events = [], []
                st.watermark = target
                return False

            result = await self.llm_call(messages)
            text = (result or "").strip()
            if not text:
                st.failures += 1
                logger.warning("Session summary: empty result for %s", channel)
                self._restore_in_flight(channel, st)
                return False

            if len(text) > self.max_chars:
                text = text[: self.max_chars].rsplit(" ", 1)[0] + "..."

            st.summary = text
            st.watermark = target
            # Commit exactly the batch that was summarized
            st.in_flight_turns, st.in_flight_events = [], []
            st.updated_at = self._clock()
            st.updates += 1
            self._save(self._norm(channel), st)
            logger.info("Session summary updated for %s (%d chars, watermark=%d)",
                        channel, len(text), target)
            return True
        except Exception as e:  # noqa: BLE001 - background task must not die loudly
            st.failures += 1
            logger.error("Session summary failed for %s: %s", channel, e)
            self._restore_in_flight(channel, st)
            return False
        finally:
            st.in_flight = False

    def _restore_in_flight(self, channel: str, st: _ChannelState) -> None:
        """A fold failed: put its batch back at the FRONT of the pending
        lists (it is older than anything harvested meanwhile), then re-apply
        the backlog cap. Nothing is duplicated -- the batch was moved, not
        copied -- and nothing newer is deleted."""
        if not st.in_flight_turns and not st.in_flight_events:
            return
        st.pending_turns = st.in_flight_turns + st.pending_turns
        st.pending_events = st.in_flight_events + st.pending_events
        st.in_flight_turns, st.in_flight_events = [], []
        overflow = len(st.pending_turns) - self.max_pending_turns
        if overflow > 0:
            del st.pending_turns[:overflow]
            logger.warning("Session summary: backlog for %s exceeded %d turns after a "
                           "failed fold; dropped the %d oldest", channel,
                           self.max_pending_turns, overflow)

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
            from utils.atomic_write import write_json_atomic
            write_json_atomic(path, {
                "summary": st.summary,
                "updated_at": st.updated_at,
                "updates": st.updates,
            })
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

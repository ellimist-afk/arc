"""
Handle for a reply that is spoken while it is still being written.

The engine fills `sentences` with an async iterator that yields TTS-ready
sentences as the model streams; the audio queue consumes it. When the
stream ends (or is abandoned), the engine finalizes `text` / `speech_text`
from what was actually yielded and sets `done`, so chat always shows what
was said — no more, no less.

`SentenceStream` wraps the engine's generator so that closing it *always*
notifies the reply — including when nothing ever pulled from it. A bare
async generator's `finally` does not run on `aclose()` if the generator was
never started, which would leave `done` unset and the coordinator waiting
for a reply that expired in the audio queue before it began.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional


@dataclass
class StreamedReply:
    personality: str = ""
    sentences: Optional[AsyncIterator[str]] = None
    text: Optional[str] = None          # chat text (personality modifications applied)
    speech_text: Optional[str] = None   # what was sent to TTS, joined
    fell_back: bool = False             # first sentence failed the guard; blocking path used
    aborted: bool = False               # consumer stopped early (skip / shutdown / TTL)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    extra: dict = field(default_factory=dict)

    async def wait(self) -> Optional[str]:
        """Block until the reply is finalized; returns the chat text."""
        await self.done.wait()
        return self.text

    def abandon(self) -> None:
        """Consumer gave up (or never started). Idempotent: if the engine
        already finalized this reply, nothing changes."""
        if not self.done.is_set():
            self.aborted = True
            self.done.set()

    def as_response(self) -> Optional[dict[str, Any]]:
        """Shape-compatible with generate_response() for callers that only
        look at text / speech_text after completion."""
        if not self.text:
            return None
        return {
            'text': self.text,
            'speech_text': self.speech_text,
            'should_speak': True,
            'personality': self.personality,
            'streamed': True,
            'fell_back': self.fell_back,
        }


class SentenceStream:
    """Async iterator over an inner async generator with a close hook that
    fires whether or not iteration ever began."""

    def __init__(self, inner: AsyncIterator[str], on_close: Optional[Callable[[], None]] = None) -> None:
        self._inner = inner
        self._on_close = on_close
        self.started = False
        self.closed = False

    def __aiter__(self) -> "SentenceStream":
        return self

    async def __anext__(self) -> str:
        self.started = True
        try:
            return await self._inner.__anext__()
        except StopAsyncIteration:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        aclose = getattr(self._inner, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001 — closing must not raise into the consumer
                pass
        if self._on_close:
            try:
                self._on_close()
            except Exception:  # noqa: BLE001
                pass

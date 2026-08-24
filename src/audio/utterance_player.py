"""
Pipeline-overlap player for one spoken utterance.

WHY THIS EXISTS:
Speaking sentence-by-sentence only helps if the TTS for sentence N+1 is
being generated *while* sentence N plays. Done naively (generate, play,
generate, play) it sounds worse than today: a silent gap between every
sentence. This runs a two-stage pipeline:

    sentences ──► [TTS producer] ──► bounded queue ──► [playback consumer]

The producer converts sentences to audio as fast as they arrive (bounded by
`prefetch_depth` so it can't run the whole reply ahead); the consumer plays
in order. Skip cancels the producer, drains the queue, and lets the caller
interrupt the clip currently playing through its own mechanism.

No audio code here: `tts` and `play` are injected, so the whole thing is
unit-testable with fakes and timing assertions. The audio queue owns the
real ones (its cache-aware TTS and its off-loop chunked playback).

Termination is signalled by the producer task finishing, not by a sentinel
in the audio queue: a sentinel needs a slot, and making room for one when
TTS outruns playback (cache hits) meant dropping a real clip.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, List, Optional

from utils.task_registry import cancel_and_wait

logger = logging.getLogger(__name__)

TTSFn = Callable[[str], Awaitable[Optional[bytes]]]
PlayFn = Callable[[bytes], Awaitable[None]]


@dataclass
class UtteranceStats:
    sentences: int = 0
    played: int = 0
    failed: int = 0        # TTS returned nothing for a sentence
    skipped: int = 0       # clips dropped by skip() after they were generated
    started_at: float = 0.0
    first_audio_at: Optional[float] = None
    finished_at: float = 0.0
    texts: List[str] = field(default_factory=list)

    @property
    def time_to_first_audio(self) -> Optional[float]:
        if self.first_audio_at is None:
            return None
        return self.first_audio_at - self.started_at

    @property
    def duration(self) -> float:
        return self.finished_at - self.started_at


class UtterancePlayer:
    def __init__(
        self,
        tts: TTSFn,
        play: PlayFn,
        *,
        prefetch_depth: int = 2,
        clock: Callable[[], float] = time.monotonic,
        on_first_audio: Optional[Callable[[], None]] = None,
    ) -> None:
        self._tts = tts
        self._play = play
        self.prefetch_depth = max(1, prefetch_depth)
        self._clock = clock
        self._on_first_audio = on_first_audio
        self._skipped = False
        self._producer: Optional[asyncio.Task] = None
        self.stats = UtteranceStats()

    @property
    def skipped(self) -> bool:
        return self._skipped

    def skip(self) -> None:
        """Stop after the clip currently playing; discard everything pending."""
        self._skipped = True
        if self._producer and not self._producer.done():
            self._producer.cancel()

    async def _produce(self, sentences: AsyncIterator[str], q: "asyncio.Queue") -> None:
        try:
            async for sentence in sentences:
                if self._skipped:
                    break
                sentence = (sentence or "").strip()
                if not sentence:
                    continue
                self.stats.sentences += 1
                self.stats.texts.append(sentence)
                try:
                    audio = await self._tts(sentence)
                except Exception as e:  # noqa: BLE001 — one bad sentence must not end the reply
                    logger.warning("TTS failed for sentence %r: %s", sentence[:40], e)
                    audio = None
                if not audio:
                    self.stats.failed += 1
                    continue
                await q.put(audio)  # backpressure: at most prefetch_depth clips ahead
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001 — upstream (LLM stream) died; play what we have
            logger.error("Utterance producer stopped early: %s", e)
        finally:
            # Release the upstream generator (LLM stream) whether we finished,
            # were skipped, or crashed — its finalizer sets the reply's text.
            aclose = getattr(sentences, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001
                    pass

    async def _next_clip(self, q: "asyncio.Queue") -> Optional[bytes]:
        """Next clip, or None once the producer is finished and the queue is drained."""
        while True:
            if not q.empty():
                return q.get_nowait()
            if self._producer is None or self._producer.done():
                return None
            getter = asyncio.ensure_future(q.get())
            try:
                done, _ = await asyncio.wait({getter, self._producer},
                                             return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                # asyncio.wait leaves its futures running; a run() cancelled
                # here must not orphan the pending Queue.get()
                getter.cancel()
                raise
            if getter in done:
                return getter.result()
            # Producer finished first; re-check the queue before giving up.
            # (cancel_and_wait: a cancelled run() must not keep looping here.)
            await cancel_and_wait(getter, what="utterance getter")

    async def run(self, sentences: AsyncIterator[str]) -> UtteranceStats:
        """Play every sentence in order, overlapping TTS with playback."""
        self.stats = UtteranceStats(started_at=self._clock())
        q: "asyncio.Queue" = asyncio.Queue(maxsize=self.prefetch_depth)
        self._producer = asyncio.create_task(self._produce(sentences, q))
        try:
            while True:
                audio = await self._next_clip(q)
                if audio is None:
                    break
                if self._skipped:
                    self.stats.skipped += 1
                    continue
                if self.stats.first_audio_at is None:
                    self.stats.first_audio_at = self._clock()
                    if self._on_first_audio:
                        try:
                            self._on_first_audio()
                        except Exception:  # noqa: BLE001
                            pass
                try:
                    await self._play(audio)
                    self.stats.played += 1
                except Exception as e:  # noqa: BLE001
                    logger.error("Playback failed mid-utterance: %s", e)
        finally:
            if not self._producer.done():
                # Propagates if run() itself is cancelled while the producer
                # is still cleaning up -- never let that look like a finish.
                await cancel_and_wait(self._producer, what="utterance producer")
            self.stats.finished_at = self._clock()
        return self.stats

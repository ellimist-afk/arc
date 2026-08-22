"""UtterancePlayer: order, overlap, first-audio latency, skip, failure isolation.

Fake TTS/play with small real sleeps so overlap is measurable. Sleeps are
milliseconds; the whole file runs in well under a second.
"""
import asyncio
import time

import pytest

from audio.utterance_player import UtterancePlayer

TTS_MS = 0.020
PLAY_MS = 0.040


class Fakes:
    def __init__(self, tts_ms=TTS_MS, play_ms=PLAY_MS, fail_on=()):
        self.tts_ms, self.play_ms, self.fail_on = tts_ms, play_ms, set(fail_on)
        self.played = []
        self.play_times = []
        self.tts_calls = []

    async def tts(self, text):
        self.tts_calls.append(text)
        await asyncio.sleep(self.tts_ms)
        if text in self.fail_on:
            return None
        return text.encode()

    async def play(self, audio):
        self.play_times.append(time.monotonic())
        await asyncio.sleep(self.play_ms)
        self.played.append(audio.decode())


async def gen(items, delay=0.0):
    for it in items:
        if delay:
            await asyncio.sleep(delay)
        yield it


SENT = ["One sentence here.", "Two sentence here.", "Three sentence here."]


async def test_plays_in_order_and_counts():
    f = Fakes()
    p = UtterancePlayer(f.tts, f.play)
    st = await p.run(gen(SENT))
    assert f.played == SENT
    assert st.sentences == 3 and st.played == 3 and st.failed == 0 and st.skipped == 0
    assert st.texts == SENT


async def test_overlap_beats_sequential_and_first_audio_is_one_tts_away():
    # Measure the sequential reference with the same fakes on the same machine,
    # so coarse Windows timers (15.6ms) inflate both sides equally.
    ref = Fakes()
    t0 = time.monotonic()
    for s in SENT:
        await ref.play(await ref.tts(s))
    sequential = time.monotonic() - t0
    t1 = time.monotonic()
    await ref.tts(SENT[0])
    one_tts = time.monotonic() - t1

    f = Fakes()
    p = UtterancePlayer(f.tts, f.play)
    st = await p.run(gen(SENT))
    assert st.duration < sequential * 0.85, f"no overlap: {st.duration:.3f}s vs sequential {sequential:.3f}s"
    assert st.time_to_first_audio is not None
    assert st.time_to_first_audio < one_tts * 1.5 + 0.020


async def test_prefetch_depth_bounds_how_far_tts_runs_ahead():
    f = Fakes(play_ms=0.060)
    p = UtterancePlayer(f.tts, f.play, prefetch_depth=1)
    # With depth 1, the producer can hold at most one finished clip while one plays.
    # Observe: by the time the first play starts, at most 2 TTS calls have begun.
    started = []

    async def tts(text):
        started.append((text, time.monotonic()))
        return await f.tts(text)

    p._tts = tts
    await p.run(gen(SENT * 2))
    first_play = f.play_times[0]
    assert sum(1 for _, t in started if t <= first_play) <= 2


async def test_skip_drops_pending_and_stops_producer():
    f = Fakes(play_ms=0.050)
    first_play_started = asyncio.Event()

    async def play(audio):
        first_play_started.set()
        await f.play(audio)

    p = UtterancePlayer(f.tts, play)

    async def skipper():
        await first_play_started.wait()  # deterministic: skip while clip 1 plays
        p.skip()

    asyncio.create_task(skipper())
    st = await p.run(gen(SENT))
    assert f.played == [SENT[0]]
    assert st.played == 1
    assert p.skipped
    assert st.played + st.skipped + st.failed <= st.sentences
    assert p._producer.done()


async def test_skip_closes_upstream_generator():
    f = Fakes(play_ms=0.050)
    closed = asyncio.Event()
    first_play_started = asyncio.Event()

    async def upstream():
        try:
            for s in SENT:
                yield s
        finally:
            closed.set()

    async def play(audio):
        first_play_started.set()
        await f.play(audio)

    p = UtterancePlayer(f.tts, play)

    async def skipper():
        await first_play_started.wait()
        p.skip()

    asyncio.create_task(skipper())
    await p.run(upstream())
    assert closed.is_set(), "upstream generator must be closed so its finalizer runs"


async def test_tts_failure_skips_only_that_sentence():
    f = Fakes(fail_on={SENT[1]})
    p = UtterancePlayer(f.tts, f.play)
    st = await p.run(gen(SENT))
    assert f.played == [SENT[0], SENT[2]]
    assert st.failed == 1 and st.played == 2


async def test_tts_exception_is_isolated():
    f = Fakes()

    async def tts(text):
        if text == SENT[0]:
            raise RuntimeError("api down")
        return await f.tts(text)

    p = UtterancePlayer(tts, f.play)
    st = await p.run(gen(SENT))
    assert f.played == SENT[1:]
    assert st.failed == 1


async def test_upstream_error_plays_what_arrived():
    f = Fakes()

    async def broken():
        yield SENT[0]
        yield SENT[1]
        raise RuntimeError("llm stream died")

    p = UtterancePlayer(f.tts, f.play)
    st = await p.run(broken())
    assert f.played == SENT[:2]
    assert st.played == 2


async def test_empty_stream_plays_nothing():
    f = Fakes()
    p = UtterancePlayer(f.tts, f.play)
    st = await p.run(gen([]))
    assert f.played == [] and st.sentences == 0 and st.time_to_first_audio is None


async def test_blank_sentences_are_ignored():
    f = Fakes()
    p = UtterancePlayer(f.tts, f.play)
    st = await p.run(gen(["", "   ", SENT[0]]))
    assert f.played == [SENT[0]] and st.sentences == 1


async def test_on_first_audio_callback_fires_once():
    f = Fakes()
    hits = []
    p = UtterancePlayer(f.tts, f.play, on_first_audio=lambda: hits.append(1))
    await p.run(gen(SENT))
    assert hits == [1]


async def test_slow_llm_stream_does_not_break_order():
    # Sentences arrive slower than playback: pipeline idles between them but stays ordered.
    f = Fakes(play_ms=0.005)
    p = UtterancePlayer(f.tts, f.play)
    st = await p.run(gen(SENT, delay=0.030))
    assert f.played == SENT and st.played == 3


async def test_instant_tts_with_full_queue_drops_nothing():
    # Regression: TTS outrunning playback (cache hits) filled the bounded queue;
    # the old sentinel-based shutdown made room for END by discarding a real clip.
    f = Fakes(tts_ms=0.0, play_ms=0.010)
    many = [f"Sentence number {i} here." for i in range(8)]
    p = UtterancePlayer(f.tts, f.play, prefetch_depth=1)
    st = await p.run(gen(many))
    assert f.played == many
    assert st.played == 8 and st.failed == 0 and st.skipped == 0


async def test_cancelling_run_does_not_hang_and_closes_upstream():
    f = Fakes(tts_ms=0.0, play_ms=0.050)
    closed = asyncio.Event()

    async def upstream():
        try:
            for s in SENT * 3:
                yield s
        finally:
            closed.set()

    p = UtterancePlayer(f.tts, f.play, prefetch_depth=1)
    task = asyncio.create_task(p.run(upstream()))
    await asyncio.sleep(0.015)  # first clip is playing, queue is full
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)
    assert closed.is_set()

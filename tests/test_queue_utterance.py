"""OptimizedAudioQueue streamed-utterance path: atomic item, no merge,
plays via the player with the queue's TTS/playback, skip cancels the rest,
expired items release their generator. No PyAudio, no network, no SQLite.
"""
import asyncio
from datetime import datetime, timedelta

import pytest

from audio.optimized_queue import AudioItem, OptimizedAudioQueue, Priority


class StubCache:
    async def get_stats(self):
        return {"entry_count": 0, "cache_size_mb": 0.0, "hit_rate": "0%"}


@pytest.fixture
def q(monkeypatch):
    queue = OptimizedAudioQueue(openai_api_key="test", enable_pre_buffering=False)
    queue.cache = StubCache()
    played = []
    tts_calls = []

    async def fake_get_or_generate(item):
        tts_calls.append(item.text)
        await asyncio.sleep(0.005)
        return item.text.encode()

    async def fake_play(audio):
        await asyncio.sleep(0.01)
        played.append(audio.decode())

    monkeypatch.setattr(queue, "_get_or_generate_audio", fake_get_or_generate)
    monkeypatch.setattr(queue, "_play_audio", fake_play)
    queue._test_played = played
    queue._test_tts = tts_calls
    return queue


async def sentences(items, closed_flag=None, delay=0.0):
    try:
        for s in items:
            if delay:
                await asyncio.sleep(delay)
            yield s
    finally:
        if closed_flag is not None:
            closed_flag.set()


SENT = ["First sentence here.", "Second sentence here.", "Third sentence here."]


async def test_utterance_is_one_item_and_plays_in_order(q):
    player = await q.queue_utterance(sentences(SENT), priority="normal", user="v")
    assert len(q.queue) == 1 and q.queue[0].utterance is player
    await q.process_next()
    assert q._test_played == SENT
    assert q._test_tts == SENT
    assert q.utterances_played == 1
    assert q.last_time_to_first_audio is not None
    stats = await q.get_stats()
    assert stats["utterances_played"] == 1


async def test_mention_bumps_priority(q):
    await q.queue_utterance(sentences(SENT), priority="normal", is_mention=True)
    assert q.queue[0].priority == Priority.HIGH
    await q.queue_utterance(sentences(SENT), priority="critical", is_mention=True)
    assert q.queue[0].priority == Priority.CRITICAL


async def test_plain_audio_never_merges_into_an_utterance(q):
    await q.queue_utterance(sentences(SENT), priority="normal", user="v")
    await q.queue_audio("follow-up text", priority="normal", user="v")
    assert len(q.queue) == 2, "same user+priority within 5s would normally merge; utterances are atomic"
    assert q.queue[0].utterance is not None or q.queue[1].utterance is not None


async def test_skip_mid_utterance_stops_remaining_sentences(q):
    first_play = asyncio.Event()
    orig_play = q._play_audio

    async def play(audio):
        first_play.set()
        await orig_play(audio)
    q._play_audio = play

    await q.queue_utterance(sentences(SENT, delay=0.002), priority="normal")

    async def skipper():
        await first_play.wait()
        q.skip()
    asyncio.create_task(skipper())
    await q.process_next()
    assert q._test_played == [SENT[0]]
    assert q.queue == []


async def test_expired_utterance_is_dropped_and_stream_closed(q):
    # Production wraps the engine generator in SentenceStream precisely because a
    # never-started async generator's finally does NOT run on aclose().
    from personality.streamed_reply import SentenceStream
    closed = asyncio.Event()
    stream = SentenceStream(sentences(SENT), on_close=closed.set)
    await q.queue_utterance(stream, priority="normal")
    q.queue[0].timestamp = datetime.now() - timedelta(seconds=10_000)
    await q.process_next()
    assert q._test_played == []
    assert closed.is_set(), "expired item must close its stream so the reply finalizes"
    assert stream.started is False and stream.closed is True


async def test_tts_failure_for_one_sentence_does_not_end_utterance(q):
    async def flaky(item):
        if item.text == SENT[1]:
            return None
        return item.text.encode()
    q._get_or_generate_audio = flaky
    await q.queue_utterance(sentences(SENT), priority="normal")
    await q.process_next()
    assert q._test_played == [SENT[0], SENT[2]]


async def test_regular_items_still_work_alongside(q):
    await q.queue_audio("plain clip", priority="high")
    await q.queue_utterance(sentences(SENT[:1]), priority="normal")
    await q.process_next()  # high-priority plain clip first
    await q.process_next()
    assert q._test_played == ["plain clip", SENT[0]]

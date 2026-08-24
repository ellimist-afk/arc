"""Streamed-reply cancellation regressions (Bugbot F4, F5).

F4: skip() and shutdown() dropped queued streamed utterances without telling
    their sentence streams, so the chat handler awaiting the reply sat there
    for the full 90s timeout.
F5: a timed-out streamed response left the original utterance queued, so it
    could speak minutes later -- next to the blocking fallback the caller had
    since generated.

No PyAudio, no network, no SQLite.
"""
import asyncio

import pytest

from audio.optimized_queue import OptimizedAudioQueue
from bot.response_coordinator import ResponseCoordinator
from personality.streamed_reply import SentenceStream, StreamedReply

SENT = ["First sentence here.", "Second sentence here.", "Third sentence here."]


class StubCache:
    async def get_stats(self):
        return {"entry_count": 0, "cache_size_mb": 0.0, "hit_rate": "0%"}

    async def close(self):
        return None


def make_reply(items=SENT, delay=0.005):
    """A StreamedReply wired exactly as the engine wires it."""
    reply = StreamedReply(personality="test")

    async def gen():
        spoken = []
        try:
            for s in items:
                await asyncio.sleep(delay)
                spoken.append(s)
                yield s
        finally:
            if spoken:
                reply.speech_text = " ".join(spoken)
                reply.text = reply.speech_text.lower()
            reply.done.set()

    reply.sentences = SentenceStream(gen(), on_close=reply.abandon)
    return reply


@pytest.fixture
def q(monkeypatch):
    queue = OptimizedAudioQueue(openai_api_key="test", enable_pre_buffering=False)
    queue.cache = StubCache()
    queue.processing_task = None
    played = []

    async def fake_tts(item):
        await asyncio.sleep(0.005)
        return item.text.encode()

    async def fake_play(audio):
        await asyncio.sleep(0.01)
        played.append(audio.decode())

    monkeypatch.setattr(queue, "_get_or_generate_audio", fake_tts)
    monkeypatch.setattr(queue, "_play_audio", fake_play)
    queue._test_played = played
    return queue


# =====================================================================
# F4 — dropped utterances must always complete
# =====================================================================

async def test_skip_unblocks_a_queued_streamed_reply_immediately():
    q_ = OptimizedAudioQueue(openai_api_key="test", enable_pre_buffering=False)
    q_.cache = StubCache()
    reply = make_reply()
    await q_.queue_utterance(reply.sentences, priority="normal")

    q_.skip()

    # The whole point: this must not wait out the coordinator's 90s timeout.
    text = await asyncio.wait_for(reply.wait(), timeout=1.0)
    assert reply.done.is_set()
    assert reply.aborted is True
    assert text is None, "nothing was spoken, so there is nothing to post"
    assert q_.queue == []
    assert q_.utterances_abandoned == 1


async def test_skip_closes_the_stream_even_though_it_never_started():
    """A never-started async generator does NOT run its `finally` on aclose()
    -- which is the whole reason SentenceStream carries an explicit close
    hook. The hook is what releases the waiter here."""
    q_ = OptimizedAudioQueue(openai_api_key="test", enable_pre_buffering=False)
    q_.cache = StubCache()
    notified = []

    async def gen():
        for s in SENT:
            yield s

    stream = SentenceStream(gen(), on_close=lambda: notified.append(1))
    await q_.queue_utterance(stream, priority="normal")
    q_.skip()
    await asyncio.sleep(0.05)          # let the scheduled cleanup run

    assert stream.started is False
    assert stream.finalized is True, "the waiter must be released"
    assert stream.closed is True, "the inner generator must be closed"
    assert notified == [1]


async def test_skip_mid_playback_runs_the_generators_own_cleanup(q):
    """When the stream HAS started, aclose() unwinds it and the generator's
    finally block runs as usual."""
    closed = asyncio.Event()

    async def gen():
        try:
            for s in SENT:
                await asyncio.sleep(0.02)
                yield s
        finally:
            closed.set()

    stream = SentenceStream(gen(), on_close=lambda: None)
    await q.queue_utterance(stream, priority="normal")
    play = asyncio.create_task(q.process_next())
    await asyncio.sleep(0.05)          # first sentence is in flight
    assert stream.started is True

    q.skip()
    await asyncio.wait_for(play, timeout=2.0)
    await asyncio.sleep(0.05)
    assert closed.is_set(), "a started generator's cleanup must run"
    assert stream.finalized is True


async def test_shutdown_unblocks_every_queued_streamed_reply():
    q_ = OptimizedAudioQueue(openai_api_key="test", enable_pre_buffering=False)
    q_.cache = StubCache()
    q_.processing_task = None
    replies = [make_reply() for _ in range(3)]
    for r in replies:
        await q_.queue_utterance(r.sentences, priority="normal")

    await q_.shutdown()

    for r in replies:
        await asyncio.wait_for(r.wait(), timeout=1.0)
        assert r.aborted is True
    assert q_.queue == []


async def test_skip_is_idempotent_and_completion_fires_once():
    q_ = OptimizedAudioQueue(openai_api_key="test", enable_pre_buffering=False)
    q_.cache = StubCache()
    fired = []
    stream = SentenceStream(_empty_gen(), on_close=lambda: fired.append(1))
    await q_.queue_utterance(stream, priority="normal")

    q_.skip()
    q_.skip()
    await stream.aclose()
    assert fired == [1], "completion state must be set exactly once"


async def _empty_gen():
    if False:
        yield ""


async def test_skipped_utterance_does_not_play_afterwards(q):
    reply = make_reply()
    await q.queue_utterance(reply.sentences, priority="normal")
    q.skip()
    await q.process_next()             # queue is empty; nothing may speak
    assert q._test_played == []


# =====================================================================
# F5 — a timed-out streamed response must not play later
# =====================================================================

class FakeTwitch:
    def __init__(self):
        self.sent = []

    def is_connected(self):
        return True

    async def send_message(self, text):
        self.sent.append(text)


async def test_timeout_abandons_the_utterance_and_posts_nothing(q):
    tw = FakeTwitch()
    rc = ResponseCoordinator(twitch_client=tw, audio_queue=q, settings_path="nope.json")
    reply = make_reply()

    text = await rc.coordinate_streamed_response(reply, timeout=0.05)

    assert text is None, "a timed-out reply must not be posted to chat"
    assert tw.sent == []
    assert q.queue == [], "the utterance must be removed from the queue"

    # And it must stay silent even if the queue keeps draining afterwards.
    await q.process_next()
    assert q._test_played == []
    await asyncio.wait_for(reply.wait(), timeout=1.0)
    assert reply.aborted is True


async def test_timeout_while_playing_stops_the_current_utterance(q):
    reply = make_reply(delay=0.05)

    player = await q.queue_utterance(reply.sentences, priority="normal")
    play_task = asyncio.create_task(q.process_next())
    await asyncio.sleep(0.02)

    assert q.cancel_utterance(player) is True
    await asyncio.wait_for(play_task, timeout=2.0)
    assert player.skipped is True
    assert len(q._test_played) <= 1, "playback must stop, not finish the reply"
    await asyncio.wait_for(reply.wait(), timeout=1.0)


async def test_caller_cancellation_also_abandons(q):
    tw = FakeTwitch()
    rc = ResponseCoordinator(twitch_client=tw, audio_queue=q, settings_path="nope.json")
    reply = make_reply(delay=5.0)      # never finishes on its own

    task = asyncio.create_task(rc.coordinate_streamed_response(reply, timeout=30))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.wait_for(reply.wait(), timeout=1.0)
    assert reply.aborted is True
    assert q.queue == []


async def test_cancel_utterance_reports_unknown_players(q):
    assert q.cancel_utterance(None) is False
    assert q.cancel_utterance(object()) is False


async def test_successful_streamed_response_is_unaffected(q):
    """The cancellation paths must not disturb the normal case."""
    tw = FakeTwitch()
    rc = ResponseCoordinator(twitch_client=tw, audio_queue=q, settings_path="nope.json")
    reply = make_reply()

    drain = asyncio.create_task(q.process_next())
    text = await rc.coordinate_streamed_response(reply, timeout=5.0)
    await asyncio.wait_for(drain, timeout=5.0)

    assert text == " ".join(SENT).lower()
    assert tw.sent == [text]
    assert q._test_played == SENT
    assert reply.aborted is False

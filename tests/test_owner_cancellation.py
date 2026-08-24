"""Owner cancellation must not be swallowed by "cancel the child and wait".

The pattern

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

is meant to ignore the cancellation it just caused. But the same except
clause also catches the CancelledError delivered when the *current* task --
the shutdown/stop owner -- is cancelled while it waits. The owner then
returns normally and a shared shutdown future publishes success for a
teardown that never finished.

`utils.task_registry.cancel_and_wait` distinguishes the two by watching the
current task's cancellation count across the await. These tests are
event-gated and deterministic; no devices, no network.
"""
import asyncio

import pytest

from audio.optimized_queue import OptimizedAudioQueue
from audio.utterance_player import UtterancePlayer
from bot.response_coordinator import ResponseCoordinator
from realtime.session import RealtimeVoiceSession
from twitch.token_refresher import TwitchTokenRefresher
from utils.task_registry import cancel_and_wait


def stubborn(cleaning: asyncio.Event, gate: asyncio.Event):
    """A child that catches its first cancellation, signals it is cleaning
    up, then blocks until released -- the shape of a processor mid-teardown."""
    async def run():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await gate.wait()          # cleanup in progress; owner may be cancelled here
    return run()


async def _cancel_owner_while_child_cleans(owner_coro, cleaning, gate):
    """Start the owner, wait until the child is inside its cleanup, cancel the
    owner. Returns the owner task (already finished) for assertions."""
    owner = asyncio.create_task(owner_coro)
    await asyncio.wait_for(cleaning.wait(), timeout=2)
    await asyncio.sleep(0)              # owner is now blocked awaiting the child
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(owner, timeout=2)
    return owner


# =====================================================================
# the helper itself
# =====================================================================

async def test_helper_quiet_when_child_cancels_cleanly():
    async def child():
        await asyncio.Event().wait()
    t = asyncio.create_task(child())
    await asyncio.sleep(0)
    await cancel_and_wait(t)            # no exception
    assert t.cancelled()


async def test_helper_quiet_when_child_suppresses_and_returns():
    async def child():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return "cleaned up"
    t = asyncio.create_task(child())
    await asyncio.sleep(0)
    await cancel_and_wait(t)
    assert t.result() == "cleaned up"


async def test_helper_raises_when_owner_is_cancelled_during_child_cleanup():
    cleaning, gate = asyncio.Event(), asyncio.Event()
    child = asyncio.create_task(stubborn(cleaning, gate))
    await asyncio.sleep(0)

    async def owner():
        await cancel_and_wait(child)
        return "RETURNED_SUCCESS"       # must never happen

    await _cancel_owner_while_child_cleans(owner(), cleaning, gate)
    assert not child.done(), "child is still cleaning up; owner did not wait for it"
    gate.set()
    await child


async def test_helper_raises_when_owner_cancelled_as_child_finishes_normally():
    """Cancellation requested in the same tick the child completes: the owner
    must still see it, even though the child returned normally."""
    gate = asyncio.Event()

    async def child():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await gate.wait()
            return "done"
    t = asyncio.create_task(child())
    await asyncio.sleep(0)

    async def owner():
        await cancel_and_wait(t)
        return "RETURNED_SUCCESS"
    o = asyncio.create_task(owner())
    await asyncio.sleep(0.01)
    gate.set()                          # child will finish...
    o.cancel()                          # ...and the owner is cancelled before it resumes
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(o, timeout=2)
    assert t.result() == "done"


async def test_helper_tolerates_done_and_failed_children():
    async def ok():
        return 1

    async def bad():
        raise RuntimeError("child failed")
    t1, t2 = asyncio.create_task(ok()), asyncio.create_task(bad())
    await asyncio.sleep(0)
    await cancel_and_wait(t1)
    await cancel_and_wait(t2)           # failure is logged, not raised
    await cancel_and_wait(None)


# =====================================================================
# OptimizedAudioQueue.shutdown(): the reproduced defect
# =====================================================================

class _Cache:
    def __init__(self):
        self.closes = 0

    async def close(self):
        self.closes += 1

    async def get_stats(self):
        return {}


class _Handle:
    def __init__(self):
        self.n = 0

    def stop_stream(self):
        pass

    def close(self):
        self.n += 1

    def terminate(self):
        self.n += 1


def _queue():
    q = OptimizedAudioQueue(openai_api_key="test", enable_pre_buffering=False)
    q.cache, q.stream, q.pyaudio = _Cache(), _Handle(), _Handle()
    return q


async def test_queue_owner_cancellation_propagates_and_retry_finishes():
    q = _queue()
    cleaning, gate = asyncio.Event(), asyncio.Event()
    q.processing_task = asyncio.create_task(stubborn(cleaning, gate))
    await asyncio.sleep(0)
    stream, pa = q.stream, q.pyaudio

    owner = asyncio.create_task(q.shutdown())
    await asyncio.wait_for(cleaning.wait(), timeout=2)   # processor got its cancel, is cleaning
    await asyncio.sleep(0)
    waiter = asyncio.create_task(q.shutdown())
    await asyncio.sleep(0.01)
    assert not owner.done() and not waiter.done()

    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(owner, timeout=2)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, timeout=2)        # never told "success"

    fut = q._shutdown_future
    assert fut is None or fut.cancelled(), "shared future must be cancelled or cleared, never True"
    assert q.cache.closes == 0 and stream.n == 0 and pa.n == 0, "nothing torn down yet"
    assert q.processing_task is not None, "retry must wait for the processor again"

    gate.set()                                           # processor finishes its cleanup
    await asyncio.wait_for(q.shutdown(), timeout=2)      # retry
    assert q.processing_task is None
    assert q.cache.closes == 1 and stream.n == 1 and pa.n == 1
    assert q._shutdown_future.result() is True
    await q.shutdown()                                   # still exactly-once
    assert q.cache.closes == 1


async def test_queue_normal_child_cancellation_stays_quiet_and_successful():
    q = _queue()

    async def processor():
        await asyncio.Event().wait()
    q.processing_task = asyncio.create_task(processor())
    await asyncio.sleep(0)
    await asyncio.wait_for(q.shutdown(), timeout=2)
    assert q._shutdown_future.result() is True
    assert q.cache.closes == 1 and q.processing_task is None


# =====================================================================
# audited stop methods
# =====================================================================

async def test_session_stop_does_not_swallow_owner_cancellation():
    s = RealtimeVoiceSession(model='m', voice='v', vad='server_vad',
                             instructions_provider=lambda: 'x', api_key='k',
                             connect=None, create_task=asyncio.ensure_future)
    s.ws = None
    cleaning, gate = asyncio.Event(), asyncio.Event()
    s._supervisor = asyncio.create_task(stubborn(cleaning, gate))
    await asyncio.sleep(0)

    await _cancel_owner_while_child_cleans(s.stop(), cleaning, gate)
    assert s._supervisor is not None, "retry must be able to wait for the supervisor"
    gate.set()
    await asyncio.wait_for(s.stop(), timeout=2)
    assert s._supervisor is None


async def test_dead_air_stop_does_not_swallow_owner_cancellation():
    rc = ResponseCoordinator(twitch_client=None, audio_queue=None, settings_path="nope.json")
    cleaning, gate = asyncio.Event(), asyncio.Event()
    rc.dead_air_task = asyncio.create_task(stubborn(cleaning, gate))
    await asyncio.sleep(0)

    await _cancel_owner_while_child_cleans(rc.stop_dead_air_prevention(), cleaning, gate)
    assert rc.dead_air_task is not None
    gate.set()
    await asyncio.wait_for(rc.stop_dead_air_prevention(), timeout=2)
    assert rc.dead_air_task is None


async def test_token_refresher_stop_does_not_swallow_owner_cancellation():
    r = TwitchTokenRefresher.__new__(TwitchTokenRefresher)
    r.running = True
    cleaning, gate = asyncio.Event(), asyncio.Event()
    r.task = asyncio.create_task(stubborn(cleaning, gate))
    await asyncio.sleep(0)

    await _cancel_owner_while_child_cleans(r.stop(), cleaning, gate)
    assert r.task is not None
    gate.set()
    r.running = True                    # stop() early-returns when already stopped
    await asyncio.wait_for(r.stop(), timeout=2)
    assert r.task is None


async def test_utterance_player_run_propagates_owner_cancellation():
    """run()'s finally cancels the producer and waits; a producer that is
    mid-cleanup must not let a cancelled run() finish normally."""
    cleaning, gate = asyncio.Event(), asyncio.Event()

    async def tts(text):
        return text.encode()

    async def play(audio):
        await asyncio.sleep(0.01)

    p = UtterancePlayer(tts, play)

    async def produce(sentences, q):     # stands in for _produce: swallows, then blocks
        await stubborn(cleaning, gate)
    p._produce = produce

    async def gen():
        if False:
            yield ""
    run = asyncio.create_task(p.run(gen()))
    await asyncio.sleep(0.01)
    run.cancel()                        # lands in _next_clip; finally now waits on the producer
    await asyncio.wait_for(cleaning.wait(), timeout=2)
    await asyncio.sleep(0)
    run.cancel()                        # owner cancelled while awaiting the producer's cleanup
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, timeout=2)
    gate.set()
    await asyncio.sleep(0.01)

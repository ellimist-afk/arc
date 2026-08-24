"""Lifecycle truthfulness (hardening pass 3).

1. A Realtime session is "connected" only once the server has acknowledged
   session.update. A failed or unacknowledged handshake is a failed attempt.
2. TalkBot.shutdown()'s shared outcome reflects what really happened:
   component failures are collected (every later component still gets its
   turn), raised to every caller consistently, and cancellation never
   publishes success.
3. OptimizedAudioQueue.shutdown() / TTSCacheSQLite.close() callers all wait
   for the real close to finish; resources close exactly once.

Every test here failed against the pre-fix implementations.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from audio.optimized_queue import OptimizedAudioQueue
from audio.tts_cache_sqlite import TTSCacheSQLite
from bot.bot import TalkBot
from realtime.session import RealtimeVoiceSession


# =====================================================================
# 1. Realtime handshake
# =====================================================================

class _WS:
    """Scriptable socket. `send_raises` fails the configuration send;
    `inbound` is what the server says back; recv blocks when empty."""

    def __init__(self, send_raises=False, inbound=()):
        self.send_raises = send_raises
        self.inbound: asyncio.Queue = asyncio.Queue()
        for ev in inbound:
            self.inbound.put_nowait(ev)
        self.outbound = []
        self.closed = False

    async def send(self, raw):
        if self.send_raises:
            raise ConnectionError("socket is dead")
        self.outbound.append(json.loads(raw))

    async def recv(self):
        item = await self.inbound.get()
        return None if item is None else json.dumps(item)

    async def close(self):
        self.closed = True
        self.inbound.put_nowait(None)


async def _no_sleep(_s):
    return None


def _session(ws_factory, **kw):
    connects = []

    async def connect(url, headers):
        ws = ws_factory()
        connects.append(ws)
        return ws

    created = []

    def create_task(coro):
        created.append(coro)
        return asyncio.ensure_future(coro)

    s = RealtimeVoiceSession(model='m', voice='v', vad='server_vad',
                             instructions_provider=lambda: 'x', api_key='k',
                             connect=connect, create_task=create_task,
                             sleep=_no_sleep, **kw)
    s.on_connected = lambda: events.append('connected')
    events = []
    s._events, s._connects, s._created = events, connects, created
    return s


async def test_failed_session_update_send_never_becomes_connected():
    s = _session(lambda: _WS(send_raises=True))
    # Bounded: the pre-fix code marked this dead socket connected and then
    # sat in recv() forever -- the defect itself would otherwise hang the test
    with pytest.raises(Exception):
        await asyncio.wait_for(s._run_once(), timeout=2)
    assert s.connected is False
    assert s._connected_at is None
    assert s._events == [], "on_connected must not fire for a dead socket"
    assert s._created == [], "the mic pump must not start before the handshake"


async def test_repeated_handshake_send_failures_exhaust_the_budget():
    s = _session(lambda: _WS(send_raises=True), max_reconnects=2)
    await asyncio.wait_for(s._supervise(), timeout=5)
    assert s.gave_up is True
    assert len(s._connects) == 3          # initial + 2 reconnects
    assert s.connected is False and s._events == []


async def test_missing_acknowledgement_times_out_as_a_failed_attempt():
    s = _session(lambda: _WS(), handshake_timeout_s=0.05)   # server never acks
    with pytest.raises(Exception):
        await asyncio.wait_for(s._run_once(), timeout=2)
    assert s.connected is False and s._connected_at is None
    assert s._events == [] and s._created == []


async def test_server_error_during_handshake_fails_the_attempt():
    err = {"type": "error", "error": {"type": "invalid_request_error",
                                      "message": "bad session config"}}
    s = _session(lambda: _WS(inbound=[{"type": "session.created"}, err]),
                 handshake_timeout_s=1.0)
    with pytest.raises(Exception) as excinfo:
        await asyncio.wait_for(s._run_once(), timeout=2)
    assert "bad session config" in str(excinfo.value)
    assert s.connected is False and s._events == [] and s._created == []


async def test_acknowledged_handshake_connects_and_starts_the_pump_once():
    acked = _WS(inbound=[{"type": "session.created"}, {"type": "session.updated"}])
    s = _session(lambda: acked, handshake_timeout_s=1.0)

    async def drive():
        # let the session connect, then close the socket to end _run_once
        for _ in range(100):
            if s.connected:
                break
            await asyncio.sleep(0.005)
        s._stopping = True
        await acked.close()

    driver = asyncio.create_task(drive())
    await asyncio.wait_for(s._run_once(), timeout=2)
    await driver
    assert s._events == ['connected']
    assert len(s._created) == 1, "exactly one mic pump, started after the ack"
    assert acked.outbound[0]["type"] == "session.update"


async def test_connected_at_is_set_only_after_the_ack():
    acked = _WS(inbound=[{"type": "session.updated"}])
    s = _session(lambda: acked, handshake_timeout_s=1.0)
    seen = {}

    def on_connected():
        seen['connected_at'] = s._connected_at
    s.on_connected = on_connected

    async def drive():
        for _ in range(100):
            if s.connected:
                break
            await asyncio.sleep(0.005)
        s._stopping = True
        await acked.close()

    driver = asyncio.create_task(drive())
    await asyncio.wait_for(s._run_once(), timeout=2)
    await driver
    assert seen['connected_at'] is not None


# =====================================================================
# 2. Shared shutdown outcome
# =====================================================================

class Calls:
    def __init__(self):
        self.order, self.counts = [], {}

    def hit(self, name):
        self.order.append(name)
        self.counts[name] = self.counts.get(name, 0) + 1


def _bot(calls, *, registry_raises=False, audio_gate=None):
    bot = TalkBot.__new__(TalkBot)
    bot.running = True
    bot.shutdown_requested = False
    bot._shutdown_future = None
    bot._stop_event = None
    bot._loop = None
    bot.api_server = None
    bot.response_coordinator = None
    bot.voice_recognition = None
    bot.realtime_backend = None
    bot.vad_ducking = None
    bot.websocket_manager = None
    bot.stream_recap = None
    bot.session_summarizer = None
    bot.first_timer = None
    bot.current_game = None

    async def registry_shutdown():
        calls.hit("task_registry")
        if registry_raises:
            raise RuntimeError("registry exploded")
    bot.task_registry = SimpleNamespace(shutdown=registry_shutdown)

    async def audio_shutdown():
        calls.hit("audio")
        if audio_gate is not None:
            await audio_gate.wait()
    bot.audio_queue = SimpleNamespace(shutdown=audio_shutdown)

    async def twitch_disconnect():
        calls.hit("twitch")
    bot.twitch_client = SimpleNamespace(disconnect=twitch_disconnect)

    async def personality_shutdown():
        calls.hit("personality")
    bot.personality_engine = SimpleNamespace(shutdown=personality_shutdown)

    class Memory:
        redis_client = None

        async def close(self):
            calls.hit("memory")
    bot.memory_system = Memory()
    return bot


async def test_early_failure_does_not_stop_later_components():
    calls = Calls()
    bot = _bot(calls, registry_raises=True)
    with pytest.raises(Exception) as excinfo:
        await bot.shutdown()
    assert "task_registry" in str(excinfo.value)
    for name in ("audio", "twitch", "personality", "memory"):
        assert calls.counts.get(name) == 1, f"{name} was not cleaned up after the early failure"
    fut = bot._shutdown_future
    assert fut.done() and fut.exception() is not None, "failure must not become success"


async def test_concurrent_callers_observe_the_same_failure():
    calls = Calls()
    bot = _bot(calls, registry_raises=True)
    results = await asyncio.gather(bot.shutdown(), bot.shutdown(), bot.shutdown(),
                                   return_exceptions=True)
    assert all(isinstance(r, Exception) for r in results), results
    assert len({type(r) for r in results}) == 1
    assert calls.counts["task_registry"] == 1 and calls.counts["memory"] == 1

    with pytest.raises(Exception):                 # a later caller: same outcome
        await bot.shutdown()
    assert calls.counts["memory"] == 1


async def test_cancelled_owner_does_not_publish_success_and_stays_retriable():
    calls = Calls()
    gate = asyncio.Event()
    bot = _bot(calls, audio_gate=gate)

    owner = asyncio.create_task(bot.shutdown())
    await asyncio.sleep(0.02)                      # owner is blocked inside audio
    waiter = asyncio.create_task(bot.shutdown())
    await asyncio.sleep(0.02)
    assert not waiter.done()

    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    with pytest.raises(asyncio.CancelledError):
        await waiter                               # no false success for the waiter either
    assert bot._shutdown_future is None or not (
        bot._shutdown_future.done() and not bot._shutdown_future.cancelled()
        and bot._shutdown_future.exception() is None
    ), "a cancelled teardown must never be recorded as complete"

    gate.set()
    await bot.shutdown()                           # retry finishes the job
    assert calls.counts["task_registry"] == 1, "completed steps are not repeated"
    assert calls.counts["audio"] == 2, "the interrupted step is retried"
    assert calls.counts["memory"] == 1
    assert bot._shutdown_future.result() is True


async def test_successful_shutdown_is_exactly_once_and_reports_success():
    calls = Calls()
    bot = _bot(calls)
    await asyncio.gather(bot.shutdown(), bot.shutdown())
    await bot.shutdown()
    assert calls.counts == {"task_registry": 1, "audio": 1, "twitch": 1,
                            "personality": 1, "memory": 1}
    assert bot._shutdown_future.result() is True


# =====================================================================
# 3. Concurrent audio-queue / cache shutdown completion
# =====================================================================

class _GatedCache:
    def __init__(self, gate, raises=False):
        self.gate, self.raises, self.closes = gate, raises, 0

    async def close(self):
        self.closes += 1
        await self.gate.wait()
        if self.raises:
            raise OSError("disk vanished")

    async def get_stats(self):
        return {}


class _Counted:
    def __init__(self):
        self.n = 0

    def stop_stream(self):
        pass

    def close(self):
        self.n += 1

    def terminate(self):
        self.n += 1


def _queue(gate, raises=False):
    q = OptimizedAudioQueue(openai_api_key="test", enable_pre_buffering=False)
    q.cache = _GatedCache(gate, raises)
    q.stream = _Counted()
    q.pyaudio = _Counted()
    q.processing_task = None
    return q


async def test_concurrent_queue_shutdown_callers_all_wait_for_completion():
    gate = asyncio.Event()
    q = _queue(gate)
    stream, pa = q.stream, q.pyaudio

    first = asyncio.create_task(q.shutdown())
    second = asyncio.create_task(q.shutdown())
    await asyncio.sleep(0.03)
    assert q.cache.closes == 1, "close started exactly once"
    assert not first.done() and not second.done(), \
        "the second caller returned while the first was still closing"

    gate.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
    assert q.cache.closes == 1
    assert stream.n == 1 and pa.n == 1
    await asyncio.wait_for(q.shutdown(), timeout=1)   # after completion: immediate, safe
    assert q.cache.closes == 1


async def test_queue_close_failure_is_reported_to_every_caller():
    gate = asyncio.Event()
    q = _queue(gate, raises=True)
    stream, pa = q.stream, q.pyaudio
    gate.set()
    results = await asyncio.gather(q.shutdown(), q.shutdown(), return_exceptions=True)
    assert all(isinstance(r, Exception) for r in results), results
    assert "cache" in str(results[0]).lower()
    assert stream.n == 1 and pa.n == 1, "device teardown still happened"


async def test_concurrent_cache_close_callers_all_wait_for_the_database():
    cache = TTSCacheSQLite.__new__(TTSCacheSQLite)
    gate = asyncio.Event()
    closes = []

    class Conn:
        async def close(self):
            closes.append(1)
            await gate.wait()
    cache.db = Conn()

    first = asyncio.create_task(cache.close())
    second = asyncio.create_task(cache.close())
    await asyncio.sleep(0.03)
    assert closes == [1]
    assert not first.done() and not second.done(), \
        "the second caller returned before the database was closed"

    gate.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
    assert closes == [1] and cache.db is None
    await asyncio.wait_for(cache.close(), timeout=1)
    assert closes == [1]


async def test_cache_close_failure_is_reported_consistently():
    cache = TTSCacheSQLite.__new__(TTSCacheSQLite)

    class Conn:
        async def close(self):
            raise OSError("locked")
    cache.db = Conn()
    results = await asyncio.gather(cache.close(), cache.close(), return_exceptions=True)
    assert all(isinstance(r, OSError) for r in results), results

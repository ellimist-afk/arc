"""Shutdown is single-owner and idempotent (hardening follow-up #1).

Before: the SIGINT handler created a raw asyncio task running shutdown()
while run()'s `finally` called shutdown() again -- two teardowns interleaving
on the same components -- and the memory cleanup called a db_manager method
that does not exist. OptimizedAudioQueue.shutdown() closed the TTS cache
*before* cancelling the processor that might still be reading it, and a
second call would stop_stream()/terminate() already-released handles.

No devices, no network: every component is a counting stub.
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from audio.optimized_queue import OptimizedAudioQueue
from audio.tts_cache_sqlite import TTSCacheSQLite
from bot.bot import TalkBot


class Calls:
    """Counts calls and records their order across stubs."""
    def __init__(self):
        self.order = []
        self.counts = {}

    def hit(self, name):
        self.order.append(name)
        self.counts[name] = self.counts.get(name, 0) + 1


def _bot(calls: Calls, slow: float = 0.0):
    bot = TalkBot.__new__(TalkBot)
    bot.running = True
    bot.shutdown_requested = False
    bot._shutdown_future = None
    bot._stop_event = None
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
    bot.personality_engine = None
    bot.twitch_client = None

    async def registry_shutdown():
        calls.hit("task_registry.shutdown")
        if slow:
            await asyncio.sleep(slow)
    bot.task_registry = SimpleNamespace(shutdown=registry_shutdown)

    async def audio_shutdown():
        calls.hit("audio_queue.shutdown")
    bot.audio_queue = SimpleNamespace(shutdown=audio_shutdown)

    class Redis:
        async def aclose(self):
            calls.hit("redis.aclose")

    class Memory:
        def __init__(self):
            self.redis_client = Redis()
            self.db_manager = SimpleNamespace(close=self._db_close)

        async def _db_close(self):
            calls.hit("db_manager.close")

        async def close(self):
            calls.hit("memory.close")
    bot.memory_system = Memory()
    return bot


# ----------------------------------------------------------- signal + run()

async def test_signal_only_requests_and_run_cleanup_tears_down_once():
    calls = Calls()
    bot = _bot(calls)
    bot._stop_event = asyncio.Event()          # what run() creates

    before = len(asyncio.all_tasks())
    bot._signal_handler(2, None)               # SIGINT
    await asyncio.sleep(0)
    assert len(asyncio.all_tasks()) == before, "the signal handler must not spawn a task"
    assert bot.shutdown_requested is True
    assert bot.running is False
    assert bot._stop_event.is_set(), "run()'s loop must be woken"
    assert calls.order == [], "nothing is torn down by the signal itself"

    await bot.shutdown()                       # run()'s finally
    assert calls.counts["task_registry.shutdown"] == 1
    assert calls.counts["audio_queue.shutdown"] == 1
    assert calls.counts["memory.close"] == 1

    await bot.shutdown()                       # a second caller (e.g. main.py)
    assert calls.counts["task_registry.shutdown"] == 1, "teardown ran twice"
    assert calls.counts["audio_queue.shutdown"] == 1
    assert calls.counts["memory.close"] == 1


async def test_two_concurrent_shutdown_callers_share_one_teardown():
    calls = Calls()
    bot = _bot(calls, slow=0.05)
    done = []

    async def caller(tag):
        await bot.shutdown()
        done.append(tag)

    await asyncio.gather(caller("a"), caller("b"), caller("c"))
    assert calls.counts["task_registry.shutdown"] == 1
    assert calls.counts["audio_queue.shutdown"] == 1
    assert calls.counts["memory.close"] == 1
    assert sorted(done) == ["a", "b", "c"], "every caller must wait for the teardown"


async def test_signal_during_setup_is_honoured_by_the_loop_later():
    calls = Calls()
    bot = _bot(calls)
    bot._signal_handler(15, None)              # SIGTERM before run() exists
    assert bot.shutdown_requested and not bot.running
    # run() creates the event and must notice the earlier request
    bot._stop_event = asyncio.Event()
    if bot.shutdown_requested:
        bot._stop_event.set()
    assert bot._stop_event.is_set()


async def test_request_shutdown_is_safe_from_another_thread():
    calls = Calls()
    bot = _bot(calls)
    bot._stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    fired = threading.Event()

    def from_thread():
        bot.request_shutdown()
        fired.set()

    threading.Thread(target=from_thread, daemon=True).start()
    fired.wait(2.0)
    for _ in range(20):                        # let call_soon_threadsafe land
        if bot._stop_event.is_set():
            break
        await asyncio.sleep(0.01)
    assert bot._stop_event.is_set()
    assert loop.is_running()


async def test_memory_cleanup_uses_the_supported_close_path():
    calls = Calls()
    bot = _bot(calls)
    await bot.shutdown()
    assert calls.counts["redis.aclose"] == 1
    assert calls.counts["memory.close"] == 1
    assert "db_manager.close" not in calls.counts, "close() already handles the db"
    assert bot.memory_system.redis_client is None, "closed reference must be cleared"


async def test_memory_cleanup_falls_back_to_db_manager_close():
    calls = Calls()
    bot = _bot(calls)

    class LegacyMemory:
        redis_client = None

        def __init__(self):
            async def _close():
                calls.hit("db_manager.close")
            self.db_manager = SimpleNamespace(close=_close)
    bot.memory_system = LegacyMemory()
    await bot.shutdown()
    assert calls.counts["db_manager.close"] == 1


# ------------------------------------------------------------ audio queue

class _Stream:
    def __init__(self, calls):
        self.calls = calls

    def stop_stream(self):
        self.calls.hit("stream.stop")

    def close(self):
        self.calls.hit("stream.close")


class _PA:
    def __init__(self, calls):
        self.calls = calls

    def terminate(self):
        self.calls.hit("pyaudio.terminate")


class _Cache:
    def __init__(self, calls):
        self.calls = calls

    async def close(self):
        self.calls.hit("cache.close")

    async def get_stats(self):
        return {}


def _queue(calls):
    q = OptimizedAudioQueue(openai_api_key="test", enable_pre_buffering=False)
    q.cache = _Cache(calls)
    q.stream = _Stream(calls)
    q.pyaudio = _PA(calls)
    return q


async def test_queue_shutdown_stops_processing_before_closing_resources():
    calls = Calls()
    q = _queue(calls)

    async def processor():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            calls.hit("processor.cancelled")
            raise
    q.processing_task = asyncio.create_task(processor())
    await asyncio.sleep(0)

    await q.shutdown()
    assert calls.order.index("processor.cancelled") < calls.order.index("cache.close")
    assert calls.order.index("processor.cancelled") < calls.order.index("stream.close")
    assert calls.order[-1] == "cache.close", "the cache is the last thing to go"


async def test_queue_shutdown_is_idempotent_and_clears_references():
    calls = Calls()
    q = _queue(calls)
    q.processing_task = None

    await q.shutdown()
    await q.shutdown()
    await q.shutdown()

    assert calls.counts == {"stream.stop": 1, "stream.close": 1,
                            "pyaudio.terminate": 1, "cache.close": 1}
    assert q.stream is None and q.pyaudio is None and q.processing_task is None


async def test_queue_shutdown_survives_a_device_that_is_already_gone():
    calls = Calls()
    q = _queue(calls)
    q.processing_task = None

    class DeadStream:
        def stop_stream(self):
            raise OSError("device unplugged")

        def close(self):
            raise OSError("device unplugged")
    q.stream = DeadStream()

    with pytest.raises(RuntimeError) as excinfo:  # reported, not swallowed
        await q.shutdown()
    assert "stream" in str(excinfo.value)
    assert calls.counts["pyaudio.terminate"] == 1, "later steps still run"
    assert calls.counts["cache.close"] == 1
    assert q.stream is None


async def test_concurrent_queue_shutdowns_close_once():
    calls = Calls()
    q = _queue(calls)
    q.processing_task = None
    await asyncio.gather(q.shutdown(), q.shutdown())
    assert calls.counts["cache.close"] == 1
    assert calls.counts["pyaudio.terminate"] == 1


# --------------------------------------------------------------- TTS cache

async def test_cache_close_is_idempotent_and_clears_the_connection():
    cache = TTSCacheSQLite.__new__(TTSCacheSQLite)
    closes = []

    class Conn:
        async def close(self):
            closes.append(1)
    cache.db = Conn()

    await cache.close()
    await cache.close()
    assert closes == [1]
    assert cache.db is None


async def test_cache_close_without_a_connection_is_a_noop():
    cache = TTSCacheSQLite.__new__(TTSCacheSQLite)
    cache.db = None
    await cache.close()                         # must not raise

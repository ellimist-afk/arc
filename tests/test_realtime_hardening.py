"""Realtime hardening regressions (Bugbot F2, F3, F6, F8, F10).

No network, no audio device, no bot setup: the session's transport is a
stub, the player/mic run in fake mode, and TalkBot is built bare.
"""
import asyncio
import threading

import pytest

from bot.bot import TalkBot
from realtime.audio_router import MicCapture, Player
from realtime.session import RealtimeVoiceSession
from utils.task_registry import TaskRegistry


# =====================================================================
# F2 — TaskRegistry name collisions cancelled sibling realtime tasks
# =====================================================================

def _bare_bot():
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'TWITCH_CHANNEL': 'cassova_'}
    bot.task_registry = TaskRegistry()
    bot.realtime_backend = None
    return bot


async def _never():
    await asyncio.Event().wait()


async def test_mic_pump_cannot_cancel_the_session_supervisor():
    """The exact collision: both were created as name='realtime_session', so
    starting the pump cancelled the supervisor that had just started it."""
    bot = _bare_bot()
    factory = bot._realtime_task_factory("realtime_session")

    async def _supervise():
        await _never()

    async def _pump_mic():
        await _never()

    supervisor = factory(_supervise())
    pump = factory(_pump_mic())
    await asyncio.sleep(0)

    assert not supervisor.cancelled() and not supervisor.done(), \
        "the mic pump cancelled its own supervisor"
    assert supervisor.get_name() != pump.get_name()
    assert supervisor.get_name() == "realtime_session__supervise"
    assert pump.get_name() == "realtime_session__pump_mic"

    for t in (supervisor, pump):
        t.cancel()
    await asyncio.gather(supervisor, pump, return_exceptions=True)


async def test_backend_callbacks_cannot_cancel_the_poll_loop_or_each_other():
    bot = _bare_bot()
    factory = bot._realtime_task_factory("realtime_backend")

    async def _poll_loop():
        await _never()

    async def handle():
        await _never()

    poll = factory(_poll_loop())
    callbacks = [factory(handle()) for _ in range(5)]
    await asyncio.sleep(0)

    assert not poll.cancelled() and not poll.done(), "a callback cancelled polling"
    for cb in callbacks:
        assert not cb.cancelled(), "callbacks cancelled each other"
    assert len({t.get_name() for t in callbacks}) == 5, "callback names collided"
    assert poll.get_name() == "realtime_backend__poll_loop"

    for t in [poll, *callbacks]:
        t.cancel()
    await asyncio.gather(poll, *callbacks, return_exceptions=True)


async def test_singleton_restart_replaces_only_its_own_predecessor():
    """A stable name is deliberate: restarting the supervisor should retire
    the previous supervisor, and nothing else."""
    bot = _bare_bot()
    factory = bot._realtime_task_factory("realtime_session")

    async def _supervise():
        await _never()

    async def _pump_mic():
        await _never()

    first = factory(_supervise())
    pump = factory(_pump_mic())
    second = factory(_supervise())
    await asyncio.sleep(0)

    assert first.cancelled() or first.done()
    assert not second.cancelled() and not pump.cancelled()

    for t in (pump, second):
        t.cancel()
    await asyncio.gather(first, pump, second, return_exceptions=True)


# =====================================================================
# F3 / F10 — session health and the reconnect budget
# =====================================================================

async def _no_sleep(_seconds):
    """Skip the real exponential backoff so the retry budget is testable."""
    return None


def _session(run_once, **kw):
    s = RealtimeVoiceSession(
        model='m', voice='v', vad='server_vad',
        instructions_provider=lambda: 'x', api_key='k',
        create_task=asyncio.ensure_future, sleep=_no_sleep, **kw)
    s._run_once = run_once
    return s


async def test_continuous_failure_still_exhausts_the_budget():
    calls = []

    async def always_fails():
        calls.append(1)
        raise RuntimeError("boom")

    s = _session(always_fails, max_reconnects=2, stable_uptime_s=3600.0)
    s._create_task = asyncio.ensure_future
    await asyncio.wait_for(s._supervise(), timeout=5)
    assert s.gave_up is True
    assert len(calls) == 3            # initial + 2 reconnects
    assert s.connected is False


async def test_intermittent_drops_never_exhaust_the_budget():
    """Six drops, each after a connection that was up long enough to count as
    stable. Before the fix the sixth retired the session permanently."""
    drops = {'n': 0}

    async def drops_after_being_stable():
        drops['n'] += 1
        if drops['n'] > 6:
            raise asyncio.CancelledError
        return  # returned cleanly == the socket closed

    # stable_uptime_s=0 => every connection counts as stable, so `attempts`
    # resets each time and the budget is never consumed twice in a row.
    s = _session(drops_after_being_stable, max_reconnects=5, stable_uptime_s=0.0)
    s._create_task = asyncio.ensure_future
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(s._supervise(), timeout=5)
    assert drops['n'] == 7
    assert s.gave_up is False, "intermittent drops must not retire the session"


async def test_unstable_flapping_is_not_treated_as_recovery():
    """Reconnects that drop again immediately must still exhaust the budget."""
    async def flaps():
        raise RuntimeError("instant drop")

    s = _session(flaps, max_reconnects=2, stable_uptime_s=60.0)
    s._create_task = asyncio.ensure_future
    await asyncio.wait_for(s._supervise(), timeout=5)
    assert s.gave_up is True


async def test_start_clears_a_previous_give_up():
    async def noop():
        raise asyncio.CancelledError

    s = _session(noop)
    s.gave_up = True
    s._create_task = lambda coro: asyncio.ensure_future(coro)
    await s.start()
    assert s.gave_up is False
    await s.stop()


def test_backend_health_reflects_session_and_running_state():
    from realtime.backend import RealtimeVoiceBackend

    class StubSession:
        gave_up = False

        def __getattr__(self, name):
            return lambda *a, **k: None

    b = RealtimeVoiceBackend.__new__(RealtimeVoiceBackend)
    b.session, b._running = StubSession(), True
    assert b.healthy is True

    b.session.gave_up = True
    assert b.healthy is False, "a session that gave up is not healthy"

    b.session.gave_up = False
    b._running = False
    assert b.healthy is False, "a stopped backend is not healthy"


# =====================================================================
# F3 — an unhealthy backend restores the legacy voice path
# =====================================================================

def _voice_bot(backend):
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'BOT_NAME': 'talkbot', 'TWITCH_CHANNEL': 'cassova_'}
    bot.realtime_backend = backend
    bot.voice_commands = None
    bot.personality_engine = None
    bot.last_voice_response = None
    bot.recent_voice_texts = []
    bot.response_times = []

    class Reg:
        def __init__(self):
            self.removed = []

        def remove(self, name):
            self.removed.append(name)
            return True

    bot.service_registry = Reg()
    return bot


class FakeBackend:
    def __init__(self, healthy=True, ready=None):
        self.healthy = healthy
        self.ready = healthy if ready is None else ready
        self.transcripts = []
        self.stopped = False

    async def on_legacy_transcript(self, text):
        self.transcripts.append(text)

    async def stop(self):
        self.stopped = True


async def test_healthy_backend_keeps_owning_the_conversation():
    backend = FakeBackend(healthy=True)
    bot = _voice_bot(backend)
    await bot._handle_voice_input('hey bud you there')
    assert backend.transcripts == ['hey bud you there']
    assert bot.realtime_backend is backend
    assert bot.recent_voice_texts == [], "staged pipeline must not also run"


async def test_dead_session_hands_the_utterance_back_to_legacy():
    """Retry exhaustion mid-stream: the wake phrase must not vanish into a
    dead session -- the legacy pipeline answers this very utterance instead.

    Reaching `recent_voice_texts` is the proof: that append sits past the
    realtime gate, past the trigger match and past the mute check, i.e. inside
    the staged pipeline proper.
    """
    backend = FakeBackend(healthy=False)
    bot = _voice_bot(backend)
    bot.muted = False

    await bot._handle_voice_input('hey bot are you still there')

    assert backend.transcripts == [], "a dead session must not be fed"
    assert backend.stopped is True, "the dead backend must be stopped"
    assert bot.realtime_backend is None, "legacy routing must be restored"
    assert bot.service_registry.removed == ['RealtimeVoiceService']
    assert bot.recent_voice_texts == ['hey bot are you still there'],         "the triggering utterance must be handled by the legacy pipeline"


async def test_later_utterances_go_straight_to_legacy_without_retrying():
    """Deactivation is sticky: once realtime is retired it is not consulted
    again, so every following wake phrase takes the legacy path directly."""
    backend = FakeBackend(healthy=False)
    bot = _voice_bot(backend)
    bot.muted = False

    await bot._handle_voice_input('hey bot first one')
    backend.stopped = False              # would flip again if it were re-used
    await bot._handle_voice_input('hey bot second one')

    assert backend.transcripts == []
    assert backend.stopped is False, "the retired backend must not be touched again"
    assert bot.recent_voice_texts == ['hey bot first one', 'hey bot second one']


async def test_deactivation_is_idempotent_and_survives_a_failing_stop():
    class ExplodingBackend(FakeBackend):
        async def stop(self):
            raise RuntimeError("device already gone")

    bot = _voice_bot(ExplodingBackend(healthy=False))
    await bot._deactivate_realtime_backend()
    assert bot.realtime_backend is None, "a failing stop must still restore legacy"
    await bot._deactivate_realtime_backend()   # no-op, must not raise
    assert bot.realtime_backend is None


# =====================================================================
# F6 — late deltas must not resume cancelled speech
# =====================================================================

def _player():
    # No thread started: enqueue/hard_stop/mark_done are exercised directly.
    return Player(pa=None, device_index=None, fake=True)


def test_late_delta_after_hard_stop_is_dropped():
    p = _player()
    p.enqueue('item-1', b'\x00' * 64)
    p.current_item = 'item-1'
    p.hard_stop('item-1')
    assert len(p.q) == 0

    p.enqueue('item-1', b'\x11' * 64)      # delta already in flight
    assert len(p.q) == 0, "a stopped item must not be resumed by a late delta"


def test_stop_item_being_consumed_does_not_reopen_the_gate():
    """_stop_item is cleared as soon as the playback loop sees it; the
    cancelled set is what keeps rejecting afterwards."""
    p = _player()
    p.current_item = 'item-1'
    p.hard_stop('item-1')
    p._stop_item = None                     # what the run loop does
    p.enqueue('item-1', b'\x22' * 64)
    assert len(p.q) == 0


def test_new_item_still_plays_after_a_stop():
    p = _player()
    p.current_item = 'item-1'
    p.hard_stop('item-1')
    p.enqueue('item-2', b'\x33' * 64)
    assert len(p.q) == 1, "only the cancelled item is blocked"


def test_terminal_completion_releases_the_cancellation():
    p = _player()
    p.current_item = 'item-1'
    p.hard_stop('item-1')
    p.mark_done('item-1')                   # provider says: no more audio
    assert 'item-1' not in p._cancelled, "cancellation state must be cleaned up"


def test_cancelled_set_stays_bounded():
    p = _player()
    for i in range(p._MAX_CANCELLED * 2):
        p.hard_stop(f'item-{i}')            # provider never confirms
    assert len(p._cancelled) <= p._MAX_CANCELLED


# =====================================================================
# F8 — pre-roll must survive arm() racing the capture thread
# =====================================================================

def test_arm_while_capture_appends_does_not_raise_or_strand_chunks():
    delivered = []
    mic = MicCapture(pa=None, device_index=None, preroll_ms=2000, loop=None,
                     on_chunk=delivered.append, fake=True)
    errors = []
    stop = threading.Event()

    def capture():
        try:
            while not stop.is_set():
                with mic._ring_lock:
                    live = mic.armed
                    if not live:
                        mic.ring.append(b'\x01' * 64)
                if live:
                    mic._deliver(b'\x01' * 64)
        except Exception as e:  # RuntimeError: deque mutated during iteration
            errors.append(e)

    t = threading.Thread(target=capture, daemon=True)
    t.start()
    try:
        for _ in range(300):
            mic.arm()
            mic.disarm()
    finally:
        stop.set()
        t.join(timeout=5)

    assert errors == [], f"capture thread raised: {errors[:1]}"
    assert mic.armed is False


def test_arm_flushes_preroll_in_order_exactly_once():
    delivered = []
    mic = MicCapture(pa=None, device_index=None, preroll_ms=2000, loop=None,
                     on_chunk=delivered.append, fake=True)
    for i in range(4):
        mic.ring.append(bytes([i]) * 8)

    flushed_ms = mic.arm()
    assert flushed_ms > 0
    assert delivered == [b''.join(bytes([i]) * 8 for i in range(4))]
    assert len(mic.ring) == 0

    delivered.clear()
    mic.disarm()
    mic.arm()
    assert delivered == [], "an empty ring must not re-deliver anything"

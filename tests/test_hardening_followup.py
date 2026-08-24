"""Hardening follow-up regressions #2-#5.

#2 ready vs healthy: a reconnecting Realtime session is kept, but the wake
   phrase it cannot currently send is answered by the legacy pipeline.
#3 reconnect budget resets only after a connection that completed its
   handshake stayed up; a slow *failed* connect must still count.
#4 cancellation memory evicts the OLDEST id when full, never a recent one.
#5 the summarizer commits exactly the batch it folded, even when the backlog
   cap trims the pending list while the LLM call is in flight.
"""
import asyncio
import time

import pytest

from bot.bot import TalkBot
from bot.channel_chat_buffer import ChannelChatBuffer
from bot.session_summarizer import StreamSessionSummarizer
from realtime.audio_router import Player
from realtime.backend import RealtimeVoiceBackend
from realtime.session import RealtimeVoiceSession


# =====================================================================
# #2 — ready vs healthy
# =====================================================================

def _backend(running=True, gave_up=False, connected=False):
    class StubSession:
        pass
    s = StubSession()
    s.gave_up, s.connected = gave_up, connected
    b = RealtimeVoiceBackend.__new__(RealtimeVoiceBackend)
    b.session, b._running = s, running
    return b


def test_reconnecting_backend_is_healthy_but_not_ready():
    b = _backend(running=True, gave_up=False, connected=False)
    assert b.healthy is True, "supervision is still alive: keep it"
    assert b.ready is False, "but it cannot send a turn right now"


def test_connected_backend_is_ready():
    assert _backend(connected=True).ready is True


def test_gave_up_or_stopped_is_neither():
    assert _backend(gave_up=True, connected=True).ready is False
    assert _backend(running=False, connected=True).ready is False


class _VoiceBackend:
    def __init__(self, healthy, ready):
        self.healthy, self.ready = healthy, ready
        self.transcripts, self.stopped = [], False

    async def on_legacy_transcript(self, text):
        self.transcripts.append(text)

    async def stop(self):
        self.stopped = True


def _voice_bot(backend):
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'BOT_NAME': 'talkbot', 'TWITCH_CHANNEL': 'cassova_'}
    bot.realtime_backend = backend
    bot.voice_commands = None
    bot.personality_engine = None
    bot.last_voice_response = None
    bot.recent_voice_texts = []
    bot.response_times = []
    bot.muted = False
    bot.service_registry = type("Reg", (), {"remove": lambda self, n: True})()
    return bot


async def test_transient_disconnect_routes_this_utterance_to_legacy_and_keeps_backend():
    backend = _VoiceBackend(healthy=True, ready=False)
    bot = _voice_bot(backend)

    await bot._handle_voice_input('hey bot are you there')

    assert backend.transcripts == [], "a disconnected session cannot take the turn"
    assert bot.recent_voice_texts == ['hey bot are you there'], \
        "the utterance must be answered by the legacy pipeline"
    assert backend.stopped is False, "transient: the backend is NOT retired"
    assert bot.realtime_backend is backend


async def test_backend_resumes_ownership_once_reconnected():
    backend = _VoiceBackend(healthy=True, ready=False)
    bot = _voice_bot(backend)
    await bot._handle_voice_input('hey bot first')
    backend.ready = True                          # supervisor reconnected
    await bot._handle_voice_input('hey bot second')
    assert backend.transcripts == ['hey bot second']
    assert bot.recent_voice_texts == ['hey bot first']


async def test_exhausted_backend_is_still_retired():
    backend = _VoiceBackend(healthy=False, ready=False)
    bot = _voice_bot(backend)
    await bot._handle_voice_input('hey bot gone')
    assert backend.stopped is True
    assert bot.realtime_backend is None
    assert bot.recent_voice_texts == ['hey bot gone']


# =====================================================================
# #3 — uptime is measured from the handshake, not the attempt
# =====================================================================

async def _no_sleep(_s):
    return None


def _session(run_once, **kw):
    s = RealtimeVoiceSession(model='m', voice='v', vad='server_vad',
                             instructions_provider=lambda: 'x', api_key='k',
                             create_task=asyncio.ensure_future, sleep=_no_sleep, **kw)
    s._run_once = run_once
    return s


async def test_slow_failed_connects_still_exhaust_the_budget():
    """Each attempt takes longer than stable_uptime_s but never completes
    the handshake. Measured from the attempt start that looked 'stable' and
    reset the budget forever; measured from the handshake it is nothing."""
    calls = []

    async def slow_then_fail():
        calls.append(1)
        await asyncio.sleep(0.03)                 # > stable_uptime_s
        raise RuntimeError("handshake never completed")

    s = _session(slow_then_fail, max_reconnects=2, stable_uptime_s=0.01)
    await asyncio.wait_for(s._supervise(), timeout=5)
    assert s.gave_up is True
    assert len(calls) == 3                        # initial + 2 reconnects


async def test_a_real_stable_connection_resets_the_budget():
    calls = []

    async def connects_then_drops(self=None):
        calls.append(1)
        if len(calls) > 8:
            raise asyncio.CancelledError
        s._connected_at = time.monotonic()        # handshake succeeded
        await asyncio.sleep(0.02)                 # stayed up > stable_uptime_s
        return

    s = _session(connects_then_drops, max_reconnects=2, stable_uptime_s=0.01)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(s._supervise(), timeout=5)
    assert len(calls) == 9
    assert s.gave_up is False


async def test_connected_at_is_cleared_between_attempts():
    async def handshake_then_fail():
        s._connected_at = time.monotonic()
        raise RuntimeError("dropped right after handshake")

    s = _session(handshake_then_fail, max_reconnects=1, stable_uptime_s=60.0)
    await asyncio.wait_for(s._supervise(), timeout=5)
    assert s._connected_at is None
    assert s.gave_up is True


# =====================================================================
# #4 — ordered cancellation memory
# =====================================================================

def _player(cap):
    p = Player(pa=None, device_index=None, fake=True)
    p._MAX_CANCELLED = cap
    return p


def test_oldest_cancellation_is_evicted_first():
    p = _player(cap=2)
    for item in ("a", "b", "c"):
        p.hard_stop(item)
    assert list(p._cancelled) == ["b", "c"]

    p.enqueue("c", b"\x00" * 8)                   # recent: still rejected
    p.enqueue("b", b"\x00" * 8)
    assert len(p.q) == 0
    p.enqueue("a", b"\x00" * 8)                   # evicted: accepted again
    assert [i for i, _ in p.q] == ["a"]


def test_recancelling_refreshes_recency():
    p = _player(cap=2)
    p.hard_stop("a")
    p.hard_stop("b")
    p.hard_stop("a")                              # a is now the most recent
    p.hard_stop("c")                              # evicts b, not a
    assert list(p._cancelled) == ["a", "c"]


def test_eviction_is_exactly_one_at_a_time():
    p = _player(cap=3)
    for i in range(10):
        p.hard_stop(f"i{i}")
    assert list(p._cancelled) == ["i7", "i8", "i9"]


def test_terminal_done_removes_without_disturbing_order():
    p = _player(cap=5)
    for item in ("a", "b", "c"):
        p.hard_stop(item)
    p.mark_done("b")
    assert list(p._cancelled) == ["a", "c"]


# =====================================================================
# #5 — in-flight batch is separate from newly pending turns
# =====================================================================

CH = "cassova_"


class FakeLLM:
    def __init__(self, reply="summary"):
        self.reply = reply
        self.calls = []
        self.block = None

    async def __call__(self, messages):
        self.calls.append(messages)
        if self.block is not None:
            await self.block.wait()
        return self.reply


def _summarizer(**kw):
    buf = ChannelChatBuffer(max_turns_per_channel=200)
    llm = FakeLLM()
    defaults = dict(turns_per_update=5, min_turns=2, max_interval_s=600,
                    max_words=50, max_chars=400, max_pending_turns=20)
    defaults.update(kw)
    s = StreamSessionSummarizer(buf, llm, bot_name="elimist_", clock=lambda: 1000.0, **defaults)
    s.get_summary(CH)
    return s, buf, llm


def _seqs(turns):
    return [t["seq"] for t in turns]


async def test_cap_trim_during_fold_does_not_corrupt_the_commit():
    s, b, llm = _summarizer()
    llm.block = asyncio.Event()
    for i in range(5):
        b.append_viewer(CH, "v", f"m{i}")
    assert s.should_update(CH)
    task = asyncio.create_task(s.update(CH))
    await asyncio.sleep(0)
    st = s._state(CH)
    assert _seqs(st.in_flight_turns) == [1, 2, 3, 4, 5]
    assert st.pending_turns == []

    # 30 arrive mid-call: the 20-turn cap trims the FRONT of pending_turns
    for i in range(5, 35):
        b.append_viewer(CH, "v", f"m{i}")
        s.should_update(CH)
    assert len(st.pending_turns) == 20
    assert _seqs(st.pending_turns)[0] == 16          # oldest 10 new ones trimmed
    assert s.stats(CH)["unsummarized_turns"] == 25    # 5 in flight + 20 pending

    llm.block.set()
    assert await task is True

    # Before: `del pending[:5]` here would have deleted seqs 16..20 -- turns
    # that were never summarized -- while the folded batch stayed committed.
    assert s.stats(CH)["watermark"] == 5
    assert st.in_flight_turns == []
    assert _seqs(st.pending_turns) == list(range(16, 36))
    folded = llm.calls[-1][1]["content"]
    assert "m4" in folded and "m5" not in folded


async def test_failed_fold_restores_its_batch_in_front_without_duplicates():
    s, b, llm = _summarizer(max_pending_turns=8)
    llm.block = asyncio.Event()
    llm.reply = ""                                   # this fold will fail
    for i in range(5):
        b.append_viewer(CH, "v", f"m{i}")
    s.should_update(CH)
    task = asyncio.create_task(s.update(CH))
    await asyncio.sleep(0)
    for i in range(5, 10):                           # 5 more mid-call
        b.append_viewer(CH, "v", f"m{i}")
        s.should_update(CH)
    llm.block.set()
    assert await task is False

    st = s._state(CH)
    assert st.in_flight_turns == []
    seqs = _seqs(st.pending_turns)
    assert len(seqs) == len(set(seqs)), "no duplicates after restore"
    assert len(seqs) == 8, "cap re-applied after restore"
    assert seqs == list(range(3, 11)), "oldest dropped, newest kept, order intact"
    assert s.stats(CH)["watermark"] == 0


async def test_exception_during_fold_restores_batch_and_events():
    s, b, llm = _summarizer()

    async def boom(messages):
        raise RuntimeError("llm down")
    s.llm_call = boom
    for i in range(3):
        b.append_viewer(CH, "v", f"m{i}")
    s.note_event(CH, "raid from someone")
    s.should_update(CH)
    assert await s.update(CH) is False
    st = s._state(CH)
    assert _seqs(st.pending_turns) == [1, 2, 3]
    assert st.pending_events == ["raid from someone"]
    assert st.in_flight_turns == [] and st.in_flight_events == []


async def test_events_noted_during_fold_survive_a_success():
    s, b, llm = _summarizer()
    llm.block = asyncio.Event()
    for i in range(5):
        b.append_viewer(CH, "v", f"m{i}")
    s.note_event(CH, "first event")
    s.should_update(CH)
    task = asyncio.create_task(s.update(CH))
    await asyncio.sleep(0)
    s.note_event(CH, "second event")                 # arrives mid-call
    llm.block.set()
    await task
    st = s._state(CH)
    assert st.pending_events == ["second event"], "only the folded event is committed"
    assert "first event" in llm.calls[-1][1]["content"]

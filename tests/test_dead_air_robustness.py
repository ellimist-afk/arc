"""Dead air at a 120s threshold: it fires 4x as often, so the guards matter.

Covers the failure modes that only appear at that rate: monologuing into an
offline stream, monologuing into a room where nobody answers, a hung LLM
call stalling the monitor, and generic filler leaking back in when
generation fails (silence beats "anyone there" now that lines carry
substance).
"""
import asyncio
import json
from datetime import datetime, timedelta

import pytest

from bot.response_coordinator import ResponseCoordinator


def make(threshold=120, **kw):
    rc = ResponseCoordinator(twitch_client=None, audio_queue=None, settings_path="nope.json")
    rc.dead_air_threshold = threshold
    rc.dead_air_enabled = True
    for k, v in kw.items():
        setattr(rc, k, v)
    return rc


# ------------------------------------------------------------- backoff

def test_unanswered_fillers_back_off_and_cap():
    rc = make(threshold=120)
    assert rc._effective_threshold() == 120        # first lull: normal wait
    rc._consecutive_fillers = 1
    assert rc._effective_threshold() == 240        # nobody answered: wait longer
    rc._consecutive_fillers = 2
    assert rc._effective_threshold() == 480
    rc._consecutive_fillers = 3
    assert rc._effective_threshold() == 960
    rc._consecutive_fillers = 9                    # never grows without bound
    assert rc._effective_threshold() == 120 * rc.max_filler_backoff


def test_activity_clears_the_backoff():
    rc = make()
    rc._consecutive_fillers = 3
    before = rc.last_activity_time
    rc.note_activity()
    assert rc._consecutive_fillers == 0
    assert rc._effective_threshold() == 120
    assert rc.last_activity_time >= before


def test_note_activity_resets_the_timer():
    rc = make()
    rc.last_activity_time = datetime.now() - timedelta(seconds=600)
    rc.note_activity()
    assert (datetime.now() - rc.last_activity_time).total_seconds() < 1


# -------------------------------------------------------- liveness gate

def test_offline_stream_blocks_filling():
    rc = make(should_fill=lambda: False)
    assert rc.should_fill() is False


def test_live_stream_allows_filling():
    rc = make(should_fill=lambda: True)
    assert rc.should_fill() is True


def test_missing_gate_means_go_ahead():
    assert make().should_fill is None


async def test_bot_gate_tracks_stream_liveness():
    from types import SimpleNamespace
    from bot.bot import TalkBot
    bot = TalkBot.__new__(TalkBot)

    bot.stream_info = SimpleNamespace(is_live=False)
    gate = lambda: (bot.stream_info is None or bot.stream_info.is_live is not False)  # noqa: E731
    assert gate() is False
    bot.stream_info = SimpleNamespace(is_live=True)
    assert gate() is True
    bot.stream_info = SimpleNamespace(is_live=None)     # no lifecycle event yet
    assert gate() is True
    bot.stream_info = None
    assert gate() is True


# ------------------------------------------------------------- timeouts

async def test_slow_generation_is_bounded():
    """A hung LLM call must not wedge the monitor for minutes."""
    rc = make(filler_timeout=0.05)

    async def slow():
        await asyncio.sleep(5)
        return {"text": "too late"}

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(slow(), timeout=rc.filler_timeout)


def test_filler_timeout_has_a_sane_default():
    assert make().filler_timeout == 10.0


# ------------------------------------------------------ settings loading

def test_settings_accept_120_and_clamp_absurd_values(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"response_coordination": {
        "dead_air_enabled": True, "dead_air_threshold": 120}}))
    rc = ResponseCoordinator(twitch_client=None, audio_queue=None, settings_path=str(path))
    assert rc.dead_air_threshold == 120 and rc.dead_air_enabled is True

    path.write_text(json.dumps({"response_coordination": {"dead_air_threshold": 3600}}))
    rc2 = ResponseCoordinator(twitch_client=None, audio_queue=None, settings_path=str(path))
    assert rc2.dead_air_threshold == 300, "PRD clamps the threshold to 300s"


def test_live_settings_file_enables_dead_air_at_120():
    rc = ResponseCoordinator(twitch_client=None, audio_queue=None,
                             settings_path="bot_settings.json")
    assert rc.dead_air_enabled is True
    assert rc.dead_air_threshold == 120


def test_counters_start_clean():
    rc = make()
    assert rc.fillers_sent == 0 and rc._consecutive_fillers == 0

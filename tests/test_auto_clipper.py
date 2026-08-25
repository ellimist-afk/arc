"""AutoClipper policy and the bot's burst-to-clip wiring."""
import asyncio
import json
from types import SimpleNamespace

from bot.bot import TalkBot
from features.auto_clipper import AutoClipper


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- policy

def test_burst_clips_once_then_cooldown_holds():
    c = Clock()
    a = AutoClipper(cooldown_s=180, clock=c)
    assert a.should_clip(True)
    a.mark_triggered()
    for _ in range(10):                    # the same burst keeps signalling
        c.t += 5
        assert not a.should_clip(True)
    assert a.bursts_suppressed == 10
    c.t += 200                             # cooldown over, next hype moment
    assert a.should_clip(True)


def test_no_burst_no_clip():
    a = AutoClipper(clock=Clock())
    assert not a.should_clip(False)
    assert a.bursts_suppressed == 0        # quiet chat is not "suppressed"


def test_disabled_never_clips():
    a = AutoClipper(enabled=False, clock=Clock())
    assert not a.should_clip(True)


def test_from_settings_reads_block_and_tolerates_missing(tmp_path):
    p = tmp_path / "bot_settings.json"
    p.write_text(json.dumps({"auto_clip": {"enabled": False, "cooldown_s": 60}}))
    a = AutoClipper.from_settings(str(p))
    assert a.enabled is False and a.cooldown_s == 60.0
    b = AutoClipper.from_settings(str(tmp_path / "nope.json"))
    assert b.enabled is True and b.cooldown_s == 180.0


def test_stats_shape():
    a = AutoClipper(clock=Clock())
    a.mark_triggered()
    assert a.stats() == {"enabled": True, "clips_triggered": 1, "bursts_suppressed": 0}


# ---------------------------------------------------------------- wiring

def _bot(is_live=True):
    bot = TalkBot.__new__(TalkBot)
    bot.stream_info = SimpleNamespace(is_live=is_live)
    bot.auto_clipper = AutoClipper(clock=Clock())
    bot.clip_calls = []

    async def create_clip(requested_by=""):
        bot.clip_calls.append(requested_by)
        return {"id": "x", "url": "https://clips.twitch.tv/x"}
    bot._create_clip = create_clip
    return bot


async def test_auto_clip_calls_create_clip_when_live():
    bot = _bot(is_live=True)
    await bot._auto_clip()
    assert bot.clip_calls == ["chat hype"]


async def test_auto_clip_skips_when_stream_known_offline():
    bot = _bot(is_live=False)
    await bot._auto_clip()
    assert bot.clip_calls == []


async def test_auto_clip_attempts_when_liveness_unknown():
    # is_live None = no lifecycle event yet; Helix fails harmlessly if offline
    bot = _bot(is_live=None)
    await bot._auto_clip()
    assert bot.clip_calls == ["chat hype"]

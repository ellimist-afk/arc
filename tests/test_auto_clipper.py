"""AutoClipper policy and the bot's burst-to-clip wiring."""
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
    assert a.stats() == {"enabled": True, "clips_triggered": 1,
                         "moment_clips": 0, "bursts_suppressed": 0,
                         "moments_suppressed": 0}


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


# ------------------------------ vision moments share the cooldown, not the tally

def _clock():
    t = {"now": 1000.0}
    return t, (lambda: t["now"])


def test_a_moment_and_a_burst_cannot_double_clip():
    """A death during a hype burst is one moment, not two clips."""
    t, clock = _clock()
    c = AutoClipper(enabled=True, cooldown_s=180.0, clock=clock,
                    clip_notable_moments=True)
    assert c.should_clip_moment() is True
    c.mark_triggered(source="moment")
    assert c.should_clip(True) is False, "the burst must see the moment's cooldown"


def test_a_burst_blocks_a_following_moment_too():
    t, clock = _clock()
    c = AutoClipper(enabled=True, cooldown_s=180.0, clock=clock,
                    clip_notable_moments=True)
    assert c.should_clip(True) is True
    c.mark_triggered()
    assert c.should_clip_moment() is False


def test_the_cooldown_expires_for_moments():
    t, clock = _clock()
    c = AutoClipper(enabled=True, cooldown_s=180.0, clock=clock,
                    clip_notable_moments=True)
    c.mark_triggered(source="moment")
    t["now"] += 181
    assert c.should_clip_moment() is True


def test_a_held_moment_is_not_counted_as_a_held_burst():
    """The recap said "N burst signals held by cooldown"; a suppressed
    vision moment inflating that number would make the report lie."""
    t, clock = _clock()
    c = AutoClipper(enabled=True, cooldown_s=180.0, clock=clock,
                    clip_notable_moments=True)
    c.mark_triggered()
    c.should_clip_moment()
    s = c.stats()
    assert s["moments_suppressed"] == 1
    assert s["bursts_suppressed"] == 0


def test_a_held_burst_is_not_counted_as_a_held_moment():
    t, clock = _clock()
    c = AutoClipper(enabled=True, cooldown_s=180.0, clock=clock)
    c.mark_triggered(source="moment")
    c.should_clip(True)
    s = c.stats()
    assert s["bursts_suppressed"] == 1
    assert s["moments_suppressed"] == 0


def test_moment_clips_are_tallied_separately():
    t, clock = _clock()
    c = AutoClipper(enabled=True, cooldown_s=0.0, clock=clock)
    c.mark_triggered(source="moment")
    c.mark_triggered()
    s = c.stats()
    assert s["clips_triggered"] == 2, "both count toward the total"
    assert s["moment_clips"] == 1, "only one came from the screen"


def test_moments_can_be_opted_out_without_disabling_bursts():
    c = AutoClipper(enabled=True, clip_notable_moments=False)
    assert c.should_clip_moment() is False
    assert c.should_clip(True) is True


def test_disabling_the_clipper_disables_both():
    c = AutoClipper(enabled=False)
    assert c.should_clip_moment() is False
    assert c.should_clip(True) is False


def test_the_bot_uses_the_moment_path():
    from pathlib import Path
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    fn = bot.split("async def _clip_notable_moment")[1].split("\n    async def ")[0]
    assert "should_clip_moment()" in fn
    assert 'mark_triggered(source="moment")' in fn


# ------------------------------------------ clipping the screen is opt-in

def test_screen_moments_do_not_clip_by_default():
    """Live 2026-09-02: three clips in eight minutes, all from this source,
    each one the instant the cooldown expired. Every kill cam and victory
    screen clears the vision NOTABLE bar, so leaving this on turns the
    cooldown into a metronome."""
    c = AutoClipper(enabled=True)
    assert c.should_clip_moment() is False
    assert c.should_clip(True) is True, "chat bursts still clip"


def test_a_held_default_moment_is_not_counted_as_suppressed():
    """Off is not the same as held: an opted-out source never competed for
    the cooldown, and the recap should not imply it did."""
    c = AutoClipper(enabled=True)
    c.should_clip_moment()
    assert c.stats()["moments_suppressed"] == 0


def test_settings_can_opt_back_in(tmp_path):
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"auto_clip": {"clip_notable_moments": True}}),
                 encoding="utf-8")
    assert AutoClipper.from_settings(str(p)).clip_notable_moments is True


def test_settings_default_to_off(tmp_path):
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"auto_clip": {"enabled": True}}), encoding="utf-8")
    assert AutoClipper.from_settings(str(p)).clip_notable_moments is False


# ------------------------------- the clip policy is tunable without a restart

def test_clip_settings_reload_without_a_restart():
    """A bot clipping too often is a problem you have RIGHT NOW. Before this,
    auto_clip was read once at setup, so calming it down cost a restart --
    and the restart costs the moment you were trying to keep."""
    from pathlib import Path
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    fn = bot.split("async def _reload_settings")[1].split("\n    async def ")[0]
    assert "'auto_clip' in bot_settings" in fn
    for field in ("clip_notable_moments", "cooldown_s", "enabled"):
        assert field in fn, f"{field} must be live-tunable"


def test_the_reload_does_not_reset_the_cooldown_or_the_counters():
    """Rebuilding the clipper on every settings save would clear the night's
    tallies -- which the recap prints -- and hand a fresh cooldown to the
    next burst."""
    from pathlib import Path
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    fn = bot.split("async def _reload_settings")[1].split("\n    async def ")[0]
    block = fn.split("'auto_clip' in bot_settings")[1]
    assert "AutoClipper(" not in block, "mutate in place, do not rebuild"
    assert "from_settings(" not in block

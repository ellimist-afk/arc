"""Vision: the co-host can see the game, and a look never blocks a reply.

Before this the bot could only riff on chat -- it had no idea the streamer
had just died to the same boss nine times. A background loop caches one
factual sentence about the screen; the reply path reads that cache only.
A slow, failed or missing look must degrade to "no screen info", never to a
delayed reply and never to a crash.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace


from features.screen_watcher import ScreenWatcher

SRC = Path("src/features/screen_watcher.py").read_text(encoding="utf-8")


def _reply(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _watcher(text="Overwatch, on the death screen.", **kw):
    seen = {}

    class Client:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    seen.update(kwargs)
                    return _reply(text)
    w = ScreenWatcher(openai_client=Client(), enabled=True, **kw)
    w.frame = bytes(1024)                      # a flat grey screen
    w._grab = lambda: ("ZmFrZQ==", w.frame)
    w.seen = seen
    return w


def _frame(value):
    """A uniform signature, so differences are exactly predictable."""
    return bytes([value]) * 1024


# --------------------------------------------------------------- defaults

def test_it_is_off_unless_switched_on():
    assert ScreenWatcher().enabled is False, "screenshots leave the machine; opt in"


def test_settings_default_to_disabled():
    cfg = json.loads(Path("bot_settings.json").read_text(encoding="utf-8"))
    assert cfg['screen_awareness']['enabled'] is False


def test_the_interval_has_a_floor():
    """Every look is an API call; a 1s interval would be a billing accident."""
    assert ScreenWatcher(interval_s=0.1).interval_s >= 15.0


def test_from_settings_reads_the_block():
    w = ScreenWatcher.from_settings(
        {'screen_awareness': {'enabled': True, 'interval_seconds': 90,
                              'bbox': [0, 0, 1920, 1080], 'max_edge': 512}},
        openai_client=object(), model='gpt-5.5')
    assert w.enabled and w.interval_s == 90
    assert w.bbox == (0, 0, 1920, 1080) and w.max_edge == 512


def test_from_settings_survives_a_missing_block():
    w = ScreenWatcher.from_settings({}, openai_client=object(), model='gpt-5.5')
    assert w.enabled is False and w.bbox is None


def test_a_malformed_bbox_is_ignored_not_crashed():
    w = ScreenWatcher.from_settings(
        {'screen_awareness': {'bbox': [1, 2]}}, openai_client=object(), model='m')
    assert w.bbox is None


# ------------------------------------------------------------- looking

async def test_a_look_caches_a_description():
    w = _watcher()
    assert await w.look() == "Overwatch, on the death screen."
    assert w.describe() == "Overwatch, on the death screen."
    assert w.looks == 1 and w.failures == 0


async def test_the_model_gets_an_effort_it_accepts():
    w = _watcher(model='gpt-5.5')
    await w.look()
    assert w.seen['reasoning_effort'] == 'none'
    w2 = _watcher(model='gpt-5-mini')
    await w2.look()
    assert w2.seen['reasoning_effort'] == 'minimal'


async def test_a_failed_look_keeps_the_previous_description():
    w = _watcher()
    await w.look()

    class Broken:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise RuntimeError("vision api down")
    w.openai_client = Broken()
    w.frame = bytes([200]) * 1024        # force a real call: the scene changed
    assert await w.look() is None
    assert w.describe() == "Overwatch, on the death screen.", "stale beats nothing"
    assert w.failures == 1


async def test_a_timeout_is_survived():
    w = _watcher()
    w.timeout_s = 0.01

    class Slow:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    await asyncio.sleep(1)
    w.openai_client = Slow()
    assert await w.look() is None
    assert w.failures == 1


async def test_an_empty_reply_is_a_failure_not_a_description():
    w = _watcher(text="   ")
    assert await w.look() is None
    assert w.describe() is None


async def test_a_failed_capture_never_calls_the_api():
    w = _watcher()
    w._grab = lambda: None
    assert await w.look() is None
    assert w.seen == {}, "no screenshot, no API call"


async def test_disabled_watchers_do_nothing():
    w = _watcher()
    w.enabled = False
    assert await w.look() is None
    assert w.seen == {}


# ------------------------------------------------------------- staleness

async def test_a_stale_description_is_withheld():
    """Narrating a game that ended ten minutes ago is worse than silence."""
    w = _watcher(interval_s=15)
    await w.look()
    w._seen_at -= 15 * 3 + 1
    assert w.describe() is None
    assert w.age_s() > 45


def test_describe_is_safe_before_the_first_look():
    w = ScreenWatcher()
    assert w.describe() is None and w.age_s() is None


# ------------------------------------------------ never blocks the reply

def test_capture_runs_off_the_event_loop():
    assert "asyncio.to_thread(self._grab)" in SRC, \
        "ImageGrab is blocking GDI work; it must not run on the loop"


def test_the_reply_path_only_reads_the_cache():
    """describe() must be a plain accessor -- no awaits, no captures."""
    body = SRC.split("def describe(")[1].split("def age_s")[0]
    assert "await" not in body and "grab" not in body


def test_the_loop_is_registry_tracked():
    assert 'create_task(self._loop(), name="screen_watcher")' in SRC


# ---------------------------------------------------------- bot wiring

def test_both_context_paths_carry_what_is_on_screen():
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    builder = Path("src/bot/optimized_context_builder.py").read_text(encoding="utf-8")
    assert "context['on_screen'] = seen" in bot, "dead-air fillers can see"
    assert '"on_screen": self._on_screen()' in builder, "replies can see"


def test_the_prompt_renders_it():
    eng = Path("src/personality/personality_engine.py").read_text(encoding="utf-8")
    assert "On screen right now:" in eng


def test_the_watcher_is_stopped_on_shutdown():
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    assert 'step("screen_watcher", self.screen_watcher.stop)' in bot


def test_vision_failure_never_breaks_startup():
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    setup = bot.split("from features.screen_watcher import ScreenWatcher")[1][:800]
    assert "except Exception" in setup and "self.screen_watcher = None" in setup


async def test_stop_is_safe_when_never_started():
    await ScreenWatcher().stop()


def test_privacy_is_documented():
    assert "PRIVACY" in SRC and "OpenAI API" in SRC


# ------------------------------------- an unchanged screen is a free look

async def test_an_unchanged_screen_costs_no_api_call():
    w = _watcher()
    await w.look()
    calls_before = dict(w.seen)
    w.seen.clear()
    assert await w.look() is not None, "the cached description is still true"
    assert w.seen == {}, "an identical screen must not be re-described"
    assert w.unchanged_skips == 1 and w.looks == 1
    assert calls_before, "the first look did call the API"


async def test_an_unchanged_screen_stays_fresh():
    """Skipping the call must not let the description rot into staleness."""
    w = _watcher(interval_s=15)
    await w.look()
    w._seen_at -= 40                      # nearly stale
    await w.look()                        # unchanged -> refreshed, no call
    assert w.describe() is not None


async def test_a_changed_screen_is_re_described():
    w = _watcher()
    await w.look()
    w.seen.clear()
    w.frame = _frame(200)                 # the scene changed completely
    assert await w.look() is not None
    assert w.seen, "a different screen must be sent to the model"
    assert w.looks == 2


async def test_the_threshold_separates_noise_from_change():
    w = _watcher()
    w.frame = _frame(100)
    await w.look()
    w.seen.clear()
    w.frame = _frame(103)                 # diff 3 -> below change_threshold 6
    await w.look()
    assert w.seen == {}, "small movement is not a new scene"
    w.frame = _frame(140)                 # diff 40 -> clearly different
    await w.look()
    assert w.seen


def test_difference_math():
    assert ScreenWatcher._difference(_frame(10), _frame(10)) == 0
    assert ScreenWatcher._difference(_frame(10), _frame(30)) == 20
    assert ScreenWatcher._difference(None, _frame(10)) == 255.0
    assert ScreenWatcher._difference(b'ab', b'abc') == 255.0, "size mismatch is total"


# --------------------------------------------------------- the scene clock

async def test_a_held_scene_is_reported_with_its_duration():
    w = _watcher()
    await w.look()
    w._scene_since -= 700                 # ~12 minutes on this screen
    out = w.describe_with_duration()
    assert "unchanged for about 12 minutes" in out


async def test_a_fresh_scene_carries_no_duration_note():
    w = _watcher()
    await w.look()
    assert w.describe_with_duration() == w.describe()


async def test_a_new_scene_restarts_the_clock():
    w = _watcher()
    await w.look()
    w._scene_since -= 700
    w.frame = _frame(240)                 # a different scene entirely
    await w.look()
    assert (w.scene_age_s() or 0) < 5, "a new scene starts a new clock"
    assert "unchanged for" not in (w.describe_with_duration() or "")


async def test_no_duration_without_a_description():
    w = ScreenWatcher()
    assert w.describe_with_duration() is None
    assert w.scene_age_s() is None


def test_the_contexts_use_the_duration_aware_view():
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    builder = Path("src/bot/optimized_context_builder.py").read_text(encoding="utf-8")
    assert "watcher.describe_with_duration()" in bot
    assert "self.screen_watcher.describe_with_duration()" in builder



# ---------------------------------------- noticing that something happened

def _notable_watcher(text, **kw):
    w = _watcher(text=text, **kw)
    w.said = []

    async def on_notable(what):
        w.said.append(what)
    w.on_notable = on_notable
    return w


async def test_the_second_look_asks_what_changed():
    """Pixel diffs cannot tell a camera pan from a death, so the model is
    given its own previous answer and asked to judge."""
    w = _watcher()
    await w.look()
    w.frame = _frame(200)
    await w.look()
    sent = w.seen['messages'][0]['content'][0]['text']
    assert "Last time you looked" in sent
    assert "Overwatch, on the death screen." in sent
    assert "NOT notable" in sent


async def test_the_first_look_has_nothing_to_compare_against():
    w = _watcher()
    await w.look()
    sent = w.seen['messages'][0]['content'][0]['text']
    assert "Last time you looked" not in sent


async def test_a_notable_moment_is_announced():
    w = _notable_watcher("NOTABLE: the run ended on the final boss")
    await w.look()
    assert w.said == ["the run ended on the final boss"]
    assert w.notables == 1


async def test_the_prefix_is_stripped_from_the_description():
    w = _notable_watcher("NOTABLE: the run ended on the final boss")
    await w.look()
    assert w.describe() == "the run ended on the final boss", "no marker in context"


async def test_ordinary_play_announces_nothing():
    w = _notable_watcher("Overwatch, walking toward the objective")
    await w.look()
    assert w.said == [] and w.notables == 0


async def test_reactions_are_rate_limited():
    """A chaotic game must not have the co-host narrating every fight."""
    w = _notable_watcher("NOTABLE: died again", notable_cooldown_s=300)
    await w.look()
    w.frame = _frame(90)
    await w.look()
    w.frame = _frame(170)
    await w.look()
    assert len(w.said) == 1, "the cooldown holds the rest"
    assert w.notables_suppressed == 2


async def test_the_cooldown_expires():
    w = _notable_watcher("NOTABLE: died again", notable_cooldown_s=300)
    await w.look()
    w._last_notable_at -= 301
    w.frame = _frame(90)
    await w.look()
    assert len(w.said) == 2


async def test_a_failing_reaction_never_breaks_the_loop():
    w = _watcher(text="NOTABLE: something happened")

    async def boom(what):
        raise RuntimeError("chat is down")
    w.on_notable = boom
    assert await w.look() is not None, "the description still lands"


async def test_a_bare_notable_prefix_is_not_a_description():
    w = _notable_watcher("NOTABLE:")
    assert await w.look() is None
    assert w.describe() is None


async def test_no_callback_means_no_crash():
    w = _watcher(text="NOTABLE: the boss died")
    assert await w.look() is not None


def test_the_bot_reacts_as_an_unsolicited_line():
    """It must inherit the topic guard and be droppable -- nobody asked."""
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    fn = bot.split("async def _react_to_screen")[1].split("def _dead_air_context")[0]
    assert "is_mention=False" in fn
    assert "Screen reaction suppressed by the guards" in fn
    assert "self.last_bot_message_at = datetime.now()" in fn, \
        "it must respect the anti-stacking breather"
    assert "self.screen_watcher.on_notable = self._react_to_screen" in bot


def test_a_flagged_moment_bypasses_the_chattiness_roll():
    """Without this the vision loop flags a death and the 3% interjection
    dice throw the reaction away -- the whole point of noticing it."""
    eng = Path("src/personality/personality_engine.py").read_text(encoding="utf-8")
    assert "(context or {}).get('always_respond')" in eng
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    assert "'always_respond': True" in bot


def test_the_bypass_does_not_make_it_a_mention():
    """It must stay unsolicited so the topic guard can still drop it."""
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    fn = bot.split("async def _react_to_screen")[1].split("def _dead_air_context")[0]
    assert "is_mention=False" in fn and "is_mention=True" not in fn

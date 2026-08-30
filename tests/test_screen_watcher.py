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
    w._grab_jpeg_b64 = lambda: "ZmFrZQ=="
    w.seen = seen
    return w


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
    w._grab_jpeg_b64 = lambda: None
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
    assert "asyncio.to_thread(self._grab_jpeg_b64)" in SRC, \
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

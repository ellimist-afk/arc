"""The co-host stops doing the same bit about the screen.

Live 2026-09-05, Overwatch 2, chat empty: four bot lines in ten minutes, all
about the screen, all the same shape (hero + did thing + one absurd simile),
three of them vision reactions and the fourth a dead-air filler that reached
for the screen too. The lexical repetition guard passed every one and always
will -- they share no words.

Two causes, both covered here. The bot never saw its own unsolicited lines
(append_assistant is called on the reply paths only), so the model writing
line #4 had never seen #1-#3. And it had no way to say nothing: the only
silence available was a guard rejection.
"""
import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from bot.bot import TalkBot
from bot.response_coordinator import ResponseCoordinator


# --------------------------------------------------------------- harness

def _bot(reply="cassidy again", passes=False, ledger=(), streak_age=360.0,
         buffer=True):
    bot = TalkBot.__new__(TalkBot)
    bot.muted = False
    bot.config = {'TWITCH_CHANNEL': 'cassova_', 'TWITCH_BOT_USERNAME': 'elimist_'}
    bot.last_bot_message_at = None
    bot.auto_clipper = None                       # the clip path is not the subject
    bot._screen_ledger = deque(ledger, maxlen=6)
    bot.filler_screen_gap_s = 600.0
    bot._filler_screen_topic_at = None
    bot._filler_screen_topic_text = None
    bot.stream_info = None
    bot.session_summarizer = None
    bot._ambient_audio = deque(maxlen=3)
    bot.ambient_max_age_s = 180.0
    bot.screen_watcher = SimpleNamespace(
        streak_started_s_ago=lambda: streak_age,
        describe_with_duration=lambda: "Overwatch 2, cassidy on the payload")
    bot.appended = []
    bot.sent = []
    bot.seen = {}

    class Buffer:
        @staticmethod
        def get_recent(channel, limit=6):
            return []

        @staticmethod
        def append_assistant(channel, username, message):
            bot.appended.append((channel, username, message))

    bot.chat_buffer = Buffer() if buffer else None

    async def generate_response(message, context, user, is_mention=False):
        bot.seen['message'] = message
        bot.seen['context'] = context
        if passes:
            context['passed'] = True
            return None
        return {'text': reply}

    bot.personality_engine = SimpleNamespace(generate_response=generate_response)

    async def coordinate_response(chat_msg=None, **kw):
        bot.sent.append(chat_msg)

    bot.response_coordinator = SimpleNamespace(coordinate_response=coordinate_response)
    return bot


# ------------------------------------------ the co-host hears itself now

async def test_a_reaction_enters_the_bots_own_chat_context():
    """Without this the model writing screen line #4 has never seen #1-#3,
    and it cannot avoid what it cannot see."""
    bot = _bot(reply="six bullets and a custody hearing")
    await bot._react_to_screen("cassidy hit the floor", "death", 1)
    assert bot.sent == ["six bullets and a custody hearing"]
    assert bot.appended == [
        ("cassova_", "elimist_", "six bullets and a custody hearing")]
    assert len(bot._screen_ledger) == 1
    _when, kind, what, line = bot._screen_ledger[0]
    assert (kind, what, line) == (
        "death", "cassidy hit the floor", "six bullets and a custody hearing")


async def test_a_missing_chat_buffer_is_a_no_op():
    bot = _bot(buffer=False)
    bot._remember_own_line("something")
    await bot._react_to_screen("a thing happened")
    assert bot.sent == ["cassidy again"], "the line still goes out"


async def test_the_ledger_reaches_the_model():
    import time
    now = time.monotonic()
    bot = _bot(ledger=[(now - 360, 'death', 'died', 'a highlight reel'),
                       (now - 120, 'win', 'won', 'the payload was personal')])
    await bot._react_to_screen("died again", "death", 1)
    rendered = bot.seen['context']['screen_ledger']
    assert rendered == ["6 min ago (death): a highlight reel",
                        "2 min ago (win): the payload was personal"]


async def test_an_empty_ledger_sends_no_ledger_key():
    bot = _bot()
    await bot._react_to_screen("first thing this stream", "death", 1)
    assert 'screen_ledger' not in bot.seen['context']


# ------------------------------------------------- it is allowed to pass

async def test_a_pass_says_nothing_and_records_nothing(caplog):
    """A co-host with nothing new passes. Before this the only silence
    available was a guard rejection."""
    bot = _bot(passes=True)
    with caplog.at_level(logging.INFO):
        await bot._react_to_screen("cassidy died again", "death", 3)
    assert bot.sent == [] and bot.appended == []
    assert len(bot._screen_ledger) == 0
    assert "Screen reaction passed: nothing new to say" in caplog.text


def test_a_pass_returns_before_the_clip_and_the_coordinator():
    """A moment nobody spoke about is not a moment worth a clip."""
    src = Path("src/bot/bot.py").read_text(encoding="utf-8")
    fn = src.split("async def _react_to_screen")[1].split("\n    def ")[0]
    passed = fn.index("Screen reaction passed")
    assert passed < fn.index("clip_notable_moment")
    assert passed < fn.index("coordinate_response")


async def test_a_guard_drop_is_still_told_apart_from_a_pass(caplog):
    bot = _bot(reply="")
    with caplog.at_level(logging.INFO):
        await bot._react_to_screen("something", "death", 1)
    assert "suppressed by the guards" in caplog.text
    assert "nothing new to say" not in caplog.text


# ------------------------------------- the fourth death is not a moment

async def test_a_repeat_run_asks_for_the_pattern():
    bot = _bot(streak_age=380.0)
    await bot._react_to_screen("cassidy died again", "death", 4)
    assert bot.seen['context']['screen_streak'] == "4th death in about 6 minutes"
    assert "PATTERN" in bot.seen['message']
    assert "4th death in about 6 minutes" in bot.seen['message']


async def test_a_short_run_omits_a_meaningless_duration():
    bot = _bot(streak_age=20.0)
    await bot._react_to_screen("died", "death", 2)
    assert bot.seen['context']['screen_streak'] == "2nd death"


async def test_the_first_of_a_kind_is_still_offered_the_pass():
    bot = _bot()
    await bot._react_to_screen("the run ended on the final boss", "boss", 1)
    assert 'screen_streak' not in bot.seen['context']
    assert "reply with exactly PASS" in bot.seen['message']
    assert bot.seen['context']['may_pass'] is True


async def test_a_broken_run_clock_does_not_lose_the_line():
    bot = _bot()

    def boom():
        raise RuntimeError("no watcher")
    bot.screen_watcher = SimpleNamespace(streak_started_s_ago=boom)
    await bot._react_to_screen("died", "death", 3)
    assert bot.seen['context']['screen_streak'] == "3rd death"
    assert bot.sent == ["cassidy again"]


def test_ordinals_read_like_english():
    assert [TalkBot._ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd"]


# --------------------------------- the filler stops being a screen line

def test_a_filler_inside_the_gap_is_steered_off_the_screen(caplog):
    """Live: screen, screen, filler-about-the-screen, screen. A reaction
    restarts the lull clock and the filler then had the screen handed to it
    as its topic."""
    import time
    bot = _bot(ledger=[(time.monotonic() - 120, 'death', 'died',
                        'a highlight reel and a loading screen')])
    with caplog.at_level(logging.INFO):
        context = bot._dead_air_context()
    assert context['on_screen'], "the co-host can still see"
    assert 'screen_is_the_topic' not in context
    assert context['just_said_about_screen'] == (
        "2 minutes ago: a highlight reel and a loading screen")
    assert "Dead air: steering the filler off the screen" in caplog.text


def test_a_filler_after_the_gap_still_takes_the_screen():
    import time
    bot = _bot(ledger=[(time.monotonic() - 1200, 'death', 'died', 'old line')])
    context = bot._dead_air_context()
    assert context['screen_is_the_topic'] is True
    assert 'just_said_about_screen' not in context
    assert bot._filler_screen_topic_at is not None
    assert bot._filler_screen_topic_text == "Overwatch 2, cassidy on the payload"


def test_an_empty_ledger_leaves_the_filler_alone():
    bot = _bot()
    assert bot._dead_air_context()['screen_is_the_topic'] is True


# ------------------------------------------- the filler hears itself too

def test_a_sent_filler_enters_the_chat_context():
    import time
    bot = _bot()
    bot._filler_screen_topic_at = time.monotonic()
    bot._filler_screen_topic_text = "Overwatch 2, cassidy on the payload"
    bot._on_filler_sent("genji got play of the match somehow")
    assert bot.appended == [
        ("cassova_", "elimist_", "genji got play of the match somehow")]
    assert len(bot._screen_ledger) == 1
    assert bot._screen_ledger[0][1] == 'filler'


def test_a_filler_that_was_not_about_the_screen_stays_out_of_the_ledger():
    bot = _bot()
    bot._on_filler_sent("chat, settle an argument for me")
    assert bot.appended, "it is still a line the co-host said"
    assert len(bot._screen_ledger) == 0


def test_a_stale_screen_topic_does_not_claim_the_filler():
    import time
    bot = _bot()
    bot._filler_screen_topic_at = time.monotonic() - 300
    bot._filler_screen_topic_text = "something long gone"
    bot._on_filler_sent("a line about something else entirely")
    assert len(bot._screen_ledger) == 0


# ------------------------------------------------ the coordinator's hook

def _coordinator():
    rc = ResponseCoordinator(twitch_client=None, audio_queue=None,
                             settings_path="nope.json")
    rc.dead_air_threshold = 1
    rc.dead_air_enabled = True
    rc.startup_time = datetime.now() - timedelta(seconds=3600)
    rc.last_activity_time = datetime.now() - timedelta(seconds=3600)

    async def generate_response(message, context, user):
        return {'text': "the payload has opinions about you"}
    rc.personality_engine = SimpleNamespace(generate_response=generate_response)
    rc.delivered = []

    async def coordinate_response(chat_msg=None, **kw):
        rc.delivered.append(chat_msg)
    rc.coordinate_response = coordinate_response
    return rc


async def _run_until_sent(rc, monkeypatch):
    real_sleep = asyncio.sleep

    async def fast(_delay, *a, **kw):
        await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", fast)
    task = asyncio.create_task(rc._dead_air_monitor())
    try:
        for _ in range(2000):
            await real_sleep(0)
            if rc.delivered:
                break
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return rc.delivered


async def test_the_filler_hook_gets_the_line_that_was_sent(monkeypatch):
    rc = _coordinator()
    heard = []
    rc.on_filler_sent = heard.append
    delivered = await _run_until_sent(rc, monkeypatch)
    assert delivered == ["the payload has opinions about you"]
    assert heard == delivered


async def test_a_raising_hook_does_not_break_the_monitor(monkeypatch, caplog):
    """The line is already out; a bad hook must not take the lull monitor
    down with it."""
    rc = _coordinator()

    def boom(_text):
        raise RuntimeError("no buffer")
    rc.on_filler_sent = boom
    with caplog.at_level(logging.DEBUG):
        delivered = await _run_until_sent(rc, monkeypatch)
    assert delivered == ["the payload has opinions about you"]
    assert "Error in dead air monitor" not in caplog.text


async def test_no_hook_is_fine(monkeypatch):
    rc = _coordinator()
    assert rc.on_filler_sent is None
    assert await _run_until_sent(rc, monkeypatch)


def test_the_bot_wires_the_hook():
    src = Path("src/bot/bot.py").read_text(encoding="utf-8")
    assert "self.response_coordinator.on_filler_sent = self._on_filler_sent" in src

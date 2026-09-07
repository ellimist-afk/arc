"""The co-host reads the room: laughs are recorded, and silence is heard.

The ledger itself is tested in test_laugh_ledger.py. This is the wiring:
every line the co-host says gets registered, every viewer laugh finds the
line it belongs to, being addressed counts as attention, and a run of
unprompted lines that got nothing stops the next one from going out.

_handle_chat_message swallows every exception into one log line, so the
tests that drive it also assert that line is absent -- otherwise a missing
attribute reads as the guard doing its job.
"""
import asyncio
import logging
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from audit_stream_log import GUARDS, parse_sessions, render  # noqa: E402
from bot.bot import TalkBot, _read_laugh_ledger  # noqa: E402
from bot.channel_chat_buffer import ChannelChatBuffer  # noqa: E402
from features.laugh_ledger import LaughLedger  # noqa: E402
from personality.personality_engine import PersonalityEngine  # noqa: E402
from test_screen_variety import _bot as _screen_bot  # noqa: E402

BOT_SRC = Path("src/bot/bot.py").read_text(encoding="utf-8")


# ------------------------------------------------------------ the buffer

def test_append_assistant_hands_back_the_lines_id():
    """The ledger scores lines by seq: the ring buffer will drop the turn
    itself long before the stream is over."""
    buf = ChannelChatBuffer()
    assert buf.append_assistant("#Cassova_", "elimist_", "one") == 1
    assert buf.append_assistant("cassova_", "elimist_", "two") == 2
    assert buf.append_assistant("cassova_", "elimist_", "   ") == 0


# ---------------------------------------------------- recording our lines

def test_an_unprompted_line_is_recorded_as_unsolicited():
    led = LaughLedger()
    bot = _screen_bot(laughs=led)
    bot._remember_own_line("six bullets and a custody hearing")
    assert led.stats()['lines'] == 1
    assert led.dry_streak(datetime.now().timestamp() + 60) == 1


def test_recording_survives_a_buffer_that_returns_nothing():
    """Older stubs (and the real buffer, on an empty line) hand back None
    or 0; a line with no id simply is not scored."""
    led = LaughLedger()
    bot = _screen_bot(laughs=led)
    bot.chat_buffer.append_assistant = lambda channel, username, message: None
    bot._remember_own_line("something")
    assert led.stats()['lines'] == 0


def test_the_three_reply_paths_record_their_line_as_solicited():
    """A reply is not volunteering, so it must never feed the quiet rule."""
    for path in ("_handle_chat_message", "_try_streamed_reply",
                 "_handle_voice_message"):
        fn = BOT_SRC.split(f"def {path}")[1].split("\n    async def ")[0]
        assert "unsolicited=False" in fn, f"{path} does not record its line"
    fn = BOT_SRC.split("def _remember_own_line")[1].split("\n    def ")[0]
    assert "unsolicited=True" in fn


# ------------------------------------------------------- the chat handler

def _chat_bot(laughs=None, reply="that was the plan", room_window=False):
    import time as _time
    bot = TalkBot.__new__(TalkBot)
    bot.muted = False
    bot.config = {'TWITCH_CHANNEL': 'cassova_', 'TWITCH_BOT_USERNAME': 'elimist_',
                  'TTS_ENABLED': False}
    bot._bot_settings = {}
    bot.laugh_ledger = laughs
    bot.last_bot_message_at = None
    bot.unsolicited_gap_s = 30.0
    bot.followup_window_s = 90.0
    bot.max_followups = 3
    bot._replied_to = {}
    bot.room_followup_window_s = 60.0
    bot._room_followup_until = (_time.monotonic() + 60.0) if room_window else 0.0
    bot._last_line_unsolicited = room_window
    bot.stale_reply_s = 45.0
    bot.stale_mention_reply_s = 120.0
    bot.first_timer = None
    bot.stream_recap = None
    bot.chat_velocity = None
    bot.auto_clipper = None
    bot.metrics_enabled = False
    bot.metrics_collector = None
    bot.message_count = 1
    bot.response_times = []
    bot.last_response = None
    bot.audio_count = 0
    bot.sent = []
    bot.contexts = []

    async def store_message(message):
        return None
    bot.memory_system = SimpleNamespace(store_message=store_message)
    bot.chat_buffer = ChannelChatBuffer()

    async def build_context(**kw):
        return {}
    bot.context_builder = SimpleNamespace(build_context=build_context)

    async def generate_response(message, context, user, is_mention=False):
        bot.contexts.append(context)
        return {'text': reply} if reply else None
    bot.personality_engine = SimpleNamespace(generate_response=generate_response)

    async def coordinate_response(chat_msg=None, **kw):
        bot.sent.append(chat_msg)
    bot.response_coordinator = SimpleNamespace(
        coordinate_response=coordinate_response, note_activity=lambda: None)

    async def _try_streamed_reply(**kw):
        return False, None
    bot._try_streamed_reply = _try_streamed_reply
    bot._schedule_summary = lambda: None
    return bot


def _msg(text, username="motherchandelure"):
    return {'username': username, 'text': text, 'channel': 'cassova_'}


def _clean(caplog):
    assert "Error handling chat message" not in caplog.text, (
        "the handler swallowed an exception; this test proved nothing")


async def test_a_laugh_in_chat_is_credited_to_the_line_before_it(caplog):
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    bot = _chat_bot(laughs=led)
    led.note_bot_line(1, "six bullets and a custody hearing",
                      datetime.now().timestamp(), unsolicited=True)
    await bot._handle_chat_message(_msg("LMAO"))
    _clean(caplog)
    assert led.score(1) == 1
    assert "Room laughed at: 'six bullets and a custody hearing' (1 so far)" \
        in caplog.text


async def test_a_laugh_is_still_an_ordinary_message(caplog):
    """It is scored AND answered; crediting must not swallow the line."""
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    bot = _chat_bot(laughs=led)
    led.note_bot_line(1, "the line", datetime.now().timestamp(), unsolicited=True)
    await bot._handle_chat_message(_msg("lmao elimist_ what game is this"))
    _clean(caplog)
    assert led.score(1) == 1
    assert bot.sent == ["that was the plan"]


async def test_an_ordinary_message_credits_nothing(caplog):
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    bot = _chat_bot(laughs=led)
    led.note_bot_line(1, "the line", datetime.now().timestamp(), unsolicited=True)
    await bot._handle_chat_message(_msg("what game is this"))
    _clean(caplog)
    assert led.score(1) == 0
    assert "Room laughed at" not in caplog.text


def test_credit_laugh_stamps_with_arrival_time_not_processing_time():
    """`received_at` is when the message ARRIVED. If `_credit_laugh` used
    `datetime.now()` instead (taken after the DB write and other awaits in
    `_handle_chat_message`), a laugh that arrived well inside the line's
    window would be stamped outside it and lose the credit to nothing but
    scheduling delay."""
    led = LaughLedger()
    bot = _screen_bot(laughs=led)
    old = datetime.now().timestamp() - 3600  # real now() is far outside
    led.note_bot_line(1, "the line", old, unsolicited=True)
    received_at = datetime.fromtimestamp(old + 5)
    bot._credit_laugh({'username': 'chatter', 'text': 'lmao'}, received_at)
    assert led.score(1) == 1


async def test_the_reply_itself_is_recorded_and_not_unsolicited(caplog):
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    bot = _chat_bot(laughs=led)
    await bot._handle_chat_message(_msg("elimist_ how are you"))
    _clean(caplog)
    assert [line.text for line in led.recent(4, before=datetime.now().timestamp() + 60)] \
        == ["that was the plan"]
    assert led.dry_streak(datetime.now().timestamp() + 60) == 0


async def test_a_mention_counts_as_the_room_paying_attention(caplog):
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    now = datetime.now().timestamp()
    for i in range(3):
        led.note_bot_line(i + 1, f"dry line {i}", now - 300 + i, unsolicited=True)
    assert led.should_stay_quiet(now) is True
    bot = _chat_bot(laughs=led)
    await bot._handle_chat_message(_msg("elimist_ you there"))
    _clean(caplog)
    assert led.should_stay_quiet(now) is False


async def test_a_room_follow_up_counts_as_attention_too(caplog):
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    now = datetime.now().timestamp()
    for i in range(3):
        led.note_bot_line(i + 1, f"dry line {i}", now - 300 + i, unsolicited=True)
    bot = _chat_bot(laughs=led, room_window=True)
    await bot._handle_chat_message(_msg("yeah that one hurt"))
    _clean(caplog)
    assert "Room follow-up from" in caplog.text
    assert led.should_stay_quiet(now) is False


async def test_a_reply_is_never_held_by_the_quiet_rule(caplog):
    """Somebody asked. The rule is only ever about lines nobody asked for."""
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    now = datetime.now().timestamp()
    for i in range(3):
        led.note_bot_line(i + 1, f"dry line {i}", now - 300 + i, unsolicited=True)
    bot = _chat_bot(laughs=led)
    await bot._handle_chat_message(_msg("elimist_ you there"))
    _clean(caplog)
    assert bot.sent == ["that was the plan"]


async def test_the_handler_works_with_no_ledger_at_all(caplog):
    caplog.set_level(logging.INFO)
    bot = _chat_bot(laughs=None)
    await bot._handle_chat_message(_msg("lmao elimist_"))
    _clean(caplog)
    assert bot.sent == ["that was the plan"]


async def test_the_reply_context_carries_the_room_reaction(caplog):
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    now = datetime.now().timestamp()
    led.note_bot_line(1, "landed", now - 300, unsolicited=True)
    led.note_viewer("someone", "lmao", now - 299)
    led.note_bot_line(2, "did not land", now - 200, unsolicited=True)
    bot = _chat_bot(laughs=led)
    await bot._handle_chat_message(_msg("elimist_ hi"))
    _clean(caplog)
    assert bot.contexts[0]['room_reaction'] == [
        "'landed' (1 laughed)", "'did not land' (nothing)"]


async def test_the_cached_context_dict_is_never_mutated(caplog):
    """build_context hands back the dict it keeps in its caches; a per-request
    fact written onto it would still be there on every later cache hit."""
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    led.note_bot_line(1, "landed", datetime.now().timestamp() - 300,
                      unsolicited=True)
    bot = _chat_bot(laughs=led)
    cached = {}

    async def build_context(**kw):
        return cached
    bot.context_builder = SimpleNamespace(build_context=build_context)
    await bot._handle_chat_message(_msg("elimist_ hi"))
    _clean(caplog)
    assert cached == {}, "the builder's own dict was written to"
    assert 'room_reaction' in bot.contexts[0]


# --------------------------------------------------- holding the next line

async def test_a_screen_moment_is_held_when_nothing_has_been_landing(caplog):
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    now = datetime.now().timestamp()
    for i in range(3):
        led.note_bot_line(i + 1, f"dry line {i}", now - 300 + i, unsolicited=True)
    bot = _screen_bot(laughs=led)
    await bot._react_to_screen("cassidy hit the floor", "death", 1)
    assert bot.sent == [], "the line went out anyway"
    assert bot.appended == [], "a held line must not enter the ledger"
    assert bot._screen_ledger == deque([]), "a held moment is not a said line"
    assert "Quiet: last 3 unprompted lines got nothing" in caplog.text


async def test_a_screen_moment_goes_out_when_the_room_is_still_with_it(caplog):
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    now = datetime.now().timestamp()
    for i in range(3):
        led.note_bot_line(i + 1, f"line {i}", now - 300 + i, unsolicited=True)
    led.note_viewer("someone", "lmao", now - 280)
    bot = _screen_bot(laughs=led, reply="six bullets")
    await bot._react_to_screen("cassidy hit the floor", "death", 1)
    assert bot.sent == ["six bullets"]
    assert "Quiet: last" not in caplog.text


async def test_the_screen_reaction_context_carries_the_room_reaction():
    led = LaughLedger()
    led.note_bot_line(1, "landed", datetime.now().timestamp() - 300,
                      unsolicited=True)
    bot = _screen_bot(laughs=led)
    await bot._react_to_screen("cassidy hit the floor", "death", 1)
    assert bot.seen['context']['room_reaction'] == ["'landed' (nothing)"]


def test_the_filler_gate_holds_on_the_same_rule(caplog):
    caplog.set_level(logging.INFO)
    led = LaughLedger()
    now = datetime.now().timestamp()
    for i in range(3):
        led.note_bot_line(i + 1, f"dry line {i}", now - 300 + i, unsolicited=True)
    bot = _screen_bot(laughs=led)
    assert bot._may_fill() is False
    assert "Quiet: last 3 unprompted lines got nothing" in caplog.text


def test_quiet_hold_logs_when_a_release_fires(caplog):
    caplog.set_level(logging.INFO)
    led = LaughLedger()  # release_s defaults to 600
    now = datetime.now().timestamp()
    for i in range(3):
        led.note_bot_line(i + 1, f"dry line {i}", now - 750 + i, unsolicited=True)
    bot = _screen_bot(laughs=led)
    assert bot._quiet_hold() is False, "the release should let this one through"
    assert "Quiet released after 10 minutes: one more try" in caplog.text


def test_quiet_hold_does_not_log_a_release_when_simply_not_quiet(caplog):
    """Only an actual release logs the release line -- not every time the
    room is not being held (which is most of the time)."""
    caplog.set_level(logging.INFO)
    bot = _screen_bot(laughs=LaughLedger())
    assert bot._quiet_hold() is False
    assert "Quiet released after" not in caplog.text


def test_the_filler_gate_still_answers_yes_normally():
    bot = _screen_bot(laughs=LaughLedger())
    assert bot._may_fill() is True
    bot.laugh_ledger = None
    assert bot._may_fill() is True


def test_the_filler_gate_keeps_the_mute_and_liveness_rules():
    bot = _screen_bot(laughs=LaughLedger())
    bot.muted = True
    assert bot._may_fill() is False
    bot.muted = False
    bot.stream_info = SimpleNamespace(is_live=False)
    assert bot._may_fill() is False
    bot.stream_info = SimpleNamespace(is_live=None)
    assert bot._may_fill() is True, "unknown liveness is not offline"


def test_the_dead_air_context_carries_the_room_reaction():
    led = LaughLedger()
    led.note_bot_line(1, "landed", datetime.now().timestamp() - 300,
                      unsolicited=True)
    bot = _screen_bot(laughs=led)
    bot.chat_buffer.get_recent = lambda channel, limit=10: []
    assert bot._dead_air_context()['room_reaction'] == ["'landed' (nothing)"]


def test_every_hook_is_a_no_op_without_a_ledger():
    bot = _screen_bot(laughs=None)
    bot._note_bot_line(1, "x", unsolicited=True)
    bot._credit_laugh({'username': 'chatter', 'text': 'lmao'})
    bot._note_engaged()
    assert bot._quiet_hold() is False
    assert bot._room_reaction() == []
    assert bot._with_room_reaction({'a': 1}) == {'a': 1}


def test_the_hooks_survive_an_object_that_never_had_a_ledger():
    """The ledger is the newest thing on a very old object; nothing may
    assume the attribute exists."""
    bot = TalkBot.__new__(TalkBot)
    bot.config = {}
    assert bot._quiet_hold() is False
    assert bot._room_reaction() == []
    bot._note_engaged()


# --------------------------------------------------------------- settings

def test_the_settings_block_is_read():
    led = _read_laugh_ledger({'laugh_ledger': {'enabled': True,
                                               'window_seconds': 45,
                                               'quiet_after': 2}})
    assert (led.window_s, led.quiet_after) == (45.0, 2)


def test_the_settings_are_parsed_tolerantly():
    led = _read_laugh_ledger({'laugh_ledger': {'window_seconds': "abc",
                                               'quiet_after': None}})
    assert (led.window_s, led.quiet_after) == (30.0, 3)
    assert _read_laugh_ledger({}).window_s == 30.0
    assert _read_laugh_ledger(None).quiet_after == 3


def test_it_can_be_switched_off():
    assert _read_laugh_ledger({'laugh_ledger': {'enabled': False}}) is None


def test_the_release_seconds_setting_is_read():
    led = _read_laugh_ledger({'laugh_ledger': {'quiet_release_seconds': 120}})
    assert led.release_s == 120.0


def test_the_release_seconds_setting_is_parsed_tolerantly():
    led = _read_laugh_ledger({'laugh_ledger': {'quiet_release_seconds': "abc"}})
    assert led.release_s == 600.0
    assert _read_laugh_ledger({}).release_s == 600.0


# ----------------------------------------------------------- the prompt

def _engine():
    return PersonalityEngine(memory_system=None, openai_api_key="test-key")


def test_the_knowledge_block_shows_how_the_room_took_the_last_lines():
    out = _engine()._format_context_knowledge(
        {'room_reaction': ["'landed' (2 laughed)", "'did not' (nothing)"]},
        "viewer")
    assert "How the room took your last lines:" in out
    assert "'landed' (2 laughed); 'did not' (nothing)" in out
    assert "Notice what landed and what did not" in out


def test_no_reaction_means_no_line_in_the_block():
    out = _engine()._format_context_knowledge({'room_reaction': []}, "viewer")
    assert "How the room took" not in out


# ------------------------------------------------------------- the audit

def test_the_audit_needles_are_what_the_source_logs():
    """test_audit_stream_log.py's own scan does not cover these keys, so the
    pin lives here: a reworded log must break a test, not vanish."""
    for key, needle in (("room_laughed", "Room laughed at"),
                        ("quiet_dry", "Quiet: last"),
                        ("quiet_release", "Quiet released after")):
        assert needle in BOT_SRC, f"{key}: source no longer logs {needle!r}"
        assert any(k == key for k, _, _ in GUARDS), f"{key} is not in GUARDS"


def test_the_audit_counts_and_labels_them():
    log = "\n".join([
        "2026-09-06 21:00:00,000 - src.bot.bot - INFO - Starting TalkBot setup...",
        "2026-09-06 21:01:00,000 - src.bot.bot - INFO - Room laughed at: 'the line' (2 so far)",
        "2026-09-06 21:02:00,000 - src.bot.bot - INFO - Quiet: last 3 unprompted lines got nothing",
    ]) + "\n"
    session = parse_sessions(log)[0]
    assert session.counts['room_laughed'] == 1
    assert session.counts['quiet_dry'] == 1
    out = render(session)
    assert "Bot lines that got a laugh within the window" in out
    assert "Unprompted lines held because the last few got nothing" in out

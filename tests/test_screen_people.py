"""People come before the screen.

Live 2026-09-06, first stream on the screen-variety build. The guards from
slice 1 all worked -- and the co-host still spent the whole session talking
about the game to nobody. From talkbot.log, 10:55-11:30:

    11:24 Screen reaction (lobby: hero select screen)     <- a MENU got a line
    11:24:36 .. 11:24:49 Holding unsolicited reply x4     <- four viewers, held

Five viewer messages in 35 minutes were held by the 30s breather because the
bot had JUST said something about the screen, and unaddressed chat only ever
had a 3.5% dice roll of a reply anyway. Two rules follow. A co-host does not
interrupt a room that is talking to narrate a death. And a viewer who speaks
right after the co-host's unprompted line is almost always answering it.
"""
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from bot.bot import TalkBot, _read_screen_people

BOT_SRC = Path("src/bot/bot.py").read_text(encoding="utf-8")


# ------------------------------------------------------------- how old is chat


def _reader(turns, buffer=True):
    """A bot with only what _seconds_since_viewer_spoke touches."""
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'TWITCH_CHANNEL': 'cassova_'}
    bot.chat_buffer = SimpleNamespace(
        get_recent=lambda channel, limit=10: list(turns)) if buffer else None
    return bot


def _turn(role, ago_s):
    return {'username': 'someone', 'message': 'hi', 'role': role,
            'timestamp': datetime.now().timestamp() - ago_s, 'seq': 1}


def test_the_newest_viewer_turn_is_what_counts():
    bot = _reader([_turn('viewer', 300), _turn('viewer', 8)])
    ago = bot._seconds_since_viewer_spoke()
    assert ago is not None and 7 <= ago <= 12


def test_the_co_hosts_own_line_is_not_the_room_talking():
    """A screen reaction is appended to the buffer as 'assistant' so the
    model can avoid repeating itself. If the role were not checked, the
    co-host would yield to its own last line forever."""
    bot = _reader([_turn('viewer', 300), _turn('assistant', 2)])
    ago = bot._seconds_since_viewer_spoke()
    assert ago is not None and ago >= 290


def test_no_viewer_has_ever_spoken():
    assert _reader([_turn('assistant', 2)])._seconds_since_viewer_spoke() is None
    assert _reader([])._seconds_since_viewer_spoke() is None


def test_no_buffer_is_not_an_error():
    assert _reader([], buffer=False)._seconds_since_viewer_spoke() is None


def test_a_broken_buffer_does_not_lose_the_line(caplog):
    bot = _reader([])

    def boom(channel, limit=10):
        raise RuntimeError("no buffer")
    bot.chat_buffer = SimpleNamespace(get_recent=boom)
    with caplog.at_level(logging.DEBUG):
        assert bot._seconds_since_viewer_spoke() is None
    assert "Chat recency unavailable" in caplog.text


def test_a_junk_timestamp_is_skipped_not_raised():
    bot = _reader([{'role': 'viewer', 'timestamp': 'yesterday'},
                   {'role': 'viewer', 'timestamp': None}])
    assert bot._seconds_since_viewer_spoke() is None


# -------------------------------------------------- the screen yields to chat


def _screen_bot(viewer_ago=None, reply="cassidy again"):
    bot = TalkBot.__new__(TalkBot)
    bot.muted = False
    bot.config = {'TWITCH_CHANNEL': 'cassova_', 'TWITCH_BOT_USERNAME': 'elimist_'}
    bot.last_bot_message_at = None
    bot.auto_clipper = None
    bot._screen_ledger = deque(maxlen=6)
    bot._filler_screen_topic_at = None
    bot._filler_screen_topic_text = None
    bot.yield_to_chat_s = 45.0
    bot.room_followup_window_s = 60.0
    bot._room_followup_until = 0.0
    bot._last_line_unsolicited = False
    bot.screen_watcher = SimpleNamespace(streak_started_s_ago=lambda kind: None)
    bot.generated = []
    bot.sent = []

    turns = [] if viewer_ago is None else [_turn('viewer', viewer_ago)]
    bot.chat_buffer = SimpleNamespace(
        get_recent=lambda channel, limit=10: list(turns),
        append_assistant=lambda channel, username, message: None)

    async def generate_response(message, context, user, is_mention=False):
        bot.generated.append(message)
        return {'text': reply}
    bot.personality_engine = SimpleNamespace(generate_response=generate_response)

    async def coordinate_response(chat_msg=None, **kw):
        bot.sent.append(chat_msg)
    bot.response_coordinator = SimpleNamespace(
        coordinate_response=coordinate_response)
    return bot


async def test_a_moment_that_lands_mid_conversation_is_dropped(caplog):
    bot = _screen_bot(viewer_ago=10)
    with caplog.at_level(logging.INFO):
        await bot._react_to_screen("cassidy hit the floor", "death", 1)
    assert bot.generated == [], "generation never even started"
    assert bot.sent == []
    assert len(bot._screen_ledger) == 0, "nothing was said, so nothing to record"
    assert "Screen moment held, chat is talking (10s ago)" in caplog.text


async def test_a_quiet_room_still_gets_the_reaction():
    bot = _screen_bot(viewer_ago=60)
    await bot._react_to_screen("cassidy hit the floor", "death", 1)
    assert bot.sent == ["cassidy again"]


async def test_an_empty_chat_is_not_a_talking_room():
    """A solo stream with nobody in chat is exactly when the screen line is
    worth the most."""
    bot = _screen_bot(viewer_ago=None)
    await bot._react_to_screen("cassidy hit the floor", "death", 1)
    assert bot.sent == ["cassidy again"]


def test_the_yield_comes_before_the_clip_and_the_generation():
    fn = BOT_SRC.split("async def _react_to_screen")[1].split("\n    def ")[0]
    held = fn.index("Screen moment held, chat is talking")
    assert held < fn.index("generate_response")
    assert held < fn.index("clip_notable_moment")
    assert held < fn.index("_screen_ledger.append")


# ------------------------------------------- the room may answer the co-host


async def test_a_screen_line_opens_the_room_window():
    bot = _screen_bot(viewer_ago=None)
    await bot._react_to_screen("cassidy hit the floor", "death", 1)
    assert bot._last_line_unsolicited is True
    assert bot._room_followup_until > 0


async def test_a_held_moment_opens_nothing():
    bot = _screen_bot(viewer_ago=5)
    await bot._react_to_screen("cassidy hit the floor", "death", 1)
    assert bot._room_followup_until == 0.0
    assert bot._last_line_unsolicited is False


def test_a_filler_opens_the_room_window_too():
    """A dead-air filler is just as unprompted as a screen reaction."""
    bot = _screen_bot(viewer_ago=None)
    bot._on_filler_sent("the payload has opinions about you")
    assert bot._last_line_unsolicited is True
    assert bot._room_followup_until > 0


def test_answering_someone_is_not_an_unprompted_line():
    bot = _screen_bot(viewer_ago=None)
    bot._replied_to = {}
    bot.followup_window_s = 90.0
    bot._last_line_unsolicited = True
    bot._note_replied_to("motherchandelure")
    assert bot._last_line_unsolicited is False


# ---------------------------------------- what the handler does with all that


def _chat_bot(unsolicited_ago_s=None, reply="that was the plan"):
    """A bot wired for exactly the path _handle_chat_message walks. The
    handler swallows every exception into one log line, so each test also
    asserts that line is absent -- otherwise a missing attribute would read
    as the guard doing its job."""
    import time as _time
    bot = TalkBot.__new__(TalkBot)
    bot.muted = False
    bot.config = {'TWITCH_CHANNEL': 'cassova_', 'TWITCH_BOT_USERNAME': 'elimist_',
                  'TTS_ENABLED': False}
    bot._bot_settings = {}
    bot.last_bot_message_at = datetime.now()      # the co-host just spoke
    bot.unsolicited_gap_s = 30.0
    bot.followup_window_s = 90.0
    bot.max_followups = 3
    bot._replied_to = {}
    bot.room_followup_window_s = 60.0
    bot._room_followup_until = (0.0 if unsolicited_ago_s is None
                                else _time.monotonic()
                                + 60.0 - unsolicited_ago_s)
    bot._last_line_unsolicited = unsolicited_ago_s is not None
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
    bot.generated = []

    async def store_message(message):
        return None
    bot.memory_system = SimpleNamespace(store_message=store_message)

    bot.chat_buffer = SimpleNamespace(
        append_viewer=lambda **kw: None,
        append_assistant=lambda **kw: None,
        get_recent=lambda channel, limit=10: [])

    async def build_context(**kw):
        return {}
    bot.context_builder = SimpleNamespace(build_context=build_context)

    async def generate_response(message, context, user, is_mention=False):
        bot.generated.append((message, is_mention))
        return {'text': reply}
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


async def test_a_viewer_answering_the_co_host_gets_a_reply(caplog):
    """The whole point. Before this the breather held them: the bot had
    said something 5s ago, and nobody is a per-viewer follow-up after a
    line the co-host started on its own."""
    bot = _chat_bot(unsolicited_ago_s=20)
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg("lol did it get you again"))
    _clean(caplog)
    assert bot.sent == ["that was the plan"]
    assert bot.generated[0][1] is True, "answered as a mention"
    assert ("Room follow-up from motherchandelure: answering the co-host's line"
            in caplog.text)


async def test_only_the_first_message_claims_the_window(caplog):
    bot = _chat_bot(unsolicited_ago_s=20)
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg("lol"))
        await bot._handle_chat_message(_msg("again?", username="someone_else"))
    _clean(caplog)
    assert bot._room_followup_until == 0.0
    assert bot.sent == ["that was the plan"], "the second one took the normal path"
    assert "Holding unsolicited reply" in caplog.text


async def test_an_expired_window_is_the_normal_path(caplog):
    bot = _chat_bot(unsolicited_ago_s=70)
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg("something unrelated"))
    _clean(caplog)
    assert bot.sent == []
    assert "Room follow-up" not in caplog.text
    assert "Holding unsolicited reply" in caplog.text


async def test_no_unprompted_line_means_no_window(caplog):
    bot = _chat_bot(unsolicited_ago_s=None)
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg("hello everyone"))
    _clean(caplog)
    assert bot.sent == []
    assert "Room follow-up" not in caplog.text


async def test_a_command_never_claims_the_window(caplog):
    """A !command is an instruction, not an answer. Its handler already
    replied; the window must still be there for whoever speaks next."""
    bot = _chat_bot(unsolicited_ago_s=20)
    before = bot._room_followup_until
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg("!persona roast"))
    _clean(caplog)
    assert bot.sent == []
    assert bot._room_followup_until == before, "the window was not consumed"
    assert "Room follow-up" not in caplog.text


async def test_a_mention_is_left_alone(caplog):
    bot = _chat_bot(unsolicited_ago_s=20)
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg("elimist_ what was that"))
    _clean(caplog)
    assert bot.sent == ["that was the plan"]
    assert "Room follow-up" not in caplog.text
    assert bot._room_followup_until != 0.0, "a mention does not spend the window"


async def test_a_follow_up_to_a_reply_still_waits_its_turn(caplog):
    """The breather bypass is only for someone answering an UNPROMPTED line.
    After a normal reply, a follow-up inside 30s is still the bot talking
    over itself."""
    import time as _time
    bot = _chat_bot(unsolicited_ago_s=None)
    bot._replied_to = {'motherchandelure': (_time.monotonic(), 1)}
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg("and another thing"))
    _clean(caplog)
    assert bot.sent == []
    assert "Holding follow-up reply" in caplog.text


async def test_a_dropped_room_followup_does_not_leak_the_bypass(caplog):
    """The room window is consumed (and _last_line_unsolicited cleared) the
    moment a message claims it -- even if that message never makes it to a
    reply, here because the co-host is muted. Before this fix the flag
    stayed True until the next unprompted line, so an unrelated per-viewer
    follow-up in between rode the bypass for free. The upgraded message
    itself must still get through when it is NOT dropped."""
    import time as _time
    bot = _chat_bot(unsolicited_ago_s=20)
    bot.muted = True
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg("lol did it get you again"))
    _clean(caplog)
    assert bot.sent == [], "muted: nothing should have gone out"
    assert bot._last_line_unsolicited is False, "the room window was consumed"
    assert bot._room_followup_until == 0.0

    # A viewer with their own, unrelated follow-up slot must still be held
    # by the breather -- the cleared flag above is not a bypass for them.
    bot.muted = False
    bot._replied_to = {'someone_else': (_time.monotonic(), 1)}
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg("what happened", username="someone_else"))
    _clean(caplog)
    assert bot.sent == [], "the unrelated per-viewer follow-up must still be held"
    assert "Holding follow-up reply" in caplog.text


async def test_the_upgraded_message_itself_is_never_held(caplog):
    """Same room follow-up as above, but not muted: it must reach the
    breather with the bypass still live for ITS OWN message, even though
    the flag it depends on gets cleared as part of claiming the window."""
    bot = _chat_bot(unsolicited_ago_s=20)
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg("lol did it get you again"))
    _clean(caplog)
    assert bot.sent == ["that was the plan"]
    assert "Holding" not in caplog.text


async def test_the_reply_reopens_the_viewers_own_follow_up_streak(caplog):
    """After the room follow-up is answered, _note_replied_to has run, so
    the conversation continues by the ordinary name-free rules."""
    bot = _chat_bot(unsolicited_ago_s=20)
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg("lol"))
    _clean(caplog)
    assert bot._is_followup("motherchandelure") is True
    assert bot._last_line_unsolicited is False, "the last line was an answer"


def test_the_breather_bypass_is_narrow():
    handler = BOT_SRC.split("async def _handle_chat_message")[1].split(
        "\n    async def ")[0]
    assert ("answering_us = room_answer or (via_followup and self._last_line_unsolicited)"
            in handler)
    assert "not answering_us" in handler


# --------------------------------------------------------------- the settings


def test_the_new_settings_survive_a_hand_edit():
    """Read inside the try block that builds ScreenWatcher: raising here
    would disable vision for one bad character."""
    assert _read_screen_people({}) == (45.0, 60.0)
    assert _read_screen_people({'screen_awareness': {}}) == (45.0, 60.0)
    assert _read_screen_people({'screen_awareness': {
        'yield_to_chat_seconds': "30", 'room_followup_seconds': 90}}) == (30.0, 90.0)
    assert _read_screen_people({'screen_awareness': {
        'yield_to_chat_seconds': "abc", 'room_followup_seconds': None}}) == (45.0, 60.0)


def test_zero_is_off_not_a_typo():
    assert _read_screen_people({'screen_awareness': {
        'yield_to_chat_seconds': 0, 'room_followup_seconds': 0}}) == (0.0, 0.0)


def test_the_settings_are_wired_at_setup():
    assert "_read_screen_people(self._bot_settings)" in BOT_SRC


def test_the_settings_are_in_the_shipped_file():
    import json
    cfg = json.loads(Path("bot_settings.json").read_text(
        encoding="utf-8"))['screen_awareness']
    assert cfg['yield_to_chat_seconds'] == 45
    assert cfg['room_followup_seconds'] == 60
    for key in ('yield_to_chat_seconds', 'room_followup_seconds'):
        assert key in cfg['_comment'], "a setting nobody documents is a dead one"

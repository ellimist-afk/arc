"""Topic wells wired into the bot: material per well, the chosen well's
context carries nothing of the game, the streamer-said deque, note() from
both send paths, the settings reader, and wells-off parity."""

import asyncio
import logging
import random
from collections import deque
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.bot import TalkBot, _read_topic_wells
from bot.response_coordinator import ResponseCoordinator
from features.topic_wells import DEFAULT_OFF_TOPIC, TopicWells

GAME_KEYS = ('on_screen', 'stream_now', 'ambient_audio', 'screen_is_the_topic',
             'just_said_about_screen', 'session_summary', 'recent_messages')


class _Buffer:
    def __init__(self, turns):
        self.turns = turns

    def get_recent(self, channel, limit=10):
        return self.turns[-limit:]

    def append_assistant(self, channel, username, message):
        return 0


def _bot(wells=True, turns=(), summary="pizza bet at 9pm", screen="cassidy on the payload"):
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'TWITCH_CHANNEL': 'cassova_', 'TWITCH_BOT_USERNAME': 'elimist_'}
    bot.muted = False
    bot.laugh_ledger = None
    bot.last_bot_message_at = None
    bot._screen_ledger = deque(maxlen=6)
    bot.filler_screen_gap_s = 600.0
    bot.room_followup_window_s = 0.0
    bot._room_followup_until = 0.0
    bot._last_line_unsolicited = False
    bot._filler_screen_topic_at = None
    bot._filler_screen_topic_text = None
    bot.stream_info = MagicMock()
    bot.stream_info.describe.return_value = "Overwatch 2, live 2h"
    bot.screen_watcher = MagicMock()
    bot.screen_watcher.describe_with_duration.return_value = screen
    bot.session_summarizer = MagicMock()
    bot.session_summarizer.get_summary.return_value = summary
    bot._ambient_audio = deque([(datetime.now(), "payload is moving")], maxlen=3)
    bot.ambient_max_age_s = 180.0
    bot.chat_buffer = _Buffer(list(turns))
    bot.context_builder = None
    bot.response_coordinator = None
    bot._streamer_said = deque(maxlen=6)
    bot._pending_well = None
    bot._wells_quiet_logged = False
    bot._wells_rng = random.Random(1)
    bot.topic_wells = TopicWells(0.34, 6) if wells else None
    bot.off_topic = list(DEFAULT_OFF_TOPIC) if wells else []
    return bot


def _viewer(name, ago_s=60):
    return {'username': name, 'message': 'hi', 'role': 'viewer',
            'timestamp': datetime.now().timestamp() - ago_s, 'seq': 1}


def test_material_per_well():
    bot = _bot(turns=[_viewer('ann'), _viewer('old', 4000),
                      {'username': 'elimist_', 'role': 'assistant', 'message': 'x',
                       'timestamp': datetime.now().timestamp(), 'seq': 2}])
    bot._streamer_said.append((datetime.now(), "i cannot believe how bad that fight went"))
    bot._streamer_said.append((datetime.now() - timedelta(seconds=400), "stale words here are too old now"))
    bot.context_builder = MagicMock()
    bot.context_builder.l1_cache = {'cassova_:ann:mention': ({'history_summary': 'bakes bread'}, 0.0)}
    bot.context_builder.l2_cache = MagicMock(cache={})
    got = bot._available_wells()
    assert set(got['game']) == {'stream_now', 'on_screen', 'ambient_audio'}
    assert got['streamer_said'] == {'quotes': ["i cannot believe how bad that fight went"]}
    assert got['earlier_tonight'] == {'summary': "pizza bet at 9pm"}
    assert got['ask_chat'] == {'names': ['ann']}
    assert got['viewer_callback'] == {'viewer': 'ann', 'fact': 'bakes bread'}
    assert got['off_topic']['entries'] == DEFAULT_OFF_TOPIC


def test_wells_without_material_are_absent():
    bot = _bot(summary="", screen=None)
    bot.stream_info = None
    bot._ambient_audio = deque(maxlen=3)
    bot.off_topic = []
    assert bot._available_wells() == {}


def test_earlier_tonight_waits_for_fifteen_minutes():
    bot = _bot()
    bot.response_coordinator = MagicMock(startup_time=datetime.now() - timedelta(minutes=5))
    assert 'earlier_tonight' not in bot._available_wells()
    bot.response_coordinator.startup_time = datetime.now() - timedelta(minutes=20)
    assert 'earlier_tonight' in bot._available_wells()


def test_streamer_said_context_has_no_game_keys(caplog):
    bot = _bot()
    bot.topic_wells.note('game')
    bot.off_topic = []
    bot.session_summarizer = None
    bot._streamer_said.append((datetime.now(), "i am never playing this map again ever"))
    with caplog.at_level(logging.INFO):
        ctx = bot._dead_air_context()
    assert ctx['well'] == 'streamer_said'
    for key in GAME_KEYS:
        assert key not in ctx, key
    assert "i am never playing this map again ever" in ctx['well_directive']
    assert bot._filler_screen_topic_at is None
    assert "Filler well: streamer_said (" in caplog.text
    assert "mixed mic bus" in caplog.text


def test_game_well_is_todays_context_plus_the_name():
    bot = _bot()
    bot.off_topic = []
    bot.session_summarizer = None
    bot.chat_buffer = _Buffer([])
    ctx = bot._game_context()
    picked = bot._dead_air_context()
    assert picked.pop('well') == 'game'
    assert picked == ctx


def test_nothing_eligible_stays_quiet_and_logs_once(caplog):
    # Two wells available (game, off_topic) so the F1 solo carve-out does not
    # apply: game sits in the last 2 (blocked) and off_topic was last used
    # (blocked), so nothing is eligible.
    bot = _bot()
    bot.session_summarizer = None
    bot.topic_wells.note('game')
    bot.topic_wells.note('off_topic')
    with caplog.at_level(logging.INFO):
        assert bot._dead_air_context() == {'stay_quiet': True}
        assert bot._dead_air_context() == {'stay_quiet': True}
    assert caplog.text.count("Filler well: none, staying quiet") == 1


def test_wells_off_is_todays_context_exactly():
    on, off = _bot(wells=True), _bot(wells=False)
    ctx = off._dead_air_context()
    assert 'well' not in ctx and 'stay_quiet' not in ctx
    assert ctx == _bot(wells=False)._game_context()
    assert ctx['screen_is_the_topic'] is True
    assert 'stream_now' in ctx and 'session_summary' in ctx


def test_off_topic_picks_differ_over_time():
    # Two wells available (off_topic, ask_chat via a real recent viewer) so
    # the F1 solo carve-out does not apply and off_topic keeps alternating
    # with something else, as before.
    bot = _bot(turns=[_viewer('ann')])
    bot.session_summarizer = None
    bot.screen_watcher = None
    bot.stream_info = None
    bot._ambient_audio = deque(maxlen=3)
    topics = []
    for _ in range(6):
        ctx = bot._dead_air_context()
        if ctx.get('well') == 'off_topic':
            topics.append(ctx['well_material']['topic'])
        bot._on_filler_sent("a line")
    assert len(topics) >= 2
    assert all(a != b for a, b in zip(topics, topics[1:]))


def test_filler_sent_notes_the_pending_well_once():
    bot = _bot()
    bot._pending_well = 'ask_chat'
    bot._on_filler_sent("what are you all eating")
    bot._on_filler_sent("another")
    assert bot.topic_wells.history() == ['ask_chat']


def test_screen_reaction_notes_game():
    bot = _bot()
    bot.yield_to_chat_s = 45.0
    bot.auto_clipper = None
    bot.personality_engine = MagicMock()
    bot.personality_engine.generate_response = AsyncMock(return_value={'text': 'nice one'})
    bot.response_coordinator = MagicMock()
    bot.response_coordinator.coordinate_response = AsyncMock()
    bot.task_registry = MagicMock()
    bot.screen_watcher.streak_started_s_ago.return_value = None
    asyncio.run(bot._react_to_screen("a triple kill", kind='kill'))
    assert bot.topic_wells.history() == ['game']


def test_streamer_said_fed_only_on_the_untriggered_path():
    src = open('src/bot/bot.py', encoding='utf-8').read()
    feed = src.index("said.append((datetime.now(), text))")
    no_trigger = src.index('logger.info(f"[VOICE] NO TRIGGER in:')
    keep = src.index("if not self._worth_keeping_ambient(text):", no_trigger)
    assert no_trigger < keep < feed
    assert src.count("said.append(") == 1
    assert "len(text.split()) >= 6" in src[keep:feed + 80]


def test_reader_is_tolerant():
    assert _read_topic_wells({})[0] is not None
    assert _read_topic_wells({'topic_wells': {'enabled': False}}) == (None, [])
    wells, topics = _read_topic_wells({'topic_wells': {
        'game_share_max': '0.3x', 'window': 'six', 'off_topic': 'cats'}})
    assert wells.game_cap() == 2 and wells.window == 6
    assert topics == ['cats']
    assert _read_topic_wells({'topic_wells': 'yes'})[0] is not None


def test_coordinator_generates_nothing_when_told_to_stay_quiet():
    rc = ResponseCoordinator.__new__(ResponseCoordinator)
    src = open('src/bot/response_coordinator.py', encoding='utf-8').read()
    quiet = src.index("if context.get('stay_quiet'):")
    assert src.index("self.context_provider()") < quiet < src.index(
        'message="[DEAD_AIR_FILLER]"')
    assert rc is not None


# ------------------------------------------------- F2: addressed vs overheard

def _voice_bot():
    """A bot with only what _handle_voice_input touches."""
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'TWITCH_CHANNEL': 'cassova_', 'TWITCH_BOT_USERNAME': 'elimist_'}
    bot._bot_settings = {}
    bot.realtime_backend = None
    bot.voice_commands = None
    bot.last_voice_response = None
    bot.recent_voice_texts = []
    bot.muted = False
    bot.last_voice_reply_at = None
    bot.voice_retrigger_gap_s = 12.0
    bot._ambient_audio = deque(maxlen=3)
    bot._streamer_said = deque(maxlen=6)

    async def store_message(message):
        return None
    bot.memory_system = SimpleNamespace(store_message=store_message)
    return bot


async def test_addressed_long_utterance_is_not_stored_as_overheard(caplog):
    """A 14-word utterance naming the bot is too long for the <=10-word
    address trigger, so it falls into the NO TRIGGER branch -- but it was
    still said TO the bot, not overheard, so it must not join
    _streamer_said (F2)."""
    bot = _voice_bot()
    text = ("elimist i think that fight was rough and we should probably "
            "think about rotating")
    assert len(text.split()) == 14
    with caplog.at_level(logging.INFO):
        await bot._handle_voice_input(text)
    assert "Error handling voice input" not in caplog.text
    assert list(bot._streamer_said) == []


async def test_unaddressed_long_utterance_is_stored_as_overheard(caplog):
    """Same shape, no bot name: an ordinary overheard line still gets
    captured (unchanged behaviour)."""
    bot = _voice_bot()
    text = ("man i think that fight was rough and we should probably "
            "think about rotating")
    assert len(text.split()) == 14
    with caplog.at_level(logging.INFO):
        await bot._handle_voice_input(text)
    assert "Error handling voice input" not in caplog.text
    assert len(bot._streamer_said) == 1
    assert bot._streamer_said[0][1] == text

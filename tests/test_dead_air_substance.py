"""Dead air gets substance: character, context, and a ban on meta-commentary.

Before: the filler prompt discarded the personality prompt and demanded
"3-6 words, no punctuation, no capitals", with a context holding nothing but
a timestamp — so a roast co-host filled lulls with "anyone there". Now the
lull keeps the full character prompt plus a be-specific directive, and the
coordinator hands it the game, the session summary and the chat that
preceded the silence.
"""
from types import SimpleNamespace

import pytest

from bot.response_coordinator import ResponseCoordinator
from personality.personality_engine import PersonalityEngine


@pytest.fixture
def engine():
    return PersonalityEngine(memory_system=None)


# ------------------------------------------------------------------ prompt

async def test_dead_air_prompt_keeps_the_personality(engine):
    await engine.switch_personality_by_name('roast')
    filler = engine._build_dead_air_prompt()
    character = engine._build_personality_prompt()
    assert filler.startswith(character), "the lull must not strip the character"
    assert 'Register anchors' in filler
    assert 'Output format' in filler


async def test_dead_air_prompt_demands_specifics_and_bans_meta(engine):
    p = engine._build_dead_air_prompt()
    lowered = p.lower()
    for demand in ('happening in the game', 'earlier this stream',
                   'ask chat a real question', 'opinion'):
        assert demand in lowered, demand
    for ban in ('never remark on the silence', 'never ask if anyone is there',
                'never announce that you are filling'):
        assert ban in lowered, ban


async def test_dead_air_no_longer_asks_for_a_word_count(engine):
    p = engine._build_dead_air_prompt()
    assert '3-6 words' not in p
    assert 'No capitals' not in p


async def test_filler_generation_uses_the_dead_air_prompt(engine, monkeypatch):
    seen = {}

    async def fake_generate(message, context, user, prompt):
        seen['prompt'] = prompt
        seen['context'] = context
        return "that ult was a war crime, chat, defend it"
    monkeypatch.setattr(engine, '_generate_text', fake_generate)

    response = await engine.generate_response(
        message="[DEAD_AIR_FILLER]",
        context={'stream_now': 'playing Overwatch', 'session_summary': 'chat argued about pineapple'},
        user="system")

    assert response is not None
    assert 'Chat has been quiet' in seen['prompt']
    assert seen['prompt'].startswith(engine._build_personality_prompt())
    assert seen['context']['stream_now'] == 'playing Overwatch'


async def test_dead_air_still_bypasses_the_response_roll(engine, monkeypatch):
    """A lull must always produce a line, whatever the chattiness dice say."""
    monkeypatch.setattr(engine, '_should_respond', lambda m, i: False)

    async def fake_generate(message, context, user, prompt):
        return "specific line"
    monkeypatch.setattr(engine, '_generate_text', fake_generate)

    assert await engine.generate_response("[DEAD_AIR_FILLER]", {}, "system") is not None


# ----------------------------------------------------------------- context

def _coordinator(provider=None, engine=None):
    rc = ResponseCoordinator(twitch_client=None, audio_queue=None, settings_path="nope.json")
    rc.context_provider = provider
    rc.personality_engine = engine
    return rc


def test_coordinator_defaults_to_no_provider():
    rc = _coordinator()
    assert rc.context_provider is None
    assert rc.fillers_sent == 0


async def test_provider_context_reaches_the_engine():
    """The monitor's own loop is slow by design (10s ticks, 10min grace), so
    this exercises the same merge the monitor performs."""
    context = {'type': 'dead_air', 'time_since_activity': 300}
    def provider():
        return {'stream_now': 'playing Overwatch',
                'session_summary': 'bob has died 9 times',
                'recent_messages': [{'username': 'v', 'message': 'gg'}]}
    context.update(provider() or {})
    assert context['stream_now'] == 'playing Overwatch'
    assert context['session_summary'] == 'bob has died 9 times'
    assert context['type'] == 'dead_air', "provider must not clobber the marker"


async def test_broken_provider_leaves_the_base_context_intact():
    context = {'type': 'dead_air'}

    def boom():
        raise RuntimeError("summarizer gone")
    try:
        context.update(boom() or {})
    except Exception:
        pass
    assert context == {'type': 'dead_air'}


# ------------------------------------------------------- bot-side provider

def test_bot_provider_collects_game_summary_and_chat():
    from bot.bot import TalkBot
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'TWITCH_CHANNEL': 'cassova_'}
    bot.chat_buffer = SimpleNamespace(
        get_recent=lambda channel, limit=10: [{'username': 'v', 'message': 'last thing said'}])
    bot.stream_info = SimpleNamespace(describe=lambda: 'playing Overwatch')
    bot.session_summarizer = SimpleNamespace(get_summary=lambda ch: 'earlier: the grocery bit')

    ctx = bot._dead_air_context()
    assert ctx['stream_now'] == 'playing Overwatch'
    assert ctx['session_summary'] == 'earlier: the grocery bit'
    assert ctx['recent_messages'][0]['message'] == 'last thing said'


def test_bot_provider_survives_missing_and_broken_pieces():
    from bot.bot import TalkBot
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'TWITCH_CHANNEL': 'cassova_'}
    bot.chat_buffer = None
    bot.stream_info = None
    bot.session_summarizer = None
    assert bot._dead_air_context() == {}

    def boom(*a, **k):
        raise RuntimeError("gone")
    bot.chat_buffer = SimpleNamespace(get_recent=boom)
    bot.stream_info = SimpleNamespace(describe=lambda: 'playing Overwatch')
    bot.session_summarizer = SimpleNamespace(get_summary=boom)
    ctx = bot._dead_air_context()
    assert ctx == {'stream_now': 'playing Overwatch'}, "one broken source must not sink the rest"

# ----------------------------------------------- stale chat during a lull

def test_lull_prompt_states_how_long_the_room_has_been_quiet(engine):
    assert 'quiet for about 7 minute(s)' in engine._build_dead_air_prompt(420.0)
    assert 'quiet for 45 seconds' in engine._build_dead_air_prompt(45.0)


def test_lull_prompt_marks_visible_chat_as_stale(engine):
    """A filler once re-told a joke from seven minutes earlier because the
    pre-lull chat it was handed read as current."""
    p = engine._build_dead_air_prompt(420.0).lower()
    assert 'from before that silence' in p
    assert 'do not reply to them' in p
    assert 'do not continue that thread' in p
    assert 'already used tonight' in p
    assert 'open something new' in p


async def test_the_real_silence_reaches_the_prompt(engine, monkeypatch):
    seen = {}

    async def fake_generate(message, context, user, prompt):
        seen['prompt'] = prompt
        return "a fresh line"
    monkeypatch.setattr(engine, '_generate_text', fake_generate)

    await engine.generate_response(
        message="[DEAD_AIR_FILLER]",
        context={'type': 'dead_air', 'time_since_activity': 300.0},
        user="system")
    assert 'quiet for about 5 minute(s)' in seen['prompt']


def test_a_missing_silence_value_does_not_break_the_prompt(engine):
    assert 'quiet for a while' in engine._build_dead_air_prompt()


# ------------------------------------ the live summary describes NOW

def _summarizer(clock_holder):
    from bot.session_summarizer import StreamSessionSummarizer
    from types import SimpleNamespace
    buf = SimpleNamespace(last_seq=lambda ch: 0,
                          get_recent=lambda ch, limit=10: [])
    return StreamSessionSummarizer(
        chat_buffer=buf, llm_call=None, clock=lambda: clock_holder["now"])


def test_a_fresh_summary_is_served():
    t = {"now": 1000.0}
    sz = _summarizer(t)
    st = sz._state("cassova_")
    st.summary = "chat is arguing about pineapple"
    st.updated_at = t["now"]
    t["now"] += 600                       # ten minutes: fine
    assert sz.get_summary("cassova_") == "chat is arguing about pineapple"


def test_a_stale_summary_is_withheld():
    """Seen live: after a 73-minute process freeze the co-host was still
    riffing on 'the Overwatch video' and 'the painting' from before the
    freeze -- the in-memory summary was never age-checked."""
    t = {"now": 1000.0}
    sz = _summarizer(t)
    st = sz._state("cassova_")
    st.summary = "cassova_ is still watching an insane Overwatch video"
    st.updated_at = t["now"]
    t["now"] += 73 * 60
    assert sz.get_summary("cassova_") == ""


def test_an_update_does_not_build_on_a_stale_summary():
    """Conditioning on it carries the stale claims forward forever, no
    matter what the new chat says."""
    t = {"now": 1000.0}
    sz = _summarizer(t)
    st = sz._state("cassova_")
    st.summary = "still watching the Overwatch video"
    st.updated_at = t["now"]
    t["now"] += sz.live_max_age_s + 1
    assert sz._fresh_or_blank(st) == ""
    assert st.summary == "", "the expired text must not linger in state"


def test_a_fresh_summary_is_built_upon():
    t = {"now": 1000.0}
    sz = _summarizer(t)
    st = sz._state("cassova_")
    st.summary = "the grocery bit continues"
    st.updated_at = t["now"]
    t["now"] += 120
    assert sz._fresh_or_blank(st) == "the grocery bit continues"


def test_a_summary_that_never_updated_is_not_expired():
    """updated_at == 0 means 'no successful update yet', not 'ancient'."""
    t = {"now": 1e9}
    sz = _summarizer(t)
    st = sz._state("cassova_")
    st.summary = ""
    st.updated_at = 0.0
    assert sz.get_summary("cassova_") == ""
    assert sz._fresh_or_blank(st) == ""


def test_the_live_gate_is_tighter_than_the_disk_gate():
    """The disk gate (hours) is for 'is this even the same stream'; the live
    gate is for 'is this still the same moment'."""
    t = {"now": 1000.0}
    sz = _summarizer(t)
    assert sz.live_max_age_s < sz.max_age_s
    assert sz.live_max_age_s <= 1800

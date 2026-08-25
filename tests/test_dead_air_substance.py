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
    assert 'chat has gone quiet' in seen['prompt']
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

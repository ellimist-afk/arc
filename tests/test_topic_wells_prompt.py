"""Topic wells in the engine: well=None is today's filler prompt byte for
byte, a non-game well drops the game option and points at its directive,
and the knowledge block renders the directive under one heading."""

from personality.personality_engine import PersonalityEngine

# Today's prompt tail, copied from the engine at 9f4321f. Do not edit to
# make a test pass: a difference here is a change to every filler.
TODAY = (
    "\n\nChat has been quiet for about 2 minute(s). Any chat lines you can see "
    "are from BEFORE that silence, so they are stale: do NOT reply to them, "
    "do NOT continue that thread, and do NOT retell a joke or topic you have "
    "already used tonight. Open something NEW.\n"
    "Break the silence with something "
    "specific: react to what is happening in the game, call back to a joke "
    "or moment from earlier this stream, ask chat a real question worth "
    "answering, or give an opinion someone would argue with. NEVER remark on "
    "the silence itself, never ask if anyone is there, and never announce that "
    "you are filling dead air -- a co-host just talks. One or two sentences."
)


def _engine():
    engine = PersonalityEngine.__new__(PersonalityEngine)
    engine._build_personality_prompt = lambda: "PERSONA"
    return engine


def test_well_none_is_todays_prompt_byte_for_byte():
    engine = _engine()
    assert engine._build_dead_air_prompt(120.0) == "PERSONA" + TODAY
    assert engine._build_dead_air_prompt(120.0, well=None) == "PERSONA" + TODAY
    assert engine._build_dead_air_prompt(120.0, well='game') == "PERSONA" + TODAY
    steered = TODAY.replace("react to what is happening in the game, ", "")
    assert engine._build_dead_air_prompt(120.0, avoid_screen=True) == "PERSONA" + steered


def test_non_game_well_drops_the_game_option():
    for well in ('streamer_said', 'earlier_tonight', 'ask_chat',
                 'viewer_callback', 'off_topic'):
        out = _engine()._build_dead_air_prompt(120.0, well=well)
        assert "happening in the game" not in out, well
        assert "This line's topic" in out, well
        assert "nothing about the game" in out, well
        assert "NEVER remark on the silence itself" in out, well


def _render(context):
    return PersonalityEngine._format_context_knowledge(
        PersonalityEngine.__new__(PersonalityEngine), context, "system")


def test_directive_renders_under_one_heading():
    out = _render({'well': 'off_topic', 'well_material': {'topic': 'food'},
                   'well_directive': 'Say one line about: food.'})
    assert "- This line's topic (off_topic): Say one line about: food." in out
    assert out.count("This line's topic") == 1


def test_game_well_renders_no_topic_heading():
    out = _render({'well': 'game', 'well_directive': 'x', 'stream_now': 'OW2'})
    assert "This line's topic" not in out
    assert "Stream right now: OW2" in out


def test_both_generation_paths_pass_the_well():
    src = open('src/personality/personality_engine.py', encoding='utf-8').read()
    assert src.count("well=context.get('well'))") == 2

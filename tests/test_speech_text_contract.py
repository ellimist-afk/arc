"""
Contract: speech_text is fed to TTS verbatim, so it must always be a
non-empty bare spoken line.

The deadpan prompt rewrite added register anchors formatted as
`scenario: "quoted line"`. Without a counter-instruction on output shape the
model echoed that framing back and TTS received quotes, labels and stage
directions — or nothing speakable at all. The prompt must pin the output
format after the anchors, and the engine must strip anchor framing while
never returning an empty speech_text. Same contract on the streamed path.

Also covers: the GPT-5 parameter adaptation the rewrite was tuned for, and
preset-name tracking that selects the per-preset anchors.
"""
from types import SimpleNamespace

import pytest

from personality.personality_engine import PersonalityEngine

PRESETS = ['sassy', 'uwu', 'cryptid', 'chaos']

# Completion shapes seen when the model mimics the register-anchor examples
FRAMED = {
    'scenario-label': 'Someone whiffs a shot: "Never seen anyone kill '
                      'someone before. Truly groundbreaking stuff."',
    'quote-wrapped': '"Zero endorsements and total radio silence."',
    'bot-name-prefix': 'elimist_: witness protection called, they want '
                       'their lurker back.',
    'stage-direction-only': '*stares in cryptid*',
}


@pytest.fixture
def engine():
    eng = PersonalityEngine(memory_system=None)
    eng.bot_name = 'elimist_'
    eng.should_respond_override = True
    return eng


async def switch(eng, name):
    assert await eng.switch_personality_by_name(name), f'unknown preset {name}'


# ------------------------------------------------------------------ prompt

@pytest.mark.parametrize('preset', PRESETS)
async def test_prompt_demands_bare_spoken_line(engine, preset):
    await switch(engine, preset)
    prompt = engine._build_personality_prompt()
    assert 'Output format' in prompt
    assert 'Register anchors' in prompt, preset
    # the output contract must come after the quoted examples it overrides
    assert prompt.rindex('Output format') > prompt.index('Register anchors')


async def test_named_presets_get_their_own_anchors_and_sassy_gets_deadpan(engine):
    await switch(engine, 'uwu')
    assert 'cutest little crime scene' in engine._build_personality_prompt()
    await switch(engine, 'cryptid')
    assert 'three hundred years' in engine._build_personality_prompt()
    await switch(engine, 'chaos')
    assert 'sack of Rome' in engine._build_personality_prompt()
    await switch(engine, 'sassy')
    p = engine._build_personality_prompt()
    assert 'witness protection' in p and 'crime scene' not in p


async def test_identity_line_uses_bot_name(engine):
    assert engine._build_personality_prompt().startswith('You are elimist_, the AI co-host')
    engine.bot_name = None
    assert engine._build_personality_prompt().startswith('You are the AI co-host')


async def test_low_sarcasm_preset_has_no_anchors(engine):
    await switch(engine, 'friendly')
    p = engine._build_personality_prompt()
    assert 'Register anchors' not in p and 'Output format' in p


# ------------------------------------------------------------- blocking path

@pytest.mark.parametrize('preset', PRESETS)
@pytest.mark.parametrize('raw', list(FRAMED.values()), ids=list(FRAMED))
async def test_speech_text_bare_and_populated(engine, preset, raw, monkeypatch):
    await switch(engine, preset)

    async def fake_generate(**kwargs):
        return raw

    monkeypatch.setattr(engine, '_generate_text', fake_generate)

    response = await engine.generate_response(
        message='hey elimist_', context={}, user='cassova_', is_mention=True)

    assert response is not None
    speech = response['speech_text']
    assert speech and speech.strip(), 'speech_text must always be populated'
    assert '*' not in speech and '[' not in speech
    assert not speech.startswith(('"', '“'))
    assert not speech.lower().startswith('someone whiffs')
    assert not speech.lower().startswith('elimist_')
    # chat text is styled from the parsed line, not the framed raw
    assert not response['text'].startswith(('"', 'someone whiffs', 'elimist_'))


def test_parse_keeps_inner_quotes_and_punctuation(engine):
    raw = 'Chat said "gg" and then left. Bold.'
    assert engine._parse_speech_text(raw) == raw


# ------------------------------------------------------------- streamed path

def chunk(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class FakeStream:
    def __init__(self, text, n=4):
        self.parts = [text[i:i + n] for i in range(0, len(text), n)]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.parts:
            raise StopAsyncIteration
        return chunk(self.parts.pop(0))


async def _collect(reply):
    out = []
    async for s in reply.sentences:
        out.append(s)
    return out


@pytest.mark.parametrize('raw, expected', [
    ('elimist_: "Never seen that before. Truly groundbreaking stuff."',
     ['Never seen that before.', 'Truly groundbreaking stuff.']),
    ('Someone whiffs a shot: "That was the worst shot ever. Genuinely impressive."',
     ['That was the worst shot ever.', 'Genuinely impressive.']),
    ('*leans in* Chat is unhinged tonight. [beat] Respectfully.',
     ['Chat is unhinged tonight.', 'Respectfully.']),
])
async def test_streamed_sentences_are_bare(engine, raw, expected):
    engine.openai_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: _aret(FakeStream(raw)))))
    reply = await engine.generate_response_streamed('hey', {}, 'cassova_', is_mention=True,
                                                    min_sentence_chars=1)
    got = await _collect(reply)
    assert got == expected
    assert reply.speech_text == ' '.join(expected)


async def _aret(v):
    return v


# ------------------------------------------------------------- gpt-5 params

def test_gpt5_param_adaptation(engine):
    engine.llm_model = 'gpt-5.5'
    engine.response_modifiers = {'temperature': 0.9, 'max_tokens': 120, 'presence_penalty': 0.3}
    p = engine._llm_params()
    assert 'max_tokens' not in p and p['max_completion_tokens'] == 120
    assert p['reasoning_effort'] == 'none'
    assert p['temperature'] == 0.9

    engine.llm_model = 'gpt-4o-mini'
    p = engine._llm_params()
    assert p == {'temperature': 0.9, 'max_tokens': 120, 'presence_penalty': 0.3}


async def test_switch_preset_by_enum_clears_named_anchor_selection(engine):
    from personality.personality_engine import PersonalityPreset
    await switch(engine, 'uwu')
    assert engine.current_personality_name == 'uwu'
    await engine.switch_preset(PersonalityPreset.FRIENDLY)
    assert engine.current_personality_name is None

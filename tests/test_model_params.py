"""One mapping from generic params to what each model's endpoint accepts.

OpenRouter ids carry a provider prefix (`openai/gpt-5.5`, `x-ai/grok-4.7`).
The old adapter matched `startswith('gpt-5')`, so `openai/gpt-5.5` would have
been sent max_tokens without a reasoning effort, and the screen watcher sent
max_completion_tokens to every model.
"""
import pytest

from personality.model_params import adapt_params, model_family, lowest_reasoning_effort

BASE = {'max_tokens': 108, 'temperature': 0.9,
        'presence_penalty': 0.4, 'frequency_penalty': 0.3}


@pytest.mark.parametrize('model, family', [
    ('gpt-5.5', 'gpt-5.5'), ('openai/gpt-5.5', 'gpt-5.5'),
    ('x-ai/grok-4.7', 'grok-4.7'), ('', ''), (None, ''),
])
def test_family_strips_the_provider_prefix(model, family):
    assert model_family(model) == family


@pytest.mark.parametrize('model, effort', [
    ('gpt-5.5', 'none'), ('openai/gpt-5.5', 'none'),
    ('gpt-5-mini', 'minimal'), ('openai/gpt-5-mini', 'minimal'),
])
def test_prefixed_gpt5_is_still_gpt5(model, effort):
    p = adapt_params(model, BASE)
    assert p['max_completion_tokens'] == 108 and 'max_tokens' not in p
    assert p['reasoning_effort'] == effort
    assert lowest_reasoning_effort(model) == effort


def test_grok_gets_classic_params_without_penalties():
    p = adapt_params('x-ai/grok-4.7', BASE)
    assert p == {'max_tokens': 108, 'temperature': 0.9}


@pytest.mark.parametrize('model', ['google/gemini-3-flash', 'anthropic/claude-x'])
def test_other_strict_providers_drop_penalties(model):
    assert 'presence_penalty' not in adapt_params(model, BASE)
    assert 'frequency_penalty' not in adapt_params(model, BASE)


def test_other_models_keep_penalties_and_get_no_effort():
    p = adapt_params('meta-llama/llama-4', dict(BASE, reasoning_effort='high'))
    assert p == BASE


def test_max_completion_tokens_is_mapped_back_for_non_gpt5():
    assert adapt_params('x-ai/grok-4.7', {'max_completion_tokens': 80}) == {'max_tokens': 80}


def test_input_is_not_mutated():
    src = dict(BASE)
    adapt_params('gpt-5.5', src)
    assert src == BASE


def test_engine_uses_the_helper_for_prefixed_ids():
    from personality.personality_engine import PersonalityEngine
    e = PersonalityEngine.__new__(PersonalityEngine)
    e.llm_model = 'openai/gpt-5.5'
    assert e._adapt_openai_params({'max_tokens': 50}) == {
        'max_completion_tokens': 50, 'reasoning_effort': 'none'}
    e.llm_model = 'x-ai/grok-4.7'
    assert e._adapt_openai_params(dict(BASE)) == {'max_tokens': 108, 'temperature': 0.9}

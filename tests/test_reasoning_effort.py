"""The lowest reasoning effort is model-specific and the values don't overlap.

Measured against the live API 2026-08-29: gpt-5.5 accepts 'none' and returns
400 for 'minimal'; gpt-5-mini accepts 'minimal' and returns 400 for 'none'.
The adapter sent 'none' to every gpt-5* model, so changing llm_model to any
non-5.5 variant would have failed every single reply with a hard 400.
"""
import pytest

from personality.personality_engine import PersonalityEngine


def _engine(model):
    e = PersonalityEngine.__new__(PersonalityEngine)
    e.llm_model = model
    return e


@pytest.mark.parametrize('model, expected', [
    ('gpt-5.5', 'none'),
    ('gpt-5.5-turbo', 'none'),
    ('gpt-5-mini', 'minimal'),
    ('gpt-5', 'minimal'),
    ('gpt-5-nano', 'minimal'),
])
def test_each_model_gets_an_effort_it_accepts(model, expected):
    assert _engine(model)._adapt_openai_params(
        {'max_tokens': 108})['reasoning_effort'] == expected


def test_max_tokens_is_renamed_for_the_gpt5_family():
    p = _engine('gpt-5.5')._adapt_openai_params({'max_tokens': 108})
    assert p['max_completion_tokens'] == 108
    assert 'max_tokens' not in p


def test_non_gpt5_models_are_left_alone():
    p = _engine('gpt-4o-mini')._adapt_openai_params({'max_tokens': 108})
    assert p == {'max_tokens': 108}, "no renaming, no reasoning_effort"


def test_an_explicit_effort_is_respected():
    p = _engine('gpt-5.5')._adapt_openai_params(
        {'max_tokens': 108, 'reasoning_effort': 'high'})
    assert p['reasoning_effort'] == 'high', "setdefault must not override a caller"


def test_a_missing_model_does_not_crash():
    e = PersonalityEngine.__new__(PersonalityEngine)
    e.llm_model = None
    assert e._adapt_openai_params({'max_tokens': 50}) == {'max_tokens': 50}

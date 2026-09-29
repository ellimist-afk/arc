"""Map generic chat-completion params onto what a given model's endpoint accepts.

Model ids arrive bare (`gpt-5.5`, on the OpenAI API) or with a provider
prefix (`openai/gpt-5.5`, `x-ai/grok-4.7`, on OpenRouter). The family is the
part after the prefix.

GPT-5 family: 'max_tokens' becomes 'max_completion_tokens', and temperature
and penalties are only accepted at the lowest reasoning effort -- also the
fastest setting. That lowest value is model-specific and does NOT overlap:
gpt-5.5 takes 'none' and rejects 'minimal', gpt-5-mini takes 'minimal' and
rejects 'none'. Either wrong value is a hard 400.

Everything else gets the classic shape: 'max_tokens', temperature kept, no
'reasoning_effort'. Presence/frequency penalties are dropped for providers
that do not reliably accept them (x-ai, google, anthropic).
"""
from typing import Any, Dict, Optional

_NO_PENALTY_PREFIXES = ('x-ai/', 'google/', 'anthropic/')
_PENALTIES = ('presence_penalty', 'frequency_penalty')


def model_family(model: Optional[str]) -> str:
    """`openai/gpt-5.5` -> `gpt-5.5`; a bare id is returned unchanged."""
    model = model or ''
    return model.split('/', 1)[1] if '/' in model else model


def lowest_reasoning_effort(model: Optional[str]) -> str:
    """The cheapest reasoning setting this GPT-5 model actually accepts."""
    return 'none' if model_family(model).startswith('gpt-5.5') else 'minimal'


def adapt_params(model: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new dict of params the endpoint serving `model` accepts."""
    adapted = dict(params)
    family = model_family(model)
    if family.startswith('gpt-5'):
        if 'max_tokens' in adapted:
            adapted['max_completion_tokens'] = adapted.pop('max_tokens')
        adapted.setdefault('reasoning_effort', lowest_reasoning_effort(model))
        return adapted
    if 'max_completion_tokens' in adapted:
        limit = adapted.pop('max_completion_tokens')
        adapted.setdefault('max_tokens', limit)
    adapted.pop('reasoning_effort', None)
    if (model or '').startswith(_NO_PENALTY_PREFIXES):
        for key in _PENALTIES:
            adapted.pop(key, None)
    return adapted

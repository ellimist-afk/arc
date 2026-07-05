"""Trigger normalization: "hey b..." misheard as "play ..." must still fire.

Observed on stream 2026-07-05: the recognizer produced 'play not the volume'
for a "hey b..." utterance, and the exact-substring trigger check could never
match. Pure string logic -- no recognizer, audio device, or bot instance.
"""
import pytest

from components.voice.trigger_match import (
    match_hey_trigger,
    normalize_leading_hey,
    _within_edit_distance_1,
)


@pytest.mark.parametrize('text, expected_how', [
    # Exact matches: original behavior preserved
    ('hey bud can you hear me', 'exact'),
    ('hey boss what do you think', 'exact'),
    ('hay bot hello', 'exact'),
    ('hey bott', 'exact'),
    # Normalized: misheard leading word, clean second token
    ('play bot are you there', 'normalized'),
    ('okay boss what next', 'normalized'),
    ('hay buddy how are you', 'normalized'),
    # Fuzzy: misheard leading word AND second token off by one edit
    # -- the actual transcript from the stream log
    ('play not the volume', 'fuzzy'),
    ('hey bus can you help', 'fuzzy'),
])
def test_trigger_fires(text, expected_how):
    matched, how = match_hey_trigger(text)
    assert matched, f'{text!r} should trigger'
    assert how == expected_how


@pytest.mark.parametrize('text', [
    # First token is not a known mishearing of 'hey'
    ('playing some music now'),
    ('that was a good play'),
    # First token qualifies but nothing downstream resembles a trigger
    ('play despacito'),
    ('a complete sentence here'),
    ('they said the stream looks great'),
    # Known miss, documented: the trigger word itself was dropped by the
    # recognizer ('hey bud turn back on' -> 'play turn back on...'), so
    # nothing is left to match against. Normalization cannot recover it.
    ('play turn back on it has memories now too so'),
    (''),
])
def test_no_false_trigger(text):
    matched, how = match_hey_trigger(text)
    assert not matched, f'{text!r} should NOT trigger (got {how})'


def test_normalize_leading_hey():
    assert normalize_leading_hey('play not the volume') == 'hey not the volume'
    assert normalize_leading_hey('okay boss') == 'hey boss'
    assert normalize_leading_hey('hello there') == ''
    assert normalize_leading_hey('') == ''
    assert normalize_leading_hey('play') == 'hey'


@pytest.mark.parametrize('a, b, close', [
    ('not', 'bot', True),    # substitution
    ('bud', 'bud', True),    # identical
    ('bu', 'bud', True),     # deletion
    ('budd', 'bud', True),   # insertion
    ('turn', 'bot', False),
    ('turn', 'bud', False),
    ('despacito', 'bot', False),
])
def test_edit_distance_gate(a, b, close):
    assert _within_edit_distance_1(a, b) is close

"""Voice dedup must catch Whisper re-emits without swallowing real speech.

The filter tested containment in BOTH directions against the last five
utterances, so a single short filler poisoned everything after it: once
"okay" or "yeah" was recorded, any later sentence containing that word --
including a direct address to the co-host -- was dropped with only a debug
log. Short fillers are the most common thing Whisper transcribes.
"""
import pytest

from bot.bot import TalkBot


def _bot(recent=()):
    bot = TalkBot.__new__(TalkBot)
    bot.recent_voice_texts = [TalkBot._normalize_voice_text(r) for r in recent]
    return bot


norm = TalkBot._normalize_voice_text


# ------------------------------------------------------------ normalizing

@pytest.mark.parametrize('raw, expected', [
    ('Hey bot!', 'hey bot'),
    ('  YEAH.  ', 'yeah'),
    ("that's insane", "that's insane"),
    ('what?! really...', 'what really'),
    ('', ''),
    (None, ''),
])
def test_normalization(raw, expected):
    assert norm(raw) == expected


# --------------------------------------------------------- the reported bug

@pytest.mark.parametrize('phrase', [
    'yeah that was insane, hey bot what do you think',
    'okay so the plan is to push left',
    'no okay wait, hey bot are you seeing this',
])
def test_a_short_filler_does_not_silence_later_sentences(phrase):
    bot = _bot(['yeah', 'okay', 'no'])
    assert not bot._is_duplicate_voice(norm(phrase)), phrase


def test_a_direct_address_survives_earlier_chatter():
    bot = _bot(['yeah', 'okay', 'right', 'wait', 'what'])
    assert not bot._is_duplicate_voice(norm('hey bot what do you think of this build'))


# ------------------------------------------------- genuine repeats still go

def test_exact_repeat_is_filtered():
    bot = _bot(['hey bot what do you think'])
    assert bot._is_duplicate_voice(norm('hey bot what do you think'))


def test_punctuation_only_difference_is_filtered():
    """Whisper punctuates the same utterance differently across takes."""
    bot = _bot(['hey bot what do you think'])
    assert bot._is_duplicate_voice(norm('Hey bot, what do you think?'))


def test_case_only_difference_is_filtered():
    bot = _bot(['yeah'])
    assert bot._is_duplicate_voice(norm('YEAH'))


def test_a_trailing_word_still_counts_as_a_re_emit():
    bot = _bot(['that was a disaster honestly'])
    assert bot._is_duplicate_voice(norm('that was a disaster honestly wow'))


def test_a_much_longer_sentence_is_not_a_re_emit():
    bot = _bot(['that was a disaster'])
    assert not bot._is_duplicate_voice(
        norm('that was a disaster and now we have to do the whole thing again'))


# ------------------------------------------------------------- edge cases

def test_empty_input_is_never_a_duplicate():
    assert not _bot(['yeah'])._is_duplicate_voice('')


def test_empty_history_never_filters():
    assert not _bot()._is_duplicate_voice(norm('hey bot'))


def test_blank_history_entries_are_skipped():
    bot = _bot()
    bot.recent_voice_texts = ['', '   ', 'yeah']
    assert not bot._is_duplicate_voice(norm('hey bot what is up'))
    assert bot._is_duplicate_voice(norm('yeah'))


def test_only_the_last_five_are_compared():
    bot = _bot(['old line here', 'a', 'b', 'c', 'd', 'e'])
    assert not bot._is_duplicate_voice(norm('old line here')), \
        "an utterance six back has aged out of the window"


def test_ratio_is_tunable():
    bot = _bot(['short phrase'])
    long_one = norm('short phrase with several extra words appended to it')
    assert not bot._is_duplicate_voice(long_one)
    assert bot._is_duplicate_voice(long_one, ratio=0.1)

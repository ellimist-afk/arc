"""Voice commands must not fire on ordinary speech.

The wake word "bot" was tested with a bare substring check, so it matched
inside "both", "robot", "bottle" and "sabotage" -- saying "both of us need
to be quiet" muted the co-host for the rest of the stream. The skip and
repeat commands needed no wake word at all and matched the bare words
"skip", "next" and "what did you say", all of which are said constantly
mid-game. And bare "talk" in the unmute pattern swallowed "talk less",
which is registered later, so asking for less chatter unmuted instead.
"""
import re

import pytest

from components.voice.voice_commands import CommandType, VoiceCommandSystem


@pytest.fixture
def vcs():
    return VoiceCommandSystem()


def fired(vcs, text):
    """Which commands process_input would run, in its own order."""
    text = text.lower().strip()
    wake = bool(vcs._wake_pattern.search(text))
    out = []
    for name, command in vcs.commands.items():
        match = re.search(command.pattern, text)
        if match:
            if not wake:
                if command.type != CommandType.MEDIA:
                    continue
                if match.start() != 0:
                    continue
            out.append(name)
    return out


# --------------------------------------------------- wake word boundaries

@pytest.mark.parametrize('phrase', [
    'both of us need to be quiet here',
    'i have both ults, shut up chat',
    'that was a bottle rocket, silence him',
    'robot voice sounds weird',
    'sabotage the point',
    'the bottom lane is quiet',
])
def test_words_containing_bot_are_not_wake_words(vcs, phrase):
    assert not vcs._wake_pattern.search(phrase), phrase
    assert fired(vcs, phrase) == [], f"{phrase!r} must not run a command"


@pytest.mark.parametrize('phrase', ['hey bot be quiet', 'ok bot louder',
                                    'yo bot talk less', 'bot mute'])
def test_real_wake_words_still_match(vcs, phrase):
    assert vcs._wake_pattern.search(phrase), phrase


def test_wake_words_can_be_replaced(vcs):
    vcs.set_wake_words(['arc'])
    assert vcs._wake_pattern.search('hey arc mute')
    assert not vcs._wake_pattern.search('architecture')
    assert not vcs._wake_pattern.search('hey bot mute')


def test_empty_wake_words_match_nothing(vcs):
    vcs.set_wake_words([])
    assert not vcs._wake_pattern.search('hey bot mute')


# ------------------------------------------- wake-word-free MEDIA commands

@pytest.mark.parametrize('phrase', [
    'okay next round',
    'skip the first objective',
    'we should skip this boss',      # "skip this" mid-sentence, no wake word
    'what did you say?',
    'i said next',
])
def test_ordinary_game_talk_does_not_control_the_bot(vcs, phrase):
    hits = [h for h in fired(vcs, phrase) if h in ('skip', 'repeat')]
    assert hits == [], f"{phrase!r} fired {hits}"


@pytest.mark.parametrize('phrase, expected', [
    ('skip that', 'skip'),
    ('skip it', 'skip'),
    ('stop talking', 'skip'),
    ('say that again', 'repeat'),
    ('repeat that', 'repeat'),
])
def test_deliberate_media_commands_still_work(vcs, phrase, expected):
    assert expected in fired(vcs, phrase), phrase


# ------------------------------------------------------ pattern collisions

def test_talk_less_is_not_an_unmute(vcs):
    """unmute is registered first, so a bare "talk" in its pattern won."""
    hits = fired(vcs, 'hey bot talk less')
    assert 'respond_less' in hits
    assert 'unmute' not in hits, hits


def test_talk_more_is_not_an_unmute(vcs):
    hits = fired(vcs, 'hey bot talk more')
    assert 'respond_more' in hits
    assert 'unmute' not in hits, hits


def test_unmute_still_works(vcs):
    for phrase in ('hey bot unmute', 'hey bot you can talk'):
        assert 'unmute' in fired(vcs, phrase), phrase


@pytest.mark.parametrize('phrase, expected', [
    ('hey bot be quiet', 'mute'),
    ('hey bot shut up', 'mute'),
    ('ok bot louder', 'volume'),
    ('hey bot toggle tts', 'toggle_tts'),
])
def test_control_commands_need_a_wake_word_and_still_work(vcs, phrase, expected):
    assert expected in fired(vcs, phrase), phrase
    bare = phrase.replace('hey bot ', '').replace('ok bot ', '')
    assert expected not in fired(vcs, bare), f"{bare!r} fired without a wake word"


# ------------------------------------------------------------- regression

def test_no_control_characters_in_any_pattern(vcs):
    """A patch once wrote literal backspace bytes instead of the \\b escape,
    which silently turned every boundary into an unmatchable control char."""
    for name, command in vcs.commands.items():
        assert '\x08' not in command.pattern, name
        re.compile(command.pattern)          # must be a valid regex
    assert '\x08' not in vcs._wake_pattern.pattern

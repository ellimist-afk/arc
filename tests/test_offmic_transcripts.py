"""Off-mic speech must not become a turn the co-host reacts to.

Talking away from the microphone still passes Silero VAD -- the audio is
quiet but real -- so Whisper decodes it into confident-looking garbage. That
became a "streamer turn", the co-host remarked on not understanding it, and
once such a remark reached the rolling session summary it was injected into
every later prompt, so it kept coming back.
"""
from types import SimpleNamespace

import pytest

from components.voice.recognition import VoiceRecognition
from personality.personality_engine import PersonalityEngine


def seg(text, no_speech=0.05, logprob=-0.2):
    return SimpleNamespace(text=text, no_speech_prob=no_speech, avg_logprob=logprob)


def _recognizer(segments):
    vr = VoiceRecognition.__new__(VoiceRecognition)
    vr.asr_engine = 'whisper'
    vr.max_no_speech_prob = 0.6
    vr.min_avg_logprob = -1.0
    vr.low_confidence_drops = 0
    vr._whisper_model = SimpleNamespace(
        transcribe=lambda *a, **k: (iter(segments), SimpleNamespace()))
    return vr


def _transcribe(vr):
    audio = SimpleNamespace(get_raw_data=lambda **k: b"\x00\x00" * 1600)
    return vr._transcribe(audio)


# ------------------------------------------------------- confidence gating

def test_clear_speech_is_kept():
    vr = _recognizer([seg("hey bot what do you think")])
    assert _transcribe(vr) == "hey bot what do you think"
    assert vr.low_confidence_drops == 0


def test_off_mic_garbage_is_dropped():
    """Low avg_logprob is what a distant, quiet decode looks like."""
    import speech_recognition as sr
    vr = _recognizer([seg("mumble something inaudible", logprob=-1.8)])
    with pytest.raises(sr.UnknownValueError):
        _transcribe(vr)
    assert vr.low_confidence_drops == 1


def test_non_speech_segments_are_dropped():
    import speech_recognition as sr
    vr = _recognizer([seg("You", no_speech=0.95)])
    with pytest.raises(sr.UnknownValueError):
        _transcribe(vr)
    assert vr.low_confidence_drops == 1


def test_a_clear_segment_survives_a_garbled_neighbour():
    vr = _recognizer([
        seg("uhh", logprob=-2.4),
        seg("hey bot did you see that"),
    ])
    assert _transcribe(vr) == "hey bot did you see that"
    assert vr.low_confidence_drops == 1


def test_thresholds_are_tunable():
    vr = _recognizer([seg("borderline", logprob=-1.5)])
    vr.min_avg_logprob = -2.0
    assert _transcribe(vr) == "borderline"


def test_missing_confidence_fields_do_not_crash():
    """Older faster-whisper builds may not populate every field."""
    vr = _recognizer([SimpleNamespace(text="hello there")])
    assert _transcribe(vr) == "hello there"


def test_none_logprob_is_treated_as_neutral():
    vr = _recognizer([seg("hello there", logprob=None)])
    assert _transcribe(vr) == "hello there"


def test_punctuation_is_still_stripped():
    vr = _recognizer([seg("hey, bud. what's up?")])
    assert _transcribe(vr) == "hey bud what's up"


# ---------------------------------------------------------- the prompt rule

@pytest.fixture
def engine():
    return PersonalityEngine(memory_system=None)


def test_prompt_forbids_narrating_technical_problems(engine):
    p = engine._build_personality_prompt().lower()
    assert 'never narrate technical problems' in p
    for topic in ('microphone', 'audio', 'not being able to hear'):
        assert topic in p, topic


def test_prompt_says_what_to_do_with_a_garbled_line(engine):
    p = engine._build_personality_prompt().lower()
    assert 'plausibly meant' in p or 'last clear topic' in p
    assert 'never point out that you missed it' in p

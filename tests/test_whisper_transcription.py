"""Whisper ASR swap: engine selection, google fallback, delivery contract.

Google reliably mangles short trigger words ("hey bud" -> "I'm gay but",
"hey bot" -> "hey boss"), so transcription moved to local faster-whisper on
CUDA. Capture, threading, and the single-delivery path (cdcf3f3) are
unchanged -- only the recognize call inside _audio_callback swapped.

Two tiers:
  - Pure-logic tests always run: stubbed transcriber/model, no GPU, no
    audio device, no model download.
  - WAV fixture tests run the real small.en on CUDA; they skip when
    faster-whisper or CUDA is unavailable or a fixture WAV has not been
    recorded yet (see tests/fixtures/README in the fixture list below).
"""
import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest
import speech_recognition as sr

from components.voice.recognition import VoiceRecognition
from components.voice.trigger_match import match_hey_trigger

FIXTURE_DIR = Path(__file__).parent / 'fixtures'


# ---------------------------------------------------------------------------
# Stubs (no GPU, no audio device)
# ---------------------------------------------------------------------------

class _Segment:
    def __init__(self, text):
        self.text = text


class _StubWhisperModel:
    """Stands in for faster_whisper.WhisperModel."""

    def __init__(self, texts):
        self.texts = texts

    def transcribe(self, samples, **kwargs):
        return [_Segment(t) for t in self.texts], None


class _StubAudioData:
    """Stands in for sr.AudioData -- 100 samples of int16 silence."""

    def get_raw_data(self, convert_rate=None, convert_width=None):
        return b'\x00\x00' * 100


# ---------------------------------------------------------------------------
# Pure-logic tests (always run)
# ---------------------------------------------------------------------------

async def test_single_delivery_preserved_with_whisper_engine():
    """The cdcf3f3 contract: exactly one delivery path per utterance."""
    vr = VoiceRecognition(asr_engine='whisper')
    vr._transcribe = lambda audio: 'hey bud can you hear me'
    received = []

    async def on_text(text):
        received.append(text)

    vr.on_text_recognized = on_text
    vr.main_loop = asyncio.get_running_loop()

    await asyncio.to_thread(vr._audio_callback, None, _StubAudioData())
    await asyncio.sleep(0.2)

    assert received == ['hey bud can you hear me']
    assert vr.audio_queue.empty(), (
        'text must not ALSO land on the queue -- single delivery path'
    )


async def test_queue_path_still_works_without_callback():
    vr = VoiceRecognition(asr_engine='whisper')
    vr._transcribe = lambda audio: 'hello there'
    await asyncio.to_thread(vr._audio_callback, None, _StubAudioData())
    assert vr.audio_queue.get_nowait() == 'hello there'


def test_load_failure_falls_back_to_google_loudly(monkeypatch, caplog):
    """Model-load failure must flip to google AND log at ERROR -- the
    operator must never stream on Google thinking they are on Whisper."""
    def _boom(*args, **kwargs):
        raise RuntimeError('forced load failure')

    monkeypatch.setitem(
        sys.modules, 'faster_whisper',
        types.SimpleNamespace(WhisperModel=_boom),
    )

    vr = VoiceRecognition(asr_engine='whisper')
    with caplog.at_level(logging.ERROR):
        assert vr._load_whisper_model() is False

    assert vr.asr_engine == 'google'
    assert vr._whisper_model is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any('WHISPER MODEL LOAD FAILED' in r.message for r in errors)
    assert any('falling back to Google' in r.message for r in errors)


class _RecordingWhisperModel:
    """Records constructor kwargs; raises on local-only when not 'cached'."""

    calls = []
    cached = True

    def __init__(self, *args, **kwargs):
        _RecordingWhisperModel.calls.append(kwargs)
        if kwargs.get('local_files_only') and not _RecordingWhisperModel.cached:
            raise FileNotFoundError('not in cache')


def _install_recording_model(monkeypatch, cached: bool):
    _RecordingWhisperModel.calls = []
    _RecordingWhisperModel.cached = cached
    monkeypatch.setitem(
        sys.modules, 'faster_whisper',
        types.SimpleNamespace(WhisperModel=_RecordingWhisperModel),
    )


def test_cached_model_loads_offline_without_download(monkeypatch, caplog):
    """A cached model must load with local_files_only=True and never take
    the download path -- no hub round-trip at stream start."""
    _install_recording_model(monkeypatch, cached=True)
    vr = VoiceRecognition(asr_engine='whisper')
    with caplog.at_level(logging.INFO):
        assert vr._load_whisper_model() is True

    assert [c['local_files_only'] for c in _RecordingWhisperModel.calls] == [True]
    assert vr.asr_engine == 'whisper'
    assert any('from local cache' in r.message for r in caplog.records)


def test_uncached_model_falls_through_to_download(monkeypatch, caplog):
    """First run / new WHISPER_MODEL: cache miss must retry with download
    rather than failing over to google."""
    _install_recording_model(monkeypatch, cached=False)
    vr = VoiceRecognition(asr_engine='whisper')
    with caplog.at_level(logging.INFO):
        assert vr._load_whisper_model() is True

    assert [c['local_files_only'] for c in _RecordingWhisperModel.calls] == [True, False]
    assert vr.asr_engine == 'whisper'
    assert any('from download' in r.message for r in caplog.records)


def test_google_engine_uses_recognize_google():
    vr = VoiceRecognition(asr_engine='google')
    vr.recognizer = types.SimpleNamespace(
        recognize_google=lambda audio: 'hey bud'
    )
    assert vr._transcribe(_StubAudioData()) == 'hey bud'


def test_whisper_empty_transcript_raises_unknown_value():
    """Silence must surface as sr.UnknownValueError, exactly like
    recognize_google, so _audio_callback's except path swallows it."""
    vr = VoiceRecognition(asr_engine='whisper')
    vr._whisper_model = _StubWhisperModel([])
    with pytest.raises(sr.UnknownValueError):
        vr._transcribe(_StubAudioData())


def test_whisper_punctuation_stripped_for_trigger_contract():
    """Whisper emits punctuation Google never did; 'Hey, bud.' must still
    substring-match the 'hey bud' trigger downstream."""
    vr = VoiceRecognition(asr_engine='whisper')
    vr._whisper_model = _StubWhisperModel([' Hey, bud.', ' Can you hear me?'])
    text = vr._transcribe(_StubAudioData())
    assert text == 'Hey bud Can you hear me'
    assert match_hey_trigger(text.lower())[0]


# ---------------------------------------------------------------------------
# Real-model WAV fixture tests (skip without GPU stack or fixtures)
# ---------------------------------------------------------------------------

def _cuda_stack_available() -> bool:
    if importlib.util.find_spec('faster_whisper') is None:
        return False
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


requires_cuda_whisper = pytest.mark.skipif(
    not _cuda_stack_available(),
    reason='faster-whisper with CUDA not available',
)

# (fixture file, must_trigger) -- recorded by the operator at ~16kHz mono;
# _transcribe resamples, so any mono WAV works. Missing fixtures skip at
# collection time so the GPU model is never loaded for nothing.
FIXTURE_CASES = [
    pytest.param(name, must_trigger,
                 marks=pytest.mark.skipif(
                     not (FIXTURE_DIR / name).exists(),
                     reason=f'fixture not recorded yet: {name}'))
    for name, must_trigger in [
        ('hey_bud.wav', True),
        ('hey_bot.wav', True),
        ('hey_buddy.wav', True),
        ('hey_boss.wav', True),
        ('hey_bud_sentence.wav', True),
        ('negative_gay_rights.wav', False),
        ('negative_plain.wav', False),
    ]
]


@pytest.fixture(scope='module')
def real_whisper():
    vr = VoiceRecognition(asr_engine='whisper')
    if not vr._load_whisper_model():
        pytest.skip('Whisper model failed to load on this machine')
    return vr


@requires_cuda_whisper
@pytest.mark.parametrize('wav_name, must_trigger', FIXTURE_CASES)
def test_fixture_wav_trigger_outcome(real_whisper, wav_name, must_trigger):
    wav_path = FIXTURE_DIR / wav_name

    import soundfile as sf
    data, rate = sf.read(wav_path, dtype='int16')
    if data.ndim > 1:
        data = data[:, 0].copy()  # take one channel if stereo
    audio = sr.AudioData(data.tobytes(), rate, 2)

    try:
        text = real_whisper._transcribe(audio)
    except sr.UnknownValueError:
        text = ''
    matched, how = match_hey_trigger(text.lower())

    if must_trigger:
        assert matched, (
            f'{wav_name}: transcript {text!r} did not fire the trigger'
        )
    else:
        assert not matched, (
            f'{wav_name}: transcript {text!r} falsely fired the trigger '
            f'(via {how})'
        )

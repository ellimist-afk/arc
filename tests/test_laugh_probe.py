"""The laugh probe: instrumentation, and nothing else.

Alan never types in chat, and Whisper's VAD drops his laugh as non-speech,
so the loudest signal the co-host gets is invisible. Before anything is
wired to it we want one stream's worth of log lines saying whether a
detector fires on his real laughs -- and whether it also fires on the game
audio that shares the mic bus. These tests pin the shape of that probe: it
scores synthetic laughter above the floor and synthetic speech below it, it
never scores a piece Whisper found words in, it is never the reason the
microphone stops working, and its log line is in the post-stream audit.
"""
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from audit_stream_log import GUARDS, parse_sessions, render  # noqa: E402
from bot.bot import _read_laugh_probe  # noqa: E402
from components.voice.recognition import VoiceRecognition  # noqa: E402
from features.laugh_probe import (HeuristicLaughDetector,  # noqa: E402
                                  LaughProbe, OnnxLaughDetector, ProbeResult)

RATE = 16000
PROBE_SRC = Path("src/features/laugh_probe.py").read_text(encoding="utf-8")


# ------------------------------------------------------------- synthetics

def synth_laugh(seconds: float = 5.0, rate: int = RATE) -> np.ndarray:
    """Voiced bursts at 4.5 Hz, with breath above 1.5 kHz: a caricature of
    a laugh, not a recording of one. The probe's numbers are for reading the
    log, so a caricature is what the constants are set against."""
    t = np.arange(int(seconds * rate)) / rate
    am = 0.5 * (1 + np.sin(2 * np.pi * 4.5 * t))
    tone = np.sin(2 * np.pi * 220 * t)
    tone += 0.5 * np.sin(2 * np.pi * 440 * t) + 0.35 * np.sin(2 * np.pi * 880 * t)
    tone += 0.3 * np.sin(2 * np.pi * 1760 * t) + 0.2 * np.sin(2 * np.pi * 2640 * t)
    breath = _band(np.random.default_rng(3).normal(0, 1.0, t.size),
                   rate, 1500.0, 5000.0)
    return ((tone + 0.9 * breath) * am * 0.3).astype(np.float32)


def synth_speech(seconds: float = 5.0, rate: int = RATE) -> np.ndarray:
    """Band-limited noise plus a voiced low tone, modulated irregularly at
    ~2.5 Hz: strongly voiced, but with no 3-7 Hz syllable case."""
    t = np.arange(int(seconds * rate)) / rate
    noise = _band(np.random.default_rng(7).normal(0, 1.0, t.size),
                  rate, 300.0, 3400.0)
    phase = (2 * np.pi * 2.5 * t + 1.2 * np.sin(2 * np.pi * 0.4 * t)
             + 0.8 * np.sin(2 * np.pi * 0.17 * t))
    am = np.clip(0.5 * (1 + np.sin(phase)), 0.1, None)
    tone = 1.4 * np.sin(2 * np.pi * 120 * t) + 0.9 * np.sin(2 * np.pi * 240 * t)
    return ((noise + tone) * am * 0.3).astype(np.float32)


def synth_silence(seconds: float = 5.0, rate: int = RATE) -> np.ndarray:
    return np.random.default_rng(11).normal(
        0, 0.0005, int(seconds * rate)).astype(np.float32)


def _band(x, rate, lo, hi):
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, 1.0 / rate)
    spec[(freqs < lo) | (freqs > hi)] = 0
    out = np.fft.irfft(spec, n=x.size)
    return out / max(float(np.max(np.abs(out))), 1e-9)


# ----------------------------------------------------------- the heuristic

def test_the_synthetic_laugh_clears_the_floor():
    result = HeuristicLaughDetector().score(synth_laugh(), RATE)
    assert result.detector == "heuristic"
    assert result.score > 0.7, result.features
    assert result.features['mod_ratio'] > 0.5


def test_the_synthetic_speech_stays_under_the_floor():
    """Strongly voiced and reasonably bright -- and still not laughter,
    because the syllable-rate case is the one that has to be made."""
    result = HeuristicLaughDetector().score(synth_speech(), RATE)
    assert result.score < 0.3, result.features
    assert result.features['voiced'] > 0.5, "the speech synthetic is voiced"


def test_silence_scores_zero_without_measuring_anything():
    result = HeuristicLaughDetector().score(synth_silence(), RATE)
    assert result.score == 0.0
    assert 'mod_ratio' not in result.features, "a dead piece is not analysed"


def test_a_piece_too_short_to_frame_scores_zero():
    assert HeuristicLaughDetector().score(
        np.zeros(200, dtype=np.float32), RATE).score == 0.0


def test_the_heuristic_is_far_inside_the_latency_bound():
    """Standing constraint: this runs on the listener thread beside Whisper.
    The bound is 50 ms per 5 s piece; measured ~5 ms on the dev CPU."""
    import time
    piece = synth_laugh()
    detector = HeuristicLaughDetector()
    detector.score(piece, RATE)  # warm numpy
    best = min(_timed(detector, piece) for _ in range(5))
    assert best < 50.0, f"{best:.1f} ms per 5 s piece"


def _timed(detector, piece):
    import time
    started = time.perf_counter()
    detector.score(piece, RATE)
    return (time.perf_counter() - started) * 1000.0


# ---------------------------------------------------------------- the ONNX

def test_an_absent_model_is_unavailable_and_says_so_once(caplog):
    with caplog.at_level(logging.INFO):
        detector = OnnxLaughDetector("C:/nope/not-a-model.onnx")
    assert detector.available is False
    assert "laugh probe: no model at" in caplog.text
    # And scoring one is a no-op rather than a raise.
    assert detector.score(synth_laugh(), RATE).score == 0.0


def test_an_empty_path_is_the_default_and_downloads_nothing():
    assert OnnxLaughDetector("").available is False
    for fetcher in ("urllib", "requests", "httpx", "huggingface", "hf_hub",
                    "torch.hub", "urlretrieve"):
        assert fetcher not in PROBE_SRC, f"the probe must not fetch: {fetcher}"


def test_a_slow_model_is_dropped_for_the_session(caplog):
    """200 ms per piece is the budget; over it the probe stops paying."""
    class Slow(OnnxLaughDetector):
        def __init__(self):
            self.model_path, self._log = "fake.onnx", logging.getLogger("t")
            self._session, self._input, self.available = object(), "x", True

        def _run(self, batch):
            return np.zeros(32, dtype=np.float32)

    slow = Slow()
    slow._session = SimpleNamespace(
        run=lambda _o, _f: [np.zeros((1, 32), dtype=np.float32)])
    import features.laugh_probe as mod
    with caplog.at_level(logging.WARNING):
        original, mod.ONNX_BUDGET_MS = mod.ONNX_BUDGET_MS, -1.0
        try:
            slow.score(synth_laugh(), RATE)
        finally:
            mod.ONNX_BUDGET_MS = original
    assert slow.available is False
    assert "laugh probe: onnx too slow" in caplog.text


# --------------------------------------------------------------- the probe

def test_a_laugh_fires_one_line_with_the_audit_needle(caplog):
    probe = LaughProbe(floor=0.6)
    with caplog.at_level(logging.INFO):
        result = probe.observe(synth_laugh(), RATE, had_words=False)
    assert result is not None
    assert "[LAUGH-PROBE] fired" in caplog.text
    assert "detector=heuristic" in caplog.text
    assert "dur=5.0s" in caplog.text
    assert probe.stats()['fired'] == 1


def test_a_piece_with_words_is_never_scored():
    """Whisper already said that was speech; scoring it again only adds
    false fires to the log we are trying to read."""
    probe = LaughProbe(floor=0.6)
    assert probe.observe(synth_laugh(), RATE, had_words=True) is None
    assert probe.stats() == {'pieces': 1, 'fired': 0,
                             'max_score': 0.0, 'ms_per_piece': 0.0}


def test_speech_and_silence_leave_no_line(caplog):
    probe = LaughProbe(floor=0.6)
    with caplog.at_level(logging.INFO):
        assert probe.observe(synth_speech(), RATE, had_words=False) is None
        assert probe.observe(synth_silence(), RATE, had_words=False) is None
    assert "[LAUGH-PROBE] fired" not in caplog.text
    assert probe.stats()['pieces'] == 2 and probe.stats()['fired'] == 0


def test_the_best_detector_wins_and_is_named(caplog):
    class Loud:
        name = "stub"
        available = True

        def score(self, samples, rate):
            return ProbeResult(0.99, "stub", {})

    probe = LaughProbe(floor=0.6, detectors=[HeuristicLaughDetector(), Loud()])
    with caplog.at_level(logging.INFO):
        result = probe.observe(synth_speech(), RATE, had_words=False)
    assert result.detector == "stub"
    assert "detector=stub" in caplog.text


def test_an_unavailable_detector_is_not_asked():
    class Dead:
        name, available = "dead", False

        def score(self, samples, rate):
            raise AssertionError("an unavailable detector must not be run")

    probe = LaughProbe(floor=0.6, detectors=[Dead(), HeuristicLaughDetector()])
    assert probe.observe(synth_laugh(), RATE, had_words=False) is not None


def test_the_cost_is_reported_at_debug_once_a_minute(caplog):
    import features.laugh_probe as mod
    probe = LaughProbe(floor=0.6)
    with caplog.at_level(logging.DEBUG):
        probe.observe(synth_speech(), RATE, had_words=False)
        probe._last_cost_log -= mod.COST_LOG_INTERVAL_S + 1
        probe.observe(synth_speech(), RATE, had_words=False)
    assert "[LAUGH-PROBE] cost" in caplog.text
    assert probe.stats()['ms_per_piece'] > 0


# ------------------------------------------------------------- the wiring

class _Audio:
    """Stands in for speech_recognition's AudioData."""

    def __init__(self, samples):
        self._raw = (np.asarray(samples) * 32767).astype(np.int16).tobytes()

    def get_raw_data(self, convert_rate=None, convert_width=None):
        assert convert_rate == 16000 and convert_width == 2
        return self._raw


class _Spy:
    def __init__(self):
        self.calls = []

    def observe(self, samples, rate, had_words):
        self.calls.append((samples, rate, had_words))
        return None


def _recognition(probe):
    vr = VoiceRecognition.__new__(VoiceRecognition)
    vr.laugh_probe = probe
    return vr


def test_the_callback_reports_words_when_whisper_found_some():
    spy = _Spy()
    _recognition(spy)._probe_laughter(_Audio(synth_laugh()), "that was close")
    samples, rate, had_words = spy.calls[0]
    assert had_words is True and rate == 16000
    assert samples.dtype == np.float32 and np.max(np.abs(samples)) <= 1.0


@pytest.mark.parametrize("text", [None, "", "   "])
def test_a_piece_with_no_transcript_is_the_candidate(text):
    spy = _Spy()
    _recognition(spy)._probe_laughter(_Audio(synth_laugh()), text)
    assert spy.calls[0][2] is False


def test_no_probe_attached_is_the_default_and_costs_nothing():
    vr = _recognition(None)
    vr._probe_laughter(_Audio(synth_laugh()), "hello")  # must not raise
    assert "self.laugh_probe = None" in Path(
        "src/components/voice/recognition.py").read_text(encoding="utf-8")


def test_a_raising_probe_never_reaches_the_listener_thread(caplog):
    class Bad:
        def observe(self, samples, rate, had_words):
            raise RuntimeError("boom")

    with caplog.at_level(logging.DEBUG):
        _recognition(Bad())._probe_laughter(_Audio(synth_laugh()), None)
    assert "Laugh probe skipped" in caplog.text


def test_the_probe_sees_the_piece_whisper_raised_on():
    """_transcribe raises UnknownValueError when nothing intelligible was
    heard -- which is the laugh. The probe has to run before that leaves."""
    src = Path("src/components/voice/recognition.py").read_text(encoding="utf-8")
    body = src.split("def _audio_callback")[1].split("def _probe_laughter")[0]
    assert "finally:" in body and "self._probe_laughter(audio, text)" in body


# ------------------------------------------------------------ the settings

def test_the_settings_build_a_probe_with_the_configured_floor():
    probe = _read_laugh_probe({'laugh_probe': {'enabled': True, 'floor': 0.42,
                                               'onnx_model_path': ""}})
    assert probe.floor == 0.42
    assert [d.name for d in probe.detectors] == ["heuristic", "onnx"]


def test_a_hand_typed_floor_falls_back_instead_of_raising():
    assert _read_laugh_probe({'laugh_probe': {'floor': "0.6f"}}).floor == 0.6
    assert _read_laugh_probe({}).floor == 0.6
    assert _read_laugh_probe(None).floor == 0.6


def test_switching_it_off_attaches_nothing():
    assert _read_laugh_probe({'laugh_probe': {'enabled': False}}) is None


def test_the_shipped_settings_carry_the_probe_and_explain_it():
    cfg = json.loads(Path("bot_settings.json").read_text(encoding="utf-8"))
    probe = cfg['laugh_probe']
    assert probe['enabled'] is True and probe['floor'] == 0.6
    assert probe['onnx_model_path'] == "", "no model file is assumed"
    assert "_comment" in probe


def test_the_bot_attaches_the_probe_to_voice_recognition():
    src = Path("src/bot/bot.py").read_text(encoding="utf-8")
    assert "self.voice_recognition.laugh_probe = _read_laugh_probe(" in src


# --------------------------------------------------------------- the audit

def test_the_probe_needle_is_what_the_source_logs():
    """The needle is produced in src/features/laugh_probe.py, which
    test_audit_stream_log.py does not scan; pin it here."""
    assert "[LAUGH-PROBE] fired" in PROBE_SRC
    assert any(k == "laugh_probe" for k, _, _ in GUARDS)


def test_the_once_only_lines_are_not_guards():
    """"No model" and "too slow" are session facts, not suppressions: a
    guard row counting them once would say nothing about the stream."""
    needles = [n for _, n, _ in GUARDS]
    assert "laugh probe: no model at" not in needles
    assert "laugh probe: onnx too slow" not in needles


def test_the_audit_counts_and_names_the_fires():
    from test_audit_stream_log import START, _log, line
    text = _log(
        START,
        line(4, "[LAUGH-PROBE] fired score=0.83 detector=heuristic dur=5.1s"),
        line(6, "[LAUGH-PROBE] fired score=0.71 detector=heuristic dur=4.8s"))
    session = parse_sessions(text)[0]
    assert session.counts["laugh_probe"] == 2
    out = render(session)
    assert "Laugh probe fired on mic audio: 2" in out
    assert "score=0.83" in out, "examples make the log readable at a glance"

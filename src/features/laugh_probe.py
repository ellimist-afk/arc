"""Listen for Alan's laugh on the mic audio the bot already captures.

PROBE ONLY. Nothing here credits the laugh ledger, queues a reply, or holds
one back: it scores each audio piece and logs the score. The point is to
answer one question off the log after a stream -- does a laugh detector fire
on his real laughs, and does it also fire on the game audio that shares the
mic bus through VoiceMeeter? -- without changing what the co-host does while
the question is open.

Whisper runs with vad_filter=True and drops laughter as non-speech, so the
pieces that produced NO text are exactly the candidates. Pieces with words
are skipped: Whisper already said that was speech.
"""
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Heuristic constants (see the design doc, section 6). These are for reading
# the log, not for shipping a decision.
SILENCE_RMS = 0.004        # mean RMS below this is a dead piece
FRAME_MS = 40.0            # analysis window
HOP_MS = 20.0              # -> 50 frames per second of envelope
PITCH_LO_HZ = 80.0         # autocorrelation search range for voicing
PITCH_HI_HZ = 400.0
VOICED_PEAK = 0.40         # normalised autocorrelation peak => frame is voiced
SYLLABLE_LO_HZ = 3.0       # laughter bursts at 4-6 Hz; speech is 2-4 Hz
SYLLABLE_HI_HZ = 7.0
ENVELOPE_LO_HZ = 0.5       # denominator band for mod_ratio
ENVELOPE_HI_HZ = 15.0
MOD_FLOOR, MOD_SPAN = 0.20, 0.45   # mod_ratio -> 0..1
TILT_FLOOR, TILT_SPAN = 0.02, 0.30  # tilt -> 0..1
W_MOD, W_VOICED, W_TILT = 0.5, 0.3, 0.2
ONNX_BUDGET_MS = 200.0     # per piece; over this the model is dropped
COST_LOG_INTERVAL_S = 60.0
# AudioSet class index for "Laughter" in a YAMNet-shaped model. Alan places
# the file; nothing here downloads one.
LAUGHTER_CLASS = 16


@dataclass
class ProbeResult:
    score: float
    detector: str
    features: Dict[str, float] = field(default_factory=dict)


def _frames(samples: np.ndarray, rate: int):
    win = max(1, int(rate * FRAME_MS / 1000.0))
    hop = max(1, int(rate * HOP_MS / 1000.0))
    if samples.size < win:
        return np.empty((0, win), dtype=np.float32), hop
    count = 1 + (samples.size - win) // hop
    strides = (samples.strides[0] * hop, samples.strides[0])
    view = np.lib.stride_tricks.as_strided(
        samples, shape=(count, win), strides=strides)
    return np.ascontiguousarray(view), hop


class HeuristicLaughDetector:
    """numpy/scipy only: voicing + syllable-rate modulation + spectral tilt.

    Laughter is a run of voiced bursts at 4-6 Hz, breathier and brighter
    than speech, which sits nearer 2-4 Hz and is less regular.
    """

    name = "heuristic"

    def score(self, samples: np.ndarray, rate: int) -> ProbeResult:
        samples = np.asarray(samples, dtype=np.float32).ravel()
        frames, hop = _frames(samples, rate)
        if frames.shape[0] < 8:
            return ProbeResult(0.0, self.name, {'reason': 0.0})
        rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
        if float(np.mean(rms)) < SILENCE_RMS:
            return ProbeResult(0.0, self.name, {'rms': float(np.mean(rms))})

        voiced_mask = self._voicing(frames, rate)
        voiced = float(np.mean(voiced_mask))
        mod_ratio = self._mod_ratio(rms, rate / hop)
        tilt = self._tilt(frames, rate, voiced_mask)

        mod_n = _norm(mod_ratio, MOD_FLOOR, MOD_SPAN)
        tilt_n = _norm(tilt, TILT_FLOOR, TILT_SPAN)
        # Correction to the design's step 5 (recorded in section 0): the
        # plain weighted sum hands any steadily-voiced piece 0.3 before a
        # single burst of laughter evidence, so ordinary speech cannot fall
        # under the 0.3 the doc asks for. Voicing and brightness only
        # SUPPORT a syllable-rate case; they never make one on their own.
        score = float(np.clip(
            mod_n * (W_MOD + W_VOICED * voiced + W_TILT * tilt_n), 0.0, 1.0))
        return ProbeResult(score, self.name, {
            'mod_ratio': round(mod_ratio, 3), 'voiced': round(voiced, 3),
            'tilt': round(tilt, 3), 'rms': round(float(np.mean(rms)), 4),
        })

    def _voicing(self, frames: np.ndarray, rate: int) -> np.ndarray:
        """Fraction of frames with a strong autocorrelation peak in 80-400 Hz."""
        lo = max(1, int(rate / PITCH_HI_HZ))
        hi = min(frames.shape[1] - 1, int(rate / PITCH_LO_HZ))
        if hi <= lo:
            return np.zeros(frames.shape[0], dtype=bool)
        centred = frames - frames.mean(axis=1, keepdims=True)
        n = int(2 ** np.ceil(np.log2(2 * frames.shape[1])))
        spec = np.fft.rfft(centred, n=n, axis=1)
        acf = np.fft.irfft(spec * np.conj(spec), n=n, axis=1)
        zero = acf[:, 0:1]
        peak = acf[:, lo:hi + 1].max(axis=1) / np.maximum(zero[:, 0], 1e-12)
        return peak >= VOICED_PEAK

    def _mod_ratio(self, rms: np.ndarray, env_rate: float) -> float:
        """Share of envelope energy in the 3-7 Hz syllable band."""
        env = rms - rms.mean()
        if not np.any(env):
            return 0.0
        win = np.hanning(env.size)
        spec = np.abs(np.fft.rfft(env * win)) ** 2
        freqs = np.fft.rfftfreq(env.size, d=1.0 / env_rate)
        total = spec[(freqs >= ENVELOPE_LO_HZ) & (freqs <= ENVELOPE_HI_HZ)].sum()
        band = spec[(freqs >= SYLLABLE_LO_HZ) & (freqs <= SYLLABLE_HI_HZ)].sum()
        return float(band / total) if total > 0 else 0.0

    def _tilt(self, frames: np.ndarray, rate: int,
              voiced_mask: np.ndarray) -> float:
        """Mean 1-4 kHz / 0-1 kHz energy over the voiced frames."""
        use = frames[voiced_mask] if voiced_mask.any() else frames
        spec = np.abs(np.fft.rfft(use * np.hanning(use.shape[1]), axis=1)) ** 2
        freqs = np.fft.rfftfreq(use.shape[1], d=1.0 / rate)
        low = spec[:, (freqs > 0) & (freqs <= 1000.0)].sum(axis=1)
        high = spec[:, (freqs > 1000.0) & (freqs <= 4000.0)].sum(axis=1)
        return float(np.mean(high / np.maximum(low, 1e-12)))


def _norm(value: float, floor: float, span: float) -> float:
    return float(np.clip((value - floor) / span, 0.0, 1.0))


class OnnxLaughDetector:
    """An audio-event ONNX model, IF Alan has placed one at the configured
    path. Never downloads. Absent file -> unavailable, one log line, and the
    heuristic carries the session on its own.
    """

    name = "onnx"

    def __init__(self, model_path: str, log=logger):
        self.model_path = model_path or ""
        self._log = log
        self._session = None
        self._input = None
        self.available = bool(
            self.model_path and os.path.exists(self.model_path))
        if not self.available:
            self._log.info(
                "laugh probe: no model at %r; heuristic only", self.model_path)

    def score(self, samples: np.ndarray, rate: int) -> ProbeResult:
        if not self.available:
            return ProbeResult(0.0, self.name, {})
        started = time.perf_counter()
        try:
            if self._session is None:
                import onnxruntime as ort  # lazy: only when a file exists
                self._session = ort.InferenceSession(
                    self.model_path, providers=['CPUExecutionProvider'])
                self._input = self._session.get_inputs()[0].name
            batch = np.asarray(samples, dtype=np.float32).reshape(1, -1)
            out = self._session.run(None, {self._input: batch})[0]
            flat = np.asarray(out, dtype=np.float32).ravel()
            idx = LAUGHTER_CLASS if flat.size > LAUGHTER_CLASS else 0
            value = float(np.clip(flat[idx], 0.0, 1.0)) if flat.size else 0.0
        except Exception as exc:  # noqa: BLE001 - a probe never breaks the mic
            self.available = False
            self._log.warning("laugh probe: onnx unusable (%s)", exc)
            return ProbeResult(0.0, self.name, {})
        cost_ms = (time.perf_counter() - started) * 1000.0
        if cost_ms > ONNX_BUDGET_MS:
            self.available = False
            self._log.warning(
                "laugh probe: onnx too slow (%.0f ms per piece, budget %.0f); "
                "disabled for this session", cost_ms, ONNX_BUDGET_MS)
        return ProbeResult(value, self.name, {'onnx_ms': round(cost_ms, 1)})


class LaughProbe:
    """One line per piece that clears the floor. That is the whole feature."""

    def __init__(self, floor: float = 0.6, detectors: Optional[List] = None,
                 log=logger):
        self.floor = float(floor)
        self.detectors = list(detectors or [HeuristicLaughDetector()])
        self._log = log
        self.pieces = 0
        self.fired = 0
        self.max_score = 0.0
        self._cost_ms = 0.0
        self._cost_n = 0
        self._last_cost_log = 0.0

    def observe(self, samples, rate: int,
                had_words: bool) -> Optional[ProbeResult]:
        self.pieces += 1
        if had_words:
            return None
        arr = np.asarray(samples, dtype=np.float32).ravel()
        if arr.size == 0:
            return None
        started = time.perf_counter()
        best: Optional[ProbeResult] = None
        for detector in self.detectors:
            if getattr(detector, 'available', True) is False:
                continue
            result = detector.score(arr, rate)
            if best is None or result.score > best.score:
                best = result
        cost_ms = (time.perf_counter() - started) * 1000.0
        self._note_cost(cost_ms)
        if best is None:
            return None
        self.max_score = max(self.max_score, best.score)
        duration = arr.size / float(rate or 1)
        if best.score < self.floor:
            self._log.debug(
                "[LAUGH-PROBE] quiet score=%.2f detector=%s %s",
                best.score, best.detector, best.features)
            return None
        self.fired += 1
        self._log.info(
            "[LAUGH-PROBE] fired score=%.2f detector=%s dur=%.1fs",
            best.score, best.detector, duration)
        self._log.debug("[LAUGH-PROBE] features %s", best.features)
        return best

    def _note_cost(self, cost_ms: float) -> None:
        self._cost_ms += cost_ms
        self._cost_n += 1
        now = time.monotonic()
        if self._last_cost_log == 0.0:
            self._last_cost_log = now
            return
        if now - self._last_cost_log >= COST_LOG_INTERVAL_S:
            self._last_cost_log = now
            self._log.debug(
                "[LAUGH-PROBE] cost %.1f ms/piece over %d pieces",
                self._cost_ms / max(1, self._cost_n), self._cost_n)

    def stats(self) -> Dict[str, float]:
        return {
            'pieces': self.pieces,
            'fired': self.fired,
            'max_score': round(self.max_score, 3),
            'ms_per_piece': round(self._cost_ms / max(1, self._cost_n), 2),
        }

"""Shared helpers for the Phase 1 Realtime spike.

Spike-only code: nothing here is imported by src/. See spike/README.md.
"""
import json
import math
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

# Audio constants used across the spike (24 kHz PCM16 mono, per Realtime API)
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2  # bytes, paInt16
CHANNELS = 1
CHUNK_MS = 20  # playback/capture chunk size; small => fast hard-stop
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000
CHUNK_BYTES = CHUNK_FRAMES * SAMPLE_WIDTH

# Prices per 1M tokens, retrieved 2026-08-18 from
# https://developers.openai.com/api/docs/pricing  (re-verify before relying on
# absolute dollar figures; ratios change rarely, absolute prices sometimes).
PRICES = {
    "gpt-realtime-2.1": {
        "audio_in": 32.00, "audio_in_cached": 0.40, "audio_out": 64.00,
        "text_in": 4.00, "text_in_cached": 0.40, "text_out": 24.00,
    },
    "gpt-realtime-2.1-mini": {
        "audio_in": 10.00, "audio_in_cached": 0.30, "audio_out": 20.00,
        "text_in": 0.60, "text_in_cached": 0.06, "text_out": 2.40,
    },
}


def ms_of_bytes(n_bytes: int) -> float:
    """Milliseconds of audio represented by n_bytes of 24 kHz PCM16 mono."""
    return n_bytes * 1000.0 / (SAMPLE_RATE * SAMPLE_WIDTH)


def bytes_of_ms(ms: float) -> int:
    """Whole bytes (frame-aligned) for ms of 24 kHz PCM16 mono."""
    frames = int(SAMPLE_RATE * ms / 1000.0)
    return frames * SAMPLE_WIDTH


class JsonlLogger:
    """Append-only JSONL event log. Thread-safe; one line per event.

    Every record carries wall-clock UTC and a monotonic timestamp so
    latencies can be computed offline regardless of clock adjustments.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")
        self._lock = threading.Lock()
        self.t0 = time.monotonic()

    def log(self, event: str, **fields):
        rec = {
            "t_wall": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "t_mono_ms": round((time.monotonic() - self.t0) * 1000.0, 3),
            "event": event,
        }
        rec.update(fields)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
        return rec

    def close(self):
        with self._lock:
            self._fh.close()


def cost_of_usage(usage: dict, model: str) -> dict:
    """Compute estimated $ cost from a response.done usage block.

    Defensive against shape drift: any missing field counts as 0, and the raw
    usage dict is always logged alongside so nothing is lost if the API's
    accounting shape changes.
    """
    p = PRICES.get(model)
    if not p:
        return {"estimated": False, "reason": f"no local prices for {model}"}
    itd = usage.get("input_token_details", {}) or {}
    otd = usage.get("output_token_details", {}) or {}
    cached = itd.get("cached_tokens_details", {}) or {}
    audio_in = itd.get("audio_tokens", 0) or 0
    text_in = itd.get("text_tokens", 0) or 0
    cached_audio = cached.get("audio_tokens", 0) or 0
    cached_text = cached.get("text_tokens", 0) or 0
    # cached tokens are a subset of input tokens; bill the uncached remainder
    audio_in_fresh = max(0, audio_in - cached_audio)
    text_in_fresh = max(0, text_in - cached_text)
    audio_out = otd.get("audio_tokens", 0) or 0
    text_out = otd.get("text_tokens", 0) or 0
    usd = (
        audio_in_fresh * p["audio_in"] + cached_audio * p["audio_in_cached"]
        + text_in_fresh * p["text_in"] + cached_text * p["text_in_cached"]
        + audio_out * p["audio_out"] + text_out * p["text_out"]
    ) / 1_000_000.0
    return {
        "estimated": True, "usd": round(usd, 6),
        "audio_in_fresh": audio_in_fresh, "audio_in_cached": cached_audio,
        "text_in_fresh": text_in_fresh, "text_in_cached": cached_text,
        "audio_out": audio_out, "text_out": text_out,
    }


def percentile(values, pct: float):
    """Nearest-rank percentile; None on empty input."""
    if not values:
        return None
    vals = sorted(values)
    k = max(0, min(len(vals) - 1, math.ceil(pct / 100.0 * len(vals)) - 1))
    return vals[k]


def rms(pcm: bytes) -> float:
    """RMS level of PCM16 bytes, 0..1."""
    if len(pcm) < 2:
        return 0.0
    import array
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not a:
        return 0.0
    return math.sqrt(sum(x * x for x in a) / len(a)) / 32768.0


def make_chirp(duration_ms=800, f0=400.0, f1=1800.0, amp=0.4) -> bytes:
    """Linear chirp as PCM16 bytes — distinctive, easy to spot on a meter."""
    import array
    n = int(SAMPLE_RATE * duration_ms / 1000.0)
    out = array.array("h")
    for i in range(n):
        t = i / SAMPLE_RATE
        f = f0 + (f1 - f0) * (i / n)
        out.append(int(amp * 32767.0 * math.sin(2 * math.pi * f * t)))
    return out.tobytes()


# ---------------------------------------------------------------------------
# PyAudio device handling (import kept lazy so --no-audio works without it)
# ---------------------------------------------------------------------------

def list_devices(pa):
    """Return device dicts with a stable-ish fingerprint.

    Bare indexes shift when Windows re-enumerates; the fingerprint
    (name|hostapi|in|out) is what a real AudioRouter would persist.
    """
    devices = []
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        host = pa.get_host_api_info_by_index(d["hostApi"])["name"]
        devices.append({
            "index": i,
            "name": d["name"],
            "host_api": host,
            "max_input_channels": d["maxInputChannels"],
            "max_output_channels": d["maxOutputChannels"],
            "default_sample_rate": d["defaultSampleRate"],
            "fingerprint": f"{d['name']}|{host}|in{d['maxInputChannels']}|out{d['maxOutputChannels']}",
        })
    return devices


def resolve_device(pa, spec: str, want_input: bool):
    """Resolve an explicit --input/--output spec to a device.

    spec forms:
      'idx:7'        exact index (fastest, but indexes can shift)
      'substring'    case-insensitive name substring; must match exactly ONE
                     device with the right direction, else this raises with
                     the candidate list — no silent defaults, ever.
    """
    devices = list_devices(pa)
    direction = "input" if want_input else "output"
    usable = [d for d in devices if (d["max_input_channels"] if want_input
                                     else d["max_output_channels"]) > 0]
    if spec.startswith("idx:"):
        idx = int(spec[4:])
        for d in usable:
            if d["index"] == idx:
                return d
        raise SystemExit(f"No {direction} device at index {idx}. "
                         f"Run audio_probe.py --list")
    matches = [d for d in usable if spec.lower() in d["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    lines = "\n".join(f"  idx:{d['index']}  {d['fingerprint']}" for d in
                      (matches or usable))
    kind = "Ambiguous" if matches else "No"
    raise SystemExit(
        f"{kind} {direction} match for '{spec}'. Candidates:\n{lines}\n"
        f"Pick a longer substring or use idx:N."
    )

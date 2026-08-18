"""Spike A — device enumeration, explicit routing, playback bookkeeping.

Proves the AudioRouter mechanics on the real rig BEFORE any OpenAI traffic:
  1. list      — enumerate devices (indexes + fingerprints), grouped by host API
  2. capture   — record N seconds from the chosen input, live RMS meter, save WAV
  3. playback  — play a chirp through the chosen output in 20 ms chunks,
                 report played-ms accounting accuracy
  4. hardstop  — start a 5 s tone, request stop at 2 s, measure request→silence
  5. echotest  — play a chirp on the output while metering the input;
                 if the chirp shows up in the mic path, Arc can hear itself
                 and the Voicemeeter routing MUST be fixed before realtime_probe
  6. failure   — long playback; disable the output device mid-clip (Windows
                 sound settings or Voicemeeter) and observe the error handling

Usage (Windows PowerShell, from repo root):
  python spike/audio_probe.py --list
  python spike/audio_probe.py --input "voicemeeter out b1" --output "voicemeeter aux input" --all
  python spike/audio_probe.py --input idx:2 --output idx:9 --test echotest

Every result is appended to spike/runs/audio_probe_<date>.jsonl.
"""
import argparse
import sys
import time
import threading
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (SAMPLE_RATE, CHANNELS, CHUNK_FRAMES, CHUNK_BYTES,
                    JsonlLogger, list_devices, resolve_device, ms_of_bytes,
                    rms, make_chirp)

try:
    import pyaudio
except ImportError:
    sys.exit("pyaudio is required for audio_probe. pip install -r spike/requirements.txt")


class ChunkedPlayer:
    """Playback in 20 ms chunks on a worker thread with played-ms accounting
    and a hard stop — the exact mechanics §9/§13 of the redesign rely on."""

    def __init__(self, pa, device_index, log):
        self.pa = pa
        self.device_index = device_index
        self.log = log
        self._stop = threading.Event()
        self.played_bytes = 0
        self.stop_requested_at = None
        self.stopped_at = None
        self.error = None

    def play(self, pcm: bytes):
        self._stop.clear()
        self.played_bytes = 0
        self.stop_requested_at = None
        self.stopped_at = None
        self.error = None
        t = threading.Thread(target=self._run, args=(pcm,), daemon=True)
        t.start()
        return t

    def request_stop(self):
        self.stop_requested_at = time.monotonic()
        self._stop.set()

    def _run(self, pcm: bytes):
        stream = None
        try:
            stream = self.pa.open(format=pyaudio.paInt16, channels=CHANNELS,
                                  rate=SAMPLE_RATE, output=True,
                                  output_device_index=self.device_index,
                                  frames_per_buffer=CHUNK_FRAMES)
            for off in range(0, len(pcm), CHUNK_BYTES):
                if self._stop.is_set():
                    break
                chunk = pcm[off:off + CHUNK_BYTES]
                stream.write(chunk)
                self.played_bytes += len(chunk)
        except OSError as e:
            self.error = repr(e)
            self.log.log("playback_error", error=self.error,
                         played_ms=round(ms_of_bytes(self.played_bytes), 1))
        finally:
            self.stopped_at = time.monotonic()
            if stream is not None:
                try:
                    stream.stop_stream(); stream.close()
                except OSError:
                    pass


def cmd_list(pa, log):
    devices = list_devices(pa)
    by_host = {}
    for d in devices:
        by_host.setdefault(d["host_api"], []).append(d)
    for host, devs in by_host.items():
        print(f"\n== {host} ==")
        for d in devs:
            io = []
            if d["max_input_channels"]:
                io.append(f"in:{d['max_input_channels']}")
            if d["max_output_channels"]:
                io.append(f"out:{d['max_output_channels']}")
            print(f"  idx:{d['index']:<3} {' '.join(io):<12} {d['name']}")
    log.log("device_list", count=len(devices), devices=devices)
    print("\nPick devices with --input/--output using a unique name substring "
          "or idx:N.\nTip: on Windows prefer the WASAPI entries over MME "
          "duplicates when both exist.")


def cmd_capture(pa, dev, seconds, log):
    print(f"Recording {seconds}s from: {dev['fingerprint']}")
    stream = pa.open(format=pyaudio.paInt16, channels=CHANNELS,
                     rate=SAMPLE_RATE, input=True,
                     input_device_index=dev["index"],
                     frames_per_buffer=CHUNK_FRAMES)
    frames, peak = [], 0.0
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        chunk = stream.read(CHUNK_FRAMES, exception_on_overflow=False)
        frames.append(chunk)
        level = rms(chunk)
        peak = max(peak, level)
        bar = "#" * int(level * 60)
        print(f"\r  RMS {level:0.3f} |{bar:<60}|", end="", flush=True)
    print()
    stream.stop_stream(); stream.close()
    out = Path(__file__).parent / "runs" / "capture_check.wav"
    out.parent.mkdir(exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(CHANNELS); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(frames))
    verdict = "OK" if peak > 0.02 else "SILENT — wrong device or muted strip?"
    print(f"  peak RMS {peak:0.3f} → {verdict}\n  saved {out} — play it back "
          f"and confirm it is YOUR mic (not desktop audio).")
    log.log("capture_test", device=dev["fingerprint"], seconds=seconds,
            peak_rms=round(peak, 4), verdict=verdict, wav=str(out))


def cmd_playback(pa, dev, log):
    pcm = make_chirp(1500)
    expect_ms = ms_of_bytes(len(pcm))
    print(f"Playing 1.5s chirp on: {dev['fingerprint']}")
    player = ChunkedPlayer(pa, dev["index"], log)
    t0 = time.monotonic()
    th = player.play(pcm)
    th.join(timeout=10)
    wall_ms = (time.monotonic() - t0) * 1000
    played_ms = ms_of_bytes(player.played_bytes)
    drift = wall_ms - played_ms
    print(f"  accounted played_ms={played_ms:0.1f}  wall={wall_ms:0.1f}  "
          f"drift={drift:+0.1f} ms (buffer depth; expect < ~120 ms)")
    log.log("playback_test", device=dev["fingerprint"],
            expected_ms=round(expect_ms, 1), played_ms=round(played_ms, 1),
            wall_ms=round(wall_ms, 1), drift_ms=round(drift, 1),
            error=player.error)
    if player.error:
        print(f"  ERROR: {player.error}")


def cmd_hardstop(pa, dev, log):
    pcm = make_chirp(5000, f0=300, f1=600)
    print("Playing 5s tone; stop will be requested at 2.0s…")
    player = ChunkedPlayer(pa, dev["index"], log)
    th = player.play(pcm)
    time.sleep(2.0)
    player.request_stop()
    th.join(timeout=5)
    latency_ms = (player.stopped_at - player.stop_requested_at) * 1000
    played_ms = ms_of_bytes(player.played_bytes)
    print(f"  stop request → writer exit: {latency_ms:0.1f} ms "
          f"(target ≤ 40 ms + device buffer)\n  played_ms at stop: {played_ms:0.1f}")
    log.log("hardstop_test", device=dev["fingerprint"],
            stop_latency_ms=round(latency_ms, 2), played_ms=round(played_ms, 1))


def cmd_echotest(pa, in_dev, out_dev, log):
    print(f"Echo self-test:\n  out: {out_dev['fingerprint']}\n  in:  {in_dev['fingerprint']}")
    print("  Stay silent for ~4 seconds…")
    stream = pa.open(format=pyaudio.paInt16, channels=CHANNELS,
                     rate=SAMPLE_RATE, input=True,
                     input_device_index=in_dev["index"],
                     frames_per_buffer=CHUNK_FRAMES)

    def meter(seconds):
        vals = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            vals.append(rms(stream.read(CHUNK_FRAMES, exception_on_overflow=False)))
        return sorted(vals)[len(vals) // 2], max(vals)  # median, peak

    base_med, base_peak = meter(2.0)
    player = ChunkedPlayer(pa, out_dev["index"], log)
    th = player.play(make_chirp(2500, amp=0.8))
    during_med, during_peak = meter(2.5)
    th.join(timeout=5)
    stream.stop_stream(); stream.close()
    # Verdict: chirp leaking into the mic path raises level well above baseline
    leaked = during_peak > max(0.03, base_peak * 3 + 0.01)
    verdict = ("FAIL — Arc can hear itself. Fix Voicemeeter routing: the bus "
               "feeding this input must exclude Arc's output strip."
               if leaked else "PASS — output not audible on input path")
    print(f"  baseline med/peak {base_med:0.3f}/{base_peak:0.3f}  "
          f"during med/peak {during_med:0.3f}/{during_peak:0.3f}\n  {verdict}")
    log.log("echo_selftest", input=in_dev["fingerprint"],
            output=out_dev["fingerprint"], base_peak=round(base_peak, 4),
            during_peak=round(during_peak, 4), leaked=leaked, verdict=verdict)


def cmd_failure(pa, dev, log):
    print("Playing 20s tone. While it plays, disable the output device\n"
          "(Windows Sound settings, or mute/kill the Voicemeeter strip).\n"
          "Watching for the failure mode…")
    player = ChunkedPlayer(pa, dev["index"], log)
    th = player.play(make_chirp(20000, f0=350, f1=350, amp=0.3))
    th.join(timeout=25)
    if player.error:
        print(f"  Device failure surfaced as: {player.error}\n"
              f"  played_ms before failure: {ms_of_bytes(player.played_bytes):0.1f}\n"
              f"  (Good: an explicit error we can catch → AudioRouter can "
              f"report 'device lost' instead of hanging.)")
    else:
        print("  Playback completed without error — either the device wasn't "
              "disabled, or Windows rerouted silently (bad: note which).")
    log.log("failure_test", device=dev["fingerprint"], error=player.error,
            played_ms=round(ms_of_bytes(player.played_bytes), 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="enumerate devices and exit")
    ap.add_argument("--input", help="input device: name substring or idx:N")
    ap.add_argument("--output", help="output device: name substring or idx:N")
    ap.add_argument("--test", choices=["capture", "playback", "hardstop",
                                       "echotest", "failure"],
                    help="run one test")
    ap.add_argument("--all", action="store_true",
                    help="run capture, playback, hardstop, echotest in order")
    ap.add_argument("--seconds", type=float, default=5.0, help="capture length")
    args = ap.parse_args()

    log = JsonlLogger(Path(__file__).parent / "runs" /
                      f"audio_probe_{time.strftime('%Y%m%d')}.jsonl")
    pa = pyaudio.PyAudio()
    try:
        if args.list or not (args.test or args.all):
            cmd_list(pa, log)
            return
        in_dev = resolve_device(pa, args.input, True) if args.input else None
        out_dev = resolve_device(pa, args.output, False) if args.output else None
        log.log("devices_selected",
                input=in_dev["fingerprint"] if in_dev else None,
                output=out_dev["fingerprint"] if out_dev else None)

        def need(dev, flag):
            if dev is None:
                raise SystemExit(f"--{flag} is required for this test "
                                 f"(explicit selection only — no defaults).")
            return dev

        tests = (["capture", "playback", "hardstop", "echotest"] if args.all
                 else [args.test])
        for t in tests:
            print(f"\n--- {t} ---")
            if t == "capture":
                cmd_capture(pa, need(in_dev, "input"), args.seconds, log)
            elif t == "playback":
                cmd_playback(pa, need(out_dev, "output"), log)
            elif t == "hardstop":
                cmd_hardstop(pa, need(out_dev, "output"), log)
            elif t == "echotest":
                cmd_echotest(pa, need(in_dev, "input"), need(out_dev, "output"), log)
            elif t == "failure":
                cmd_failure(pa, need(out_dev, "output"), log)
    finally:
        pa.terminate()
        log.close()


if __name__ == "__main__":
    main()

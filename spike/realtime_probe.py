"""Spike B — OpenAI Realtime speech-to-speech probe (isolated; no bot code).

What it proves, per docs/ARC_REALTIME_REDESIGN.md §21:
  - WebSocket session to a gpt-realtime model, mic → session → pinned output
  - VAD on, automatic responses OFF: the LOCAL attention layer authorizes
    every response (keypress, or auto-on-turn-end in scripted mode)
  - PASSIVE vs ARMED states with a configurable mic pre-roll (default ~2 s)
    flushed into the session on arming — the wake-flush mechanic
  - Genuine barge-in: stop local playback, response.cancel,
    conversation.item.truncate at measured played-ms
  - False-start (cough) handling via a short cancellation grace period
  - JSONL metrics: first-audio latency, interrupt→silence latency, usage,
    estimated cost, disconnects, device errors

Run (Windows PowerShell, from repo root; OPENAI_API_KEY in env or .env):
  python spike/realtime_probe.py --input "voicemeeter out b1" --output "voicemeeter aux input"
  python spike/realtime_probe.py --input idx:2 --output idx:9 --scripted

Keys: [a]rm (flush pre-roll, start streaming)  [p]assive  [SPACE] authorize
      response  [n]ext scripted step  [q]uit

Smoke test without hardware or API key (uses spike/fake_realtime_server.py):
  python spike/fake_realtime_server.py &
  python spike/realtime_probe.py --no-audio --auto-authorize \
      --url ws://localhost:8787 --max-minutes 0.4
"""
import argparse
import asyncio
import base64
import collections
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (SAMPLE_RATE, CHANNELS, CHUNK_FRAMES, CHUNK_BYTES, CHUNK_MS,
                    JsonlLogger, resolve_device, ms_of_bytes, bytes_of_ms,
                    cost_of_usage, make_chirp, percentile)

# Windows consoles default to cp1252; the probes print arrows/bullets.
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    import websockets
except ImportError:
    sys.exit("pip install -r spike/requirements.txt (needs websockets)")

DEFAULT_INSTRUCTIONS = (
    "You are a witty, concise AI co-host on a Twitch stream. The person "
    "speaking to you is the streamer. Keep replies to one or two short "
    "sentences; be dry and playful, never rambling. This is a technical "
    "test session; if asked to count or read numbers, comply exactly."
)

SCRIPT = [
    ("normal turn",      "Say: 'Hey, can you hear me? Introduce yourself in one sentence.'"),
    ("normal turn",      "Ask any casual question. Let the answer finish."),
    ("fast follow-up",   "The INSTANT the answer ends, ask a follow-up. No pause."),
    ("fast follow-up",   "Again — immediate follow-up as it finishes."),
    ("interruption",     "Ask it to count slowly from 1 to 20. INTERRUPT around 5 by talking over it."),
    ("interruption",     "Ask for a long story. Interrupt mid-sentence with a new question."),
    ("cough test",       "Ask it to count to 20 again. While it speaks, make ONE short cough/noise (<0.3s) and stay silent — playback should continue."),
    ("normal turn",      "Ask: 'What number did you get to before I interrupted you earlier?' (truncation check: it should not claim numbers you never heard)"),
    ("device failure",   "Ask it to count to 30. While it speaks, disable the output device in Windows/Voicemeeter. Re-enable after."),
    ("normal turn",      "Say goodbye. Let it answer fully."),
]


# ---------------------------------------------------------------------------
# Audio backends: real PyAudio, or a paced null backend for --no-audio runs
# ---------------------------------------------------------------------------

class MicCapture:
    """Capture thread → pre-roll ring buffer always; live queue when armed."""

    def __init__(self, pa, device_index, preroll_ms, loop, log, fake=False):
        self.pa, self.device_index, self.loop, self.log = pa, device_index, loop, log
        self.fake = fake
        maxlen = max(1, bytes_of_ms(preroll_ms) // CHUNK_BYTES)
        self.ring = collections.deque(maxlen=maxlen)
        self.live = asyncio.Queue()
        self.armed = False
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()

    def arm(self):
        """Flush pre-roll into the live queue, then stream continuously."""
        flushed = b"".join(self.ring)
        self.ring.clear()
        self.armed = True
        if flushed:
            self.loop.call_soon_threadsafe(self.live.put_nowait, flushed)
        self.log.log("mic_armed", preroll_flushed_ms=round(ms_of_bytes(len(flushed)), 1))

    def disarm(self):
        self.armed = False
        self.log.log("mic_passive")

    def _run(self):
        stream = None
        try:
            if not self.fake:
                import pyaudio
                stream = self.pa.open(format=pyaudio.paInt16, channels=CHANNELS,
                                      rate=SAMPLE_RATE, input=True,
                                      input_device_index=self.device_index,
                                      frames_per_buffer=CHUNK_FRAMES)
            while not self._stop.is_set():
                if self.fake:
                    time.sleep(CHUNK_MS / 1000.0)
                    chunk = b"\x00" * CHUNK_BYTES
                else:
                    chunk = stream.read(CHUNK_FRAMES, exception_on_overflow=False)
                if self.armed:
                    self.loop.call_soon_threadsafe(self.live.put_nowait, chunk)
                else:
                    self.ring.append(chunk)
        except OSError as e:
            self.log.log("mic_error", error=repr(e))
        finally:
            if stream is not None:
                try:
                    stream.stop_stream(); stream.close()
                except OSError:
                    pass


class Player:
    """Chunked playback thread with per-item played-ms accounting, hard stop,
    and device-failure capture. Null-device mode paces writes with sleep so
    played-ms stays meaningful in --no-audio smoke runs."""

    def __init__(self, pa, device_index, log, fake=False):
        self.pa, self.device_index, self.log, self.fake = pa, device_index, log, fake
        self.q = collections.deque()          # (item_id, bytes)
        self.cv = threading.Condition()
        self.played_ms = collections.defaultdict(float)
        self.first_play_mono = {}             # item_id -> monotonic first-write
        self.current_item = None
        self.device_error = None
        self._stop_item = None
        self._shutdown = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def shutdown(self):
        with self.cv:
            self._shutdown = True
            self.cv.notify()

    def enqueue(self, item_id, pcm):
        with self.cv:
            self.q.append((item_id, pcm))
            self.cv.notify()

    def hard_stop(self, item_id):
        """Drop queued audio for item_id and stop mid-chunk-boundary.
        Returns monotonic timestamp when the request was made."""
        with self.cv:
            self._stop_item = item_id
            self.q.clear()
            self.cv.notify()
        return time.monotonic()

    def _open(self):
        if self.fake:
            return None
        import pyaudio
        return self.pa.open(format=pyaudio.paInt16, channels=CHANNELS,
                            rate=SAMPLE_RATE, output=True,
                            output_device_index=self.device_index,
                            frames_per_buffer=CHUNK_FRAMES)

    def _run(self):
        stream = None
        try:
            stream = self._open()
            while True:
                with self.cv:
                    while not self.q and not self._shutdown:
                        self.current_item = None
                        self.cv.wait(timeout=0.25)
                    if self._shutdown:
                        return
                    item_id, pcm = self.q.popleft()
                if self._stop_item == item_id:
                    continue
                self.current_item = item_id
                for off in range(0, len(pcm), CHUNK_BYTES):
                    if self._stop_item == item_id or self._shutdown:
                        break
                    chunk = pcm[off:off + CHUNK_BYTES]
                    try:
                        if self.fake:
                            time.sleep(len(chunk) / (SAMPLE_RATE * 2))
                        else:
                            stream.write(chunk)
                    except OSError as e:
                        self.device_error = repr(e)
                        self.log.log("playback_device_error", error=self.device_error,
                                     item_id=item_id,
                                     played_ms=round(self.played_ms[item_id], 1))
                        # try one reopen; if it fails, drop remaining audio
                        try:
                            if stream is not None:
                                stream.close()
                            stream = self._open()
                            self.log.log("playback_device_reopened")
                        except OSError as e2:
                            self.log.log("playback_reopen_failed", error=repr(e2))
                            with self.cv:
                                self.q.clear()
                            break
                        continue
                    if item_id not in self.first_play_mono:
                        self.first_play_mono[item_id] = time.monotonic()
                    self.played_ms[item_id] += ms_of_bytes(len(chunk))
        finally:
            if stream is not None:
                try:
                    stream.stop_stream(); stream.close()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------

class Probe:
    def __init__(self, args, log):
        self.args, self.log = args, log
        self.loop = None
        self.ws = None
        self.mic = None
        self.player = None
        self.pa = None
        # response bookkeeping
        self.active_response = None    # response_id while in progress
        self.item_of_response = {}     # response_id -> audio item_id
        self.t_speech_stopped = None
        self.t_authorized = None
        self.pending = {}              # response_id -> latency fields
        self.awaiting_authorization = False
        # interruption bookkeeping
        self.interrupt_pending_since = None
        self.speech_stopped_during_grace = False
        # stats
        self.first_audio_ms = []
        self.interrupt_silence_ms = []
        self.total_usd = 0.0
        self.disconnects = 0
        self.unknown_events = collections.Counter()
        self.script_idx = 0
        self.quit = False

    # ---- lifecycle ----
    async def run(self):
        self.loop = asyncio.get_running_loop()
        if not self.args.no_audio:
            import pyaudio
            self.pa = pyaudio.PyAudio()
            in_dev = resolve_device(self.pa, self.args.input, True)
            out_dev = resolve_device(self.pa, self.args.output, False)
            self.log.log("devices_selected", input=in_dev["fingerprint"],
                         output=out_dev["fingerprint"])
            in_idx, out_idx = in_dev["index"], out_dev["index"]
        else:
            in_idx = out_idx = None
            self.log.log("devices_selected", input="(no-audio)", output="(no-audio)")

        self.mic = MicCapture(self.pa, in_idx, self.args.preroll_ms, self.loop,
                              self.log, fake=self.args.no_audio)
        self.player = Player(self.pa, out_idx, self.log, fake=self.args.no_audio)
        self.mic.start()
        self.player.start()
        threading.Thread(target=self._keys, daemon=True).start()
        if self.args.no_audio:
            self.mic.arm()   # headless wiring test: arm immediately

        if self.args.scripted:
            self._print_script_step()

        deadline = time.monotonic() + self.args.max_minutes * 60
        attempts = 0
        while time.monotonic() < deadline and not self.quit:
            try:
                await self._session(deadline)
                break  # clean exit
            except (websockets.ConnectionClosed, OSError) as e:
                self.disconnects += 1
                attempts += 1
                self.log.log("disconnect", error=repr(e), attempts=attempts)
                if attempts > 3:
                    print("Too many disconnects; giving up.")
                    break
                delay = min(2 ** attempts, 8)
                print(f"\n[reconnect in {delay}s…]")
                await asyncio.sleep(delay)
        self._finish()

    async def _session(self, deadline):
        url = self.args.url or f"wss://api.openai.com/v1/realtime?model={self.args.model}"
        headers = {}
        if not self.args.url:
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise SystemExit("OPENAI_API_KEY not set (env or .env).")
            headers["Authorization"] = f"Bearer {key}"
        self.log.log("connecting", url=url.split("?")[0], model=self.args.model)
        async with websockets.connect(url, additional_headers=headers,
                                      max_size=1 << 24) as ws:
            self.ws = ws
            self.log.log("connected")
            # drop any mic backlog accumulated while disconnected
            while not self.mic.live.empty():
                self.mic.live.get_nowait()
            print("[connected — press 'a' to ARM the mic when ready]")
            await self._send({
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": self.args.model,
                    "output_modalities": ["audio"],
                    "instructions": self._instructions(),
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                            "transcription": {"model": "gpt-4o-mini-transcribe"},
                            "turn_detection": {
                                "type": self.args.vad,
                                # LOCAL attention stays authoritative:
                                "create_response": False,
                                "interrupt_response": False,
                            },
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                            "voice": self.args.voice,
                        },
                    },
                },
            })
            sender = asyncio.create_task(self._pump_mic())
            try:
                while time.monotonic() < deadline and not self.quit:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    await self._handle(json.loads(raw))
            finally:
                sender.cancel()

    def _instructions(self):
        if self.args.instructions and Path(self.args.instructions).exists():
            return Path(self.args.instructions).read_text(encoding="utf-8")
        return DEFAULT_INSTRUCTIONS

    async def _send(self, obj):
        try:
            await self.ws.send(json.dumps(obj))
        except (websockets.ConnectionClosed, AttributeError) as e:
            self.log.log("send_failed", type=obj.get("type"), error=repr(e))

    async def _pump_mic(self):
        while True:
            chunk = await self.mic.live.get()
            await self._send({"type": "input_audio_buffer.append",
                              "audio": base64.b64encode(chunk).decode()})

    # ---- server events ----
    async def _handle(self, ev):
        t = ev.get("type", "?")
        now = time.monotonic()
        if t == "input_audio_buffer.speech_started":
            self.log.log("speech_started", audio_start_ms=ev.get("audio_start_ms"))
            if self.player.current_item is not None:
                await self._begin_interrupt(now)
        elif t == "input_audio_buffer.speech_stopped":
            self.t_speech_stopped = now
            self.log.log("speech_stopped", audio_end_ms=ev.get("audio_end_ms"))
            if self.interrupt_pending_since is not None:
                self.speech_stopped_during_grace = True
                # gap = VAD-reported onset→offset wall time; tunes --grace-ms
                self.log.log("grace_window_speech_gap_ms",
                             gap_ms=round((now - self.interrupt_pending_since) * 1000, 1))
            elif self.args.auto_authorize:
                await self.authorize("auto_on_turn_end")
            else:
                self.awaiting_authorization = True
                print("\n[turn detected — SPACE to authorize response]")
        elif t == "response.created":
            rid = ev.get("response", {}).get("id")
            self.active_response = rid
            self.pending[rid] = {"t_created": now,
                                 "t_speech_stopped": self.t_speech_stopped,
                                 "t_authorized": self.t_authorized}
            self.log.log("response_created", response_id=rid)
        elif t in ("response.output_audio.delta", "response.audio.delta"):
            if t == "response.audio.delta":
                self.unknown_events["legacy_name:response.audio.delta"] += 1
            rid = ev.get("response_id")
            item_id = ev.get("item_id")
            if rid and rid in self.pending and "t_first_delta" not in self.pending[rid]:
                self.pending[rid]["t_first_delta"] = now
                self.item_of_response[rid] = item_id
            pcm = base64.b64decode(ev.get("delta", ""))
            if pcm:
                self.player.enqueue(item_id, pcm)
        elif t in ("response.output_audio_transcript.done",
                   "response.output_audio_transcript.delta"):
            if t.endswith(".done"):
                self.log.log("arc_said", transcript=ev.get("transcript"))
        elif t == "conversation.item.input_audio_transcription.completed":
            self.log.log("you_said", transcript=ev.get("transcript"))
            print(f"\n  you: {ev.get('transcript', '').strip()}")
        elif t == "response.done":
            await self._response_done(ev, now)
        elif t == "conversation.item.truncated":
            self.log.log("item_truncated_ack", item_id=ev.get("item_id"),
                         audio_end_ms=ev.get("audio_end_ms"))
        elif t == "rate_limits.updated":
            self.log.log("rate_limits", limits=ev.get("rate_limits"))
        elif t == "error":
            self.log.log("api_error", error=ev.get("error"))
            print(f"\n[api error] {ev.get('error')}")
        elif t in ("session.created", "session.updated"):
            self.log.log(t, session_keys=sorted((ev.get("session") or {}).keys()))
        else:
            if self.unknown_events[t] == 0:
                self.log.log("unhandled_event", type=t)
            self.unknown_events[t] += 1

    async def _response_done(self, ev, now):
        resp = ev.get("response", {}) or {}
        rid = resp.get("id")
        usage = resp.get("usage", {}) or {}
        cost = cost_of_usage(usage, self.args.model)
        if cost.get("estimated"):
            self.total_usd += cost["usd"]
        lat = self.pending.pop(rid, {})
        first_audio = None
        item_id = self.item_of_response.get(rid)
        t_played = self.player.first_play_mono.get(item_id)
        if t_played and lat.get("t_speech_stopped"):
            first_audio = (t_played - lat["t_speech_stopped"]) * 1000
            self.first_audio_ms.append(first_audio)
        self.log.log("response_done", response_id=rid, status=resp.get("status"),
                     first_audio_ms=round(first_audio, 1) if first_audio else None,
                     model_latency_ms=round((lat["t_first_delta"] - lat["t_authorized"]) * 1000, 1)
                     if lat.get("t_first_delta") and lat.get("t_authorized") else None,
                     usage=usage, cost=cost, session_total_usd=round(self.total_usd, 4))
        if first_audio:
            print(f"  [first-audio {first_audio:0.0f} ms · "
                  f"${cost.get('usd', 0):0.4f} · total ${self.total_usd:0.3f}]")
        if self.active_response == rid:
            self.active_response = None

    # ---- local attention actions ----
    async def authorize(self, reason):
        self.t_authorized = time.monotonic()
        self.awaiting_authorization = False
        self.log.log("response_authorized", reason=reason)
        await self._send({"type": "response.create"})

    async def _begin_interrupt(self, t_onset):
        """Grace-window barge-in: don't kill playback for a cough."""
        if self.interrupt_pending_since is not None:
            return
        self.interrupt_pending_since = t_onset
        self.speech_stopped_during_grace = False
        self.log.log("interrupt_pending", grace_ms=self.args.grace_ms)
        asyncio.create_task(self._grace_timer(t_onset))

    async def _grace_timer(self, t_onset):
        await asyncio.sleep(self.args.grace_ms / 1000.0)
        false_start = self.speech_stopped_during_grace
        self.interrupt_pending_since = None
        if false_start:
            self.log.log("false_start_ignored",
                         note="speech ended within grace; playback continued")
            print("\n  [cough ignored — playback continues]")
            return
        # real barge-in
        item_id = self.player.current_item
        rid = self.active_response
        t_req = self.player.hard_stop(item_id)
        # writer exits within one chunk; approximate silence at request+chunk
        silence_ms = (t_req - t_onset) * 1000 + CHUNK_MS
        self.interrupt_silence_ms.append(silence_ms)
        played = self.player.played_ms.get(item_id, 0.0)
        if rid:
            await self._send({"type": "response.cancel", "response_id": rid})
        if item_id:
            await self._send({"type": "conversation.item.truncate",
                              "item_id": item_id, "content_index": 0,
                              "audio_end_ms": int(played)})
        self.log.log("interrupted", item_id=item_id, response_id=rid,
                     onset_to_silence_ms=round(silence_ms, 1),
                     played_ms=round(played, 1),
                     note="silence_ms includes the grace window by design")
        print(f"\n  [interrupted: silent {silence_ms:0.0f} ms after onset "
              f"(incl. {self.args.grace_ms} ms grace); truncated at {played:0.0f} ms]")

    # ---- keyboard ----
    def _keys(self):
        try:
            import msvcrt
            def getch():
                return msvcrt.getwch()
        except ImportError:
            def getch():
                ch = sys.stdin.read(1)
                if not ch:          # EOF (piped/headless run): idle, don't quit
                    time.sleep(3600)
                    return ""
                return ch
        while True:
            ch = getch().lower()
            if ch == "q":
                self.quit = True
                return
            if ch == "a":
                self.mic.arm()
                print("\n[ARMED — streaming mic (pre-roll flushed)]")
            elif ch == "p":
                self.mic.disarm()
                print("\n[PASSIVE — buffering locally, nothing sent]")
            elif ch in (" ", "\r", "\n"):
                asyncio.run_coroutine_threadsafe(
                    self.authorize("keypress"), self.loop)
            elif ch == "n" and self.args.scripted:
                self.script_idx += 1
                self._print_script_step()

    def _print_script_step(self):
        if self.script_idx < len(SCRIPT):
            kind, text = SCRIPT[self.script_idx]
            print(f"\n=== Step {self.script_idx + 1}/{len(SCRIPT)} [{kind}] ===\n"
                  f"    {text}\n    (press n when done)")
            self.log.log("script_step", step=self.script_idx + 1, kind=kind)
        else:
            print("\n=== Script complete — press q to finish ===")

    # ---- summary ----
    def _finish(self):
        self.mic.stop()
        self.player.shutdown()
        if self.pa:
            self.pa.terminate()
        summary = {
            "first_audio_p50_ms": percentile(self.first_audio_ms, 50),
            "first_audio_p95_ms": percentile(self.first_audio_ms, 95),
            "first_audio_n": len(self.first_audio_ms),
            "interrupt_silence_p50_ms": percentile(self.interrupt_silence_ms, 50),
            "interrupt_silence_n": len(self.interrupt_silence_ms),
            "grace_ms": self.args.grace_ms,
            "total_usd": round(self.total_usd, 4),
            "disconnects": self.disconnects,
            "unhandled_event_types": dict(self.unknown_events),
        }
        self.log.log("session_summary", **summary)
        print("\n----- session summary -----")
        for k, v in summary.items():
            print(f"  {k}: {v}")
        print(f"  log: {self.log.path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", help="mic device: name substring or idx:N")
    ap.add_argument("--output", help="output device: name substring or idx:N")
    ap.add_argument("--model", default="gpt-realtime-2.1-mini")
    ap.add_argument("--voice", default="marin")
    ap.add_argument("--vad", default="semantic_vad",
                    choices=["semantic_vad", "server_vad"])
    ap.add_argument("--preroll-ms", type=int, default=2000,
                    help="mic pre-roll flushed on arming (default ~2s)")
    ap.add_argument("--grace-ms", type=int, default=250,
                    help="barge-in grace window; 0 = cancel immediately")
    ap.add_argument("--instructions", help="path to instructions text file")
    ap.add_argument("--scripted", action="store_true",
                    help="run the guided 10-turn scenario (implies --auto-authorize)")
    ap.add_argument("--auto-authorize", action="store_true",
                    help="authorize a response on every turn end (still a "
                         "LOCAL decision; server auto-response stays off)")
    ap.add_argument("--url", help="override endpoint (fake server smoke test)")
    ap.add_argument("--no-audio", action="store_true",
                    help="null audio backend (wiring test without devices)")
    ap.add_argument("--max-minutes", type=float, default=20.0)
    ap.add_argument("--out", help="JSONL path (default spike/runs/realtime_<ts>.jsonl)")
    args = ap.parse_args()
    if args.scripted:
        args.auto_authorize = True
    if not args.no_audio and not (args.input and args.output):
        ap.error("--input and --output are required (explicit selection only); "
                 "or use --no-audio for a wiring test")

    # .env convenience (repo root), no dependency if absent
    env_file = Path(__file__).parent.parent / ".env"
    if "OPENAI_API_KEY" not in os.environ and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1].strip()

    out = args.out or (Path(__file__).parent / "runs" /
                       f"realtime_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
    log = JsonlLogger(out)
    log.log("config", **{k: v for k, v in vars(args).items() if k != "out"})
    probe = Probe(args, log)
    try:
        asyncio.run(probe.run())
    except KeyboardInterrupt:
        probe._finish()
    finally:
        log.close()


if __name__ == "__main__":
    main()

"""AudioRouter: single owner of realtime PCM capture and playback (doc §13).

Ported from spike/realtime_probe.py (MicCapture, Player), which was validated
on the streaming rig 2026-08-22: B3 capture clean, echotest PASS, hard-stop
16 ms, barge-in onset->silence ~20 ms raw. Changes from the spike:

  - pause()/resume(): the AttentionRouter's grace window pauses playback at
    speech onset (Arc never talks over the streamer while we decide) and
    either resumes (cough) or hard-stops (real barge-in).
  - mark_done(item_id): the session signals when an item's audio is complete
    so the player can report "finished" only when it has really drained.
  - Callbacks instead of print/JSONL; errors raise AudioDeviceError with the
    candidate list instead of SystemExit.
  - Shares an injected PyAudio instance (single-instance rule, CLAUDE.md).

All blocking audio I/O stays on dedicated threads; nothing here touches the
event loop except via loop.call_soon_threadsafe.
"""
import collections
import logging
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 24 kHz PCM16 mono, per the Realtime API. Small chunks => fast hard-stop.
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1
CHUNK_MS = 20
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000
CHUNK_BYTES = CHUNK_FRAMES * SAMPLE_WIDTH


def ms_of_bytes(n_bytes: int) -> float:
    return n_bytes * 1000.0 / (SAMPLE_RATE * SAMPLE_WIDTH)


def bytes_of_ms(ms: float) -> int:
    return int(SAMPLE_RATE * ms / 1000.0) * SAMPLE_WIDTH


class AudioDeviceError(RuntimeError):
    """Device not found / ambiguous. No silent default fallback, ever."""


def list_devices(pa) -> List[dict]:
    """Devices with a stable fingerprint (name|hostapi|in|out); bare indexes
    shift when Windows re-enumerates."""
    out = []
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        host = pa.get_host_api_info_by_index(d["hostApi"])["name"]
        out.append({
            "index": i, "name": d["name"], "host_api": host,
            "max_input_channels": d["maxInputChannels"],
            "max_output_channels": d["maxOutputChannels"],
            "default_sample_rate": d["defaultSampleRate"],
            "fingerprint": (f"{d['name']}|{host}|in{d['maxInputChannels']}"
                            f"|out{d['maxOutputChannels']}"),
        })
    return out


def resolve_device(pa, spec: str, want_input: bool) -> dict:
    """'idx:N' or a case-insensitive name substring that matches exactly ONE
    device of the right direction. Anything else raises with candidates."""
    direction = "input" if want_input else "output"
    usable = [d for d in list_devices(pa)
              if (d["max_input_channels"] if want_input
                  else d["max_output_channels"]) > 0]
    if spec.startswith("idx:"):
        idx = int(spec[4:])
        for d in usable:
            if d["index"] == idx:
                return d
        raise AudioDeviceError(f"No {direction} device at index {idx}")
    matches = [d for d in usable if spec.lower() in d["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    lines = "\n".join(f"  idx:{d['index']}  {d['fingerprint']}"
                      for d in (matches or usable))
    kind = "Ambiguous" if matches else "No"
    raise AudioDeviceError(
        f"{kind} {direction} match for {spec!r}. Candidates:\n{lines}\n"
        f"Pick a longer substring or use idx:N.")


class MicCapture:
    """Capture thread. Pre-roll ring buffer always; live delivery when armed.

    on_chunk(pcm) is invoked ON THE EVENT LOOP (via call_soon_threadsafe) so
    the consumer can touch asyncio state directly. arm() flushes the pre-roll
    first, so a wake utterance arrives as part of one natural turn (doc §8).
    """

    def __init__(self, pa, device_index: Optional[int], preroll_ms: int, loop,
                 on_chunk: Callable[[bytes], None], fake: bool = False,
                 on_error: Optional[Callable[[str], None]] = None):
        self.pa, self.device_index, self.loop = pa, device_index, loop
        self.on_chunk, self.on_error, self.fake = on_chunk, on_error, fake
        self.ring = collections.deque(
            maxlen=max(1, bytes_of_ms(preroll_ms) // CHUNK_BYTES))
        # The capture thread appends to `ring` while arm() (event loop) drains
        # it. Without this lock the drain could raise "deque mutated during
        # iteration", lose chunks, or set armed=True after a chunk had already
        # been ringed -- leaving LISTENING with an effectively unarmed mic.
        self._ring_lock = threading.Lock()
        self.armed = False
        self.preroll_flushed_ms = 0.0
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="realtime-mic")

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def arm(self) -> float:
        """Flush pre-roll to the consumer, then stream live. Returns ms flushed.

        The snapshot-and-arm is atomic against the capture thread; delivery
        happens outside the lock so capture never blocks on loop/network work.
        """
        with self._ring_lock:
            flushed = b"".join(self.ring)
            self.ring.clear()
            self.armed = True
            self.preroll_flushed_ms = ms_of_bytes(len(flushed))
        if flushed:
            self._deliver(flushed)
        return self.preroll_flushed_ms

    def disarm(self) -> None:
        self.armed = False

    def _deliver(self, pcm: bytes) -> None:
        if self.loop is not None:
            if self.loop.is_closed():
                return          # shutting down; drop the chunk quietly
            try:
                self.loop.call_soon_threadsafe(self.on_chunk, pcm)
            except RuntimeError:
                pass            # loop closed between the check and the call
        else:
            self.on_chunk(pcm)

    def _run(self) -> None:
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
                # Re-check `armed` under the lock: arm() may land between the
                # read above and the append below, and a chunk ringed after
                # the flush would be stranded until the next arm().
                with self._ring_lock:
                    live = self.armed
                    if not live:
                        self.ring.append(chunk)
                if live:
                    self._deliver(chunk)
        except OSError as e:
            logger.error(f"Realtime mic capture error: {e!r}")
            if self.on_error:
                self.on_error(repr(e))
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except OSError:
                    pass


class Player:
    """Chunked playback thread with per-item played-ms accounting, pause /
    resume, hard stop, and one device reopen attempt on failure.

    played_ms[item_id] counts only chunks actually written to the device --
    the authoritative truncation value (doc §9). Callbacks run on the loop
    when one is given. Fake mode paces writes with sleep so played-ms stays
    meaningful in tests.
    """

    def __init__(self, pa, device_index: Optional[int], fake: bool = False,
                 loop=None,
                 on_item_started: Optional[Callable[[str], None]] = None,
                 on_item_finished: Optional[Callable[[str, float], None]] = None,
                 on_device_error: Optional[Callable[[str], None]] = None):
        self.pa, self.device_index, self.fake, self.loop = pa, device_index, fake, loop
        self.on_item_started = on_item_started
        self.on_item_finished = on_item_finished
        self.on_device_error = on_device_error
        self.q: collections.deque = collections.deque()      # (item_id, pcm)
        self.cv = threading.Condition()
        self.played_ms: Dict[str, float] = collections.defaultdict(float)
        self.first_play_mono: Dict[str, float] = {}
        self.current_item: Optional[str] = None
        self.paused = False
        self.device_error: Optional[str] = None
        self._done_items = set()
        self._stop_item: Optional[str] = None
        # Items stopped by hard_stop(). `_stop_item` is cleared as soon as the
        # playback loop observes it, so it cannot keep rejecting the deltas
        # still in flight from the provider; this does, until the provider
        # signals the item is terminally done (mark_done). Insertion-ordered
        # so that when the bound is hit the OLDEST cancellation is forgotten,
        # never a recent one whose deltas may still be arriving.
        self._cancelled: "collections.OrderedDict[str, None]" = collections.OrderedDict()
        self._shutdown = False
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="realtime-player")

    # Bound on remembered cancellations (see hard_stop).
    _MAX_CANCELLED = 256

    def start(self) -> None:
        self.thread.start()

    def shutdown(self) -> None:
        with self.cv:
            self._shutdown = True
            self.cv.notify()

    def enqueue(self, item_id: str, pcm: bytes) -> None:
        with self.cv:
            if item_id in self._cancelled:
                # Late delta for speech the user already interrupted: dropping
                # it is what stops a barge-in from resuming a second later.
                return
            self.q.append((item_id, pcm))
            self.cv.notify()

    def mark_done(self, item_id: str) -> None:
        """The producer has no more audio for item_id."""
        with self.cv:
            self._done_items.add(item_id)
            # Terminal for this item: no further deltas can arrive, so stop
            # tracking it (this is what keeps _cancelled from growing).
            self._cancelled.pop(item_id, None)
            self.cv.notify()

    def pause(self) -> float:
        """Stop writing at the next chunk boundary; keep queued audio.
        Returns played_ms of the current item (frozen while paused)."""
        with self.cv:
            self.paused = True
            item = self.current_item
        return self.played_ms.get(item, 0.0) if item else 0.0

    def resume(self) -> None:
        with self.cv:
            self.paused = False
            self.cv.notify()

    def hard_stop(self, item_id: Optional[str] = None) -> float:
        """Drop queued audio and stop within one chunk. Returns played_ms of
        the stopped item -- never more than was actually written."""
        with self.cv:
            item = item_id or self.current_item
            self._stop_item = item
            if item is not None:
                # Re-cancelling refreshes recency
                self._cancelled.pop(item, None)
                self._cancelled[item] = None
                while len(self._cancelled) > self._MAX_CANCELLED:
                    # Provider never sent a terminal event for the oldest id;
                    # forget it rather than grow without bound.
                    self._cancelled.popitem(last=False)
            self.paused = False
            self.q.clear()
            self.cv.notify()
        return self.played_ms.get(item, 0.0) if item else 0.0

    def _emit(self, fn, *args) -> None:
        if fn is None:
            return
        if self.loop is not None:
            if self.loop.is_closed():
                return
            try:
                self.loop.call_soon_threadsafe(fn, *args)
            except RuntimeError:
                pass
        else:
            fn(*args)

    def _open(self):
        if self.fake:
            return None
        import pyaudio
        return self.pa.open(format=pyaudio.paInt16, channels=CHANNELS,
                            rate=SAMPLE_RATE, output=True,
                            output_device_index=self.device_index,
                            frames_per_buffer=CHUNK_FRAMES)

    def _finish_current(self) -> None:
        item = self.current_item
        if item is not None:
            self.current_item = None
            self._done_items.discard(item)
            self._emit(self.on_item_finished, item, self.played_ms.get(item, 0.0))

    def _run(self) -> None:
        stream = None
        try:
            stream = self._open()
            while True:
                with self.cv:
                    while not self.q and not self._shutdown:
                        if (self.current_item is not None
                                and self.current_item in self._done_items):
                            self._finish_current()
                        self.cv.wait(timeout=0.05)
                    if self._shutdown:
                        return
                    item_id, pcm = self.q.popleft()
                if self._stop_item == item_id:
                    continue
                if self.current_item != item_id:
                    self._finish_current()
                    self.current_item = item_id
                for off in range(0, len(pcm), CHUNK_BYTES):
                    with self.cv:
                        while self.paused and self._stop_item != item_id \
                                and not self._shutdown:
                            self.cv.wait(timeout=0.05)
                    if self._stop_item == item_id or self._shutdown:
                        break
                    chunk = pcm[off:off + CHUNK_BYTES]
                    try:
                        if self.fake:
                            time.sleep(len(chunk) / (SAMPLE_RATE * SAMPLE_WIDTH))
                        else:
                            stream.write(chunk)
                    except OSError as e:
                        self.device_error = repr(e)
                        logger.error(f"Realtime playback device error: {e!r}")
                        self._emit(self.on_device_error, self.device_error)
                        try:
                            if stream is not None:
                                stream.close()
                            stream = self._open()
                            logger.info("Realtime playback device reopened")
                        except OSError as e2:
                            logger.error(f"Realtime playback reopen failed: {e2!r}")
                            with self.cv:
                                self.q.clear()
                            break
                        continue
                    if item_id not in self.first_play_mono:
                        self.first_play_mono[item_id] = time.monotonic()
                        self._emit(self.on_item_started, item_id)
                    self.played_ms[item_id] += ms_of_bytes(len(chunk))
                if self._stop_item == item_id:
                    with self.cv:
                        self._stop_item = None
                        if self.current_item == item_id:
                            self.current_item = None
                            self._done_items.discard(item_id)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except OSError:
                    pass


class AudioRouter:
    """Composition root for the realtime audio path: resolves pinned devices,
    owns the mic and player threads. fake=True runs a paced null backend."""

    def __init__(self, *, input_spec: str = "", output_spec: str = "",
                 preroll_ms: int = 2000, loop=None, pa=None, fake: bool = False,
                 on_chunk: Callable[[bytes], None] = lambda pcm: None,
                 on_item_started=None, on_item_finished=None,
                 on_device_error=None, on_mic_error=None):
        self.fake = fake
        self.pa = pa
        self.input_fingerprint = self.output_fingerprint = "(fake)"
        in_idx = out_idx = None
        if not fake:
            if self.pa is None:
                import pyaudio
                self.pa = pyaudio.PyAudio()
            in_dev = resolve_device(self.pa, input_spec, True)
            out_dev = resolve_device(self.pa, output_spec, False)
            self.input_fingerprint = in_dev["fingerprint"]
            self.output_fingerprint = out_dev["fingerprint"]
            in_idx, out_idx = in_dev["index"], out_dev["index"]
        self.mic = MicCapture(self.pa, in_idx, preroll_ms, loop, on_chunk,
                              fake=fake, on_error=on_mic_error)
        self.player = Player(self.pa, out_idx, fake=fake, loop=loop,
                             on_item_started=on_item_started,
                             on_item_finished=on_item_finished,
                             on_device_error=on_device_error)

    def start(self) -> None:
        self.mic.start()
        self.player.start()
        logger.info(f"AudioRouter started: in={self.input_fingerprint} "
                    f"out={self.output_fingerprint}")

    def shutdown(self) -> None:
        self.mic.stop()
        self.player.shutdown()

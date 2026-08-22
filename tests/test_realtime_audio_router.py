"""AudioRouter mechanics on the null (fake) backend: played-ms accounting,
pause/resume for the barge-in grace window, hard stop, pre-roll arm flush.

No audio device is opened; the fake backend paces writes in real time so
played-ms is meaningful. Clips are kept short to keep the suite fast.
"""
import asyncio
import threading
import time

import pytest

from realtime.audio_router import (CHUNK_BYTES, MicCapture, Player,
                                   bytes_of_ms, ms_of_bytes, resolve_device,
                                   AudioDeviceError)


def _wait(cond, timeout=2.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_played_ms_counts_only_written_audio_and_finishes_when_done():
    started, finished = [], []
    p = Player(None, None, fake=True,
               on_item_started=started.append,
               on_item_finished=lambda i, ms: finished.append((i, ms)))
    p.start()
    clip = b"\x00" * bytes_of_ms(200)
    p.enqueue("item_a", clip)
    p.mark_done("item_a")
    assert _wait(lambda: finished)
    item, ms = finished[0]
    assert item == "item_a" and started == ["item_a"]
    assert abs(ms - 200.0) < 1e-6
    assert p.current_item is None
    p.shutdown()


def test_hard_stop_never_reports_more_than_played():
    p = Player(None, None, fake=True)
    p.start()
    p.enqueue("item_b", b"\x00" * bytes_of_ms(2000))
    assert _wait(lambda: p.played_ms["item_b"] >= 100)
    played = p.hard_stop("item_b")
    assert 100 <= played < 2000
    assert _wait(lambda: p.current_item is None)
    # nothing further written after the stop
    settled = p.played_ms["item_b"]
    time.sleep(0.1)
    assert p.played_ms["item_b"] == settled
    assert played <= settled  # reported at request time, never ahead of writes
    p.shutdown()


def test_pause_freezes_played_ms_and_resume_continues():
    finished = []
    p = Player(None, None, fake=True,
               on_item_finished=lambda i, ms: finished.append(ms))
    p.start()
    p.enqueue("item_c", b"\x00" * bytes_of_ms(400))
    p.mark_done("item_c")
    assert _wait(lambda: p.played_ms["item_c"] >= 60)
    frozen = p.pause()
    time.sleep(0.15)
    # at most one chunk (20 ms) can land after the pause request
    assert p.played_ms["item_c"] - frozen <= ms_of_bytes(CHUNK_BYTES) + 1e-6
    assert not finished, "paused audio must not finish"
    p.resume()
    assert _wait(lambda: finished)
    assert abs(finished[0] - 400.0) < 1e-6
    p.shutdown()


def test_mic_arm_flushes_preroll_then_streams_live():
    chunks = []
    mic = MicCapture(None, None, preroll_ms=200, loop=None,
                     on_chunk=chunks.append, fake=True)
    mic.start()
    # passive: ring fills, nothing delivered
    assert _wait(lambda: len(mic.ring) >= 5)
    assert chunks == []
    flushed_ms = mic.arm()
    assert flushed_ms > 0 and chunks and len(chunks[0]) == bytes_of_ms(flushed_ms)
    n = len(chunks)
    assert _wait(lambda: len(chunks) > n), "armed mic must stream live chunks"
    mic.disarm()
    n2 = len(chunks)
    time.sleep(0.08)
    assert len(chunks) == n2, "disarmed mic must not deliver"
    mic.stop()


class _FakePA:
    def __init__(self, devices):
        self._d = devices

    def get_device_count(self):
        return len(self._d)

    def get_device_info_by_index(self, i):
        name, inp, out = self._d[i]
        return {"name": name, "hostApi": 0, "maxInputChannels": inp,
                "maxOutputChannels": out, "defaultSampleRate": 44100.0}

    def get_host_api_info_by_index(self, i):
        return {"name": "MME"}


def test_resolve_device_is_explicit_never_defaults():
    pa = _FakePA([("Voicemeeter Out B3", 8, 0), ("Voicemeeter Out B1", 8, 0),
                  ("Voicemeeter Input", 0, 8)])
    assert resolve_device(pa, "out b3", True)["index"] == 0
    assert resolve_device(pa, "idx:2", False)["index"] == 2
    with pytest.raises(AudioDeviceError):
        resolve_device(pa, "voicemeeter out", True)      # ambiguous
    with pytest.raises(AudioDeviceError):
        resolve_device(pa, "samson", True)               # absent
    with pytest.raises(AudioDeviceError):
        resolve_device(pa, "idx:0", False)               # wrong direction

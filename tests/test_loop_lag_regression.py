"""Loop-starvation fix: get_queued_text must never block the event loop.

The bug: VoiceRecognition.get_queued_text called the blocking
queue.Queue.get(timeout=...) inline from an async method. Polled with
timeout=0.5, it stalled the loop ~0.5s of every ~0.6s cycle -- the chronic
"[LOOP LAG] Event loop stalled ~240ms" every ~1.24s (the lag monitor's 1s
probe woke mid-block, catching a uniform 0-500ms residual).

This test replays that polling pattern against LoopLagMonitor and fails if
the loop drifts past the project's 100ms bar. No audio device is used --
the queue is plain queue.Queue and stays empty (the worst case).
"""
import asyncio
import contextlib
import time

from components.voice.recognition import VoiceRecognition
from monitoring.loop_lag_monitor import LoopLagMonitor


async def test_polling_empty_queue_does_not_stall_loop():
    vr = VoiceRecognition()
    monitor = LoopLagMonitor(interval=0.1, warn_threshold_ms=100.0)
    monitor_task = asyncio.create_task(monitor.run())

    # The old poller's pattern: repeated waits on an empty queue. With the
    # inline blocking get, each call stalled the loop for the full timeout
    # and the monitor showed ~250ms max drift here.
    deadline = time.perf_counter() + 1.2
    while time.perf_counter() < deadline:
        assert await vr.get_queued_text(timeout=0.25) is None

    monitor.stop()
    monitor_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await monitor_task

    assert monitor.samples >= 5, 'monitor must have actually sampled'
    assert monitor.max_drift_ms < 100.0, (
        f'event loop stalled {monitor.max_drift_ms:.0f}ms while waiting on '
        f'the recognition queue -- get_queued_text is blocking the loop again'
    )


async def test_get_queued_text_still_returns_queued_text():
    vr = VoiceRecognition()
    vr.audio_queue.put('hey bot hello')
    assert await vr.get_queued_text(timeout=0.25) == 'hey bot hello'

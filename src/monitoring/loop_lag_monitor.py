"""
Event-loop lag monitor.

The asyncio loop should never be blocked for long by synchronous work
(PyAudio writes, numpy crunching, sync file/network I/O). When it is, every
latency-sensitive coroutine starves -- most visibly the Twitch EventSub
WebSocket, which then fails to answer a server PING in time and gets torn
down with close code 4002.

This probe measures that starvation directly. It schedules a wake-up every
``interval`` seconds and compares the actual sleep duration against the
requested one. The overshoot ("drift") is time the loop spent unable to run
this coroutine -- i.e. time it was blocked doing something synchronous. A
healthy loop drifts by a millisecond or two; a loop stalled inside a blocking
``stream.write()`` drifts by hundreds.

Usage (wire into a TaskRegistry, never a raw create_task):

    self._loop_lag_monitor = LoopLagMonitor(warn_threshold_ms=100.0)
    self.task_registry.create_task(
        self._loop_lag_monitor.run(),
        name="loop_lag_monitor",
    )

Then read ``monitor.get_stats()`` for max/avg drift, or watch the logs for
the WARNING emitted whenever a single tick overshoots ``warn_threshold_ms``.
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)


class LoopLagMonitor:
    """
    Measures asyncio event-loop lag via sleep-drift sampling.

    A dedicated coroutine sleeps for ``interval`` seconds in a loop. Because
    ``asyncio.sleep`` can only resume once the loop is free, the difference
    between the wall-clock elapsed time and ``interval`` is the loop's lag for
    that tick. This is the cheapest reliable gauge of loop starvation.
    """

    def __init__(
        self,
        interval: float = 1.0,
        warn_threshold_ms: float = 100.0,
        on_stall: Optional[Callable[[float], None]] = None,
    ):
        """
        Args:
            interval: Seconds between probe samples. 1.0s is a cheap, low-noise
                default; the drift, not the interval, is what matters.
            warn_threshold_ms: Emit a WARNING when a single tick's drift
                exceeds this. 100ms is the project's "no stall should exceed
                this under active TTS + voice" bar.
            on_stall: Optional callback invoked with the drift (ms) whenever a
                tick exceeds the threshold. Kept synchronous and trivial -- it
                runs on the loop.
        """
        self.interval = interval
        self.warn_threshold_ms = warn_threshold_ms
        self.on_stall = on_stall

        self._running = False

        # Rolling statistics (milliseconds)
        self.samples = 0
        self.max_drift_ms = 0.0
        self.total_drift_ms = 0.0
        self.last_drift_ms = 0.0
        self.stalls = 0  # ticks that exceeded warn_threshold_ms

    async def run(self) -> None:
        """
        Run the probe until :meth:`stop` is called or the task is cancelled.

        Designed to be launched via TaskRegistry (never a raw
        ``asyncio.create_task``) per the project's async-task rule.
        """
        self._running = True
        logger.info(
            f"LoopLagMonitor started (interval={self.interval}s, "
            f"warn>{self.warn_threshold_ms:.0f}ms)"
        )

        # perf_counter is monotonic and high-resolution -- correct for
        # measuring short durations regardless of wall-clock adjustments.
        expected = time.perf_counter() + self.interval

        try:
            while self._running:
                await asyncio.sleep(self.interval)

                now = time.perf_counter()
                # Drift = how much later than scheduled we actually woke up.
                drift_ms = max(0.0, (now - expected) * 1000.0)
                expected = now + self.interval

                self.samples += 1
                self.last_drift_ms = drift_ms
                self.total_drift_ms += drift_ms
                if drift_ms > self.max_drift_ms:
                    self.max_drift_ms = drift_ms

                if drift_ms > self.warn_threshold_ms:
                    self.stalls += 1
                    # Blaming "synchronous work" for a multi-minute gap sent
                    # the diagnosis the wrong way: a 73-minute stall turned
                    # out to be the console's QuickEdit selection freezing
                    # the whole PROCESS (one click in the window pauses it
                    # until Esc/Enter). Name the likely cause by scale.
                    if drift_ms > 30_000:
                        cause = ("the whole process was suspended -- a click in "
                                 "the console window (QuickEdit selection, press "
                                 "Esc / disable QuickEdit) or system sleep, not "
                                 "code")
                    else:
                        cause = "synchronous work is blocking the loop"
                    logger.warning(
                        f"[LOOP LAG] Event loop stalled ~{drift_ms:.0f}ms "
                        f"(threshold {self.warn_threshold_ms:.0f}ms) -- "
                        f"{cause}"
                    )
                    if self.on_stall is not None:
                        try:
                            self.on_stall(drift_ms)
                        except Exception as e:
                            logger.error(f"LoopLagMonitor on_stall callback failed: {e}")

        except asyncio.CancelledError:
            logger.debug("LoopLagMonitor cancelled")
            raise
        finally:
            self._running = False
            logger.info(
                f"LoopLagMonitor stopped after {self.samples} samples "
                f"(max drift {self.max_drift_ms:.0f}ms, {self.stalls} stalls)"
            )

    def stop(self) -> None:
        """Signal the probe loop to exit after its current sleep."""
        self._running = False

    def get_stats(self) -> Dict[str, Any]:
        """
        Return current lag statistics (all durations in milliseconds).

        ``avg_drift_ms`` and ``max_drift_ms`` are the headline before/after
        numbers: on a healthy loop both stay near zero.
        """
        avg = self.total_drift_ms / self.samples if self.samples else 0.0
        return {
            "samples": self.samples,
            "last_drift_ms": round(self.last_drift_ms, 2),
            "avg_drift_ms": round(avg, 2),
            "max_drift_ms": round(self.max_drift_ms, 2),
            "stalls": self.stalls,
            "warn_threshold_ms": self.warn_threshold_ms,
            "running": self._running,
        }

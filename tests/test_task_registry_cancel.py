"""Cancelling a tracked task must not be logged as a task failure.

The bug: _task_done called task.exception() unconditionally, and on a
CANCELLED task that re-raises CancelledError instead of returning it. Every
cancelled task therefore blew up inside its own done-callback, and asyncio
logged a full traceback per task on every shutdown -- five of them in the
2026-08-22 realtime boot, which read like a crash but was an orderly exit.
"""
import asyncio
import logging

import pytest

from utils.task_registry import TaskRegistry


async def test_cancelled_task_is_not_logged_as_an_error(caplog):
    registry = TaskRegistry()

    async def forever():
        await asyncio.sleep(3600)

    task = registry.create_task(forever(), name="sleeper")
    await asyncio.sleep(0.05)

    # Cancel directly: registry.shutdown() clears task_stats, which would
    # hide what the done-callback recorded.
    with caplog.at_level(logging.ERROR):
        task.cancel()
        await asyncio.sleep(0.05)      # let the done-callback run

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, f"cancelled task logged as failure: {[r.message for r in errors]}"
    assert registry.task_stats["sleeper"]["status"] == "cancelled"
    assert "error" not in registry.task_stats["sleeper"]
    await registry.shutdown()


async def test_genuinely_failing_task_is_still_reported(caplog):
    """The cancellation guard must not hide real failures."""
    registry = TaskRegistry()

    async def boom():
        raise ValueError("real failure")

    registry.create_task(boom(), name="boom")
    with caplog.at_level(logging.ERROR):
        await asyncio.sleep(0.1)

    assert registry.task_stats["boom"]["status"] == "done"
    assert "real failure" in registry.task_stats["boom"]["error"]
    assert any("boom" in r.message and "real failure" in r.message
               for r in caplog.records if r.levelno >= logging.ERROR)
    await registry.shutdown()

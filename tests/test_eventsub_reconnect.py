"""A planned reconnect must not stall the event loop.

Live 2026-08-27 12:19:47: "EventSub handler for 'session_reconnect' blocked
the read loop for 5.00s". The handler awaited websocket.close() while
running INSIDE the read loop, and close() waits for Twitch's close-handshake
reply -- which only that same read loop can receive. It deadlocked until the
handshake timed out, stalling every task in the process for seconds.
"""
import asyncio
from pathlib import Path

import pytest

from twitch.eventsub_websocket import EventSubWebSocket

SRC = Path("src/twitch/eventsub_websocket.py").read_text(encoding="utf-8")
HANDLER = SRC.split("elif message_type == 'session_reconnect'")[1].split("elif message_type")[0]


def test_the_handler_does_not_await_the_close():
    assert "await self.websocket.close()" not in HANDLER, \
        "awaiting close inside the read loop deadlocks the handshake"
    assert "registry_create_task" in HANDLER, "the close must be scheduled"


def test_the_close_is_registry_tracked():
    assert 'name="eventsub_reconnect_close"' in HANDLER


def _sub():
    es = EventSubWebSocket.__new__(EventSubWebSocket)
    es.reconnect_url = None
    es.websocket = None
    return es


async def test_reconnect_url_is_captured_and_the_loop_is_not_blocked():
    es = _sub()
    closed = asyncio.Event()

    class SlowSocket:
        async def close(self):
            await asyncio.sleep(0.2)      # stands in for the close handshake
            closed.set()
    es.websocket = SlowSocket()

    msg = {'metadata': {'message_type': 'session_reconnect'},
           'payload': {'session': {'reconnect_url': 'wss://new.example/ws'}}}
    started = asyncio.get_running_loop().time()
    await es._process_message(msg)
    elapsed = asyncio.get_running_loop().time() - started

    assert es.reconnect_url == 'wss://new.example/ws'
    assert elapsed < 0.1, f"handler blocked the read loop for {elapsed:.2f}s"
    await asyncio.wait_for(closed.wait(), timeout=2)


async def test_a_failing_close_does_not_escape():
    es = _sub()

    class BrokenSocket:
        async def close(self):
            raise RuntimeError("socket already gone")
    es.websocket = BrokenSocket()

    await es._process_message({'metadata': {'message_type': 'session_reconnect'},
                              'payload': {'session': {'reconnect_url': 'wss://x/ws'}}})
    await asyncio.sleep(0.05)             # let the scheduled close run
    assert es.reconnect_url == 'wss://x/ws'


async def test_no_socket_is_fine():
    es = _sub()
    await es._process_message({'metadata': {'message_type': 'session_reconnect'},
                              'payload': {'session': {'reconnect_url': 'wss://y/ws'}}})
    assert es.reconnect_url == 'wss://y/ws'

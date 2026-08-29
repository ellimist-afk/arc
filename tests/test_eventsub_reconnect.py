"""A planned reconnect must not stall the event loop.

Live 2026-08-27 12:19:47: "EventSub handler for 'session_reconnect' blocked
the read loop for 5.00s". The handler awaited websocket.close() while
running INSIDE the read loop, and close() waits for Twitch's close-handshake
reply -- which only that same read loop can receive. It deadlocked until the
handshake timed out, stalling every task in the process for seconds.
"""
import asyncio
from pathlib import Path


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


# ------------------------------------------------ the real blind window

SUB = SRC.split("async def _subscribe_to_events")[1].split("async def ")[0]
WELCOME = SRC.split("if message_type == 'session_welcome'")[1].split("elif message_type")[0]


def test_the_welcome_no_longer_declares_the_gap_over():
    """A fresh session has no subscriptions yet; events in that sliver are
    dropped too, so measuring to the welcome under-reports the outage."""
    assert "re-established after" not in WELCOME
    assert "gap_started = self._disconnected_at" in WELCOME


def test_the_gap_is_handed_to_the_resubscribe():
    assert "gap_started=gap_started" in WELCOME


def test_the_blind_window_is_reported_after_the_last_subscription():
    report = SUB.index("EventSub blind for")
    loop = SUB.index("await self._create_subscription(sub)")
    assert loop < report, "the window closes only once subscriptions are back"
    assert "offline + resubscribe" in SUB


def test_a_first_connection_reports_no_gap():
    """gap_started is None on the very first session -- nothing was missed."""
    assert "if gap_started is not None:" in SUB


# --------------------------------------------- a rejected reconnect handoff

def test_4007_is_called_out_distinctly():
    """Live 2026-08-27 12:19:53: Close(code=4007, 'invalid reconnect
    attempt') -- Twitch refused the handoff because the read-loop deadlock
    made us five seconds late. It read as a generic close."""
    assert "4007" in SRC
    assert "reconnect handoff REJECTED" in SRC


def test_4007_check_reads_both_close_frames():
    block = SRC.split("reconnect handoff REJECTED")[0]
    tail = block[block.rindex("if getattr("):]
    assert "rcvd" in tail and "sent" in tail, "either side may carry the code"

"""EventSub session_welcome must not block the WebSocket read loop.

The bug: the session_welcome branch of _process_message awaited
_subscribe_to_events() inline -- ~13 sequential subscription POSTs, 3.11s
observed -- stalling the message pump (and the PONGs to Twitch's server
pings) for the whole duration.

No network, no Twitch connection: _subscribe_to_events is patched with a
slow stand-in and _process_message is fed a synthetic welcome frame.
"""
import asyncio
import time

from twitch.eventsub_websocket import EventSubWebSocket


def make_welcome_frame():
    return {
        'metadata': {'message_type': 'session_welcome'},
        'payload': {
            'session': {
                'id': 'test-session-123',
                'reconnect_url': None,
                'keepalive_timeout_seconds': 10,
            }
        },
    }


async def test_session_welcome_returns_before_subscriptions_finish():
    client = EventSubWebSocket(client_id='x', access_token='y', channel_name='z')

    subscribed = asyncio.Event()

    async def slow_subscribe(gap_started=None):
        await asyncio.sleep(1.0)  # stands in for the 3.11s of POSTs
        subscribed.set()

    client._subscribe_to_events = slow_subscribe

    start = time.perf_counter()
    await client._process_message(make_welcome_frame())
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5, (
        f'_process_message blocked {elapsed:.2f}s on session_welcome -- '
        f'subscriptions are running inline on the read loop again'
    )
    assert client.session_id == 'test-session-123'

    # The subscribe work must still happen, just off the read loop
    await asyncio.wait_for(subscribed.wait(), timeout=3.0)

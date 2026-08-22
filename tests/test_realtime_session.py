"""RealtimeVoiceSession against an in-process fake transport: no websockets,
no network, no API key. Verifies the session contract the backend relies on:
configuration with local-authoritative turn detection, event -> callback
translation, the three control messages, and reconnect behavior.
"""
import asyncio
import base64
import json

import pytest

from realtime.session import RealtimeVoiceSession


class FakeWS:
    """Minimal ws-like object: the test pushes inbound events, the session's
    outbound messages are collected."""

    def __init__(self):
        self.inbound: asyncio.Queue = asyncio.Queue()
        self.outbound = []
        self.closed = False

    async def send(self, raw):
        self.outbound.append(json.loads(raw))

    async def recv(self):
        item = await self.inbound.get()
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)

    async def close(self):
        self.closed = True
        await self.inbound.put(None)

    def push(self, ev):
        self.inbound.put_nowait(ev)

    def sent_types(self):
        return [m["type"] for m in self.outbound]


class Harness:
    def __init__(self, max_reconnects=2):
        self.sockets = []
        self.events = []
        self.session = RealtimeVoiceSession(
            model="gpt-realtime-2.1-mini", voice="marin", vad="server_vad",
            instructions_provider=lambda: "persona text", api_key="k",
            connect=self._connect, max_reconnects=max_reconnects)
        s = self.session
        s.on_connected = lambda: self.events.append(("connected",))
        s.on_disconnected = lambda r: self.events.append(("disconnected", r))
        s.on_speech_started = lambda ms: self.events.append(("speech_started", ms))
        s.on_speech_stopped = lambda ms: self.events.append(("speech_stopped", ms))
        s.on_audio_delta = lambda i, r, pcm: self.events.append(("audio", i, r, pcm))
        s.on_audio_done = lambda i, r: self.events.append(("audio_done", i, r))
        s.on_transcript = lambda role, t: self.events.append(("transcript", role, t))
        s.on_response_done = lambda r, st, u: self.events.append(("done", r, st, u))

    async def _connect(self, url, headers):
        assert headers.get("Authorization") == "Bearer k"
        ws = FakeWS()
        self.sockets.append(ws)
        return ws

    @property
    def ws(self):
        return self.sockets[-1]

    async def settle(self, n=8):
        # Drain deterministically: yield until the session has consumed every
        # queued event (sleeping a fixed number of times raced the receive
        # loop and truncated the batch).
        for _ in range(200):
            if not self.sockets or self.ws.inbound.empty():
                break
            await asyncio.sleep(0.005)
        for _ in range(n):
            await asyncio.sleep(0.005)


async def test_configures_with_local_authoritative_turn_detection():
    h = Harness()
    await h.session.start()
    await h.settle()
    cfg = h.ws.outbound[0]
    assert cfg["type"] == "session.update"
    td = cfg["session"]["audio"]["input"]["turn_detection"]
    assert td == {"type": "server_vad", "create_response": False,
                  "interrupt_response": False}
    assert cfg["session"]["instructions"] == "persona text"
    assert cfg["session"]["audio"]["output"]["voice"] == "marin"
    assert ("connected",) in h.events
    await h.session.stop()


async def test_events_translate_to_provider_neutral_callbacks():
    h = Harness()
    await h.session.start()
    await h.settle()
    pcm = b"\x01\x02" * 10
    for ev in [
        {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
        {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 900},
        {"type": "response.created", "response": {"id": "resp_1"}},
        {"type": "response.output_audio.delta", "response_id": "resp_1",
         "item_id": "item_1", "delta": base64.b64encode(pcm).decode()},
        {"type": "response.output_audio.done", "response_id": "resp_1",
         "item_id": "item_1"},
        {"type": "response.output_audio_transcript.done", "transcript": "Hi."},
        {"type": "conversation.item.input_audio_transcription.completed",
         "transcript": "hey bud"},
        {"type": "conversation.item.added"},               # benign, ignored
        {"type": "response.done", "response": {"id": "resp_1",
                                               "status": "completed",
                                               "usage": {"total_tokens": 5}}},
    ]:
        h.ws.push(ev)
    await h.settle()
    assert ("speech_started", 100) in h.events
    assert ("speech_stopped", 900) in h.events
    assert ("audio", "item_1", "resp_1", pcm) in h.events
    assert ("audio_done", "item_1", "resp_1") in h.events
    assert ("transcript", "arc", "Hi.") in h.events
    assert ("transcript", "streamer", "hey bud") in h.events
    assert ("done", "resp_1", "completed", {"total_tokens": 5}) in h.events
    assert h.session.item_of_response["resp_1"] == "item_1"
    assert h.session.active_response is None, "cleared on response.done"
    await h.session.stop()


async def test_control_messages_and_mic_pump():
    h = Harness()
    await h.session.start()
    await h.settle()
    h.ws.push({"type": "response.created", "response": {"id": "resp_9"}})
    await h.settle()
    assert await h.session.authorize()
    assert await h.session.cancel_response()          # uses active_response
    assert await h.session.truncate("item_9", 1234.9)
    assert await h.session.inject_text("[Twitch chat] viewer 'x': \"hi\"")
    h.session.send_audio(b"\x00" * 4)
    await h.settle()
    out = h.ws.outbound[1:]
    assert [m["type"] for m in out] == [
        "response.create", "response.cancel", "conversation.item.truncate",
        "conversation.item.create", "input_audio_buffer.append"]
    assert out[1]["response_id"] == "resp_9"
    assert out[2] == {"type": "conversation.item.truncate", "item_id": "item_9",
                      "content_index": 0, "audio_end_ms": 1234}
    assert out[3]["item"]["role"] == "user"
    assert base64.b64decode(out[4]["audio"]) == b"\x00" * 4
    await h.session.stop()


async def test_drop_reconnects_with_fresh_config_and_reports():
    h = Harness(max_reconnects=3)
    await h.session.start()
    await h.settle()
    h.ws.push(ConnectionError("socket dropped"))
    await asyncio.sleep(2.3)          # first backoff is 2 s
    assert len(h.sockets) == 2, "a new socket must be opened after a drop"
    assert h.sockets[1].outbound[0]["type"] == "session.update"
    assert any(e[0] == "disconnected" for e in h.events)
    assert h.session.reconnects == 1
    assert h.session.connected
    await h.session.stop()

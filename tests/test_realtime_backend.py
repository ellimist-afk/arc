"""RealtimeVoiceBackend end-to-end with fake audio, a fake transport, and the
real AttentionRouter. Asserts the §8/§9 flows as observable effects:

  wake (via legacy transcript) -> mic armed, window open
  streamer turn end           -> exactly one response.create
  barge-in                    -> pause, then stop + cancel + truncate at the
                                 played-ms frozen at onset
  cough                       -> pause, then resume; nothing cancelled
  provider drop               -> playback stopped, audio released, PASSIVE

Authorization discipline is checked throughout: every response.create on
the wire corresponds to an AUTHORIZE_RESPONSE decision.
"""
import asyncio
import base64
import json

import pytest

from attention.config import AttentionConfig
from attention.router import AttentionRouter
from attention.stimulus import Action, ConvState
from realtime.audio_router import AudioRouter, bytes_of_ms
from realtime.backend import RealtimeVoiceBackend
from realtime.session import RealtimeVoiceSession


class FakeWS:
    def __init__(self):
        self.inbound: asyncio.Queue = asyncio.Queue()
        self.outbound = []

    async def send(self, raw):
        msg = json.loads(raw)
        self.outbound.append(msg)
        if msg.get("type") == "session.update":
            # the real server acknowledges the configuration; the session is
            # not "connected" until it does
            self.inbound.put_nowait({"type": "session.updated"})

    async def recv(self):
        item = await self.inbound.get()
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)

    async def close(self):
        await self.inbound.put(None)

    def push(self, ev):
        self.inbound.put_nowait(ev)

    def types(self):
        return [m["type"] for m in self.outbound]


class Rig:
    """One backend wired to fake audio + fake transport + a manual clock."""

    def __init__(self, grace_s=0.25, window_s=45.0):
        self.now = 1000.0
        self.sockets = []
        self.audio = AudioRouter(fake=True, preroll_ms=200)
        self.session = RealtimeVoiceSession(
            model="gpt-realtime-2.1-mini", voice="marin", vad="server_vad",
            instructions_provider=lambda: "persona", api_key="k",
            connect=self._connect, max_reconnects=3)
        self.router = AttentionRouter(AttentionConfig(
            streamer_username="cassova_", grace_s=grace_s, window_s=window_s))
        self.backend = RealtimeVoiceBackend(
            audio=self.audio, session=self.session, router=self.router,
            streamer_username="cassova_", clock=lambda: self.now,
            on_release_audio=lambda: self.released.append(True))
        self.released = []

    async def _connect(self, url, headers):
        ws = FakeWS()
        self.sockets.append(ws)
        return ws

    @property
    def ws(self):
        return self.sockets[-1]

    async def start(self):
        await self.backend.start()
        await self.settle()

    async def stop(self):
        await self.backend.stop()

    async def settle(self, n=10):
        for _ in range(n):
            await asyncio.sleep(0.01)

    async def push(self, ev, n=10):
        self.ws.push(ev)
        await self.settle(n)

    def creates(self):
        return self.ws.types().count("response.create")

    def authorized(self):
        return sum(1 for d in self.backend.decisions
                   for _ in [0] if Action.AUTHORIZE_RESPONSE in d.actions) \
            + sum(1 for d in self.backend.decisions for r in d.released
                  if Action.AUTHORIZE_RESPONSE in r.actions)

    async def arc_speaks(self, item_id="item_1", rid="resp_1", ms=1500):
        """Server starts a response and streams ms of audio for item_id."""
        await self.push({"type": "response.created", "response": {"id": rid}})
        pcm = b"\x00" * bytes_of_ms(ms)
        await self.push({"type": "response.output_audio.delta", "response_id": rid,
                         "item_id": item_id,
                         "delta": base64.b64encode(pcm).decode()})
        # wait until the player has actually written something
        for _ in range(100):
            if self.audio.player.played_ms.get(item_id, 0) >= 100:
                break
            await asyncio.sleep(0.01)
        await self.settle()


async def test_wake_then_turn_authorizes_exactly_one_response():
    rig = Rig()
    await rig.start()
    assert rig.backend.state is ConvState.PASSIVE
    assert not rig.audio.mic.armed

    # unrelated legacy transcript: nothing happens, mic stays local
    assert await rig.backend.on_legacy_transcript("playing some music now") is None
    assert not rig.audio.mic.armed and rig.creates() == 0

    d = await rig.backend.on_legacy_transcript("play not the volume")  # fuzzy
    assert d.rule == "R2.wake_open" and Action.OPEN_WINDOW in d.actions
    assert rig.backend.state is ConvState.LISTENING
    assert rig.audio.mic.armed, "window open => mic streams to the session"

    await rig.push({"type": "input_audio_buffer.speech_started", "audio_start_ms": 0})
    assert rig.creates() == 0, "no response while the streamer is still talking"
    await rig.push({"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 1200})
    assert rig.creates() == 1
    assert rig.authorized() == 1
    await rig.stop()


async def test_barge_in_pauses_then_stops_cancels_and_truncates_at_frozen_ms():
    rig = Rig(grace_s=0.25)
    await rig.start()
    await rig.backend.manual_arm()
    await rig.arc_speaks(ms=3000)
    assert rig.backend.state is ConvState.ARC_SPEAKING
    assert rig.audio.player.current_item == "item_1"

    # streamer talks over Arc -> grace: playback pauses, nothing cancelled yet
    await rig.push({"type": "input_audio_buffer.speech_started", "audio_start_ms": 5000})
    assert rig.backend.state is ConvState.INTERRUPT_PENDING
    assert rig.audio.player.paused
    frozen = next(d for d in rig.backend.decisions if d.rule == "R-int.grace_start")
    onset_ms = rig.audio.player.played_ms["item_1"]
    assert "response.cancel" not in rig.ws.types()

    # speech continues past the grace window -> commit the interruption
    rig.now += 0.3
    await rig.backend.poll()
    await rig.settle()
    commit = next(d for d in rig.backend.decisions if d.rule == "R-int.commit")
    assert commit.truncate_item_id == "item_1"
    assert rig.backend.state is ConvState.LISTENING
    assert not rig.audio.player.paused
    for _ in range(50):
        if rig.audio.player.current_item is None:
            break
        await asyncio.sleep(0.01)
    assert rig.audio.player.current_item is None, "audio discarded"

    types = rig.ws.types()
    assert "response.cancel" in types and "conversation.item.truncate" in types
    trunc = next(m for m in rig.ws.outbound if m["type"] == "conversation.item.truncate")
    assert trunc["item_id"] == "item_1"
    # truncation value is the played-ms frozen at speech onset (doc §9): it
    # must not exceed what had been written by then (+ one chunk of slack)
    assert trunc["audio_end_ms"] <= onset_ms + 20
    assert trunc["audio_end_ms"] == int(commit.truncate_played_ms)
    cancel = next(m for m in rig.ws.outbound if m["type"] == "response.cancel")
    assert cancel["response_id"] == "resp_1"
    await rig.stop()


async def test_cough_within_grace_resumes_and_cancels_nothing():
    rig = Rig(grace_s=0.25)
    await rig.start()
    await rig.backend.manual_arm()
    await rig.arc_speaks(ms=3000)

    await rig.push({"type": "input_audio_buffer.speech_started", "audio_start_ms": 5000})
    assert rig.audio.player.paused
    await rig.push({"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 5150})
    assert rig.backend.state is ConvState.ARC_SPEAKING
    assert not rig.audio.player.paused, "false start => playback resumes"
    rig.now += 0.3
    await rig.backend.poll()
    assert "response.cancel" not in rig.ws.types()
    assert "conversation.item.truncate" not in rig.ws.types()
    assert rig.creates() == 0, "a cough is not a turn; nothing authorized"
    await rig.stop()


async def test_provider_drop_stops_playback_releases_audio_and_goes_passive():
    rig = Rig()
    await rig.start()
    await rig.backend.manual_arm()
    await rig.arc_speaks(ms=3000)
    assert rig.audio.mic.armed
    rig.ws.push(ConnectionError("socket dropped"))
    await rig.settle(20)
    assert rig.backend.state is ConvState.PASSIVE
    assert not rig.audio.mic.armed, "mic goes local-only on provider loss"
    assert rig.released == [True], "legacy pipeline gets the output device"
    for _ in range(50):
        if rig.audio.player.current_item is None:
            break
        await asyncio.sleep(0.01)
    assert rig.audio.player.current_item is None
    await rig.stop()


async def test_window_expiry_disarms_mic():
    rig = Rig(window_s=45.0)
    await rig.start()
    await rig.backend.manual_arm()
    assert rig.audio.mic.armed
    rig.now += 46.0
    await rig.backend.poll()
    assert rig.backend.state is ConvState.PASSIVE
    assert not rig.audio.mic.armed
    await rig.stop()


async def test_every_response_create_has_an_authorizing_decision():
    rig = Rig()
    await rig.start()
    await rig.backend.manual_arm()
    for i in range(3):
        await rig.push({"type": "input_audio_buffer.speech_started", "audio_start_ms": i * 1000})
        await rig.push({"type": "input_audio_buffer.speech_stopped", "audio_end_ms": i * 1000 + 500})
    assert rig.creates() == 3 == rig.authorized() == rig.backend.authorized_count
    await rig.stop()

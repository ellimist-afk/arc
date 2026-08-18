"""A tiny fake Realtime server for wiring tests (no API key, no audio HW).

Speaks just enough of the GA protocol to exercise realtime_probe.py:
  - session.created / session.updated
  - synthesizes a speech turn (speech_started → speech_stopped) every ~6 s
  - on response.create: response.created → paced response.output_audio.delta
    chirp chunks (~4 s of audio) → response.done with a plausible usage block
  - mid-response, injects ONE barge-in (speech_started, sustained) so the
    probe's interrupt path runs, and later ONE cough (speech_started followed
    by speech_stopped ~150 ms later) so the grace path runs
  - answers response.cancel with a cancelled response.done and
    conversation.item.truncate with conversation.item.truncated

Run:  python spike/fake_realtime_server.py   (listens on ws://localhost:8787)
Then: python spike/realtime_probe.py --no-audio --auto-authorize \
          --url ws://localhost:8787 --max-minutes 0.5
"""
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import make_chirp, ms_of_bytes

try:
    import websockets
except ImportError:
    sys.exit("pip install websockets")

PORT = 8787
CHUNK_MS = 100  # server-side delta pacing


class FakeSession:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0
        self.cancelled = set()
        self.did_barge = False
        self.did_cough = False

    async def send(self, obj):
        await self.ws.send(json.dumps(obj))

    async def run(self):
        await self.send({"type": "session.created",
                         "session": {"id": "sess_fake", "model": "fake"}})
        speech_task = asyncio.create_task(self.speech_loop())
        try:
            async for raw in self.ws:
                ev = json.loads(raw)
                t = ev.get("type")
                if t == "session.update":
                    await self.send({"type": "session.updated",
                                     "session": ev.get("session", {})})
                elif t == "response.create":
                    asyncio.create_task(self.respond())
                elif t == "response.cancel":
                    self.cancelled.add(ev.get("response_id"))
                elif t == "conversation.item.truncate":
                    await self.send({"type": "conversation.item.truncated",
                                     "item_id": ev.get("item_id"),
                                     "content_index": 0,
                                     "audio_end_ms": ev.get("audio_end_ms")})
                # input_audio_buffer.append: accepted silently, like the real API
        finally:
            speech_task.cancel()

    async def speech_loop(self):
        """A fake streamer turn every ~6 s (only useful before/between responses)."""
        while True:
            await asyncio.sleep(6)
            await self.send({"type": "input_audio_buffer.speech_started",
                             "audio_start_ms": int(time.monotonic() * 1000) % 100000,
                             "item_id": f"item_user_{self.n}"})
            await asyncio.sleep(1.2)
            await self.send({"type": "input_audio_buffer.speech_stopped",
                             "audio_end_ms": int(time.monotonic() * 1000) % 100000,
                             "item_id": f"item_user_{self.n}"})
            self.n += 1

    async def respond(self):
        self.n += 1
        rid = f"resp_{self.n}"
        item = f"item_asst_{self.n}"
        await self.send({"type": "response.created", "response": {"id": rid}})
        pcm = make_chirp(4000, amp=0.3)
        chunk_bytes = int(24000 * 2 * CHUNK_MS / 1000)
        sent_ms = 0.0
        # schedule one barge-in on the 2nd response, one cough on the 3rd
        barge_at = 1500 if (not self.did_barge and self.n >= 2) else None
        cough_at = 1500 if (barge_at is None and not self.did_cough and self.n >= 3) else None
        for off in range(0, len(pcm), chunk_bytes):
            if rid in self.cancelled:
                await self.send({"type": "response.done", "response": {
                    "id": rid, "status": "cancelled",
                    "usage": self._usage(sent_ms)}})
                return
            await self.send({"type": "response.output_audio.delta",
                             "response_id": rid, "item_id": item,
                             "delta": base64.b64encode(pcm[off:off + chunk_bytes]).decode()})
            sent_ms += ms_of_bytes(min(chunk_bytes, len(pcm) - off))
            if barge_at and sent_ms >= barge_at:
                barge_at = None
                self.did_barge = True
                await self.send({"type": "input_audio_buffer.speech_started",
                                 "audio_start_ms": 0, "item_id": "item_barge"})
                # no speech_stopped for a while => sustained speech => interrupt
            if cough_at and sent_ms >= cough_at:
                cough_at = None
                self.did_cough = True
                await self.send({"type": "input_audio_buffer.speech_started",
                                 "audio_start_ms": 0, "item_id": "item_cough"})
                await asyncio.sleep(0.15)
                await self.send({"type": "input_audio_buffer.speech_stopped",
                                 "audio_end_ms": 150, "item_id": "item_cough"})
            await asyncio.sleep(CHUNK_MS / 1000.0)
        await self.send({"type": "response.done", "response": {
            "id": rid, "status": "completed", "usage": self._usage(sent_ms)}})
        # after the barge-in interrupt, give the probe a fresh turn to answer
        if self.did_barge and not self.did_cough:
            await asyncio.sleep(0.5)

    def _usage(self, out_ms):
        # rough: ~10 audio tokens per second in each direction
        out_tok = max(1, int(out_ms / 100))
        return {"total_tokens": 120 + out_tok, "input_tokens": 120,
                "output_tokens": out_tok,
                "input_token_details": {"text_tokens": 100, "audio_tokens": 20,
                                        "cached_tokens": 0,
                                        "cached_tokens_details": {
                                            "text_tokens": 0, "audio_tokens": 0}},
                "output_token_details": {"text_tokens": 5, "audio_tokens": out_tok}}


async def main():
    async def handler(ws):
        print("client connected")
        try:
            await FakeSession(ws).run()
        except websockets.ConnectionClosed:
            pass
        print("client disconnected")

    async with websockets.serve(handler, "localhost", PORT, max_size=1 << 24):
        print(f"fake Realtime server on ws://localhost:{PORT}")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

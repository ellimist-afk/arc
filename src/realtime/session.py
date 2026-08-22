"""RealtimeVoiceSession: OpenAI Realtime WebSocket client (doc §7).

Responsibilities, and nothing else:
  - connect / configure / reconnect with backoff (session lifecycle)
  - stream mic PCM in (input_audio_buffer.append) while the backend feeds it
  - translate provider events into provider-neutral callbacks
  - send the three control messages the attention layer may request:
    response.create (authorize), response.cancel, conversation.item.truncate

Local attention is authoritative: turn detection runs server-side but
create_response and interrupt_response are OFF -- a response is created only
when RealtimeVoiceBackend executes an AUTHORIZE_RESPONSE decision.

The transport is injectable (`connect=`) so the full event flow is testable
without websockets, a network, or an API key. API facts (event names, session
shape) verified 2026-08-18/22 against the GA Realtime API and the spike run.
"""
import asyncio
import base64
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
DEFAULT_URL = "wss://api.openai.com/v1/realtime"

# GA events that arrive on every turn and carry nothing we act on.
BENIGN_EVENTS = frozenset({
    "input_audio_buffer.committed",
    "conversation.item.added", "conversation.item.done",
    "conversation.item.input_audio_transcription.delta",
    "response.output_item.added", "response.output_item.done",
    "response.content_part.added", "response.content_part.done",
    "response.output_audio_transcript.delta",
})


class _Callbacks:
    """Optional provider-neutral hooks; every one may be None."""
    on_connected: Optional[Callable[[], None]] = None
    on_disconnected: Optional[Callable[[str], None]] = None
    on_speech_started: Optional[Callable[[Optional[int]], None]] = None
    on_speech_stopped: Optional[Callable[[Optional[int]], None]] = None
    on_audio_delta: Optional[Callable[[str, str, bytes], None]] = None
    on_audio_done: Optional[Callable[[str, str], None]] = None
    on_transcript: Optional[Callable[[str, str], None]] = None      # role, text
    on_response_done: Optional[Callable[[str, str, dict], None]] = None
    on_error: Optional[Callable[[dict], None]] = None


async def _default_connect(url: str, headers: Dict[str, str]):
    import websockets
    try:
        return await websockets.connect(url, additional_headers=headers,
                                        max_size=1 << 24)
    except TypeError:  # websockets < 14 used extra_headers
        return await websockets.connect(url, extra_headers=headers,
                                        max_size=1 << 24)


class RealtimeVoiceSession(_Callbacks):
    def __init__(self, *, model: str, voice: str, vad: str,
                 instructions_provider: Callable[[], str],
                 api_key: Optional[str], url: Optional[str] = None,
                 connect: Optional[Callable[..., Awaitable[Any]]] = None,
                 create_task: Optional[Callable] = None,
                 max_reconnects: int = 5):
        self.model, self.voice, self.vad = model, voice, vad
        self.instructions_provider = instructions_provider
        self.api_key, self.url = api_key, url
        self._connect = connect or _default_connect
        self._create_task = create_task or asyncio.create_task
        self.max_reconnects = max_reconnects
        self.ws = None
        self.connected = False
        self.active_response: Optional[str] = None
        self.item_of_response: Dict[str, str] = {}
        self.reconnects = 0
        self.sent: list = []               # last N outbound types (diagnostics)
        self._mic_q: asyncio.Queue = asyncio.Queue()
        self._supervisor: Optional[asyncio.Task] = None
        self._stopping = False

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._stopping = False
        self._supervisor = self._create_task(self._supervise())

    async def stop(self) -> None:
        self._stopping = True
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
        if self._supervisor is not None:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except (asyncio.CancelledError, Exception):
                pass
            self._supervisor = None

    async def _supervise(self) -> None:
        attempts = 0
        while not self._stopping:
            try:
                await self._run_once()
                if self._stopping:
                    return
                attempts += 1
                reason = "closed"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                attempts += 1
                reason = repr(e)
            self.connected = False
            self.ws = None
            self.active_response = None
            self.reconnects += 1
            logger.warning(f"Realtime session dropped ({reason}); "
                           f"attempt {attempts}/{self.max_reconnects}")
            if self.on_disconnected:
                self.on_disconnected(reason)
            if attempts > self.max_reconnects or self._stopping:
                logger.error("Realtime session: giving up reconnecting")
                return
            await asyncio.sleep(min(2 ** attempts, 8))

    async def _run_once(self) -> None:
        url = self.url or f"{DEFAULT_URL}?model={self.model}"
        headers = {}
        if not self.url:
            if not self.api_key:
                raise RuntimeError("OPENAI_API_KEY not set")
            headers["Authorization"] = f"Bearer {self.api_key}"
        ws = await self._connect(url, headers)
        self.ws = ws
        try:
            while not self._mic_q.empty():      # drop stale mic backlog
                self._mic_q.get_nowait()
            await self._send(self._session_update())
            self.connected = True
            logger.info(f"Realtime session connected: model={self.model} "
                        f"voice={self.voice} vad={self.vad}")
            if self.on_connected:
                self.on_connected()
            pump = self._create_task(self._pump_mic())
            try:
                while not self._stopping:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if raw is None:
                        return
                    ev = json.loads(raw)
                    if not isinstance(ev, dict):
                        return          # close() sentinel / malformed frame
                    await self._handle(ev)
            finally:
                pump.cancel()
        finally:
            self.connected = False
            try:
                await ws.close()
            except Exception:
                pass

    def _session_update(self) -> dict:
        return {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": self.model,
                "output_modalities": ["audio"],
                "instructions": self.instructions_provider(),
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                        "transcription": {"model": "gpt-4o-mini-transcribe"},
                        "turn_detection": {
                            "type": self.vad,
                            "create_response": False,     # attention layer
                            "interrupt_response": False,  # is authoritative
                        },
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                        "voice": self.voice,
                    },
                },
            },
        }

    # ------------------------------------------------------------- outbound
    async def _send(self, obj: dict) -> bool:
        if self.ws is None:
            return False
        try:
            await self.ws.send(json.dumps(obj))
            self.sent.append(obj.get("type"))
            if len(self.sent) > 200:
                del self.sent[:100]
            return True
        except Exception as e:
            logger.warning(f"Realtime send failed ({obj.get('type')}): {e!r}")
            return False

    def send_audio(self, pcm: bytes) -> None:
        """Called on the loop with mic PCM (only while armed, by design)."""
        if self.connected:
            self._mic_q.put_nowait(pcm)

    async def _pump_mic(self) -> None:
        while True:
            pcm = await self._mic_q.get()
            await self._send({"type": "input_audio_buffer.append",
                              "audio": base64.b64encode(pcm).decode("ascii")})

    async def authorize(self) -> bool:
        """Exactly one response.create per AUTHORIZE_RESPONSE decision."""
        return await self._send({"type": "response.create"})

    async def cancel_response(self, response_id: Optional[str] = None) -> bool:
        rid = response_id or self.active_response
        if not rid:
            return False
        return await self._send({"type": "response.cancel", "response_id": rid})

    async def truncate(self, item_id: str, played_ms: float) -> bool:
        """Server drops unheard audio AND its transcript (doc §9). We never
        send more than we actually played."""
        return await self._send({"type": "conversation.item.truncate",
                                 "item_id": item_id, "content_index": 0,
                                 "audio_end_ms": int(max(0.0, played_ms))})

    async def inject_text(self, text: str) -> bool:
        """Attributed text item (chat injection, doc §10); never a response."""
        return await self._send({
            "type": "conversation.item.create",
            "item": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": text}]}})

    # -------------------------------------------------------------- inbound
    async def _handle(self, ev: dict) -> None:
        t = ev.get("type", "?")
        if t == "input_audio_buffer.speech_started":
            if self.on_speech_started:
                self.on_speech_started(ev.get("audio_start_ms"))
        elif t == "input_audio_buffer.speech_stopped":
            if self.on_speech_stopped:
                self.on_speech_stopped(ev.get("audio_end_ms"))
        elif t == "response.created":
            self.active_response = (ev.get("response") or {}).get("id")
        elif t in ("response.output_audio.delta", "response.audio.delta"):
            rid, item_id = ev.get("response_id"), ev.get("item_id")
            if rid and item_id:
                self.item_of_response[rid] = item_id
            pcm = base64.b64decode(ev.get("delta", "") or "")
            if pcm and self.on_audio_delta:
                self.on_audio_delta(item_id, rid, pcm)
        elif t == "response.output_audio.done":
            if self.on_audio_done:
                self.on_audio_done(ev.get("item_id"), ev.get("response_id"))
        elif t == "response.output_audio_transcript.done":
            if self.on_transcript:
                self.on_transcript("arc", ev.get("transcript") or "")
        elif t == "conversation.item.input_audio_transcription.completed":
            if self.on_transcript:
                self.on_transcript("streamer", ev.get("transcript") or "")
        elif t == "response.done":
            resp = ev.get("response") or {}
            rid = resp.get("id")
            if self.active_response == rid:
                self.active_response = None
            if self.on_response_done:
                self.on_response_done(rid, resp.get("status"),
                                      resp.get("usage") or {})
        elif t == "error":
            err = ev.get("error") or {}
            # response.cancel racing a finished response is benign (doc §9)
            if "no active response" in str(err.get("message", "")).lower():
                logger.debug(f"Realtime benign error: {err}")
            else:
                logger.error(f"Realtime API error: {err}")
            if self.on_error:
                self.on_error(err)
        elif t in ("session.created", "session.updated", "rate_limits.updated"):
            logger.debug(f"Realtime {t}")
        elif t in BENIGN_EVENTS:
            pass
        else:
            logger.debug(f"Realtime unhandled event: {t}")

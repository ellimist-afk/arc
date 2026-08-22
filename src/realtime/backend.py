"""RealtimeVoiceBackend: the VOICE_BACKEND=realtime glue (doc §3, §8, §9).

Turns provider/audio events into Stimuli, drives the AttentionRouter (the
ONLY decision-maker), and executes its Actions in order. It never decides
anything itself -- every response.create, pause, stop, cancel and truncate
traces back to a logged AttentionDecision with a reason.

Wake detection in PASSIVE reuses the legacy Whisper path (U3, resolved by
reuse): the bot feeds every legacy transcript to on_legacy_transcript(); a
trigger_match hit becomes WAKE_PHRASE_DETECTED, the router opens the window,
and the mic pre-roll (which contains the wake utterance) is flushed into the
Realtime session as one natural turn. Passive audio never leaves the machine.
"""
import logging
import time
import uuid
from typing import Callable, Optional

from attention.router import AttentionRouter
from attention.stimulus import (Action, Actor, AttentionDecision, ConvState,
                                Source, Stimulus, StimulusType, Trust)
from components.voice.trigger_match import match_hey_trigger
from realtime.audio_router import AudioRouter
from realtime.session import RealtimeVoiceSession

logger = logging.getLogger(__name__)


class RealtimeVoiceBackend:
    def __init__(self, *, audio: AudioRouter, session: RealtimeVoiceSession,
                 router: AttentionRouter, streamer_username: str = "",
                 channel: str = "", clock: Callable[[], float] = time.monotonic,
                 poll_interval_s: float = 0.1, create_task=None,
                 on_decision: Optional[Callable[[AttentionDecision], None]] = None,
                 on_release_audio: Optional[Callable[[], None]] = None):
        self.audio, self.session, self.router = audio, session, router
        self.clock, self.poll_interval_s = clock, poll_interval_s
        self._create_task = create_task
        self.on_decision, self.on_release_audio = on_decision, on_release_audio
        self.channel = channel
        self.streamer = Actor(user_id=streamer_username, username=streamer_username,
                              roles=("streamer",))
        self._loop = None                  # set in start()
        self.decisions: list = []          # recent decisions (diagnostics)
        self.authorized_count = 0
        self._poll_task = None
        self._running = False

        # audio -> backend
        audio.mic.on_chunk = self._on_mic_chunk
        audio.player.on_item_started = self._on_item_started
        audio.player.on_item_finished = self._on_item_finished
        audio.player.on_device_error = lambda err: logger.error(
            f"Realtime output device error: {err}")
        # session -> backend
        session.on_speech_started = self._on_speech_started
        session.on_speech_stopped = self._on_speech_stopped
        session.on_audio_delta = self._on_audio_delta
        session.on_audio_done = self._on_audio_done
        session.on_transcript = self._on_transcript
        session.on_disconnected = self._on_disconnected

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        import asyncio
        self._running = True
        self._loop = asyncio.get_running_loop()
        # audio threads marshal their callbacks onto this loop
        self.audio.mic.loop = self._loop
        self.audio.player.loop = self._loop
        self.audio.start()
        await self.session.start()
        if self._create_task is not None:
            self._poll_task = self._create_task(self._poll_loop())
        logger.info("RealtimeVoiceBackend started (state PASSIVE, mic local-only)")

    async def stop(self) -> None:
        self._running = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        await self.session.stop()
        self.audio.shutdown()

    @property
    def state(self) -> ConvState:
        return self.router.state

    async def _poll_loop(self) -> None:
        import asyncio
        while self._running:
            await asyncio.sleep(self.poll_interval_s)
            await self.poll()

    async def poll(self, now: Optional[float] = None) -> None:
        """Timer-driven transitions (grace expiry, window expiry)."""
        now = self.clock() if now is None else now
        for d in self.router.poll(now):
            await self._execute(d)

    # ------------------------------------------------------------- stimuli
    def _stim(self, type_: StimulusType, source: Source, actor: Actor,
              trust: Trust, payload: Optional[dict] = None,
              now: Optional[float] = None) -> Stimulus:
        return Stimulus(id=uuid.uuid4().hex[:12], type=type_, source=source,
                        actor=actor, ts=self.clock() if now is None else now,
                        channel=self.channel, trust=trust, payload=payload or {})

    def _voice(self, type_: StimulusType, payload: Optional[dict] = None) -> Stimulus:
        return self._stim(type_, Source.STREAMER_VOICE, self.streamer,
                          Trust.TRUSTED, payload)

    async def handle(self, stim: Stimulus) -> AttentionDecision:
        decision = self.router.handle(stim, stim.ts)
        await self._execute(decision)
        return decision

    async def on_legacy_transcript(self, text: str) -> Optional[AttentionDecision]:
        """Legacy Whisper transcript while PASSIVE: wake-phrase detection.
        While a window is open the Realtime session owns the turn, so legacy
        transcripts are ignored (logged at debug)."""
        if self.router.state is not ConvState.PASSIVE:
            logger.debug(f"[ATTENTION] legacy transcript ignored (window open): {text!r}")
            return None
        matched, how = match_hey_trigger(text.lower())
        if not matched:
            return None
        logger.info(f"[ATTENTION] wake phrase via {how} match: {text!r}")
        return await self.handle(self._voice(
            StimulusType.WAKE_PHRASE_DETECTED, {"phrase": text, "how": how}))

    async def manual_arm(self) -> AttentionDecision:
        return await self.handle(self._stim(StimulusType.MANUAL_ARM,
                                            Source.INTERNAL, self.streamer,
                                            Trust.TRUSTED))

    async def manual_disarm(self) -> AttentionDecision:
        return await self.handle(self._stim(StimulusType.MANUAL_DISARM,
                                            Source.INTERNAL, self.streamer,
                                            Trust.TRUSTED))

    # --- session callbacks (run on the loop) ---
    def _on_speech_started(self, audio_start_ms) -> None:
        current = self.audio.player.current_item
        payload = {"audio_start_ms": audio_start_ms}
        if current is not None:
            # authoritative played-ms, captured BEFORE the pause action runs
            payload["played_ms"] = self.audio.player.played_ms.get(current, 0.0)
            payload["item_id"] = current
        self._schedule(self.handle(self._voice(StimulusType.SPEECH_STARTED, payload)))

    def _on_speech_stopped(self, audio_end_ms) -> None:
        self._schedule(self.handle(self._voice(
            StimulusType.SPEECH_ENDED, {"audio_end_ms": audio_end_ms})))

    def _on_audio_delta(self, item_id: str, response_id: str, pcm: bytes) -> None:
        self.audio.player.enqueue(item_id, pcm)

    def _on_audio_done(self, item_id: str, response_id: str) -> None:
        self.audio.player.mark_done(item_id)

    def _on_transcript(self, role: str, text: str) -> None:
        logger.info(f"[REALTIME] {role}: {text}")

    def _on_disconnected(self, reason: str) -> None:
        self._schedule(self.handle(self._stim(
            StimulusType.PROVIDER_DISCONNECTED, Source.INTERNAL, self.streamer,
            Trust.TRUSTED, {"reason": reason})))

    # --- audio callbacks (run on the loop) ---
    def _on_mic_chunk(self, pcm: bytes) -> None:
        self.session.send_audio(pcm)

    def _on_item_started(self, item_id: str) -> None:
        self._schedule(self.handle(self._voice(
            StimulusType.ARC_SPEECH_STARTED, {"item_id": item_id})))

    def _on_item_finished(self, item_id: str, played_ms: float) -> None:
        self._schedule(self.handle(self._voice(
            StimulusType.ARC_SPEECH_ENDED, {"item_id": item_id,
                                            "played_ms": played_ms})))

    def _schedule(self, coro) -> None:
        """Schedule a coroutine on the backend's loop.

        Audio callbacks may arrive on the player/mic threads when the
        AudioRouter was built without a loop, and asyncio.ensure_future needs
        a running loop in the CALLING thread. Marshal explicitly instead.
        """
        import asyncio
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                (self._create_task or asyncio.ensure_future)(coro)
            else:
                asyncio.run_coroutine_threadsafe(coro, loop)
            return
        if self._create_task is not None:
            self._create_task(coro)
        else:
            asyncio.ensure_future(coro)

    # ------------------------------------------------------------ executor
    async def _execute(self, d: AttentionDecision) -> None:
        self.decisions.append(d)
        if len(self.decisions) > 500:
            del self.decisions[:250]
        logger.info(f"[ATTENTION] {d.rule} {d.state_before.value}->"
                    f"{d.state_after.value}: {d.reason}"
                    + (f" actions={[a.value for a in d.actions]}" if d.actions else ""))
        if self.on_decision:
            self.on_decision(d)
        player, session = self.audio.player, self.session
        for action in d.actions:
            if action is Action.OPEN_WINDOW:
                flushed = self.audio.mic.arm()
                logger.info(f"[REALTIME] mic armed; pre-roll flushed {flushed:.0f} ms")
            elif action is Action.CLOSE_WINDOW:
                self.audio.mic.disarm()
                logger.info("[REALTIME] mic passive (local-only)")
            elif action is Action.AUTHORIZE_RESPONSE:
                self.authorized_count += 1
                await session.authorize()
            elif action is Action.PAUSE_PLAYBACK:
                player.pause()
            elif action is Action.RESUME_PLAYBACK:
                player.resume()
            elif action is Action.STOP_PLAYBACK:
                player.hard_stop()
            elif action is Action.CANCEL_RESPONSE:
                await session.cancel_response()
            elif action is Action.TRUNCATE_AT_PLAYED_MS:
                if d.truncate_item_id is not None:
                    await session.truncate(d.truncate_item_id,
                                           d.truncate_played_ms or 0.0)
            elif action is Action.RELEASE_AUDIO_OWNERSHIP:
                if self.on_release_audio:
                    self.on_release_audio()
            # EXTEND_WINDOW / DEFER_TO_TURN_BOUNDARY / CLEAR_PENDING: router-internal
        for released in d.released:
            await self._execute(released)

"""AttentionRouter v1 — the only component that decides whether anything
responds (redesign doc §5, §8, §10).

Pure and deterministic: no clock reads, no I/O, no external SDK types.
The runtime drives it with two calls:

    decision  = router.handle(stimulus, now)   # one decision per stimulus
    decisions = router.poll(now)               # timer expiries + deferred work

Contract: `now` is injected (same monotonic clock as Stimulus.ts); call
poll() after every handle() and on a periodic tick; every stimulus yields
exactly one decision (even IGNOREs), so behavior is greppable and replayable.
"""
from typing import Dict, List, Optional, Tuple

from attention import chat_rules
from attention.config import AttentionConfig
from attention.guards import StimulusHygiene
from attention.stimulus import (Action, AttentionDecision, ConvState,
                                Disposition, Stimulus, StimulusType)

PRI_STREAMER = 100                     # chat/platform tiers: see chat_rules
PRI_CONTEXT = chat_rules.PRI_CONTEXT


class AttentionRouter:
    def __init__(self, config: AttentionConfig):
        self.cfg = config.normalized()
        self.state = ConvState.PASSIVE
        # absolute deadlines on the injected clock; None = unarmed
        self.window_deadline: Optional[float] = None
        self.grace_deadline: Optional[float] = None
        # interruption bookkeeping
        self._speech_open = False            # a streamer turn is in progress
        self._pending_interrupt_stim: Optional[str] = None
        self._current_item_id: Optional[str] = None   # Arc item now playing
        self._frozen_played_ms: Optional[float] = None  # frozen at pause
        self._hygiene = StimulusHygiene(self.cfg)   # R0.*, R7 guards
        self._mention_last: Dict[str, float] = {}   # cooldowns: user -> ts
        self._last_chat_voice: Optional[float] = None
        # deferred respond decisions awaiting a turn boundary
        self._deferred: List[AttentionDecision] = []

    # ------------------------------------------------------------------ api

    def handle(self, stim: Stimulus, now: float) -> AttentionDecision:
        dropped = self._hygiene.check(stim, now)
        if dropped is not None:
            rule, reason = dropped
            return self._decision(stim.id, Disposition.IGNORE, rule, reason,
                                  self.state, now)
        handler = {
            StimulusType.WAKE_PHRASE_DETECTED: self._on_wake,
            StimulusType.SPEECH_STARTED: self._on_speech_started,
            StimulusType.SPEECH_ENDED: self._on_speech_ended,
            StimulusType.ARC_SPEECH_STARTED: self._on_arc_started,
            StimulusType.ARC_SPEECH_ENDED: self._on_arc_ended,
            StimulusType.CHAT_MESSAGE: self._chat(chat_rules.decide_chat),
            StimulusType.CHAT_MENTION: self._chat(chat_rules.decide_mention),
            StimulusType.CHANNEL_POINT_REDEMPTION:
                self._chat(chat_rules.decide_redemption),
            StimulusType.PLATFORM_EVENT: self._chat(chat_rules.decide_platform),
            StimulusType.MANUAL_ARM: self._on_manual_arm,
            StimulusType.MANUAL_DISARM: self._on_reset_like,
            StimulusType.PROVIDER_DISCONNECTED: self._on_reset_like,
            StimulusType.RESET: self._on_reset_like,
        }[stim.type]
        return handler(stim, now)

    def poll(self, now: float) -> List[AttentionDecision]:
        """Timer-driven transitions; deterministic given (state, now)."""
        out: List[AttentionDecision] = []
        # 1. grace expiry → the interruption is real: stop/cancel/truncate
        if (self.state is ConvState.INTERRUPT_PENDING
                and self.grace_deadline is not None
                and now >= self.grace_deadline):
            before = self.state
            self.state = ConvState.LISTENING
            self.grace_deadline = None
            self._extend_window(now)
            out.append(self._decision(
                self._pending_interrupt_stim or "timer:grace",
                Disposition.IGNORE, "R-int.commit",
                "barge-in grace expired with speech continuing; discard the "
                f"paused audio, cancel the response, truncate item "
                f"{self._current_item_id!r} at {self._frozen_played_ms} ms "
                "(value frozen at pause)",
                before, now, priority=PRI_STREAMER,
                actions=(Action.STOP_PLAYBACK, Action.CANCEL_RESPONSE,
                         Action.TRUNCATE_AT_PLAYED_MS),
                truncate_item_id=self._current_item_id,
                truncate_played_ms=self._frozen_played_ms))
            self._pending_interrupt_stim = None
            self._frozen_played_ms = None
        # 2. safety net only: releases normally ride the boundary decision
        #    itself; this drain covers a boundary event lost downstream.
        if self.state is ConvState.LISTENING and not self._speech_open:
            out.extend(self._release_deferred(now))
        # 3. window expiry → PASSIVE (never while Arc or the streamer speaks)
        if (self.state is ConvState.LISTENING
                and not self._speech_open
                and self.window_deadline is not None
                and now >= self.window_deadline):
            before = self.state
            self._close_window()
            out.append(self._decision(
                "timer:window_expiry", Disposition.IGNORE, "R-win.expiry",
                f"conversation window idle past {self.cfg.window_s:.0f}s; "
                "returning to passive", before, now,
                actions=(Action.CLOSE_WINDOW,)))
        return out

    # ------------------------------------------------------------- handlers

    def _on_wake(self, stim: Stimulus, now: float) -> AttentionDecision:
        before = self.state
        phrase = stim.payload.get("phrase", "")
        if self.state is ConvState.PASSIVE:
            self.state = ConvState.LISTENING
            self._extend_window(now)
            return self._decision(
                stim.id, Disposition.IGNORE, "R2.wake_open",
                f"wake phrase {phrase!r}; conversation window opened "
                f"({self.cfg.window_s:.0f}s)", before, now,
                priority=PRI_STREAMER, actions=(Action.OPEN_WINDOW,))
        self._extend_window(now)
        return self._decision(
            stim.id, Disposition.IGNORE, "R2.wake_extend",
            f"wake phrase {phrase!r} during open window; extended",
            before, now, priority=PRI_STREAMER, actions=(Action.EXTEND_WINDOW,))

    def _on_speech_started(self, stim: Stimulus, now: float) -> AttentionDecision:
        before = self.state
        self._speech_open = True
        if self.state is ConvState.ARC_SPEAKING:
            self.state = ConvState.INTERRUPT_PENDING
            self.grace_deadline = now + self.cfg.grace_s
            self._pending_interrupt_stim = stim.id
            # freeze the truncation target now; immutable until grace resolves
            self._frozen_played_ms = stim.payload.get("played_ms")
            return self._decision(
                stim.id, Disposition.IGNORE, "R-int.grace_start",
                f"speech over Arc; playback paused for "
                f"{self.cfg.grace_s * 1000:.0f}ms grace window (Arc never "
                "talks over the streamer while we decide)", before, now,
                priority=PRI_STREAMER, actions=(Action.PAUSE_PLAYBACK,))
        if self.state is ConvState.LISTENING:
            self._extend_window(now)
            return self._decision(stim.id, Disposition.IGNORE, "R1.turn_open",
                                  "streamer turn opened", before, now,
                                  actions=(Action.EXTEND_WINDOW,))
        # PASSIVE / INTERRUPT_PENDING: bookkeeping only
        return self._decision(stim.id, Disposition.IGNORE, "R0.passive_speech",
                              "speech onset outside a conversation window",
                              before, now)

    def _on_speech_ended(self, stim: Stimulus, now: float) -> AttentionDecision:
        before = self.state
        self._speech_open = False
        if self.state is ConvState.INTERRUPT_PENDING:
            # speech ended while grace was still running → cough/false start
            self.state = ConvState.ARC_SPEAKING
            self.grace_deadline = None
            self._pending_interrupt_stim = None
            self._frozen_played_ms = None      # nothing was truncated
            return self._decision(
                stim.id, Disposition.IGNORE, "R-int.false_start",
                "speech ended within grace window; resuming paused playback",
                before, now,
                actions=(Action.RESUME_PLAYBACK, Action.CLEAR_PENDING))
        if self.state is ConvState.LISTENING:
            self._extend_window(now)
            return self._decision(
                stim.id, Disposition.RESPOND_VOICE, "R1.streamer_turn",
                "completed streamer turn in open conversation; respond",
                before, now, priority=PRI_STREAMER,
                actions=(Action.EXTEND_WINDOW, Action.AUTHORIZE_RESPONSE),
                released=self._release_deferred(now))
        if self.state is ConvState.PASSIVE and self.cfg.passive_speech_context:
            return self._decision(stim.id, Disposition.CONTEXT_ONLY,
                                  "R9.passive_context",
                                  "passive speech kept as context only",
                                  before, now, priority=PRI_CONTEXT)
        return self._decision(stim.id, Disposition.IGNORE, "R0.passive_speech",
                              "speech outside a conversation window; no wake "
                              "phrase — not addressed to the assistant",
                              before, now)

    def _on_arc_started(self, stim: Stimulus, now: float) -> AttentionDecision:
        before = self.state
        if self.state in (ConvState.LISTENING, ConvState.PASSIVE):
            self.state = ConvState.ARC_SPEAKING
        self._current_item_id = stim.payload.get("item_id")
        return self._decision(stim.id, Disposition.IGNORE, "R-arc.start",
                              "arc playback started", before, now)

    def _on_arc_ended(self, stim: Stimulus, now: float) -> AttentionDecision:
        before = self.state
        if self.state in (ConvState.ARC_SPEAKING, ConvState.INTERRUPT_PENDING):
            self.state = ConvState.LISTENING
            self.grace_deadline = None
            self._pending_interrupt_stim = None
            self._frozen_played_ms = None
            self._extend_window(now)
        self._current_item_id = None
        return self._decision(stim.id, Disposition.IGNORE, "R-arc.end",
                              "arc playback finished; window extended",
                              before, now, actions=(Action.EXTEND_WINDOW,),
                              released=self._release_deferred(now))

    def _chat(self, fn):
        """Bind a chat_rules policy function as a handler."""
        return lambda stim, now: fn(self, stim, now)

    def _on_manual_arm(self, stim: Stimulus, now: float) -> AttentionDecision:
        before = self.state
        if self.state is ConvState.PASSIVE:
            self.state = ConvState.LISTENING
        self._extend_window(now)
        return self._decision(stim.id, Disposition.IGNORE, "R-ctl.arm",
                              "manual arm; window opened/extended", before, now,
                              actions=(Action.OPEN_WINDOW,))

    def _on_reset_like(self, stim: Stimulus, now: float) -> AttentionDecision:
        before = self.state
        rule = {"manual_disarm": "R-ctl.disarm",
                "provider_disconnected": "R-ctl.disconnect",
                "reset": "R-ctl.reset"}[stim.type.value]
        interrupted_playback = before in (ConvState.ARC_SPEAKING,
                                          ConvState.INTERRUPT_PENDING)
        self._close_window()
        self._speech_open = False
        self._deferred.clear()
        if stim.type is StimulusType.PROVIDER_DISCONNECTED:
            # Realtime failure: Arc's buffered speech must NOT finish — stop
            # and clear playback and hand the output device back to the
            # legacy announcement pipeline immediately (redesign §12).
            actions = (Action.STOP_PLAYBACK, Action.RELEASE_AUDIO_OWNERSHIP,
                       Action.CLOSE_WINDOW, Action.CLEAR_PENDING)
            reason = ("voice backend disconnected: playback stopped and "
                      "cleared, audio ownership released to the legacy "
                      "pipeline, back to passive")
        else:
            actions = (Action.CLOSE_WINDOW, Action.CLEAR_PENDING)
            if interrupted_playback:
                actions = (Action.STOP_PLAYBACK,) + actions
            reason = (f"{stim.type.value}: window closed, pending state "
                      "cleared, back to passive")
        return self._decision(stim.id, Disposition.IGNORE, rule, reason,
                              before, now, actions=actions)

    # -------------------------------------------------------------- helpers

    def _release_deferred(self, now: float) -> Tuple[AttentionDecision, ...]:
        """Turn-boundary release: fresh items re-emerge with
        AUTHORIZE_RESPONSE, stale ones drop with a logged reason."""
        if not self._deferred:
            return ()
        out = []
        for d in self._deferred:
            if now - d.ts > self.cfg.stale_after_s:
                out.append(self._decision(
                    d.stimulus_id, Disposition.IGNORE, "R-defer.stale",
                    f"deferred response went stale after {now - d.ts:.1f}s; "
                    "dropped", self.state, now))
            else:
                out.append(self._decision(
                    d.stimulus_id, d.disposition, d.rule,
                    d.reason + " (released at turn boundary)",
                    self.state, now, priority=d.priority,
                    actions=(Action.AUTHORIZE_RESPONSE,),
                    cooldowns=d.cooldowns_touched))
        self._deferred.clear()
        return tuple(out)

    def _extend_window(self, now: float) -> None:
        self.window_deadline = now + self.cfg.window_s

    def _close_window(self) -> None:
        self.state = ConvState.PASSIVE
        self.window_deadline = None
        self.grace_deadline = None
        self._pending_interrupt_stim = None
        self._frozen_played_ms = None
        self._current_item_id = None

    def _decision(self, stim_id: str, disposition: Disposition, rule: str,
                  reason: str, before: ConvState, now: float, priority: int = 0,
                  actions: Tuple[Action, ...] = (),
                  cooldowns: Tuple[str, ...] = (),
                  released: Tuple[AttentionDecision, ...] = (),
                  truncate_item_id: Optional[str] = None,
                  truncate_played_ms: Optional[float] = None) -> AttentionDecision:
        return AttentionDecision(
            stimulus_id=stim_id, disposition=disposition, reason=reason,
            rule=rule, state_before=before, state_after=self.state, ts=now,
            priority=priority, actions=actions, cooldowns_touched=cooldowns,
            released=released, truncate_item_id=truncate_item_id,
            truncate_played_ms=truncate_played_ms)

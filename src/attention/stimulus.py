"""Typed contracts for the AttentionRouter (redesign doc §6).

Provider-neutral domain events only: nothing here knows about OpenAI event
shapes, Twitch IRC tags, or PyAudio. Adapters translate external events into
Stimulus instances; the router turns them into AttentionDecisions.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class StimulusType(Enum):
    # streamer voice (from the voice backend adapter, provider-neutral)
    WAKE_PHRASE_DETECTED = "wake_phrase_detected"   # matched configured phrase
    SPEECH_STARTED = "speech_started"               # streamer voice onset
    SPEECH_ENDED = "speech_ended"                   # completed streamer turn
    # Arc's own playback lifecycle (from the audio layer adapter)
    ARC_SPEECH_STARTED = "arc_speech_started"
    ARC_SPEECH_ENDED = "arc_speech_ended"
    # Twitch (from chat/EventSub adapters)
    CHAT_MESSAGE = "chat_message"
    CHAT_MENTION = "chat_mention"                   # @bot or configured alias
    CHANNEL_POINT_REDEMPTION = "channel_point_redemption"
    PLATFORM_EVENT = "platform_event"               # follow/sub/gift/cheer/raid/ad
    # control plane (dashboard / runtime)
    MANUAL_ARM = "manual_arm"
    MANUAL_DISARM = "manual_disarm"
    PROVIDER_DISCONNECTED = "provider_disconnected" # voice backend dropped
    RESET = "reset"


class Source(Enum):
    STREAMER_VOICE = "streamer_voice"
    VIEWER_CHAT = "viewer_chat"
    PLATFORM_EVENT = "platform_event"
    INTERNAL = "internal"


class Trust(Enum):
    TRUSTED = "trusted"       # streamer voice, system, dashboard
    UNTRUSTED = "untrusted"   # all viewer-originated text (incl. usernames)


@dataclass(frozen=True)
class Actor:
    user_id: str = ""
    username: str = ""
    roles: Tuple[str, ...] = ()   # e.g. ("streamer",) ("mod",) ("viewer",)

    def has_role(self, role: str) -> bool:
        return role in self.roles


SYSTEM_ACTOR = Actor(user_id="system", username="system", roles=("system",))


@dataclass(frozen=True)
class Stimulus:
    id: str                       # unique per event; duplicates are dropped
    type: StimulusType
    source: Source
    actor: Actor
    ts: float                     # seconds; same clock the router is driven with
    channel: str = ""
    trust: Trust = Trust.UNTRUSTED
    payload: Dict[str, Any] = field(default_factory=dict)
    # payload keys by type (all optional, all provider-neutral):
    #   SPEECH_STARTED: played_ms — when this onset lands during Arc playback,
    #                   the audio adapter includes the authoritative played-ms
    #                   of the current item, frozen as playback pauses. The
    #                   router carries it through the grace period and emits
    #                   it numerically on the committed-interrupt decision.
    #   SPEECH_ENDED:   text (transcript, if the backend produced one)
    #   CHAT_*:         text
    #   CHANNEL_POINT_REDEMPTION: reward_id, text
    #   PLATFORM_EVENT: kind (follow|sub|gift_sub|cheer|raid|ad_break), amount
    #   ARC_SPEECH_*:   item_id (opaque handle for truncation bookkeeping)


class ConvState(Enum):
    PASSIVE = "passive"                    # window closed; no voice answers
    LISTENING = "listening"                # window open, Arc silent
    ARC_SPEAKING = "arc_speaking"          # Arc audio playing
    INTERRUPT_PENDING = "interrupt_pending"  # speech over Arc; grace running


class Disposition(Enum):
    IGNORE = "ignore"
    CONTEXT_ONLY = "context_only"
    RESPOND_TEXT = "respond_text"
    RESPOND_VOICE = "respond_voice"
    RESPOND_BOTH = "respond_both"
    TOOL_ACTION = "tool_action"            # reserved for v2 (ToolRegistry)
    ANNOUNCE = "announce"                  # legacy TTS queue path


class Action(Enum):
    """Observable side-effect requests. The router never performs them; the
    runtime executes them in order. This is the doc §6 contract plus an
    explicit action tuple so interruption is replayable (see report §5)."""
    OPEN_WINDOW = "open_window"
    EXTEND_WINDOW = "extend_window"
    CLOSE_WINDOW = "close_window"
    AUTHORIZE_RESPONSE = "authorize_response"      # exactly one response.create
    DEFER_TO_TURN_BOUNDARY = "defer_to_turn_boundary"
    PAUSE_PLAYBACK = "pause_playback"              # grace started: Arc goes
                                                   # quiet while we decide;
                                                   # played_ms freezes here
    RESUME_PLAYBACK = "resume_playback"            # false start: unpause
    STOP_PLAYBACK = "stop_playback"                # discard current/paused audio
    CANCEL_RESPONSE = "cancel_response"
    TRUNCATE_AT_PLAYED_MS = "truncate_at_played_ms"
    RELEASE_AUDIO_OWNERSHIP = "release_audio_ownership"  # legacy queue takes
                                                         # the output device
    CLEAR_PENDING = "clear_pending"


@dataclass(frozen=True)
class AttentionDecision:
    stimulus_id: str
    disposition: Disposition
    reason: str                   # human-readable; always log-worthy
    rule: str                     # deciding rule id, e.g. "R3.chat_mention"
    state_before: ConvState
    state_after: ConvState
    ts: float                     # injected 'now' at decision time
    priority: int = 0
    actions: Tuple[Action, ...] = ()
    cooldowns_touched: Tuple[str, ...] = ()
    # Deferred respond-worthy decisions released BY this decision's turn
    # boundary (arc-speech end / streamer-turn end). Carried on the boundary
    # decision itself so release never depends on a later poll() call.
    released: Tuple["AttentionDecision", ...] = ()
    # Set ONLY on a committed-interrupt decision (TRUNCATE_AT_PLAYED_MS in
    # actions): the truncation target, self-contained — the executor must
    # use these values, never re-read live playback state (which may have
    # drifted or been torn down by the time the decision is executed).
    truncate_item_id: Optional[str] = None
    truncate_played_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stimulus_id": self.stimulus_id,
            "disposition": self.disposition.value,
            "reason": self.reason,
            "rule": self.rule,
            "state_before": self.state_before.value,
            "state_after": self.state_after.value,
            "ts": self.ts,
            "priority": self.priority,
            "actions": [a.value for a in self.actions],
            "cooldowns_touched": list(self.cooldowns_touched),
            "released": [d.to_dict() for d in self.released],
            "truncate_item_id": self.truncate_item_id,
            "truncate_played_ms": self.truncate_played_ms,
        }

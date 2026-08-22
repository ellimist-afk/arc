"""AttentionRouter configuration.

Every threshold, phrase, alias, and identity lives here — nothing
installation-specific is hardcoded in the router (redesign doc §14). Values
below are product defaults; a profile (env / bot_settings.json via the
unified config layer, wired at integration time) overrides them.
"""
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class AttentionConfig:
    # identity (empty by default — supplied by the installation profile)
    bot_username: str = ""                       # bot's own chat account
    streamer_username: str = ""                  # the co-host human
    mention_aliases: Tuple[str, ...] = ()        # e.g. ("arc",) — adapter matches
    ignored_users: Tuple[str, ...] = ()          # known bots etc.

    # conversation window (doc §8)
    window_s: float = 45.0                       # configurable 30–60 s
    # barge-in grace (doc §9 / spike finding U5)
    grace_s: float = 0.25

    # cooldowns / rate limits (doc §10 policy table)
    mention_cooldown_s: float = 30.0             # per-viewer, R3
    chat_voice_min_interval_s: float = 10.0      # global gap between
                                                 # chat-triggered voice replies
    # event hygiene
    stale_after_s: float = 90.0                  # older stimuli are dropped
    dedupe_window: int = 256                     # remembered stimulus ids

    # policy toggles
    respond_to_mentions_while_passive: bool = True   # R3 without open window
    redemption_opens_window: bool = True             # R4
    passive_speech_context: bool = False             # store passive speech as
                                                     # context (doc default:
                                                     # passive audio unsent)
    announce_platform_events: bool = True            # R5 → legacy queue

    def normalized(self) -> "AttentionConfig":
        """Lowercase identity fields once so the router never re-normalizes."""
        return AttentionConfig(**{
            **{f.name: getattr(self, f.name) for f in fields(self)},
            "bot_username": self.bot_username.lower(),
            "streamer_username": self.streamer_username.lower(),
            "mention_aliases": tuple(a.lower() for a in self.mention_aliases),
            "ignored_users": tuple(u.lower() for u in self.ignored_users),
        })

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AttentionConfig":
        known = {f.name for f in fields(cls)}
        kwargs = {}
        for k, v in d.items():
            if k not in known:
                continue
            if isinstance(v, list):
                v = tuple(v)
            kwargs[k] = v
        return cls(**kwargs).normalized()

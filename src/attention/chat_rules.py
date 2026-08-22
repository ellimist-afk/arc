"""Twitch-side attention policy (rules R3–R5, R9) for the AttentionRouter.

Split from router.py so the voice state machine and the chat policy each stay
under the 300-line PRD cap. These functions are pure apart from the cooldown
and deferral bookkeeping they perform on the router instance passed in; only
AttentionRouter calls them.
"""
from attention.stimulus import (Action, AttentionDecision, ConvState,
                                Disposition, Stimulus)

PRI_REDEMPTION = 80
PRI_MENTION = 60
PRI_ANNOUNCE = 30
PRI_CONTEXT = 10


def _mid_turn(r, state: ConvState) -> bool:
    """True while somebody holds the floor: Arc speaking/paused-in-grace, or
    the streamer mid-turn. Respond-worthy chat defers to the turn boundary."""
    return (state in (ConvState.ARC_SPEAKING, ConvState.INTERRUPT_PENDING)
            or (state is ConvState.LISTENING and r._speech_open))


def decide_chat(r, stim: Stimulus, now: float) -> AttentionDecision:
    return r._decision(stim.id, Disposition.CONTEXT_ONLY, "R9.chat_context",
                       "ordinary chat; buffered as context only",
                       r.state, now, priority=PRI_CONTEXT)


def decide_mention(r, stim: Stimulus, now: float) -> AttentionDecision:
    before = r.state
    user = stim.actor.username.lower()
    last = r._mention_last.get(user)
    if last is not None and now - last < r.cfg.mention_cooldown_s:
        return r._decision(
            stim.id, Disposition.CONTEXT_ONLY, "R3.viewer_cooldown",
            f"mention from '{user}' within {r.cfg.mention_cooldown_s:.0f}s "
            "per-viewer cooldown; kept as context", before, now,
            priority=PRI_CONTEXT)
    if (r._last_chat_voice is not None
            and now - r._last_chat_voice < r.cfg.chat_voice_min_interval_s):
        r._mention_last[user] = now      # a text reply still counts
        return r._decision(
            stim.id, Disposition.RESPOND_TEXT, "R3.global_cooldown",
            "chat-voice interval active; text-only reply", before, now,
            priority=PRI_MENTION, actions=(Action.AUTHORIZE_RESPONSE,),
            cooldowns=("mention:" + user,))
    if before is ConvState.PASSIVE and not r.cfg.respond_to_mentions_while_passive:
        return r._decision(
            stim.id, Disposition.CONTEXT_ONLY, "R3.passive_muted",
            "mention while passive; passive mentions disabled by config",
            before, now, priority=PRI_CONTEXT)
    r._mention_last[user] = now
    r._last_chat_voice = now
    cooldowns = ("mention:" + user, "chat_voice:global")
    if _mid_turn(r, before):
        who = "Arc is speaking" if before is not ConvState.LISTENING \
            else "the streamer is mid-turn"
        d = r._decision(
            stim.id, Disposition.RESPOND_BOTH, "R3.chat_mention",
            f"direct mention from '{user}' while {who}; deferred to turn "
            "boundary", before, now, priority=PRI_MENTION,
            actions=(Action.DEFER_TO_TURN_BOUNDARY,), cooldowns=cooldowns)
        r._deferred.append(d)
        return d
    if before is ConvState.LISTENING:
        r._extend_window(now)
    return r._decision(
        stim.id, Disposition.RESPOND_BOTH, "R3.chat_mention",
        f"direct mention from '{user}'; respond in voice and chat",
        before, now, priority=PRI_MENTION,
        actions=(Action.AUTHORIZE_RESPONSE,), cooldowns=cooldowns)


def decide_redemption(r, stim: Stimulus, now: float) -> AttentionDecision:
    before = r.state
    actions = (Action.AUTHORIZE_RESPONSE,)
    if r.cfg.redemption_opens_window:
        if r.state is ConvState.PASSIVE:
            r.state = ConvState.LISTENING
            actions = (Action.OPEN_WINDOW, Action.AUTHORIZE_RESPONSE)
        r._extend_window(now)
    if _mid_turn(r, before):
        d = r._decision(
            stim.id, Disposition.RESPOND_VOICE, "R4.redemption",
            "channel-point redemption during an active turn; deferred to "
            "turn boundary", before, now, priority=PRI_REDEMPTION,
            actions=(Action.DEFER_TO_TURN_BOUNDARY,))
        r._deferred.append(d)
        return d
    return r._decision(
        stim.id, Disposition.RESPOND_VOICE, "R4.redemption",
        f"redemption '{stim.payload.get('reward_id', '?')}' guarantees "
        "consideration; respond", before, now,
        priority=PRI_REDEMPTION, actions=actions)


def decide_platform(r, stim: Stimulus, now: float) -> AttentionDecision:
    kind = stim.payload.get("kind", "event")
    if not r.cfg.announce_platform_events:
        return r._decision(stim.id, Disposition.CONTEXT_ONLY, "R5.announce_off",
                           f"{kind} noted as context (announcements off)",
                           r.state, now, priority=PRI_CONTEXT)
    return r._decision(stim.id, Disposition.ANNOUNCE, "R5.platform_event",
                       f"{kind} routed to announcement queue",
                       r.state, now, priority=PRI_ANNOUNCE)

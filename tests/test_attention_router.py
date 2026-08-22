"""AttentionRouter v1: every transition, deterministic, injected time.

No I/O, no clocks, no sleeps — pure (state, stimulus, now) → decision checks.
Covers the scenario list from the isolated-AttentionRouter task plus every
state×event transition in the router's table.
"""
import itertools

from attention.config import AttentionConfig
from attention.router import AttentionRouter
from attention.stimulus import (Action, Actor, ConvState, Disposition, Source,
                                Stimulus, StimulusType, Trust)

_ids = itertools.count()

STREAMER = Actor(user_id="u1", username="TestStreamer", roles=("streamer",))
VIEWER = Actor(user_id="u2", username="some_viewer", roles=("viewer",))
VIEWER2 = Actor(user_id="u3", username="other_viewer", roles=("viewer",))

CFG = AttentionConfig(
    bot_username="test_bot_account",
    streamer_username="teststreamer",
    mention_aliases=("assistant",),
    ignored_users=("spambot",),
    window_s=45.0, grace_s=0.25,
    mention_cooldown_s=30.0, chat_voice_min_interval_s=10.0,
)


def stim(type_, actor=STREAMER, ts=0.0, sid=None, trust=Trust.TRUSTED,
         source=Source.STREAMER_VOICE, **payload):
    return Stimulus(id=sid or f"s{next(_ids)}", type=type_, source=source,
                    actor=actor, ts=ts, trust=trust, payload=payload)


def open_conversation(r, t=0.0):
    d = r.handle(stim(StimulusType.WAKE_PHRASE_DETECTED, ts=t, phrase="hey bot"), t)
    assert r.state is ConvState.LISTENING
    return d


def arc_speaks(r, t):
    return r.handle(stim(StimulusType.ARC_SPEECH_STARTED, ts=t,
                         source=Source.INTERNAL, item_id="item1"), t)


# ---------------------------------------------------------------- scenarios

def test_passive_background_conversation_is_ignored():
    r = AttentionRouter(CFG)
    for t in (1.0, 2.0, 3.0):
        r.handle(stim(StimulusType.SPEECH_STARTED, ts=t - 0.5), t - 0.5)
        d = r.handle(stim(StimulusType.SPEECH_ENDED, ts=t, text="talking to discord"), t)
        assert d.disposition is Disposition.IGNORE
        assert d.rule == "R0.passive_speech"
        assert Action.AUTHORIZE_RESPONSE not in d.actions
    assert r.state is ConvState.PASSIVE
    assert r.poll(100.0) == []  # nothing pending, no timers armed


def test_passive_speech_context_toggle():
    r = AttentionRouter(AttentionConfig(passive_speech_context=True))
    d = r.handle(stim(StimulusType.SPEECH_ENDED, ts=1.0, text="ambient"), 1.0)
    assert d.disposition is Disposition.CONTEXT_ONLY


def test_wake_phrase_opens_window():
    r = AttentionRouter(CFG)
    d = open_conversation(r, 5.0)
    assert d.state_before is ConvState.PASSIVE
    assert d.state_after is ConvState.LISTENING
    assert Action.OPEN_WINDOW in d.actions
    assert d.rule == "R2.wake_open"
    assert r.window_deadline == 5.0 + CFG.window_s


def test_wake_phrase_during_window_extends():
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    d = r.handle(stim(StimulusType.WAKE_PHRASE_DETECTED, ts=10.0, phrase="hey bot"), 10.0)
    assert d.rule == "R2.wake_extend"
    assert r.window_deadline == 10.0 + CFG.window_s


def test_authorized_response_on_turn_end_in_window():
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=1.0), 1.0)
    d = r.handle(stim(StimulusType.SPEECH_ENDED, ts=3.0, text="why am I losing"), 3.0)
    assert d.disposition is Disposition.RESPOND_VOICE
    assert d.rule == "R1.streamer_turn"
    assert Action.AUTHORIZE_RESPONSE in d.actions
    assert d.priority == 100


def test_follow_up_without_wake_phrase():
    """Follow-up turns inside the window never require re-waking."""
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    for t in (2.0, 6.0, 11.0):
        r.handle(stim(StimulusType.SPEECH_STARTED, ts=t - 1), t - 1)
        d = r.handle(stim(StimulusType.SPEECH_ENDED, ts=t), t)
        assert Action.AUTHORIZE_RESPONSE in d.actions
        assert r.window_deadline == t + CFG.window_s  # each turn extends


def test_fast_follow_up_right_after_arc_finishes():
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    r.handle(stim(StimulusType.ARC_SPEECH_ENDED, ts=4.0, source=Source.INTERNAL), 4.0)
    assert r.state is ConvState.LISTENING
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=4.05), 4.05)
    d = r.handle(stim(StimulusType.SPEECH_ENDED, ts=5.0), 5.0)
    assert Action.AUTHORIZE_RESPONSE in d.actions


def test_genuine_interruption_stop_cancel_truncate():
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    d1 = r.handle(stim(StimulusType.SPEECH_STARTED, ts=2.0,
                       played_ms=1234.5), 2.0)
    assert d1.rule == "R-int.grace_start"
    assert d1.actions == (Action.PAUSE_PLAYBACK,)   # Arc goes quiet at once
    assert r.state is ConvState.INTERRUPT_PENDING
    assert r.poll(2.1) == []            # still inside grace: nothing happens
    out = r.poll(2.0 + CFG.grace_s)     # grace expiry commits the interrupt
    assert len(out) == 1
    d2 = out[0]
    assert d2.rule == "R-int.commit"
    assert d2.actions == (Action.STOP_PLAYBACK, Action.CANCEL_RESPONSE,
                          Action.TRUNCATE_AT_PLAYED_MS)
    # the truncation target is numeric and self-contained on the decision —
    # the executor must never re-read live playback state
    assert d2.truncate_played_ms == 1234.5
    assert d2.truncate_item_id == "item1"
    assert r.state is ConvState.LISTENING
    # the interrupting turn then completes and gets a normal response
    d3 = r.handle(stim(StimulusType.SPEECH_ENDED, ts=3.5), 3.5)
    assert Action.AUTHORIZE_RESPONSE in d3.actions


def test_frozen_played_ms_survives_grace_unchanged():
    """The value captured at PAUSE_PLAYBACK is immutable until the grace
    period resolves — later events cannot overwrite it."""
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=2.0, played_ms=800.0), 2.0)
    # a second (duplicate-ish) onset during grace carries a different value;
    # it must not replace the frozen one
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=2.1, played_ms=999.0), 2.1)
    d = r.poll(2.0 + CFG.grace_s)[0]
    assert d.rule == "R-int.commit"
    assert d.truncate_played_ms == 800.0


def test_false_start_clears_frozen_value_next_interrupt_uses_new_one():
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=2.0, played_ms=500.0), 2.0)
    r.handle(stim(StimulusType.SPEECH_ENDED, ts=2.1), 2.1)   # cough: resumed
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=4.0, played_ms=2500.0), 4.0)
    d = r.poll(4.0 + CFG.grace_s)[0]
    assert d.truncate_played_ms == 2500.0     # fresh freeze, not the old 500


def test_commit_without_played_ms_is_explicit_none():
    """If the adapter could not supply played_ms, the decision says so
    honestly (None) rather than pretending a number exists."""
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=2.0), 2.0)  # no played_ms
    d = r.poll(2.0 + CFG.grace_s)[0]
    assert d.truncate_played_ms is None
    assert d.truncate_item_id == "item1"


def test_cough_false_interruption_within_grace():
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    d0 = r.handle(stim(StimulusType.SPEECH_STARTED, ts=2.0), 2.0)
    assert Action.PAUSE_PLAYBACK in d0.actions      # paused during the doubt
    d = r.handle(stim(StimulusType.SPEECH_ENDED, ts=2.15), 2.15)  # 150ms cough
    assert d.rule == "R-int.false_start"
    assert r.state is ConvState.ARC_SPEAKING
    assert Action.RESUME_PLAYBACK in d.actions      # unpause, don't restart
    assert Action.STOP_PLAYBACK not in d.actions
    assert Action.AUTHORIZE_RESPONSE not in d.actions
    assert r.poll(3.0) == []            # no stale grace timer left behind


def test_mention_cooldown_per_viewer():
    r = AttentionRouter(CFG)
    m1 = stim(StimulusType.CHAT_MENTION, actor=VIEWER, ts=0.0,
              trust=Trust.UNTRUSTED, source=Source.VIEWER_CHAT, text="@bot hi")
    d1 = r.handle(m1, 0.0)
    assert d1.disposition is Disposition.RESPOND_BOTH
    assert "mention:some_viewer" in d1.cooldowns_touched
    m2 = stim(StimulusType.CHAT_MENTION, actor=VIEWER, ts=5.0,
              trust=Trust.UNTRUSTED, source=Source.VIEWER_CHAT, text="again")
    d2 = r.handle(m2, 5.0)
    assert d2.rule == "R3.viewer_cooldown"
    assert d2.disposition is Disposition.CONTEXT_ONLY
    m3 = stim(StimulusType.CHAT_MENTION, actor=VIEWER, ts=31.0,
              trust=Trust.UNTRUSTED, source=Source.VIEWER_CHAT, text="later")
    assert r.handle(m3, 31.0).disposition is Disposition.RESPOND_BOTH


def test_global_chat_voice_interval_degrades_to_text():
    r = AttentionRouter(CFG)
    r.handle(stim(StimulusType.CHAT_MENTION, actor=VIEWER, ts=0.0,
                  source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED), 0.0)
    d = r.handle(stim(StimulusType.CHAT_MENTION, actor=VIEWER2, ts=4.0,
                      source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED), 4.0)
    assert d.rule == "R3.global_cooldown"
    assert d.disposition is Disposition.RESPOND_TEXT


def test_mention_while_arc_speaking_released_on_arc_end_boundary():
    """Release rides ON the boundary decision — no poll() required."""
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    d = r.handle(stim(StimulusType.CHAT_MENTION, actor=VIEWER, ts=2.0,
                      source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED), 2.0)
    assert d.actions == (Action.DEFER_TO_TURN_BOUNDARY,)
    assert r.poll(2.5) == []            # not released while Arc speaks
    end = r.handle(stim(StimulusType.ARC_SPEECH_ENDED, ts=6.0,
                        source=Source.INTERNAL), 6.0)
    assert len(end.released) == 1
    rel = end.released[0]
    assert rel.stimulus_id == d.stimulus_id
    assert Action.AUTHORIZE_RESPONSE in rel.actions
    assert r.poll(6.0) == []            # queue emptied; no double release


def test_deferred_during_streamer_turn_released_on_speech_end():
    """Mention lands while the STREAMER is mid-turn → defers, releases on
    the streamer's own turn-end decision."""
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=1.0), 1.0)   # turn open
    d = r.handle(stim(StimulusType.CHAT_MENTION, actor=VIEWER, ts=2.0,
                      source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED), 2.0)
    assert d.actions == (Action.DEFER_TO_TURN_BOUNDARY,)
    end = r.handle(stim(StimulusType.SPEECH_ENDED, ts=3.0), 3.0)
    assert Action.AUTHORIZE_RESPONSE in end.actions            # R1 respond
    assert len(end.released) == 1
    assert end.released[0].stimulus_id == d.stimulus_id
    assert Action.AUTHORIZE_RESPONSE in end.released[0].actions
    assert end.released[0].priority < end.priority             # streamer wins
    assert r.poll(3.5) == []


def test_deferred_mention_goes_stale_on_boundary():
    cfg = AttentionConfig(stale_after_s=30.0)
    r = AttentionRouter(cfg)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    r.handle(stim(StimulusType.CHAT_MENTION, actor=VIEWER, ts=2.0,
                  source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED), 2.0)
    end = r.handle(stim(StimulusType.ARC_SPEECH_ENDED, ts=40.0,
                        source=Source.INTERNAL), 40.0)
    assert end.released[0].rule == "R-defer.stale"
    assert end.released[0].disposition is Disposition.IGNORE


def test_deferred_stale_on_streamer_boundary():
    cfg = AttentionConfig(stale_after_s=30.0)
    r = AttentionRouter(cfg)
    open_conversation(r, 0.0)
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=1.0), 1.0)
    r.handle(stim(StimulusType.CHAT_MENTION, actor=VIEWER, ts=2.0,
                  source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED), 2.0)
    end = r.handle(stim(StimulusType.SPEECH_ENDED, ts=45.0), 45.0)
    assert end.released[0].rule == "R-defer.stale"


def test_lost_arc_end_recovers_through_interrupt_boundary():
    """Playback dies without ARC_SPEECH_ENDED: the streamer talking anyway
    drives interrupt → LISTENING, and the streamer's turn-end decision
    carries the deferred release. No poll-ordering dependence."""
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    d = r.handle(stim(StimulusType.CHAT_MENTION, actor=VIEWER, ts=2.0,
                      source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED), 2.0)
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=3.0), 3.0)
    r.poll(3.0 + CFG.grace_s)                       # interrupt commit
    end = r.handle(stim(StimulusType.SPEECH_ENDED, ts=4.0), 4.0)
    assert any(x.stimulus_id == d.stimulus_id and
               Action.AUTHORIZE_RESPONSE in x.actions for x in end.released)
    assert r.poll(5.0) == []


def test_poll_drain_is_a_true_safety_net():
    """Belt-and-suspenders: if the router is ever LISTENING-and-quiet with a
    deferred item still queued (a boundary decision got lost downstream),
    the next poll() drains it. State forced directly — no event path leaves
    this configuration by design."""
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    d = r.handle(stim(StimulusType.CHAT_MENTION, actor=VIEWER, ts=2.0,
                      source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED), 2.0)
    r.state = ConvState.LISTENING       # simulate lost boundary delivery
    r._speech_open = False
    out = r.poll(3.0)
    assert any(x.stimulus_id == d.stimulus_id and
               Action.AUTHORIZE_RESPONSE in x.actions for x in out)
    assert r.poll(3.5) == []            # drained exactly once


def test_redemption_guaranteed_and_opens_window():
    r = AttentionRouter(CFG)
    d = r.handle(stim(StimulusType.CHANNEL_POINT_REDEMPTION, actor=VIEWER,
                      ts=0.0, source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED,
                      reward_id="ai_reward", text="roast him"), 0.0)
    assert d.disposition is Disposition.RESPOND_VOICE
    assert Action.OPEN_WINDOW in d.actions
    assert r.state is ConvState.LISTENING


def test_manual_arm_and_disarm():
    r = AttentionRouter(CFG)
    d = r.handle(stim(StimulusType.MANUAL_ARM, ts=0.0, source=Source.INTERNAL), 0.0)
    assert r.state is ConvState.LISTENING and Action.OPEN_WINDOW in d.actions
    arc_speaks(r, 1.0)
    d2 = r.handle(stim(StimulusType.MANUAL_DISARM, ts=2.0, source=Source.INTERNAL), 2.0)
    assert r.state is ConvState.PASSIVE
    assert Action.STOP_PLAYBACK in d2.actions      # disarm silences Arc
    assert Action.CLOSE_WINDOW in d2.actions


def test_window_expiry_returns_to_passive():
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    assert r.poll(44.9) == []
    out = r.poll(45.0)
    assert [d.rule for d in out] == ["R-win.expiry"]
    assert r.state is ConvState.PASSIVE
    assert r.poll(46.0) == []           # expiry fires exactly once


def test_window_never_expires_mid_streamer_turn():
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=44.0), 44.0)
    assert r.poll(120.0) == []          # turn still open: no expiry
    d = r.handle(stim(StimulusType.SPEECH_ENDED, ts=121.0), 121.0)
    assert Action.AUTHORIZE_RESPONSE in d.actions   # turn completes normally


def test_window_never_expires_while_arc_speaks():
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    assert r.poll(120.0) == []          # ARC_SPEAKING: expiry deferred
    r.handle(stim(StimulusType.ARC_SPEECH_ENDED, ts=121.0, source=Source.INTERNAL), 121.0)
    assert r.state is ConvState.LISTENING
    assert r.window_deadline == 121.0 + CFG.window_s  # re-extended at arc end


def test_duplicate_stimulus_id_dropped():
    r = AttentionRouter(CFG)
    s = stim(StimulusType.WAKE_PHRASE_DETECTED, ts=0.0, sid="dup1", phrase="x")
    r.handle(s, 0.0)
    d = r.handle(s, 0.5)
    assert d.rule == "R0.duplicate"
    assert d.disposition is Disposition.IGNORE


def test_stale_stimulus_dropped():
    r = AttentionRouter(CFG)
    d = r.handle(stim(StimulusType.CHAT_MENTION, actor=VIEWER, ts=0.0,
                      source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED), 100.0)
    assert d.rule == "R0.stale"


def test_own_and_ignored_users_never_answered():
    r = AttentionRouter(CFG)
    self_msg = stim(StimulusType.CHAT_MENTION,
                    actor=Actor(username="Test_Bot_Account"), ts=0.0,
                    source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED)
    assert r.handle(self_msg, 0.0).rule == "R0.self"
    bot_msg = stim(StimulusType.CHAT_MESSAGE, actor=Actor(username="SpamBot"),
                   ts=0.0, source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED)
    assert r.handle(bot_msg, 0.0).rule == "R7.ignored_user"


def test_ordinary_chat_is_context_only():
    r = AttentionRouter(CFG)
    d = r.handle(stim(StimulusType.CHAT_MESSAGE, actor=VIEWER, ts=0.0,
                      source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED,
                      text="POG"), 0.0)
    assert d.disposition is Disposition.CONTEXT_ONLY
    assert d.rule == "R9.chat_context"


def test_platform_events_announce_and_toggle():
    r = AttentionRouter(CFG)
    d = r.handle(stim(StimulusType.PLATFORM_EVENT, actor=VIEWER, ts=0.0,
                      source=Source.PLATFORM_EVENT, trust=Trust.UNTRUSTED,
                      kind="raid", amount=12), 0.0)
    assert d.disposition is Disposition.ANNOUNCE
    r2 = AttentionRouter(AttentionConfig(announce_platform_events=False))
    d2 = r2.handle(stim(StimulusType.PLATFORM_EVENT, ts=0.0,
                        source=Source.PLATFORM_EVENT, kind="follow"), 0.0)
    assert d2.disposition is Disposition.CONTEXT_ONLY


def test_disconnect_resets_cleanly():
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=2.0), 2.0)  # grace pending
    d = r.handle(stim(StimulusType.PROVIDER_DISCONNECTED, ts=2.1,
                      source=Source.INTERNAL), 2.1)
    assert r.state is ConvState.PASSIVE
    # buffered Arc speech must NOT finish; legacy pipeline takes the device
    assert Action.STOP_PLAYBACK in d.actions
    assert Action.RELEASE_AUDIO_OWNERSHIP in d.actions
    assert Action.CLOSE_WINDOW in d.actions
    assert Action.CLEAR_PENDING in d.actions
    assert r.poll(10.0) == []           # no orphaned grace/window timers
    # a fresh wake works normally after reset
    open_conversation(r, 12.0)
    assert r.state is ConvState.LISTENING


def test_interrupt_pending_cleared_if_arc_finishes_first():
    r = AttentionRouter(CFG)
    open_conversation(r, 0.0)
    arc_speaks(r, 1.0)
    r.handle(stim(StimulusType.SPEECH_STARTED, ts=2.0), 2.0)
    # Arc's clip ends before grace expiry: nothing to interrupt any more
    r.handle(stim(StimulusType.ARC_SPEECH_ENDED, ts=2.1, source=Source.INTERNAL), 2.1)
    assert r.state is ConvState.LISTENING
    assert r.poll(3.0) == []            # grace timer must not fire late


def test_every_decision_carries_reason_rule_and_states():
    r = AttentionRouter(CFG)
    kinds = [
        stim(StimulusType.WAKE_PHRASE_DETECTED, ts=0.0, phrase="x"),
        stim(StimulusType.SPEECH_STARTED, ts=0.1),
        stim(StimulusType.SPEECH_ENDED, ts=0.2),
        stim(StimulusType.CHAT_MESSAGE, actor=VIEWER, ts=0.3,
             source=Source.VIEWER_CHAT, trust=Trust.UNTRUSTED),
        stim(StimulusType.MANUAL_DISARM, ts=0.4, source=Source.INTERNAL),
    ]
    t = 0.0
    for s in kinds:
        t += 0.1
        d = r.handle(s, t)
        assert d.reason and d.rule
        assert d.state_before is not None and d.state_after is not None
        assert d.ts == t


def test_determinism_same_inputs_same_decisions():
    def run():
        global _ids
        r = AttentionRouter(CFG)
        seq = []
        events = [
            (0.0, stim(StimulusType.WAKE_PHRASE_DETECTED, ts=0.0, sid="w1", phrase="x")),
            (1.0, stim(StimulusType.SPEECH_ENDED, ts=1.0, sid="t1")),
            (2.0, stim(StimulusType.ARC_SPEECH_STARTED, ts=2.0, sid="a1",
                       source=Source.INTERNAL)),
            (3.0, stim(StimulusType.SPEECH_STARTED, ts=3.0, sid="i1")),
        ]
        for t, s in events:
            seq.append(r.handle(s, t).to_dict())
            seq.extend(d.to_dict() for d in r.poll(t))
        seq.extend(d.to_dict() for d in r.poll(3.0 + CFG.grace_s))
        return seq
    assert run() == run()

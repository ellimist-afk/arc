"""Replay harness: fixtures in → deterministic decision streams out."""
import json
from pathlib import Path

from attention.config import AttentionConfig
from attention.replay import read_jsonl, replay
from attention.stimulus import Action

FIXTURES = Path(__file__).parent / "fixtures" / "attention"

CFG = AttentionConfig.from_dict({
    "bot_username": "test_bot_account",
    "ignored_users": ["nightbot_clone"],
    "window_s": 45.0, "grace_s": 0.25,
    "mention_cooldown_s": 30.0, "chat_voice_min_interval_s": 10.0,
})


def run_fixture(name, cfg=CFG):
    with open(FIXTURES / name, encoding="utf-8") as fh:
        records = read_jsonl(fh)
    records.sort(key=lambda r: float(r["t"]))
    return list(replay(iter(records), cfg))


def rules(decisions):
    return [d["rule"] for d in decisions]


def test_normal_conversation_fixture():
    out = run_fixture("normal_conversation.jsonl")
    rs = rules(out)
    assert rs[0] == "R2.wake_open"
    # both streamer turns authorized, no interruptions, window expires at end
    assert rs.count("R1.streamer_turn") == 2
    assert "R-int.commit" not in rs
    assert rs[-1] == "R-win.expiry"
    authorized = [d for d in out if "authorize_response" in d["actions"]]
    assert len(authorized) == 2


def test_interruption_fixture():
    out = run_fixture("interruption.jsonl")
    rs = rules(out)
    assert "R-int.grace_start" in rs
    commit = next(d for d in out if d["rule"] == "R-int.commit")
    assert commit["actions"] == ["stop_playback", "cancel_response",
                                 "truncate_at_played_ms"]
    # numeric truncation target frozen at pause, carried on the decision
    assert commit["truncate_played_ms"] == 2800.0
    assert commit["truncate_item_id"] == "item_count"
    # the interrupting turn still gets answered afterwards
    after = out[out.index(commit):]
    assert any(d["rule"] == "R1.streamer_turn" and
               "authorize_response" in d["actions"] for d in after)


def test_false_interruption_fixture():
    out = run_fixture("false_interruption.jsonl")
    rs = rules(out)
    assert "R-int.false_start" in rs
    assert "R-int.commit" not in rs                 # cough never interrupts
    assert not any("stop_playback" in d["actions"] for d in out)


def test_noisy_stream_fixture():
    out = run_fixture("noisy_stream.jsonl")
    by_id = {}
    for d in out:
        by_id.setdefault(d["stimulus_id"], []).append(d)
    assert by_id["p1"][0]["rule"] == "R0.passive_speech"      # passive talk
    assert by_id["c1"][0]["disposition"] == "context_only"    # ordinary chat
    assert by_id["c2"][0]["rule"] == "R7.ignored_user"        # bot filtered
    assert by_id["m1"][0]["disposition"] == "respond_both"    # first mention
    assert by_id["m2"][0]["rule"] == "R3.viewer_cooldown"     # same viewer
    assert by_id["m3"][0]["rule"] == "R3.global_cooldown"     # flood → text
    assert by_id["m1"][1]["rule"] == "R0.duplicate"           # redelivery
    assert by_id["r1"][0]["disposition"] == "announce"        # raid → legacy
    assert by_id["old1"][0]["rule"] == "R0.stale"             # 89s-old event
    # exactly one voice-capable response in the whole noisy window
    voiced = [d for d in out if d["disposition"] in ("respond_both",
                                                     "respond_voice")]
    assert len(voiced) == 1


def test_replay_is_deterministic_and_serializable():
    a = run_fixture("interruption.jsonl")
    b = run_fixture("interruption.jsonl")
    assert a == b
    # every decision round-trips through JSON (the harness's output contract)
    for d in a:
        assert json.loads(json.dumps(d)) == d
        assert d["reason"] and d["rule"]

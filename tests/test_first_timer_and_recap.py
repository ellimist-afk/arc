"""FirstTimerGreeter policy and StreamRecap rendering. Pure logic, injected clocks."""
import json

from features.first_timer import FirstTimerGreeter
from features.stream_recap import StreamRecap


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ------------------------------------------------------------ first timer

def greeter(**kw):
    c = Clock()
    g = FirstTimerGreeter(min_interval_s=45, suppress_after_raid_s=120, clock=c, **kw)
    return g, c


def test_greets_on_first_message_flag_only():
    g, c = greeter()
    assert not g.should_greet({"is_first_message": False})
    assert not g.should_greet({})
    assert g.should_greet({"is_first_message": True})


def test_disabled_never_greets():
    g, c = greeter(enabled=False)
    assert not g.should_greet({"is_first_message": True})


def test_rate_limited_between_greetings():
    g, c = greeter()
    assert g.should_greet({"is_first_message": True})
    g.mark_greeted()
    c.t += 10
    assert not g.should_greet({"is_first_message": True})
    c.t += 40
    assert g.should_greet({"is_first_message": True})
    assert g.stats() == {"enabled": True, "greeted": 1, "suppressed": 1}


def test_raid_suppresses_greetings_for_a_window():
    g, c = greeter()
    g.note_raid()
    c.t += 30
    assert not g.should_greet({"is_first_message": True})
    c.t += 100
    assert g.should_greet({"is_first_message": True})


def test_from_settings_reads_block_and_tolerates_missing(tmp_path):
    path = tmp_path / "bot_settings.json"
    path.write_text(json.dumps({"first_timer_greeting": {"enabled": False, "min_interval_s": 9}}))
    g = FirstTimerGreeter.from_settings(str(path))
    assert g.enabled is False and g.min_interval_s == 9.0 and g.suppress_after_raid_s == 120.0
    g2 = FirstTimerGreeter.from_settings(str(tmp_path / "nope.json"))
    assert g2.enabled is True and g2.min_interval_s == 45.0


# ----------------------------------------------------------------- recap

def test_recap_counts_and_renders(tmp_path):
    c = Clock(1_700_000_000.0)
    r = StreamRecap("#Cassova_", out_dir=str(tmp_path), clock=c)
    assert not r.has_activity
    for name in ["alice", "bob", "Alice", "carol", "alice"]:
        r.record_message(name)
        c.t += 60
    r.record_response(spoken=True)
    r.record_response(spoken=False)
    r.record_event("bob raided with 12 viewers")
    c.t += 3600

    md = r.render("Everyone argued about pineapple.", {"Repetition guard rejections": 3})
    assert md.startswith("# Stream recap — cassova_ — ")
    assert "- Chat messages: 5 from 3 chatters" in md
    assert "- Co-host replies: 2 (1 spoken)" in md
    assert "- Repetition guard rejections: 3" in md
    assert "- alice: 3" in md and "- bob: 1" in md
    assert "raided with 12 viewers" in md
    assert "Everyone argued about pineapple." in md
    assert "(4m)" in md  # first message -> last message window (4 x 60s), not the trailing idle hour

    path = r.write("Everyone argued about pineapple.", {"Repetition guard rejections": 3})
    assert path is not None and path.exists()
    assert path.name.startswith("recap_cassova__")
    assert path.read_text(encoding="utf-8") == md


def test_recap_without_summary_says_so_and_reset_clears():
    r = StreamRecap("ch", clock=Clock())
    r.record_message("x")
    md = r.render("")
    assert "_No session summary was produced._" in md
    r.reset()
    assert not r.has_activity and r.messages == 0 and not r.chatters

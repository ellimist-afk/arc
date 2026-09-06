"""Plain over clever: the co-host stops building comparisons.

Live 2026-09-06, persona uwu -- "it's extremely unfunny too". Twelve of the
last 41 unsolicited lines, and nearly every reply, were the same
construction: an observation, a pet name, and a manufactured comparison.

    gary's credibility just fell over like cassova_ trying to peel
    that french kiss arrived with airport customs energy
    that's basically cassova_ doing homework with a snack trail

The shape is invisible to the repetition guard's other checks -- every one
of those lines is about a different thing, shares no n-grams, and names a
different subject. So the guard matches the shape itself on the raw text,
the prompt asks for plain first, the anchors stop demonstrating similes,
and sampling stops reaching into the tail where the weird comparisons live.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from personality.repetition_guard import RepetitionGuard  # noqa: E402


def _guard(**kw):
    g = RepetitionGuard(**kw)
    # A little history, so nothing passes merely for want of anything to
    # compare against.
    g.record("chat is arguing about pineapple pizza again")
    g.record("the boss fight is going better than expected")
    return g


# ------------------------------------------------------- the shapes, rejected

def test_the_live_lines_are_rejected():
    """Each row of the failure matrix that must fail."""
    g = _guard()
    for draft in (
        "gary's credibility just fell over like cassova_ trying to peel",
        "that french kiss arrived with airport customs energy",
        "that's basically a snack trail",
        "the timeline got spawn-camped, it's just a graveyard now",
        "january 2026 memes already look exhausted, very doomscroll coded",
        "the whole lobby was giving abandoned mall vibes",
        "you played like a champ",
    ):
        v = g.check(draft)
        assert not v.ok, draft
        assert v.comparison, draft
        assert "crafted comparison" in v.reason, draft


def test_plain_lines_pass():
    g = _guard()
    for draft in (
        "i like that build",
        "feels like you got robbed",
        "oh no. you were doing so well for a second.",
        "nah",
        "wait you hit that?? okay. proud of you.",
        "the energy in here is unreal",   # "energy" mid-line is not the shape
    ):
        v = g.check(draft)
        assert v.ok, f"{draft} -> {v.reason}"
        assert v.comparison is None, draft


def test_the_matched_span_is_reported():
    g = _guard()
    v = g.check("that's basically a snack trail")
    assert v.comparison == "basically a"
    assert "crafted comparison: 'basically a'" in v.reason


def test_the_hint_tells_it_to_say_the_plain_version():
    g = _guard()
    v = g.check("that french kiss arrived with airport customs energy")
    hint = g.avoid_hint(v)
    assert "No comparisons." in hint
    assert "say the plain version, shorter" in hint
    assert "shortest honest reaction" in hint


# ------------------------------------------------------------ no exemptions

def test_short_drafts_are_checked_too():
    """The short-text path compares by exact match only; the shape still
    has to be caught there or "basically a snack" walks straight through."""
    g = _guard()
    v = g.check("basically a snack")
    assert not v.ok and v.comparison == "basically a"


def test_the_first_line_of_a_session_is_checked():
    """Empty history returns early, before every other check. A cold start
    is exactly where the shape showed up live."""
    g = RepetitionGuard()
    assert not g.history
    v = g.check("that arrived with airport customs energy")
    assert not v.ok and v.comparison


def test_a_viewer_saying_like_a_does_not_license_a_simile_back():
    """No topic_exempt equivalent: the shape is banned regardless of what
    was said to the bot."""
    g = _guard()
    v = g.check("honestly yeah, it played like a slideshow",
                fresh_topic=True,
                topic_exempt=RepetitionGuard.topic_words(
                    "does this run like a slideshow for you too"))
    assert not v.ok and v.comparison


def test_the_flag_turns_it_off():
    g = _guard(ban_comparisons=False)
    v = g.check("that's basically a snack trail")
    assert v.ok and v.comparison is None
    assert "crafted comparison" not in v.reason


def test_settings_carry_the_flag():
    """The key has to be in bot_settings.json and in the loader's key list,
    or turning it off in the settings file does nothing."""
    import json
    settings = json.loads(Path("bot_settings.json").read_text(encoding="utf-8"))
    assert settings["repetition_guard"]["ban_comparisons"] is True
    engine_src = Path("src/personality/personality_engine.py").read_text(
        encoding="utf-8", errors="replace")
    assert "'ban_comparisons'" in engine_src


# -------------------------------------------------------- the audit report

def test_the_comparison_rejection_is_auditable_after_the_stream():
    """The reason string is the needle. If it is reworded, the guard drops
    off the post-stream report silently -- so it is pinned to the source
    that produces it and to the auditor that looks for it. The auditor's
    own source scan (test_audit_stream_log.py) never reads
    repetition_guard.py, so the pin lives here instead, the same way
    test_streamer_target.py pins streamer_punchline."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
    from audit_stream_log import GUARDS, parse_sessions, render  # noqa: E402

    guard_src = Path("src/personality/repetition_guard.py").read_text(encoding="utf-8")
    assert "crafted comparison: " in guard_src

    row = [g for g in GUARDS if g[0] == "comparison_reject"]
    assert row, "comparison_reject is not in GUARDS"
    key, needle, label = row[0]
    assert needle == "crafted comparison"

    log = ("2026-09-06 13:20:00,000 - src.bot.bot - INFO - Starting TalkBot setup...\n"
           "2026-09-06 13:21:00,000 - src.personality.personality_engine - INFO - "
           "Repetition guard rejected draft (crafted comparison: 'basically a'); "
           "regenerating\n")
    session = parse_sessions(log)[0]
    assert session.counts[key] == 1
    assert label in render(session)

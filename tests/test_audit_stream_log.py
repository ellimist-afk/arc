"""The post-stream auditor must attribute log lines to the right guard.

Every guard logs a distinctive line when it acts; the auditor's whole value
is turning those into per-session counts. These tests pin the signature
strings to what the source actually logs, so a reworded log message breaks
a test here instead of silently vanishing from the report.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from audit_stream_log import GUARDS, parse_sessions, render  # noqa: E402

L = "2026-08-27 21:{m:02d}:00,000 - src.bot.bot - {lvl} - {msg}"


def line(minute, msg, lvl="INFO"):
    return L.format(m=minute, lvl=lvl, msg=msg)


START = line(0, "Starting TalkBot setup...")


def _log(*lines):
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------ sessioning

def test_sessions_split_on_the_startup_marker():
    text = _log(START, line(1, "hello"),
                line(10, "Starting TalkBot setup..."), line(11, "again"))
    sessions = parse_sessions(text)
    assert len(sessions) == 2
    assert sessions[0].end == "2026-08-27 21:01:00"
    assert sessions[1].start == "2026-08-27 21:10:00"


def test_a_log_that_begins_mid_session_still_reports():
    sessions = parse_sessions(_log(line(5, "mid-run line")))
    assert len(sessions) == 1
    assert "before log start" in sessions[0].start


def test_continuation_lines_are_tolerated():
    text = _log(START, "Traceback (most recent call last):",
                '  File "x.py", line 1', line(2, "recovered"))
    assert parse_sessions(text)[0].end == "2026-08-27 21:02:00"


# ------------------------------------------------------- guard attribution

def test_each_guard_signature_is_counted():
    msgs = {
        "dead_air_abort": "Dead air ended while the line was being generated; staying quiet",
        "freshness_drop": "Reply ready 287s after the message (limit 45s); the room has moved on -- dropping",
        "breather_hold": "Holding follow-up reply: bot spoke 12s ago",
        "rerun_reject": "Repetition guard rejected draft (re-told topic: 'sigaren', 'waifu'); regenerating",
        "tic_reject": "Repetition guard rejected draft (hot phrases: 'exactly how'); regenerating",
        "voice_drop": "Dropped low-confidence transcript 'uh uh uh' (no_speech_prob=0.81)",
        "followup_upgrade": "Follow-up from GoodStuffBuds treated as a mention",
        "streak_spent": "Follow-up streak spent for goodstuffbuds; needs the name again",
    }
    text = _log(START, *(line(i + 1, m) for i, m in enumerate(msgs.values())))
    s = parse_sessions(text)[0]
    for key in msgs:
        assert s.counts[key] == 1, key


def test_signatures_match_what_the_source_actually_logs():
    """A reworded log line must break here, not silently drop off the report."""
    src = "".join(
        Path(p).read_text(encoding="utf-8")
        for p in ("src/bot/bot.py", "src/bot/response_coordinator.py",
                  "src/personality/personality_engine.py",
                  "src/components/voice/recognition.py",
                  "src/features/ad_announcer.py"))
    sourced = {  # keys whose needle is a literal in some logger call
        "dead_air_abort": "Dead air ended while the line was being ",
        "dead_air_quiet": "Dead air: no line worth sending; staying quiet",
        "freshness_drop": "the room has moved on -- dropping",
        "repetition_skip": "retry also repetitive",
        "repetition_forced": "both drafts repetitive, delivering least-bad",
        "followup_upgrade": "treated as a mention",
        "streak_spent": "Follow-up streak spent",
        "voice_drop": "Dropped low-confidence transcript",
        "ad_fallback": "Ad line timed out",
        "ad_failed": "Ad line failed",
    }
    for key, needle in sourced.items():
        assert needle in src, f"{key}: source no longer logs {needle!r}"


def test_guard_hold_lines_are_info_not_debug():
    """DEBUG never reaches talkbot.log at the default level, so the two
    breather lines were bumped to INFO to stay auditable."""
    src = Path("src/bot/bot.py").read_text(encoding="utf-8")
    assert 'logger.info(f"Holding' in src
    assert 'logger.info(f"Follow-up streak spent' in src


def test_rerun_and_tic_rejections_are_distinguished():
    text = _log(
        START,
        line(1, "Repetition guard rejected draft (re-told topic: 'waifu', 'sigaren'); regenerating"),
        line(2, "Repetition guard rejected draft (hot phrases: 'exactly how'; similarity 0.11); regenerating"),
    )
    s = parse_sessions(text)[0]
    assert s.counts["rerun_reject"] == 1
    assert s.counts["tic_reject"] == 1
    assert s.counts["rejections_total"] == 2


# ------------------------------------------------------------- the report

def test_report_shows_counts_and_examples():
    text = _log(START,
                line(1, "TTS Decision: TTS_ENABLED=False, should_speak=False"),
                line(2, "Reply ready 300s after the message (limit 45s); the room has moved on -- dropping", "WARNING"))
    out = render(parse_sessions(text)[0])
    assert "Replies delivered: 1" in out
    assert "Late replies dropped" in out
    assert "21:02:00" in out, "examples carry timestamps"


def test_quiet_guards_are_listed_not_hidden():
    out = render(parse_sessions(_log(START, line(1, "nothing special")))[0])
    assert "never fired" in out
    for _, _, label in GUARDS[:3]:
        assert label in out


def test_warnings_are_grouped_by_shape():
    text = _log(START,
                line(1, "Reply ready 61s after the message (limit 45s); the room has moved on -- dropping", "WARNING"),
                line(2, "Reply ready 90s after the message (limit 45s); the room has moved on -- dropping", "WARNING"))
    s = parse_sessions(text)[0]
    assert max(s.warnings.values()) == 2, "digits collapse so identical problems group"

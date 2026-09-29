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
        "streamer_punchline": ("Repetition guard rejected draft "
                               "(streamer punchline: cassova_ in 1 of last 4); regenerating"),
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


# ------------------------------------------------- the screen variety guards

def test_the_screen_variety_signatures_are_what_the_source_logs():
    """Live 2026-09-05 the co-host did four interchangeable screen lines in
    ten minutes. Three new guards stop that, so the audit has to be able to
    say how often each one fired."""
    src = "".join(
        Path(p).read_text(encoding="utf-8")
        for p in ("src/bot/bot.py", "src/features/screen_watcher.py"))
    for key, needle in (
            ("screen_cooldown_hold", "Screen moment held by cooldown"),
            ("screen_same_kind", "Screen moment held, same kind"),
            ("screen_pass", "Screen reaction passed"),
            ("filler_off_screen", "Dead air: steering the filler off the screen")):
        assert needle in src, f"{key}: source no longer logs {needle!r}"
        assert any(k == key for k, _, _ in GUARDS), f"{key} is not in GUARDS"


def test_screen_look_does_not_double_count_holds():
    """The hold lines used to start with "Screen: " too, so every hold was
    also counted as a fresh look. They were reworded to drop the colon so
    the screen_look needle stops matching them."""
    text = _log(START,
                line(1, "Screen: same old menu, nothing new"),
                line(2, "Screen moment held by cooldown (something happened)"),
                line(3, "Screen moment held, same kind (death, 2 in a row)"))
    s = parse_sessions(text)[0]
    assert s.counts["screen_look"] == 1
    assert s.counts["screen_cooldown_hold"] == 1
    assert s.counts["screen_same_kind"] == 1


def test_the_screen_variety_guards_are_reported():
    text = _log(START,
                line(1, "Screen moment held by cooldown (something happened)"),
                line(2, "Screen moment held, same kind (death, 2 in a row)"),
                line(3, "Screen reaction passed: nothing new to say"),
                line(4, "Dead air: steering the filler off the screen (said about it 120s ago)"))
    out = render(parse_sessions(text)[0])
    assert "Screen moments held by the flat reaction cooldown: 1" in out
    assert "Screen moments held because that kind was just reacted to: 1" in out
    assert "Screen moments the co-host chose to let pass: 1" in out
    assert "Fillers steered to another well because the screen was just discussed: 1" in out


# --------------------------------------------- the people-over-screen guards


def test_the_people_over_screen_signatures_are_what_the_source_logs():
    """Live 2026-09-06 two menus got lines and five viewers were held while
    the co-host narrated the screen. The audit has to be able to say how
    often each of the three new guards fired."""
    src = "".join(
        Path(p).read_text(encoding="utf-8")
        for p in ("src/bot/bot.py", "src/features/screen_watcher.py"))
    for key, needle in (
            ("screen_menu_hold", "Screen moment held, menus are not moments"),
            ("screen_yield_chat", "Screen moment held, chat is talking"),
            ("room_followup", "Room follow-up from")):
        assert needle in src, f"{key}: source no longer logs {needle!r}"
        assert any(k == key for k, _, _ in GUARDS), f"{key} is not in GUARDS"


def test_the_people_over_screen_guards_are_reported():
    text = _log(START,
                line(1, "Screen moment held, menus are not moments (lobby)"),
                line(2, "Screen moment held, chat is talking (12s ago)"),
                line(3, "Room follow-up from motherchandelure: answering the co-host's line"))
    out = render(parse_sessions(text)[0])
    assert "Menu/lobby screens the vision model flagged and the co-host ignored: 1" in out
    assert "Screen moments held because viewers were talking: 1" in out
    assert "Viewers answering an unprompted line who got a reply: 1" in out


def test_a_menu_hold_is_not_counted_as_a_same_kind_hold():
    """Both start "Screen moment held, "; the needles must stay disjoint."""
    text = _log(START,
                line(1, "Screen moment held, menus are not moments (lobby)"),
                line(2, "Screen moment held, same kind (death, 2 in a row)"),
                line(3, "Screen moment held, chat is talking (12s ago)"))
    s = parse_sessions(text)[0]
    assert s.counts["screen_menu_hold"] == 1
    assert s.counts["screen_same_kind"] == 1
    assert s.counts["screen_yield_chat"] == 1
    assert s.counts["screen_look"] == 0, "a hold is not a look"


def test_a_room_follow_up_is_told_apart_from_a_viewer_follow_up():
    """"Follow-up from X ... treated as a mention" is the followup_upgrade
    needle; "Room follow-up from X" no longer ends that way, so a room
    follow-up must not also be double-counted as a followup_upgrade."""
    text = _log(START,
                line(1, "Follow-up from cassova_ treated as a mention"),
                line(2, "Room follow-up from motherchandelure: answering the co-host's line"))
    s = parse_sessions(text)[0]
    assert s.counts["room_followup"] == 1
    assert s.counts["followup_upgrade"] == 1, "the room follow-up must not also count as a followup_upgrade"


# ------------------------------------------------------------ topic wells

def test_the_topic_well_signatures_are_what_the_source_logs():
    src = Path("src/bot/bot.py").read_text(encoding="utf-8")
    assert 'logger.info(f"Filler well: {choice.well} ({reason})")' in src
    assert 'logger.info("Filler well: none, staying quiet")' in src
    from features.topic_wells import WELLS
    keys = {k for k, _, _ in GUARDS}
    for well in WELLS:
        assert f"filler_well_{well}" in keys, well


def test_the_topic_wells_are_counted_per_well():
    s = parse_sessions(_log(
        START,
        line(1, "Filler well: game (5 eligible, last none, game 0/6)"),
        line(2, "Filler well: off_topic (4 eligible, last game, game 1/6)"),
        line(3, "Filler well: streamer_said (3 eligible, last off_topic, "
                "game 1/6, mixed mic bus: may be game audio)"),
        line(4, "Filler well: none, staying quiet")))[0]
    assert s.counts["filler_well_game"] == 1
    assert s.counts["filler_well_off_topic"] == 1
    assert s.counts["filler_well_streamer_said"] == 1
    assert s.counts["filler_well_none"] == 1
    assert not s.counts.get("filler_well_ask_chat")

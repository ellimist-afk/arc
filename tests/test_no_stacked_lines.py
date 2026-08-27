"""Two bot lines must not land back to back from independent triggers.

Seen live 2026-08-25 at 21:03: a dead-air filler (lull began 21:01, 120s
threshold) fired while a reply to a fresh viewer message was being
generated, so the co-host posted two lines in the same minute -- one of
them answering a four-minute-old message. The lull check happens seconds
before the send, and generation takes long enough for the room to restart.
"""
from datetime import datetime, timedelta
from pathlib import Path

from bot.response_coordinator import ResponseCoordinator

MONITOR = Path("src/bot/response_coordinator.py").read_text(encoding="utf-8").split(
    "async def _dead_air_monitor")[1]
HANDLER = Path("src/bot/bot.py").read_text(encoding="utf-8")


# ------------------------------------------------ dead-air late-send guard

def test_monitor_captures_the_lull_before_generating():
    assert "lull_marker = self.last_activity_time" in MONITOR
    assert MONITOR.index("lull_marker = self.last_activity_time") < \
        MONITOR.index("generate_response"), "the marker must predate the LLM call"


def test_monitor_drops_the_line_if_the_lull_ended():
    recheck = MONITOR.index("if self.last_activity_time != lull_marker:")
    assert MONITOR.index("generate_response") < recheck, \
        "the recheck must come after generation"
    assert recheck < MONITOR.index("coordinate_response"), \
        "and before anything is sent"
    tail = MONITOR[recheck:].split("continue")[0]
    assert "staying quiet" in tail


def test_a_fresh_message_moves_the_marker():
    rc = ResponseCoordinator(twitch_client=None, audio_queue=None, settings_path="nope.json")
    marker = rc.last_activity_time
    rc.note_activity()                      # what a viewer message does
    assert rc.last_activity_time != marker or \
        (datetime.now() - marker).total_seconds() < 0.001


async def test_our_own_reply_in_flight_also_moves_the_marker():
    """coordinate_response stamps last_activity_time first thing, so a reply
    that lands during filler generation ends the lull too."""
    rc = ResponseCoordinator(twitch_client=None, audio_queue=None, settings_path="nope.json")
    rc.last_activity_time = datetime.now() - timedelta(seconds=300)
    marker = rc.last_activity_time
    await rc.coordinate_response(chat_msg="a reply", audio_task=None)
    assert rc.last_activity_time != marker


# ---------------------------------------------- unsolicited-reply breather

def test_bot_declares_the_gap():
    assert "self.unsolicited_gap_s = 30.0" in HANDLER
    assert "self.last_bot_message_at = None" in HANDLER


def test_gate_sits_before_generation_and_spares_mentions():
    gate = HANDLER.index("Skipping unsolicited reply")
    block_start = HANDLER.rindex("if not is_mention and not greet", 0, gate)
    assert "self.last_bot_message_at is not None" in HANDLER[block_start:gate]
    assert gate < HANDLER.index("Sentence-streamed path (flag-gated)"), \
        "the gate must come before any reply generation"


def test_both_send_paths_stamp_the_timestamp():
    stamps = HANDLER.count("self.last_bot_message_at = datetime.now()")
    assert stamps == 2, f"blocking and streamed paths must both stamp ({stamps})"


def test_gate_math():
    now = datetime.now()
    gap_s = 30.0
    spoke_10s_ago = now - timedelta(seconds=10)
    spoke_45s_ago = now - timedelta(seconds=45)
    assert (now - spoke_10s_ago).total_seconds() < gap_s, "10s ago -> hold"
    assert (now - spoke_45s_ago).total_seconds() >= gap_s, "45s ago -> free to speak"

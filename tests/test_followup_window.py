"""A follow-up in a conversation the co-host started counts as talking to it.

Seen live 2026-08-25: GoodStuffBuds asked "@elimist_ ever wonder if your wife
will come back?", got an answer, then asked "how did you parallel park your
setup" -- plainly a follow-up about the joke the bot had just made -- and got
nothing, because is_bot_addressed requires the bot's name and chat does not
re-type it. The second half of the exchange fell back to the 3% roll.
"""
import time

from bot.bot import TalkBot, is_bot_addressed


def _bot(window=90.0, cap=3):
    b = TalkBot.__new__(TalkBot)
    b._replied_to = {}
    b.followup_window_s = window
    b.max_followups = cap
    return b


# ------------------------------------------------ the underlying blind spot

def test_a_follow_up_has_no_name_in_it():
    assert is_bot_addressed("@elimist_ ever wonder if your wife will come back?", "elimist_")
    assert not is_bot_addressed("how did you parallel park your setup", "elimist_"), \
        "this is why the follow-up window is needed"


# ----------------------------------------------------------- the window

def test_no_window_before_the_bot_has_spoken():
    assert _bot()._is_followup("goodstuffbuds") is False


def test_the_viewer_the_bot_answered_gets_a_window():
    b = _bot()
    b._note_replied_to("GoodStuffBuds")
    assert b._is_followup("goodstuffbuds") is True


def test_the_window_is_per_viewer():
    b = _bot()
    b._note_replied_to("goodstuffbuds")
    assert b._is_followup("anakayzee") is False


def test_usernames_are_matched_case_and_at_insensitively():
    b = _bot()
    b._note_replied_to("@GoodStuffBuds")
    assert b._is_followup("goodstuffbuds")
    assert b._is_followup("@GOODSTUFFBUDS")


def test_the_window_expires():
    b = _bot(window=0.0)
    b._note_replied_to("goodstuffbuds")
    time.sleep(0.05)   # > Windows clock granularity
    assert b._is_followup("goodstuffbuds") is False


def test_blank_usernames_are_ignored():
    b = _bot()
    b._note_replied_to("")
    assert b._replied_to == {}
    assert b._is_followup("") is False


# ---------------------------------------------------- ping-pong protection

def test_a_streak_runs_out():
    """A real back-and-forth is welcome; an endless one is not."""
    b = _bot(cap=3)
    for _ in range(3):
        b._note_replied_to("goodstuffbuds")
    assert b._is_followup("goodstuffbuds") is False, "streak spent"


def test_the_streak_allows_a_genuine_exchange():
    b = _bot(cap=3)
    b._note_replied_to("goodstuffbuds")
    assert b._is_followup("goodstuffbuds")
    b._note_replied_to("goodstuffbuds")
    assert b._is_followup("goodstuffbuds")


def test_expiry_clears_the_streak_for_a_fresh_conversation():
    b = _bot(window=0.0, cap=3)
    for _ in range(3):                        # streak fully spent
        b._note_replied_to("goodstuffbuds")
    time.sleep(0.05)   # > Windows clock granularity
    b._is_followup("goodstuffbuds")           # expires and sweeps
    assert "goodstuffbuds" not in b._replied_to
    b._note_replied_to("goodstuffbuds")       # a new exchange starts clean
    b.followup_window_s = 90.0
    assert b._is_followup("goodstuffbuds"), "the spent streak must not carry over"


def test_stale_entries_are_swept_so_the_map_cannot_grow_all_stream():
    b = _bot(window=0.0)
    for i in range(50):
        b._note_replied_to(f"viewer{i}")
    time.sleep(0.05)   # > Windows clock granularity
    b._is_followup("viewer0")
    assert b._replied_to == {}, "expired entries must be dropped"

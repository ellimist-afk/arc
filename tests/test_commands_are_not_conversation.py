"""A chat command must not also get a conversational reply.

!persona, !ad, !clip and friends each have their own handler registered on
the same on_message stream as ordinary chat. Nothing marked them as
commands, so "!persona roast" was answered twice: once by the command, and
again by the co-host riffing on it. That second reply was near certain
whenever the mod had just been talking to the bot, because the follow-up
window promotes their next message to a mention.
"""
from pathlib import Path

import pytest

BOT = Path("src/bot/bot.py").read_text(encoding="utf-8")
HANDLER = BOT.split("async def _handle_chat_message")[1].split("\n    async def ")[0]


def test_the_handler_returns_early_on_a_command():
    assert "startswith('!')" in HANDLER
    assert "Chat command; no conversational reply" in HANDLER


def test_activity_is_still_noted_before_the_bail():
    """A command proves someone is present, so the dead-air timer must see
    it -- otherwise typing !persona could be followed by a lull filler."""
    note = HANDLER.index("note_activity()")
    bail = HANDLER.index("Chat command; no conversational reply")
    assert note < bail


def test_the_command_never_reaches_generation_or_context():
    bail = HANDLER.index("Chat command; no conversational reply")
    for later in ("store_message", "is_bot_addressed", "received_at"):
        assert HANDLER.index(later) > bail, f"{later} must come after the bail"


def test_leading_whitespace_does_not_smuggle_a_command_through():
    assert ".lstrip().startswith('!')" in HANDLER


@pytest.mark.parametrize("text, is_command", [
    ("!persona roast", True),
    ("!ad 90", True),
    ("!clip", True),
    ("  !persona", True),
    ("hey what's up", False),
    ("that was a 10!", False),
    ("", False),
    ("i said !!! loudly", False),
])
def test_command_detection_matches_intent(text, is_command):
    assert text.lstrip().startswith('!') is is_command


def test_an_absent_text_key_does_not_crash():
    """message.get('text') can be None; None.lstrip() would raise."""
    assert "(message.get('text') or '')" in HANDLER

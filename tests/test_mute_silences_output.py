""""Hey bot, shut up" must actually stop it talking.

The mute command is described as "Mutes the bot temporarily" and fires on
"mute", "shut up", "be quiet" and "silence". But `self.muted` was only ever
read in the voice-input handler, so a muted co-host stopped LISTENING and
carried on replying in chat, filling lulls, and (once vision landed)
reacting to the screen. That is the opposite of what the command says.
"""
from pathlib import Path

BOT = Path("src/bot/bot.py").read_text(encoding="utf-8")
VOICE = Path("src/components/voice/voice_commands.py").read_text(encoding="utf-8")


def test_the_command_promises_silence():
    assert r"\b(mute|shut up|be quiet|silence)\b" in VOICE
    assert "self.bot.muted = True" in VOICE


def test_muting_stops_chat_replies():
    assert 'logger.info("Muted; not replying in chat")' in BOT


def test_the_chat_check_precedes_generation():
    """Muted must cost nothing: no LLM call, no reply built and thrown away."""
    check = BOT.index("Muted; not replying in chat")
    # Searched FROM the mute check: both markers also occur earlier in the
    # voice handler, and a file-wide index would compare the wrong ones.
    assert check < BOT.index("Sentence-streamed path (flag-gated)", check)
    assert check < BOT.index("# Track last response for repeat command", check)


def test_muting_silences_even_a_direct_mention():
    """The streamer asked for quiet; a viewer @-ing it does not override
    that. `respond_less` exists for the softer setting."""
    check = BOT.index("Muted; not replying in chat")
    block = BOT.rindex("if self.muted:", 0, check)
    guard = BOT[block:check]
    assert "is_mention" not in guard, "mute must not be conditional on mentions"


def test_muting_stops_screen_reactions():
    fn = BOT.split("async def _react_to_screen")[1].split("def _dead_air_context")[0]
    assert "Muted; not reacting to the screen" in fn


def test_muting_stops_dead_air_fillers():
    gate = BOT.split("self.response_coordinator.should_fill = lambda: (")[1].split(")\n")[0]
    assert "not self.muted" in gate
    assert "is_live is not False" in gate, "the liveness rule must survive"


def test_events_still_announce_while_muted():
    """A raid or a gifted sub is an occasion, not chatter; silencing those
    would lose something a viewer actually paid for."""
    for handler in ("_process_raid", "_on_gift_sub"):
        fn = BOT.split(f"def {handler}")[1][:1200]
        assert "self.muted" not in fn, f"{handler} must not be gated on mute"


def test_a_muted_bot_still_learns():
    """Memory and the session summary keep running: unmuting into an empty
    head would make the silence cost more than it should."""
    check = BOT.index("Muted; not replying in chat")
    tail = BOT[check:check + 260]
    assert "_schedule_summary()" in tail
    assert BOT.index("await self.memory_system.store_message(message)") < check

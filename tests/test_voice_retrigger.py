"""One spoken question must get one answer.

Seen live 2026-08-30 17:20: the streamer asked about diamond wafers once,
but Whisper chunked the continuing speech into two utterances that BOTH
contained "hey bot" -- each chunk triggered its own reply, so the same
question was answered twice, seconds apart, in different words. The text
dedup cannot catch it: the two transcripts differ too much lexically.
"""
from datetime import datetime, timedelta
from pathlib import Path

BOT = Path("src/bot/bot.py").read_text(encoding="utf-8")
VOICE = BOT.split('logger.info(f"Processing voice input:')[1].split("\n    async def ")[0]


def test_the_gap_is_declared():
    assert "self.voice_retrigger_gap_s = 12.0" in BOT
    assert "self.last_voice_reply_at = None" in BOT


def test_a_trigger_right_after_a_reply_is_held():
    assert "Voice trigger held" in VOICE
    assert "same question re-chunked" in VOICE


def test_the_stamp_lands_when_the_reply_completes():
    after_time = BOT.split('logger.info(f"Voice response time:')[1][:120]
    assert "self.last_voice_reply_at = datetime.now()" in after_time


def test_voice_commands_are_not_gated():
    """"hey bot mute" must always work; only conversational triggers wait."""
    commands = BOT.index("voice_commands.process_input(text)")
    gate = BOT.index("Voice trigger held")
    assert commands < gate, "commands are handled before the retrigger gate"


def test_the_first_trigger_is_never_held():
    gate = BOT.split("Voice trigger held")[0]
    assert "getattr(self, 'last_voice_reply_at', None)" in gate[-500:], \
        "a fresh or partially-built bot has no previous reply to be near"


def test_gap_math():
    now = datetime.now()
    gap_s = 12.0
    replied_5s_ago = now - timedelta(seconds=5)
    replied_20s_ago = now - timedelta(seconds=20)
    assert (now - replied_5s_ago).total_seconds() < gap_s, "5s later -> same question"
    assert (now - replied_20s_ago).total_seconds() >= gap_s, "20s later -> a new one"


def test_the_gate_sits_after_mute_and_before_generation():
    mute = VOICE.index("Bot is muted")
    gate = VOICE.index("Voice trigger held")
    assert mute < gate
    assert "recent_voice_texts.append" in VOICE[gate:], \
        "a held trigger must not even be recorded as handled input"

"""Audio heard on the mixed bus must not become the streamer's words.

Alan routes everything through VoiceMeeter, so the mic bus carries the
video or game as well as his voice. Untriggered audio was stored under the
streamer's username: a YouTuber's outro ("and I'll see you guys in the next
video") went into memory and context as something cassova_ said. On a mixed
bus the voices cannot be told apart, so ambient audio is attributed to its
own pseudo-speaker and framed in the prompt as background, not a person.
"""
from pathlib import Path

BOT = Path("src/bot/bot.py").read_text(encoding="utf-8")


# --------------------------------------------------------------- storage

def test_untriggered_audio_is_not_the_streamer():
    block = BOT.split("[VOICE] NO TRIGGER in:")[1][:1400]
    assert "'username': 'stream_audio'" in block
    assert "'user_id': 'stream_audio'" in block
    assert "TWITCH_CHANNEL" not in block.split("await self.memory_system")[0], \
        "the streamer's name must not label audio that may not be theirs"


def test_triggered_audio_is_still_the_streamer():
    """Nobody else says "hey bot" at the mic; an addressed line is Alan's."""
    after_trigger = BOT.split("Processing voice input:")[1]
    naming = after_trigger.split("voice_message = {")[0][-300:]
    assert "self.config.get('TWITCH_CHANNEL'" in naming
    dict_body = after_trigger.split("voice_message = {")[1][:200]
    assert "'username': username," in dict_body


def test_the_triggers_spam_line_is_gone():
    """It printed on every single utterance -- hundreds of times a session."""
    assert "Available triggers: hey bud" not in BOT


# ---------------------------------------------------------------- prompt

def test_ambient_lines_are_framed_as_background():
    src = Path("src/personality/personality_engine.py").read_text(encoding="utf-8")
    assert "elif username == 'stream_audio':" in src
    assert "[heard on stream audio, not a person]:" in src
    branch = src.split("elif username == 'stream_audio':")[1]
    assert branch.index("[heard on stream audio") < branch.index("# Someone in chat said this"), \
        "the ambient branch must be checked before the person branch"


def test_real_viewers_still_render_with_their_names():
    src = Path("src/personality/personality_engine.py").read_text(encoding="utf-8")
    assert 'f"{username}: {text}"' in src

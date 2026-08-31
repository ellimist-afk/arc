"""Game and video dialogue reaches the co-host as ambient context.

The attribution fix stopped crediting the video's lines to the streamer,
but ambient lines only went to the memory DB -- the reply context reads the
chat buffer, which the ambient path never touches, so the bot stopped
HEARING the dialogue at all. It now flows through a small dedicated window:
capped at three lines so a cutscene-heavy game cannot flood real chat out
of context, and aged out after three minutes so a dead moment is not
narrated as current.
"""
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from bot.bot import TalkBot

BOT = Path("src/bot/bot.py").read_text(encoding="utf-8")
BUILDER = Path("src/bot/optimized_context_builder.py").read_text(encoding="utf-8")
ENGINE = Path("src/personality/personality_engine.py").read_text(encoding="utf-8")


def _bot(entries=()):
    b = TalkBot.__new__(TalkBot)
    b._ambient_audio = deque(entries, maxlen=3)
    b.ambient_max_age_s = 180.0
    return b


# ------------------------------------------------------------ the window

def test_fresh_dialogue_is_served_oldest_first():
    now = datetime.now()
    b = _bot([(now - timedelta(seconds=60), "first line"),
              (now - timedelta(seconds=5), "second line")])
    assert b._ambient_context() == ["first line", "second line"]


def test_old_moments_age_out():
    now = datetime.now()
    b = _bot([(now - timedelta(seconds=400), "a cutscene from ten minutes ago"),
              (now - timedelta(seconds=30), "still current")])
    assert b._ambient_context() == ["still current"]


def test_the_window_is_capped_at_three():
    """A dialogue-heavy game must not flood the context."""
    now = datetime.now()
    b = _bot()
    for i in range(10):
        b._ambient_audio.append((now, f"npc line {i}"))
    assert len(b._ambient_audio) == 3
    assert b._ambient_context() == ["npc line 7", "npc line 8", "npc line 9"]


def test_a_partially_built_bot_serves_nothing():
    b = TalkBot.__new__(TalkBot)
    assert b._ambient_context() == []


# ----------------------------------------------------------- the capture

def test_the_ambient_path_feeds_the_window():
    block = BOT.split("[VOICE] NO TRIGGER in:")[1][:1800]
    assert "self._ambient_audio.append((datetime.now(), text))" in block


def test_noise_never_enters_the_window():
    block = BOT.split("[VOICE] NO TRIGGER in:")[1][:1800]
    filter_at = block.index("_worth_keeping_ambient")
    append_at = block.index("self._ambient_audio.append")
    assert filter_at < append_at, "hallucinated noise must be filtered first"


def test_deque_is_imported_at_module_level():
    """py_compile passed while deque was undefined -- the crash would have
    been at startup, in production, on the first construction."""
    head = BOT[:600]
    assert "from collections import deque" in head


# ------------------------------------------------------------- delivery

def test_both_context_paths_carry_it():
    assert "context['ambient_audio'] = heard" in BOT, "dead-air fillers hear it"
    assert '"ambient_audio": self._ambient()' in BUILDER, "replies hear it"
    assert "self.context_builder.ambient_provider = self._ambient_context" in BOT


def test_a_broken_provider_does_not_break_context():
    body = BUILDER.split("def _ambient(")[1].split("def _on_screen")[0]
    assert "except Exception" in body


def test_the_prompt_says_who_is_talking():
    assert "the video or game" in ENGINE
    assert "NOT the streamer or a viewer" in ENGINE


def test_the_prompt_caps_what_it_quotes():
    block = ENGINE.split("Heard on stream audio just now")[0][-400:]
    assert "heard[-3:]" in block, "never more than three quoted lines"

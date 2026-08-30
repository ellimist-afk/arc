"""!persona switches the co-host's voice from chat.

Twelve presets ship in all_personalities.json and the only way to pick one
was to hand-edit bot_settings.json. Switching is mod-gated because it
changes how the co-host talks to everyone, and cooldowned so a preset war
cannot happen mid-stream.

No test here writes to the real bot_settings.json.
"""
import json
from pathlib import Path

import pytest

from features.persona_command import PersonaCommand


class Engine:
    def __init__(self, name="uwu", ok=True, boom=False):
        self.current_personality_name = name
        self.ok = ok
        self.boom = boom
        self.asked = []

    async def switch_personality_by_name(self, name):
        self.asked.append(name)
        if self.boom:
            raise RuntimeError("engine exploded")
        if self.ok:
            self.current_personality_name = name
        return self.ok


class Chat:
    def __init__(self, boom=False):
        self.sent = []
        self.boom = boom

    async def send_message(self, text):
        if self.boom:
            raise RuntimeError("chat down")
        self.sent.append(text)


@pytest.fixture
def cmd(tmp_path):
    settings = tmp_path / "bot_settings.json"
    settings.write_text(json.dumps({"personality": {"preset": "uwu"}}), encoding="utf-8")
    personas = tmp_path / "all_personalities.json"
    personas.write_text(json.dumps(
        {"uwu": {"traits": {}}, "roast": {"traits": {}}, "chaos": {"traits": {}}}),
        encoding="utf-8")
    c = PersonaCommand(personality_engine=Engine(), twitch_client=Chat(),
                       settings_path=str(settings), personalities_path=str(personas))
    c.settings_file = settings
    return c


def msg(text, mod=False, user="viewer", channel="cassova_"):
    return {"text": text, "username": user, "channel": channel, "is_mod": mod}


# ------------------------------------------------------------- parsing

@pytest.mark.parametrize("text, expected", [
    ("!persona", (True, None)),
    ("!persona roast", (True, "roast")),
    ("  !PERSONA  Roast  ", (True, "roast")),
    ("!persona roast extra words", (True, "roast")),
])
def test_recognised_forms(text, expected):
    assert PersonaCommand.parse(text) == expected


@pytest.mark.parametrize("text", [
    "!personality", "!personas", "hello !persona", "", "   ", "persona roast",
])
def test_non_commands_are_ignored(text):
    assert PersonaCommand.parse(text)[0] is False


async def test_an_unrelated_message_returns_nothing(cmd):
    assert await cmd.handle(msg("just chatting")) is None
    assert cmd.twitch_client.sent == []


# ------------------------------------------------------------- listing

async def test_bare_persona_lists_current_and_available(cmd):
    await cmd.handle(msg("!persona"))
    said = cmd.twitch_client.sent[-1]
    assert "uwu" in said and "roast" in said and "chaos" in said


async def test_anyone_may_ask_what_the_persona_is(cmd):
    """Listing is harmless; only switching is gated."""
    await cmd.handle(msg("!persona", mod=False))
    assert cmd.twitch_client.sent


# ---------------------------------------------------------- permission

async def test_a_viewer_cannot_switch(cmd):
    await cmd.handle(msg("!persona roast", mod=False))
    assert cmd.personality_engine.asked == []
    assert cmd.personality_engine.current_personality_name == "uwu"


async def test_a_mod_can_switch(cmd):
    await cmd.handle(msg("!persona roast", mod=True))
    assert cmd.personality_engine.asked == ["roast"]
    assert "roast" in cmd.twitch_client.sent[-1]


async def test_the_broadcaster_can_switch_without_the_mod_badge(cmd):
    await cmd.handle(msg("!persona roast", mod=False, user="cassova_"))
    assert cmd.personality_engine.asked == ["roast"]


async def test_a_blank_username_is_not_the_broadcaster(cmd):
    """An empty username must not match an empty channel and grant rights."""
    await cmd.handle(msg("!persona roast", mod=False, user="", channel=""))
    assert cmd.personality_engine.asked == []


# ----------------------------------------------------------- validation

async def test_an_unknown_persona_is_refused_with_the_list(cmd):
    await cmd.handle(msg("!persona banana", mod=True))
    assert cmd.personality_engine.asked == []
    said = cmd.twitch_client.sent[-1]
    assert "banana" in said and "roast" in said


async def test_a_failed_switch_is_reported(cmd):
    cmd.personality_engine.ok = False
    await cmd.handle(msg("!persona roast", mod=True))
    assert "Could not switch" in cmd.twitch_client.sent[-1]
    assert cmd.switches == 0


async def test_an_exploding_engine_does_not_escape(cmd):
    cmd.personality_engine.boom = True
    await cmd.handle(msg("!persona roast", mod=True))
    assert "Could not switch" in cmd.twitch_client.sent[-1]


# ------------------------------------------------------------ cooldown

async def test_a_second_switch_is_held(cmd):
    await cmd.handle(msg("!persona roast", mod=True))
    await cmd.handle(msg("!persona chaos", mod=True))
    assert cmd.personality_engine.asked == ["roast"], "the second must be held"
    assert "give it" in cmd.twitch_client.sent[-1]


async def test_the_cooldown_expires(cmd):
    await cmd.handle(msg("!persona roast", mod=True))
    cmd._last_switch_at -= cmd.cooldown_s + 1
    await cmd.handle(msg("!persona chaos", mod=True))
    assert cmd.personality_engine.asked == ["roast", "chaos"]


async def test_the_first_switch_is_never_held(cmd):
    """A fresh bot has _last_switch_at == 0; that must not read as 'just now'."""
    await cmd.handle(msg("!persona chaos", mod=True))
    assert cmd.personality_engine.asked == ["chaos"]


# ---------------------------------------------------------- persistence

async def test_the_choice_is_written_back(cmd):
    await cmd.handle(msg("!persona roast", mod=True))
    saved = json.loads(cmd.settings_file.read_text(encoding="utf-8"))
    assert saved["personality"]["preset"] == "roast"


async def test_other_settings_survive_the_write(cmd):
    data = json.loads(cmd.settings_file.read_text(encoding="utf-8"))
    data["TTS_ENABLED"] = False
    data["voice"] = {"model": "shimmer"}
    cmd.settings_file.write_text(json.dumps(data), encoding="utf-8")
    await cmd.handle(msg("!persona roast", mod=True))
    saved = json.loads(cmd.settings_file.read_text(encoding="utf-8"))
    assert saved["TTS_ENABLED"] is False and saved["voice"]["model"] == "shimmer"


async def test_an_unwritable_settings_file_still_switches(cmd):
    """The voice change matters more than remembering it."""
    cmd.settings_path = "/nonexistent/dir/bot_settings.json"
    await cmd.handle(msg("!persona roast", mod=True))
    assert cmd.personality_engine.asked == ["roast"]
    assert "roast" in cmd.twitch_client.sent[-1]


# ------------------------------------------------------------ resilience

async def test_a_missing_personalities_file_does_not_block_switching(tmp_path):
    settings = tmp_path / "s.json"
    settings.write_text("{}", encoding="utf-8")
    c = PersonaCommand(personality_engine=Engine(), twitch_client=Chat(),
                       settings_path=str(settings),
                       personalities_path=str(tmp_path / "missing.json"))
    assert c.available() == []
    await c.handle(msg("!persona roast", mod=True))
    assert c.personality_engine.asked == ["roast"], "unvalidated, but not blocked"


async def test_a_dead_chat_connection_does_not_raise(cmd):
    cmd.twitch_client = Chat(boom=True)
    assert await cmd.handle(msg("!persona", mod=True)) is None


async def test_disabled_does_nothing(cmd):
    cmd.enabled = False
    assert await cmd.handle(msg("!persona roast", mod=True)) is None


def test_the_real_preset_file_parses():
    names = PersonaCommand(personalities_path="all_personalities.json").available()
    assert "uwu" in names and "roast" in names and len(names) >= 10


def test_the_bot_registers_the_handler():
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    assert "self._setup_persona_command()" in bot
    assert "self.twitch_client.on_message(self.persona_command.handle)" in bot
    setup = bot.split("def _setup_persona_command")[1].split("async def _handle_ad_commands")[0]
    assert "except Exception" in setup, "a chat command must never break startup"

"""Every setting must actually do something.

This project keeps growing controls that are declared, documented, and wired
to nothing: `personality.custom_traits` still held sassy values while the
preset was uwu; `tts_voice`/`tts_speed` sat beside the `voice.*` keys that
are really read; the `voice_commands` flag could be turned off and voice
commands still ran. Each one looks like a knob and silently isn't, which is
worse than not offering it -- you change it, nothing happens, and there is
no error to notice.

So: every key in the settings files must appear somewhere in the source. A
new key with no reader fails here instead of on stream.
"""
import json
from pathlib import Path

import pytest

SOURCE = "\n".join(
    p.read_text(encoding="utf-8", errors="replace")
    for p in list(Path("src").rglob("*.py")) + [Path("main.py")]
)

# Keys that are legitimately absent from the source.
ALLOWED_UNREAD = {
    # Documentation for a human editing the file by hand.
    "_comment",
}


def _leaf_keys(obj, prefix=""):
    for key, value in (obj or {}).items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from _leaf_keys(value, path + ".")
        else:
            yield path, key


def _dead_keys(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    dead = []
    for full, key in _leaf_keys(data):
        if key in ALLOWED_UNREAD or key.startswith("_"):
            continue
        if f"'{key}'" not in SOURCE and f'"{key}"' not in SOURCE:
            dead.append(full)
    return dead


@pytest.mark.parametrize("settings_file", ["bot_settings.json", "feature_flags.json"])
def test_no_setting_is_read_by_nothing(settings_file):
    dead = _dead_keys(settings_file)
    assert dead == [], (
        f"{settings_file}: these keys are read by no code, so editing them "
        f"does nothing: {dead}. Wire them up or delete them.")


def test_the_tts_keys_that_survive_are_the_ones_that_work():
    """optimized_queue reads voice.model / voice.speed. The top-level
    tts_voice / tts_speed duplicated them and were read by nothing."""
    cfg = json.loads(Path("bot_settings.json").read_text(encoding="utf-8"))
    assert "voice" in cfg and "model" in cfg["voice"] and "speed" in cfg["voice"]
    assert "tts_voice" not in cfg and "tts_speed" not in cfg
    queue = Path("src/audio/optimized_queue.py").read_text(encoding="utf-8")
    assert "settings.get('voice', {}).get('model'" in queue


def test_the_voice_commands_flag_is_honoured():
    """It could be set to false and "hey bot, mute" would still hijack the
    stream, because nothing ever read it."""
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    assert "if feature_flags.get('voice_commands', True):" in bot
    gate = bot.index("if feature_flags.get('voice_commands', True):")
    assert bot.index("VoiceCommandSystem(bot=self)") > gate, \
        "construction must sit inside the gate"
    assert "Voice commands disabled by feature flag" in bot


def test_the_default_keeps_voice_commands_on():
    """A missing flag must not silently disable a working feature."""
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    assert "feature_flags.get('voice_commands', True)" in bot


def test_the_scan_would_catch_a_newly_dead_key(tmp_path):
    """Proves the check has teeth rather than passing vacuously."""
    f = tmp_path / "fake_settings.json"
    f.write_text(json.dumps({"a_key_no_code_reads": 1}), encoding="utf-8")
    assert _dead_keys(f) == ["a_key_no_code_reads"]


def test_the_scan_accepts_a_key_the_code_reads(tmp_path):
    f = tmp_path / "fake_settings.json"
    f.write_text(json.dumps({"voice_commands": True}), encoding="utf-8")
    assert _dead_keys(f) == []

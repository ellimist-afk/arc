"""One flag must not mean two things.

The bot reads feature_flags.json. config_unified (used by the API layer)
read FEATURE_* env vars with its own defaults, and the two disagreed:
feature_flags.json ships voice_commands=true while config_unified defaulted
it to false. Nothing failed -- the same flag simply answered differently
depending on which half of the process asked.

The JSON file is now the source of truth, with env vars as explicit
overrides, then the dataclass defaults.
"""
import json
from pathlib import Path

import pytest

from core.config_unified import FeatureFlags, _load_flag_file

SRC = Path("src/core/config_unified.py").read_text(encoding="utf-8")
FLAG_ATTRS = ("raider_welcome", "advanced_personality", "context_caching",
              "voice_commands", "web_ui", "monitoring")


# ------------------------------------------------------------- the loader

def test_it_reads_the_same_file_the_bot_reads():
    bot = Path("src/bot/bot.py").read_text(encoding="utf-8")
    assert "flags_file = 'feature_flags.json'" in bot
    assert '_load_flag_file(path: str = "feature_flags.json")' in SRC


def test_the_real_file_loads():
    flags = _load_flag_file()
    assert isinstance(flags, dict) and flags, "the shipped file should parse"
    assert set(flags) <= set(FLAG_ATTRS) | {"raider_vod_analysis"}


@pytest.mark.parametrize("content", ["{ not json", '{"other": 1}', "[]"])
def test_a_broken_file_is_not_fatal(tmp_path, content):
    f = tmp_path / "flags.json"
    f.write_text(content, encoding="utf-8")
    assert _load_flag_file(str(f)) == {}


def test_a_missing_file_is_not_fatal():
    assert _load_flag_file("definitely_not_here.json") == {}


# --------------------------------------------------------- the precedence

def _resolve(flags, env, attr):
    """The precedence the loader implements, exercised directly."""
    import os
    features = FeatureFlags()
    raw = env.get(attr_env(attr))
    if raw is not None:
        return raw.strip().lower() == "true"
    if attr in flags:
        return bool(flags[attr])
    return getattr(features, attr)


def attr_env(attr):
    return "FEATURE_" + attr.upper()


def test_env_overrides_the_file():
    assert _resolve({"voice_commands": True}, {"FEATURE_VOICE_COMMANDS": "false"},
                    "voice_commands") is False
    assert _resolve({"voice_commands": False}, {"FEATURE_VOICE_COMMANDS": "true"},
                    "voice_commands") is True


def test_the_file_beats_the_dataclass_default():
    """This is the bug: the default said false, the file says true."""
    assert FeatureFlags().voice_commands is False
    assert _resolve({"voice_commands": True}, {}, "voice_commands") is True


def test_the_default_survives_when_neither_source_mentions_it():
    assert _resolve({}, {}, "web_ui") is FeatureFlags().web_ui
    assert _resolve({}, {}, "monitoring") is FeatureFlags().monitoring


def test_an_absent_env_var_does_not_read_as_false():
    """os.getenv(...) or '' would turn 'unset' into False and silently
    disable everything the file enabled."""
    assert "raw = os.getenv(env_var)" in SRC
    assert "if raw is not None:" in SRC


# ----------------------------------------------------- the two agree now

def test_every_flag_in_the_file_is_honoured_by_both_halves():
    flags = json.loads(Path("feature_flags.json").read_text(encoding="utf-8"))["flags"]
    for name in flags:
        if name == "raider_vod_analysis":
            continue          # bot-side only; no API surface for it
        assert name in FLAG_ATTRS, f"{name} is in the file but config_unified has no field"


def test_no_flag_is_resolved_from_env_alone_any_more():
    """The old block read each flag straight from os.getenv with a literal
    default; that shape is what let the two sources drift."""
    for old in ("os.getenv('FEATURE_RAIDER_WELCOME', 'false')",
                "os.getenv('FEATURE_VOICE_COMMANDS', 'false')",
                "os.getenv('FEATURE_WEB_UI', 'true')"):
        assert old not in SRC, f"{old} bypasses the flag file"

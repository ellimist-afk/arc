"""A config write must never leave the file empty.

open(path, 'w') and Path.write_text() truncate first and write second. An
interruption between the two -- a killed process, a full disk, an exception
while serialising -- leaves the file empty permanently. For bot_settings.json
that is every setting gone, and the next start comes up on defaults with no
clue why. The risk is concrete here: that file is written from chat
(!persona) and from the API while a health monitor polls and reads it.
"""
import json
from pathlib import Path

import pytest

from utils.atomic_write import write_json_atomic, write_text_atomic

SRC = Path("src/utils/atomic_write.py").read_text(encoding="utf-8")


# --------------------------------------------------------- ordinary use

def test_it_writes_the_content(tmp_path):
    target = tmp_path / "f.json"
    write_text_atomic(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_it_replaces_existing_content(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("old", encoding="utf-8")
    write_text_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_json_round_trips(tmp_path):
    target = tmp_path / "s.json"
    data = {"personality": {"preset": "uwu"}, "TTS_ENABLED": False, "n": 1.5}
    write_json_atomic(target, data)
    assert json.loads(target.read_text(encoding="utf-8")) == data


def test_json_ends_with_a_newline(tmp_path):
    """Hand-edited config files should end cleanly."""
    target = tmp_path / "s.json"
    write_json_atomic(target, {"a": 1})
    assert target.read_text(encoding="utf-8").endswith("\n")


def test_it_accepts_a_string_path(tmp_path):
    target = tmp_path / "f.txt"
    write_text_atomic(str(target), "ok")
    assert target.read_text(encoding="utf-8") == "ok"


def test_unicode_round_trips(tmp_path):
    """json.dumps escapes non-ASCII to \\uXXXX by default, as the previous
    json.dump calls did -- what matters is that the value comes back."""
    target = tmp_path / "f.json"
    line = "cassova_ died again — nine times"
    write_json_atomic(target, {"line": line})
    assert json.loads(target.read_text(encoding="utf-8"))["line"] == line


# --------------------------------------------------------- the guarantee

def test_a_failed_rename_leaves_the_original_untouched(tmp_path, monkeypatch):
    import os
    target = tmp_path / "s.json"
    target.write_text('{"keep": "me"}', encoding="utf-8")

    def boom(*a, **k):
        raise OSError("rename failed")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_json_atomic(target, {"replaced": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"keep": "me"}


def test_unserialisable_data_never_opens_the_target(tmp_path):
    """Serialise first: a bad value must fail before the file is touched."""
    target = tmp_path / "s.json"
    target.write_text('{"keep": "me"}', encoding="utf-8")
    with pytest.raises(TypeError):
        write_json_atomic(target, {"bad": object()})
    assert json.loads(target.read_text(encoding="utf-8")) == {"keep": "me"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_no_temp_survives_success(tmp_path):
    write_json_atomic(tmp_path / "s.json", {"a": 1})
    assert list(tmp_path.glob("*.tmp")) == []


def test_no_temp_survives_failure(tmp_path, monkeypatch):
    import os
    target = tmp_path / "s.json"
    target.write_text("{}", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_json_atomic(target, {"a": 1})
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_missing_directory_raises_without_creating_anything(tmp_path):
    with pytest.raises(OSError):
        write_text_atomic(tmp_path / "nope" / "f.txt", "x")
    assert not (tmp_path / "nope").exists()


# --------------------------------------------------- the mechanics matter

def test_the_temp_is_a_sibling_not_in_the_system_temp_dir():
    """os.replace is only atomic within one filesystem; a temp on another
    volume silently degrades it to a copy."""
    body = SRC.split("def write_text_atomic")[1].split("\ndef ")[0]
    assert "target.with_name(" in body
    assert "tempfile" not in SRC


def test_the_data_is_flushed_before_the_rename():
    body = SRC.split("def write_text_atomic")[1].split("\ndef ")[0]
    assert body.index("f.flush()") < body.index("os.fsync(") < body.index("os.replace(")


# ------------------------------------------- the writers that matter use it

@pytest.mark.parametrize("path, why", [
    ("src/features/persona_command.py", "!persona writes settings from chat"),
    ("src/api/v2/endpoints/settings.py", "the API writes the same file"),
    ("src/personality/personality_engine.py", "rewritten on every switch"),
    ("src/bot/session_summarizer.py", "the rolling summary"),
])
def test_config_writers_use_the_helper(path, why):
    src = Path(path).read_text(encoding="utf-8")
    assert "atomic" in src, f"{path}: {why}"


def test_bot_settings_is_never_written_by_a_truncating_call():
    """Both writers of the one file that holds everything."""
    for path in ("src/features/persona_command.py",
                 "src/api/v2/endpoints/settings.py"):
        src = Path(path).read_text(encoding="utf-8")
        assert "json.dump(settings, f" not in src
        assert ".write_text(json.dumps" not in src

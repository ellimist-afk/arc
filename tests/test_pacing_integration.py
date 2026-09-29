"""Pacing multiplier inside PersonalityEngine._should_respond.

The roll itself is random; these tests pin the die (monkeypatch random) and
assert the multiplier moves the threshold the way the pacing contract says:
quiet chat makes the same die pass, busy chat makes it fail, mentions are
never touched, and a broken multiplier degrades to the flat behavior.
"""
import pytest

import personality.personality_engine as pe_mod
from personality.personality_engine import PersonalityEngine


@pytest.fixture
def engine():
    e = PersonalityEngine(memory_system=None)
    e.current_traits.chattiness = 60          # base roll: 60/2000 = 3%
    return e


def set_die(monkeypatch, value):
    monkeypatch.setattr(pe_mod.random, "random", lambda: value)


def test_quiet_boost_makes_a_marginal_die_pass(engine, monkeypatch):
    set_die(monkeypatch, 0.05)                # above 3%, below 3% * 2.5
    assert engine._should_respond("no punctuation here", is_mention=False) is False
    engine.pacing_multiplier = lambda: 2.5
    assert engine._should_respond("no punctuation here", is_mention=False) is True


def test_busy_damp_makes_a_passing_die_fail(engine, monkeypatch):
    set_die(monkeypatch, 0.02)                # below 3%, above 3% * 0.35
    assert engine._should_respond("no punctuation here", is_mention=False) is True
    engine.pacing_multiplier = lambda: 0.35
    assert engine._should_respond("no punctuation here", is_mention=False) is False


def test_mentions_ignore_pacing_entirely(engine, monkeypatch):
    set_die(monkeypatch, 0.999)
    engine.pacing_multiplier = lambda: 0.0001
    assert engine._should_respond("anything", is_mention=True) is True


def test_broken_multiplier_degrades_to_flat_behavior(engine, monkeypatch):
    def boom():
        raise RuntimeError("tracker gone")
    engine.pacing_multiplier = boom
    set_die(monkeypatch, 0.02)
    assert engine._should_respond("no punctuation here", is_mention=False) is True
    set_die(monkeypatch, 0.05)
    assert engine._should_respond("no punctuation here", is_mention=False) is False


def test_boosted_probability_is_still_capped(engine, monkeypatch):
    engine.current_traits.chattiness = 100    # 5% base
    engine.pacing_multiplier = lambda: 100.0  # absurd boost
    set_die(monkeypatch, 0.149)
    assert engine._should_respond("hello? big question", is_mention=False) is True
    set_die(monkeypatch, 0.151)               # past the 15% cap
    assert engine._should_respond("hello? big question", is_mention=False) is False


@pytest.mark.parametrize("message", [
    "goodluck Amish lol",
    "this rework looks rough",
    "support feels rough",
    "your aim is fine",
    "they changed sombra",
])
def test_greeting_fragments_in_ordinary_words_do_not_boost_replies(
        engine, monkeypatch, message):
    set_die(monkeypatch, 0.04)  # Above the 3% base, below the greeting boost.
    assert engine._should_respond(message, is_mention=False) is False


@pytest.mark.parametrize("message", [
    "hi",
    "HELLO everyone",
    "hey, chat",
    "sup!",
    "(yo)",
])
def test_standalone_greetings_still_boost_replies(engine, monkeypatch, message):
    set_die(monkeypatch, 0.04)
    assert engine._should_respond(message, is_mention=False) is True

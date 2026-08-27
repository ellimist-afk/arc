"""A self-initiated line must not re-tell a recent bit in fresh words.

Seen live 2026-08-26 at 03:49/03:53: the co-host riffed on Sigaren's waifu
stock server, then a dead-air filler four minutes later did the same bit
again. The lexical checks scored the pair at near-zero overlap -- new words,
same topic -- so only a topic-level check can catch a rerun. It is opt-in
(fresh_topic=True) because a REPLY reusing the conversation's topic words is
normal; only lines where the bot picks its own subject must pick a new one.
"""
from personality.repetition_guard import RepetitionGuard, _distinctive, tokenize

LIVE_FIRST = ("Sigaren made a tiny waifu datacenter that watches money they "
              "refuse to understand and somehow that is the most engineer "
              "sentence tonight")
LIVE_RERUN = ("Sigaren outsourcing stock anxiety to a waifu server is "
              "genuinely efficient and also exactly how skynet gets a "
              "brokerage account")


def _guard(**kw):
    g = RepetitionGuard(**kw)
    return g


# ------------------------------------------------------------- the defect

def test_the_live_rerun_is_caught():
    g = _guard()
    g.record(LIVE_FIRST)
    v = g.check(LIVE_RERUN, fresh_topic=True)
    assert not v.ok
    assert v.retold_words == ['sigaren', 'waifu']
    assert v.retold_from == LIVE_FIRST


def test_the_lexical_checks_alone_would_have_missed_it():
    """Documents why the topic check exists at all."""
    g = _guard()
    g.record(LIVE_FIRST)
    assert g.check(LIVE_RERUN).ok, "n-gram overlap cannot see a re-worded bit"


def test_a_reply_may_reuse_the_topic():
    g = _guard()
    g.record(LIVE_FIRST)
    assert g.check(LIVE_RERUN, fresh_topic=False).ok


def test_a_genuinely_new_filler_passes():
    g = _guard()
    g.record(LIVE_FIRST)
    v = g.check("cassova_ has been aiming at the same doorway for ten minutes "
                "chat should we tell him", fresh_topic=True)
    assert v.ok


# ----------------------------------------------------------- boundaries

def test_one_shared_word_is_conversation_not_a_rerun():
    g = _guard()
    g.record("ana said the cat gets more square footage than she does")
    v = g.check("chat vote now is a cat a liquid or a solid", fresh_topic=True)
    assert v.ok, "one shared word ('cat') must not trip the check"


def test_shared_words_must_come_from_the_same_past_line():
    """Two words scattered across different outputs are just the stream's
    vocabulary; two from ONE line are that line's premise."""
    g = _guard()
    g.record("ana bought an ergonomic keyboard for her desk")
    g.record("cassova_ missed every shot in that dungeon")
    v = g.check("an ergonomic dungeon speedrun would fix this stream",
                fresh_topic=True)
    assert v.ok


def test_the_window_is_bounded():
    g = _guard(topic_window=3)
    g.record(LIVE_FIRST)
    for filler in ("line about pizza toppings tonight",
                   "line about traffic lights downtown",
                   "line about penguin documentaries"):
        g.record(filler)
    v = g.check(LIVE_RERUN, fresh_topic=True)
    assert v.ok, "a bit from before the window has cooled off"


def test_stopwords_and_short_words_are_never_topics():
    assert _distinctive(tokenize("that was the and it is so")) == set()
    assert 'cat' not in _distinctive(tokenize("the cat sat"))       # len < 4
    assert _distinctive(tokenize("waifu datacenter")) == {'waifu', 'datacenter'}


def test_empty_history_and_empty_candidate_are_fine():
    g = _guard()
    assert g.check("anything at all here", fresh_topic=True).ok
    g.record(LIVE_FIRST)
    assert g.check("", fresh_topic=True).ok


# -------------------------------------------------------- reporting & hint

def test_reason_names_the_retold_words():
    g = _guard()
    g.record(LIVE_FIRST)
    v = g.check(LIVE_RERUN, fresh_topic=True)
    assert "re-told topic" in v.reason
    assert "'sigaren'" in v.reason and "'waifu'" in v.reason


def test_avoid_hint_demands_a_different_subject():
    g = _guard()
    g.record(LIVE_FIRST)
    v = g.check(LIVE_RERUN, fresh_topic=True)
    hint = g.avoid_hint(v)
    assert "COMPLETELY different subject" in hint
    assert "waifu" in hint


def test_engine_marks_only_dead_air_as_fresh_topic():
    from pathlib import Path
    src = Path("src/personality/personality_engine.py").read_text(encoding="utf-8")
    assert 'fresh_topic = message == "[DEAD_AIR_FILLER]"' in src
    assert src.count("fresh_topic=fresh_topic") == 2, \
        "both the first draft and the retry must use the same mode"

"""RepetitionGuard: pure n-gram logic over the bot's own recent outputs.

No model, no clock, no I/O. Each test records a small history and asks for
a verdict on a candidate; thresholds are pinned explicitly so the defaults
can move without silently changing what these tests mean.
"""
import pytest

from personality.repetition_guard import RepetitionGuard, ngrams, tokenize


def guard(**kw):
    base = dict(history_size=20, similarity_threshold=0.45,
                opening_cooldown=5, phrase_cooldown=8, catchphrase_min_uses=2)
    base.update(kw)
    return RepetitionGuard(**base)


# ----------------------------------------------------------------- helpers

def test_tokenize_drops_punctuation_keeps_apostrophes():
    assert tokenize("Don't panic, chat -- it's FINE!") == ["don't", "panic", "chat", "it's", "fine"]


def test_ngrams_short_input():
    assert ngrams(["a", "b"], 3) == []
    assert ngrams(["a", "b", "c"], 2) == [("a", "b"), ("b", "c")]


# ----------------------------------------------------------------- baseline

def test_empty_history_accepts_anything():
    g = guard()
    v = g.check("literally anything goes here")
    assert v.ok and v.score == 0.0


def test_blank_candidate_is_ok_not_crash():
    g = guard()
    g.record("hello chat")
    assert g.check("").ok
    assert g.check("   ").ok


def test_record_ignores_blank():
    g = guard()
    g.record("")
    g.record("!!!")
    assert g.history == []


# ------------------------------------------------------------ similarity

def test_near_verbatim_restatement_rejected():
    g = guard()
    g.record("honestly that build is a war crime and you know it")
    v = g.check("Honestly, that build is a war crime, and you know it.")
    assert not v.ok
    assert v.score >= 0.45
    assert v.nearest.startswith("honestly that build")


def test_same_substance_different_words_accepted():
    g = guard()
    g.record("honestly that build is a war crime and you know it")
    v = g.check("your loadout should be tried at the hague")
    assert v.ok
    assert v.score < 0.45


def test_similarity_is_max_over_window_not_just_last():
    g = guard()
    g.record("the pineapple pizza discourse ends today chat")
    for i in range(5):
        g.record(f"unrelated line number {i} about the boss fight")
    v = g.check("the pineapple pizza discourse ends today chat, seriously")
    assert not v.ok


def test_history_bounded():
    g = guard(history_size=3)
    for i in range(10):
        g.record(f"completely distinct sentence number {i} with extra words")
    assert len(g.history) == 3
    assert g.history[0].endswith("number 7 with extra words")


# --------------------------------------------------------------- openings

def test_reused_opener_rejected_even_if_rest_differs():
    g = guard()
    g.record("oh chat, we are not doing this again tonight")
    v = g.check("oh chat, the raid boss has other plans for you")
    assert not v.ok
    assert v.reused_opening == "oh chat"
    assert v.score < 0.45  # it's the opener, not the body, that failed


def test_opener_cooldown_expires():
    g = guard(opening_cooldown=2)
    g.record("oh chat, first one")
    g.record("meanwhile the boss is doing something else entirely")
    g.record("separately, someone in chat needs to calm down a bit")
    v = g.check("oh chat, this is fine now")
    assert v.ok
    assert v.reused_opening is None


# ---------------------------------------------------------- catchphrases

def test_catchphrase_goes_on_cooldown_after_two_uses():
    g = guard()
    g.record("that was a skill issue, full stop, move on")
    v1 = g.check("bold of you to call it a skill issue when you're doing worse")
    assert v1.ok, "one prior use is not yet a catchphrase"
    g.record("bold of you to call it a skill issue when you're doing worse")
    v2 = g.check("not to be rude but this is a skill issue again")
    assert not v2.ok
    assert "a skill issue" in v2.hot_phrases


def test_stopword_only_trigrams_never_count_as_catchphrase():
    g = guard()
    g.record("so that is the plan to win the round")
    g.record("so that is what we do at the fork")
    v = g.check("so that is chat losing the plot somewhere")
    assert v.hot_phrases == [], "'so that is' recurs but carries no content"
    # only the opener ("so that") can trip it
    assert v.reused_opening == "so that"


def test_catchphrase_counted_once_per_output():
    g = guard()
    g.record("skill issue skill issue skill issue skill issue")  # one output, many repeats
    v = g.check("chat says skill issue and i am inclined to agree")
    assert v.hot_phrases == [], "repeats inside one output don't make a catchphrase"


# ------------------------------------------------------------ short text

def test_short_text_exact_match_only():
    g = guard(short_text_tokens=4)
    g.record("anyone there")
    assert not g.check("anyone there?").ok        # same tokens -> reject
    assert g.check("chat seems quiet").ok         # different short text -> ok
    assert g.check("chat anyone awake tonight").ok  # >= 4 tokens: n-gram path, low overlap
    v = g.check("anyone there chat tonight")
    assert not v.ok and v.reused_opening == "anyone there"  # n-gram path still checks openers


# ---------------------------------------------------------------- hints

def test_avoid_hint_names_what_went_wrong():
    g = guard()
    g.record("oh chat, that is a skill issue and nothing else")
    g.record("oh chat, again, a skill issue, truly")
    v = g.check("oh chat, a skill issue once more")
    assert not v.ok
    hint = g.avoid_hint(v)
    assert "repeated yourself" in hint
    assert '"oh chat"' in hint
    assert "skill issue" in hint
    assert "different opener" in hint


def test_reason_is_human_readable():
    g = guard()
    g.record("oh chat, that is a skill issue and nothing else")
    v = g.check("oh chat, that is a skill issue and nothing else")
    assert "opening 'oh chat' reused" in v.reason
    assert "similarity 1.00" in v.reason


def test_clear_resets_everything():
    g = guard()
    g.record("oh chat, here we go")
    g.clear()
    assert g.history == []
    assert g.check("oh chat, here we go").ok

"""A two-word tic must not hide behind its changing third word.

Seen live 2026-08-26: "is exactly how skynet gets a brokerage account"
(03:53), "is exactly how you end up with three configs" (04:56) -- chat
called out "keep saying exactly how". The catchphrase check counted
trigrams only, and the tail word differs every time ("exactly how skynet",
"exactly how you"), so the tic never went hot. Bigrams are counted now, at
a higher bar (3 uses vs the trigram's 2) because any two-word sequence
recurs by chance often enough that 2 would over-trigger.
"""
from personality.repetition_guard import RepetitionGuard

TIC_LINES = [
    "spending a fresh reset on neovim theology is exactly how you end up with three configs",
    "watching stocks with a waifu server is also exactly how skynet gets a brokerage account",
    "daring people to click was exactly how cursed marketing worked",
]
CANDIDATE = "and that is exactly how empires fall chat"


def _warm(n):
    g = RepetitionGuard()
    for line in TIC_LINES[:n]:
        g.record(line)
    return g


# ------------------------------------------------------------- the defect

def test_the_live_tic_is_caught_on_the_fourth_use():
    g = _warm(3)
    v = g.check(CANDIDATE)
    assert not v.ok
    assert "exactly how" in v.hot_phrases


def test_trigrams_alone_never_saw_it():
    """The changing tail word is the whole reason bigrams are needed."""
    g = _warm(3)
    tri_hot = {p for p in g.hot_phrases() if len(p) == 3}
    assert not any('exactly' in tri for tri in tri_hot), \
        "no single trigram recurs -- the tic lives at the bigram level"


def test_a_natural_double_is_allowed():
    g = _warm(2)
    assert g.check(CANDIDATE).ok, "two uses is conversation, not a tic"


def test_trigram_threshold_is_unchanged():
    g = RepetitionGuard()
    g.record("chat the tiny hamster of doom returns")
    g.record("beware the tiny hamster of doom again")
    v = g.check("i summon the tiny hamster of doom")
    assert not v.ok, "a trigram still goes hot at 2 uses"
    assert any("tiny hamster" in p for p in v.hot_phrases)


# ------------------------------------------------------------ boundaries

def test_stopword_only_bigrams_never_go_hot():
    g = RepetitionGuard()
    for line in ("of the many things tonight",
                 "because of the rain outside",
                 "top of the leaderboard climb"):
        g.record(line)
    assert g.check("one of the good rounds").ok


def test_the_tic_cools_off_outside_the_window():
    g = RepetitionGuard(phrase_cooldown=3)
    for line in TIC_LINES:
        g.record(line)
    for line in ("a line about pizza toppings tonight",
                 "a line about traffic lights downtown",
                 "a line about penguin documentaries"):
        g.record(line)
    assert g.check(CANDIDATE).ok, "old uses outside the window must not count"


def test_repeats_within_one_output_count_once():
    g = RepetitionGuard()
    g.record("exactly how and exactly how and exactly how")
    assert g.check(CANDIDATE).ok, "one rambling line is not three uses"


def test_hint_names_the_tic_for_the_retry():
    g = _warm(3)
    v = g.check(CANDIDATE)
    hint = g.avoid_hint(v)
    assert "exactly how" in hint


def test_fresh_lines_still_pass():
    g = _warm(3)
    assert g.check("cassova_ just walked into the same wall twice chat").ok

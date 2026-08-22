"""SentenceSplitter: boundaries, abbreviations, holds, run-on cuts, streaming parity."""
import pytest

from audio.sentence_splitter import SentenceSplitter, split_text


def stream(text, chunk=3, **kw):
    """Feed text in small chunks, return everything emitted (incl. flush)."""
    s = SentenceSplitter(**kw)
    out = []
    for i in range(0, len(text), chunk):
        out += s.feed(text[i:i + chunk])
    tail = s.flush()
    if tail:
        out.append(tail)
    return out


# ------------------------------------------------------------- boundaries

def test_basic_terminators():
    assert split_text("First one. Second one! Third one? Done.", min_chars=1) == [
        "First one.", "Second one!", "Third one?", "Done."
    ]


def test_terminator_needs_following_whitespace():
    s = SentenceSplitter(min_chars=1)
    assert s.feed("Hold on.") == []          # stream might continue: "Hold on.5"? no — but unknown
    assert s.feed(" Go.") == ["Hold on."]     # the space proves the first was complete
    assert s.flush() == "Go."


def test_repeated_terminators_and_closing_quotes():
    assert split_text('He said "no!!!" Then left?! "Really." Yes.', min_chars=1) == [
        'He said "no!!!"', "Then left?!", '"Really."', "Yes."
    ]


def test_newline_counts_as_whitespace_boundary():
    assert split_text("Line one.\nLine two.", min_chars=1) == ["Line one.", "Line two."]


# ---------------------------------------------------------- false boundaries

@pytest.mark.parametrize("text", [
    "Mr. Beast is here today.",
    "Ask Dr. Disrespect about it later.",
    "Use e.g. the second build for this.",
    "Plan A. then plan B. then we cry together.",
    "J. K. Rowling wrote that one apparently.",
])
def test_abbreviations_and_initials_do_not_split(text):
    assert split_text(text, min_chars=1) == [text]


def test_decimal_and_version_numbers_do_not_split():
    assert split_text("Patch 3.5 changed it. Wild.", min_chars=1) == ["Patch 3.5 changed it.", "Wild."]
    assert split_text("It was v2. beta was worse honestly.", min_chars=1) == ["It was v2. beta was worse honestly."]


def test_number_then_capital_does_split():
    assert split_text("Died at 2. Then rallied.", min_chars=1) == ["Died at 2.", "Then rallied."]


# ------------------------------------------------------------------ holds

def test_short_sentence_is_held_and_joined():
    assert split_text("Oh. Chat, that is genuinely bad.", min_chars=12) == ["Oh. Chat, that is genuinely bad."]


def test_held_sentence_flushes_alone_at_end():
    assert split_text("Long enough sentence here. Ok.", min_chars=12) == ["Long enough sentence here.", "Ok."]


def test_multiple_short_sentences_accumulate():
    assert split_text("No. Nope. Never. Absolutely not happening.", min_chars=14) == [
        "No. Nope. Never.", "Absolutely not happening."
    ]


# --------------------------------------------------------------- run-ons

def test_run_on_is_cut_at_soft_punctuation():
    # comma lands inside the cut window [60%, 100%] of max_chars
    words = ("word " * 16).strip() + ", " + ("more " * 30).strip() + " end."
    out = split_text(words, min_chars=1, max_chars=120)
    assert len(out) >= 3
    assert out[0].endswith(",")
    assert all(len(o) <= 120 for o in out[:-1])
    assert " ".join(out) == words


def test_run_on_is_cut_even_when_fed_all_at_once():
    # The terminator is far past max_chars: we must not wait for it
    words = ("word " * 60).strip() + "."
    out = split_text(words, min_chars=1, max_chars=100)
    assert len(out) >= 3
    assert max(len(o) for o in out) <= 100


def test_run_on_without_punctuation_cuts_at_whitespace():
    words = ("word " * 60).strip() + "."
    out = split_text(words, min_chars=1, max_chars=100)
    assert len(out) >= 2
    assert all(not o.startswith(" ") for o in out)
    assert " ".join(out) == words


# --------------------------------------------------------------- streaming

@pytest.mark.parametrize("chunk", [1, 2, 5, 17, 1000])
def test_streaming_matches_whole_text(chunk):
    text = ('Honestly? That build is a war crime. Mr. Beast would not approve, e.g. the helmet. '
            'Patch 3.5 made it worse!! "Fix it," chat said. Ok.')
    assert stream(text, chunk=chunk, min_chars=12) == split_text(text, min_chars=12)


def test_empty_and_whitespace_input():
    s = SentenceSplitter()
    assert s.feed("") == []
    assert s.feed("   ") == []
    assert s.flush() is None
    assert s.emitted == 0


def test_pending_reports_unemitted_text():
    s = SentenceSplitter(min_chars=12)
    s.feed("Ok. Still going")
    assert s.pending == "Ok. Still going"

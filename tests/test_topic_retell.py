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


def test_engine_marks_fillers_and_interjections_as_fresh_topic():
    """Mentions are exempt: an addressed reply is owed whatever its topic."""
    from pathlib import Path
    src = Path("src/personality/personality_engine.py").read_text(encoding="utf-8")
    assert 'fresh_topic = is_filler or not is_mention' in src
    # Scoped to the guard function: counting file-wide also caught the
    # streamed path's own call and broke the moment that was added.
    guard = src.split("async def _enforce_variety")[1].split("\n    async def ")[0]
    assert guard.count("fresh_topic=fresh_topic") == 2, \
        "both the first draft and the retry must use the same mode"
    assert guard.count("topic_exempt=topic_exempt") == 2


# ------------------------------------- interjections re-running their own bit

FINGERPRINTS_BIT = ("i still think shipping the plugin as 100% claude is "
                    "honest branding cute little fingerprints and all as long "
                    "as cassova_ doesnt polish it into dust")
FINGERPRINTS_RERUN = ("claude fingerprints are cute until cassova_ turns one "
                      "honest plugin note into a twelve-minute ethics boss fight")


def test_an_interjection_rerunning_its_own_bit_is_caught():
    """Seen live 2026-08-26 at 04:52/05:00: the co-host did the claude-
    fingerprints bit, then eight minutes later did it again while the message
    it was answering was about something else entirely."""
    g = _guard()
    g.record(FINGERPRINTS_BIT)
    exempt = RepetitionGuard.topic_words("OMEGALUL is this the austrailian package")
    v = g.check(FINGERPRINTS_RERUN, fresh_topic=True, topic_exempt=exempt)
    assert not v.ok
    assert 'fingerprints' in v.retold_words and 'claude' in v.retold_words


def test_riffing_on_the_message_being_answered_is_never_a_rerun():
    """04:56 mixed the fresh chatgpt-reset message into the running neovim
    thread -- a good line. Words from the answered message are exempt."""
    g = _guard()
    g.record("neovim wins if you want plugins that feel alive instead of "
             "little haunted vim fossils")
    exempt = RepetitionGuard.topic_words(
        "ChatGPT weekly just reset, we are open for business Kreygasm")
    v = g.check("spending a fresh chatgpt reset on late-night neovim theology "
                "means three configs and no plugin shipped",
                fresh_topic=True, topic_exempt=exempt)
    assert v.ok


def test_topic_words_helper_extracts_distinctive_words():
    assert RepetitionGuard.topic_words("ChatGPT weekly just reset") == \
        {'chatgpt', 'weekly', 'reset'}
    assert RepetitionGuard.topic_words("") == set()


def test_exempt_words_cannot_mask_a_rerun_on_other_words():
    """Exemption removes only the answered message's words; the rest of a
    re-told premise still counts."""
    g = _guard()
    g.record(FINGERPRINTS_BIT)
    exempt = RepetitionGuard.topic_words("what about the claude thing")
    v = g.check(FINGERPRINTS_RERUN, fresh_topic=True, topic_exempt=exempt)
    assert not v.ok, "'claude' is exempt but fingerprints+cassova+honest remain"


# ------------------------- generic vocabulary is not a shared topic

def test_common_words_alone_never_signal_a_rerun():
    """Live 2026-08-27: the guard rejected drafts for "re-told topic:
    'because', 'character'" and "'chatting', 'somehow'" -- generic words, not
    topics -- and the retry rejections silenced the co-host six times."""
    for first, second in [
        ("the whole character thing is funny because chat said so",
         "because a character bit lands when chat is awake"),
        ("somehow chatting at this hour still works",
         "chatting somehow beats sleeping i guess"),
    ]:
        g = _guard()
        g.record(first)
        assert g.check(second, fresh_topic=True).ok, (first, second)


def test_one_identifying_word_is_enough_to_catch_a_rerun():
    """'5090s' names the bit even when 'next' and 'year' ride along."""
    g = _guard()
    g.record("if 5090s land next year the current prices look like a prank")
    v = g.check("if 5090s ship next year every card today ages badly",
                fresh_topic=True)
    assert not v.ok
    assert '5090s' in v.retold_words


def test_two_identifying_words_still_trip_it():
    g = _guard()
    g.record("gpu clearance racks are basically a graveyard for gpus")
    assert not g.check("clearance gpus are just haunted silicon",
                       fresh_topic=True).ok


def test_identifying_filter_keeps_the_stream_vocabulary_out():
    from personality.repetition_guard import _identifying
    assert _identifying({'because', 'character', 'chatting', 'somehow'}) == set()
    assert _identifying({'waifu', 'chat', 'stream'}) == {'waifu'}

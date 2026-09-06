"""The streamer is not the default punchline.

Live 2026-09-06, persona uwu: every line the co-host said in twenty minutes
closed on the streamer's name -- a chatter's meme, an ad break, a disease
joke, a French kiss. He was the subject of none of them. Checks 1-4 of the
repetition guard saw nothing: his handle is one token and the lines were
otherwise entirely different.

So the guard counts him. A draft that names the streamer when he has
already been named in the recent window is rejected and rewritten without
him -- unless the line is genuinely about him: the message being answered
names him, or the line is a reaction to what is on his screen.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from personality.repetition_guard import RepetitionGuard, names_target  # noqa: E402

ENGINE_SRC = Path("src/personality/personality_engine.py").read_text(
    encoding="utf-8", errors="replace")

TARGET = "cassova_"


def _guard(**kw):
    g = RepetitionGuard(**kw)
    return g


def _fill(g, n, naming=0):
    """n accepted outputs, the first `naming` of which name the target."""
    for i in range(n):
        if i < naming:
            g.record(f"{TARGET} just walked into that wall on purpose number {i}")
        else:
            g.record(f"chat is arguing about pineapple pizza again round {i}")


DRAFT = f"gary's credibility fell over like {TARGET} trying to peel an orange"


# ------------------------------------------------------------- token match

def test_the_target_is_matched_on_tokens_not_the_raw_handle():
    assert names_target("look at Cassova_ go", TARGET)
    assert names_target("CASSOVA_, again?", "cassova_")
    assert names_target(f"nice one {TARGET}!", TARGET)
    assert not names_target("chat is quiet tonight", TARGET)


def test_a_multi_word_target_needs_the_words_together():
    assert names_target("say hi to big cat now", "big cat")
    assert not names_target("the cat is big", "big cat")


def test_an_empty_target_matches_nothing():
    assert not names_target("cassova_ again", "")
    assert not names_target("cassova_ again", None)


def test_a_handle_that_starts_with_the_target_matches():
    """"cassova_fan" tokenizes to ["cassova", "fan"], so the target's single
    token is a contiguous run inside it. Accepted deliberately -- see
    names_target -- and pinned so the behaviour is a decision, not a bug."""
    assert names_target("cassova_fan said hi", TARGET)


# ------------------------------------------------------------ the counting

def test_naming_him_once_in_the_window_is_fine():
    g = _guard()
    _fill(g, 4, naming=0)
    assert g.check(DRAFT, target=TARGET).ok


def test_naming_him_again_after_he_was_just_the_punchline_is_rejected():
    g = _guard()
    _fill(g, 4, naming=1)
    v = g.check(DRAFT, target=TARGET)
    assert not v.ok
    assert v.overused_target == TARGET
    assert v.target_uses == 1
    assert "streamer punchline" in v.reason
    assert f"streamer punchline: {TARGET} in 1 of last 4" in v.reason


def test_a_message_that_names_him_makes_the_line_his_again():
    g = _guard()
    _fill(g, 4, naming=1)
    assert g.check(DRAFT, target=TARGET, target_exempt=True).ok


def test_a_draft_that_does_not_name_him_passes_whatever_the_history():
    g = _guard()
    _fill(g, 4, naming=4)
    assert g.check("chat that pizza take is a war crime and you know it",
                   target=TARGET).ok


def test_no_target_means_the_check_is_off():
    g = _guard()
    _fill(g, 4, naming=4)
    assert g.check(DRAFT).ok
    assert g.check(DRAFT, target=None).ok
    assert g.check(DRAFT, target="").ok


def test_uses_outside_the_window_do_not_count():
    g = _guard(target_window=2)
    _fill(g, 1, naming=1)          # names him
    _fill(g, 2, naming=0)          # two lines later he is out of the window
    assert g.check(DRAFT, target=TARGET).ok


def test_the_limit_is_configurable():
    g = _guard(target_max=2)
    _fill(g, 4, naming=1)
    assert g.check(DRAFT, target=TARGET).ok, "one use is under a max of two"
    g2 = _guard(target_max=2)
    _fill(g2, 4, naming=2)
    assert not g2.check(DRAFT, target=TARGET).ok


def test_a_short_line_naming_him_is_caught_too():
    """"cassova_ again lol" is three tokens: the short-text path returns
    early, and the reflex is exactly the same one."""
    g = _guard()
    _fill(g, 4, naming=1)
    v = g.check(f"{TARGET} again lol", target=TARGET)
    assert not v.ok and v.overused_target == TARGET


def test_the_hint_tells_the_model_to_drop_him():
    g = _guard()
    _fill(g, 4, naming=1)
    v = g.check(DRAFT, target=TARGET)
    hint = g.avoid_hint(v)
    assert f"Leave {TARGET} out of this one entirely" in hint
    assert "was the punchline in 1 of your last 4 lines" in hint
    assert "The joke is about the topic at hand." in hint


def test_a_clean_verdict_says_nothing_about_the_target():
    g = _guard()
    _fill(g, 4, naming=0)
    v = g.check(DRAFT, target=TARGET)
    assert v.overused_target is None
    assert "streamer punchline" not in g.avoid_hint(v)


def test_the_guard_still_records_only_accepted_output():
    g = _guard()
    _fill(g, 4, naming=1)
    before = list(g.history)
    g.check(DRAFT, target=TARGET)
    assert list(g.history) == before, "checking must not record"


# --------------------------------------------------------- engine wiring

def test_both_paths_compute_the_target_the_same_way():
    """One helper, called by the blocking and the streamed path, so the two
    cannot drift the way the topic rule once did."""
    assert ENGINE_SRC.count("self._roast_target(message, context)") == 2
    helper = ENGINE_SRC.split("def _roast_target(")[1].split("\n    async def ")[0]
    assert "target = self.streamer_name" in helper
    assert "names_target(message, target)" in helper
    assert "'screen_is_the_topic'" in helper


def test_the_blocking_path_passes_the_target_to_both_checks():
    body = ENGINE_SRC.split("async def _enforce_variety")[1].split("\n    async def ")[0]
    assert body.count("target=target, target_exempt=target_exempt") == 2, \
        "the draft and the retry are both checked against the target"


def test_the_streamed_gate_passes_the_target():
    stream = ENGINE_SRC.split("async def _sentence_stream")[1]
    assert stream.count("target, target_exempt)") == 2, \
        "first sentence and tail both gated"


def test_only_the_first_sentence_of_a_streamed_reply_is_gated_on_the_target():
    """A later sentence of the same reply may name him: the reply as a whole
    was already allowed to be about him."""
    gate = ENGINE_SRC.split("def _gate_sentence(")[1].split("\n    async def ")[0]
    assert "target=target if first else None" in gate


def test_the_settings_keys_reach_the_guard():
    loop = ENGINE_SRC.split("guard_cfg = settings.get('repetition_guard')")[1][:400]
    assert "'target_window'" in loop and "'target_max'" in loop


# ------------------------------------------------------------- the prompt

def _prompt(name="cassova_"):
    from personality.personality_engine import PersonalityEngine, PersonalityTraits
    e = PersonalityEngine.__new__(PersonalityEngine)
    e.current_traits = PersonalityTraits()
    e.streamer_name = name
    e.bot_name = "elimist_"
    e.current_personality_name = ""
    return e._build_personality_prompt()


def test_the_standing_favourite_target_instruction_is_gone():
    """"your favorite and safest roast target" in every preset's system
    prompt beat its own "when there is a real opening" qualifier: the model
    reached for the streamer as the closer of any line with no other name
    in it."""
    prompt = _prompt()
    assert "favorite and safest" not in prompt
    assert "safest roast target" not in prompt


def test_the_prompt_makes_him_fair_game_only_when_the_line_is_about_him():
    prompt = _prompt()
    assert ("cassova_ is fair game ONLY when the line is about cassova_: "
            "something cassova_ just did on screen, said, or a viewer said "
            "about cassova_.") in prompt
    assert "Then roast cassova_ in one sharp, playful line." in prompt
    assert "never make cassova_ the punchline by default" in prompt
    assert ("Answering a viewer about their own topic: the joke is about "
            "that topic.") in prompt


def test_the_comparison_closer_is_a_banned_joke_shape():
    """"gary's credibility fell over like cassova_ trying to peel an orange"
    -- the topic was gary, the punchline was the streamer."""
    prompt = _prompt()
    assert ("- closing a line about something else with a comparison to "
            "cassova_") in prompt
    banned = prompt.split("Joke shapes that are BANNED")[1]
    assert "comparison to cassova_" in banned, "it belongs in the banned list"


def test_the_rule_carries_whatever_the_streamer_is_called():
    assert "someone_else is fair game ONLY" in _prompt("someone_else")
    assert "the streamer is fair game ONLY" in _prompt("")

"""The streamed and blocking reply paths must enforce the same rules.

Two behaviours were added to the blocking path and never mirrored here, so
turning on tts_streaming silently changed what the co-host would say:

1. `always_respond` (a vision-flagged moment the bot already decided is
   worth saying) was ignored, so the 3% chattiness roll discarded 97% of
   the moments we had just paid a vision call to notice.
2. The topic-retell check was never applied at all -- the fix for the
   co-host re-telling its own bits simply did not exist on this path.
"""
from pathlib import Path

SRC = Path("src/personality/personality_engine.py").read_text(encoding="utf-8")
STREAMED = SRC.split("async def generate_response_streamed")[1].split("async def _sentence_stream")[0]
SENTENCE_STREAM = SRC.split("async def _sentence_stream")[1]
BLOCKING = SRC.split("async def generate_response")[1].split("async def _enforce_variety")[0]


# --------------------------------------------------- the chattiness bypass

def test_both_paths_honour_always_respond():
    assert SRC.count("(context or {}).get('always_respond')") == 2, \
        "the bypass must exist on both paths"


def test_the_streamed_roll_checks_the_bypass_first():
    gate = STREAMED.index("_should_respond(message, is_mention)")
    before = STREAMED[:gate]
    assert "always_respond" in before, "the bypass must short-circuit the roll"


def test_dead_air_still_bypasses_on_both_paths():
    assert STREAMED.count('message != "[DEAD_AIR_FILLER]"') >= 1
    assert BLOCKING.count('message != "[DEAD_AIR_FILLER]"') >= 1


# ------------------------------------------------------- the topic check

def test_the_streamed_gate_applies_the_topic_check():
    assert "fresh_topic=fresh_topic and first" in SRC, \
        "the topic check must reach the streamed guard"


def test_the_streamed_path_computes_the_same_topic_mode():
    assert "fresh_topic = is_filler or not is_mention" in SENTENCE_STREAM
    assert "self.repetition_guard.topic_words(message)" in SENTENCE_STREAM


def test_the_topic_rule_is_identical_on_both_paths():
    """A copy that drifts is worse than no copy: it changes behaviour only
    when a flag is flipped."""
    assert SRC.count("fresh_topic = is_filler or not is_mention") == 2


def test_only_the_first_sentence_gets_the_topic_check():
    """Later sentences of one reply legitimately share its topic; checking
    them would reject every multi-sentence answer."""
    assert "fresh_topic and first" in SRC


def test_both_gate_calls_pass_the_topic_mode():
    assert SENTENCE_STREAM.count("self._gate_sentence(") == 2
    assert SENTENCE_STREAM.count("fresh_topic, topic_exempt") == 2


def test_gate_signature_defaults_keep_it_safe_to_call_bare():
    sig = SRC.split("def _gate_sentence(")[1].split(")")[0]
    assert "fresh_topic: bool = False" in sig
    assert "topic_exempt" in sig

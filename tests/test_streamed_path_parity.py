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


# ------------------------------- the streamer-target check, actually run

# Everything above reads the source. That is enough to catch a check that
# was never copied to the other path, and nothing else: it cannot tell
# whether the copy WORKS. The target check is the newest of these rules and
# the only one with no executing parity test at all, so this drives both
# paths with the same guard history, the same message and the same drafts,
# and asserts the same line comes out.
import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from personality.personality_engine import PersonalityEngine  # noqa: E402

TARGET = "cassova_"
# Names the streamer, and nothing else the guard objects to.
NAMES_HIM = (f"gary's credibility fell over and {TARGET} laughed for a full "
             f"minute. and then it got worse for everyone.")
CLEAN = "chat is arguing about pineapple pizza again."


class _Stream:
    def __init__(self, text):
        self.parts = [text[i:i + 4] for i in range(0, len(text), 4)]
        self.i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.i >= len(self.parts):
            raise StopAsyncIteration
        part = self.parts[self.i]
        self.i += 1
        return SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=part))])


class _Client:
    """One script, served streamed or blocking as asked."""

    def __init__(self, script):
        self.script = list(script)
        self.served = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        text = self.script.pop(0)
        self.served.append(text)
        if kw.get("stream"):
            return _Stream(text)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=text))])


def _engine(*script):
    e = PersonalityEngine(memory_system=None, openai_api_key="test-key")
    e.streamer_name = TARGET
    e.should_respond_override = True
    e.openai_client = _Client(script)
    # He was the punchline once already in the recent window; naming him
    # again is what the guard exists to stop.
    e.repetition_guard.record(f"{TARGET} just walked into that wall on purpose")
    for i in range(3):
        e.repetition_guard.record(f"someone asked about the keyboard again {i}")
    return e


async def test_both_paths_reject_the_same_streamer_punchline():
    """Same history, same message, same drafts -> the same delivered line.
    A source assertion cannot tell a working copy from a spelled one."""
    message = "what even happened there"

    blocking = _engine(NAMES_HIM, CLEAN)
    got = await blocking.generate_response(message, {}, "viewer", is_mention=True)
    assert got is not None
    assert TARGET not in got['text'], "the blocking path delivered the punchline"
    assert "pineapple" in got['text']

    streamed = _engine(NAMES_HIM, CLEAN)
    reply = await streamed.generate_response_streamed(
        message, {}, "viewer", is_mention=True, min_sentence_chars=1)
    assert reply is not None
    said = []
    async for sentence in reply.sentences:
        said.append(sentence)
    whole = " ".join(said)
    assert TARGET not in whole, "the streamed path delivered the punchline"
    assert "pineapple" in whole
    assert streamed.openai_client.served == blocking.openai_client.served
